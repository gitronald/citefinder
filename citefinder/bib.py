"""BibTeX parsing (via bibtexparser) and bib-side query helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bibtexparser.entrypoint import parse_string as parse_bib_string
from bibtexparser.middlewares.names import (
    parse_single_name_into_parts,
    split_multiple_persons_names,
)

from citefinder.openalex import normalize_title_query
from citefinder.signals import BibCitation, strip_braces

log = logging.getLogger("citefinder")


@dataclass
class Entry:
    etype: str
    key: str
    fields: dict[str, str]


def parse_entries(text: str) -> list[Entry]:
    """Parse a BibTeX string into a list of `Entry` records.

    A block bibtexparser cannot parse (duplicate field or citation key,
    unterminated brace) is skipped with a logged warning.
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

    Strips braces, whitespace, a `https://doi.org/` (or `dx.doi.org`)
    prefix, and a `doi:` label.
    """
    return _DOI_PREFIX_RE.sub("", strip_braces(s).strip()).strip()


def first_author_surname(author_field: str) -> str:
    """Surname of the first author in a BibTeX `author = {...}` value.

    Keeps braced corporate authors whole and joins von and last parts
    ("van de Rijt"), matching how Crossref stores `family`.
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

    Plain `?search=` matches abstracts too and returns noise.
    """
    return normalize_title_query(strip_braces(entry.fields.get("title", "")))


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
