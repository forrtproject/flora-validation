"""Behavioural tests for skip request validation and untouched-record retirement."""
import pytest
from pydantic import ValidationError

from csv_to_db import _is_retirable
from tests.test_dashboard_split import flora


def test_skip_request_defaults_to_legacy_reason_for_rolling_deployments():
    req = flora.SkipRequest(record_id="00000000-0000-0000-0000-000000000001")
    assert req.reason_code == flora.LEGACY_SKIP_REASON == "prefer_another"
    assert "prefer_another" not in flora.SKIP_COMMENT_REQUIRED_CODES


def test_skip_request_rejects_submission_failed_from_public_callers():
    with pytest.raises(ValidationError):
        flora.SkipRequest(
            record_id="00000000-0000-0000-0000-000000000001",
            reason_code="submission_failed",
        )


def test_skip_comment_required_codes_and_thresholds():
    assert flora.SKIP_COMMENT_REQUIRED_CODES == {"eligibility_unclear", "data_quality", "other"}
    assert flora.SKIP_DISTINCT_VALIDATOR_THRESHOLD == 5
    assert flora.SKIP_ISSUE_VALIDATOR_THRESHOLD == 2


def test_only_untouched_records_can_be_retired():
    untouched = {
        "validation_status": "unvalidated",
        "has_activity": False,
        "in_validated": False,
        "assigned": False,
    }
    assert _is_retirable(untouched) is True
    for status in ("rejected", "validated", "validation_inprogress", "need_review"):
        assert _is_retirable({**untouched, "validation_status": status}) is False
    assert _is_retirable({**untouched, "has_activity": True}) is False
    assert _is_retirable({**untouched, "in_validated": True}) is False
    assert _is_retirable({**untouched, "assigned": True}) is False
