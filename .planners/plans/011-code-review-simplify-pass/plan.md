---
id: 11
slug: code-review-simplify-pass
status: active
branch: feature/code-review-simplify-pass
created: 2026-09-04T19:18:10-07:00
concluded:
pr: https://github.com/gitronald/citefinder/pull/50
---

# Review and simplify the whole package at max effort

## Plan

### Why

Every plan so far (000–010) added or reshaped one feature, and four of the
modules were absorbed from external scripts rather than written in place. The
package has never had a whole-tree review: the only reviews have been per-PR
diffs, which by construction cannot see duplication *across* PRs or dead code
left behind when a later plan changed a module's role. With 0.9.0 shipped and
the tree clean, this is the cheap moment to do one full pass before the next
feature lands on top.

Two goals, in this order:

1. **Correctness** — find real bugs (wrong conditions, dropped guards,
   encoding/normalization mismatches, resource handling, broken callers) and
   fix them at the source with a regression test each.
2. **Simplicity** — remove duplication, dead branches, and needless
   indirection without changing behavior, the CLI's output, or the public API.

"Max effort" means the review runs at the top level the `/code-review` skill
defines (`high`: all four finder briefs, a 25-candidate cap, up to 10
verifiers, plus the gap sweep), and it runs once **per module group** rather
than once over the whole tree, so each finder works on a diff it can actually
hold. That is roughly 4 finders + up to 10 verifiers per pass, three passes,
all on `sonnet`/`haiku` per the subagent-model rule. The budget is deliberate;
do not trim it to save tokens.

### Scope

In scope: every module under `citefinder/`, the bundled skill body
`citefinder/prompts/skill.md`, and `tests/`. The generated stub
`.claude/skills/use-citefinder/SKILL.md` is out of scope (regenerate it with
`citefinder install --local` only if the frontmatter changes).

Out of scope, even if the review suggests them: new features, dependency
upgrades, changes to the JSONL cache format, renaming or removing anything
exported from `citefinder/__init__.py`, and changing CLI flag names or output
formats. A simplification that needs any of those is logged as a follow-up
plan, not done here.

### Review passes

The `/code-review` skill scopes on a diff. For a whole-tree target, produce
the diff against the empty tree so every line shows as added:

```bash
git diff $(git hash-object -t tree /dev/null) HEAD -- <paths>
```

Run three passes, each at `high`, each with its own fact sheet, in this order
(inner layers first so a finding in the HTTP layer is known before the
pipeline that depends on it is reviewed):

| Pass | Group | Paths |
|---|---|---|
| A | HTTP + cache layer | `_base.py`, `cache.py`, `client.py`, `openalex.py`, `tests/test_cache.py`, `tests/test_client.py`, `tests/test_openalex.py`, `tests/test_retry.py` |
| B | Verification pipeline | `bib.py`, `signals.py`, `adapters.py`, `verify.py`, `tests/test_bib.py`, `tests/test_signals.py`, `tests/test_adapters.py`, `tests/test_verify.py` |
| C | Surfaces | `cli.py`, `config.py`, `install.py`, `bib_table.py`, `__init__.py`, `prompts/skill.md`, `tests/test_config.py`, `tests/test_install.py`, `tests/test_bib_table.py`, `tests/conftest.py` |

Each pass reports through `ReportFindings` and the findings are copied into
this plan's Log as `file:line — summary — verdict`, so the record survives the
session. Findings are **not** fixed during the pass; all three passes finish
first, then fixes proceed in severity order across the pooled list. A finding
in one group that implicates another (e.g. an adapter quirk that the CLI
compensates for) is fixed once, at the source.

### Simplify pass

After the confirmed bugs are fixed, run `/simplify` over the accumulated
diff, then take a targeted pass over the seeds below. Each seed is a
*candidate* from a first inventory, to be verified against the code, not a
commitment:

1. **`strip_braces` is defined three times** with identical bodies:
   `bib.strip_braces`, `signals._strip_braces`, and `adapters._strip_braces`.
   Keep one (`bib.py` already exports it) and import it from the other two.
   Check whether `signals.normalize_title` and `adapters._openalex_surname`
   want the same normalization or only look alike.
2. **The top-level `doi`/`search` commands and the `crossref doi`/`crossref
   search` subcommands** in `cli.py` repeat the same build-client → look up →
   not-found exit → emit sequence. A shared helper taking the client and the
   lookup callable would collapse four bodies to one; check that Typer's
   option wiring survives the extraction.
3. **`cli.py` is 828 lines** and carries config-file loading
   (`_load_configs`, `_apply_config`) alongside the commands. CLAUDE.md
   documents `config.py` as the config module but places loading in the CLI.
   Decide whether loading belongs in `config.py` with the CLI calling one
   function; move it only if the result is smaller and clearer, and update the
   CLAUDE.md package table if anything moves.
4. **The `verify` command body** (`cli.py`, roughly 120 lines) mixes argument
   resolution, source construction, the per-entry loop, and result writing.
   Look for a seam that lets the orchestration live in `verify.py` next to
   `verify_entry`, leaving the command as argument handling.
5. **Test fixtures.** `tests/test_config.py` and `tests/test_install.py`
   are the two largest files in the repo. Look for repeated
   setup that belongs in `conftest.py`, and for tests that assert on the
   same behavior from two angles.
6. **Retry and pacing knobs** are resolved in `cli.py` (`_checked_knob`,
   `_source_client_kwargs`, `_env_number`) but consumed in `_base.py`. Check
   whether validation is duplicated on both sides.

Every simplification must leave `uv run pytest` green with no test edited
except to relocate a helper. If a simplification needs a test change beyond
an import path, it is changing behavior and belongs in the bug list or out
of scope.

### Fix policy

- Bugs: fix at the source (never a compensating patch at the call site) with
  a paired regression test in the matching `tests/test_<module>.py`.
- Simplifications: no new tests; existing tests are the guard.
- One commit per finding or per closely related group, so a wrong fix can be
  reverted alone.
- A finding judged a false positive after fixing starts is reverted and logged
  as `no_change_needed` with the reason.
- User-visible fixes get a line under `[Unreleased]` in `CHANGELOG.md`.
  Pure simplifications do not.

### Check gate

The gate is what the repo already runs, executed before the first change
(to record a baseline) and after every commit:

```bash
uv run pytest
uv run pre-commit run --all-files    # ruff format, ruff, pyrefly, planners validate
uv run citefinder install --check    # skill stub drift
```

CI runs the same lint + type check + test matrix on Python 3.11–3.14; the PR
does not merge until the matrix is green.

### Implementation order

1. Branch `feature/code-review-simplify-pass` from `dev` in a worktree; run
   the gate on the untouched tree and record test count and timing in the Log.
2. Review pass A, then B, then C. After each, append the findings to the Log.
3. Fix confirmed bugs in severity order, one commit each, gate after each.
4. `/simplify` over the accumulated diff, then the seeded candidates above,
   one commit each, gate after each.
5. Update `CHANGELOG.md` `[Unreleased]` and the CLAUDE.md package table if
   any responsibility moved between modules.
6. Open the PR against `dev`; `/planners close` runs its own review gate on
   the PR diff, which doubles as a check that the fixes themselves are clean.

### Deliverables

- A Log in this plan listing every finding with its verdict and outcome,
  including the ones skipped and why.
- A PR against `dev` with one commit per finding.
- Any out-of-scope work the review surfaced, written up as follow-up plans.

## Log

### 2026-09-04 — baseline

Branch `feature/code-review-simplify-pass` created in a worktree off `dev`
at the activation commit. Gate on the untouched tree:

- `uv run pytest`: 255 passed in 1.20s (1.7s wall).
- `uv run pre-commit run --all-files`: ruff format, ruff, pyrefly, planners
  validate all pass.
- `uv run citefinder install --check`: skill stub ok.

Tree size: 6463 lines across `citefinder/` and `tests/`. Largest files:
`cli.py` 828, `tests/test_config.py` 722, `tests/test_install.py` 553,
`tests/test_retry.py` 429, `tests/test_verify.py` 428.

### 2026-09-04 — pass A findings (HTTP + cache layer)

`/code-review high` over `_base.py`, `cache.py`, `client.py`, `openalex.py`
and their tests: 4 finders, 28 raw candidates, 22 after dedup, 5 verifiers,
17 confirmed + 1 plausible from the gap sweep, 5 rejected.

| # | Location | Finding | Verdict |
|---|---|---|---|
| A03 | `cache.py:35` | `_replay` has no error handling; one truncated trailing line aborts every later `JsonlCache` construction and orphans all prior records | CONFIRMED |
| A01 | `client.py:31` | DOI interpolated raw into the URL path; `#` and `?` truncate or reshape the request while the cache key keeps the full DOI | CONFIRMED |
| A02 | `openalex.py:150` | Same raw DOI interpolation in `/works/doi:{doi}` | CONFIRMED |
| A04 | `cache.py:45` | `put()` updates `_store` before `json.dumps`; a non-serialisable value leaves memory and disk diverged (public API only) | CONFIRMED |
| A07 | `openalex.py:90` | `_normalize_title_query` duplicates `bib.build_title_query`; `verify.Source.search` normalizes every OpenAlex title twice | CONFIRMED |
| A09 | `_base.py:146` | Knob validation predicate and message text duplicated in `cli._checked_knob` | CONFIRMED |
| A10 | `openalex.py:58` | `_strip_mailto` imported only to sit in `__all__`; nothing in the module calls it | CONFIRMED |
| A12 | `openalex.py:40` | `API_KEY_ENV_VAR` exported for reuse but `cli.py:185` and `config.py:52` hardcode the literal | CONFIRMED |
| A08 | `openalex.py:108` | `OpenAlexClient.__init__` redeclares 12 base params; `**kwargs` would pass pyrefly but lose kwarg-name checking | CONFIRMED (trade-off) |
| A15 | `_base.py:89` | tz-naive `Retry-After` date branch never exercised; reachable with an RFC-valid `-0000` date | CONFIRMED |
| A14 | `_base.py:84` | `Retry-After: nan` untested (only `inf`) | CONFIRMED |
| A16 | `_base.py:59` | `_strip_mailto` with mailto as the only param untested | CONFIRMED |
| A20 | `tests/test_client.py:33`, `tests/test_openalex.py:44` | `test_lookup_doi_404_returns_none` is a strict subset of `test_404_is_cached` in both files | CONFIRMED |
| A21 | `tests/conftest.py:20` | `mock_response.headers` is a plain dict, not `CaseInsensitiveDict`; inert today | CONFIRMED |
| A06 | `_base.py:195` | `2**attempt` overflows before the `max_wait` cap at attempt 1024; needs `--max-retries >= 1025` and ~17 h of 429s | CONFIRMED (impractical) |
| A17 | `openalex.py:84` | `reconstruct_abstract` mishandles non-list index values; unreachable from real OpenAlex payloads | CONFIRMED (malformed input only) |
| A18 | `client.py:52` | `:03d` padding gives `-01` for negatives and 4 digits above 999; library-only, CLI never passes a negative int | CONFIRMED (low) |
| G1 | `_base.py:228` | Case variants of one DOI are separate cache entries and separate requests | PLAUSIBLE (gap sweep) |

Rejected: `_base.py:210` no retry on `ConnectionError`/`Timeout` (documented
scope); `client.py:38` no `rows` guard (the API's contract; a 400 raises and
is never cached); `openalex.py:152` `search()` dead (README documents it as
library API); `openalex.py:152` shared search helper (URL building and
result nesting differ; not a net simplification); `_base.py:59` blank-value
cache-key collision (no two real requests collide; re-encoding is
deterministic).

### 2026-09-04 — pass B findings (verification pipeline)

`/code-review high` over `bib.py`, `signals.py`, `adapters.py`, `verify.py`
and their tests: 4 finders, 35 raw candidates, 25 after dedup, 5 verifiers,
21 verified (19 confirmed, 2 plausible incl. one from the gap sweep), 4
rejected.

| # | Location | Finding | Verdict |
|---|---|---|---|
| B16 | `verify.py:122` | `citation_from_entry` runs outside any try/except; `author = {Smith, Jane,}` raises `InvalidNameError` and the CLI loop (no guard) aborts the whole run | CONFIRMED |
| B01 | `bib.py:35` | `parse_entries` ignores `library.failed_blocks`; duplicate field key, duplicate entry key, or unterminated block silently drops the entry | CONFIRMED |
| B06 | `signals.py:106` | `normalize_title` deletes non-Latin scripts; identical CJK/Cyrillic titles score 0.0 and fail | CONFIRMED |
| B04 | `signals.py:223` | `check_author` passes on a shared von particle alone (`van de Rijt` vs `van der Berg`) | CONFIRMED |
| B05 | `signals.py:249` | `container_similarity` has no one-to-one pairing; one short token prefix-matches many (`Data Database Dataset` vs `Data` = 1.0) | CONFIRMED |
| B17 | `verify.py:107` | Crossref `candidate_title` drops the subtitle the DOI path keeps; split-title hits score 0.235 and go UNMATCHED | CONFIRMED |
| B12 | `adapters.py:61` | Crossref corporate authors use `name`, not `family`; author signal stays unknown | CONFIRMED |
| B24 | `verify.py:210` | `matched_doi` is `""` on the search path but `None` on the DOI path | CONFIRMED |
| B18 | `verify.py:220` | Skip-source note says "signals disagree" when the status came from too few confirmations | CONFIRMED |
| B07 | `signals.py:102` | `strip_braces` defined three times (bib, signals, adapters); signals is the leaf | CONFIRMED |
| B08 | `signals.py:39` | `Status.header` has no consumer; the docstring's `render_summary` never existed | CONFIRMED |
| B20 | `verify.py:57` | `Result.method` comment lists `"skipped"`, never assigned | CONFIRMED |
| B14 | `adapters.py:6` | Docstring says a new source needs only an adapter; `Source` branches on the name in four methods | CONFIRMED |
| B26 | `tests/test_verify.py:32` | No test runs the real `Source` dispatch to completion; CLI verify tests land in `error` via the `isinstance` assert | CONFIRMED |
| B19 | `verify.py:196` | `SKIP_SOURCE_TYPES` checked three times; the skip-type "no plausible hit" combination is unreachable | CONFIRMED |
| B22 | `verify.py:183` | Bib title re-tokenised per candidate and once more for the word count | CONFIRMED |
| B15 | `adapters.py:21` | `date-parts` `[[None]]`/`[]` guard untested | CONFIRMED |
| B10 | `signals.py:263` | `check_container` with no candidates untested | CONFIRMED |
| B03 | `bib.py:105` | `first_author_surname` parsed twice per Crossref search entry (~5 µs) | CONFIRMED (negligible) |
| B21 | `verify.py:83` | `Source` string dispatch in four methods; likely right-sized given the public constructor | PLAUSIBLE |
| B27 | `README.md:257` | Status list order differs from the enum; nothing renders in enum order | CONFIRMED (cosmetic) |
| GB1 | `verify.py:121` | URL-form or `doi:`-prefixed bib DOI is sent untouched and 404s | PLAUSIBLE (gap sweep) |

Rejected: `bib.py:57` leading `and` folded into the surname (bibtexparser's
documented behaviour on malformed input); `signals.py:192` `2020a`/`2020.0`
years (not realistic `.bib` values; the table round trip keeps strings);
`adapters.py:100` capitalised particle surname mismatch (token overlap passes
every constructible pair); `verify.py:209` `assert work is not None` (the
adapters return `None` only for `None` input).

Context surfaced by a verifier: `origin/claude/max-full-package-review-38smq5`
is an unmerged single-commit branch from 2026-06-20 (based on 0.4.2) that
proposed overlapping fixes — reserved-column collision in `bib_to_table`,
stricter `table_to_bib` validation, string `publication_year` coercion,
single-token prefix matching disabled in `container_similarity`, `doi = {}`
as `None`, and OpenAlex title normalisation moved to the client. It is stale
against `dev` and is not merged here; each proposal is weighed on its own in
the fix phase.

### 2026-09-04 — pass C findings (surfaces)

`/code-review high` over `cli.py`, `config.py`, `install.py`, `bib_table.py`,
`__init__.py`, `prompts/skill.md` and their tests: 4 finders, 42 raw
candidates, 23 after dedup, 5 verifiers, 20 verified (17 confirmed, 3
plausible), 3 rejected.

| # | Location | Finding | Verdict |
|---|---|---|---|
| C09 | `prompts/skill.md:24` | Skill body tells the agent to load a skill that exists only in a private repo; hard rule for a public package | CONFIRMED |
| C01 | `cli.py:534` | `verify --source crossref` never passes `mailto` to `CrossrefClient`; flag, env, and config value all silently dropped | CONFIRMED |
| C06 | `bib_table.py:43` | A bib field named `key` or `entry_type` is silently overwritten by the id columns and cannot round-trip | CONFIRMED |
| C07 | `bib_table.py:75` | `table_to_bib` emits malformed BibTeX for a value with an unbalanced `}`; later fields vanish on re-parse; reachable from a quote-delimited bib value | CONFIRMED |
| C03 | `cli.py:429` | `bib-to-table --fields key,title` raises polars `DuplicateError` | CONFIRMED |
| C04 | `cli.py:120` | An empty env var counts as set in `_apply_config` but as unset everywhere else, so the config value is lost and nothing wins | CONFIRMED |
| C02 | `cli.py:482` | `--source` is a `str`, so `case_sensitive=False` is a no-op; `--source OpenAlex` exits 2 | CONFIRMED |
| C05 | `cli.py:49` | `_anchor`'s `expanduser` raises `RuntimeError` for `~nosuchuser`; from a config file that crashes `import citefinder.cli` | CONFIRMED |
| C11 | `install.py:321` | `install.check()` called only by its own tests; the CLI re-implements it; docstring claims otherwise | CONFIRMED |
| C10 | `bib_table.py:3` | "Designed for the editorial workflow" implies one specific consumer | CONFIRMED |
| C15 | `bib_table.py:88` | Manual field union/fill duplicates `pl.DataFrame(list[dict])`; needs `infer_schema_length=None` or a late-appearing field is dropped | CONFIRMED |
| C12 | `cli.py:371` | `doi`, `crossref doi`, `crossref chapter` repeat the not-found exit block | CONFIRMED |
| C21 | `cli.py:378` | `search`, `crossref search`, `crossref chapter`, `bib-to-table`, `table-to-bib` bodies and several error branches are never exercised (cli.py 85% covered) | CONFIRMED |
| C22 | `install.py:169` | `_frontmatter`'s `ValueError` is uncaught by `install`/`--check`; `_read_plain`'s except branch untested | CONFIRMED |
| C20 | `tests/test_install.py:393` | `test_installed_stub_holds_no_instructions` adds no fact beyond two existing tests | CONFIRMED |
| C19 | `tests/test_config.py:144` | `test_cache_dir_flag_beats_env` and `test_env_cache_dir_beats_user_config` are strict subsets of the precedence matrix; the `user_config_beats_default` one is not (covers the crossref path) | PLAUSIBLE |
| C14 | `cli.py:476` | Extracting the verify loop is a redistribution, not a reduction; only worth it for testability | PLAUSIBLE |
| C16 | `cli.py:147` | Paired Option objects could use per-shape helpers; modest | PLAUSIBLE |
| C17 | `cli.py:709` | `"0"` for the Crossref `min_interval` default is hand-mirrored; deriving it would print `0.0` and break a locked test | CONFIRMED (leave) |
| C23 | `README.md:407` | Crash-safe claim false today; true as written once A03 lands | CONFIRMED (no doc change) |

Rejected: `install.py:141` CRLF frontmatter (every read path translates line
endings first; unreachable in-package); `cli.py:60` moving config loading
into `config.py` (that module is deliberately typer-free and documented as
never loading config; a wash in lines with a worse boundary); `cli.py:743`
`config` printing `mailto` (not a secret, sent in every request, local
diagnostic).

Pass C gap sweep (main loop): nothing additional.

Review totals across the three passes: 105 raw candidates, 70 after dedup,
60 verified findings (53 confirmed, 7 plausible), 12 rejected.
