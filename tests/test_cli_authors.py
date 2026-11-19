"""Tests for the `citefinder author` and `authors-from-cache` commands."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from citefinder.cache import JsonlCache, read_records
from citefinder.cli import app

runner = CliRunner()

API = "https://api.openalex.org"


@pytest.fixture(autouse=True)
def _env(config_env: None) -> None:
    """Every test here starts from the sandboxed config environment."""


# --- author --------------------------------------------------------------------


def test_author_prints_the_summary_fields(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    cache = tmp_path / "c.jsonl"
    result = runner.invoke(app, ["author", "A1", "--cache", str(cache)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "id": "https://openalex.org/A1",
        "display_name": "Jané Smith",
        "display_name_alternatives": ["J. Smith", "Jané Smith"],
        "orcid": None,
        "works_count": 12,
        "last_known_institutions": ["Example University"],
    }
    assert captured["cache_path"] == cache


def test_author_raw_prints_the_whole_record(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    args = ["author", "0000-0002-1825-0097", "--raw", "--cache", str(tmp_path / "c")]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["cited_by_count"] == 340
    assert record["last_known_institutions"] == [{"display_name": "Example University"}]


def test_author_reports_a_miss_and_exits_one(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    result = runner.invoke(app, ["author", "A404", "--cache", str(tmp_path / "c")])
    assert result.exit_code == 1
    assert "not found: A404" in result.output


def test_author_rejects_a_name_before_building_a_client(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    result = runner.invoke(
        app, ["author", "Jane Smith", "--cache", str(tmp_path / "c")]
    )
    assert result.exit_code == 2
    assert "not an OpenAlex author ID or ORCID: 'Jane Smith'" in result.output
    assert captured == {}


# --- authors-from-cache ----------------------------------------------------------

HEADER = "id\tdisplay_name\talternatives\torcid"


def _work(*author_ids: str) -> dict[str, Any]:
    return {
        "authorships": [
            {"author": {"id": f"https://openalex.org/{a}"}} for a in author_ids
        ]
    }


def _profile(author_id: str, name: str, *alternatives: str, orcid: str | None = None):
    return {
        "id": f"https://openalex.org/{author_id}",
        "orcid": orcid,
        "display_name": name,
        "display_name_alternatives": [*alternatives, name],
    }


PROFILES = {
    "A1": _profile(
        "A1", "Jané Smith", "J. Smith", orcid="https://orcid.org/0000-0002-1825-0097"
    ),
    # A recorded spelling can hold a line break.
    "A2": _profile("A2", "Sam Lee", "Sam\n    Lee", "S. Lee"),
}


@pytest.fixture
def run_cache(tmp_path: Path) -> Path:
    """A verify run's cache: two DOI lookups, a search page, and a cached 404."""
    cache = JsonlCache(tmp_path / "run" / "openalex.jsonl")
    cache.put(f"{API}/works/doi:10.1/a", _work("A2", "A1"))
    cache.put(f"{API}/works/doi:10.1/b", _work("A1", "A3"))
    cache.put(f"{API}/works?filter=title.search:x", {"results": [_work("A9")]})
    cache.put(f"{API}/works/doi:10.1/gone", None)
    return cache.path


@pytest.fixture
def api(monkeypatch, mock_response) -> Callable[..., list[str]]:
    """Serve `PROFILES` (404 otherwise) from a patched session; returns the calls."""

    def _install(fail: str | None = None) -> list[str]:
        calls: list[str] = []

        def get(self: object, url: str, **kwargs: object) -> Any:
            author_id = url.rsplit("/", 1)[-1].split("?")[0]
            calls.append(author_id)
            if author_id == fail:
                return mock_response(500)
            if author_id in PROFILES:
                return mock_response(200, PROFILES[author_id])
            return mock_response(404)

        monkeypatch.setattr("requests.Session.get", get)
        return calls

    return _install


def _no_network(monkeypatch) -> None:
    def get(*args: object, **kwargs: object) -> None:
        raise AssertionError("authors-from-cache reached the network")

    monkeypatch.setattr("requests.Session.get", get)


def test_authors_from_cache_prints_one_row_per_distinct_author(
    run_cache: Path, api: Callable[..., list[str]]
) -> None:
    calls = api()
    result = runner.invoke(app, ["authors-from-cache", str(run_cache)])

    assert result.exit_code == 0, result.output
    # First-seen order; the search page's `A9` is not fetched.
    assert calls == ["A2", "A1", "A3"]
    assert result.stdout.splitlines() == [
        HEADER,
        "https://openalex.org/A2\tSam Lee\tS. Lee\t",
        "https://openalex.org/A1\tJané Smith\tJ. Smith"
        "\thttps://orcid.org/0000-0002-1825-0097",
        "https://openalex.org/A3\t\t\t",
    ]
    assert "3 distinct author(s)" in result.stderr
    assert "Done: 3 network call(s), 0 cache hit(s), 1 not found." in result.stderr


def test_authors_from_cache_labels_each_row_with_the_id_on_the_work(
    run_cache: Path, monkeypatch, mock_response
) -> None:
    # Every request is answered by one other profile, as for IDs merged into it.
    def get(self: object, url: str, **kwargs: object) -> Any:
        return mock_response(200, _profile("A9", "Merged Person"))

    monkeypatch.setattr("requests.Session.get", get)
    result = runner.invoke(app, ["authors-from-cache", str(run_cache)])

    assert result.exit_code == 0, result.output
    # A row joins back to its works, and the rows match the distinct IDs counted.
    assert result.stdout.splitlines() == [
        HEADER,
        "https://openalex.org/A2\tMerged Person\t\t",
        "https://openalex.org/A1\tMerged Person\t\t",
        "https://openalex.org/A3\tMerged Person\t\t",
    ]
    assert "3 distinct author(s)" in result.stderr


def test_authors_from_cache_rerun_answers_from_the_same_file(
    run_cache: Path, api: Callable[..., list[str]], monkeypatch
) -> None:
    api()
    first = runner.invoke(app, ["authors-from-cache", str(run_cache)])
    keys = [row["key"] for row in read_records(run_cache)]
    assert f"{API}/authors/A1" in keys
    # The 404 is cached too, so the gone profile is not asked for again.
    assert f"{API}/authors/A3" in keys

    _no_network(monkeypatch)
    second = runner.invoke(app, ["authors-from-cache", str(run_cache)])

    assert second.exit_code == 0, second.output
    assert second.stdout == first.stdout
    assert "Done: 0 network call(s), 3 cache hit(s), 1 not found." in second.stderr


def test_authors_from_cache_reads_the_shared_cache_as_a_fallback(
    tmp_path: Path, run_cache: Path, monkeypatch
) -> None:
    shared = JsonlCache(tmp_path / "caches" / "openalex.jsonl")
    for author_id in ("A1", "A2", "A3"):
        shared.put(f"{API}/authors/{author_id}", PROFILES.get(author_id))
    before = run_cache.read_bytes()
    _no_network(monkeypatch)

    args = [
        "authors-from-cache",
        str(run_cache),
        "--cache-dir",
        str(shared.path.parent),
    ]
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "Done: 0 network call(s), 3 cache hit(s), 1 not found." in result.stderr
    assert run_cache.read_bytes() == before


def test_authors_from_cache_no_fallback_reads_only_the_named_file(
    tmp_path: Path, run_cache: Path, api: Callable[..., list[str]]
) -> None:
    shared = JsonlCache(tmp_path / "caches" / "openalex.jsonl")
    shared.put(f"{API}/authors/A1", PROFILES["A1"])
    calls = api()

    args = [
        "authors-from-cache",
        str(run_cache),
        "--cache-dir",
        str(shared.path.parent),
    ]
    result = runner.invoke(app, [*args, "--no-fallback"])

    assert result.exit_code == 0, result.output
    assert calls == ["A2", "A1", "A3"]


def test_authors_from_cache_stops_on_an_http_error_and_resumes(
    run_cache: Path, api: Callable[..., list[str]]
) -> None:
    api(fail="A1")
    args = ["authors-from-cache", str(run_cache), "--max-retries", "0"]
    stopped = runner.invoke(app, args)

    assert stopped.exit_code == 1
    assert stopped.stdout.splitlines() == [
        HEADER,
        "https://openalex.org/A2\tSam Lee\tS. Lee\t",
    ]
    assert "Error: 500 error" in stopped.stderr
    assert "Stopped after 1 of 3; re-run to resume from the cache." in stopped.stderr

    calls = api()
    resumed = runner.invoke(app, args)

    assert resumed.exit_code == 0, resumed.output
    assert calls == ["A1", "A3"]
    assert "Done: 2 network call(s), 1 cache hit(s), 1 not found." in resumed.stderr


def test_authors_from_cache_with_no_linked_authors_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    cache = JsonlCache(tmp_path / "crossref.jsonl")
    cache.put("https://api.crossref.org/works/10.1/a", {"message": {"DOI": "10.1/a"}})
    _no_network(monkeypatch)

    result = runner.invoke(app, ["authors-from-cache", str(cache.path)])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "no OpenAlex DOI lookups with linked authors" in result.stderr


def test_authors_from_cache_rejects_a_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["authors-from-cache", str(tmp_path / "nope.jsonl")])
    assert result.exit_code == 1
    assert "is not a file" in result.output
