"""Library of Congress catalog client and a small MARC reader.

The catalog's SRU gateway (`http://lx2.loc.gov:210/LCDB`, plain HTTP — it
speaks no TLS) returns a record as MARCXML for a Library of Congress
Control Number (`bath.lccn=`) or an ISBN (`bath.isbn=`). For a trade book
that is the Cataloging in Publication record — what is printed on the
copyright page, so the place of publication, the imprint and its parent,
and the edition statement are as the publisher supplied them. (The older
`https://lccn.loc.gov/<lccn>/marcxml` permalinks now 404; the bare
permalink still redirects to the catalog's search page, so `lccn_url`
keeps it as the human-readable link.)

The body is XML, not JSON, so `LocClient` parses it into plain dicts before
caching (`parse_marcxml`), and the cache stays JSONL with one row per
query. `marc_book` then reads the handful of fields a citation uses:

- `020` ISBN (`$a`, with a qualifier in `$q` or in parentheses)
- `100`/`110`, `700`/`710` contributor names
- `245 $a $b` title and subtitle
- `250 $a` edition statement
- `264` (indicator 2 = 1, publication) or the older `260`: `$a` place,
  `$b` publisher, `$c` date
- `040 $a` cataloging agency (`DLC` is the Library of Congress itself)

ISBD punctuation — the ` :`, ` /`, and trailing `.` or `,` that separate
subfields in a printed catalog card — is stripped on the way out. Casing is
kept as cataloged: catalog titles are sentence case, and title case is the
caller's job.
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

# The Library of Congress publishes per-endpoint burst limits for its
# newer APIs and none for the LCCN permalink service; one request per
# second is well inside all of them.
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
"""The first four-digit run in a date string (`2025.`, `[2025]`, `Oct 21, 2025`).
Shared with `citefinder.books` so both sources read dates one way."""
_PAREN_QUALIFIER = re.compile(r"\s*\(([^)]*)\)\s*$")


def normalize_lccn(lccn: str) -> str:
    """`lccn` as the permalink service wants it: no spaces or hyphens.

    Catalog records write LCCNs with internal spacing (`  2025007165`,
    `sn 85-042021`); the permalink form is the compact one.
    """
    return re.sub(r"[\s-]+", "", lccn.strip())


def strip_isbd(value: str) -> str:
    """`value` without the ISBD separator punctuation a MARC subfield ends
    with, and without a surrounding `[...]` where the whole value was
    supplied by the cataloger rather than printed on the item."""
    value = _ISBD_TRAILING.sub("", value.strip())
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    return value


def _local(tag: str) -> str:
    """An element's tag without its `{namespace}` prefix."""
    return tag.rsplit("}", 1)[-1]


_MARC_CHILDREN = {"leader", "controlfield", "datafield"}


def _is_marc_record(el: ET.Element) -> bool:
    """A `<record>` holding MARC fields — not the `<zs:record>` envelope an
    SRU response wraps it in, whose local name is also `record`."""
    return _local(el.tag) == "record" and any(
        _local(child.tag) in _MARC_CHILDREN for child in el
    )


def parse_marcxml(text: str | bytes) -> MarcRecord | None:
    """The first MARC `<record>` in an XML document as plain JSON, or `None`
    when the document holds none (an SRU response with zero records, an
    empty `<collection>`, an HTML error page).

    Accepts a bare record, a `<collection>`, or an SRU `searchRetrieveResponse`,
    as text or as the raw bytes of the response — bytes are preferred, so the
    XML declaration decides the encoding rather than an HTTP header guess.
    Raises `ValueError` when `text` is not XML at all.
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
    """Every `020 $a`, split from its qualifier (`$q`, or the older
    parenthesized form inside `$a`)."""
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
    """Personal and corporate names from the main and added entries, in
    record order, as cataloged (`Doctorow, Cory`)."""
    names: list[str] = []
    for field in _fields(record, "100", "110", "700", "710"):
        name = _sub(field, "a")
        if name and name not in names:
            names.append(name)
    return names


def _publication(record: MarcRecord) -> MarcField | None:
    """The publication statement: a `264` with indicator 2 = 1, else the
    first `264` of any kind, else a `260`."""
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
    `cataloging_agency`; a field the record does not carry is `None` (or an
    empty list). Nothing here is inferred: a record without a `264 $a` has
    no place, and stays that way.
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

    There is no polite pool and no contact parameter; `mailto` is accepted
    for signature parity with the other clients and goes into the
    User-Agent only. Every other knob behaves as documented on
    `CachedJsonClient`.
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
        # The gateway sends `text/xml` with no charset, which `requests`
        # would decode as ISO-8859-1 (`response.text`); the raw bytes let the
        # XML declaration decide, so a `Müller` stays a `Müller`. A 200 whose
        # body holds no record (an unknown number is a zero-record response,
        # not a 404) is cached as a miss, the same as a 404.
        return parse_marcxml(response.content)

    def lookup_lccn(self, lccn: str) -> MarcRecord | None:
        """The parsed MARC record for `lccn`, or `None` when there is none."""
        return self._get(self.sru_url(f"bath.lccn={normalize_lccn(lccn)}"))

    def lookup_isbn(self, isbn: str) -> MarcRecord | None:
        """The parsed MARC record cataloged under `isbn`, or `None`.

        An ISBN can sit on more than one record (a reissue cataloged
        separately); the first the catalog ranks is returned.
        """
        return self._get(self.sru_url(f"bath.isbn={normalize_isbn(isbn)}"))
