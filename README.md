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
2. Shell environment: `OPENALEX_API_KEY`, `OPENALEX_MAILTO`, `CROSSREF_MAILTO`.
3. Project-local `.env` in the current working directory or any parent.
4. **`~/.config/citefinder/config.toml`** (honors `$XDG_CONFIG_HOME`) — store
   it once on this machine.

```toml
# ~/.config/citefinder/config.toml
[openalex]
api_key = "your-openalex-key"
mailto = "you@example.com"

[crossref]
mailto = "you@example.com"
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

df = bib_to_table(open("refs.bib").read())   # key, entry_type, then fields alphabetical
new_bib = table_to_bib(df)                    # back to .bib, null cells skipped
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
```

`verify` walks each entry: if a `doi` field is present it resolves the DOI; otherwise it searches by author + title + year. Each result is checked against four signals (title, year, first-author surname, container) and bucketed by status. Output goes to `data/citefinder/<bib-stem>/<source>/`: a `<source>.jsonl` cache and a structured `results.json`. Re-running is cheap — every cache hit is served from disk.

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
