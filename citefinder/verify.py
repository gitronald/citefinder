"""Verification orchestration: bib entry vs metadata source.

`Source` wraps a Crossref or OpenAlex client behind a small,
shape-independent surface — `lookup_doi`, `search`, `to_work`,
`candidate_doi`, `candidate_title` — so `verify_entry` doesn't need
to know which source it's talking to. The two source-specific
adapters live in `citefinder.adapters`.

Books are the exception: most have no DOI, so a `BookLookup` (Open
Library, then the Library of Congress; see `citefinder.books`) can be
passed alongside the source. A book entry with an `isbn` is then checked
against the catalog record instead of searched for, and one without gets
Open Library candidates at most.
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
    first_author_surname,
    normalize_doi,
    strip_braces,
)
from citefinder.books import BookLookup, compare_book
from citefinder.cache import JsonlCache, LayeredCache
from citefinder.client import CrossrefClient
from citefinder.models import CrossrefWork, OpenAlexWork
from citefinder.openalex import OpenAlexClient
from citefinder.openlibrary import normalize_isbn
from citefinder.signals import (
    MIN_TITLE_TOKENS,
    BibCitation,
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

# Entry types a `BookLookup` applies to. For the two container types the
# ISBN names the book the chapter is in, so the check runs against
# `booktitle` and `editor` rather than the chapter's own title and author.
BOOK_TYPES = {"book", "inbook", "incollection"}
CONTAINER_BOOK_TYPES = {"inbook", "incollection"}


SourceRecord = CrossrefWork | OpenAlexWork
"""A raw record from either source; `Source.name` says which."""


@dataclass
class Result:
    key: str
    etype: str
    title: str
    year: str
    bib_doi: str | None
    method: str  # "doi" | "search" | "isbn"
    status: Status
    matched_doi: str | None = None
    matched_title: str | None = None
    similarity: float | None = None
    note: str = ""
    candidates: list[dict[str, str]] = field(default_factory=list)
    signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    suggestions: list[dict[str, str]] = field(default_factory=list)
    """Bib fields a book's catalog record would change or add (`isbn` path
    only); see `citefinder.books.compare_book` for the shape."""


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

    def _records(self, cache: JsonlCache | LayeredCache) -> int:
        """Entries in `cache` that are records or cached 404s.

        The client's quota snapshot lives in the same file under
        `rate_limit_key`; a count that included it would call a cache
        holding nothing but bookkeeping "1 entries".
        """
        key = self.client.rate_limit_key
        return len(cache) - (1 if key is not None and key in cache else 0)

    def cache_size(self) -> int:
        """Records the client can answer from cache, across every layer."""
        cache = getattr(self.client, "cache", None)
        return self._records(cache) if cache is not None else 0

    def fallback_size(self) -> int | None:
        """Records in the read-only fallback layer, or `None` without one."""
        cache = getattr(self.client, "cache", None)
        if not isinstance(cache, LayeredCache) or not cache.fallbacks:
            return None
        return self._records(cache.fallbacks[0])

    @property
    def retries(self) -> int:
        """Retries the client has made so far, for a run summary."""
        return self.client.retries

    @property
    def network_calls(self) -> int:
        """Requests that reached the network so far, for a run summary."""
        return self.client.network_calls


def _catalog_surname(name: str) -> str:
    """The surname in a contributor name as a catalog or Open Library writes
    it: `Doctorow, Cory` (inverted) or `Cory Doctorow` (direct order)."""
    if "," in name:
        return name.split(",", 1)[0].strip()
    return name.split()[-1] if name.split() else ""


def _book_fields(entry: Entry) -> dict[str, str]:
    """The bib fields a book record is compared against, brace-stripped.

    For a chapter entry the ISBN identifies the containing book, so its
    `booktitle` stands in for `title` and its `editor` for `author`.
    """
    fields = {k: strip_braces(v) for k, v in entry.fields.items()}
    if entry.etype in CONTAINER_BOOK_TYPES:
        fields["title"] = fields.get("booktitle", "")
        fields["author"] = fields.get("editor", "")
    return fields


def verify_book(entry: Entry, books: BookLookup, base: Result) -> Result:
    """Check a book entry against its ISBN's catalog record, into `base`.

    Title, year, and first contributor go through the same signal checks as
    a DOI lookup — the ISBN resolved to this record, so one disagreement is
    metadata loss rather than a different book. A record confirmed only by a
    retailer feed tops out at `probable`. `base.suggestions` lists what the
    bib should change or add to agree with the record.
    """
    isbn = normalize_isbn(entry.fields.get("isbn", ""))
    base.method = "isbn"
    try:
        record = books.record(isbn)
    except Exception as e:
        base.status = Status.ERROR
        base.note = f"ISBN lookup failed: {e}"
        return base
    if record is None:
        base.status = Status.UNMATCHED
        base.note = f"ISBN {isbn} not in Open Library"
        return base

    fields = _book_fields(entry)
    citation = BibCitation(
        title=fields.get("title") or None,
        year=fields.get("year") or None,
        first_author_surname=(
            first_author_surname(fields["author"]) if fields.get("author") else None
        ),
    )
    lead = record.contributors[0].value if record.contributors else None
    work = Work(
        title=record.full_title,
        year=int(record.year.value) if record.year else None,
        first_author_surname=_catalog_surname(lead) if lead else None,
    )
    base.matched_title = record.full_title
    base.signals = compute_signals(citation, work)
    base.similarity = base.signals["title"].get("sim")
    base.status, base.note = status_from_signals(base.signals, doi_resolved=True)
    base.suggestions = compare_book(record, fields)
    where = "Library of Congress" if record.loc_url else "Open Library"
    if not record.confirmed:
        if base.status == Status.MATCHED:
            base.status = Status.PROBABLE
        base.note = (
            "Open Library record is retailer-fed, not confirmed by a catalog"
            + (f"; {base.note}" if base.note else "")
        )
    elif base.note:
        base.note = f"{base.note} ({where} record)"
    else:
        base.note = f"{where} record"
    return base


def _book_candidates(
    entry: Entry, books: BookLookup, title: str
) -> list[dict[str, str]]:
    """Open Library search hits for a book entry with no ISBN, as
    candidates carrying an `isbn` to confirm."""
    fields = _book_fields(entry)
    query = fields.get("title") or title
    if not query:
        return []
    author = first_author_surname(fields["author"]) if fields.get("author") else None
    docs = books.search(query, author or None, rows=3)
    out: list[dict[str, str]] = []
    for doc in docs:
        hit_title = doc.get("title") or ""
        if doc.get("subtitle"):
            hit_title = f"{hit_title}: {doc['subtitle']}"
        isbns = doc.get("isbn") or []
        out.append(
            {
                "doi": "",
                "title": hit_title,
                "similarity": f"{title_similarity(query, hit_title):.2f}",
                "isbn": isbns[0] if isbns else "",
                "source": "openlibrary",
            }
        )
    return out


def verify_entry(
    entry: Entry, source: Source, books: BookLookup | None = None
) -> Result:
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

    is_book = books is not None and entry.etype in BOOK_TYPES
    # A book with an ISBN and no DOI is checked against its catalog record;
    # the academic indexes rarely carry trade books at all.
    if is_book and not bib_doi and strip_braces(entry.fields.get("isbn", "")):
        assert books is not None
        return verify_book(entry, books, base)

    result = _verify_against_source(entry, source, citation, title, bib_doi, base)
    # A book with no ISBN keeps its source verdict; the Open Library hits
    # are leads a reader can confirm, appended rather than applied.
    if is_book and not bib_doi and result.status != Status.MATCHED:
        assert books is not None
        try:
            result.candidates.extend(_book_candidates(entry, books, title))
        except Exception as e:
            result.note = f"{result.note}; Open Library search failed: {e}"
    return result


def _verify_against_source(
    entry: Entry,
    source: Source,
    citation: BibCitation,
    title: str,
    bib_doi: str | None,
    base: Result,
) -> Result:
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
