"""citefinder — Crossref and OpenAlex reference lookups with local JSONL caching."""

from typing import TYPE_CHECKING, Any

from citefinder.adapters import crossref_to_work, openalex_to_work
from citefinder.bib import Entry, parse_entries
from citefinder.cache import (
    JsonlCache,
    MergeStats,
    SourceStats,
    merge_caches,
    read_records,
    summarize_caches,
    write_records,
)
from citefinder.client import CrossrefClient
from citefinder.config import resolve_cache_path
from citefinder.openalex import OpenAlexClient, is_arxiv_doi, reconstruct_abstract
from citefinder.signals import (
    BibCitation,
    Status,
    Work,
    compute_signals,
    status_from_signals,
)
from citefinder.verify import Result, Source, verify_entry

if TYPE_CHECKING:
    # Imported eagerly for type checkers and IDEs only; at runtime the two
    # names below resolve through `__getattr__`.
    from citefinder.bib_table import bib_to_table, table_to_bib

# `bib_table` is the one module that pulls in polars, which costs more to
# import than everything else in the package combined. Deferring it to first
# attribute access keeps `import citefinder` (and every CLI command that
# never tabulates a `.bib`) off that bill, while `from citefinder import
# bib_to_table` still works — Python falls back to a module-level
# `__getattr__` (PEP 562) for a name the module body did not bind.
_LAZY = {"bib_to_table", "table_to_bib"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from citefinder import bib_table

        return getattr(bib_table, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # `__getattr__` names are invisible to the default `dir()`, which lists
    # only what the module body bound; spell them out so tab-completion and
    # `dir(citefinder)` still show the full public surface.
    return sorted(__all__)


__all__ = [
    "BibCitation",
    "CrossrefClient",
    "Entry",
    "JsonlCache",
    "MergeStats",
    "OpenAlexClient",
    "Result",
    "Source",
    "SourceStats",
    "Status",
    "Work",
    "bib_to_table",
    "compute_signals",
    "crossref_to_work",
    "is_arxiv_doi",
    "merge_caches",
    "openalex_to_work",
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
