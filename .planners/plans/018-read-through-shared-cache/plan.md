---
id: 18
slug: read-through-shared-cache
status: active
branch: feature/read-through-shared-cache
created: 2026-09-12T20:52:05-07:00
concluded:
pr: https://github.com/gitronald/citefinder/pull/64
---

# Read the shared cache as a fallback for per-run caches

## Plan

Successor to [[016-cache-maintenance-commands]]. That plan built `cache merge`,
which folds every per-run cache into the shared `<cache_dir>/<source>.jsonl`,
and deliberately left one thing out: nothing reads the merged file during a
lookup. `verify` opens only the per-run cache it derives from the bib path, so
every new bib starts cold and refetches DOIs an earlier run already resolved.
This plan closes that gap without touching where anything is written.

### Goal

A record any earlier run fetched is a cache hit for every later run, from any
per-run cache directory, while the per-run cache stays the only file a run
writes to.

- **Per-run cache: read first, sole write target.** A fetch made by this run
  wins over anything older, and the run's evidence stays beside its
  `results.json`, exactly as today.
- **Shared cache: read-only fallback.** Consulted only on a per-run miss. Never
  written by a lookup; it changes only through an explicit `cache merge`, which
  keeps plan 016's rule that a routine run cannot launder a row into it.
- **Cached 404s fall back too.** A `None` in the shared cache is a hit, so a DOI
  known to be dead is not refetched. `cache merge` already decides which wins
  when a 404 and a record disagree; this plan does not second-guess it.

### Design

**A layered cache object in `citefinder/cache.py`.** `_base` only calls `get`,
`put`, and `__contains__` on `self.cache`, plus `__len__` for the pre-load
count, so a small class satisfies it without changing the client protocol:

```python
class LayeredCache:
    """A writable primary cache backed by read-only fallbacks."""

    def __init__(self, primary: JsonlCache, *fallbacks: JsonlCache) -> None: ...

    def __contains__(self, key): primary, then each fallback in order
    def get(self, key):          first layer that contains it
    def put(self, key, value):   primary only
    def __len__(self):           distinct keys across all layers
```

- `__contains__` must be layered too, not just `get`: `_base` checks
  `key in self.cache` before `get`, and a cached `None` is only a hit if the
  membership test sees it.
- A fallback whose file does not exist is an empty layer, not an error, so the
  option is safe to wire on by default before any merge has run.
- The fallback is opened read-only in the sense that nothing calls `put` on it;
  `JsonlCache` itself needs no new mode.
- The rate-limit snapshot (`rate_limit_key`) is stored through the same cache,
  so a fresh per-run cache would inherit the shared file's snapshot. That is
  the correct behavior: the snapshot is a source-wide counter, and the merge
  already treats it as newest-`ts`-wins. Keep it, and add a test asserting the
  per-run snapshot still wins once one is captured.

**A `fallback_cache` argument on the clients.** `CrossrefClient` and
`OpenAlexClient` gain `fallback_cache: str | Path | None = None`, forwarded to
`_base`, which builds `LayeredCache(JsonlCache(cache_path),
JsonlCache(fallback_cache))` when both are given. Passing `cache=` as an object
bypasses it, as today. Where a file is given for `cache_path` only, nothing
changes.

**CLI wiring.** `verify` passes `fallback_cache=resolve_cache_path(source,
_verify_root(cache_dir))`, the same shared file `cache merge` writes and the
`doi`/`search` commands already use directly. Guard the one case where they
coincide: when `--out` or a `--cache` makes the per-run path equal the shared
path, pass no fallback rather than layering a file over itself.

- `--no-fallback` on `verify` disables it, so a run can be pinned to a single
  file (fixture caches in tests, or a deliberately cold run for
  measurement). A config key is not needed; the flag is per run by nature.
- The `Cache:` header line grows a second clause, and the pre-load count now
  spans both layers:

  ```
  Cache: <out_dir>/crossref.jsonl (12 entries pre-loaded)
  Fallback: <cache_dir>/crossref.jsonl (1268 entries)
  ```

  Both lines matter to anyone reading a verify log after the fact. A fallback
  line showing `0 entries` against a populated cache directory is the tell that
  the merge has not been run, or that the wrong `cache_dir` resolved.

- The `doi`, `search`, and `crossref` subcommands already read and write the
  shared file directly, so they need no fallback. Leave them alone.

**Cache-effectiveness counters.** `_base` reports cached-vs-fetched per run.
A fallback hit should count as a cache hit, not a fetch, so the counter reads
the layered object like any other cache. No new counter unless the split turns
out to be useful; if it does, `hits_from_fallback` is a one-line addition.

### Out of scope

- **Writing through to the shared file.** Explicitly not: the merge stays the
  only writer, for the reasons plan 016 gives.
- **Auto-merge after a run.** Same reason.
- **More than one fallback layer at the CLI.** `LayeredCache` accepts several
  so a caller building clients in Python can chain them, but `verify` wires
  exactly one.
- **Offline `lookup` over the shared cache.** Still the plausible next step
  named in plan 016; still waits on a caller to settle the output shape.

### Implementation order

1. `LayeredCache` in `cache.py` with unit tests in `test_cache.py`: primary
   wins, fallback hit including a cached `None`, `put` never touches the
   fallback file, missing fallback file is empty, `__len__` counts distinct
   keys, rate-limit snapshot precedence.
2. `fallback_cache` on `_base`, `CrossrefClient`, and `OpenAlexClient`, with a
   `test_client.py` case that a DOI present only in the fallback produces no
   HTTP request.
3. `verify` wiring, `--no-fallback`, the same-path guard, and the header lines,
   with `test_verify.py` cases for the fallback hit, the flag, and the
   coincident-path case.
4. Docs: README's verify and cache sections, the skill recipe if it describes
   the `Cache:` header, and a CHANGELOG entry under Unreleased.

## Log

### 2026-09-12

- Activated on `dev` (`ad3fa6b`); work in a worktree on
  `feature/read-through-shared-cache`. Draft PR opened after the first
  implementation commit and recorded in `pr:` (`a4e7142`, with the regenerated
  index — the frontmatter change alone failed `planners validate`).
- Steps 1–2 (`889d056`): `LayeredCache` in `cache.py` as specified, exported
  from `citefinder`. `JsonlCache` gained a `keys()` method so `__len__` can
  count distinct keys across layers. `fallback_cache` added to `_base`,
  `CrossrefClient`, and `OpenAlexClient`; it only applies when `cache_path` is
  given, so a `cache=` object is used as-is. Tests: six `LayeredCache` cases in
  `test_cache.py` (including the rate-limit snapshot inheriting from the
  fallback until the run captures its own) and a `test_client.py` case showing
  a fallback record and a fallback 404 make no request while a miss writes to
  the per-run file only.
- Step 3 (`12d9630`): `verify` passes the shared `<cache-dir>/<source>.jsonl` as
  the fallback, `--no-fallback` disables it, and a resolved-path comparison drops
  it when `--out` makes the per-run file the shared one. The pre-load count
  spans both layers and a `Fallback: <path> (N entries)` line follows `Cache:`.
  **Deviation:** the CLI tests landed in `tests/test_config.py`, beside the
  existing `verify` CLI tests, not `test_verify.py`, which only covers
  `verify_entry`. Four cases: default fallback path and header, the flag, the
  coincident-path guard, and an end-to-end run where the network is patched to
  fail and the entry is still a hit with both cache files left untouched.
- Step 4 (`b1f837d`): README verify example and cache-maintenance section
  (header sample, what `0 entries` means), skill cache-maintenance paragraph,
  and an Unreleased CHANGELOG entry. The skill never described the `Cache:`
  header, so the paragraph claiming the caches "do not learn from each other"
  was the part that needed rewriting.
- Gate: ruff and pyrefly clean; 399 tests pass at 97.57% coverage. Seven
  `test_install.py` tests fail for an environmental reason unrelated to this
  plan: stray `.git` and `.claude` directories at the root of the system temp
  dir make `find_repo_root` stop there for pytest's temp paths.
