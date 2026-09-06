---
id: 15
slug: template-upgrade
status: done
branch: feature/template-upgrade
created: 2026-09-06T14:01:05-07:00
concluded: 2026-09-06T14:05:05-07:00
pr: https://github.com/gitronald/citefinder/pull/56
---

# Sync tooling with proj-template 0.8

## Plan

Bring the repo's tooling config up to proj-template 0.8.x (the 0.8.0 release
added coverage enforcement; 0.8.1a0 is the current dev tip). The repo is a
**package** (hatchling build, `project.scripts`, published to PyPI), so the
package column of the template's sync matrix applies.

Most rows are already in sync from earlier upgrades. Per-row decisions:

| Template path | Decision |
|---|---|
| `pyproject.toml` `[tool.ruff*]`, `[tool.pyrefly*]` | already in sync; keep the repo's explicit `project-includes` (`citefinder`, `tests`) instead of the template's `.` + excludes, since it is narrower and deliberate |
| `pyproject.toml` dev group | merge the template's floors (`pyrefly>=1.0.0`, `ruff>=0.15`); keep the repo's extras (`hatchling`, `types-requests`) and its higher `pytest` floor |
| `pyproject.toml` `[tool.pytest.ini_options]`, `[tool.coverage.*]` | **add**: `addopts = "--cov --cov-report=term-missing"`, `run.source = ["citefinder"]`, branch coverage, the template's `exclude_lines`, and `fail_under` pinned to the repo's current total (rounded down) |
| `[build-system]`, sdist `only-include`, urls, scripts | already in sync |
| `.pre-commit-config.yaml` | already in sync; the repo's ruff rev (v0.16.4) is newer than the template's, keep it |
| `.python-version` | already 3.14 |
| `.gitignore` | in sync. The repo deliberately tracks `.claude/` (CLAUDE.md, hooks, skills) and ignores only `settings.local.json`, so the template's blanket `.claude/` entry is not adopted |
| `.claude/settings.json` + `hooks/lint-typecheck.sh` | hook script already tracked and identical; **add** the tracked `settings.json` so the Stop hook and permission profile ship with the repo rather than living only in the untracked local settings |
| `.claude/CLAUDE.md` | already at the canonical path; refresh the stale `Tests` and `Type checking` bullets in `## Development` to the template wording; leave everything else |
| `.github/workflows/test.yml` | matrix and `UV_PYTHON` pin already present; drop the inline `--cov` flags from the pytest step now that `addopts` carries them. Keep the repo's SHA-pinned actions (already hardened; the template says not to convert either way during an upgrade) and its newer `setup-uv` |
| `.github/workflows/publish.yml` | sync the `PUBLISH_ENABLED` repository-variable gate on both jobs, keeping the SHA pins. The repo publishes today, so set the variable to `true` when the PR lands so tag pushes keep publishing |
| `.github/dependabot.yml` | in sync (repo adds `target-branch: dev`, keep). Confirm repo toggles: alerts on, security updates off |
| `.planners/` | present |
| `citefinder/`, `tests/`, `README.md`, `CHANGELOG.md` | never touched, except a changelog entry for the coverage gate |

Verification gate before the PR: `uv sync --all-groups`, ruff check + format
check, pyrefly, `pre-commit run --all-files`, and `uv run pytest` (which now
runs with coverage and the floor).

## Log

- Classified as a package; applied the package column of the sync matrix.
- `pyproject.toml`: added `addopts = "--cov --cov-report=term-missing"` and
  the `[tool.coverage.*]` sections with `source = ["citefinder"]`, branch
  coverage, and `fail_under = 97` (the current total was 97.02%, so the floor
  holds the line; raise it as coverage grows). Merged the `pyrefly>=1.0.0` and
  `ruff>=0.15` floors; the lock only gained the two specifiers.
- CI `test.yml`: the pytest step is now bare `uv run pytest`; `addopts` carries
  the coverage flags. SHA-pinned actions and the newer `setup-uv` kept as is.
- `publish.yml`: both jobs gated on `vars.PUBLISH_ENABLED == 'true'`. The
  repository variable was meant to be set to `true` alongside the PR so
  releases keep publishing, but the 0.9.4 release review found it had never
  been created. Set it with `gh variable set PUBLISH_ENABLED --body true`
  before tagging, or the publish workflow skips.
- `.claude/settings.json` added and tracked (template copy). The
  machine-local settings file duplicated the Stop hook, so its hooks block was
  removed on disk to avoid running the gate twice; that file is ignored and
  not part of the branch.
- `.claude/CLAUDE.md`: refreshed the Tests and Type checking bullets only.
- Skipped: the template's `.gitignore` blanket `.claude/` entry (repo tracks
  its Claude payload deliberately), template `project-includes = ["."]`
  (repo's explicit list is narrower), and the older template ruff/`setup-uv`
  pins. Dependabot repo toggles were already alerts on, security updates off.
- Gate: ruff check, ruff format check, pyrefly, `pre-commit run --all-files`,
  and pytest (321 passed, 97.02% coverage) all green before the PR.
