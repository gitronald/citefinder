"""Open Library client with optional caching.

Open Library (https://openlibrary.org) is the book counterpart of Crossref:
an edition record per ISBN carrying title, subtitle, publishers, places of
publication, and — the useful part — the Library of Congress Control Number
(LCCN) that leads to the cataloging record printed on the copyright page
(see `citefinder.loc`).

Two lookup styles, each cached separately:

- `lookup_isbn(isbn)` — one edition via `/isbn/{isbn}.json`; a 404 is
  cached as `None`.
- `search(title, author)` — `/search.json`, for a book entry with no ISBN.
  Hits carry candidate ISBNs to confirm, never to apply.

Open Library has no polite pool: it asks callers for a descriptive
User-Agent with contact details and a modest request rate, so `mailto`
goes into the User-Agent rather than the query string, and the client is
paced at one request per second by default.
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

# Open Library documents no hard limit but asks for "modest" rates; one
# request per second keeps a long `verify --books` run unremarkable.
OPENLIBRARY_MIN_INTERVAL = 1.0

# `source_records` prefixes that mean a retailer feed rather than a library
# catalog. An edition fed only by these is a lead: the data is usually right
# but nobody with the book in hand has checked it.
RETAILER_PREFIXES = ("amazon:", "bwb:", "promise:")

# What a search hit is asked to carry; `isbn` is the point of the request.
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

    A bib `isbn` field may list several (`978-0-374-61932-9, 0374619328`);
    only the first is kept — that is the one the citation identifies the
    book by.
    """
    first = re.split(r"[,;]", isbn.strip(), maxsplit=1)[0]
    return _ISBN_SEPARATORS.sub("", first).upper()


def retailer_only(source_records: list[str] | None) -> bool:
    """Whether every `source_records` entry names a retailer feed.

    An empty or missing list is *not* retailer-only: it says nothing about
    provenance, and the record is reported as plain Open Library data.
    """
    if not source_records:
        return False
    return all(
        record.lower().startswith(RETAILER_PREFIXES) for record in source_records
    )


class OpenLibraryClient(CachedJsonClient):
    """Open Library lookups, paced to one request per second.

    `mailto` is the contact Open Library asks for; it is appended to the
    User-Agent (`citefinder/x.y (...; mailto:you@example.com)`) rather than
    sent as a query parameter, so cache keys never carry it and the base
    client's `mailto` stays unset. Every other knob behaves as documented on
    `CachedJsonClient`.
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
            user_agent = _default_user_agent()
            if mailto:
                user_agent = f"{user_agent[:-1]}; mailto:{mailto})"
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

        `/isbn/<isbn>.json` redirects to the edition's own `/books/OL…M.json`;
        the cache is keyed by the ISBN URL, so the same book asked for by
        ISBN-10 and ISBN-13 is two rows, each a complete record.
        """
        return self._get(f"{self.isbn_url(isbn)}.json")

    def lookup_author(self, key: str) -> OpenLibraryAuthor | None:
        """An author record by its `/authors/OL…A` key (as an edition lists
        them), or `None`."""
        return self._get(f"{OPENLIBRARY_BASE}{key}.json")

    def search(
        self, title: str, author: str | None = None, rows: int = 3
    ) -> list[OpenLibraryDoc]:
        """Search editions by title (and author), for a book with no ISBN.

        Each hit is a *work* with every ISBN of every edition under `isbn`,
        so a hit names candidates rather than one answer.
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
