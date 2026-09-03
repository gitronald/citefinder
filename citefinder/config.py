"""Cache-path resolution and the config-file layer behind the CLI.

`resolve_cache_path` is the one rule for where a source's JSONL cache
lives, shared by the CLI and by wrappers that build clients themselves.
The config-file helpers are what `citefinder.cli` reads at startup; the
library never loads a config file on its own — clients take an explicit
`cache_path`, `mailto`, and `api_key`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    """`~/.cache/citefinder`, the root every cache path derives from when
    nothing sets `cache_dir`.

    A function rather than a module constant so that importing the package
    never touches the home directory: `Path.home()` raises when no home can
    be determined (an unnamed UID in a container, say), and a caller that
    passes its own `cache_dir` never needs it.
    """
    return Path.home() / ".cache" / "citefinder"


def resolve_cache_path(source: str, cache_dir: str | Path | None = None) -> Path:
    """Path of `source`'s JSONL cache: `<cache_dir>/<source>.jsonl`.

    `cache_dir` defaults to `~/.cache/citefinder`; a leading `~` is
    expanded. A relative `cache_dir` comes back relative — what it is
    anchored to is the caller's call. The CLI resolves a flag or env value
    against the working directory and a config-file value against the
    file's own directory, so `cache_dir = "data/citefinder"` in a
    project's config means that project's `data/citefinder` wherever the
    command runs from.
    """
    root = default_cache_dir() if cache_dir is None else Path(cache_dir).expanduser()
    return root / f"{source}.jsonl"


PROJECT_CONFIG_NAME = "citefinder.toml"

# Env name -> (section, key) in a config file; a `None` section is the
# file's top level. The CLI reads config files into these env names, so a
# config value and a shell/`.env` value reach the commands the same way.
ENV_KEYS: dict[str, tuple[str | None, str]] = {
    "CITEFINDER_CACHE_DIR": (None, "cache_dir"),
    "OPENALEX_API_KEY": ("openalex", "api_key"),
    "OPENALEX_MAILTO": ("openalex", "mailto"),
    "OPENALEX_MAX_RETRIES": ("openalex", "max_retries"),
    "OPENALEX_MIN_INTERVAL": ("openalex", "min_interval"),
    "CROSSREF_MAILTO": ("crossref", "mailto"),
    "CROSSREF_MAX_RETRIES": ("crossref", "max_retries"),
    "CROSSREF_MIN_INTERVAL": ("crossref", "min_interval"),
}

# The sections a config file may carry, each of which must be a table.
SECTIONS: tuple[str, ...] = tuple(
    dict.fromkeys(section for section, _ in ENV_KEYS.values() if section is not None)
)


def user_config_path() -> Path:
    """`~/.config/citefinder/config.toml`, honoring `$XDG_CONFIG_HOME`."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_dir = Path(xdg) if xdg else Path.home() / ".config"
    return config_dir / "citefinder" / "config.toml"


def find_project_config(start: Path | None = None) -> Path | None:
    """The nearest project config at or above `start` (the working directory).

    Walks up the way `.env` discovery does, for the first `citefinder.toml`
    or `pyproject.toml` carrying a `[tool.citefinder]` table; when both sit
    in one directory the dedicated file wins. A `pyproject.toml` that fails
    to parse is returned as the candidate rather than skipped, so the
    caller's `load_config` surfaces the error instead of silently reading
    a config further up.
    """
    here = (Path.cwd() if start is None else start).resolve()
    for directory in (here, *here.parents):
        dedicated = directory / PROJECT_CONFIG_NAME
        if dedicated.is_file():
            return dedicated
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file() and _declares_citefinder(pyproject):
            return pyproject
    return None


def _declares_citefinder(pyproject: Path) -> bool:
    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return True
    tool = data.get("tool")
    return isinstance(tool, dict) and "citefinder" in tool


def load_config(path: Path) -> dict[str, Any]:
    """Parse a config file; for a `pyproject.toml`, its `[tool.citefinder]`.

    Raises `OSError`, `tomllib.TOMLDecodeError`, or `ValueError` (a
    `tool.citefinder`, or one of `SECTIONS`, that is not a table) — how to
    report a broken file is the caller's decision (the CLI warns and falls
    through).
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if path.name != "pyproject.toml":
        config = data
    else:
        tool = data.get("tool")
        table = tool.get("citefinder") if isinstance(tool, dict) else None
        if table is None:
            return {}
        if not isinstance(table, dict):
            # A scalar or array under `[tool]` is a broken config, not an
            # absent one: raise so the caller warns instead of silently
            # reading an empty table over a real config further up the tree.
            kind = type(table).__name__
            raise ValueError(f"[tool.citefinder] is not a table (got {kind})")
        config = table
    for section in SECTIONS:
        value = config.get(section)
        if value is not None and not isinstance(value, dict):
            # `openalex = 5` where `[openalex]` was meant: the same kind of
            # broken file, raised here so the caller never reads keys off a
            # scalar.
            kind = type(value).__name__
            raise ValueError(f"[{section}] is not a table (got {kind})")
    return config
