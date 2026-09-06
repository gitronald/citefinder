"""Tests for citefinder.models — record shapes and the drift check."""

from __future__ import annotations

from typing import Any

from citefinder.adapters import crossref_to_work, openalex_to_work
from citefinder.models import (
    CacheRow,
    CrossrefEnvelope,
    CrossrefSearchMessage,
    CrossrefWork,
    OpenAlexSearchPage,
    OpenAlexWork,
    cache_drift,
    undeclared_keys,
)

# Synthetic records that touch every nested shape the model declares. They
# are the fixtures the drift check is measured against, so a key added here
# must also be added to the model (or the test that expects zero drift fails).

CROSSREF_WORK: CrossrefWork = {
    "DOI": "10.1000/xyz123",
    "URL": "https://doi.org/10.1000/xyz123",
    "prefix": "10.1000",
    "member": "1",
    "type": "journal-article",
    "source": "Crossref",
    "score": 1.0,
    "title": ["A Paper"],
    "subtitle": ["With a Subtitle"],
    "short-title": [],
    "original-title": [],
    "container-title": ["Journal of Things"],
    "short-container-title": ["JoT"],
    "publisher": "Example Press",
    "ISSN": ["1234-5678"],
    "issn-type": [{"type": "print", "value": "1234-5678"}],
    "author": [
        {
            "family": "Smith",
            "given": "Jane",
            "sequence": "first",
            "affiliation": [],
            "ORCID": "https://orcid.org/0000-0000-0000-0000",
            "authenticated-orcid": False,
        },
        {"name": "World Health Organization", "sequence": "additional"},
    ],
    "issued": {"date-parts": [[2020, 5, 1]]},
    "created": {
        "date-parts": [[2020, 4, 30]],
        "date-time": "2020-04-30T00:00:00Z",
        "timestamp": 1588204800000,
    },
    "indexed": {
        "date-parts": [[2024, 1, 1]],
        "date-time": "2024-01-01T00:00:00Z",
        "timestamp": 1704067200000,
        "version": "3.30.0",
    },
    "published": {"date-parts": [[2020, 5, 1]]},
    "published-print": {"date-parts": [[2020, 5]]},
    "published-online": {"date-parts": [[2020, 4, 15]]},
    "volume": "12",
    "issue": "3",
    "page": "100-120",
    "journal-issue": {"issue": "3", "published-print": {"date-parts": [[2020, 5]]}},
    "event": {"name": "Conf", "location": "Town", "start": {"date-parts": [[2020]]}},
    "abstract": "<jats:p>Text.</jats:p>",
    "language": "en",
    "subject": [],
    "license": [
        {
            "URL": "https://example.com/license",
            "content-version": "vor",
            "delay-in-days": 0,
            "start": {"date-parts": [[2020, 5, 1]]},
        }
    ],
    "reference": [
        {"key": "ref1", "DOI": "10.1000/abc", "doi-asserted-by": "publisher"},
        {"key": "ref2", "unstructured": "Doe, J. (1999). Old paper."},
    ],
    "reference-count": 2,
    "references-count": 2,
    "is-referenced-by-count": 5,
    "relation": {},
    "funder": [{"name": "Funder", "award": ["123"]}],
}

OPENALEX_WORK: OpenAlexWork = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1000/xyz123",
    "ids": {
        "openalex": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1000/xyz123",
    },
    "type": "article",
    "type_crossref": None,
    "display_name": "A Paper",
    "title": "A Paper",
    "primary_location": {
        "id": "doi:10.1000/xyz123",
        "source": {
            "id": "https://openalex.org/S1",
            "display_name": "Journal of Things",
            "issn_l": "1234-5678",
            "issn": ["1234-5678"],
            "type": "journal",
            "is_oa": False,
            "is_in_doaj": False,
            "is_core": True,
            "host_organization": "https://openalex.org/P1",
            "host_organization_name": "Example Press",
            "host_organization_lineage": ["https://openalex.org/P1"],
            "host_organization_lineage_names": ["Example Press"],
        },
        "landing_page_url": "https://doi.org/10.1000/xyz123",
        "pdf_url": None,
        "license": None,
        "license_id": None,
        "version": "publishedVersion",
        "raw_source_name": "Journal of Things",
        "raw_type": "journal-article",
        "is_oa": False,
        "is_accepted": True,
        "is_published": True,
    },
    "best_oa_location": None,
    "locations": [],
    "locations_count": 1,
    "publication_year": 2020,
    "publication_date": "2020-05-01",
    "biblio": {"volume": "12", "issue": "3", "first_page": "100", "last_page": "120"},
    "language": "en",
    "authorships": [
        {
            "author_position": "first",
            "author": {
                "id": "https://openalex.org/A1",
                "display_name": "Jane Smith",
                "orcid": None,
            },
            "raw_author_name": "Jane Smith",
            "raw_orcid": None,
            "institutions": [
                {
                    "id": "https://openalex.org/I1",
                    "display_name": "Example University",
                    "ror": None,
                    "country_code": "US",
                    "type": "education",
                    "lineage": ["https://openalex.org/I1"],
                }
            ],
            "affiliations": [],
            "raw_affiliation_strings": ["Example University"],
            "countries": ["US"],
            "is_corresponding": True,
        },
        {"author_position": "last", "raw_author_name": "Unlinked Author"},
    ],
    "abstract_inverted_index": {"Some": [0], "text.": [1]},
    "open_access": {
        "is_oa": False,
        "oa_status": "closed",
        "oa_url": None,
        "any_repository_has_fulltext": False,
    },
    "primary_topic": {
        "id": "https://openalex.org/T1",
        "display_name": "Topic",
        "score": 0.9,
        "subfield": {"id": "1", "display_name": "Subfield"},
        "field": {"id": "2", "display_name": "Field"},
        "domain": {"id": "3", "display_name": "Domain"},
    },
    "topics": [],
    "cited_by_count": 5,
    "counts_by_year": [{"year": 2021, "cited_by_count": 5}],
    "fwci": 1.2,
    "referenced_works": [],
    "referenced_works_count": 0,
    "related_works": [],
    "created_date": "2020-05-02",
    "updated_date": "2024-01-01T00:00:00",
}


def test_fixtures_have_no_drift() -> None:
    assert undeclared_keys(CROSSREF_WORK, CrossrefWork) == []
    assert undeclared_keys(OPENALEX_WORK, OpenAlexWork) == []


def test_undeclared_keys_reports_dotted_paths() -> None:
    record: dict[str, Any] = {
        "DOI": "10.1000/x",
        "brand-new": 1,
        "issued": {"date-parts": [[2020]], "season": "spring"},
        "author": [{"family": "A"}, {"family": "B", "nickname": "Bee"}],
        "relation": {"has-preprint": [{"id": "x", "id-type": "doi"}]},
    }
    assert undeclared_keys(record, CrossrefWork) == [
        "author[].nickname",
        "brand-new",
        "issued.season",
    ]


def test_undeclared_keys_walks_nullable_and_list_nesting() -> None:
    record: dict[str, Any] = {
        "best_oa_location": {"source": {"display_name": "S", "extra": 1}},
        "authorships": [{"author": {"display_name": "A", "extra": 2}}],
    }
    assert undeclared_keys(record, OpenAlexWork) == [
        "authorships[].author.extra",
        "best_oa_location.source.extra",
    ]


def test_undeclared_keys_ignores_non_dict_input() -> None:
    assert undeclared_keys(None, OpenAlexWork) == []
    assert undeclared_keys([1, 2], OpenAlexWork) == []


def test_envelope_and_search_page_shapes() -> None:
    envelope: CrossrefEnvelope = {
        "status": "ok",
        "message-type": "work",
        "message-version": "1.0.0",
        "message": CROSSREF_WORK,
    }
    assert undeclared_keys(envelope, CrossrefEnvelope) == []
    search: CrossrefSearchMessage = {
        "items": [CROSSREF_WORK],
        "items-per-page": 3,
        "total-results": 1,
        "query": {},
        "facets": {},
    }
    assert undeclared_keys(search, CrossrefSearchMessage) == []
    page: OpenAlexSearchPage = {
        "meta": {"count": 1, "page": 1, "per_page": 3, "db_response_time_ms": 10},
        "results": [OPENALEX_WORK],
        "group_by": [],
    }
    assert undeclared_keys(page, OpenAlexSearchPage) == []


def test_cache_row_shape() -> None:
    row: CacheRow = {
        "key": "https://api.crossref.org/works/10.1/x",
        "value": None,
        "ts": 0.0,
    }
    assert undeclared_keys(row, CacheRow) == []


def test_adapters_accept_typed_records() -> None:
    cr = crossref_to_work(CROSSREF_WORK)
    assert cr is not None
    assert cr.title == "A Paper: With a Subtitle"
    assert cr.year == 2020
    assert cr.first_author_surname == "Smith"
    oa = openalex_to_work(OPENALEX_WORK)
    assert oa is not None
    assert oa.title == "A Paper"
    assert oa.first_author_surname == "Smith"
    assert oa.container_names == ["Journal of Things"]


def test_cache_drift_routes_by_key_host_and_kind() -> None:
    rows: list[CacheRow] = [
        # A Crossref work in its envelope, carrying one unknown key.
        {
            "key": "https://api.crossref.org/works/10.1/a",
            "value": {"status": "ok", "message": {**CROSSREF_WORK, "novel": 1}},
            "ts": 0.0,
        },
        # A Crossref search page whose hit carries the same unknown key.
        {
            "key": "https://api.crossref.org/works?query.bibliographic=x&rows=3",
            "value": {"message": {"items": [{**CROSSREF_WORK, "novel": 1}]}},
            "ts": 0.0,
        },
        # An OpenAlex work misfiled under a Crossref-looking path is still
        # routed by its host.
        {
            "key": "https://api.openalex.org/works/doi:10.1/a",
            "value": {**OPENALEX_WORK, "fresh": True},
            "ts": 0.0,
        },
        {
            "key": "https://api.openalex.org/works?filter=title.search:x",
            "value": {"meta": {"count": 1}, "results": [OPENALEX_WORK]},
            "ts": 0.0,
        },
        # Cached 404s and other endpoints are skipped, not counted.
        {
            "key": "https://api.openalex.org/works/doi:10.1/gone",
            "value": None,
            "ts": 0.0,
        },
        {
            "key": "https://api.openalex.org/authors/A1",
            "value": {"id": "A1"},
            "ts": 0.0,
        },
    ]
    drift = cache_drift(rows)
    assert set(drift) == {
        "crossref-work",
        "crossref-search",
        "openalex-work",
        "openalex-search",
    }
    assert drift["crossref-work"] == (1, {"novel": 1})
    assert drift["crossref-search"] == (1, {"items[].novel": 1})
    assert drift["openalex-work"] == (1, {"fresh": 1})
    assert drift["openalex-search"] == (1, {})
