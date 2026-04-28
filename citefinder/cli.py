"""citefinder CLI."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from citefinder.client import CrossrefClient

app = typer.Typer(help="Crossref reference lookups with local JSONL caching.")

DEFAULT_CACHE = Path.home() / ".cache" / "citefinder" / "crossref.jsonl"


def _client(cache: Path) -> CrossrefClient:
    return CrossrefClient(cache_path=cache)


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
