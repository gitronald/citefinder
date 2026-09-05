"""Tests for citefinder.signals — signal checks and status reduction."""

from citefinder.signals import (
    MIN_TITLE_TOKENS,
    BibCitation,
    Status,
    Work,
    check_author,
    check_container,
    check_title,
    check_year,
    compute_signals,
    container_similarity,
    normalize_title,
    status_from_signals,
    title_similarity,
    title_tokens,
)


def test_normalize_title_lowercases_and_strips_punctuation() -> None:
    assert normalize_title("Hello, World!") == "hello world"


def test_normalize_title_handles_unicode_diacritics() -> None:
    # NFKD decomposition + dropping combining marks: "café" → "cafe".
    assert normalize_title("Café Résumé") == "cafe resume"


def test_normalize_title_keeps_non_latin_scripts() -> None:
    # NFKD cannot fold these to ASCII; deleting them left no tokens at all.
    assert normalize_title("深度学习综述") == "深度学习综述"
    assert normalize_title("Глубокое, обучение!") == "глубокое обучение"


def test_check_title_identical_non_latin_titles_pass() -> None:
    title = "Глубокое обучение для текста"
    assert check_title(title, title)["verdict"] == "pass"


def test_title_similarity_identical_is_one() -> None:
    assert title_similarity("Hello World", "Hello World") == 1.0


def test_title_similarity_disjoint_is_zero() -> None:
    assert title_similarity("Hello World", "Foo Bar") == 0.0


def test_title_similarity_empty_inputs() -> None:
    assert title_similarity("", "anything") == 0.0


def test_check_title_high_overlap_passes() -> None:
    r = check_title("A Study of Things", "A Study of Things")
    assert r["verdict"] == "pass"


def test_check_title_no_overlap_fails() -> None:
    r = check_title("Apples Oranges", "Bicycles Trucks")
    assert r["verdict"] == "fail"


def test_check_title_partial_is_unknown() -> None:
    # A 50% Jaccard overlap should land in the "unknown" band so we don't
    # punish source-side title truncation.
    r = check_title("foo bar baz", "foo bar")
    assert r["verdict"] == "unknown"


def test_check_title_missing_input_is_unknown() -> None:
    assert check_title(None, "Title")["verdict"] == "unknown"
    assert check_title("Title", None)["verdict"] == "unknown"


def test_title_tokens_counts_normalized_words() -> None:
    assert MIN_TITLE_TOKENS == 3
    assert title_tokens("Influence") == {"influence"}
    assert len(title_tokens("Deep Learning")) < MIN_TITLE_TOKENS
    assert len(title_tokens("A Study of Things")) >= MIN_TITLE_TOKENS


def test_check_title_short_bib_title_cannot_pass() -> None:
    # cialdini2003influence: a one-word bib title scores 1.0 against any
    # record that contains the word, so a perfect match proves nothing.
    r = check_title("Influence", "Influence")
    assert r["verdict"] == "unknown"
    assert r["sim"] == 1.0
    assert "need 3" in r["note"]


def test_check_title_short_bib_title_still_fails_on_no_overlap() -> None:
    assert check_title("Influence", "Bicycles Trucks Cars")["verdict"] == "fail"


def test_check_title_short_bib_title_is_not_rescued_by_containment() -> None:
    # A one-word title is a subset of every title containing the word, so
    # containment is not evidence of truncation here — the low Jaccard stands
    # and the DOI path reports the deficient bib title for review.
    r = check_title("Influence", "Influence: Science and Practice")
    assert r["verdict"] == "fail"


def test_check_title_source_truncation_is_unknown() -> None:
    # Fang2022 (10.1145/3510003.3510121): OpenAlex stores only the part
    # before the colon in both `display_name` and `title`.
    bib = (
        '"This Is Damn Slick!": Estimating the Impact of Tweets on Open Source '
        "Project Popularity and New Contributors"
    )
    r = check_title(bib, '"This is damn slick!"')
    assert r["verdict"] == "unknown"
    assert r["sim"] < 0.30
    assert "truncation" in r["note"]


def test_check_title_bib_truncation_is_unknown() -> None:
    # The mirror case: the bib omits the subtitle the source carries.
    r = check_title(
        "Backstabber's Knife Collection",
        "Backstabber's Knife Collection: A Review of Open Source Software "
        "Supply Chain Attacks",
    )
    assert r["verdict"] == "unknown"


def test_check_title_overlap_without_containment_still_fails() -> None:
    # Sharing one word is not truncation; the sets are not nested.
    r = check_title("A Study of Social Things", "Social Bots in the Wild")
    assert r["verdict"] == "fail"


def test_check_year_exact_match() -> None:
    assert check_year("2020", 2020)["verdict"] == "pass"


def test_check_year_within_one_year_passes() -> None:
    # Tolerates preprint-vs-proceedings drift.
    assert check_year("2020", 2021)["verdict"] == "pass"
    assert check_year("2020", 2019)["verdict"] == "pass"


def test_check_year_far_off_fails() -> None:
    assert check_year("2020", 2018)["verdict"] == "fail"


def test_check_year_unparseable_is_unknown() -> None:
    assert check_year("forthcoming", 2020)["verdict"] == "unknown"
    assert check_year("2020", None)["verdict"] == "unknown"


def test_check_author_token_overlap_passes() -> None:
    # Compound surname on one side, single token on the other — token-overlap
    # rule lets these match.
    assert check_author("Larios Vargas", "Vargas")["verdict"] == "pass"


def test_check_author_no_overlap_fails() -> None:
    assert check_author("Smith", "Jones")["verdict"] == "fail"


def test_check_author_shared_particle_alone_is_not_agreement() -> None:
    # `van` is a particle, not a name; two unrelated Dutch surnames share it.
    assert check_author("van de Rijt", "van der Berg")["verdict"] == "fail"
    assert check_author("van de Rijt", "van de Rijt")["verdict"] == "pass"
    # OpenAlex may capitalise the particle the bib keeps lower-case.
    assert check_author("de Wolf", "De Wolf")["verdict"] == "pass"
    # An all-lower-case surname has no particles to drop.
    assert check_author("bell hooks", "hooks")["verdict"] == "pass"


def test_check_author_missing_is_unknown() -> None:
    assert check_author(None, "Smith")["verdict"] == "unknown"


def test_container_similarity_prefix_matches_abbreviations() -> None:
    # `proc` should match `proceedings` via the prefix rule.
    sim = container_similarity("Proc ICML", "Proceedings of ICML")
    assert sim > 0.0


def test_container_similarity_pairs_each_token_once() -> None:
    # One short token used to prefix-match every longer token on the other
    # side and score a perfect match against an unrelated venue.
    assert container_similarity("Data Database Dataset", "Data") < 0.50
    assert container_similarity("proc", "proceedings procedure procession") < 0.50
    # Genuine abbreviations still clear the pass threshold.
    assert container_similarity("Proc ICML", "Proceedings of ICML") >= 0.50
    assert container_similarity("Journal of Things", "Journal of Things") == 1.0


def test_check_container_full_overlap_passes() -> None:
    r = check_container("Journal of Things", ["Journal of Things"])
    assert r["verdict"] == "pass"


def test_check_container_unrelated_fails() -> None:
    r = check_container("Nature", ["Annual Review of Sociology"])
    assert r["verdict"] == "fail"


def test_check_container_picks_best_alias() -> None:
    # Crossref returns multiple aliases; the check should pick the closest.
    r = check_container("Journal of Things", ["Random Other", "Journal of Things"])
    assert r["verdict"] == "pass"
    assert r["crossref"] == "Journal of Things"


def test_compute_signals_builds_all_four() -> None:
    cit = BibCitation(
        title="A Paper on Things",
        year="2020",
        first_author_surname="Smith",
        container="J",
    )
    work = Work(
        title="A Paper on Things",
        year=2020,
        first_author_surname="Smith",
        container_names=["J"],
    )
    signals = compute_signals(cit, work)
    assert set(signals) == {"title", "year", "author", "container"}
    assert all(s["verdict"] == "pass" for s in signals.values())


def _signals(verdicts: dict[str, str]) -> dict:
    return {
        k: {"verdict": v, "bib": None, "crossref": None} for k, v in verdicts.items()
    }


def test_status_from_signals_two_passes_is_matched() -> None:
    s, _ = status_from_signals(
        _signals(
            {
                "title": "pass",
                "year": "pass",
                "author": "unknown",
                "container": "unknown",
            }
        )
    )
    assert s == Status.MATCHED


def test_status_from_signals_one_fail_is_probable() -> None:
    s, note = status_from_signals(
        _signals(
            {"title": "pass", "year": "fail", "author": "pass", "container": "unknown"}
        )
    )
    assert s == Status.PROBABLE
    assert "year" in note


def test_status_from_signals_two_fails_is_mismatch() -> None:
    s, _ = status_from_signals(
        _signals(
            {"title": "fail", "year": "fail", "author": "pass", "container": "unknown"}
        )
    )
    assert s == Status.MISMATCH


def test_status_from_signals_all_unknown_is_probable() -> None:
    s, _ = status_from_signals(
        _signals(dict.fromkeys(["title", "year", "author", "container"], "unknown"))
    )
    assert s == Status.PROBABLE


def test_status_from_signals_doi_resolved_single_non_title_fail_is_matched() -> None:
    # Ohm2020 / messing2014: the bib's own DOI resolved, three signals
    # confirm, one disagrees. Source-side metadata loss, not a different work.
    signals = _signals(
        {"title": "pass", "year": "fail", "author": "pass", "container": "pass"}
    )
    signals["year"].update({"bib": "2014", "crossref": "2012"})
    s, note = status_from_signals(signals, doi_resolved=True)
    assert s == Status.MATCHED
    assert note == (
        "DOI resolved; source disagrees on: year (bib '2014' vs source '2012')"
    )
    # The same signals without the DOI claim stay probable.
    assert status_from_signals(signals)[0] == Status.PROBABLE


def test_status_from_signals_doi_resolved_title_fail_stays_probable() -> None:
    # A typoed DOI that lands on a related work fails only on title.
    s, _ = status_from_signals(
        _signals(
            {"title": "fail", "year": "pass", "author": "pass", "container": "pass"}
        ),
        doi_resolved=True,
    )
    assert s == Status.PROBABLE


def test_status_from_signals_doi_resolved_needs_two_passes() -> None:
    # One disagreement with nothing confirming is not a match, DOI or not;
    # it must not rank above the zero-fail case, which stays probable.
    for passing in ([], ["author"]):
        verdicts = dict.fromkeys(["title", "author", "container"], "unknown")
        verdicts.update(dict.fromkeys(passing, "pass"))
        verdicts["year"] = "fail"
        s, note = status_from_signals(_signals(verdicts), doi_resolved=True)
        assert s == Status.PROBABLE
        assert note.startswith("Source disagrees on: year")


def test_status_keeps_str_compatibility() -> None:
    # StrEnum guarantees: status values compare equal to their string form.
    assert Status.MATCHED == "matched"
    assert Status.MATCHED.header.startswith("Matched")
