"""Tests for citefinder.bib — parsing and bib-side query helpers."""

from citefinder.bib import (
    build_search_query,
    build_title_query,
    citation_from_entry,
    first_author_surname,
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
    # Real bib files use both `Author = ...` and `author = ...`. Field keys
    # are lowercased so downstream lookups don't have to care.
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


def test_strip_braces() -> None:
    assert strip_braces("{Hello World}") == "Hello World"
    assert strip_braces("  {a {b} c}  ") == "a b c"
    assert strip_braces("") == ""


def test_first_author_surname_simple() -> None:
    assert first_author_surname("Smith, Jane") == "Smith"


def test_first_author_surname_first_then_multiple() -> None:
    assert first_author_surname("Smith, Jane and Lee, Sue and Kim, Joe") == "Smith"


def test_first_author_surname_corporate() -> None:
    # Braces around the whole name keep it together — bibtexparser shouldn't
    # split a corporate author on whitespace or " and ".
    name = "{Association for Computing Machinery}"
    assert first_author_surname(name) == "Association for Computing Machinery"


def test_first_author_surname_von_particles() -> None:
    # `van de Rijt` has von=['van','de'], last=['Rijt']. Crossref stores the
    # combined form as `family`, so we join both — otherwise the author
    # check would compare "Rijt" against Crossref's "van de Rijt".
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


def test_build_title_query_strips_braces() -> None:
    # build_title_query only strips braces; OpenAlex-specific normalization
    # (apostrophe remap, reserved-char stripping) is applied later at the
    # client boundary in OpenAlexClient.search_title.
    [e] = parse_entries("@article{x, title = {The {LLM} Revolution}}")
    assert build_title_query(e) == "The LLM Revolution"


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
