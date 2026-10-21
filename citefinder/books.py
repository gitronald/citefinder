"""Book metadata by ISBN: Open Library, then the Library of Congress.

The two sources are merged field by field, the Library of Congress winning
where it has a value, and every field records its source:

- `loc`: the catalog record.
- `openlibrary`: an Open Library edition fed by a library catalog or scan.
- `openlibrary:retailer`: an edition fed only by retailers; a lead to check,
  not a value to apply.

A missing value never counts as agreement, and an imprint and its parent
publisher both match (see `publisher_names`).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from citefinder.loc import YEAR_RE, LocClient, marc_book
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
        """Whether the title, year, and first contributor (where present) all
        come from a catalog source."""
        if self.title is None or not self.title.confirmed:
            return False
        signal_fields = [self.year, self.contributors[0] if self.contributors else None]
        return all(f is None or f.confirmed for f in signal_fields)

    def as_dict(self) -> dict[str, Any]:
        """A JSON-ready view: each field as `{"value", "source"}`."""
        return asdict(self)


def publisher_names(publisher: str) -> list[str]:
    """The publisher string and each imprint or parent named in it.

    `MCD, Farrar, Straus and Giroux` yields the whole string, each part, and
    every comma-joined run of neighbours (`Farrar, Straus and Giroux`).
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
    year_match = YEAR_RE.search(date)
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
    """Fill empty scalar fields of `record` from `values`; append new list items."""
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

    Open Library is asked first. The Library of Congress (when `loc` is given)
    is then asked by the edition's LCCN, or by ISBN when there is none.
    Catalog values are merged first, so they win.
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
    "confirmed"}`: `reason` is `missing` or `differs`; `confirmed` is `no`
    for a retailer-fed value, offered as a candidate. Bib values are
    `strip_braces`d by the caller. A field the record lacks is never a
    disagreement.
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
            elif not _same(bib_title, record.title.value):
                # A missing bib title is suggested like any other difference.
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
        # Catalogs qualify a city (`New York, USA`); a bare city matches.
        city = _PUBLISHER_SPLIT.split(place)[0]
        if not (_same(bib_place, place) or _same(bib_place, city)):
            suggest("location", BookField(city, record.place.source), bib_place)

    bib_year = fields.get("year")
    if record.year is not None:
        year_match = YEAR_RE.search(bib_year or "")
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
