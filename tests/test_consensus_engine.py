import json
import pytest
from unittest.mock import MagicMock, patch


BASE_RECORD = {
    "record_id": "rec-001",
    "doi_r": "10.1000/rep", "study_r": "Rep Study", "year_r": "2022",
    "url_r": "", "ref_r": "", "abstract_r": "We replicated X.",
    "doi_o": "10.1000/orig", "study_o": "Orig Study", "year_o": "2018",
    "url_o": "https://doi.org/10.1000/orig", "ref_o": "",
    "type": "replication", "outcome": "success",
    "outcome_quote": "We replicated.", "out_quote_source": "abstract",
    "validation_status": "validation_inprogress",
}

H1_AGREE = {
    "validator_slot": "human_1", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H2_AGREE = {
    "validator_slot": "human_2", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H1_DISAGREE = {
    "validator_slot": "human_1", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H2_DISAGREE = {
    "validator_slot": "human_2", "type_check": "correct",
    "original_check": "correct", "outcome_check": "incorrect",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": "failure", "corrected_type": None,
}

LLM_AGREE_ALL = {
    "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
    "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None,
    "context": "sanity_check", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_AGREE_H1 = {
    "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
    "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None,
    "context": "tiebreaker", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_AGREE_H2 = {
    "type_check": "correct", "original_check": "correct", "outcome_check": "incorrect",
    "corrected_outcome": "failure", "corrected_doi_o": None, "corrected_type": None,
    "context": "tiebreaker", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_3WAY = {
    "type_check": "incorrect", "original_check": "correct", "outcome_check": "correct",
    "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": "reproduction",
    "context": "tiebreaker", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_ERROR = {
    "error": "API timeout", "context": "sanity_check", "vote_score": 15,
    "model": "gemini-2.0-flash", "validated_at": "2026-05-14T00:00:00+00:00",
}


def _make_cur(human_rows, record, senior_count=0):
    cur = MagicMock()
    cur.fetchall.return_value = human_rows
    # evaluate_consensus calls fetchone twice: first the unvalidated record,
    # then the senior-validator COUNT(*) row. Return each in turn.
    cur.fetchone.side_effect = [record, {"senior_count": senior_count}]
    return cur


def test_returns_early_when_only_one_human():
    """evaluate_consensus does nothing when only one human slot is complete."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_AGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()


def test_both_agree_no_corrections_sets_validated():
    """Both humans agree with no corrections → validated status."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_AGREE, H2_AGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str
    assert "need_review" not in calls_str


def test_both_agree_llm_errors_still_validates():
    """LLM error during sanity check does not block validation."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_AGREE, H2_AGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_ERROR):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str


def test_both_agree_different_corrections_sets_need_review():
    """Both humans agree on checks but have different corrections → need_review, no LLM."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "corrected_doi_o": "10.1000/a"}
    h2 = {**H2_AGREE, "corrected_doi_o": "10.1000/b"}
    cur = _make_cur([h1, h2], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str


def test_humans_disagree_llm_agrees_h1_sets_validated():
    """Humans disagree; LLM matches H1 → validated with H1 verdict."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_DISAGREE, H2_DISAGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_H1):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str


def test_humans_disagree_llm_agrees_h2_sets_validated():
    """Humans disagree; LLM matches H2 → validated with H2 verdict."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_DISAGREE, H2_DISAGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_H2):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str


def test_humans_disagree_3way_split_sets_need_review():
    """3-way split → need_review."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_DISAGREE, H2_DISAGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_3WAY):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str


def test_humans_disagree_llm_error_sets_need_review():
    """LLM error during tiebreaker → need_review."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_DISAGREE, H2_DISAGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_ERROR):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str


def test_both_agree_same_url_suggestion_flows_to_final():
    """Both humans agree and suggest the same replication URL → final_url_r is written."""
    from consensus_engine import evaluate_consensus
    url = "https://new.example/paper"
    h1 = {**H1_AGREE, "corrected_url_r": url}
    h2 = {**H2_AGREE, "corrected_url_r": url}
    cur = _make_cur([h1, h2], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "final_url_r" in calls_str   # column written
    assert url in calls_str             # with the suggested value
    assert "need_review" not in calls_str


def test_diverging_url_suggestions_set_need_review():
    """Checks agree but humans suggest different URLs → need_review, no LLM call."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "corrected_url_r": "https://a.example"}
    h2 = {**H2_AGREE, "corrected_url_r": "https://b.example"}
    cur = _make_cur([h1, h2], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str


def test_senior_agreement_auto_validates():
    """When a senior validator is involved, agreement auto-validates (no admin step)."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_AGREE, H2_AGREE], BASE_RECORD, senior_count=2)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "INSERT INTO validated" in calls_str   # success path inserts the validated row
    assert "need_review" not in calls_str


# ---------------------------------------------------------------------------
# Outcome-quote source detection
# ---------------------------------------------------------------------------

def test_quote_source_for_found_in_abstract():
    from consensus_engine import quote_source_for
    assert quote_source_for("we replicated", "We Replicated X, fully.") == "abstract"


def test_quote_source_for_not_in_abstract():
    from consensus_engine import quote_source_for
    assert quote_source_for("a sentence from the body", "Unrelated abstract.") == "full_text"


def test_quote_source_for_empty_quote_is_none():
    from consensus_engine import quote_source_for
    assert quote_source_for("", "Some abstract.") is None
    assert quote_source_for(None, "Some abstract.") is None


def test_resolve_quote_source_keeps_existing_when_agreed():
    """No validator suggestion → trust the extracted source, don't re-check."""
    from consensus_engine import _resolve_quote_source
    rec = {"abstract_r": "Totally unrelated.", "outcome_quote": "We replicated.",
           "out_quote_source": "full_text"}
    assert _resolve_quote_source(rec, []) == "full_text"


def test_resolve_quote_source_checks_longest_suggestion():
    """Validators suggested new quotes → longest is checked against the abstract."""
    from consensus_engine import _resolve_quote_source
    rec = {"abstract_r": "We found a strong and lasting effect across samples.",
           "outcome_quote": "old", "out_quote_source": "abstract"}
    suggested = ["a strong effect", "we found a strong and lasting effect"]
    assert _resolve_quote_source(rec, suggested) == "abstract"
    # longest suggestion not in abstract → full_text
    suggested2 = ["x", "a paraphrase that is nowhere in the abstract text"]
    assert _resolve_quote_source(rec, suggested2) == "full_text"
