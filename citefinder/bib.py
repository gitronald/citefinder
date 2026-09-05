"""BibTeX parsing and bib-side query helpers.

bibtexparser v2 does the heavy lifting — handles brace/quote-delimited
values, nested braces, `@string` and `@comment` blocks, and quirky
real-world bib files. This module adapts the result into the small
`Entry` shape used by the verifier and exposes the helpers that turn
a bib entry into something Crossref/OpenAlex can search for.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bibtexparser.entrypoint import parse_string as parse_bib_string
from bibtexparser.middlewares.names import (
    parse_single_name_into_parts,
    split_multiple_persons_names,
)

from citefinder.signals import BibCitation, strip_braces

log = logging.getLogger("citefinder")


@dataclass
class Entry:
    etype: str
    key: str
    fields: dict[str, str]


def parse_entries(text: str) -> list[Entry]:
    """Parse a BibTeX string into a list of `Entry` records.

    A block bibtexparser cannot turn into an entry — a duplicated field key,
    a repeated citation key, an unterminated brace — is left out of the
    result and reported with a warning, so a shorter-than-expected entry
    count has a visible cause rather than a silent one.
    """
    library = parse_bib_string(text)
    for block in library.failed_blocks:
        reason = str(block.error) or type(block.error).__name__
        line = block.start_line
        where = f"line {line + 1}" if line is not None else "unknown line"
        log.warning("skipped unparsable bib block at %s: %s", where, reason)
    entries: list[Entry] = []
    for e in library.entries:
        fields = {f.key.lower(): f.value for f in e.fields}
        entries.append(Entry(etype=e.entry_type.lower(), key=e.key, fields=fields))
    return entries


_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def normalize_doi(s: str) -> str:
    """A bare DOI from the forms a `.bib` or a source may carry.

    Braces and whitespace go, as does a `https://doi.org/` (or `dx.doi.org`)
    URL prefix or a `doi:` label — exported bibs use all three, and a source
    given the URL form 404s on a DOI it does index.
    """
    return _DOI_PREFIX_RE.sub("", strip_braces(s).strip()).strip()


def first_author_surname(author_field: str) -> str:
    """Surname of the first author in a BibTeX `author = {...}` value.

    Uses bibtexparser's name parser, which (a) keeps corporate authors
    like `{Association for Computing Machinery}` together instead of
    splitting on whitespace and " and ", and (b) separates name particles
    (`van de Rijt, Arnout` → von=['van','de'], last=['Rijt']). Crossref
    stores `family` as the combined von+last (e.g. "van de Rijt"), so we
    join both here for a like-for-like comparison.
    """
    if not author_field:
        return ""
    persons = split_multiple_persons_names(author_field)
    if not persons:
        return ""
    parts = parse_single_name_into_parts(persons[0])
    return strip_braces(" ".join(parts.von + parts.last)).strip()


def build_search_query(entry: Entry) -> str:
    """Crossref-shaped query: author + title + year for `query.bibliographic`."""
    parts = []
    if "author" in entry.fields:
        parts.append(first_author_surname(entry.fields["author"]))
    if "title" in entry.fields:
        parts.append(strip_braces(entry.fields["title"]))
    if "year" in entry.fields:
        parts.append(strip_braces(entry.fields["year"]))
    return " ".join(p for p in parts if p)


def build_title_query(entry: Entry) -> str:
    """Title-only query for OpenAlex `filter=title.search:`.

    OpenAlex's plain `?search=` runs full-text against title + abstract
    and was returning unrelated noise for our author + title + year
    queries. Restricting to title-only via the filter syntax matches
    what verifies search hits in the first place: title similarity.

    OpenAlex filter syntax reserves `,` (filter separator), `|` (OR),
    `:` (field separator), and `!` (negation), so titles containing
    them return HTTP 400. Strip those out — `title.search` is fuzzy
    anyway, so dropping punctuation doesn't hurt recall.

    Straight apostrophes are also remapped to U+2019 because OpenAlex's
    title index stores the curly form (e.g., Ohm2020 is indexed as
    `Backstabber’s Knife Collection`); a query with `Backstabber's`
    returns zero hits while `Backstabber’s` finds the paper.
    """
    title = strip_braces(entry.fields.get("title", ""))
    title = title.replace("'", "’")
    title = re.sub(r"[,:|!?]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def citation_from_entry(entry: Entry) -> BibCitation:
    return BibCitation(
        title=strip_braces(entry.fields.get("title", "")) or None,
        year=strip_braces(entry.fields.get("year", "")) or None,
        first_author_surname=(
            first_author_surname(entry.fields["author"])
            if entry.fields.get("author")
            else None
        ),
        container=strip_braces(
            entry.fields.get("journal") or entry.fields.get("booktitle") or ""
        )
        or None,
    )
