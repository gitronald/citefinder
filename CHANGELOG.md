# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- `verify` keeps a DOI-resolved entry `matched` when exactly one non-title
  signal disagrees and at least two confirm, and records the disagreement in
  `note` (`DOI resolved; source disagrees on: year (...)`). OpenAlex truncates titles at the colon,
  stores the LNCS series name instead of the booktitle, and reports preprint
  years; those no longer land such entries in `probable`. A title
  disagreement, or two disagreeing signals, still does.
- The title check no longer passes on a bib title of fewer than three words,
  and search no longer selects a candidate on such a title: the entry is
  `unmatched` (`skip-source` for `@online`/`@misc`) with a "title too short"
  note and the hits stay in `candidates`. A one-word title such as "Influence" previously matched any
  record containing the word.
- The title check reports `unknown` rather than `fail` when one title is a
  truncation of the other (subtitle dropped on either side) and the shorter
  side still has at least three words.
- Title signals carry a `note` when the verdict was downgraded for either
  reason above.
- The report-reading guide in the skill body and README distinguishes
  `method=doi` `probable` (title disagrees, or too few signals) from
  `matched` with a note (one non-title field disagrees, usually source-side
  metadata loss).

### Added

- `MIN_TITLE_TOKENS` and `title_tokens` in `citefinder.signals`, and a
  `doi_resolved` keyword on `status_from_signals` that applies the DOI rule
  above.

## [0.7.0] - 2026-09-02

### Added

- Project-level config: the CLI reads the nearest `citefinder.toml`, or a
  `[tool.citefinder]` table in `pyproject.toml`, found by walking up from the
  working directory. Same keys as the user config; precedence is flag > env
  and `.env` > project config > user config > default. An `api_key` in a
  project config is ignored with a warning, since the file is meant to be
  committed.
- `cache_dir` setting — a top-level key in either config file, the
  `CITEFINDER_CACHE_DIR` env var, or `--cache-dir` on `doi`, `search`,
  `verify`, and the `crossref` subcommands — as the one directory every
  cache path derives from: lookups use `<cache_dir>/<source>.jsonl` and
  `verify` writes under `<cache_dir>/<bib-stem>/<source>/`. A relative value
  in a config file resolves against that file's directory; a relative flag
  or env value against the working directory. `--cache` and `--out` still
  win.
- `citefinder config` prints each resolved setting with its source (`flag`,
  `env`, `project`, `user`, or `default`) and the cache paths the lookups
  and `verify` would use.
- `resolve_cache_path(source, cache_dir=None)`, exported from the package,
  for wrappers that build clients themselves.

### Changed

- `verify`'s default output directory derives from `cache_dir` when one is
  set; without one it is `data/citefinder/<bib-stem>/<source>/` under the
  working directory, as before.
- `--cache` on the lookup commands defaults to `<cache_dir>/<source>.jsonl`
  rather than a fixed home-directory path — the same
  `~/.cache/citefinder/<source>.jsonl` when nothing sets `cache_dir`.
- `verify --out` is anchored to the working directory and expands a leading
  `~`, the same as `--cache-dir`, so `results.json` and the `<source>.jsonl`
  cache always land in one directory.

## [0.6.0] - 2026-09-02

### Added

- Both clients retry a `429`, `502`, `503`, or `504` response, up to
  `max_retries` times (default 3), honoring `Retry-After` (delta-seconds or
  HTTP-date, capped at `max_wait`, default 60 s) and otherwise backing off
  exponentially from `backoff_base` (default 1 s) with jitter. Other 4xx
  responses still raise immediately, 404 is still cached as `None`, and an
  error response is never written to the cache.
- `min_interval` paces consecutive requests from one client instance; cache
  hits are not paced.
- Constructor knobs `max_retries`, `backoff_base`, `max_wait`, and
  `min_interval` on `CrossrefClient` and `OpenAlexClient`; `--max-retries`
  and `--min-interval` on `doi`, `search`, `verify`, and the `crossref`
  subcommands; `max_retries` / `min_interval` keys under `[openalex]` and
  `[crossref]` in `config.toml` (env: `OPENALEX_MAX_RETRIES`,
  `OPENALEX_MIN_INTERVAL`, `CROSSREF_MAX_RETRIES`, `CROSSREF_MIN_INTERVAL`).
  Each knob must be finite and non-negative: the constructors raise
  `ValueError` otherwise, and the CLI exits 2.
- Each retry logs a warning on the `citefinder` logger, and the client's
  `retries` counter is reported in the `verify` summary line.
- The skill and README carry a guide to reading `verify` output by
  `method` × `status`, and the skill notes which source fields carry the
  family/given name split (Crossref's `author[i].family` / `given`).

### Changed

- `OpenAlexClient` paces requests at `min_interval=0.1` s by default,
  matching OpenAlex's documented 10 requests per second. Pass
  `min_interval=0` to restore the previous unpaced behavior.

## [0.5.0] - 2026-09-01

### Added

- The `use-citefinder` Claude Code skill now ships inside the package, at
  `citefinder/prompts/skill.md`, and `citefinder skill` prints it on demand.
  The instructions an agent reads therefore always come from the installed
  version — there is no copy of them on disk to fall out of date.
- `citefinder install` materializes a small dispatcher stub into
  `~/.claude/skills/use-citefinder/` (or `--local`, into the current repo's
  `.claude/`): the skill's frontmatter triggers plus a pointer to `citefinder
  skill`, stamped with the version and mode it was rendered for.
- `citefinder install --check` reports `ok` / `drifted` / `missing` for the
  stub and exits non-zero unless `ok`. `--force` re-materializes, and is also
  required to overwrite a file at the target path that citefinder did not
  generate.

### Changed

- Dependency bumps: `polars` 1.40.1 → 1.44.1 and `python-dotenv` 1.2.2 →
  1.2.3 (runtime), plus dev tools `pytest` 9.1.1, `ruff` 0.16.4, and
  `pre-commit` 4.6.2. The `setup-uv` CI action moved to v10.0.1.

## [0.4.3] - 2026-08-24

### Fixed

- Cache paths given with a leading `~` (e.g.
  `cache_path="~/.cache/citefinder/openalex.jsonl"`) now expand to the
  user's home directory instead of creating a literal `~/` directory under
  the current working directory.

### Security

- `urllib3` bumped 2.6.3 → 2.7.0: fixes two high-severity advisories
  (decompression-bomb safeguards bypassed in parts of the streaming API;
  sensitive headers forwarded across origins in proxied low-level redirects).
- `idna` bumped 3.13 → 3.19: fixes a bypass of the CVE-2024-3651 fix in
  `idna.encode()`.

### Changed

- Consolidated Dependabot bumps: `requests` lower bound raised to 2.34.2,
  `typer` 0.27.1 (upstream drops its `click` dependency), and dev tools
  `ruff` 0.16.3, `pyrefly` 1.2.0, and `types-requests` 2.33.0.20260712.
  Pre-commit ruff hook rev aligned to v0.16.3, and ruff 0.16's new markdown
  code-block formatting applied to the README and skill docs.
- GitHub Actions: every workflow action is now pinned to a commit SHA —
  `checkout` v7.0.1, `setup-uv` v9.0.0, `upload-artifact` v7.0.1,
  `download-artifact` v8.0.1, and `gh-action-pypi-publish` v1.14.2
  (previously tracking the mutable `release/v1` branch).
- Dependabot config: updates are grouped per ecosystem with cooldown
  windows, PRs now target `dev`, and the `semver-major-days` cooldown key
  (unsupported for the `github-actions` ecosystem, and grounds for GitHub
  rejecting the whole config) was removed.
- Test workflow: `UV_PYTHON` pinned per matrix cell so each cell tests its
  own interpreter instead of silently re-resolving to `.python-version`.
- The published sdist is now allowlisted to the package source, README,
  changelog, and license — internal plan files, agent tooling, and tests no
  longer ship to PyPI.

## [0.4.2] - 2026-05-07

### Removed

- CLI `citefinder parse <bib>`: superseded by `citefinder bib-to-table` (wider,
  faithful tabulation that round-trips through `table-to-bib`).

## [0.4.1] - 2026-05-07

### Added

- New `citefinder.bib_table` module: `bib_to_table` tabulates a `.bib` file
  into a wide polars DataFrame (one row per entry, one column per field);
  `table_to_bib` is the inverse, regenerating BibTeX from the table.
- CLI `citefinder bib-to-table <bib>`: emits a polars table to the terminal,
  or CSV to stdout via `--csv`. `--fields` filters to a subset of columns.
- CLI `citefinder table-to-bib <csv>`: converts a CSV (from
  `bib-to-table --csv`) back into a `.bib` file. Round-trip-safe except for
  field ordering within entries.

### Changed

- `polars` promoted to a runtime dependency (used by `bib_table`).

## [0.4.0] - 2026-05-01

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
