"""Cache-path resolution and the config-file layer behind the CLI.

The library never loads a config file itself; clients take explicit values.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    """`~/.cache/citefinder`, the default cache root.

    A function, not a constant: `Path.home()` can raise, and import must not.
    """
    return Path.home() / ".cache" / "citefinder"


def resolve_cache_path(source: str, cache_dir: str | Path | None = None) -> Path:
    """Path of `source`'s JSONL cache: `<cache_dir>/<source>.jsonl`.

    `cache_dir` defaults to `~/.cache/citefinder`; a leading `~` is
    expanded. A relative `cache_dir` stays relative; anchoring it is the
    caller's job.
    """
    root = default_cache_dir() if cache_dir is None else Path(cache_dir).expanduser()
    return root / f"{source}.jsonl"


PROJECT_CONFIG_NAME = "citefinder.toml"

# Env name -> (section, key) in a config file; a `None` section is top level.
ENV_KEYS: dict[str, tuple[str | None, str]] = {
    "CITEFINDER_CACHE_DIR": (None, "cache_dir"),
    "OPENALEX_API_KEY": ("openalex", "api_key"),
    "OPENALEX_MAILTO": ("openalex", "mailto"),
    "OPENALEX_MAX_RETRIES": ("openalex", "max_retries"),
    "OPENALEX_MIN_INTERVAL": ("openalex", "min_interval"),
    "CROSSREF_MAILTO": ("crossref", "mailto"),
    "CROSSREF_MAX_RETRIES": ("crossref", "max_retries"),
    "CROSSREF_MIN_INTERVAL": ("crossref", "min_interval"),
    "OPENLIBRARY_MAILTO": ("openlibrary", "mailto"),
    "OPENLIBRARY_MAX_RETRIES": ("openlibrary", "max_retries"),
    "OPENLIBRARY_MIN_INTERVAL": ("openlibrary", "min_interval"),
    "LOC_MAX_RETRIES": ("loc", "max_retries"),
    "LOC_MIN_INTERVAL": ("loc", "min_interval"),
}

# Sections a config file may carry; each must be a table.
SECTIONS: tuple[str, ...] = tuple(
    dict.fromkeys(section for section, _ in ENV_KEYS.values() if section is not None)
)


def user_config_path() -> Path:
    """`~/.config/citefinder/config.toml`, honoring `$XDG_CONFIG_HOME`."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_dir = Path(xdg) if xdg else Path.home() / ".config"
    return config_dir / "citefinder" / "config.toml"


def find_project_config(start: Path | None = None) -> Path | None:
    """The nearest project config at or above `start` (default: cwd).

    Finds `citefinder.toml`, or a `pyproject.toml` with `[tool.citefinder]`;
    the dedicated file wins in one directory. An unparsable `pyproject.toml`
    is returned so `load_config` raises rather than a parent config winning.
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
    `tool.citefinder` or section that is not a table).
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
            # Broken, not absent: raise rather than shadow a parent config.
            kind = type(table).__name__
            raise ValueError(f"[tool.citefinder] is not a table (got {kind})")
        config = table
    for section in SECTIONS:
        value = config.get(section)
        if value is not None and not isinstance(value, dict):
            # e.g. `openalex = 5`: keep callers from reading keys off a scalar.
            kind = type(value).__name__
            raise ValueError(f"[{section}] is not a table (got {kind})")
    return config
