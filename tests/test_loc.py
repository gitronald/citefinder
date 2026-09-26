"""Tests for the Library of Congress client and the MARC reader."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from citefinder.cache import JsonlCache
from citefinder.loc import (
    LocClient,
    marc_book,
    normalize_lccn,
    parse_marcxml,
    strip_isbd,
)
from tests.book_fixtures import LCCN, MARCXML, MARCXML_260, SRU_EMPTY, SRU_RESPONSE

# --- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Enshittification :", "Enshittification"),
        ("why everything suddenly got worse /", "why everything suddenly got worse"),
        ("MCD, Farrar, Straus and Giroux,", "MCD, Farrar, Straus and Giroux"),
        ("2025.", "2025"),
        ("[2025]", "2025"),
        ("[Washington, D.C.] :", "Washington, D.C."),
        ("  ", ""),
    ],
)
def test_strip_isbd(raw: str, clean: str) -> None:
    assert strip_isbd(raw) == clean


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("  2025007165", "2025007165"), ("sn 85-042021", "sn85042021")],
)
def test_normalize_lccn(raw: str, clean: str) -> None:
    assert normalize_lccn(raw) == clean


# --- parse_marcxml ------------------------------------------------------------


def test_parse_marcxml_flattens_record() -> None:
    record = parse_marcxml(MARCXML)
    assert record is not None
    assert record["leader"].startswith("01234cam")
    assert record["controlfields"]["001"] == "23456789"
    title = next(f for f in record["fields"] if f["tag"] == "245")
    assert title["ind1"] == "1"
    assert title["subfields"][0] == ["a", "Enshittification :"]


def test_parse_marcxml_reads_record_inside_collection() -> None:
    record = parse_marcxml(MARCXML_260)
    assert record is not None
    assert [f["tag"] for f in record["fields"]] == ["245", "260", "710"]


def test_parse_marcxml_unwraps_sru_envelope() -> None:
    record = parse_marcxml(SRU_RESPONSE)
    assert record is not None
    assert record["controlfields"]["001"] == "23456789"
    assert any(f["tag"] == "245" for f in record["fields"])


def test_parse_marcxml_without_record_is_none() -> None:
    assert parse_marcxml('<collection xmlns="http://www.loc.gov/MARC21/slim"/>') is None
    assert parse_marcxml(SRU_EMPTY) is None


def test_parse_marcxml_rejects_non_xml() -> None:
    with pytest.raises(ValueError, match="not MARCXML"):
        parse_marcxml("<html><body>Not found")


# --- marc_book ----------------------------------------------------------------


def test_marc_book_reads_the_citation_fields() -> None:
    record = parse_marcxml(MARCXML)
    assert record is not None
    book = marc_book(record)
    assert book["title"] == "Enshittification"
    assert book["subtitle"] == (
        "why everything suddenly got worse and what to do about it"
    )
    assert book["contributors"] == ["Doctorow, Cory"]
    assert book["publisher"] == "MCD, Farrar, Straus and Giroux"
    assert book["place"] == "New York"
    assert book["date"] == "2025"
    assert book["year"] == 2025
    assert book["edition"] == "First edition"
    assert book["lccn"] == LCCN
    assert book["cataloging_agency"] == "DLC"


def test_marc_book_splits_isbn_qualifiers_in_both_forms() -> None:
    record = parse_marcxml(MARCXML)
    assert record is not None
    assert marc_book(record)["isbns"] == [
        {"isbn": "9780374619329", "qualifier": "hardcover"},
        {"isbn": "0374619328", "qualifier": "hardcover"},
    ]


def test_marc_book_falls_back_to_260_and_unbrackets() -> None:
    record = parse_marcxml(MARCXML_260)
    assert record is not None
    book = marc_book(record)
    assert book["title"] == "A report on things"
    assert book["subtitle"] is None
    assert book["place"] == "Washington, D.C."
    assert book["publisher"] == "The Office"
    assert book["year"] == 1999
    assert book["contributors"] == ["Some Agency"]
    assert book["isbns"] == []
    assert book["lccn"] is None
    assert book["edition"] is None


def test_marc_book_on_empty_record_is_all_none() -> None:
    book = marc_book({"leader": "", "controlfields": {}, "fields": []})
    assert book["title"] is None
    assert book["place"] is None
    assert book["contributors"] == []


# --- LocClient ----------------------------------------------------------------


@pytest.fixture
def setup(tmp_path: Path) -> tuple[LocClient, MagicMock]:
    cache = JsonlCache(tmp_path / "loc.jsonl")
    client = LocClient(cache=cache, mailto="you@example.com")
    session = MagicMock()
    client.session = session  # type: ignore[assignment]
    return client, session


def test_lookup_lccn_parses_and_caches_marcxml(
    setup: tuple[LocClient, MagicMock], mock_response
) -> None:
    client, session = setup
    response = mock_response(200)
    response.text = SRU_RESPONSE
    session.get.return_value = response
    record = client.lookup_lccn(f"  {LCCN}")
    assert record is not None
    assert record["controlfields"]["001"] == "23456789"
    called = session.get.call_args[0][0]
    assert called.startswith("http://lx2.loc.gov:210/LCDB?")
    assert f"query=bath.lccn%3D{LCCN}" in called
    assert "recordSchema=marcxml" in called
    assert "maximumRecords=1" in called
    # The parsed dict, not the XML, is what the cache holds.
    again = client.lookup_lccn(LCCN)
    assert again == record
    assert session.get.call_count == 1


def test_lookup_lccn_404_is_none(
    setup: tuple[LocClient, MagicMock], mock_response
) -> None:
    client, session = setup
    session.get.return_value = mock_response(404)
    assert client.lookup_lccn("nope") is None


def test_lookup_lccn_200_without_record_is_none(
    setup: tuple[LocClient, MagicMock], mock_response
) -> None:
    client, session = setup
    response = mock_response(200)
    response.text = SRU_EMPTY
    session.get.return_value = response
    assert client.lookup_lccn(LCCN) is None
    assert client.lookup_lccn(LCCN) is None
    assert session.get.call_count == 1


def test_lookup_isbn_queries_by_isbn(
    setup: tuple[LocClient, MagicMock], mock_response
) -> None:
    client, session = setup
    response = mock_response(200)
    response.text = MARCXML  # a bare record parses too
    session.get.return_value = response
    assert client.lookup_isbn("978-0-374-61932-9") is not None
    assert "query=bath.isbn%3D9780374619329" in session.get.call_args[0][0]


def test_mailto_lands_in_user_agent_not_url() -> None:
    client = LocClient(mailto="you@example.com")
    agent = str(client.session.headers["User-Agent"])
    assert agent.endswith("; mailto:you@example.com)")
    assert client.mailto is None
    assert "mailto" not in str(LocClient().session.headers["User-Agent"])
    assert client.lccn_url(" 2025007165") == "https://lccn.loc.gov/2025007165"
