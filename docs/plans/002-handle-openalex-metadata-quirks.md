---
status: draft
branch:
created: 2026-05-01T10:30:45-07:00
completed:
pr:
---

# Reduce false positives and false negatives in signal checks

## Plan

A side-by-side run of `citefinder verify` against Crossref and OpenAlex on
shen2026social/refs.bib (54 entries) surfaced four signal-check weaknesses
that produce wrong verdicts. Each is independent and fixable; bundling them
because they share the same "find a regression test, tweak the threshold,
re-run" shape.

### 1. Short bib titles produce false `title=pass`

`check_title` returns `pass` when Jaccard similarity ≥ 0.85. A bib title
with a single distinctive word ("Influence") trivially scores 1.0 against
any candidate that contains that word, even if the candidate is unrelated.
Hit on shen2026social/cialdini2003influence: OpenAlex returned an unrelated
1985 paper that shared the word "Influence" → title.pass → status was
`probable` instead of `mismatch`.

**Fix idea:** require a minimum bib-title token count (probably 3) before
title.pass is allowed. Below that, downgrade pass to `unknown` so the
status reduction can't lean on it. Add `cialdini2003influence` as a
regression fixture.

### 2. OpenAlex truncates colon-separated titles

OpenAlex's `display_name`/`title` fields drop everything after the colon for
some records. Hit on Fang2022: bib has `"This Is Damn Slick!": Estimating
the Impact of Tweets...`, OpenAlex stores only `"This is damn slick!"`.
Verdict was title.fail despite identical DOI.

**Fix idea:** when both records share a DOI, weight the title check more
forgivingly (allow lower threshold, or skip it entirely on a DOI hit since
DOI-resolved records are by construction the same work). Verify against
the Fang2022 fixture.

### 3. OpenAlex returns series name without booktitle

For proceedings papers indexed via LNCS-style series, OpenAlex's
`primary_location.source.display_name` is the series ("Lecture notes in
computer science") and the booktitle isn't stored. Hit on Ohm2020:
container.fail despite identical DOI.

**Fix idea:** same as #2 — DOI-resolved records shouldn't be punished for
a single missing alias. Alternatively, when the source name contains
"lecture notes" or "proceedings" generically, treat as `unknown` not `fail`.

### 4. OpenAlex returns preprint year instead of publication year

`messing2014selective` has bib year 2014, OpenAlex returns 2012 (preprint),
diff = 2 → year.fail. The current ±1 tolerance handles preprint-vs-published
drift but not preprint-vs-late-published drift.

**Fix idea:** widen tolerance to ±2 years, OR check `publication_year` and
fall back to a later one if both are present. Verify with messing2014.

### Cross-cutting: weight DOI-resolved records differently

Several of the above bugs only trigger when a DOI matches but a single
metadata signal disagrees. Worth considering: when `method == "doi"`, a
single signal disagreement should be reported but never bump the status
out of `matched` — the DOI is the authoritative identity claim.

This is a one-line change in `verify_entry` (after the DOI lookup
succeeds, override `status_from_signals` with `MATCHED` and put the
disagreement in `note`). Decide whether to do this generally or fix each
quirk individually.

## Notes

- All fixtures should come from real bib runs, not synthetic — the goal
  is to capture the *kinds* of metadata loss real sources exhibit.
- The signal layer (`citefinder/signals.py`) is the right place for #1.
  Cases #2-#4 can live there too if implemented as threshold tweaks, or
  in `verify.py` if implemented as DOI-aware status overrides.
