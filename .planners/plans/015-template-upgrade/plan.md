---
id: 15
slug: template-upgrade
status: active
branch: feature/template-upgrade
created: 2026-09-06T14:01:05-07:00
concluded:
pr:
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
