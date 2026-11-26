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
    """Execute _processSubmitQueue in Node to verify real queue progression:
    1. A terminal 4xx item without a server failure_stamp is parked (blocked=true)
       without calling /skip or /submission-failures/release, and a later item in
       the queue still submits cleanly.
    2. A terminal failure WITH a server failure_stamp calls
       /submission-failures/release with that stamp before dequeuing the item."""
    import json
    import subprocess
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "docs" / "app.js").read_text(encoding="utf-8")
    ownership_src = js[js.index("const _queueOwner ="):js.index("const _myFailed =")]
    fn_src = (
        "async function _processSubmitQueue()"
        + js.split("async function _processSubmitQueue()", 1)[1].split("function _celebratePoints", 1)[0]
    )
    harness = f"""
const calls = [];
const toasts = [];
let stillQueuedDuringRelease = null;
let _submitProcessing = false;
const SUBMIT_MAX_ATTEMPTS = 3;
const state = {{ coder: {{ coder_id: "v1" }} }};
let _submitQueue = [
  {{ key: "k_other", owner_id: "v2", paper: "Other Validator", attempts: 0, blocked: false, payload: {{ submission_id: "s_other", kind: "ok" }} }},
  {{ key: "k1", owner_id: "v1", paper: "Bad First", attempts: 0, blocked: false, payload: {{ submission_id: "s1", kind: "reject4xx" }} }},
  {{ key: "k2", owner_id: "v1", paper: "Stamped Second", attempts: 2, blocked: false, payload: {{ submission_id: "s2", kind: "rollback500" }} }},
  {{ key: "k3", owner_id: "v1", paper: "Good Third", attempts: 0, blocked: false, payload: {{ submission_id: "s3", kind: "ok" }} }},
];
let _failedSubmits = [];
{ownership_src}
const _newSubmissionId = () => "new-id";
const _persistSubmits = () => {{}};
const _persistFailed = () => {{}};
const _updatePendingIndicator = () => {{}};
const _celebrateQueued = () => {{}};
const refreshStats = async () => {{}};
const refreshLeaderboard = async () => {{}};
const showToast = (m) => toasts.push(m);
const _sleep = async () => {{}};
const _isTerminalErr = (e) => !!(e && e.terminal);
const _slotNoLongerOwned = () => false;
const _dequeueSubmit = (item) => {{
  const i = _submitQueue.indexOf(item);
  if (i !== -1) _submitQueue.splice(i, 1);
}};
async function api(path, method, body) {{
  calls.push({{ path, method, body }});
  if (path === "/judge") {{
    if (body.kind === "reject4xx") {{
      const err = new Error("Bad request");
      err.terminal = true;
      throw err;
    }}
    if (body.kind === "rollback500") {{
      const err = new Error("DB error");
      err.detail = {{ code: "judgement_save_failed", failure_stamp: "stamp-xyz" }};
      throw err;
    }}
    return {{ points_earned: 1 }};
  }}
  if (path === "/submission-failures/release") {{
    stillQueuedDuringRelease = _submitQueue.some((it) => it.key === "k2");
    return {{ status: "released" }};
  }}
  throw new Error("unexpected path " + path);
}}
{fn_src}
_processSubmitQueue().then(() => {{
  console.log(JSON.stringify({{ calls, remaining: _submitQueue, failed: _failedSubmits, stillQueuedDuringRelease }}));
}}).catch((e) => {{ console.error(e); process.exit(1); }});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True)
    result = json.loads(proc.stdout)

    paths = [c["path"] for c in result["calls"]]
    assert "/skip" not in paths
    # Foreign-owned item k_other is NEVER sent and stays queued; k1 parked; k2 minted stamp &
    # stayed queued until release; k3 submitted cleanly!
    judge_ids = [c["body"]["submission_id"] for c in result["calls"] if c["path"] == "/judge"]
    assert judge_ids == ["s1", "s2", "s3"]
    releases = [c["body"] for c in result["calls"] if c["path"] == "/submission-failures/release"]
    assert releases == [{"failure_stamp": "stamp-xyz"}]
    assert result["stillQueuedDuringRelease"] is True
    assert [it["key"] for it in result["remaining"]] == ["k_other", "k1"]
    assert result["remaining"][0]["blocked"] is False
    assert result["remaining"][1]["blocked"] is True
    assert result["remaining"][1]["rejected"] is True
    assert len(result["failed"]) == 1 and result["failed"][0]["paper"] == "Stamped Second"
