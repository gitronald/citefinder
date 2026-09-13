---
name: use-citefinder
description: Look up DOIs, search Crossref or OpenAlex, resolve book chapters, and verify whole `.bib` files with `citefinder` — a small Crossref + OpenAlex client with a JSONL cache that survives sessions and remembers 404s. Use this whenever the user wants to verify a DOI, find a paper by author + title, check whether a citation is real, resolve a chapter DOI, look up an arXiv/preprint DOI Crossref doesn't index, generate canonical metadata for a reference list, or audit a `.bib` file end-to-end — even when they don't say "Crossref" or "DOI" explicitly. Phrases like "is this paper real?", "find the published version", "look up this citation", "the subagent gave me these papers — verify them", "audit refs.bib", or "what's the DOI for X?" should trigger it.
metadata:
  version: "1.0.0"
---

# Use citefinder

`citefinder` (https://github.com/gitronald/citefinder) is a small Python library + CLI for Crossref **and OpenAlex** lookups, with a JSONL-backed cache. Use it instead of raw `curl https://api.crossref.org/...` because:

- The cache survives sessions, so re-running verification is cheap.
- 404s are cached, so known-missing DOIs don't get re-queried.
- The cache is JSONL (one record per line) — `grep`-able, diffable, and crash-safe.
- It exposes both a Python API (for batch work, scripts, notebooks) and a CLI (for ad-hoc lookups).

## When to use this skill

- Verifying that a DOI resolves to the paper the user expects (the most common need).
- Finding the canonical / published DOI from an arxiv ID, SSRN URL, preprint title, or an `(Author Year)` inline citation.
- Resolving a book chapter DOI when you only have the book's DOI and a chapter number.
- Auditing a whole `.bib` file: which entries match, which have wrong DOIs, which can't be found.
- Sanity-checking a list of references produced by a research subagent or extracted from a PDF.
- Building or enriching a bibliography (`.bib`, CSV) from an outline.

## Install / availability check

If citefinder isn't already a dependency:

```bash
uv add citefinder  # or: uv add git+https://github.com/gitronald/citefinder
```

Confirm it's wired:

```bash
{cli} --help
```

## Where these instructions come from

You are reading the output of `{cli} skill use-citefinder`, printed from inside the
installed package — so it always matches the CLI you are about to run, and
there is no copy of it anywhere to go stale. The file in
`.claude/skills/use-citefinder/` is only a stub carrying the trigger metadata
and pointing here. The reference material below is printed the same way, by
`doc` commands, so it has no copy on disk either.

To change this content, edit `citefinder/prompts/skills/use-citefinder/` in
the citefinder repo and release; there is nothing to re-copy. `{cli} install --check`
verifies the *stub*, which changes rarely.

## Four core operations

### 1. Verify a single DOI

```python
from citefinder import CrossrefClient

client = CrossrefClient(cache_path="~/.cache/citefinder/crossref.jsonl")
work = client.lookup_doi("10.1126/science.aap9559")
if work is None:
    # 404 — the DOI doesn't resolve. May be fabricated, mistyped, or too new for Crossref's index.
    ...
else:
    print(work["title"][0])
```

CLI (top-level commands default to OpenAlex; use the `crossref` subcommand for Crossref-specific shapes):

```bash
{cli} doi 10.1126/science.aap9559                  # OpenAlex
{cli} crossref doi 10.1126/science.aap9559         # Crossref
```

**Always compare the returned title to the title you expected.** This is the single most important habit. Subagents and PDF extractors regularly produce DOIs that are *off by a few characters* in the suffix (e.g., `psrm.2025.14` vs `psrm.2025.10063`) — those wrong suffixes often resolve to a real-but-different paper in the same journal. The DOI lookup itself returns 200; only a title comparison catches it.

### 2. Search bibliographically

When you don't have a DOI (or the DOI you have is suspect), search by free-form text:

```python
hits = client.search_bibliographic(
    f"{first_author_last_name} {distinctive_title_words}",
    rows=3,
)
for hit in hits:
    print(hit["DOI"], "-", hit["title"][0])
```

CLI:

```bash
{cli} search "Backstabber's Knife Collection"               # OpenAlex (title-only filter)
{cli} crossref search "Wolfowicz hate speech meta-analysis" # Crossref (author + title + year)
```

Note: `{cli} search` (OpenAlex) runs a title-only filter — pass just title words. `{cli} crossref search` accepts free-form bibliographic queries (author + title + year) and is closer in behavior to a generic "find this paper" query.

Tips for good queries:

- First author's last name plus 2–4 distinctive title words is usually enough.
- Avoid generic words ("study", "analysis", "the") — they dilute the relevance score.
- For preprints, both an SSRN/arxiv DOI and a published DOI may come back. Prefer the published one unless the user wants the preprint.

### 3. Look up a book chapter

Many edited volumes follow the convention `{book_doi}.{NNN}` for chapter DOIs (e.g., `10.1017/9781108890960.005` for chapter 5).

```python
chapter = client.lookup_book_chapter("10.1017/9781108890960", 5)
```

CLI:

```bash
{cli} crossref chapter 10.1017/9781108890960 5
```

`lookup_book_chapter` zero-pads numeric chapters to 3 digits. Pass a string instead (`client.lookup_book_chapter(book_doi, "ch1a")`) for publishers using a different format.

### 4. Verify a whole .bib file

When the user has a `.bib` and asks "audit these references" / "check what's wrong" / "which entries don't resolve" — use the bib-verification pipeline rather than calling `lookup_doi` per entry by hand. It parses, resolves DOIs, falls back to bibliographic search, checks four signals (title, year, first-author surname, container), and buckets each entry by status. Given names are never compared — load `{cli} doc use-citefinder/given-names` for the offline check.

CLI:

```bash
{cli} verify refs.bib                       # OpenAlex (default)
{cli} verify refs.bib --source crossref     # ...or Crossref
{cli} verify refs.bib --out path/to/dir/    # custom output directory
{cli} verify refs.bib --min-interval 0.5 --max-retries 5   # slow down for a strict rate limit
```

Output lands in `<cache_dir>/<bib-dir>[-<bib-stem>]/<source>/` — `data/citefinder/` under the working directory when no `cache_dir` is configured (`{cli} config` shows which). `<bib-dir>` is the directory holding the `.bib`; the `-<bib-stem>` suffix is added for a file not named `refs.bib` (`paper/refs.bib` → `paper/`, `paper/extra.bib` → `paper-extra/`):

- `<source>.jsonl` — append-only response cache; re-running is cheap.
- `results.json` — structured per-entry result (status, matched DOI, signals).

Per-entry statuses: `matched` (signals confirm the work; for a DOI hit, `note` may record one disagreeing field), `probable` (one signal disagreed, or too few could be checked — review), `mismatch` (≥2 signals disagreed — DOI to wrong work), `doi-not-found` (404 — common for arXiv/preprint DOIs in Crossref), `unmatched` (no plausible hit), `skip-source` (`@online`/`@misc` — verify via URL), `error`.

**Reading the report.** Read `results.json` by `method` × `status`:

- `method=doi` with `mismatch` — a real defect: the bib's own DOI resolves to a different work. Fix the entry.
- `method=doi` with `probable` — the DOI resolved but the title disagrees, or too few fields could be checked. A title-only disagreement is usually a deficient bib title (one or two words, or copied from the wrong paper), but can be a typoed DOI that lands on a related paper by the same author — compare the two titles in `signals`. When the note says too few signals confirm, fill in the missing author, year, or journal/booktitle and re-run.
- `method=doi` with `matched` and a non-empty `note` — the DOI resolved and the other signals confirm the work, but one field disagrees. With OpenAlex this is usually the source's metadata, not the bib's: it truncates titles at the colon, stores the series name ("Lecture notes in computer science") instead of the booktitle, and reports the online-first or preprint year. Do not rewrite the entry to match the source; for a year disagreement follow `{cli} doc use-citefinder/year-mismatches`.
- `method=search` with `matched` and a non-empty `matched_doi` — a DOI candidate for an entry that lacked one. Confirm the title, then add it.
- `method=search` with `mismatch` / `probable` — usually a wrong-work false positive: books, reports, and other sources the index carries poorly get matched to a similarly titled record. Not a reason to rewrite the entry.
- `unmatched` (or `skip-source` for `@online`/`@misc`) with a "title too short" note — the bib title has fewer than three words, so search cannot tell hits apart. Pick from `candidates` by hand, or complete the title and re-run.
- `unmatched`, `skip-source`, and `doi-not-found` — noise unless they cluster around one publisher or entry type; then look for a systematic cause (a preprint server the source doesn't index, a publisher whose DOI convention the search misses).

Crossref and OpenAlex are complementary — Crossref has richer metadata for indexed records (full title + subtitle, multiple container aliases) but doesn't index arXiv/preprints; OpenAlex covers preprints but sometimes truncates titles or returns preprint years instead of publication years. For a thorough audit, run both and compare.

For programmatic use:

```python
from citefinder import OpenAlexClient, Source, parse_entries, verify_entry

source = Source(name="openalex", client=OpenAlexClient(cache_path="cache.jsonl"))
for entry in parse_entries(open("refs.bib").read()):
    r = verify_entry(entry, source)
    print(r.key, r.status, r.matched_doi)
```

For a quick non-network preview of what's in a `.bib` (useful for sanity-checking parsing or dumping to CSV):

```bash
{cli} bib-to-table refs.bib                          # wide polars table to terminal
{cli} bib-to-table refs.bib --csv > refs.csv         # ...or CSV to stdout
{cli} bib-to-table refs.bib --fields title,year,doi  # subset of columns
```

`bib-to-table` ↔ `table-to-bib` round-trips, so the CSV is also an editing surface — fix entries in a spreadsheet, then regenerate the `.bib`:

```bash
{cli} bib-to-table refs.bib --csv > refs.csv         # edit refs.csv in a spreadsheet
{cli} table-to-bib refs.csv --out refs.bib           # regenerate
```

Field order within each entry is not preserved (it follows the CSV's column order), but keys, entry types, and field values round-trip verbatim. To eyeball two or three columns side by side without mid-token wrapping, load `{cli} doc use-citefinder/inspect-table`.

## Key behaviors to know

- **Cache path:** check for a project config before passing `--cache`. A repo that sets `cache_dir` in `citefinder.toml` (or `[tool.citefinder]` in `pyproject.toml`) already routes every command's cache there — lookups to `<cache_dir>/<source>.jsonl`, `verify` to `<cache_dir>/<bib-dir>[-<bib-stem>]/<source>/` — from any working directory inside it. `{cli} config` prints where a lookup will write and why (`flag`, `env`, `project`, `user`, or `default`). Without a config the default is `~/.cache/citefinder/<source>.jsonl`; pass `--cache-dir` (or `--cache` for one file) only when there is no project config and you want results committed alongside an outline so collaborators don't re-query.
- **Latest value wins on replay.** Re-querying after a fix transparently overwrites — no manual cache invalidation needed.
- **`None` is a real cache value.** A cached `None` means "Crossref returned 404 for this DOI" — citefinder uses it to avoid re-hitting known-missing DOIs. If you suspect Crossref has now indexed a paper it didn't before, delete that line from the JSONL or use a fresh cache path. If another cache in the project already has the record, `{cli} cache merge` supersedes the 404 with it (see *Cache maintenance* below).
- **`lookup_doi` returns the `message` payload directly,** not the full Crossref envelope. So you access `work["title"][0]`, not `work["message"]["title"][0]`.
- **`title` is a list, not a string.** Crossref returns titles as arrays. Use `work["title"][0]`.
- **`search_bibliographic` returns the items list,** which may be empty. Always handle the empty case.
- **Rate limits retry themselves.** A `429` (and `502`/`503`/`504`) is retried up to 3 times, honoring `Retry-After` or backing off 1 s / 2 s / 4 s, and requests are paced by default to the rate each API advertises (OpenAlex 10/s; Crossref 1/s, or 3/s when a `mailto` puts you in its polite pool). Only the final failure surfaces — in `verify` as a per-entry `error` whose note names the status, plus the retry count in the summary line. An error response is **never cached**, so there is nothing to purge after a rate limit: wait for it to clear and re-run, or slow the run down with `--min-interval 0.5` / `--max-retries 5` (also `max_retries` / `min_interval` in `config.toml`).

## Cache maintenance: `{cli} cache`

A project accumulates caches in two shapes: the shared `<cache_dir>/<source>.jsonl` that `doi`/`search` write, and one cache per `verify` run under `<cache_dir>/<bib-dir>[-<bib-stem>]/<source>/`. A `verify` run reads the shared file as a read-only fallback (its header prints a `Fallback:` line with the entry count; `--no-fallback` turns it off), so anything already merged is a cache hit. What a run fetches stays in its own cache, though, so it reaches later runs only after a merge — and a 404 cached before a deposit landed sits next to the record that later resolved it. A `Fallback: ... (0 entries)` line in a project with earlier runs means nothing has been merged yet.

```bash
{cli} cache stats                                # what every cache under cache_dir holds
{cli} cache merge                                # dry run: what consolidating would do
{cli} cache merge --write                        # rewrite <cache_dir>/<source>.jsonl
{cli} cache merge --write --keep-records         # ...never letting a 404 replace a record
{cli} cache compact path/to/openalex.jsonl       # dedupe one file to a line per key
```

- **Merging is always explicit.** No lookup and no `verify` run consolidates as a side effect, and both commands are a dry run until `--write`, so read the report before applying it.
- **Newest `ts` wins across files** (later line within one file), and the winner keeps its own `ts`. Rows are routed by the host in their key, not by the file name, so a record that landed in the wrong file is refiled under the source that answered it.
- **A stale cached 404 is what merging fixes:** a run that got a record for a DOI another file 404'd supersedes it, reported as `404s superseded by a record`. The reverse also happens — check `records superseded by a 404` on every run, and re-run with `--keep-records` if a transient upstream failure is the likelier explanation than a real removal.
- **Inputs are never modified.** `merge` only rewrites the shared cache, so each `verify` run's evidence stays beside its `results.json`. Do not merge while a lookup or `verify` run is writing into the same directory; re-run it afterwards instead.
- `merge_caches`, `summarize_caches`, `read_records`, and `write_records` are importable from `citefinder` if you need this in a script, as are the `MergeStats` and `SourceStats` dataclasses they report through.

## Reference material

Detail a step consults rather than something every lookup needs. Load each one
at the step that calls for it:

- `{cli} doc use-citefinder/openalex` — the OpenAlex fallback for arXiv /
  preprint / thin-metadata DOIs, the Crossref ↔ OpenAlex field map, which
  fields carry the family/given name split, abstracts, the OpenAlex API key
  and config files, and picking `mailto`.
- `{cli} doc use-citefinder/given-names` — the offline given-name and
  diacritics check over a `verify` run's cache, with a runnable recipe.
- `{cli} doc use-citefinder/year-mismatches` — when Crossref and OpenAlex
  disagree on a work's year, and which year to cite.
- `{cli} doc use-citefinder/inspect-table` — a side-by-side plain-text view of
  `bib_to_table` columns for ad-hoc audits.

## When citefinder isn't enough

For **generating formatted BibTeX strings** from a DOI or query, use [`fetchbib`](https://github.com/mr-devs/fetchbib) (`fbib`) instead — it handles doi.org content negotiation, arXiv routing, and BibTeX-flavored config (protect titles, exclude ISSN, etc.). citefinder returns raw JSON for verification; fetchbib emits paste-ready BibTeX for citation lists.

Drop down to raw HTTP (`requests.get("https://api.crossref.org/...")`) only if you need:

- Crossref or OpenAlex endpoints citefinder doesn't wrap (Crossref `/funders`, `/journals`, `/types`; OpenAlex `/authors`, `/institutions`, `/sources`).
- A one-off query you specifically don't want cached.
- Streaming through large result sets via `cursor` pagination.

For everything else, prefer citefinder so the cache stays the single source of truth across sessions.
