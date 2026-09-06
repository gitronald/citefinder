---
id: 14
slug: source-record-models
status: active
branch: feature/source-record-models
created: 2026-09-05T17:54:22-07:00
concluded:
pr: https://github.com/gitronald/citefinder/pull/54
---

# Model the Crossref and OpenAlex record shapes

## Plan

### Problem

Every Crossref and OpenAlex record in the package is `dict[str, Any]`. The
adapters, `verify.Source`, the clients, and the skill's offline recipes all
index into the raw JSON by string key, and the only written record of those
shapes is a four-row field map in the README. Nothing checks that a key a
function reaches for exists, or what type it carries, and every new consumer
(the given-name recipe in plan 012, the author profiles in plan 013) rediscovers
the shape by reading cache files.

The sources' schemas are large and they change. A complete model is not the
goal. The goal is a rough, deliberately incomplete model of the fields that
are actually observed, which the package can lean on now and grow as new
fields matter.

### Evidence

A survey of the caches written by real verify runs (about 490 Crossref work
records, 460 Crossref search pages, 460 OpenAlex work records, and 435 OpenAlex
search pages, spanning journal articles, proceedings papers, chapters, books,
preprints, and reports) established which fields are present on every record
and which are optional. Notable observations that the model must encode:

- **Crossref** `title`, `subtitle`, `container-title`, `short-container-title`,
  and `ISSN` are always lists, often empty. `author` is present on 99% and each
  entry carries either `family`/`given` (person) or `name` (organisation); both
  are optional. Date blocks (`issued`, `created`, `published`, `published-print`,
  `published-online`) share one `{date-parts, date-time?, timestamp?}` shape,
  and `date-parts` can be `[[None]]`. `published-print` is absent on 18% and
  `published-online` on 29%. `abstract` is present on 45%. `type` is a fixed
  vocabulary (`journal-article`, `proceedings-article`, `book-chapter`, `book`,
  `monograph`, `edited-book`, `posted-content`, `report`, `reference-book`,
  `other`).
- **Crossref** DOI and search responses are wrapped in an envelope
  `{status, message-type, message-version, message}`; a search page's
  `message` holds `items`, `total-results`, `items-per-page`, `query`, and
  `facets`.
- **OpenAlex** work records are flat and every top-level key is always present,
  but many are nullable: `abstract_inverted_index`, `best_oa_location`,
  `language`, `fwci`, `apc_list`, `content_urls`. `authorships[].author` is
  always a dict here, but the given-name recipe already met records where it
  is missing, so the model keeps it optional. `primary_location.source` is
  null on 19%. `ids` always has `doi` and `openalex`; `mag` on 48%, `pmid` on
  10%. `type` is OpenAlex's own vocabulary (`article`, `conference-paper`,
  `preprint`, `book`, `book-chapter`, `review`, `editorial`, `report`,
  `reference-entry`, `dissertation`, `other`); `type_crossref` was null on
  every record. No record carried `host_venue`, which the adapter still reads
  for older cache files.
- **OpenAlex** search pages are `{meta, results, group_by}`; `meta` carries
  `count`, `page`, `per_page`, `db_response_time_ms`, and newer fields
  (`x_query`, `cost_usd`) that did not exist when the client was written.
- The **cache row** is `{key, value, ts}` with `value` null for a cached 404,
  and the cache file does not guarantee the source: one OpenAlex-shaped record
  was found in a Crossref cache file, so consumers should route by the key's
  host, not the file name.

### Design

**Representation: `TypedDict`, not dataclasses.** The records are dicts in the
cache and dicts on the wire, and every consumer already indexes them as dicts.
A `TypedDict` describes that shape for the type checker without converting
anything, costs nothing at runtime, and lets the model stay incomplete: with
`total=False` every key is optional, and `Required[...]` marks the handful the
survey found on every record. A dataclass layer would mean a second
representation to keep in sync and a parse step on every cache hit; when a
consumer wants a projection it already has `Work` (and plan 013's
`AuthorProfile`) for that.

**Module: `citefinder/models.py`.** One module holding both sources plus the
cache row, grouped by source, with a module docstring stating the survey that
produced it and the rule for growing it: add a key when a consumer starts
reading it, mark it `Required` only when the survey shows 100% coverage, and
type nullable fields as `X | None`. Roughly:

- Crossref: `CrossrefDate`, `CrossrefAuthor`, `CrossrefReference`,
  `CrossrefLicense`, `CrossrefFunder`, `CrossrefEvent`, `CrossrefJournalIssue`,
  `CrossrefWork`, `CrossrefSearchMessage`, `CrossrefEnvelope` (generic over
  `message`).
- OpenAlex: `OpenAlexAuthor`, `OpenAlexInstitution`, `OpenAlexAuthorship`,
  `OpenAlexSource`, `OpenAlexLocation`, `OpenAlexBiblio`, `OpenAlexIds`,
  `OpenAlexOpenAccess`, `OpenAlexTopic`, `OpenAlexWork`, `OpenAlexMeta`,
  `OpenAlexSearchPage`.
- Cache: `CacheRow`.
- Literal aliases for the two `type` vocabularies, kept as `str` unions the
  checker can widen (`CrossrefWorkType`, `OpenAlexWorkType`).

Fields with under about 5% coverage in the survey are left out unless a
consumer needs them; the docstring says so, so their absence is not read as
"never present".

**Drift check.** A small helper `undeclared_keys(record, model)` that walks a
record against a `TypedDict` (one level of nesting for dict-valued and
list-of-dict fields) and returns the dotted paths the model does not declare.
It is what makes "expected to change over time" operational: a test runs it
over the synthetic fixtures, and a maintainer can run it over a real cache
file to see what the sources have added. Reuses `typing.get_type_hints` and
the `__annotations__` of nested `TypedDict`s; no new dependency.

**Wiring.** Replace `dict[str, Any]` with the model types in the signatures
that hand records around: `CrossrefClient.lookup_doi` / `search_bibliographic`,
`OpenAlexClient.lookup_doi` / `search` / `search_title`, `reconstruct_abstract`,
`crossref_to_work` / `openalex_to_work` / `crossref_full_title`, and
`verify.Source`. Runtime behaviour does not change; the JSON still comes out of
`CachedJsonClient._get` as `Any`, which assigns to a `TypedDict` without a cast.
The skill prompt's field tables point at the module as the reference.

### Out of scope

- Validating records at runtime (the model is for the type checker and the
  reader; a record that lacks a `Required` key does not raise).
- Author profile records (`/authors/{id}`), which plan 013 adds; that plan
  should extend `models.py` rather than open a new module.
- Modelling `abstract_inverted_index` beyond `dict[str, list[int]]`,
  `reference[]` beyond the keys the survey saw, or Crossref `relation`.

### Implementation order

1. `models.py` with the TypedDicts, the two vocabularies, `CacheRow`, and
   `undeclared_keys`.
2. Tests: synthetic Crossref and OpenAlex records that exercise every declared
   nested shape, `undeclared_keys` reporting a planted unknown key and nothing
   else, and the adapters accepting the typed records.
3. Wire the signatures in `client.py`, `openalex.py`, `adapters.py`, and
   `verify.py`; run pyrefly and fix what it now sees.
4. Docs: CLAUDE.md package map, README field-map pointer, skill prompt
   pointer, changelog entry.

## Log

- Survey: a scratch script walked every JSONL cache written by real verify
  runs and tallied dotted paths with coverage and value types, up to three
  levels deep; the Evidence section is its summary. The same script, turned
  into `undeclared_keys`, was then folded over every record against the first
  draft of the model. It found 28 undeclared paths on Crossref works and 2 on
  OpenAlex works; the ones above about 5% (`indexed.version`,
  `countries_distinct_count`, `institutions_distinct_count`, `relevance_score`
  on search hits, `reference[].edition`, `funder[].award-info`) went into the
  model and the tail was left out on purpose.
- Decision: no `Required[...]` keys after all. Marking even the 100%-coverage
  keys required would force every test fixture and every hand-built record to
  carry them, which the adapters' defensive `.get` style never assumed. The
  coverage comment carries the information instead; the docstring says so.
- Decision: `verify.Source` keeps one `SourceRecord = CrossrefWork |
  OpenAlexWork` alias and narrows with `cast` inside the `self.name` branches,
  because a `TypedDict` cannot be narrowed by `isinstance`.
- Wiring surfaced no runtime change. pyrefly flagged only test fixtures, which
  now carry `CrossrefWork` / `OpenAlexWork` annotations; the CLI conftest
  stub's `lookup_doi` returns `Any` since it stands in for both clients.
- PR: https://github.com/gitronald/citefinder/pull/54
