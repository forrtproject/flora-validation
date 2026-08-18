import json
import pytest
from unittest.mock import patch, MagicMock


SAMPLE_RECORD = {
    "record_id": "abc-123",
    "doi_r": "10.1000/replication",
    "abstract_r": "We attempted to replicate Smith et al. (2020). Our results failed to replicate the original finding.",
    "doi_o": "10.1000/original",
    "study_o": "Original Study Title",
    "year_o": "2020",
    "type": "replication",
    "outcome": "success",
    "outcome_quote": "We successfully replicated the finding.",
}


def test_run_llm_validation_returns_correct_keys():
    """run_llm_validation returns a dict with all required keys."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "correct",
        "original_check": "correct",
        "outcome_check": "incorrect",
        "corrected_outcome": "failure",
        "corrected_doi_o": None,
        "corrected_type": None,
        "notes": "Abstract says failed to replicate but outcome coded as success",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert result["type_check"] in ("correct", "incorrect")
    assert result["original_check"] in ("correct", "incorrect")
    assert result["outcome_check"] in ("correct", "incorrect")
    assert result["context"] == "sanity_check"
    assert "model" in result
    assert "validated_at" in result
    assert "vote_score" in result


def test_run_llm_validation_tiebreaker_context():
    """context field is stored correctly for tiebreaker calls."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "correct",
        "original_check": "correct",
        "outcome_check": "correct",
        "corrected_outcome": None,
        "corrected_doi_o": None,
        "corrected_type": None,
        "notes": "",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="tiebreaker")

    assert result["context"] == "tiebreaker"


def test_run_llm_validation_handles_api_error():
    """API errors are caught; result has error key."""
    from llm_validator import run_llm_validation
    with patch("llm_validator._call_gemini", side_effect=Exception("API timeout")):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert "error" in result
    assert result["context"] == "sanity_check"


def test_run_llm_validation_handles_malformed_json():
    """Malformed JSON from LLM is caught; result has error key."""
    from llm_validator import run_llm_validation
    with patch("llm_validator._call_gemini", return_value="not valid json {{"):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert "error" in result


def test_missing_check_becomes_uncertain_not_correct():
    """A check field the model omits must default to 'uncertain' — the old
    behavior silently defaulted to 'correct', which could win a tiebreak on a
    guess rather than genuine confidence."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "original_check": "correct", "outcome_check": "correct",
        "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None, "notes": "",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")
    assert result["type_check"] == "uncertain"


def test_invalid_check_value_coerced_to_uncertain():
    """A malformed/unexpected check value (not correct/incorrect/uncertain) is
    coerced to 'uncertain', never silently accepted or defaulted to 'correct'."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "not sure honestly", "original_check": "correct", "outcome_check": "correct",
        "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None, "notes": "",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")
    assert result["type_check"] == "uncertain"


def test_offschema_outcome_synonym_is_mapped():
    """A near-miss label the model invents (e.g. 'inconsistent') is mapped onto
    the real schema category ('failed') rather than passed through verbatim —
    this is the exact scenario from FLoRA issue #4."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "correct", "original_check": "correct", "outcome_check": "incorrect",
        "corrected_outcome": "inconsistent", "corrected_doi_o": None, "corrected_type": None,
        "notes": "Abstract says results were inconsistent with the previous study.",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="tiebreaker")
    assert result["corrected_outcome"] == "failed"
    assert result["outcome_check"] == "incorrect"


def test_unmappable_outcome_is_dropped_and_check_downgraded():
    """A suggestion with no synonym mapping is never passed through as an
    off-schema string: corrected_outcome becomes null, the check is downgraded
    to 'uncertain' (a confident 'incorrect' with no valid fix is misleading),
    and the raw suggestion is preserved in notes so nothing is silently lost."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "correct", "original_check": "correct", "outcome_check": "incorrect",
        "corrected_outcome": "sort of replicated with caveats", "corrected_doi_o": None,
        "corrected_type": None, "notes": "Complicated result.",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")
    assert result["corrected_outcome"] is None
    assert result["outcome_check"] == "uncertain"
    assert "sort of replicated with caveats" in result["notes"]


def test_reproduction_axis_rejects_a_value_from_the_other_axis():
    """The two axes have separate vocabularies. A robustness value offered for the
    computational axis looks plausible and is nonsense, so it is dropped and the
    check downgraded rather than stored."""
    result = _repro_result(computation="robust")
    assert result["corrected_outcome_computation"] is None
    assert result["outcome_check"] == "uncertain"
    assert "not a valid axis value" in result["notes"]


def test_dropped_outcome_note_survives_verbose_model_notes():
    """The 'suggestion dropped' flag must never be crowded out of the 200-char
    notes budget by a verbose model `notes` field — truncating notes first and
    appending second would silently lose exactly the information this feature
    exists to preserve."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "correct", "original_check": "correct", "outcome_check": "incorrect",
        "corrected_outcome": "a totally invented category", "corrected_doi_o": None,
        "corrected_type": None,
        "notes": "x" * 250,   # long enough to fill the 200-char budget alone
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")
    assert len(result["notes"]) <= 200
    assert "a totally invented category" in result["notes"]


def test_blank_doi_o_gets_an_honest_sentinel_not_a_blank_line():
    """A DOI-less original (book, chapter, pre-DOI paper) must not render as a
    blank 'Original study DOI:' line in the prompt — that reads as a data-quality
    gap and could bias original_check. It should say plainly that none exists."""
    from llm_validator import run_llm_validation
    record = {**SAMPLE_RECORD, "doi_o": ""}
    captured = {}

    def fake_call(prompt, record_type):
        captured["prompt"] = prompt
        return json.dumps({
            "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
            "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None, "notes": "",
        })

    with patch("llm_validator._call_gemini", side_effect=fake_call):
        run_llm_validation(record, context="sanity_check")

    assert "Original study DOI: \n" not in captured["prompt"]
    assert "no registered DOI" in captured["prompt"]


def _repro_result(computation=None, robustness=None, notes=""):
    """Run a reproduction record through the validator with the given axis
    suggestions. Reproductions are judged on two independent axes — there is no
    compound label to correct."""
    from llm_validator import run_llm_validation
    repro_record = {**SAMPLE_RECORD, "type": "reproduction"}
    mock_response_text = json.dumps({
        "type_check": "correct", "original_check": "correct", "outcome_check": "incorrect",
        "corrected_outcome_computation": computation,
        "corrected_outcome_robustness": robustness,
        "corrected_doi_o": None, "corrected_type": None, "notes": notes,
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        return run_llm_validation(repro_record, context="sanity_check")


def test_reproduction_axes_pass_through_independently():
    """Both axes are carried, and neither is derived from the other."""
    result = _repro_result(computation="computational issues", robustness="robust")
    assert result["corrected_outcome_computation"] == "computational issues"
    assert result["corrected_outcome_robustness"] == "robust"


def test_reproduction_can_correct_one_axis_and_leave_the_other():
    """The axes are independent: disagreeing about the computation says nothing
    about robustness, so a null there means 'no correction', not 'unknown'."""
    result = _repro_result(computation="technical failure")
    assert result["corrected_outcome_computation"] == "technical failure"
    assert result["corrected_outcome_robustness"] is None


def test_reproduction_never_returns_a_compound_label():
    """The joined string is retired — the axes ARE the judgement."""
    result = _repro_result(computation="computationally reproducible", robustness="robust")
    assert result["corrected_outcome"] is None


def test_reproduction_invented_axis_value_is_dropped():
    """Validation must not become a licence to accept anything."""
    result = _repro_result(computation="mostly reproducible-ish")
    assert result["corrected_outcome_computation"] is None
    assert "not a valid axis value" in result["notes"]


def test_technical_failure_is_accepted_before_the_extractor_emits_it():
    """It is in the codebook but never yet written to a CSV. The model may reach it
    first, and dropping a correct judgement for that reason would be wrong."""
    assert _repro_result(computation="technical failure")["corrected_outcome_computation"] \
        == "technical failure"


def test_run_llm_validation_retries_once_on_failure():
    """_call_gemini is called twice when first call fails then succeeds."""
    from llm_validator import run_llm_validation
    good_response = json.dumps({
        "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
        "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None, "notes": "",
    })
    with patch("llm_validator._call_gemini", side_effect=[Exception("transient"), good_response]) as mock_call:
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert mock_call.call_count == 2
    assert "error" not in result
