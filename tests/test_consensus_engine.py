import json
import pytest
from unittest.mock import MagicMock, patch


BASE_RECORD = {
    "record_id": "rec-001",
    "doi_r": "10.1000/rep", "study_r": "1", "title_r": "Rep Study", "year_r": "2022",
    "url_r": "", "ref_r": "", "abstract_r": "We replicated X.",
    "doi_o": "10.1000/orig", "study_o": "2", "title_o": "Orig Study", "year_o": "2018",
    "url_o": "https://doi.org/10.1000/orig", "ref_o": "",
    "type": "replication", "outcome": "success",
    "outcome_quote": "We replicated.", "out_quote_source": "abstract",
    "validation_status": "validation_inprogress",
}

H1_AGREE = {
    "validator_slot": "human_1", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_title_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H2_AGREE = {
    "validator_slot": "human_2", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_title_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H1_DISAGREE = {
    "validator_slot": "human_1", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_title_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H2_DISAGREE = {
    "validator_slot": "human_2", "type_check": "correct",
    "original_check": "correct", "outcome_check": "incorrect",
    "corrected_doi_o": None, "corrected_title_o": None,
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


def _make_cur(human_rows, record, senior_count=0, senior_reject=0, experienced_count=0):
    cur = MagicMock()
    cur.fetchall.return_value = human_rows
    # evaluate_consensus calls fetchone in order: the unvalidated record, the
    # senior-reject-guard COUNT(*), the auto-validation stats (how many of the two
    # are Trusted/Senior, how many experienced), and — only when a rule applies —
    # the duplicate check (None: no other validated entry has this identity).
    cur.fetchone.side_effect = [
        record,
        {"n": senior_reject},
        {"trusted_count": senior_count, "experienced_count": experienced_count},
        None,
    ]
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


def test_unsure_routes_to_need_review():
    """A validator answering 'Can't tell' (unsure) sends the record to review, no LLM,
    even when the raw checks otherwise agree."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "additional_checks": {"was_unsure_original": True}}
    cur = _make_cur([h1, H2_AGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str
    assert "consensus_reached" not in calls_str      # not auto-resolved
    assert "INSERT INTO validated" not in calls_str  # never written to the export table


def test_diverging_published_doi_sets_need_review():
    """Only one validator supplies a published-article DOI → treated like any other
    correction conflict: need_review, no LLM call."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "doi_r_published": "10.1234/published"}
    cur = _make_cur([h1, H2_AGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str
    assert "INSERT INTO validated" not in calls_str


def test_agreeing_published_doi_flows_to_final():
    """Both validators supply the same published DOI → it is written to the record."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "doi_r_published": "10.1234/published"}
    h2 = {**H2_AGREE, "doi_r_published": "10.1234/published"}
    cur = _make_cur([h1, h2], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "doi_r_published" in calls_str      # column written
    assert "10.1234/published" in calls_str    # with the agreed value
    assert "need_review" not in calls_str


def test_llm_matches_uncertain_never_matches_human():
    """'uncertain' must never equal a human's 'correct'/'incorrect' — this is the
    whole mechanism that lets an unconfident LLM fall through to need_review
    without any special-casing in _llm_matches itself."""
    from consensus_engine import _llm_matches
    llm = {"type_check": "uncertain", "original_check": "correct", "outcome_check": "correct"}
    human = {"type_check": "correct", "original_check": "correct", "outcome_check": "correct"}
    assert _llm_matches(llm, human) is False


def test_tiebreaker_llm_uncertain_on_disputed_field_sets_need_review():
    """Humans disagree; the LLM is 'uncertain' (not 'correct'/'incorrect') on the
    very field they disagree on → LLM matches neither → need_review, same as a
    genuine 3-way split. Confirms an unconfident LLM can no longer accidentally
    resolve a tiebreak by defaulting to 'correct'."""
    from consensus_engine import evaluate_consensus
    llm_uncertain = {
        "type_check": "correct", "original_check": "correct", "outcome_check": "uncertain",
        "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None,
        "context": "tiebreaker", "model": "gemini-3.1-flash-lite", "vote_score": 15,
        "validated_at": "2026-08-16T00:00:00+00:00", "notes": "",
    }
    cur = _make_cur([H1_DISAGREE, H2_DISAGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=llm_uncertain):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str
    assert "INSERT INTO validated" not in calls_str


def test_published_doi_formats_agree():
    """The same DOI pasted as a resolver link vs bare, different case → still
    agreement (normalized compare), not a spurious conflict."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "doi_r_published": "10.1234/Published"}
    h2 = {**H2_AGREE, "doi_r_published": "https://doi.org/10.1234/published"}
    cur = _make_cur([h1, h2], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" not in calls_str
    assert "doi_r_published" in calls_str


def test_quote_flag_routes_to_need_review():
    """A submission auto-flagged by the frontend quote gate (outcome quote not
    found in the abstract) sends the record to review, no LLM, even on agreement."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "additional_checks": {"quote_not_in_abstract": True}}
    cur = _make_cur([h1, H2_AGREE], BASE_RECORD)
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str
    assert "INSERT INTO validated" not in calls_str


def test_quote_flag_stops_senior_auto_validate():
    """The quote flag must also stop the senior auto-validate shortcut — nothing
    with a suspect quote reaches the export table unreviewed."""
    from consensus_engine import evaluate_consensus
    h2 = {**H2_AGREE, "additional_checks": {"quote_not_in_abstract": True}}
    cur = _make_cur([H1_AGREE, h2], BASE_RECORD, senior_count=2)
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str
    assert "INSERT INTO validated" not in calls_str


def test_quote_flag_tolerates_json_string_column():
    """additional_checks arriving as a raw JSON string (no jsonb adapter) still routes."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "additional_checks": json.dumps({"quote_not_in_abstract": True})}
    cur = _make_cur([h1, H2_AGREE], BASE_RECORD)
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
    assert "auto_validated_rule = %s" in calls_str and "'trusted'" in calls_str


def _status_of(cur):
    """The validation_status the evaluation wrote."""
    for call in cur.execute.call_args_list:
        sql, *rest = call.args
        if sql.startswith("UPDATE unvalidated SET validation_status"):
            return rest[0][0]
    return None


def test_one_trusted_validator_auto_validates():
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_AGREE, H2_AGREE], BASE_RECORD, senior_count=1)
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    assert _status_of(cur) == "validated"


def test_two_experienced_validators_auto_validate_one_does_not():
    from consensus_engine import evaluate_consensus
    for experienced, status in ((2, "validated"), (1, "consensus_reached")):
        cur = _make_cur([H1_AGREE, H2_AGREE], BASE_RECORD, experienced_count=experienced)
        with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
            evaluate_consensus(cur, "rec-001")
        assert _status_of(cur) == status, experienced


@pytest.mark.parametrize("llm", [LLM_ERROR, {**LLM_AGREE_ALL, "outcome_check": "incorrect"},
                                 {**LLM_AGREE_ALL, "outcome_check": "uncertain"}])
def test_no_auto_validation_unless_the_ai_check_agrees(llm):
    """Even a Senior's agreement waits for an admin when the AI check failed or
    disagrees (the old Senior shortcut ignored it)."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_AGREE, H2_AGREE], BASE_RECORD, senior_count=2, experienced_count=2)
    with patch("consensus_engine.run_llm_validation", return_value=llm):
        evaluate_consensus(cur, "rec-001")
    assert _status_of(cur) == "consensus_reached"
    assert "INSERT INTO validated" not in str(cur.execute.call_args_list)


@pytest.mark.parametrize("fix", [
    {},                                              # nothing supplied: the wrong original
    {"corrected_doi_o": "10.1000/right"},            # keeps the wrong paper's title
    {"corrected_title_o": "The right original"},     # keeps the wrong paper's DOI and work
])
def test_a_disputed_original_always_waits_for_an_admin(fix):
    """Consensus fills in only what the validators supplied and keeps the rest of
    the original both said is wrong."""
    from consensus_engine import evaluate_consensus
    wrong = {"original_check": "incorrect", **fix}
    llm = {**LLM_AGREE_ALL, "original_check": "incorrect"}
    cur = _make_cur([{**H1_AGREE, **wrong}, {**H2_AGREE, **wrong}], BASE_RECORD, senior_count=2)
    with patch("consensus_engine.run_llm_validation", return_value=llm):
        evaluate_consensus(cur, "rec-001")
    assert _status_of(cur) == "consensus_reached"


def test_a_duplicate_of_a_validated_entry_waits_for_an_admin():
    """Publishing it would take over the other entry's row; an admin merges them."""
    from consensus_engine import evaluate_consensus
    cur = _make_cur([H1_AGREE, H2_AGREE], BASE_RECORD, senior_count=2)
    cur.fetchone.side_effect = [BASE_RECORD, {"n": 0},
                                {"trusted_count": 2, "experienced_count": 0},
                                {"record_id": "rec-other"}]
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    assert _status_of(cur) == "consensus_reached"
    assert "INSERT INTO validated" not in str(cur.execute.call_args_list)


@pytest.mark.parametrize("change", [
    {"additional_checks": {"was_unsure_outcome": True}},
    {"additional_checks": {"quote_not_in_abstract": True}},
    {"additional_checks": {"senior_reject": True}},
    {"outcome_check": "incorrect", "corrected_outcome": "failure"},     # disagree
])
def test_the_rule_applies_to_plain_agreement_only(change):
    from consensus_engine import auto_validation_rule
    assert auto_validation_rule(H1_AGREE, H2_AGREE, LLM_AGREE_ALL, 1, 2) == "trusted"
    assert auto_validation_rule(H1_AGREE, {**H2_AGREE, **change}, LLM_AGREE_ALL, 1, 2) is None


def test_neither_type_is_never_auto_validated():
    from consensus_engine import auto_validation_rule
    neither = {"type_check": "incorrect", "corrected_type": "not_validation"}
    h1, h2 = {**H1_AGREE, **neither}, {**H2_AGREE, **neither}
    llm = {**LLM_AGREE_ALL, "type_check": "incorrect"}
    assert auto_validation_rule(h1, h2, llm, 2, 2) is None


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
