"""BibTeX parsing and bib-side query helpers.

bibtexparser v2 does the heavy lifting — handles brace/quote-delimited
values, nested braces, `@string` and `@comment` blocks, and quirky
real-world bib files. This module adapts the result into the small
`Entry` shape used by the verifier and exposes the helpers that turn
a bib entry into something Crossref/OpenAlex can search for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bibtexparser.entrypoint import parse_string as parse_bib_string
from bibtexparser.middlewares.names import (
    parse_single_name_into_parts,
    split_multiple_persons_names,
)

from citefinder.signals import BibCitation


@dataclass
class Entry:
    etype: str
    key: str
    fields: dict[str, str]


def parse_entries(text: str) -> list[Entry]:
    """Parse a BibTeX string into a list of `Entry` records."""
    library = parse_bib_string(text)
    entries: list[Entry] = []
    for e in library.entries:
        fields = {f.key.lower(): f.value for f in e.fields}
        entries.append(Entry(etype=e.entry_type.lower(), key=e.key, fields=fields))
    return entries


def strip_braces(s: str) -> str:
    return re.sub(r"[{}]", "", s).strip()


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
    """Brace-stripped title for OpenAlex's title-only search.

    OpenAlex-specific normalization (remapping straight apostrophes to the
    curly U+2019 its index stores, dropping filter-reserved punctuation that
    would 400 the request) lives at the client boundary in
    `OpenAlexClient.search_title`, so this just extracts the bib title.
    """
    return strip_braces(entry.fields.get("title", ""))


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
