"""Find and assess fragments of an OpenAlex author profile.

OpenAlex builds author profiles by automated disambiguation, so one person can
be split across several author IDs. Starting from one profile (the anchor),
this module searches for other profiles under a compatible name and weighs
each against the anchor's works: a duplicate title, co-authors in common, the
years the works span.

The result is a report an editor reads. Nothing here merges profiles or
rewrites a name, and a name match alone never makes a fragment.

`name_tokens`, `match_names`, and `assess` are pure; `find_fragments` drives
them against an `OpenAlexClient`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

from citefinder.adapters import (
    AuthorProfile,
    AuthorWork,
    openalex_to_author,
    openalex_to_author_work,
)
from citefinder.openalex import MAX_PER_PAGE, OpenAlexClient
from citefinder.signals import MIN_TITLE_TOKENS, same_title, title_tokens

__all__ = [
    "Anchor",
    "Assessment",
    "Candidate",
    "DuplicateTitle",
    "FragmentReport",
    "NameMatch",
    "Verdict",
    "assess",
    "best_match",
    "family_tokens",
    "find_fragments",
    "match_names",
    "name_tokens",
    "same_name_pairs",
    "search_queries",
]

# Defaults measured on seven anchors (plan 022). An author search costs ten
# times a works list, and an anchor's spellings reduced to at most four queries.
DEFAULT_MAX_SEARCHES = 3
DEFAULT_MAX_CANDIDATES = 50
# Works-list pages read for the anchor: 2,000 works.
MAX_ANCHOR_PAGES = 10

# Shared co-authors that make a fragment. No namesake with a full-name match
# shared one; a candidate matched on an initial or the family name can.
SHARED_COAUTHORS_STRONG = 1
SHARED_COAUTHORS_WEAK = 2

NameMatch = Literal["strong", "weak"]


class Verdict(StrEnum):
    """What the evidence says about a candidate profile."""

    LIKELY_FRAGMENT = "likely-fragment"
    POSSIBLE = "possible"
    DIFFERENT = "different"
    UNCHECKED = "unchecked"


# --- names ------------------------------------------------------------------

# Letters NFKD does not decompose into a base letter and a mark.
_UNDECOMPOSED = str.maketrans(
    {"ı": "i", "ø": "o", "ł": "l", "đ": "d", "ð": "d", "æ": "ae", "œ": "oe", "þ": "th"}
)
_APOSTROPHES = re.compile(r"['’‘`ʼ]")
_NOT_WORD = re.compile(r"[\W_]+")


def name_tokens(name: str) -> tuple[str, ...]:
    """The comparable tokens of a personal name, in order.

    Case and diacritics are folded (`Kimberlé` and `Kimberle` are one token),
    an apostrophe joins its neighbours (`O'Brien` is `obrien`), any other
    punctuation separates (`G.L.A.` is three initials, `Nava-Muñoz` two
    tokens), and a number is dropped (a birth year in `Doe, Jane 1959-`).
    """
    text = unicodedata.normalize("NFKD", name.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _APOSTROPHES.sub("", text.translate(_UNDECOMPOSED))
    return tuple(t for t in _NOT_WORD.split(text) if t and not t.isdigit())


def _is_initial(token: str) -> bool:
    """A single letter of an alphabet. A lone CJK character is a whole name."""
    return len(token) == 1 and unicodedata.category(token) == "Ll"


def _words(tokens: Iterable[str]) -> list[str]:
    """The tokens that are not initials."""
    return [t for t in tokens if not _is_initial(t)]


def family_tokens(name: str) -> frozenset[str]:
    """The words of `name` other than its first given name.

    Read from the order: every word after the first, or, for `Family, Given`,
    every word before the comma. A match on these alone is a match on a family
    name, which namesakes share.
    """
    head, comma, tail = name.partition(",")
    if comma and name_tokens(tail):
        return frozenset(_words(name_tokens(head)))
    return frozenset(_words(name_tokens(name))[1:])


def _expands(a: str, b: str) -> bool:
    """Whether one token is an initial and the other a word it begins."""
    if _is_initial(a) == _is_initial(b):
        return False
    initial, word = (a, b) if _is_initial(a) else (b, a)
    return word.startswith(initial)


def _match_tokens(
    a: Sequence[str], b: Sequence[str], *families: Collection[str]
) -> NameMatch | None:
    """`match_names` on tokens. Each of `families` is one name's family words;
    the match has to reach outside every one of them."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 2:
        return None
    rest = list(long_)
    unpaired: list[str] = []
    full: list[str] = []  # words paired with the same word
    paired: list[str] = []  # every pair, by its word (or its initial)
    for token in short:
        if token in rest:
            rest.remove(token)
            paired.append(token)
            if not _is_initial(token):
                full.append(token)
        else:
            unpaired.append(token)
    for token in unpaired:
        partner = next((other for other in rest if _expands(token, other)), None)
        if partner is None:
            return None
        rest.remove(partner)
        paired.append(partner if _is_initial(token) else token)
    if not full or len(_words(rest)) > 1:
        return None
    if any(family and set(family).issuperset(paired) for family in families):
        return None
    in_full = not unpaired and len(full) >= 2
    if in_full and not any(set(family).issuperset(full) for family in families):
        return "strong"
    return "weak"


def match_names(a: str, b: str, family: Collection[str] = ()) -> NameMatch | None:
    """Whether two names can be one person's, and how firmly.

    The tokens of the shorter name pair off one to one with tokens of the
    longer, in any order, so an inverted name matches. An initial pairs with a
    word it begins. The longer name may keep any number of unpaired initials
    and one unpaired word (a middle name), and at least one word must pair
    with the same word.

    `"strong"` is two or more words paired in full with no initial expanded:
    `Jane Q. Doe` against `Q Doe Jane` or `Doe, Jane`. `"weak"` is any other
    match: `J. Doe`. `None` is a conflict (`Jane R. Doe`, `John Doe`) or a name
    of one token.

    `family` names the words that are family name (see `family_tokens`). A
    match made of those alone is `None`, and a strong match needs a word
    outside them, so a bare `van der Meer` does not match every van der Meer.
    """
    return _match_tokens(name_tokens(a), name_tokens(b), frozenset(family))


def best_match(
    names: Iterable[str], others: Iterable[str], family: Collection[str] = ()
) -> NameMatch | None:
    """The firmest `match_names` between any of `names` and any of `others`."""
    family = frozenset(family)
    left = {name_tokens(name) for name in names}
    right = {name_tokens(name) for name in others}
    best: NameMatch | None = None
    for a in left:
        for b in right:
            match = _match_tokens(a, b, family)
            if match == "strong":
                return match
            best = best or match
    return best


def profile_names(profile: AuthorProfile) -> list[str]:
    """Every spelling on a profile: the preferred name, then the alternatives."""
    names = [profile.display_name or "", *profile.display_name_alternatives]
    return list(dict.fromkeys(name for name in names if name.strip()))


def search_queries(profile: AuthorProfile) -> list[str]:
    """The name searches that can find another profile of this person.

    A query is the words of one spelling, initials dropped: OpenAlex matches
    every query word and folds diacritics, so fewer words return more profiles
    and `Jane Doe` covers `Jane Q. Doe`. A spelling whose words include an
    earlier query's is skipped, as is one with a single word or only the family
    name, which would return every namesake.

    Spellings of the preferred name come first, shortest first.
    """
    preferred = set(name_tokens(profile.display_name or ""))
    family = family_tokens(profile.display_name or "")
    forms: list[tuple[str, ...]] = []
    for name in profile_names(profile):
        words = tuple(dict.fromkeys(_words(name_tokens(name))))
        if len(words) < 2 or family.issuperset(words):
            continue
        if not any(set(words) == set(form) for form in forms):
            forms.append(words)
    forms.sort(key=lambda form: (not preferred.issuperset(form), len(form)))
    queries: list[tuple[str, ...]] = []
    for form in forms:
        if not any(set(query) < set(form) for query in queries):
            queries.append(form)
    return [" ".join(query) for query in queries]


# --- assessment -------------------------------------------------------------


def _fold(text: str) -> str:
    return " ".join(name_tokens(text))


def _same_orcid(a: str | None, b: str | None) -> bool | None:
    """Whether two ORCIDs are the same, or None when either is missing."""
    if not a or not b:
        return None
    return a.strip().lower().rsplit("/", 1)[-1] == b.strip().lower().rsplit("/", 1)[-1]


@dataclass
class DuplicateTitle:
    """Two work records with one title, one on each profile."""

    candidate: AuthorWork
    anchor: AuthorWork

    @property
    def doi_on(self) -> Literal["anchor", "candidate", "both", "neither"]:
        """Which record carries a DOI: the one to cite."""
        if self.anchor.doi and self.candidate.doi:
            return "both"
        if self.anchor.doi:
            return "anchor"
        return "candidate" if self.candidate.doi else "neither"

    def as_dict(self) -> dict[str, Any]:
        def brief(work: AuthorWork) -> dict[str, Any]:
            keys = ("id", "doi", "title", "year", "type", "venue")
            return {key: getattr(work, key) for key in keys}

        return {
            "candidate": brief(self.candidate),
            "anchor": brief(self.anchor),
            "doi_on": self.doi_on,
        }


@dataclass
class Assessment:
    """A verdict on one candidate profile and the evidence behind it.

    The works fields are None when the candidate's works were not compared:
    an ORCID settled it, or the cap left it unchecked.
    """

    verdict: Verdict
    reason: str
    name_match: NameMatch | None = None
    duplicate_titles: list[DuplicateTitle] = field(default_factory=list)
    # Co-authors of the candidate, on works inside the anchor's years, whose
    # full name is also a co-author's on the anchor's works.
    shared_coauthors: list[str] = field(default_factory=list)
    # Work records that list both profiles, left out of the comparison.
    shared_works: list[str] = field(default_factory=list)
    works_compared: int | None = None
    works_in_span: int | None = None
    shared_fields: list[str] = field(default_factory=list)
    shared_institutions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "name_match": self.name_match,
            "evidence": None,
        }
        if self.works_compared is not None:
            out["evidence"] = {
                "duplicate_titles": [dup.as_dict() for dup in self.duplicate_titles],
                "shared_coauthors": self.shared_coauthors,
                "shared_works": self.shared_works,
                "works_compared": self.works_compared,
                "works_in_span": self.works_in_span,
                "shared_fields": self.shared_fields,
                "shared_institutions": self.shared_institutions,
            }
        return out


class Anchor:
    """An anchor profile with its works indexed, to assess many candidates.

    `works` are every work attached to the profile, or as many as were read.
    """

    def __init__(self, profile: AuthorProfile, works: Sequence[AuthorWork] = ()):
        self.profile = profile
        self.names = profile_names(profile)
        self.family = family_tokens(profile.display_name or "")
        self.works = list(works)
        years = [work.year for work in self.works if work.year is not None]
        self.span: tuple[int, int] | None = (min(years), max(years)) if years else None
        self.fields = _own_fields(self.works)
        self.institutions = _own_institutions(profile, self.works)
        self._ids = {work.id for work in self.works if work.id}
        self._titles = [title_tokens(work.title or "") for work in self.works]
        self._title_index: dict[str, list[int]] = {}
        for i, tokens in enumerate(self._titles):
            for token in tokens:
                self._title_index.setdefault(token, []).append(i)
        self._name_tokens = {name_tokens(name) for name in self.names}
        self._coauthors: list[tuple[str, ...]] = []
        self._coauthor_index: dict[str, list[int]] = {}
        seen: set[tuple[str, ...]] = set()
        for tokens, _ in _coauthor_names(self.works, profile.id):
            if tokens in seen or self.is_own_name(tokens):
                continue
            seen.add(tokens)
            for word in set(_words(tokens)):
                self._coauthor_index.setdefault(word, []).append(len(self._coauthors))
            self._coauthors.append(tokens)

    def is_own_name(self, tokens: Sequence[str]) -> bool:
        """Whether `tokens` could be the anchor's own name, under any ID."""
        return any(_match_tokens(tokens, own, self.family) for own in self._name_tokens)

    def in_span(self, work: AuthorWork) -> bool:
        """Whether a work falls inside the anchor's years; undated counts as in."""
        if self.span is None or work.year is None:
            return True
        return self.span[0] <= work.year <= self.span[1]

    def duplicate_of(self, work: AuthorWork) -> AuthorWork | None:
        """The anchor's record with `work`'s title, if it is another record."""
        tokens = title_tokens(work.title or "")
        if len(tokens) < MIN_TITLE_TOKENS:
            return None
        overlap = Counter(
            i for token in tokens for i in self._title_index.get(token, ())
        )
        for i, shared in overlap.most_common():
            if shared < MIN_TITLE_TOKENS:
                break
            if same_title(tokens, self._titles[i]):
                return self.works[i]
        return None

    def has_coauthor(self, tokens: Sequence[str]) -> bool:
        """Whether a full name is a co-author's on the anchor's works."""
        words = set(_words(tokens))
        if len(words) < 2:
            return False
        overlap = Counter(
            i for word in words for i in self._coauthor_index.get(word, ())
        )
        return any(
            shared >= 2 and _match_tokens(tokens, self._coauthors[i]) == "strong"
            for i, shared in overlap.items()
        )

    def has_work(self, work: AuthorWork) -> bool:
        return work.id is not None and work.id in self._ids


def _coauthor_names(
    works: Iterable[AuthorWork], own_id: str | None
) -> Iterable[tuple[tuple[str, ...], str]]:
    """`(tokens, name as printed)` for every author but the profile's own."""
    for work in works:
        for author in work.authors:
            if own_id is not None and author.id == own_id:
                continue
            for name in author.names:
                yield name_tokens(name), name


def _own_fields(works: Iterable[AuthorWork]) -> dict[str, str]:
    """Folded field name -> field name, over a profile's works."""
    return {
        _fold(work.field_name): work.field_name for work in works if work.field_name
    }


def _own_institutions(
    profile: AuthorProfile, works: Iterable[AuthorWork]
) -> dict[str, str]:
    """Folded name -> name of every institution tied to the profile: its last
    known ones, and those on its own authorships."""
    names = list(profile.last_known_institutions)
    for work in works:
        for author in work.authors:
            if profile.id is not None and author.id == profile.id:
                names.extend(author.institutions)
    return {_fold(name): name for name in names if name}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def assess(
    anchor: Anchor,
    candidate: AuthorProfile,
    works: Sequence[AuthorWork] | None = None,
) -> Assessment:
    """Weigh `candidate` as a fragment of the anchor's profile.

    `works` are the candidate's works, or None when they were not fetched.
    In order:

    - No name match (`best_match` over both profiles' spellings): `different`.
    - Both have an ORCID: `likely-fragment` when it is the same, `different`
      when it is not. Neither needs the works.
    - `works` is None: `unchecked`.
    - A work with the title of one of the anchor's, on another record, or
      enough co-authors in common on works inside the anchor's years (one with
      a strong name match, two with a weak one): `likely-fragment`.
    - No works on either side to compare: `possible`.
    - Every dated work outside the anchor's years, or no field and no
      institution in common: `different`.
    - Otherwise `possible`: the name matches and a field or an institution
      overlaps, with nothing from the works either way.

    A work record that lists both profiles is left out of the comparison and
    reported in `shared_works`: it would share every co-author with itself.
    """
    match = best_match(profile_names(candidate), anchor.names, anchor.family)
    if match is None:
        return Assessment(Verdict.DIFFERENT, "the names do not match")
    same_orcid = _same_orcid(anchor.profile.orcid, candidate.orcid)
    if same_orcid:
        return Assessment(Verdict.LIKELY_FRAGMENT, "same ORCID", match)
    if same_orcid is False:
        return Assessment(Verdict.DIFFERENT, "different ORCID", match)
    if works is None:
        return Assessment(Verdict.UNCHECKED, "works not compared", match)

    shared_works = [work.id for work in works if work.id and anchor.has_work(work)]
    own = [work for work in works if not anchor.has_work(work)]
    duplicates = [
        DuplicateTitle(candidate=work, anchor=twin)
        for work in own
        if (twin := anchor.duplicate_of(work)) is not None
    ]
    inside = [work for work in own if anchor.in_span(work)]
    coauthors: dict[tuple[str, ...], str] = {}
    for tokens, name in _coauthor_names(inside, candidate.id):
        if tokens in coauthors or anchor.is_own_name(tokens):
            continue
        if anchor.has_coauthor(tokens):
            coauthors[tokens] = name
    fields = _own_fields(own)
    institutions = _own_institutions(candidate, own)
    result = Assessment(
        Verdict.POSSIBLE,
        "",
        match,
        duplicate_titles=duplicates,
        shared_coauthors=sorted(coauthors.values()),
        shared_works=shared_works,
        works_compared=len(works),
        works_in_span=len(inside),
        shared_fields=sorted(
            name for key, name in fields.items() if key in anchor.fields
        ),
        shared_institutions=sorted(
            name for key, name in institutions.items() if key in anchor.institutions
        ),
    )
    needed = SHARED_COAUTHORS_STRONG if match == "strong" else SHARED_COAUTHORS_WEAK
    enough = len(coauthors) >= needed
    if duplicates or enough:
        parts = []
        if duplicates:
            parts.append(_plural(len(duplicates), "duplicate title"))
        if enough:
            parts.append(_plural(len(coauthors), "shared co-author"))
        result.verdict = Verdict.LIKELY_FRAGMENT
        result.reason = " and ".join(parts)
    elif not own or not anchor.works:
        result.reason = "no works to compare"
    elif not inside:
        result.verdict = Verdict.DIFFERENT
        result.reason = "works fall outside the anchor's years"
    elif not result.shared_fields and not result.shared_institutions:
        result.verdict = Verdict.DIFFERENT
        result.reason = "no title, co-author, field, or institution in common"
    else:
        result.reason = "the name and a field or an institution match, the works do not"
    return result


# --- the whole check --------------------------------------------------------


@dataclass
class Candidate:
    """A profile that passed the name gate, with its assessment."""

    profile: AuthorProfile
    assessment: Assessment

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self.profile), **self.assessment.as_dict()}


_VERDICT_ORDER = (
    Verdict.LIKELY_FRAGMENT,
    Verdict.POSSIBLE,
    Verdict.UNCHECKED,
    Verdict.DIFFERENT,
)


@dataclass
class FragmentReport:
    """What `find_fragments` found for one anchor."""

    anchor: AuthorProfile
    # Works read for the anchor, and how many it has; both None when no
    # candidate needed them.
    anchor_works: int | None = None
    anchor_works_total: int | None = None
    anchor_years: tuple[int, int] | None = None
    # One `{"query", "count", "returned"}` per search made.
    searches: list[dict[str, Any]] = field(default_factory=list)
    queries_skipped: int = 0  # name queries beyond the search cap
    # Every profile that passed the name gate, most likely first.
    candidates: list[Candidate] = field(default_factory=list)
    profiles_searched: int = 0  # distinct profiles returned, anchor excluded
    name_mismatches: int = 0
    works_lists: int = 0  # works-list pages asked for
    network_calls: int = 0  # requests that reached the network
    credits_before: float | None = None
    credits_after: float | None = None

    def counts(self) -> dict[str, int]:
        """Profiles returned, split by how each was settled."""
        verdicts = Counter(
            "different_orcid"
            if c.assessment.reason == "different ORCID"
            else c.assessment.verdict.value
            for c in self.candidates
        )
        return {
            "profiles_searched": self.profiles_searched,
            "name_mismatch": self.name_mismatches,
            "different_orcid": verdicts["different_orcid"],
            **{verdict.value: verdicts[verdict.value] for verdict in _VERDICT_ORDER},
        }

    def as_dict(self, everything: bool = False) -> dict[str, Any]:
        """The report as JSON-ready data.

        Lists the `likely-fragment` and `possible` candidates; `everything`
        adds the `unchecked` and `different` ones, which are otherwise only
        counted.
        """
        shown = (Verdict.LIKELY_FRAGMENT, Verdict.POSSIBLE)
        listed = [
            c for c in self.candidates if everything or c.assessment.verdict in shown
        ]
        return {
            "anchor": {
                **asdict(self.anchor),
                "works_compared": self.anchor_works,
                "works_total": self.anchor_works_total,
                "years": list(self.anchor_years) if self.anchor_years else None,
            },
            "searches": self.searches,
            "queries_skipped": self.queries_skipped,
            "candidates": [c.as_dict() for c in listed],
            "counts": self.counts(),
            "budget": {
                "searches": len(self.searches),
                "works_lists": self.works_lists,
                "network_calls": self.network_calls,
                "credits_before": self.credits_before,
                "credits_after": self.credits_after,
            },
        }


def _header_number(snapshot: dict[str, Any] | None, name: str) -> float | None:
    headers = snapshot.get("headers") if isinstance(snapshot, dict) else None
    try:
        return float(headers[name]) if isinstance(headers, dict) else None
    except (KeyError, TypeError, ValueError):
        return None


class _Meter:
    """Reads the credit balance around a run from the client's quota headers.

    The balance before is the first response's remaining credits plus what
    that request cost; a run answered from the cache has neither reading.
    """

    def __init__(self, client: OpenAlexClient) -> None:
        self.client = client
        self.start = client.network_calls
        self.before: float | None = None
        self.after: float | None = None

    def observe(self) -> None:
        if self.client.network_calls == self.start:
            return
        remaining = _header_number(self.client.rate_limit, "x-ratelimit-remaining")
        if remaining is None:
            return
        if self.before is None:
            used = _header_number(self.client.rate_limit, "x-ratelimit-credits-used")
            self.before = remaining + (used or 0.0)
        self.after = remaining

    @property
    def calls(self) -> int:
        return self.client.network_calls - self.start


def _anchor_works(
    client: OpenAlexClient, author_id: str, refresh: bool, meter: _Meter
) -> tuple[list[AuthorWork], int | None, int]:
    """Every work of the anchor, up to `MAX_ANCHOR_PAGES` pages.

    Returns the works, the total OpenAlex reports, and the pages asked for.
    """
    works: dict[str, AuthorWork] = {}
    total: int | None = None
    pages = 0
    for number in range(1, MAX_ANCHOR_PAGES + 1):
        page = client.author_works_page(author_id, page=number, refresh=refresh)
        meter.observe()
        pages += 1
        results = page.get("results") or []
        for record in results:
            work = openalex_to_author_work(record)
            works.setdefault(work.id or f"#{len(works)}", work)
        count = (page.get("meta") or {}).get("count")
        total = count if isinstance(count, int) else total
        if len(results) < MAX_PER_PAGE or (total is not None and len(works) >= total):
            break
    return list(works.values()), total, pages


def find_fragments(
    client: OpenAlexClient,
    author_id: str,
    *,
    max_searches: int = DEFAULT_MAX_SEARCHES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    refresh: bool = False,
) -> FragmentReport | None:
    """Search for other profiles of one author and assess each.

    `author_id` is an OpenAlex author ID or an ORCID (as `lookup_author`
    takes); None is returned when OpenAlex has no such profile.

    Runs `search_queries` up to `max_searches`, keeps the profiles whose names
    match the anchor's, settles those with an ORCID, and compares works for up
    to `max_candidates` of the rest: strong name matches first, then the
    profiles with the fewest works, since a fragment is typically a handful.
    The rest are `unchecked`. The anchor's works are read only when a
    candidate needs them.

    Every request is cached by URL, so a re-run costs nothing and an
    interrupted run resumes. `refresh` fetches the searches and works lists
    again: a cached search page does not learn that OpenAlex has since split
    or merged a profile.
    """
    meter = _Meter(client)
    profile = openalex_to_author(client.lookup_author(author_id))
    meter.observe()
    if profile is None:
        return None
    report = FragmentReport(anchor=profile)
    queries = search_queries(profile)
    report.queries_skipped = max(0, len(queries) - max_searches)

    found: dict[str, AuthorProfile] = {}
    for query in queries[:max_searches]:
        page = client.author_search_page(query, rows=MAX_PER_PAGE, refresh=refresh)
        meter.observe()
        results = page.get("results") or []
        report.searches.append(
            {
                "query": query,
                "count": (page.get("meta") or {}).get("count"),
                "returned": len(results),
            }
        )
        for result in results:
            other = openalex_to_author(result)
            if other is None or not other.id or other.id == profile.id:
                continue
            found.setdefault(other.id, other)
    report.profiles_searched = len(found)

    anchor = Anchor(profile)
    settled: list[Candidate] = []
    open_: list[Candidate] = []
    for other in found.values():
        assessment = assess(anchor, other)
        if assessment.name_match is None:
            report.name_mismatches += 1
        elif assessment.verdict is Verdict.UNCHECKED:
            open_.append(Candidate(other, assessment))
        else:
            settled.append(Candidate(other, assessment))

    open_.sort(
        key=lambda c: (
            c.assessment.name_match != "strong",
            c.profile.works_count or 0,
            c.profile.id or "",
        )
    )
    checked = open_[: max(0, max_candidates)]
    if checked and profile.id:
        works, total, pages = _anchor_works(client, profile.id, refresh, meter)
        anchor = Anchor(profile, works)
        report.anchor_works, report.anchor_works_total = len(works), total
        report.anchor_years = anchor.span
        report.works_lists += pages
        for candidate in checked:
            page = client.author_works_page(candidate.profile.id or "", refresh=refresh)
            meter.observe()
            report.works_lists += 1
            theirs = [openalex_to_author_work(w) for w in page.get("results") or []]
            candidate.assessment = assess(anchor, candidate.profile, theirs)

    report.candidates = sorted(
        [*settled, *open_],
        key=lambda c: (
            _VERDICT_ORDER.index(c.assessment.verdict),
            c.assessment.name_match != "strong",
            c.profile.works_count or 0,
            c.profile.id or "",
        ),
    )
    report.network_calls = meter.calls
    report.credits_before, report.credits_after = meter.before, meter.after
    return report


# --- two IDs in one bibliography --------------------------------------------


def same_name_pairs(
    profiles: Iterable[tuple[str, AuthorProfile]],
) -> list[tuple[str, str]]:
    """Pairs of profiles under different IDs whose names match in full.

    `profiles` are `(label, profile)` pairs, the label being whatever the
    caller knows each by; the result pairs labels, in the order given. Two
    labels that resolve to one profile are not a pair. Only a strong
    `match_names` counts, across every spelling of both: nearly every profile
    lists an initials form, so a weak match would pair any two authors who
    share a family name and an initial.

    In a bibliography such a pair is one person split across two profiles or
    two namesakes; `find_fragments` on either settles which.
    """
    entries = [(label, profile) for label, profile in profiles if profile.id]
    spellings = [{name_tokens(name) for name in profile_names(p)} for _, p in entries]
    families = [family_tokens(p.display_name or "") for _, p in entries]
    by_word: dict[str, list[int]] = {}
    for i, forms in enumerate(spellings):
        for word in {word for tokens in forms for word in _words(tokens)}:
            by_word.setdefault(word, []).append(i)
    # Two profiles can only match in full if they share two words.
    shared: Counter[tuple[int, int]] = Counter()
    for members in by_word.values():
        for at, i in enumerate(members):
            for j in members[at + 1 :]:
                shared[(i, j)] += 1
    pairs: list[tuple[str, str]] = []
    for (i, j), words in sorted(shared.items()):
        if words < 2 or entries[i][1].id == entries[j][1].id:
            continue
        # Each profile's own family words: one of the two names may be inverted.
        if any(
            _match_tokens(a, b, families[i], families[j]) == "strong"
            for a in spellings[i]
            for b in spellings[j]
        ):
            pairs.append((entries[i][0], entries[j][0]))
    return pairs
