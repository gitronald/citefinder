---
id: 12
slug: verify-given-name-check
status: draft
branch:
created: 2026-09-05T15:54:41-07:00
concluded:
pr:
---

# Surface given-name and diacritic differences from cached author records

## Plan

### Problem

The verify pipeline compares authors on the **first author's surname only**
(`signals.check_author`, fed by `Work.first_author_surname` from
`adapters.crossref_to_work` / `openalex_to_work`). Given names never enter the
comparison, so a bib entry whose given name is misspelled, missing a diacritic,
or abbreviated against the record still reports `author: pass` and lands in
`matched`. The downstream surname audit in a consumer's `use-citefinder` workflow
has the same blind spot by design: it compares `family` only.

The data to catch this is **already on disk** after any verify run:

- Crossref work records carry `author[i].given` / `author[i].family` per author.
- OpenAlex work records carry, per authorship, both `raw_author_name` (the byline
  as printed on that work) and `author.display_name` (the author's canonical
  profile name, which is where a diacritic the byline dropped tends to survive).

Example of the shape (a public 1991 law-review article): the bib and both
bylines read `Kimberle`; only the embedded OpenAlex `author.display_name` reads
`Kimberlé W. Crenshaw`. No current check reads that field, so no check could
have flagged it.

### Scope

Skill-side first. This plan changes `citefinder/prompts/skill.md` (the bundled
`use-citefinder` skill) so an editor running the audit knows the given-name
check exists, which cached fields settle it, and how to run it offline. A small
code option is listed as a follow-up, not as part of this plan.

Out of scope: fetching author profiles from the `/authors/{id}` endpoint (see
[[openalex-author-profiles]]), and any change to how `check_author` scores the
`author` signal.

### Approach

1. **Add a "Given names and diacritics" subsection** to the verify section of
   `skill.md`, next to the existing "Which fields carry the name split" note.
   It should say, in this order:
   - The four signals never look at given names; `author: pass` means the
     surname matched and nothing more.
   - Which cached field answers which question:
     - *what the byline printed* → Crossref `given` + `family`, or OpenAlex
       `raw_author_name`;
     - *how the author's name is canonically spelled* → OpenAlex
       `authorships[i].author.display_name`. This is the field that carries a
       diacritic the publisher's deposit dropped.
   - The two known traps that already appear elsewhere in the skill and apply
     here verbatim: the cache rows are wrapped (`{key, value, ts}`), and bib
     values are TeX-escaped (`Kimberl{\'e}`) while API values are Unicode, so
     de-escape before comparing.
   - A disagreement is a **byline check, never an auto-fix**. A record that
     lacks a diacritic the author uses today is common for older articles; the
     editor decides which form the citation carries.

2. **Provide the offline recipe** as a runnable snippet: read the paper's
   `openalex.jsonl` and `crossref.jsonl`, join to the bib by DOI, and print
   every author position where the bib given name differs from either the
   Crossref `given` or the OpenAlex `display_name` given-name portion after
   Unicode normalization (`NFC`) and de-escaping. Use `bib_to_table` for the
   bib side and `parse_single_name_into_parts` for the OpenAlex string, as
   `adapters._openalex_surname` already does for the surname. Print
   `key, position, bib, crossref, openalex` so the editor can eyeball it.

3. **State the "validate a zero" rule for this recipe** — re-run it against a
   copy of the bib with one given name deliberately altered and confirm it
   flags that one, before trusting an empty result. This mirrors the trap list
   already in the skill.

4. **Re-materialize the stub.** `citefinder install --local --check` reports
   drift after the prompt changes; consumers re-run `citefinder install`.

### Follow-up (optional, separate plan if wanted)

A `given-names` note on the verify report: in `verify_entry`, when the entry has
a DOI hit, compare each bib author's given name to the record and append a
`note` line (not a signal, so status buckets do not move). This would surface
the finding in `summary.md` without an ad-hoc script, at the cost of extending
`Work` with a full author list rather than `first_author_surname` alone.

### Implementation order

1. Write the skill subsection and recipe.
2. Run the recipe against a real cache to confirm it flags a known diacritic
   difference and stays quiet on a matched entry.
3. Bump the skill's version marker if the install stub tracks one; run
   `citefinder install --local --check` in a consumer to confirm drift is
   reported and resolves on reinstall.
4. Changelog entry under `[Unreleased]`.
