"""Tests for citefinder.adapters — Crossref/OpenAlex JSON to Work."""

from citefinder.adapters import (
    crossref_to_work,
    openalex_doi,
    openalex_to_work,
)

# --- Crossref --------------------------------------------------------------


def test_crossref_to_work_typical() -> None:
    record = {
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
    record = {
        "title": ["Backstabber's Knife Collection"],
        "subtitle": ["A Practical Guide"],
        "issued": {"date-parts": [[2020]]},
    }
    work = crossref_to_work(record)
    assert work is not None
    assert work.title == "Backstabber's Knife Collection: A Practical Guide"


def test_crossref_to_work_year_falls_back_through_keys() -> None:
    # `published-print` missing → falls through to `published-online`.
    record = {
        "title": ["X"],
        "published-online": {"date-parts": [[2019]]},
    }
    work = crossref_to_work(record)
    assert work is not None and work.year == 2019


def test_crossref_to_work_returns_none_for_missing_record() -> None:
    assert crossref_to_work(None) is None


def test_crossref_to_work_reads_a_corporate_author_name() -> None:
    # Crossref gives organisations a `name`, not `family`; the bib side keeps
    # `{World Health Organization}` whole, so the author signal can confirm.
    record = {"title": ["X"], "author": [{"name": "World Health Organization"}]}
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
    record = {
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
    # Mirrors the bib-side `first_author_surname` behavior — must combine
    # von + last so equality checks match.
    record = {
        "display_name": "Paper",
        "authorships": [{"author": {"display_name": "Arnout van de Rijt"}}],
    }
    work = openalex_to_work(record)
    assert work is not None
    assert work.first_author_surname == "van de Rijt"


def test_openalex_to_work_falls_back_to_host_venue() -> None:
    # Older records expose the venue under `host_venue` instead.
    record = {
        "display_name": "X",
        "host_venue": {"display_name": "Old Venue"},
    }
    work = openalex_to_work(record)
    assert work is not None
    assert work.container_names == ["Old Venue"]


def test_openalex_to_work_dedupes_when_primary_and_host_match() -> None:
    record = {
        "display_name": "X",
        "primary_location": {"source": {"display_name": "Same Venue"}},
        "host_venue": {"display_name": "Same Venue"},
    }
    work = openalex_to_work(record)
    assert work is not None
    assert work.container_names == ["Same Venue"]


def test_openalex_to_work_returns_none_for_missing_record() -> None:
    assert openalex_to_work(None) is None
