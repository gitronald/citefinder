---
id: 13
slug: openalex-author-profiles
status: draft
branch:
created: 2026-09-05T15:54:41-07:00
concluded:
pr:
---

# Fetch and cache OpenAlex author profiles

## Plan

### Problem

citefinder wraps two OpenAlex endpoints, `/works/doi:{doi}` and
`/works?filter=title.search:…`, and nothing else. Author identity questions
that the work record cannot settle have no path through the tool:

- the author's **current preferred spelling** versus the byline on one work
  (a diacritic added later, a name change, an initial dropped);
- **alternative spellings** OpenAlex has merged into one profile
  (`display_name_alternatives`), which is what confirms two differently
  spelled bylines are the same person;
- **ORCID** and current affiliation, for disambiguating common surnames.

The work record embeds only `author.id`, `author.display_name`, and
`author.orcid` per authorship. Everything else lives at `/authors/{id}`, which
the skill currently lists under "endpoints citefinder doesn't wrap".

This plan follows [[verify-given-name-check]], which uses only the embedded
`display_name`; profiles are the second step for the cases that field cannot
resolve.

### Scope

- A client method and a CLI command to fetch one author profile by OpenAlex
  author ID (`A…`) or ORCID, cached in the same JSONL cache the works use.
- A batch helper that, given a verify run's `openalex.jsonl`, fetches the
  profile for every distinct `author.id` it contains.
- Skill documentation for both.

Out of scope: changing verify's signals or status buckets; author *search* by
name (`/authors?search=`), which is a different question with a different
error profile; Crossref has no author endpoint, so nothing on that side.

### Design

**Client.** `OpenAlexClient.lookup_author(author_id: str) -> dict | None`,
fetching `{OPENALEX_BASE}/authors/{id}`. Accept the bare `A123…`, the full
`https://openalex.org/A123…` URL as it appears in work records, and an ORCID
(`0000-…` or its URL) which OpenAlex resolves via `/authors/orcid:{orcid}`.
Normalize the identifier before it becomes the cache key so the three forms
share one entry. The retry, pacing, and API-key behavior come from
`CachedJsonClient` unchanged.

**Cache.** Same `JsonlCache` and same file as works (`openalex.jsonl`); the
key prefix distinguishes the record kind (works are keyed by DOI today, so use
the resolved `https://openalex.org/A…` URL as the key). A 404 is cached like a
missing DOI so a merged or deleted profile is not re-fetched every run.

**Record shape.** Plan [[source-record-models]] (014) added `citefinder/models.py`
with `TypedDict`s for the raw records; the embedded authorship stub is
`OpenAlexAuthor`. Declare the full `/authors/{id}` record there as
`OpenAlexAuthorProfile` (the keys the client reads, with coverage comments from
a survey of fetched profiles), teach `models.cache_drift` to route
`/authors/` keys to it so `citefinder drift` covers profiles too, and type
`lookup_author` with it. Do not open a second models module.

**Projection.** A small `AuthorProfile` dataclass in `adapters.py` for the
fields callers actually use: `id`, `display_name`, `display_name_alternatives`,
`orcid`, `works_count`, `last_known_institutions` (names only). The raw record
stays available in the cache.

**CLI.**

```bash
citefinder author A5012345678            # one profile, JSON to stdout
citefinder author 0000-0002-1825-0097    # by ORCID
citefinder authors-from-run data/citefinder/<paper>/openalex/openalex.jsonl
```

The second command walks the cached work records, collects distinct author
IDs, fetches each (cache-first), and prints a table of `id, display_name,
alternatives, orcid` so an editor can scan the whole bib's authors at once.
Name it to match the existing verb style of the CLI once decided; the shape
matters more than the spelling.

**Rate limits.** Profiles are one request per distinct author, so a 60-entry
bib is roughly 150 requests on a cold cache. The existing `min_interval`
pacing applies; document that the batch command is cache-first and idempotent.

### Implementation order

1. `lookup_author` on `OpenAlexClient` with identifier normalization and
   tests against recorded fixtures (bare ID, URL, ORCID, 404).
2. `AuthorProfile` projection and an `openalex_to_author` adapter.
3. `citefinder author` command.
4. Batch command over a run's `openalex.jsonl`.
5. Skill update: move `/authors` out of the "not wrapped" list, add a short
   "Author profiles" section that says when to reach for it (only after the
   embedded `display_name` check leaves a question open) and repeats the rule
   that a profile informs a byline check and never rewrites a name by itself.
6. Changelog entry under `[Unreleased]`.

### Open questions

- Whether the batch command belongs under `verify` as a flag
  (`verify --author-profiles`) rather than a separate command. A flag keeps
  one entry point but makes verify slower on a cold cache; a separate command
  keeps verify's runtime predictable. Leaning separate.
- Whether to store profiles in `openalex.jsonl` or a sibling
  `openalex-authors.jsonl`. One file is simpler; two keep the works cache's
  size and replay time unchanged. Decide when implementing step 1.

## Log

- **2026-11-19T14:05:39-08:00** — Review: still open. Nothing since the plan was written wraps `/authors`: `OpenAlexClient` has no `lookup_author`, and the skill body still lists `/authors` as unwrapped. The files the plan names (`citefinder/models.py`, `cache_drift`, `adapters.py`) are present. What the implementation takes from the repo as it stands now, where that differs from the spec:
  - **Cache key.** The spec keys a profile on `https://openalex.org/A...`. `cache merge` and `cache stats` (plan 016) route rows by the request host `api.openalex.org`, and the drift routing this plan asks for matches `/authors/` in the key, so that key would be unroutable in all three. The key is the request URL, `https://api.openalex.org/authors/A...`, which is what `CachedJsonClient._get` writes without special handling.
  - **ORCID.** An ORCID cannot share the ID's cache entry before a fetch, since only the response maps one to the other. The bare ID and its URL forms normalize to one key; an ORCID keys on `/authors/orcid:<orcid>`, and a profile fetched that way is also stored under its `/authors/A...` key.
  - **Skill.** The skill is packaged with pkgskills since plan 019: commands are written `{cli} ...`, reference material is a declared `Doc`, and the body carries a `metadata.version` to bump.
  - **Open question 1:** a separate command, as the plan leans. **Open question 2:** the same `openalex.jsonl`. The read-through fallback (plan 018), `cache merge`, and `cache stats` find caches by the `<source>.jsonl` name, so a sibling file would be outside all of them.
  - **Which works the batch walks:** DOI-lookup rows (`/works/`), the same set the given-names recipe from plan 012 reads. Search pages hold up to three candidates per entry, most of them not the cited work.
