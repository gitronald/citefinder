"""Tests for the OpenAlex client and helpers (HTTP mocked)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from citefinder.cache import JsonlCache
from citefinder.client import is_arxiv_doi
from citefinder.openalex import OpenAlexClient, _strip_mailto, reconstruct_abstract


def _mock_response(status: int, payload: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


@pytest.fixture
def setup(tmp_path: Path) -> tuple[OpenAlexClient, MagicMock]:
    cache = JsonlCache(tmp_path / "cache.jsonl")
    client = OpenAlexClient(cache=cache, mailto="test@example.com")
    session = MagicMock()
    client.session = session  # type: ignore[assignment]
    return client, session


def test_lookup_doi_returns_work(setup: tuple[OpenAlexClient, MagicMock]) -> None:
    client, session = setup
    session.get.return_value = _mock_response(
        200,
        {"id": "W123", "display_name": "A Paper", "doi": "https://doi.org/10.1/test"},
    )
    result = client.lookup_doi("10.1/test")
    assert result == {
        "id": "W123",
        "display_name": "A Paper",
        "doi": "https://doi.org/10.1/test",
    }


def test_lookup_doi_404_returns_none(
    setup: tuple[OpenAlexClient, MagicMock],
) -> None:
    client, session = setup
    session.get.return_value = _mock_response(404)
    assert client.lookup_doi("10.1/missing") is None


def test_lookup_doi_uses_cache_on_repeat(
    setup: tuple[OpenAlexClient, MagicMock],
) -> None:
    client, session = setup
    session.get.return_value = _mock_response(200, {"display_name": "A Paper"})
    client.lookup_doi("10.1/test")
    client.lookup_doi("10.1/test")
    assert session.get.call_count == 1


def test_404_is_cached(setup: tuple[OpenAlexClient, MagicMock]) -> None:
    client, session = setup
    session.get.return_value = _mock_response(404)
    assert client.lookup_doi("10.1/missing") is None
    assert client.lookup_doi("10.1/missing") is None
    assert session.get.call_count == 1


def test_search_returns_results(setup: tuple[OpenAlexClient, MagicMock]) -> None:
    client, session = setup
    session.get.return_value = _mock_response(
        200,
        {"results": [{"id": "W1"}, {"id": "W2"}]},
    )
    items = client.search("hate speech", rows=2)
    assert [i["id"] for i in items] == ["W1", "W2"]
    called_url = session.get.call_args[0][0]
    assert "search=hate+speech" in called_url
    assert "per-page=2" in called_url


def test_polite_pool_mailto_added_to_url(
    setup: tuple[OpenAlexClient, MagicMock],
) -> None:
    client, session = setup
    session.get.return_value = _mock_response(200, {"display_name": "X"})
    client.lookup_doi("10.1/test")
    called_url = session.get.call_args[0][0]
    assert "mailto=test%40example.com" in called_url


def test_cache_key_strips_mailto(tmp_path: Path) -> None:
    """Cache should be unaffected by the mailto used on the request."""
    cache = JsonlCache(tmp_path / "cache.jsonl")

    client_a = OpenAlexClient(cache=cache, mailto="a@example.com")
    client_a.session = MagicMock()  # type: ignore[assignment]
    client_a.session.get.return_value = _mock_response(200, {"display_name": "A Paper"})
    client_a.lookup_doi("10.1/test")

    client_b = OpenAlexClient(cache=cache, mailto="b@example.com")
    client_b.session = MagicMock()  # type: ignore[assignment]
    client_b.lookup_doi("10.1/test")
    assert client_b.session.get.call_count == 0


def test_reconstruct_abstract_orders_by_position() -> None:
    work = {
        "abstract_inverted_index": {
            "Hello": [0],
            "world": [1, 3],
            "cruel": [2],
        }
    }
    assert reconstruct_abstract(work) == "Hello world cruel world"


def test_reconstruct_abstract_missing_returns_none() -> None:
    assert reconstruct_abstract({}) is None
    assert reconstruct_abstract({"abstract_inverted_index": None}) is None
    assert reconstruct_abstract({"abstract_inverted_index": {}}) is None


def test_strip_mailto_preserves_other_params() -> None:
    url = "https://api.openalex.org/works?search=foo&per-page=3&mailto=x@y.com"
    assert _strip_mailto(url) == "https://api.openalex.org/works?search=foo&per-page=3"


def test_strip_mailto_no_query_passthrough() -> None:
    url = "https://api.openalex.org/works/doi:10.1/test"
    assert _strip_mailto(url) == url


def test_is_arxiv_doi() -> None:
    assert is_arxiv_doi("10.48550/arXiv.2410.21554")
    assert is_arxiv_doi("10.48550/arxiv.2410.21554")  # case-insensitive
    assert not is_arxiv_doi("10.1126/science.aap9559")
    assert not is_arxiv_doi("10.1145/3442188.3445922")
