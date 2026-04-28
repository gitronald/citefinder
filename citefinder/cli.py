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


def _client(cache: Path) -> CrossrefClient:
    return CrossrefClient(cache_path=cache)


def _openalex_client(
    cache: Path, mailto: str | None, api_key: str | None
) -> OpenAlexClient:
    return OpenAlexClient(cache_path=cache, mailto=mailto, api_key=api_key)


@app.command()
def doi(
    doi: str,
    cache: Path = typer.Option(DEFAULT_CACHE, help="JSONL cache path."),
) -> None:
    """Look up a single DOI."""
    result = _client(cache).lookup_doi(doi)
    if result is None:
        typer.echo(f"not found: {doi}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command()
def search(
    query: str,
    rows: int = typer.Option(3, help="Number of results to return."),
    cache: Path = typer.Option(DEFAULT_CACHE, help="JSONL cache path."),
) -> None:
    """Search Crossref by free-form bibliographic query."""
    items = _client(cache).search_bibliographic(query, rows=rows)
    typer.echo(json.dumps(items, indent=2, ensure_ascii=False))


@app.command()
def chapter(
    book_doi: str,
    chapter: str,
    cache: Path = typer.Option(DEFAULT_CACHE, help="JSONL cache path."),
) -> None:
    """Look up a book chapter by `{book_doi}.{NNN}` pattern."""
    chapter_arg: int | str = int(chapter) if chapter.isdigit() else chapter
    result = _client(cache).lookup_book_chapter(book_doi, chapter_arg)
    if result is None:
        typer.echo(f"not found: {book_doi}.{chapter}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@openalex_app.command("doi")
def openalex_doi(
    doi: str,
    cache: Path = typer.Option(DEFAULT_OPENALEX_CACHE, help="JSONL cache path."),
    mailto: str | None = typer.Option(
        None, help="Email for OpenAlex polite pool (faster, higher quota)."
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="OPENALEX_API_KEY",
        help="OpenAlex API key (also read from OPENALEX_API_KEY env or .env).",
    ),
) -> None:
    """Look up a single DOI via OpenAlex."""
    result = _openalex_client(cache, mailto, api_key).lookup_doi(doi)
    if result is None:
        typer.echo(f"not found: {doi}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@openalex_app.command("search")
def openalex_search(
    query: str,
    rows: int = typer.Option(3, help="Number of results to return."),
    cache: Path = typer.Option(DEFAULT_OPENALEX_CACHE, help="JSONL cache path."),
    mailto: str | None = typer.Option(
        None, help="Email for OpenAlex polite pool (faster, higher quota)."
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="OPENALEX_API_KEY",
        help="OpenAlex API key (also read from OPENALEX_API_KEY env or .env).",
    ),
) -> None:
    """Search OpenAlex by free-text query (title + abstract)."""
    items = _openalex_client(cache, mailto, api_key).search(query, rows=rows)
    typer.echo(json.dumps(items, indent=2, ensure_ascii=False))
