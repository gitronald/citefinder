"""Tests for quota-header capture, its cached snapshot, and `ratelimit`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from citefinder._base import RATE_LIMIT_PERSIST_INTERVAL
from citefinder.cache import JsonlCache, read_records, source_for_key
from citefinder.cli import _age, app
from citefinder.client import CrossrefClient
from citefinder.models import CacheRow, _model_for
from citefinder.openalex import OpenAlexClient

runner = CliRunner()

OA_HEADERS = {
    "x-ratelimit-limit": "1000",
    "x-ratelimit-remaining": "998",
    "x-ratelimit-cost-usd": "0.0001",
    "content-type": "application/json",
}


def make_openalex(tmp_path: Path, **kwargs) -> tuple[OpenAlexClient, MagicMock]:
    client = OpenAlexClient(cache_path=tmp_path / "openalex.jsonl", **kwargs)
    session = MagicMock()
    client.session = session  # type: ignore[assignment]
    return client, session


def snapshot_rows(path: Path) -> list[CacheRow]:
    """The cached rows holding a quota snapshot rather than a lookup."""
    return [row for row in read_records(path) if "__ratelimit" in row["key"]]


def test_quota_headers_are_captured_from_an_ordinary_lookup(
    tmp_path: Path, mock_response
) -> None:
    """Knowing the budget costs no extra request: it rides on every response."""
    client, session = make_openalex(tmp_path)
    session.get.return_value = mock_response(200, {"id": "W1"}, headers=OA_HEADERS)

    client.lookup_doi("10.1/x")

    assert client.rate_limit is not None
    headers = client.rate_limit["headers"]
    assert headers["x-ratelimit-remaining"] == "998"
    # Only the quota headers are kept.
    assert "content-type" not in headers


def test_the_snapshot_is_stored_in_the_cache_and_reloaded(
    tmp_path: Path, mock_response
) -> None:
    client, session = make_openalex(tmp_path)
    session.get.return_value = mock_response(200, {"id": "W1"}, headers=OA_HEADERS)
    client.lookup_doi("10.1/x")

    # A fresh client over the same cache reports what the last run saw.
    reloaded = OpenAlexClient(cache_path=tmp_path / "openalex.jsonl")
    assert reloaded.rate_limit is not None
    assert reloaded.rate_limit["headers"]["x-ratelimit-remaining"] == "998"


def test_the_snapshot_key_survives_merge_and_is_skipped_by_drift() -> None:
    """A URL-shaped key on the API's own host routes like any other row.

    A non-URL key would be counted `unroutable` and dropped by `cache merge`;
    a `/works` key would show up as permanent drift. This is neither.
    """
    key = OpenAlexClient.rate_limit_key
    assert key is not None
    assert source_for_key(key) == "openalex"
    assert source_for_key(CrossrefClient.rate_limit_key) == "crossref"
    row: CacheRow = {"key": key, "value": {"headers": {}, "ts": 0.0}, "ts": 0.0}
    assert _model_for(row) is None


def test_persisting_is_throttled_so_the_log_does_not_grow_per_request(
    tmp_path: Path, mock_response
) -> None:
    """The cache is append-only; a counter must not add a line per request."""
    now = [1000.0]
    client, session = make_openalex(tmp_path, clock=lambda: now[0])
    session.get.return_value = mock_response(200, {"id": "W1"}, headers=OA_HEADERS)

    client.lookup_doi("10.1/one")
    client.lookup_doi("10.1/two")
    assert len(snapshot_rows(tmp_path / "openalex.jsonl")) == 1

    now[0] += RATE_LIMIT_PERSIST_INTERVAL + 1
    client.lookup_doi("10.1/three")
    assert len(snapshot_rows(tmp_path / "openalex.jsonl")) == 2


def test_refresh_probes_without_caching_the_body(tmp_path: Path, mock_response) -> None:
    """The probe is a status check, not a lookup: nothing is cached from it."""
    client, session = make_openalex(tmp_path)
    session.get.return_value = mock_response(200, {"id": "W1"}, headers=OA_HEADERS)

    client.refresh_rate_limit()

    probe = OpenAlexClient.rate_limit_probe
    assert probe is not None
    called = session.get.call_args[0][0]
    assert called.startswith(probe)
    cache = JsonlCache(tmp_path / "openalex.jsonl")
    assert probe not in cache
    assert client.rate_limit is not None


def test_refresh_writes_exactly_one_row(tmp_path: Path, mock_response) -> None:
    """Capture-then-force must not store the same reading twice."""
    client, session = make_openalex(tmp_path)
    session.get.return_value = mock_response(200, {"id": "W1"}, headers=OA_HEADERS)

    client.refresh_rate_limit()

    assert len(snapshot_rows(tmp_path / "openalex.jsonl")) == 1


def test_ratelimit_reports_the_stored_snapshot(tmp_path: Path, mock_response) -> None:
    client, session = make_openalex(tmp_path)
    session.get.return_value = mock_response(200, {"id": "W1"}, headers=OA_HEADERS)
    client.lookup_doi("10.1/x")

    result = runner.invoke(
        app, ["ratelimit", "--cache", str(tmp_path / "openalex.jsonl")]
    )

    assert result.exit_code == 0, result.output
    assert "openalex" in result.output
    assert "x-ratelimit-remaining" in result.output
    assert "998" in result.output


def test_ratelimit_says_so_when_nothing_is_recorded(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ratelimit", "--cache", str(tmp_path / "empty.jsonl")])
    assert result.exit_code == 0, result.output
    assert "nothing recorded yet" in result.output


def test_ratelimit_rejects_an_unknown_source(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["ratelimit", "--source", "scopus", "--cache", str(tmp_path / "x.jsonl")]
    )
    assert result.exit_code == 2
    assert "unknown source" in result.output


def test_ratelimit_reports_crossref_too(tmp_path: Path, mock_response) -> None:
    client = CrossrefClient(cache_path=tmp_path / "crossref.jsonl", min_interval=0)
    client.session = MagicMock()  # type: ignore[assignment]
    client.session.get.return_value = mock_response(
        200,
        {"message": {}},
        headers={"x-rate-limit-limit": "3", "x-rate-limit-interval": "1s"},
    )
    client.lookup_doi("10.1/x")

    result = runner.invoke(
        app,
        [
            "ratelimit",
            "--source",
            "crossref",
            "--cache",
            str(tmp_path / "crossref.jsonl"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "x-rate-limit-interval" in result.output
    assert "1s" in result.output


def test_ratelimit_refresh_asks_for_a_current_reading(
    tmp_path: Path, monkeypatch
) -> None:
    """The CLI builds its own client, so the probe is patched at the method."""
    calls: list[str] = []

    def fake_refresh(self) -> dict[str, Any]:
        calls.append("refreshed")
        self.rate_limit = {
            "headers": {"x-ratelimit-remaining": "42"},
            "ts": time.time(),
        }
        return self.rate_limit

    monkeypatch.setattr(OpenAlexClient, "refresh_rate_limit", fake_refresh)
    result = runner.invoke(
        app, ["ratelimit", "--refresh", "--cache", str(tmp_path / "openalex.jsonl")]
    )

    assert result.exit_code == 0, result.output
    assert calls == ["refreshed"]
    assert "42" in result.output


def test_capture_survives_a_response_with_no_readable_headers(tmp_path: Path) -> None:
    """A test double or exotic transport must not break the lookup itself."""
    client, session = make_openalex(tmp_path)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": "W1"}
    response.headers = object()  # no `.items()`
    session.get.return_value = response

    assert client.lookup_doi("10.1/x") == {"id": "W1"}
    assert client.rate_limit is None


def test_a_client_with_no_cache_still_tracks_in_memory(mock_response) -> None:
    """Nothing to persist to is not a reason to stop counting."""
    client = OpenAlexClient()
    client.session = MagicMock()  # type: ignore[assignment]
    client.session.get.return_value = mock_response(
        200, {"id": "W1"}, headers=OA_HEADERS
    )

    client.lookup_doi("10.1/x")

    assert client.rate_limit is not None
    assert client.rate_limit["headers"]["x-ratelimit-remaining"] == "998"


def test_refresh_is_a_no_op_for_a_source_with_no_probe(tmp_path: Path) -> None:
    client, session = make_openalex(tmp_path)
    client.rate_limit_probe = None  # type: ignore[assignment]

    assert client.refresh_rate_limit() is None
    assert session.get.call_count == 0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-5.0, "in the future"),
        (0.0, "just now"),
        (59.0, "just now"),
        (600.0, "10m ago"),
        (7200.0, "2h ago"),
        (200_000.0, "2d ago"),
    ],
)
def test_age_reads_as_a_rough_duration(seconds: float, expected: str) -> None:
    assert _age(seconds) == expected
