"""Open Library client with optional caching.

Looks up book editions by ISBN (`/isbn/{isbn}.json`) and searches by title
and author for entries with no ISBN. `mailto` goes into the User-Agent, and
requests are paced at one per second.
"""

from __future__ import annotations

import re
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
)
from citefinder.cache import JsonlCache, LayeredCache
from citefinder.models import OpenLibraryAuthor, OpenLibraryDoc, OpenLibraryEdition

OPENLIBRARY_BASE = "https://openlibrary.org"

# Open Library asks for modest request rates.
OPENLIBRARY_MIN_INTERVAL = 1.0

# `source_records` prefixes that mean a retailer feed rather than a library catalog.
RETAILER_PREFIXES = ("amazon:", "bwb:", "promise:")

_SEARCH_FIELDS = "key,title,subtitle,author_name,first_publish_year,isbn,publisher"

__all__ = [
    "OPENLIBRARY_BASE",
    "OPENLIBRARY_MIN_INTERVAL",
    "RETAILER_PREFIXES",
    "OpenLibraryClient",
    "normalize_isbn",
    "retailer_only",
]

_ISBN_SEPARATORS = re.compile(r"[\s-]+")


def normalize_isbn(isbn: str) -> str:
    """`isbn` with hyphens and spaces removed and a check digit `x` uppercased.

    Only the first of several listed ISBNs is kept.
    """
    first = re.split(r"[,;]", isbn.strip(), maxsplit=1)[0]
    return _ISBN_SEPARATORS.sub("", first).upper()


def retailer_only(source_records: list[str] | None) -> bool:
    """Whether every `source_records` entry names a retailer feed.

    An empty or missing list is not retailer-only.
    """
    if not source_records:
        return False
    return all(
        record.lower().startswith(RETAILER_PREFIXES) for record in source_records
    )


class OpenLibraryClient(CachedJsonClient):
    """Open Library lookups, paced to one request per second.

    `mailto` is appended to the User-Agent, not sent as a query parameter,
    so cache keys never carry it.
    """

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
        min_interval: float = OPENLIBRARY_MIN_INTERVAL,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
        fallback_cache: str | Path | None = None,
    ) -> None:
        if user_agent is None:
            user_agent = _default_user_agent(mailto)
        super().__init__(
            cache=cache,
            cache_path=cache_path,
            mailto=None,
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
        self.contact = mailto

    @staticmethod
    def isbn_url(isbn: str) -> str:
        """The human-readable edition page for `isbn`, for reports."""
        return f"{OPENLIBRARY_BASE}/isbn/{normalize_isbn(isbn)}"

    def lookup_isbn(self, isbn: str) -> OpenLibraryEdition | None:
        """The edition record for `isbn`, or `None` when Open Library has none.

        The cache is keyed by the ISBN URL, so ISBN-10 and ISBN-13 lookups of
        one book are separate rows.
        """
        return self._get(f"{self.isbn_url(isbn)}.json")

    def lookup_author(self, key: str) -> OpenLibraryAuthor | None:
        """An author record by its `/authors/OL…A` key, or `None`."""
        return self._get(f"{OPENLIBRARY_BASE}{key}.json")

    def search(
        self, title: str, author: str | None = None, rows: int = 3
    ) -> list[OpenLibraryDoc]:
        """Search editions by title (and author), for a book with no ISBN.

        Each hit is a work listing every edition's ISBN under `isbn`, so hits
        are candidates.
        """
        params = {"title": title}
        if author:
            params["author"] = author
        params["fields"] = _SEARCH_FIELDS
        params["limit"] = str(rows)
        payload = self._get(f"{OPENLIBRARY_BASE}/search.json?{urlencode(params)}")
        if payload is None:
            return []
        return payload.get("docs", [])
