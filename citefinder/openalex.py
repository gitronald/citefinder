"""OpenAlex API client with optional caching.

Looks up works by DOI, free-text search, or title-only search, and author
profiles by OpenAlex author ID or ORCID. Searches authors by name and lists
the works attached to an author ID. Cache keys drop the polite-pool `mailto`;
404s are cached as `None`.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast
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
from citefinder.models import (
    CacheRow,
    OpenAlexAuthor,
    OpenAlexAuthorProfile,
    OpenAlexAuthorSearchPage,
    OpenAlexAuthorship,
    OpenAlexInstitution,
    OpenAlexSearchPage,
    OpenAlexWork,
)

OPENALEX_BASE = "https://api.openalex.org"
API_KEY_ENV_VAR = "OPENALEX_API_KEY"

# Characters reserved by OpenAlex `filter=` syntax (a value containing them
# returns HTTP 400); `?` is stripped defensively.
_FILTER_RESERVED_RE = re.compile(r"[,:|!?]")

# `A5012345678`, bare or as the `https://openalex.org/A...` URL work records
# carry (the API's own `/authors/A...` URL is accepted too).
_AUTHOR_ID_RE = re.compile(
    r"(?:https?://(?:api\.)?openalex\.org/(?:authors/)?)?(A[0-9]+)", re.IGNORECASE
)
# `[0-9]`, not `\d`, which also matches digits of other scripts.
_ORCID_RE = re.compile(
    r"(?:https?://orcid\.org/|orcid:)?([0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X])",
    re.IGNORECASE,
)

# The most results OpenAlex returns on one page.
MAX_PER_PAGE = 200

# What an author search keeps of each profile: the fields `AuthorProfile`
# reads. A whole profile is about 9 KB, most of it topics and affiliations.
AUTHOR_SEARCH_FIELDS = (
    "id",
    "orcid",
    "display_name",
    "display_name_alternatives",
    "works_count",
    "last_known_institutions",
)
# What a works list asks for. `select=` takes root keys only, so
# `_trim_author_work` cuts the rest.
AUTHOR_WORK_FIELDS = (
    "id",
    "doi",
    "display_name",
    "publication_year",
    "type",
    "authorships",
    "primary_location",
    "primary_topic",
)

__all__ = [
    "API_KEY_ENV_VAR",
    "AUTHOR_SEARCH_FIELDS",
    "AUTHOR_WORK_FIELDS",
    "DEFAULT_MIN_INTERVAL",
    "MAX_PER_PAGE",
    "OPENALEX_BASE",
    "OpenAlexClient",
    "author_path",
    "cached_author_ids",
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


def author_path(identifier: str) -> str:
    """The `/authors/<...>` path segment for an OpenAlex author ID or an ORCID.

    `A5012345678` and `https://openalex.org/A5012345678` both give
    `A5012345678`; an ORCID, bare or as its `https://orcid.org/` URL, gives
    `orcid:0000-...`. Raises `ValueError` for anything else, so a name or a
    work ID never becomes a request.
    """
    text = identifier.strip()
    if match := _AUTHOR_ID_RE.fullmatch(text):
        return match[1].upper()
    if match := _ORCID_RE.fullmatch(text):
        return f"orcid:{match[1].upper()}"
    raise ValueError(f"not an OpenAlex author ID or ORCID: {identifier!r}")


def cached_author_ids(rows: Iterable[CacheRow]) -> list[str]:
    """Distinct `author.id`s on the works in `rows`, in first-seen order.

    Reads DOI lookups (`/works/` keys) only: a search page holds candidates,
    most of them not the cited work. Authorships OpenAlex could not link to a
    profile carry no ID and are skipped, as is an `id` that is not an author's.
    """
    seen: dict[str, None] = {}
    for row in rows:
        key, work = row.get("key"), row.get("value")
        if not isinstance(key, str) or not isinstance(work, dict):
            continue
        if not key.startswith(f"{OPENALEX_BASE}/works/"):
            continue
        for authorship in work.get("authorships") or []:
            author = authorship.get("author") if isinstance(authorship, dict) else None
            author_id = author.get("id") if isinstance(author, dict) else None
            if isinstance(author_id, str) and _AUTHOR_ID_RE.fullmatch(author_id):
                seen.setdefault(author_id)
    return list(seen)


def _trim_authorship(authorship: OpenAlexAuthorship) -> OpenAlexAuthorship:
    author = authorship.get("author") or {}
    slim_author: OpenAlexAuthor = {}
    if author.get("id"):
        slim_author["id"] = author["id"]
    if author.get("display_name"):
        slim_author["display_name"] = author["display_name"]
    institutions: list[OpenAlexInstitution] = [
        {"display_name": name}
        for inst in authorship.get("institutions") or []
        if (name := inst.get("display_name"))
    ]
    slim: OpenAlexAuthorship = {"author": slim_author, "institutions": institutions}
    if authorship.get("raw_author_name"):
        slim["raw_author_name"] = authorship["raw_author_name"]
    return slim


def _trim_author_work(work: OpenAlexWork) -> OpenAlexWork:
    """`work` cut to what a comparison of two authors' works reads.

    Each author keeps an ID, the profile name, the byline, and institution
    names; the venue and the topic's field keep their names. An untrimmed
    work is about 8 KB, nearly all of it affiliation strings.
    """
    raw: dict[str, Any] = dict(work)
    kept = ("id", "doi", "display_name", "publication_year", "type")
    slim = cast(OpenAlexWork, {k: raw[k] for k in kept if raw.get(k) is not None})
    slim["authorships"] = [
        _trim_authorship(authorship) for authorship in work.get("authorships") or []
    ]
    source = (work.get("primary_location") or {}).get("source") or {}
    if source.get("display_name"):
        slim["primary_location"] = {"source": {"display_name": source["display_name"]}}
    field = (work.get("primary_topic") or {}).get("field") or {}
    if field.get("display_name"):
        slim["primary_topic"] = {"field": {"display_name": field["display_name"]}}
    return slim


def _trim_works_page(page: OpenAlexSearchPage) -> OpenAlexSearchPage:
    """A works-list page with each result cut by `_trim_author_work`."""
    results = [_trim_author_work(work) for work in page.get("results") or []]
    return {**page, "results": results}


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

    def lookup_author(self, author_id: str) -> OpenAlexAuthorProfile | None:
        """An author's OpenAlex profile, or `None` if not found.

        `author_id` is an OpenAlex author ID (`A...`, bare or as the
        `https://openalex.org/A...` URL in a work's authorships) or an ORCID
        (bare or as a URL); anything else raises `ValueError`. Every form of an
        ID shares one cache entry. An ORCID has its own, and the profile it
        returns is also stored under its ID so a later lookup by ID is a hit.
        """
        path = author_path(author_id)
        profile = self._get(f"{OPENALEX_BASE}/authors/{path}")
        if profile is not None and path.startswith("orcid:"):
            self._alias_author(profile)
        return profile

    def _alias_author(self, profile: OpenAlexAuthorProfile) -> None:
        """Store `profile` under its own ID's key, unless a record is there.

        A 404 cached for the ID is replaced: the profile just fetched is newer.
        """
        if self.cache is None:
            return
        try:
            key = f"{OPENALEX_BASE}/authors/{author_path(profile.get('id') or '')}"
        except ValueError:  # a record without a usable `id`
            return
        if self.cache.get(key) is None:
            self.cache.put(key, profile)

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

    def author_search_page(
        self, name: str, rows: int = 25, *, refresh: bool = False
    ) -> OpenAlexAuthorSearchPage:
        """One page of author profiles matching `name` (`/authors?search=`).

        OpenAlex matches every word of `name` against each profile's spellings,
        diacritics folded, so a shorter name returns more profiles. Each result
        holds `AUTHOR_SEARCH_FIELDS` only, which is what `openalex_to_author`
        reads. `meta.count` is the number of matches, which can exceed `rows`
        (at most `MAX_PER_PAGE`). A blank `name` makes no request. `refresh`
        fetches the page again in place of the cached one.
        """
        name = " ".join(name.split())
        if not name:
            return {}
        params = {
            "search": name,
            "per-page": str(rows),
            "select": ",".join(AUTHOR_SEARCH_FIELDS),
        }
        url = f"{OPENALEX_BASE}/authors?{urlencode(params)}"
        return self._get(url, refresh=refresh) or {}

    def search_authors(self, name: str, rows: int = 25) -> list[OpenAlexAuthorProfile]:
        """Author profiles matching `name`, best match first.

        The results of `author_search_page`: each is a profile with its summary
        fields only.
        """
        return self.author_search_page(name, rows).get("results", [])

    def author_works_page(
        self,
        author_id: str,
        rows: int = MAX_PER_PAGE,
        page: int = 1,
        *,
        refresh: bool = False,
    ) -> OpenAlexSearchPage:
        """One page of the works attached to an author ID (`filter=author.id:`).

        `author_id` is an OpenAlex author ID, bare or as a URL; an ORCID raises
        `ValueError`. Each work is cut by `_trim_author_work` before it is
        cached, so it holds an ID, DOI, title, year, type, each author's names
        and institutions, the venue name, and the topic's field. `meta.count`
        is the number of works across every page.
        """
        path = author_path(author_id)
        if path.startswith("orcid:"):
            raise ValueError(f"not an OpenAlex author ID: {author_id!r}")
        url = (
            f"{OPENALEX_BASE}/works?filter=author.id:{path}"
            f"&select={','.join(AUTHOR_WORK_FIELDS)}&per-page={rows}&page={page}"
        )
        return self._get(url, refresh=refresh, trim=_trim_works_page) or {}

    def author_works(
        self, author_id: str, rows: int = MAX_PER_PAGE
    ) -> list[OpenAlexWork]:
        """The first `rows` works attached to an author ID.

        The results of the first `author_works_page`; page through that method
        for an author with more.
        """
        return self.author_works_page(author_id, rows).get("results", [])
