"""citefinder CLI.

Top-level commands default to OpenAlex — it indexes Crossref *plus* arXiv,
preprints, and repository deposits, so a single `citefinder doi` or
`citefinder search` works for the broadest range of citations. Crossref
remains accessible via the `crossref` subcommand for its own workflows
(book-chapter lookup, the canonical published-deposit metadata).
"""

from __future__ import annotations

import json
import os
import sys
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from operator import itemgetter
from pathlib import Path
from typing import Any, Literal

import typer
from dotenv import find_dotenv, load_dotenv

from citefinder import install as install_mod
from citefinder._base import DEFAULT_MAX_RETRIES, validate_knob
from citefinder.bib import parse_entries
from citefinder.bib_table import bib_to_table, table_to_bib
from citefinder.client import CrossrefClient
from citefinder.config import (
    ENV_KEYS,
    find_project_config,
    load_config,
    resolve_cache_path,
    user_config_path,
)
from citefinder.models import cache_drift
from citefinder.openalex import DEFAULT_MIN_INTERVAL, OpenAlexClient
from citefinder.verify import Result, Source, verify_entry

# Load `.env` from the current working directory (or any parent) so users can
# keep `OPENALEX_API_KEY` out of their shell rc and out of bash history.
# Library users are unaffected — this only runs when the CLI is invoked.
load_dotenv(find_dotenv(usecwd=True))


def _anchor(path: str | Path, base: Path) -> Path:
    """`path` made absolute against `base`; a leading `~` expands first.

    Raises `ValueError` for a `~user` form naming an unknown account, which
    `Path.expanduser` reports as a bare `RuntimeError`.
    """
    try:
        expanded = Path(path).expanduser()
    except RuntimeError as exc:
        raise ValueError(f"cannot expand {str(path)!r}: {exc}") from exc
    return base / expanded


@contextmanager
def _report_errors(
    *kinds: type[Exception], prefix: str = "", code: int = 1
) -> Iterator[None]:
    """Turn a library exception into an `Error:` line and exit, no traceback.

    Exit 1 for a failure in the work itself; `code=2` for a usage error (a
    bad flag or env value), the code click itself uses for those.
    """
    try:
        yield
    except kinds as exc:
        typer.echo(f"Error: {prefix}{exc}", err=True)
        raise typer.Exit(code=code) from exc


def _anchor_or_exit(path: str | Path, base: Path) -> Path:
    """`_anchor` for a flag or env value, where a bad `~user` is a usage error."""
    with _report_errors(ValueError, code=2):
        return _anchor(path, base)


# Env name -> which config file `_load_configs` took its value from
# ("project" or "user"). Names it did not set came from a flag, the shell
# env, or `.env`. `citefinder config` reports these.
_config_sources: dict[str, str] = {}


def _load_configs() -> None:
    """Populate env vars from the project config, then the user config, for
    any names not already set.

    The project config is the nearest `citefinder.toml` or `pyproject.toml`
    with a `[tool.citefinder]` table at or above the working directory; the
    user config is `~/.config/citefinder/config.toml` (honors
    `$XDG_CONFIG_HOME`). Each fills only names still unset, so shell env and
    `.env` win over both and the project file wins over the user file: a
    setting that is a property of a repo (where its caches go) lives with
    the repo, while credentials stay per machine.

    Expected format, either file (under `[tool.citefinder]` in pyproject):
        cache_dir = "data/citefinder"   # relative: to this file's dir

        [openalex]
        api_key = "oa_pk_..."   # user config or .env only, never a project file
        mailto = "you@example.com"
        max_retries = 3
        min_interval = 0.1

        [crossref]
        mailto = "you@example.com"
        max_retries = 3
        min_interval = 0
    """
    _config_sources.clear()
    project = find_project_config()
    if project is not None:
        _apply_config(project, "project")
    user = user_config_path()
    if user.is_file():
        _apply_config(user, "user")


def _apply_config(path: Path, kind: Literal["project", "user"]) -> None:
    try:
        config = load_config(path)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        # A broken config file must never take the whole CLI down —
        # `citefinder skill` is the only copy of the skill instructions on
        # this machine. Warn and fall through to the next source.
        typer.echo(f"warning: ignoring {path}: {exc}", err=True)
        return
    for env_name, (section, key) in ENV_KEYS.items():
        table = config if section is None else config.get(section) or {}
        value = table.get(key)
        # `is not None`, not truthiness: `max_retries = 0` is a real setting.
        if value is None or value == "":
            continue
        if kind == "project" and key == "api_key":
            # A project config is meant to be committed, and a key in it
            # would be too. Skipped even when the env already carries one,
            # so the warning fires as long as the key is on disk.
            typer.echo(
                f"warning: ignoring {section}.{key} in {path}: keep API keys "
                f"out of project config; use .env or {user_config_path()}",
                err=True,
            )
            continue
        # An empty value is "unset" everywhere else the env is read (typer's
        # `envvar`, `_env_number`, `_cache_dir`), so treat it the same here or
        # `OPENALEX_MAILTO=""` would block the config value and win nothing.
        if os.environ.get(env_name):
            continue
        if key == "cache_dir":
            # Anchored to the file, not the working directory, so a relative
            # `cache_dir` names the same place whatever the command's cwd.
            try:
                value = _anchor(str(value), path.parent)
            except ValueError as exc:
                typer.echo(f"warning: ignoring {key} in {path}: {exc}", err=True)
                continue
        os.environ[env_name] = str(value)
        _config_sources[env_name] = kind


_load_configs()

app = typer.Typer(
    help="OpenAlex (default) + Crossref reference lookups with local JSONL caching."
)
crossref_app = typer.Typer(
    help="Crossref lookups (canonical published-deposit metadata)."
)
app.add_typer(crossref_app, name="crossref")

_CACHE_HELP = (
    "JSONL cache path (default: <cache-dir>/{source}.jsonl). Overrides --cache-dir."
)
_CACHE_DIR_HELP = (
    "Directory the {what} derives from: <cache-dir>/{layout} (default {default}). "
    "Also CITEFINDER_CACHE_DIR in the env or `cache_dir` in a config file."
)
OpenAlexCacheOption = typer.Option(
    None, "--cache", help=_CACHE_HELP.format(source="openalex")
)
CrossrefCacheOption = typer.Option(
    None, "--cache", help=_CACHE_HELP.format(source="crossref")
)
CacheDirOption = typer.Option(
    None,
    "--cache-dir",
    help=_CACHE_DIR_HELP.format(
        what="cache path", layout="<source>.jsonl", default="~/.cache/citefinder"
    ),
)
VerifyCacheDirOption = typer.Option(
    None,
    "--cache-dir",
    help=_CACHE_DIR_HELP.format(
        what="output dir",
        layout="<bib-dir>[-<bib-stem>]/<source>/",
        default="data/citefinder under the working directory",
    ),
)
RowsOption = typer.Option(3, help="Number of results to return.")
OpenAlexMailtoOption = typer.Option(
    None,
    "--mailto",
    envvar="OPENALEX_MAILTO",
    help="Email for OpenAlex's polite pool (also OPENALEX_MAILTO env or config.toml).",
)
CrossrefMailtoOption = typer.Option(
    None,
    "--mailto",
    envvar="CROSSREF_MAILTO",
    help="Email for Crossref's polite pool (also CROSSREF_MAILTO env or config.toml).",
)
ApiKeyOption = typer.Option(
    None,
    "--api-key",
    envvar="OPENALEX_API_KEY",
    help="OpenAlex API key (also OPENALEX_API_KEY env, .env, or config.toml).",
)

_MAX_RETRIES_HELP = (
    "Retries after a 429/502/503/504 response; 0 disables (default 3). "
    "Also {env} env or config.toml."
)
_MIN_INTERVAL_HELP = (
    "Minimum seconds between requests (default {default}). "
    "Also {env} env or config.toml."
)
OpenAlexMaxRetriesOption = typer.Option(
    None,
    "--max-retries",
    min=0,
    envvar="OPENALEX_MAX_RETRIES",
    help=_MAX_RETRIES_HELP.format(env="OPENALEX_MAX_RETRIES"),
)
OpenAlexMinIntervalOption = typer.Option(
    None,
    "--min-interval",
    min=0.0,
    envvar="OPENALEX_MIN_INTERVAL",
    help=_MIN_INTERVAL_HELP.format(default="0.1", env="OPENALEX_MIN_INTERVAL"),
)
CrossrefMaxRetriesOption = typer.Option(
    None,
    "--max-retries",
    min=0,
    envvar="CROSSREF_MAX_RETRIES",
    help=_MAX_RETRIES_HELP.format(env="CROSSREF_MAX_RETRIES"),
)
CrossrefMinIntervalOption = typer.Option(
    None,
    "--min-interval",
    min=0.0,
    envvar="CROSSREF_MIN_INTERVAL",
    help=_MIN_INTERVAL_HELP.format(default="0", env="CROSSREF_MIN_INTERVAL"),
)


def _client_kwargs(
    max_retries: int | None, min_interval: float | None
) -> dict[str, Any]:
    """Constructor kwargs for the knobs a user actually set.

    An unset flag is omitted rather than passed as `None`, so the client's
    own default (per-source pacing, 3 retries) stays in force.
    """
    kwargs: dict[str, Any] = {}
    if max_retries is not None:
        kwargs["max_retries"] = _checked_knob("max_retries", max_retries)
    if min_interval is not None:
        kwargs["min_interval"] = _checked_knob("min_interval", min_interval)
    return kwargs


def _checked_knob(name: str, value: float) -> float:
    """Reject a knob value the client would refuse, with a clean exit 2.

    Every CLI path funnels through here: click's `min=0` on the flags lets
    `inf`/`nan` through, and `verify`'s env fallback skips click's range
    check entirely, so the client's own bound is applied up front.
    """
    with _report_errors(ValueError, code=2):
        return validate_knob(name, value)


def _source_client_kwargs(
    source: str,
    max_retries: int | None,
    min_interval: float | None,
    mailto: str | None,
) -> dict[str, Any]:
    """Like `_client_kwargs`, falling back to the chosen source's env vars.

    `verify` picks its source at runtime, so its flags can't bind a single
    `envvar`; an unset flag reads `<SOURCE>_MAX_RETRIES` /
    `<SOURCE>_MIN_INTERVAL` / `<SOURCE>_MAILTO` (which config.toml also
    feeds) so `verify --source crossref` honors the `[crossref]` section.
    """
    prefix = source.upper()
    if max_retries is None:
        max_retries = _env_number(f"{prefix}_MAX_RETRIES", int)
    if min_interval is None:
        min_interval = _env_number(f"{prefix}_MIN_INTERVAL", float)
    kwargs = _client_kwargs(max_retries, min_interval)
    kwargs["mailto"] = mailto or os.environ.get(f"{prefix}_MAILTO") or None
    return kwargs


def _env_number(name: str, cast: type[int] | type[float]) -> Any:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return cast(raw)
    except ValueError:
        kind = "an integer" if cast is int else "a number"
        typer.echo(
            f"Error: {name}={raw!r} is not {kind} (set in the env or config.toml)",
            err=True,
        )
        raise typer.Exit(code=2) from None


def _cache_dir(flag: Path | None) -> Path | None:
    """The directory a command derives its cache paths from, or `None`
    when nothing is set and the command's own default applies.

    `--cache-dir` first, then `CITEFINDER_CACHE_DIR` (which config.toml
    also feeds). A relative flag or env value is anchored to the working
    directory, as `--cache` and `--out` are; a config-file value was
    anchored to the file's directory when it was loaded.
    """
    if flag is not None:
        return _anchor_or_exit(flag, Path.cwd())
    env = os.environ.get("CITEFINDER_CACHE_DIR")
    if not env:
        return None
    return _anchor_or_exit(env, Path.cwd())


def _cache_path(source: str, cache: Path | None, cache_dir: Path | None) -> Path:
    """`--cache` verbatim when given, else `<cache_dir>/<source>.jsonl`."""
    if cache is not None:
        return cache
    return resolve_cache_path(source, _cache_dir(cache_dir))


def _verify_root(cache_dir: Path | None) -> Path:
    """The directory `verify` files its output under: `cache_dir` when one
    is set, else `data/citefinder` under the working directory. Shared with
    `citefinder config` so the path it prints is the one `verify` writes."""
    return _cache_dir(cache_dir) or Path.cwd() / "data" / "citefinder"


_PRIMARY_BIB_STEM = "refs"


def _verify_out_dir(bib_file: Path, source: str, cache_dir: Path | None) -> Path:
    """The default directory `verify` writes to:
    `<root>/<bib-dir>[-<bib-stem>]/<source>/`, where `<bib-dir>` is the name
    of the directory holding the `.bib` and the `-<bib-stem>` suffix is added
    unless the file is the primary `refs.bib`.

    Keying on the directory rather than the stem keeps runs from different
    directories apart even when they name their bibliographies alike
    (`refs.bib` everywhere, or an `extra.bib` in each); keying on the stem
    alone funnelled them into one directory, the later run overwriting the
    earlier. The path is made absolute first, since a bare `verify refs.bib`
    has no parent component to name the directory by; symlinks are left
    alone so a linked directory keeps its own name and its existing cache.
    """
    bib = Path(os.path.normpath(bib_file.absolute()))
    name = bib.parent.name
    if bib.stem != _PRIMARY_BIB_STEM:
        name = f"{name}-{bib.stem}"
    return _verify_root(cache_dir) / name / source


def _emit(result: object) -> None:
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


def _emit_or_exit(result: object | None, label: str) -> None:
    """Print a lookup result as JSON, or report `not found: <label>` and exit 1."""
    if result is None:
        typer.echo(f"not found: {label}", err=True)
        raise typer.Exit(code=1)
    _emit(result)


# --- top-level (OpenAlex) ---------------------------------------------------


@app.command()
def doi(
    doi: str,
    cache: Path | None = OpenAlexCacheOption,
    cache_dir: Path | None = CacheDirOption,
    mailto: str | None = OpenAlexMailtoOption,
    api_key: str | None = ApiKeyOption,
    max_retries: int | None = OpenAlexMaxRetriesOption,
    min_interval: float | None = OpenAlexMinIntervalOption,
) -> None:
    """Look up a single DOI via OpenAlex."""
    client = OpenAlexClient(
        cache_path=_cache_path("openalex", cache, cache_dir),
        mailto=mailto,
        api_key=api_key,
        **_client_kwargs(max_retries, min_interval),
    )
    _emit_or_exit(client.lookup_doi(doi), doi)


@app.command()
def search(
    title: str,
    rows: int = RowsOption,
    cache: Path | None = OpenAlexCacheOption,
    cache_dir: Path | None = CacheDirOption,
    mailto: str | None = OpenAlexMailtoOption,
    api_key: str | None = ApiKeyOption,
    max_retries: int | None = OpenAlexMaxRetriesOption,
    min_interval: float | None = OpenAlexMinIntervalOption,
) -> None:
    """Search OpenAlex by title (title-only filter; tuned for citation lookup)."""
    client = OpenAlexClient(
        cache_path=_cache_path("openalex", cache, cache_dir),
        mailto=mailto,
        api_key=api_key,
        **_client_kwargs(max_retries, min_interval),
    )
    items = client.search_title(title, rows=rows)
    _emit(items)


# --- bib parsing & verification --------------------------------------------


@app.command("bib-to-table")
def bib_to_table_cmd(
    bib_file: Path,
    csv_out: bool = typer.Option(
        False, "--csv", help="Output CSV to stdout instead of a polars table."
    ),
    fields: str | None = typer.Option(
        None,
        "--fields",
        help="Comma-separated list of additional columns to include "
        "(besides key/entry_type). Default: all fields present in the file.",
    ),
) -> None:
    """Tabulate a `.bib` file into a wide table (one row per entry).

    Default output is a polars table sized for terminal viewing. `--csv`
    writes to stdout for piping into a spreadsheet or another tool.
    `--fields` filters to a subset (`key` and `entry_type` are always shown).
    """
    import polars as pl

    if not bib_file.is_file():
        typer.echo(f"Error: {bib_file} is not a file", err=True)
        raise typer.Exit(code=1)

    with _report_errors(ValueError):
        df = bib_to_table(bib_file.read_text())

    if fields:
        # `dict.fromkeys` keeps order and drops repeats, so naming `key` or
        # `entry_type` again does not become a duplicate projection.
        requested = (f.strip() for f in fields.split(","))
        wanted = dict.fromkeys(["key", "entry_type", *requested])
        present = [c for c in wanted if c in df.columns]
        df = df.select(present)

    if csv_out:
        sys.stdout.write(df.write_csv())
    else:
        pl.Config.set_tbl_rows(max(len(df), 50))
        pl.Config.set_tbl_width_chars(180)
        pl.Config.set_fmt_str_lengths(80)
        print(df)


@app.command("table-to-bib")
def table_to_bib_cmd(
    csv_file: Path,
    out: Path | None = typer.Option(
        None, "--out", help="Write `.bib` here. Defaults to stdout."
    ),
) -> None:
    """Convert a CSV (from `bib-to-table --csv`) back into a `.bib` file.

    Inverse of `bib-to-table`. Input CSV must have `key` and `entry_type`
    columns; remaining columns become bib fields. Empty cells are
    treated as absent fields. Field order within each entry follows
    the CSV's column order, so the source bib's original field order
    is not recoverable.
    """
    import polars as pl

    if not csv_file.is_file():
        typer.echo(f"Error: {csv_file} is not a file", err=True)
        raise typer.Exit(code=1)

    # `infer_schema_length=0` keeps every column as a string — year,
    # volume, etc. are bib values, not numbers, and downstream consumers
    # expect them to round-trip verbatim.
    df = pl.read_csv(csv_file, infer_schema_length=0)
    with _report_errors(ValueError):
        bib = table_to_bib(df)
    if out:
        out.write_text(bib)
    else:
        sys.stdout.write(bib)


@app.command()
def verify(
    bib_file: Path,
    source: Literal["crossref", "openalex"] = typer.Option(
        "openalex",
        "--source",
        help="Metadata source to verify against.",
        case_sensitive=False,
    ),
    mailto: str | None = typer.Option(
        None,
        "--mailto",
        help="Email for the source's polite pool "
        "(also <SOURCE>_MAILTO env or config.toml).",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output dir (default: <cache-dir>/<bib-dir>[-<bib-stem>]/<source>/). "
        "Overrides --cache-dir.",
    ),
    cache_dir: Path | None = VerifyCacheDirOption,
    max_retries: int | None = typer.Option(
        None,
        "--max-retries",
        min=0,
        help=_MAX_RETRIES_HELP.format(env="<SOURCE>_MAX_RETRIES"),
    ),
    min_interval: float | None = typer.Option(
        None,
        "--min-interval",
        min=0.0,
        help=_MIN_INTERVAL_HELP.format(
            default="0.1 for OpenAlex, 0 for Crossref", env="<SOURCE>_MIN_INTERVAL"
        ),
    ),
) -> None:
    """Verify a `.bib` against Crossref or OpenAlex.

    For each entry: if `doi` is present, look up that DOI directly;
    otherwise search by author + title + year. Writes a JSONL response
    cache and a structured `results.json` to the output directory.
    """
    if not bib_file.is_file():
        typer.echo(f"Error: {bib_file} is not a file", err=True)
        raise typer.Exit(code=1)

    # Default output comes from `_verify_out_dir`; its per-source subdir lets
    # crossref and openalex outputs coexist for side-by-side comparison
    # without collision. `--out` is anchored to cwd like `--cache-dir`, so a
    # quoted `~` expands and the cache below lands beside `results.json`.
    if out is not None:
        out_dir = _anchor_or_exit(out, Path.cwd())
    else:
        out_dir = _verify_out_dir(bib_file, source, cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_path = resolve_cache_path(source, out_dir)
    knobs = _source_client_kwargs(source, max_retries, min_interval, mailto)
    if source == "crossref":
        src = Source(
            name="crossref", client=CrossrefClient(cache_path=cache_path, **knobs)
        )
    else:
        src = Source(
            name="openalex", client=OpenAlexClient(cache_path=cache_path, **knobs)
        )

    entries = parse_entries(bib_file.read_text())
    starting_cache_size = src.cache_size()
    typer.echo(f"Parsed {len(entries)} entries from {bib_file}")
    typer.echo(f"Source: {source}")
    typer.echo(f"Cache: {cache_path} ({starting_cache_size} entries pre-loaded)\n")

    status_counts: dict[str, int] = {}
    network_calls = 0
    results: list[Result] = []
    width = len(str(len(entries)))
    t0 = time.monotonic()

    for i, entry in enumerate(entries, 1):
        cache_before = src.cache_size()
        typer.echo(f"  [{i:>{width}}/{len(entries)}] {entry.key:<30}", nl=False)
        r = verify_entry(entry, src)
        results.append(r)
        cache_after = src.cache_size()
        was_network = cache_after > cache_before
        if was_network:
            network_calls += 1
        status_counts[r.status] = status_counts.get(r.status, 0) + 1
        sim = f"{r.similarity:.2f}" if r.similarity is not None else "  - "
        net_or_hit = "net" if was_network else "hit"
        running = " ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
        typer.echo(f" {r.status:<14} {r.method:<7} sim={sim} [{net_or_hit}]  {running}")

    elapsed = time.monotonic() - t0
    retries = getattr(src.client, "retries", 0)
    typer.echo(
        f"\nDone in {elapsed:.1f}s — {network_calls} network call(s), "
        f"{len(entries) - network_calls} cache hit(s), "
        f"{retries} retr{'y' if retries == 1 else 'ies'}."
    )
    typer.echo(
        "Final counts — "
        + ", ".join(
            f"{k}: {v}"
            for k, v in sorted(status_counts.items(), key=itemgetter(1), reverse=True)
        )
    )

    payload = {
        "bib_path": str(bib_file),
        "source": source,
        "results": [asdict(r) for r in results],
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )

    typer.echo(f"\nWrote {out_dir}/results.json, {cache_path.name}")


# --- claude code skill ------------------------------------------------------


@app.command()
def skill() -> None:
    """Print the full `use-citefinder` skill instructions.

    The generated stub in `.claude/skills/` points here rather than carrying a
    copy of the body, so the instructions an agent reads always come from the
    installed package and can never be a stale duplicate.
    """
    # The body uses characters beyond legacy console codepages (arrows, >=);
    # on a non-UTF-8 stdout (Windows cp1252, PYTHONIOENCODING overrides)
    # degrade them rather than crash — a traceback here would zero out the
    # skill's whole delivery path.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")
    typer.echo(install_mod.skill_body(), nl=False)


def _installed_status(
    root: Path, version: str, mode: install_mod.Mode, local: bool
) -> tuple[Path | None, Literal["ok", "drifted", "missing"], install_mod.Mode]:
    """Where the checked stub sits, its drift status, and the mode it was judged by.

    `--local` narrows the check to the per-repo copy; otherwise whichever copy
    is installed (global first, matching Claude Code's own precedence) is
    judged by where it sits. Raises `ValueError` when the bundled body cannot
    be rendered to compare against.
    """
    if local:
        where = install_mod.skill_path(root, mode)
        return where, install_mod.check_mode(root, version, mode), mode
    found = install_mod.resolve_installed(root)
    if found is None:
        return None, "missing", mode
    where, mode = found
    return where, install_mod.check_mode(root, version, mode), mode


@app.command()
def install(
    local: bool = typer.Option(
        False,
        "--local",
        help="Install into the enclosing repo's .claude/ instead of ~/.claude/.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Report ok/drifted/missing without writing; exits 1 unless ok.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite a file at the target path that citefinder did not generate.",
    ),
) -> None:
    """Materialize the `use-citefinder` Claude Code skill stub.

    Writes `.claude/skills/use-citefinder/SKILL.md`: the skill's frontmatter
    triggers plus a short stub pointing at `citefinder skill`, which prints the
    instructions from the installed package. Global by default (`~/.claude/`,
    serving every repo); `--local` vendors the stub in the enclosing repo (the
    nearest ancestor with `.git` or `.claude/` — where Claude Code loads skills
    from). `--check` reports whether the stub still matches this version's
    render.
    """
    try:
        version = metadata_version("citefinder")
    except PackageNotFoundError:
        # Same degraded fallback as `_default_user_agent` in `_base.py` — a
        # missing distribution record should not make the stub unmanageable.
        version = "0.0.0"
    root = install_mod.find_repo_root()
    mode: install_mod.Mode = "local" if local else "global"

    if check:
        with _report_errors(ValueError):
            where, status, mode = _installed_status(root, version, mode, local)
        typer.echo(f"skill: {status}" + (f" ({where})" if where else ""))
        if status != "ok":
            # `missing` has nothing to overwrite, so it needs a plain install,
            # not the --force repair a drifted file calls for.
            fix = install_mod.install_command(mode, force=status == "drifted")
            typer.echo(f"  run: {fix}", err=True)
            raise typer.Exit(code=1)
        return

    path = install_mod.skill_path(root, mode)
    # A dangling symlink fails `exists()` (it follows the link) but still
    # occupies the path — and is never ours, so it stays behind --force too.
    occupied = path.is_symlink() or path.exists()
    if occupied and not install_mod.is_generated(path) and not force:
        typer.echo(
            f"Error: {path} exists and was not generated by citefinder — "
            "refusing to overwrite a hand-authored skill.\n"
            "Re-run with --force to replace it.",
            err=True,
        )
        raise typer.Exit(code=1)

    # OSError: a plain file squatting where `.claude/` should be, a directory at
    # SKILL.md itself. ValueError: a bundled body with no frontmatter to lift.
    with _report_errors(OSError, ValueError, prefix=f"cannot write {path}: "):
        written = install_mod.write_skill(root, version, mode)
    typer.echo(f"wrote {written} (citefinder {version}, mode={mode})")


# --- config ------------------------------------------------------------------

# What a setting is when nothing sets it: the clients' own defaults. Crossref
# has no named min_interval constant; "0" mirrors `CachedJsonClient`'s `0.0`.
_SETTING_DEFAULTS = {
    "OPENALEX_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
    "OPENALEX_MIN_INTERVAL": str(DEFAULT_MIN_INTERVAL),
    "CROSSREF_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
    "CROSSREF_MIN_INTERVAL": "0",
}


@app.command()
def drift(cache: Path) -> None:
    """Report keys the records in a JSONL cache carry that the models lack.

    Read-only. Rows are routed by the request host in their key (never by the
    file name) and grouped by kind: `crossref-work`, `crossref-search`,
    `openalex-work`, `openalex-search`. Each undeclared dotted path is printed
    with the share of records that carried it, most common first. A path that
    shows up on most records is a candidate for the model; a rare one is the
    tail the model leaves out on purpose. Torn lines are skipped, as the cache
    loader skips them.
    """
    if not cache.is_file():
        typer.echo(f"Error: {cache}: not a file", err=True)
        raise typer.Exit(code=1)
    rows = []
    with cache.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    drift_by_kind = cache_drift(rows)
    if not drift_by_kind:
        typer.echo("no crossref or openalex work records found")
        return
    for kind, (count, paths) in sorted(drift_by_kind.items()):
        typer.echo(f"{kind} ({count} records): {len(paths)} undeclared paths")
        for path, n in paths.most_common():
            typer.echo(f"  {n / count:4.0%}  {path}")


@app.command("config")
def config_cmd(cache_dir: Path | None = CacheDirOption) -> None:
    """Show each setting, where it came from, and the paths lookups would use.

    Read-only. Each value is tagged with its source: `flag`, `env` (shell or
    `.env`), `project` or `user` (the config files named at the top), or
    `default`. Run it from the directory a lookup ran in to see why it wrote
    where it did; pass `--cache-dir` to preview a flag's effect.
    """
    project = find_project_config()
    user = user_config_path()
    typer.echo(f"project config: {project or '(none)'}")
    typer.echo(f"user config:    {user if user.is_file() else f'(none: {user})'}")
    typer.echo()

    root = _cache_dir(cache_dir)
    if cache_dir is not None:
        source = "flag"
    elif root is not None:
        source = _config_sources.get("CITEFINDER_CACHE_DIR", "env")
    else:
        source = "default"
    rows = [("cache_dir", source, str(root) if root else "(unset)")]
    for env_name, (section, key) in ENV_KEYS.items():
        if section is None:
            continue
        raw = os.environ.get(env_name)
        if raw:
            # Never print a credential; that it is set, and from where, is
            # what the reader needs.
            value = "(set)" if key == "api_key" else raw
            source = _config_sources.get(env_name, "env")
        else:
            value = _SETTING_DEFAULTS.get(env_name, "(none)")
            source = "default"
        rows.append((f"{section}.{key}", source, value))
    label_width = max(len(label) for label, _, _ in rows)
    for label, source, value in rows:
        typer.echo(f"{label:<{label_width}}  {source:<7}  {value}")

    typer.echo()
    typer.echo(f"openalex cache:  {resolve_cache_path('openalex', root)}")
    typer.echo(f"crossref cache:  {resolve_cache_path('crossref', root)}")
    verify_root = _verify_root(cache_dir)
    typer.echo(
        f"verify output:   {verify_root / '<bib-dir>[-<bib-stem>]' / '<source>'}/"
    )


# --- crossref subcommand ----------------------------------------------------


@crossref_app.command("doi")
def crossref_doi(
    doi: str,
    cache: Path | None = CrossrefCacheOption,
    cache_dir: Path | None = CacheDirOption,
    mailto: str | None = CrossrefMailtoOption,
    max_retries: int | None = CrossrefMaxRetriesOption,
    min_interval: float | None = CrossrefMinIntervalOption,
) -> None:
    """Look up a single DOI via Crossref."""
    client = CrossrefClient(
        cache_path=_cache_path("crossref", cache, cache_dir),
        mailto=mailto,
        **_client_kwargs(max_retries, min_interval),
    )
    _emit_or_exit(client.lookup_doi(doi), doi)


@crossref_app.command("search")
def crossref_search(
    query: str,
    rows: int = RowsOption,
    cache: Path | None = CrossrefCacheOption,
    cache_dir: Path | None = CacheDirOption,
    mailto: str | None = CrossrefMailtoOption,
    max_retries: int | None = CrossrefMaxRetriesOption,
    min_interval: float | None = CrossrefMinIntervalOption,
) -> None:
    """Search Crossref by free-form bibliographic query (author + title + year)."""
    client = CrossrefClient(
        cache_path=_cache_path("crossref", cache, cache_dir),
        mailto=mailto,
        **_client_kwargs(max_retries, min_interval),
    )
    items = client.search_bibliographic(query, rows=rows)
    _emit(items)


@crossref_app.command("chapter")
def crossref_chapter(
    book_doi: str,
    chapter: str,
    cache: Path | None = CrossrefCacheOption,
    cache_dir: Path | None = CacheDirOption,
    mailto: str | None = CrossrefMailtoOption,
    max_retries: int | None = CrossrefMaxRetriesOption,
    min_interval: float | None = CrossrefMinIntervalOption,
) -> None:
    """Look up a book chapter by `{book_doi}.{NNN}` pattern."""
    chapter_arg: int | str = int(chapter) if chapter.isdigit() else chapter
    client = CrossrefClient(
        cache_path=_cache_path("crossref", cache, cache_dir),
        mailto=mailto,
        **_client_kwargs(max_retries, min_interval),
    )
    result = client.lookup_book_chapter(book_doi, chapter_arg)
    _emit_or_exit(result, f"{book_doi}.{chapter}")
