"""Shared HTTP-with-cache plumbing for Crossref and OpenAlex clients.

Both clients fetch JSON over HTTPS, treat 404 as a cached `None`, and key the
cache by URL with any polite-pool `mailto` stripped (so changing the email
on a request doesn't invalidate prior cache entries).

Transient failures — 429 (rate limited) and 502/503/504 (upstream hiccups)
— are retried with a bounded wait before the request is given up on, and
requests can be paced by a minimum interval so a long `verify` run stays
under a source's published rate limit. Nothing is written to the cache until
a 2xx or 404 arrives, so an error response is never replayed from disk.
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

from citefinder.cache import JsonlCache

log = logging.getLogger("citefinder")

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_MAX_WAIT = 60.0

# Statuses worth a second try: the rate limiter (429) and the gateway-side
# errors a busy API returns while it is overloaded or restarting. Every other
# 4xx/5xx is either the caller's fault (400, 401, 403) or not going to change
# on retry, so those raise on the first attempt.
RETRY_STATUSES = frozenset({429, 502, 503, 504})


def _default_user_agent() -> str:
    try:
        ver = version("citefinder")
    except PackageNotFoundError:
        ver = "0.0.0"
    return f"citefinder/{ver} (https://github.com/gitronald/citefinder)"


def _doi_path(doi: str) -> str:
    """`doi` made safe to interpolate into a URL path.

    Only the three characters that change URL structure are encoded: a raw
    `#` starts a fragment (nothing after it is ever sent), a raw `?` starts a
    query, and a raw `%` would be read as an existing escape. Everything else
    `requests` already transmits correctly, and leaving it alone keeps the
    cache keys built from these URLs identical for every ordinary DOI.
    """
    return doi.replace("%", "%25").replace("#", "%23").replace("?", "%3F")


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

    RFC 9110 allows two forms: delta-seconds (`"2"`) and an HTTP-date
    (`"Wed, 21 Oct 2015 07:28:00 GMT"`). For the date form the wait is
    measured from `now`, a POSIX timestamp defaulting to `time.time()`; a
    date already in the past yields `0.0`. Returns `None` when the header is
    absent or unparseable, so the caller falls back to its own backoff.
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

    `mailto`, if set, is appended as a `?mailto=...` query param on outgoing
    requests (Crossref and OpenAlex both honor this for their polite pools)
    and stripped from the cache key so the same record cached under one
    email is reused after a rotation.

    Retry and pacing knobs:

    - `max_retries` — extra attempts after the first for a 429/502/503/504
      response. `0` disables retrying. After the last attempt the original
      `HTTPError` propagates.
    - `backoff_base` — first backoff step in seconds when the response
      carries no `Retry-After`; each step doubles, plus up to half a step of
      jitter.
    - `max_wait` — ceiling on any single wait, whether from `Retry-After` or
      backoff.
    - `min_interval` — minimum seconds between the start of consecutive
      requests from this instance. Cache hits are not requests and are never
      paced.

    All four must be finite and non-negative; anything else raises
    `ValueError` here rather than from `sleep` on a later request.

    `sleep`, `monotonic`, and `clock` are test seams (`time.sleep`,
    `time.monotonic`, and `time.time` by default) so a fake clock can drive
    the retry loop without waiting.
    """

    def __init__(
        self,
        cache: JsonlCache | None = None,
        cache_path: str | Path | None = None,
        mailto: str | None = None,
        user_agent: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        max_wait: float = DEFAULT_MAX_WAIT,
        min_interval: float = 0.0,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if cache is None and cache_path is not None:
            cache = JsonlCache(cache_path)
        self.cache = cache
        self.mailto = mailto
        self.timeout = timeout
        for name, value in (
            ("max_retries", max_retries),
            ("backoff_base", backoff_base),
            ("max_wait", max_wait),
            ("min_interval", min_interval),
        ):
            # `inf`/`nan` and negatives would only surface later as an
            # OverflowError/ValueError out of `time.sleep`, mid-run.
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_wait = max_wait
        self.min_interval = min_interval
        # Number of retried requests so far — a run-level tally callers can
        # report (the `verify` CLI prints it in its summary line).
        self.retries = 0
        self._sleep = sleep
        self._monotonic = monotonic
        self._clock = clock
        self._last_request_at: float | None = None
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or _default_user_agent()

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
            step = self.backoff_base * 2**attempt
            wait = step + random.uniform(0, step / 2)
        return min(wait, self.max_wait)

    def _fetch(self, url: str) -> requests.Response:
        """GET `url`, pacing by `min_interval` and retrying transient statuses.

        Returns the final response — a 2xx/404 to be cached, or the last
        error response for the caller to raise on. Nothing here raises for
        an HTTP status, so a 429 that never clears surfaces as the same
        `HTTPError` it would have without retries.
        """
        attempt = 0
        while True:
            self._pace()
            response = self.session.get(url, timeout=self.timeout)
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

    def _get(self, url: str) -> Any | None:
        cache_key = self._cache_key(url)
        if self.cache is not None and cache_key in self.cache:
            return self.cache.get(cache_key)
        response = self._fetch(self._request_url(url))
        if response.status_code == 404:
            value: Any | None = None
        else:
            response.raise_for_status()
            value = response.json()
        if self.cache is not None:
            self.cache.put(cache_key, value)
        return value
