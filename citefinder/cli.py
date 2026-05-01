"""citefinder CLI.

Top-level commands default to OpenAlex — it indexes Crossref *plus* arXiv,
preprints, and repository deposits, so a single `citefinder doi` or
`citefinder search` works for the broadest range of citations. Crossref
remains accessible via the `crossref` subcommand for its own workflows
(book-chapter lookup, the canonical published-deposit metadata).
"""

from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

from citefinder.bib import first_author_surname, parse_entries, strip_braces
from citefinder.client import CrossrefClient
from citefinder.openalex import OpenAlexClient
from citefinder.verify import Result, Source, verify_entry

# Load `.env` from the current working directory (or any parent) so users can
# keep `OPENALEX_API_KEY` out of their shell rc and out of bash history.
# Library users are unaffected — this only runs when the CLI is invoked.
load_dotenv(find_dotenv(usecwd=True))

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
MailtoOption = typer.Option(
    None, help="Email for the source's polite pool (faster, higher quota)."
)
ApiKeyOption = typer.Option(
    None,
    "--api-key",
    envvar="OPENALEX_API_KEY",
    help="OpenAlex API key (also read from OPENALEX_API_KEY env or .env).",
)


def _emit(result: object) -> None:
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


# --- top-level (OpenAlex) ---------------------------------------------------


@app.command()
def doi(
    doi: str,
    cache: Path = OpenAlexCacheOption,
    mailto: str | None = MailtoOption,
    api_key: str | None = ApiKeyOption,
) -> None:
    """Look up a single DOI via OpenAlex."""
    client = OpenAlexClient(cache_path=cache, mailto=mailto, api_key=api_key)
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
    mailto: str | None = MailtoOption,
    api_key: str | None = ApiKeyOption,
) -> None:
    """Search OpenAlex by title (title-only filter; tuned for citation lookup)."""
    client = OpenAlexClient(cache_path=cache, mailto=mailto, api_key=api_key)
    items = client.search_title(title, rows=rows)
    _emit(items)


# --- bib parsing & verification --------------------------------------------


PARSE_COLUMNS = ["key", "etype", "title", "author", "year", "doi", "container"]


@app.command()
def parse(
    bib_file: Path,
    out: Path | None = typer.Option(
        None, "--out", help="Write CSV here. Defaults to stdout."
    ),
) -> None:
    """Parse a `.bib` file and emit CSV (no network calls).

    `author` is the first-author surname (the form used downstream for
    matching). `container` is `journal` or `booktitle`, whichever the
    entry has.
    """
    if not bib_file.is_file():
        typer.echo(f"Error: {bib_file} is not a file", err=True)
        raise typer.Exit(code=1)

    entries = parse_entries(bib_file.read_text())
    sink = out.open("w", newline="") if out else sys.stdout
    try:
        writer = csv.writer(sink)
        writer.writerow(PARSE_COLUMNS)
        for e in entries:
            writer.writerow(
                [
                    e.key,
                    e.etype,
                    strip_braces(e.fields.get("title", "")),
                    first_author_surname(e.fields.get("author", "")),
                    strip_braces(e.fields.get("year", "")),
                    strip_braces(e.fields.get("doi", "")),
                    strip_braces(
                        e.fields.get("journal") or e.fields.get("booktitle") or ""
                    ),
                ]
            )
    finally:
        if out:
            sink.close()


@app.command()
def verify(
    bib_file: Path,
    source: str = typer.Option(
        "openalex",
        "--source",
        help="Metadata source to verify against.",
        case_sensitive=False,
    ),
    mailto: str | None = MailtoOption,
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output dir (default: data/citefinder/<bib-stem>/<source>/).",
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
    if source == "crossref":
        src = Source(name="crossref", client=CrossrefClient(cache_path=cache_path))
    else:
        src = Source(
            name="openalex",
            client=OpenAlexClient(cache_path=cache_path, mailto=mailto),
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
    typer.echo(
        f"\nDone in {elapsed:.1f}s — {network_calls} network call(s), "
        f"{len(entries) - network_calls} cache hit(s)."
    )
    typer.echo(
        "Final counts — "
        + ", ".join(
            f"{k}: {v}" for k, v in sorted(status_counts.items(), key=lambda kv: -kv[1])
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


# --- crossref subcommand ----------------------------------------------------


@crossref_app.command("doi")
def crossref_doi(
    doi: str,
    cache: Path = CrossrefCacheOption,
    mailto: str | None = MailtoOption,
) -> None:
    """Look up a single DOI via Crossref."""
    result = CrossrefClient(cache_path=cache, mailto=mailto).lookup_doi(doi)
    if result is None:
        typer.echo(f"not found: {doi}", err=True)
        raise typer.Exit(code=1)
    _emit(result)


@crossref_app.command("search")
def crossref_search(
    query: str,
    rows: int = RowsOption,
    cache: Path = CrossrefCacheOption,
    mailto: str | None = MailtoOption,
) -> None:
    """Search Crossref by free-form bibliographic query (author + title + year)."""
    client = CrossrefClient(cache_path=cache, mailto=mailto)
    items = client.search_bibliographic(query, rows=rows)
    _emit(items)


@crossref_app.command("chapter")
def crossref_chapter(
    book_doi: str,
    chapter: str,
    cache: Path = CrossrefCacheOption,
    mailto: str | None = MailtoOption,
) -> None:
    """Look up a book chapter by `{book_doi}.{NNN}` pattern."""
    chapter_arg: int | str = int(chapter) if chapter.isdigit() else chapter
    client = CrossrefClient(cache_path=cache, mailto=mailto)
    result = client.lookup_book_chapter(book_doi, chapter_arg)
    if result is None:
        typer.echo(f"not found: {book_doi}.{chapter}", err=True)
        raise typer.Exit(code=1)
    _emit(result)
