"""Shared HTTP-with-cache plumbing for the JSON API clients.

JSON GETs are cached by URL (`mailto` stripped from the key) with 404 stored
as `None`. Transient statuses are retried with a bounded wait, and requests can
be paced by a minimum interval. Only 2xx and 404 responses are cached.
"""

from __future__ import annotations

import logging
import math
import random
import time
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from citefinder.cache import JsonlCache, LayeredCache

log = logging.getLogger("citefinder")

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_MAX_WAIT = 60.0

# Minimum spacing between consecutive uncached requests from one client.
# OpenAlex's binding limit is a daily credit budget, not this rate; Crossref
# sets its own default.
DEFAULT_MIN_INTERVAL = 0.1

# Statuses worth retrying: the rate limiter and gateway errors. Every other
# 4xx/5xx raises on the first attempt.
RETRY_STATUSES = frozenset({429, 502, 503, 504})

# Quota headers: OpenAlex sends `x-ratelimit-*`, Crossref `x-rate-limit-*`.
RATE_LIMIT_HEADER_PREFIXES = ("x-ratelimit-", "x-rate-limit-")

# Seconds between cache writes of the quota snapshot (the cache is append-only,
# so writing every capture would add a line per request).
RATE_LIMIT_PERSIST_INTERVAL = 60.0


def package_version() -> str:
    """The installed `citefinder` version, or `0.0.0` without a distribution record."""
    try:
        return version("citefinder")
    except PackageNotFoundError:
        return "0.0.0"


def _default_user_agent(contact: str | None = None) -> str:
    """`citefinder/<version> (<project url>[; mailto:<contact>])`.

    Sources without a polite-pool parameter take `contact` in the User-Agent.
    """
    agent = f"citefinder/{package_version()} (https://github.com/gitronald/citefinder"
    if contact:
        agent = f"{agent}; mailto:{contact}"
    return f"{agent})"


def _doi_path(doi: str) -> str:
    """`doi` made safe to interpolate into a URL path.

    Only `%`, `#`, and `?` are encoded; leaving the rest alone keeps cache keys
    stable for ordinary DOIs.
    """
    return doi.replace("%", "%25").replace("#", "%23").replace("?", "%3F")


def validate_knob(name: str, value: float) -> float:
    """`value` if it is a finite number >= 0, else `ValueError` naming `name`.

    Catches `inf`/`nan` and negatives before they fail inside `time.sleep`.
    """
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")
    return value


def _strip_mailto(url: str) -> str:
    """Return `url` with any `mailto` query param removed."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = [(k, v) for k, v in parse_qsl(parts.query) if k != "mailto"]
    return str(urlunsplit(parts._replace(query=urlencode(pairs))))


def retry_after_seconds(
    header_value: str | None, now: float | None = None
) -> float | None:
    """Parse a `Retry-After` header into a number of seconds to wait.

    Accepts delta-seconds or an HTTP-date (measured from `now`, default
    `time.time()`; a past date yields `0.0`). Returns `None` when the header is
    absent or unparseable.
    """
    if header_value is None:
        return None
    value = header_value.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        return max(0.0, seconds) if math.isfinite(seconds) else None
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


class CachedJsonClient:
    """HTTP client that caches JSON GETs by URL, with 404 cached as `None`.

    `mailto`, if set, is sent as a `?mailto=...` query param but stripped from
    the cache key.

    Retry and pacing knobs (all finite and >= 0, else `ValueError`):

    - `max_retries` — extra attempts for a retryable status; `0` disables
      retrying. The last `HTTPError` propagates.
    - `backoff_base` — first backoff step in seconds without a `Retry-After`;
      doubles each step, plus up to half a step of jitter.
    - `max_wait` — ceiling on any single wait.
    - `min_interval` — minimum seconds between request starts. Cache hits are
      never paced.

    `fallback_cache`, with `cache_path`, is a read-only layer consulted on a
    miss (see `LayeredCache`). `sleep`, `monotonic`, and `clock` are test seams.

    A subclass sets `rate_limit_key` and `rate_limit_probe` to opt into quota
    tracking.
    """

    # Cache key of the quota snapshot, and the cheapest request that returns the
    # headers.
    rate_limit_key: str | None = None
    rate_limit_probe: str | None = None

    def __init__(
        self,
        cache: JsonlCache | LayeredCache | None = None,
        cache_path: str | Path | None = None,
        mailto: str | None = None,
        user_agent: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        max_wait: float = DEFAULT_MAX_WAIT,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
        fallback_cache: str | Path | None = None,
    ) -> None:
        if cache is None and cache_path is not None:
            cache = JsonlCache(cache_path)
            if fallback_cache is not None:
                cache = LayeredCache(cache, JsonlCache(fallback_cache))
        self.cache: JsonlCache | LayeredCache | None = cache
        self.mailto = mailto
        self.timeout = timeout
        for name, value in (
            ("max_retries", max_retries),
            ("backoff_base", backoff_base),
            ("max_wait", max_wait),
            ("min_interval", min_interval),
        ):
            validate_knob(name, value)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_wait = max_wait
        self.min_interval = min_interval
        self.retries = 0  # retried requests so far
        self.network_calls = 0  # requests that reached the network
        self._sleep = sleep
        self._monotonic = monotonic
        self._clock = clock
        self._last_request_at: float | None = None
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or _default_user_agent()
        # Last quota state seen (`{"headers": ..., "ts": ...}`), seeded from the cache.
        self.rate_limit: dict[str, Any] | None = self._load_rate_limit()
        self._rate_limit_persisted: dict[str, Any] | None = None
        self._rate_limit_persisted_at: float | None = None

    def _cache_key(self, url: str) -> str:
        return _strip_mailto(url)

    def _request_url(self, url: str) -> str:
        if not self.mailto:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{urlencode({'mailto': self.mailto})}"

    def _pace(self) -> None:
        """Sleep off whatever remains of `min_interval` since the last send."""
        if self.min_interval <= 0:
            return
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = self.min_interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()  # a real sleep can overshoot
        self._last_request_at = now

    def _retry_wait(self, response: requests.Response, attempt: int) -> float:
        """Seconds to wait before retrying `response`, `attempt` retries in."""
        wait = retry_after_seconds(response.headers.get("Retry-After"), self._clock())
        if wait is None:
            # Cap the exponent so the float multiplication cannot overflow.
            step = self.backoff_base * 2 ** min(attempt, 60)
            wait = step + random.uniform(0, step / 2)
        return min(wait, self.max_wait)

    def _fetch(self, url: str) -> requests.Response:
        """GET `url`, pacing by `min_interval` and retrying transient statuses.

        Returns the final response; the caller raises on an error status.
        """
        attempt = 0
        while True:
            self._pace()
            response = self.session.get(url, timeout=self.timeout)
            self._capture_rate_limit(response)
            status = response.status_code
            if status not in RETRY_STATUSES or attempt >= self.max_retries:
                return response
            wait = self._retry_wait(response, attempt)
            attempt += 1
            self.retries += 1
            log.warning(
                "%s from %s: retry %d/%d in %.1fs",
                status,
                urlsplit(url).netloc,
                attempt,
                self.max_retries,
                wait,
            )
            self._sleep(wait)

    def _load_rate_limit(self) -> dict[str, Any] | None:
        """The snapshot a previous run stored, if this source keeps one.

        Stored under a URL-shaped key so `cache merge` routes it like any other
        row (newest `ts` wins) and `citefinder drift` skips it.
        """
        if self.cache is None or self.rate_limit_key is None:
            return None
        stored = self.cache.get(self.rate_limit_key)
        return stored if isinstance(stored, dict) else None

    def _capture_rate_limit(self, response: requests.Response) -> None:
        """Record the quota headers on `response`, if it carries any."""
        try:
            items = list(response.headers.items())
        except (AttributeError, TypeError):  # a test double without headers
            return
        headers = {
            name.lower(): value
            for name, value in items
            if name.lower().startswith(RATE_LIMIT_HEADER_PREFIXES)
        }
        if not headers:
            return
        self.rate_limit = {"headers": headers, "ts": self._clock()}
        self._persist_rate_limit()

    def _persist_rate_limit(self, force: bool = False) -> None:
        """Write the snapshot to the cache, throttled unless `force`."""
        if self.cache is None or self.rate_limit_key is None:
            return
        if self.rate_limit is None:
            return
        # Checked before `force` so a refresh stores one row, not two.
        if self.rate_limit is self._rate_limit_persisted:
            return
        now = self._clock()
        if (
            not force
            and self._rate_limit_persisted_at is not None
            and now - self._rate_limit_persisted_at < RATE_LIMIT_PERSIST_INTERVAL
        ):
            return
        self.cache.put(self.rate_limit_key, self.rate_limit)
        self._rate_limit_persisted = self.rate_limit
        self._rate_limit_persisted_at = now

    def refresh_rate_limit(self) -> dict[str, Any] | None:
        """Fetch the quota headers on demand, and persist what comes back.

        Uses `rate_limit_probe`; the response body is discarded, not cached.
        """
        if self.rate_limit_probe is None:
            return self.rate_limit
        self.network_calls += 1
        self._fetch(self._request_url(self.rate_limit_probe))
        self._persist_rate_limit(force=True)
        return self.rate_limit

    def _get(self, url: str) -> Any | None:
        cache_key = self._cache_key(url)
        if self.cache is not None and cache_key in self.cache:
            return self.cache.get(cache_key)
        self.network_calls += 1
        response = self._fetch(self._request_url(url))
        if response.status_code == 404:
            value: Any | None = None
        else:
            response.raise_for_status()
            value = self._decode(response)
        if self.cache is not None:
            self.cache.put(cache_key, value)
        return value

    def _decode(self, response: requests.Response) -> Any:
        """The JSON-serializable value a 2xx `response` caches as.

        JSON by default; a source with another format (MARCXML) overrides this
        to return plain dicts and lists.
        """
        return response.json()
