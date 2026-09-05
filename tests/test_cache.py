"""Tests for the JSONL cache."""

import logging
from pathlib import Path

import pytest

from citefinder.cache import JsonlCache
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
