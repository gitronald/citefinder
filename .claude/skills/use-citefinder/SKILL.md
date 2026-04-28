---
name: use-citefinder
description: Look up DOIs, search Crossref, and resolve book chapters with `citefinder` — a small Crossref client with a JSONL cache that survives sessions and remembers 404s. Use this whenever the user wants to verify a DOI, find a paper by author + title, check whether a citation is real, resolve a chapter DOI, or generate canonical metadata for a reference list — even when they don't say "Crossref" or "DOI" explicitly. Phrases like "is this paper real?", "find the published version", "look up this citation", "the subagent gave me these papers — verify them", or "what's the DOI for X?" should trigger it.
---

# Use citefinder

`citefinder` (https://github.com/gitronald/citefinder) is a small Python library + CLI for Crossref lookups, with a JSONL-backed cache. Use it instead of raw `curl https://api.crossref.org/...` because:

- The cache survives sessions, so re-running verification is cheap.
- 404s are cached, so known-missing DOIs don't get re-queried.
- The cache is JSONL (one record per line) — `grep`-able, diffable, and crash-safe.
- It exposes both a Python API (for batch work, scripts, notebooks) and a CLI (for ad-hoc lookups).

## When to use this skill

- Verifying that a DOI resolves to the paper the user expects (the most common need).
- Finding the canonical / published DOI from an arxiv ID, SSRN URL, preprint title, or an `(Author Year)` inline citation.
- Resolving a book chapter DOI when you only have the book's DOI and a chapter number.
- Sanity-checking a list of references produced by a research subagent or extracted from a PDF.
- Building or enriching a bibliography (`.bib`, CSV) from an outline.

If the user describes a multi-step Zotero/bibliography workflow, also load the `resolve-zotero-references` skill — it composes citefinder with Zotero matching and a verification loop.

## Install / availability check

If citefinder isn't already a dependency:

```bash
uv add citefinder  # or: uv add git+https://github.com/gitronald/citefinder
```

Confirm it's wired:

```bash
uv run citefinder --help
```

## Three core operations

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

CLI:

```bash
citefinder doi 10.1126/science.aap9559
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
citefinder search "Wolfowicz hate speech meta-analysis" --rows 3
```

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
citefinder chapter 10.1017/9781108890960 5
```

`lookup_book_chapter` zero-pads numeric chapters to 3 digits. Pass a string instead (`client.lookup_book_chapter(book_doi, "ch1a")`) for publishers using a different format.

## Key behaviors to know

- **Cache path:** defaults to `~/.cache/citefinder/crossref.jsonl`. Use a project-local path (e.g., `data/crossref-cache.jsonl`) when you want results committed alongside an outline so collaborators don't re-query.
- **Latest value wins on replay.** Re-querying after a fix transparently overwrites — no manual cache invalidation needed.
- **`None` is a real cache value.** A cached `None` means "Crossref returned 404 for this DOI" — citefinder uses it to avoid re-hitting known-missing DOIs. If you suspect Crossref has now indexed a paper it didn't before, delete that line from the JSONL or use a fresh cache path.
- **`lookup_doi` returns the `message` payload directly,** not the full Crossref envelope. So you access `work["title"][0]`, not `work["message"]["title"][0]`.
- **`title` is a list, not a string.** Crossref returns titles as arrays. Use `work["title"][0]`.
- **`search_bibliographic` returns the items list,** which may be empty. Always handle the empty case.

## When citefinder isn't enough

Drop down to raw HTTP (`requests.get("https://api.crossref.org/...")`) only if you need:

- Crossref endpoints citefinder doesn't wrap (`/funders`, `/journals`, `/types`).
- A one-off query you specifically don't want cached.
- Streaming through large result sets via `cursor` pagination.

For everything else, prefer citefinder so the cache stays the single source of truth across sessions.
