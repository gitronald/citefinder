"""Crossref API client: DOI lookup, bibliographic search, and book chapters.

Responses are cached by URL, with 404s cached as `None`. Pass `mailto` to use
Crossref's polite pool; the cache key strips it, so changing it keeps the cache.
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
from citefinder.cache import JsonlCache, LayeredCache
from citefinder.models import CrossrefWork

CROSSREF_BASE = "https://api.crossref.org"

# Crossref's observed limits (1 request/second anonymous, 3/second polite).
# It adjusts them, so a caller can pass `min_interval` explicitly.
CROSSREF_MIN_INTERVAL = 1.0
CROSSREF_POLITE_MIN_INTERVAL = 0.34


def is_polite(mailto: str | None, user_agent: str | None = None) -> bool:
    """Whether a request reaches Crossref's polite pool.

    Either a `mailto` or a `mailto:` in the User-Agent counts.
    """
    if mailto:
        return True
    return "mailto:" in (user_agent or _default_user_agent()).lower()


class CrossrefClient(CachedJsonClient):
    """Crossref lookups, paced to the caller's rate.

    `min_interval` defaults to the polite or anonymous interval per
    `is_polite`; passing it explicitly (including `0`) overrides that.
    """

    # `rows=0` returns the rate-limit headers and no records.
    rate_limit_key = f"{CROSSREF_BASE}/__ratelimit"
    rate_limit_probe = f"{CROSSREF_BASE}/works?rows=0"

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
        min_interval: float | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
        fallback_cache: str | Path | None = None,
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
            fallback_cache=fallback_cache,
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
        """Look up a chapter DOI as `{book_doi}.{NNN}`.

        An int is zero-padded to 3 digits; a string is used as given.
        """
        suffix = f"{int(chapter):03d}" if isinstance(chapter, int) else chapter
        return self.lookup_doi(f"{book_doi}.{suffix}")
