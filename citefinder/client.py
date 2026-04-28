"""Crossref API client with optional caching.

Three lookup styles, each cached separately:

- `lookup_doi(doi)` — single-DOI metadata
- `search_bibliographic(query, rows)` — title/author/keyword search
- `lookup_book_chapter(book_doi, chapter)` — `{book_doi}.{NNN}` pattern

Caching is keyed by URL so that the same request returns a stable response
across sessions. Negative results (404) are cached as `None` to avoid
re-hammering Crossref for known-missing DOIs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from citefinder.cache import JsonlCache

CROSSREF_BASE = "https://api.crossref.org"
DEFAULT_TIMEOUT = 30.0


def is_arxiv_doi(doi: str) -> bool:
    """Whether a DOI is an arXiv-issued DOI.

    arXiv mints DOIs under the `10.48550` prefix (e.g.
    `10.48550/arXiv.2410.21554`). Crossref does not index these, so callers
    should route them to a source that does (OpenAlex, arXiv API).
    """
    return doi.lower().startswith("10.48550/arxiv.")


class CrossrefClient:
    def __init__(
        self,
        cache: JsonlCache | None = None,
        cache_path: str | Path | None = None,
        user_agent: str = "citefinder/0.1 (https://github.com/gitronald/citefinder)",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if cache is None and cache_path is not None:
            cache = JsonlCache(cache_path)
        self.cache = cache
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent

    def _get(self, url: str) -> Any | None:
        if self.cache is not None and url in self.cache:
            return self.cache.get(url)
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code == 404:
            value: Any | None = None
        else:
            response.raise_for_status()
            value = response.json()
        if self.cache is not None:
            self.cache.put(url, value)
        return value

    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        """Fetch metadata for a single DOI. Returns None if not found."""
        url = f"{CROSSREF_BASE}/works/{doi}"
        payload = self._get(url)
        if payload is None:
            return None
        return payload.get("message")

    def search_bibliographic(self, query: str, rows: int = 3) -> list[dict[str, Any]]:
        """Search Crossref by free-form bibliographic query."""
        params = {"query.bibliographic": query, "rows": str(rows)}
        url = f"{CROSSREF_BASE}/works?{urlencode(params)}"
        payload = self._get(url)
        if payload is None:
            return []
        return payload.get("message", {}).get("items", [])

    def lookup_book_chapter(
        self, book_doi: str, chapter: int | str
    ) -> dict[str, Any] | None:
        """Look up a chapter using the `{book_doi}.{NNN}` convention.

        Pads numeric chapter numbers to 3 digits (the common pattern), but
        accepts a string for publishers that use a different format.
        """
        suffix = f"{int(chapter):03d}" if isinstance(chapter, int) else chapter
        return self.lookup_doi(f"{book_doi}.{suffix}")
