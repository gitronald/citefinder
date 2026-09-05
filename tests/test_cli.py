"""Tests for CLI commands not covered by the config, install, or retry suites."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from citefinder.cli import app

runner = CliRunner()

BIB = """@article{smith2020,
  author = {Smith, Jane},
  title = {A Study of Things},
  year = {2020},
  doi = {10.1234/abc},
}
"""


def test_bib_to_table_fields_may_repeat_the_id_columns(tmp_path: Path) -> None:
    # Naming `key` in --fields used to produce a duplicate projection and a
    # polars traceback; the id columns are always shown, so repeats collapse.
    bib = tmp_path / "refs.bib"
    bib.write_text(BIB, encoding="utf-8")
    args = ["bib-to-table", str(bib), "--csv", "--fields", "key,title"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "key,entry_type,title"


def test_table_to_bib_writes_the_file_named_by_out(tmp_path: Path) -> None:
    csv = tmp_path / "refs.csv"
    csv.write_text("key,entry_type,title\nx,article,T\n", encoding="utf-8")
    out = tmp_path / "out.bib"
    result = runner.invoke(app, ["table-to-bib", str(csv), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == "@article{x,\n  title = {T},\n}\n"


def test_search_commands_emit_the_hits_as_json(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    cache = str(tmp_path / "c.jsonl")
    for args in (["search", "A Study of Things"], ["crossref", "search", "Smith 2020"]):
        result = runner.invoke(app, [*args, "--rows", "2", "--cache", cache])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []
        assert captured["cache_path"] == Path(cache)


def test_doi_commands_report_a_miss_and_exit_one(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    cache = str(tmp_path / "c.jsonl")
    for args in (["doi", "10.1/missing"], ["crossref", "doi", "10.1/missing"]):
        result = runner.invoke(app, [*args, "--cache", cache])
        assert result.exit_code == 1
        assert "not found: 10.1/missing" in result.output


def test_crossref_chapter_pads_digits_and_passes_strings_through(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    cache = str(tmp_path / "c.jsonl")
    result = runner.invoke(
        app, ["crossref", "chapter", "10.1017/book", "5", "--cache", cache]
    )
    assert json.loads(result.output) == {"id": "10.1017/book.005"}
    result = runner.invoke(
        app, ["crossref", "chapter", "10.1017/book", "ch1a", "--cache", cache]
    )
    assert json.loads(result.output) == {"id": "10.1017/book.ch1a"}


def test_verify_rejects_a_missing_bib_file(
    tmp_path: Path, captured: dict[str, Any]
) -> None:
    missing = tmp_path / "nope.bib"
    result = runner.invoke(
        app, ["verify", str(missing), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == 1
    assert "is not a file" in result.output
