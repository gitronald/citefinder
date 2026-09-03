---
id: 9
slug: project-cache-config
status: draft
branch:
created: 2026-09-02T18:33:44-07:00
concluded:
pr:
---

# Resolve cache paths from a project-level config

## Plan

### Problem

The CLI's two command families disagree about where a repo's caches live. `verify` writes under the working directory (`data/citefinder/<stem>/<source>/`, with a `refs.bib` filed under its parent directory's name), so it lands inside the repo it is run from. `doi`, `search`, and the `crossref` subcommands default their `--cache` to `~/.cache/citefinder/<source>.jsonl`, and the flag is the only way to move it: the config layer (`~/.config/citefinder/config.toml`, plus `.env` and shell env) carries `mailto`, `api_key`, `max_retries`, and `min_interval` but nothing about cache paths. A repo that keeps its caches on a shared or synced directory has to pass `--cache` on every single lookup, and the instruction to do so lives in that repo's agent rules rather than in anything the tool reads.

Config is also user-level only. There is no per-repo file the tool discovers, so a setting that is really a property of the project (where its caches go, which polite-pool address to use) has to be repeated per machine.

### Design

1. **A `cache_dir` setting.** One directory from which every cache path derives: lookups use `<cache_dir>/<source>.jsonl`, `verify` uses `<cache_dir>/<stem>/<source>/` (the current cwd-relative default becomes the fallback when nothing is set). Sources, in precedence order: an explicit `--cache` on a lookup command or `--out` on `verify` (unchanged, always wins); a new `--cache-dir` flag on the same commands; `CITEFINDER_CACHE_DIR` in the env or `.env`; `cache_dir` in a project config; `cache_dir` in the user config; the built-in defaults. A relative `cache_dir` from a project config resolves against that config file's directory, so `data/citefinder` means the repo's `data/citefinder` whatever the working directory; a relative value from the env or the flag resolves against the working directory, as flags do today.
2. **Project config discovery.** Walk up from the working directory, the way `find_dotenv(usecwd=True)` already does for `.env`, for the first of `citefinder.toml` or a `pyproject.toml` with a `[tool.citefinder]` table. Same keys and sections as the user config (`[openalex]`, `[crossref]`, plus top-level `cache_dir`). Precedence: flag > env and `.env` > project config > user config > default. Implementation: `_load_user_config` becomes `_load_configs`, reading the project file first and the user file second, each filling only env names still unset, so the existing "env wins" contract holds and the project overrides the user file without new plumbing.
3. **Secrets stay out of project files.** A project config is meant to be committed. If one carries `api_key`, warn on stderr and ignore the key, pointing at `.env` or the user config; `mailto` is fine to commit.
4. **`citefinder config` command.** Prints each resolved setting with where it came from (`flag`, `env`, `project <path>`, `user <path>`, `default`) and the cache paths the lookups and `verify` would use. Read-only; the debugging surface for "why did it write there".
5. **Library side.** A small `resolve_cache_path(source, cache_dir=None)` helper next to the clients, used by the CLI and available to wrappers that build clients themselves. The clients keep taking `cache_path`; nothing changes for library users.

### Tests

- Discovery: from a nested working directory, `citefinder.toml` two levels up is found; `[tool.citefinder]` in `pyproject.toml` is found; the nearer file wins when both exist; nothing found leaves the defaults.
- Precedence matrix for `cache_dir` and `mailto`: flag over env, env over project, project over user, user over default; `--cache` still beats `--cache-dir`.
- Relative-path anchoring: a project `cache_dir` resolves against the config's directory; an env value resolves against cwd.
- `verify` honors `cache_dir` for its output directory and still files `refs.bib` by parent name; `--out` still wins.
- A project config with `api_key` warns, is ignored, and the user config's key still applies.
- A malformed project file warns and falls through, mirroring the user-config behavior.
- `citefinder config` output names the source of each value.

### Docs

- README: a "Configuration" section with the two file locations, the key list, the precedence table, and the note about secrets; the lookup examples drop `--cache` where the project config covers it.
- `citefinder/prompts/skill.md`: the "Cache path" bullet says to check for a project config before passing `--cache`, and mentions `citefinder config` for finding out where a lookup will write.
- CHANGELOG `[Unreleased]`: Added (project config, `cache_dir`, `--cache-dir`, `CITEFINDER_CACHE_DIR`, `citefinder config`), Changed (`verify` default derives from `cache_dir` when set).

### Implementation order

1. `resolve_cache_path` and the `cache_dir` plumbing on the lookup commands and `verify`, with the flag and env sources and the precedence tests.
2. Project config discovery in `_load_configs`, the secrets warning, and the discovery and anchoring tests.
3. `citefinder config`.
4. README, skill text, changelog.
5. Release as a minor version. A downstream repo then commits `cache_dir = "data/citefinder"` (or wherever it keeps them) and drops the per-command `--cache` from its agent rules.

### Out of scope

- Per-source cache directories or any change to the JSONL format.
- Migrating existing home-directory caches into a project directory.
- Reading settings from `.env` beyond the existing env-name mapping; `.env` stays a place for secrets and one-off overrides, not a second config format.
