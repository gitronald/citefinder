"""Source-agnostic bib-vs-metadata signal layer.

Defines the canonical `Work` and `BibCitation` records, the four per-signal
checks (title, year, first-author surname, container), and the status
reduction. Source-specific JSON is mapped to `Work` in `citefinder.adapters`.
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

    Each member carries a `header`, a one-line description for reports. It is
    not named `title` to avoid shadowing `str.title()`.
    """

    header: str

    def __new__(cls, value: str, header: str) -> Status:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.header = header
        return obj

    MATCHED = (
        "matched",
        "Matched (≥2 signals pass, 0 fail; or DOI resolved with 1 disagreement noted)",
    )
    PROBABLE = "probable", "Probable (1 fail or mostly unknowns — review)"
    MISMATCH = "mismatch", "Mismatch (≥2 signals disagree — DOI to wrong work)"
    DOI_NOT_FOUND = (
        "doi-not-found",
        "DOI not in metadata source (404)",
    )
    NOT_INDEXED = (
        "not-indexed",
        "DOI registered with DataCite, not this source: verify with --source openalex",
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
    """A canonical metadata record, produced by the adapters.

    `container_names` lists every venue alias a record exposes (full and
    abbreviated journal name, series and booktitle); the check uses the best match.
    """

    title: str | None = None
    year: int | None = None
    first_author_surname: str | None = None
    container_names: list[str] = field(default_factory=list)


# --- pure helpers -----------------------------------------------------------

# Fewer title tokens than this cannot confirm a match ("Influence" scores a
# perfect Jaccard against any record containing it): `check_title` tops out at
# `unknown` and search-path selection refuses to match on title alone.
MIN_TITLE_TOKENS = 3


def strip_braces(s: str) -> str:
    """`s` with BibTeX's protective braces removed and the ends trimmed."""
    return re.sub(r"[{}]", "", s).strip()


def normalize_title(s: str) -> str:
    """Lower-case word characters only, diacritics folded, one space apart.

    Keeps `\\w` characters so non-Latin scripts (CJK, Cyrillic, Arabic) survive.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = strip_braces(s).lower()
    s = re.sub(r"[^\w ]+|_", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Scripts written without spaces: compared by character bigrams, each counting
# as one token against `MIN_TITLE_TOKENS`.
_CJK_RUN = re.compile(
    r"[\u3040-\u30ff"  # hiragana, katakana
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"  # CJK ideographs, compatibility
    r"\U00020000-\U0002ffff]+"  # CJK ideograph extensions B-F
)


def _tokens(normalized: str) -> list[str]:
    """Whitespace tokens of a `normalize_title` string, with each run of CJK
    ideographs or kana expanded into its character bigrams (a lone character
    stays whole)."""
    out: list[str] = []
    for token in _CJK_RUN.sub(r" \g<0> ", normalized).split():
        if len(token) > 1 and _CJK_RUN.fullmatch(token):
            out.extend(token[i : i + 2] for i in range(len(token) - 1))
        else:
            out.append(token)
    return out


def title_tokens(s: str) -> set[str]:
    """The normalized word set a title check compares."""
    return set(_tokens(normalize_title(s)))


def _jaccard(sa: set[str], sb: set[str]) -> float:
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity of the normalized word sets."""
    return _jaccard(title_tokens(a), title_tokens(b))


def _is_truncation(sa: set[str], sb: set[str]) -> bool:
    """One title is the other with words dropped, and the shorter side is
    still long enough to be distinctive.

    Sources truncate titles and bibs omit subtitles, so a low Jaccard can
    reflect a missing subtitle. A subset under `MIN_TITLE_TOKENS` words does
    not count.
    """
    if not (sa < sb or sb < sa):
        return False
    return min(len(sa), len(sb)) >= MIN_TITLE_TOKENS


# --- per-signal checks ------------------------------------------------------
# Each check returns:
#   {"verdict": "pass"|"fail"|"unknown", "bib": ..., "crossref": ..., ...}
# The "crossref" key holds the metadata source's value, whichever source it is.
# Status decisions read only the verdicts.


def check_title(bib_title: str | None, work_title: str | None) -> dict[str, Any]:
    if not bib_title or not work_title:
        return {
            "verdict": "unknown",
            "bib": bib_title,
            "crossref": work_title,
            "sim": None,
        }
    sa = title_tokens(bib_title)
    sb = title_tokens(work_title)
    s = _jaccard(sa, sb)
    if s >= 0.85:
        v = "pass"
    elif s < 0.30:
        v = "fail"
    else:
        v = "unknown"  # sources truncate titles
    note = None
    if v == "pass" and len(sa) < MIN_TITLE_TOKENS:
        # Too short to confirm; let the other signals decide.
        v = "unknown"
        note = f"bib title has {len(sa)} word(s); need {MIN_TITLE_TOKENS} to confirm"
    elif v == "fail" and _is_truncation(sa, sb):
        v = "unknown"
        note = "one title is a truncation of the other (subtitle dropped)"
    out: dict[str, Any] = {
        "verdict": v,
        "bib": bib_title,
        "crossref": work_title,
        "sim": round(s, 2),
    }
    if note:
        out["note"] = note
    return out


def check_year(bib_year_raw: str | None, work_year: int | None) -> dict[str, Any]:
    bib_year_str = strip_braces(bib_year_raw or "")
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
    # +-1 tolerates preprint-vs-proceedings drift.
    v = "pass" if diff <= 1 else "fail"
    return {"verdict": v, "bib": str(by), "crossref": str(work_year), "diff": diff}


def _surname_tokens(surname: str) -> set[str]:
    """Normalized tokens of a surname, minus its name particles.

    A wholly lower-case word (`van`, `de`) is a particle: two authors can
    share one, so it is not agreement. A surname of only such words keeps them.
    """
    kept = [word for word in surname.split() if not word.islower()]
    return title_tokens(" ".join(kept)) or title_tokens(surname)


def check_author(bib_surname: str | None, work_surname: str | None) -> dict[str, Any]:
    if not bib_surname or not work_surname:
        return {
            "verdict": "unknown",
            "bib": bib_surname or None,
            "crossref": work_surname or None,
        }
    # Token overlap tolerates a compound surname ("Larios Vargas") against its
    # last token ("Vargas").
    bib_tokens = _surname_tokens(bib_surname)
    work_tokens = _surname_tokens(work_surname)
    v = "pass" if (bib_tokens & work_tokens) else "fail"
    return {"verdict": v, "bib": bib_surname, "crossref": work_surname}


def _container_token_match(a: str, b: str, min_prefix: int = 4) -> bool:
    """Tokens match if equal, or one is a prefix (>= `min_prefix` chars) of the other.

    Catches abbreviations such as `proc` and `proceedings`.
    """
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= min_prefix and long_.startswith(short)


def container_similarity(a: str, b: str) -> float:
    """Like `title_similarity`, but tokens may match by prefix (abbreviated venues)."""
    sa = list(dict.fromkeys(_tokens(normalize_title(a))))
    sb = list(dict.fromkeys(_tokens(normalize_title(b))))
    if not sa or not sb:
        return 0.0
    # Pair each token at most once, exact pairs first, so a short prefix
    # cannot inflate the score or steal another token's exact twin.
    exact = set(sa) & set(sb)
    unmatched_b = [tb for tb in sb if tb not in exact]
    pairs = len(exact)
    for ta in (ta for ta in sa if ta not in exact):
        for i, tb in enumerate(unmatched_b):
            if _container_token_match(ta, tb):
                del unmatched_b[i]
                pairs += 1
                break
    return pairs / len(set(sa) | set(sb))


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


def status_from_signals(
    signals: dict[str, dict[str, Any]], *, doi_resolved: bool = False
) -> tuple[Status, str]:
    """Reduce four signals to a status + human-readable note.

    A single failing signal is often source-side metadata loss, so `mismatch`
    needs >= 2 failures; one failure is `probable`.

    With `doi_resolved` (the record came from the bib's own DOI), a single
    non-title failure next to >= 2 passes stays `matched` with the disagreement
    in the note. Fewer than two passes stays `probable`.
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
        if doi_resolved and fails[0] != "title" and len(passes) >= 2:
            note = fail_note()
            return Status.MATCHED, f"DOI resolved; {note[:1].lower()}{note[1:]}"
        return Status.PROBABLE, fail_note()
    if len(passes) >= 2:
        return Status.MATCHED, ""
    return Status.PROBABLE, f"only {len(passes)} signal(s) confirm; rest unknown"
