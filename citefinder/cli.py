"""citefinder CLI."""

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
    help="Crossref and OpenAlex reference lookups with local JSONL caching."
)
openalex_app = typer.Typer(
    help="OpenAlex lookups (Crossref fallback / preprint coverage)."
)
app.add_typer(openalex_app, name="openalex")

DEFAULT_CACHE = Path.home() / ".cache" / "citefinder" / "crossref.jsonl"
DEFAULT_OPENALEX_CACHE = Path.home() / ".cache" / "citefinder" / "openalex.jsonl"

CacheOption = typer.Option(DEFAULT_CACHE, help="JSONL cache path.")
OpenAlexCacheOption = typer.Option(DEFAULT_OPENALEX_CACHE, help="JSONL cache path.")
RowsOption = typer.Option(3, help="Number of results to return.")
MailtoOption = typer.Option(
    None, help="Email for OpenAlex polite pool (faster, higher quota)."
)
ApiKeyOption = typer.Option(
    None,
    "--api-key",
    envvar="OPENALEX_API_KEY",
    help="OpenAlex API key (also read from OPENALEX_API_KEY env or .env).",
)


def _emit(result: object) -> None:
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command()
def doi(doi: str, cache: Path = CacheOption) -> None:
    """Look up a single DOI."""
    result = CrossrefClient(cache_path=cache).lookup_doi(doi)
    if result is None:
        typer.echo(f"not found: {doi}", err=True)
        raise typer.Exit(code=1)
    _emit(result)


@app.command()
def search(query: str, rows: int = RowsOption, cache: Path = CacheOption) -> None:
    """Search Crossref by free-form bibliographic query."""
    items = CrossrefClient(cache_path=cache).search_bibliographic(query, rows=rows)
    _emit(items)


@app.command()
def chapter(book_doi: str, chapter: str, cache: Path = CacheOption) -> None:
    """Look up a book chapter by `{book_doi}.{NNN}` pattern."""
    chapter_arg: int | str = int(chapter) if chapter.isdigit() else chapter
    result = CrossrefClient(cache_path=cache).lookup_book_chapter(book_doi, chapter_arg)
    if result is None:
        typer.echo(f"not found: {book_doi}.{chapter}", err=True)
        raise typer.Exit(code=1)
    _emit(result)


@openalex_app.command("doi")
def openalex_doi(
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


@openalex_app.command("search")
def openalex_search(
    query: str,
    rows: int = RowsOption,
    cache: Path = OpenAlexCacheOption,
    mailto: str | None = MailtoOption,
    api_key: str | None = ApiKeyOption,
) -> None:
    """Search OpenAlex by free-text query (title + abstract)."""
    client = OpenAlexClient(cache_path=cache, mailto=mailto, api_key=api_key)
    items = client.search(query, rows=rows)
    _emit(items)
