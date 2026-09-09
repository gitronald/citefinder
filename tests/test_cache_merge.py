"""Tests for cache merging, inventory, and atomic rewriting."""

import json
from pathlib import Path
from typing import Any, cast

from citefinder.cache import (
    merge_caches,
    read_records,
    source_for_key,
    summarize_caches,
    write_records,
)
from citefinder.models import CacheRow

CROSSREF = "https://api.crossref.org/works/10.1000/a"
OPENALEX = "https://api.openalex.org/works/doi:10.1000/a"
OPENALEX_SEARCH = "https://api.openalex.org/works?filter=title.search:x"


def write(path: Path, *rows: CacheRow) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return path


def row(key: str, value: Any, ts: float | None = 0.0) -> CacheRow:
    """A cache row; `ts=None` writes one with no timestamp at all, as a
    hand-edited or externally produced cache can carry."""
    record: dict[str, Any] = {"key": key, "value": value}
    if ts is not None:
        record["ts"] = ts
    return cast(CacheRow, record)


# --- routing ----------------------------------------------------------------


def test_source_for_key_routes_by_host() -> None:
    assert source_for_key(CROSSREF) == "crossref"
    assert source_for_key(OPENALEX_SEARCH) == "openalex"
    assert source_for_key("https://example.org/works/1") is None
    assert source_for_key(None) is None
    # A host that merely mentions the API's name is not the API.
    assert source_for_key("https://api.crossref.org.evil.test/works/1") is None


def test_source_for_key_ignores_host_case() -> None:
    # A host is case-insensitive, so a key spelled in caps must route the
    # same; without the fold it lands as unroutable and is dropped by a merge.
    assert source_for_key("https://API.Crossref.ORG/works/10.1000/a") == "crossref"


def test_a_row_whose_key_is_not_a_url_is_unroutable(tmp_path: Path) -> None:
    path = write(tmp_path / "openalex.jsonl", row("not-a-url", {"v": 1}, 100.0))
    assert source_for_key("not-a-url") is None
    rows, stats = merge_caches([path], source="openalex")
    assert (rows, stats.unroutable) == ([], 1)


def test_a_row_is_routed_by_host_not_by_file_name(tmp_path: Path) -> None:
    misdirected = write(
        tmp_path / "openalex.jsonl",
        row(OPENALEX, {"id": "W1"}),
        row(CROSSREF, {"DOI": "10.1000/a"}),
    )
    rows, stats = merge_caches([misdirected], source="openalex")
    assert [r["key"] for r in rows] == [OPENALEX]
    # The stray row is another source's business, not this merge's.
    assert stats.misrouted == 0

    # The same file, merged for the other source, yields the stray row —
    # counted as misrouted, because this merge is the one that rehomes it.
    rows, stats = merge_caches([misdirected], source="crossref")
    assert [r["key"] for r in rows] == [CROSSREF]
    assert (stats.misrouted, stats.rows_read) == (1, 1)


def test_unroutable_rows_are_dropped_only_when_merging_into_a_source(
    tmp_path: Path,
) -> None:
    stray = "https://example.org/works/1"
    path = write(
        tmp_path / "openalex.jsonl",
        row(OPENALEX, {"id": "W1"}),
        row(stray, {"id": "X"}),
    )
    rows, stats = merge_caches([path], source="openalex")
    assert [r["key"] for r in rows] == [OPENALEX]
    assert stats.unroutable == 1

    # Compacting a file in place reports them but keeps them: rewriting
    # someone's cache must not silently lose readable rows.
    rows, stats = merge_caches([path])
    assert sorted(str(r["key"]) for r in rows) == sorted([OPENALEX, stray])
    assert stats.unroutable == 1


def test_a_row_with_a_non_string_key_is_never_written_back(tmp_path: Path) -> None:
    path = write(tmp_path / "openalex.jsonl", cast(CacheRow, {"key": 7, "value": 1}))
    rows, stats = merge_caches([path])
    assert rows == []
    assert (stats.rows_read, stats.unroutable) == (0, 1)


# --- ordering ---------------------------------------------------------------


def test_newest_ts_wins_across_files_regardless_of_read_order(tmp_path: Path) -> None:
    old = write(tmp_path / "old" / "openalex.jsonl", row(OPENALEX, {"v": "old"}, 100.0))
    new = write(tmp_path / "new" / "openalex.jsonl", row(OPENALEX, {"v": "new"}, 200.0))

    for paths in ([old, new], [new, old]):
        rows, stats = merge_caches(paths)
        assert [r["value"] for r in rows] == [{"v": "new"}]
        assert stats.replaced == 1
        assert stats.keys == 1
        assert stats.files == 2


def test_the_winner_keeps_its_own_ts(tmp_path: Path) -> None:
    a = write(tmp_path / "a" / "openalex.jsonl", row(OPENALEX, {"v": 1}, 100.0))
    b = write(tmp_path / "b" / "openalex.jsonl", row(OPENALEX, {"v": 2}, 200.0))
    rows, _ = merge_caches([a, b])
    assert rows[0]["ts"] == 200.0


def test_within_one_file_the_later_line_wins_even_with_an_older_ts(
    tmp_path: Path,
) -> None:
    # Line order is time order inside an append-only log; a clock that went
    # backwards does not reorder it.
    path = write(
        tmp_path / "openalex.jsonl",
        row(OPENALEX, {"v": "first"}, 200.0),
        row(OPENALEX, {"v": "second"}, 100.0),
    )
    rows, stats = merge_caches([path])
    assert [r["value"] for r in rows] == [{"v": "second"}]
    assert stats.replaced == 1


def test_a_row_without_ts_loses_but_is_not_dropped(tmp_path: Path) -> None:
    stamped = write(tmp_path / "a" / "openalex.jsonl", row(OPENALEX, {"v": 1}, 100.0))
    bare = write(
        tmp_path / "b" / "openalex.jsonl",
        row(OPENALEX, {"v": 2}, None),
        row(OPENALEX_SEARCH, {"results": []}, None),
    )
    rows, stats = merge_caches([bare, stamped])
    assert stats.missing_ts == 2
    by_key = {r["key"]: r for r in rows}
    assert by_key[OPENALEX]["value"] == {"v": 1}
    # The only row for its key still lands in the output, `ts` or not.
    assert by_key[OPENALEX_SEARCH]["value"] == {"results": []}
    # Oldest first: the unstamped row sorts ahead of the stamped one.
    assert [r["key"] for r in rows] == [OPENALEX_SEARCH, OPENALEX]


def test_a_ts_that_is_not_a_number_counts_as_missing(tmp_path: Path) -> None:
    # `True` is an `int` in Python, so without the explicit bool guard a row
    # stamped `true` would silently be read as ts=1.0 and beat a real one.
    path = write(
        tmp_path / "a" / "openalex.jsonl",
        cast(CacheRow, {"key": OPENALEX, "value": {"v": "bool"}, "ts": True}),
        cast(CacheRow, {"key": OPENALEX_SEARCH, "value": {"v": "str"}, "ts": "100"}),
    )
    stamped = write(
        tmp_path / "b" / "openalex.jsonl", row(OPENALEX, {"v": "real"}, 0.5)
    )
    rows, stats = merge_caches([path, stamped])
    assert stats.missing_ts == 2
    by_key = {r["key"]: r for r in rows}
    assert by_key[OPENALEX]["value"] == {"v": "real"}


def test_a_tie_on_ts_is_broken_the_same_way_whichever_file_is_read_first(
    tmp_path: Path,
) -> None:
    # Two independent logs can stamp the same second. `ts` alone leaves the
    # winner to the glob, which would consolidate the same two caches into
    # different files depending on the walk.
    a = write(tmp_path / "a" / "openalex.jsonl", row(OPENALEX, {"v": "a"}, 100.0))
    b = write(tmp_path / "b" / "openalex.jsonl", row(OPENALEX, {"v": "b"}, 100.0))
    first, _ = merge_caches([a, b])
    second, _ = merge_caches([b, a])
    assert first == second


def test_a_record_beats_a_404_stamped_the_same_instant(tmp_path: Path) -> None:
    miss = write(tmp_path / "a" / "crossref.jsonl", row(CROSSREF, None, 100.0))
    hit = write(tmp_path / "b" / "crossref.jsonl", row(CROSSREF, {"DOI": "x"}, 100.0))
    for paths in ([miss, hit], [hit, miss]):
        rows, stats = merge_caches(paths)
        assert rows[0]["value"] == {"DOI": "x"}
        assert (stats.nulls_superseded, stats.records_superseded) == (1, 0)


def test_output_is_ordered_oldest_first(tmp_path: Path) -> None:
    path = write(
        tmp_path / "openalex.jsonl",
        row(OPENALEX_SEARCH, {"results": []}, 300.0),
        row(OPENALEX, {"v": 1}, 100.0),
    )
    rows, _ = merge_caches([path])
    assert [r["ts"] for r in rows] == [100.0, 300.0]


# --- nulls ------------------------------------------------------------------


def test_a_record_supersedes_an_earlier_cached_404(tmp_path: Path) -> None:
    miss = write(tmp_path / "a" / "crossref.jsonl", row(CROSSREF, None, 100.0))
    hit = write(tmp_path / "b" / "crossref.jsonl", row(CROSSREF, {"DOI": "x"}, 200.0))
    rows, stats = merge_caches([miss, hit])
    assert rows[0]["value"] == {"DOI": "x"}
    assert (stats.nulls_superseded, stats.records_superseded) == (1, 0)


def test_a_later_404_supersedes_a_record_unless_keep_records(tmp_path: Path) -> None:
    hit = write(tmp_path / "a" / "crossref.jsonl", row(CROSSREF, {"DOI": "x"}, 100.0))
    miss = write(tmp_path / "b" / "crossref.jsonl", row(CROSSREF, None, 200.0))

    rows, stats = merge_caches([hit, miss])
    assert rows[0]["value"] is None
    assert (stats.records_superseded, stats.records_kept) == (1, 0)

    rows, stats = merge_caches([hit, miss], keep_records=True)
    assert rows[0]["value"] == {"DOI": "x"}
    assert (stats.records_superseded, stats.records_kept) == (0, 1)


def test_the_null_report_does_not_depend_on_the_order_files_are_read(
    tmp_path: Path,
) -> None:
    miss = write(tmp_path / "a" / "crossref.jsonl", row(CROSSREF, None, 100.0))
    hit = write(tmp_path / "b" / "crossref.jsonl", row(CROSSREF, {"DOI": "x"}, 200.0))
    for paths in ([miss, hit], [hit, miss]):
        rows, stats = merge_caches(paths)
        assert rows[0]["value"] == {"DOI": "x"}
        assert (stats.replaced, stats.nulls_superseded) == (1, 1)


def test_keep_records_holds_within_one_file_too(tmp_path: Path) -> None:
    # A verify run that 404'd after an earlier run had the record appends the
    # null to the same log; line order alone would let it win.
    path = write(
        tmp_path / "crossref.jsonl",
        row(CROSSREF, {"DOI": "x"}, 100.0),
        row(CROSSREF, None, 200.0),
    )
    rows, stats = merge_caches([path], keep_records=True)
    assert rows[0]["value"] == {"DOI": "x"}
    assert (stats.records_kept, stats.records_superseded) == (1, 0)

    rows, stats = merge_caches([path])
    assert rows[0]["value"] is None
    assert (stats.records_kept, stats.records_superseded) == (0, 1)


def test_a_ts_of_zero_is_a_timestamp_not_an_absent_one(tmp_path: Path) -> None:
    # The epoch is falsy, so tracking "newest seen" with `or` discards a
    # legitimately accumulated 0.0 and recomputes it from the row in hand —
    # which flips this key's report from `records kept` to `404 superseded`.
    record = write(tmp_path / "a" / "crossref.jsonl", row(CROSSREF, {"DOI": "x"}, None))
    stamped_null = write(tmp_path / "b" / "crossref.jsonl", row(CROSSREF, None, 0.0))
    bare_null = write(tmp_path / "c" / "crossref.jsonl", row(CROSSREF, None, None))

    rows, stats = merge_caches([record, stamped_null, bare_null], keep_records=True)
    assert rows[0]["value"] == {"DOI": "x"}
    assert (stats.records_kept, stats.nulls_superseded) == (1, 0)


def test_keep_records_still_lets_a_record_replace_a_404(tmp_path: Path) -> None:
    miss = write(tmp_path / "a" / "crossref.jsonl", row(CROSSREF, None, 100.0))
    hit = write(tmp_path / "b" / "crossref.jsonl", row(CROSSREF, {"DOI": "x"}, 200.0))
    rows, stats = merge_caches([miss, hit], keep_records=True)
    assert rows[0]["value"] == {"DOI": "x"}
    assert stats.nulls_superseded == 1


# --- compaction -------------------------------------------------------------


def test_compaction_keeps_one_line_per_key_and_is_idempotent(tmp_path: Path) -> None:
    path = write(
        tmp_path / "openalex.jsonl",
        row(OPENALEX, {"v": 1}, 100.0),
        row(OPENALEX, {"v": 2}, 200.0),
        row(OPENALEX_SEARCH, {"results": []}, 300.0),
    )
    rows, stats = merge_caches([path])
    assert (stats.rows_read, stats.keys, stats.replaced) == (3, 2, 1)
    write_records(path, rows)

    again, stats = merge_caches([path])
    assert again == rows
    assert (stats.rows_read, stats.keys, stats.replaced) == (2, 2, 0)
    assert read_records(path) == rows


def test_merge_skips_paths_that_do_not_exist(tmp_path: Path) -> None:
    path = write(tmp_path / "openalex.jsonl", row(OPENALEX, {"v": 1}, 100.0))
    rows, stats = merge_caches([path, tmp_path / "crossref.jsonl"])
    assert stats.files == 1
    assert len(rows) == 1


def test_merge_reads_the_same_unreadable_lines_the_cache_skips(tmp_path: Path) -> None:
    path = write(tmp_path / "openalex.jsonl", row(OPENALEX, {"v": 1}, 100.0))
    with path.open("a", encoding="utf-8") as f:
        f.write('{"key": "trunc')
    rows, stats = merge_caches([path])
    assert (stats.rows_read, len(rows)) == (1, 1)


# --- writing ----------------------------------------------------------------


def test_write_records_replaces_atomically_and_leaves_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "openalex.jsonl"
    write_records(path, [row(OPENALEX, {"v": 1}, 100.0)])
    assert read_records(path) == [row(OPENALEX, {"v": 1}, 100.0)]
    assert not path.with_name(path.name + ".tmp").exists()

    write_records(path, [row(OPENALEX, {"v": 2}, 200.0)])
    assert read_records(path) == [row(OPENALEX, {"v": 2}, 200.0)]


def test_written_rows_reload_through_the_cache(tmp_path: Path) -> None:
    from citefinder.cache import JsonlCache

    path = tmp_path / "openalex.jsonl"
    write_records(path, [row(OPENALEX, {"v": 1}, 100.0), row(CROSSREF, None, 200.0)])
    cache = JsonlCache(path)
    assert cache.get(OPENALEX) == {"v": 1}
    assert CROSSREF in cache and cache.get(CROSSREF) is None


# --- inventory --------------------------------------------------------------


def test_summarize_counts_rows_keys_and_kinds_per_source(tmp_path: Path) -> None:
    a = write(
        tmp_path / "a" / "openalex.jsonl",
        row(OPENALEX, None, 100.0),
        row(OPENALEX_SEARCH, {"results": []}, 150.0),
    )
    b = write(
        tmp_path / "b" / "openalex.jsonl",
        row(OPENALEX, {"id": "W1"}, 200.0),
        row(CROSSREF, {"DOI": "x"}, None),
        row("https://example.org/x", {}, 250.0),
    )
    summary = summarize_caches([a, b])

    openalex = summary["openalex"]
    assert (openalex.files, openalex.rows, openalex.keys) == (2, 3, 2)
    assert (openalex.lookups, openalex.searches) == (1, 1)
    # The 404 was refetched into a record, so nothing is a miss today.
    assert openalex.misses == 0
    assert openalex.newest_ts == 200.0

    crossref = summary["crossref"]
    assert (crossref.files, crossref.rows, crossref.keys) == (1, 1, 1)
    assert crossref.missing_ts == 1
    assert crossref.newest_ts is None

    assert summary["unroutable"].rows == 1


def test_summarize_reports_a_key_whose_newest_row_is_a_404(tmp_path: Path) -> None:
    path = write(
        tmp_path / "crossref.jsonl",
        row(CROSSREF, {"DOI": "x"}, 100.0),
        row(CROSSREF, None, 200.0),
    )
    summary = summarize_caches([path])
    assert (summary["crossref"].keys, summary["crossref"].misses) == (1, 1)


def test_summarize_of_nothing_is_empty(tmp_path: Path) -> None:
    assert summarize_caches([tmp_path / "openalex.jsonl"]) == {}


def test_summarize_counts_a_row_with_a_non_string_key_but_keys_nothing(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path / "openalex.jsonl",
        row(OPENALEX, {"v": 1}, 100.0),
        cast(CacheRow, {"key": 7, "value": 1, "ts": 200.0}),
    )
    summary = summarize_caches([path])
    assert (summary["unroutable"].rows, summary["unroutable"].keys) == (1, 0)
    assert summary["openalex"].keys == 1
