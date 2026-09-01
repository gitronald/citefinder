"""Tests for the CLI's `~/.config/citefinder/config.toml` loading."""

import os

from citefinder.cli import _load_user_config


def test_load_user_config_populates_env(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "citefinder" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[openalex]\nmailto = "you@example.com"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("OPENALEX_MAILTO", raising=False)

    _load_user_config()

    assert os.environ.get("OPENALEX_MAILTO") == "you@example.com"


def test_load_user_config_ignores_a_malformed_toml(
    tmp_path, monkeypatch, capsys
) -> None:
    """A credentials-file typo must not crash the whole CLI at import — with
    the skill body served by `citefinder skill`, that would zero out the
    skill's only delivery path on the machine."""
    cfg = tmp_path / "citefinder" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("this is not valid toml [[[", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    _load_user_config()  # must not raise

    assert "warning: ignoring" in capsys.readouterr().err
