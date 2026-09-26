"""Tests for the ISBN chain: Open Library, then the Library of Congress,
merged field by field with provenance."""

from typing import Any

import pytest

from citefinder.books import (
    BookField,
    BookLookup,
    BookRecord,
    book_record,
    compare_book,
    publisher_names,
)
from citefinder.loc import LocClient, parse_marcxml
from citefinder.openlibrary import OpenLibraryClient
from tests.book_fixtures import (
    AUTHOR,
    CATALOG_EDITION,
    EDITION,
    ISBN,
    LCCN,
    MARCXML,
    SEARCH_PAGE,
)


class FakeOpenLibrary(OpenLibraryClient):
    def __init__(self, editions: dict[str, Any]) -> None:
        super().__init__()
        self.editions = editions
        self.calls: list[str] = []

    def lookup_isbn(  # pyrefly: ignore[missing-override-decorator]
        self, isbn: str
    ) -> Any:
        self.calls.append(isbn)
        return self.editions.get(isbn)

    def lookup_author(  # pyrefly: ignore[missing-override-decorator]
        self, key: str
    ) -> Any:
        return AUTHOR if key == AUTHOR["key"] else None

    def search(  # pyrefly: ignore[missing-override-decorator]
        self, title: str, author: str | None = None, rows: int = 3
    ) -> list[Any]:
        return list(SEARCH_PAGE["docs"])


class FakeLoc(LocClient):
    def __init__(self, records: dict[str, Any]) -> None:
        super().__init__()
        self.records = records
        self.calls: list[str] = []

    def lookup_lccn(  # pyrefly: ignore[missing-override-decorator]
        self, lccn: str
    ) -> Any:
        self.calls.append(f"lccn:{lccn}")
        return self.records.get(lccn)

    def lookup_isbn(  # pyrefly: ignore[missing-override-decorator]
        self, isbn: str
    ) -> Any:
        self.calls.append(f"isbn:{isbn}")
        return self.records.get(isbn)


@pytest.fixture
def openlibrary() -> FakeOpenLibrary:
    return FakeOpenLibrary({ISBN: EDITION, "9780553418811": CATALOG_EDITION})


@pytest.fixture
def loc() -> FakeLoc:
    return FakeLoc({LCCN: parse_marcxml(MARCXML)})


def test_loc_wins_field_by_field(openlibrary: FakeOpenLibrary, loc: FakeLoc) -> None:
    record = book_record("978-0-374-61932-9", openlibrary, loc)
    assert record is not None
    assert record.isbn == ISBN
    assert record.title == BookField("Enshittification", "loc")
    assert record.subtitle == BookField(
        "why everything suddenly got worse and what to do about it", "loc"
    )
    assert record.publisher == BookField("MCD, Farrar, Straus and Giroux", "loc")
    assert record.place == BookField("New York", "loc")
    assert record.year == BookField("2025", "loc")
    assert record.edition == BookField("First edition", "loc")
    assert record.lccn == BookField(LCCN, "loc")
    assert record.contributors[0] == BookField("Doctorow, Cory", "loc")
    # Open Library fills in only what the catalog record lacks, marked as
    # retailer-fed since its only source record is an Amazon feed.
    assert BookField("Cory Doctorow", "openlibrary:retailer") in record.contributors
    assert [i.value for i in record.isbns] == [ISBN, "0374619328"]
    assert {i.source for i in record.isbns} == {"loc"}
    assert record.openlibrary_url == f"https://openlibrary.org/isbn/{ISBN}"
    assert record.loc_url == f"https://lccn.loc.gov/{LCCN}"
    assert record.full_title == (
        "Enshittification: why everything suddenly got worse and what to do about it"
    )
    assert record.confirmed
    assert loc.calls == [f"lccn:{LCCN}"]


def test_retailer_only_edition_without_loc_is_unconfirmed(
    openlibrary: FakeOpenLibrary,
) -> None:
    record = book_record(ISBN, openlibrary)  # no LoC client
    assert record is not None
    assert record.title == BookField("Enshittification", "openlibrary:retailer")
    assert record.place == BookField("New York, USA", "openlibrary:retailer")
    assert record.publisher is not None
    assert record.publisher.value == "MCD / Farrar, Straus and Giroux"
    assert record.year is not None and record.year.value == "2025"
    assert record.loc_url is None
    assert not record.confirmed
    assert not (record.title is not None and record.title.confirmed)


def test_missing_lccn_falls_back_to_a_loc_isbn_query(
    openlibrary: FakeOpenLibrary, loc: FakeLoc
) -> None:
    record = book_record("9780553418811", openlibrary, loc)
    assert record is not None
    assert loc.calls == ["isbn:9780553418811"]
    assert record.title == BookField("Weapons of Math Destruction", "openlibrary")
    assert record.subtitle is None
    assert record.contributors == [BookField("Cathy O'Neil", "openlibrary")]
    assert record.lccn is None
    assert record.loc_url is None
    assert record.confirmed


def test_lccn_without_loc_record_keeps_openlibrary(
    openlibrary: FakeOpenLibrary,
) -> None:
    empty = FakeLoc({})
    record = book_record(ISBN, openlibrary, empty)
    assert record is not None
    assert empty.calls == [f"lccn:{LCCN}", f"isbn:{ISBN}"]
    assert record.loc_url is None
    assert record.title is not None and record.title.source == "openlibrary:retailer"


def test_loc_alone_makes_a_record(loc: FakeLoc) -> None:
    by_isbn = FakeLoc({ISBN: parse_marcxml(MARCXML)})
    record = book_record(ISBN, FakeOpenLibrary({}), by_isbn)
    assert record is not None
    assert by_isbn.calls == [f"isbn:{ISBN}"]
    assert record.openlibrary_url is None
    assert record.loc_url == f"https://lccn.loc.gov/{LCCN}"
    assert record.title == BookField("Enshittification", "loc")
    assert record.confirmed


def test_unknown_isbn_is_none(openlibrary: FakeOpenLibrary, loc: FakeLoc) -> None:
    assert book_record("0000000000", openlibrary, loc) is None
    assert loc.calls == ["isbn:0000000000"]
    assert book_record("0000000000", openlibrary) is None


def test_as_dict_is_json_shaped(openlibrary: FakeOpenLibrary, loc: FakeLoc) -> None:
    record = book_record(ISBN, openlibrary, loc)
    assert record is not None
    data = record.as_dict()
    assert data["title"] == {"value": "Enshittification", "source": "loc"}
    assert data["isbn"] == ISBN
    assert data["loc_url"] == f"https://lccn.loc.gov/{LCCN}"


def test_book_lookup_wraps_the_pair(openlibrary: FakeOpenLibrary, loc: FakeLoc) -> None:
    lookup = BookLookup(openlibrary, loc)
    assert lookup.record(ISBN) is not None
    assert lookup.network_calls == 0 and lookup.retries == 0
    alone = BookLookup(openlibrary)
    assert alone.record("0000000000") is None
    assert alone.network_calls == 0 and alone.retries == 0


# --- publisher_names / compare_book -----------------------------------------


def test_publisher_names_offers_imprint_parent_and_joins() -> None:
    names = publisher_names("MCD, Farrar, Straus and Giroux")
    assert names[0] == "MCD, Farrar, Straus and Giroux"
    assert "MCD" in names
    assert "Farrar, Straus and Giroux" in names
    assert "Straus and Giroux" in names
    assert publisher_names("MCD / Farrar, Straus and Giroux")[1:3] == ["MCD", "Farrar"]


@pytest.fixture
def merged(openlibrary: FakeOpenLibrary, loc: FakeLoc) -> BookRecord:
    record = book_record(ISBN, openlibrary, loc)
    assert record is not None
    return record


def test_compare_book_suggests_missing_location_and_subtitle(
    merged: BookRecord,
) -> None:
    bib = {"title": "Enshittification", "publisher": "MCD", "year": "2025"}
    suggestions = compare_book(merged, bib)
    by_field = {s["field"]: s for s in suggestions}
    assert set(by_field) == {"title", "location"}
    assert by_field["title"]["value"] == merged.full_title
    assert by_field["title"]["reason"] == "differs"
    assert by_field["title"]["source"] == "loc"
    assert by_field["location"] == {
        "field": "location",
        "value": "New York",
        "source": "loc",
        "reason": "missing",
        "current": "",
        "confirmed": "yes",
    }


def test_compare_book_imprint_alone_is_not_flagged(merged: BookRecord) -> None:
    full = merged.full_title or ""
    for publisher in (
        "MCD",
        "Farrar, Straus and Giroux",
        "mcd, farrar, straus and giroux",
    ):
        bib = {
            "title": full,
            "publisher": publisher,
            "year": "2025",
            "address": "New York",
        }
        assert compare_book(merged, bib) == []


def test_compare_book_flags_differing_fields(merged: BookRecord) -> None:
    bib = {
        "title": "Something Else Entirely",
        "publisher": "Penguin",
        "year": "2024",
        "location": "London",
        "edition": "2",
    }
    fields = {
        s["field"]: (s["reason"], s["current"]) for s in compare_book(merged, bib)
    }
    assert fields == {
        "title": ("differs", "Something Else Entirely"),
        "publisher": ("differs", "Penguin"),
        "location": ("differs", "London"),
        "year": ("differs", "2024"),
        "edition": ("differs", "2"),
    }


def test_compare_book_place_qualifier_and_retailer_flag(
    openlibrary: FakeOpenLibrary,
) -> None:
    record = book_record(ISBN, openlibrary)  # retailer-fed, `New York, USA`
    assert record is not None
    bib = {"title": "Enshittification", "publisher": "MCD", "address": "New York"}
    suggestions = compare_book(record, bib)
    by_field = {s["field"]: s for s in suggestions}
    assert "location" not in by_field  # `New York` is `New York, USA`
    assert by_field["year"]["confirmed"] == "no"
    assert by_field["year"]["reason"] == "missing"
    assert by_field["title"]["source"] == "openlibrary:retailer"


def test_compare_book_is_silent_where_the_record_is() -> None:
    record = BookRecord(isbn=ISBN, title=BookField("T", "loc"))
    assert compare_book(record, {"title": "T"}) == []
    assert compare_book(record, {"title": "T", "publisher": "P", "year": "1"}) == []


def test_compare_book_suggests_a_missing_title() -> None:
    record = BookRecord(isbn=ISBN, title=BookField("Some Title", "loc"))
    for fields in ({}, {"title": ""}):
        (suggestion,) = compare_book(record, fields)
        assert suggestion["field"] == "title"
        assert suggestion["reason"] == "missing"
        assert suggestion["value"] == "Some Title"


def test_confirmed_needs_every_signal_field_from_a_catalog() -> None:
    title = BookField("T", "loc")
    assert BookRecord(isbn=ISBN, title=title).confirmed
    assert BookRecord(isbn=ISBN, title=title, year=BookField("2019", "loc")).confirmed
    retailer_year = BookField("2019", "openlibrary:retailer")
    assert not BookRecord(isbn=ISBN, title=title, year=retailer_year).confirmed
    retailer_name = BookField("A. Writer", "openlibrary:retailer")
    assert not BookRecord(
        isbn=ISBN, title=title, contributors=[retailer_name]
    ).confirmed
    assert not BookRecord(
        isbn=ISBN, title=BookField("T", "openlibrary:retailer")
    ).confirmed


def test_loc_record_without_a_date_leaves_the_year_unconfirmed(
    openlibrary: FakeOpenLibrary,
) -> None:
    # Both 264s go: the copyright one (indicator 4) would otherwise stand in.
    undated = MARCXML.replace('<datafield tag="264"', '<datafield tag="999"')
    record = book_record(ISBN, openlibrary, FakeLoc({LCCN: parse_marcxml(undated)}))
    assert record is not None
    assert record.title is not None and record.title.source == "loc"
    assert record.year == BookField("2025", "openlibrary:retailer")
    assert not record.confirmed
