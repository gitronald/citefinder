"""Verification orchestration: bib entry vs metadata source.

`Source` wraps a Crossref or OpenAlex client behind a small,
shape-independent surface — `lookup_doi`, `search`, `to_work`,
`candidate_doi`, `candidate_title` — so `verify_entry` doesn't need
to know which source it's talking to. The two source-specific
adapters live in `citefinder.adapters`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from citefinder.adapters import (
    crossref_full_title,
    crossref_to_work,
    openalex_doi,
    openalex_to_work,
)
from citefinder.bib import (
    Entry,
    build_search_query,
    build_title_query,
    citation_from_entry,
    normalize_doi,
    strip_braces,
)
from citefinder.client import CrossrefClient
from citefinder.models import CrossrefWork, OpenAlexWork
from citefinder.openalex import OpenAlexClient
from citefinder.signals import (
    MIN_TITLE_TOKENS,
    Status,
    Work,
    compute_signals,
    status_from_signals,
    title_similarity,
    title_tokens,
)

# Entry types whose sources usually aren't indexed in academic metadata
# services — a miss is expected, not a failure. We still try the search
# in case a blog post or report ended up indexed.
SKIP_SOURCE_TYPES = {"online", "misc"}

# Threshold for "this title looks like a real match." The metadata source
# returns ranked results regardless of relevance, so cheap fuzzy similarity
# on the top hit is the simplest way to reject obviously-wrong matches.
TITLE_MATCH_THRESHOLD = 0.55


SourceRecord = CrossrefWork | OpenAlexWork
"""A raw record from either source; `Source.name` says which."""


@dataclass
class Result:
    key: str
    etype: str
    title: str
    year: str
    bib_doi: str | None
    method: str  # "doi" | "search"
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

    def lookup_doi(self, doi: str) -> SourceRecord | None:
        return self.client.lookup_doi(doi)

    def search(self, entry: Entry, rows: int = 3) -> Sequence[SourceRecord]:
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

    def to_work(self, raw: SourceRecord | None) -> Work | None:
        if self.name == "crossref":
            return crossref_to_work(cast(CrossrefWork, raw))
        return openalex_to_work(cast(OpenAlexWork, raw))

    def candidate_doi(self, item: SourceRecord) -> str:
        if self.name == "crossref":
            return cast(CrossrefWork, item).get("DOI", "") or ""
        return openalex_doi(cast(OpenAlexWork, item).get("doi"))

    def candidate_title(self, item: SourceRecord) -> str:
        if self.name == "crossref":
            # Title and subtitle rejoined, as `crossref_to_work` does on the
            # DOI path: a split record must score like the work it is.
            return crossref_full_title(cast(CrossrefWork, item)) or ""
        return cast(OpenAlexWork, item).get("display_name") or ""

    def cache_size(self) -> int:
        cache = getattr(self.client, "cache", None)
        return len(cache) if cache is not None else 0


def verify_entry(entry: Entry, source: Source) -> Result:
    title = strip_braces(entry.fields.get("title", ""))
    year = strip_braces(entry.fields.get("year", ""))
    # `or None` folds an empty `doi = {}` into "no DOI", like a missing field.
    bib_doi = normalize_doi(entry.fields.get("doi", "")) or None

    base = Result(
        key=entry.key,
        etype=entry.etype,
        title=title,
        year=year,
        bib_doi=bib_doi,
        method="doi" if bib_doi else "search",
        status=Status.ERROR,
    )

    # The author field goes through bibtexparser's name parser, which raises
    # on malformed input (`Smith, Jane,`). Report that on the entry rather
    # than letting one typo abort a whole `verify` run.
    try:
        citation = citation_from_entry(entry)
    except Exception as e:
        base.note = f"could not parse bib fields: {e}"
        return base

    # If a DOI is in the bib, resolve it AND check four signals (title / year /
    # first-author / container) against the source record. DOI existence
    # isn't enough — a typoed or wrong DOI can resolve to a different work.
    if bib_doi:
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
        # The bib's own DOI resolved to this record, so one non-title
        # disagreement is metadata loss, not a different work (see
        # `status_from_signals`).
        base.status, base.note = status_from_signals(base.signals, doi_resolved=True)
        return base

    # No DOI — bibliographic search. Skip-source types still get tried but
    # are reported under their own bucket.
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

    # Candidate selection uses raw title similarity on each hit's title so
    # the adapter runs only for the hit that is finally chosen.
    candidates: list[dict[str, str]] = []
    best_sim = 0.0
    best_item: SourceRecord | None = None
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

    skip_type = entry.etype in SKIP_SOURCE_TYPES
    if skip_type and best_sim < TITLE_MATCH_THRESHOLD:
        base.status = Status.SKIP_SOURCE
        base.note = f"@{entry.etype}: not expected in source; verify via URL"
        return base

    # A one- or two-word bib title scores a perfect similarity against any
    # hit that contains those words, so it cannot pick a candidate on its own.
    # Fall through to unmatched and leave the candidates for a human.
    n_words = len(title_tokens(title))
    short_title = n_words < MIN_TITLE_TOKENS
    hit = best_item if best_sim >= TITLE_MATCH_THRESHOLD else None
    if hit is not None and not short_title:
        work = source.to_work(hit)
        assert work is not None  # hit is a real record
        # `or None`: a hit without a DOI is "no DOI", as on the DOI path.
        base.matched_doi = source.candidate_doi(hit) or None
        base.matched_title = work.title
        base.signals = compute_signals(citation, work)
        base.similarity = best_sim
        base.status, base.note = status_from_signals(base.signals)
        # For @online / @misc, the canonical source is the URL — any
        # search hit other than a clean signal-pass match is almost
        # certainly a derived artifact (a reprinted policy, a chapter that
        # cites the report, etc.). Route those to skip-source so the
        # report doesn't suggest a misleading DOI.
        if skip_type and base.status != Status.MATCHED:
            signal_note = base.note
            failed = any(s["verdict"] == "fail" for s in base.signals.values())
            why = "signals disagree" if failed else "signals do not confirm"
            base.status = Status.SKIP_SOURCE
            base.note = f"@{entry.etype}: {why} ({signal_note}); verify via URL"
            base.matched_doi = None
            base.matched_title = None
        return base

    if hit is None:
        # Only a non-skip type gets here without a hit; the skip types
        # returned above the moment their best hit fell short.
        base.status = Status.UNMATCHED
        base.note = "no plausible source hit"
        return base

    # The short title blocked the hit; say so, and keep the skip-source
    # framing for @online / @misc, whose canonical source is the URL anyway.
    why = (
        f"title too short to match by search ({n_words} word(s), "
        f"need {MIN_TITLE_TOKENS})"
    )
    if skip_type:
        base.status = Status.SKIP_SOURCE
        base.note = f"@{entry.etype}: {why}; verify via URL"
    else:
        base.status = Status.UNMATCHED
        base.note = f"{why}; review candidates"
    return base
