"""Library of Congress catalog client and a small MARC reader.

Queries the catalog's SRU gateway (`http://lx2.loc.gov:210/LCDB`, plain HTTP)
by LCCN or ISBN and parses the MARCXML into plain dicts before caching.
`marc_book` reads the fields a citation uses (title, contributors, edition,
publication place, publisher, and date) with ISBD punctuation stripped and
casing kept as cataloged.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from citefinder._base import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_WAIT,
    DEFAULT_TIMEOUT,
    CachedJsonClient,
    _default_user_agent,
)
from citefinder.cache import JsonlCache, LayeredCache
from citefinder.models import MarcField, MarcRecord
from citefinder.openlibrary import normalize_isbn

LOC_BASE = "https://lccn.loc.gov"
LOC_SRU = "http://lx2.loc.gov:210/LCDB"

# One request per second is well inside the Library of Congress burst limits.
LOC_MIN_INTERVAL = 1.0

__all__ = [
    "LOC_BASE",
    "LOC_MIN_INTERVAL",
    "LOC_SRU",
    "YEAR_RE",
    "LocClient",
    "marc_book",
    "normalize_lccn",
    "parse_marcxml",
    "strip_isbd",
]

_ISBD_TRAILING = re.compile(r"[\s:/;=,.]+$")
YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
"""The first four-digit run in a date string (`2025.`, `[2025]`, `Oct 21, 2025`)."""
_PAREN_QUALIFIER = re.compile(r"\s*\(([^)]*)\)\s*$")


def normalize_lccn(lccn: str) -> str:
    """`lccn` with spaces and hyphens removed (`sn 85-042021` -> `sn85042021`)."""
    return re.sub(r"[\s-]+", "", lccn.strip())


def strip_isbd(value: str) -> str:
    """`value` without trailing ISBD punctuation or surrounding `[...]`."""
    value = _ISBD_TRAILING.sub("", value.strip())
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    return value


def _local(tag: str) -> str:
    """An element's tag without its `{namespace}` prefix."""
    return tag.rsplit("}", 1)[-1]


_MARC_CHILDREN = {"leader", "controlfield", "datafield"}


def _is_marc_record(el: ET.Element) -> bool:
    """A `<record>` holding MARC fields, not the `<zs:record>` SRU envelope."""
    return _local(el.tag) == "record" and any(
        _local(child.tag) in _MARC_CHILDREN for child in el
    )


def parse_marcxml(text: str | bytes) -> MarcRecord | None:
    """The first MARC `<record>` in an XML document as plain JSON, or `None`.

    Accepts a bare record, a `<collection>`, or an SRU response. Prefer bytes,
    so the XML declaration decides the encoding. Raises `ValueError` when
    `text` is not XML.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"not MARCXML: {e}") from None
    record = next((el for el in root.iter() if _is_marc_record(el)), None)
    if record is None:
        return None
    parsed: MarcRecord = {"leader": "", "controlfields": {}, "fields": []}
    for el in record:
        name = _local(el.tag)
        if name == "leader":
            parsed["leader"] = el.text or ""
        elif name == "controlfield":
            parsed["controlfields"][el.get("tag", "")] = el.text or ""
        elif name == "datafield":
            field: MarcField = {
                "tag": el.get("tag", ""),
                "ind1": el.get("ind1", " "),
                "ind2": el.get("ind2", " "),
                "subfields": [
                    [sub.get("code", ""), sub.text or ""]
                    for sub in el
                    if _local(sub.tag) == "subfield"
                ],
            }
            parsed["fields"].append(field)
    return parsed


def _fields(record: MarcRecord, *tags: str) -> list[MarcField]:
    return [f for f in record.get("fields", []) if f.get("tag") in tags]


def _sub(field: MarcField, code: str) -> str | None:
    """The first `$code` subfield of `field`, ISBD-stripped, or `None`."""
    for sub_code, value in field.get("subfields", []):
        if sub_code == code and value.strip():
            return strip_isbd(value)
    return None


def _subs(field: MarcField, code: str) -> list[str]:
    return [
        strip_isbd(value)
        for sub_code, value in field.get("subfields", [])
        if sub_code == code and value.strip()
    ]


def _isbns(record: MarcRecord) -> list[dict[str, str]]:
    """Every `020 $a`, split from its qualifier (`$q` or parenthesized in `$a`)."""
    out: list[dict[str, str]] = []
    for field in _fields(record, "020"):
        raw = _sub(field, "a")
        if raw is None:
            continue
        qualifier = _sub(field, "q") or ""
        match = _PAREN_QUALIFIER.search(raw)
        if match:
            qualifier = qualifier or match.group(1)
            raw = raw[: match.start()]
        out.append(
            {
                "isbn": normalize_isbn(raw),
                "qualifier": strip_isbd(qualifier).strip("()"),
            }
        )
    return out


def _contributors(record: MarcRecord) -> list[str]:
    """Personal and corporate names from main and added entries, in record order."""
    names: list[str] = []
    for field in _fields(record, "100", "110", "700", "710"):
        name = _sub(field, "a")
        if name and name not in names:
            names.append(name)
    return names


def _publication(record: MarcRecord) -> MarcField | None:
    """The publication `264` (indicator 2 = 1), else the first `264`, else a `260`."""
    fields = _fields(record, "264")
    for field in fields:
        if field.get("ind2") == "1":
            return field
    if fields:
        return fields[0]
    older = _fields(record, "260")
    return older[0] if older else None


def marc_book(record: MarcRecord) -> dict[str, Any]:
    """The citation-relevant fields of a book record.

    Returns a dict with `title`, `subtitle`, `contributors`, `publisher`,
    `place`, `date`, `year`, `edition`, `isbns`, `lccn`, and
    `cataloging_agency`; a field the record lacks is `None` (or an empty list).
    Nothing is inferred.
    """
    title_field = next(iter(_fields(record, "245")), None)
    publication = _publication(record)
    date = _sub(publication, "c") if publication else None
    year_match = YEAR_RE.search(date) if date else None
    lccn_field = next(iter(_fields(record, "010")), None)
    agency = next(iter(_fields(record, "040")), None)
    edition = next(iter(_fields(record, "250")), None)
    return {
        "title": _sub(title_field, "a") if title_field else None,
        "subtitle": _sub(title_field, "b") if title_field else None,
        "contributors": _contributors(record),
        "publisher": _sub(publication, "b") if publication else None,
        "place": _sub(publication, "a") if publication else None,
        "date": date,
        "year": int(year_match.group(1)) if year_match else None,
        "edition": _sub(edition, "a") if edition else None,
        "isbns": _isbns(record),
        "lccn": normalize_lccn(_sub(lccn_field, "a") or "") or None
        if lccn_field
        else None,
        "cataloging_agency": _sub(agency, "a") if agency else None,
    }


class LocClient(CachedJsonClient):
    """Library of Congress LCCN and ISBN lookups, cached as parsed MARC.

    `mailto` goes into the User-Agent only; there is no polite pool.
    """

    def __init__(
        self,
        cache: JsonlCache | LayeredCache | None = None,
        cache_path: str | Path | None = None,
        mailto: str | None = None,
        user_agent: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        max_wait: float = DEFAULT_MAX_WAIT,
        min_interval: float = LOC_MIN_INTERVAL,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
        fallback_cache: str | Path | None = None,
    ) -> None:
        if user_agent is None:
            user_agent = _default_user_agent(mailto)
        super().__init__(
            cache=cache,
            cache_path=cache_path,
            mailto=None,
            user_agent=user_agent,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
            max_wait=max_wait,
            min_interval=min_interval,
            sleep=sleep,
            monotonic=monotonic,
            clock=clock,
            fallback_cache=fallback_cache,
        )

    @staticmethod
    def lccn_url(lccn: str) -> str:
        """The human-readable permalink for `lccn`, for reports."""
        return f"{LOC_BASE}/{normalize_lccn(lccn)}"

    @staticmethod
    def sru_url(query: str) -> str:
        """The SRU request for one MARCXML record matching a CQL `query`."""
        params = {
            "version": "1.1",
            "operation": "searchRetrieve",
            "query": query,
            "maximumRecords": "1",
            "recordSchema": "marcxml",
        }
        return f"{LOC_SRU}?{urlencode(params)}"

    def _decode(  # pyrefly: ignore[missing-override-decorator]  (3.11 has no `override`)
        self, response: requests.Response
    ) -> Any:
        # Parse raw bytes: the gateway sends no charset, so `response.text`
        # would decode as ISO-8859-1. A 200 with no record is cached as a miss.
        return parse_marcxml(response.content)

    def lookup_lccn(self, lccn: str) -> MarcRecord | None:
        """The parsed MARC record for `lccn`, or `None` when there is none."""
        return self._get(self.sru_url(f"bath.lccn={normalize_lccn(lccn)}"))

    def lookup_isbn(self, isbn: str) -> MarcRecord | None:
        """The parsed MARC record cataloged under `isbn`, or `None`.

        When several records carry the ISBN, the catalog's first is returned.
        """
        return self._get(self.sru_url(f"bath.isbn={normalize_isbn(isbn)}"))
