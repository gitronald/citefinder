---
id: 20
slug: route-datacite-dois
status: active
branch: feature/route-datacite-dois
created: 2026-09-24T14:16:07-07:00
concluded:
pr: https://github.com/gitronald/citefinder/pull/72
---

# Route DataCite DOIs away from Crossref in verify

## Plan

### Problem

`citefinder verify --source crossref` sends every DOI in the bib to Crossref.
DOIs registered with DataCite (arXiv's `10.48550`, Zenodo's `10.5281`, and
other repository prefixes) are never in Crossref, so each one comes back
`doi-not-found` with the note "DOI not in source (404) — common for arXiv /
preprint DOIs". The package already knows this: `is_arxiv_doi()` in
`openalex.py` says callers "should route them to a source that does", but
`verify_entry` never calls it.

The cost is in the report, not the request (the 404 is cached after the first
run). A Crossref report puts the expected 404s in the same status as the ones
that matter: a DOI with a typo, a retracted registration, or a DOI copied from
the wrong work. A reader has to sort them by prefix and dismiss the preprints one
by one. Downstream tools that treat `doi-not-found` as "check this DOI" flag
every preprint as suspect.

### Goal

In a Crossref run, "Crossref cannot index this DOI" is reported separately from
"this DOI does not resolve", and it takes no network call.

### Design

- **Registrar helper.** Add `datacite_registrar(doi: str) -> str | None` beside
  `is_arxiv_doi`, backed by an explicit prefix map
  (`{"10.48550": "arXiv", "10.5281": "Zenodo"}`), case-insensitive on the
  prefix. Rewrite `is_arxiv_doi` in terms of it and export both. Keep the map
  small and explicit: adding a prefix is a one-line change with a test.
- **New status.** `Status.NOT_INDEXED = ("not-indexed", "DOI registered
  outside this source (DataCite): verify with --source openalex")`.
- **Routing in `verify_entry`.** When the source is Crossref and the bib DOI has
  a DataCite prefix, return `NOT_INDEXED` before calling `lookup_doi`, with a note
  naming the registrar. There is no request and no cached 404 row.
  `matched_doi` stays empty and no signals are computed.
- **OpenAlex unchanged.** OpenAlex indexes arXiv, so a 404 there is a real
  miss and stays `doi-not-found`.
- **`crossref doi` subcommand.** The same check applies: print a one-line hint
  ("registered with DataCite; try `citefinder doi <doi>`, which uses OpenAlex")
  instead of a not-found.
- **Report and summary.** `not-indexed` gets its own bucket and count line, so
  a run's totals still add up to the number of entries.

### Compatibility

This changes `results.json`: these entries move from `doi-not-found` to
`not-indexed`. A consumer that counted `doi-not-found` as suspect stops seeing
them, which is the point. A consumer that reads every status needs to recognize
the new value. Note the change under `Changed` in the CHANGELOG, and release it
as a minor version.

### Tests

- `datacite_registrar`: arXiv and Zenodo prefixes in mixed case, a Crossref
  prefix, and an empty string.
- `verify_entry` with a stub Crossref source whose `lookup_doi` raises if it
  is called: a DataCite DOI returns `not-indexed` without the call.
- The same entry with a stub OpenAlex source still looks the DOI up, and a
  `None` there is still `doi-not-found`.
- The report's totals include the new bucket.

### Out of scope

- Resolving the registration agency for arbitrary prefixes through the doi.org
  RA endpoint (`https://doi.org/ra/<prefix>`). It would generalize the map, but
  it is a network call per prefix. It's a candidate follow-up if more DataCite
  repositories show up in practice.
- A DataCite metadata source. OpenAlex already covers the registries that
  matter here.
