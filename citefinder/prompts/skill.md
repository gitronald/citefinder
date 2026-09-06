---
name: use-citefinder
description: Look up DOIs, search Crossref or OpenAlex, resolve book chapters, and verify whole `.bib` files with `citefinder` — a small Crossref + OpenAlex client with a JSONL cache that survives sessions and remembers 404s. Use this whenever the user wants to verify a DOI, find a paper by author + title, check whether a citation is real, resolve a chapter DOI, look up an arXiv/preprint DOI Crossref doesn't index, generate canonical metadata for a reference list, or audit a `.bib` file end-to-end — even when they don't say "Crossref" or "DOI" explicitly. Phrases like "is this paper real?", "find the published version", "look up this citation", "the subagent gave me these papers — verify them", "audit refs.bib", or "what's the DOI for X?" should trigger it.
---

# Use citefinder

`citefinder` (https://github.com/gitronald/citefinder) is a small Python library + CLI for Crossref **and OpenAlex** lookups, with a JSONL-backed cache. Use it instead of raw `curl https://api.crossref.org/...` because:

- The cache survives sessions, so re-running verification is cheap.
- 404s are cached, so known-missing DOIs don't get re-queried.
- The cache is JSONL (one record per line) — `grep`-able, diffable, and crash-safe.
- It exposes both a Python API (for batch work, scripts, notebooks) and a CLI (for ad-hoc lookups).

## When to use this skill

- Verifying that a DOI resolves to the paper the user expects (the most common need).
- Finding the canonical / published DOI from an arxiv ID, SSRN URL, preprint title, or an `(Author Year)` inline citation.
- Resolving a book chapter DOI when you only have the book's DOI and a chapter number.
- Auditing a whole `.bib` file: which entries match, which have wrong DOIs, which can't be found.
- Sanity-checking a list of references produced by a research subagent or extracted from a PDF.
- Building or enriching a bibliography (`.bib`, CSV) from an outline.

## Install / availability check

If citefinder isn't already a dependency:

```bash
uv add citefinder  # or: uv add git+https://github.com/gitronald/citefinder
```

Confirm it's wired:

```bash
uv run citefinder --help
```

## Where these instructions come from

You are reading the output of `citefinder skill`, printed from inside the
installed package — so it always matches the CLI you are about to run, and
there is no copy of it anywhere to go stale. The file in
`.claude/skills/use-citefinder/` is only a stub carrying the trigger metadata
and pointing here.

To change this content, edit `citefinder/prompts/skill.md` in the citefinder
repo and release; there is nothing to re-copy. `citefinder install --check`
verifies the *stub*, which changes rarely.

## Four core operations

### 1. Verify a single DOI

```python
from citefinder import CrossrefClient

client = CrossrefClient(cache_path="~/.cache/citefinder/crossref.jsonl")
work = client.lookup_doi("10.1126/science.aap9559")
if work is None:
    # 404 — the DOI doesn't resolve. May be fabricated, mistyped, or too new for Crossref's index.
    ...
else:
    print(work["title"][0])
```

CLI (top-level commands default to OpenAlex; use the `crossref` subcommand for Crossref-specific shapes):

```bash
citefinder doi 10.1126/science.aap9559                  # OpenAlex
citefinder crossref doi 10.1126/science.aap9559         # Crossref
```

**Always compare the returned title to the title you expected.** This is the single most important habit. Subagents and PDF extractors regularly produce DOIs that are *off by a few characters* in the suffix (e.g., `psrm.2025.14` vs `psrm.2025.10063`) — those wrong suffixes often resolve to a real-but-different paper in the same journal. The DOI lookup itself returns 200; only a title comparison catches it.

### 2. Search bibliographically

When you don't have a DOI (or the DOI you have is suspect), search by free-form text:

```python
hits = client.search_bibliographic(
    f"{first_author_last_name} {distinctive_title_words}",
    rows=3,
)
for hit in hits:
    print(hit["DOI"], "-", hit["title"][0])
```

CLI:

```bash
citefinder search "Backstabber's Knife Collection"               # OpenAlex (title-only filter)
citefinder crossref search "Wolfowicz hate speech meta-analysis" # Crossref (author + title + year)
```

Note: `citefinder search` (OpenAlex) runs a title-only filter — pass just title words. `citefinder crossref search` accepts free-form bibliographic queries (author + title + year) and is closer in behavior to a generic "find this paper" query.

Tips for good queries:

- First author's last name plus 2–4 distinctive title words is usually enough.
- Avoid generic words ("study", "analysis", "the") — they dilute the relevance score.
- For preprints, both an SSRN/arxiv DOI and a published DOI may come back. Prefer the published one unless the user wants the preprint.

### 3. Look up a book chapter

Many edited volumes follow the convention `{book_doi}.{NNN}` for chapter DOIs (e.g., `10.1017/9781108890960.005` for chapter 5).

```python
chapter = client.lookup_book_chapter("10.1017/9781108890960", 5)
```

CLI:

```bash
citefinder crossref chapter 10.1017/9781108890960 5
```

`lookup_book_chapter` zero-pads numeric chapters to 3 digits. Pass a string instead (`client.lookup_book_chapter(book_doi, "ch1a")`) for publishers using a different format.

### 4. Verify a whole .bib file

When the user has a `.bib` and asks "audit these references" / "check what's wrong" / "which entries don't resolve" — use the bib-verification pipeline rather than calling `lookup_doi` per entry by hand. It parses, resolves DOIs, falls back to bibliographic search, checks four signals (title, year, first-author surname, container), and buckets each entry by status. Given names are never compared — see *Given names and diacritics* below for the offline check.

CLI:

```bash
citefinder verify refs.bib                       # OpenAlex (default)
citefinder verify refs.bib --source crossref     # ...or Crossref
citefinder verify refs.bib --out path/to/dir/    # custom output directory
citefinder verify refs.bib --min-interval 0.5 --max-retries 5   # slow down for a strict rate limit
```

Output lands in `<cache_dir>/<bib-dir>[-<bib-stem>]/<source>/` — `data/citefinder/` under the working directory when no `cache_dir` is configured (`citefinder config` shows which). `<bib-dir>` is the directory holding the `.bib`; the `-<bib-stem>` suffix is added for a file not named `refs.bib` (`paper/refs.bib` → `paper/`, `paper/extra.bib` → `paper-extra/`):

- `<source>.jsonl` — append-only response cache; re-running is cheap.
- `results.json` — structured per-entry result (status, matched DOI, signals).

Per-entry statuses: `matched` (signals confirm the work; for a DOI hit, `note` may record one disagreeing field), `probable` (one signal disagreed, or too few could be checked — review), `mismatch` (≥2 signals disagreed — DOI to wrong work), `doi-not-found` (404 — common for arXiv/preprint DOIs in Crossref), `unmatched` (no plausible hit), `skip-source` (`@online`/`@misc` — verify via URL), `error`.

**Reading the report.** Read `results.json` by `method` × `status`:

- `method=doi` with `mismatch` — a real defect: the bib's own DOI resolves to a different work. Fix the entry.
- `method=doi` with `probable` — the DOI resolved but the title disagrees, or too few fields could be checked. A title-only disagreement is usually a deficient bib title (one or two words, or copied from the wrong paper), but can be a typoed DOI that lands on a related paper by the same author — compare the two titles in `signals`. When the note says too few signals confirm, fill in the missing author, year, or journal/booktitle and re-run.
- `method=doi` with `matched` and a non-empty `note` — the DOI resolved and the other signals confirm the work, but one field disagrees. With OpenAlex this is usually the source's metadata, not the bib's: it truncates titles at the colon, stores the series name ("Lecture notes in computer science") instead of the booktitle, and reports the online-first or preprint year. Do not rewrite the entry to match the source; for a year disagreement follow the year guidance below.
- `method=search` with `matched` and a non-empty `matched_doi` — a DOI candidate for an entry that lacked one. Confirm the title, then add it.
- `method=search` with `mismatch` / `probable` — usually a wrong-work false positive: books, reports, and other sources the index carries poorly get matched to a similarly titled record. Not a reason to rewrite the entry.
- `unmatched` (or `skip-source` for `@online`/`@misc`) with a "title too short" note — the bib title has fewer than three words, so search cannot tell hits apart. Pick from `candidates` by hand, or complete the title and re-run.
- `unmatched`, `skip-source`, and `doi-not-found` — noise unless they cluster around one publisher or entry type; then look for a systematic cause (a preprint server the source doesn't index, a publisher whose DOI convention the search misses).

Crossref and OpenAlex are complementary — Crossref has richer metadata for indexed records (full title + subtitle, multiple container aliases) but doesn't index arXiv/preprints; OpenAlex covers preprints but sometimes truncates titles or returns preprint years instead of publication years. For a thorough audit, run both and compare.

For programmatic use:

```python
from citefinder import OpenAlexClient, Source, parse_entries, verify_entry

source = Source(name="openalex", client=OpenAlexClient(cache_path="cache.jsonl"))
for entry in parse_entries(open("refs.bib").read()):
    r = verify_entry(entry, source)
    print(r.key, r.status, r.matched_doi)
```

For a quick non-network preview of what's in a `.bib` (useful for sanity-checking parsing or dumping to CSV):

```bash
citefinder bib-to-table refs.bib                          # wide polars table to terminal
citefinder bib-to-table refs.bib --csv > refs.csv         # ...or CSV to stdout
citefinder bib-to-table refs.bib --fields title,year,doi  # subset of columns
```

`bib-to-table` ↔ `table-to-bib` round-trips, so the CSV is also an editing surface — fix entries in a spreadsheet, then regenerate the `.bib`:

```bash
citefinder bib-to-table refs.bib --csv > refs.csv         # edit refs.csv in a spreadsheet
citefinder table-to-bib refs.csv --out refs.bib           # regenerate
```

Field order within each entry is not preserved (it follows the CSV's column order), but keys, entry types, and field values round-trip verbatim.

## Key behaviors to know

- **Cache path:** check for a project config before passing `--cache`. A repo that sets `cache_dir` in `citefinder.toml` (or `[tool.citefinder]` in `pyproject.toml`) already routes every command's cache there — lookups to `<cache_dir>/<source>.jsonl`, `verify` to `<cache_dir>/<bib-dir>[-<bib-stem>]/<source>/` — from any working directory inside it. `citefinder config` prints where a lookup will write and why (`flag`, `env`, `project`, `user`, or `default`). Without a config the default is `~/.cache/citefinder/<source>.jsonl`; pass `--cache-dir` (or `--cache` for one file) only when there is no project config and you want results committed alongside an outline so collaborators don't re-query.
- **Latest value wins on replay.** Re-querying after a fix transparently overwrites — no manual cache invalidation needed.
- **`None` is a real cache value.** A cached `None` means "Crossref returned 404 for this DOI" — citefinder uses it to avoid re-hitting known-missing DOIs. If you suspect Crossref has now indexed a paper it didn't before, delete that line from the JSONL or use a fresh cache path.
- **`lookup_doi` returns the `message` payload directly,** not the full Crossref envelope. So you access `work["title"][0]`, not `work["message"]["title"][0]`.
- **`title` is a list, not a string.** Crossref returns titles as arrays. Use `work["title"][0]`.
- **`search_bibliographic` returns the items list,** which may be empty. Always handle the empty case.
- **Rate limits retry themselves.** A `429` (and `502`/`503`/`504`) is retried up to 3 times, honoring `Retry-After` or backing off 1 s / 2 s / 4 s, and OpenAlex requests are paced to 10 per second by default. Only the final failure surfaces — in `verify` as a per-entry `error` whose note names the status, plus the retry count in the summary line. An error response is **never cached**, so there is nothing to purge after a rate limit: wait for it to clear and re-run, or slow the run down with `--min-interval 0.5` / `--max-retries 5` (also `max_retries` / `min_interval` in `config.toml`).

## OpenAlex fallback for arXiv / preprint / thin-metadata DOIs

Crossref doesn't index arXiv DOIs (`10.48550/arXiv.*`) and many repository deposits — those return 404 from `lookup_doi`. Crossref also frequently has thin metadata (missing abstract, abbreviated title, no affiliations) on records that exist. Use OpenAlex as the second source in those cases:

```python
from citefinder import CrossrefClient, OpenAlexClient, is_arxiv_doi

crossref = CrossrefClient(cache_path="~/.cache/citefinder/crossref.jsonl")
openalex = OpenAlexClient(
    cache_path="~/.cache/citefinder/openalex.jsonl",
    mailto="you@example.com",  # opts into OpenAlex's polite pool — faster, higher daily quota
)

doi = "10.48550/arXiv.2410.21554"
if is_arxiv_doi(doi):
    work = openalex.lookup_doi(doi)  # arXiv DOIs go straight to OpenAlex
else:
    work = crossref.lookup_doi(doi) or openalex.lookup_doi(
        doi
    )  # Crossref-first, OpenAlex fallback
```

CLI (top-level commands are OpenAlex by default):

```bash
citefinder doi 10.48550/arXiv.2410.21554
citefinder search "fact-checking large language models"
```

OpenAlex's schema differs from Crossref — different keys for the same data:

| Crossref | OpenAlex |
|---|---|
| `work["title"][0]` (+ `subtitle[0]`) | `work["display_name"]` |
| `work["author"][0]["family"]` | `work["authorships"][0]["author"]["display_name"]` |
| `work["container-title"][0]` | `work["primary_location"]["source"]["display_name"]` |
| `work["published-print"]["date-parts"][0][0]` | `work["publication_year"]` |

**Which fields carry the name split.** For a family/given boundary question — where the surname starts in a multi-part or non-Western name — compare against Crossref's `author[i].family` and `author[i].given`: that split is the one the publisher deposited. OpenAlex exposes only a flat, first-name-first `display_name` (and `raw_author_name` in byline order), which you would have to re-parse, so it cannot settle the question.

OpenAlex stores abstracts as an `abstract_inverted_index` (`{word: [positions]}`), not a string. Use the helper:

```python
from citefinder import reconstruct_abstract

abstract = reconstruct_abstract(work)  # returns plain string or None
```

### Given names and diacritics

The four verify signals never look at given names. `author: pass` means the first author's **surname** matched the record and nothing more — a given name that is misspelled, abbreviated, or missing a diacritic against the record still lands in `matched`. Checking given names is a separate, offline pass over the cache that `verify` already wrote.

Which cached field answers which question:

- *What the byline printed* → Crossref `author[i].given` + `author[i].family`, or OpenAlex `authorships[i].raw_author_name`. Both reproduce the publisher's deposit, diacritics included or dropped as deposited.
- *How the author's name is canonically spelled* → OpenAlex `authorships[i].author.display_name`, the author's profile name. This is the field that carries a diacritic the deposit dropped: for a 1991 law-review article the bib and both bylines read `Kimberle`, and only the embedded `display_name` reads `Kimberlé W. Crenshaw`.

The record shapes are declared in `citefinder.models` (`CrossrefWork`, `OpenAlexWork`, `CrossrefAuthor`, `OpenAlexAuthorship`, ...) — read that module before guessing a key, and run `citefinder drift <cache.jsonl>` to see what the cached records carry that the model does not.

Two traps when reading the cache by hand:

- **Cache rows are wrapped.** Each JSONL line is `{"key": <request URL>, "value": <payload>, "ts": ...}`. A DOI lookup's key contains `/works/`; a search page's key contains `/works?`. A cached 404 is `"value": null`. The Crossref value is the full envelope, so the work is `value["message"]`; the OpenAlex value is the work itself.
- **Bib values are TeX-escaped, API values are Unicode.** `Kimberl{\'e}` in the bib is `Kimberlé` in the record. De-escape the bib side (`pylatexenc`, already installed as a bibtexparser dependency) and normalize both sides to NFC before comparing, or every accented name reads as a difference.

Offline recipe — read the paper's `crossref.jsonl` and `openalex.jsonl`, join to the bib by DOI, and print every author position whose first given-name token differs from either source. It compares the first token only, because middle names and initials differ between sources far too often to be a useful signal:

```python
import json
import unicodedata
from pathlib import Path

from bibtexparser.middlewares.names import (
    parse_single_name_into_parts,
    split_multiple_persons_names,
)
from pylatexenc.latex2text import LatexNodes2Text

from citefinder import bib_to_table
from citefinder.bib import normalize_doi

decode = LatexNodes2Text().latex_to_text  # Kimberl{\'e} -> Kimberlé


def nfc(s: str | None) -> str | None:
    """NFC-normalize, keeping None as "no value to compare"."""
    return unicodedata.normalize("NFC", s).strip() if s else None


def first_token(s: str) -> str:
    return s.split(" ")[0]


def given(name: str | None) -> str | None:
    """Given-name portion of a flat first-name-first string, or None."""
    parts = parse_single_name_into_parts(name) if name else None
    return " ".join(parts.first) if parts and parts.first else None


def works_by_doi(path: str, unwrap=lambda v: v) -> dict:
    out = {}
    for line in Path(path).open(encoding="utf-8"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a line torn by a crash mid-write; JsonlCache skips it too
        if "/works/" not in rec["key"] or rec["value"] is None:
            continue  # a search page, or a cached 404
        work = unwrap(rec["value"])
        doi = normalize_doi(work.get("DOI") or work.get("doi") or "")
        if doi:
            out[doi.lower()] = work
    return out


crossref = works_by_doi(
    "data/citefinder/paper/crossref/crossref.jsonl", lambda v: v["message"]
)
openalex = works_by_doi("data/citefinder/paper/openalex/openalex.jsonl")

df = bib_to_table(open("paper/refs.bib", encoding="utf-8").read())
checked = 0
print("key\tpos\tbib\tcrossref\topenalex")
for r in df.iter_rows(named=True):
    doi = normalize_doi(r.get("doi") or "").lower()
    if not doi or not r.get("author"):
        continue
    checked += 1
    cr = (crossref.get(doi) or {}).get("author") or []
    oa = (openalex.get(doi) or {}).get("authorships") or []
    # Split on the raw TeX so `{Corporate, Author and Sons}` stays one person;
    # decode only the given-name portion.
    for i, person in enumerate(split_multiple_persons_names(r["author"])):
        b = nfc(decode(given(person) or ""))
        if not b:
            continue  # corporate author, `others`, or no given name in the bib
        c = nfc(cr[i].get("given")) if i < len(cr) else None
        o = (
            nfc(given((oa[i].get("author") or {}).get("display_name")))
            if i < len(oa)
            else None
        )
        if (c and first_token(c) != first_token(b)) or (
            o and first_token(o) != first_token(b)
        ):
            print(r["key"], i + 1, b, c, o, sep="\t")
print(f"checked {checked} of {len(df)} entries (those with a DOI and an author field)")
```

A row means the bib and at least one source disagree; an empty `crossref` or `openalex` column means that source has no record for the DOI, fewer authors than the bib, or no given name at that position (a corporate author, say). Positions where the bib itself has no given name are skipped, and the last line says how many entries were actually compared — a bib whose entries mostly lack DOIs produces a clean-looking empty result for the wrong reason. Read the output as a **byline check, never an auto-fix**: a record that lacks a diacritic the author uses today is common for older articles, and the editor decides which form the citation carries. Do not rewrite the bib to match the record, and do not rewrite it to match the profile name either without checking the printed byline.

**Validate a zero.** Before trusting an empty result, copy the bib, alter one given name (`Virginia` → `Virgina`), and re-run: the altered entry must appear, and only that entry. A recipe that stays silent on the altered copy is reading the wrong cache path or joining on DOIs that never match (a `https://doi.org/` prefix left on one side, say), not reporting a clean bib.

### Year mismatches between Crossref and OpenAlex — flag and prefer the final printed record

Crossref and OpenAlex regularly disagree on a work's year because they index different events. Crossref's `published-print` tracks the issue/volume year; OpenAlex's `publication_year` often collapses to the online-first or precursor date. Treat any year mismatch as something to flag for review, then default to the **final printed record** — the journal volume year, or for books the publisher's first-published edition year.

Two patterns to watch:

- **Online-first vs volume year (journal articles).** A DOI minted in 2016-10 for online-first, printed later in a volume (2018-09). Crossref splits it cleanly (`published-print` 2018-09, `created` 2016-10); OpenAlex's `publication_year` is 2016. Cite the volume year (2018).
- **Precursor work vs published edition (books).** A monograph DOI may surface in OpenAlex as a `dissertation` dated 2020, while Crossref returns the same DOI as a `monograph` issued 2022 — the dissertation became the book. Cite the publisher's first-published year (2022).

Quick mismatch check:

```python
cr_year = (work_cr.get("published-print") or work_cr.get("issued") or {}).get(
    "date-parts", [[None]]
)[0][0]
oa_year = work_oa.get("publication_year")
if cr_year != oa_year:
    # flag for human review; default to printed-volume / published-edition year
    ...
```

If only OpenAlex has the record, sanity-check its `type` field — `dissertation` or `posted-content` next to a journal/monograph DOI is the giveaway that you're looking at a precursor, not the cite-target.

### OpenAlex API key (optional, for higher rate limits)

`OpenAlexClient` reads the API key in this order: explicit `api_key=...` arg → `OPENALEX_API_KEY` env var → (CLI only) project-local `.env` → (CLI only) `~/.config/citefinder/config.toml`. The key is sent as `Authorization: Bearer ...`, never in the URL or cache key.

For ad-hoc lookups, no key is needed — common-pool requests work fine. To store the key once per machine, drop a TOML file at the XDG config path:

```toml
# ~/.config/citefinder/config.toml
[openalex]
api_key = "your-openalex-key"
mailto = "you@example.com"

[crossref]
mailto = "you@example.com"
```

Each section is optional; omit anything you don't need. The file is plain-text — recommend `chmod 600` so it's only readable by the user.

The CLI picks the config up automatically; project-local `.env` and shell env still override it. A repo can also commit a **project config** — `citefinder.toml`, or `[tool.citefinder]` in `pyproject.toml`, found by walking up from the working directory — with the same keys minus `api_key` (a key there is ignored with a warning, since the file is meant to be committed). Typically it carries `cache_dir` and `mailto`; it sits between the env and the user config in precedence (flag > env and `.env` > project > user > default). `citefinder config` prints the resolved result with each value's source. For programmatic library use, neither file is auto-loaded — pass `api_key=...` and `mailto=...` explicitly or set the env vars before constructing the client.

### Picking `mailto`

Use a project alias (e.g. the `authors` email in `pyproject.toml`) or omit entirely. Don't drop the user's personal email into `mailto` without asking — it's an outbound identifier, and a project/noreply address is the right default.

## Inspecting `bib_to_table` output side-by-side in the terminal

`citefinder.bib_to_table` returns a polars DataFrame, one row per bib entry. polars's default text rendering wraps long values mid-string — fine for short columns, ugly for URLs and titles where the wrap point lands inside a token.

For ad-hoc audits that show two or three fields side by side (e.g. `doi` vs. `url`, `title` vs. `journal`), use this dynamic-width plain-text helper instead. Each column expands to fit its longest value, so URLs and DOIs never break across lines:

```python
from citefinder import bib_to_table

df = bib_to_table(open("refs.bib").read())
fields = ["key", "doi", "url"]  # adjust to taste
rows = [r for r in df.iter_rows(named=True) if all(r.get(f) for f in fields[1:])]

widths = {f: max(len(str(r[f])) for r in rows + [{f: f}]) for f in fields}
sep = "+" + "+".join("-" * (widths[f] + 2) for f in fields) + "+"
hdr = "| " + " | ".join(f"{f:<{widths[f]}}" for f in fields) + " |"
print(f"{len(rows)} rows\n")
print(sep)
print(hdr)
print(sep)
for r in rows:
    print("| " + " | ".join(f"{str(r[f]):<{widths[f]}}" for f in fields) + " |")
print(sep)
```

Edit `fields` and the row-filter predicate for the columns you want. Keep this as a one-off rendering helper — don't promote it into a script unless the same audit shows up across many papers.

## When citefinder isn't enough

For **generating formatted BibTeX strings** from a DOI or query, use [`fetchbib`](https://github.com/mr-devs/fetchbib) (`fbib`) instead — it handles doi.org content negotiation, arXiv routing, and BibTeX-flavored config (protect titles, exclude ISSN, etc.). citefinder returns raw JSON for verification; fetchbib emits paste-ready BibTeX for citation lists.

Drop down to raw HTTP (`requests.get("https://api.crossref.org/...")`) only if you need:

- Crossref or OpenAlex endpoints citefinder doesn't wrap (Crossref `/funders`, `/journals`, `/types`; OpenAlex `/authors`, `/institutions`, `/sources`).
- A one-off query you specifically don't want cached.
- Streaming through large result sets via `cursor` pagination.

For everything else, prefer citefinder so the cache stays the single source of truth across sessions.
