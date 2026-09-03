"""Tests for transient-error retries, `Retry-After` parsing, and pacing.

All waits go through an injected fake clock, so nothing here sleeps.
"""

from __future__ import annotations

import logging
import os
from email.utils import formatdate
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from typer.testing import CliRunner

from citefinder._base import retry_after_seconds
from citefinder.cache import JsonlCache
from citefinder.cli import _load_user_config, app
from citefinder.client import CrossrefClient
from citefinder.openalex import OpenAlexClient

WALL_NOW = 1_700_000_000.0  # 2023-11-14T22:13:20Z

runner = CliRunner()


class FakeClock:
    """Monotonic and wall clocks that advance only when something sleeps."""

    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = WALL_NOW
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.mono += seconds
        self.wall += seconds

    def monotonic(self) -> float:
        return self.mono

    def time(self) -> float:
        return self.wall


def http_date(ts: float) -> str:
    return formatdate(ts, usegmt=True)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_client(
    tmp_path: Path, clock: FakeClock, **kwargs: Any
) -> tuple[CrossrefClient, MagicMock]:
    client = CrossrefClient(
        cache=JsonlCache(tmp_path / "cache.jsonl"),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        clock=clock.time,
        **kwargs,
    )
    session = MagicMock()
    client.session = session  # type: ignore[assignment]
    return client, session


def cache_lines(tmp_path: Path) -> list[str]:
    path = tmp_path / "cache.jsonl"
    return path.read_text().splitlines() if path.exists() else []


# --- retry_after_seconds ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("2", 2.0),
        ("0", 0.0),
        ("-5", 0.0),
        ("garbage", None),
        ("inf", None),
    ],
)
def test_retry_after_seconds_delta_form(
    value: str | None, expected: float | None
) -> None:
    assert retry_after_seconds(value, now=WALL_NOW) == expected


def test_retry_after_seconds_http_date_form() -> None:
    assert retry_after_seconds(http_date(WALL_NOW + 90), now=WALL_NOW) == pytest.approx(
        90.0
    )
    assert retry_after_seconds(http_date(WALL_NOW - 90), now=WALL_NOW) == 0.0


# --- retry loop -------------------------------------------------------------


def test_retry_after_delta_then_success(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock)
    session.get.side_effect = [
        mock_response(429, headers={"Retry-After": "2"}),
        mock_response(200, {"message": {"DOI": "10.1/x"}}),
    ]

    assert client.lookup_doi("10.1/x") == {"DOI": "10.1/x"}

    assert clock.sleeps == [2.0]
    assert session.get.call_count == 2
    assert client.retries == 1
    assert len(cache_lines(tmp_path)) == 1


def test_retry_after_http_date_waits_until_then(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock)
    session.get.side_effect = [
        mock_response(429, headers={"Retry-After": http_date(WALL_NOW + 30)}),
        mock_response(200, {"message": {}}),
    ]
    client.lookup_doi("10.1/x")
    assert clock.sleeps == [pytest.approx(30.0)]


def test_retry_after_http_date_in_the_past_waits_zero(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock)
    session.get.side_effect = [
        mock_response(429, headers={"Retry-After": http_date(WALL_NOW - 30)}),
        mock_response(200, {"message": {}}),
    ]
    client.lookup_doi("10.1/x")
    assert clock.sleeps == [0.0]


def test_exhausted_retries_raise_after_backoff(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock, max_retries=3, backoff_base=1.0)
    session.get.side_effect = [mock_response(429) for _ in range(4)]

    with pytest.raises(requests.HTTPError):
        client.lookup_doi("10.1/x")

    assert session.get.call_count == 4
    assert client.retries == 3
    assert len(clock.sleeps) == 3
    for attempt, wait in enumerate(clock.sleeps):
        step = 1.0 * 2**attempt
        assert step <= wait <= step * 1.5, (attempt, wait)
    # Nothing reached the cache: no file, no in-memory entry.
    assert cache_lines(tmp_path) == []
    assert client.cache is not None and len(client.cache) == 0


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_errors_are_retried(
    status: int, tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock)
    session.get.side_effect = [
        mock_response(status),
        mock_response(200, {"message": {"ok": True}}),
    ]
    assert client.lookup_doi("10.1/x") == {"ok": True}
    assert len(clock.sleeps) == 1
    assert client.retries == 1


def test_client_errors_are_not_retried(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock)
    session.get.side_effect = [mock_response(400)]
    with pytest.raises(requests.HTTPError):
        client.lookup_doi("10.1/x")
    assert session.get.call_count == 1
    assert clock.sleeps == []
    assert client.retries == 0
    assert cache_lines(tmp_path) == []


def test_404_is_cached_as_none_without_retry(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock)
    session.get.side_effect = [mock_response(404)]
    assert client.lookup_doi("10.1/missing") is None
    assert session.get.call_count == 1
    assert clock.sleeps == []
    assert len(cache_lines(tmp_path)) == 1


def test_retry_after_is_capped_at_max_wait(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock, max_wait=60.0)
    session.get.side_effect = [
        mock_response(429, headers={"Retry-After": "600"}),
        mock_response(200, {"message": {}}),
    ]
    client.lookup_doi("10.1/x")
    assert clock.sleeps == [60.0]


def test_max_retries_zero_disables_retrying(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock, max_retries=0)
    session.get.side_effect = [mock_response(429)]
    with pytest.raises(requests.HTTPError):
        client.lookup_doi("10.1/x")
    assert session.get.call_count == 1
    assert clock.sleeps == []


def test_retry_logs_a_warning(
    tmp_path: Path, clock: FakeClock, mock_response, caplog
) -> None:
    client, session = make_client(tmp_path, clock)
    session.get.side_effect = [
        mock_response(429, headers={"Retry-After": "2"}),
        mock_response(200, {"message": {}}),
    ]
    with caplog.at_level(logging.WARNING, logger="citefinder"):
        client.lookup_doi("10.1/x")
    assert "429 from api.crossref.org: retry 1/3 in 2.0s" in caplog.text


# --- pacing -----------------------------------------------------------------


def test_min_interval_paces_consecutive_misses(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    client, session = make_client(tmp_path, clock, min_interval=1.0)
    session.get.return_value = mock_response(200, {"message": {}})

    client.lookup_doi("10.1/a")  # first request: nothing to wait for
    assert clock.sleeps == []

    clock.mono += 0.25
    client.lookup_doi("10.1/b")  # second miss: sleep off the remaining gap
    assert clock.sleeps == [pytest.approx(0.75)]

    client.lookup_doi("10.1/a")  # cache hit: no request, so no pacing
    assert len(clock.sleeps) == 1

    clock.mono += 5.0
    client.lookup_doi("10.1/c")  # well past the interval: no sleep
    assert len(clock.sleeps) == 1


def test_pacing_also_applies_to_retries(
    tmp_path: Path, clock: FakeClock, mock_response
) -> None:
    """A retry is a request: a short Retry-After still waits out min_interval."""
    client, session = make_client(tmp_path, clock, min_interval=1.0)
    session.get.side_effect = [
        mock_response(429, headers={"Retry-After": "0.5"}),
        mock_response(200, {"message": {}}),
    ]
    client.lookup_doi("10.1/x")
    assert clock.sleeps == [pytest.approx(0.5), pytest.approx(0.5)]


def test_default_pacing_per_source(tmp_path: Path) -> None:
    assert OpenAlexClient(cache_path=tmp_path / "oa.jsonl").min_interval == 0.1
    assert CrossrefClient(cache_path=tmp_path / "cr.jsonl").min_interval == 0


# --- CLI and config ---------------------------------------------------------


def test_cli_max_retries_zero_disables_retrying(
    tmp_path: Path, monkeypatch, mock_response
) -> None:
    session = MagicMock()
    session.headers = {}
    session.get.return_value = mock_response(429)
    monkeypatch.setattr("citefinder._base.requests.Session", lambda: session)

    result = runner.invoke(
        app,
        ["doi", "10.1/x", "--max-retries", "0", "--cache", str(tmp_path / "oa.jsonl")],
    )

    assert isinstance(result.exception, requests.HTTPError)
    assert session.get.call_count == 1


def test_config_retry_keys_reach_the_client(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "citefinder" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "[openalex]\nmax_retries = 0\nmin_interval = 0.5\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # `_load_user_config` writes os.environ directly; delenv records the
    # prior (absent) state so monkeypatch removes the keys again at teardown.
    for name in ("OPENALEX_MAX_RETRIES", "OPENALEX_MIN_INTERVAL"):
        monkeypatch.delenv(name, raising=False)

    _load_user_config()

    # `max_retries = 0` is a real setting, not an unset one.
    assert os.environ["OPENALEX_MAX_RETRIES"] == "0"
    assert os.environ["OPENALEX_MIN_INTERVAL"] == "0.5"

    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def lookup_doi(self, doi: str) -> dict[str, str]:
            return {"id": doi}

    monkeypatch.setattr("citefinder.cli.OpenAlexClient", FakeClient)
    result = runner.invoke(
        app, ["doi", "10.1/x", "--cache", str(tmp_path / "oa.jsonl")]
    )

    assert result.exit_code == 0, result.output
    assert captured["max_retries"] == 0
    assert captured["min_interval"] == 0.5


def test_verify_reports_retries_in_summary(tmp_path: Path, monkeypatch) -> None:
    bib = tmp_path / "refs.bib"
    bib.write_text(
        "@article{k1,\n  title = {A Paper},\n  year = {2020},\n  doi = {10.1/x},\n}\n",
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.cache = None
            self.retries = 2

        def lookup_doi(self, doi: str) -> None:
            return None

    monkeypatch.setattr("citefinder.cli.OpenAlexClient", FakeClient)
    result = runner.invoke(
        app, ["verify", str(bib), "--out", str(tmp_path / "out"), "--max-retries", "5"]
    )

    assert result.exit_code == 0, result.output
    assert "2 retries" in result.output
