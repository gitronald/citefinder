---
id: 8
slug: retry-rate-limited-requests
status: active
branch: feature/retry-rate-limited-requests
created: 2026-09-02T16:51:43-07:00
concluded:
pr:
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
