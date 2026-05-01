---
status: active
branch: feature/absorb-bib-verification
created: 2026-05-01T09:27:40-07:00
completed:
pr:
---

# Absorb bib verification scripts into the package

## Plan

Promote the modular logic in `scripts/signals.py` and `scripts/verify-bib.py`
into the `citefinder` package so it can be imported, tested, and maintained as
first-class library code rather than one-off scripts.

### Scope

**`scripts/comparison.py`** → `citefinder/signals.py` (rename + verbatim
move). The module is the source-agnostic "signal layer": canonical types
in, per-signal verdicts computed, status reduced out. Renamed from
`comparison` because that name was generic — `signals` describes what's
actually inside.

- `Status` (StrEnum), `BibCitation`, `Work` (dataclasses)
- Pure signal-check functions: `normalize_title`, `title_similarity`,
  `container_similarity`, `check_title`, `check_year`, `check_author`,
  `check_container`, `compute_signals`, `status_from_signals`

**`scripts/verify-bib.py`** — split as follows:

1. **BibTeX parsing** → `citefinder/bib.py`
   - `parse_entries(text: str) -> list[Entry]` — bibtexparser v2 wrapper
   - `Entry` dataclass
   - Helpers: `strip_braces`, `first_author_surname`, `build_search_query`,
     `build_title_query`
   - `citation_from_entry(entry: Entry) -> BibCitation`

2. **Source adapters** → `citefinder/adapters.py`
   - `crossref_to_work`, `openalex_to_work` and their private helpers
   - Pure functions over the source JSON shape — no client imports.

3. **Verification orchestration** → `citefinder/verify.py`
   - `Source` dataclass (thin wrapper around `CrossrefClient` / `OpenAlexClient`)
   - `verify_entry(entry, source) -> Result`
   - `Result` dataclass
   - Constants: `SKIP_SOURCE_TYPES`, `TITLE_MATCH_THRESHOLD`

   `Source` lives here (not in `adapters.py`) because it holds a live client.
   Keeping `adapters.py` import-free of the clients lets the adapters be
   tested as pure JSON-shape transforms.

4. **CLI entry point** — wire two top-level Typer commands:
   - `citefinder parse <bib> [--out <file>]` — parse a `.bib` file and emit
     CSV. Default to stdout for piping; `--out` writes to a file. Columns:
     `key, etype, title, author, year, doi, container` where `author` is the
     **first-author surname** (the form used downstream for matching) and
     `container` is `journal` or `booktitle`. No network calls.
   - `citefinder verify <bib>` — run the full verification pipeline (replaces
     `scripts/verify-bib.py main()`). Accepts `--source`, `--mailto`, `--out`.

### Public API (`citefinder/__init__.py`)

Re-export the names users will import directly. Implementation-detail
helpers (the individual `check_*` functions, normalization helpers,
bib-side query builders, internal constants) stay module-private —
importable from their module if needed, but not surfaced from the
package root.

New additions to `__all__`:
- From `bib`: `Entry`, `parse_entries`
- From `signals`: `Status`, `BibCitation`, `Work`, `compute_signals`,
  `status_from_signals`
- From `adapters`: `crossref_to_work`, `openalex_to_work`
- From `verify`: `Source`, `Result`, `verify_entry`

### Dependencies

`bibtexparser>=2.0.0b0` is currently in the `dev` group of `pyproject.toml`.
Promote it to runtime `dependencies` since `citefinder/bib.py` will import it
at module load time.

### Module layout after this plan

```
citefinder/
├── __init__.py          # re-export public API
├── _base.py             # existing
├── cache.py             # existing
├── client.py            # existing (Crossref)
├── openalex.py          # existing
├── bib.py               # NEW: Entry, parse_entries, bib helpers
├── signals.py        # NEW: Work, BibCitation, Status, signal checks
├── adapters.py          # NEW: crossref_to_work, openalex_to_work (pure)
├── verify.py            # NEW: Source, Result, verify_entry, constants
└── cli.py               # UPDATED: add `parse` and `verify` commands
```

### Migration steps

1. Create `citefinder/signals.py` from `scripts/comparison.py` — minimal
   changes (adjust imports, confirm tests pass).
2. Create `citefinder/bib.py` — `Entry`, `parse_entries`, and bib helpers.
3. Create `citefinder/adapters.py` — `Work` adapters and `Source` wrapper.
4. Create `citefinder/verify.py` — `Result`, `verify_entry`, constants.
5. Add `bibtexparser` dependency.
6. Update `citefinder/__init__.py` `__all__` with new public names.
7. Add `citefinder parse` and `citefinder verify` commands in `cli.py`.
8. Delete `scripts/verify-bib.py` and `scripts/comparison.py` — the CLI
   covers the use case 1:1, no shims (they rot).
9. Add tests under `tests/`: `test_bib.py` (parse_entries, name parsing,
   query builders), `test_signals.py` (signal checks, status reduction),
   `test_adapters.py` (Crossref/OpenAlex JSON → Work fixtures), and a small
   `test_verify.py` covering `verify_entry` against a fake `Source`.

### What stays in `scripts/`

Pipeline runners with no reusable logic (e.g., a future `summarize-bib.py`)
can stay as scripts. Only logic worth unit-testing or re-using across entry
points moves into the package. After this plan, `scripts/` should be empty
of the absorbed files.
