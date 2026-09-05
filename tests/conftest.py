"""Shared pytest fixtures."""

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from citefinder.client import CrossrefClient
from citefinder.openalex import OpenAlexClient


@pytest.fixture
def mock_response():
    """Factory for fake `requests.Response` objects."""

    def _make(
        status: int,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> MagicMock:
        response = MagicMock()
        response.status_code = status
        # Real `requests` headers are case-insensitive; keep the fake honest.
        response.headers = CaseInsensitiveDict(headers or {})
        response.json.return_value = payload
        response.raise_for_status = MagicMock()
        if status >= 400:
            # Mirror `requests`: 4xx/5xx raise, and the response rides
            # along so callers can read the status off the exception.
            response.raise_for_status.side_effect = requests.HTTPError(
                f"{status} error", response=response
            )
        return response

    return _make


@pytest.fixture
def captured(monkeypatch) -> dict[str, Any]:
    """Swap both CLI clients for a stub that records its constructor kwargs.

    The stub subclasses both real clients (without their `__init__`) so it
    passes `Source.search`'s `isinstance` narrowing and the CLI tests that
    reach the search path exercise it, rather than landing in `error`.
    """
    seen: dict[str, Any] = {}

    class FakeClient(OpenAlexClient, CrossrefClient):
        def __init__(self, **kwargs: Any) -> None:  # no session, no cache
            seen.update(kwargs)
            self.cache = None
            self.retries = 0

        def lookup_doi(  # pyrefly: ignore[missing-override-decorator]
            self, doi: str
        ) -> dict[str, str] | None:
            # A DOI ending in `/missing` plays the 404 so not-found paths run.
            return None if doi.endswith("/missing") else {"id": doi}

        def search_title(  # pyrefly: ignore[missing-override-decorator]
            self, title: str, rows: int = 3
        ) -> list[Any]:
            return []

        def search_bibliographic(  # pyrefly: ignore[missing-override-decorator]
            self, query: str, rows: int = 3
        ) -> list[Any]:
            return []

    monkeypatch.setattr("citefinder.cli.OpenAlexClient", FakeClient)
    monkeypatch.setattr("citefinder.cli.CrossrefClient", FakeClient)
    return seen
