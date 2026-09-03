---
id: 8
slug: retry-rate-limited-requests
status: done
branch: feature/retry-rate-limited-requests
created: 2026-09-02T16:51:43-07:00
concluded: 2026-09-02T17:53:19-07:00
pr: https://github.com/gitronald/citefinder/pull/42
---

# Retry rate-limited requests with backoff and pace the search fallback

## Plan

### Problem

`CachedJsonClient._get` (`citefinder/_base.py`) is the single HTTP path for both clients. It sends one request per call with no pacing, and on any status other than 200 or 404 it calls `raise_for_status()` and raises. A 429 therefore surfaces as an `HTTPError` on the first attempt, and every caller that walks a list — `verify_entry` over a `.bib`, and a downstream `verify-bib.py` wrapper — catches it per entry and records `error`. Once a source starts rate-limiting, every remaining entry in the run fails the same way: one paper's Crossref search pass recorded 30 of 30 no-DOI entries as `search failed: 429`, and its OpenAlex pass 39.

Two things follow from reading the code, and both belong in the docs because a downstream skill guessed wrong about them:

- A 429 is never cached. The raise happens before `cache.put`, so an immediate re-run fails again only because the limit is still in force, not because an error was replayed. Advice to purge cache lines after a 429 is a no-op.
- Nothing honors `Retry-After` or paces requests. Crossref sends `Retry-After` plus `X-Rate-Limit-Limit` / `X-Rate-Limit-Interval` on its responses; OpenAlex documents 10 requests per second and a daily cap and returns a JSON error body on 429.

### Design

1. **Retry in `_get`.** On 429, 502, 503, and 504, sleep and retry up to `max_retries` (default 3). The wait is the `Retry-After` header when present — parse both the delta-seconds and the HTTP-date form — capped at `max_wait` (default 60 s); otherwise exponential backoff from `backoff_base` (default 1 s): `base * 2**attempt` plus jitter of up to half a step. After the last attempt, raise the original `HTTPError`. Other 4xx raise immediately with no retry, and 404 stays a cached `None`. Nothing is written to the cache until a 2xx arrives.
2. **Pacing.** A `min_interval` (seconds between requests per client instance, measured from the previous request's start) enforced in `_get` before sending. Default 0 for Crossref and 0.1 s for `OpenAlexClient`, matching the documented 10 req/s. Cache hits do not count as requests and are not paced.
3. **Injectable clock.** `_get` takes its `sleep` and `monotonic` from constructor parameters that default to `time.sleep` / `time.monotonic`, so the tests drive a fake clock and never wait. The Retry-After parsing is a pure helper, `retry_after_seconds(header_value, now)`, tested on its own.
4. **Visibility.** One `logging.getLogger("citefinder")` warning per retry naming the status, the attempt, and the wait, and a `retries` counter on the client that `verify` reports in its end-of-run summary line. Callers keep receiving the same exception on exhaustion, so the downstream per-entry `error` handling is unchanged.
5. **Knobs.** Constructor parameters (`max_retries`, `backoff_base`, `max_wait`, `min_interval`) on `CachedJsonClient` and both subclasses; `--max-retries` and `--min-interval` on `citefinder verify`, `search`, `doi`, and the `crossref` subcommands; `retries` and `min_interval` keys under `[openalex]` / `[crossref]` in `config.toml`, wired through `_load_user_config` next to `mailto`.

### Tests

New `tests/test_retry.py`, with the existing `mock_response` fixture and a scripted `session.get` side effect:

- 429 with `Retry-After: 2` then 200: one sleep of 2 s, the 200 body returned and cached once, the cache file holding exactly one line.
- 429 with an HTTP-date `Retry-After`: the wait is the difference from the fake `now`, and a date in the past waits 0.
- 429 repeated `max_retries + 1` times: `HTTPError` raised, sleeps at the backoff schedule with jitter bounded, cache file untouched (no line written), `retries` counter equals `max_retries`.
- 503 retried, 400 not retried, 404 cached as `None` without a retry.
- `min_interval` pacing: two consecutive misses sleep for the remaining gap; a cache hit between them does not.
- `Retry-After` above `max_wait` is capped.
- CLI and config: `--max-retries 0` disables retrying; a `config.toml` value reaches the client.

### Docs

- README: a "Rate limits and retries" subsection under the clients — what is retried, the defaults, the knobs, and that error responses are never cached.
- `citefinder/prompts/skill.md`: a "Key behaviors" bullet on 429 handling, replacing any advice to purge the cache after a rate limit.
- CHANGELOG `[Unreleased]`: Added (retry, pacing, knobs) and Changed (OpenAlex default pacing).

### Upstream the report-reading guide from a downstream skill copy

A downstream repo carries a full copy of the bundled skill rather than the stub, and over time it grew content that belongs here. Diffing the two copies (0.5.0 against the repo copy) shows the copy is identical except for three additions and the paths. Two of the additions document the tool, not the downstream repo's editing policy, so they move into `citefinder/prompts/skill.md` in the "Verify a whole .bib file" section:

- **Reading the report.** A short guide keyed on the `method` × `status` combinations the verifier emits, placed right after the status list. `method=doi` with `mismatch` / `probable` is a real defect in the bib's own DOI or a disagreeing field. `method=search` with `matched` and a non-empty `matched_doi` is a DOI candidate for an entry that lacks one. `method=search` with `mismatch` / `probable` is usually a wrong-work false positive (books, reports, and other sources the index carries poorly) and is not a reason to rewrite the entry. `unmatched`, `skip-source`, and `doi-not-found` are noise unless they cluster around one publisher or type. The same guide, condensed, goes in the README's verify section.
- **Which fields carry the name split.** For family/given boundary questions, Crossref's `author[i].family` and `author[i].given` are the publisher-deposited split and the fields to compare against; OpenAlex exposes only a flat first-name-first `display_name` (and `raw_author_name` in byline order), which would have to be re-parsed. Goes under the OpenAlex fallback section. The downstream repo's pointer to its own surname-audit script stays out; that is a downstream concern.

What stays downstream: the downstream repo's cache-path convention (its `data/` symlink and per-paper output layout), which is repo policy and becomes a project rule there. Once this ships, the downstream repo can replace its full copy with `citefinder install --local --force` and use `citefinder install --local --check` as the drift check; its bib-editing skill already defers to this section for report reading, so the guide must land upstream before the copy is replaced.

### Implementation order

1. `retry_after_seconds` helper and the retry loop in `_get`, with the injectable clock and the exhaustion test.
2. `min_interval` pacing and its tests.
3. Constructor knobs on both clients; CLI options; config keys.
4. Logging and the `retries` counter in the `verify` summary.
5. README, skill text, changelog — including the report-reading guide and the name-split note from the section above (they can also ship ahead of the retry work as a docs-only patch release, since nothing else depends on them).
6. Release as a minor version (new behavior, backward compatible). Then in the downstream repo: bump the pin, replace its "429 rate-limit guardrail" note with a one-line note that retries are automatic, add the cache-path project rule, and swap the full skill copy for the installed stub.

### Out of scope

- Concurrent or async requests.
- Caching 429 or any error payload (explicitly rejected; the cache holds only records and 404s).
- Managing the OpenAlex daily budget across runs; the run surfaces the 429 and stops retrying after `max_retries`.
- Reading `X-Rate-Limit-*` headers to auto-tune pacing. Worth a follow-up if the fixed defaults still trip Crossref.

## Log

- 2026-09-02T17:20:38-07:00 — Activated on `dev` and implemented on `feature/retry-rate-limited-requests` (draft PR #42). Steps 1–5 of the implementation order shipped in three commits: the retry loop, pacing, knobs, logging, and `retries` counter with `tests/test_retry.py` (24 tests, all driven by a fake clock); the CLI flags and `config.toml` keys; and the docs (README "Rate limits and retries" section, the report-reading guide in the README and skill, the name-split note in the skill, and the changelog). Step 6 (minor release, then the downstream pin bump and stub swap) is left for the release.
- Two small departures from the spec, both deliberate:
  - The `config.toml` key is `max_retries`, not `retries`, so the config key, the `--max-retries` flag, and the constructor parameter share one name. The env fallbacks follow the same shape (`OPENALEX_MAX_RETRIES`, `OPENALEX_MIN_INTERVAL`, and the `CROSSREF_` pair).
  - The injectable clock is three seams rather than two: `sleep` and `monotonic` as planned, plus `clock` (wall time, `time.time`) because the HTTP-date form of `Retry-After` has to be measured against wall time, not the monotonic clock. All three are keyword-only so they stay out of the way of the public knobs.
- Fixed a latent bug while wiring the config keys: `_load_user_config` skipped falsy values, which would have dropped `max_retries = 0` (and `min_interval = 0`). It now checks `is not None`.
- `verify` picks its source at runtime, so its `--max-retries` / `--min-interval` flags do not bind a single `envvar`; an unset flag reads `<SOURCE>_MAX_RETRIES` / `<SOURCE>_MIN_INTERVAL` for whichever source is chosen, so `verify --source crossref` honors the `[crossref]` section.
- The `mock_response` fixture now takes `headers` and raises `requests.HTTPError` from `raise_for_status` for any 4xx/5xx, mirroring `requests`; existing tests only ever used 200 and 404, so nothing else changed.
- 2026-09-02T17:50:03-07:00 — Review gate before merge: `/code-review` at medium on PR #42 (posted as a PR comment). Five findings confirmed, three candidates rejected as conventions the file already had. Fixes in commit `50246ca`; gate green (ruff, ruff format, pyrefly, 196 tests).
  - **Review follow-up.** Raised: (1) the retry and pacing knobs were never validated — `--min-interval inf` passes click's `min=0.0` and crashed in `time.sleep` on the second uncached request, and a negative `max_wait` did the same on the first retry; (2) `verify`'s env fallback skipped the lower bound the flags enforce, so `OPENALEX_MAX_RETRIES=-5` was silently clamped; (3) a float `max_retries = 3.0` in `config.toml` was rejected as "not a number" without naming `config.toml`; (4) `verify`'s help strings retyped the shared templates by hand; (5) `_pace` read the monotonic clock twice on the no-sleep path. Actioned all five: the constructors reject a non-finite or negative knob with `ValueError` (replacing the silent `max(0, max_retries)` clamp); `_client_kwargs`, the funnel every CLI command already routes knobs through, exits 2 for the same inputs, which covers flags, env, and config at once; `_env_number` names the expected type and `config.toml`; `verify` formats the templates; `_pace` reuses its first read and re-reads only after a real sleep. Nine tests added (five knob cases, four CLI). Conscious no-ops: the `DEFAULT_MIN_INTERVAL` export, the per-source Option pairs, and the per-command client construction all match the module's existing pattern; a float `max_retries` is still rejected — only the message changed.
- 2026-09-02T17:54:00-07:00 — CI failed on the new `verify --help` test after the fix commit: in CI, typer renders help through rich with ANSI codes and wraps at 80 columns, splitting the `<SOURCE>_MAX_RETRIES` token that the local run printed as plain text. Rewrote the test to read the help strings off the option objects (`typer.main.get_group`) instead of rendered output, commit `b6e1dfb`; CI green on all four Python versions.

## Retrospective

- The spec held. Retry loop, pacing, knobs, logging, tests, and docs shipped as designed; the only departures were the `max_retries` config key name and a third clock seam for the HTTP-date `Retry-After`, both logged when they happened.
- Injectable clocks paid for themselves twice: the retry tests never sleep, and the review's verifiers reproduced the `inf` crash through the same seam in seconds.
- The review caught a class the tests missed: boundary validation. The tests exercised the retry arithmetic thoroughly but never fed a knob a nonsensical value, and click's `min=0` gave false comfort — it rejects negatives but not `inf` or `nan`. When a value crosses from CLI or config into a library constructor, test the boundary with the values the parser lets through, not only the ones it rejects.
- Look for the existing funnel before adding validation per call site. Every command already passed knobs through `_client_kwargs`, so one check there covered five commands and three input paths.
- Documenting what the code does not do (a 429 is never cached, nothing paced requests) was as useful as the feature itself: it retired the downstream advice to purge cache lines after a rate limit.
- Still open: the downstream pin bump and stub swap ride on the next minor release, and `X-Rate-Limit-*` auto-tuning stays a follow-up if Crossref keeps tripping the fixed defaults.
