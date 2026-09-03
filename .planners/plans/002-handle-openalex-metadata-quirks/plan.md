---
id: 2
slug: handle-openalex-metadata-quirks
status: done
branch: feature/handle-openalex-metadata-quirks
created: 2026-05-01T10:30:45-07:00
concluded: 2026-09-02T22:53:22-07:00
pr: https://github.com/gitronald/citefinder/pull/46
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

## Log

- **2026-09-02T21:04:31-07:00** — Reviewed against 0.7.1a0 to decide whether
  the plan still applies. It does: none of the four items or the
  cross-cutting DOI-authority idea has shipped. Every commit touching
  `signals.py`, `verify.py`, `adapters.py`, or `tests/` since the plan was
  written is config, retry, skill-install, bib-table, or tilde work;
  `check_title`, `check_year`, `check_container`, `status_from_signals`, and
  the DOI branch of `verify_entry` are unchanged, and no regression fixture
  exists for any named entry.
  - Live OpenAlex records (fetched 2026-09-02) still show every quirk, and
    the current code yields the verdicts described above:
    - Fang2022 (`10.1145/3510003.3510121`): `display_name` and `title` are
      both `"This is damn slick!"`; there is no subtitle field.
      title=fail → `probable`.
    - Ohm2020 (`10.1007/978-3-030-52683-2_2`): primary source is "Lecture
      notes in computer science"; none of the record's 7 locations carries
      the booktitle. container=fail → `probable`.
    - messing2014 (`10.1177/0093650212466406`): `publication_year` 2012,
      `publication_date` 2012-12-31, while `biblio` reports volume 41
      issue 8 (the 2014 print volume). year=fail → `probable`.
    - `check_title("Influence", "Influence")` → pass.
  - Revisions to the plan, recorded here rather than rewriting the spec:
    1. **Promote the cross-cutting DOI-aware override to the primary fix.**
       It resolves #2, #3, and #4 in one change in `verify_entry`. Keep the
       single disagreement in `note` so the year-review flag survives.
       Guard: a DOI that resolves to a *related* work (same author, same
       year, different paper) fails only on title, so require the title
       verdict to be at least `unknown` before overriding, or lean on the
       containment check in (3).
    2. **Keep #1 as its own signal-layer change and extend it to candidate
       selection.** `verify_entry` picks the best search hit with the same
       `title_similarity` at `TITLE_MATCH_THRESHOLD = 0.55`, so a one-word
       bib title also selects the wrong candidate before signals run. The
       minimum-token rule must apply at both sites. The title-only OpenAlex
       search predates this plan and did not mitigate the case.
    3. **#2 has no adapter fix.** The Crossref subtitle join was already in
       `adapters.py` when this plan was written; OpenAlex truncates both
       `display_name` and `title`, so only the signal or status layer can
       help. A containment check is sharper than a lower threshold: when the
       source title's tokens are a strict subset of the bib title's, treat
       it as truncation (`unknown`), not disagreement.
    4. **Drop the year-fallback half of #4.** OpenAlex exposes no second
       year field to fall back to; only the wider tolerance or the DOI
       override remain. Plan 006 documented this quirk in the skill and
       README and framed a year mismatch as a review flag, which is what
       `probable` already means, so #4 is now partly a design choice. The
       DOI override with the disagreement kept in `note` preserves the
       flag.
    5. **Add a docs step: reconcile the report-reading guide.** The
       `method` × `status` guide shipped in 0.6.0 (skill body and README)
       says `method=doi` with `probable` is "a real defect — fix the
       entry." That is right for Crossref, which carries subtitles and
       container aliases, but wrong for OpenAlex, the default `verify`
       source: for the three records above it would truncate a title,
       replace a booktitle with a series name, or set a year the same
       skill's year guidance says not to cite. Update that guide alongside
       the code change, whichever way the status decision goes.
    6. **Fixtures.** Use the three live records above as the real-run
       fixtures the Notes call for; `cialdini2003influence` remains the
       fixture for #1.
- **2026-09-02T21:14:26-07:00** — Implemented on `feature/handle-openalex-metadata-quirks`
  (draft PR: https://github.com/gitronald/citefinder/pull/46).
  - `signals.py`: `MIN_TITLE_TOKENS = 3`, `title_tokens`, `is_short_title`.
    `check_title` downgrades a pass to `unknown` when the bib title has fewer
    than three words, and a fail to `unknown` when one title's tokens are a
    strict subset of the other's and the shorter side has at least three
    words. Revision 3 named only source-side truncation; a bib that omits the
    subtitle is the same phenomenon and failed the same way, so the rule is
    symmetric. A one-word subset does not count as truncation, so `Influence`
    vs `Influence: Science and Practice` still fails on the DOI path, which is
    the right review flag for a deficient bib title. Downgrades carry a `note`
    in the signal dict.
  - `verify.py`: on the DOI path, a `probable` verdict from exactly one
    non-title fail becomes `matched`, with the note prefixed `DOI resolved;`.
    Title fails and double fails are untouched (the related-work guard from
    revision 1). On the search path, a short bib title cannot select a
    candidate; the result is `unmatched` (or `skip-source`) with a "title too
    short" note and the hits left in `candidates`. Chose the gate over
    signal-based candidate selection for short titles to keep the rule one
    line at both sites; ranking short-title candidates by their other signals
    is a possible follow-up.
  - Year tolerance stays at ±1 (revision 4): the DOI override handles
    messing2014 and keeps the disagreement in the note.
  - Fixtures: Fang2022, Ohm2020, and messing2014 run their trimmed live
    OpenAlex records through `openalex_to_work` and `verify_entry` in
    `tests/test_verify.py`. Fang2022 is `matched` with no note (title
    `unknown` by truncation, three passes); the other two are `matched` with
    the disagreement noted. `cialdini2003influence` covers the short-title
    gate; signal-level tests cover the min-token and truncation rules.
  - Docs: the report-reading guide in the skill body and README splits
    `method=doi` into `mismatch`, `probable` (title disagrees or too few
    signals), and `matched` with a note, and adds the "title too short"
    `unmatched` row. CHANGELOG `[Unreleased]` records the behavior changes.
- **2026-09-02T22:53:46-07:00** — Review gate on PR #46 (medium): 7 confirmed,
  1 plausible; fixes in `1e0aaa8`.
  - **Review follow-up.**
    - Actioned: the DOI override required no confirming signal, so one fail
      plus three unknowns became `matched` while zero fails plus one pass
      stayed `probable`. Moved the rule into
      `status_from_signals(doi_resolved=True)` with a two-passes guard, and
      added tests at the reducer and `verify_entry` levels. The skip-source
      short-title note lost its `@etype:` and verify-via-URL framing;
      restructured the search-path tail so it keeps them, documented the
      `skip-source` variant in the README, skill body, and CHANGELOG, and
      added a test. Cleanups: one shared `_jaccard`, one `hit` binding, one
      tokenization; `is_short_title` was left without a consumer and removed
      (CHANGELOG updated).
    - Conscious no-ops: (1) a short title's `unknown` verdict lets a DOI
      record by a different author with matching year and venue stay
      `matched` with an author note. With the two-passes guard the override
      never beats the status the same signals would get with the
      disagreeing one unknown, and the note carries the disagreement.
      (2) Possessive apostrophes inflate the token count ("Kant's Ethics" is
      three tokens). That is pre-existing `normalize_title` behavior shared
      with every similarity score, so it stays.
  - Gate: `ruff check`, `ruff format --check`, `pyrefly check`, `pytest`
    (250 passed).

## Retrospective

- The cross-cutting DOI override was the right primary fix. One rule in the
  reducer resolved three of the four quirks and kept the year-review flag in
  the note, where the per-quirk threshold tweaks in the original spec would
  have loosened every source at once.
- The override started in `verify.py` and moved into `status_from_signals`
  at review, which is where it belonged: the fails and passes lists the
  reducer already builds are exactly what the guard needs, and the
  two-passes guard fell out of reading the existing "two passes is matched"
  rule next to it.
- A new "downgrade to unknown" rule interacts with every consumer of
  `unknown`; the short-title rule quietly weakened the override's title
  guard. Check each new verdict path against the status reduction table
  before shipping, not after.
- Real, trimmed API records as fixtures caught the truncation and
  series-name cases directly and read well in the tests. The fetch date on
  each record matters, since OpenAlex may fix the metadata later and the
  fixture then documents a historical quirk.
- Next time, run the review gate before opening the draft PR so the
  follow-up lands in the initial diff rather than as an addendum.
