---
id: 23
slug: build-bib-from-records
status: draft
branch:
created: 2026-11-23T15:00:53-08:00
concluded:
pr:
---

# Build bib files from cached lookup records

## Plan

### Problem

citefinder looks references up and verifies a bib file against Crossref and
OpenAlex, but it has no way to go the other way: from the records it has
cached to a bib file. A bib built from lookup records today is built by a
one-off script and then corrected by hand, and nothing records which fields
were copied from a record, which were reshaped, and which were guessed.

This plan adds a builder: one command that turns cached records into a `.bib`
file, reports every value it had to guess, and takes a file of checked
corrections so a rebuild reproduces them.

### What a record gives, and what the builder has to do

A synthetic example, in the shape the two sources return. Crossref:

```json
{"DOI": "10.5555/example.1", "type": "journal-article",
 "title": ["A Study of Things"], "subtitle": ["What We Found"],
 "author": [{"given": "Jane Q.", "family": "Doe"}, {"given": "Ana", "family": "Ruiz Moreno"}],
 "container-title": ["Journal of Examples &amp; Cases"],
 "volume": "12", "issue": "3", "page": "101-120",
 "issued": {"date-parts": [[2020, 11, 2]]}, "published-print": {"date-parts": [[2021, 3]]}}
```

OpenAlex, for the same work:

```json
{"doi": "https://doi.org/10.5555/example.1", "type": "review",
 "title": "A Study of Things: What We Found",
 "authorships": [{"author": {"display_name": "Jane Q. Doe"}, "raw_author_name": "Jane Q. Doe"},
                 {"author": {"display_name": "Ana Ruiz‐Moreno"}, "raw_author_name": "Ana Ruiz Moreno"}],
 "primary_location": {"source": {"display_name": "Journal of Examples & Cases"}},
 "biblio": {"volume": "12", "issue": "3", "first_page": "101", "last_page": "120"},
 "publication_year": 2020}
```

The entry built from them:

```bibtex
@article{doe2021study,
  author = {Doe, Jane Q. and Ruiz Moreno, Ana},
  title = {{A Study of Things: What We Found}},
  year = {2021},
  journal = {Journal of Examples & Cases},
  volume = {12},
  number = {3},
  pages = {101--120},
  doi = {10.5555/example.1}
}
```

| Bib field | Crossref | OpenAlex | Rule |
|---|---|---|---|
| key | | | first author's family name, year, first title word that is not a stopword |
| author | `given` and `family` per author | one name string per author | `family, given`, joined with ` and ` |
| title | `title` and `subtitle` apart | joined with `: ` | join with `: `, in double braces |
| journal | `container-title`, HTML-escaped | `primary_location.source.display_name` | unescape entities |
| year | `published-print`, else `issued` | `publication_year`, the online year | the issue's year |
| volume, number, pages | `volume`, `issue`, `page` | `biblio` | `issue` to `number`, `-` to `--` |
| entry type | `type` | `type` | map to a BibTeX type (`journal-article` and `review` to `@article`) |
| doi | bare | `https://doi.org/` URL | bare |

Crossref's fields are used where Crossref has the DOI, and OpenAlex's where it
does not or where Crossref lacks a field.

Records are found by cache `key`, not by searching a line for the DOI: a
record's reference list names other works' DOIs, so a text search can return
a record that cites the work instead of the work's own.

### Where the builder has to guess

OpenAlex gives no family/given split, so a name split from it is a guess, and
a compound family name ("Ruiz Moreno") is where it goes wrong. Its
`display_name` can carry U+2010 for a hyphen where `raw_author_name` has ASCII
`-`. Its year is the online year, and its `type` needs mapping. Choosing
between two years is a guess with either source.

A guessed value must not look the same as a copied one:

- **Per-field provenance.** Each built field carries its source, its record
  path, and one of four labels: `copied` (as in the record), `normalized`
  (changed without loss: entities, dashes, the DOI prefix), `inferred` (a rule
  that can be wrong: a name split, a choice between years, a type mapping), or
  `override` (from the corrections file). An inferred field keeps the raw value
  and what the other source gave. This follows `verify.Result`, which carries
  `status`, `note`, and `signals`.
- **In the CLI.** The command ends with a count of entries with inferred fields
  and lists each one (key, field, reason). `--strict` makes any inferred field
  an error. A marker inside the `.bib` is opt-in at most, since bib managers can
  drop comments and unknown fields; the provenance file is the record.
- **Corrections.** A file of fixes a person has checked, keyed by DOI, each with
  its field, value, and reason. An overridden field is labeled `override`, so
  the count of inferred fields falls as entries are checked.
- **In the docs.** A table of each bib field, its path in each source, and
  whether it is copied or inferred, with a measured error rate where a rule
  guesses, in the style of the field notes in `models.py`. The first measure:
  how often a name split from OpenAlex disagrees with Crossref's `family`, on
  cached works both sources have.

One way to guess less, not verified: an OpenAlex author profile's `full_name`
is sometimes inverted (`Family, Given`), which settles the split when it is,
at one more lookup per author.

### Files

Only the bib goes in the project's repository. Everything else goes under the
cache root, beside `verify`'s output for the same bib, in the directory
`_verify_out_dir` already names (`<root>/<bib-dir>[-<bib-stem>]/`, with no stem
suffix for `refs.bib`). For a bib at `reports/background/refs.bib`, with the
default root:

```
data/citefinder/crossref.jsonl                  # records, shared by every bib
data/citefinder/openalex.jsonl
data/citefinder/background/dois.txt             # the build's input
data/citefinder/background/overrides.toml       # checked corrections
data/citefinder/background/provenance.jsonl     # generated, one line per entry
reports/background/refs.bib                     # the only file in the repository
```

The build reads only the cache, with no network, so the same inputs give the
same files. A provenance line carries every field on every line, `None` when
unknown, plus the citefinder version and each record's `ts`.

### Test cases

Written synthetically, one per kind of correction a hand-built bib needs:
title and subtitle joined; HTML entities in a journal name; print year over
online year; the online year kept where the print year falls before the period
the work covers (an override); journal taken from the DOI prefix when Crossref
has no record; the DOI looked up kept when Crossref's record carries a previous
version's; an issue label that is a date dropped; pages from OpenAlex when
Crossref has none; a family name spelled differently from the record (an
override); a title in capitals restored; a compound family name from OpenAlex
flagged as inferred.

### Implementation order

1. The field mapping for Crossref records, with provenance, and the test cases
   that need only Crossref.
2. OpenAlex as a fallback per field, with the inferred labels.
3. The overrides file and the `override` label.
4. The CLI command: DOI list in, `refs.bib` and `provenance.jsonl` out, the
   summary, and `--strict`.
5. The docs table, with the measured rate for name splits.

### Open questions

1. **The cache's default root.** Lookups default to `~/.cache/citefinder`, and
   `verify` and `authors` to `data/citefinder` under the working directory. The
   builder writes beside `verify`, so it takes `verify`'s root; should lookups
   move to it too?
2. **Overrides outside git.** A fix's history is not in commits, so each
   override's `reason` has to stand alone. Is that enough, or should the
   command keep an append-only log of override edits?
3. **The overrides file's shape.** How an author is addressed (by position, by
   name), and what happens to an override when the record it corrects changes.
