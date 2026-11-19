---
id: 13
slug: openalex-author-profiles
status: done
branch: feature/013-openalex-author-profiles
created: 2026-09-05T15:54:41-07:00
concluded: 2026-11-19T14:39:01-08:00
pr: https://github.com/gitronald/citefinder/pull/96
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
- **2026-11-19T14:05:42-08:00** — Activated.
  - Branch: `feature/013-openalex-author-profiles`
  - Base: `dev` at `7fdfd66`
  - Worktree: `.worktrees/013-openalex-author-profiles`
  - PR: pending
- **2026-11-19T14:10:18-08:00** — PR opened: https://github.com/gitronald/citefinder/pull/96
- **2026-11-19T14:17:10-08:00** — Implemented steps 1 to 6 on `feature/013-openalex-author-profiles`; checks ran at 2557202 (`ruff check`, `ruff format --check`, `pyrefly check`, and `uv run pytest`: 498 passed, coverage 97.97%, Python 3.14 only).
  - **Client.** `OpenAlexClient.lookup_author` with `author_path` normalizing the bare ID, the `https://openalex.org/A...` URL, and an ORCID (bare, URL, or `orcid:`). A name or a work ID raises `ValueError` before any request. An ORCID lookup also stores the profile under its `/authors/A...` key, replacing a 404 cached there.
  - **Model.** `OpenAlexAuthorProfile` (plus `OpenAlexAuthorIds`, `OpenAlexAffiliation`) with coverage from 531 live profiles: the authors on the DOI lookups of one existing works cache, fetched without an API key or `mailto` into a scratch cache. `citefinder drift` reports zero undeclared paths on all 531. `cache_drift` routes `/authors/` keys to a new `openalex-author` kind.
  - **Projection.** `AuthorProfile` and `openalex_to_author` in `adapters.py`, exported from the package.
  - **CLI.** `citefinder author <id-or-orcid>` prints the projection, and `--raw` the whole record (not in the spec; a profile runs about 10 KB, so the summary is the default). The batch command is `citefinder authors-from-cache <openalex.jsonl>`, not the spec's `authors-from-run`: the argument is any OpenAlex cache, the shared one included. It streams tab-separated rows, stores profiles in the file it was given, and reads the shared cache as a read-only fallback with `--no-fallback`, sharing verify's helper (`_shared_fallback`).
  - **Found in the live run.** 2 of 531 profiles carry an alternative spelling with a line break in it, so the table collapses whitespace to keep one row per author. One key (`awards_api_url`) was missing on 5 profiles. Authors per work came to 2.87 (531 on 185 works), so the spec's estimate of 150 requests for a 60-entry bib is closer to 170; the skill says so.
  - **Skill.** The section went into the `given-names` reference, which is where the embedded `display_name` check lives, with pointers from the body. Body version 1.1.0 to 1.2.0, and the local stub regenerated because it carries that version.
  - **Fixtures are synthetic.** The spec asks for recorded fixtures; the tests use made-up records of the surveyed shape so no real person's profile is committed.
  - **Not verified:** CI on Python 3.11 to 3.13. What OpenAlex returns for a merged or deleted profile: none of the 531 lookups returned a 404 or a different ID than asked for, so the cached-404 path is covered by mocked tests only. Open question 2's cost side: 531 profiles added 4.2 MB to a works cache.
- **2026-11-19T14:17:23-08:00** — Correction to the entry above: the 4.2 MB figure counted 380 of the profiles. Measured on the whole scratch cache, the 531 profile rows are 5.9 MB beside 6.1 MB of work and search rows, so storing profiles in the same `openalex.jsonl` about doubles that file and its replay. Open question 2 was decided for the same file because merge, stats, and the read-through fallback find caches by name; this is the cost of that choice, for the review to weigh.
- **2026-11-19T14:17:25-08:00** — Implemented: 13 commits ahead of `dev`.
- **2026-11-19T14:18:11-08:00** — CI passed on the pull request at 9a864df for Python 3.11, 3.12, 3.13, and 3.14, which settles the first "not verified" item above.
- **2026-11-19T14:39:32-08:00** — Review before merge, at medium, of the PR at 4929722: five findings verified, three fixed in b34f945, d9cefa8, and 7ab949c, each with a regression test that fails without its fix. Checks ran at 7ab949c (`ruff check`, `ruff format --check`, `pyrefly check`, and `uv run pytest`: 502 passed, coverage 97.97%, Python 3.14 only).
  - **Review follow-up, fixed.** `citefinder drift` sent any OpenAlex key containing the text `/authors/` to the author model, so a DOI or a searched title holding that text was counted as a profile. It now routes on the URL path.
  - **Fixed.** The author ID and ORCID patterns used `\d`, which matches digits of other scripts, so such an ID became a request and a cached 404 where it should exit 2. They take `[0-9]` only.
  - **Fixed.** `authors-from-cache` labelled a row with the `id` of the profile served. It prints the ID the work carries, so every row joins back to its works and the rows match the distinct count. This changes output only when OpenAlex answers one ID with another's profile, which is still not verified against the live API.
  - **Conscious no-ops.** `authors-from-cache` parses the cache file twice (once to collect IDs, once in the client's replay): one extra parse beside a paced request per uncached author. Superseded rows would add author IDs, but no code path writes a works key twice. `author --raw` builds the summary it discards.
  - **Rejected.** Validating the ID before the client is built (intended, and tested); `_one_line` as a copy of `normalize_title_query` (that one also rewrites punctuation); the batch command's `--cache-dir` option as a copy of verify's (a different layout).
  - The review is posted on the PR.

## Retrospective

- **Re-reading the spec against the repo before starting was the step that mattered most.** The plan sat for ten weeks while three other plans changed how caches are keyed, merged, and found. The spec's cache key (`https://openalex.org/A...`) would have been unroutable by `cache merge`, `cache stats`, and the drift check it asked for, and its "three forms share one entry" could not hold for an ORCID. Both were caught in the review entry written on activation, before any code.
- **Surveying live profiles first shaped the code more than the spec did.** 531 real profiles gave the model its coverage figures, and turned up line breaks inside recorded spellings, a key missing on some records, and a request estimate (about 2.9 authors per work) higher than the spec's. None of these would have come from the API documentation.
- **Measure the whole artifact before quoting a number.** The first cost figure for keeping profiles in the works cache counted 380 of the 531 rows and was corrected a minute later. The real cost of the same-file decision is that profiles about double `openalex.jsonl` and its replay. That was accepted so that merge, stats, and the read-through fallback keep finding caches by name; a sibling file is the lever if replay time becomes a problem.
- **The three review fixes were one mistake made three ways:** matching on the text of a value where its structure was meant (a substring of a cache key for a URL path, `\d` for ASCII digits, the served record's ID for the requested one). `_model_for` still matches works rows by substring; a new kind of row should be routed on the parsed URL.
- **Still not verified:** what OpenAlex returns for a merged or deleted author profile. No surveyed lookup returned a 404 or a different ID than asked for, so the cached-404 path and the row for a profile served under another ID are covered by mocked tests only. The fixtures are synthetic by choice, to keep real people's profiles out of the repo.
