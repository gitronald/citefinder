---
id: 5
slug: expand-tilde-in-cache-path
status: draft
branch:
created: 2026-08-23T21:45:53-07:00
concluded:
pr:
---

# Expand tilde in cache paths

## Plan

### Problem

`JsonlCache.__init__` (`citefinder/cache.py`) stores its path with a bare
`Path(path)` and no `.expanduser()`. `pathlib` never expands tildes, so a
literal `cache_path="~/.cache/citefinder/openalex.jsonl"` is a *relative*
path whose first component is a directory named `~`. The write side then
compounds it: `put()` runs `self.path.parent.mkdir(parents=True,
exist_ok=True)`, silently manufacturing a `./~/.cache/citefinder/` chain
under the current working directory and appending cache records there.

Only the Python API is exposed. The CLI never hits it: its defaults are
built absolute (`Path.home() / ".cache" / ...`), and a user passing
`--cache ~/...` gets shell expansion before typer sees the string. But
passing a `~` string to the `cache_path` constructor arg — which
documented usage examples do — reproduces it every time.

Observed in the wild (2026-08): a downstream editorial-workflow skill's
example code passed `cache_path="~/.cache/citefinder/openalex.jsonl"` from
a repo root and left a stray `~/` directory in that repo.

Consequences beyond the stray directory:

1. **The cache silently fails its purpose** — each CWD gets its own orphan
   cache, and `if self.path.exists()` on construction never finds the real
   home cache, so cached lookups and remembered 404s are not reused across
   sessions.
2. **Deletion footgun** — a literal `~` directory invites `rm -rf ~`,
   which unquoted targets the user's actual home directory.
3. **Repo pollution** — the orphan dir shows up as untracked `~/` wherever
   the API was invoked.

### Fix

One line in `JsonlCache.__init__`:

```python
self.path = Path(path).expanduser()
```

This covers every entry point — both `CrossrefClient` and `OpenAlexClient`
funnel `cache_path` through `JsonlCache` via `_base.py`. No other path
inputs in the package accept user-supplied strings that plausibly carry a
tilde (CLI options are shell- or `Path.home()`-resolved; `bib` inputs are
read via CLI arguments).

### Tests

- `JsonlCache("~/citefinder-test-cache.jsonl").path` resolves under
  `Path.home()` (no literal `~` component); use `tmp_path` +
  `monkeypatch.setenv("HOME", ...)` so the test never touches the real
  home directory.
- Constructing `OpenAlexClient(cache_path="~/...")` and performing a cached
  `put`/`get` round-trip creates no `./~` directory in the CWD
  (`monkeypatch.chdir(tmp_path)`).

### Changelog

Add a `[Unreleased]` **Fixed** entry: cache paths given with a leading `~`
now expand to the user's home directory instead of creating a literal `~/`
directory under the current working directory.

### Implementation order

1. Fix + tests in one commit.
2. Changelog entry.
