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

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from citefinder.cache import JsonlCache

OPENALEX_BASE = "https://api.openalex.org"
DEFAULT_TIMEOUT = 30.0


def reconstruct_abstract(work: dict[str, Any]) -> str | None:
    """Reassemble OpenAlex's `abstract_inverted_index` into plain text.

    OpenAlex stores abstracts as `{word: [positions, ...]}` rather than a
    string, ostensibly to sidestep copyright concerns. Invert the mapping to
    recover the abstract.
    """
    index = work.get("abstract_inverted_index")
    if not index:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in index.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions)


def _strip_mailto(url: str) -> str:
    """Return `url` with any `mailto` query param removed."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = [(k, v) for k, v in parse_qsl(parts.query) if k != "mailto"]
    return str(urlunsplit(parts._replace(query=urlencode(pairs))))


class OpenAlexClient:
    def __init__(
        self,
        cache: JsonlCache | None = None,
        cache_path: str | Path | None = None,
        mailto: str | None = None,
        user_agent: str = "citefinder/0.1 (https://github.com/gitronald/citefinder)",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if cache is None and cache_path is not None:
            cache = JsonlCache(cache_path)
        self.cache = cache
        self.mailto = mailto
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent

    def _polite(self, url: str) -> str:
        if not self.mailto:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{urlencode({'mailto': self.mailto})}"

    def _get(self, url: str) -> Any | None:
        cache_key = _strip_mailto(url)
        if self.cache is not None and cache_key in self.cache:
            return self.cache.get(cache_key)
        response = self.session.get(self._polite(url), timeout=self.timeout)
        if response.status_code == 404:
            value: Any | None = None
        else:
            response.raise_for_status()
            value = response.json()
        if self.cache is not None:
            self.cache.put(cache_key, value)
        return value

    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        """Fetch OpenAlex metadata for a DOI. Returns None if not found."""
        url = f"{OPENALEX_BASE}/works/doi:{doi}"
        return self._get(url)

    def search(self, query: str, rows: int = 3) -> list[dict[str, Any]]:
        """Search OpenAlex by free-text query (title + abstract)."""
        params = {"search": query, "per-page": str(rows)}
        url = f"{OPENALEX_BASE}/works?{urlencode(params)}"
        payload = self._get(url)
        if payload is None:
            return []
        return payload.get("results", [])
