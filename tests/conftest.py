"""Shared pytest fixtures."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_response():
    """Factory for fake `requests.Response` objects."""

    def _make(status: int, payload: dict | None = None) -> MagicMock:
        response = MagicMock()
        response.status_code = status
        response.json.return_value = payload
        response.raise_for_status = MagicMock()
        return response

    return _make
