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
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import typer
from dotenv import find_dotenv, load_dotenv

from citefinder import install as install_mod
from citefinder._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_INTERVAL,
    package_version,
    validate_knob,
)
from citefinder.bib import parse_entries
from citefinder.cache import (
    SOURCE_HOSTS,
    MergeStats,
    SourceStats,
    merge_caches,
    read_records,
    summarize_caches,
    write_records,
)
from citefinder.client import (
    CROSSREF_MIN_INTERVAL,
    CROSSREF_POLITE_MIN_INTERVAL,
    CrossrefClient,
    is_polite,
)
from citefinder.config import (
    ENV_KEYS,
    default_cache_dir,
    find_project_config,
    load_config,
    resolve_cache_path,
    user_config_path,
)
from citefinder.models import cache_drift
from citefinder.openalex import OpenAlexClient
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
) -> Generator[None, None, None]:
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
        min_interval = 0.1
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
cache_app = typer.Typer(help="Inspect and consolidate the JSONL caches.")
app.add_typer(cache_app, name="cache")

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


def _pacing_options(source: str, min_interval_default: str) -> tuple[Any, Any]:
    """The `--max-retries` / `--min-interval` pair bound to one source's env.

    Both flags differ between sources only by the `<SOURCE>_` env prefix and
    the pacing default named in the help, so a new source declares its pair
    here rather than growing another two near-identical option blocks.
    `verify` builds its own inline: it picks the source at runtime, so it
    cannot bind a single `envvar` and reads them via `_source_client_kwargs`.
    """
    prefix = source.upper()
    return (
        typer.Option(
            None,
            "--max-retries",
            min=0,
            envvar=f"{prefix}_MAX_RETRIES",
            help=_MAX_RETRIES_HELP.format(env=f"{prefix}_MAX_RETRIES"),
        ),
        typer.Option(
            None,
            "--min-interval",
            min=0.0,
            envvar=f"{prefix}_MIN_INTERVAL",
            help=_MIN_INTERVAL_HELP.format(
                default=min_interval_default, env=f"{prefix}_MIN_INTERVAL"
            ),
        ),
    )


# Crossref's default depends on whether the caller is polite, so its help
# names both rates rather than a single number.
_MIN_INTERVAL_DEFAULT = str(DEFAULT_MIN_INTERVAL)
_CROSSREF_MIN_INTERVAL_DEFAULT = (
    f"{CROSSREF_MIN_INTERVAL} anonymous, {CROSSREF_POLITE_MIN_INTERVAL} with a mailto"
)
_VERIFY_MIN_INTERVAL_DEFAULT = (
    f"{DEFAULT_MIN_INTERVAL} for OpenAlex, "
    f"{CROSSREF_MIN_INTERVAL} for Crossref "
    f"({CROSSREF_POLITE_MIN_INTERVAL} with a mailto)"
)

OpenAlexMaxRetriesOption, OpenAlexMinIntervalOption = _pacing_options(
    "openalex", _MIN_INTERVAL_DEFAULT
)
CrossrefMaxRetriesOption, CrossrefMinIntervalOption = _pacing_options(
    "crossref", _CROSSREF_MIN_INTERVAL_DEFAULT
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


def _openalex_client(
    cache: Path | None,
    cache_dir: Path | None,
    mailto: str | None,
    api_key: str | None,
    max_retries: int | None,
    min_interval: float | None,
) -> OpenAlexClient:
    """An `OpenAlexClient` from the options every top-level command shares."""
    return OpenAlexClient(
        cache_path=_cache_path("openalex", cache, cache_dir),
        mailto=mailto,
        api_key=api_key,
        **_client_kwargs(max_retries, min_interval),
    )


def _crossref_client(
    cache: Path | None,
    cache_dir: Path | None,
    mailto: str | None,
    max_retries: int | None,
    min_interval: float | None,
) -> CrossrefClient:
    """A `CrossrefClient` from the options every `crossref` subcommand shares."""
    return CrossrefClient(
        cache_path=_cache_path("crossref", cache, cache_dir),
        mailto=mailto,
        **_client_kwargs(max_retries, min_interval),
    )


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


def _require_file(path: Path, label: str = "", code: int = 1) -> None:
    """Exit rather than let a command run on a path that is not a file.

    Every command that takes a path opens with this check, so the wording is
    one string: a missing `.bib`, a mistyped cache, and a directory passed
    where a file belongs all report the same way. `label` names the flag when
    the path came from one, and `code=2` marks it a usage error.
    """
    if not path.is_file():
        typer.echo(f"Error: {label}{path} is not a file", err=True)
        raise typer.Exit(code=code)


def _to_json(payload: object) -> str:
    """The one JSON rendering both stdout and `results.json` use."""
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _emit(result: object) -> None:
    typer.echo(_to_json(result))


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
    client = _openalex_client(
        cache, cache_dir, mailto, api_key, max_retries, min_interval
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
    client = _openalex_client(
        cache, cache_dir, mailto, api_key, max_retries, min_interval
    )
    items = client.search_title(title, rows=rows)
    _emit(items)


def _age(seconds: float) -> str:
    """A rough age for a snapshot: precision past the unit is noise here."""
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


@app.command("ratelimit")
def ratelimit(
    source: str = typer.Option(
        "openalex", "--source", help="Which API's quota to report."
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Ask the API now instead of reporting the last headers seen. "
        "Costs one request (zero credits on OpenAlex). Implied when nothing "
        "is recorded yet.",
    ),
    cache: Path | None = typer.Option(
        None, "--cache", help="JSONL cache holding the snapshot."
    ),
    cache_dir: Path | None = CacheDirOption,
    mailto: str | None = typer.Option(None, "--mailto", help="Polite-pool email."),
    api_key: str | None = ApiKeyOption,
) -> None:
    """Report what the source last said about your remaining quota.

    Every response carries the quota headers, so the client records them as
    it works and stores the newest in the cache. Reporting them therefore
    costs nothing; `--refresh` issues one request to get a current reading,
    as does a first run with nothing stored yet.
    """
    if source not in ("openalex", "crossref"):
        typer.echo(f"Error: unknown source {source!r} (openalex or crossref)", err=True)
        raise typer.Exit(code=2)
    client: OpenAlexClient | CrossrefClient
    if source == "openalex":
        client = _openalex_client(cache, cache_dir, mailto, api_key, None, None)
    else:
        client = _crossref_client(cache, cache_dir, mailto, None, None)

    snapshot = client.rate_limit
    headers = snapshot.get("headers") if isinstance(snapshot, dict) else None
    # Nothing stored yet is the one case a stale-but-free reading can't
    # answer, so take one; a later run then reads it from the cache.
    if refresh or not isinstance(headers, dict) or not headers:
        snapshot = client.refresh_rate_limit()
        headers = snapshot.get("headers") if isinstance(snapshot, dict) else None
    if not isinstance(headers, dict) or not headers:
        typer.echo(f"{source}: the API returned no quota headers")
        return
    ts = snapshot.get("ts") if isinstance(snapshot, dict) else None
    when = _age(time.time() - ts) if isinstance(ts, (int, float)) else "age unknown"
    typer.echo(f"{source}  (recorded {when})")
    width = max(len(name) for name in headers)
    for name in sorted(headers):
        typer.echo(f"  {name:<{width}}  {headers[name]}")


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

    from citefinder.bib_table import bib_to_table

    _require_file(bib_file)

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

    from citefinder.bib_table import table_to_bib

    _require_file(csv_file)

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
            default=_VERIFY_MIN_INTERVAL_DEFAULT, env="<SOURCE>_MIN_INTERVAL"
        ),
    ),
) -> None:
    """Verify a `.bib` against Crossref or OpenAlex.

    For each entry: if `doi` is present, look up that DOI directly;
    otherwise search by author + title + year. Writes a JSONL response
    cache and a structured `results.json` to the output directory.
    """
    _require_file(bib_file)

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

    status_counts: Counter[str] = Counter()
    results: list[Result] = []
    width = len(str(len(entries)))
    t0 = time.monotonic()

    for i, entry in enumerate(entries, 1):
        calls_before = src.network_calls
        typer.echo(f"  [{i:>{width}}/{len(entries)}] {entry.key:<30}", nl=False)
        r = verify_entry(entry, src)
        results.append(r)
        # `verify_entry` makes at most one lookup per entry — the DOI path and
        # the search path each return — so this reads as "this entry went to
        # the network", and the run total below is a straight call count.
        was_network = src.network_calls > calls_before
        status_counts[r.status] += 1
        sim = f"{r.similarity:.2f}" if r.similarity is not None else "  - "
        net_or_hit = "net" if was_network else "hit"
        running = " ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
        typer.echo(f" {r.status:<14} {r.method:<7} sim={sim} [{net_or_hit}]  {running}")

    elapsed = time.monotonic() - t0
    retries = src.retries
    network_calls = src.network_calls
    typer.echo(
        f"\nDone in {elapsed:.1f}s — {network_calls} network call(s), "
        f"{len(entries) - network_calls} cache hit(s), "
        f"{retries} retr{'y' if retries == 1 else 'ies'}."
    )
    typer.echo(
        "Final counts — "
        + ", ".join(f"{k}: {v}" for k, v in status_counts.most_common())
    )

    payload = {
        "bib_path": str(bib_file),
        "source": source,
        "results": [asdict(r) for r in results],
    }
    (out_dir / "results.json").write_text(_to_json(payload) + "\n")

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
    version = package_version()
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

# What a setting is when nothing sets it: the clients' own defaults.
_SETTING_DEFAULTS = {
    "OPENALEX_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
    "OPENALEX_MIN_INTERVAL": _MIN_INTERVAL_DEFAULT,
    "CROSSREF_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
}


def _setting_default(env_name: str) -> str:
    """The value a setting takes when nothing sets it.

    Crossref's pacing default is resolved rather than looked up: it follows
    whichever rate a request would actually get, so `citefinder config` run
    with a `mailto` configured reports the polite interval the client will
    really use, not the anonymous one.
    """
    if env_name == "CROSSREF_MIN_INTERVAL":
        polite = is_polite(os.environ.get("CROSSREF_MAILTO"))
        return str(CROSSREF_POLITE_MIN_INTERVAL if polite else CROSSREF_MIN_INTERVAL)
    return _SETTING_DEFAULTS.get(env_name, "(none)")


@app.command()
def drift(cache: Path) -> None:
    """Report keys the records in a JSONL cache carry that the models lack.

    Read-only. Rows are routed by the request host in their key (never by the
    file name) and grouped by kind: `crossref-work`, `crossref-search`,
    `openalex-work`, `openalex-search`. Each undeclared dotted path is printed
    with the share of records that carried it, most common first. A path that
    shows up on most records is a candidate for the model; a rare one is the
    tail the model leaves out on purpose. Unreadable lines are skipped the way
    the cache loader skips them.
    """
    _require_file(cache)
    drift_by_kind = cache_drift(read_records(cache))
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
            value = _setting_default(env_name)
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


# --- cache maintenance ------------------------------------------------------
#
# Consolidation is always an explicit step. No lookup, and no `verify` run,
# merges anything as a side effect: a per-run cache stays beside its own
# `results.json`, and a misrouted row only ever moves because someone ran
# `cache merge`.

_SOURCES: tuple[str, ...] = tuple(sorted(set(SOURCE_HOSTS.values())))

ExtraOption = typer.Option(
    None,
    "--extra",
    help="Another cache file to merge in (repeatable): a copy from elsewhere, "
    "or a sync tool's conflicted duplicate. Rows are routed by their own host.",
)
KeepRecordsOption = typer.Option(
    False,
    "--keep-records",
    help="Never let a cached 404 supersede a real record, however new it is.",
)
WriteOption = typer.Option(
    False, "--write", help="Apply the merge. Without it, nothing is written."
)


def _cache_root(cache_dir: Path | None) -> Path:
    """The directory the cache commands read, or exit 1 when it is not one.

    A missing directory is an error, never an empty cache: a dropped mount
    or a mistyped `--cache-dir` must not report zero rows as if the cache
    were simply new.
    """
    root = _cache_dir(cache_dir) or default_cache_dir()
    if not root.is_dir():
        typer.echo(f"Error: {root}: not a directory", err=True)
        raise typer.Exit(code=1)
    return root


def _find(root: Path, pattern: str) -> list[Path]:
    with _report_errors(OSError, prefix=f"cannot read {root}: "):
        return sorted(root.rglob(pattern))


def _labelled(pairs: list[tuple[str, object]]) -> None:
    width = max(len(label) for label, _ in pairs)
    for label, value in pairs:
        typer.echo(f"  {label:<{width}}  {value}")


def _when(ts: float | None) -> str:
    """A row timestamp as local ISO time, or the raw value if it is not one."""
    if ts is None:
        return "(none)"
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return str(ts)


def _report_merge(stats: MergeStats) -> None:
    """The outcome of one merge: what it read, and every decision it made.

    The null-versus-record counts print even at zero — a merge that silently
    replaced a good record with a stale 404 is exactly what a reader needs to
    see the run it happened in. The rest print only when they fire.
    """
    pairs: list[tuple[str, object]] = [
        ("rows read", stats.rows_read),
        ("distinct keys", stats.keys),
        ("duplicate rows dropped", stats.replaced),
        ("404s superseded by a record", stats.nulls_superseded),
        ("records superseded by a 404", stats.records_superseded),
    ]
    for label, value in (
        ("records kept over a newer 404", stats.records_kept),
        ("rows with no ts", stats.missing_ts),
        ("misrouted rows (host != file)", stats.misrouted),
        ("unroutable rows", stats.unroutable),
    ):
        if value:
            pairs.append((label, value))
    _labelled(pairs)


def _apply(rows: list[Any], target: Path, write: bool) -> None:
    if not write:
        typer.echo(f"  would write {target} ({len(rows)} lines); pass --write to apply")
        return
    with _report_errors(OSError, prefix=f"cannot write {target}: "):
        write_records(target, rows)
    typer.echo(f"  wrote {target} ({len(rows)} lines)")


@cache_app.command("stats")
def cache_stats_cmd(cache_dir: Path | None = CacheDirOption) -> None:
    """Inventory every JSONL cache under the cache directory.

    Read-only, and recursive: the shared `<source>.jsonl` caches and every
    per-run `verify` cache under them are counted together, so this is the
    answer to "what has this project actually fetched". Rows are grouped by
    the host that answered them, never by file name, so a misdirected record
    is counted under the source it really came from. Distinct-key counts use
    the newest row for each key, so `cached 404s` is what a lookup would hit
    today.
    """
    root = _cache_root(cache_dir)
    paths = _find(root, "*.jsonl")
    typer.echo(f"cache dir: {root}")
    typer.echo(f"files:     {len(paths)}")
    summary = summarize_caches(paths)
    if not summary:
        typer.echo("\nno cache rows found")
        return
    for name in sorted(summary):
        stats: SourceStats = summary[name]
        typer.echo(f"\n{name}")
        _labelled(
            [
                ("files", stats.files),
                ("rows", stats.rows),
                (
                    "distinct keys",
                    f"{stats.keys} ({stats.lookups} lookup(s), "
                    f"{stats.searches} search(es))",
                ),
                ("cached 404s", stats.misses),
                ("rows with no ts", stats.missing_ts),
                ("newest fetch", _when(stats.newest_ts)),
            ]
        )


@cache_app.command("merge")
def cache_merge_cmd(
    cache_dir: Path | None = CacheDirOption,
    extra: list[Path] | None = ExtraOption,
    keep_records: bool = KeepRecordsOption,
    write: bool = WriteOption,
) -> None:
    """Consolidate every cache under the cache directory into the shared ones.

    Reads every `<cache-dir>/**/*.jsonl` — the shared caches plus every
    per-run `verify` cache — and any `--extra` file, then rewrites
    `<cache-dir>/<source>.jsonl` with one line per key. Every file is offered
    to every source and each row goes where its host says, so a record that
    landed in the wrong file is filed under the source that answered it
    rather than lost. Across independent files the newest `ts` wins (line
    order says nothing between logs); within one file the later line does, as
    the cache itself replays it. Winners keep their own `ts`.

    Dry run by default. The inputs are never modified: a `verify` run's cache
    stays beside its `results.json`, so consolidating is reversible by
    re-running the merge.
    """
    root = _cache_root(cache_dir)
    extras = [_anchor_or_exit(p, Path.cwd()) for p in extra or []]
    for path in extras:
        _require_file(path, label="--extra ", code=2)
    # `merge_caches` reads a file named twice only once, so an `--extra` that
    # the glob already found does not inflate the counts below.
    inputs = _find(root, "*.jsonl") + extras
    typer.echo(f"cache dir: {root}")
    # Every source is merged before anything is written: the targets are
    # inputs too, so rewriting one mid-run would take a misrouted row out
    # from under the pass that was about to rehome it.
    merged = [
        (source, merge_caches(inputs, source=source, keep_records=keep_records))
        for source in _SOURCES
    ]
    for source, (rows, stats) in merged:
        typer.echo(f"\n{source} ({stats.files} file(s) read)")
        if not rows:
            typer.echo("  no rows found")
            continue
        _report_merge(stats)
        _apply(rows, resolve_cache_path(source, root), write)


@cache_app.command("compact")
def cache_compact_cmd(
    path: Path,
    keep_records: bool = KeepRecordsOption,
    write: bool = WriteOption,
) -> None:
    """Dedupe one JSONL cache to a single line per key, in place.

    The same merge as `cache merge` over a single file, so a shared cache
    that has grown a long tail of repeat lookups shrinks to one line per key
    with each winner's own `ts` preserved. Idempotent, and lossless: rows
    from neither API are reported but kept, unlike a merge into a source's
    cache, which drops them.

    Dry run by default. `--write` replaces the file atomically (a temporary
    file moved over it), never by rewriting it in place.
    """
    _require_file(path)
    rows, stats = merge_caches([path], keep_records=keep_records)
    typer.echo(f"{path}")
    _report_merge(stats)
    _apply(rows, path, write)


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
    client = _crossref_client(cache, cache_dir, mailto, max_retries, min_interval)
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
    client = _crossref_client(cache, cache_dir, mailto, max_retries, min_interval)
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
    client = _crossref_client(cache, cache_dir, mailto, max_retries, min_interval)
    result = client.lookup_book_chapter(book_doi, chapter_arg)
    _emit_or_exit(result, f"{book_doi}.{chapter}")
