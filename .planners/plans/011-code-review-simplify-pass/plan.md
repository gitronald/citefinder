---
id: 11
slug: code-review-simplify-pass
status: draft
branch: feature/code-review-simplify-pass
created: 2026-09-04T19:18:10-07:00
concluded:
pr:
---

# Review and simplify the whole package at max effort

## Plan

### Why

Every plan so far (000–010) added or reshaped one feature, and four of the
modules were absorbed from external scripts rather than written in place. The
package has never had a whole-tree review: the only reviews have been per-PR
diffs, which by construction cannot see duplication *across* PRs or dead code
left behind when a later plan changed a module's role. With 0.9.0 shipped and
the tree clean, this is the cheap moment to do one full pass before the next
feature lands on top.

Two goals, in this order:

1. **Correctness** — find real bugs (wrong conditions, dropped guards,
   encoding/normalization mismatches, resource handling, broken callers) and
   fix them at the source with a regression test each.
2. **Simplicity** — remove duplication, dead branches, and needless
   indirection without changing behavior, the CLI's output, or the public API.

"Max effort" means the review runs at the top level the `/code-review` skill
defines (`high`: all four finder briefs, a 25-candidate cap, up to 10
verifiers, plus the gap sweep), and it runs once **per module group** rather
than once over the whole tree, so each finder works on a diff it can actually
hold. That is roughly 4 finders + up to 10 verifiers per pass, three passes,
all on `sonnet`/`haiku` per the subagent-model rule. The budget is deliberate;
do not trim it to save tokens.

### Scope

In scope: every module under `citefinder/`, the bundled skill body
`citefinder/prompts/skill.md`, and `tests/`. The generated stub
`.claude/skills/use-citefinder/SKILL.md` is out of scope (regenerate it with
`citefinder install --local` only if the frontmatter changes).

Out of scope, even if the review suggests them: new features, dependency
upgrades, changes to the JSONL cache format, renaming or removing anything
exported from `citefinder/__init__.py`, and changing CLI flag names or output
formats. A simplification that needs any of those is logged as a follow-up
plan, not done here.

### Review passes

The `/code-review` skill scopes on a diff. For a whole-tree target, produce
the diff against the empty tree so every line shows as added:

```bash
git diff $(git hash-object -t tree /dev/null) HEAD -- <paths>
```

Run three passes, each at `high`, each with its own fact sheet, in this order
(inner layers first so a finding in the HTTP layer is known before the
pipeline that depends on it is reviewed):

| Pass | Group | Paths |
|---|---|---|
| A | HTTP + cache layer | `_base.py`, `cache.py`, `client.py`, `openalex.py`, `tests/test_cache.py`, `tests/test_client.py`, `tests/test_openalex.py`, `tests/test_retry.py` |
| B | Verification pipeline | `bib.py`, `signals.py`, `adapters.py`, `verify.py`, `tests/test_bib.py`, `tests/test_signals.py`, `tests/test_adapters.py`, `tests/test_verify.py` |
| C | Surfaces | `cli.py`, `config.py`, `install.py`, `bib_table.py`, `__init__.py`, `prompts/skill.md`, `tests/test_config.py`, `tests/test_install.py`, `tests/test_bib_table.py`, `tests/conftest.py` |

Each pass reports through `ReportFindings` and the findings are copied into
this plan's Log as `file:line — summary — verdict`, so the record survives the
session. Findings are **not** fixed during the pass; all three passes finish
first, then fixes proceed in severity order across the pooled list. A finding
in one group that implicates another (e.g. an adapter quirk that the CLI
compensates for) is fixed once, at the source.

### Simplify pass

After the confirmed bugs are fixed, run `/simplify` over the accumulated
diff, then take a targeted pass over the seeds below. Each seed is a
*candidate* from a first inventory, to be verified against the code, not a
commitment:

1. **`strip_braces` is defined three times** with identical bodies:
   `bib.strip_braces`, `signals._strip_braces`, and `adapters._strip_braces`.
   Keep one (`bib.py` already exports it) and import it from the other two.
   Check whether `signals.normalize_title` and `adapters._openalex_surname`
   want the same normalization or only look alike.
2. **The top-level `doi`/`search` commands and the `crossref doi`/`crossref
   search` subcommands** in `cli.py` repeat the same build-client → look up →
   not-found exit → emit sequence. A shared helper taking the client and the
   lookup callable would collapse four bodies to one; check that Typer's
   option wiring survives the extraction.
3. **`cli.py` is 828 lines** and carries config-file loading
   (`_load_configs`, `_apply_config`) alongside the commands. CLAUDE.md
   documents `config.py` as the config module but places loading in the CLI.
   Decide whether loading belongs in `config.py` with the CLI calling one
   function; move it only if the result is smaller and clearer, and update the
   CLAUDE.md package table if anything moves.
4. **The `verify` command body** (`cli.py`, roughly 120 lines) mixes argument
   resolution, source construction, the per-entry loop, and result writing.
   Look for a seam that lets the orchestration live in `verify.py` next to
   `verify_entry`, leaving the command as argument handling.
5. **Test fixtures.** `tests/test_config.py` and `tests/test_install.py`
   are the two largest files in the repo. Look for repeated
   setup that belongs in `conftest.py`, and for tests that assert on the
   same behavior from two angles.
6. **Retry and pacing knobs** are resolved in `cli.py` (`_checked_knob`,
   `_source_client_kwargs`, `_env_number`) but consumed in `_base.py`. Check
   whether validation is duplicated on both sides.

Every simplification must leave `uv run pytest` green with no test edited
except to relocate a helper. If a simplification needs a test change beyond
an import path, it is changing behavior and belongs in the bug list or out
of scope.

### Fix policy

- Bugs: fix at the source (never a compensating patch at the call site) with
  a paired regression test in the matching `tests/test_<module>.py`.
- Simplifications: no new tests; existing tests are the guard.
- One commit per finding or per closely related group, so a wrong fix can be
  reverted alone.
- A finding judged a false positive after fixing starts is reverted and logged
  as `no_change_needed` with the reason.
- User-visible fixes get a line under `[Unreleased]` in `CHANGELOG.md`.
  Pure simplifications do not.

### Check gate

The gate is what the repo already runs, executed before the first change
(to record a baseline) and after every commit:

```bash
uv run pytest
uv run pre-commit run --all-files    # ruff format, ruff, pyrefly, planners validate
uv run citefinder install --check    # skill stub drift
```

CI runs the same lint + type check + test matrix on Python 3.11–3.14; the PR
does not merge until the matrix is green.

### Implementation order

1. Branch `feature/code-review-simplify-pass` from `dev` in a worktree; run
   the gate on the untouched tree and record test count and timing in the Log.
2. Review pass A, then B, then C. After each, append the findings to the Log.
3. Fix confirmed bugs in severity order, one commit each, gate after each.
4. `/simplify` over the accumulated diff, then the seeded candidates above,
   one commit each, gate after each.
5. Update `CHANGELOG.md` `[Unreleased]` and the CLAUDE.md package table if
   any responsibility moved between modules.
6. Open the PR against `dev`; `/planners close` runs its own review gate on
   the PR diff, which doubles as a check that the fixes themselves are clean.

### Deliverables

- A Log in this plan listing every finding with its verdict and outcome,
  including the ones skipped and why.
- A PR against `dev` with one commit per finding.
- Any out-of-scope work the review surfaced, written up as follow-up plans.
