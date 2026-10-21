"""citefinder — Crossref and OpenAlex reference lookups with local JSONL caching."""

from typing import TYPE_CHECKING, Any

from citefinder.adapters import crossref_to_work, openalex_to_work
from citefinder.bib import Entry, parse_entries
from citefinder.books import BookField, BookLookup, BookRecord, book_record
from citefinder.cache import (
    JsonlCache,
    LayeredCache,
    MergeStats,
    SourceStats,
    merge_caches,
    read_records,
    summarize_caches,
    write_records,
)
from citefinder.client import CrossrefClient
from citefinder.config import resolve_cache_path
from citefinder.loc import LocClient, marc_book, parse_marcxml
from citefinder.openalex import (
    OpenAlexClient,
    datacite_note,
    datacite_registrar,
    is_arxiv_doi,
    reconstruct_abstract,
)
from citefinder.openlibrary import OpenLibraryClient, normalize_isbn
from citefinder.signals import (
    BibCitation,
    Status,
    Work,
    compute_signals,
    status_from_signals,
)
from citefinder.verify import Result, Source, verify_entry

if TYPE_CHECKING:
    # For type checkers only; at runtime these resolve through `__getattr__`.
    from citefinder.bib_table import bib_to_table, table_to_bib

# `bib_table` imports polars, which is slow; load it on first access.
_LAZY = {"bib_to_table", "table_to_bib"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from citefinder import bib_table

        return getattr(bib_table, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # List the lazy names too, which the default `dir()` omits.
    return sorted(__all__)


__all__ = [
    "BibCitation",
    "BookField",
    "BookLookup",
    "BookRecord",
    "CrossrefClient",
    "Entry",
    "JsonlCache",
    "LayeredCache",
    "LocClient",
    "MergeStats",
    "OpenAlexClient",
    "OpenLibraryClient",
    "Result",
    "Source",
    "SourceStats",
    "Status",
    "Work",
    "bib_to_table",
    "book_record",
    "compute_signals",
    "crossref_to_work",
    "datacite_note",
    "datacite_registrar",
    "is_arxiv_doi",
    "marc_book",
    "merge_caches",
    "normalize_isbn",
    "openalex_to_work",
    "parse_marcxml",
    "parse_entries",
    "read_records",
    "reconstruct_abstract",
    "resolve_cache_path",
    "status_from_signals",
    "summarize_caches",
    "table_to_bib",
    "verify_entry",
    "write_records",
]
