# Given names and diacritics

The four verify signals never look at given names. `author: pass` means the first author's **surname** matched the record and nothing more — a given name that is misspelled, abbreviated, or missing a diacritic against the record still lands in `matched`. Checking given names is a separate, offline pass over the cache that `verify` already wrote.

Which cached field answers which question:

- *What the byline printed* → Crossref `author[i].given` + `author[i].family`, or OpenAlex `authorships[i].raw_author_name`. Both reproduce the publisher's deposit, diacritics included or dropped as deposited.
- *How the author's name is canonically spelled* → OpenAlex `authorships[i].author.display_name`, the author's profile name. This is the field that carries a diacritic the deposit dropped: for a 1991 law-review article the bib and both bylines read `Kimberle`, and only the embedded `display_name` reads `Kimberlé W. Crenshaw`.

The record shapes are declared in `citefinder.models` (`CrossrefWork`, `OpenAlexWork`, `CrossrefAuthor`, `OpenAlexAuthorship`, ...) — read that module before guessing a key, and run `{cli} drift <cache.jsonl>` to see what the cached records carry that the model does not.

Two traps when reading the cache by hand:

- **Cache rows are wrapped.** Each JSONL line is `{"key": <request URL>, "value": <payload>, "ts": ...}`. A DOI lookup's key contains `/works/`; a search page's key contains `/works?`; an author profile's (see *Author profiles* below) contains `/authors/`. A cached 404 is `"value": null`. The Crossref value is the full envelope, so the work is `value["message"]`; the OpenAlex value is the work itself.
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

## Author profiles

The recipe reads one name per author: the `display_name` embedded in the work record. Fetch the author's full OpenAlex profile only when that leaves a question open:

- **Are two differently spelled bylines one person?** `display_name_alternatives` lists every spelling OpenAlex merged into the profile, so two bylines that resolve to one author ID, each listed there, are one author as far as OpenAlex can tell.
- **Is this the spelling the author uses now?** The profile's `display_name` is the current preferred form (a diacritic added later, a name change, an initial dropped); the copy embedded in an old cached work can lag it.
- **Which of several people with this surname is it?** `orcid` and `last_known_institutions`.

```bash
{cli} author A5012345678              # by the `author.id` on a cached work, bare or as its URL
{cli} author 0000-0002-1825-0097      # ...or by ORCID, bare or as its URL
{cli} author A5012345678 --raw        # the whole record instead of the summary
{cli} authors-from-cache data/citefinder/paper/openalex/openalex.jsonl
```

`author` prints six fields: `id`, `display_name`, `display_name_alternatives`, `orcid`, `works_count`, and `last_known_institutions` (names only). It takes an author ID or an ORCID, never a name: get the ID from `authorships[i].author.id` in the cached work, or search by name in a script with `OpenAlexClient.search_authors`. A profile OpenAlex no longer has prints `not found` and exits 1.

`authors-from-cache` does the same for a whole bibliography. It walks the DOI lookups in a `verify` run's `openalex.jsonl`, fetches the profile of every distinct linked author, and prints one tab-separated row each (`id`, `display_name`, `alternatives`, `orcid`), in the order the authors first appear, with progress and a summary on stderr. `id` is the ID the cached work carries, so a row joins back to its works. `alternatives` is `display_name_alternatives` without the preferred name, joined with `; `. Pipe it through `sort -t $'\t' -k2` to bring near-identical names together, or `column -t -s $'\t'` to read it.

- **One request per distinct author not yet cached** — close to three per entry in one surveyed cache (531 authors on 185 works), so a 60-entry bib is roughly 170 requests on a cold cache, paced by `min_interval`. Single-record lookups do not draw on OpenAlex's daily credit budget.
- **Cache-first and idempotent.** Profiles are appended to the same `openalex.jsonl`, cached 404s included, so a re-run answers from disk and a run stopped by a rate limit resumes where it stopped. A miss falls back, read-only, to the shared `<cache_dir>/openalex.jsonl` as `verify` does (`--no-fallback` turns that off), and `{cli} cache merge` consolidates profiles like any other row.
- **Only DOI lookups are walked.** An entry `verify` matched by search has its work inside a search page of candidates, which is skipped. Add the `matched_doi` to the bib, re-run `verify`, then re-run this.
- **Unlinked authors have no row.** An authorship OpenAlex could not attach to a profile carries no `author.id`; only `raw_author_name` exists for it.

A profile is OpenAlex's automated disambiguation, not a statement by the author: it can merge two people who share a name or split one person across two IDs (see *One author split across profiles* below), and its alternatives include deposit artifacts (inverted `Family, Given` forms, bare initials). So the rule above holds here too: **a profile informs a byline check and never rewrites a name by itself.** Report the disagreement with the profile's evidence, and leave the choice of form to the editor and the printed byline.

In a script, `OpenAlexClient.lookup_author(id_or_orcid)` returns the raw record (`OpenAlexAuthorProfile` in `citefinder.models`) and `openalex_to_author` projects it onto the six-field `AuthorProfile`:

```python
from citefinder import OpenAlexClient, openalex_to_author

client = OpenAlexClient(cache_path="data/citefinder/paper/openalex/openalex.jsonl")
profile = openalex_to_author(client.lookup_author("https://openalex.org/A5012345678"))
if profile is not None:
    print(profile.display_name, profile.display_name_alternatives)
```

## One author split across profiles

OpenAlex can file one person's works under several author IDs. The usual cause is a duplicate record of one paper: no DOI, every author's name inverted, and a new author ID minted for each of them. A bib entry with no `doi` can then match the duplicate by title, and carries its scrambled names and no DOI candidate.

Check for a split when:

- `authors-from-cache` ends with `N pair(s) of author IDs have names that match in full`, or two rows of its table read as one name;
- a profile's `works_count` is far below what the author has published, or a paper you know is missing from it;
- a matched record has inverted author names and no DOI.

```bash
{cli} authors-from-cache data/citefinder/paper/openalex/openalex.jsonl --same-names
{cli} author-fragments A5012345678             # other profiles of this author, each assessed
{cli} author-fragments A5012345678 --all       # list the `different` and `unchecked` candidates too
{cli} author-fragments A5012345678 --refresh   # refetch what the cache holds
```

`--same-names` prints one row (`id_a`, `name_a`, `id_b`, `name_b`) per pair of author IDs in the cache whose names match in full, in any order and with diacritics folded, in place of the table. It makes no request once the profiles are cached. A pair is one person under two profiles or two namesakes, and `author-fragments` on either ID says which. Pairs that match only through an initial are left out: nearly every profile lists an initials form, so those would pair any two authors who share a family name and a first letter.

`author-fragments` takes one profile (the anchor) by author ID or ORCID. It searches for profiles under a compatible name, sets aside those with a different ORCID, and compares the works of the rest with the anchor's. It prints JSON with five parts (`anchor`, `searches`, `candidates`, `counts`, `budget`) and a summary on stderr. Read each candidate's `verdict` with its `reason` and `evidence`:

- **`likely-fragment`** — the same ORCID; or a `duplicate_titles` entry, a work with the title of one of the anchor's on another record; or `shared_coauthors`, full names that are also co-authors on the anchor's works. For a duplicate title, `doi_on` says which record carries the DOI (`anchor`, `candidate`, `both`, or `neither`): cite that record and take the names from it.
- **`possible`** — the name matches and a field or an institution does too (`shared_fields`, `shared_institutions`), or there were no works to compare. The works neither confirm it nor rule it out, and most such candidates are namesakes.
- **`different`** — a different ORCID, works that fall outside the anchor's years, or no title, co-author, field, or institution in common. These are counted in `counts` and listed only with `--all`.
- **`unchecked`** — the name matches and `--max-candidates` stopped before its works were fetched. It is not a `different`: raise the cap.

`name_match` is `strong` when two or more words of the name match in full with none left over, and `weak` when the match runs through an initial or leaves a word unmatched. `shared_coauthors` makes a fragment at one name for a strong match and two for a weak one, and only when they are at least a fifth of the candidate's `coauthors_compared`: one name in common out of a hundred is chance. A record that lists both profiles is in `shared_works` and left out of the comparison.

What it costs, and where it stops:

- **Searches are the cost.** An author search costs ten times a works list, and a profile lookup is free. `budget` and the last stderr line give the searches, works lists, and network calls made and the credit balance before and after. A check on an uncommon name is about a dozen credits and a common one about 70 at the default caps, of the 1,000 a day OpenAlex gives a caller with no API key. Run it on the authors in question, not on every author of a bib.
- **Two caps.** `--max-searches` (default 3) limits the name searches, one per distinct spelling. `--max-candidates` (default 50) limits the works lists, taking full-name matches first and then the smallest profiles, since a fragment is typically a handful of works. The summary says what each cap left out.
- **A search returns at most 200 profiles.** For a very common name the summary reads `returned 200 of 6425 profiles: the candidates are incomplete`, and a fragment can be among the rest.
- **It searches by name.** A fragment filed under a misspelling that shares no two words with any of the anchor's spellings is not found.
- **Cache-first, stale results included.** Every request is cached by URL, so a re-run is free and an interrupted run resumes. A cached search does not learn that OpenAlex has since merged or split a profile: pass `--refresh` to fetch the searches and works lists again.

A verdict is evidence for an editor, like the profile itself: it never merges two profiles, and never rewrites a name by itself.

In a script, `find_fragments` returns the same report as a `FragmentReport`, and `OpenAlexClient.search_authors` and `author_works` are the two requests under it:

```python
from citefinder import OpenAlexClient, find_fragments

client = OpenAlexClient(cache_path="data/citefinder/paper/openalex/openalex.jsonl")
report = find_fragments(client, "A5012345678", max_candidates=25)
if report is not None:
    for candidate in report.candidates:
        result = candidate.assessment
        print(candidate.profile.id, result.verdict, result.reason)
```
