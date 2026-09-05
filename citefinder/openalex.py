"""OpenAlex API client with optional caching.

OpenAlex (https://api.openalex.org) merges Crossref + Unpaywall + ORCID + ROR
+ repository sources, so it often has metadata that Crossref alone is missing
(abstracts, full author lists, affiliations) and indexes records that Crossref
404s (arXiv preprints, repository deposits).

Three lookup styles, each cached separately:

- `lookup_doi(doi)` — single-DOI metadata via `/works/doi:{doi}`
- `search(query, rows)` — free-text search across titles and abstracts
- `search_title(title, rows)` — title-only search via `filter=title.search:`

Caching is keyed by URL with the polite-pool `mailto` stripped (handled by
the base client), so changing the email used for a request does not
invalidate prior cache entries. Negative results (404) are cached as `None`.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from citefinder._base import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_WAIT,
    DEFAULT_TIMEOUT,
    CachedJsonClient,
    _doi_path,
    _strip_mailto,
)
from citefinder.cache import JsonlCache

OPENALEX_BASE = "https://api.openalex.org"
API_KEY_ENV_VAR = "OPENALEX_API_KEY"

# OpenAlex documents a limit of 10 requests per second (plus a daily cap), so
# consecutive uncached requests from one client are spaced at least this far
# apart by default. Crossref publishes no fixed rate, so its default is 0.
DEFAULT_MIN_INTERVAL = 0.1

# OpenAlex `filter=` syntax reserves these characters (`,` separates filters,
# `|` is OR, `:` separates field from value, `!` is negation). Including them
# in the value returns HTTP 400, so they're stripped before quoting. `?` is
# stripped too as a defense against URL-parsing oddities.
_FILTER_RESERVED_RE = re.compile(r"[,:|!?]")

__all__ = [
    "API_KEY_ENV_VAR",
    "DEFAULT_MIN_INTERVAL",
    "OPENALEX_BASE",
    "OpenAlexClient",
    "_strip_mailto",
    "is_arxiv_doi",
    "reconstruct_abstract",
]


def is_arxiv_doi(doi: str) -> bool:
    """Whether a DOI is an arXiv-issued DOI.

    arXiv mints DOIs under the `10.48550` prefix (e.g.
    `10.48550/arXiv.2410.21554`). Crossref does not index these, so callers
    should route them to a source that does (OpenAlex, arXiv API).
    """
    return doi.lower().startswith("10.48550/arxiv.")


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


def _normalize_title_query(title: str) -> str:
    """Prepare a title for OpenAlex's `filter=title.search:` syntax.

    Two known quirks:

    - OpenAlex's title index stores curly right-single-quotes (U+2019), not
      straight ASCII apostrophes. A query for `Backstabber's` returns zero
      hits while `Backstabber's` finds the paper. Remap before sending.
    - The `filter=` syntax reserves `,`, `:`, `|`, and `!`; including them
      returns HTTP 400. Strip them out — `title.search` is fuzzy, so missing
      punctuation doesn't hurt recall.
    """
    title = title.replace("'", "’")
    title = _FILTER_RESERVED_RE.sub(" ", title)
    return re.sub(r"\s+", " ", title).strip()


class OpenAlexClient(CachedJsonClient):
    def __init__(
        self,
        cache: JsonlCache | None = None,
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
        )
        # Falls back to env var so users can `export OPENALEX_API_KEY=...` (or
        # set it in a `.env` file the CLI loads at startup) without threading
        # the key through every call site.
        self.api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if self.api_key:
            # Header (not query param) so the key never lands in cache keys,
            # logs, or HTTP referer trails.
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        """Fetch OpenAlex metadata for a DOI. Returns None if not found."""
        return self._get(f"{OPENALEX_BASE}/works/doi:{_doi_path(doi)}")

    def search(self, query: str, rows: int = 3) -> list[dict[str, Any]]:
        """Search OpenAlex by free-text query (title + abstract)."""
        params = {"search": query, "per-page": str(rows)}
        payload = self._get(f"{OPENALEX_BASE}/works?{urlencode(params)}")
        if payload is None:
            return []
        return payload.get("results", [])

    def search_title(self, title: str, rows: int = 3) -> list[dict[str, Any]]:
        """Search OpenAlex by title only (`filter=title.search:`).

        Title-restricted matching is the right shape for citation
        verification: the default `?search=` query runs full-text against
        title + abstract and returns unrelated noise for typical
        author+title+year inputs. The query is normalized first to handle
        OpenAlex's curly-apostrophe quirk and to drop filter-reserved
        punctuation that would 400 the request.
        """
        normalized = _normalize_title_query(title)
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
