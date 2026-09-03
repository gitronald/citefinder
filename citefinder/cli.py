"""citefinder CLI.

Top-level commands default to OpenAlex — it indexes Crossref *plus* arXiv,
preprints, and repository deposits, so a single `citefinder doi` or
`citefinder search` works for the broadest range of citations. Crossref
remains accessible via the `crossref` subcommand for its own workflows
(book-chapter lookup, the canonical published-deposit metadata).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import tomllib
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from operator import itemgetter
from pathlib import Path
from typing import Any

import typer
from dotenv import find_dotenv, load_dotenv

from citefinder import install as install_mod
from citefinder.bib import parse_entries
from citefinder.bib_table import bib_to_table, table_to_bib
from citefinder.client import CrossrefClient
from citefinder.openalex import OpenAlexClient
from citefinder.verify import Result, Source, verify_entry

# Load `.env` from the current working directory (or any parent) so users can
# keep `OPENALEX_API_KEY` out of their shell rc and out of bash history.
# Library users are unaffected — this only runs when the CLI is invoked.
load_dotenv(find_dotenv(usecwd=True))


def _load_user_config() -> None:
    """Read `~/.config/citefinder/config.toml` (honors `$XDG_CONFIG_HOME`)
    and populate env vars for any values not already set. Lowest-priority
    fallback — `.env` and shell env still win — so users can store keys
    once per machine while overriding per-shell or per-project.

    Expected format:
        [openalex]
        api_key = "oa_pk_..."
        mailto = "you@example.com"
        max_retries = 3
        min_interval = 0.1

        [crossref]
        mailto = "you@example.com"
        max_retries = 3
        min_interval = 0
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_dir = Path(xdg) if xdg else Path.home() / ".config"
    config_path = config_dir / "citefinder" / "config.toml"
    if not config_path.is_file():
        return
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # A broken credentials file must never take the whole CLI down —
        # `citefinder skill` is the only copy of the skill instructions on
        # this machine. Warn and fall through to env vars and flags.
        typer.echo(f"warning: ignoring {config_path}: {exc}", err=True)
        return
    mappings = {
        "OPENALEX_API_KEY": ("openalex", "api_key"),
        "OPENALEX_MAILTO": ("openalex", "mailto"),
        "OPENALEX_MAX_RETRIES": ("openalex", "max_retries"),
        "OPENALEX_MIN_INTERVAL": ("openalex", "min_interval"),
        "CROSSREF_MAILTO": ("crossref", "mailto"),
        "CROSSREF_MAX_RETRIES": ("crossref", "max_retries"),
        "CROSSREF_MIN_INTERVAL": ("crossref", "min_interval"),
    }
    for env_name, (section, key) in mappings.items():
        value = (config.get(section) or {}).get(key)
        # `is not None`, not truthiness: `max_retries = 0` is a real setting.
        if value is not None and value != "" and env_name not in os.environ:
            os.environ[env_name] = str(value)


_load_user_config()

app = typer.Typer(
    help="OpenAlex (default) + Crossref reference lookups with local JSONL caching."
)
crossref_app = typer.Typer(
    help="Crossref lookups (canonical published-deposit metadata)."
)
app.add_typer(crossref_app, name="crossref")

DEFAULT_OPENALEX_CACHE = Path.home() / ".cache" / "citefinder" / "openalex.jsonl"
DEFAULT_CROSSREF_CACHE = Path.home() / ".cache" / "citefinder" / "crossref.jsonl"

OpenAlexCacheOption = typer.Option(DEFAULT_OPENALEX_CACHE, help="JSONL cache path.")
CrossrefCacheOption = typer.Option(DEFAULT_CROSSREF_CACHE, help="JSONL cache path.")
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
    check entirely, so this is the one place the bound is enforced.
    """
    if not math.isfinite(value) or value < 0:
        typer.echo(
            f"Error: {name} must be a finite number >= 0, got {value!r}", err=True
        )
        raise typer.Exit(code=2)
    return value


def _source_client_kwargs(
    source: str, max_retries: int | None, min_interval: float | None
) -> dict[str, Any]:
    """Like `_client_kwargs`, falling back to the chosen source's env vars.

    `verify` picks its source at runtime, so its flags can't bind a single
    `envvar`; an unset flag reads `<SOURCE>_MAX_RETRIES` /
    `<SOURCE>_MIN_INTERVAL` (which config.toml also feeds) so
    `verify --source crossref` honors the `[crossref]` section.
    """
    prefix = source.upper()
    if max_retries is None:
        max_retries = _env_number(f"{prefix}_MAX_RETRIES", int)
    if min_interval is None:
        min_interval = _env_number(f"{prefix}_MIN_INTERVAL", float)
    return _client_kwargs(max_retries, min_interval)


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


def _emit(result: object) -> None:
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


# --- top-level (OpenAlex) ---------------------------------------------------


@app.command()
def doi(
    doi: str,
    cache: Path = OpenAlexCacheOption,
    mailto: str | None = OpenAlexMailtoOption,
    api_key: str | None = ApiKeyOption,
    max_retries: int | None = OpenAlexMaxRetriesOption,
    min_interval: float | None = OpenAlexMinIntervalOption,
) -> None:
    """Look up a single DOI via OpenAlex."""
    client = OpenAlexClient(
        cache_path=cache,
        mailto=mailto,
        api_key=api_key,
        **_client_kwargs(max_retries, min_interval),
    )
    result = client.lookup_doi(doi)
    if result is None:
        typer.echo(f"not found: {doi}", err=True)
        raise typer.Exit(code=1)
    _emit(result)


@app.command()
def search(
    title: str,
    rows: int = RowsOption,
    cache: Path = OpenAlexCacheOption,
    mailto: str | None = OpenAlexMailtoOption,
    api_key: str | None = ApiKeyOption,
    max_retries: int | None = OpenAlexMaxRetriesOption,
    min_interval: float | None = OpenAlexMinIntervalOption,
) -> None:
    """Search OpenAlex by title (title-only filter; tuned for citation lookup)."""
    client = OpenAlexClient(
        cache_path=cache,
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

    df = bib_to_table(bib_file.read_text())

    if fields:
        wanted = ["key", "entry_type", *(f.strip() for f in fields.split(","))]
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
    bib = table_to_bib(df)
    if out:
        out.write_text(bib)
    else:
        sys.stdout.write(bib)


@app.command()
def verify(
    bib_file: Path,
    source: str = typer.Option(
        "openalex",
        "--source",
        help="Metadata source to verify against.",
        case_sensitive=False,
    ),
    mailto: str | None = OpenAlexMailtoOption,
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output dir (default: data/citefinder/<bib-stem>/<source>/).",
    ),
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
    if source not in ("crossref", "openalex"):
        typer.echo("Error: --source must be 'crossref' or 'openalex'", err=True)
        raise typer.Exit(code=2)
    if not bib_file.is_file():
        typer.echo(f"Error: {bib_file} is not a file", err=True)
        raise typer.Exit(code=1)

    # Default output: cwd/data/citefinder/<bib-stem>/<source>/. Per-source
    # subdir lets crossref and openalex outputs coexist for side-by-side
    # comparison without collision.
    stem = bib_file.stem
    if stem == "refs":
        stem = bib_file.parent.name
    out_dir = out or (Path.cwd() / "data" / "citefinder" / stem / source)
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_path = out_dir / f"{source}.jsonl"
    knobs = _source_client_kwargs(source, max_retries, min_interval)
    if source == "crossref":
        src = Source(
            name="crossref", client=CrossrefClient(cache_path=cache_path, **knobs)
        )
    else:
        src = Source(
            name="openalex",
            client=OpenAlexClient(cache_path=cache_path, mailto=mailto, **knobs),
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
        # `--local` narrows the check to the per-repo location; bare `--check`
        # resolves whichever copy is installed (global first, matching Claude
        # Code's own precedence) and judges it by where it sits.
        where: Path | None
        if local:
            where = install_mod.skill_path(root, mode)
            status = install_mod.check_mode(root, version, mode)
        else:
            found = install_mod.resolve_installed(root)
            where = found[0] if found else None
            if found is not None:
                mode = found[1]
                status = install_mod.check_mode(root, version, mode)
            else:
                status = "missing"
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

    try:
        written = install_mod.write_skill(root, version, mode)
    except OSError as exc:
        # e.g. a plain file squatting where `.claude/` should be, or a
        # directory at SKILL.md itself — report it, don't traceback.
        typer.echo(f"Error: cannot write {path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"wrote {written} (citefinder {version}, mode={mode})")


# --- crossref subcommand ----------------------------------------------------


@crossref_app.command("doi")
def crossref_doi(
    doi: str,
    cache: Path = CrossrefCacheOption,
    mailto: str | None = CrossrefMailtoOption,
    max_retries: int | None = CrossrefMaxRetriesOption,
    min_interval: float | None = CrossrefMinIntervalOption,
) -> None:
    """Look up a single DOI via Crossref."""
    client = CrossrefClient(
        cache_path=cache, mailto=mailto, **_client_kwargs(max_retries, min_interval)
    )
    result = client.lookup_doi(doi)
    if result is None:
        typer.echo(f"not found: {doi}", err=True)
        raise typer.Exit(code=1)
    _emit(result)


@crossref_app.command("search")
def crossref_search(
    query: str,
    rows: int = RowsOption,
    cache: Path = CrossrefCacheOption,
    mailto: str | None = CrossrefMailtoOption,
    max_retries: int | None = CrossrefMaxRetriesOption,
    min_interval: float | None = CrossrefMinIntervalOption,
) -> None:
    """Search Crossref by free-form bibliographic query (author + title + year)."""
    client = CrossrefClient(
        cache_path=cache, mailto=mailto, **_client_kwargs(max_retries, min_interval)
    )
    items = client.search_bibliographic(query, rows=rows)
    _emit(items)


@crossref_app.command("chapter")
def crossref_chapter(
    book_doi: str,
    chapter: str,
    cache: Path = CrossrefCacheOption,
    mailto: str | None = CrossrefMailtoOption,
    max_retries: int | None = CrossrefMaxRetriesOption,
    min_interval: float | None = CrossrefMinIntervalOption,
) -> None:
    """Look up a book chapter by `{book_doi}.{NNN}` pattern."""
    chapter_arg: int | str = int(chapter) if chapter.isdigit() else chapter
    client = CrossrefClient(
        cache_path=cache, mailto=mailto, **_client_kwargs(max_retries, min_interval)
    )
    result = client.lookup_book_chapter(book_doi, chapter_arg)
    if result is None:
        typer.echo(f"not found: {book_doi}.{chapter}", err=True)
        raise typer.Exit(code=1)
    _emit(result)
