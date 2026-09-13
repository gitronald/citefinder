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
| `citefinder/prompts/skill.md` | `citefinder/prompts/skills/use-citefinder/SKILL.md` (see skill structure below) |
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

Plus `render_cli=True` and the `docs` tuple (below). Defaults do the rest:
`modes` is `("global", "local")` with global first, which is today's default,
and `local_prefix` is `uv run`, which is today's local invocation. `cli.py` mounts the two commands
with `register(app, HOST)` in place of the deleted ones, and `pyproject.toml`
registers the host under the `pkgskills.hosts` entry-point group so the
library's own `pkgskills hosts` and `pkgskills check` discover it.

### Skill structure

Adopting the library is also the point to adopt the
[Agent Skills specification](https://agentskills.io/specification) layout
the library enforces, rather than moving one file to satisfy a check. Three
parts:

**The skill directory.** The body moves from the flat
`citefinder/prompts/skill.md` to
`citefinder/prompts/skills/use-citefinder/SKILL.md`. Running
`assert_spec_conformant` from the published `0.5.1` against today's flat file
reports exactly one violation, `entry-file`, and nothing else: the
frontmatter `name` matches the skill, the 754-character `description` sits
under the 1024 limit, and there is no `metadata` block to get wrong. The
`[tool.hatch.build] artifacts` glob widens from `citefinder/prompts/*.md` to
`citefinder/prompts/**/*.md` so the wheel ships the whole directory, and the
wheel test keeps proving that.

**A `metadata.version` field.** The spec's `metadata` map is the place for a
skill's own version, separate from the package version in the stamp. Add
`metadata: {version: "1.0.0"}`, quoted, since an unquoted `1.0` reads as a
float and fails the spec check. Unlike a dispatcher, a single-source skill
lifts its whole frontmatter block into the stub, so the field reaches the
installed `SKILL.md` and Claude Code sees it. Bump it when the body's
instructions change materially, not on every package release.

**Reference material into `references/`.** The body is 443 lines, and about
half of it is detail a step consults rather than instructions every
invocation needs: the given-names-and-diacritics recipe (roughly 100 lines),
the OpenAlex fallback and API-key setup, and the `bib_to_table` inspection
walkthrough. The spec puts that in `references/` next to `SKILL.md`. A
printed body cannot read a sibling file, so each reference is declared as a
`Doc` on the host and the body loads it with `{cli} doc use-citefinder/<name>`
at the step that needs it:

```python
docs=(
    Doc(name="use-citefinder/given-names",
        source="skills/use-citefinder/references/given-names.md"),
    ...
)
```

Which sections move is an implementation-time judgment; the test for each is
whether a lookup that never touches that topic needs it in context. The
`## When to use`, install check, four core operations, and key behaviors
sections stay in the body. `tests/test_skill_recipe.py` runs the given-name
recipe as printed, so it re-points at the reference file's text.

### `{cli}` and `render_cli`

A `Doc` is loaded by a `{cli} doc ...` command, and that token is substituted
only when the host renders it, so `render_cli=True` goes on the `Host`. Once
it is on, the body's other command mentions follow: the nine bare
`citefinder ...` mentions and the one `uv run citefinder` become `{cli} ...`,
so a local install prints `uv run citefinder verify ...` and a global one
prints `citefinder verify ...`, which is the mode-correct form the current
body cannot produce. With the token in use, `assert_prompt_commands` does real
work: every `{cli}` mention in the body and the references resolves against
the CLI, and every `doc` mention against the declared docs, so a renamed
reference or a dropped subcommand fails the suite.

### Behavior that changes

Verified by rendering today's prompt through the published `0.5.1` as a
single-source skill:

- **The stub body is the library's, the frontmatter is not.** A single-source
  skill lifts its source's frontmatter verbatim, so `name` and the
  `description` triggers are byte-identical to today's stub, and the new
  `metadata.version` rides along. The body below
  the stamp is the library's template: the same shape (a drift-check gate,
  the `skill` command, the not-installed fallback) with the check moved
  before the load. The repo's committed `.claude/skills/use-citefinder/SKILL.md`
  is regenerated once.
- **The stub tells the model to run `citefinder skill use-citefinder`**, the
  skill's name, rather than bare `citefinder skill`. Bare `citefinder skill`
  still prints the body on a host with one skill (the library falls through
  to the only body when no name is given), so the README, the prompt's own
  self-reference, and any downstream repo that scripted the bare form keep
  working. `citefinder skill --list` and `citefinder doc <name>` are new.
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
  exercised by rendering, plus every `{cli}` and `doc` mention in the body
  and references.
- `doc use-citefinder/<name>` prints each reference with `{cli}` rendered for
  the installed mode.
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
  `host.py`, list the `prompts/skills/use-citefinder/` tree, and update the
  skill paragraph to point at the new source path.
- `CHANGELOG.md` `[Unreleased]`: the dependency, the source path move, the
  `doc` command and the references it serves, the new `skill <name>` /
  `--list` forms, the `--check` table, and the `foreign`
  note with `--force` as the remedy.

### Implementation order

1. Add `pkgskills>=0.5.1` to `dependencies`; move the prompt to
   `skills/use-citefinder/SKILL.md`; widen the hatch `artifacts` glob; add
   `metadata.version`.
2. Split the reference sections into `references/`, convert command mentions
   to `{cli}`, and point the body at each reference's `doc` command.
3. Add `citefinder/host.py` with `HOST`, `render_cli=True`, and the `Doc`s;
   register the entry point.
4. Mount `register(app, HOST)` in `cli.py`; delete the `skill` and `install`
   commands and `_installed_status`; delete `citefinder/install.py`.
5. Replace `tests/test_install.py` with `tests/test_host.py`; re-point
   `tests/test_skill_recipe.py`.
6. Regenerate the committed local stub with
   `uv run citefinder install --local --force`; confirm
   `uv run citefinder install --local --check` reports `ok`, that the stub's
   frontmatter is byte-identical to the previous one, and that
   `uv run citefinder skill` and each `doc` command print the expected text
   with `uv run citefinder` rendered in.
7. Docs and changelog.

### Out of scope

The substance of the skill's instructions: the split moves text into
references and rewrites command prefixes, and changes nothing else about what
the skill tells the model to do. Anything under `citefinder/` other than
`host.py`, `install.py`, `cli.py`, and the prompt tree. The lookup, cache, and
verification code and their tests are unaffected.
