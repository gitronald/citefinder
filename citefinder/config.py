"""Cache-path resolution and the config-file layer behind the CLI.

`resolve_cache_path` is the one rule for where a source's JSONL cache
lives, shared by the CLI and by wrappers that build clients themselves.
The config-file helpers are what `citefinder.cli` reads at startup; the
library never loads a config file on its own — clients take an explicit
`cache_path`, `mailto`, and `api_key`.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "citefinder"


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
    root = DEFAULT_CACHE_DIR if cache_dir is None else Path(cache_dir).expanduser()
    return root / f"{source}.jsonl"
