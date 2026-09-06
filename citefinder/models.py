"""Typed shapes for the raw Crossref and OpenAlex records the package handles.

These are `TypedDict`s, not parsers: a record read from the cache or the wire
is already a dict, and stays one. The classes exist so the type checker can
see which keys a consumer reaches for, and so a reader has one place that
says what a record looks like without opening a cache file.

The model is deliberately rough and incomplete. It was pieced together from a
survey of caches written by real verify runs (a few hundred work records and
search pages per source, spanning journal articles, proceedings papers,
chapters, books, preprints, and reports) and it is expected to change as the
sources do. Rules for growing it:

- Every key is optional (`total=False`). The comment after a key records the
  share of surveyed records that carried it; `100%` means "always seen", not
  "guaranteed", and a record missing that key must not crash a consumer.
- A key whose value was sometimes JSON `null` is typed `X | None`.
- Keys seen on fewer than about 5% of records are left out unless a consumer
  reads them. Absence from the model does not mean the source never sends it.
- Add a key when a consumer starts reading it; add a nested class when a
  consumer reaches inside a dict-valued field.

`undeclared_keys` reports what a real record carries that the model does not,
so drift can be measured against any cache file rather than guessed at.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict, get_args, get_type_hints, is_typeddict

__all__ = [
    "CacheRow",
    "CrossrefAuthor",
    "CrossrefDate",
    "CrossrefEnvelope",
    "CrossrefEvent",
    "CrossrefFunder",
    "CrossrefJournalIssue",
    "CrossrefLicense",
    "CrossrefReference",
    "CrossrefSearchMessage",
    "CrossrefWork",
    "CrossrefWorkType",
    "OpenAlexAuthor",
    "OpenAlexAuthorship",
    "OpenAlexBiblio",
    "OpenAlexIds",
    "OpenAlexInstitution",
    "OpenAlexLocation",
    "OpenAlexMeta",
    "OpenAlexOpenAccess",
    "OpenAlexSearchPage",
    "OpenAlexSource",
    "OpenAlexTopic",
    "OpenAlexWork",
    "OpenAlexWorkType",
    "undeclared_keys",
]

# --- Cache ------------------------------------------------------------------


class CacheRow(TypedDict):
    """One line of a `JsonlCache` file.

    `key` is the request URL with the `mailto` stripped. A cached 404 has
    `value: None`. The file name does not guarantee the source: route by the
    host in `key` (`api.crossref.org` / `api.openalex.org`) when reading a
    cache by hand, because a misdirected record has been seen in practice.
    """

    key: str
    value: Any
    ts: float


# --- Crossref ---------------------------------------------------------------

CrossrefWorkType = Literal[
    "journal-article",
    "proceedings-article",
    "book-chapter",
    "book",
    "monograph",
    "edited-book",
    "reference-book",
    "posted-content",
    "report",
    "other",
]
"""Values of `CrossrefWork["type"]` seen in the survey. Crossref defines more."""

CrossrefDate = TypedDict(
    "CrossrefDate",
    {
        # `[[year, month, day]]`, shorter when the source knows less; the
        # inner list can be `[None]` on `issued` for undated works.
        "date-parts": list[list[int | None]],
        "date-time": str,  # ISO 8601; on created/deposited/indexed only
        "timestamp": int,  # ms since epoch; on created/deposited/indexed only
        "version": str,  # on `indexed` only
    },
    total=False,
)

CrossrefAuthor = TypedDict(
    "CrossrefAuthor",
    {
        # A person carries `family` (+ `given`); an organisation carries
        # `name` instead. Neither is guaranteed.
        "family": str,  # 99%
        "given": str,  # 99%
        "name": str,  # 1%, organisational authors
        "suffix": str,
        "sequence": Literal["first", "additional"],  # 99%
        "affiliation": list[dict[str, Any]],  # 99%, usually `[]`
        "ORCID": str,  # 33%, as a URL
        "authenticated-orcid": bool,  # 33%
        "role": list[dict[str, Any]],  # 60%
    },
    total=False,
)

CrossrefReference = TypedDict(
    "CrossrefReference",
    {
        "key": str,  # 68%
        "DOI": str,  # 34%
        "doi-asserted-by": Literal["publisher", "crossref"],  # 34%
        "unstructured": str,  # 27%
        "year": str,  # 26%
        "author": str,  # 25%
        "first-page": str,  # 15%
        "journal-title": str,  # 13%
        "volume": str,  # 13%
        "article-title": str,  # 10%
        "volume-title": str,  # 10%
        "issue": str,  # 7%
        "edition": str,
        "series-title": str,
    },
    total=False,
)

CrossrefLicense = TypedDict(
    "CrossrefLicense",
    {
        "URL": str,
        "content-version": str,  # "vor" | "am" | "tdm" | "unspecified"
        "delay-in-days": int,
        "start": CrossrefDate,
    },
    total=False,
)

CrossrefFunder = TypedDict(
    "CrossrefFunder",
    {
        "name": str,
        "DOI": str,
        "doi-asserted-by": str,
        "id": list[dict[str, Any]],
        "award": list[str],
        "award-info": list[dict[str, Any]],
    },
    total=False,
)


class CrossrefEvent(TypedDict, total=False):
    name: str
    location: str
    acronym: str
    sponsor: list[str]
    number: str
    start: CrossrefDate
    end: CrossrefDate


CrossrefJournalIssue = TypedDict(
    "CrossrefJournalIssue",
    {
        "issue": str,
        "published-print": CrossrefDate,
        "published-online": CrossrefDate,
    },
    total=False,
)

CrossrefWork = TypedDict(
    "CrossrefWork",
    {
        # --- identity (100%)
        "DOI": str,  # bare, lower-case
        "URL": str,  # `https://doi.org/...`
        "prefix": str,
        "member": str,
        "type": CrossrefWorkType,
        "subtype": str,  # 1%, e.g. "preprint" under posted-content
        "source": str,  # "Crossref"
        "score": float,  # relevance on search hits; 1.0 on DOI lookups
        # --- titles and venue (100%; lists, often empty)
        "title": list[str],
        "subtitle": list[str],
        "short-title": list[str],
        "original-title": list[str],
        "container-title": list[str],
        "short-container-title": list[str],
        "group-title": str,  # 1%
        "publisher": str,
        "publisher-location": str,  # 24%
        "ISSN": list[str],  # 70%
        "issn-type": list[dict[str, str]],  # 70%
        "ISBN": list[str],  # 15%
        "isbn-type": list[dict[str, str]],  # 15%
        # --- people
        "author": list[CrossrefAuthor],  # 99%
        "editor": list[CrossrefAuthor],  # 1%
        "institution": list[dict[str, Any]],  # grants and theses on search hits
        # --- dates (`issued`/`created`/`published` 100%)
        "issued": CrossrefDate,
        "created": CrossrefDate,
        "deposited": CrossrefDate,
        "indexed": CrossrefDate,
        "published": CrossrefDate,
        "published-print": CrossrefDate,  # 82%
        "published-online": CrossrefDate,  # 71%
        "published-other": CrossrefDate,  # 2%
        "posted": CrossrefDate,  # 1%
        # --- location in the venue
        "volume": str,  # 65%
        "issue": str,  # 58%
        "page": str,  # 78%, "123-145"
        "article-number": str,  # 11%
        "edition-number": str,  # 9%
        "journal-issue": CrossrefJournalIssue,  # 58%
        "event": CrossrefEvent,  # 14%, proceedings
        # --- content and links
        "abstract": str,  # 45%, JATS XML
        "language": str,  # 67%
        "subject": list[str],
        "link": list[dict[str, str]],  # 80%
        "license": list[CrossrefLicense],  # 66%
        "resource": dict[str, Any],
        "content-domain": dict[str, Any],
        "update-policy": str,  # 53%
        "alternative-id": list[str],  # 63%
        # --- references and citations
        "reference": list[CrossrefReference],  # 68%
        "reference-count": int,
        "references-count": int,
        "is-referenced-by-count": int,
        "relation": dict[str, list[dict[str, Any]]],  # usually `{}`
        "funder": list[CrossrefFunder],  # 20%
        "assertion": list[dict[str, Any]],  # 37%
        "archive": list[str],  # 2%
    },
    total=False,
)

CrossrefSearchMessage = TypedDict(
    "CrossrefSearchMessage",
    {
        "items": list[CrossrefWork],
        "items-per-page": int,
        "total-results": int,
        "query": dict[str, Any],
        "facets": dict[str, Any],
    },
    total=False,
)

CrossrefEnvelope = TypedDict(
    "CrossrefEnvelope",
    {
        # A DOI lookup wraps one `CrossrefWork`; a search wraps a
        # `CrossrefSearchMessage`. `message-type` says which.
        "status": str,  # "ok"
        "message-type": Literal["work", "work-list"],
        "message-version": str,
        "message": CrossrefWork | CrossrefSearchMessage,
    },
    total=False,
)


# --- OpenAlex ---------------------------------------------------------------

OpenAlexWorkType = Literal[
    "article",
    "conference-paper",
    "preprint",
    "book",
    "book-chapter",
    "review",
    "editorial",
    "report",
    "reference-entry",
    "dissertation",
    "other",
]
"""Values of `OpenAlexWork["type"]` seen in the survey. OpenAlex defines more."""


class OpenAlexAuthor(TypedDict, total=False):
    """The profile stub embedded in an authorship. The full profile lives at
    `/authors/{id}` and is not modelled here."""

    id: str  # `https://openalex.org/A...`
    display_name: str  # the profile's canonical spelling, not the byline
    orcid: str | None


class OpenAlexInstitution(TypedDict, total=False):
    id: str
    display_name: str
    ror: str | None
    country_code: str | None
    type: str
    lineage: list[str]


class OpenAlexAuthorship(TypedDict, total=False):
    author_position: Literal["first", "middle", "last"]
    # Present on every surveyed record, but seen missing in the wild on
    # authorships OpenAlex could not link to a profile.
    author: OpenAlexAuthor
    raw_author_name: str  # what the byline printed
    raw_orcid: str | None  # 81%
    institutions: list[OpenAlexInstitution]
    affiliations: list[dict[str, Any]]
    raw_affiliation_strings: list[str]
    countries: list[str]
    is_corresponding: bool


class OpenAlexSource(TypedDict, total=False):
    id: str
    display_name: str
    issn_l: str | None
    issn: list[str] | None
    type: str  # "journal" | "repository" | "conference" | "book series" | ...
    is_oa: bool
    is_in_doaj: bool
    is_core: bool
    host_organization: str | None
    host_organization_name: str | None
    host_organization_lineage: list[str]
    host_organization_lineage_names: list[str]


class OpenAlexLocation(TypedDict, total=False):
    id: str
    source: OpenAlexSource | None  # None on 19% of primary locations
    landing_page_url: str | None
    pdf_url: str | None
    license: str | None
    license_id: str | None
    version: str | None  # "publishedVersion" | "acceptedVersion" | ...
    raw_source_name: str | None
    raw_type: str | None
    is_oa: bool
    is_accepted: bool
    is_published: bool | None


class OpenAlexBiblio(TypedDict, total=False):
    volume: str | None
    issue: str | None
    first_page: str | None
    last_page: str | None


class OpenAlexIds(TypedDict, total=False):
    openalex: str
    doi: str  # `https://doi.org/...`
    mag: str  # 48%
    pmid: str  # 10%
    pmcid: str


class OpenAlexOpenAccess(TypedDict, total=False):
    is_oa: bool
    oa_status: str  # "gold" | "green" | "hybrid" | "bronze" | "diamond" | "closed"
    oa_url: str | None
    any_repository_has_fulltext: bool | None


class OpenAlexTopic(TypedDict, total=False):
    id: str
    display_name: str
    score: float
    subfield: dict[str, str]
    field: dict[str, str]
    domain: dict[str, str]


class OpenAlexWork(TypedDict, total=False):
    """An OpenAlex work. Every key below was present on every surveyed record;
    the nullable ones are typed `| None`."""

    # --- identity
    id: str  # `https://openalex.org/W...`
    doi: str  # `https://doi.org/...`; `openalex_doi` strips it
    ids: OpenAlexIds
    type: OpenAlexWorkType
    type_crossref: str | None  # null on every surveyed record
    # --- title, venue, dates
    display_name: str
    title: str
    primary_location: OpenAlexLocation
    best_oa_location: OpenAlexLocation | None
    locations: list[OpenAlexLocation]
    locations_count: int
    host_venue: dict[str, Any]  # retired field; older cache files only
    publication_year: int
    publication_date: str  # "YYYY-MM-DD"
    biblio: OpenAlexBiblio
    language: str | None
    # --- people
    authorships: list[OpenAlexAuthorship]
    corresponding_author_ids: list[str]
    corresponding_institution_ids: list[str]
    institutions: list[dict[str, Any]]
    countries_distinct_count: int
    institutions_distinct_count: int
    # --- content
    abstract_inverted_index: dict[str, list[int]] | None  # 88% non-null
    has_fulltext: bool
    has_content: dict[str, bool]
    content_urls: dict[str, str] | None
    open_access: OpenAlexOpenAccess
    indexed_in: list[str]
    is_retracted: bool
    is_paratext: bool
    is_xpac: bool
    # --- classification
    primary_topic: OpenAlexTopic
    topics: list[OpenAlexTopic]
    keywords: list[dict[str, Any]]
    concepts: list[dict[str, Any]]
    mesh: list[dict[str, Any]]
    sustainable_development_goals: list[dict[str, Any]]
    # --- citations and funding
    cited_by_count: int
    counts_by_year: list[dict[str, int]]
    fwci: float | None
    citation_normalized_percentile: dict[str, Any] | None
    cited_by_percentile_year: dict[str, int] | None
    referenced_works: list[str]
    referenced_works_count: int
    related_works: list[str]
    funders: list[dict[str, Any]]
    awards: list[dict[str, Any]]
    apc_list: dict[str, Any] | None
    apc_paid: dict[str, Any] | None
    # --- bookkeeping
    created_date: str
    updated_date: str
    relevance_score: float  # search hits only


class OpenAlexMeta(TypedDict, total=False):
    count: int
    page: int
    per_page: int
    db_response_time_ms: int
    groups_count: int | None
    # Newer fields; absent from older cached pages.
    x_query: dict[str, Any]
    cost_usd: float


class OpenAlexSearchPage(TypedDict, total=False):
    meta: OpenAlexMeta
    results: list[OpenAlexWork]
    group_by: list[dict[str, Any]]


# --- Drift check ------------------------------------------------------------


def _nested_model(hint: Any) -> type | None:
    """The TypedDict class inside a hint like `X`, `X | None`, or `list[X]`."""
    if is_typeddict(hint):
        return hint
    for arg in get_args(hint):
        found = _nested_model(arg)
        if found is not None:
            return found
    return None


def undeclared_keys(record: Any, model: type, prefix: str = "") -> list[str]:
    """Dotted paths in `record` that `model` does not declare.

    Walks dict-valued and list-of-dict-valued keys whose declared type names a
    nested TypedDict; other values are not inspected. List elements are
    reported with `[]` (`author[].suffix`). The result is sorted and unique,
    so a batch of records can be folded into one set for a drift report:

        drift: set[str] = set()
        for row in rows:
            drift.update(undeclared_keys(row["value"], OpenAlexWork))
    """
    if not isinstance(record, dict):
        return []
    hints = get_type_hints(model)
    out: set[str] = set()
    for key, value in record.items():
        path = f"{prefix}{key}"
        if key not in hints:
            out.add(path)
            continue
        nested = _nested_model(hints[key])
        if nested is None:
            continue
        if isinstance(value, dict):
            out.update(undeclared_keys(value, nested, f"{path}."))
        elif isinstance(value, list):
            for item in value:
                out.update(undeclared_keys(item, nested, f"{path}[]."))
    return sorted(out)
