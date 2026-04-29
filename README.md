# citefinder

OpenAlex (default) + Crossref reference lookups with local JSONL caching.

A small Python library + CLI for verifying academic references against the
OpenAlex and Crossref APIs. Every lookup is appended to an append-only JSONL
log so repeated queries (across verification passes or sessions) are served
from the cache. Negative results (404s) are cached too, so known-missing DOIs
aren't re-hit.

OpenAlex is the default source: it merges Crossref + Unpaywall + ORCID + ROR
+ repository sources, so it covers what Crossref alone is missing — arXiv
DOIs (`10.48550/arXiv.*`), other preprints, repository deposits — and
frequently has richer metadata (abstracts, full author lists, affiliations)
for records that exist in both. Crossref is still available via the
`crossref` subcommand for its own workflows (book-chapter lookup, the
canonical published-deposit metadata).

### OpenAlex API key (optional)

OpenAlex works without authentication, but a free API key gives you higher
limits and tier-specific endpoints.

- Docs: https://developers.openalex.org/
- Sign up / generate a key: https://openalex.org/login?redirect=/settings/api-key

The key is read in this order:

1. `api_key=...` argument to `OpenAlexClient(...)` (or `--api-key` on the CLI).
2. `OPENALEX_API_KEY` environment variable.
3. A `.env` file in the current working directory or any parent (loaded by
   the CLI; library users can opt in via `from dotenv import load_dotenv`).

```bash
# .env
OPENALEX_API_KEY=oa_pk_...
```

The key is sent as `Authorization: Bearer ...`, never as a URL parameter, so
it doesn't land in cache keys, logs, or referer headers.


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

### OpenAlex (default)

```python
from citefinder import OpenAlexClient, is_arxiv_doi, reconstruct_abstract

openalex = OpenAlexClient(
    cache_path="~/.cache/citefinder/openalex.jsonl",
    mailto="you@example.com",  # opts into OpenAlex's polite pool — faster, higher quota
)

# Single DOI (works for arXiv DOIs that Crossref doesn't index)
work = openalex.lookup_doi("10.48550/arXiv.2410.21554")

# Title-only search — tuned for citation verification. Handles OpenAlex's
# curly-apostrophe quirk and strips filter-reserved punctuation that would
# 400 the request, so straight ASCII inputs match curly-quoted indexed titles.
hits = openalex.search_title("Backstabber's Knife Collection", rows=3)

# Free-text search across titles + abstracts (noisier; prefer search_title
# for citation lookup)
hits = openalex.search("fact-checking large language models", rows=3)

# OpenAlex stores abstracts as an inverted index — reconstruct to plain text
abstract = reconstruct_abstract(work) if work else None

# Helper for routing logic
assert is_arxiv_doi("10.48550/arXiv.2410.21554")
```

The `mailto` argument is optional but recommended: it puts requests into
OpenAlex's [polite pool](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication#the-polite-pool)
for faster responses. The cache key strips `mailto` so changing it doesn't
invalidate prior entries.

### Crossref

```python
from citefinder import CrossrefClient

client = CrossrefClient(cache_path="~/.cache/citefinder/crossref.jsonl")

# Single DOI
work = client.lookup_doi("10.1126/science.aap9559")
print(work["title"][0])

# Bibliographic search (author + title + year)
hits = client.search_bibliographic("Wolfowicz hate speech meta-analysis", rows=3)

# Book chapter via {book_doi}.{NNN} pattern
chapter = client.lookup_book_chapter("10.1017/9781108890960", 5)
```

OpenAlex's schema differs from Crossref. Quick map:

| Crossref | OpenAlex |
|---|---|
| `work["title"][0]` (+ `subtitle[0]`) | `work["display_name"]` |
| `work["author"][0]["family"]` | `work["authorships"][0]["author"]["display_name"]` |
| `work["container-title"][0]` | `work["primary_location"]["source"]["display_name"]` |
| `work["published-print"]["date-parts"][0][0]` | `work["publication_year"]` |

## CLI usage

```bash
# OpenAlex (default)
citefinder doi 10.48550/arXiv.2410.21554 --mailto you@example.com
citefinder search "Backstabber's Knife Collection" --rows 3

# Crossref
citefinder crossref doi 10.1126/science.aap9559
citefinder crossref search "Wolfowicz hate speech meta-analysis" --rows 3
citefinder crossref chapter 10.1017/9781108890960 5
```

Default caches live at `~/.cache/citefinder/openalex.jsonl` and
`~/.cache/citefinder/crossref.jsonl` — separate files so sources don't mix.
Override with `--cache <path>` per command.

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
