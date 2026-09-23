"""Tests for the preprint_dedup port (R/preprint_dedup.R, FReD issue #105).

Values checked against R come from cache/confirmed_preprint_duplicates.csv, which
records the similarity R itself computed for each pair — so these compare against
the original implementation's own output rather than a guess at it.
"""
import json
import os
from datetime import datetime, timezone

import pandas as pd
import pytest

import preprint_dedup as pdd


# ── DOI handling ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doi", [
    "10.31234/osf.io/uhbk9", "10.1101/2023.05.26.542419",
    "10.2139/ssrn.2187902", "10.53841/bpscep.2011.1.12.29",
    "10.21203/rs.3.rs-7544401/v1", "10.31219/osf.io/x394e_v2",
    "10.31222/osf.io/sjyp3",
])
def test_preprint_dois_are_recognised(doi):
    assert pdd.is_preprint_doi(doi)


def test_metaarxiv_preprint_loses_to_its_published_replication():
    """10.31222/osf.io/sjyp3 is the MetaArXiv preprint of Kohrt et al.'s published
    replication (10.1098/rsos.221306) of Smaldino & McElreath 2016. It slipped past
    the dedup logic because 10.31222/ (MetaArXiv) was missing from
    PREPRINT_DOI_PREFIXES, so is_preprint_doi() said False for a real preprint and
    the default rule fell through to the arbitrary doi_2-loses tie-break instead of
    reliably dropping the preprint."""
    remove, keep = pdd.default_resolve_pair(
        "replication", "10.1098/rsos.221306", "10.31222/osf.io/sjyp3",
        pdd.is_preprint_doi("10.1098/rsos.221306"),
        pdd.is_preprint_doi("10.31222/osf.io/sjyp3"))
    assert remove == "10.31222/osf.io/sjyp3"
    assert keep == "10.1098/rsos.221306"


@pytest.mark.parametrize("doi", ["10.1177/0956797620939054", "10.1037/a0021524", "", None])
def test_published_dois_are_not_preprints(doi):
    assert not pdd.is_preprint_doi(doi)


def test_double_slash_typo_is_repaired():
    """10.1037//0022 and 10.1037/0022 are the same paper; without this repair the
    whole DOI-variant route finds nothing."""
    assert pdd.normalize_doi("10.1037//0022-3514.46.4.778") == "10.1037/0022-3514.46.4.778"


def test_percent_encoding_is_decoded():
    encoded = "10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y"
    plain = "10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y"
    assert pdd.normalize_doi(encoded) == plain


def test_resolver_prefixes_are_stripped():
    assert pdd.normalize_doi("https://doi.org/10.1/A") == "10.1/a"
    assert pdd.normalize_doi("doi:10.1/a") == "10.1/a"


# ── titles ────────────────────────────────────────────────────────────────────

def test_title_similarity_matches_what_r_recorded():
    """0.991 is the value the confirmed file records for this pair."""
    sim = pdd.title_similarity(
        "Fighting COVID-19 misinformation on social media: Experimental evidence "
        "for a scalable accuracy nudge intervention",
        "Fighting COVID-19 Misinformation on Social Media: Experimental Evidence "
        "for a Scalable Accuracy-Nudge Intervention")
    assert round(sim, 3) == 0.991


def test_case_and_punctuation_do_not_matter():
    assert pdd.title_similarity("The Stroop Effect!", "the stroop effect") == 1.0


def test_markup_is_stripped_before_comparing():
    assert pdd.title_similarity(
        "Psychometric Reliability of <scp>ERN</scp> and Pe",
        "Psychometric Reliability of ERN and Pe") == 1.0


def test_an_absent_title_is_never_similar():
    assert pdd.title_similarity(None, "anything") == 0.0
    assert pdd.title_similarity("", "") == 0.0


# ── authors ───────────────────────────────────────────────────────────────────

def test_surname_from_openalex_style_authors():
    assert pdd.extract_first_author("Gordon Pennycook; Jonathon McPhetres") == "pennycook"


def test_surname_from_family_comma_given():
    assert pdd.extract_first_author("Bem, Daryl J.") == "bem"


def test_no_author_gives_none():
    assert pdd.extract_first_author(None) is None
    assert pdd.extract_first_author("  ") is None


# ── default resolution rule ───────────────────────────────────────────────────

def test_a_lone_preprint_loses():
    remove, _ = pdd.default_resolve_pair(
        "original (fuzzy)", "10.31234/osf.io/uhbk9", "10.1177/0956797620939054",
        True, False)
    assert remove == "10.31234/osf.io/uhbk9"


def test_a_lone_preprint_loses_from_either_position():
    remove, _ = pdd.default_resolve_pair(
        "replication", "10.1177/x", "10.31234/osf.io/y", False, True)
    assert remove == "10.31234/osf.io/y"


def test_for_doi_variants_the_non_canonical_loses():
    remove, keep = pdd.default_resolve_pair(
        "original (DOI variant)", "10.1037//0022-3514.46.4.778",
        "10.1037/0022-3514.46.4.778", False, False)
    assert remove == "10.1037//0022-3514.46.4.778"
    assert keep == "10.1037/0022-3514.46.4.778"


def test_otherwise_doi_2_loses_deterministically():
    assert pdd.default_resolve_pair("replication", "10.1/a", "10.2/b",
                                    False, False) == ("10.2/b", "10.1/a")


def test_two_preprints_still_resolve_deterministically():
    remove, _ = pdd.default_resolve_pair(
        "replication", "10.31234/osf.io/a", "10.31234/osf.io/b", True, True)
    assert remove == "10.31234/osf.io/b"


def test_a_pair_with_a_missing_doi_cannot_be_resolved():
    """One confirmed row genuinely has doi_2 absent."""
    assert pdd.default_resolve_pair("replication", "10.1/a", None, False, False) is None


# ── held for review ───────────────────────────────────────────────────────────

def test_generic_osf_dois_are_not_preprints():
    """10.17605/ holds OSF projects and registrations, not just preprints."""
    assert not pdd.is_preprint_doi("10.17605/osf.io/vufm2")
    assert pdd.is_repository_doi("10.17605/OSF.IO/VUFM2")


def _reproductions(first_authors, dois=("10.17605/osf.io/v9ykq", "10.17605/osf.io/vufm2")):
    """Two SCORE reproduction reports of Ku & Zaroff (2014), as in the sheet."""
    return pd.DataFrame({
        "doi_o": ["10.1016/j.jenvp.2014.10.008"] * 2,
        "doi_r": list(dois),
        "title_r": ["Reproduction (with author data): Ku & Zaroff (2014, Journal "
                    "of Environmental Psychology)"] * 2,
        "author_r": list(first_authors),
        "year_r": ["2022", "2020"],
        "title_o": ["Ku & Zaroff"] * 2,
        "author_o": ["Ku"] * 2,
    })


def test_independent_osf_reproductions_are_not_even_candidates():
    """Identical templated titles by different teams are not duplicates."""
    candidates = pdd.find_duplicates(_reproductions(["Parsons", "Sonmez"]), verbose=False)
    assert candidates.empty or "replication" not in set(candidates["side"])


def test_differing_first_authors_are_held_and_both_rows_kept(tmp_path):
    frame = _reproductions(["Parsons", "Sonmez"],
                           dois=("10.31234/osf.io/aaaaa", "10.31234/osf.io/bbbbb"))
    out, log = pdd.resolve(frame, confirmed_path=tmp_path / "none.csv",
                           candidates_out=None, verbose=False)
    assert len(out) == 2
    row = log[log["side"] == "replication"].iloc[0]
    assert row["applied_action"] == pdd.NEEDS_REVIEW
    assert row["resolution"] == "review: first authors differ"


def test_two_preprints_need_a_matching_first_author():
    candidate = {"side": "replication", "first_author_1": None, "first_author_2": "fox",
                 "is_preprint_1": True, "is_preprint_2": True}
    assert pdd.review_reason(candidate) == "two preprints without a matching first author"


def test_two_preprints_by_the_same_author_still_resolve_automatically():
    candidate = {"side": "replication", "first_author_1": "fox", "first_author_2": "fox",
                 "is_preprint_1": True, "is_preprint_2": True}
    assert pdd.review_reason(candidate) is None


def test_doi_variants_are_never_held():
    """One normalised DOI already proves they are the same paper."""
    candidate = {"side": "original (DOI variant)", "first_author_1": "a",
                 "first_author_2": "b", "is_preprint_1": False, "is_preprint_2": False}
    assert pdd.review_reason(candidate) is None


def test_a_confirmed_override_beats_review(tmp_path):
    confirmed = tmp_path / "confirmed.csv"
    confirmed.write_text("side,doi_1,doi_2,action\n"
                         "replication,10.31234/osf.io/aaaaa,10.31234/osf.io/bbbbb,keep_1\n",
                         encoding="utf-8")
    frame = _reproductions(["Parsons", "Sonmez"],
                           dois=("10.31234/osf.io/aaaaa", "10.31234/osf.io/bbbbb"))
    out, _ = pdd.resolve(frame, confirmed_path=confirmed, candidates_out=None,
                         verbose=False)
    assert list(out["doi_r"]) == ["10.31234/osf.io/aaaaa"]


def test_candidates_carry_what_a_reviewer_needs(tmp_path):
    frame = _reproductions(["Parsons", "Sonmez"],
                           dois=("10.31234/osf.io/aaaaa", "10.31234/osf.io/bbbbb"))
    frame["display_id"] = ["REPRO-000001", "REPRO-000002"]
    frame["type"] = "reproduction"
    frame["outcome"] = ["computationally reproducible, robust",
                        "computational issues, not checked"]
    frame["url_r"] = [None, "https://osf.io/x"]
    candidate = pdd.find_duplicates(frame, verbose=False).iloc[0]
    assert candidate["source_display_id_2"] == "REPRO-000002"
    assert candidate["outcome_1"] == "computationally reproducible, robust"
    assert candidate["url_2"] == "https://osf.io/x"


# ── rulings made on the website ───────────────────────────────────────────────

def test_a_website_ruling_wins_over_the_file_for_the_same_pair(tmp_path):
    confirmed = tmp_path / "confirmed.csv"
    confirmed.write_text("side,doi_1,doi_2,action\nreplication,10.1/a,10.1/b,keep_1\n",
                         encoding="utf-8")
    rows = pdd.confirmed_decisions(confirmed, [
        {"side": "replication", "doi_1": "10.1/B", "doi_2": "10.1/A", "action": "keep_both"}])
    assert [r["action"] for r in rows] == ["keep_both"]


def test_a_ruling_with_an_unknown_action_is_ignored(tmp_path):
    rows = pdd.confirmed_decisions(tmp_path / "none.csv", [
        {"side": "replication", "doi_1": "10.1/a", "doi_2": "10.1/b", "action": "maybe"}])
    assert rows == []


def test_a_website_keep_both_keeps_a_pair_the_rules_would_drop(tmp_path):
    """Same author and a lone preprint: the default rule drops the preprint."""
    frame = _reproductions(["Fox", "Fox"], dois=("10.31234/osf.io/x", "10.1016/j.x.2020"))
    ruling = {"side": "replication", "doi_1": "10.31234/osf.io/x",
              "doi_2": "10.1016/j.x.2020", "action": "keep_both"}
    out, log = pdd.resolve(frame, confirmed_path=tmp_path / "none.csv",
                           candidates_out=None, verbose=False, rulings=[ruling])
    assert len(out) == 2
    assert log.iloc[0]["applied_action"] == "keep_both"


def test_a_website_keep_ruling_applies_before_enrichment(tmp_path):
    frame = _reproductions(["Parsons", "Sonmez"],
                           dois=("10.31234/osf.io/aaaaa", "10.31234/osf.io/bbbbb"))
    ruling = {"side": "replication", "doi_1": "10.31234/osf.io/aaaaa",
              "doi_2": "10.31234/osf.io/bbbbb", "action": "keep_2"}
    out = pdd.apply_confirmed(frame, confirmed_path=tmp_path / "none.csv",
                              verbose=False, rulings=[ruling])
    assert list(out["doi_r"]) == ["10.31234/osf.io/bbbbb"]


def test_only_undecided_pairs_are_unresolved():
    log = [{"applied_action": a} for a in
           ("needs_review", "auto_keep_1", "auto_keep_2", "keep_1", "keep_both", "skipped")]
    assert [d["applied_action"] for d in pdd.unresolved(log)] == \
        ["needs_review", "auto_keep_1", "auto_keep_2"]


def test_the_candidates_log_is_headed_by_its_instructions(tmp_path):
    path = tmp_path / "out" / "candidates.csv"
    pdd.write_candidates([{"side": "replication", "doi_1": "10.1/a", "doi_2": "10.1/b",
                           "applied_action": "needs_review"}], path)
    rows = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert rows.iloc[0]["side"] == "INSTRUCTIONS -->"
    assert "FLoRA tab" in rows.iloc[0]["resolution"]
    assert rows.iloc[1]["applied_action"] == "needs_review"


def test_a_pair_missing_a_doi_is_skipped_not_queued(tmp_path):
    """Nobody can rule on it (a ruling names both DOIs), so queueing it would leave a
    warning on every run that no one can clear."""
    frame = _reproductions(["Parsons", "Sonmez"], dois=("10.31234/osf.io/aaaaa", None))
    _, log = pdd.resolve(frame, confirmed_path=tmp_path / "none.csv",
                         candidates_out=None, verbose=False)
    row = log[log["side"] == "replication"].iloc[0]
    assert row["applied_action"] == "skipped"
    assert pdd.unresolved(log.to_dict("records")) == []


def test_the_review_issue_can_be_filed_from_a_build_that_writes_no_log(tmp_path, monkeypatch):
    """build() passes candidates_out=None; the issue text still names the log."""
    bodies = []

    def fake_gh(*args, **kwargs):
        if args[1] == "list":
            return "[]"
        body_file = args[args.index("--body-file") + 1]
        bodies.append(open(body_file, encoding="utf-8").read())
        return "https://github.com/x/y/issues/1"

    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh", fake_gh)
    frame = _reproductions(["Parsons", "Sonmez"],
                           dois=("10.31234/osf.io/aaaaa", "10.31234/osf.io/bbbbb"))
    pdd.resolve(frame, confirmed_path=_stale_file(tmp_path), candidates_out=None,
                verbose=False, open_review_issue=True)
    assert bodies and pdd.CANDIDATES_PATH.name in bodies[0]


def test_a_repository_doi_still_loses_to_a_publisher_doi(tmp_path):
    frame = _reproductions(["Fox", "Fox"], dois=("10.17605/osf.io/x", "10.1016/j.x.2020"))
    out, _ = pdd.resolve(frame, confirmed_path=tmp_path / "none.csv",
                         candidates_out=None, verbose=False)
    assert list(out["doi_r"]) == ["10.1016/j.x.2020"]


# ── the confirmed file ────────────────────────────────────────────────────────

def test_the_confirmed_file_loads_past_its_bom():
    """Excel writes a BOM; read as plain utf-8 the first column name carries it and
    every lookup of "side" silently misses."""
    rows = pdd.load_confirmed()
    assert rows and all("side" in r for r in rows)


def test_the_instructions_row_is_not_a_decision():
    assert {r["action"] for r in pdd.load_confirmed()} <= pdd.VALID_ACTIONS


def test_keep_1_keeps_doi_1():
    assert pdd.derive_remove_keep("keep_1", "A", "B") == ("B", "A")


def test_keep_2_keeps_doi_2():
    assert pdd.derive_remove_keep("keep_2", "A", "B") == ("A", "B")


def test_keep_both_removes_nothing():
    assert pdd.derive_remove_keep("keep_both", "A", "B") == (None, None)


def test_pair_key_is_order_and_case_independent():
    assert pdd.pair_key("10.1/A", "10.2/b") == pdd.pair_key("10.2/B", "10.1/a")


# ── alt identifiers ───────────────────────────────────────────────────────────

def test_alt_identifiers_dedupe_case_insensitively():
    assert pdd.append_alt_identifier("10.1/a", "10.1/A") == "10.1/a"


def test_alt_identifiers_accumulate():
    assert pdd.append_alt_identifier("10.1/a", "10.2/b") == "10.1/a, 10.2/b"


def test_alt_identifier_of_nothing_is_none():
    assert pdd.append_alt_identifier(None, None) is None


# ── merging (doi_o, doi_r) groups ─────────────────────────────────────────────

def _pair_frame(**over):
    base = {
        "doi_o": ["10.1/a", "10.1/a"], "doi_r": ["10.2/b", "10.2/b"],
        "url_r": [None, None], "study_o": ["1", "2"],
        "outcome": ["successful", "successful"],
        "outcome_quote": ["q1", "q2"], "source": ["validated", "COS"],
        "record_id": ["r1", "r2"], "merged_record_ids": [[], []],
    }
    base.update(over)
    return pd.DataFrame(base)


def test_compatible_rows_merge():
    out, _ = pdd.merge_doi_pair_dups(_pair_frame(), verbose=False)
    assert len(out) == 1


def test_two_distinct_urls_keep_the_rows_apart():
    """They may be different replication reports of one dataset."""
    frame = _pair_frame(url_r=["https://osf.io/x", "https://osf.io/y"])
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert len(out) == 2


def test_study_numbers_are_combined_not_dropped():
    out, _ = pdd.merge_doi_pair_dups(_pair_frame(), verbose=False)
    assert out["study_o"].iloc[0] == "1; 2"


def test_quotes_are_combined():
    out, _ = pdd.merge_doi_pair_dups(_pair_frame(), verbose=False)
    assert out["outcome_quote"].iloc[0] == "q1 || q2"


def test_cos_wins_as_the_source():
    out, _ = pdd.merge_doi_pair_dups(_pair_frame(), verbose=False)
    assert out["source"].iloc[0] == "COS"


def test_disagreeing_mixable_outcomes_become_mixed():
    # No website record here: with one, its outcome would win instead.
    frame = _pair_frame(outcome=["successful", "failed"], source=["replications", "COS"])
    out, conflicts = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert out["outcome"].iloc[0] == "mixed"
    assert len(conflicts) == 1


def test_an_unmixable_clash_is_retained_for_validation():
    """Kept as "A || B" so validate_flora reports it — merging it into something
    valid would hide a source-data mistake."""
    frame = _pair_frame(outcome=["successful", "computationally reproducible"],
                        source=["replications", "COS"])
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert pdd.OUTCOME_CLASH_SEP in out["outcome"].iloc[0]


def test_absorbed_record_ids_survive_the_merge():
    """flora_registry needs them to recognise the record if a reviewer later
    promotes one of the absorbed rows."""
    out, _ = pdd.merge_doi_pair_dups(_pair_frame(), verbose=False)
    assert "r2" in out["merged_record_ids"].iloc[0]


def test_a_replication_and_a_reproduction_of_one_pair_stay_two_records():
    """The absorb bug: merged, the reproduction was published as a replication
    with 'failed || computational issues, robustness challenges' as its outcome."""
    frame = _pair_frame(type=["replication", "reproduction"],
                        outcome=["failed", "computational issues, robustness challenges"])
    out, conflicts = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert sorted(out["type"]) == ["replication", "reproduction"]
    assert set(out["outcome"]) == {"failed", "computational issues, robustness challenges"}
    assert conflicts.empty


def test_two_rows_of_one_type_still_merge():
    frame = _pair_frame(type=["reproduction", "reproduction"])
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert len(out) == 1 and out["type"].iloc[0] == "reproduction"


def test_a_conflict_names_the_type_it_happened_in():
    frame = _pair_frame(type=["replication", "replication"], outcome=["successful", "failed"])
    _, conflicts = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert conflicts["type"].tolist() == ["replication"]


def _typed_rows(rows):
    """(doi_r, type) rows under one original, all with one templated title."""
    return pd.DataFrame({
        "doi_o": ["10.1/original"] * len(rows),
        "doi_r": [doi for doi, _ in rows],
        "type": [kind for _, kind in rows],
        "title_r": ["A replication and reproduction of X"] * len(rows),
        "author_r": ["Fox"] * len(rows),
        "year_r": ["2021"] * len(rows),
        "title_o": ["X"] * len(rows),
        "author_o": ["Orig"] * len(rows),
    })


def test_a_preprint_drop_spares_a_record_type_the_publication_lacks(tmp_path):
    """The preprint has a replication and a reproduction row; the publication only
    a replication row. Dropping the preprint must not take the reproduction."""
    frame = _typed_rows([("10.31234/osf.io/pre", "replication"),
                         ("10.31234/osf.io/pre", "reproduction"),
                         ("10.1016/j.pub", "replication")])
    out, log = pdd.resolve(frame, confirmed_path=tmp_path / "none.csv",
                           candidates_out=None, verbose=False)
    assert log.iloc[0]["applied_action"] == "auto_keep_2"
    kept = set(zip(out["doi_r"], out["type"]))
    assert kept == {("10.1016/j.pub", "replication"), ("10.31234/osf.io/pre", "reproduction")}


def test_rows_of_different_types_are_not_preprint_candidates():
    frame = _typed_rows([("10.31234/osf.io/pre", "reproduction"),
                         ("10.1016/j.pub", "replication")])
    assert pdd.find_duplicates(frame, verbose=False).empty


def _website_and_sheet(**over):
    """FLORA-001697: the entry sheet and the website disagree on robustness."""
    base = {
        "doi_o": ["10.3982/ecta6248"] * 2, "doi_r": ["10.1002/jae.2861"] * 2,
        "url_r": [None, None], "type": ["reproduction"] * 2,
        "source": ["reproductions", "validated"],
        "record_id": ["sheet", "website"], "merged_record_ids": [[], []],
        "outcome": ["computationally reproducible, robust",
                    "computationally reproducible, not checked"],
        "outcome_computation": ["computationally reproducible"] * 2,
        "outcome_robustness": ["robust", "not checked"],
        "outcome_quote": ["sheet quote", None],
    }
    base.update(over)
    return pd.DataFrame(base)


def test_the_website_record_wins_a_merge():
    out, conflicts = pdd.merge_doi_pair_dups(_website_and_sheet(), verbose=False)
    row = out.iloc[0]
    assert row["outcome"] == "computationally reproducible, not checked"
    assert row["outcome_robustness"] == "not checked"
    assert row["source"] == "validated"
    # Values only: the row stays the one it was (and keeps its published id), with
    # the website record traceable in its provenance.
    assert row["record_id"] == "sheet" and row["merged_record_ids"] == ["website"]
    # Still logged, so the sheet can be corrected, but settled.
    assert conflicts.iloc[0]["resolved_by"] == "website record"
    assert pdd.OUTCOME_CLASH_SEP not in row["outcome"]


def test_a_field_the_website_left_blank_falls_back_to_what_we_have():
    out, _ = pdd.merge_doi_pair_dups(_website_and_sheet(), verbose=False)
    assert out.iloc[0]["outcome_quote"] == "sheet quote"


def test_without_a_website_record_the_merge_rules_are_unchanged():
    frame = _website_and_sheet(source=["reproductions", "reproductions"])
    out, conflicts = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert pdd.OUTCOME_CLASH_SEP in out.iloc[0]["outcome"]
    assert conflicts.iloc[0]["resolved_by"] == "merge rules"


def test_rows_a_reviewer_ruled_distinct_are_never_merged():
    """'Keep — distinct' in Source Records means its own record; the merge used to
    fold such rows together anyway, one step after the dedup exempted them."""
    frame = _pair_frame(outcome=["successful", "failed"], source=["replications", "COS"],
                        duplicate_status=["distinct", "distinct"])
    out, conflicts = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert sorted(out["outcome"]) == ["failed", "successful"]
    assert conflicts.empty


def test_an_unruled_row_still_merges_beside_a_distinct_one():
    frame = pd.concat([_pair_frame(duplicate_status=[None, None]),
                       _pair_frame(duplicate_status=["distinct", "distinct"])
                       .assign(record_id=["r3", "r4"])], ignore_index=True)
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert len(out) == 3          # r1+r2 merged; r3 and r4 kept as ruled


def test_the_first_website_row_with_a_judgement_speaks_for_the_website():
    frame = _website_and_sheet(
        source=["validated", "validated"], record_id=["blank-web", "web"],
        outcome=[None, "computationally reproducible, not checked"],
        outcome_computation=[None, "computationally reproducible"],
        outcome_robustness=[None, "not checked"])
    frame = pd.concat([frame, _website_and_sheet().iloc[[0]]], ignore_index=True)
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert out.iloc[0]["outcome"] == "computationally reproducible, not checked"


def test_a_website_row_without_a_judgement_does_not_override_the_evidence():
    frame = _website_and_sheet(
        source=["replications", "validated"], type=["replication"] * 2,
        outcome=["successful", None], outcome_computation=[None, None],
        outcome_robustness=[None, None], outcome_quote=["sheet quote", "web quote"])
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert out.iloc[0]["outcome"] == "successful"
    # No judgement of its own, so its quote is merged like any other.
    assert out.iloc[0]["outcome_quote"] == "sheet quote || web quote"


def test_an_osf_and_a_preprint_doi_without_matching_authors_are_held():
    """A generic OSF DOI loses like a preprint when resolving, so it must count as
    one for the review rule too, or the pair falls to the arbitrary tie-break."""
    candidate = {"side": "replication", "doi_1": "10.17605/osf.io/aaaa1",
                 "doi_2": "10.31234/osf.io/bbbb2", "first_author_1": None,
                 "first_author_2": "parsons", "is_preprint_1": False, "is_preprint_2": True}
    assert pdd.review_reason(candidate) == "two preprints without a matching first author"


def test_a_recent_ruling_in_the_tab_counts_as_review_activity(tmp_path, monkeypatch):
    """Rulings made on the website never touch the confirmed file, which would
    otherwise look abandoned and file an issue every night."""
    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh", lambda *a, **k: pytest.fail("no issue expected"))
    assert pdd.maybe_open_review_issue(
        _auto(), confirmed_path=_stale_file(tmp_path), verbose=False,
        last_ruling_at=datetime.now(timezone.utc)) == "skipped"


def test_rows_without_a_pair_are_untouched():
    frame = _pair_frame(doi_r=[None, None])
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert len(out) == 2


# ── review issue filing ───────────────────────────────────────────────────────

def _auto(n=3):
    return [{"resolution": "auto: drop doi_2", "applied_action": "auto_keep_1",
             "doi_1": f"10.1/a{i}", "doi_2": f"10.2/b{i}",
             "side": "replication", "title_sim": 0.95} for i in range(n)]


def test_held_pairs_also_count_toward_the_review_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh", lambda *a, **k: "[]" if a[1] == "list" else "url")
    held = [{"resolution": "review: first authors differ",
             "applied_action": pdd.NEEDS_REVIEW, "doi_1": "10.1/a", "doi_2": "10.2/b",
             "side": "replication", "title_sim": 1.0}]
    assert pdd.maybe_open_review_issue(held, confirmed_path=_stale_file(tmp_path),
                                       verbose=False) == "filed"


def test_no_auto_resolutions_means_no_issue():
    decisions = [{"resolution": "override: keep_1"}, {"resolution": "skipped: NA DOI"}]
    assert pdd.maybe_open_review_issue(decisions, verbose=False) == "skipped"


def test_a_freshly_touched_confirmed_file_needs_no_issue(tmp_path):
    """Pairs resolved by the default rule are fine while somebody is still
    confirming them — the nudge is for when nobody is."""
    confirmed = tmp_path / "confirmed.csv"
    confirmed.write_text("side,doi_1,doi_2,action\n", encoding="utf-8")
    assert pdd.maybe_open_review_issue(_auto(), confirmed_path=confirmed,
                                       verbose=False) == "skipped"


def test_a_missing_confirmed_file_counts_as_infinitely_stale(tmp_path, monkeypatch):
    """No file at all is the worst case, not the best: nothing has ever been
    confirmed."""
    monkeypatch.setattr(pdd.shutil, "which", lambda _: None)   # gh absent -> skip
    missing = tmp_path / "nope.csv"
    assert pdd.maybe_open_review_issue(_auto(), confirmed_path=missing,
                                       verbose=False) == "skipped"


def test_without_gh_nothing_is_attempted(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pdd.shutil, "which", lambda _: None)
    monkeypatch.setattr(pdd, "_gh", lambda *a, **k: calls.append(a))
    stale = tmp_path / "confirmed.csv"
    stale.write_text("x", encoding="utf-8")
    os.utime(stale, (0, 0))
    assert pdd.maybe_open_review_issue(_auto(), confirmed_path=stale,
                                       verbose=False) == "skipped"
    assert not calls


def _stale_file(tmp_path):
    path = tmp_path / "confirmed.csv"
    path.write_text("x", encoding="utf-8")
    os.utime(path, (0, 0))
    return path


def test_a_new_issue_is_filed_when_none_exists(tmp_path, monkeypatch):
    seen = []

    def fake_gh(*args, **kwargs):
        seen.append(args)
        if args[1] == "list":
            return "[]"
        return "https://github.com/x/y/issues/1"

    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh", fake_gh)
    result = pdd.maybe_open_review_issue(_auto(), confirmed_path=_stale_file(tmp_path),
                                         verbose=False)
    assert result == "filed"
    assert any(a[1] == "create" for a in seen)


def test_an_active_issue_is_left_alone(tmp_path, monkeypatch):
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    listing = json.dumps([{"number": 7, "title": pdd.DEDUP_ISSUE_MARKER + " x",
                           "updatedAt": recent}])
    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh", lambda *a, **k: listing if a[1] == "list" else "ok")
    assert pdd.maybe_open_review_issue(_auto(), confirmed_path=_stale_file(tmp_path),
                                       verbose=False) == "skipped"


def test_a_stale_issue_gets_a_comment(tmp_path, monkeypatch):
    old = "2020-01-01T00:00:00Z"
    listing = json.dumps([{"number": 7, "title": pdd.DEDUP_ISSUE_MARKER + " x",
                           "updatedAt": old}])
    seen = []

    def fake_gh(*args, **kwargs):
        seen.append(args)
        return listing if args[1] == "list" else "https://github.com/x/y/issues/7#c1"

    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh", fake_gh)
    assert pdd.maybe_open_review_issue(_auto(), confirmed_path=_stale_file(tmp_path),
                                       verbose=False) == "commented"
    assert any(a[1] == "comment" for a in seen)


def test_an_unmarked_issue_is_not_treated_as_ours(tmp_path, monkeypatch):
    """Someone else's open issue must not suppress the nudge."""
    listing = json.dumps([{"number": 9, "title": "unrelated",
                           "updatedAt": "2020-01-01T00:00:00Z"}])
    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh",
                        lambda *a, **k: listing if a[1] == "list" else "url")
    assert pdd.maybe_open_review_issue(_auto(), confirmed_path=_stale_file(tmp_path),
                                       verbose=False) == "filed"


def test_gh_failure_never_raises(tmp_path, monkeypatch):
    """A dataset build that already succeeded must not fail on a nudge."""
    monkeypatch.setattr(pdd.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pdd, "_gh", lambda *a, **k: None)
    assert pdd.maybe_open_review_issue(_auto(), confirmed_path=_stale_file(tmp_path),
                                       verbose=False) == "skipped"


def test_issue_filing_is_off_by_default():
    """build() runs on every FLoRA tab load; a default of True would file issues
    when somebody opens a page."""
    import inspect
    assert inspect.signature(pdd.resolve).parameters["open_review_issue"].default is False
