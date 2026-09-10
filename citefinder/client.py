"""Crossref API client with optional caching.

Three lookup styles, each cached separately:

- `lookup_doi(doi)` — single-DOI metadata
- `search_bibliographic(query, rows)` — title/author/keyword search
- `lookup_book_chapter(book_doi, chapter)` — `{book_doi}.{NNN}` pattern

Caching is keyed by URL so that the same request returns a stable response
across sessions. Negative results (404) are cached as `None` to avoid
re-hammering Crossref for known-missing DOIs.

Pass `mailto="you@example.com"` to opt into Crossref's polite pool — sent
as a `?mailto=…` query param. The cache key strips it, so rotating the
email doesn't invalidate prior entries. Doing so also raises the default
request rate: see `CrossrefClient` below and docs/crossref.md.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from citefinder._base import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_WAIT,
    DEFAULT_TIMEOUT,
    CachedJsonClient,
    _default_user_agent,
    _doi_path,
)
from citefinder.cache import JsonlCache
from citefinder.models import CrossrefWork

CROSSREF_BASE = "https://api.crossref.org"

# Crossref advertises the rate in effect per response, in `X-Rate-Limit-Limit`
# and `X-Rate-Limit-Interval`. Measured 2026-09-09: 1 request/second for an
# anonymous caller, 3/second in the polite pool. These are the intervals that
# keep a run inside those rates rather than relying on 429 retries; both are a
# point-in-time reading of a limit Crossref adjusts, not a contract, so a
# caller that measures something different passes `min_interval` explicitly.
CROSSREF_MIN_INTERVAL = 1.0
CROSSREF_POLITE_MIN_INTERVAL = 0.34


def is_polite(mailto: str | None, user_agent: str | None = None) -> bool:
    """Whether a request carrying these would reach Crossref's polite pool.

    Crossref accepts contact information two ways — a `mailto` query param
    or a `mailto:` inside the User-Agent — and measurement confirms both
    earn the same (higher) rate, so both count here. The default User-Agent
    carries only a project URL, so a client with no `mailto` is anonymous.
    """
    if mailto:
        return True
    return "mailto:" in (user_agent or _default_user_agent()).lower()


class CrossrefClient(CachedJsonClient):
    """Crossref lookups, paced to the rate the caller is entitled to.

    `min_interval` defaults to whichever of `CROSSREF_MIN_INTERVAL` /
    `CROSSREF_POLITE_MIN_INTERVAL` matches `is_polite(mailto, user_agent)`,
    so supplying contact information speeds the client up rather than
    leaving it to discover the higher rate by hitting 429s. Passing
    `min_interval` explicitly (including `0`, unpaced) overrides both.

    Every other knob behaves as documented on `CachedJsonClient`.
    """

    # Crossref advertises only a rate, so there is no budget to spend down;
    # `rows=0` asks for the headers and no records.
    rate_limit_key = f"{CROSSREF_BASE}/__ratelimit"
    rate_limit_probe = f"{CROSSREF_BASE}/works?rows=0"

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
        min_interval: float | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if min_interval is None:
            min_interval = (
                CROSSREF_POLITE_MIN_INTERVAL
                if is_polite(mailto, user_agent)
                else CROSSREF_MIN_INTERVAL
            )
        super().__init__(
            cache=cache,
            cache_path=cache_path,
            mailto=mailto,
            user_agent=user_agent,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
            max_wait=max_wait,
            min_interval=min_interval,
            sleep=sleep,
            monotonic=monotonic,
            clock=clock,
        )

    def lookup_doi(self, doi: str) -> CrossrefWork | None:
        """Fetch metadata for a single DOI. Returns None if not found."""
        payload = self._get(f"{CROSSREF_BASE}/works/{_doi_path(doi)}")
        if payload is None:
            return None
        return payload.get("message")

    def search_bibliographic(self, query: str, rows: int = 3) -> list[CrossrefWork]:
        """Search Crossref by free-form bibliographic query."""
        params = {"query.bibliographic": query, "rows": str(rows)}
        payload = self._get(f"{CROSSREF_BASE}/works?{urlencode(params)}")
        if payload is None:
            return []
        return payload.get("message", {}).get("items", [])

    def lookup_book_chapter(
        self, book_doi: str, chapter: int | str
    ) -> CrossrefWork | None:
        """Look up a chapter using the `{book_doi}.{NNN}` convention.

        Pads numeric chapter numbers to 3 digits (the common pattern), but
        accepts a string for publishers that use a different format.
        """
        suffix = f"{int(chapter):03d}" if isinstance(chapter, int) else chapter
        return self.lookup_doi(f"{book_doi}.{suffix}")
