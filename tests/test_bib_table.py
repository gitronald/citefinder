"""Tests for citefinder.bib_table — wide-table view of a `.bib` file."""

import polars as pl
import pytest

from citefinder.bib import parse_entries
from citefinder.bib_table import bib_to_table, table_to_bib


def test_bib_to_table_basic() -> None:
    text = """@article{smith2020,
      author = {Smith, Jane},
      title = {A Study of Things},
      journal = {J. Things},
      year = {2020},
      doi = {10.1234/abc}
    }"""
    df = bib_to_table(text)
    assert df.shape == (1, 7)
    assert df.columns[:2] == ["key", "entry_type"]
    # Remaining columns are sorted alphabetically.
    assert df.columns[2:] == ["author", "doi", "journal", "title", "year"]
    row = df.row(0, named=True)
    assert row["key"] == "smith2020"
    assert row["entry_type"] == "article"
    assert row["doi"] == "10.1234/abc"


def test_bib_to_table_uniform_schema_across_entries() -> None:
    # Different entries have different fields — the resulting frame should
    # have a uniform schema, with nulls filling missing fields.
    text = """
    @article{a, author = {A, X}, title = {T1}, year = {2020}}
    @book{b, author = {B, Y}, title = {T2}, publisher = {P}}
    """
    df = bib_to_table(text)
    assert df.shape == (2, 6)
    assert set(df.columns) == {
        "key",
        "entry_type",
        "author",
        "title",
        "year",
        "publisher",
    }
    rows = {r["key"]: r for r in df.iter_rows(named=True)}
    assert rows["a"]["publisher"] is None
    assert rows["b"]["year"] is None
    assert rows["b"]["publisher"] == "P"


def test_bib_to_table_preserves_literal_type_field_alongside_entry_type() -> None:
    # SSRN papers set `type = {SSRN Scholarly Paper}` as a real field.
    # The entry kind goes in `entry_type`, so the literal `type` field
    # survives in its own column without collision.
    text = """@misc{ssrn1,
      author = {Author, A.},
      title = {A Paper},
      type = {SSRN Scholarly Paper},
      year = {2024}
    }"""
    df = bib_to_table(text)
    row = df.row(0, named=True)
    assert row["entry_type"] == "misc"
    assert row["type"] == "SSRN Scholarly Paper"


def test_bib_to_table_refuses_fields_named_like_the_id_columns() -> None:
    # BibTeX has a real `key` sort field; silently replacing it with the
    # citation key would lose it on round trip.
    text = "@misc{x, key = {sort-me}, title = {T}}"
    with pytest.raises(ValueError, match=r"\['key'\] collide"):
        bib_to_table(text)


def test_bib_to_table_empty_bib() -> None:
    df = bib_to_table("")
    assert df.shape == (0, 2)
    assert df.columns == ["key", "entry_type"]
    assert df.schema["key"] == pl.Utf8


def test_bib_to_table_lowercases_field_keys() -> None:
    # citefinder's parser lowercases field keys, so `DOI` shows up as `doi`.
    text = (
        "@article{x, Author = {Smith, J.}, Title = {T}, DOI = {10.1/x}, Year = {2020}}"
    )
    df = bib_to_table(text)
    assert "doi" in df.columns
    assert "DOI" not in df.columns
    assert df.row(0, named=True)["doi"] == "10.1/x"


# --- table_to_bib --------------------------------------------------------


def test_table_to_bib_basic() -> None:
    df = pl.DataFrame(
        {
            "key": ["smith2020"],
            "entry_type": ["article"],
            "author": ["Smith, Jane"],
            "title": ["A Study"],
            "year": ["2020"],
        }
    )
    text = table_to_bib(df)
    assert text.startswith("@article{smith2020,\n")
    assert "  author = {Smith, Jane}," in text
    assert "  title = {A Study}," in text
    assert "  year = {2020}," in text
    assert text.endswith("}\n")


def test_table_to_bib_skips_null_fields() -> None:
    df = pl.DataFrame(
        {
            "key": ["a", "b"],
            "entry_type": ["article", "book"],
            "author": ["A, X", "B, Y"],
            "publisher": [None, "P"],
            "year": ["2020", None],
        }
    )
    text = table_to_bib(df)
    [a, b] = parse_entries(text)
    assert a.fields == {"author": "A, X", "year": "2020"}
    assert b.fields == {"author": "B, Y", "publisher": "P"}


def test_table_to_bib_handles_entry_with_no_fields() -> None:
    # An entry with only key/entry_type (everything else null) should
    # still serialize to valid bibtex, not an empty body with a stray comma.
    df = pl.DataFrame({"key": ["bare"], "entry_type": ["misc"], "title": [None]})
    text = table_to_bib(df)
    assert text == "@misc{bare,\n}\n"
    [e] = parse_entries(text)
    assert e.key == "bare"
    assert e.etype == "misc"
    assert e.fields == {}


def test_table_to_bib_round_trip_preserves_data() -> None:
    # Source -> bib_to_table -> table_to_bib -> parse should yield the
    # same entry set: same keys, same entry_types, same field values.
    # Field order within an entry is not preserved (bib_to_table sorts
    # alphabetically), but that's a documented limitation, not data loss.
    src_text = """
    @article{a,
      author = {A, X},
      title = {T1 with {Braces}},
      journal = {J},
      year = {2020},
      doi = {10.1/a}
    }
    @book{b,
      author = {B, Y},
      title = {T2},
      publisher = {P},
      year = {2021}
    }
    """
    src_entries = {e.key: e for e in parse_entries(src_text)}
    regen_entries = {
        e.key: e for e in parse_entries(table_to_bib(bib_to_table(src_text)))
    }
    assert set(src_entries) == set(regen_entries)
    for key in src_entries:
        assert src_entries[key].etype == regen_entries[key].etype
        assert src_entries[key].fields == regen_entries[key].fields


def test_table_to_bib_empty_dataframe() -> None:
    df = pl.DataFrame(schema={"key": pl.Utf8, "entry_type": pl.Utf8})
    assert table_to_bib(df) == ""


def test_table_to_bib_requires_key_and_entry_type() -> None:
    df = pl.DataFrame({"key": ["a"], "title": ["T"]})
    with pytest.raises(ValueError, match="entry_type"):
        table_to_bib(df)

    df = pl.DataFrame({"entry_type": ["article"], "title": ["T"]})
    with pytest.raises(ValueError, match="key"):
        table_to_bib(df)


def test_table_to_bib_preserves_unicode_and_nested_braces() -> None:
    # Non-ASCII characters and nested braces (BibTeX's title-case
    # protection) must survive a round trip verbatim.
    df = pl.DataFrame(
        {
            "key": ["alcaniz"],
            "entry_type": ["incollection"],
            "editor": ["Alcañiz, Mariano"],
            "title": ["Future {Directions} for {XR}"],
        }
    )
    [e] = parse_entries(table_to_bib(df))
    assert e.fields["editor"] == "Alcañiz, Mariano"
    assert e.fields["title"] == "Future {Directions} for {XR}"
