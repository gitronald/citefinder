---
id: 21
slug: book-metadata-by-isbn
status: done
branch: feature/book-metadata-by-isbn
created: 2026-09-24T14:32:00-07:00
concluded: 2026-09-25T23:54:16-07:00
pr: https://github.com/gitronald/citefinder/pull/73
---

# Look up book metadata by ISBN through Open Library and the Library of Congress

## Plan

### Problem

Books are the weakest case in a bib check. Most trade books have no DOI, so
Crossref and OpenAlex usually return `unmatched` or a same-word article, and the
fields a book citation needs (full title with subtitle, publisher or imprint,
place of publication, year) go unchecked. The fallback is the publisher's own
page, which is good for title and imprint but often omits the city.

Worked example: a bib entry read

```bibtex
@book{doctorow2025enshittification,
  author = {Doctorow, Cory},
  year = {2025},
  title = {Enshittification},
  publisher = {MCD},
}
```

- `verify` returned `unmatched`. Its three candidates were articles with
  "Enshittification" in the title.
- The publisher's page,
  https://us.macmillan.com/books/9780374619329/enshittification/, gave the
  subtitle ("Why Everything Suddenly Got Worse and What to Do About It"), the
  imprint (MCD), the ISBN (9780374619329), and the on-sale date. It gave **no
  place of publication**.
- The ISBN resolved the gap in two steps:
  1. **Open Library** (`https://openlibrary.org/isbn/9780374619329.json`)
     returned `publish_places: ["New York, USA"]`,
     `publishers: ["MCD / Farrar, Straus and Giroux"]`, and
     `lccn: ["2025007165"]`. Its `source_records` was `["amazon:0374619328"]`,
     a retailer feed, so it is a lead rather than a source of record.
  2. **Library of Congress** (`https://lccn.loc.gov/2025007165/marcxml`),
     using the LCCN from step 1, returned the Cataloging in Publication
     record, which is what is printed on the copyright page:
     `245 Enshittification : why everything suddenly got worse and what to do
     about it`, `250 First edition.`,
     `264 New York : MCD, Farrar, Straus and Giroux, 2025.`

That's three hand-run requests and some reading of MARC tags. The pattern is
general: ISBN → Open Library → LCCN → Library of Congress. citefinder should run
it the same way every time.

### Goal

Given an ISBN, or a book entry whose ISBN can be found, citefinder returns a
normalized book record (title, subtitle, contributors, publisher, place, year,
edition, ISBNs, LCCN), cached like every other lookup. Each field records which
source it came from, so a Library of Congress value is distinguishable from a
retailer-fed one.

### Design

- **`OpenLibraryClient`** (`citefinder/openlibrary.py`), a `CachedJsonClient`
  like the other sources:
  - `lookup_isbn(isbn)` → `https://openlibrary.org/isbn/<isbn>.json`; a 404
    is cached as `None`.
  - `search(title, author)` → `https://openlibrary.org/search.json` for
    entries with no ISBN, returning candidate ISBNs to confirm, never to
    apply.
  - Its model keeps `source_records`, so provenance survives. A record fed only
    by `amazon:` or other retailer entries is marked `provenance="retailer"`.
- **`LocClient`** (`citefinder/loc.py`):
  - `lookup_lccn(lccn)` → `https://lccn.loc.gov/<lccn>/marcxml`. The body is
    MARCXML, not JSON, so the client parses it to a dict before caching, and
    the cache stays JSONL with one row per LCCN.
  - A small MARC reader for the fields a citation uses: `020` (ISBN and
    qualifier), `100`/`700` (names), `245 $a $b` (title, subtitle), `250`
    (edition), `264 _1 $a $b $c` (place, publisher, date; with `260` as the
    older equivalent), and `040 $a` (cataloging agency; `DLC` means LoC
    cataloged it).
  - ISBD punctuation (`" :"`, `" /"`, trailing `.` and `,`) is stripped when
    reading. Casing is kept as cataloged, since catalog titles are in sentence
    case and title case is the caller's job.
- **The chain:** `book_record(isbn)` runs Open Library, then LoC if an LCCN is
  present, and merges the results with LoC winning field by field. Every field
  carries a `source` of `loc`, `openlibrary`, or `openlibrary:retailer`.
  Both requests go through the normal cache, so a second run is offline.
- **CLI:** `citefinder isbn <isbn>` prints the merged record with each field's
  source, plus the two URLs, so the evidence is one click away.
- **In `verify`:** for `@book` (and the `@incollection`/`@inbook` container)
  with an `isbn` field and no DOI, run the chain and compare title/subtitle,
  publisher, location, and year, reporting disagreements and fields the bib is
  missing (such as `location`) as suggestions. Entries with no `isbn` get an
  Open Library search candidate at most. This is opt-in behind `--books` for
  the first release, so existing runs and reports don't change.
- **Pacing:** Open Library asks for a descriptive User-Agent with contact
  details and modest request rates. Default to 1 req/s for both hosts, with the
  same retry and backoff knobs as the other clients.

### Rules the record must keep

- **An imprint and its parent are both right.** LoC's `264 $b` often reads
  "Imprint, Parent". Report both, and don't flag a bib that carries only the
  imprint.
- **Retailer-fed data is a lead.** An Open Library value whose only source is
  a retailer feed is never reported as confirmed. When LoC has no record, the
  field is reported as `unconfirmed` with the Open Library value as a
  candidate.
- **Silence is not agreement.** A record missing a field (a publisher page with
  no city, a MARC record with no `264 $a`) leaves that field unconfirmed. It
  doesn't count as a match.

### Tests

- Fixtures: the Open Library JSON and the LoC MARCXML for ISBN 9780374619329
  (trimmed), plus a book with no LCCN and a 404 ISBN.
- MARC reader: `245`/`264` punctuation stripping, `260` fallback, and multiple
  `020`s with qualifiers.
- Chain: LoC overrides Open Library field by field; retailer-only provenance
  is marked; a missing LCCN stops after Open Library without error.
- `verify --books`: a bib missing `location` and subtitle gets both as
  suggestions with LoC as the source, and an entry whose publisher is only the
  imprint is not flagged.

### Out of scope

- WorldCat or other catalogs that need an API key.
- Chapter-level metadata inside edited volumes. Crossref's chapter lookup
  already covers the DOI case.
- Writing any value into a bib. This plan reports; applying stays the caller's
  decision.

## Log

### 2026-09-25 — implementation

- `eaf413d` add open library and loc clients with isbn book chain —
  `openlibrary.py`, `loc.py`, `books.py`, the `books` argument on
  `verify_entry`, the `isbn` command, `verify --books`, config keys, cache
  hosts, models.
- `8894afd` record pr url on plan 021.
- `a24a7ea` update loc client to sru, add isbn fallback, tests, docs.
- Merged `dev` (plan 020, DataCite routing) into the branch; conflicts were
  import lists and adjacent doc blocks, resolved by keeping both sides.

**The plan's LoC endpoint is dead.** A live run cached a miss for the worked
example: `https://lccn.loc.gov/<lccn>/marcxml` returns 404 for every LCCN
tried, and the bare permalink now redirects to the new catalog's search
page. Two live alternatives were found; the catalog's SRU gateway
(`http://lx2.loc.gov:210/LCDB`, `recordSchema=marcxml`) was chosen over the
`loc.gov/item/<lccn>/?fo=json` API because it returns the MARC fields the
plan's reader was written for (the JSON API flattens `264` into one ISBD
string). Consequences:

- The gateway is plain HTTP; it does not answer on TLS.
- It also takes `bath.isbn=`, so the chain no longer depends on Open Library
  carrying an LCCN: with no edition or no LCCN the catalog is asked by ISBN,
  and a record can come from the catalog alone.
- The cache host is `lx2.loc.gov:210` (the router keys on `netloc`, port
  included); `lccn_url` keeps `https://lccn.loc.gov/<lccn>` as the human
  link since it still resolves.
- An unknown number is a 200 with zero records, not a 404, so `_decode`
  returns `None` for a body without a MARC record and it is cached as a
  miss like a 404. The parser skips the SRU `<zs:record>` envelope, whose
  local name is also `record`.

**Title signal.** A one-word bib title (`Enshittification`) against the
catalog's title-with-subtitle scored 0.08 and failed the title check. The
book path now compares against the bare catalog title when the bib title
equals it, and the subtitle becomes a suggestion; against anything else the
full title is used.

### 2026-09-25 — review follow-up

`/code-review` at level medium on PR #73 (two finders, three verifiers) raised
ten findings; nine actioned in `7885ae4`, each with a regression test:

- A malformed `editor` on a chapter entry raised out of `verify_book` and
  aborted the run — the entry's `author` was guarded, the remapped editor was
  not. Guarded; reports a per-entry `error`.
- MARCXML was parsed from `response.text`; the gateway's `text/xml` carries
  no charset, which `requests` decodes as ISO-8859-1, garbling non-ASCII
  names. The raw bytes are parsed now so the XML declaration decides.
- `compare_book` never suggested a *missing* title, unlike every other
  field. It does now.
- `BookRecord.confirmed` looked only at the title's source, so a retailer-fed
  year behind a catalog title could still produce a "Library of Congress
  record" match. It now requires the title, year, and first contributor to
  be catalog-sourced.
- Three inline copies of the ISBN-cleaning rule and two of the year regex
  collapsed into `normalize_isbn` and `YEAR_RE`; the contact-in-User-Agent
  splice moved into `_default_user_agent(contact)`; the `isbn` command uses
  `BookLookup.record`.
- Conscious no-op: the CLI fakes in `conftest.py` overlap the unit-test
  fakes in `tests/test_books.py`, but they serve different roles
  (constructor-kwargs capture without a session vs. injectable data).

## Retrospective

- **Verify the endpoint before writing the reader.** The plan's LoC URL was
  hand-tested when the plan was written and dead two days later (the LoC
  catalog migration retired the `/marcxml` permalinks). The first live run
  caught it only because the fixture-driven tests could not; a one-request
  probe at the start of implementation would have saved a rewrite of the
  client half. The SRU gateway that replaced it turned out better — it
  takes an ISBN directly, so the chain no longer depends on Open Library.
- **Fixture-only tests miss transport.** The charset bug (`response.text`
  under a charset-less `text/xml`) is invisible to a `MagicMock` response
  and to any ASCII fixture. Worth a rule for future non-JSON sources: parse
  bytes, and keep one non-ASCII fixture.
- **"Confirmed" has to follow the signals, not the title.** The provenance
  rule was written per field but the verdict cap was keyed to one field;
  the review found the gap the plan's tests did not describe.
- **Parallel plans on the same modules merge fine when both add.** Plan 020
  landed first; every conflict was an import list or an adjacent doc block,
  resolved by keeping both sides, and the merged suite passed unchanged.
- **Merging dev before the review** meant the review saw the real PR diff
  and the index regeneration after a conflicted merge was not forgotten.
