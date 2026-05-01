# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- New `citefinder.bib` module: BibTeX parsing via bibtexparser v2 (`Entry`,
  `parse_entries`, `first_author_surname`, query builders).
- New `citefinder.signals` module: source-agnostic signal layer with
  canonical `Work` and `BibCitation` shapes, four per-signal checks (title,
  year, first-author surname, container), and `status_from_signals` reduction
  to a `Status` verdict (`matched`, `probable`, `mismatch`, `doi-not-found`,
  `unmatched`, `skip-source`, `error`).
- New `citefinder.adapters` module: pure JSON-shape transforms
  (`crossref_to_work`, `openalex_to_work`) for adapting source-specific
  records into the canonical `Work` shape.
- New `citefinder.verify` module: orchestration layer with `Source` (a thin
  wrapper around `CrossrefClient` / `OpenAlexClient`), `Result`, and
  `verify_entry` for per-entry verification against either source.
- CLI `citefinder parse <bib>`: parse a `.bib` file and emit CSV with columns
  `key, etype, title, author, year, doi, container` (first-author surname).
  Defaults to stdout; `--out PATH` writes to a file. No network calls.
- CLI `citefinder verify <bib>`: verify a `.bib` against Crossref or OpenAlex.
  Defaults to OpenAlex; `--source crossref` switches. Writes a JSONL response
  cache and a structured `results.json` to `data/citefinder/<stem>/<source>/`.
- Configuration via `~/.config/citefinder/config.toml` (honors
  `$XDG_CONFIG_HOME`): a lowest-priority fallback for `OPENALEX_API_KEY`,
  `OPENALEX_MAILTO`, and `CROSSREF_MAILTO`. Project-local `.env` and shell
  env still override.
- New env vars `OPENALEX_MAILTO` and `CROSSREF_MAILTO`. The `--mailto` flag
  now reads from the source-appropriate variable.
- Tests for the new modules: `test_bib.py`, `test_signals.py`,
  `test_adapters.py`, `test_verify.py` (54 new cases).

### Changed

- `bibtexparser>=2.0.0b0` promoted from `dev` to runtime `dependencies` —
  required at module load by `citefinder.bib` and `citefinder.adapters`.
- Public API expanded in `citefinder/__init__.py` to re-export the new
  parsing, verification, and signal-layer surface.

### Removed

- `.claude/settings.local.json` is no longer tracked (now in `.gitignore`).
