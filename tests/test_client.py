"""Tests for the Crossref client (HTTP mocked)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from citefinder.cache import JsonlCache
from citefinder.client import CrossrefClient


@pytest.fixture
def setup(tmp_path: Path) -> tuple[CrossrefClient, MagicMock]:
    cache = JsonlCache(tmp_path / "cache.jsonl")
    client = CrossrefClient(cache=cache)
    session = MagicMock()
    client.session = session  # type: ignore[assignment]
    return client, session


def test_lookup_doi_returns_message(
    setup: tuple[CrossrefClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(
        200, {"message": {"title": ["A Paper"], "DOI": "10.1/test"}}
    )
    result = client.lookup_doi("10.1/test")
    assert result == {"title": ["A Paper"], "DOI": "10.1/test"}


def test_lookup_doi_404_returns_none(
    setup: tuple[CrossrefClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(404)
    assert client.lookup_doi("10.1/missing") is None


def test_lookup_doi_uses_cache_on_repeat(
    setup: tuple[CrossrefClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"message": {"title": ["A Paper"]}})
    client.lookup_doi("10.1/test")
    client.lookup_doi("10.1/test")
    assert session.get.call_count == 1


def test_404_is_cached(
    setup: tuple[CrossrefClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(404)
    assert client.lookup_doi("10.1/missing") is None
    assert client.lookup_doi("10.1/missing") is None
    assert session.get.call_count == 1


def test_search_bibliographic_returns_items(
    setup: tuple[CrossrefClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(
        200,
        {"message": {"items": [{"DOI": "10.1/a"}, {"DOI": "10.1/b"}]}},
    )
    items = client.search_bibliographic("hate speech meta-analysis", rows=2)
    assert [i["DOI"] for i in items] == ["10.1/a", "10.1/b"]
    called_url = session.get.call_args[0][0]
    assert "query.bibliographic=hate+speech+meta-analysis" in called_url
    assert "rows=2" in called_url


def test_lookup_book_chapter_pads_numeric_chapter(
    setup: tuple[CrossrefClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"message": {"title": ["Chapter 5"]}})
    client.lookup_book_chapter("10.1017/9781108890960", 5)
    called_url = session.get.call_args[0][0]
    assert called_url.endswith("10.1017/9781108890960.005")


def test_lookup_book_chapter_string_chapter_passthrough(
    setup: tuple[CrossrefClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(
        200, {"message": {"title": ["Chapter Foo"]}}
    )
    client.lookup_book_chapter("10.1234/book", "ch1a")
    called_url = session.get.call_args[0][0]
    assert called_url.endswith("10.1234/book.ch1a")


def test_polite_pool_mailto_added_to_url(
    tmp_path: Path,
    mock_response,
) -> None:
    """Crossref polite pool: mailto appended as a query param on every request."""
    cache = JsonlCache(tmp_path / "cache.jsonl")
    client = CrossrefClient(cache=cache, mailto="x@example.com")
    client.session = MagicMock()  # type: ignore[assignment]
    client.session.get.return_value = mock_response(200, {"message": {"title": ["X"]}})

    client.lookup_doi("10.1/test")
    doi_url = client.session.get.call_args[0][0]
    assert "mailto=x%40example.com" in doi_url
    assert doi_url.startswith("https://api.crossref.org/works/10.1/test?mailto=")

    client.search_bibliographic("hate speech", rows=2)
    search_url = client.session.get.call_args[0][0]
    assert "mailto=x%40example.com" in search_url
    # Existing query string already had ?, so mailto must be joined with &
    assert search_url.count("?") == 1


def test_cache_key_strips_mailto(tmp_path: Path, mock_response) -> None:
    """Cache should be unaffected by the mailto used on the request."""
    cache = JsonlCache(tmp_path / "cache.jsonl")

    client_a = CrossrefClient(cache=cache, mailto="a@example.com")
    client_a.session = MagicMock()  # type: ignore[assignment]
    client_a.session.get.return_value = mock_response(
        200, {"message": {"title": ["A"]}}
    )
    client_a.lookup_doi("10.1/test")

    client_b = CrossrefClient(cache=cache, mailto="b@example.com")
    client_b.session = MagicMock()  # type: ignore[assignment]
    client_b.lookup_doi("10.1/test")
    assert client_b.session.get.call_count == 0


def test_no_mailto_no_query_param(tmp_path: Path, mock_response) -> None:
    """No mailto → no `?mailto=` appended; URL unchanged."""
    cache = JsonlCache(tmp_path / "cache.jsonl")
    client = CrossrefClient(cache=cache)
    client.session = MagicMock()  # type: ignore[assignment]
    client.session.get.return_value = mock_response(200, {"message": {"title": ["X"]}})

    client.lookup_doi("10.1/test")
    called_url = client.session.get.call_args[0][0]
    assert "mailto" not in called_url
    assert called_url == "https://api.crossref.org/works/10.1/test"
