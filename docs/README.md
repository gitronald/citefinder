# Docs

Reference notes that outlive a single release. Usage lives in the top-level
[README](../README.md); the plan record lives in [`.planners/`](../.planners/).

- [Crossref rate limits](crossref.md) — what the API advertises, what the
  polite pool changes, how `citefinder`'s default pacing compares, and how to
  stay inside the advertised rate.
- [OpenAlex rate limits and costs](openalex.md) — the daily credit budget that
  replaced the old per-second limit, what each kind of lookup costs, and which
  `citefinder` calls are free.
