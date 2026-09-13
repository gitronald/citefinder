# OpenAlex fallback for arXiv / preprint / thin-metadata DOIs

Crossref doesn't index arXiv DOIs (`10.48550/arXiv.*`) and many repository deposits — those return 404 from `lookup_doi`. Crossref also frequently has thin metadata (missing abstract, abbreviated title, no affiliations) on records that exist. Use OpenAlex as the second source in those cases:

```python
from citefinder import CrossrefClient, OpenAlexClient, is_arxiv_doi

crossref = CrossrefClient(cache_path="~/.cache/citefinder/crossref.jsonl")
openalex = OpenAlexClient(
    cache_path="~/.cache/citefinder/openalex.jsonl",
    mailto="you@example.com",  # opts into OpenAlex's polite pool — faster, higher daily quota
)

doi = "10.48550/arXiv.2410.21554"
if is_arxiv_doi(doi):
    work = openalex.lookup_doi(doi)  # arXiv DOIs go straight to OpenAlex
else:
    work = crossref.lookup_doi(doi) or openalex.lookup_doi(
        doi
    )  # Crossref-first, OpenAlex fallback
```

CLI (top-level commands are OpenAlex by default):

```bash
{cli} doi 10.48550/arXiv.2410.21554
{cli} search "fact-checking large language models"
```

OpenAlex's schema differs from Crossref — different keys for the same data:

| Crossref | OpenAlex |
|---|---|
| `work["title"][0]` (+ `subtitle[0]`) | `work["display_name"]` |
| `work["author"][0]["family"]` | `work["authorships"][0]["author"]["display_name"]` |
| `work["container-title"][0]` | `work["primary_location"]["source"]["display_name"]` |
| `work["published-print"]["date-parts"][0][0]` | `work["publication_year"]` |

**Which fields carry the name split.** For a family/given boundary question — where the surname starts in a multi-part or non-Western name — compare against Crossref's `author[i].family` and `author[i].given`: that split is the one the publisher deposited. OpenAlex exposes only a flat, first-name-first `display_name` (and `raw_author_name` in byline order), which you would have to re-parse, so it cannot settle the question.

OpenAlex stores abstracts as an `abstract_inverted_index` (`{word: [positions]}`), not a string. Use the helper:

```python
from citefinder import reconstruct_abstract

abstract = reconstruct_abstract(work)  # returns plain string or None
```

## OpenAlex API key (optional, for higher rate limits)

`OpenAlexClient` reads the API key in this order: explicit `api_key=...` arg → `OPENALEX_API_KEY` env var → (CLI only) project-local `.env` → (CLI only) `~/.config/citefinder/config.toml`. The key is sent as `Authorization: Bearer ...`, never in the URL or cache key.

For ad-hoc lookups, no key is needed — common-pool requests work fine. To store the key once per machine, drop a TOML file at the XDG config path:

```toml
# ~/.config/citefinder/config.toml
[openalex]
api_key = "your-openalex-key"
mailto = "you@example.com"

[crossref]
mailto = "you@example.com"
```

Each section is optional; omit anything you don't need. The file is plain-text — recommend `chmod 600` so it's only readable by the user.

The CLI picks the config up automatically; project-local `.env` and shell env still override it. A repo can also commit a **project config** — `citefinder.toml`, or `[tool.citefinder]` in `pyproject.toml`, found by walking up from the working directory — with the same keys minus `api_key` (a key there is ignored with a warning, since the file is meant to be committed). Typically it carries `cache_dir` and `mailto`; it sits between the env and the user config in precedence (flag > env and `.env` > project > user > default). `{cli} config` prints the resolved result with each value's source. For programmatic library use, neither file is auto-loaded — pass `api_key=...` and `mailto=...` explicitly or set the env vars before constructing the client.

## Picking `mailto`

Use a project alias (e.g. the `authors` email in `pyproject.toml`) or omit entirely. Don't drop the user's personal email into `mailto` without asking — it's an outbound identifier, and a project/noreply address is the right default.
