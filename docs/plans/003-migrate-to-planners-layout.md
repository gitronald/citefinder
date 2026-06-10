---
id: 3
slug: migrate-to-planners-layout
status: active
branch: feature/migrate-to-planners-layout
created: 2026-06-10T09:40:57-07:00
concluded:
pr:
---

# Migrate plans to the .planners layout

## Plan

Migrate this repo from the legacy `docs/plans/` + `TODO.md` layout to the
`planners` package's `.planners/` layout, following the fleet rollout runbook
(quipus plan 033, `planners-migration` skill).

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
