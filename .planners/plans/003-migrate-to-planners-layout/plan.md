---
id: 3
slug: migrate-to-planners-layout
status: done
branch: feature/migrate-to-planners-layout
created: 2026-06-10T09:40:57-07:00
concluded: 2026-06-10T09:45:06-07:00
pr: https://github.com/gitronald/citefinder/pull/26
---

# Migrate plans to the .planners layout

## Plan

Migrate this repo from the legacy `docs/plans/` + `TODO.md` layout to the
`planners` package's `.planners/` layout, following the fleet rollout runbook
(the `planners-migration` skill).

Scope:

- Standardize frontmatter on all 3 legacy plans (000-002): add `id`/`slug`
  and rename the terminal field `completed` -> `concluded`.
- Move each plan to `.planners/plans/<NNN>-<slug>/plan.md` and repoint any
  in-repo references to the old paths.
- Reconcile `TODO.md`: a done archive plan for its one done unplanned item
  (the bib-table command, which carried no plan marker). The open item's
  plan 002 is `draft`, so its open work survives. Then retire `TODO.md`.
- `docs/README.md`: strip the plans boilerplate section, keep the curated
  Guides section (mixed-case carve-out).
- Install the planners holder, append the validate hook to the existing
  pre-commit config, activate it, generate the index, and validate.

## Log
**2026-06-10 — migration run (review list + decisions).**

- **Frontmatter pass (3 legacy plans, 000-002):** added `id`/`slug`; renamed
  `completed:` -> `concluded:`. Done plans already carried branch + PR.
- **TODO.md reconciliation:** the done bib-table item carried no plan marker
  -> archive plan 004 (`concluded` = migration date); the open item's plan
  002 remains `draft`.
- **docs/README.md (mixed case):** kept Guides; plans section repointed to
  `.planners/`.
- **Review follow-up (fix):** the review caught a stale `docs/plans/` link
  in CLAUDE.md — the run had skipped the Step 5a reference sweep. Repointed
  (commit 8325233) and re-grepped the tree clean. Check gate: pytest 110
  passed; `planners validate` ok (5 plans).
- **Hook:** appended to the existing ruff/pyrefly config and active.
  Installed from the worktree — re-install from the main checkout after
  merge.

## Retrospective

- The one finding across ten repos so far came from skipping the mechanical
  reference sweep — the review gate caught it, which is exactly the
  defense-in-depth the runbook intends. Don't inline-optimize away Step 5a.
