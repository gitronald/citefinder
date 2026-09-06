# Claude Settings

This file provides guidance to [Claude Code](claude.ai/code).

## Package Structure

```
citefinder/
├── __init__.py         # public API re-exports
├── _base.py            # CachedJsonClient base for the HTTP clients
├── cache.py            # JsonlCache (append-only JSONL key-value store)
├── client.py           # CrossrefClient
├── config.py           # resolve_cache_path + config-file discovery/loading (read by the CLI; never auto-loaded by the library)
├── openalex.py         # OpenAlexClient + abstract reconstruction + arXiv routing
├── bib.py              # Entry, parse_entries, bib-side query helpers
├── signals.py          # Status, BibCitation, Work, signal checks, status reduction
├── models.py           # TypedDicts for raw Crossref/OpenAlex records + undeclared_keys/cache_drift drift check
├── adapters.py         # crossref_to_work, openalex_to_work (pure JSON adapters)
├── verify.py           # Source, Result, verify_entry orchestration
├── bib_table.py        # bib_to_table / table_to_bib (bib <-> wide polars DataFrame)
├── install.py          # stub render/stamp/drift-check for the Claude Code skill
├── prompts/skill.md    # canonical `use-citefinder` skill body (package data)
└── cli.py              # Typer CLI: doi, search, verify, bib-to-table, table-to-bib, drift, config, skill, install, crossref subcommand
```

The `use-citefinder` skill follows the
[planners](https://github.com/gitronald/planners) pattern: the body lives only
in `citefinder/prompts/skill.md` and is printed by `citefinder skill`.
`.claude/skills/use-citefinder/SKILL.md` is a **generated stub** — frontmatter
triggers plus a pointer to that command — written by `citefinder install
--local`. Edit the prompt body; the stub does not need regenerating unless the
frontmatter or the stub template itself changes. `citefinder install --check`
reports stub drift.

The four bib-verification modules (`bib`, `signals`, `adapters`, `verify`)
were absorbed from external scripts in plan
[`001-absorb-bib-verification-scripts.md`](.planners/plans/001-absorb-bib-verification-scripts/plan.md)
— `signals.py` is shape-independent, `adapters.py` is the per-source JSON
boundary, `verify.py` orchestrates lookups against a `Source`. The raw record
shapes those adapters read are declared in `models.py` as deliberately
incomplete `TypedDict`s (plan
[`014-source-record-models`](.planners/plans/014-source-record-models/plan.md)):
every key is optional, coverage comments say how often a surveyed record
carried it, and `citefinder drift <cache.jsonl>` (`cache_drift` in the
library) reports what real records carry that the model does not. Add a key
when a consumer starts reading it, or when drift shows it on most records.

## Configuration

The CLI reads two TOML files with the same keys: a committed **project
config** (`citefinder.toml`, or `[tool.citefinder]` in `pyproject.toml`, found
by walking up from the working directory) and the **user config**
`~/.config/citefinder/config.toml` (honors `$XDG_CONFIG_HOME`):

```toml
cache_dir = "data/citefinder"   # one root for every cache path; relative to this file

[openalex]
api_key = "..."    # user config or .env only — a project config ignores it with a warning
mailto = "..."
max_retries = 3    # optional retry/pacing knobs
min_interval = 0.1

[crossref]
mailto = "..."
max_retries = 3
min_interval = 0
```

Lookup precedence (CLI), highest first: flag (`--cache`/`--out` beat
`--cache-dir`; `--api-key`/`--mailto`/`--max-retries`/`--min-interval`) →
shell env, then project `.env` (`CITEFINDER_CACHE_DIR`, `OPENALEX_API_KEY`,
`OPENALEX_MAILTO`, `CROSSREF_MAILTO`, `<SOURCE>_MAX_RETRIES`,
`<SOURCE>_MIN_INTERVAL`) → project config → user config → default
(`~/.cache/citefinder/<source>.jsonl` for lookups,
`data/citefinder/<bib-dir>[-<bib-stem>]/<source>/` under cwd for `verify`).
Config files populate the env names at CLI import (`_load_configs` in
`cli.py`), so the commands read one source; `citefinder config` prints the
resolved values with their sources. Library users pass values to the client
constructors explicitly — config files are CLI-only.

## Development

- Install: `uv sync --all-groups`
- Tests: `uv run pytest`
- Linting: pre-commit hooks run ruff format + lint on commit
- Type checking: pre-commit hooks run pyrefly on commit
- CI: GitHub Actions runs lint + type check + test matrix (Python 3.11–3.14) on push/PR to dev/main

## Release Automation

Use [stanza](https://github.com/gitronald/stanza) for release workflows:

```bash
stanza release [patch|minor|major|prerelease]
stanza init
```
