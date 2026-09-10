# Crossref rate limits

What the Crossref REST API advertises, how `citefinder` paces against it, and
where the two disagree. Written after the v0.10.2 release, which gave
`CrossrefClient` the same `min_interval = 0.1` default as `OpenAlexClient`.

## What Crossref advertises

Crossref does not publish a fixed rate in prose. It advertises the limit in
effect on every response, in two headers:

> Any rate limits that are in effect will be advertised in the
> `X-Rate-Limit-Limit` and `X-Rate-Limit-Interval` HTTP headers.
>
> — [CrossRef/rest-api-doc](https://github.com/CrossRef/rest-api-doc)

The `50 requests a second` figure that circulates in blog posts and older
client code is the **example** used in that document, not a promise. Read the
headers instead — they are the only authoritative statement of the current
limit, and they vary by pool.

## Measured values

Measured 2026-09-09, from a residential connection with no API key:

| Request | `X-Rate-Limit-Limit` | `X-Rate-Limit-Interval` |
| --- | --- | --- |
| anonymous | 1 | 1s |
| `?mailto=you@example.com` | 3 | 1s |
| `mailto:` in the `User-Agent` | 3 | 1s |

Reproduce with:

```bash
curl -s -o /dev/null -D - "https://api.crossref.org/works?rows=1" | grep -i '^x-rate'
curl -s -o /dev/null -D - "https://api.crossref.org/works?rows=1&mailto=you@example.com" | grep -i '^x-rate'
```

These are a point-in-time reading, not a contract. Crossref adjusts them —
"from time to time Crossref needs to impose rate limits to ensure that the free
API is usable by all" — so re-measure rather than trusting this table.

## The polite pool

Supplying contact information moves a request to a reserved pool, which is why
the second and third rows above get 3x the anonymous rate:

> any API queries that use HTTPS and have appropriate contact information will
> be directed to a special pool of API machines that are reserved for polite
> users.

Two ways in, both accepted:

- a `mailto=` query parameter, or
- a `mailto:` inside the `User-Agent` string.

`citefinder` supports the first directly — pass `mailto=` to `CrossrefClient`,
set `CROSSREF_MAILTO` in the env, or put `mailto` under `[crossref]` in
`config.toml`. The cache key strips the parameter, so rotating the address does
not invalidate cached responses. Measured above: the User-Agent form earns the
same pool as the query parameter, so a client that sets a contact-bearing
User-Agent is already polite without the parameter.

## How citefinder paces, and the gap

Since v0.10.2 both clients default to `min_interval = 0.1` — one request per
100 ms, or 10 per second — from the shared `citefinder._base.DEFAULT_MIN_INTERVAL`.

Against the values measured above, **that default is faster than Crossref
advertises**: 10x the anonymous limit and roughly 3x the polite-pool limit. It
is still a strict improvement on what preceded it (before v0.10.2 the Crossref
client was unpaced and would burst as fast as the network allowed), but it does
not by itself keep a run inside the advertised rate.

What absorbs the difference is the retry path: a `429` is retried up to
`max_retries` times, honoring `Retry-After` when Crossref sends it and backing
off exponentially otherwise, and error responses are never cached. A run that
outpaces the limit slows down rather than failing.

To stay inside the advertised rate instead of relying on retries, set the
interval explicitly:

```python
# ~3/s, the polite-pool rate
CrossrefClient(cache_path="...", mailto="you@example.com", min_interval=0.34)

# ~1/s, the anonymous rate
CrossrefClient(cache_path="...", min_interval=1.0)
```

```bash
citefinder verify refs.bib --source crossref --min-interval 0.34
```

Or persist it under `[crossref]` in `config.toml`:

```toml
[crossref]
mailto = "you@example.com"
min_interval = 0.34
```

The cost is wall-clock on a cold cache: a 150-entry bibliography is ~15 s of
pacing at 0.1 s versus ~50 s at 0.34 s. A warm cache pays none of it — cache
hits are not requests and are never paced.

## OpenAlex, for contrast

The `0.1` default was inherited from OpenAlex, whose documented limits have
since changed shape. OpenAlex now meters a **daily budget in credits**, not a
sustained request rate: every account gets $1 of API usage per day free, a free
API key raises the budget 10x, and `429` is returned when the budget is spent
**or** when a client exceeds 100 requests per second. Responses carry
`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Credits-Used`, and
`X-RateLimit-Reset` (seconds to the midnight-UTC reset). The current
documentation does not describe a `mailto` polite pool.

So the comment on `DEFAULT_MIN_INTERVAL` — which cites OpenAlex's "10 requests
per second (plus a daily cap)" — describes an earlier published limit. The
`0.1` value remains comfortably inside OpenAlex's current 100/s ceiling; what
governs an OpenAlex run now is the daily budget, not the interval. See
[openalex.md](openalex.md) for what each kind of lookup costs against that
budget.

## Sources

- [CrossRef/rest-api-doc](https://github.com/CrossRef/rest-api-doc) — rate-limit
  headers, the polite pool, and both ways to supply contact information.
- [Tips for using the Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/)
  — "pay attention to the http status code and back off if you start seeing 429
  statuses."
- [OpenAlex: authentication](https://help.openalex.org/api/authentication/) —
  API-key handling, the 100 req/s ceiling, and the `X-RateLimit-*` headers.
- [OpenAlex: pricing](https://help.openalex.org/access/pricing/) — the $1/day
  free budget and paid tiers.
- Live header measurements, 2026-09-09, reproduced by the `curl` commands above.
