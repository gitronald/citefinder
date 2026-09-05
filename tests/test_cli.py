"""Tests for CLI commands not covered by the config, install, or retry suites."""

from pathlib import Path

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
