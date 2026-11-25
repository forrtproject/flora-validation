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


def test_manual_skip_clears_the_draft_only_after_release_succeeds():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "docs" / "app.js").read_text(encoding="utf-8")
    body = js.split("async function onSkip()", 1)[1].split("async function submitJudgement()", 1)[0]
    request_at = body.index('await api("/skip"')
    clear_at = body.index("_clearDraft(pair.pair_id)")
    assert request_at < clear_at
    failure_path = body[request_at:clear_at]
    assert "catch (e)" in failure_path and "return;" in failure_path
    assert clear_at < body.index("await refreshAll()")


def test_failed_submission_needs_a_server_stamp_and_progresses_past_parked_items():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "docs" / "app.js").read_text(encoding="utf-8")
    body = js.split("async function _processSubmitQueue()", 1)[1].split("function _celebratePoints", 1)[0]
    failure = body.split("if (giveUp)", 1)[1]
    assert 'api("/submission-failures/release"' in failure
    assert "failure_stamp: item.failure_stamp" in failure
    assert 'api("/skip"' not in failure
    assert "if (!item.failure_stamp)" in failure
    assert "_submitQueue.shift()" not in failure
    assert failure.index('api("/submission-failures/release"') < failure.index("_dequeueSubmit(item)")
