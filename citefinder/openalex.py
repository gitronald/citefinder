"""OpenAlex API client with optional caching.

Looks up works by DOI, free-text search, or title-only search. Cache keys
drop the polite-pool `mailto`; 404s are cached as `None`.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlencode

from citefinder._base import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_WAIT,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_TIMEOUT,
    CachedJsonClient,
    _doi_path,
)
from citefinder.cache import JsonlCache, LayeredCache
from citefinder.models import OpenAlexWork

OPENALEX_BASE = "https://api.openalex.org"
API_KEY_ENV_VAR = "OPENALEX_API_KEY"

# Characters reserved by OpenAlex `filter=` syntax (a value containing them
# returns HTTP 400); `?` is stripped defensively.
_FILTER_RESERVED_RE = re.compile(r"[,:|!?]")

__all__ = [
    "API_KEY_ENV_VAR",
    "DEFAULT_MIN_INTERVAL",
    "OPENALEX_BASE",
    "OpenAlexClient",
    "datacite_note",
    "datacite_registrar",
    "is_arxiv_doi",
    "normalize_title_query",
    "reconstruct_abstract",
]


# DOI prefixes registered with DataCite, which Crossref never indexes.
DATACITE_PREFIXES = {
    "10.48550": "arXiv",
    "10.5281": "Zenodo",
}


def datacite_registrar(doi: str) -> str | None:
    """The DataCite repository that minted `doi`, or `None`.

    Matches the prefix (case-insensitive), e.g. `10.48550/arXiv.2410.21554`
    -> `"arXiv"`. `None` means an unknown prefix, not a Crossref DOI.
    """
    prefix, sep, _ = doi.strip().lower().partition("/")
    return DATACITE_PREFIXES.get(prefix) if sep else None


def datacite_note(registrar: str) -> str:
    """Why Crossref has no record of a DOI that `registrar` minted."""
    return f"{registrar} DOI registered with DataCite, not Crossref"


def is_arxiv_doi(doi: str) -> bool:
    """Whether a DOI is an arXiv-issued DOI (`10.48550` prefix)."""
    return datacite_registrar(doi) == "arXiv"


def reconstruct_abstract(work: OpenAlexWork) -> str | None:
    """Reassemble OpenAlex's `abstract_inverted_index` into plain text.

    OpenAlex stores abstracts as `{word: [positions, ...]}`; this inverts
    the mapping.
    """
    index = work.get("abstract_inverted_index")
    if not index:
        return None
    positions = sorted((i, word) for word, idxs in index.items() for i in idxs)
    if not positions:
        return None
    return " ".join(word for _, word in positions)


def normalize_title_query(title: str) -> str:
    """Prepare a title for OpenAlex's `filter=title.search:` syntax.

    The title index stores curly apostrophes (U+2019), so straight ones are
    remapped; filter-reserved punctuation is replaced with spaces.
    """
    title = title.replace("'", "’")
    title = _FILTER_RESERVED_RE.sub(" ", title)
    return re.sub(r"\s+", " ", title).strip()


class OpenAlexClient(CachedJsonClient):
    # A single-entity lookup costs no credits, so probing the budget is free.
    rate_limit_key = f"{OPENALEX_BASE}/__ratelimit"
    rate_limit_probe = f"{OPENALEX_BASE}/works/W2741809807"

    def __init__(
        self,
        cache: JsonlCache | LayeredCache | None = None,
        cache_path: str | Path | None = None,
        mailto: str | None = None,
        api_key: str | None = None,
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
        # Falls back to the env var so callers need not thread the key through.
        self.api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if self.api_key:
            # A header, not a query param, so the key stays out of cache keys and logs.
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def lookup_doi(self, doi: str) -> OpenAlexWork | None:
        """OpenAlex metadata for a DOI, or `None` if not found."""
        return self._get(f"{OPENALEX_BASE}/works/doi:{_doi_path(doi)}")

    def search(self, query: str, rows: int = 3) -> list[OpenAlexWork]:
        """Search OpenAlex by free-text query (title + abstract)."""
        params = {"search": query, "per-page": str(rows)}
        payload = self._get(f"{OPENALEX_BASE}/works?{urlencode(params)}")
        if payload is None:
            return []
        return payload.get("results", [])

    def search_title(self, title: str, rows: int = 3) -> list[OpenAlexWork]:
        """Search OpenAlex by title only (`filter=title.search:`).

        Less noisy than `search`, which also matches abstracts. The title is
        passed through `normalize_title_query` first.
        """
        normalized = normalize_title_query(title)
        if not normalized:
            return []
        url = (
            f"{OPENALEX_BASE}/works?filter=title.search:{quote(normalized)}"
            f"&per-page={rows}"
        )
        payload = self._get(url)
        if payload is None:
            return []
        return payload.get("results", [])
