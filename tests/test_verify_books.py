"""Tests for the book path of `verify_entry`: an ISBN checked against its
catalog record, and Open Library candidates for a book without one."""

from typing import Any

import pytest

from citefinder.bib import parse_entries
from citefinder.books import BookLookup
from citefinder.loc import parse_marcxml
from citefinder.openalex import OpenAlexClient
from citefinder.signals import Status
from citefinder.verify import Source, verify_entry
from tests.book_fixtures import BOOK_BIB, EDITION, ISBN, LCCN, MARCXML
from tests.test_books import FakeLoc, FakeOpenLibrary


class _NoSource(OpenAlexClient):
    """An OpenAlex client that finds nothing by search and must not be
    asked for a DOI on the ISBN path."""

    def __init__(self) -> None:  # no session, no cache
        self.cache = None
        self.retries = 0
        self.network_calls = 0

    def lookup_doi(  # pyrefly: ignore[missing-override-decorator]
        self, doi: str
    ) -> Any:
        raise AssertionError("source consulted")

    def search_title(  # pyrefly: ignore[missing-override-decorator]
        self, title: str, rows: int = 3
    ) -> list[Any]:
        return []


@pytest.fixture
def source() -> Source:
    return Source(name="openalex", client=_NoSource())


@pytest.fixture
def books() -> BookLookup:
    openlibrary = FakeOpenLibrary({ISBN: EDITION})
    return BookLookup(openlibrary, FakeLoc({LCCN: parse_marcxml(MARCXML)}))


def _entry(text: str):
    return parse_entries(text)[0]


def test_isbn_path_matches_and_suggests(source: Source, books: BookLookup) -> None:
    r = verify_entry(_entry(BOOK_BIB), source, books)
    assert r.method == "isbn"
    assert r.status == Status.MATCHED, r.note
    assert r.matched_doi is None
    assert r.matched_title is not None and r.matched_title.startswith(
        "Enshittification: "
    )
    assert r.signals["title"]["verdict"] in {"pass", "unknown"}
    assert r.signals["year"]["verdict"] == "pass"
    assert r.signals["author"]["verdict"] == "pass"
    assert "Library of Congress record" in r.note
    by_field = {s["field"]: s for s in r.suggestions}
    assert set(by_field) == {"title", "location"}
    assert by_field["location"]["value"] == "New York"
    assert by_field["location"]["source"] == "loc"
    assert by_field["title"]["value"] == r.matched_title
    # `publisher = {MCD}` is the imprint of `MCD, Farrar, Straus and Giroux`.
    assert "publisher" not in by_field


def test_without_books_the_isbn_is_ignored(source: Source) -> None:
    r = verify_entry(_entry(BOOK_BIB), source)
    assert r.method == "search"
    assert r.status == Status.UNMATCHED
    assert r.suggestions == []


def test_doi_beats_isbn(books: BookLookup) -> None:
    class Resolving(_NoSource):
        def lookup_doi(  # pyrefly: ignore[missing-override-decorator]
            self, doi: str
        ) -> Any:
            return None  # a cached 404

    src = Source(name="openalex", client=Resolving())
    text = BOOK_BIB.replace("  isbn =", "  doi = {10.1/x},\n  isbn =")
    r = verify_entry(_entry(text), src, books)
    assert r.method == "doi"
    assert r.status == Status.DOI_NOT_FOUND


def test_unknown_isbn_is_unmatched(source: Source, books: BookLookup) -> None:
    text = BOOK_BIB.replace(ISBN, "0000000000")
    r = verify_entry(_entry(text), source, books)
    assert r.method == "isbn"
    assert r.status == Status.UNMATCHED
    assert r.note == "ISBN 0000000000 not in Open Library"


def test_isbn_lookup_error_is_reported(source: Source) -> None:
    class Exploding(FakeOpenLibrary):
        def lookup_isbn(  # pyrefly: ignore[missing-override-decorator]
            self, isbn: str
        ) -> Any:
            raise RuntimeError("boom")

    r = verify_entry(_entry(BOOK_BIB), source, BookLookup(Exploding({})))
    assert r.status == Status.ERROR
    assert r.note == "ISBN lookup failed: boom"


def test_retailer_only_record_tops_out_at_probable(source: Source) -> None:
    books = BookLookup(FakeOpenLibrary({ISBN: EDITION}))  # no LoC
    r = verify_entry(_entry(BOOK_BIB), source, books)
    assert r.status == Status.PROBABLE
    assert r.note.startswith("Open Library record is retailer-fed")
    assert all(s["confirmed"] == "no" for s in r.suggestions)


def test_malformed_editor_is_this_entrys_error(
    source: Source, books: BookLookup
) -> None:
    text = f"""@incollection{{ch1,
  author = {{Someone, Else}},
  editor = {{Doctorow, Cory,}},
  title = {{A chapter}},
  booktitle = {{Enshittification}},
  year = {{2025}},
  isbn = {{{ISBN}}},
}}
"""
    r = verify_entry(_entry(text), source, books)  # must not raise
    assert r.method == "isbn"
    assert r.status == Status.ERROR
    assert r.note.startswith("could not parse bib fields")


def test_retailer_year_behind_a_catalog_title_caps_at_probable(
    source: Source,
) -> None:
    # Both 264s go: the copyright one (indicator 4) would otherwise stand in.
    undated = MARCXML.replace('<datafield tag="264"', '<datafield tag="999"')
    books = BookLookup(
        FakeOpenLibrary({ISBN: EDITION}), FakeLoc({LCCN: parse_marcxml(undated)})
    )
    r = verify_entry(_entry(BOOK_BIB), source, books)
    assert r.signals["year"]["verdict"] == "pass"  # the retailer year agreed
    assert r.status == Status.PROBABLE
    assert r.note.startswith("Open Library record is retailer-fed")


def test_wrong_book_is_a_mismatch(source: Source, books: BookLookup) -> None:
    text = (
        BOOK_BIB.replace("Doctorow, Cory", "Jones, Alice")
        .replace("{Enshittification}", "{A Different Book About Cats}")
        .replace("2025", "1999")
    )
    r = verify_entry(_entry(text), source, books)
    assert r.status == Status.MISMATCH
    assert {s["field"] for s in r.suggestions} >= {"title", "year", "location"}


def test_chapter_entry_checks_the_container_book(
    source: Source, books: BookLookup
) -> None:
    text = f"""@incollection{{ch1,
  author = {{Someone, Else}},
  editor = {{Doctorow, Cory}},
  title = {{A chapter with its own title}},
  booktitle = {{Enshittification}},
  publisher = {{Farrar, Straus and Giroux}},
  year = {{2025}},
  isbn = {{{ISBN}}},
}}
"""
    r = verify_entry(_entry(text), source, books)
    assert r.method == "isbn"
    assert r.status == Status.MATCHED, r.note
    assert r.signals["author"]["verdict"] == "pass"  # editor vs the catalog's 100
    by_field = {s["field"]: s for s in r.suggestions}
    assert "publisher" not in by_field
    assert by_field["title"]["current"] == "Enshittification"


def test_book_without_isbn_gets_openlibrary_candidates(
    source: Source, books: BookLookup
) -> None:
    text = BOOK_BIB.replace(f"  isbn = {{{ISBN}}},\n", "")
    r = verify_entry(_entry(text), source, books)
    assert r.method == "search"
    assert r.status == Status.UNMATCHED
    assert len(r.candidates) == 1
    hit = r.candidates[0]
    assert hit["isbn"] == ISBN
    assert hit["source"] == "openlibrary"
    assert hit["doi"] == ""
    assert hit["title"].startswith("Enshittification: Why")
    assert r.suggestions == []


def test_openlibrary_search_failure_is_noted(source: Source) -> None:
    class Exploding(FakeOpenLibrary):
        def search(  # pyrefly: ignore[missing-override-decorator]
            self, title: str, author: str | None = None, rows: int = 3
        ) -> list[Any]:
            raise RuntimeError("boom")

    text = BOOK_BIB.replace(f"  isbn = {{{ISBN}}},\n", "")
    r = verify_entry(_entry(text), source, BookLookup(Exploding({})))
    assert r.status == Status.UNMATCHED
    assert r.note.endswith("; Open Library search failed: boom")
    assert r.candidates == []


def test_non_book_types_ignore_books(source: Source, books: BookLookup) -> None:
    text = BOOK_BIB.replace("@book", "@article")
    r = verify_entry(_entry(text), source, books)
    assert r.method == "search"
    assert r.candidates == []
