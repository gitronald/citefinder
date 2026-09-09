---
id: 16
slug: cache-maintenance-commands
status: active
branch: feature/cache-maintenance-commands
created: 2026-09-09T10:19:50-07:00
concluded:
pr: https://github.com/gitronald/citefinder/pull/59
---

# Add cache maintenance commands: merge, compact, and stats

## Plan

### Problem

A project's cache directory holds many JSONL files, and nothing in the package
consolidates or inspects them.

Two layouts write into it. `doi`, `search`, and the `crossref` subcommands use
the shared per-source cache, `<cache-dir>/<source>.jsonl` (`resolve_cache_path`).
`verify` writes a cache per run, `<cache-dir>/<bib-dir>[-<bib-stem>]/<source>/<source>.jsonl`
(`_verify_out_dir`, plan 010) — deliberately, so runs over different directories
stay apart and each run's evidence sits beside its own `results.json`.

The consequence is that the caches never learn from each other:

- A DOI fetched by one `verify` run is invisible to the next, which refetches it.
  Refetching is what provokes the rate limiting plan 008 added retries for, so
  the cost compounds exactly where the corpus overlaps.
- A cached 404 (`value: null`) in one file is invisible to another file that
  holds a real record for the same key — a DOI that 404'd before a deposit
  landed and resolved months later now lives in two files with no rule saying
  which is current.
- There is no way to ask what the cache holds. `drift` reports keys the records
  carry that the models lack, which is a schema question, not an inventory one.

The pieces to build this exist but do not compose. `read_records` returns every
`{key, value, ts}` row of a file with its timestamp; `JsonlCache._replay`
discards `ts` and keeps only the value, so the cache class cannot express a
merge — latest-line-wins is correct within one append-only file and meaningless
across independent ones. And `read_records` is public in `citefinder.cache` but
missing from `__all__`, so a caller reaching for the only timestamp-preserving
reader is reaching past the package's declared surface.

### Design

**Order by `ts`, not by line.** Within one file, line order is time order, so
latest-line-wins is right. Across files, line order carries no information: two
caches are independent logs. The merge sorts rows by `ts` and lets the newest
win, and it keeps the winning row's original `ts` rather than restamping — `ts`
is the fetch time, the only freshness signal a row carries.

**A missing `ts` is possible.** `read_records` validates `key` and `value` only,
so a hand-edited or externally produced row can arrive without one. Treat it as
the oldest possible timestamp (it loses every contest) and report the count
rather than dropping the row or crashing.

**Route by host, not by filename.** `CacheRow` already documents that the file
name does not guarantee the source, "because a misdirected record has been seen
in practice". A merge that trusts filenames would launder one such record into
the shared cache permanently. Route each row by the host in its `key`
(`api.crossref.org` / `api.openalex.org`), report anything that lands somewhere
other than its file suggests, and keep unroutable rows out of the output.

**Null-versus-record needs a decision, not a default.** Under pure `ts`-wins, a
key that resolved in March and 404s in September ends as a null, discarding a
good record on the strength of a transient upstream failure. Recommend
`ts`-wins as the default with the count of nulls-superseding-records reported
every run, plus `--keep-records` to make a null never supersede a real record.
The inverse case — a null superseded by a record — is unambiguous and is the
main thing the merge is for; report it as the headline number.

**Writes are atomic and never in place.** Write `<path>.tmp` and `os.replace`.
On a synced or network filesystem a replace over an open handle fails outright
rather than corrupting the file, which is the desired outcome; an in-place
rewrite races a concurrent appender and loses rows.

### Commands

A `cache` subcommand group (`app.add_typer`, as `crossref` already does), all
taking the existing `--cache-dir` resolution so a project config points them at
the same directory as every other command.

- **`cache stats`** — per source: files found, rows, distinct keys split into
  DOI lookups and searches, cached 404s, rows with no `ts`, and the newest `ts`.
  Fails loudly when the directory is missing or unreadable rather than reporting
  zeros: a dropped mount and an empty cache must not look alike.
- **`cache merge`** — reads every `<cache-dir>/**/<source>.jsonl` plus any
  `--extra <path>` (a copy from elsewhere, or a sync tool's conflicted
  duplicate), merges as above, and rewrites the shared `<cache-dir>/<source>.jsonl`
  as one line per key. Dry run by default; `--write` to apply. Reports rows read,
  distinct keys, keys where a later row replaced an earlier one, nulls
  superseded by records, records superseded by nulls, and misrouted rows.
- **`cache compact <path>`** — the same merge over a single file: dedupe to one
  line per key, preserving each winner's `ts`. Idempotent, and the natural thing
  to run on a shared cache that has grown a long tail of repeat lookups.

Per-run `verify` caches are inputs to `merge`, never outputs. Nothing this plan
adds deletes or rewrites them, so a run's evidence stays beside its
`results.json` and consolidation stays a separate, reversible step.

### API surface

- Export `read_records` in `__all__`.
- Put the merge itself in `cache.py` as an importable function over paths
  returning the winning rows plus a summary, so the CLI command is a thin
  wrapper and a caller can consolidate without shelling out.
- `JsonlCache` stays single-path. Merging is maintenance, not a read path, and a
  multi-path constructor would invite it into the hot path where replaying every
  file on every client construction is exactly the cost to avoid.

### Implementation order

1. The merge function in `cache.py` with unit tests: `ts` ordering across files,
   `ts` preservation, a missing `ts`, one line per key, idempotent compaction, a
   null superseded by a record and the reverse, `--keep-records`, and a row
   whose host disagrees with its file.
2. `read_records` into `__all__`.
3. `cache stats`.
4. `cache merge` (dry run, then `--write` with the tmp-and-replace path) and
   `cache compact`.
5. `CHANGELOG.md`, `README.md`, and the skill text: what the commands do, and
   that a merge is a deliberate step rather than something any other command
   triggers.

### Out of scope

- **Any change to where `verify` writes.** The per-run cache directory is the
  point of plan 010 and stays authoritative for its run.
- **Automatic merging.** No command consolidates as a side effect; a merge is
  always explicit, so a bad row is never laundered into the shared cache by a
  routine lookup.
- **Offline query over a merged cache** (`lookup <doi>`, title search across
  cached records). Plausible next step, but the useful version joins the cache
  to something outside it, so the output shape should settle in a caller first.
- **A different store format.** Append-only JSONL survives sync and interrupted
  writes, and `read_records` already degrades one torn line rather than the file.
  A derived index is a follow-up only if replay latency becomes a real problem.
- **`parse_entries` failure reporting.** It logs a warning and drops a block it
  cannot parse (duplicate field key, repeated citation key, unterminated brace)
  with no programmatic signal, so a caller cannot count or surface them. Real,
  unrelated to caches, and its own small plan.
- **A reserved-column guard on `bib_to_table`.** It refuses fields colliding with
  `key`/`entry_type`, but a caller adding its own columns to the frame has no way
  to extend that set. Also its own small plan; a caller can check locally today.

### Open questions

- Whether `--keep-records` should be the default. It is the safer behavior and
  the harder one to explain; leaving `ts`-wins as the default keeps one rule for
  the whole merge, with the count reported every run.
- Whether `stats` should read the per-run caches at all, or only the shared one.
  Reading everything makes it the inventory command; reading one file makes it
  cheap enough to run habitually.
- Whether `merge` should refuse when a source file is being appended to
  concurrently. Detecting that portably is awkward; documenting "do not merge
  during a run" may be the honest limit.

## Log

**2026-09-09** — Implemented on `feature/cache-maintenance-commands`
([PR #59](https://github.com/gitronald/citefinder/pull/59)), in the order the
plan set out: merge core plus unit tests, exports, then `stats`, `merge`, and
`compact`, then the docs.

Three things came out differently from the spec, all found by running the
commands against a cache shaped like a real one:

- **`merge` globs `**/*.jsonl`, not `**/<source>.jsonl` per source.** With the
  per-source glob, a misrouted row was reported and then dropped: the openalex
  record sitting in `crossref.jsonl` never reached the openalex merge, because
  that pass only opened files named `openalex.jsonl`. Routing by host is
  pointless if the file name still decides which rows get *considered*, so
  every cache file is now offered to every source's merge. A consequence: the
  targets are inputs too, so every source is merged before anything is
  written — writing `crossref.jsonl` mid-run would take the misrouted row out
  from under the pass about to rehome it.
- **Unroutable rows are dropped only when merging into a source's cache.** The
  plan's rule ("keep unroutable rows out of the output") is about not
  laundering a foreign row into `<source>.jsonl`. Applied to `compact`, whose
  output *is* its input, the same rule silently deletes readable rows from
  someone's file. `merge_caches(source=...)` drops them; `source=None`, what
  `compact` uses, reports and keeps them.
- **The counters are computed per key at the end, not as rows arrive.** The
  first pass counted a replacement whenever a row displaced the one held so
  far, which made `replaced` and `nulls_superseded` depend on the order the
  files happened to be globbed in — the same two caches reported 1 or 0
  depending on which was read first. Each key now collects its rows and
  resolves once (`_Contest`), so a report describes the caches, not the walk.
  `misrouted` is likewise counted only for rows a merge keeps, so the number
  says how many records the run actually rehomes.

Open questions, as resolved:

- **`--keep-records` is not the default.** `ts`-wins stays the single rule for
  the whole merge, with `records superseded by a 404` printed on every run
  (even at zero) so the case is visible in the run it happens in.
- **`stats` reads every cache under the directory**, per-run ones included —
  it is the inventory command, and the plan's own "files found" per source
  only means something if it walks them all.
- **`merge` does not detect a concurrent appender.** Documented as a limit in
  the README and the skill ("do not merge during a run; re-run afterwards")
  rather than guessed at portably. The atomic replace means the failure mode
  is missing rows, not a corrupt file, and re-running picks them up.

Coverage held at 97.29% against the 97.0 floor; full suite 360 passed.
