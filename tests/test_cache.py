"""Tests for the JSONL cache."""

from pathlib import Path

from citefinder.cache import JsonlCache


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


def test_caches_none_for_misses(tmp_path: Path) -> None:
    cache = JsonlCache(tmp_path / "c.jsonl")
    cache.put("missing-doi", None)
    assert "missing-doi" in cache
    assert cache.get("missing-doi") is None
