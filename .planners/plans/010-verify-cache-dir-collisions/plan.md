---
id: 10
slug: verify-cache-dir-collisions
status: done
branch: feature/verify-cache-dir-collisions
created: 2026-09-04T12:51:36-07:00
concluded: 2026-09-04T17:59:11-07:00
pr: https://github.com/gitronald/citefinder/pull/48
---

# Derive the verify cache directory from the source directory, not the bib stem

## Plan

### Problem

`verify` picks its per-run output directory from the **bib file's stem**, with a
single hardcoded exception (`citefinder/cli.py:501-503`):

```python
stem = bib_file.stem
if stem == "refs":
    stem = bib_file.parent.name
out_dir = _verify_root(cache_dir) / stem / source
```

The exception exists for a good reason: when every directory names its
bibliography `refs.bib`, keying on the stem would funnel every run into one
`refs/` directory. Falling back to the parent directory name restores a unique
identity per source directory.

But the fallback is keyed to one literal filename, so the guarantee it provides
evaporates for any other name. The invariant the layout actually wants is *the
cache directory identifies the source directory*; the current code only achieves
that when the file happens to be called `refs.bib`.

Two consequences:

1. **Silent cross-directory collision.** Two sibling directories that each hold a
   same-named secondary bibliography (`refs-unused.bib`, `extra.bib`, …) both
   resolve to one stem, so both runs write `results.json` and the source JSONL
   into the same directory. The second run overwrites the first with no warning.
   Same for two directories that both use `references.bib`.
2. **Inconsistent layout.** A directory whose bibliography is not `refs.bib`
   lands in a stem-named sibling of the directory-named entries, so the cache
   root becomes a mix of two naming schemes that no longer reads as one index.

Renaming a bibliography to or from `refs.bib` also silently relocates its cache,
orphaning the previous run's JSONL — the next verify starts cold and refetches
every entry, which is exactly the path that provokes upstream rate limiting.

### Candidate fixes

**A — parent-first, stem as qualifier (recommended).** Always derive from the
parent directory, appending the stem only when it is not the primary name:

```python
stem = (
    bib_file.parent.name
    if bib_file.stem == "refs"
    else f"{bib_file.parent.name}-{bib_file.stem}"
)
```

Unique across directories by construction, and byte-identical to today's layout
for the `refs.bib` case, so existing caches keep resolving and no migration is
forced. Only non-`refs` files relocate — the set that is already inconsistent.

**B — always nest under the parent** (`<parent>/<stem>/<source>/`). The cleanest
invariant, but it moves *every* existing cache, including the common case, so it
needs a migration path or a fallback read.

**C — make the primary name configurable** (e.g. `primary_bib` in the
`[tool.citefinder]` table). Least invasive, but it only moves the hardcoded
string into config; multiple secondary bib files still collide.

**D — detect and refuse.** Keep the current derivation, but error when the
resolved directory already holds a `results.json` written from a different
source path. Turns silent data loss into a visible failure without fixing the
layout; a reasonable companion to A, not a substitute.

Recommend **A**, optionally with **D** as a guard.

### Implementation order

1. Add regression tests covering the collision: two directories, each with a
   same-named non-`refs` bib, must resolve to different output directories; a
   `refs.bib` must resolve exactly as it does today.
2. Change the derivation in `cli.py`; keep `--out` overriding it untouched.
3. Check whether any other entry point derives the same path (the wrapper
   scripts and `resolve_cache_path`) so the rule lives in one helper rather than
   being restated.
4. Note the relocation for non-`refs` bibliographies in `CHANGELOG.md`, and
   mention that an existing stem-named cache directory can simply be renamed to
   the new name to preserve its JSONL.

### Out of scope

- The shared top-level lookup caches (`<cache_dir>/<source>.jsonl`), which are
  keyed by source, not by input file, and are unaffected.
- Any change to `--out`, which stays an explicit escape hatch.

## Log

- 2026-09-04T17:21:15-07:00 — Activated on `dev`; work on `feature/verify-cache-dir-collisions`,
  draft PR https://github.com/gitronald/citefinder/pull/48.
- Implemented option A: a `_verify_out_dir` helper in `cli.py` derives
  `<root>/<bib-dir>[-<bib-stem>]/<source>/` from the absolute bib path.
  `refs.bib` output is byte-identical to before; any other file moves from
  `<bib-stem>/` to `<bib-dir>-<bib-stem>/`. Regression tests cover the
  sibling-directory collision and the unchanged `refs.bib` case.
- Found along the way: a bare relative `verify refs.bib` (the form the
  skill's own examples use) had an empty parent name and filed its output
  under `<root>/<source>/`. Anchoring the path first fixes it; regression
  test added and the fix noted in the changelog.
- Step 3 check: no other entry point derives the per-bib directory.
  `resolve_cache_path` only builds `<dir>/<source>.jsonl`, and no wrapper
  scripts live in this repo, so the rule lives in the one helper.
- Option D (refuse when the directory holds another bib's `results.json`)
  left out. The residual collision under A is two bib files with the same
  directory name *and* stem under different parents (`a/paper/refs.bib`
  vs `b/paper/refs.bib`), which the old scheme already had for `refs.bib`.
  Guarding it would need the resolved path stored in `results.json` and
  would refuse after an innocent directory rename, so it is a separate call.
- 2026-09-04T18:00:26-07:00 — Review follow-up (PR #48, level medium, four verified findings):
  - Actioned: the helper resolved the path, and `resolve()` follows
    symlinks, so a bib reached through a linked project directory was keyed
    on the link target's name and its existing `refs.bib` cache orphaned,
    the exact relocation the plan promised not to force. It now anchors
    with `os.path.normpath(bib_file.absolute())`; regression tests cover a
    symlinked directory and `../refs.bib`. The call-site comment in
    `verify` was trimmed to what the helper's docstring does not say.
  - Conscious no-op: the flat `<dir>-<stem>` join is ambiguous when a
    directory name carries a hyphen at the split (`paper-my/notes.bib` vs
    `paper/my-notes.bib`, `paper-extra/refs.bib` vs `paper/extra.bib`).
    Narrower than the class removed here, and the flat layout is what keeps
    existing `refs.bib` caches in place; option B would remove it at the
    cost of moving every cache.
  - Conscious no-op: a bib directly under the filesystem root still yields
    an empty directory name.
  - CI was red on `ruff format --check .`: ruff 0.16 formats Python code
    blocks inside markdown, and this plan's one-line example was too long.
    The pre-commit `ruff-format` hook only covers Python files, so the
    failure only surfaces in CI; the block was reformatted.

## Retrospective

- Option A landed as specified. The one design change came from review:
  `resolve()` was the wrong primitive for naming, since following symlinks
  changes the name the user sees and silently moves their cache. "Absolute"
  and "resolved" differ exactly where project directories are links
  (mounted drives, shared folders), which is common for bibliographies.
- The bare relative `refs.bib` bug stayed invisible until a test used the
  form the skill's own examples use. Tests that mirror documented
  invocations catch more than tests built from absolute `tmp_path` paths.
- A flat `<dir>-<stem>` name trades a small ambiguity for zero migration.
  If a second collision report ever arrives, nest (option B) with a
  fallback read of the old location rather than adding more suffix rules.
- Local pre-commit and CI disagree on markdown: CI's `ruff format --check .`
  formats fenced Python in `.md` files, but the hook only runs on `.py`.
  Aligning the hook, or excluding `.planners/` from ruff, would keep a
  plan's code sample from failing CI.
