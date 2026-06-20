"""Tabulate a `.bib` file into a wide DataFrame for visual inspection.

Designed for the editorial workflow: before running any cleanup, see the
bib at a glance — which entries have abstracts, who has uppercase field
keys, where the URLs sit, which titles need title-case work, etc. Reading
a flat tabular view is faster than scrolling through `.bib` source.

Note: citefinder lowercases all field keys on read, so this view normalizes
`DOI` -> `doi`. To inspect the original case, look at the `.bib` source.
"""

from __future__ import annotations

import polars as pl

from citefinder.bib import parse_entries


def _braces_balanced(s: str) -> bool:
    """Whether `{`/`}` in `s` are balanced and never close before opening.

    A brace-delimited bib value must satisfy this or it corrupts the entry
    when wrapped in `{...}` (`a } b` would truncate at the stray `}`).
    """
    depth = 0
    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def bib_to_table(text: str) -> pl.DataFrame:
    """Parse a bib string into a wide DataFrame.

    One row per entry, one column per field.

    Missing fields fill with null. Columns are ordered: `key`,
    `entry_type`, then the rest alphabetically. The lower-cased field
    keys come from citefinder's parser (uppercase variants like `DOI`
    are normalized on read).

    `entry_type` holds the entry kind (e.g., `article`, `book`, `misc`).
    The name avoids `type` because real bibs sometimes use a literal
    `type` field (e.g., SSRN papers set `type = {SSRN Scholarly Paper}`),
    which would otherwise collide with the entry-kind column.
    """
    entries = parse_entries(text)

    all_fields: set[str] = set()
    for e in entries:
        all_fields.update(e.fields.keys())

    # `key` and `entry_type` are reserved for the citation key and entry kind.
    # A bib field with either name (BibTeX has a real `key` sort field) would
    # be silently overwritten and lost on round-trip, so refuse loudly instead.
    reserved = sorted(all_fields & {"key", "entry_type"})
    if reserved:
        raise ValueError(
            f"bib field(s) {reserved} collide with reserved table columns "
            "(key, entry_type); cannot tabulate without data loss"
        )

    rows: list[dict[str, str | None]] = []
    for e in entries:
        row: dict[str, str | None] = {f: e.fields.get(f) for f in all_fields}
        row["key"] = e.key
        row["entry_type"] = e.etype
        rows.append(row)

    if not rows:
        return pl.DataFrame(schema={"key": pl.Utf8, "entry_type": pl.Utf8})

    df = pl.DataFrame(rows)
    other = sorted(c for c in df.columns if c not in ("key", "entry_type"))
    return df.select(["key", "entry_type", *other])


def table_to_bib(df: pl.DataFrame, indent: str = "  ") -> str:
    """Serialize a `bib_to_table`-shaped DataFrame back to a `.bib` string.

    Inverse of `bib_to_table`. Requires `key` and `entry_type` columns;
    every other column becomes a bib field. Null cells are skipped
    (treated as absent fields). Field order in each entry follows
    the DataFrame's column order, so a frame produced by
    `bib_to_table` round-trips into alphabetically-ordered fields —
    the bib's original field order is not recoverable from the table.
    """
    missing = {"key", "entry_type"} - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

    field_cols = [c for c in df.columns if c not in ("key", "entry_type")]
    chunks: list[str] = []
    for row in df.iter_rows(named=True):
        key, etype = row["key"], row["entry_type"]
        if key is None or etype is None:
            raise ValueError(
                f"row has null key/entry_type: key={key!r}, entry_type={etype!r}"
            )
        fields = [(c, row[c]) for c in field_cols if row[c] is not None]
        for c, v in fields:
            # Values are emitted verbatim inside `{...}`. A non-string cell
            # (e.g. a float `12.0` from an un-typed frame) would stringify into
            # a corrupted value, and unbalanced braces would break the entry's
            # brace nesting — reject both rather than write a malformed bib.
            if not isinstance(v, str):
                raise ValueError(
                    f"field {c!r} in entry {key!r} is {type(v).__name__}, not str; "
                    "cast table columns to strings before serializing"
                )
            if not _braces_balanced(v):
                raise ValueError(
                    f"field {c!r} in entry {key!r} has unbalanced braces: {v!r}"
                )
        header = f"@{etype}{{{key},"
        if fields:
            body = ",\n".join(f"{indent}{c} = {{{v}}}" for c, v in fields)
            chunks.append(f"{header}\n{body},\n}}")
        else:
            chunks.append(f"{header}\n}}")

    return "\n\n".join(chunks) + "\n" if chunks else ""
