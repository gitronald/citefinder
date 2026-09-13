# Given names and diacritics

The four verify signals never look at given names. `author: pass` means the first author's **surname** matched the record and nothing more — a given name that is misspelled, abbreviated, or missing a diacritic against the record still lands in `matched`. Checking given names is a separate, offline pass over the cache that `verify` already wrote.

Which cached field answers which question:

- *What the byline printed* → Crossref `author[i].given` + `author[i].family`, or OpenAlex `authorships[i].raw_author_name`. Both reproduce the publisher's deposit, diacritics included or dropped as deposited.
- *How the author's name is canonically spelled* → OpenAlex `authorships[i].author.display_name`, the author's profile name. This is the field that carries a diacritic the deposit dropped: for a 1991 law-review article the bib and both bylines read `Kimberle`, and only the embedded `display_name` reads `Kimberlé W. Crenshaw`.

The record shapes are declared in `citefinder.models` (`CrossrefWork`, `OpenAlexWork`, `CrossrefAuthor`, `OpenAlexAuthorship`, ...) — read that module before guessing a key, and run `{cli} drift <cache.jsonl>` to see what the cached records carry that the model does not.

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
