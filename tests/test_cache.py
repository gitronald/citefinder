"""Tests for the JSONL cache."""

import json
import logging
from pathlib import Path

import pytest

from citefinder.cache import JsonlCache, LayeredCache, read_records
from citefinder.openalex import OpenAlexClient


def test_put_and_get(tmp_path: Path) -> None:
    cache = JsonlCache(tmp_path / "c.jsonl")
    cache.put("key1", {"value": 1})
    assert cache.get("key1") == {"value": 1}
    assert "key1" in cache
    assert "missing" not in cache
    assert cache.get("missing") is None


def test_replay_after_reload(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    cache = JsonlCache(path)
    cache.put("a", 1)
    cache.put("b", 2)

    reloaded = JsonlCache(path)
    assert reloaded.get("a") == 1
    assert reloaded.get("b") == 2
    assert len(reloaded) == 2


def test_latest_value_wins(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    cache = JsonlCache(path)
    cache.put("k", "first")
    cache.put("k", "second")
    assert cache.get("k") == "second"

    reloaded = JsonlCache(path)
    assert reloaded.get("k") == "second"


def test_replay_skips_a_corrupt_line_and_keeps_the_rest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "c.jsonl"
    JsonlCache(path).put("a", 1)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"key": "b", "val')  # a write interrupted mid-append
    with caplog.at_level(logging.WARNING, logger="citefinder"):
        cache = JsonlCache(path)
    assert cache.get("a") == 1
    assert "b" not in cache
    assert "skipping unreadable cache line" in caplog.text

    # The next record starts on a fresh line, so it survives a reload too.
    cache.put("c", 3)
    reloaded = JsonlCache(path)
    assert reloaded.get("c") == 3
    assert len(reloaded) == 2


def test_replay_skips_a_line_torn_inside_a_multibyte_character(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Values are written with ensure_ascii=False, so a crash can leave the
    # file ending on the first byte of a two-byte character. Decoding the
    # whole file at once raised UnicodeDecodeError and lost every record.
    path = tmp_path / "c.jsonl"
    JsonlCache(path).put("a", 1)
    record = json.dumps({"key": "b", "value": "café"}, ensure_ascii=False).encode()
    with path.open("ab") as f:
        f.write(record[: record.index("é".encode()) + 1])
    with caplog.at_level(logging.WARNING, logger="citefinder"):
        cache = JsonlCache(path)
    assert cache.get("a") == 1
    assert "b" not in cache
    assert "skipping unreadable cache line" in caplog.text
    cache.put("c", 3)
    assert JsonlCache(path).get("c") == 3


def test_replay_skips_a_record_without_key_or_value(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text('{"key": "a", "value": 1}\n{"nope": true}\n42\n', encoding="utf-8")
    cache = JsonlCache(path)
    assert cache.get("a") == 1
    assert len(cache) == 1


def test_put_of_an_unserialisable_value_leaves_no_trace(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    cache = JsonlCache(path)
    with pytest.raises(TypeError):
        cache.put("k", {1, 2})  # a set is not JSON
    assert "k" not in cache
    assert len(cache) == 0
    assert not path.exists() or path.read_text() == ""


def test_caches_none_for_misses(tmp_path: Path) -> None:
    cache = JsonlCache(tmp_path / "c.jsonl")
    cache.put("missing-doi", None)
    assert "missing-doi" in cache
    assert cache.get("missing-doi") is None


def test_tilde_path_expands_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cache = JsonlCache("~/citefinder-test-cache.jsonl")
    assert cache.path == tmp_path / "citefinder-test-cache.jsonl"
    assert "~" not in cache.path.parts


def test_tilde_cache_path_creates_no_literal_tilde_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)

    client = OpenAlexClient(cache_path="~/.cache/citefinder/openalex.jsonl")
    assert client.cache is not None
    client.cache.put("k", {"v": 1})
    assert client.cache.get("k") == {"v": 1}

    assert not (cwd / "~").exists()
    assert (home / ".cache" / "citefinder" / "openalex.jsonl").exists()


def test_read_records_keeps_duplicates_and_skips_unreadable_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "c.jsonl"
    rows = [
        {"key": "k", "value": 1, "ts": 0},
        {"key": "k", "value": 2, "ts": 1},
        {"value": "no key", "ts": 2},
    ]
    path.write_bytes(
        b"\n".join(json.dumps(r).encode("utf-8") for r in rows) + b'\n{"key": "\xc3'
    )
    with caplog.at_level(logging.WARNING, logger="citefinder"):
        records = read_records(path)
    assert [r["value"] for r in records] == [1, 2]
    assert [r.message for r in caplog.records] == [
        f"{path}:3: skipping unreadable cache line",
        f"{path}:4: skipping unreadable cache line",
    ]
    # The cache itself still lets the latest duplicate win.
    assert JsonlCache(path).get("k") == 2


# --- LayeredCache -----------------------------------------------------------


def _layered(tmp_path: Path) -> tuple[LayeredCache, JsonlCache, JsonlCache]:
    primary = JsonlCache(tmp_path / "run" / "c.jsonl")
    fallback = JsonlCache(tmp_path / "shared.jsonl")
    return LayeredCache(primary, fallback), primary, fallback


def test_layered_primary_wins_over_fallback(tmp_path: Path) -> None:
    layered, primary, fallback = _layered(tmp_path)
    fallback.put("k", "old")
    primary.put("k", "new")
    assert layered.get("k") == "new"


def test_layered_fallback_hit_includes_a_cached_none(tmp_path: Path) -> None:
    layered, _, fallback = _layered(tmp_path)
    fallback.put("rec", {"id": 1})
    fallback.put("dead", None)
    assert "rec" in layered and layered.get("rec") == {"id": 1}
    # A cached 404 is a hit, not a miss: membership must see it.
    assert "dead" in layered and layered.get("dead") is None
    assert "absent" not in layered and layered.get("absent") is None


def test_layered_put_never_touches_the_fallback(tmp_path: Path) -> None:
    primary = JsonlCache(tmp_path / "run" / "c.jsonl")
    shared = tmp_path / "shared.jsonl"
    shared.write_text('{"key": "x", "value": 1, "ts": 1.0}\n')
    before = shared.read_bytes()
    layered = LayeredCache(primary, JsonlCache(shared))
    layered.put("y", 2)
    assert shared.read_bytes() == before
    assert JsonlCache(tmp_path / "run" / "c.jsonl").get("y") == 2


def test_layered_missing_fallback_file_is_an_empty_layer(tmp_path: Path) -> None:
    layered, _, _ = _layered(tmp_path)
    assert "k" not in layered
    assert len(layered) == 0
    assert not (tmp_path / "shared.jsonl").exists()


def test_layered_len_counts_distinct_keys(tmp_path: Path) -> None:
    layered, primary, fallback = _layered(tmp_path)
    primary.put("a", 1)
    primary.put("b", 2)
    fallback.put("b", 3)
    fallback.put("c", None)
    assert len(layered) == 3


def test_layered_rate_limit_snapshot_prefers_the_primary(tmp_path: Path) -> None:
    key = OpenAlexClient.rate_limit_key
    assert key is not None
    shared = JsonlCache(tmp_path / "shared.jsonl")
    shared.put(key, {"headers": {"x-ratelimit-remaining": "10"}, "ts": 1.0})
    run = tmp_path / "run" / "openalex.jsonl"

    # A fresh per-run cache inherits the source-wide snapshot...
    client = OpenAlexClient(cache_path=run, fallback_cache=shared.path)
    assert client.rate_limit == shared.get(key)

    # ...until the run captures its own, which then wins.
    JsonlCache(run).put(key, {"headers": {"x-ratelimit-remaining": "5"}, "ts": 2.0})
    client = OpenAlexClient(cache_path=run, fallback_cache=shared.path)
    assert client.rate_limit is not None
    assert client.rate_limit["headers"]["x-ratelimit-remaining"] == "5"
