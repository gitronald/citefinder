"""Verification orchestration: bib entry vs metadata source.

`Source` wraps a Crossref or OpenAlex client behind a small,
shape-independent surface — `lookup_doi`, `search`, `to_work`,
`candidate_doi`, `candidate_title` — so `verify_entry` doesn't need
to know which source it's talking to. The two source-specific
adapters live in `citefinder.adapters`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from citefinder.adapters import (
    crossref_to_work,
    openalex_doi,
    openalex_to_work,
)
from citefinder.bib import (
    Entry,
    build_search_query,
    build_title_query,
    citation_from_entry,
    strip_braces,
)
from citefinder.client import CrossrefClient
from citefinder.openalex import OpenAlexClient
from citefinder.signals import (
    Status,
    Work,
    compute_signals,
    status_from_signals,
    title_similarity,
)

# Entry types whose sources usually aren't indexed in academic metadata
# services — a miss is expected, not a failure. We still try the search
# in case a blog post or report ended up indexed.
SKIP_SOURCE_TYPES = {"online", "misc"}

# Threshold for "this title looks like a real match." The metadata source
# returns ranked results regardless of relevance, so cheap fuzzy similarity
# on the top hit is the simplest way to reject obviously-wrong matches.
TITLE_MATCH_THRESHOLD = 0.55


@dataclass
class Result:
    key: str
    etype: str
    title: str
    year: str
    bib_doi: str | None
    method: str  # "doi" | "search" | "skipped"
    status: Status
    matched_doi: str | None = None
    matched_title: str | None = None
    similarity: float | None = None
    note: str = ""
    candidates: list[dict[str, str]] = field(default_factory=list)
    signals: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Source:
    """Shape-independent wrapper around CrossrefClient / OpenAlexClient.

    The two clients differ in (a) search method name, (b) JSON field
    names, and (c) DOI representation in search hits. `Source` hides
    those differences so `verify_entry` stays source-agnostic.
    """

    name: str  # "crossref" | "openalex"
    client: CrossrefClient | OpenAlexClient

    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        return self.client.lookup_doi(doi)

    def search(self, entry: Entry, rows: int = 3) -> list[dict[str, Any]]:
        if self.name == "crossref":
            assert isinstance(self.client, CrossrefClient)
            return self.client.search_bibliographic(
                build_search_query(entry), rows=rows
            )
        # OpenAlex: title-only search. The default `?search=` runs full-text
        # over title + abstract, which is too noisy for our author+title+year
        # query shape, so we use the title-only filter via `search_title`.
        assert isinstance(self.client, OpenAlexClient)
        title = build_title_query(entry)
        if not title:
            return []
        return self.client.search_title(title, rows=rows)

    def to_work(self, raw: dict[str, Any] | None) -> Work | None:
        return (
            crossref_to_work(raw) if self.name == "crossref" else openalex_to_work(raw)
        )

    def candidate_doi(self, item: dict[str, Any]) -> str:
        if self.name == "crossref":
            return item.get("DOI", "") or ""
        return openalex_doi(item.get("doi"))

    def candidate_title(self, item: dict[str, Any]) -> str:
        if self.name == "crossref":
            titles = item.get("title") or []
            return titles[0] if titles else ""
        return item.get("display_name") or ""

    def cache_size(self) -> int:
        cache = getattr(self.client, "cache", None)
        return len(cache) if cache is not None else 0


def verify_entry(entry: Entry, source: Source) -> Result:
    title = strip_braces(entry.fields.get("title", ""))
    year = strip_braces(entry.fields.get("year", ""))
    # `... or None` collapses both a missing `doi` field and a present-but-
    # empty one (`doi = {}`) to None, so the reported `bib_doi` doesn't
    # conflate "no DOI" with "blank DOI".
    bib_doi = strip_braces(entry.fields.get("doi", "")) or None
    citation = citation_from_entry(entry)

    base = Result(
        key=entry.key,
        etype=entry.etype,
        title=title,
        year=year,
        bib_doi=bib_doi,
        method="",
        status=Status.ERROR,
    )

    # If a DOI is in the bib, resolve it AND check four signals (title / year /
    # first-author / container) against the source record. DOI existence
    # isn't enough — a typoed or wrong DOI can resolve to a different work.
    if bib_doi:
        base.method = "doi"
        try:
            raw = source.lookup_doi(bib_doi)
        except Exception as e:
            base.status = Status.ERROR
            base.note = f"DOI lookup failed: {e}"
            return base
        work = source.to_work(raw)
        if work is None:
            base.status = Status.DOI_NOT_FOUND
            base.note = "DOI not in source (404) — common for arXiv / preprint DOIs"
            return base
        base.matched_doi = bib_doi
        base.matched_title = work.title
        base.signals = compute_signals(citation, work)
        base.similarity = base.signals["title"].get("sim")
        base.status, base.note = status_from_signals(base.signals)
        return base

    # No DOI — bibliographic search. Skip-source types still get tried but
    # are reported under their own bucket.
    base.method = "search"
    if not entry.fields.get("title") and not entry.fields.get("author"):
        base.status = Status.ERROR
        base.note = "no author/title/year to query"
        return base

    try:
        items = source.search(entry, rows=3)
    except Exception as e:
        base.status = Status.ERROR
        base.note = f"search failed: {e}"
        return base

    # Candidate selection still uses raw title-sim because the candidate
    # report shows the source's stored title (not the reassembled one) and
    # because it lets us short-circuit before paying the adapter cost.
    candidates: list[dict[str, str]] = []
    best_sim = 0.0
    best_item: dict[str, Any] | None = None
    for item in items:
        item_title = source.candidate_title(item)
        sim = title_similarity(title, item_title)
        candidates.append(
            {
                "doi": source.candidate_doi(item),
                "title": item_title,
                "similarity": f"{sim:.2f}",
            }
        )
        if sim > best_sim:
            best_sim = sim
            best_item = item
    base.candidates = candidates

    if entry.etype in SKIP_SOURCE_TYPES and best_sim < TITLE_MATCH_THRESHOLD:
        base.status = Status.SKIP_SOURCE
        base.note = f"@{entry.etype}: not expected in source; verify via URL"
        return base

    if best_item is not None and best_sim >= TITLE_MATCH_THRESHOLD:
        work = source.to_work(best_item)
        assert work is not None  # best_item is a real record
        base.matched_doi = source.candidate_doi(best_item)
        base.matched_title = work.title
        base.signals = compute_signals(citation, work)
        base.similarity = best_sim
        base.status, base.note = status_from_signals(base.signals)
        # For @online / @misc, the canonical source is the URL — any
        # search hit other than a clean signal-pass match is almost
        # certainly a derived artifact (a reprinted policy, a chapter that
        # cites the report, etc.). Route those to skip-source so the
        # report doesn't suggest a misleading DOI.
        if entry.etype in SKIP_SOURCE_TYPES and base.status != Status.MATCHED:
            signal_note = base.note
            base.status = Status.SKIP_SOURCE
            base.note = (
                f"@{entry.etype}: signals disagree ({signal_note}); verify via URL"
            )
            base.matched_doi = None
            base.matched_title = None
        return base

    if entry.etype in SKIP_SOURCE_TYPES:
        base.status = Status.SKIP_SOURCE
        base.note = f"@{entry.etype}: no plausible hit; verify via URL"
    else:
        base.status = Status.UNMATCHED
        base.note = "no plausible source hit"
    return base
