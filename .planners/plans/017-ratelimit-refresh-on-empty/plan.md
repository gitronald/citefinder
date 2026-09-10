---
id: 17
slug: ratelimit-refresh-on-empty
status: done
branch: null
created: 2026-09-10T01:13:28-07:00
concluded: 2026-09-10T01:12:29-07:00
pr: null
---

# Refresh the ratelimit snapshot when none is recorded

## Plan

Retrospective plan: the change landed directly on `dev` before this file was
written, as a small follow-up to the quota tracking that added
`citefinder ratelimit`.

### Problem

`citefinder ratelimit` reports the quota snapshot the client last stored in the
cache, and makes a request only with `--refresh`. On a first run, or against a
cache that has never seen a response, there is nothing stored, and the command
printed:

```
openalex: nothing recorded yet (run a lookup, or --refresh)
```

The default mode was answering with an instruction to rerun the same command
with a flag. The "no request without `--refresh`" guarantee bought nothing in
that case: an empty snapshot carries no information to protect.

### Approach

When the stored snapshot has no headers, fall back to the same probe `--refresh`
uses:

- The probe is cheap by design: the OpenAlex probe is a single-entity lookup
  (zero credits), and the Crossref probe is `works?rows=0`.
- `refresh_rate_limit` persists its reading with `force=True`, so the fallback
  fires once per cache; later runs read the stored snapshot and make no request.
- If the probe also returns no quota headers, report that the API returned none,
  rather than the old "nothing recorded yet" message.

### Scope

- `citefinder/cli.py`: the `ratelimit` command reads the stored snapshot and
  refreshes when `--refresh` is set or the snapshot is empty; update the
  `--refresh` help and the docstring.
- `tests/test_ratelimit.py`: replace the "nothing recorded" test (it would now
  make a real request) with tests for the fallback refresh, for no request when a
  snapshot is stored, and for the no-headers message.
- Docs: reword the "makes no request" claims in `docs/openalex.md`,
  `docs/crossref.md`, `README.md`, and the `[Unreleased]` changelog entry.

### Trade-off accepted

An offline run against an empty cache now fails with a request error instead of
printing the "nothing recorded" message. That output was never actionable, so
losing it costs nothing.

## Log

- **2026-09-10T01:12:29-07:00**: Implemented in `9c8f0ff`
  (`update: ratelimit refreshes when nothing is recorded`) on `dev`, directly
  after the quota tracking commit `ef30e48`. Full suite passes (395 tests,
  97.87% coverage); ruff lint, format check, and pyrefly are clean. No dedicated
  branch or PR.

## Retrospective

- The original design treated "no request by default" as a rule, when it only
  mattered when a stored reading existed to fall back on. Treating the empty
  case as a refresh keeps the free path for every run after the first.
- The old test for the empty case would have silently started hitting the
  network after this change. Any test that runs the CLI against an empty cache
  needs the probe patched, which the replacement tests do at
  `OpenAlexClient.refresh_rate_limit`.
- The "no request" claim appeared in four places (two docs pages, the README,
  and the changelog). A behavior guarantee repeated in prose is easy to miss
  when the behavior changes, so check for all copies of it.
- Caught in review of the freshly added command, before any release shipped it.
  A quick "what does the first run look like?" pass is worth doing for any new
  command that depends on cached state.
