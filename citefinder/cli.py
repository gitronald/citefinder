"""citefinder CLI.

Top-level commands default to OpenAlex, which covers Crossref plus arXiv and
other repository deposits. The `crossref` subcommand queries Crossref directly.
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
from pkgskills import register

from citefinder._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_INTERVAL,
    validate_knob,
)
from citefinder.bib import normalize_doi, parse_entries
from citefinder.books import BookLookup
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
from citefinder.host import HOST
from citefinder.loc import LOC_MIN_INTERVAL, LocClient
from citefinder.models import cache_drift
from citefinder.openalex import OpenAlexClient, datacite_note, datacite_registrar
from citefinder.openlibrary import OPENLIBRARY_MIN_INTERVAL, OpenLibraryClient
from citefinder.verify import Result, Source, verify_entry

# Load `.env` from the working directory or a parent; CLI only, never the library.
load_dotenv(find_dotenv(usecwd=True))


def _anchor(path: str | Path, base: Path) -> Path:
    """`path` made absolute against `base`; a leading `~` expands first.

    Raises `ValueError` for a `~user` naming an unknown account.
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

    Exit 1 for a failure in the work itself; `code=2` for a usage error.
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


# Env name -> "project" or "user", for values `_load_configs` set from a config file.
_config_sources: dict[str, str] = {}


def _load_configs() -> None:
    """Populate unset env vars from the project config, then the user config.

    Each file fills only names still unset, so shell env and `.env` win over
    both, and the project file wins over the user file.
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
        # A broken config must not take the CLI down.
        typer.echo(f"warning: ignoring {path}: {exc}", err=True)
        return
    for env_name, (section, key) in ENV_KEYS.items():
        table = config if section is None else config.get(section) or {}
        value = table.get(key)
        # `max_retries = 0` is a real setting, so don't test truthiness.
        if value is None or value == "":
            continue
        if kind == "project" and key == "api_key":
            # A project config is committed, so a key in it would leak.
            typer.echo(
                f"warning: ignoring {section}.{key} in {path}: keep API keys "
                f"out of project config; use .env or {user_config_path()}",
                err=True,
            )
            continue
        # An empty env value counts as unset, as it does everywhere else it is read.
        if os.environ.get(env_name):
            continue
        if key == "cache_dir":
            # Relative to the config file, not the working directory.
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
# Mounts `skill`, `doc`, and `install`.
register(app, HOST)

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

    `verify` builds its own: it picks the source at runtime, so it reads the
    env via `_source_client_kwargs`.
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


# Crossref's default depends on whether the caller is polite, so the help names both.
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
# `isbn` drives two clients; the Library of Congress one takes the same values.
OpenLibraryMaxRetriesOption, OpenLibraryMinIntervalOption = _pacing_options(
    "openlibrary", str(OPENLIBRARY_MIN_INTERVAL)
)
OpenLibraryMailtoOption = typer.Option(
    None,
    "--mailto",
    envvar="OPENLIBRARY_MAILTO",
    help="Contact email Open Library asks for, sent in the User-Agent "
    "(also OPENLIBRARY_MAILTO env or config.toml).",
)
NoLocOption = typer.Option(
    False,
    "--no-loc",
    help="Stop after Open Library; do not follow its LCCN to the Library of Congress.",
)


def _client_kwargs(
    max_retries: int | None, min_interval: float | None
) -> dict[str, Any]:
    """Constructor kwargs for the knobs a user set; unset ones keep client defaults."""
    kwargs: dict[str, Any] = {}
    if max_retries is not None:
        kwargs["max_retries"] = _checked_knob("max_retries", max_retries)
    if min_interval is not None:
        kwargs["min_interval"] = _checked_knob("min_interval", min_interval)
    return kwargs


def _checked_knob(name: str, value: float) -> float:
    """Reject a knob value the client would refuse, with exit 2.

    Click's `min=0` lets `inf`/`nan` through and `verify`'s env fallback skips
    it, so the client's own bound is applied here.
    """
    with _report_errors(ValueError, code=2):
        return validate_knob(name, value)


def _source_client_kwargs(
    source: str,
    max_retries: int | None,
    min_interval: float | None,
    mailto: str | None,
) -> dict[str, Any]:
    """Like `_client_kwargs`, with unset flags falling back to `<SOURCE>_*` env vars."""
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
    """The directory cache paths derive from, or `None` for the command's default.

    `--cache-dir` first, then `CITEFINDER_CACHE_DIR`; relative values anchor
    to the working directory.
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


def _book_lookup(
    cache_dir: Path | None,
    mailto: str | None,
    max_retries: int | None,
    min_interval: float | None,
    *,
    with_loc: bool = True,
    out_dir: Path | None = None,
    fallback: bool = False,
) -> BookLookup:
    """The Open Library + Library of Congress pair behind `isbn` and `verify --books`.

    Caches live under `out_dir` when given, else the cache dir; `fallback`
    adds the shared files as read-only fallbacks.
    """
    root = out_dir if out_dir is not None else _cache_dir(cache_dir)
    shared = _verify_root(cache_dir) if fallback else None

    def paths(source: str) -> dict[str, Any]:
        path = resolve_cache_path(source, root)
        if shared is None:
            return {"cache_path": path}
        shared_path = resolve_cache_path(source, shared)
        coincide = shared_path.resolve() == path.resolve()
        return {"cache_path": path, "fallback_cache": None if coincide else shared_path}

    openlibrary = OpenLibraryClient(
        **paths("openlibrary"),
        **_source_client_kwargs("openlibrary", max_retries, min_interval, mailto),
    )
    if not with_loc:
        return BookLookup(openlibrary)
    knobs = _source_client_kwargs("loc", max_retries, min_interval, None)
    knobs["mailto"] = openlibrary.contact
    loc = LocClient(**paths("loc"), **knobs)
    return BookLookup(openlibrary, loc)


def _verify_root(cache_dir: Path | None) -> Path:
    """The directory `verify` files its output under (also what `config` prints)."""
    return _cache_dir(cache_dir) or Path.cwd() / "data" / "citefinder"


_PRIMARY_BIB_STEM = "refs"


def _verify_out_dir(bib_file: Path, source: str, cache_dir: Path | None) -> Path:
    """The default `verify` output dir: `<root>/<bib-dir>[-<bib-stem>]/<source>/`.

    The stem suffix is omitted for `refs.bib`. The path is made absolute
    first, without resolving symlinks, so a linked directory keeps its name.
    """
    bib = Path(os.path.normpath(bib_file.absolute()))
    name = bib.parent.name
    if bib.stem != _PRIMARY_BIB_STEM:
        name = f"{name}-{bib.stem}"
    return _verify_root(cache_dir) / name / source


def _require_file(path: Path, label: str = "", code: int = 1) -> None:
    """Exit unless `path` is a file; `label` names the flag it came from."""
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
def isbn(
    isbn: str,
    cache_dir: Path | None = CacheDirOption,
    mailto: str | None = OpenLibraryMailtoOption,
    max_retries: int | None = OpenLibraryMaxRetriesOption,
    min_interval: float | None = OpenLibraryMinIntervalOption,
    no_loc: bool = NoLocOption,
) -> None:
    """Look up a book by ISBN: Open Library, then the Library of Congress.

    Prints the merged record with each field's source (`loc`, `openlibrary`,
    or `openlibrary:retailer`, a lead rather than a confirmation) and each
    source's record URL.
    """
    books = _book_lookup(
        cache_dir, mailto, max_retries, min_interval, with_loc=not no_loc
    )
    record = books.record(isbn)
    _emit_or_exit(record.as_dict() if record is not None else None, isbn)


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
    """Search OpenAlex by title."""
    client = _openalex_client(
        cache, cache_dir, mailto, api_key, max_retries, min_interval
    )
    items = client.search_title(title, rows=rows)
    _emit(items)


def _age(seconds: float) -> str:
    """A rough age for a snapshot."""
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

    Reads the newest quota headers stored in the cache, at no cost. `--refresh`
    issues one request for a current reading, as does a first run.
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
    # With nothing stored, a cached reading can't answer; fetch one.
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

    `--csv` writes CSV to stdout; `--fields` filters columns (`key` and
    `entry_type` are always shown).
    """
    import polars as pl

    from citefinder.bib_table import bib_to_table

    _require_file(bib_file)

    with _report_errors(ValueError):
        df = bib_to_table(bib_file.read_text())

    if fields:
        # `dict.fromkeys` keeps order and drops repeats.
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

    The CSV needs `key` and `entry_type` columns; the rest become bib fields,
    with empty cells treated as absent. Fields follow the CSV's column order.
    """
    import polars as pl

    from citefinder.bib_table import table_to_bib

    _require_file(csv_file)

    # `infer_schema_length=0` keeps every column a string so values round-trip verbatim.
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
    no_fallback: bool = typer.Option(
        False,
        "--no-fallback",
        help="Read only this run's cache, not the shared <cache-dir>/<source>.jsonl.",
    ),
    books: bool = typer.Option(
        False,
        "--books",
        help="Check @book/@inbook/@incollection entries with an `isbn` against "
        "Open Library and the Library of Congress instead of the source; "
        "entries without one get Open Library candidates.",
    ),
) -> None:
    """Verify a `.bib` against Crossref or OpenAlex.

    Looks up each entry's `doi` directly, else searches by author + title +
    year. Writes a JSONL response cache and `results.json` to the output dir.

    With `--books`, a book entry with an `isbn` and no `doi` is checked against
    its catalog record (`method=isbn`), and `results.json` gains per-entry
    `suggestions`.

    A miss in the per-run cache falls back, read-only, to the shared cache
    that `cache merge` maintains.
    """
    _require_file(bib_file)

    if out is not None:
        out_dir = _anchor_or_exit(out, Path.cwd())
    else:
        out_dir = _verify_out_dir(bib_file, source, cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_path = resolve_cache_path(source, out_dir)
    # The shared file `cache merge` writes; no fallback if it is the run's own cache.
    shared = resolve_cache_path(source, _verify_root(cache_dir))
    coincide = shared.resolve() == cache_path.resolve()
    fallback = None if no_fallback or coincide else shared
    knobs = _source_client_kwargs(source, max_retries, min_interval, mailto)
    if source == "crossref":
        client = CrossrefClient(cache_path=cache_path, fallback_cache=fallback, **knobs)
        src = Source(name="crossref", client=client)
    else:
        client = OpenAlexClient(cache_path=cache_path, fallback_cache=fallback, **knobs)
        src = Source(name="openalex", client=client)

    lookup = None
    if books:
        lookup = _book_lookup(
            cache_dir,
            None,
            None,
            None,
            out_dir=out_dir,
            fallback=not no_fallback,
        )

    entries = parse_entries(bib_file.read_text())
    starting_cache_size = src.cache_size()
    typer.echo(f"Parsed {len(entries)} entries from {bib_file}")
    typer.echo(f"Source: {source}" + (" (+ books: openlibrary, loc)" if books else ""))
    typer.echo(f"Cache: {cache_path} ({starting_cache_size} entries pre-loaded)")
    if fallback is not None:
        typer.echo(f"Fallback: {fallback} ({src.fallback_size() or 0} entries)")
    typer.echo()

    status_counts: Counter[str] = Counter()
    results: list[Result] = []
    width = len(str(len(entries)))
    t0 = time.monotonic()

    def network_calls() -> int:
        return src.network_calls + (lookup.network_calls if lookup else 0)

    def retries() -> int:
        return src.retries + (lookup.retries if lookup else 0)

    for i, entry in enumerate(entries, 1):
        calls_before = network_calls()
        typer.echo(f"  [{i:>{width}}/{len(entries)}] {entry.key:<30}", nl=False)
        r = verify_entry(entry, src, lookup)
        results.append(r)
        # A book check can make several calls per entry, so the run total counts calls.
        was_network = network_calls() > calls_before
        status_counts[r.status] += 1
        sim = f"{r.similarity:.2f}" if r.similarity is not None else "  - "
        net_or_hit = "net" if was_network else "hit"
        running = " ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
        typer.echo(f" {r.status:<14} {r.method:<7} sim={sim} [{net_or_hit}]  {running}")

    elapsed = time.monotonic() - t0
    total_retries = retries()
    total_calls = network_calls()
    typer.echo(
        f"\nDone in {elapsed:.1f}s — {total_calls} network call(s), "
        f"{max(len(entries) - total_calls, 0)} cache hit(s), "
        f"{total_retries} retr{'y' if total_retries == 1 else 'ies'}."
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


# --- config ------------------------------------------------------------------

# What a setting is when nothing sets it: the clients' own defaults.
_SETTING_DEFAULTS = {
    "OPENALEX_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
    "OPENALEX_MIN_INTERVAL": _MIN_INTERVAL_DEFAULT,
    "CROSSREF_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
    "OPENLIBRARY_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
    "OPENLIBRARY_MIN_INTERVAL": str(OPENLIBRARY_MIN_INTERVAL),
    "LOC_MAX_RETRIES": str(DEFAULT_MAX_RETRIES),
    "LOC_MIN_INTERVAL": str(LOC_MIN_INTERVAL),
}


def _setting_default(env_name: str) -> str:
    """The value a setting takes when nothing sets it.

    Crossref's pacing default follows whether a `mailto` is configured.
    """
    if env_name == "CROSSREF_MIN_INTERVAL":
        polite = is_polite(os.environ.get("CROSSREF_MAILTO"))
        return str(CROSSREF_POLITE_MIN_INTERVAL if polite else CROSSREF_MIN_INTERVAL)
    return _SETTING_DEFAULTS.get(env_name, "(none)")


@app.command()
def drift(cache: Path) -> None:
    """Report keys the records in a JSONL cache carry that the models lack.

    Rows are grouped by the request host in their key into `crossref-work`,
    `crossref-search`, `openalex-work`, and `openalex-search`. Each undeclared
    dotted path is printed with the share of records carrying it, most common
    first.
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

    Each value is tagged with its source: `flag`, `env` (shell or `.env`),
    `project`, `user`, or `default`. Pass `--cache-dir` to preview a flag.
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
            # Never print a credential.
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
    for name in ("openalex", "crossref", "openlibrary", "loc"):
        typer.echo(f"{name + ' cache:':<19} {resolve_cache_path(name, root)}")
    verify_root = _verify_root(cache_dir)
    typer.echo(
        f"verify output:   {verify_root / '<bib-dir>[-<bib-stem>]' / '<source>'}/"
    )


# --- cache maintenance ------------------------------------------------------
#
# Consolidation is always explicit: only `cache merge` merges anything.

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

    A missing directory is an error, not an empty cache (a dropped mount or a typo).
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
    """Print what a merge read and decided.

    The 404-versus-record counts print even at zero; the rest only when nonzero.
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

    Recursive: the shared `<source>.jsonl` caches and every per-run `verify`
    cache are counted together. Rows are grouped by the host that answered
    them, not by file name. Distinct-key counts use the newest row per key.
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

    Reads every `<cache-dir>/**/*.jsonl` and any `--extra` file, then rewrites
    `<cache-dir>/<source>.jsonl` with one line per key. Each row goes to the
    source its host names, wherever it was found. Across files the newest `ts`
    wins; within one file the later line does. Winners keep their own `ts`.

    Dry run by default. Inputs are never modified.
    """
    root = _cache_root(cache_dir)
    extras = [_anchor_or_exit(p, Path.cwd()) for p in extra or []]
    for path in extras:
        _require_file(path, label="--extra ", code=2)
    # `merge_caches` reads a file named twice only once.
    inputs = _find(root, "*.jsonl") + extras
    typer.echo(f"cache dir: {root}")
    # Merge every source before writing: the targets are inputs too.
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

    The same merge as `cache merge` over one file. Rows from neither API are
    reported but kept, unlike a merge into a source's cache.

    Dry run by default. `--write` replaces the file atomically.
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
    # Crossref never indexes DataCite DOIs; skip the guaranteed 404.
    registrar = datacite_registrar(normalize_doi(doi))
    if registrar is not None:
        typer.echo(
            f"{doi}: {datacite_note(registrar)}; "
            f"try `citefinder doi {doi}`, which uses OpenAlex",
            err=True,
        )
        raise typer.Exit(code=1)
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
    """Search Crossref by bibliographic query (author + title + year)."""
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
