"""OpenAlex API client with optional caching.

OpenAlex (https://api.openalex.org) merges Crossref + Unpaywall + ORCID + ROR
+ repository sources, so it often has metadata that Crossref alone is missing
(abstracts, full author lists, affiliations) and indexes records that Crossref
404s (arXiv preprints, repository deposits).

Two lookup styles, each cached separately:

- `lookup_doi(doi)` — single-DOI metadata via `/works/doi:{doi}`
- `search(query, rows)` — free-text search across titles and abstracts

Caching is keyed by URL with the polite-pool `mailto` stripped, so changing
the email used for a polite-pool request does not invalidate prior cache
entries. Negative results (404) are cached as `None`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from citefinder._base import DEFAULT_TIMEOUT, CachedJsonClient
from citefinder.cache import JsonlCache

OPENALEX_BASE = "https://api.openalex.org"
API_KEY_ENV_VAR = "OPENALEX_API_KEY"


def reconstruct_abstract(work: dict[str, Any]) -> str | None:
    """Reassemble OpenAlex's `abstract_inverted_index` into plain text.

    OpenAlex stores abstracts as `{word: [positions, ...]}` rather than a
    string, ostensibly to sidestep copyright concerns. Invert the mapping to
    recover the abstract.
    """
    index = work.get("abstract_inverted_index")
    if not index:
        return None
    positions = sorted((i, word) for word, idxs in index.items() for i in idxs)
    if not positions:
        return None
    return " ".join(word for _, word in positions)


def _strip_mailto(url: str) -> str:
    """Return `url` with any `mailto` query param removed."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = [(k, v) for k, v in parse_qsl(parts.query) if k != "mailto"]
    return str(urlunsplit(parts._replace(query=urlencode(pairs))))


class OpenAlexClient(CachedJsonClient):
    def __init__(
        self,
        cache: JsonlCache | None = None,
        cache_path: str | Path | None = None,
        mailto: str | None = None,
        api_key: str | None = None,
        user_agent: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(
            cache=cache,
            cache_path=cache_path,
            user_agent=user_agent,
            timeout=timeout,
        )
        self.mailto = mailto
        # Falls back to env var so users can `export OPENALEX_API_KEY=...` (or
        # set it in a `.env` file the CLI loads at startup) without threading
        # the key through every call site.
        self.api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if self.api_key:
            # Header (not query param) so the key never lands in cache keys,
            # logs, or HTTP referer trails.
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def _cache_key(self, url: str) -> str:
        return _strip_mailto(url)

    def _request_url(self, url: str) -> str:
        if not self.mailto:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{urlencode({'mailto': self.mailto})}"

    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        """Fetch OpenAlex metadata for a DOI. Returns None if not found."""
        return self._get(f"{OPENALEX_BASE}/works/doi:{doi}")

    def search(self, query: str, rows: int = 3) -> list[dict[str, Any]]:
        """Search OpenAlex by free-text query (title + abstract)."""
        params = {"search": query, "per-page": str(rows)}
        payload = self._get(f"{OPENALEX_BASE}/works?{urlencode(params)}")
        if payload is None:
            return []
        return payload.get("results", [])
