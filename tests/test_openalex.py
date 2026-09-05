"""Tests for the OpenAlex client and helpers (HTTP mocked)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from citefinder._base import _strip_mailto
from citefinder.cache import JsonlCache
from citefinder.openalex import (
    OpenAlexClient,
    is_arxiv_doi,
    normalize_title_query,
    reconstruct_abstract,
)


@pytest.fixture
def setup(tmp_path: Path) -> tuple[OpenAlexClient, MagicMock]:
    cache = JsonlCache(tmp_path / "cache.jsonl")
    client = OpenAlexClient(cache=cache, mailto="test@example.com")
    session = MagicMock()
    client.session = session  # type: ignore[assignment]
    return client, session


def test_lookup_doi_returns_work(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(
        200,
        {"id": "W123", "display_name": "A Paper", "doi": "https://doi.org/10.1/test"},
    )
    result = client.lookup_doi("10.1/test")
    assert result == {
        "id": "W123",
        "display_name": "A Paper",
        "doi": "https://doi.org/10.1/test",
    }


def test_lookup_doi_encodes_url_structural_characters(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"display_name": "X"})
    client.lookup_doi("10.1/a#b?c")
    called_url = session.get.call_args[0][0]
    assert called_url.startswith("https://api.openalex.org/works/doi:10.1/a%23b%3Fc?")
    client.lookup_doi("10.1/a#b?c")
    assert session.get.call_count == 1


def test_lookup_doi_uses_cache_on_repeat(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"display_name": "A Paper"})
    client.lookup_doi("10.1/test")
    client.lookup_doi("10.1/test")
    assert session.get.call_count == 1


def test_404_is_cached(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(404)
    assert client.lookup_doi("10.1/missing") is None
    assert client.lookup_doi("10.1/missing") is None
    assert session.get.call_count == 1


def test_search_returns_results(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(
        200,
        {"results": [{"id": "W1"}, {"id": "W2"}]},
    )
    items = client.search("hate speech", rows=2)
    assert [i["id"] for i in items] == ["W1", "W2"]
    called_url = session.get.call_args[0][0]
    assert "search=hate+speech" in called_url
    assert "per-page=2" in called_url


def test_search_title_uses_title_search_filter(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"results": [{"id": "W1"}]})
    client.search_title("Backstabber's Knife Collection", rows=2)
    called_url = session.get.call_args[0][0]
    assert "filter=title.search:" in called_url
    assert "per-page=2" in called_url


def test_search_title_remaps_apostrophe_to_curly(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"results": []})
    client.search_title("Backstabber's Knife Collection")
    called_url = session.get.call_args[0][0]
    # U+2019 is %E2%80%99 in URL-encoded form
    assert "%E2%80%99" in called_url
    assert "Backstabber%27s" not in called_url  # straight apostrophe gone


def test_search_title_strips_reserved_filter_chars(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"results": []})
    client.search_title("Title: with, all|reserved!chars?")
    called_url = session.get.call_args[0][0]
    # Title value should be free of the reserved chars (encoded or raw).
    _, _, value = called_url.partition("filter=title.search:")
    value = value.split("&", 1)[0]
    for ch in (":", ",", "|", "!", "?", "%2C", "%3A", "%7C", "%21", "%3F"):
        assert ch not in value


def test_search_title_empty_returns_empty(
    setup: tuple[OpenAlexClient, MagicMock],
) -> None:
    client, session = setup
    assert client.search_title("") == []
    assert client.search_title(",,,!!?") == []
    assert session.get.call_count == 0


def test_search_title_404_returns_empty(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(404)
    assert client.search_title("Some Title") == []


def testnormalize_title_query() -> None:
    assert normalize_title_query("Backstabber's") == "Backstabber’s"
    assert normalize_title_query("a, b: c|d!e?") == "a b c d e"
    assert normalize_title_query("  spaced   out  ") == "spaced out"
    assert normalize_title_query("") == ""


def test_polite_pool_mailto_added_to_url(
    setup: tuple[OpenAlexClient, MagicMock],
    mock_response,
) -> None:
    client, session = setup
    session.get.return_value = mock_response(200, {"display_name": "X"})
    client.lookup_doi("10.1/test")
    called_url = session.get.call_args[0][0]
    assert "mailto=test%40example.com" in called_url


def test_cache_key_strips_mailto(tmp_path: Path, mock_response) -> None:
    """Cache should be unaffected by the mailto used on the request."""
    cache = JsonlCache(tmp_path / "cache.jsonl")

    client_a = OpenAlexClient(cache=cache, mailto="a@example.com")
    client_a.session = MagicMock()  # type: ignore[assignment]
    client_a.session.get.return_value = mock_response(200, {"display_name": "A Paper"})
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


def test_api_key_sets_authorization_header() -> None:
    client = OpenAlexClient(api_key="test-key-123")
    assert client.session.headers.get("Authorization") == "Bearer test-key-123"


def test_api_key_falls_back_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "env-key-456")
    client = OpenAlexClient()
    assert client.api_key == "env-key-456"
    assert client.session.headers.get("Authorization") == "Bearer env-key-456"


def test_api_key_explicit_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "env-key")
    client = OpenAlexClient(api_key="explicit-key")
    assert client.api_key == "explicit-key"
    assert client.session.headers.get("Authorization") == "Bearer explicit-key"


def test_no_api_key_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    client = OpenAlexClient()
    assert client.api_key is None
    assert "Authorization" not in client.session.headers


def test_api_key_not_in_url(tmp_path: Path, mock_response) -> None:
    """API key should never end up in the URL (only in headers)."""
    cache = JsonlCache(tmp_path / "cache.jsonl")
    client = OpenAlexClient(cache=cache, api_key="secret-key", mailto="x@y.com")
    client.session = MagicMock()  # type: ignore[assignment]
    client.session.headers = {"Authorization": "Bearer secret-key"}
    client.session.get.return_value = mock_response(200, {"display_name": "X"})
    client.lookup_doi("10.1/test")
    called_url = client.session.get.call_args[0][0]
    assert "secret-key" not in called_url
    assert "api_key" not in called_url
