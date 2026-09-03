"""Tests for cache-path resolution and the CLI's config-file loading."""

import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from citefinder.cli import _load_user_config, app
from citefinder.config import DEFAULT_CACHE_DIR, resolve_cache_path

runner = CliRunner()

CONFIG_ENV = (
    "CITEFINDER_CACHE_DIR",
    "OPENALEX_API_KEY",
    "OPENALEX_MAILTO",
    "OPENALEX_MAX_RETRIES",
    "OPENALEX_MIN_INTERVAL",
    "CROSSREF_MAILTO",
    "CROSSREF_MAX_RETRIES",
    "CROSSREF_MIN_INTERVAL",
)


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch) -> None:
    """Start from an empty config env and an empty user config dir.

    The loader writes `os.environ` directly; `delenv` records the prior
    (absent) state so monkeypatch removes whatever a test loads at teardown.
    """
    for name in CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


@pytest.fixture
def captured(monkeypatch) -> dict[str, Any]:
    """Swap both clients for a stub that records its constructor kwargs."""
    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            self.cache = None
            self.retries = 0

        def lookup_doi(self, doi: str) -> dict[str, str]:
            return {"id": doi}

        def search_title(self, title: str, rows: int = 3) -> list[Any]:
            return []

        def search_bibliographic(self, query: str, rows: int = 3) -> list[Any]:
            return []

    monkeypatch.setattr("citefinder.cli.OpenAlexClient", FakeClient)
    monkeypatch.setattr("citefinder.cli.CrossrefClient", FakeClient)
    return seen


def write_user_config(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "xdg" / "citefinder" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, encoding="utf-8")
    return cfg


def write_bib(directory: Path, name: str = "refs.bib") -> Path:
    """A one-entry bib with no DOI, so `verify` searches (and finds nothing)."""
    directory.mkdir(parents=True, exist_ok=True)
    bib = directory / name
    bib.write_text("@article{k1,\n  title = {A Paper},\n  year = {2020},\n}\n")
    return bib


# --- resolve_cache_path -----------------------------------------------------


def test_resolve_cache_path_defaults_to_the_home_cache_dir() -> None:
    assert resolve_cache_path("openalex") == DEFAULT_CACHE_DIR / "openalex.jsonl"
    assert DEFAULT_CACHE_DIR == Path.home() / ".cache" / "citefinder"


def test_resolve_cache_path_joins_cache_dir_and_expands_home(tmp_path: Path) -> None:
    assert resolve_cache_path("crossref", tmp_path) == tmp_path / "crossref.jsonl"
    assert resolve_cache_path("crossref", "~/x") == Path.home() / "x" / "crossref.jsonl"
    # A relative dir stays relative: anchoring is the caller's decision.
    assert resolve_cache_path("openalex", "data") == Path("data") / "openalex.jsonl"


# --- cache_dir precedence on the lookup commands ----------------------------


def test_lookup_defaults_to_the_home_cache_dir(captured) -> None:
    result = runner.invoke(app, ["doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == DEFAULT_CACHE_DIR / "openalex.jsonl"


def test_cache_flag_beats_cache_dir(tmp_path: Path, captured) -> None:
    explicit = tmp_path / "mine.jsonl"
    args = ["doi", "10.1/x", "--cache", str(explicit), "--cache-dir", str(tmp_path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == explicit


def test_cache_dir_flag_beats_env(tmp_path: Path, monkeypatch, captured) -> None:
    monkeypatch.setenv("CITEFINDER_CACHE_DIR", str(tmp_path / "env"))
    result = runner.invoke(
        app, ["doi", "10.1/x", "--cache-dir", str(tmp_path / "flag")]
    )
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == tmp_path / "flag" / "openalex.jsonl"


def test_env_cache_dir_beats_user_config(tmp_path: Path, monkeypatch, captured) -> None:
    write_user_config(tmp_path, f'cache_dir = "{tmp_path / "user"}"\n')
    monkeypatch.setenv("CITEFINDER_CACHE_DIR", str(tmp_path / "env"))

    _load_user_config()
    result = runner.invoke(app, ["doi", "10.1/x"])

    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == tmp_path / "env" / "openalex.jsonl"


def test_user_config_cache_dir_beats_default(tmp_path: Path, captured) -> None:
    write_user_config(tmp_path, f'cache_dir = "{tmp_path / "user"}"\n')

    _load_user_config()

    result = runner.invoke(app, ["doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == tmp_path / "user" / "openalex.jsonl"

    # The crossref subcommands derive their own file from the same dir.
    result = runner.invoke(app, ["crossref", "doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == tmp_path / "user" / "crossref.jsonl"


def test_relative_flag_and_env_cache_dir_anchor_to_cwd(
    tmp_path: Path, monkeypatch, captured
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doi", "10.1/x", "--cache-dir", "data"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == tmp_path / "data" / "openalex.jsonl"

    monkeypatch.setenv("CITEFINDER_CACHE_DIR", "data")
    result = runner.invoke(app, ["doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == tmp_path / "data" / "openalex.jsonl"


def test_relative_user_config_cache_dir_anchors_to_the_config_file(
    tmp_path: Path, monkeypatch, captured
) -> None:
    """A relative `cache_dir` names the same place whatever the cwd is."""
    cfg = write_user_config(tmp_path, 'cache_dir = "caches"\n')
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")

    _load_user_config()

    assert os.environ["CITEFINDER_CACHE_DIR"] == str(cfg.parent / "caches")
    result = runner.invoke(app, ["doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == cfg.parent / "caches" / "openalex.jsonl"


# --- verify ------------------------------------------------------------------


def test_verify_default_output_is_under_cwd(
    tmp_path: Path, monkeypatch, captured
) -> None:
    bib = write_bib(tmp_path / "paper")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["verify", str(bib)])

    assert result.exit_code == 0, result.output
    # `refs.bib` is filed under its parent directory's name.
    out = tmp_path / "data" / "citefinder" / "paper" / "openalex"
    assert (out / "results.json").is_file()
    assert captured["cache_path"] == out / "openalex.jsonl"


def test_verify_output_derives_from_cache_dir(tmp_path: Path, captured) -> None:
    bib = write_bib(tmp_path / "paper")
    caches = tmp_path / "caches"

    result = runner.invoke(app, ["verify", str(bib), "--cache-dir", str(caches)])

    assert result.exit_code == 0, result.output
    assert (caches / "paper" / "openalex" / "results.json").is_file()
    assert captured["cache_path"] == caches / "paper" / "openalex" / "openalex.jsonl"


def test_verify_honors_env_cache_dir_and_source(
    tmp_path: Path, monkeypatch, captured
) -> None:
    bib = write_bib(tmp_path, "thesis.bib")
    monkeypatch.setenv("CITEFINDER_CACHE_DIR", str(tmp_path / "caches"))

    result = runner.invoke(app, ["verify", str(bib), "--source", "crossref"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "caches" / "thesis" / "crossref" / "results.json").is_file()


def test_verify_out_beats_cache_dir(tmp_path: Path, captured) -> None:
    bib = write_bib(tmp_path / "paper")
    out, caches = tmp_path / "out", tmp_path / "caches"

    result = runner.invoke(
        app, ["verify", str(bib), "--out", str(out), "--cache-dir", str(caches)]
    )

    assert result.exit_code == 0, result.output
    assert (out / "results.json").is_file()
    assert not caches.exists()


# --- user config file ---------------------------------------------------------


def test_load_user_config_populates_env(tmp_path: Path) -> None:
    write_user_config(tmp_path, '[openalex]\nmailto = "you@example.com"\n')

    _load_user_config()

    assert os.environ.get("OPENALEX_MAILTO") == "you@example.com"


def test_load_user_config_ignores_a_malformed_toml(tmp_path: Path, capsys) -> None:
    """A credentials-file typo must not crash the whole CLI at import — with
    the skill body served by `citefinder skill`, that would zero out the
    skill's only delivery path on the machine."""
    write_user_config(tmp_path, "this is not valid toml [[[")

    _load_user_config()  # must not raise

    assert "warning: ignoring" in capsys.readouterr().err
