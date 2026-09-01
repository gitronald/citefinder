# Claude Settings

This file provides guidance to [Claude Code](claude.ai/code).

## Package Structure

```
citefinder/
├── __init__.py         # public API re-exports
├── _base.py            # CachedJsonClient base for the HTTP clients
├── cache.py            # JsonlCache (append-only JSONL key-value store)
├── client.py           # CrossrefClient
├── openalex.py         # OpenAlexClient + abstract reconstruction + arXiv routing
├── bib.py              # Entry, parse_entries, bib-side query helpers
├── signals.py          # Status, BibCitation, Work, signal checks, status reduction
├── adapters.py         # crossref_to_work, openalex_to_work (pure JSON adapters)
├── verify.py           # Source, Result, verify_entry orchestration
├── bib_table.py        # bib_to_table / table_to_bib (bib <-> wide polars DataFrame)
├── install.py          # render/stamp/drift-check for the bundled Claude Code skill
├── prompts/skill.md    # canonical `use-citefinder` skill body (package data)
└── cli.py              # Typer CLI: doi, search, verify, bib-to-table, table-to-bib, install, crossref subcommand
```

`.claude/skills/use-citefinder/SKILL.md` is **generated** — a stamped copy of
`citefinder/prompts/skill.md` written by `citefinder install --local`. Edit the
prompt body, then re-run the install; edits made directly to the `.claude/`
copy are lost on the next one. `citefinder install --check` reports drift.

The four bib-verification modules (`bib`, `signals`, `adapters`, `verify`)
were absorbed from external scripts in plan
[`001-absorb-bib-verification-scripts.md`](.planners/plans/001-absorb-bib-verification-scripts/plan.md)
— `signals.py` is shape-independent, `adapters.py` is the per-source JSON
boundary, `verify.py` orchestrates lookups against a `Source`.

## Configuration

CLI users can store credentials once in `~/.config/citefinder/config.toml`
(honors `$XDG_CONFIG_HOME`):

```toml
[openalex]
api_key = "..."
mailto = "..."

[crossref]
mailto = "..."
```

Lookup precedence (CLI), highest first: `--api-key`/`--mailto` flag → shell
env (`OPENALEX_API_KEY`, `OPENALEX_MAILTO`, `CROSSREF_MAILTO`) → project
`.env` → `config.toml`. Library users pass values to the client constructors
explicitly — config file is CLI-only.

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
