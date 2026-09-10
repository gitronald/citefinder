# OpenAlex rate limits and costs

OpenAlex no longer meters a sustained request rate. It meters a **daily budget
in credits**, and different kinds of lookup cost different amounts — a single
entity by identifier is free, a search is not. That changes what "rate limit"
means for `citefinder`: pacing is not the binding constraint, the daily budget
is. See [crossref.md](crossref.md) for the other source, which still works the
old way.

## The budget

| | Keyless (measured) | With a free API key (documented) |
| --- | --- | --- |
| Daily budget | 1000 credits / $0.10 | 10x that, ~$1.00 |
| Resets | midnight UTC | midnight UTC |

One credit is $0.0001. OpenAlex documents that "every account gets $1 of API
usage per day for free" and that a free API key raises the budget 10x; the
$0.10 figure above is what a keyless request actually advertised when measured,
which is consistent with that 10x.

A `429` comes back when the daily budget is spent **or** when a client exceeds
**100 requests per second**.

## The headers

Every response carries the budget state:

```
x-ratelimit-limit: 1000              # daily budget, in credits
x-ratelimit-remaining: 998           # credits left today
x-ratelimit-limit-usd: 0.1           # the same budget in dollars
x-ratelimit-remaining-usd: 0.0998
x-ratelimit-credits-used: 1          # what THIS request cost
x-ratelimit-cost-usd: 0.0001
x-ratelimit-reset: 62065             # seconds until the midnight-UTC reset
```

Read `x-ratelimit-credits-used` to price any call shape yourself:

```bash
curl -s -o /dev/null -D - "https://api.openalex.org/works?search=kelp" | grep -i '^x-ratelimit'
```

## What each call costs

Measured 2026-09-09, keyless. Credits are per request, independent of
`per-page`:

| Call | Credits | Used by |
| --- | --- | --- |
| `/works/W2741809807` (entity by ID) | 0 | — |
| `/works/doi:10.1371/journal.pone.0000308` | **0** | `lookup_doi` |
| `/works?filter=doi:...` | 1 | — |
| `/works?per-page=1` (plain list) | 1 | — |
| `/works?group_by=publication_year` | 1 | — |
| `/works?search=...` | **10** | `search` |
| `/works?filter=title.search:...` | **10** | `search_title` |

This matches OpenAlex's published pricing — single entities free, list/filter
$0.10 per 1000 calls, full-text search $1 per 1000 — and adds the detail that
matters most here: **`filter=title.search:` is priced as a search, not as a
filter.**

Note the 1-credit row for `filter=doi:`. Fetching a DOI through the filter
syntax costs a credit; fetching the same record through the entity path costs
nothing. `citefinder`'s `lookup_doi` uses the free form (`/works/doi:{doi}`).

## What this means for citefinder

**DOI lookups are free.** A `.bib` where every entry carries a DOI verifies for
zero credits, however large it is. Budget is only consumed by entries that fall
back to a title search.

**Title searches are the whole cost.** At 10 credits each, a keyless 1000-credit
day buys about **100 searched entries**; a free API key raises that to roughly
1000. A bibliography of 150 entries with no DOIs will exhaust a keyless budget
before it finishes — with an API key it fits comfortably.

**Pacing is not the constraint.** The default `min_interval = 0.1` sends 10
requests per second, well inside the documented 100/s ceiling. Lowering it will
not save budget (credits are per request, not per second) and raising it will
not buy more. What conserves budget is DOIs in the `.bib`, and the cache: a
cached lookup issues no request and costs nothing.

**Set an API key when searching at volume.** `citefinder` sends it as
`Authorization: Bearer <key>`, so it never lands in cache keys or URL logs:

```toml
# ~/.config/citefinder/config.toml — user config or .env only, never a project file
[openalex]
api_key = "..."
```

## mailto does nothing here

Crossref's polite pool is entered with a `mailto`. OpenAlex's current
documentation does not describe a polite pool, and measurement agrees: adding
`&mailto=you@example.com` returned an identical budget and identical per-call
cost. `citefinder` still supports `mailto` for OpenAlex and it is harmless to
set, but it buys nothing today. The lever that exists is the API key.

## Why the default is still 0.1

OpenAlex once published a 10 req/s limit, and `DEFAULT_MIN_INTERVAL = 0.1` came
from it. That figure is no longer what OpenAlex documents — the current ceiling
is 100 req/s alongside the credit budget above — but the value is kept as a
deliberate conservative floor rather than raised to match: at 10/s a run stays
an order of magnitude inside the ceiling, and since credits are charged per
request and not per second, going faster would buy nothing but a higher chance
of tripping the 429. Crossref, whose limit *is* a per-second rate, resolves its
default from the polite-pool check instead — see [crossref.md](crossref.md).

## Sources

- [OpenAlex: authentication](https://help.openalex.org/api/authentication/) —
  API-key handling, the 100 req/s ceiling, the `429` conditions, and the
  `X-RateLimit-*` headers.
- [OpenAlex: pricing](https://help.openalex.org/access/pricing/) — the $1/day
  free budget, pay-as-you-go, and the annual tiers.
- [OpenAlex: example costs](https://help.openalex.org/access/example-costs/) —
  single entities free, list/filter $0.10 per 1000, full-text and semantic
  search $1 per 1000, PDF downloads $10 per 1000.
- Live header measurements, 2026-09-09, keyless, reproduced by the `curl`
  commands above.
