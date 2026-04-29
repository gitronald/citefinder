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

from typing import Any
from urllib.parse import urlencode

from citefinder._base import CachedJsonClient

CROSSREF_BASE = "https://api.crossref.org"


def is_arxiv_doi(doi: str) -> bool:
    """Whether a DOI is an arXiv-issued DOI.

    arXiv mints DOIs under the `10.48550` prefix (e.g.
    `10.48550/arXiv.2410.21554`). Crossref does not index these, so callers
    should route them to a source that does (OpenAlex, arXiv API).
    """
    return doi.lower().startswith("10.48550/arxiv.")


class CrossrefClient(CachedJsonClient):
    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        """Fetch metadata for a single DOI. Returns None if not found."""
        payload = self._get(f"{CROSSREF_BASE}/works/{doi}")
        if payload is None:
            return None
        return payload.get("message")

    def search_bibliographic(self, query: str, rows: int = 3) -> list[dict[str, Any]]:
        """Search Crossref by free-form bibliographic query."""
        params = {"query.bibliographic": query, "rows": str(rows)}
        payload = self._get(f"{CROSSREF_BASE}/works?{urlencode(params)}")
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
