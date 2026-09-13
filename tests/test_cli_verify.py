"""Tests for the `citefinder verify` CLI: output layout, source and
mailto selection, and the shared-cache fallback."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from citefinder.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(config_env: None) -> None:
    """Every test here starts from the sandboxed config environment."""


# --- verify ------------------------------------------------------------------


def test_verify_default_output_is_under_cwd(
    tmp_path: Path, monkeypatch, captured, write_bib
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
    tmp_path: Path, captured, write_bib
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
    tmp_path: Path, captured, write_bib
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


def test_verify_anchors_a_relative_bib_path_before_naming_its_directory(
    tmp_path: Path, monkeypatch, captured, write_bib
) -> None:
    """A bare `verify refs.bib` has no parent component; without anchoring
    it to the working directory first, the directory name came out empty and
    the output collapsed into `<root>/<source>/`.
    """
    write_bib(tmp_path / "paper")
    monkeypatch.chdir(tmp_path / "paper")

    result = runner.invoke(app, ["verify", "refs.bib"])

    assert result.exit_code == 0, result.output
    out = tmp_path / "paper" / "data" / "citefinder" / "paper" / "openalex"
    assert (out / "results.json").is_file()
    assert captured["cache_path"] == out / "openalex.jsonl"


def test_verify_collapses_dot_dot_before_naming_the_directory(
    tmp_path: Path, monkeypatch, captured, write_bib
) -> None:
    """`verify ../refs.bib` from a subdirectory names the output after the
    bib's real directory, not `..`.
    """
    write_bib(tmp_path / "paper")
    (tmp_path / "paper" / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "paper" / "sub")
    caches = tmp_path / "caches"

    result = runner.invoke(app, ["verify", "../refs.bib", "--cache-dir", str(caches)])

    assert result.exit_code == 0, result.output
    assert (caches / "paper" / "openalex" / "results.json").is_file()
    assert [p.name for p in caches.iterdir()] == ["paper"]


def test_verify_keeps_a_symlinked_directory_under_its_own_name(
    tmp_path: Path, captured, write_bib
) -> None:
    """A bib reached through a symlinked directory is keyed on the name the
    user pointed at, not the link's target, so the cache written under that
    name before this layout change is the one that is read.
    """
    write_bib(tmp_path / "2026-foo-final")
    link = tmp_path / "paper"
    link.symlink_to(tmp_path / "2026-foo-final", target_is_directory=True)
    caches = tmp_path / "caches"

    result = runner.invoke(
        app, ["verify", str(link / "refs.bib"), "--cache-dir", str(caches)]
    )

    assert result.exit_code == 0, result.output
    out = caches / "paper" / "openalex"
    assert (out / "results.json").is_file()
    assert captured["cache_path"] == out / "openalex.jsonl"
    assert not (caches / "2026-foo-final").exists()


def test_verify_output_derives_from_cache_dir(
    tmp_path: Path, captured, write_bib
) -> None:
    bib = write_bib(tmp_path / "paper")
    caches = tmp_path / "caches"

    result = runner.invoke(app, ["verify", str(bib), "--cache-dir", str(caches)])

    assert result.exit_code == 0, result.output
    assert (caches / "paper" / "openalex" / "results.json").is_file()
    assert captured["cache_path"] == caches / "paper" / "openalex" / "openalex.jsonl"


def test_verify_honors_env_cache_dir_and_source(
    tmp_path: Path, monkeypatch, captured, write_bib
) -> None:
    bib = write_bib(tmp_path / "paper", "thesis.bib")
    monkeypatch.setenv("CITEFINDER_CACHE_DIR", str(tmp_path / "caches"))

    result = runner.invoke(app, ["verify", str(bib), "--source", "crossref"])

    assert result.exit_code == 0, result.output
    out = tmp_path / "caches" / "paper-thesis" / "crossref"
    assert (out / "results.json").is_file()


def test_verify_out_beats_cache_dir(tmp_path: Path, captured, write_bib) -> None:
    bib = write_bib(tmp_path / "paper")
    out, caches = tmp_path / "out", tmp_path / "caches"

    result = runner.invoke(
        app, ["verify", str(bib), "--out", str(out), "--cache-dir", str(caches)]
    )

    assert result.exit_code == 0, result.output
    assert (out / "results.json").is_file()
    assert not caches.exists()


def test_verify_out_expands_home_and_keeps_the_cache_beside_results(
    tmp_path: Path, monkeypatch, captured, write_bib
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


def test_verify_source_accepts_any_case_and_rejects_unknowns(
    tmp_path: Path, captured, write_bib
) -> None:
    # `case_sensitive=False` only takes effect on a choice-typed option; as a
    # plain `str` it was a no-op and `--source OpenAlex` was refused.
    bib = write_bib(tmp_path / "paper")
    out = str(tmp_path / "out")
    result = runner.invoke(
        app, ["verify", str(bib), "--source", "OpenAlex", "--out", out]
    )
    assert result.exit_code == 0, result.output
    assert "Source: openalex" in result.output

    result = runner.invoke(app, ["verify", str(bib), "--source", "bogus", "--out", out])
    assert result.exit_code == 2
    assert "bogus" in result.output


def test_verify_mailto_follows_the_chosen_source(
    tmp_path: Path, monkeypatch, captured, write_bib
) -> None:
    # `verify` used to hand `--mailto` only to OpenAlex; Crossref runs sent
    # no polite-pool email at all, whatever the flag, env, or config said.
    bib = write_bib(tmp_path / "paper")
    out = str(tmp_path / "out")
    monkeypatch.setenv("OPENALEX_MAILTO", "oa@example.com")
    monkeypatch.setenv("CROSSREF_MAILTO", "cr@example.com")

    runner.invoke(app, ["verify", str(bib), "--source", "crossref", "--out", out])
    assert captured["mailto"] == "cr@example.com"

    runner.invoke(app, ["verify", str(bib), "--source", "openalex", "--out", out])
    assert captured["mailto"] == "oa@example.com"

    args = ["verify", str(bib), "--source", "crossref", "--out", out]
    runner.invoke(app, [*args, "--mailto", "flag@example.com"])
    assert captured["mailto"] == "flag@example.com"


# --- verify's shared-cache fallback -------------------------------------------


def test_verify_falls_back_to_the_shared_cache(
    tmp_path: Path, captured, write_bib
) -> None:
    bib = write_bib(tmp_path / "paper")
    caches = tmp_path / "caches"

    result = runner.invoke(app, ["verify", str(bib), "--cache-dir", str(caches)])

    assert result.exit_code == 0, result.output
    assert captured["fallback_cache"] == caches / "openalex.jsonl"
    assert f"Fallback: {caches / 'openalex.jsonl'} (0 entries)" in result.output


def test_verify_no_fallback_flag_pins_one_file(
    tmp_path: Path, captured, write_bib
) -> None:
    bib = write_bib(tmp_path / "paper")
    args = ["verify", str(bib), "--cache-dir", str(tmp_path), "--no-fallback"]

    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert captured["fallback_cache"] is None
    assert "Fallback:" not in result.output


def test_verify_out_at_the_cache_root_does_not_layer_a_file_over_itself(
    tmp_path: Path, captured, write_bib
) -> None:
    bib = write_bib(tmp_path / "paper")
    caches = tmp_path / "caches"

    result = runner.invoke(
        app, ["verify", str(bib), "--out", str(caches), "--cache-dir", str(caches)]
    )

    assert result.exit_code == 0, result.output
    assert captured["cache_path"] == caches / "openalex.jsonl"
    assert captured["fallback_cache"] is None


def test_verify_fallback_hit_makes_no_request_and_writes_only_the_run(
    tmp_path: Path, monkeypatch
) -> None:
    from citefinder.cache import JsonlCache

    caches = tmp_path / "caches"
    shared = JsonlCache(caches / "openalex.jsonl")
    shared.put("https://api.openalex.org/works/doi:10.1/dead", None)
    before = shared.path.read_bytes()
    bib = tmp_path / "paper" / "refs.bib"
    bib.parent.mkdir()
    bib.write_text("@article{k1,\n  title = {A Paper},\n  doi = {10.1/dead},\n}\n")

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("verify reached the network")

    monkeypatch.setattr("requests.Session.get", no_network)
    result = runner.invoke(app, ["verify", str(bib), "--cache-dir", str(caches)])

    assert result.exit_code == 0, result.output
    assert "(1 entries pre-loaded)" in result.output
    assert "(1 entries)" in result.output
    assert "[hit]" in result.output
    assert "0 network call(s), 1 cache hit(s)" in result.output
    assert shared.path.read_bytes() == before
    assert not (caches / "paper" / "openalex" / "openalex.jsonl").exists()


def test_verify_counts_exclude_the_rate_limit_snapshot(
    tmp_path: Path, monkeypatch, write_bib
) -> None:
    """A shared cache holding only quota bookkeeping is `0 entries`, since
    the README reads a nonzero fallback count as "something was merged"."""
    from citefinder.cache import JsonlCache
    from citefinder.openalex import OpenAlexClient

    caches = tmp_path / "caches"
    key = OpenAlexClient.rate_limit_key
    assert key is not None
    JsonlCache(caches / "openalex.jsonl").put(key, {"headers": {}, "ts": 1.0})
    bib = write_bib(tmp_path / "paper")
    monkeypatch.setattr(
        "requests.Session.get", lambda *a, **k: (_ for _ in ()).throw(OSError("off"))
    )

    result = runner.invoke(app, ["verify", str(bib), "--cache-dir", str(caches)])

    assert "(0 entries pre-loaded)" in result.output
    assert f"Fallback: {caches / 'openalex.jsonl'} (0 entries)" in result.output
