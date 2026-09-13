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
])
def test_preprint_dois_are_recognised(doi):
    assert pdd.is_preprint_doi(doi)


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
    frame = _pair_frame(outcome=["successful", "failed"])
    out, conflicts = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert out["outcome"].iloc[0] == "mixed"
    assert len(conflicts) == 1


def test_an_unmixable_clash_is_retained_for_validation():
    """Kept as "A || B" so validate_flora reports it — merging it into something
    valid would hide a source-data mistake."""
    frame = _pair_frame(outcome=["successful", "computationally reproducible"])
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert pdd.OUTCOME_CLASH_SEP in out["outcome"].iloc[0]


def test_absorbed_record_ids_survive_the_merge():
    """flora_registry needs them to recognise the record if a reviewer later
    promotes one of the absorbed rows."""
    out, _ = pdd.merge_doi_pair_dups(_pair_frame(), verbose=False)
    assert "r2" in out["merged_record_ids"].iloc[0]


def test_rows_without_a_pair_are_untouched():
    frame = _pair_frame(doi_r=[None, None])
    out, _ = pdd.merge_doi_pair_dups(frame, verbose=False)
    assert len(out) == 2


# ── review issue filing ───────────────────────────────────────────────────────

def _auto(n=3):
    return [{"resolution": "auto: drop doi_2", "applied_action": "auto_keep_1",
             "doi_1": f"10.1/a{i}", "doi_2": f"10.2/b{i}",
             "side": "replication", "title_sim": 0.95} for i in range(n)]


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
