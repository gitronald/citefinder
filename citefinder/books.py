"""Book metadata by ISBN: Open Library, then the Library of Congress.

The chain is `ISBN -> Open Library edition -> LCCN -> Library of Congress
MARC record`, with a direct ISBN query on the catalog when Open Library has
no edition or no LCCN, merged field by field with the Library of Congress
winning wherever it has a value. Every field remembers where it came from:

- `loc` — the catalog record, usually Cataloging in Publication data
  supplied by the publisher and printed on the copyright page.
- `openlibrary` — an Open Library edition fed by at least one library
  catalog or scan.
- `openlibrary:retailer` — an Open Library edition fed only by retailer
  feeds (Amazon, Better World Books). Usually right, never confirmed: a
  lead to check, not a value to apply.

Three rules the merged record keeps:

- **An imprint and its parent are both right.** A catalog `264 $b` often
  reads `MCD, Farrar, Straus and Giroux`; `publisher_names` splits that so a
  bib carrying only the imprint is not flagged.
- **Retailer-fed data is a lead.** A field whose only source is a retailer
  feed is reported as unconfirmed, with the value as a candidate.
- **Silence is not agreement.** A source that lacks a field leaves it
  unconfirmed; a missing value never counts as a match.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from citefinder.loc import LocClient, marc_book
from citefinder.models import OpenLibraryDoc, OpenLibraryEdition
from citefinder.openlibrary import OpenLibraryClient, normalize_isbn, retailer_only
from citefinder.signals import normalize_title

LOC_SOURCE = "loc"
OPENLIBRARY_SOURCE = "openlibrary"
RETAILER_SOURCE = "openlibrary:retailer"

__all__ = [
    "BookField",
    "BookLookup",
    "BookRecord",
    "book_record",
    "compare_book",
    "publisher_names",
]

_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_PUBLISHER_SPLIT = re.compile(r"\s*(?:,|;|/|:)\s*")


@dataclass
class BookField:
    """One value and the source that supplied it."""

    value: str
    source: str  # LOC_SOURCE | OPENLIBRARY_SOURCE | RETAILER_SOURCE

    @property
    def confirmed(self) -> bool:
        """Whether a catalog, rather than a retailer feed, stands behind it."""
        return self.source != RETAILER_SOURCE


@dataclass
class BookRecord:
    """The merged record for one ISBN. A field the sources lack is `None`."""

    isbn: str
    title: BookField | None = None
    subtitle: BookField | None = None
    contributors: list[BookField] = field(default_factory=list)
    publisher: BookField | None = None
    place: BookField | None = None
    year: BookField | None = None
    edition: BookField | None = None
    isbns: list[BookField] = field(default_factory=list)
    lccn: BookField | None = None
    openlibrary_url: str | None = None
    loc_url: str | None = None
    """Where each source's record can be read; `None` when it had none."""

    @property
    def full_title(self) -> str | None:
        """`title: subtitle`, or the title alone."""
        if self.title is None:
            return None
        if self.subtitle is None:
            return self.title.value
        return f"{self.title.value}: {self.subtitle.value}"

    @property
    def confirmed(self) -> bool:
        """Whether the title itself comes from a catalog source."""
        return self.title is not None and self.title.confirmed

    def as_dict(self) -> dict[str, Any]:
        """A JSON-ready view: each field as `{"value", "source"}`."""
        return asdict(self)


def publisher_names(publisher: str) -> list[str]:
    """The publisher string and each imprint or parent named in it.

    `MCD, Farrar, Straus and Giroux` -> the whole string, `MCD`, `Farrar`,
    `Straus and Giroux`, and, since a comma-split can cut through a single
    house's name, every comma-joined run of neighbours as well (`Farrar,
    Straus and Giroux`). Matching any one of them counts as agreement.
    """
    whole = publisher.strip()
    parts = [p for p in _PUBLISHER_SPLIT.split(whole) if p]
    names = [whole, *parts]
    for i in range(len(parts)):
        for j in range(i + 2, len(parts) + 1):
            names.append(", ".join(parts[i:j]))
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and normalize_title(a or "") == normalize_title(b or "")


def _openlibrary_fields(
    edition: OpenLibraryEdition, client: OpenLibraryClient
) -> dict[str, Any]:
    """Open Library's answer to each `BookRecord` field, or `None`."""
    names: list[str] = []
    for ref in edition.get("authors") or []:
        key = ref.get("key")
        author = client.lookup_author(key) if key else None
        name = (author or {}).get("name")
        if name:
            names.append(name)
    if not names and edition.get("by_statement"):
        names.append(edition["by_statement"].rstrip(". "))
    publishers = edition.get("publishers") or []
    places = edition.get("publish_places") or []
    date = edition.get("publish_date") or ""
    year_match = _YEAR.search(date)
    isbns = [*(edition.get("isbn_13") or []), *(edition.get("isbn_10") or [])]
    lccns = edition.get("lccn") or []
    return {
        "title": edition.get("title") or None,
        "subtitle": edition.get("subtitle") or None,
        "contributors": names,
        "publisher": " / ".join(publishers) if publishers else None,
        "place": places[0] if places else None,
        "year": year_match.group(1) if year_match else None,
        "edition": edition.get("edition_name") or None,
        "isbns": [normalize_isbn(i) for i in isbns],
        "lccn": lccns[0] if lccns else None,
    }


def _merge(record: BookRecord, values: dict[str, Any], source: str) -> None:
    """Fill every empty scalar field of `record` from `values`, and extend
    the list fields with what is not already there."""
    for name in ("title", "subtitle", "publisher", "place", "year", "edition", "lccn"):
        value = values.get(name)
        if value is not None and getattr(record, name) is None:
            setattr(record, name, BookField(str(value), source))
    for name in ("contributors", "isbns"):
        held: list[BookField] = getattr(record, name)
        known = {f.value for f in held}
        for value in values.get(name) or []:
            if value and value not in known:
                held.append(BookField(str(value), source))
                known.add(value)


def book_record(
    isbn: str, openlibrary: OpenLibraryClient, loc: LocClient | None = None
) -> BookRecord | None:
    """The merged record for `isbn`, or `None` when neither source has it.

    Open Library is asked first, for the edition and the LCCN it carries.
    The Library of Congress (when a `loc` client is given) is then asked by
    that LCCN, or by the ISBN itself when there is no edition or no LCCN.
    Catalog values are laid down first so they win the merge; Open Library
    fills whatever the catalog record left empty. Every lookup goes through
    its client's cache, so a second run is offline.
    """
    isbn = normalize_isbn(isbn)
    edition = openlibrary.lookup_isbn(isbn)
    from_openlibrary = (
        _openlibrary_fields(edition, openlibrary) if edition is not None else None
    )
    marc = None
    if loc is not None:
        lccn = from_openlibrary.get("lccn") if from_openlibrary else None
        marc = loc.lookup_lccn(lccn) if lccn else None
        if marc is None:
            marc = loc.lookup_isbn(isbn)
    if edition is None and marc is None:
        return None
    record = BookRecord(isbn=isbn)
    if marc is not None:
        assert loc is not None
        from_loc = marc_book(marc)
        from_loc["isbns"] = [i["isbn"] for i in from_loc["isbns"]]
        _merge(record, from_loc, LOC_SOURCE)
        loc_lccn = from_loc.get("lccn") or (
            from_openlibrary.get("lccn") if from_openlibrary else None
        )
        record.loc_url = loc.lccn_url(loc_lccn) if loc_lccn else None
    if edition is not None and from_openlibrary is not None:
        record.openlibrary_url = openlibrary.isbn_url(isbn)
        source = (
            RETAILER_SOURCE
            if retailer_only(edition.get("source_records"))
            else OPENLIBRARY_SOURCE
        )
        _merge(record, from_openlibrary, source)
    return record


def compare_book(record: BookRecord, fields: dict[str, str]) -> list[dict[str, str]]:
    """What the bib `fields` should change or add to agree with `record`.

    Each suggestion is `{"field", "value", "source", "reason", "current",
    "confirmed"}`: `reason` is `missing` (the bib has no such field) or
    `differs`; `confirmed` is `yes` for a catalog-sourced value and `no`
    for a retailer-fed one, which is offered as a candidate rather than
    a correction. Bib values are `strip_braces`d by the caller.

    Comparisons follow the module rules: a bib publisher matching any name
    in `publisher_names` agrees; a field the record lacks is silent, never
    a disagreement; a bib title that is the catalog title without its
    subtitle gets the subtitle as a suggestion rather than a mismatch.
    """
    out: list[dict[str, str]] = []

    def suggest(name: str, want: BookField, current: str | None) -> None:
        out.append(
            {
                "field": name,
                "value": want.value,
                "source": want.source,
                "reason": "missing" if not current else "differs",
                "current": current or "",
                "confirmed": "yes" if want.confirmed else "no",
            }
        )

    bib_title = fields.get("title")
    if record.title is not None:
        full = record.full_title or record.title.value
        if not _same(bib_title, full):
            if record.subtitle is not None and _same(bib_title, record.title.value):
                suggest("title", BookField(full, record.subtitle.source), bib_title)
            elif bib_title and not _same(bib_title, record.title.value):
                suggest("title", BookField(full, record.title.source), bib_title)

    bib_publisher = fields.get("publisher")
    if record.publisher is not None:
        names = {normalize_title(n) for n in publisher_names(record.publisher.value)}
        if not bib_publisher or normalize_title(bib_publisher) not in names:
            suggest("publisher", record.publisher, bib_publisher)

    # biblatex spells it `location`; bibtex `address`. Either satisfies.
    bib_place = fields.get("location") or fields.get("address")
    if record.place is not None:
        place = record.place.value
        # Catalogs qualify a city (`New York, USA`, `Cambridge, Mass.`); the
        # bib's bare city is the same place.
        city = _PUBLISHER_SPLIT.split(place)[0]
        if not (_same(bib_place, place) or _same(bib_place, city)):
            suggest("location", BookField(city, record.place.source), bib_place)

    bib_year = fields.get("year")
    if record.year is not None:
        year_match = _YEAR.search(bib_year or "")
        if not year_match or year_match.group(1) != record.year.value:
            suggest("year", record.year, bib_year)

    bib_edition = fields.get("edition")
    if record.edition is not None and bib_edition:
        if not _same(bib_edition, record.edition.value):
            suggest("edition", record.edition, bib_edition)
    return out


@dataclass
class BookLookup:
    """The two clients `verify --books` and `citefinder isbn` drive."""

    openlibrary: OpenLibraryClient
    loc: LocClient | None = None

    def record(self, isbn: str) -> BookRecord | None:
        return book_record(isbn, self.openlibrary, self.loc)

    def search(
        self, title: str, author: str | None, rows: int = 3
    ) -> list[OpenLibraryDoc]:
        return self.openlibrary.search(title, author, rows=rows)

    @property
    def network_calls(self) -> int:
        calls = self.openlibrary.network_calls
        return calls + (self.loc.network_calls if self.loc else 0)

    @property
    def retries(self) -> int:
        retries = self.openlibrary.retries
        return retries + (self.loc.retries if self.loc else 0)
