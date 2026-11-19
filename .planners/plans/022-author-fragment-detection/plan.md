---
id: 22
slug: author-fragment-detection
status: active
branch: feature/022-author-fragment-detection
created: 2026-11-19T14:28:07-08:00
concluded:
pr:
---

# Find and assess fragmented OpenAlex author profiles

## Plan

### Problem

OpenAlex builds author profiles by automated disambiguation, so one person can
be split across several author IDs. Plan [[openalex-author-profiles]] (013)
wraps `/authors/{id}`, which takes an author ID or an ORCID: it can describe a
profile it is pointed at, and cannot find a second profile for the same person.

A manual check on one author during 013 found such a fragment, and showed both
why it matters and what evidence settles it:

- The author's main profile holds 53 works. A second profile holds one work,
  has no ORCID and no institutions, and carries the same three name tokens in a
  different order (`Q Doe Jane` for `Jane Q. Doe`).
- The fragment exists because OpenAlex holds a duplicate record of one
  conference paper: no DOI, no citations, every author's name inverted, and a
  new author ID minted for each of them. The co-authors are fragmented with it.
- A bib entry for that paper with no `doi` can match the duplicate through
  title search, and then carries the scrambled names and no DOI candidate.
- Name search alone is noisy. Three spellings of the name returned 51 profiles.
  One was the fragment. Topic and affiliation overlap did not separate it from
  namesakes; the duplicate title and the co-authors' names did.

This plan automates that check for any author, as a report an editor reads. It
never merges profiles and never rewrites a name.

### What the manual check measured

Recorded here because the caps and defaults below rest on it. One author, so
step 1 repeats it on more before anything is fixed.

| Query form | Profiles returned | Anchor | Fragment |
|---|---|---|---|
| preferred name, as the profile spells it | 3 | rank 1 | rank 3 |
| given name and family name, no middle initial | 17 | rank 1 | rank 16 |
| initials and family name | 34 | absent | absent |

- An author search cost `0.001` of the `0.10` daily budget an unauthenticated
  caller gets; a works list filtered by author cost `0.0001`. Single-profile
  lookups cost nothing. The whole check (three searches, eight works lists)
  used 38 of 1,000 credits.
- Seven other small profiles with a compatible name were other people. Their
  works were decades or fields away from the anchor's, with no shared title or
  co-author.

### Scope

- Two client methods: author search by name, and the works attached to an
  author ID.
- A pure assessment step: given the anchor profile, its works, a candidate
  profile, and the candidate's works, return a verdict and the evidence for it.
- A command that runs the whole check for one author.
- A free check inside `authors-from-cache`: two author IDs in one cache whose
  names are compatible.
- Skill, README, and changelog.

Out of scope:

- Merging profiles, or reporting them to OpenAlex.
- A title-first sweep: searching every one of the anchor's works by title for
  duplicate records. It would catch a fragment under a misspelling that name
  search misses, at one search per work, which is ten times the cost of a works
  list. Revisit after this lands if name-first proves too leaky.
- Running the full check over every author in a bib by default. At the costs
  above an unauthenticated caller can afford a few dozen authors a day.
- Crossref, which has no author records.

### Design

**Depends on 013.** `lookup_author`, `author_path`, `AuthorProfile`, and
`authors-from-cache` are on 013's branch. Start this after it merges.

**Client.** On `OpenAlexClient`:

- `search_authors(name, rows)` fetches `/authors?search=`. Results are full
  profile records, so no follow-up lookup is needed per candidate.
- `author_works(author_id, rows)` fetches `/works?filter=author.id:`, with a
  `select=` that keeps the fields the assessment reads (ID, DOI, title, year,
  type, authorships, venue), so a page stays small.

Both go through `CachedJsonClient._get`, so they are cached by URL, paced, and
retried like every other call. Declare the search page in `models.py`
(`OpenAlexAuthorSearchPage`) and route its key in `cache_drift`; the works list
already routes as `openalex-search`.

**Candidates.** Search on the anchor's preferred name, then on each alternative
spelling whose token set differs from one already searched, up to a cap. The
initials form is not searched: in the one measurement it returned neither the
anchor nor the fragment.

**Name gate, no requests.** Reduce a name to tokens: case-folded, punctuation
stripped, diacritics kept but compared NFC-normalized. A candidate passes when
its tokens map one to one onto the tokens of the anchor's preferred name or of
one alternative, in any order, where an initial matches a token starting with
that letter and at least one multi-letter token matches in full. So for
`Jane Q. Doe`: `Q Doe Jane` and `Doe, Jane` pass, `J. Doe` passes weakly,
`Jane R. Doe` and `John Doe` fail. Order-insensitive matching is the point: it
is what catches an inverted name.

**ORCID, no requests.** A candidate whose ORCID differs from the anchor's is
`different`. One that shares it is `likely-fragment` without further evidence.

**Works evidence, one request per surviving candidate.** Fetch the anchor's
works once and each candidate's works, then compare:

- **Duplicate title.** A candidate work whose title matches an anchor work's,
  using the title comparison `signals.py` already has.
- **Shared co-authors.** Co-author names on the candidate's works that pass the
  name gate against co-authors on the anchor's works. Compared by name, not by
  ID, because a duplicate record fragments the co-authors too.
- **Years.** Whether the candidate's works fall inside the span of the anchor's.

**Verdict.**

| Verdict | When |
|---|---|
| `likely-fragment` | same ORCID, or a duplicate title, or shared co-authors on a work inside the anchor's span |
| `possible` | passes the name gate, overlapping topics or institutions, nothing from the works either way |
| `different` | a different ORCID, or works that share no title or co-author and sit outside the anchor's span or fields |
| `unchecked` | passed the name gate, works not fetched because of the cap |

A name match with topic overlap never reaches `likely-fragment` by itself. A
candidate the cap skipped is reported as `unchecked`, never as `different`.

**Output.** JSON, like `author` and `isbn`: the anchor's summary, then each
candidate with its verdict and the evidence behind it, then counts of what the
name gate and the ORCID check ruled out. For a duplicate title, name both work
records and which one carries the DOI: that is the line an editor acts on, by
citing the record with the DOI.

**Budget.** The command prints how many searches and works lists it made and
the credit reading before and after, from the quota headers the client already
tracks. `--max-candidates` caps the works lists, smallest profiles first, since
a fragment is typically a handful of works.

**Free check in `authors-from-cache`.** With the profiles already fetched, flag
every pair of author IDs in the cache whose names pass the name gate against
each other. No requests. In a bib this is either a split or two namesakes, and
it is the editor's actual question: are these two bylines one person. The flag
points at the pair; the full command settles it.

### Implementation order

1. **Measure.** Repeat the manual check on several anchors: a common surname, a
   name with a diacritic, a multi-part family name, and one with many works.
   Record per query form what came back, what the name gate kept, and the cost.
   Fix the query cap, the candidate cap, and the co-author threshold from that,
   and log the numbers. No code beyond a scratch script.
2. `search_authors` and `author_works`, the search-page model, drift routing,
   and tests.
3. The name gate and the assessment signals as pure functions, with tests on
   synthetic cases: token permutation, initials, a conflicting initial, a
   different ORCID, a duplicate title, shared co-authors, disjoint years.
4. The command, with tests against a mocked session.
5. The same-name flag in `authors-from-cache`.
6. Skill: extend the "Author profiles" section of the `given-names` reference
   with when to run this and how to read each verdict, and bump the body's
   version. README and changelog.

Test fixtures are synthetic records of the surveyed shape. No real person's
profile is committed.

### Open questions

1. **Command name.** `author-fragments <id-or-orcid>` is the working name.
2. **Anchors with more works than one page.** Whether to page through all of
   them or compare against the most recent page and say so in the output.
3. **Is `possible` worth printing by default?** For a common name it may be most
   of the output. It could sit behind a flag, with the count always shown.
4. **How `authors-from-cache` presents the flag.** A fifth column changes a
   table 013 just defined; a second block on stderr is not greppable.
5. **Stale negatives.** A cached search page keeps saying "no fragment" after
   OpenAlex creates one, and "fragment" after it merges one. Either document
   deleting the line, as the cached-404 guidance does, or add a way to refetch.
6. **Shared co-authors without a duplicate title.** Whether one shared name is
   enough for `likely-fragment` or it takes two. Step 1 should show how often
   namesakes share a co-author name.

## Log

- **2026-11-19T14:48:50-08:00** — Activated.
  - Branch: `feature/022-author-fragment-detection`
  - Base: `dev` at `599b248`
  - Worktree: `.worktrees/022-author-fragment-detection`
  - PR: pending
- **2026-11-19T15:00:13-08:00** — Step 1, measured: seven anchors, unauthenticated, through a scratch script and a scratch cache (neither kept). Anchors came from the author IDs on an existing local works cache: a name with a diacritic, a common Western surname, a three-token name whose accent the profile's preferred spelling lacks, a very common two-token name, a name with a dotless i, a multi-part family name with particles, and one with 1,260 works.
  - Cost, from the quota headers: an author search is 10 credits of the 1,000 a day, a works-list page is 1, a profile lookup is 0. `x-ratelimit-credits-used` is per request, so the reading before a run is the first response's remaining plus its own cost.
  - Query forms. OpenAlex matches every query token and folds diacritics, so a shorter query returns a superset: for the three-token name the preferred spelling returned 1 profile (the anchor) and the two multi-letter tokens returned 2, the second a real fragment. Decision: a query is the multi-letter tokens of a spelling, fewest tokens first, skipping any set that contains one already searched. That left 1, 2, 1, 1, 2, 1, and 4 queries per anchor, so the cap is 3.
  - The initials form returned 7, 1,123, and 1 profiles on three anchors and found no fragment the other forms missed. It stays unsearched, as planned.
  - A family-name-only spelling among the alternatives (a bare `van der Meer`, a bare `De Vos`) became a query that returned 1,287 and 1,662 profiles and passed namesakes through the gate. Decision: a query, and a strong name match, needs a token outside the family part of the preferred name.
  - Diacritics. The fragment above carries the accent and the anchor's preferred name does not, so a gate that kept diacritics would have dropped it. Decision: the gate folds them, departing from the plan's "diacritics kept".
  - A candidate is gated on its preferred name and its alternatives: a one-work profile displayed with an initial listed the anchor's full name among its alternatives.
  - Candidates left after the ORCID check, per anchor: 4, 121, 1, 5 (of a search truncated at 200 of 6,425), 37, 14, 44. The ORCID check alone ruled out 195 of 200 for the very common name. Fragments found had 1 to 9 works. Decision: check candidates with a full-name match first, then smallest first, default cap 50.
  - Shared co-authors (open question 6). Of 105 namesakes with a full-name match and no duplicate title, none shared a co-author's full name with the anchor. The two false positives were candidates matched on the family name or an initial only, each sharing one co-author name with an anchor of 122 and 1,237 works. Sixteen profiles that are plainly the anchor shared 1 to 18. Decision: one shared co-author with a full-name match, two with a weaker one.
  - `possible` was 11 of 135 checked candidates and `different` most of the rest, so `possible` prints by default and `different` is a count (open question 3).
  - Size. A full profile is about 9 KB and a work about 8 KB, nearly all of it the affiliation strings in `authorships`, which `select=` cannot trim: the scratch cache reached 37 MB. Decision: the author search selects the six summary fields, the works list selects the fields compared, and each works page is trimmed to them before it is cached. A candidate's fields and institutions are then read from its works, not from profile topics.
  - An anchor's works are all paged (open question 2): 1,237 works were 7 pages, 7 credits, less than one search.
