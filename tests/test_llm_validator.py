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
