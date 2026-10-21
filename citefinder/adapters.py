"""Adapters from source-specific JSON to the canonical `Work` shape.

Everything that knows the Crossref or OpenAlex JSON shape lives here.
"""

from __future__ import annotations

from bibtexparser.middlewares.names import parse_single_name_into_parts

from citefinder.bib import normalize_doi
from citefinder.models import CrossrefWork, OpenAlexWork
from citefinder.signals import Work, strip_braces

# --- Crossref ---------------------------------------------------------------


def _crossref_extract_year(work: CrossrefWork) -> int | None:
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


def crossref_full_title(work: CrossrefWork) -> str | None:
    """Title joined with its `subtitle` as `title: subtitle`."""
    titles = work.get("title") or []
    if not titles:
        return None
    main = titles[0]
    subs = work.get("subtitle") or []
    if subs and subs[0]:
        return f"{main}: {subs[0]}"
    return main


def crossref_to_work(message: CrossrefWork | None) -> Work | None:
    """Adapt a Crossref `work` record into the canonical `Work` shape.

    Returns None for a missing record (Crossref 404).
    """
    if message is None:
        return None
    cr_authors = message.get("author") or []
    first_author = ""
    if cr_authors and isinstance(cr_authors[0], dict):
        # A person carries `family`/`given`; an organisation carries `name`.
        first = cr_authors[0]
        first_author = (first.get("family") or first.get("name") or "").strip()
    # Keep every container name (full, abbreviated, series, booktitle).
    ct_full = list(message.get("container-title") or [])
    ct_short = list(message.get("short-container-title") or [])
    return Work(
        title=crossref_full_title(message),
        year=_crossref_extract_year(message),
        first_author_surname=first_author or None,
        container_names=[c for c in (ct_full + ct_short) if c],
    )


# --- OpenAlex ---------------------------------------------------------------


def _openalex_surname(display_name: str) -> str:
    """Extract a surname from OpenAlex's `display_name` ("Arnout van de Rijt").

    Uses the same von+last parsing as `first_author_surname`.
    """
    name = (display_name or "").strip()
    if not name:
        return ""
    parts = parse_single_name_into_parts(name)
    return strip_braces(" ".join(parts.von + parts.last)).strip()


def openalex_doi(doi_url: str | None) -> str:
    """Bare DOI from OpenAlex's `https://doi.org/...` form."""
    return normalize_doi(doi_url) if doi_url else ""


def openalex_to_work(message: OpenAlexWork | None) -> Work | None:
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
    # Older records use `host_venue`.
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
