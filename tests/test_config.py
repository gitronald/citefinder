"""Tests for cache-path resolution and the CLI's config-file loading."""

import importlib
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from citefinder.cli import _load_configs, app
from citefinder.config import (
    PROJECT_CONFIG_NAME,
    default_cache_dir,
    find_project_config,
    load_config,
    resolve_cache_path,
)

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
    """Start from an empty config env, an empty user config dir, and a
    sandboxed working directory.

    The loader writes `os.environ` directly; `delenv` records the prior
    (absent) state so monkeypatch removes whatever a test loads at teardown.
    Pinning cwd keeps project-config discovery inside `tmp_path` rather
    than walking the repo's ancestors, and a fresh `_config_sources` means
    no source label leaks in from an earlier test.
    """
    for name in CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("citefinder.cli._config_sources", {})


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
    assert resolve_cache_path("openalex") == default_cache_dir() / "openalex.jsonl"
    assert default_cache_dir() == Path.home() / ".cache" / "citefinder"


def test_resolve_cache_path_joins_cache_dir_and_expands_home(tmp_path: Path) -> None:
    assert resolve_cache_path("crossref", tmp_path) == tmp_path / "crossref.jsonl"
    assert resolve_cache_path("crossref", "~/x") == Path.home() / "x" / "crossref.jsonl"
    # A relative dir stays relative: anchoring is the caller's decision.
    assert resolve_cache_path("openalex", "data") == Path("data") / "openalex.jsonl"


def test_importing_config_never_touches_the_home_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """`Path.home()` raises where no home can be determined (an unnamed UID
    in a container); the default must be computed lazily, not at import.
    """
    import citefinder.config as config

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", no_home)
    importlib.reload(config)  # must not raise

    expected = tmp_path / "openalex.jsonl"
    assert config.resolve_cache_path("openalex", tmp_path) == expected
    with pytest.raises(RuntimeError):
        config.resolve_cache_path("openalex")


# --- cache_dir precedence on the lookup commands ----------------------------


def test_lookup_defaults_to_the_home_cache_dir(captured) -> None:
    result = runner.invoke(app, ["doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == default_cache_dir() / "openalex.jsonl"


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

    _load_configs()
    result = runner.invoke(app, ["doi", "10.1/x"])

    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == tmp_path / "env" / "openalex.jsonl"


def test_user_config_cache_dir_beats_default(tmp_path: Path, captured) -> None:
    write_user_config(tmp_path, f'cache_dir = "{tmp_path / "user"}"\n')

    _load_configs()

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

    _load_configs()

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


def test_verify_files_a_non_primary_bib_under_its_directory(
    tmp_path: Path, captured
) -> None:
    """A bib not named `refs.bib` is keyed on its directory too, with the
    stem as a qualifier, so it sits beside the directory's `refs.bib` run.
    """
    caches = tmp_path / "caches"
    write_bib(tmp_path / "paper")
    extra = write_bib(tmp_path / "paper", "extra.bib")

    result = runner.invoke(app, ["verify", str(extra), "--cache-dir", str(caches)])

    assert result.exit_code == 0, result.output
    out = caches / "paper-extra" / "openalex"
    assert (out / "results.json").is_file()
    assert captured["cache_path"] == out / "openalex.jsonl"


def test_verify_keeps_same_named_bibs_in_sibling_directories_apart(
    tmp_path: Path, captured
) -> None:
    """Two directories that each hold an `extra.bib` used to share one
    `extra/` output directory, the second run silently overwriting the first.
    """
    caches = tmp_path / "caches"
    a = write_bib(tmp_path / "paper-a", "extra.bib")
    b = write_bib(tmp_path / "paper-b", "extra.bib")

    for bib in (a, b):
        result = runner.invoke(app, ["verify", str(bib), "--cache-dir", str(caches)])
        assert result.exit_code == 0, result.output

    written = {p.parent.parent.name for p in caches.glob("*/openalex/results.json")}
    assert written == {"paper-a-extra", "paper-b-extra"}
    assert not (caches / "extra").exists()


def test_verify_resolves_a_relative_bib_path_before_naming_its_directory(
    tmp_path: Path, monkeypatch, captured
) -> None:
    """A bare `verify refs.bib` has no parent component; without resolving,
    the directory name came out empty and the output collapsed into
    `<root>/<source>/`.
    """
    write_bib(tmp_path / "paper")
    monkeypatch.chdir(tmp_path / "paper")

    result = runner.invoke(app, ["verify", "refs.bib"])

    assert result.exit_code == 0, result.output
    out = tmp_path / "paper" / "data" / "citefinder" / "paper" / "openalex"
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
    bib = write_bib(tmp_path / "paper", "thesis.bib")
    monkeypatch.setenv("CITEFINDER_CACHE_DIR", str(tmp_path / "caches"))

    result = runner.invoke(app, ["verify", str(bib), "--source", "crossref"])

    assert result.exit_code == 0, result.output
    out = tmp_path / "caches" / "paper-thesis" / "crossref"
    assert (out / "results.json").is_file()


def test_verify_out_beats_cache_dir(tmp_path: Path, captured) -> None:
    bib = write_bib(tmp_path / "paper")
    out, caches = tmp_path / "out", tmp_path / "caches"

    result = runner.invoke(
        app, ["verify", str(bib), "--out", str(out), "--cache-dir", str(caches)]
    )

    assert result.exit_code == 0, result.output
    assert (out / "results.json").is_file()
    assert not caches.exists()


def test_verify_out_expands_home_and_keeps_the_cache_beside_results(
    tmp_path: Path, monkeypatch, captured
) -> None:
    """A quoted `~` reaches the command unexpanded; `--out` is anchored like
    `--cache-dir`, so both output files land in the same expanded directory.
    """
    bib = write_bib(tmp_path / "paper")
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["verify", str(bib), "--out", "~/vout"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "vout" / "results.json").is_file()
    assert captured["cache_path"] == tmp_path / "vout" / "openalex.jsonl"


# --- user config file ---------------------------------------------------------


def test_load_configs_populates_env(tmp_path: Path) -> None:
    write_user_config(tmp_path, '[openalex]\nmailto = "you@example.com"\n')

    _load_configs()

    assert os.environ.get("OPENALEX_MAILTO") == "you@example.com"


def test_pyproject_with_a_non_table_citefinder_key_warns_and_falls_through(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A scalar `citefinder` under `[tool]` is a broken config, not an absent
    one: warn and fall through to the user config, the same as malformed
    TOML, rather than silently reading an empty table.
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    pyproject = project_dir / "pyproject.toml"
    pyproject.write_text('[tool]\ncitefinder = "oops"\n', encoding="utf-8")
    user_dir = tmp_path / "user"
    write_user_config(tmp_path, f'cache_dir = "{user_dir}"\n')
    monkeypatch.chdir(project_dir)

    _load_configs()  # must not raise

    assert find_project_config(project_dir) == pyproject
    with pytest.raises(ValueError, match="not a table"):
        load_config(pyproject)
    err = capsys.readouterr().err
    assert f"warning: ignoring {pyproject}" in err
    assert "not a table" in err
    assert os.environ["CITEFINDER_CACHE_DIR"] == str(user_dir)


def test_load_configs_ignores_a_malformed_toml(tmp_path: Path, capsys) -> None:
    """A credentials-file typo must not crash the whole CLI at import — with
    the skill body served by `citefinder skill`, that would zero out the
    skill's only delivery path on the machine."""
    write_user_config(tmp_path, "this is not valid toml [[[")

    _load_configs()  # must not raise

    assert "warning: ignoring" in capsys.readouterr().err


# --- project config discovery -------------------------------------------------


def write_project_config(directory: Path, body: str, pyproject: bool = False) -> Path:
    """`citefinder.toml`, or the same keys under `[tool.citefinder]`."""
    directory.mkdir(parents=True, exist_ok=True)
    if pyproject:
        path = directory / "pyproject.toml"
        body = "[tool.citefinder]\n" + body.replace("[", "[tool.citefinder.")
    else:
        path = directory / PROJECT_CONFIG_NAME
    path.write_text(body, encoding="utf-8")
    return path


def test_find_project_config_walks_up_to_citefinder_toml(tmp_path: Path) -> None:
    cfg = write_project_config(tmp_path, 'cache_dir = "data"\n')
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_project_config(deep) == cfg


def test_find_project_config_accepts_a_pyproject_tool_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert find_project_config(tmp_path) is None

    cfg = write_project_config(tmp_path, 'cache_dir = "data"\n', pyproject=True)
    assert find_project_config(tmp_path) == cfg


def test_find_project_config_prefers_the_nearer_then_the_dedicated_file(
    tmp_path: Path,
) -> None:
    outer = write_project_config(tmp_path, 'cache_dir = "outer"\n')
    inner = write_project_config(
        tmp_path / "inner", 'cache_dir = "inner"\n', pyproject=True
    )
    assert find_project_config(tmp_path / "inner") == inner
    assert find_project_config(tmp_path) == outer

    dedicated = write_project_config(tmp_path / "inner", 'cache_dir = "d"\n')
    assert find_project_config(tmp_path / "inner") == dedicated


def test_load_config_reads_the_tool_table_from_pyproject(tmp_path: Path) -> None:
    body = 'cache_dir = "data"\n[openalex]\nmailto = "p@example.com"\n'
    cfg = write_project_config(tmp_path, body, pyproject=True)
    assert load_config(cfg) == {
        "cache_dir": "data",
        "openalex": {"mailto": "p@example.com"},
    }

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert load_config(tmp_path / "pyproject.toml") == {}


def test_no_project_config_leaves_the_defaults(
    tmp_path: Path, monkeypatch, captured
) -> None:
    monkeypatch.chdir(tmp_path)

    _load_configs()

    assert "CITEFINDER_CACHE_DIR" not in os.environ
    result = runner.invoke(app, ["doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == default_cache_dir() / "openalex.jsonl"


LEVELS = ("flag", "env", "project", "user", "default")


@pytest.mark.parametrize("winner", LEVELS)
def test_precedence_matrix(winner: str, tmp_path: Path, monkeypatch, captured) -> None:
    """With every source at or below `winner` set, `winner`'s value reaches
    the client — for `cache_dir` and `mailto` alike."""
    active = LEVELS[LEVELS.index(winner) :]
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)

    def body(level: str) -> str:
        where = (tmp_path / level).as_posix()
        return f'cache_dir = "{where}"\n[openalex]\nmailto = "{level}@example.com"\n'

    if "user" in active:
        write_user_config(tmp_path, body("user"))
    if "project" in active:
        write_project_config(project_dir, body("project"))
    if "env" in active:
        monkeypatch.setenv("CITEFINDER_CACHE_DIR", str(tmp_path / "env"))
        monkeypatch.setenv("OPENALEX_MAILTO", "env@example.com")
    args = ["doi", "10.1/x"]
    if "flag" in active:
        args += ["--cache-dir", str(tmp_path / "flag"), "--mailto", "flag@example.com"]

    _load_configs()
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    if winner == "default":
        assert captured["cache_path"] == default_cache_dir() / "openalex.jsonl"
        assert captured["mailto"] is None
    else:
        assert captured["cache_path"] == tmp_path / winner / "openalex.jsonl"
        assert captured["mailto"] == f"{winner}@example.com"


def test_relative_project_cache_dir_anchors_to_the_config_dir(
    tmp_path: Path, monkeypatch, captured
) -> None:
    """`cache_dir = "data/citefinder"` means the repo's `data/citefinder`
    from any working directory inside it, for lookups and `verify` alike."""
    project_dir = tmp_path / "proj"
    write_project_config(project_dir, 'cache_dir = "data/citefinder"\n')
    deep = project_dir / "src" / "pkg"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    _load_configs()

    root = project_dir / "data" / "citefinder"
    assert os.environ["CITEFINDER_CACHE_DIR"] == str(root)
    result = runner.invoke(app, ["doi", "10.1/x"])
    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == root / "openalex.jsonl"

    bib = write_bib(deep)
    result = runner.invoke(app, ["verify", str(bib)])
    assert result.exit_code == 0, result.output
    assert (root / "pkg" / "openalex" / "results.json").is_file()


def test_project_api_key_is_ignored_with_a_warning(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project_dir = tmp_path / "proj"
    write_project_config(
        project_dir, '[openalex]\napi_key = "leaked"\nmailto = "p@example.com"\n'
    )
    write_user_config(tmp_path, '[openalex]\napi_key = "mine"\n')
    monkeypatch.chdir(project_dir)

    _load_configs()

    err = capsys.readouterr().err
    assert "warning: ignoring openalex.api_key in" in err
    assert str(project_dir / PROJECT_CONFIG_NAME) in err
    assert os.environ["OPENALEX_API_KEY"] == "mine"
    assert os.environ["OPENALEX_MAILTO"] == "p@example.com"


@pytest.mark.parametrize("pyproject", [False, True])
def test_malformed_project_config_warns_and_falls_through(
    tmp_path: Path, monkeypatch, capsys, pyproject: bool
) -> None:
    project_dir = tmp_path / "proj"
    write_project_config(project_dir, "this is not valid toml [[[", pyproject)
    write_user_config(tmp_path, f'cache_dir = "{(tmp_path / "user").as_posix()}"\n')
    monkeypatch.chdir(project_dir)

    _load_configs()  # must not raise

    assert "warning: ignoring" in capsys.readouterr().err
    assert os.environ["CITEFINDER_CACHE_DIR"] == str(tmp_path / "user")


@pytest.mark.parametrize("pyproject", [False, True])
def test_non_table_section_warns_and_falls_through(
    tmp_path: Path, monkeypatch, capsys, pyproject: bool
) -> None:
    """`openalex = 5` where `[openalex]` was meant is a broken file, not an
    absent section: warn and fall through rather than crash the CLI at
    import reading keys off a scalar."""
    project_dir = tmp_path / "proj"
    cfg = write_project_config(project_dir, "openalex = 5\n", pyproject)
    write_user_config(tmp_path, f'cache_dir = "{(tmp_path / "user").as_posix()}"\n')
    monkeypatch.chdir(project_dir)

    _load_configs()  # must not raise

    with pytest.raises(ValueError, match=r"\[openalex\] is not a table \(got int\)"):
        load_config(cfg)
    err = capsys.readouterr().err
    assert f"warning: ignoring {cfg}" in err
    assert os.environ["CITEFINDER_CACHE_DIR"] == str(tmp_path / "user")


# --- citefinder config --------------------------------------------------------


def config_rows(output: str) -> dict[str, tuple[str, str]]:
    """The settings table: label -> (source, value)."""
    rows: dict[str, tuple[str, str]] = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        # Header and path lines read `<word> <word>: ...`; settings don't.
        if len(parts) == 3 and not parts[1].endswith(":"):
            rows[parts[0]] = (parts[1], parts[2])
    return rows


def test_config_names_the_source_of_each_value(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "proj"
    project = write_project_config(
        project_dir, 'cache_dir = "data"\n[openalex]\nmailto = "p@example.com"\n'
    )
    user = write_user_config(
        tmp_path,
        '[openalex]\napi_key = "secret"\n[crossref]\nmailto = "u@example.com"\n',
    )
    monkeypatch.setenv("OPENALEX_MAX_RETRIES", "7")
    monkeypatch.chdir(project_dir)
    _load_configs()

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert f"project config: {project}" in out
    assert f"user config:    {user}" in out
    rows = config_rows(out)
    assert rows["cache_dir"] == ("project", str(project_dir / "data"))
    assert rows["openalex.mailto"] == ("project", "p@example.com")
    assert rows["openalex.api_key"] == ("user", "(set)")
    assert "secret" not in out
    assert rows["openalex.max_retries"] == ("env", "7")
    assert rows["openalex.min_interval"] == ("default", "0.1")
    assert rows["crossref.mailto"] == ("user", "u@example.com")
    assert rows["crossref.max_retries"] == ("default", "3")
    assert f"openalex cache:  {project_dir / 'data' / 'openalex.jsonl'}" in out
    assert f"crossref cache:  {project_dir / 'data' / 'crossref.jsonl'}" in out
    assert (
        f"verify output:   {project_dir / 'data'}/<bib-dir>[-<bib-stem>]/<source>/"
        in out
    )


def test_config_with_nothing_set_reports_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _load_configs()

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "project config: (none)" in out
    assert "user config:    (none:" in out
    rows = config_rows(out)
    assert rows["cache_dir"] == ("default", "(unset)")
    assert rows["openalex.mailto"] == ("default", "(none)")
    assert rows["openalex.min_interval"] == ("default", "0.1")
    assert rows["crossref.min_interval"] == ("default", "0")
    assert f"openalex cache:  {default_cache_dir() / 'openalex.jsonl'}" in out
    assert (
        f"verify output:   {tmp_path / 'data' / 'citefinder'}/<bib-dir>[-<bib-stem>]/"
        in out
    )


def test_config_reports_a_cache_dir_flag_anchored_to_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["config", "--cache-dir", "caches"])

    assert result.exit_code == 0, result.output
    rows = config_rows(result.output)
    assert rows["cache_dir"] == ("flag", str(tmp_path / "caches"))
    assert (
        f"verify output:   {tmp_path / 'caches'}/<bib-dir>[-<bib-stem>]/<source>/"
        in result.output
    )


def test_config_verify_output_is_where_verify_writes(tmp_path: Path, captured) -> None:
    """`config` and `verify` derive the output root from one helper, so the
    path `config` prints is the one `verify` writes to.
    """
    write_project_config(tmp_path, 'cache_dir = "caches"\n')
    _load_configs()
    shown = runner.invoke(app, ["config"]).output
    line = next(ln for ln in shown.splitlines() if ln.startswith("verify output:"))
    template = line.split(":", 1)[1].strip()
    expected = Path(
        template.replace("<bib-dir>[-<bib-stem>]", "paper").replace(
            "<source>", "openalex"
        )
    )

    bib = write_bib(tmp_path / "paper")
    result = runner.invoke(app, ["verify", str(bib)])

    assert result.exit_code == 0, result.output
    assert expected == tmp_path / "caches" / "paper" / "openalex"
    assert (expected / "results.json").is_file()
    assert captured["cache_path"] == expected / "openalex.jsonl"
