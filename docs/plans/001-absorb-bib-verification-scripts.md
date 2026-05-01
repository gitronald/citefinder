---
status: draft
branch: feature/absorb-bib-verification
created: 2026-05-01T09:27:40-07:00
completed:
pr:
---

# Absorb bib verification scripts into the package

## Plan

Promote the modular logic in `scripts/comparison.py` and `scripts/verify-bib.py`
into the `citefinder` package so it can be imported, tested, and maintained as
first-class library code rather than one-off scripts.

### Scope

**`scripts/comparison.py`** — entirely library-worthy. Contains:
- `Status` (StrEnum), `BibCitation`, `Work` (dataclasses)
- Pure signal-check functions: `normalize_title`, `title_similarity`,
  `container_similarity`, `check_title`, `check_year`, `check_author`,
  `check_container`, `compute_signals`, `status_from_signals`

Move these verbatim into `citefinder/comparison.py` (or `citefinder/verify.py`
— decide during implementation based on what name feels more natural to importers).

**`scripts/verify-bib.py`** — mixed. Break it into:

1. **BibTeX parsing** → `citefinder/bib.py`
   - `parse_entries(text: str) -> list[Entry]` — bibtexparser v2 wrapper
   - `Entry` dataclass
   - Helpers: `strip_braces`, `first_author_surname`, `build_search_query`,
     `build_title_query`
   - `citation_from_entry(entry: Entry) -> BibCitation`

2. **Source adapters** → `citefinder/adapters.py` (or fold into `bib.py` if
   small enough)
   - `crossref_to_work`, `openalex_to_work` and their private helpers
   - `Source` dataclass (thin wrapper over the two clients)

3. **Verification orchestration** → `citefinder/verify.py` (or keep as a CLI
   entry point)
   - `verify_entry(entry, source) -> Result`
   - `Result` dataclass
   - Constants: `SKIP_SOURCE_TYPES`, `TITLE_MATCH_THRESHOLD`

4. **CLI entry point** — wire a `citefinder verify-bib` subcommand (Typer) that
   replicates what `scripts/verify-bib.py main()` does today. Keep the script
   in place during transition if needed, or replace it with a thin shim.

### Dependencies

`bibtexparser` (v2) is not yet in `pyproject.toml`. Add it as a runtime
dependency (`uv add bibtexparser`).

### Module layout after this plan

```
citefinder/
├── __init__.py          # re-export public API
├── _base.py             # existing
├── cache.py             # existing
├── client.py            # existing (Crossref)
├── openalex.py          # existing
├── bib.py               # NEW: Entry, parse_entries, bib helpers
├── comparison.py        # NEW: Work, BibCitation, Status, signal checks
├── adapters.py          # NEW: crossref_to_work, openalex_to_work, Source
├── verify.py            # NEW: Result, verify_entry, constants
└── cli.py               # UPDATED: add `verify-bib` subcommand
```

### Migration steps

1. Create `citefinder/comparison.py` from `scripts/comparison.py` — minimal
   changes (adjust imports, confirm tests pass).
2. Create `citefinder/bib.py` — `Entry`, `parse_entries`, and bib helpers.
3. Create `citefinder/adapters.py` — `Work` adapters and `Source` wrapper.
4. Create `citefinder/verify.py` — `Result`, `verify_entry`, constants.
5. Add `bibtexparser` dependency.
6. Update `citefinder/__init__.py` `__all__` with new public names.
7. Add `verify-bib` CLI subcommand in `cli.py`.
8. Update `scripts/verify-bib.py` to import from the package (thin shim) or
   delete it if the CLI covers the use case.
9. Write or move tests from any existing test scripts into `tests/`.

### What stays in `scripts/`

Scripts that are one-off pipeline runners (e.g., `summarize-bib.py`) and don't
contain reusable logic can stay as scripts. Only logic that is worth unit-testing
or re-using across multiple entry points moves into the package.
