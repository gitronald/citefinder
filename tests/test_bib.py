"""Tests for citefinder.bib — parsing and bib-side query helpers."""

import logging

import pytest

from citefinder.bib import (
    build_search_query,
    build_title_query,
    citation_from_entry,
    first_author_surname,
    normalize_doi,
    parse_entries,
    strip_braces,
)


def test_parse_entries_basic() -> None:
    text = """@article{smith2020,
      author = {Smith, Jane},
      title = {A Study of Things},
      journal = {J. Things},
      year = {2020},
      doi = {10.1234/abc}
    }"""
    entries = parse_entries(text)
    assert len(entries) == 1
    e = entries[0]
    assert e.etype == "article"
    assert e.key == "smith2020"
    assert e.fields["author"] == "Smith, Jane"
    assert e.fields["doi"] == "10.1234/abc"


def test_parse_entries_field_keys_lowercased() -> None:
    # Field keys are lowercased.
    text = "@article{x, Author = {Smith, J.}, Title = {T}, Year = {2020}}"
    [e] = parse_entries(text)
    assert "author" in e.fields and "title" in e.fields and "year" in e.fields


def test_parse_entries_multiple() -> None:
    text = """
    @article{a, author = {A, X}, title = {T1}, year = {2020}}
    @inproceedings{b, author = {B, Y}, title = {T2}, year = {2021}}
    """
    entries = parse_entries(text)
    assert [e.key for e in entries] == ["a", "b"]
    assert [e.etype for e in entries] == ["article", "inproceedings"]


@pytest.mark.parametrize(
    ("text", "kept", "line", "reason"),
    [
        # A duplicated field key drops the whole entry.
        (
            "@article{x, author = {A}, author = {B}, title = {T}}",
            [],
            1,
            "Duplicate field",
        ),
        # A repeated citation key drops the second entry.
        (
            "@article{x, title = {T1}}\n@article{x, title = {T2}}",
            ["x"],
            2,
            "Duplicate entry",
        ),
        # An unterminated brace aborts that block; earlier ones survive.
        (
            "@article{ok, title = {T}}\n@article{x, title = {Open",
            ["ok"],
            2,
            "BlockAborted",
        ),
    ],
)
def test_parse_entries_warns_on_dropped_blocks(
    text: str,
    kept: list[str],
    line: int,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Skipped blocks are logged with the 1-based line an editor shows.
    with caplog.at_level(logging.WARNING, logger="citefinder"):
        entries = parse_entries(text)
    assert [e.key for e in entries] == kept
    assert f"skipped unparsable bib block at line {line}:" in caplog.text
    assert reason in caplog.text


def test_strip_braces() -> None:
    assert strip_braces("{Hello World}") == "Hello World"
    assert strip_braces("  {a {b} c}  ") == "a b c"
    assert strip_braces("") == ""


@pytest.mark.parametrize(
    ("raw", "bare"),
    [
        ("10.1234/abc", "10.1234/abc"),
        ("{https://doi.org/10.1234/abc}", "10.1234/abc"),
        ("http://dx.doi.org/10.1234/abc", "10.1234/abc"),
        ("HTTPS://DOI.ORG/10.1234/ABC", "10.1234/ABC"),
        ("doi:10.1234/abc", "10.1234/abc"),
        ("DOI: 10.1234/abc ", "10.1234/abc"),
        ("{}", ""),
    ],
)
def test_normalize_doi(raw: str, bare: str) -> None:
    assert normalize_doi(raw) == bare


def test_first_author_surname_simple() -> None:
    assert first_author_surname("Smith, Jane") == "Smith"


def test_first_author_surname_first_then_multiple() -> None:
    assert first_author_surname("Smith, Jane and Lee, Sue and Kim, Joe") == "Smith"


def test_first_author_surname_corporate() -> None:
    # Braces keep a corporate author whole.
    name = "{Association for Computing Machinery}"
    assert first_author_surname(name) == "Association for Computing Machinery"


def test_first_author_surname_von_particles() -> None:
    # The von part is joined, matching Crossref's combined `family`.
    assert first_author_surname("van de Rijt, Arnout") == "van de Rijt"


def test_first_author_surname_empty() -> None:
    assert first_author_surname("") == ""


def test_build_search_query_full() -> None:
    text = """@article{x,
      author = {Smith, Jane and Lee, Sue},
      title = {A Study of Things},
      year = {2020}
    }"""
    [e] = parse_entries(text)
    assert build_search_query(e) == "Smith A Study of Things 2020"


def test_build_search_query_omits_missing_fields() -> None:
    [e] = parse_entries("@misc{x, title = {Just a Title}}")
    assert build_search_query(e) == "Just a Title"


def test_build_title_query_strips_filter_chars_and_remaps_apostrophe() -> None:
    [e] = parse_entries("@article{x, title = {Backstabber's, Knife: Collection!}}")
    # `,` `:` `!` are stripped (reserved in OpenAlex filters); `'` becomes U+2019.
    assert build_title_query(e) == "Backstabber’s Knife Collection"


def test_citation_from_entry_pulls_journal_then_booktitle() -> None:
    text = """@article{x,
      author = {Smith, Jane},
      title = {T},
      year = {2020},
      journal = {J}
    }"""
    [e] = parse_entries(text)
    cit = citation_from_entry(e)
    assert cit.title == "T"
    assert cit.year == "2020"
    assert cit.first_author_surname == "Smith"
    assert cit.container == "J"


def test_citation_from_entry_falls_back_to_booktitle() -> None:
    text = "@inproceedings{x, title = {T}, booktitle = {Proc FOO}}"
    [e] = parse_entries(text)
    assert citation_from_entry(e).container == "Proc FOO"


def test_citation_from_entry_missing_fields_become_none() -> None:
    [e] = parse_entries("@misc{x, title = {T}}")
    cit = citation_from_entry(e)
    assert cit.year is None
    assert cit.first_author_surname is None
    assert cit.container is None
