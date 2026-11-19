"""Tests for the author-fragment check: the name gate, the assessment, and
the run against a mocked OpenAlex. Every profile and work here is synthetic."""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from citefinder.adapters import AuthorProfile, AuthorWork, WorkAuthor
from citefinder.fragments import (
    Anchor,
    Verdict,
    assess,
    best_match,
    family_tokens,
    find_fragments,
    match_names,
    name_tokens,
    same_name_pairs,
    search_queries,
)
from citefinder.openalex import OpenAlexClient

ANCHOR_ID = "https://openalex.org/A1"
OTHER_ID = "https://openalex.org/A2"


# --- names ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "tokens"),
    [
        ("Jane Q. Doe", ("jane", "q", "doe")),
        ("DOE, Jané", ("doe", "jane")),
        ("G.L.A. van der Meer", ("g", "l", "a", "van", "der", "meer")),
        ("Séan O’Brien-Nava", ("sean", "obrien", "nava")),
        ("Emine Yılmaz", ("emine", "yilmaz")),
        ("Doe, Jane 1959-", ("doe", "jane")),
        ("Jane\n   Doe", ("jane", "doe")),
        ("", ()),
    ],
)
def test_name_tokens_fold_case_diacritics_and_punctuation(
    name: str, tokens: tuple[str, ...]
) -> None:
    assert name_tokens(name) == tokens


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("Q Doe Jane", "strong"),  # the tokens in another order
        ("Doe, Jane", "strong"),  # the middle initial left off
        ("Jané Q. Doe", "strong"),  # a diacritic one side lacks
        ("J. Doe", "weak"),  # an initial for the given name
        ("Jane Quinn Doe", "weak"),  # the initial spelled out
        ("Q. Doe", "weak"),
        ("Jane R. Doe", None),  # a conflicting initial
        ("John Doe", None),
        ("Jane Roe", None),
        ("Doe", None),  # one token says nothing
        ("Jane Doe Smith Jones", None),  # two extra words is another name
    ],
)
def test_match_names_against_one_name(candidate: str, expected: str | None) -> None:
    family = family_tokens("Jane Q. Doe")
    assert match_names(candidate, "Jane Q. Doe", family) == expected
    assert match_names("Jane Q. Doe", candidate, family) == expected


def test_family_tokens_reads_the_order_or_the_comma() -> None:
    assert family_tokens("Jane Q. Doe") == {"doe"}
    assert family_tokens("Toni G.L.A. van der Meer") == {"van", "der", "meer"}
    assert family_tokens("Willem De Vos") == {"de", "vos"}
    assert family_tokens("de Vos, Willem M.") == {"de", "vos"}
    assert family_tokens("J. Ignacio Doe") == {"doe"}
    assert family_tokens("Madonna") == frozenset()


def test_a_family_name_alone_is_not_a_match() -> None:
    family = family_tokens("Toni G.L.A. van der Meer")
    # A bare family name among the alternatives must not admit every namesake.
    assert match_names("Jan T. M. van der Meer", "van der Meer", family) is None
    assert match_names("van der Meer", "Van Der Meer", family) is None
    # Without the family words it would.
    assert match_names("van der Meer", "Van Der Meer") == "strong"
    # An initial for the given name is still a weak match, never a strong one.
    assert match_names("T. van der Meer", "Toni van der Meer", family) == "weak"
    assert match_names("W. De Vos", "W. De Vos", {"de", "vos"}) == "weak"
    assert match_names("Toni van der Meer", "Toni G.L.A. van der Meer", family) == (
        "strong"
    )


def test_a_lone_cjk_character_is_a_word_not_an_initial() -> None:
    assert match_names("李 明", "明 李") == "strong"
    assert match_names("李 明", "李 萌") is None


def test_best_match_takes_the_firmest_pair_of_spellings() -> None:
    family = family_tokens("Jane Q. Doe")
    anchor = ["Jane Q. Doe", "J. Doe"]
    assert best_match(["J Doe", "Jane Doe"], anchor, family) == "strong"
    assert best_match(["J Doe"], anchor, family) == "weak"
    # `John Doe` reaches the anchor only through its initials form.
    assert best_match(["John Doe"], anchor, family) == "weak"
    assert best_match(["John Roe"], anchor, family) is None
    assert best_match([], anchor, family) is None


def _profile(
    name: str,
    *alternatives: str,
    id: str = ANCHOR_ID,
    orcid: str | None = None,
    works_count: int = 1,
    institutions: tuple[str, ...] = (),
) -> AuthorProfile:
    return AuthorProfile(
        id=id,
        display_name=name,
        display_name_alternatives=[*alternatives, name],
        orcid=orcid,
        works_count=works_count,
        last_known_institutions=list(institutions),
    )


def test_search_queries_drop_initials_and_narrower_spellings() -> None:
    profile = _profile(
        "Jane Quinn Doe", "Doe, Jane Q.", "J. Doe", "Jané Doe", "Prof. Jane Doe"
    )
    # `jane doe` returns every profile the longer spellings would.
    assert search_queries(profile) == ["doe jane"]


def test_search_queries_keep_a_differently_spelled_name() -> None:
    profile = _profile("Tim Doe", "Doe, Timothy", "T. Doe", "Timothy Doe (12345)")
    assert search_queries(profile) == ["tim doe", "doe timothy"]


def test_search_queries_never_search_a_family_name_alone() -> None:
    profile = _profile(
        "Toni G.L.A. van der Meer", "G.L.A. van der Meer", "Van Der Meer", "T. Meer"
    )
    assert search_queries(profile) == ["toni van der meer"]
    assert search_queries(_profile("Madonna")) == []
    assert search_queries(AuthorProfile()) == []


def test_search_queries_put_the_preferred_names_spellings_first() -> None:
    profile = _profile("Willem M. de Vos", "Doe Wittern", "Willem M Vos", "W. Devos")
    assert search_queries(profile) == ["willem vos", "doe wittern"]


# --- assessment ----------------------------------------------------------------


def _work(
    title: str,
    year: int | None = 2015,
    *coauthors: str,
    id: str = "",
    doi: str | None = None,
    own: str | None = OTHER_ID,
    field: str | None = None,
    institution: str | None = None,
) -> AuthorWork:
    authors = [
        WorkAuthor(
            id=own, names=["X"], institutions=[institution] if institution else []
        )
    ]
    authors += [WorkAuthor(id=None, names=[name]) for name in coauthors]
    return AuthorWork(
        id=id or f"https://openalex.org/W{abs(hash((title, year, own)))}",
        doi=doi,
        title=title,
        year=year,
        field_name=field,
        authors=authors,
    )


ANCHOR_ORCID = "https://orcid.org/0000-0002-1825-0097"
ANCHOR_PROFILE = _profile(
    "Jane Q. Doe", "J. Doe", orcid=ANCHOR_ORCID, institutions=("Example University",)
)
ANCHOR_WORKS = [
    _work(
        "Measuring the spread of rumours in networks",
        2012,
        "Sam Lee",
        "María García",
        id="https://openalex.org/W10",
        doi="10.1/rumours",
        own=ANCHOR_ID,
        field="Computer Science",
    ),
    _work(
        "A survey of things",
        2020,
        "Robin Park",
        id="https://openalex.org/W11",
        own=ANCHOR_ID,
        field="Computer Science",
    ),
]


@pytest.fixture
def anchor() -> Anchor:
    return Anchor(ANCHOR_PROFILE, ANCHOR_WORKS)


def _candidate(name: str = "Q Doe Jane", **kwargs: Any) -> AuthorProfile:
    return _profile(name, id=OTHER_ID, **kwargs)


def test_anchor_reads_its_years_fields_and_institutions(anchor: Anchor) -> None:
    assert anchor.span == (2012, 2020)
    assert set(anchor.fields) == {"computer science"}
    assert set(anchor.institutions) == {"example university"}
    assert Anchor(ANCHOR_PROFILE).span is None


def test_assess_rejects_a_name_that_does_not_match(anchor: Anchor) -> None:
    result = assess(anchor, _candidate("John Roe"), ANCHOR_WORKS)
    assert (result.verdict, result.name_match) == (Verdict.DIFFERENT, None)
    assert result.reason == "the names do not match"


def test_assess_settles_on_an_orcid_without_works() -> None:
    orcid = "https://orcid.org/0000-0002-1825-0097"
    anchor = Anchor(_profile("Jane Q. Doe", orcid=orcid))
    same = assess(anchor, _candidate(orcid="0000-0002-1825-0097"))
    assert (same.verdict, same.reason) == (Verdict.LIKELY_FRAGMENT, "same ORCID")
    other = assess(anchor, _candidate(orcid="https://orcid.org/0000-0001-0000-0000"))
    assert (other.verdict, other.reason) == (Verdict.DIFFERENT, "different ORCID")
    # One ORCID on either side settles nothing.
    assert assess(anchor, _candidate()).verdict is Verdict.UNCHECKED


def test_assess_without_works_is_unchecked_never_different(anchor: Anchor) -> None:
    result = assess(anchor, _candidate())
    assert (result.verdict, result.name_match) == (Verdict.UNCHECKED, "strong")
    assert result.works_compared is None
    assert result.as_dict()["evidence"] is None


def test_a_duplicate_title_on_another_record_is_a_likely_fragment(
    anchor: Anchor,
) -> None:
    duplicate = _work("Measuring the Spread of Rumours in Networks.", 2012)
    result = assess(anchor, _candidate(), [duplicate])
    assert result.verdict is Verdict.LIKELY_FRAGMENT
    assert result.reason == "1 duplicate title"
    [found] = result.duplicate_titles
    assert found.anchor.id == "https://openalex.org/W10"
    # The anchor's record carries the DOI: that is the one to cite.
    assert found.doi_on == "anchor"
    evidence = result.as_dict()["evidence"]
    assert evidence["duplicate_titles"][0]["anchor"]["doi"] == "10.1/rumours"
    assert evidence["duplicate_titles"][0]["candidate"]["doi"] is None


@pytest.mark.parametrize(
    ("anchor_doi", "candidate_doi", "doi_on"),
    [
        ("10.1/a", "10.1/b", "both"),
        (None, "10.1/b", "candidate"),
        (None, None, "neither"),
    ],
)
def test_duplicate_title_says_which_record_has_the_doi(
    anchor_doi: str | None, candidate_doi: str | None, doi_on: str
) -> None:
    mine = _work("One title of several words", id="W1", doi=anchor_doi, own=ANCHOR_ID)
    anchor = Anchor(ANCHOR_PROFILE, [mine])
    theirs = _work("One title of several words", id="W2", doi=candidate_doi)
    [found] = assess(anchor, _candidate(), [theirs]).duplicate_titles
    assert found.doi_on == doi_on


def test_a_short_or_merely_similar_title_is_not_a_duplicate(anchor: Anchor) -> None:
    works = [
        _work("A survey", 2020),  # too short to be distinctive
        _work("Measuring the spread of disease in herds", 2012),
    ]
    assert assess(anchor, _candidate(), works).duplicate_titles == []


def test_a_record_listing_both_profiles_is_set_aside(anchor: Anchor) -> None:
    # The anchor's own record, on the candidate's list too: two authorships on
    # one work. It shares its title and every co-author with itself.
    result = assess(anchor, _candidate(), [ANCHOR_WORKS[0]])
    assert result.shared_works == ["https://openalex.org/W10"]
    assert result.duplicate_titles == []
    assert result.shared_coauthors == []
    assert (result.verdict, result.reason) == (Verdict.POSSIBLE, "no works to compare")


def test_one_shared_coauthor_is_enough_with_a_strong_name_match(
    anchor: Anchor,
) -> None:
    # The duplicate record inverted every name on it.
    work = _work("Another paper entirely", 2013, "Lee Sam", "Kim Other")
    result = assess(anchor, _candidate(), [work])
    assert result.verdict is Verdict.LIKELY_FRAGMENT
    assert result.reason == "1 shared co-author"
    assert result.shared_coauthors == ["Lee Sam"]


def test_a_weak_name_match_needs_two_shared_coauthors(anchor: Anchor) -> None:
    one = _work("Another paper entirely", 2013, "Sam Lee", field="Computer Science")
    result = assess(anchor, _candidate("J. Doe"), [one])
    assert (result.name_match, result.verdict) == ("weak", Verdict.POSSIBLE)
    assert result.shared_coauthors == ["Sam Lee"]

    two = _work("Another paper entirely", 2013, "Sam Lee", "Garcia, Maria")
    result = assess(anchor, _candidate("J. Doe"), [two])
    assert result.verdict is Verdict.LIKELY_FRAGMENT
    assert result.reason == "2 shared co-authors"


def test_a_coauthor_counts_only_by_full_name_and_inside_the_years(
    anchor: Anchor,
) -> None:
    works = [
        _work("Initials only", 2013, "S. Lee", "Lee", field="Computer Science"),
        _work("Long before the anchor", 1975, "Sam Lee"),
        # The anchor's own name is not its co-author.
        _work("With a namesake", 2014, "Jane Doe"),
    ]
    result = assess(anchor, _candidate(), works)
    assert result.shared_coauthors == []
    assert result.works_in_span == 2
    assert result.verdict is Verdict.POSSIBLE


def test_an_undated_work_counts_as_inside_the_years(anchor: Anchor) -> None:
    result = assess(anchor, _candidate(), [_work("No year on it", None, "Sam Lee")])
    assert result.works_in_span == 1
    assert result.verdict is Verdict.LIKELY_FRAGMENT


def test_works_outside_the_anchors_years_are_a_different_person(
    anchor: Anchor,
) -> None:
    works = [_work("An old paper", 1975, field="Computer Science")]
    result = assess(anchor, _candidate(), works)
    assert result.verdict is Verdict.DIFFERENT
    assert result.reason == "works fall outside the anchor's years"
    assert result.works_in_span == 0


def test_no_field_or_institution_in_common_is_a_different_person(
    anchor: Anchor,
) -> None:
    result = assess(anchor, _candidate(), [_work("On barley", 2015, field="Agronomy")])
    assert result.verdict is Verdict.DIFFERENT
    assert result.reason == "no title, co-author, field, or institution in common"


def test_a_shared_field_or_institution_alone_is_only_possible(anchor: Anchor) -> None:
    by_field = assess(
        anchor, _candidate(), [_work("On graphs", 2015, field="Computer science")]
    )
    assert by_field.verdict is Verdict.POSSIBLE
    assert by_field.shared_fields == ["Computer science"]

    work = _work("On barley", 2015, field="Agronomy", institution="Example University")
    by_work = assess(anchor, _candidate(), [work])
    assert by_work.verdict is Verdict.POSSIBLE
    assert by_work.shared_institutions == ["Example University"]

    profile = _candidate(institutions=("EXAMPLE UNIVERSITY",))
    by_profile = assess(anchor, profile, [_work("On barley", 2015, field="Agronomy")])
    assert by_profile.verdict is Verdict.POSSIBLE


def test_no_works_on_either_side_is_possible_not_different() -> None:
    bare = Anchor(ANCHOR_PROFILE)
    assert assess(bare, _candidate(), []).verdict is Verdict.POSSIBLE
    result = assess(bare, _candidate(), [_work("On barley", 2015)])
    assert (result.verdict, result.reason) == (Verdict.POSSIBLE, "no works to compare")


# --- two IDs in one bibliography -------------------------------------------------


def test_same_name_pairs_flags_full_name_matches_across_ids() -> None:
    profiles = [
        ("A1", _profile("Jane Q. Doe", "J. Doe", id="A1")),
        ("A2", _profile("Sam Lee", "S. Lee", id="A2")),
        ("A3", _profile("Doe Jane", id="A3")),
        # Shares only the initials form with A1.
        ("A4", _profile("John Doe", "J. Doe", id="A4")),
        # An ID OpenAlex has merged into A2's profile is not a second person.
        ("A5", _profile("Sam Lee", "S. Lee", id="A2")),
        ("A6", _profile("Jané Doe", id="A6")),
        ("A7", AuthorProfile()),
    ]
    assert same_name_pairs(profiles) == [("A1", "A3"), ("A1", "A6"), ("A3", "A6")]
    assert same_name_pairs([]) == []


# --- the whole check -------------------------------------------------------------

API = "https://api.openalex.org"


def _record(profile: AuthorProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "orcid": profile.orcid,
        "display_name": profile.display_name,
        "display_name_alternatives": profile.display_name_alternatives,
        "works_count": profile.works_count,
        "last_known_institutions": [
            {"display_name": name} for name in profile.last_known_institutions
        ],
    }


def _raw_work(work: AuthorWork) -> dict[str, Any]:
    return {
        "id": work.id,
        "doi": f"https://doi.org/{work.doi}" if work.doi else None,
        "display_name": work.title,
        "publication_year": work.year,
        "authorships": [
            {
                "author": {"id": author.id, "display_name": author.names[0]},
                "institutions": [{"display_name": n} for n in author.institutions],
            }
            for author in work.authors
        ],
        "primary_topic": {"field": {"display_name": work.field_name}},
    }


FRAGMENT = _profile("Q Doe Jane", id="https://openalex.org/A2")
NAMESAKE = _profile("Jane Doe", id="https://openalex.org/A3", works_count=4)
INITIALS = _profile("J. Doe", id="https://openalex.org/A4", works_count=2)
STRANGER = _profile("John Roe", id="https://openalex.org/A5")
WITH_ORCID = _profile(
    "Jane Doe",
    id="https://openalex.org/A6",
    orcid="https://orcid.org/0000-0001-0000-0000",
)
WORKS = {
    "A1": ANCHOR_WORKS,
    "A2": [_work("Measuring the spread of rumours in networks", 2012, own=FRAGMENT.id)],
    "A3": [_work("On barley", 2015, own=NAMESAKE.id, field="Agronomy")],
    "A4": [_work("On graphs", 2016, own=INITIALS.id, field="Computer Science")],
}


@pytest.fixture
def api(monkeypatch, mock_response) -> Callable[..., list[str]]:
    """Serve one anchor, a search page, and works lists; returns the paths asked."""

    def _install(
        anchor: AuthorProfile = ANCHOR_PROFILE,
        found: tuple[AuthorProfile, ...] = (
            FRAGMENT,
            NAMESAKE,
            INITIALS,
            STRANGER,
            WITH_ORCID,
        ),
        remaining: int = 1000,
        fail: str | None = None,
    ) -> list[str]:
        calls: list[str] = []
        left = [remaining]

        def get(self: object, url: str, **kwargs: object) -> Any:
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            if parts.path.startswith("/authors/"):
                calls.append(parts.path)
                if parts.path.rsplit("/", 1)[-1] != "A1":
                    return mock_response(404)
                payload, cost = _record(anchor), 0
            elif parts.path == "/authors":
                calls.append(f"search:{query['search'][0]}")
                results = [_record(anchor), *(_record(p) for p in found)]
                payload, cost = {"meta": {"count": 6}, "results": results}, 10
            else:
                author = query["filter"][0].rsplit(":", 1)[-1]
                calls.append(f"works:{author}:{query['page'][0]}")
                if author == fail:
                    return mock_response(500)
                works = [_raw_work(w) for w in WORKS.get(author, [])]
                payload, cost = {"meta": {"count": len(works)}, "results": works}, 1
            left[0] -= cost
            headers = {
                "X-RateLimit-Remaining": str(left[0]),
                "X-RateLimit-Credits-Used": str(cost),
            }
            return mock_response(200, payload, headers)

        monkeypatch.setattr("requests.Session.get", get)
        return calls

    return _install


@pytest.fixture
def client(tmp_path: Path) -> OpenAlexClient:
    return OpenAlexClient(cache_path=tmp_path / "openalex.jsonl", min_interval=0)


def test_find_fragments_runs_the_whole_check(
    client: OpenAlexClient, api: Callable[..., list[str]]
) -> None:
    calls = api()
    report = find_fragments(client, "A1")

    assert report is not None
    # One search, the anchor's works once, then a works list per open candidate:
    # the strong name matches first, the smaller profile first among them.
    assert calls == [
        "/authors/A1",
        "search:jane doe",
        "works:A1:1",
        "works:A2:1",
        "works:A3:1",
        "works:A4:1",
    ]
    verdicts = {c.profile.id: c.assessment.verdict for c in report.candidates}
    assert verdicts == {
        FRAGMENT.id: Verdict.LIKELY_FRAGMENT,
        INITIALS.id: Verdict.POSSIBLE,
        NAMESAKE.id: Verdict.DIFFERENT,
        WITH_ORCID.id: Verdict.DIFFERENT,
    }
    assert report.counts() == {
        "profiles_searched": 5,
        "name_mismatch": 1,
        "different_orcid": 1,
        "likely-fragment": 1,
        "possible": 1,
        "unchecked": 0,
        "different": 1,
    }
    assert (report.anchor_works, report.anchor_works_total) == (2, 2)
    assert report.anchor_years == (2012, 2020)
    assert report.searches == [{"query": "jane doe", "count": 6, "returned": 6}]
    # Ten credits for the search, one per works list; the lookup is free.
    assert (report.credits_before, report.credits_after) == (1000, 986)
    assert (report.works_lists, report.network_calls) == (4, 6)


def test_find_fragments_report_lists_what_an_editor_acts_on(
    client: OpenAlexClient, api: Callable[..., list[str]]
) -> None:
    api()
    report = find_fragments(client, "A1")
    assert report is not None
    shown = report.as_dict()
    assert shown["anchor"]["display_name"] == "Jane Q. Doe"
    assert shown["anchor"]["years"] == [2012, 2020]
    assert [c["verdict"] for c in shown["candidates"]] == [
        "likely-fragment",
        "possible",
    ]
    fragment = shown["candidates"][0]
    assert fragment["id"] == FRAGMENT.id
    assert fragment["reason"] == "1 duplicate title"
    assert fragment["evidence"]["duplicate_titles"][0]["doi_on"] == "anchor"
    assert shown["budget"] == {
        "searches": 1,
        "works_lists": 4,
        "network_calls": 6,
        "credits_before": 1000,
        "credits_after": 986,
    }
    everything = report.as_dict(everything=True)
    assert [(c["verdict"], c["reason"]) for c in everything["candidates"]] == [
        ("likely-fragment", "1 duplicate title"),
        ("possible", "the name and a field or an institution match, the works do not"),
        ("different", "different ORCID"),
        ("different", "no title, co-author, field, or institution in common"),
    ]


def test_find_fragments_settles_an_orcid_without_a_works_list(
    client: OpenAlexClient, api: Callable[..., list[str]]
) -> None:
    same = _profile("Jane Doe", id="https://openalex.org/A7", orcid=ANCHOR_ORCID)
    calls = api(found=(same, WITH_ORCID, STRANGER))
    report = find_fragments(client, "A1")

    assert report is not None
    # No candidate is left open, so not even the anchor's works are read.
    assert calls == ["/authors/A1", "search:jane doe"]
    assert report.counts() == {
        "profiles_searched": 3,
        "name_mismatch": 1,
        "different_orcid": 1,
        "likely-fragment": 1,
        "possible": 0,
        "unchecked": 0,
        "different": 0,
    }
    assert report.anchor_works is None
    assert report.as_dict()["anchor"]["works_compared"] is None


def test_find_fragments_cap_leaves_the_rest_unchecked(
    client: OpenAlexClient, api: Callable[..., list[str]]
) -> None:
    calls = api()
    report = find_fragments(client, "A1", max_candidates=1)

    assert report is not None
    assert [c for c in calls if c.startswith("works:")] == ["works:A1:1", "works:A2:1"]
    counts = report.counts()
    assert (counts["likely-fragment"], counts["unchecked"], counts["different"]) == (
        1,
        2,
        0,
    )
    # With no works lists allowed, nothing is fetched and nothing is ruled out.
    calls.clear()
    report = find_fragments(client, "A1", max_candidates=0)
    assert report is not None
    assert calls == []
    assert report.counts()["unchecked"] == 3


def test_find_fragments_search_cap_reports_what_it_skipped(
    client: OpenAlexClient, api: Callable[..., list[str]]
) -> None:
    anchor = _profile("Tim Doe", "Timothy Doe", "Timmy Doe")
    calls = api(anchor=anchor, found=())
    report = find_fragments(client, "A1", max_searches=2)

    assert report is not None
    assert calls == ["/authors/A1", "search:tim doe", "search:timothy doe"]
    assert report.queries_skipped == 1
    assert report.candidates == []


def test_find_fragments_rerun_is_free_and_refresh_refetches(
    client: OpenAlexClient, api: Callable[..., list[str]]
) -> None:
    calls = api()
    first = find_fragments(client, "A1")
    calls.clear()
    again = find_fragments(client, "A1")

    assert first is not None and again is not None
    assert calls == []
    assert again.counts() == first.counts()
    # Answered from the cache: no request, so no credit reading.
    assert (again.network_calls, again.credits_before, again.credits_after) == (
        0,
        None,
        None,
    )
    find_fragments(client, "A1", refresh=True)
    # The profile stays cached; the search and the works lists are asked again.
    assert calls == [
        "search:jane doe",
        "works:A1:1",
        "works:A2:1",
        "works:A3:1",
        "works:A4:1",
    ]


def test_find_fragments_returns_none_for_a_missing_profile(
    client: OpenAlexClient, api: Callable[..., list[str]]
) -> None:
    calls = api()
    assert find_fragments(client, "A404") is None
    assert calls == ["/authors/A404"]


def test_find_fragments_pages_through_the_anchors_works(
    client: OpenAlexClient, monkeypatch, mock_response
) -> None:
    pages: list[str] = []

    def get(self: object, url: str, **kwargs: object) -> Any:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if parts.path.startswith("/authors/"):
            return mock_response(200, _record(ANCHOR_PROFILE))
        if parts.path == "/authors":
            return mock_response(200, {"results": [_record(FRAGMENT)]})
        author = query["filter"][0].rsplit(":", 1)[-1]
        number = int(query["page"][0])
        pages.append(f"{author}:{number}")
        if author != "A1":
            return mock_response(200, {"results": []})
        # 450 works: two full pages and a short one.
        count = 200 if number < 3 else 50
        works = [
            {"id": f"W{number}-{i}", "display_name": f"Paper {number} {i}"}
            for i in range(count)
        ]
        return mock_response(200, {"meta": {"count": 450}, "results": works})

    monkeypatch.setattr("requests.Session.get", get)
    report = find_fragments(client, "A1")

    assert report is not None
    assert pages == ["A1:1", "A1:2", "A1:3", "A2:1"]
    assert (report.anchor_works, report.anchor_works_total) == (450, 450)
    assert report.works_lists == 4
