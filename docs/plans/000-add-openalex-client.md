---
status: active
branch: feature/openalex-client
created: 2026-04-28T10:44:18-07:00
completed:
pr:
---

# Add OpenAlex client and arXiv DOI routing as Crossref fallback

## Plan

Crossref is the canonical source for verifying published metadata, but it has two recurring failure modes that hurt `verify-bib.py` in the `jots` repo:

1. **arXiv / preprint DOIs 404** in Crossref — verify-bib labels these `doi-not-found`. They are real DOIs, just not in Crossref's index.
2. **Thin or partial metadata** in Crossref deposits — missing abstracts, abbreviated titles, capped author lists, missing affiliations. Verify-bib's `check_*` signal functions return `unknown` in those cases.

OpenAlex (`api.openalex.org`) merges Crossref + Unpaywall + ORCID + ROR + repository sources, and frequently has the data Crossref lacks for the same DOI. Adding it as a structured fallback gives verify-bib a second-source signal without giving up Crossref as the primary canonical source.

### Scope

1. **`citefinder.openalex.OpenAlexClient`** — sibling class to `CrossrefClient`, same shape:
   - `__init__(cache, cache_path, mailto, user_agent, timeout)` — `mailto` enters OpenAlex's "polite pool" (faster, higher daily quota). Constructor arg with a sensible UA fallback; do **not** bake an email into the package default.
   - `lookup_doi(doi) -> dict | None` — `GET /works/doi:{doi}`, 404 → `None`, cached as `None` like the Crossref client.
   - `search(query, rows=3) -> list[dict]` — `GET /works?search=...&per-page={rows}`.
   - `reconstruct_abstract(work) -> str | None` — OpenAlex stores abstracts as an `abstract_inverted_index` (`{word: [positions]}`); reassemble to a plain string. Standalone function so callers can choose to invoke it.
   - Separate `JsonlCache` file (`openalex.jsonl`) — never mix sources in one cache log.

2. **arXiv DOI routing** — arXiv DOIs (`10.48550/arXiv.*`) deterministically miss Crossref. Two options:
   - (a) Route them to OpenAlex first (cheapest path, OpenAlex indexes arXiv).
   - (b) Add an `arxiv.py` client that hits the arXiv API directly.
   Start with (a) — OpenAlex covers the common case and we already need it for the broader fallback. Revisit (b) if OpenAlex coverage is insufficient. Add a small helper `is_arxiv_doi(doi) -> bool` to `client.py` (or a shared `dois.py`) that the routing logic in verify-bib can consume.

3. **CLI parity** — add `citefinder openalex doi <DOI>` and `citefinder openalex search <QUERY>` subcommands paralleling the existing Crossref ones. Same `--cache` flag pattern, distinct default cache file.

4. **`verify-bib.py` integration (in `jots` repo, separate PR)** — out of scope for this plan's commit but worth noting the consumer:
   - On `doi-not-found` from Crossref → fall through to `OpenAlexClient.lookup_doi`. If hit, run the same 4-signal check against the OpenAlex record (with adapter functions for the different schema).
   - On Crossref signals returning `unknown` → optionally fetch OpenAlex and re-check that signal. Status reporting needs a provenance field so reports show which source confirmed each signal.
   - Adapt the signal functions: OpenAlex uses `display_name` (single string, no subtitle split), `authorships[].author.display_name`, `primary_location.source.display_name`, `publication_year`. The schema gap is significant enough that signal extraction should live behind a small adapter, not be inlined.

5. **Tests** — add `tests/test_openalex.py`:
   - Unit tests for `reconstruct_abstract` with a known inverted index.
   - Integration test for `lookup_doi` against a stable, well-formed DOI (mark with `@pytest.mark.integration` so the default suite stays offline).
   - Cache round-trip test mirroring the Crossref one.

### Non-goals

- BibTeX formatting. `fetchbib` (https://github.com/mr-devs/fetchbib) already handles "DOI/query → formatted BibTeX string" with arXiv routing, OpenAlex search, and BibTeX-flavored config (protect-titles, exclude-issn). citefinder is a verification/lookup library that returns raw JSON; staying in that lane keeps the two tools complementary rather than overlapping.
- A unified `Reference` data model that normalizes Crossref + OpenAlex into one schema. Premature — let consumers (verify-bib) drive what fields they actually need before normalizing.
- Direct `arxiv.org` API integration. Defer until OpenAlex's arXiv coverage proves insufficient.

### Build sequence

1. `OpenAlexClient` + cache wiring + unit tests.
2. `reconstruct_abstract` helper + tests.
3. `is_arxiv_doi` helper + tests.
4. CLI subcommands.
5. Integration test (skipped by default).
6. README update — note the new client, polite-pool email guidance, and link to OpenAlex docs.
7. Follow-up plan in the `jots` repo for the verify-bib fallthrough wiring.

### Open questions

- Should `OpenAlexClient` accept a Crossref Work as a hint (e.g., to disambiguate ambiguous search hits)? Probably not — keep clients independent and let verify-bib do the cross-referencing.
- Does the polite-pool `mailto` go in the URL or a header? Both work; OpenAlex docs prefer URL param. Use URL param for visibility in the cache key (different emails → different cache entries, which is correct because different polite-pool tiers may behave differently).
