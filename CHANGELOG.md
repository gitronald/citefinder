# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.1] - 2026-05-01

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

## [0.3.0] - 2026-04-29

### Added

- `OpenAlexClient.search_title`: title-only search via OpenAlex
  `filter=title.search:`. Normalizes apostrophes (curly U+2019, since
  OpenAlex's title index stores them that way) and strips filter-reserved
  characters (`,`, `:`, `|`, `!`) that would 400 the request.
- `--mailto` flag on the `crossref` subcommand for the Crossref polite pool.
- New `citefinder._base.CachedJsonClient` base: shared HTTP + cache + mailto
  plumbing for both clients.

### Changed

- **Top-level CLI commands now default to OpenAlex.** `citefinder doi` and
  `citefinder search` hit OpenAlex; Crossref is reachable via the `crossref`
  subcommand (`citefinder crossref doi`, `... search`, `... chapter`). The
  rationale: OpenAlex covers Crossref + arXiv + preprints + repositories,
  so a single command works for the broadest range of citations.
- `mailto` handling lifted into `CachedJsonClient` so cache keys strip the
  `mailto` query param uniformly across both clients (rotating the email
  doesn't invalidate prior cache entries).
- `is_arxiv_doi` moved from the top level into `citefinder.openalex` for
  cohesion with the routing logic.
- README expanded: schema-difference table between Crossref and OpenAlex,
  CLI args section, note on the SQLite alternative to JSONL caching.

## [0.2.1] - 2026-04-28

### Changed

- `use-citefinder` skill expanded with API-key setup, mailto guidance, and a
  pointer to `fetchbib` for BibTeX-string generation.
- GitHub Actions: bumped `actions/upload-artifact` to 7,
  `actions/download-artifact` to 8, and `astral-sh/setup-uv` to 8.1.0.

## [0.2.0] - 2026-04-28

### Added

- `OpenAlexClient`: lookups against `https://api.openalex.org` with the same
  JSONL caching contract as `CrossrefClient`. Supports `lookup_doi`,
  `search`, optional `mailto` for the polite pool, and an `api_key`
  (read from constructor arg, `OPENALEX_API_KEY` env var, or `.env` loaded
  by the CLI; sent as `Authorization: Bearer ...` so it never lands in cache
  keys or logs).
- `is_arxiv_doi(doi)` helper for routing arXiv DOIs (`10.48550/arXiv.*`)
  to OpenAlex, since Crossref doesn't index them.
- `reconstruct_abstract(work)` helper: reassembles OpenAlex's
  `abstract_inverted_index` (`{word: [positions]}`) into plain text.
- CLI `openalex` subcommand: `doi`, `search` for ad-hoc OpenAlex lookups.
- `use-citefinder` skill (`.claude/skills/use-citefinder/SKILL.md`) with
  guidance for DOI verification, bibliographic search, book-chapter
  resolution, and OpenAlex fallback.

## [0.1.0] - 2026-04-27

### Added

- `CrossrefClient`: DOI lookups, bibliographic search, and book-chapter
  resolution against `https://api.crossref.org`. Supports a `mailto` for
  Crossref's polite pool (faster, higher quota).
- `JsonlCache`: append-only JSONL key-value store. Cache key strips
  request-specific noise so equivalent requests share entries; negative
  results (404s) are cached as `None` so known-missing DOIs aren't re-hit.
- Typer CLI entry point (`citefinder`) with `doi`, `search`, and `chapter`
  commands.
- GitHub Actions: lint + type check + test matrix (Python 3.11–3.14) on
  push/PR to `dev`/`main`.
