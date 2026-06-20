"""Source-agnostic bib-vs-metadata signal layer.

Verification compares each bib entry against a record from a metadata
service (Crossref, OpenAlex, and possibly Semantic Scholar / DataCite
later). Most of those services expose the same four signals — title,
year, first-author surname, container/venue — under different JSON
shapes. This module is the *shape-independent* part: the canonical
`Work` and `BibCitation` records, the per-signal checks, and the
status reduction.

To support a new source, write a `<source>_to_work(record) -> Work`
adapter at the API boundary (see `citefinder.adapters`); this module
never has to change.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# --- status enum ------------------------------------------------------------


class Status(StrEnum):
    """Verdict for a single bib entry after metadata verification.

    Each member carries a `header` for the report. StrEnum members are
    also `str`, so existing string comparisons, JSON serialization, and
    dict keys keep working unchanged. Iteration order is declaration
    order, which doubles as the report's section order.

    Note: the per-member attribute is named `header`, not `title`, to
    avoid shadowing `str.title()` (the built-in title-case method).
    """

    header: str

    def __new__(cls, value: str, header: str) -> Status:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.header = header
        return obj

    MATCHED = "matched", "Matched (≥2 signals pass, 0 fail)"
    PROBABLE = "probable", "Probable (1 fail or mostly unknowns — review)"
    MISMATCH = "mismatch", "Mismatch (≥2 signals disagree — DOI to wrong work)"
    DOI_NOT_FOUND = (
        "doi-not-found",
        "DOI not in metadata source (404 — likely arXiv / preprint)",
    )
    UNMATCHED = "unmatched", "Unmatched (no plausible hit)"
    SKIP_SOURCE = "skip-source", "Skip source (@online / @misc — verify via URL)"
    ERROR = "error", "Error (lookup raised, or no author/title/year)"


# --- canonical record shapes ------------------------------------------------


@dataclass
class BibCitation:
    """The bib-side fields we compare against a Work."""

    title: str | None = None
    year: str | None = None  # raw bib year string; check_year parses it
    first_author_surname: str | None = None
    container: str | None = None  # journal or booktitle, whichever the bib has


@dataclass
class Work:
    """A canonical metadata record. Adapters produce these from source-specific JSON.

    `container_names` is a *list* because real records often expose multiple
    aliases for the venue — Crossref returns both the full and abbreviated
    journal name, and for proceedings papers both the series ("Lecture Notes
    in Computer Science") and the booktitle ("Detection of Intrusions...").
    The check uses the best match across them.
    """

    title: str | None = None
    year: int | None = None
    first_author_surname: str | None = None
    container_names: list[str] = field(default_factory=list)


# --- pure helpers -----------------------------------------------------------


def _strip_braces(s: str) -> str:
    return re.sub(r"[{}]", "", s).strip()


def normalize_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _strip_braces(s).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(a: str, b: str) -> float:
    """Cheap Jaccard over normalized word sets — robust to small punctuation
    or capitalization differences without pulling in a fuzzy-match dep."""
    sa = set(normalize_title(a).split())
    sb = set(normalize_title(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# --- per-signal checks ------------------------------------------------------
# Each check returns:
#   {"verdict": "pass"|"fail"|"unknown", "bib": ..., "crossref": ..., ...}
# The "crossref" key is kept for historical compatibility with the rendered
# report; it really means "the metadata source".
# Status decisions read only the verdicts; the other fields are for the report.


def check_title(bib_title: str | None, work_title: str | None) -> dict[str, Any]:
    if not bib_title or not work_title:
        return {
            "verdict": "unknown",
            "bib": bib_title,
            "crossref": work_title,
            "sim": None,
        }
    s = title_similarity(bib_title, work_title)
    if s >= 0.85:
        v = "pass"
    elif s < 0.30:
        v = "fail"
    else:
        v = "unknown"  # sources sometimes truncate titles; don't punish that.
    return {"verdict": v, "bib": bib_title, "crossref": work_title, "sim": round(s, 2)}


def check_year(bib_year_raw: str | None, work_year: int | None) -> dict[str, Any]:
    bib_year_str = _strip_braces(bib_year_raw or "")
    try:
        by = int(bib_year_str)
    except (TypeError, ValueError):
        by = None
    if by is None or work_year is None:
        return {
            "verdict": "unknown",
            "bib": bib_year_str or None,
            "crossref": work_year,
        }
    diff = abs(by - work_year)
    # ±1 tolerates preprint-vs-proceedings drift.
    v = "pass" if diff <= 1 else "fail"
    return {"verdict": v, "bib": str(by), "crossref": str(work_year), "diff": diff}


def check_author(bib_surname: str | None, work_surname: str | None) -> dict[str, Any]:
    if not bib_surname or not work_surname:
        return {
            "verdict": "unknown",
            "bib": bib_surname or None,
            "crossref": work_surname or None,
        }
    # Token-overlap rather than exact-equal: tolerates one source giving
    # the compound surname ("Larios Vargas") while the other gives only
    # the last token ("Vargas"). Real author conflicts (Petty vs Marquart,
    # Cai vs Fang) still fail because no tokens overlap.
    bib_tokens = set(normalize_title(bib_surname).split())
    work_tokens = set(normalize_title(work_surname).split())
    v = "pass" if (bib_tokens & work_tokens) else "fail"
    return {"verdict": v, "bib": bib_surname, "crossref": work_surname}


def _container_token_match(
    a: str, b: str, allow_prefix: bool = True, min_prefix: int = 4
) -> bool:
    """Two container tokens match if equal, or (when `allow_prefix`) one is a
    `min_prefix`-long prefix of the other. Prefix matching catches ACM/IEEE
    abbreviations: `proc` ↔ `proceedings`, `interact` ↔ `interaction`,
    `comput` ↔ `computer`.

    A 4-char prefix is inherently ambiguous on a lone token — `comp` would
    match `companion` as readily as `computer` — so callers disable
    `allow_prefix` when either side is a single-token venue and require an
    exact match there instead.
    """
    if a == b:
        return True
    if not allow_prefix:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= min_prefix and long_.startswith(short)


def container_similarity(a: str, b: str) -> float:
    """Like `title_similarity` but token equality is loosened to prefix
    matching, since bibs frequently abbreviate venue names while
    metadata sources keep the full form.

    Prefix matching is only enabled when *both* sides are multi-word
    venues. For a single-token venue ("Nature", "PNAS") a 4-char prefix
    match is too loose, so those fall back to exact token equality.
    """
    sa = normalize_title(a).split()
    sb = normalize_title(b).split()
    if not sa or not sb:
        return 0.0
    allow_prefix = len(sa) > 1 and len(sb) > 1
    matched_a: set[str] = set()
    matched_b: set[str] = set()
    for ta in sa:
        for tb in sb:
            if _container_token_match(ta, tb, allow_prefix=allow_prefix):
                matched_a.add(ta)
                matched_b.add(tb)
    union_size = len(set(sa) | set(sb))
    return max(len(matched_a), len(matched_b)) / union_size if union_size else 0.0


def check_container(
    bib_container: str | None, work_container_names: list[str]
) -> dict[str, Any]:
    bib_container = (bib_container or "").strip()
    candidates = [c for c in work_container_names if c]
    if not bib_container or not candidates:
        return {
            "verdict": "unknown",
            "bib": bib_container or None,
            "crossref": candidates[0] if candidates else None,
        }
    best_sim = 0.0
    best_cand = candidates[0]
    for c in candidates:
        s = container_similarity(bib_container, c)
        if s > best_sim:
            best_sim = s
            best_cand = c
    if best_sim >= 0.50:
        v = "pass"
    elif best_sim < 0.20:
        v = "fail"
    else:
        v = "unknown"
    return {
        "verdict": v,
        "bib": bib_container,
        "crossref": best_cand,
        "sim": round(best_sim, 2),
    }


def compute_signals(citation: BibCitation, work: Work) -> dict[str, dict[str, Any]]:
    return {
        "title": check_title(citation.title, work.title),
        "year": check_year(citation.year, work.year),
        "author": check_author(
            citation.first_author_surname, work.first_author_surname
        ),
        "container": check_container(citation.container, work.container_names),
    }


def status_from_signals(signals: dict[str, dict[str, Any]]) -> tuple[Status, str]:
    """Reduce four signals to a status + human-readable note.

    A single signal disagreeing is often a metadata data quality issue
    (title truncation, journal abbreviation, NBER preprint year vs published
    year), not a genuinely wrong record — so we only call it `mismatch` when
    ≥2 signals fail. Single failures land in `probable` for human eyeball.
    """
    fails = [k for k, v in signals.items() if v["verdict"] == "fail"]
    passes = [k for k, v in signals.items() if v["verdict"] == "pass"]

    def fail_note() -> str:
        parts = []
        for k in fails:
            v = signals[k]
            parts.append(f"{k} (bib {v.get('bib')!r} vs source {v.get('crossref')!r})")
        return "Source disagrees on: " + "; ".join(parts)

    if len(fails) >= 2:
        return Status.MISMATCH, fail_note()
    if len(fails) == 1:
        return Status.PROBABLE, fail_note()
    if len(passes) >= 2:
        return Status.MATCHED, ""
    return Status.PROBABLE, f"only {len(passes)} signal(s) confirm; rest unknown"
