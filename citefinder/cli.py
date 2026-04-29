"""citefinder CLI.

Top-level commands default to OpenAlex — it indexes Crossref *plus* arXiv,
preprints, and repository deposits, so a single `citefinder doi` or
`citefinder search` works for the broadest range of citations. Crossref
remains accessible via the `crossref` subcommand for its own workflows
(book-chapter lookup, the canonical published-deposit metadata).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

from citefinder.client import CrossrefClient
from citefinder.openalex import OpenAlexClient

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
