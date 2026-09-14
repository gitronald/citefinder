# Year mismatches between Crossref and OpenAlex — flag and prefer the final printed record

Crossref and OpenAlex regularly disagree on a work's year because they index different events. Crossref's `published-print` tracks the issue/volume year; OpenAlex's `publication_year` often collapses to the online-first or precursor date. Treat any year mismatch as something to flag for review, then default to the **final printed record** — the journal volume year, or for books the publisher's first-published edition year.

Two patterns to watch:

- **Online-first vs volume year (journal articles).** A DOI minted in 2016-10 for online-first, printed later in a volume (2018-09). Crossref splits it cleanly (`published-print` 2018-09, `created` 2016-10); OpenAlex's `publication_year` is 2016. Cite the volume year (2018).
- **Precursor work vs published edition (books).** A monograph DOI may surface in OpenAlex as a `dissertation` dated 2020, while Crossref returns the same DOI as a `monograph` issued 2022 — the dissertation became the book. Cite the publisher's first-published year (2022).

Quick mismatch check:

```python
cr_year = (work_cr.get("published-print") or work_cr.get("issued") or {}).get(
    "date-parts", [[None]]
)[0][0]
oa_year = work_oa.get("publication_year")
if cr_year != oa_year:
    # flag for human review; default to printed-volume / published-edition year
    ...
```

If only OpenAlex has the record, sanity-check its `type` field — `dissertation` or `posted-content` next to a journal/monograph DOI is the giveaway that you're looking at a precursor, not the cite-target.
