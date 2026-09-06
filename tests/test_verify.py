"""Tests for citefinder.verify.verify_entry against a fake Source."""

from typing import Any

from citefinder.bib import parse_entries
from citefinder.client import CrossrefClient
from citefinder.models import CrossrefWork, OpenAlexWork
from citefinder.openalex import OpenAlexClient
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


def test_doi_single_non_title_disagreement_stays_matched() -> None:
    # The bib's own DOI resolved, three signals confirm, one disagrees: the
    # DOI is the identity claim, so the entry is matched and the
    # disagreement rides along in the note for review.
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020},
      journal = {Journal of Things},
      doi = {10.1/test}
    }"""
    entry = _make_entry(text)
    work = _matching_work()
    work.year = 2017
    src = _fake_source(doi_record={"any": "shape"}, work_for_doi=work)
    r = verify_entry(entry, src)
    assert r.status == Status.MATCHED
    assert r.note.startswith("DOI resolved; source disagrees on: year")
    assert r.signals["year"]["verdict"] == "fail"


def test_doi_title_disagreement_stays_probable() -> None:
    # Guard: a typoed DOI can land on a *related* work — same author, year,
    # and venue, different paper — which fails only on title. The override
    # must not swallow that.
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020},
      journal = {Journal of Things},
      doi = {10.1/sibling}
    }"""
    entry = _make_entry(text)
    work = _matching_work()
    work.title = "Measuring Other Stuff Entirely"
    src = _fake_source(doi_record={"any": "shape"}, work_for_doi=work)
    r = verify_entry(entry, src)
    assert r.status == Status.PROBABLE
    assert r.signals["title"]["verdict"] == "fail"


def test_doi_single_disagreement_without_confirmation_stays_probable() -> None:
    # Guard: the override needs two confirming signals. A bib with only a
    # title and year whose DOI resolves to an ambiguous title and a year
    # thirty years off has nothing confirming identity.
    text = """@article{x,
      title = {Selective Exposure Effects Online},
      year = {2020},
      doi = {10.1/sparse}
    }"""
    entry = _make_entry(text)
    work = Work(
        title="Selective Exposure Effects",
        year=1990,
        first_author_surname=None,
        container_names=[],
    )
    src = _fake_source(doi_record={"any": "shape"}, work_for_doi=work)
    r = verify_entry(entry, src)
    assert r.signals["title"]["verdict"] == "unknown"
    assert r.signals["year"]["verdict"] == "fail"
    assert r.status == Status.PROBABLE
    assert r.note.startswith("Source disagrees on: year")


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


def test_bib_doi_in_url_form_is_normalized_before_lookup() -> None:
    # Exported bibs often carry `https://doi.org/...`; the sources 404 on it.
    seen: list[str] = []

    class Client:
        def lookup_doi(self, doi: str) -> dict[str, Any]:
            seen.append(doi)
            return {"any": "shape"}

    src = Source(name="crossref", client=Client())  # type: ignore[arg-type]
    src.to_work = lambda raw: _matching_work()  # type: ignore[assignment]
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020},
      journal = {Journal of Things},
      doi = {https://doi.org/10.1/test}
    }"""
    r = verify_entry(_make_entry(text), src)
    assert seen == ["10.1/test"]
    assert r.bib_doi == "10.1/test"
    assert r.matched_doi == "10.1/test"


def test_empty_doi_field_takes_the_search_path() -> None:
    text = "@article{x, title = {A Study of Things}, doi = {}}"
    r = verify_entry(_make_entry(text), _fake_source(search_items=[]))
    assert r.bib_doi is None
    assert r.method == "search"


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


def test_malformed_author_field_yields_error_not_exception() -> None:
    # bibtexparser's name parser raises on a trailing comma. One bad entry
    # must land in `error` with a reason, not abort the whole run.
    text = """@article{x,
      author = {Smith, Jane,},
      title = {A Study of Things},
      year = {2020},
      doi = {10.1/test}
    }"""
    entry = _make_entry(text)
    src = _fake_source(doi_record={"any": "shape"}, work_for_doi=_matching_work())
    r = verify_entry(entry, src)
    assert r.status == Status.ERROR
    assert r.method == "doi"  # the path it would have taken, never ""
    assert r.note.startswith("could not parse bib fields:")
    assert "Trailing comma" in r.note


# --- OpenAlex quirks: real records through the real adapter -----------------
# Trimmed to the fields `openalex_to_work` reads, values as returned by the
# OpenAlex API on 2026-09-02. Each is a bib entry whose own DOI resolved to
# the right work and that `verify` nevertheless reported as `probable`.


def _openalex_source(record: dict[str, Any]) -> Source:
    class FakeClient:
        def lookup_doi(self, doi: str) -> dict[str, Any]:
            return record

    return Source(name="openalex", client=FakeClient())  # type: ignore[arg-type]


FANG2022_OPENALEX = {
    "doi": "https://doi.org/10.1145/3510003.3510121",
    # Both title fields stop at the colon; there is no subtitle field.
    "display_name": '"This is damn slick!"',
    "title": '"This is damn slick!"',
    "publication_year": 2022,
    "authorships": [{"author": {"display_name": "Hongbo Fang"}}],
    "primary_location": {
        "source": {
            "display_name": (
                "Proceedings of the 44th International Conference on "
                "Software Engineering"
            )
        }
    },
}

OHM2020_OPENALEX = {
    "doi": "https://doi.org/10.1007/978-3-030-52683-2_2",
    "display_name": (
        "Backstabber\u2019s Knife Collection: A Review of Open Source Software "
        "Supply Chain Attacks"
    ),
    "title": (
        "Backstabber\u2019s Knife Collection: A Review of Open Source Software "
        "Supply Chain Attacks"
    ),
    "publication_year": 2020,
    "authorships": [{"author": {"display_name": "Marc Ohm"}}],
    # The LNCS series name; none of the record's locations carries the
    # booktitle (DIMVA 2020).
    "primary_location": {
        "source": {"display_name": "Lecture notes in computer science"}
    },
}

MESSING2014_OPENALEX = {
    "doi": "https://doi.org/10.1177/0093650212466406",
    "display_name": "Selective Exposure in the Age of Social Media",
    "title": "Selective Exposure in the Age of Social Media",
    # Online-first date; `biblio` reports volume 41 issue 8, the 2014 print
    # volume, but there is no second year field to fall back to.
    "publication_year": 2012,
    "publication_date": "2012-12-31",
    "authorships": [{"author": {"display_name": "Solomon Messing"}}],
    "primary_location": {"source": {"display_name": "Communication Research"}},
}


def test_openalex_truncated_title_with_doi_is_matched() -> None:
    text = """@inproceedings{Fang2022,
      author = {Fang, Hongbo and Lamba, Hemank and Herbsleb, James and Vasilescu, Bogdan},
      title = {{"This Is Damn Slick!"}: Estimating the Impact of Tweets on Open Source Project Popularity and New Contributors},
      booktitle = {Proceedings of the 44th International Conference on Software Engineering},
      year = {2022},
      doi = {10.1145/3510003.3510121}
    }"""  # noqa: E501 -- real bib lines, kept verbatim
    r = verify_entry(_make_entry(text), _openalex_source(FANG2022_OPENALEX))
    assert r.method == "doi"
    assert r.signals["title"]["verdict"] == "unknown"
    assert "truncation" in r.signals["title"]["note"]
    assert r.status == Status.MATCHED
    assert r.note == ""


def test_openalex_series_name_container_with_doi_is_matched() -> None:
    text = """@inproceedings{Ohm2020,
      author = {Ohm, Marc and Plate, Henrik and Sykosch, Arnold and Meier, Michael},
      title = {Backstabber's Knife Collection: A Review of Open Source Software Supply Chain Attacks},
      booktitle = {Detection of Intrusions and Malware, and Vulnerability Assessment},
      year = {2020},
      doi = {10.1007/978-3-030-52683-2_2}
    }"""  # noqa: E501 -- real bib lines, kept verbatim
    r = verify_entry(_make_entry(text), _openalex_source(OHM2020_OPENALEX))
    assert r.signals["container"]["verdict"] == "fail"
    assert r.status == Status.MATCHED
    assert "DOI resolved; source disagrees on: container" in r.note


def test_openalex_preprint_year_with_doi_is_matched_with_note() -> None:
    text = """@article{messing2014selective,
      author = {Messing, Solomon and Westwood, Sean J.},
      title = {Selective Exposure in the Age of Social Media},
      journal = {Communication Research},
      year = {2014},
      volume = {41},
      number = {8},
      doi = {10.1177/0093650212466406}
    }"""
    r = verify_entry(_make_entry(text), _openalex_source(MESSING2014_OPENALEX))
    assert r.signals["year"] == {
        "verdict": "fail",
        "bib": "2014",
        "crossref": "2012",
        "diff": 2,
    }
    assert r.status == Status.MATCHED
    assert (
        r.note
        == "DOI resolved; source disagrees on: year (bib '2014' vs source '2012')"
    )


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


def test_search_matches_a_cjk_title_through_the_short_title_gate() -> None:
    # A CJK title is one whitespace token; the gate read it as a one-word
    # title and refused to select the exact hit. Bigram tokens clear it.
    text = """@article{x,
      author = {Wang, Wei},
      title = {深度学习综述},
      year = {2020},
      journal = {Journal of Things}
    }"""
    src = _fake_source(
        search_items=[{"DOI": "10.1/cjk", "title": ["深度学习综述"]}],
        work_for_search=Work(
            title="深度学习综述",
            year=2020,
            first_author_surname="Wang",
            container_names=["Journal of Things"],
        ),
    )
    r = verify_entry(_make_entry(text), src)
    assert r.status == Status.MATCHED
    assert r.matched_doi == "10.1/cjk"


def test_search_match_without_a_doi_reports_none() -> None:
    # The DOI path uses None for "no DOI"; the search path used to leave "".
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      year = {2020},
      journal = {Journal of Things}
    }"""
    src = _fake_source(
        search_items=[{"title": ["A Study of Things"]}],
        work_for_search=_matching_work(),
    )
    r = verify_entry(_make_entry(text), src)
    assert r.status == Status.MATCHED
    assert r.matched_doi is None


def test_search_short_title_cannot_select_a_candidate() -> None:
    # cialdini2003influence: the one-word title "Influence" scores 1.0
    # against an unrelated 1985 paper titled "Influence". Title similarity
    # cannot pick a candidate here; leave the hit in `candidates` and report
    # why.
    text = """@book{cialdini2003influence,
      author = {Cialdini, Robert B.},
      title = {Influence},
      year = {2003},
      publisher = {Allyn and Bacon}
    }"""
    entry = _make_entry(text)
    src = _fake_source(
        search_items=[{"DOI": "10.1/unrelated", "title": ["Influence"]}],
        work_for_search=_wrong_work(),
    )
    r = verify_entry(entry, src)
    assert r.status == Status.UNMATCHED
    assert r.matched_doi is None
    assert r.note == (
        "title too short to match by search (1 word(s), need 3); review candidates"
    )
    assert r.candidates[0]["similarity"] == "1.00"


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


# --- search path through the real Source dispatch ---------------------------
# `_fake_source` replaces `Source`'s methods; these two keep them and swap only
# the HTTP client, so the crossref/openalex branches themselves are exercised.


class _FakeCrossref(CrossrefClient):
    def __init__(self, items: list[CrossrefWork]) -> None:  # no session, no cache
        self.items = items

    # `typing.override` is 3.12+ and the package supports 3.11.
    def search_bibliographic(  # pyrefly: ignore[missing-override-decorator]
        self, query: str, rows: int = 3
    ) -> list[CrossrefWork]:
        return self.items


class _FakeOpenAlex(OpenAlexClient):
    def __init__(self, items: list[OpenAlexWork]) -> None:
        self.items = items

    def search_title(  # pyrefly: ignore[missing-override-decorator]
        self, title: str, rows: int = 3
    ) -> list[OpenAlexWork]:
        return self.items


def test_crossref_search_candidate_title_includes_the_subtitle() -> None:
    # Crossref splits `title` and `subtitle`. The DOI path already rejoins
    # them; candidate scoring must see the same full title, or a split record
    # scores about 0.24 against its own bib entry and goes unmatched.
    text = """@inproceedings{x,
      author = {Ohm, Marc},
      title = {Backstabber's Knife Collection: A Review of Open Source Software Supply Chain Attacks},
      booktitle = {Detection of Intrusions and Malware, and Vulnerability Assessment},
      year = {2020}
    }"""  # noqa: E501
    hit: CrossrefWork = {
        "DOI": "10.1/split",
        "title": ["Backstabber's Knife Collection"],
        "subtitle": ["A Review of Open Source Software Supply Chain Attacks"],
        "author": [{"family": "Ohm", "given": "Marc"}],
        "issued": {"date-parts": [[2020]]},
        "container-title": [
            "Detection of Intrusions and Malware, and Vulnerability Assessment"
        ],
    }
    src = Source(name="crossref", client=_FakeCrossref([hit]))
    r = verify_entry(_make_entry(text), src)
    assert r.status == Status.MATCHED
    assert r.matched_doi == "10.1/split"
    assert r.candidates[0]["title"].startswith("Backstabber's Knife Collection: A")


def test_openalex_search_path_through_the_real_source() -> None:
    text = """@article{x,
      author = {Smith, Jane},
      title = {A Study of Things},
      journal = {Journal of Things},
      year = {2020}
    }"""
    hit: OpenAlexWork = {
        "doi": "https://doi.org/10.1/oa",
        "display_name": "A Study of Things",
        "publication_year": 2020,
        "authorships": [{"author": {"display_name": "Jane Smith"}}],
        "primary_location": {"source": {"display_name": "Journal of Things"}},
    }
    src = Source(name="openalex", client=_FakeOpenAlex([hit]))
    r = verify_entry(_make_entry(text), src)
    assert r.status == Status.MATCHED
    assert r.matched_doi == "10.1/oa"  # the URL prefix is stripped


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


def test_online_unconfirmed_match_says_signals_do_not_confirm() -> None:
    # Nothing disagreed here; too few fields could be checked. The note must
    # not claim a disagreement it then quotes as "rest unknown".
    text = "@online{x, title = {A Study of Things}}"
    src = _fake_source(
        search_items=[{"DOI": "10.1/x", "title": ["A Study of Things"]}],
        work_for_search=Work(title="A Study of Things"),
    )
    r = verify_entry(_make_entry(text), src)
    assert r.status == Status.SKIP_SOURCE
    assert r.note == (
        "@online: signals do not confirm (only 1 signal(s) confirm; rest unknown); "
        "verify via URL"
    )


def test_online_short_title_hit_is_skip_source_with_url_note() -> None:
    # A short title blocks candidate selection for @online / @misc too, and
    # the note keeps the skip-source framing: the canonical source is the URL.
    text = """@misc{x,
      author = {Org},
      title = {Data},
      year = {2020}
    }"""
    entry = _make_entry(text)
    src = _fake_source(
        search_items=[{"DOI": "10.1/unrelated", "title": ["Data"]}],
        work_for_search=_wrong_work(),
    )
    r = verify_entry(entry, src)
    assert r.status == Status.SKIP_SOURCE
    assert r.matched_doi is None
    assert r.note == (
        "@misc: title too short to match by search (1 word(s), need 3); verify via URL"
    )
    assert r.candidates[0]["similarity"] == "1.00"
