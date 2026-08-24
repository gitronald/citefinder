---
id: 6
slug: update-use-citefinder-skill
status: draft
branch: feature/update-use-citefinder-skill
created: 2026-08-24T08:20:14-07:00
concluded:
pr:
---

# Port downstream improvements into the use-citefinder skill

## Plan

### Problem

The `use-citefinder` skill (`.claude/skills/use-citefinder/SKILL.md`) gets
hand-copied into consuming repos, and one downstream copy accumulated two
genuinely useful sections that never made it back here. A comparison audit
(2026-08) found the downstream copy otherwise *behind* this one (stale CLI
shapes, pre-`config.toml` credential docs), so this repo's version is the
right base — but those two sections are general-purpose knowledge that
belongs in the canonical skill. Keeping copies in sync mechanically is a
separate effort ([007](../007-bundle-skill-in-package/plan.md)); this plan
just settles the content.

### Change 1 — year-mismatch guidance (Crossref vs OpenAlex)

Add a subsection to the existing "OpenAlex fallback" section (after the
schema table and `reconstruct_abstract` helper): **"Year mismatches between
Crossref and OpenAlex — flag and prefer the final printed record."**

Content to port:

- Why they disagree: Crossref's `published-print` tracks the issue/volume
  year; OpenAlex's `publication_year` often collapses to the online-first
  or precursor date. Treat any mismatch as something to flag for review,
  then default to the final printed record (journal volume year; for books
  the publisher's first-published edition year).
- The two recurring patterns, each with its resolution:
  1. **Online-first vs volume year** (journal articles) — DOI minted for
     online-first (e.g. 2016-10), printed later in a volume (2018-09).
     Crossref splits it cleanly; OpenAlex reports 2016. Cite 2018.
  2. **Precursor work vs published edition** (books) — the same DOI
     surfaces in OpenAlex as a `dissertation` dated 2020 but in Crossref
     as a `monograph` issued 2022. Cite 2022.
- A quick mismatch-check snippet comparing
  `(work_cr.get("published-print") or work_cr.get("issued"))` year against
  `work_oa["publication_year"]`.
- The OpenAlex-only tell: a `type` of `dissertation` or `posted-content`
  next to a journal/monograph DOI means a precursor record, not the
  cite-target.

This complements the existing one-line caveat in the verify section
("sometimes... returns preprint years instead of publication years") —
leave that line in place and let it stay brief; the new subsection is the
full treatment.

### Change 2 — side-by-side `bib_to_table` terminal rendering helper

Add a section before "When citefinder isn't enough": **"Inspecting
`bib_to_table` output side-by-side in the terminal."**

Content to port: polars's default rendering wraps long values mid-string
(bad for URLs/DOIs); a short dynamic-width plain-text helper that renders
2–3 chosen fields with each column expanded to its longest value, plus the
guidance to keep it a one-off rendering snippet rather than promoting it
into a script. Port the code block as-is, adjusting the example path to a
generic `refs.bib`.

### Adaptation rules for the port

- Keep this skill's conventions, not the downstream copy's: tool-default
  cache paths (`~/.cache/citefinder/...`), placeholder emails
  (`you@example.com`), current CLI shapes (top-level = OpenAlex,
  `crossref` subcommand).
- Downstream repo-specific material (local cache-path conventions, storage
  mount caveats) stays downstream — do not port it.

### Changelog

None. The skill file is repo content, not part of the wheel — it becomes a
packaged, changelog-worthy artifact in
[007](../007-bundle-skill-in-package/plan.md).

### Implementation order

1. Add the year-mismatch subsection.
2. Add the `bib_to_table` rendering-helper section.
3. Read the full skill top to bottom for flow/consistency with the two
   insertions.
