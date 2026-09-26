---
id: 21
slug: book-metadata-by-isbn
status: active
branch: feature/book-metadata-by-isbn
created: 2026-09-24T14:32:00-07:00
concluded:
pr:
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
