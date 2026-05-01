"""Tests for citefinder.verify.verify_entry against a fake Source."""

from typing import Any

from citefinder.bib import parse_entries
from citefinder.signals import Status, Work
from citefinder.verify import Source, verify_entry


def _make_entry(text: str):
    return parse_entries(text)[0]


def _matching_work() -> Work:
    return Work(
        title="A Study of Things",
        year=2020,
        first_author_surname="Smith",
        container_names=["Journal of Things"],
    )


def _wrong_work() -> Work:
    return Work(
        title="A Completely Different Paper About Cats",
        year=1985,
        first_author_surname="Jones",
        container_names=["Some Other Venue"],
    )


def _fake_source(
    name: str = "crossref",
    *,
    doi_record: dict[str, Any] | None | type[KeyError] = None,
    work_for_doi: Work | None = None,
    search_items: list[dict[str, Any]] | None = None,
    work_for_search: Work | None = None,
) -> Source:
    """Build a Source whose methods return canned values."""

    class FakeClient:
        def lookup_doi(self, doi: str) -> dict[str, Any] | None:
            if doi_record is KeyError:
                raise RuntimeError("network exploded")
            return doi_record  # type: ignore[return-value]

    src = Source(name=name, client=FakeClient())  # type: ignore[arg-type]

    def _to_work(raw: dict[str, Any] | None) -> Work | None:
        # The DOI path passes the lookup result; the search path passes the
        # search-hit dict. Distinguish so each returns the right canned Work.
        if raw is None:
            return None
        if raw is doi_record:
            return work_for_doi
        return work_for_search

    src.to_work = _to_work  # type: ignore[assignment]
    src.search = lambda entry, rows=3: list(search_items or [])  # type: ignore[assignment]
    src.candidate_doi = lambda item: item.get("DOI", "")  # type: ignore[assignment]
    src.candidate_title = lambda item: (item.get("title") or [""])[0]  # type: ignore[assignment]
    return src


# --- DOI path ---------------------------------------------------------------


def test_doi_lookup_signals_match() -> None:
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020},
      journal = {Journal of Things},
      doi = {10.1/test}
    }"""
    entry = _make_entry(text)
    src = _fake_source(
        doi_record={"any": "shape"},
        work_for_doi=_matching_work(),
    )
    r = verify_entry(entry, src)
    assert r.status == Status.MATCHED
    assert r.method == "doi"
    assert r.matched_doi == "10.1/test"


def test_doi_lookup_signals_disagree_is_mismatch() -> None:
    # DOI resolves but the source record points at a totally different work.
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020},
      journal = {Journal of Things},
      doi = {10.1/wrong}
    }"""
    entry = _make_entry(text)
    src = _fake_source(
        doi_record={"any": "shape"},
        work_for_doi=_wrong_work(),
    )
    r = verify_entry(entry, src)
    assert r.status == Status.MISMATCH


def test_doi_404_returns_doi_not_found() -> None:
    text = "@article{x, title = {T}, year = {2020}, doi = {10.1/missing}}"
    entry = _make_entry(text)
    src = _fake_source(doi_record=None, work_for_doi=None)
    r = verify_entry(entry, src)
    assert r.status == Status.DOI_NOT_FOUND


def test_doi_lookup_exception_yields_error() -> None:
    text = "@article{x, title = {T}, doi = {10.1/x}}"
    entry = _make_entry(text)
    src = _fake_source(doi_record=KeyError)
    r = verify_entry(entry, src)
    assert r.status == Status.ERROR
    assert "DOI lookup failed" in r.note


# --- search path ------------------------------------------------------------


def test_search_finds_matching_hit() -> None:
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020},
      journal = {Journal of Things}
    }"""
    entry = _make_entry(text)
    src = _fake_source(
        search_items=[{"DOI": "10.1/found", "title": ["A Study of Things"]}],
        work_for_search=_matching_work(),
    )
    r = verify_entry(entry, src)
    assert r.status == Status.MATCHED
    assert r.method == "search"
    assert r.matched_doi == "10.1/found"


def test_search_no_plausible_hit_is_unmatched() -> None:
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020}
    }"""
    entry = _make_entry(text)
    # Top hit's title overlaps too little to clear the threshold.
    src = _fake_source(
        search_items=[{"DOI": "10.1/x", "title": ["Totally Unrelated Subject"]}],
    )
    r = verify_entry(entry, src)
    assert r.status == Status.UNMATCHED


def test_search_with_no_query_fields_is_error() -> None:
    text = "@article{x, year = {2020}}"
    entry = _make_entry(text)
    src = _fake_source(search_items=[])
    r = verify_entry(entry, src)
    assert r.status == Status.ERROR
    assert "no author/title/year" in r.note


# --- @online / @misc skip-source bucket ------------------------------------


def test_online_with_no_match_is_skip_source() -> None:
    text = """@online{x,
      author = {Org},
      title = {A blog post},
      year = {2020}
    }"""
    entry = _make_entry(text)
    src = _fake_source(search_items=[])
    r = verify_entry(entry, src)
    assert r.status == Status.SKIP_SOURCE


def test_online_with_disagreeing_match_routes_to_skip_source() -> None:
    # A high-similarity hit exists but signals disagree (year/author wrong).
    # For @online, that's almost certainly a derived artifact, so the
    # verifier should bury it under skip-source and drop the matched DOI.
    text = """@online{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020}
    }"""
    entry = _make_entry(text)
    src = _fake_source(
        search_items=[{"DOI": "10.1/fake", "title": ["A Study of Things"]}],
        work_for_search=_wrong_work(),
    )
    r = verify_entry(entry, src)
    assert r.status == Status.SKIP_SOURCE
    assert r.matched_doi is None
