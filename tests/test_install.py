"""Tests for materializing the bundled `use-citefinder` skill."""

import os
import subprocess
import sys
import zipfile
from importlib import resources
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from typer.testing import CliRunner

from citefinder import install as install_mod
from citefinder.cli import app

VERSION = "9.9.9"
runner = CliRunner()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A repo root at `tmp_path/repo` with `$HOME` pointed at `tmp_path/home`.

    Both install modes write inside `tmp_path`, so a global install in a test
    can never touch the developer's real `~/.claude/`.
    """
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(repo)
    return repo


# --- bundled body -----------------------------------------------------------


def test_body_is_loadable_as_package_data() -> None:
    """The body must be reachable as package data, not just as a repo file."""
    resource = resources.files("citefinder.prompts").joinpath("skill.md")
    assert resource.is_file()
    assert install_mod.load_body() == resource.read_text(encoding="utf-8")


def test_built_wheel_ships_the_bundled_body(tmp_path, monkeypatch) -> None:
    """Guards the hatchling include.

    An editable install resolves `citefinder.prompts` straight to the repo, so
    it would keep working even if the build config stopped shipping the body —
    and `install` would then fail only for people who installed from PyPI.
    Building a real wheel is the only check that catches that.
    """
    hatchling_build = pytest.importorskip("hatchling.build")
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    wheel = hatchling_build.build_wheel(str(tmp_path))
    with zipfile.ZipFile(tmp_path / wheel) as archive:
        names = archive.namelist()
        assert "citefinder/prompts/skill.md" in names
        assert archive.read("citefinder/prompts/skill.md").decode() == (
            install_mod.load_body()
        )


def test_body_has_skill_frontmatter() -> None:
    front, body = install_mod.split_frontmatter(install_mod.load_body())
    assert front.startswith("---\n")
    assert "name: use-citefinder" in front
    assert "description:" in front
    assert body.lstrip().startswith("#")


def test_split_frontmatter_reconstructs_input() -> None:
    text = "---\nname: x\n---\n\nbody\n"
    front, body = install_mod.split_frontmatter(text)
    assert front == "---\nname: x\n---\n"
    assert body == "\nbody\n"
    assert front + body == text


def test_split_frontmatter_without_frontmatter() -> None:
    assert install_mod.split_frontmatter("# no frontmatter\n") == (
        "",
        "# no frontmatter\n",
    )


# --- render -----------------------------------------------------------------


def test_stub_carries_the_frontmatter_verbatim() -> None:
    """The triggers are the one thing Claude Code must read off disk."""
    front, _ = install_mod.split_frontmatter(install_mod.load_body())
    rendered = install_mod.render_stub(VERSION, "global")
    assert rendered.startswith(front)
    assert "name: use-citefinder" in front


def test_stub_does_not_duplicate_the_body() -> None:
    """The whole point of the stub: the instructions live in exactly one place."""
    rendered = install_mod.render_stub(VERSION, "global")
    body = install_mod.skill_body()

    # Nothing of substance from the body is copied into the stub...
    for paragraph in body.split("\n\n"):
        line = paragraph.strip()
        if len(line) > 80:
            assert line not in rendered
    # ...and the stub stays far smaller than the body it points at.
    assert len(rendered) < len(body) / 3


def test_stub_points_at_the_skill_command() -> None:
    assert "citefinder skill" in install_mod.render_stub(VERSION, "global")
    assert "uv run citefinder skill" in install_mod.render_stub(VERSION, "local")


def test_stub_names_a_mode_correct_check_command() -> None:
    """A local stub must say `--local` — bare `--check` prefers the global
    copy, so the bare command could report on a different file entirely."""
    assert "citefinder install --check" in install_mod.render_stub(VERSION, "global")
    assert "uv run citefinder install --local --check" in install_mod.render_stub(
        VERSION, "local"
    )


def test_skill_body_strips_frontmatter() -> None:
    body = install_mod.skill_body()
    assert not body.startswith("---")
    assert "name: use-citefinder" not in body
    assert body.startswith("# ")
    assert body.endswith("\n")
    # It is the bundled body's content, just without the metadata block.
    assert body.strip() in install_mod.load_body()


def test_render_stamp_carries_version_and_mode() -> None:
    for mode in ("global", "local"):
        rendered = install_mod.render_stub(VERSION, mode)
        assert f"generated by citefinder {VERSION} (mode={mode})" in rendered


def test_render_stamp_names_a_mode_correct_repair_command() -> None:
    assert "run: citefinder install --force" in install_mod.render_stub(
        VERSION, "global"
    )
    assert "run: uv run citefinder install --local --force" in install_mod.render_stub(
        VERSION, "local"
    )


def test_install_command_variants() -> None:
    assert install_mod.install_command("global") == "citefinder install"
    assert install_mod.install_command("global", force=True) == (
        "citefinder install --force"
    )
    assert install_mod.install_command("local") == "uv run citefinder install --local"
    assert install_mod.install_command("local", force=True) == (
        "uv run citefinder install --local --force"
    )


def test_invocation_and_skill_command_per_mode() -> None:
    assert install_mod.invocation("global") == "citefinder"
    assert install_mod.invocation("local") == "uv run citefinder"
    assert install_mod.skill_command("global") == "citefinder skill"
    assert install_mod.skill_command("local") == "uv run citefinder skill"
    assert install_mod.check_command("global") == "citefinder install --check"
    assert install_mod.check_command("local") == (
        "uv run citefinder install --local --check"
    )


# --- path resolution --------------------------------------------------------


def test_skill_path_per_mode(sandbox) -> None:
    repo = sandbox
    assert install_mod.skill_path(repo, "local") == repo / install_mod.SKILL_REL
    assert install_mod.skill_path(repo, "global") == Path.home() / install_mod.SKILL_REL
    assert install_mod.skill_path(repo, "local") != install_mod.skill_path(
        repo, "global"
    )


def test_write_skill_creates_parents_and_writes_render(sandbox) -> None:
    repo = sandbox
    for mode in ("global", "local"):
        path = install_mod.write_skill(repo, VERSION, mode)
        assert path == install_mod.skill_path(repo, mode)
        assert path.read_text(encoding="utf-8") == install_mod.render_stub(
            VERSION, mode
        )


def test_resolve_installed_prefers_global(sandbox) -> None:
    repo = sandbox
    assert install_mod.resolve_installed(repo) is None

    install_mod.write_skill(repo, VERSION, "local")
    assert install_mod.resolve_installed(repo) == (
        install_mod.skill_path(repo, "local"),
        "local",
    )

    install_mod.write_skill(repo, VERSION, "global")
    assert install_mod.resolve_installed(repo) == (
        install_mod.skill_path(repo, "global"),
        "global",
    )


def test_find_repo_root_walks_up_to_a_marker(tmp_path) -> None:
    repo = tmp_path / "repo"
    sub = repo / "docs" / "deep"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()
    assert install_mod.find_repo_root(sub) == repo


def test_find_repo_root_falls_back_to_the_start(tmp_path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    assert install_mod.find_repo_root(bare) == bare


# --- drift check ------------------------------------------------------------


def test_check_missing_then_ok(sandbox) -> None:
    repo = sandbox
    assert install_mod.check(repo, VERSION) == "missing"

    install_mod.write_skill(repo, VERSION, "local")
    assert install_mod.check(repo, VERSION) == "ok"


def test_check_ignores_a_version_only_bump(sandbox) -> None:
    """A release changes only the stamp's version token; flagging that as
    drift would fire after every routine upgrade and train users to ignore
    `--check`. Drift means the rendered *content* changed."""
    repo = sandbox
    install_mod.write_skill(repo, VERSION, "local")
    assert install_mod.check(repo, "9.9.10") == "ok"


def test_check_judges_a_moved_copy_by_its_location(sandbox) -> None:
    """A local-rendered stub carried to the global path embeds `uv run`
    commands that are wrong for global use — the location decides."""
    repo = sandbox
    path = install_mod.skill_path(repo, "global")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(install_mod.render_stub(VERSION, "local"), encoding="utf-8")
    assert install_mod.check(repo, VERSION) == "drifted"


def test_check_detects_a_hand_edited_body(sandbox) -> None:
    repo = sandbox
    path = install_mod.write_skill(repo, VERSION, "local")
    path.write_text(
        path.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8"
    )
    assert install_mod.check(repo, VERSION) == "drifted"


def test_check_reads_an_unstamped_file_as_drifted(sandbox) -> None:
    repo = sandbox
    path = install_mod.skill_path(repo, "local")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: use-citefinder\n---\n\nhand-written\n", encoding="utf-8"
    )
    assert install_mod.check(repo, VERSION) == "drifted"


def test_check_mode_targets_one_location(sandbox) -> None:
    repo = sandbox
    install_mod.write_skill(repo, VERSION, "global")
    assert install_mod.check_mode(repo, VERSION, "global") == "ok"
    assert install_mod.check_mode(repo, VERSION, "local") == "missing"


def test_check_treats_a_squatting_directory_as_drifted(sandbox) -> None:
    repo = sandbox
    (repo / install_mod.SKILL_REL).mkdir(parents=True)
    assert install_mod.check_mode(repo, VERSION, "local") == "drifted"
    assert install_mod.check(repo, VERSION) == "drifted"


def test_check_treats_a_dangling_symlink_as_drifted(sandbox) -> None:
    """`exists()` follows the link and says nothing is there, but the path is
    occupied — report it, and never hint the plain install that would write
    through it."""
    repo = sandbox
    path = repo / install_mod.SKILL_REL
    path.parent.mkdir(parents=True)
    path.symlink_to(repo / "gone.md")
    assert install_mod.check_mode(repo, VERSION, "local") == "drifted"
    assert install_mod.check(repo, VERSION) == "drifted"


# --- overwrite guard --------------------------------------------------------


def test_is_generated_only_for_our_own_artifact(sandbox) -> None:
    repo = sandbox
    generated = install_mod.write_skill(repo, VERSION, "local")
    assert install_mod.is_generated(generated)

    hand_written = repo / "hand.md"
    hand_written.write_text(
        "---\nname: use-citefinder\n---\n\nmine\n", encoding="utf-8"
    )
    assert not install_mod.is_generated(hand_written)

    assert not install_mod.is_generated(repo / "absent.md")

    a_dir = repo / "dir.md"
    a_dir.mkdir()
    assert not install_mod.is_generated(a_dir)


def test_is_generated_rejects_a_symlink(sandbox) -> None:
    repo = sandbox
    target = repo / "target.md"
    target.write_text(install_mod.render_stub(VERSION, "local"), encoding="utf-8")
    link = repo / "link.md"
    link.symlink_to(target)
    assert not install_mod.is_generated(link)


def test_is_generated_needs_the_stamp_line_not_a_prose_mention(sandbox) -> None:
    """Quoting the README's stamp example in a hand-written skill must not
    make the file overwritable without --force."""
    repo = sandbox
    hand = repo / "hand.md"
    hand.write_text(
        "---\nname: use-citefinder\n---\n\nMy notes: the stamp looks like "
        "'<!-- generated by citefinder 0.4.4 -->' when installed.\n",
        encoding="utf-8",
    )
    assert not install_mod.is_generated(hand)


def test_write_skill_replaces_a_symlink_instead_of_writing_through_it(
    sandbox,
) -> None:
    repo = sandbox
    target = repo / "elsewhere.md"
    target.write_text("theirs\n", encoding="utf-8")
    path = repo / install_mod.SKILL_REL
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    install_mod.write_skill(repo, VERSION, "local")
    assert not path.is_symlink()
    assert install_mod.is_generated(path)
    assert target.read_text(encoding="utf-8") == "theirs\n"


# --- CLI --------------------------------------------------------------------


def test_cli_skill_prints_the_body_from_the_package() -> None:
    result = runner.invoke(app, ["skill"])
    assert result.exit_code == 0
    assert result.stdout == install_mod.skill_body()
    # The instructions, not the trigger metadata.
    assert not result.stdout.startswith("---")
    assert "Four core operations" in result.stdout


def test_cli_skill_needs_no_installed_stub(sandbox) -> None:
    """The body is served from the package, so it works before any install."""
    assert install_mod.resolve_installed(sandbox) is None
    result = runner.invoke(app, ["skill"])
    assert result.exit_code == 0
    assert len(result.stdout) > 1000


def test_cli_install_local_writes_the_repo_copy(sandbox) -> None:
    repo = sandbox
    result = runner.invoke(app, ["install", "--local"])
    assert result.exit_code == 0
    path = install_mod.skill_path(repo, "local")
    assert path.is_file()
    assert install_mod.is_generated(path)
    assert "mode=local" in path.read_text(encoding="utf-8")


def test_installed_stub_holds_no_instructions(sandbox) -> None:
    """Nothing on disk duplicates the package's body — the drift can't happen."""
    runner.invoke(app, ["install", "--local"])
    on_disk = install_mod.skill_path(sandbox, "local").read_text(encoding="utf-8")
    assert "Four core operations" not in on_disk
    assert "lookup_book_chapter" not in on_disk
    assert len(on_disk) < 2500


def test_cli_install_defaults_to_global(sandbox) -> None:
    repo = sandbox
    result = runner.invoke(app, ["install"])
    assert result.exit_code == 0
    assert install_mod.skill_path(repo, "global").is_file()
    assert not install_mod.skill_path(repo, "local").exists()


def test_cli_check_reports_each_status(sandbox) -> None:
    missing = runner.invoke(app, ["install", "--local", "--check"])
    assert missing.exit_code == 1
    assert "missing" in missing.stdout

    runner.invoke(app, ["install", "--local"])
    ok = runner.invoke(app, ["install", "--local", "--check"])
    assert ok.exit_code == 0
    assert "skill: ok" in ok.stdout

    path = install_mod.skill_path(sandbox, "local")
    path.write_text(path.read_text(encoding="utf-8") + "edit\n", encoding="utf-8")
    drifted = runner.invoke(app, ["install", "--local", "--check"])
    assert drifted.exit_code == 1
    assert "drifted" in drifted.stdout


def test_cli_check_writes_nothing(sandbox) -> None:
    repo = sandbox
    runner.invoke(app, ["install", "--local", "--check"])
    assert not install_mod.skill_path(repo, "local").exists()
    assert not install_mod.skill_path(repo, "global").exists()


def test_cli_check_auto_resolves_the_installed_mode(sandbox) -> None:
    runner.invoke(app, ["install", "--local"])
    result = runner.invoke(app, ["install", "--check"])
    assert result.exit_code == 0
    assert "skill: ok" in result.stdout
    assert str(install_mod.skill_path(sandbox, "local")) in result.stdout


def test_cli_refuses_to_overwrite_an_unstamped_file(sandbox) -> None:
    repo = sandbox
    path = install_mod.skill_path(repo, "local")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = "---\nname: use-citefinder\n---\n\nhand-written\n"
    path.write_text(original, encoding="utf-8")

    refused = runner.invoke(app, ["install", "--local"])
    assert refused.exit_code == 1
    assert path.read_text(encoding="utf-8") == original

    forced = runner.invoke(app, ["install", "--local", "--force"])
    assert forced.exit_code == 0
    assert install_mod.is_generated(path)


def test_cli_reinstall_over_a_generated_copy_needs_no_force(sandbox) -> None:
    """The upgrade path: refreshing our own artifact is never a clobber."""
    repo = sandbox
    runner.invoke(app, ["install", "--local"])
    path = install_mod.skill_path(repo, "local")
    path.write_text(path.read_text(encoding="utf-8") + "stale\n", encoding="utf-8")

    result = runner.invoke(app, ["install", "--local"])
    assert result.exit_code == 0
    assert "stale" not in path.read_text(encoding="utf-8")


def test_cli_install_never_writes_through_a_symlink(sandbox) -> None:
    """A symlink at the target — dangling or not — must not route the write
    to a foreign file: plain install refuses, --force replaces the link."""
    repo = sandbox
    target = repo / "elsewhere.md"
    path = repo / install_mod.SKILL_REL
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    refused = runner.invoke(app, ["install", "--local"])
    assert refused.exit_code == 1
    assert not target.exists()  # the dangling link created nothing

    target.write_text("theirs\n", encoding="utf-8")
    forced = runner.invoke(app, ["install", "--local", "--force"])
    assert forced.exit_code == 0
    assert not path.is_symlink()
    assert install_mod.is_generated(path)
    assert target.read_text(encoding="utf-8") == "theirs\n"


def test_cli_install_local_from_a_subdirectory_targets_the_repo_root(
    sandbox, monkeypatch
) -> None:
    """Claude Code loads skills from the repo root at startup; an install run
    from `docs/` must not bury the stub in `docs/.claude/`."""
    repo = sandbox
    (repo / ".git").mkdir()
    sub = repo / "docs"
    sub.mkdir()
    monkeypatch.chdir(sub)

    result = runner.invoke(app, ["install", "--local"])
    assert result.exit_code == 0
    assert install_mod.skill_path(repo, "local").is_file()
    assert not (sub / ".claude").exists()


def test_cli_install_reports_a_squatting_parent_file_cleanly(sandbox) -> None:
    """A plain file where `.claude/` should be must give a clean error, not a
    NotADirectoryError traceback."""
    (Path.home() / ".claude").write_text("not a directory\n", encoding="utf-8")
    result = runner.invoke(app, ["install"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, OSError)


def test_cli_install_force_reports_a_squatting_directory_cleanly(sandbox) -> None:
    """A directory at SKILL.md itself under --force must give a clean error,
    not an IsADirectoryError traceback."""
    repo = sandbox
    (repo / install_mod.SKILL_REL).mkdir(parents=True)
    result = runner.invoke(app, ["install", "--local", "--force"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, OSError)


def test_cli_install_degrades_when_distribution_metadata_is_absent(
    sandbox, monkeypatch
) -> None:
    """Mirrors `_default_user_agent`'s 0.0.0 fallback in `_base.py`."""

    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("citefinder.cli.metadata_version", _raise)
    result = runner.invoke(app, ["install", "--local"])
    assert result.exit_code == 0
    text = install_mod.skill_path(sandbox, "local").read_text(encoding="utf-8")
    assert "generated by citefinder 0.0.0" in text


def test_cli_skill_survives_a_non_utf8_stdout() -> None:
    """The body contains characters outside cp1252 (arrows, >=); a legacy
    console or redirect must get degraded characters, not a
    UnicodeEncodeError traceback — the stub makes this command the only
    delivery path for the instructions."""
    result = subprocess.run(
        [sys.executable, "-c", "from citefinder.cli import app; app(['skill'])"],
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"Four core operations" in result.stdout
