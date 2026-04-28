---
status: done
branch: feature/openalex-client
created: 2026-04-28T10:44:18-07:00
completed: 2026-04-28T12:31:48-07:00
pr: https://github.com/gitronald/citefinder/pull/5
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

## Log

### 2026-04-28 — initial implementation

Implemented in build-sequence order. All commits on `feature/openalex-client`:

- `971c7d7` — `OpenAlexClient` (lookup_doi, search), `reconstruct_abstract`, `_strip_mailto` cache-key helper, `is_arxiv_doi` predicate, `__init__` exports, two CLI subcommands (`citefinder openalex doi`, `citefinder openalex search`) with separate default cache file.
- `9765fd8` — `use-citefinder` skill updated with OpenAlex fallback section, arXiv routing example, and Crossref↔OpenAlex schema-diff table.
- `2e1a9be` — 12 unit tests covering DOI lookup, search, 404 caching, polite-pool `mailto` round-trip, cache-key strip, abstract reconstruction, and arXiv detection.
- `a8541ce` — README updated with OpenAlex section, schema map, mailto guidance, and CLI examples.

### 2026-04-28 — API key support (added during plan close)

Scope expansion driven by the user's README draft surfacing OpenAlex's API-key option, which the original plan hadn't covered.

- `5bfc9d0` — added `api_key` arg to `OpenAlexClient.__init__` with fallback to `OPENALEX_API_KEY` env var. CLI loads `.env` from CWD-or-parent at startup via `python-dotenv`. Both subcommands accept `--api-key` with the env var also read by Typer's `envvar=`. Key sent as `Authorization: Bearer ...` header (never URL param) to keep it out of cache keys, logs, and referer trails. 5 new tests covering explicit > env-var > none precedence and absence-from-URL.

Decision against URL-param auth: cache keys would either leak the key or require another `_strip_*` helper, both worse than just using the documented header form.

### Decisions worth flagging

- **Cache key strips `mailto`** but the network request keeps it — so changing your polite-pool email doesn't invalidate your cache.
- **No `arxiv.py` client.** Routing arXiv DOIs through OpenAlex covers the common case; an arXiv-direct client is deferred until OpenAlex coverage proves insufficient. `is_arxiv_doi` is a predicate, not a router — callers (verify-bib) make the routing decision.
- **No unified `Reference` model.** Each client returns its source's raw JSON; normalization happens at the consumer when needed.
- **No live-API integration test** added in this PR. Plan called for one with `@pytest.mark.integration`; deferred since the unit tests cover the contract and a live test would require deciding on an opt-in mailto/api_key for CI. Easy to add later.
- **`fetchbib` boundary held.** No BibTeX formatting in citefinder — keeps the two tools complementary.

## Retrospective

- The original plan's "open question" about polite-pool `mailto` placement (URL vs header) had a wrong assumption baked in: I'd written that different emails justify different cache entries because they hit different polite-pool tiers. They don't — the polite pool is binary, not tiered. Fixed in implementation by stripping `mailto` from the cache key.
- Scope grew during the close: API-key support was out of the original plan but was the natural follow-up the moment OpenAlex auth was on the table. Worth folding in rather than splitting because the constructor signature would otherwise have churned twice.
- The user pushed back on demo calls using their personal email as the polite-pool `mailto`. Fixed locally and elevated to a global rule (`~/.claude/rules/privacy.md`) — don't auto-pull personal email from environment context for service identifiers.
- Ruff/pyrefly hooks caught: a stale `urlunsplit` return-type annotation and missing line wrapping. Both were one-line fixes; the type one (`urlunsplit` overloads as `str | bytes`) is worth remembering for future `urllib.parse` usage.
- Verify-bib in `jots` will need a small adapter for OpenAlex's schema (`display_name`, `authorships[].author.display_name`, etc.). Schemas were different enough to confirm the plan's "no unified `Reference` model" call — premature normalization would have been the wrong move.
