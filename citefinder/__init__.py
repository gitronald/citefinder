"""citefinder — Crossref and OpenAlex reference lookups with local JSONL caching."""

from citefinder.adapters import crossref_to_work, openalex_to_work
from citefinder.bib import Entry, parse_entries
from citefinder.cache import JsonlCache
from citefinder.client import CrossrefClient
from citefinder.openalex import OpenAlexClient, is_arxiv_doi, reconstruct_abstract
from citefinder.signals import (
    BibCitation,
    Status,
    Work,
    compute_signals,
    status_from_signals,
)
from citefinder.verify import Result, Source, verify_entry

__all__ = [
    "BibCitation",
    "CrossrefClient",
    "Entry",
    "JsonlCache",
    "OpenAlexClient",
    "Result",
    "Source",
    "Status",
    "Work",
    "compute_signals",
    "crossref_to_work",
    "is_arxiv_doi",
    "openalex_to_work",
    "parse_entries",
    "reconstruct_abstract",
    "status_from_signals",
    "verify_entry",
]
