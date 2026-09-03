"""Shared pytest fixtures."""

from unittest.mock import MagicMock

import pytest
import requests


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
        response.headers = dict(headers or {})
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
