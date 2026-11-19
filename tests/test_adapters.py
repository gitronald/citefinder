"""Tests for citefinder.adapters — Crossref/OpenAlex JSON to Work and AuthorProfile."""

import pytest

from citefinder.adapters import (
    AuthorProfile,
    crossref_to_work,
    openalex_doi,
    openalex_to_author,
    openalex_to_work,
)
from citefinder.models import CrossrefWork, OpenAlexAuthorProfile, OpenAlexWork

# --- Crossref --------------------------------------------------------------


def test_crossref_to_work_typical() -> None:
    record: CrossrefWork = {
        "title": ["A Paper"],
        "author": [{"family": "Smith", "given": "Jane"}],
        "issued": {"date-parts": [[2020]]},
        "container-title": ["Journal of Things"],
        "short-container-title": ["JoT"],
    }
    work = crossref_to_work(record)
    assert work is not None
    assert work.title == "A Paper"
    assert work.year == 2020
    assert work.first_author_surname == "Smith"
    # Both full and short container names get passed through.
    assert work.container_names == ["Journal of Things", "JoT"]


def test_crossref_to_work_concatenates_subtitle() -> None:
    record: CrossrefWork = {
        "title": ["Backstabber's Knife Collection"],
        "subtitle": ["A Practical Guide"],
        "issued": {"date-parts": [[2020]]},
    }
    work = crossref_to_work(record)
    assert work is not None
    assert work.title == "Backstabber's Knife Collection: A Practical Guide"


def test_crossref_to_work_year_falls_back_through_keys() -> None:
    # `published-print` missing → falls through to `published-online`.
    record: CrossrefWork = {
        "title": ["X"],
        "published-online": {"date-parts": [[2019]]},
    }
    work = crossref_to_work(record)
    assert work is not None and work.year == 2019


@pytest.mark.parametrize("date_parts", [[[None]], [], [[]]])
def test_crossref_to_work_tolerates_empty_date_parts(date_parts: list) -> None:
    work = crossref_to_work({"title": ["X"], "issued": {"date-parts": date_parts}})
    assert work is not None
    assert work.year is None


def test_crossref_to_work_returns_none_for_missing_record() -> None:
    assert crossref_to_work(None) is None


def test_crossref_to_work_reads_a_corporate_author_name() -> None:
    # Organisations carry `name`, not `family`.
    record: CrossrefWork = {
        "title": ["X"],
        "author": [{"name": "World Health Organization"}],
    }
    work = crossref_to_work(record)
    assert work is not None
    assert work.first_author_surname == "World Health Organization"


def test_crossref_to_work_handles_missing_author() -> None:
    work = crossref_to_work({"title": ["X"], "issued": {"date-parts": [[2020]]}})
    assert work is not None
    assert work.first_author_surname is None


# --- OpenAlex --------------------------------------------------------------


def test_openalex_doi_strips_url_prefix() -> None:
    assert openalex_doi("https://doi.org/10.1234/abc") == "10.1234/abc"
    assert openalex_doi("https://dx.doi.org/10.1234/abc") == "10.1234/abc"
    assert openalex_doi(None) == ""


def test_openalex_to_work_typical() -> None:
    record: OpenAlexWork = {
        "display_name": "A Paper",
        "publication_year": 2020,
        "authorships": [
            {"author": {"display_name": "Jane Smith"}},
        ],
        "primary_location": {"source": {"display_name": "Journal of Things"}},
    }
    work = openalex_to_work(record)
    assert work is not None
    assert work.title == "A Paper"
    assert work.year == 2020
    assert work.first_author_surname == "Smith"
    assert work.container_names == ["Journal of Things"]


def test_openalex_to_work_keeps_von_particles_with_surname() -> None:
    # Combines von + last, like the bib-side `first_author_surname`.
    record: OpenAlexWork = {
        "display_name": "Paper",
        "authorships": [{"author": {"display_name": "Arnout van de Rijt"}}],
    }
    work = openalex_to_work(record)
    assert work is not None
    assert work.first_author_surname == "van de Rijt"


def test_openalex_to_work_falls_back_to_host_venue() -> None:
    # Falls back to `host_venue`.
    record: OpenAlexWork = {
        "display_name": "X",
        "host_venue": {"display_name": "Old Venue"},
    }
    work = openalex_to_work(record)
    assert work is not None
    assert work.container_names == ["Old Venue"]


def test_openalex_to_work_dedupes_when_primary_and_host_match() -> None:
    record: OpenAlexWork = {
        "display_name": "X",
        "primary_location": {"source": {"display_name": "Same Venue"}},
        "host_venue": {"display_name": "Same Venue"},
    }
    work = openalex_to_work(record)
    assert work is not None
    assert work.container_names == ["Same Venue"]


def test_openalex_to_work_returns_none_for_missing_record() -> None:
    assert openalex_to_work(None) is None


# --- OpenAlex author profiles ----------------------------------------------


def test_openalex_to_author_projects_the_name_check_fields() -> None:
    record: OpenAlexAuthorProfile = {
        "id": "https://openalex.org/A1",
        "orcid": "https://orcid.org/0000-0002-1825-0097",
        "display_name": "Jané Smith",
        "display_name_alternatives": ["J. Smith", "Jane Smith", "Jané Smith"],
        "works_count": 12,
        "cited_by_count": 340,
        "last_known_institutions": [
            {"id": "https://openalex.org/I1", "display_name": "Example University"},
            {"id": "https://openalex.org/I2"},
            {"id": "https://openalex.org/I3", "display_name": "Example Institute"},
        ],
    }
    assert openalex_to_author(record) == AuthorProfile(
        id="https://openalex.org/A1",
        display_name="Jané Smith",
        display_name_alternatives=["J. Smith", "Jane Smith", "Jané Smith"],
        orcid="https://orcid.org/0000-0002-1825-0097",
        works_count=12,
        last_known_institutions=["Example University", "Example Institute"],
    )


def test_openalex_to_author_tolerates_a_sparse_record() -> None:
    # `orcid` and `last_known_institutions` are null on some profiles.
    record: OpenAlexAuthorProfile = {
        "id": "https://openalex.org/A1",
        "display_name": "J. Smith",
        "orcid": None,
        "last_known_institutions": None,
    }
    assert openalex_to_author(record) == AuthorProfile(
        id="https://openalex.org/A1", display_name="J. Smith"
    )


def test_openalex_to_author_returns_none_for_missing_record() -> None:
    assert openalex_to_author(None) is None
