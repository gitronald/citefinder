"""Tests for `citefinder isbn` and `verify --books` (clients stubbed)."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from citefinder.cli import app
from tests.book_fixtures import BOOK_BIB, ISBN

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(config_env: None) -> None:
    """Every test here starts from the sandboxed config environment."""


def test_isbn_prints_merged_record_with_sources(
    tmp_path: Path, captured_books: dict[str, Any]
) -> None:
    result = runner.invoke(app, ["isbn", ISBN, "--cache-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["title"] == {"value": "Enshittification", "source": "loc"}
    assert record["place"] == {"value": "New York", "source": "loc"}
    assert record["loc_url"] == "https://lccn.loc.gov/2025007165"
    assert record["openlibrary_url"] == f"https://openlibrary.org/isbn/{ISBN}"
    assert captured_books["openlibrary"]["cache_path"] == tmp_path / "openlibrary.jsonl"
    assert captured_books["loc"]["cache_path"] == tmp_path / "loc.jsonl"


def test_isbn_no_loc_stops_at_openlibrary(
    tmp_path: Path, captured_books: dict[str, Any]
) -> None:
    args = ["isbn", ISBN, "--cache-dir", str(tmp_path), "--no-loc"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["title"]["source"] == "openlibrary:retailer"
    assert record["loc_url"] is None
    assert "loc" not in captured_books


def test_isbn_miss_exits_one(tmp_path: Path, captured_books: dict[str, Any]) -> None:
    result = runner.invoke(app, ["isbn", "0000000000", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "0000000000" in result.output


def test_isbn_knobs_reach_both_clients(
    tmp_path: Path, captured_books: dict[str, Any], monkeypatch
) -> None:
    monkeypatch.setenv("OPENLIBRARY_MAILTO", "you@example.com")
    args = ["isbn", ISBN, "--cache-dir", str(tmp_path), "--max-retries", "1"]
    args += ["--min-interval", "2"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    for name in ("openlibrary", "loc"):
        assert captured_books[name]["max_retries"] == 1
        assert captured_books[name]["min_interval"] == 2.0
        assert captured_books[name]["mailto"] == "you@example.com"


def test_verify_books_checks_the_isbn(
    tmp_path: Path, captured: dict[str, Any], captured_books: dict[str, Any]
) -> None:
    bib = tmp_path / "paper" / "refs.bib"
    bib.parent.mkdir()
    bib.write_text(BOOK_BIB)
    caches = tmp_path / "caches"
    args = ["verify", str(bib), "--books", "--cache-dir", str(caches)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Source: openalex (+ books: openlibrary, loc)" in result.output
    assert "matched" in result.output
    assert "isbn" in result.output
    out_dir = caches / "paper" / "openalex"
    payload = json.loads((out_dir / "results.json").read_text())
    (entry,) = payload["results"]
    assert entry["method"] == "isbn"
    assert entry["status"] == "matched"
    assert {s["field"] for s in entry["suggestions"]} == {"title", "location"}
    # The book caches land beside the source cache, with the shared root
    # as the read-only fallback.
    assert captured_books["openlibrary"]["cache_path"] == out_dir / "openlibrary.jsonl"
    assert (
        captured_books["openlibrary"]["fallback_cache"] == caches / "openlibrary.jsonl"
    )
    assert captured_books["loc"]["cache_path"] == out_dir / "loc.jsonl"
    assert captured_books["loc"]["fallback_cache"] == caches / "loc.jsonl"
    assert "2 network call(s)" in result.output


def test_verify_books_no_fallback(
    tmp_path: Path, captured: dict[str, Any], captured_books: dict[str, Any]
) -> None:
    bib = tmp_path / "refs.bib"
    bib.write_text(BOOK_BIB)
    args = ["verify", str(bib), "--books", "--no-fallback", "--out", str(tmp_path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "fallback_cache" not in captured_books["openlibrary"]


def test_verify_books_out_at_cache_root_does_not_layer_a_file_over_itself(
    tmp_path: Path, captured: dict[str, Any], captured_books: dict[str, Any]
) -> None:
    bib = tmp_path / "refs.bib"
    bib.write_text(BOOK_BIB)
    root = tmp_path / "root"
    args = ["verify", str(bib), "--books", "--cache-dir", str(root), "--out", str(root)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert captured_books["openlibrary"]["fallback_cache"] is None


def test_verify_without_books_leaves_the_clients_alone(
    tmp_path: Path, captured: dict[str, Any], captured_books: dict[str, Any]
) -> None:
    bib = tmp_path / "refs.bib"
    bib.write_text(BOOK_BIB)
    result = runner.invoke(app, ["verify", str(bib), "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured_books == {}
    payload = json.loads((tmp_path / "results.json").read_text())
    assert payload["results"][0]["method"] == "search"


def test_config_lists_book_settings_and_caches(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "openlibrary.min_interval  default  1.0" in result.output
    assert "loc.min_interval" in result.output
    assert f"openlibrary cache:  {tmp_path / 'openlibrary.jsonl'}" in result.output
    assert f"loc cache:          {tmp_path / 'loc.jsonl'}" in result.output
