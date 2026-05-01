"""Adapters from source-specific JSON to the canonical `Work` shape.

Everything that knows the Crossref or OpenAlex JSON shape lives here.
To wire a new metadata source (Semantic Scholar, DataCite, ...), write
an analogous `<source>_to_work` function — the rest of the verifier
(signal checks, status reduction, rendering) doesn't need to change.
"""

from __future__ import annotations

import re
from typing import Any

from bibtexparser.middlewares.names import parse_single_name_into_parts

from citefinder.signals import Work

# --- Crossref ---------------------------------------------------------------


def _crossref_extract_year(work: dict[str, Any]) -> int | None:
    for k in ("published-print", "published-online", "issued", "created"):
        block = work.get(k)
        if not isinstance(block, dict):
            continue
        dp = block.get("date-parts") or [[]]
        if dp and dp[0] and dp[0][0] is not None:
            try:
                return int(dp[0][0])
            except (TypeError, ValueError):
                pass
    return None


def _crossref_full_title(work: dict[str, Any]) -> str | None:
    """Crossref splits long titles across `title` and `subtitle` (e.g.
    Fang2022's main title is in `title` and the colon-after part is in
    `subtitle`). Concatenate so downstream comparison sees the full title.
    """
    titles = work.get("title") or []
    if not titles:
        return None
    main = titles[0]
    subs = work.get("subtitle") or []
    if subs and subs[0]:
        return f"{main}: {subs[0]}"
    return main


def crossref_to_work(message: dict[str, Any] | None) -> Work | None:
    """Adapt a Crossref `work` record into the canonical `Work` shape.

    Returns None for a missing record (Crossref 404) so the caller can
    surface `doi-not-found` distinctly.
    """
    if message is None:
        return None
    cr_authors = message.get("author") or []
    first_author = ""
    if cr_authors and isinstance(cr_authors[0], dict):
        first_author = (cr_authors[0].get("family") or "").strip()
    # Crossref returns multiple container names — the full and abbreviated
    # journal names, plus for proceedings papers both the series ("Lecture
    # Notes in Computer Science") and the booktitle ("Detection of
    # Intrusions..."). Pass them all along; the container check picks best.
    ct_full = list(message.get("container-title") or [])
    ct_short = list(message.get("short-container-title") or [])
    return Work(
        title=_crossref_full_title(message),
        year=_crossref_extract_year(message),
        first_author_surname=first_author or None,
        container_names=[c for c in (ct_full + ct_short) if c],
    )


# --- OpenAlex ---------------------------------------------------------------


def _strip_braces(s: str) -> str:
    return re.sub(r"[{}]", "", s).strip()


def _openalex_surname(display_name: str) -> str:
    """Extract a surname from OpenAlex's `display_name` ("Arnout van de Rijt").

    OpenAlex stores authors first-name-first as a single string. Reuse
    bibtexparser's name parser — it knows von particles ("van de") group
    with the surname, matching what `first_author_surname` does on the bib
    side so equality checks work.
    """
    name = (display_name or "").strip()
    if not name:
        return ""
    parts = parse_single_name_into_parts(name)
    return _strip_braces(" ".join(parts.von + parts.last)).strip()


def openalex_doi(doi_url: str | None) -> str:
    """OpenAlex returns DOIs as `https://doi.org/10.xxx`. Strip to bare DOI."""
    if not doi_url:
        return ""
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi_url).strip()


def openalex_to_work(message: dict[str, Any] | None) -> Work | None:
    """Adapt an OpenAlex `work` record into the canonical `Work` shape."""
    if message is None:
        return None
    authorships = message.get("authorships") or []
    first_author = ""
    if authorships and isinstance(authorships[0], dict):
        author = authorships[0].get("author") or {}
        first_author = _openalex_surname(author.get("display_name") or "")
    container_names: list[str] = []
    primary = message.get("primary_location") or {}
    src = primary.get("source") or {}
    if src.get("display_name"):
        container_names.append(src["display_name"])
    # Some older records expose the venue under `host_venue` instead.
    host = message.get("host_venue") or {}
    if host.get("display_name") and host["display_name"] not in container_names:
        container_names.append(host["display_name"])
    year = message.get("publication_year")
    return Work(
        title=message.get("display_name") or message.get("title"),
        year=int(year) if isinstance(year, int) else None,
        first_author_surname=first_author or None,
        container_names=container_names,
    )
