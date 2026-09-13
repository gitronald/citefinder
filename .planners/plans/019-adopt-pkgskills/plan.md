---
id: 19
slug: adopt-pkgskills
status: draft
branch:
created: 2026-09-13T10:42:53-07:00
concluded:
pr:
---

# Adopt pkgskills for skill packaging and install

## Plan

`pkgskills` is the generalized form of the machinery this package grew in
plan [007](../007-bundle-skill-in-package/plan.md): a prompt shipped as package
data, printed on demand by a `skill` command, and materialized as a
version-stamped stub where Claude Code reads it, with `install --check`
judging drift. `citefinder/install.py` (342 lines) and the `skill` / `install`
commands in `cli.py` reimplement exactly that, and `tests/test_install.py`
(585 lines) covers the reimplementation. Adopting the library deletes all of
it and leaves one small declaration behind. The bib-verification and lookup
code is untouched.

This host is the library's simplest case: one single-source skill, no rules,
no agents, no pre-commit hooks, no repo lines, no permissions ladder. The
`Host` declaration is a dozen lines.

### Dependency

Depend on the **published distribution**, `pkgskills>=0.5.1` from PyPI,
resolved normally: not a path or editable dependency, not a git URL. `0.5.1`
is the current release and the right floor: `0.5.0` fixed the stub's
frontmatter shape (JSON-quoted generated fields, PyYAML parsing) and `0.5.1`
followed within the day, so a lower floor would let two consumers render two
stub shapes and report each other's as drifted.

Runtime cost is two transitive dependencies: `typer`, already required here,
and PyYAML, new here, which the library uses to read prompt frontmatter.
The library's `requires-python` is `>=3.11`, the same as this package's.

### What moves

| Today | After |
|---|---|
| `citefinder/install.py`: stamping, mode resolution, stub rendering, path/write/check, drift and hand-authored detection, symlink refusal, repo-root walk | `pkgskills.artifacts` and `pkgskills.rendering`, driven by the `Host` |
| `cli.py` `skill` command, including the non-UTF-8 stdout degrade | the library's `skill` command (same degrade, in `_print_prompt`) |
| `cli.py` `install` command with `--local` / `--check` / `--force` and `_installed_status` | the library's `install` command, same three flags |
| `citefinder/prompts/skill.md` | `citefinder/prompts/skills/use-citefinder/SKILL.md` (see layout below) |
| `tests/test_install.py` | a short `tests/test_host.py` over what stays |

Nothing stays behind in `install.py`; the module is deleted. `citefinder.host`
holds the declaration:

```python
HOST = Host(
    dist="citefinder",
    cli="citefinder",
    prompts="citefinder.prompts",
    artifacts=(
        Skill(name="use-citefinder", sources=("skills/use-citefinder/SKILL.md",)),
    ),
)
```

Defaults do the rest: `modes` is `("global", "local")` with global first,
which is today's default; `local_prefix` is `uv run`, which is today's local
invocation; `render_cli` stays off (below). `cli.py` mounts the two commands
with `register(app, HOST)` in place of the deleted ones, and `pyproject.toml`
registers the host under the `pkgskills.hosts` entry-point group so the
library's own `pkgskills hosts` and `pkgskills check` discover it.

### Prompt layout

The library's spec check requires a skill source to be stored as
`<name>/SKILL.md`, not a flat file. Running `assert_spec_conformant` from the
published `0.5.1` against today's flat `skill.md` reports exactly one
violation, `entry-file`, and nothing else: the frontmatter `name` matches the
skill, the 754-character `description` sits under the 1024 limit, and there
is no `metadata` block to get wrong. So the body moves to
`citefinder/prompts/skills/use-citefinder/SKILL.md`, byte-for-byte, and the
`[tool.hatch.build] artifacts` glob widens from `citefinder/prompts/*.md` to
`citefinder/prompts/**/*.md` so the wheel still ships it. The wheel test
keeps proving that.

The prompt's own "Where these instructions come from" section names
`citefinder skill` and `citefinder install --check`; both remain correct
(next section) and the path it cites for the stub is unchanged, so the body's
text needs only its one self-reference to the source path updated.

### `render_cli` stays off

The body names its commands as bare `citefinder ...` throughout, and once as
`uv run citefinder`. The library's `{cli}` token would render those
mode-correctly, but rewriting the body's command mentions is a content change
to the skill, not an install-machinery swap, and `assert_prompt_commands`
scans only `{cli}` mentions, so it passes trivially either way. Leave the
token out; adopting it is a candidate follow-up plan.

### Behavior that changes

Verified by rendering today's prompt through the published `0.5.1` as a
single-source skill:

- **The stub body is the library's, the frontmatter is not.** A single-source
  skill lifts its source's frontmatter verbatim, so `name` and the
  `description` triggers are byte-identical to today's stub. The body below
  the stamp is the library's template: the same shape (a drift-check gate,
  the `skill` command, the not-installed fallback) with the check moved
  before the load. The repo's committed `.claude/skills/use-citefinder/SKILL.md`
  is regenerated once.
- **The stub tells the model to run `citefinder skill use-citefinder`**, the
  skill's name, rather than bare `citefinder skill`. Bare `citefinder skill`
  still prints the body on a host with one skill (the library falls through
  to the only body when no name is given), so the README, the prompt's own
  self-reference, and any downstream repo that scripted the bare form keep
  working. `citefinder skill --list` is new.
- **The stamp gains `via pkgskills X`**, and the library masks both versions
  when judging drift, so a routine upgrade of either package still reads `ok`.
- **Every stub installed by an earlier release classifies `foreign`, not
  `drifted`.** The old stamp lacks the `via pkgskills` token, so the library
  cannot verify it wrote the file, and reports `a citefinder stamp pkgskills
  did not write; hand-edited, or from a release before citefinder adopted
  pkgskills`. A bare `install` refuses over it; `install --force` replaces it.
  That is a one-time, visible, self-healing event, but it lands on every
  machine and repo with a stub, so the changelog entry must use the word
  `foreign` and name `--force` as the remedy.
- **`install --check` prints the library's status table** (one row per
  artifact, with a reason on anything not `ok`) instead of today's
  `skill: <status> (<path>)` line. Exit status is unchanged: non-zero unless
  everything is `ok`. Nothing in this repo's hooks or CI parses the old line.
- **Mode flags are unchanged.** Because global is the host's default mode,
  the library's generated `install` / `--check` / `--force` commands are the
  same strings today's stub carries, with `--local` appended only in local
  mode.

Behavior that does not change, and that the new tests should pin: root
discovery walks up to the nearest `.git` or `.claude/` directory; a symlink,
directory, or unreadable file at the target is never written through and
needs `--force`; a hand-authored file with no stamp is refused without
`--force`; the global copy wins over a stale local one; `citefinder skill`
needs no installed stub.

### Tests

`tests/test_install.py` goes. Of its coverage, keep and re-point two things:
the wheel-ships-the-body test (over the new path, using
`pkgskills.testing.wheel_files` or the existing in-process build) and the
body-is-loadable-as-package-data test. Add `tests/test_host.py`:

- `assert_spec_conformant(HOST)` and `assert_prompt_commands(HOST, app)` from
  `pkgskills.testing`, which check statically what the deleted tests
  exercised by rendering.
- Over `pkgskills.testing.sandbox`: `install --local` writes the stub at the
  expected path with today's frontmatter verbatim; `install --check` reports
  `ok` after it; a stub carrying the pre-adoption stamp (`generated by
  citefinder 0.4.4 (mode=local); ...`) is `foreign`, refused by bare `install`,
  and replaced by `--force`; bare `skill` and `skill use-citefinder` print the
  same body; `skill` prints without a stub installed.

The coverage floor is 97%; deleting 342 fully covered lines and adding a
dozen declarative ones should not move it, but check the report before
opening the PR.

### Docs

- `README.md` "Claude Code skill" section: the stamp example gains the
  `via pkgskills` token, `citefinder skill use-citefinder` and `skill --list`
  join the command list, and the `--check` output description follows the
  table. The design story (no copy of the body, so nothing goes stale) is
  unchanged.
- `.claude/CLAUDE.md` package map: replace the `install.py` row with
  `host.py` and update the skill paragraph to point at the new source path.
- `CHANGELOG.md` `[Unreleased]`: the dependency, the source path move, the
  new `skill <name>` / `--list` forms, the `--check` table, and the `foreign`
  note with `--force` as the remedy.

### Implementation order

1. Add `pkgskills>=0.5.1` to `dependencies`; move the prompt to
   `skills/use-citefinder/SKILL.md`; widen the hatch `artifacts` glob.
2. Add `citefinder/host.py` with `HOST`; register the entry point.
3. Mount `register(app, HOST)` in `cli.py`; delete the `skill` and `install`
   commands and `_installed_status`; delete `citefinder/install.py`.
4. Replace `tests/test_install.py` with `tests/test_host.py`.
5. Regenerate the committed local stub with
   `uv run citefinder install --local --force`; confirm
   `uv run citefinder install --local --check` reports `ok`, that the stub's
   frontmatter is byte-identical to the previous one, and that
   `uv run citefinder skill` prints the same body as before the move.
6. Docs and changelog.

### Out of scope

The content of the skill body (beyond its one path self-reference), the
`{cli}` token, and anything under `citefinder/` other than `host.py`,
`install.py`, `cli.py`, and the prompt's location. The lookup, cache, and
verification code and their tests are unaffected.
