"""Tabulate a `.bib` file into a wide DataFrame for visual inspection.

A flat view for a first look at a bib. Field keys are lowercased on read.
"""

from __future__ import annotations

import polars as pl

from citefinder.bib import parse_entries


def bib_to_table(text: str) -> pl.DataFrame:
    """Parse a bib string into a wide DataFrame.

    One row per entry, one column per field; missing fields are null.
    Columns are `key`, `entry_type`, then the rest alphabetically.
    `entry_type` is not named `type` to avoid colliding with a bib `type` field.
    """
    entries = parse_entries(text)

    # A bib field named `key` or `entry_type` would be lost; refuse instead.
    fields = {f for e in entries for f in e.fields}
    reserved = sorted(fields & {"key", "entry_type"})
    if reserved:
        raise ValueError(
            f"bib field(s) {reserved} collide with the table's key/entry_type columns"
        )

    if not entries:
        return pl.DataFrame(schema={"key": pl.Utf8, "entry_type": pl.Utf8})

    rows = [{"key": e.key, "entry_type": e.etype, **e.fields} for e in entries]
    # Scan every row for columns so a late-appearing field is not dropped.
    df = pl.DataFrame(rows, infer_schema_length=None)
    other = sorted(c for c in df.columns if c not in ("key", "entry_type"))
    return df.select(["key", "entry_type", *other])


def _braces_balanced(s: str) -> bool:
    """Whether every `}` in `s` closes an earlier `{` and none stay open."""
    depth = 0
    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def table_to_bib(df: pl.DataFrame, indent: str = "  ") -> str:
    """Serialize a `bib_to_table`-shaped DataFrame back to a `.bib` string.

    Requires `key` and `entry_type` columns; other columns become fields,
    in column order. Null cells are skipped.
    """
    missing = {"key", "entry_type"} - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

    field_cols = [c for c in df.columns if c not in ("key", "entry_type")]
    chunks: list[str] = []
    for row in df.iter_rows(named=True):
        fields = [(c, row[c]) for c in field_cols if row[c] is not None]
        for c, v in fields:
            # Values are emitted verbatim in `{...}`; a stray brace breaks re-parsing.
            if not _braces_balanced(str(v)):
                raise ValueError(
                    f"field {c!r} in entry {row['key']!r} has unbalanced braces: {v!r}"
                )
        header = f"@{row['entry_type']}{{{row['key']},"
        if fields:
            body = ",\n".join(f"{indent}{c} = {{{v}}}" for c, v in fields)
            chunks.append(f"{header}\n{body},\n}}")
        else:
            chunks.append(f"{header}\n}}")

    return "\n\n".join(chunks) + "\n" if chunks else ""
