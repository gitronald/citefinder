"""Tests for the `citefinder cache` maintenance commands."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from citefinder.cache import read_records
from citefinder.cli import app

runner = CliRunner()

CROSSREF = "https://api.crossref.org/works/10.1000/b"
OPENALEX = "https://api.openalex.org/works/doi:10.1000/a"
OPENALEX_SEARCH = "https://api.openalex.org/works?filter=title.search:x"


def write(path: Path, *rows: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """A cache root shaped like a real one: a shared cache plus a per-run
    `verify` cache, with a 404 in one that the other has a record for."""
    root = tmp_path / "cache"
    write(
        root / "openalex.jsonl",
        {"key": OPENALEX, "value": None, "ts": 100.0},
        {"key": OPENALEX_SEARCH, "value": {"results": []}, "ts": 150.0},
    )
    write(
        root / "refs" / "openalex" / "openalex.jsonl",
        {"key": OPENALEX, "value": {"id": "W1"}, "ts": 200.0},
    )
    write(root / "crossref.jsonl", {"key": CROSSREF, "value": {"DOI": "x"}, "ts": 50.0})
    return root


def reported(output: str, label: str) -> str:
    """The value printed beside `label`, whatever the column padding is."""
    match = re.search(rf"^\s*{re.escape(label)}\s\s+(.+)$", output, re.MULTILINE)
    assert match is not None, f"{label!r} not in:\n{output}"
    return match.group(1)


def section(output: str, name: str) -> str:
    """One source's block of a report, so a label is read off the right one."""
    match = re.search(rf"^{name}\b.*?(?=\n\n|\Z)", output, re.MULTILINE | re.DOTALL)
    assert match is not None, f"no {name} section in:\n{output}"
    return match.group(0)


def run(*args: str) -> Any:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result


# --- stats ------------------------------------------------------------------


def test_stats_inventories_every_cache_under_the_directory(cache_dir: Path) -> None:
    out = run("cache", "stats", "--cache-dir", str(cache_dir)).output
    assert "files:     3" in out
    # Two openalex files, three rows, two distinct keys, and the 404 was
    # refetched into a record, so nothing is a miss today.
    openalex = section(out, "openalex")
    assert reported(openalex, "files") == "2"
    assert reported(openalex, "rows") == "3"
    assert reported(openalex, "distinct keys") == "2 (1 lookup(s), 1 search(es))"
    assert reported(openalex, "cached 404s") == "0"


def test_stats_fails_on_a_missing_directory_rather_than_reporting_zeros(
    tmp_path: Path,
) -> None:
    result = runner.invoke(app, ["cache", "stats", "--cache-dir", str(tmp_path / "no")])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_stats_reports_an_empty_cache_directory(tmp_path: Path) -> None:
    out = run("cache", "stats", "--cache-dir", str(tmp_path)).output
    assert "no cache rows found" in out


def test_stats_reads_the_cache_dir_from_the_environment(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CITEFINDER_CACHE_DIR", str(cache_dir))
    assert "files:     3" in run("cache", "stats").output


# --- merge ------------------------------------------------------------------


def test_merge_is_a_dry_run_by_default(cache_dir: Path) -> None:
    before = (cache_dir / "openalex.jsonl").read_text(encoding="utf-8")
    out = run("cache", "merge", "--cache-dir", str(cache_dir)).output
    assert "would write" in out and "pass --write" in out
    assert (cache_dir / "openalex.jsonl").read_text(encoding="utf-8") == before


def test_merge_writes_one_line_per_key_and_leaves_the_inputs_alone(
    cache_dir: Path,
) -> None:
    per_run = cache_dir / "refs" / "openalex" / "openalex.jsonl"
    before = per_run.read_text(encoding="utf-8")
    out = run("cache", "merge", "--cache-dir", str(cache_dir), "--write").output
    assert reported(section(out, "openalex"), "404s superseded by a record") == "1"

    rows = read_records(cache_dir / "openalex.jsonl")
    assert [r["key"] for r in rows] == [OPENALEX_SEARCH, OPENALEX]
    assert rows[1]["value"] == {"id": "W1"}
    assert rows[1]["ts"] == 200.0  # the winner's own fetch time, not now
    # A verify run's evidence stays beside its results.json.
    assert per_run.read_text(encoding="utf-8") == before


def test_merge_is_idempotent(cache_dir: Path) -> None:
    run("cache", "merge", "--cache-dir", str(cache_dir), "--write")
    merged = (cache_dir / "openalex.jsonl").read_text(encoding="utf-8")
    run("cache", "merge", "--cache-dir", str(cache_dir), "--write")
    assert (cache_dir / "openalex.jsonl").read_text(encoding="utf-8") == merged


def test_merge_rehomes_a_row_the_wrong_file_holds(cache_dir: Path) -> None:
    # A misdirected record in the crossref cache belongs to openalex, and the
    # file it sits in must not decide where it ends up.
    write(
        cache_dir / "crossref.jsonl",
        {"key": CROSSREF, "value": {"DOI": "x"}, "ts": 50.0},
        {
            "key": "https://api.openalex.org/works/doi:10.1000/z",
            "value": {},
            "ts": 60.0,
        },
    )
    out = run("cache", "merge", "--cache-dir", str(cache_dir), "--write").output
    assert reported(section(out, "openalex"), "misrouted rows (host != file)") == "1"
    assert [r["key"] for r in read_records(cache_dir / "crossref.jsonl")] == [CROSSREF]
    keys = [r["key"] for r in read_records(cache_dir / "openalex.jsonl")]
    assert "https://api.openalex.org/works/doi:10.1000/z" in keys


def test_merge_keep_records_refuses_to_let_a_newer_404_win(cache_dir: Path) -> None:
    write(
        cache_dir / "refs" / "openalex" / "openalex.jsonl",
        {"key": OPENALEX, "value": {"id": "W1"}, "ts": 200.0},
        {"key": OPENALEX, "value": None, "ts": 300.0},
    )
    out = section(
        run("cache", "merge", "--cache-dir", str(cache_dir)).output, "openalex"
    )
    assert reported(out, "records superseded by a 404") == "1"

    out = section(
        run("cache", "merge", "--cache-dir", str(cache_dir), "--keep-records").output,
        "openalex",
    )
    assert reported(out, "records superseded by a 404") == "0"
    assert reported(out, "records kept over a newer 404") == "1"


def test_merge_takes_an_extra_file_from_outside_the_cache_dir(
    cache_dir: Path, tmp_path: Path
) -> None:
    extra = write(
        tmp_path / "openalex (conflicted copy).jsonl",
        {"key": OPENALEX_SEARCH, "value": {"results": [1]}, "ts": 999.0},
    )
    run(
        "cache",
        "merge",
        "--cache-dir",
        str(cache_dir),
        "--extra",
        str(extra),
        "--write",
    )
    rows = {r["key"]: r["value"] for r in read_records(cache_dir / "openalex.jsonl")}
    assert rows[OPENALEX_SEARCH] == {"results": [1]}


def test_merge_rejects_an_extra_that_is_not_a_file(cache_dir: Path) -> None:
    result = runner.invoke(
        app, ["cache", "merge", "--cache-dir", str(cache_dir), "--extra", "nope.jsonl"]
    )
    assert result.exit_code == 2
    assert "not a file" in result.output


def test_merge_reports_a_source_with_no_rows(tmp_path: Path) -> None:
    write(tmp_path / "crossref.jsonl", {"key": CROSSREF, "value": {}, "ts": 1.0})
    out = run("cache", "merge", "--cache-dir", str(tmp_path)).output
    assert "openalex (1 file(s) read)\n  no rows found" in out


# --- compact ----------------------------------------------------------------


def test_compact_dedupes_one_file_in_place(tmp_path: Path) -> None:
    path = write(
        tmp_path / "openalex.jsonl",
        {"key": OPENALEX, "value": {"v": 1}, "ts": 100.0},
        {"key": OPENALEX, "value": {"v": 2}, "ts": 200.0},
    )
    out = run("cache", "compact", str(path)).output
    assert reported(out, "duplicate rows dropped") == "1"
    assert len(read_records(path)) == 2  # dry run left the file alone

    run("cache", "compact", str(path), "--write")
    rows = read_records(path)
    assert [r["value"] for r in rows] == [{"v": 2}]
    assert not path.with_name(path.name + ".tmp").exists()


def test_compact_keeps_a_row_it_cannot_route(tmp_path: Path) -> None:
    path = write(
        tmp_path / "openalex.jsonl",
        {"key": OPENALEX, "value": {"v": 1}, "ts": 100.0},
        {"key": "https://example.org/works/1", "value": {"v": 2}, "ts": 200.0},
    )
    out = run("cache", "compact", str(path), "--write").output
    assert reported(out, "unroutable rows") == "1"
    assert len(read_records(path)) == 2


def test_compact_rejects_a_path_that_is_not_a_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["cache", "compact", str(tmp_path)])
    assert result.exit_code == 1
    assert "not a file" in result.output


def test_stats_prints_a_readable_timestamp_or_the_raw_value(tmp_path: Path) -> None:
    write(
        tmp_path / "openalex.jsonl",
        {"key": OPENALEX, "value": {"v": 1}},  # a hand-edited row, no ts
    )
    out = section(
        run("cache", "stats", "--cache-dir", str(tmp_path)).output, "openalex"
    )
    assert reported(out, "newest fetch") == "(none)"
    assert reported(out, "rows with no ts") == "1"

    # A ts no calendar can hold is printed as-is rather than crashing the run.
    write(tmp_path / "openalex.jsonl", {"key": OPENALEX, "value": {}, "ts": 1e30})
    out = section(
        run("cache", "stats", "--cache-dir", str(tmp_path)).output, "openalex"
    )
    assert reported(out, "newest fetch") == "1e+30"
