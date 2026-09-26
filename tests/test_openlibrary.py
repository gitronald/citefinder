"""Tests for the Open Library client and helpers (HTTP mocked)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from citefinder.cache import JsonlCache
from citefinder.openlibrary import (
    OPENLIBRARY_MIN_INTERVAL,
    OpenLibraryClient,
    normalize_isbn,
    retailer_only,
)
from tests.book_fixtures import AUTHOR, EDITION, ISBN, SEARCH_PAGE


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("978-0-374-61932-9", "9780374619329"),
        (" 0 374 61932 x ", "037461932X"),
        ("9780374619329, 0374619328", "9780374619329"),
        ("9780374619329; 0374619328", "9780374619329"),
    ],
)
def test_normalize_isbn(raw: str, clean: str) -> None:
    assert normalize_isbn(raw) == clean


@pytest.mark.parametrize(
    ("records", "retailer"),
    [
        (["amazon:0374619328"], True),
        (["bwb:9780374619329", "promise:bwb_daily_pallets_2025"], True),
        (["amazon:0374619328", "marc:marc_loc_2016/x:1"], False),
        (["ia:enshittification0000doct"], False),
        ([], False),
        (None, False),
    ],
)
def test_retailer_only(records: list[str] | None, retailer: bool) -> None:
    assert retailer_only(records) is retailer


@pytest.fixture
def setup(tmp_path: Path) -> tuple[OpenLibraryClient, MagicMock]:
    cache = JsonlCache(tmp_path / "openlibrary.jsonl")
    client = OpenLibraryClient(cache=cache, mailto="you@example.com")
    session = MagicMock()
    client.session = session  # type: ignore[assignment]
    return client, session


def test_defaults_and_user_agent() -> None:
    client = OpenLibraryClient(mailto="you@example.com")
    assert client.min_interval == OPENLIBRARY_MIN_INTERVAL
    assert client.mailto is None  # never a query parameter
    assert client.contact == "you@example.com"
    agent = str(client.session.headers["User-Agent"])
    assert agent.startswith("citefinder/")
    assert agent.endswith("; mailto:you@example.com)")
    custom = OpenLibraryClient(mailto="you@example.com", user_agent="mine/1")
    assert custom.session.headers["User-Agent"] == "mine/1"


def test_lookup_isbn_normalizes_and_caches(
    setup: tuple[OpenLibraryClient, MagicMock], mock_response
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, EDITION)
    edition = client.lookup_isbn("978-0-374-61932-9")
    assert edition == EDITION
    assert session.get.call_args[0][0] == f"https://openlibrary.org/isbn/{ISBN}.json"
    assert client.lookup_isbn(ISBN) == EDITION
    assert session.get.call_count == 1
    assert (
        client.isbn_url("978-0-374-61932-9") == f"https://openlibrary.org/isbn/{ISBN}"
    )


def test_lookup_isbn_404_is_cached_none(
    setup: tuple[OpenLibraryClient, MagicMock], mock_response
) -> None:
    client, session = setup
    session.get.return_value = mock_response(404)
    assert client.lookup_isbn("0000000000") is None
    assert client.lookup_isbn("0000000000") is None
    assert session.get.call_count == 1


def test_lookup_author(
    setup: tuple[OpenLibraryClient, MagicMock], mock_response
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, AUTHOR)
    assert client.lookup_author("/authors/OL1394244A") == AUTHOR
    called = session.get.call_args[0][0]
    assert called == "https://openlibrary.org/authors/OL1394244A.json"


def test_search_sends_title_author_and_fields(
    setup: tuple[OpenLibraryClient, MagicMock], mock_response
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, SEARCH_PAGE)
    docs = client.search("Enshittification", "Doctorow", rows=2)
    assert docs == SEARCH_PAGE["docs"]
    called = session.get.call_args[0][0]
    assert called.startswith("https://openlibrary.org/search.json?")
    assert "title=Enshittification" in called
    assert "author=Doctorow" in called
    assert "fields=key%2Ctitle" in called
    assert "limit=2" in called
    assert "mailto" not in called


def test_search_without_author_or_results(
    setup: tuple[OpenLibraryClient, MagicMock], mock_response
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"numFound": 0, "docs": []})
    assert client.search("Nothing") == []
    assert "author=" not in session.get.call_args[0][0]
    session.get.return_value = mock_response(404)
    assert client.search("Gone") == []
