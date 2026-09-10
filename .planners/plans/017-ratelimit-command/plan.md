---
id: 17
slug: ratelimit-command
status: done
branch: null
created: 2026-09-10T00:00:43-07:00
concluded: 2026-09-10T01:28:04-07:00
pr: null
---

# Track API quotas and add the ratelimit command

## Plan

Retrospective plan: the work landed directly on `dev`, with no branch or PR,
before this file was written. It covers the whole addition: quota tracking in
the clients, the `citefinder ratelimit` command, and the follow-up that makes
the command refresh when nothing is recorded yet.

The `created` timestamp is taken from a commit, not from when the file was
written. The file was scaffolded at 2026-09-10T01:13:28-07:00, after the work
was committed, so `created` is backdated to the authored time of `db44e89`
(00:00:43), the last commit before the feature. The work ended with `6fb42da`
(01:28:04), the last implementation commit, which sets `concluded`. The plan was
first scoped to the follow-up alone, as `017-ratelimit-refresh-on-empty`, and
was then widened and renamed to cover the whole feature.

### Problem

OpenAlex spends a daily credit budget, and Crossref advertises a per-second
rate. Both report their current state in headers on every response, but the
clients dropped them. The only way to learn how much budget was left was to
make a request by hand and read the headers. The docs commits just before this
work (`4c4b0f6`, `f3486f3`, and `db44e89`) described the limits and their costs;
this plan adds the tooling to see them.

### Approach

- **Capture from existing traffic.** `CachedJsonClient._fetch` hands every
  response to `_capture_rate_limit`, which keeps the headers starting with
  `x-ratelimit-` (OpenAlex) or `x-rate-limit-` (Crossref) in
  `client.rate_limit` as `{"headers": {...}, "ts": float}`. Knowing the budget
  costs no extra request.
- **Persist in the existing cache.** The snapshot is stored under a URL-shaped
  key on the API's own host (`<base>/__ratelimit`). `cache merge` routes it by
  host like any other row and keeps the newest `ts`, which is what a counter
  wants. `citefinder drift` models only `/works` keys, so it skips the row. A
  new client seeds `rate_limit` from the cache, so a fresh process reports what
  the last run saw.
- **Throttle writes.** The cache is an append-only log, so the snapshot is
  written at most once per `RATE_LIMIT_PERSIST_INTERVAL` (60 seconds). Without
  that, a 150-entry verify would add a line per request.
- **Probe on demand.** `refresh_rate_limit()` fetches the source's
  `rate_limit_probe`, the cheapest request that still carries the headers:
  a single OpenAlex entity by ID (zero credits) and Crossref `works?rows=0`. The
  body is discarded and never cached, and the reading is written once.
- **Opt in per source.** A client subclass sets `rate_limit_key` and
  `rate_limit_probe`; leaving them `None` disables tracking.
- **CLI.** `citefinder ratelimit [--source openalex|crossref] [--refresh]`
  prints the stored headers, sorted, with a rough age from `_age`. It takes the
  usual `--cache`, `--cache-dir`, `--mailto`, and `--api-key` options.

### Scope

- `citefinder/_base.py`: capture, persistence, throttling, and
  `refresh_rate_limit`.
- `citefinder/openalex.py` and `citefinder/client.py`: each source's key and
  probe.
- `citefinder/cli.py`: the `ratelimit` command and `_age`.
- `tests/test_ratelimit.py`: capture, reload, merge and drift routing,
  throttling, the probe, and the CLI.
- Docs: a "Checking the budget" section in `docs/openalex.md`, a note in
  `docs/crossref.md`, README usage lines, and a changelog entry.

### Follow-up: refresh when nothing is recorded

Added after reviewing the first cut of the command.

#### Problem

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

#### Approach

When the stored snapshot has no headers, fall back to the same probe `--refresh`
uses:

- The probe is cheap by design: the OpenAlex probe is a single-entity lookup
  (zero credits), and the Crossref probe is `works?rows=0`.
- `refresh_rate_limit` persists its reading with `force=True`, so the fallback
  fires once per cache; later runs read the stored snapshot and make no request.
- If the probe also returns no quota headers, report that the API returned none,
  rather than the old "nothing recorded yet" message.

#### Scope

- `citefinder/cli.py`: the `ratelimit` command reads the stored snapshot and
  refreshes when `--refresh` is set or the snapshot is empty; update the
  `--refresh` help and the docstring.
- `tests/test_ratelimit.py`: replace the "nothing recorded" test (it would now
  make a real request) with tests for the fallback refresh, for no request when a
  snapshot is stored, and for the no-headers message.
- Docs: reword the "makes no request" claims in `docs/openalex.md`,
  `docs/crossref.md`, `README.md`, and the `[Unreleased]` changelog entry.

#### Trade-off accepted

An offline run against an empty cache now fails with a request error instead of
printing the "nothing recorded" message. That output was never actionable, so
losing it costs nothing.

## Log

- **2026-09-10T01:09:17-07:00**: Quota tracking and the `ratelimit` command
  landed in `ef30e48` (`add: quota tracking and ratelimit command`) on `dev`,
  with 15 tests in `tests/test_ratelimit.py`, the docs sections, README lines,
  and a changelog entry.
- **2026-09-10T01:12:29-07:00**: Follow-up implemented in `9c8f0ff`
  (`update: ratelimit refreshes when nothing is recorded`) on `dev`, directly
  after the quota tracking commit `ef30e48`. Full suite passes (395 tests,
  97.87% coverage); ruff lint, format check, and pyrefly are clean. No dedicated
  branch or PR.
- **2026-09-10T01:15:37-07:00**: Plan scaffolded and closed after the fact
  (`4c7747e`, `5a82f10`), scoped to the follow-up only; `created` backdated to a
  commit time (`17e61ec`).
- **2026-09-10T01:28:04-07:00**: Code review of `ef30e48..HEAD` at medium
  effort. Four candidates were verified; one was confirmed and fixed in
  `6fb42da` (`update: share the refresh stub in ratelimit tests`), which moves a
  `fake_refresh` closure copied between two CLI tests into a `stub_refresh`
  helper. Full suite passes (395 tests, 97.87% coverage); lint, format check,
  and pyrefly are clean.
  - Review follow-up, conscious no-ops: the offline traceback on an empty cache
    (the same unguarded call already existed with `--refresh`, no CLI command
    catches `requests` errors, and the trade-off is recorded above); the header
    extraction written twice in `ratelimit` (one short function, and the two
    checks decide different things); and a missing end-to-end persistence test
    (a two-run probe works, and breaking persistence fails six existing tests).
- **2026-09-10T01:29:54-07:00**: Widened the plan to cover the whole feature:
  retitled, renamed from `017-ratelimit-refresh-on-empty` to
  `017-ratelimit-command`, `created` moved to `db44e89` and `concluded` to
  `6fb42da`, with the follow-up kept as its own section.

## Retrospective

- Reading the headers off responses the client already makes kept quota
  tracking free. The only path that makes a request is the probe, and its
  endpoint was picked to cost zero credits.
- Storing the snapshot as a URL-shaped cache row reused `cache merge` routing
  and `drift` filtering without new storage or special cases. The key's shape
  was the design decision, not the storage.
- An append-only cache makes a frequently updated value expensive to store.
  Throttled writes, plus an identity check so a refresh stores one row instead
  of two, kept the log from growing with every request.
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
