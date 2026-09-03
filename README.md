# citefinder

OpenAlex (default) + Crossref reference lookups with local JSONL caching.

A small Python library + CLI for verifying academic references against the
OpenAlex and Crossref APIs. Every lookup is appended to an append-only JSONL
log so repeated queries (across verification passes or sessions) are served
from the cache. Negative results (404s) are cached too, so known-missing DOIs
aren't re-hit.

OpenAlex is the default source: it merges Crossref + Unpaywall + ORCID + ROR
+ repository sources, so it covers what Crossref alone is missing — arXiv
DOIs (`10.48550/arXiv.*`), other preprints, repository deposits — and
frequently has richer metadata (abstracts, full author lists, affiliations)
for records that exist in both. Crossref is still available via the
`crossref` subcommand for its own workflows (book-chapter lookup, the
canonical published-deposit metadata).

### Configuration: API key and mailto

OpenAlex works without authentication, but a free API key gives you higher
limits and tier-specific endpoints. Both Crossref and OpenAlex honor a
`mailto` for their polite pools (faster responses, higher quotas).

- OpenAlex docs: https://developers.openalex.org/
- Sign up / generate an OpenAlex key: https://openalex.org/login?redirect=/settings/api-key

Lookup order (CLI), highest priority first:

1. CLI flag: `--api-key`, `--mailto`.
2. Shell environment: `OPENALEX_API_KEY`, `OPENALEX_MAILTO`, `CROSSREF_MAILTO`
   (plus the retry and pacing variables under
   [Rate limits and retries](#rate-limits-and-retries)).
3. Project-local `.env` in the current working directory or any parent.
4. **`~/.config/citefinder/config.toml`** (honors `$XDG_CONFIG_HOME`) — store
   it once on this machine.

```toml
# ~/.config/citefinder/config.toml
[openalex]
api_key = "your-openalex-key"
mailto = "you@example.com"
max_retries = 3    # optional — see "Rate limits and retries"
min_interval = 0.1

[crossref]
mailto = "you@example.com"
max_retries = 3
min_interval = 0
```

The file is plain-text — if your environment is shared, `chmod 600
~/.config/citefinder/config.toml` so it's only readable by you. Each section
is optional; omit anything you don't need.

Library users: pass `api_key=...` and `mailto=...` to the client constructors
explicitly. The config-file fallback is CLI-only (it shouldn't be a surprise
side effect of importing the library).

The API key is sent as `Authorization: Bearer ...`, never as a URL parameter,
so it doesn't land in cache keys, logs, or referer headers.


## Install

```bash
uv add citefinder
```

Or for development:

```bash
git clone https://github.com/gitronald/citefinder
cd citefinder
uv sync
```

## Library usage

### OpenAlex (default)

```python
from citefinder import OpenAlexClient, is_arxiv_doi, reconstruct_abstract

openalex = OpenAlexClient(
    cache_path="~/.cache/citefinder/openalex.jsonl",
    mailto="you@example.com",  # opts into OpenAlex's polite pool — faster, higher quota
)

# Single DOI (works for arXiv DOIs that Crossref doesn't index)
work = openalex.lookup_doi("10.48550/arXiv.2410.21554")

# Title-only search — tuned for citation verification. Handles OpenAlex's
# curly-apostrophe quirk and strips filter-reserved punctuation that would
# 400 the request, so straight ASCII inputs match curly-quoted indexed titles.
hits = openalex.search_title("Backstabber's Knife Collection", rows=3)

# Free-text search across titles + abstracts (noisier; prefer search_title
# for citation lookup)
hits = openalex.search("fact-checking large language models", rows=3)

# OpenAlex stores abstracts as an inverted index — reconstruct to plain text
abstract = reconstruct_abstract(work) if work else None

# Helper for routing logic
assert is_arxiv_doi("10.48550/arXiv.2410.21554")
```

The `mailto` argument is optional but recommended: it puts requests into
OpenAlex's [polite pool](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication#the-polite-pool)
for faster responses. The cache key strips `mailto` so changing it doesn't
invalidate prior entries.

### Crossref

```python
from citefinder import CrossrefClient

client = CrossrefClient(
    cache_path="~/.cache/citefinder/crossref.jsonl",
    mailto="you@example.com",  # opts into Crossref's polite pool — faster, higher quota
)

# Single DOI
work = client.lookup_doi("10.1126/science.aap9559")
print(work["title"][0])

# Bibliographic search (author + title + year)
hits = client.search_bibliographic("Wolfowicz hate speech meta-analysis", rows=3)

# Book chapter via {book_doi}.{NNN} pattern
chapter = client.lookup_book_chapter("10.1017/9781108890960", 5)
```

Crossref and OpenAlex both honor `mailto` for their polite pools; the cache
key strips it on either side, so rotating the email doesn't invalidate prior
entries.

OpenAlex's schema differs from Crossref. Quick map:

| Field | Crossref | OpenAlex |
|---|---|---|
| Title | `work["title"][0]` (+ optional `subtitle[0]`) | `work["display_name"]` |
| First author | `work["author"][0]["family"]` (surname only) | `work["authorships"][0]["author"]["display_name"]` (**full name** — parse for surname) |
| Container | `work["container-title"][0]` (+ `short-container-title`) | `work["primary_location"]["source"]["display_name"]` (+ `host_venue` on older records) |
| Year | `published-print` / `published-online` / `issued` / `created` → `["date-parts"][0][0]` | `work["publication_year"]` (int) |

The two sources regularly disagree on a work's year because they index
different events: Crossref's `published-print` tracks the printed
issue/volume year, while OpenAlex's `publication_year` often collapses to
the online-first date — or, for books, to a precursor work (e.g., the
dissertation a monograph grew out of; a `type` of `dissertation` or
`posted-content` next to a journal/monograph DOI is the giveaway). Treat a
year mismatch as a flag for review, and default to the final printed
record: the journal volume year, or the publisher's first-published edition
year for books.

### Rate limits and retries

Both clients retry a request that comes back `429` (rate limited) or
`502`/`503`/`504` (gateway errors), up to `max_retries` times (default 3),
before raising the original `HTTPError`. The wait honors the response's
`Retry-After` header when present — both the delta-seconds and the HTTP-date
form — and otherwise backs off exponentially from `backoff_base` (default
1 s: 1 s, 2 s, 4 s, each plus up to half a step of jitter). Any single wait
is capped at `max_wait` (default 60 s). Other 4xx responses raise
immediately, and 404 is still cached as `None`.

Requests can also be paced: `min_interval` is the minimum number of seconds
between the start of consecutive requests from one client instance. It
defaults to `0.1` for `OpenAlexClient`, matching OpenAlex's documented 10
requests per second, and `0` for `CrossrefClient`. Cache hits are not
requests and are never paced. All four knobs must be finite and
non-negative; anything else raises `ValueError` at construction.

```python
openalex = OpenAlexClient(
    cache_path="~/.cache/citefinder/openalex.jsonl",
    max_retries=5,  # extra attempts after the first; 0 disables retrying
    backoff_base=1.0,  # first backoff step when there is no Retry-After
    max_wait=60.0,  # ceiling on any single wait
    min_interval=0.2,  # seconds between requests
)
openalex.lookup_doi("10.48550/arXiv.2410.21554")
print(openalex.retries)  # retries so far on this instance
```

Each retry logs one warning on the `citefinder` logger naming the status,
the attempt, and the wait. The `retries` counter on the client tallies them
for the run, and `citefinder verify` prints it in its summary line.

Error responses are never cached: nothing is written until a 2xx or 404
arrives, so a run that hit the rate limit can simply be re-run once the
limit clears. There is no cache line to purge.

On the CLI, `--max-retries` and `--min-interval` are accepted by `doi`,
`search`, `verify`, and the `crossref` subcommands. `OPENALEX_MAX_RETRIES` /
`OPENALEX_MIN_INTERVAL` and `CROSSREF_MAX_RETRIES` / `CROSSREF_MIN_INTERVAL`
are the environment fallbacks, and `max_retries` / `min_interval` under
`[openalex]` / `[crossref]` in `config.toml` the lowest-priority ones.

### Bib verification

A `.bib` file can be parsed and verified against either source end-to-end:

```python
from citefinder import (
    OpenAlexClient,
    Source,
    parse_entries,
    verify_entry,
)

source = Source(name="openalex", client=OpenAlexClient(cache_path="cache.jsonl"))

for entry in parse_entries(open("refs.bib").read()):
    result = verify_entry(entry, source)
    print(result.key, result.status, result.matched_doi)
```

Each `Result` reports a `Status` (matched / probable / mismatch / unmatched / doi-not-found / skip-source / error) plus the four signals — title, year, first-author surname, container — that drove the verdict. `BibCitation` and `Work` are the canonical shapes; `crossref_to_work` and `openalex_to_work` adapt source-specific JSON into `Work`. See `citefinder/signals.py` for the signal-check thresholds.

### Bib ↔ table

A `.bib` file can be loaded into a wide polars DataFrame (one row per entry, one column per field) for inspection or bulk editing, then serialized back:

```python
from citefinder import bib_to_table, table_to_bib

df = bib_to_table(open("refs.bib").read())  # key, entry_type, then fields alphabetical
new_bib = table_to_bib(df)  # back to .bib, null cells skipped
```

`bib_to_table` lowercases field keys (`DOI` → `doi`) and stores the entry kind in `entry_type` to avoid collision with the literal `type` field that some entries carry (e.g., SSRN papers set `type = {SSRN Scholarly Paper}`). `table_to_bib` requires `key` and `entry_type` columns and serializes the rest in column order. The round-trip is lossless on field values and entry types; the original within-entry field order and any source-file `@string`/`@comment` blocks are not preserved.

## CLI usage

```bash
# OpenAlex (default)
citefinder doi 10.48550/arXiv.2410.21554 --mailto you@example.com
citefinder search "Backstabber's Knife Collection" --rows 3

# Crossref
citefinder crossref doi 10.1126/science.aap9559 --mailto you@example.com
citefinder crossref search "Wolfowicz hate speech meta-analysis" --rows 3
citefinder crossref chapter 10.1017/9781108890960 5

# .bib verification
citefinder verify refs.bib                               # full pipeline (defaults to OpenAlex)
citefinder verify refs.bib --source crossref             # ...or against Crossref
citefinder verify refs.bib --out path/to/output/dir/     # custom output directory

# .bib ↔ table
citefinder bib-to-table refs.bib                            # wide polars table to terminal
citefinder bib-to-table refs.bib --csv > refs.csv           # ...or CSV to stdout
citefinder bib-to-table refs.bib --fields title,year,doi    # subset of columns
citefinder table-to-bib refs.csv                            # CSV back to .bib on stdout
citefinder table-to-bib refs.csv --out refs.regen.bib       # ...or to a file

# Claude Code skill (see "Claude Code skill" below)
citefinder skill                                            # print the skill body
citefinder install                                          # stub into ~/.claude/
citefinder install --local                                  # ...or the current repo
citefinder install --check                                  # ok | drifted | missing
```

`verify` walks each entry: if a `doi` field is present it resolves the DOI; otherwise it searches by author + title + year. Each result is checked against four signals (title, year, first-author surname, container) and bucketed by status. Output goes to `data/citefinder/<bib-stem>/<source>/`: a `<source>.jsonl` cache and a structured `results.json`. Re-running is cheap — every cache hit is served from disk.

Read `results.json` by `method` × `status`:

- `method=doi` with `mismatch` or `probable` — a real defect: the bib's own DOI resolves to a different work, or a field (year, first author, container) disagrees with the source record.
- `method=search` with `matched` and a non-empty `matched_doi` — a DOI candidate for an entry that lacked one.
- `method=search` with `mismatch` or `probable` — usually a wrong-work false positive (books, reports, and other sources the index carries poorly), not a reason to rewrite the entry.
- `unmatched`, `skip-source`, and `doi-not-found` — noise unless they cluster around one publisher or entry type.

`bib-to-table` and `table-to-bib` are inverses: the first turns a `.bib` into a wide table (terminal view by default, `--csv` for piping), the second reads such a CSV back into a `.bib`. Useful for spreadsheet-style review or bulk edits before regenerating the file. The round-trip is lossless on data; within-entry field order and source-file formatting are not preserved.

### CLI arguments

- `--cache PATH` — JSONL cache path. Defaults to
  `~/.cache/citefinder/openalex.jsonl` for top-level commands and
  `~/.cache/citefinder/crossref.jsonl` for `crossref` subcommands. Separate
  files so sources don't mix; override per command if you want
  per-project caches (e.g., `--cache ./data/refs.jsonl`).
- `--rows N` *(search only)* — Number of results to return. Default `3`.
- `--mailto EMAIL` — Opts the request into the source's polite pool (both
  OpenAlex and Crossref honor it): faster responses and a higher quota.
  Sent as a `?mailto=…` query param; stripped from the cache key, so
  rotating the email doesn't invalidate prior entries.
- `--api-key KEY` *(OpenAlex only)* — OpenAlex API key for higher
  rate limits and tier-specific endpoints. Also read from `OPENALEX_API_KEY`
  in the env or a `.env` file (loaded from cwd or any parent). Sent as
  `Authorization: Bearer <key>` so it never lands in cache keys, URL logs,
  or referer headers.
- `--max-retries N` — Retries after a `429`/`502`/`503`/`504` response;
  `0` disables. Default `3`. Also `OPENALEX_MAX_RETRIES` /
  `CROSSREF_MAX_RETRIES` in the env or `max_retries` in `config.toml`.
- `--min-interval SECONDS` — Minimum gap between consecutive requests.
  Default `0.1` for OpenAlex, `0` for Crossref. Also `OPENALEX_MIN_INTERVAL`
  / `CROSSREF_MIN_INTERVAL` in the env or `min_interval` in `config.toml`.
  `verify` reads the variables for whichever `--source` it runs against.

## Claude Code skill

The `use-citefinder` [Claude Code](https://claude.ai/code) skill lives **inside
the package**, at `citefinder/prompts/skill.md`. It is never copied out. The
instructions are printed on demand:

```bash
citefinder skill        # the full skill body, from the installed package
```

`citefinder install` materializes only a ~1.7 KB **stub** into a `.claude/`
tree — the skill's frontmatter (the `description` triggers Claude Code matches
on, which have to be readable off disk) plus a pointer to `citefinder skill`:

```bash
citefinder install                  # ~/.claude/ — serves every repo
citefinder install --local          # <repo>/.claude/ — vendored per repo
citefinder install --check          # ok | drifted | missing (exits 1 unless ok)
citefinder install --force          # overwrite a file citefinder didn't generate
```

Because the body is fetched rather than copied, the instructions an agent reads
always come from the version of citefinder that is actually installed. The
failure this design removes was a hand-copied skill still documenting a pre-0.4
CLI in a repo whose lockfile pinned 0.4.2 — with no copy, there is nothing to
fall out of date.

The stub carries a stamp naming the version and mode it was rendered for:

```
<!-- generated by citefinder 0.4.4 (mode=global); do not edit — run: citefinder install --force -->
```

The stub changes rarely, but it is still checkable — `--check` compares it
against what the *currently installed* citefinder would render (ignoring the
stamped version itself, so a routine upgrade alone never reads as drift), and
exits non-zero on `drifted` or `missing`:

```bash
uv add -U citefinder          # or: uv tool upgrade citefinder
citefinder install --check
citefinder install --force    # re-materialize if it reports drift
```

Add `--local` to both commands when the stub is vendored in a repo. A file at
the target path that citefinder did not generate (no stamp) is never
overwritten without `--force`, so a hand-authored skill of the same name is
safe. To change the skill's content, edit `citefinder/prompts/skill.md` and
release — there is no `.claude/` copy to re-sync.

## Why JSONL?

The cache is an append-only log: every lookup is one JSON object per line.
Benefits:

- **Auditable**: `cat`/`grep` to see every query that ever ran.
- **Diffable**: plays nicely with git if you want to commit a project's cache.
- **Crash-safe**: an interrupted write loses at most the last line.
- **Recoverable**: rebuild the in-memory dict by replaying the log.

Latest value wins on replay, so over-writes are a no-op semantic.

**SQLite alternative.** A SQLite-backed cache is another reasonable
implementation — it would trade the audit log and `grep`-ability for faster
random access on very large caches (millions of entries) and concurrent
writers. The current scale of citefinder use (per-project bibs, tens of
thousands of entries at most) doesn't need it, and replaying a JSONL on
startup is fast enough that the simplicity wins. If a future workload pushes
past those limits, swapping the storage layer is a single class — `JsonlCache`
in `citefinder/cache.py` — behind the same `get` / `put` / `__contains__`
interface.

## Tests

```bash
uv run pytest
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md) for the release history.
