# citefinder

Crossref reference lookups with local JSONL caching.

A small Python library + CLI for verifying academic references against the
Crossref API. Every lookup is appended to an append-only JSONL log so repeated
queries (across verification passes or sessions) are served from the cache.
Negative results (404s) are cached too, so known-missing DOIs aren't re-hit.

## Install

```bash
uv add citefinder
```

Or for development:

```bash
git clone https://github.com/gitronald/citefinder
cd citefinder
uv sync
```

## Library usage

```python
from citefinder import CrossrefClient

client = CrossrefClient(cache_path="~/.cache/citefinder/crossref.jsonl")

# Single DOI
work = client.lookup_doi("10.1126/science.aap9559")
print(work["title"][0])

# Bibliographic search
hits = client.search_bibliographic("Wolfowicz hate speech meta-analysis", rows=3)

# Book chapter via {book_doi}.{NNN} pattern
chapter = client.lookup_book_chapter("10.1017/9781108890960", 5)
```

## CLI usage

```bash
# Single DOI
citefinder doi 10.1126/science.aap9559

# Search by author + title
citefinder search "Wolfowicz hate speech meta-analysis" --rows 3

# Book chapter
citefinder chapter 10.1017/9781108890960 5
```

The default cache lives at `~/.cache/citefinder/crossref.jsonl`. Override
with `--cache <path>` per command.

## Why JSONL?

The cache is an append-only log: every lookup is one JSON object per line.
Benefits:

- **Auditable**: `cat`/`grep` to see every query that ever ran.
- **Diffable**: plays nicely with git if you want to commit a project's cache.
- **Crash-safe**: an interrupted write loses at most the last line.
- **Recoverable**: rebuild the in-memory dict by replaying the log.

Latest value wins on replay, so over-writes are a no-op semantic.

## Tests

```bash
uv run pytest
```
