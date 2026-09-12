"""Cross-layer regression coverage for durable validator skip feedback."""
import re
import time
from pathlib import Path
from unittest.mock import patch

from cleanup_orphans import _is_deletable_orphan


ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "docs" / "style.css").read_text(encoding="utf-8")
CLEANUP = (ROOT / "cleanup_orphans.py").read_text(encoding="utf-8")


def _function_body(source: str, name: str, next_name: str) -> str:
    return source.split(f"def {name}(", 1)[1].split(f"def {next_name}(", 1)[0]


def test_skip_history_has_an_append_only_table_with_legacy_compatible_reasons():
    assert "CREATE TABLE IF NOT EXISTS validation_skips" in SCHEMA
    assert "record_id     UUID        NOT NULL REFERENCES unvalidated(record_id)" in SCHEMA
    assert "validator_id  INTEGER     NOT NULL REFERENCES validators(id)" in SCHEMA
    assert "queue_id      UUID        REFERENCES validation_queue(queue_id)" in SCHEMA
    for reason in (
        "prefer_another",
        "inaccessible",
        "eligibility_unclear",
        "data_quality",
        "interpretation_unclear",
        "other",
        "submission_failed",
    ):
        assert f"'{reason}'" in SCHEMA
    assert "validation_skips_reason_code_check" in SCHEMA
    assert "idx_validation_skips_escalation" in SCHEMA

    # Historical rows remain readable, but the public request model cannot mint
    # new events that pretend to be automatic submission failures.
    skip_model = APP.split("class SkipRequest(BaseModel):", 1)[1].split(
        "class SubmissionFailureReleaseRequest", 1
    )[0]
    assert '"submission_failed"' not in skip_model


def test_skip_release_and_history_insert_are_one_conditional_transaction():
    release = _function_body(APP, "_release_skipped_slot", "_restore_status_after_skip")
    assert "AND is_validated = FALSE" in release
    assert "RETURNING queue_id, validator_slot" in release

    body = _function_body(APP, "skip_pair", "senior_reject")
    assert "SELECT record_id FROM unvalidated WHERE record_id = %s FOR UPDATE" in body
    assert body.index("FOR UPDATE") < body.index("_release_skipped_slot")
    assert "released = _release_skipped_slot" in body
    assert 'raise HTTPException(409, "This record is no longer assigned to you")' in body
    assert body.index("_release_skipped_slot") < body.index("_insert_skip_event")
    assert body.index("_insert_skip_event") < body.index("skipped_count = skipped_count + 1")
    assert 'if req.reason_code == "inaccessible"' in body
    assert "restricted_access = TRUE" in body


def test_judgement_and_skip_use_record_first_lock_order():
    judge = _function_body(APP, "judge", "skip_pair")
    assert "SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE" in judge
    assert judge.index("FROM unvalidated") < judge.index("FROM validation_queue")

    restricted = _function_body(APP, "report_restricted", "my_assignments")
    assert "SELECT record_id FROM unvalidated WHERE record_id = %s FOR UPDATE" in restricted
    assert restricted.index("FOR UPDATE") < restricted.index("_release_skipped_slot")


def test_skip_comment_rules_are_enforced_server_side():
    assert 'SKIP_COMMENT_REQUIRED_CODES = {"eligibility_unclear", "data_quality", "other"}' in APP
    body = _function_body(APP, "skip_pair", "senior_reject")
    assert "len(comment) > 1000" in body
    assert "req.reason_code in SKIP_COMMENT_REQUIRED_CODES and not comment" in body


def test_admin_skip_panel_uses_distinct_validator_thresholds():
    assert "SKIP_DISTINCT_VALIDATOR_THRESHOLD = 5" in APP
    assert "SKIP_ISSUE_VALIDATOR_THRESHOLD = 2" in APP
    assert "COUNT(DISTINCT vs.validator_id) > {SKIP_DISTINCT_VALIDATOR_THRESHOLD}" in APP
    assert ") >= {SKIP_ISSUE_VALIDATOR_THRESHOLD}" in APP
    assert '"skipped":          f"WHERE {_SKIP_PANEL_SQL}"' in APP
    assert '"skipped": c_skipped' in APP
    assert 'entry["skip_review_required"]' in APP
    assert "const skipEscalated = !!e.skip_review_required" in JS


def test_admin_detail_returns_named_skip_history_and_summary():
    body = _function_body(APP, "admin_entry_detail", "admin_flag_queue")
    assert "FROM validation_skips vs" in body
    assert "JOIN validators v ON v.id = vs.validator_id" in body
    assert "v.handle AS validator_name" in body
    assert '"skip_history": skip_history' in body
    assert '"skip_summary": skip_summary' in body
    assert "FROM submission_failure_releases sfr" in body
    assert '"submission_failure_history": submission_failure_history' in body


def test_orphan_retention_matches_admin_decisions_judgements_and_final_rows():
    assert _is_deletable_orphan("unvalidated", False) is True
    assert _is_deletable_orphan("unvalidated", True) is False
    # The admin UI calls the rejected state "Excluded".
    assert _is_deletable_orphan("rejected", False) is False
    assert _is_deletable_orphan("excluded", False) is False
    assert _is_deletable_orphan("validated", False) is False
    assert _is_deletable_orphan("unvalidated", False, True) is False
    # An assignment can set this workflow status without a submitted judgement;
    # assignment alone is explicitly not a retention decision.
    assert _is_deletable_orphan("validation_inprogress", False) is True
    assert _is_deletable_orphan("validation_inprogress", True) is False
    assert "OR u.validator_1 IS NOT NULL" in CLEANUP
    assert "OR u.validator_2 IS NOT NULL" in CLEANUP
    assert "SELECT 1 FROM validated v" in CLEANUP
    assert "has_validated_record" in CLEANUP


def test_skips_alone_do_not_protect_an_orphan_and_are_deleted_first():
    assert "FROM validation_skips s" in CLEANUP
    assert "and not skip_count" not in CLEANUP
    assert "DELETE FROM validation_skips WHERE record_id" in CLEANUP
    assert "DELETE FROM assignments WHERE record_id" in CLEANUP
    assert "DELETE FROM validator_messages vm" in CLEANUP
    assert CLEANUP.index("DELETE FROM validation_skips WHERE record_id") < CLEANUP.index(
        "DELETE FROM validation_queue WHERE record_id"
    )


def test_apply_cleanup_freezes_validation_writes_before_safety_check():
    assert "LOCK TABLE unvalidated, validation_queue" in CLEANUP
    assert "validation_queue, validated" in CLEANUP
    assert "validation_skips, submission_failure_releases" in CLEANUP
    assert "record_metadata" in CLEANUP
    assert "assignments, validator_messages" in CLEANUP
    assert "IN EXCLUSIVE MODE NOWAIT" in CLEANUP
    assert CLEANUP.index("LOCK TABLE unvalidated") < CLEANUP.index(
        "SELECT u.record_id, u.pair_id"
    )


def test_skip_reason_sheet_and_collapsed_admin_history_are_wired_end_to_end():
    assert 'id="skip-reason-modal"' in HTML
    for reason in (
        "prefer_another",
        "inaccessible",
        "eligibility_unclear",
        "data_quality",
        "interpretation_unclear",
        "other",
    ):
        assert f'value="{reason}"' in HTML
    assert 'value="submission_failed"' not in HTML
    assert 'data-filter="skipped"' in HTML
    assert 'id="fc-skipped"' in HTML
    assert "showSkipReasonDialog()" in JS
    assert "reason_code: details.reason_code" in JS
    assert "comment: details.comment" in JS
    assert '<details class="admin-skip-history">' in JS
    assert '<details class="admin-skip-history" open>' not in JS
    assert "item.validator_name" in JS
    assert "item.comment" in JS


def test_static_skip_requires_and_persists_the_pair_identity():
    static_skip = JS.split('if (route === "/skip")', 1)[1].split(
        'if (route === "/leaderboard")', 1
    )[0]
    assert "const pairId = body.pair_id" in static_skip
    assert 'body.record_id !== "undefined"' in static_skip
    assert 'throw new Error("Static skip requires a pair_id")' in static_skip
    assert "pair_id: pairId" in static_skip
    assert "reason_code: body.reason_code" in static_skip
    assert "comment: body.comment" in static_skip
    assert 'body.reason_code === "submission_failed"' in static_skip
    assert 'throw new Error("submission_failed is not a public skip reason")' in static_skip


def test_manual_skip_clears_the_draft_only_after_release_succeeds():
    body = JS.split("async function onSkip()", 1)[1].split(
        "async function submitJudgement()", 1
    )[0]
    request_at = body.index('await api("/skip"')
    clear_at = body.index("_clearDraft(pair.pair_id)")

    assert "const pair = state.currentPair" in body
    assert request_at < clear_at
    # The API failure path returns before it can reach draft clearing.
    failure_path = body[request_at:clear_at]
    assert "catch (e)" in failure_path
    assert "await showAlert(e.message);" in failure_path
    assert "return;" in failure_path
    assert clear_at < body.index("await refreshAll()")


def test_failed_submission_needs_a_server_stamp_and_is_not_discarded_early():
    body = JS.split("async function _processSubmitQueue()", 1)[1].split(
        "function _celebratePoints", 1
    )[0]
    failure = body.split("if (giveUp)", 1)[1]

    assert 'api("/submission-failures/release"' in failure
    assert "failure_stamp: item.failure_stamp" in failure
    assert 'api("/skip"' not in failure
    assert "if (!item.failure_stamp)" in failure
    assert "No server-authorized automatic release; pending data kept" in failure
    # A server rejection is reported as one. It will not clear by waiting, so
    # "release pending" would name a release that never happens.
    assert "item.rejected = _isTerminalErr(e)" in failure
    assert "the server rejected it; your work is kept" in failure
    assert failure.index("if (!item.failure_stamp)") < failure.index("item.rejected")
    assert "catch (releaseError)" in failure
    assert "pending data kept" in failure
    assert ".catch(() => {})" not in failure
    # Removal happens only after the release call, whichever way the item is
    # taken off the queue (it is spliced by index now, not shifted, so that a
    # parked item earlier in the queue does not block the ones behind it).
    assert "_submitQueue.shift()" not in failure
    assert failure.index('api("/submission-failures/release"') < failure.index(
        "_submitQueue.splice(idx, 1)"
    )


def test_submission_failure_release_is_scoped_single_use_and_not_a_skip():
    assert "CREATE TABLE IF NOT EXISTS submission_failure_releases" in SCHEMA
    for state in ("save_failed", "released", "slot_closed", "expired"):
        assert f"'{state}'" in SCHEMA
    assert "submission_id   UUID        NOT NULL UNIQUE" in SCHEMA
    assert "stamp_hash      TEXT        NOT NULL UNIQUE" in SCHEMA
    assert "Raw stamps are never persisted" in SCHEMA

    issuer = _function_body(
        APP, "_issue_submission_failure_stamp", "_try_issue_submission_failure_stamp"
    )
    assert "secrets.token_urlsafe(32)" in issuer
    assert 'hashlib.sha256(raw_stamp.encode("utf-8")).hexdigest()' in issuer
    assert "req.submission_id is None" in issuer
    assert "AND validator_id = %s" in issuer
    assert "AND is_validated = FALSE" in issuer
    assert "same_owner" in issuer

    expiry = _function_body(
        APP, "_expire_submission_failure_stamps", "_server_controlled_submission_failure"
    )
    assert "status = 'expired'" in expiry
    assert "status = 'save_failed' AND expires_at <= NOW()" in expiry
    assert "_expire_submission_failure_stamps(cur)" in APP

    release = _function_body(APP, "release_failed_submission", "senior_reject")
    assert "record -> queue -> audit row" in release
    assert release.index("FROM unvalidated") < release.index("FROM validation_queue")
    assert release.index("FROM validation_queue") < release.index(
        "FROM submission_failure_releases", release.index("FROM validation_queue")
    )
    assert "failure_stamp" in release
    assert "stamp_hash" in release
    assert "status = 'released'" in release
    assert "status = 'slot_closed'" in release
    assert "status = 'expired'" in release
    assert "_restore_status_after_skip" in release
    assert "validation_skips" not in release
    assert "SET skipped_count" not in release


def test_client_persists_submission_identity_and_structured_stamp_metadata():
    assert "httpErr.detail = err.detail" in JS
    enqueue = JS.split("function _enqueueSubmit(payload, paper)", 1)[1].split(
        "async function _processSubmitQueue()", 1
    )[0]
    assert "payload.submission_id || _newSubmissionId()" in enqueue
    assert "submission_id: submissionId" in enqueue

    process = JS.split("async function _processSubmitQueue()", 1)[1].split(
        "function _celebratePoints", 1
    )[0]
    assert 'e?.detail?.code === "judgement_save_failed"' in process
    assert "item.failure_stamp = e.detail.failure_stamp" in process
    assert "Network-only failures never mint one" in process


def test_judgement_enter_shortcut_is_disabled_inside_dialogs():
    keyboard = JS.split("/* ---------- Keyboard ---------- */", 1)[1].split(
        "/* ---------- Too-fast guard ---------- */", 1
    )[0]
    modal_guard = "if (document.querySelector('[role=\"dialog\"]:not(.hidden)')) return;"
    assert modal_guard in keyboard
    assert keyboard.index(modal_guard) < keyboard.index('if (e.key === "Enter")')


# ---------------------------------------------------------------------------
# Rolling-deployment safety: an old page and a new page must both be survivable
# ---------------------------------------------------------------------------

def test_skip_accepts_a_request_from_the_previous_frontend(tmp_path):
    """An old page posts no reason_code; rejecting it would strand a validator.

    The old page clears its local draft BEFORE calling /api/skip, so a 422 both
    destroys unsent work and leaves the record claimed.
    """
    model = APP.split("class SkipRequest(BaseModel):", 1)[1].split(
        "class SubmissionFailureReleaseRequest", 1
    )[0]
    assert "] = LEGACY_SKIP_REASON" in model, "reason_code must have a default"
    assert 'LEGACY_SKIP_REASON = "prefer_another"' in APP

    # The legacy default must stay harmless: no comment demanded, and no
    # contribution to the issue-based escalation that pages admins.
    assert '"prefer_another"' not in APP.split(
        "SKIP_COMMENT_REQUIRED_CODES = ", 1
    )[1].split("\n", 1)[0]
    escalation = _function_body(APP, "_skip_summary", "_failure_message")
    assert "prefer_another" not in escalation


def test_skip_response_lets_a_new_page_detect_an_old_backend():
    """New page -> old backend must not claim the context was saved."""
    body = _function_body(APP, "skip_pair", "release_failed_submission")
    assert '"reason_recorded": True' in body

    handler = JS.split("async function onSkip()", 1)[1].split(
        "async function submitJudgement()", 1
    )[0]
    assert "result = await api(\"/skip\"" in handler
    assert "reasonRecorded" in handler
    # The richer confirmations are conditional on that acknowledgement.
    assert handler.index("reasonRecorded") < handler.index("Sent to Restricted access.")
    assert '"Record skipped."' in handler


def test_index_assets_are_content_fingerprinted_and_revalidated():
    """A cached app.js must not outlive the deployment that changed it."""
    from static_assets import FINGERPRINTED_ASSETS, fingerprinted_index

    # The raw file stays valid for the GitHub Pages static build.
    assert '<script src="./app.js"></script>' in HTML
    assert '<link rel="stylesheet" href="./style.css">' in HTML

    served = fingerprinted_index(ROOT / "docs")
    for name in FINGERPRINTED_ASSETS:
        assert f'"./{name}"' not in served
        assert f'"./{name}?v=' in served

    # CDN and font links carry their own versions and must be left alone.
    assert "cdn.jsdelivr.net/npm/marked@12/marked.min.js" in served
    assert "?v=" not in served.split("fonts.googleapis.com", 1)[1].split(">", 1)[0]

    index_route = APP.split("def serve_index()", 1)[1].split("app.mount(", 1)[0]
    assert "fingerprinted_index(DOCS)" in index_route
    assert "no-cache" in index_route
    # The explicit route must be registered before the catch-all static mount.
    assert APP.index("def serve_index()") < APP.index('app.mount("/", StaticFiles')


def test_asset_fingerprint_tracks_content(tmp_path):
    from static_assets import asset_fingerprint, fingerprinted_index

    (tmp_path / "app.js").write_text("console.log(1);", encoding="utf-8")
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="./style.css">'
        '<script src="./app.js"></script>',
        encoding="utf-8",
    )

    first = asset_fingerprint(tmp_path, "app.js")
    assert first == asset_fingerprint(tmp_path, "app.js")
    assert first in fingerprinted_index(tmp_path)

    # Same byte length, and possibly the same filesystem timestamp tick: only
    # the content differs, and that alone must produce a new URL.
    (tmp_path / "app.js").write_text("console.log(2);", encoding="utf-8")
    changed = asset_fingerprint(tmp_path, "app.js")
    assert changed != first
    # Each asset gets its own fingerprint; an unchanged file keeps its URL.
    assert changed != asset_fingerprint(tmp_path, "style.css")

    # A missing asset degrades to a placeholder rather than failing the page.
    (tmp_path / "app.js").unlink()
    assert asset_fingerprint(tmp_path, "app.js") == "0"


def test_a_settled_asset_is_hashed_once_and_then_cached(tmp_path):
    """A deployed file never changes again, so page loads must not re-read it."""
    import os
    import static_assets

    asset = tmp_path / "app.js"
    asset.write_bytes(b"console.log(1);")
    # Age the file past the settle window rather than sleeping through it.
    old_time = time.time() - 3600
    os.utime(asset, (old_time, old_time))
    static_assets._CACHE.pop(asset, None)

    reads = []
    real_read = Path.read_bytes

    def counting_read(self):
        reads.append(self)
        return real_read(self)

    with patch.object(Path, "read_bytes", counting_read):
        first = static_assets.asset_fingerprint(tmp_path, "app.js")
        second = static_assets.asset_fingerprint(tmp_path, "app.js")
        third = static_assets.asset_fingerprint(tmp_path, "app.js")

    assert first == second == third
    assert len(reads) == 1, "a settled asset must be read exactly once"

    # A new deployment writes new bytes; the stale entry must not survive it.
    asset.write_bytes(b"console.log(22);")
    os.utime(asset, (old_time + 1, old_time + 1))
    assert static_assets.asset_fingerprint(tmp_path, "app.js") != first


def test_a_just_written_asset_is_not_cached_on_an_ambiguous_stamp(tmp_path):
    """(mtime, size) cannot separate two same-length writes in one tick."""
    import static_assets

    asset = tmp_path / "app.js"
    asset.write_bytes(b"console.log(1);")
    static_assets._CACHE.pop(asset, None)

    static_assets.asset_fingerprint(tmp_path, "app.js")
    assert asset not in static_assets._CACHE, (
        "a file written moments ago must be re-hashed, not trusted"
    )


def test_a_rejected_submission_is_labelled_distinctly_from_a_pending_release():
    """Both pending views share one label helper, so they cannot disagree."""
    label = JS.split("function _pendingLabel(item) {", 1)[1].split(
        "function _updatePendingIndicator()", 1
    )[0]
    assert 'if (item.rejected) return "rejected by server; kept"' in label
    assert 'if (item.release_error) return "save failed; release pending"' in label
    assert label.index("item.rejected") < label.index("item.release_error")

    # The recovery renderer is the single place where queue labels are applied;
    # both its done-screen and modal surfaces are built from the same rows.
    assert JS.count('"save failed; release pending"') == 1
    renderer = JS.split("function _renderPendingPanel", 1)[1].split(
        "/* ---------- Pair timer cleanup", 1
    )[0]
    assert "_pendingLabel(it)" in renderer
    assert 'queueRows("done")' in renderer
    assert 'queueRows("modal")' in renderer

    # A rotated stamp means the item is recoverable again, not a rejection.
    queue = JS.split("async function _processSubmitQueue()", 1)[1].split(
        "function _celebratePoints", 1
    )[0]
    rotation = queue.split('e?.detail?.code === "judgement_save_failed"', 1)[1]
    assert "item.rejected = false" in rotation.split("const giveUp", 1)[0]


def test_pending_submission_recovery_is_available_from_the_header_and_accessible():
    assert 'id="pending-saves-btn"' in HTML
    assert 'aria-haspopup="dialog"' in HTML
    assert 'aria-controls="pending-submissions-modal"' in HTML
    assert 'id="pending-submissions-modal"' in HTML
    assert 'aria-modal="true"' in HTML
    assert 'aria-labelledby="pending-submissions-title"' in HTML
    assert 'aria-describedby="pending-submissions-intro"' in HTML
    assert 'id="pending-submissions-close"' in HTML
    assert 'aria-label="Close pending judgements"' in HTML

    assert '$("#pending-saves-btn")?.addEventListener("click", openPendingSubmissions)' in JS
    assert '$("#pending-submissions-close")?.addEventListener("click", closePendingSubmissions)' in JS
    assert "if (event.target === event.currentTarget) closePendingSubmissions()" in JS

    key_handler = JS.split("function _onPendingModalKeydown(event)", 1)[1].split(
        "function openPendingSubmissions", 1
    )[0]
    assert 'event.key === "Escape"' in key_handler
    assert 'event.key !== "Tab"' in key_handler
    assert "event.preventDefault()" in key_handler
    assert "last.focus()" in key_handler
    assert "first.focus()" in key_handler

    opener = JS.split("function openPendingSubmissions()", 1)[1].split(
        "function closePendingSubmissions", 1
    )[0]
    closer = JS.split("function closePendingSubmissions()", 1)[1].split(
        "function _updatePendingIndicator", 1
    )[0]
    assert "_pendingModalReturnFocus = document.activeElement" in opener
    assert 'document.addEventListener("keydown", _onPendingModalKeydown)' in opener
    assert 'document.removeEventListener("keydown", _onPendingModalKeydown)' in closer
    assert "returnFocus.focus()" in closer
    assert "#pending-submissions-modal" in CSS
    assert ".pending-recovery-panel" in CSS
    assert "prefers-reduced-motion: reduce" in CSS


def test_blocked_pending_items_can_retry_export_or_be_explicitly_discarded():
    actions = JS.split("function _pendingActions(key, labelId)", 1)[1].split(
        "function _exportPendingSubmission", 1
    )[0]
    assert 'data-act="retry"' in actions
    assert 'data-act="export"' in actions
    assert ">Export JSON</button>" in actions
    assert 'data-act="discard"' in actions
    assert 'data-act="copy"' not in actions

    exporter = JS.split("function _exportPendingSubmission(item)", 1)[1].split(
        "async function _pendingAction", 1
    )[0]
    assert 'format: "flora-pending-judgement"' in exporter
    assert "new Blob" in exporter
    assert "URL.createObjectURL(blob)" in exporter
    assert "link.download = `flora-pending-judgement-${identity}.json`" in exporter
    assert "link.click()" in exporter
    assert "URL.revokeObjectURL(objectUrl)" in exporter
    assert "navigator.clipboard" not in exporter
    assert "failure_stamp" in exporter and "Deliberately omit" in exporter

    action = JS.split("async function _pendingAction(action, key)", 1)[1].split(
        "function _renderPendingPanel", 1
    )[0]
    retry = action.split('if (action === "retry")', 1)[1].split(
        'if (action === "export")', 1
    )[0]
    for stale_field in ("failure_stamp", "failure_id", "failure_expires_at"):
        assert f"delete item.{stale_field}" in retry
    assert "_submitQueue.splice" not in retry

    export = action.split('if (action === "export")', 1)[1].split(
        'if (action === "discard")', 1
    )[0]
    assert "_exportPendingSubmission(item)" in export
    assert "_submitQueue.splice" not in export

    discard = action.split('if (action === "discard")', 1)[1]
    assert discard.index("await showDialog") < discard.index("if (!ok) return")
    assert discard.index("if (!ok) return") < discard.index("_submitQueue.splice(idx, 1)")


def test_a_parked_submission_cannot_block_later_queue_items():
    queue = JS.split("async function _processSubmitQueue()", 1)[1].split(
        "function _celebratePoints", 1
    )[0]
    # The property, not its spelling: the head item is not taken unconditionally,
    # and "blocked" is part of what makes an item ineligible. Ownership was later
    # added to the same predicate, which must not break this guarantee.
    assert re.search(r"_submitQueue\.findIndex\(\(it\) => !it\.blocked\b", queue)
    assert "if (idx === -1) break" in queue
    assert "_submitQueue[0]" not in queue
    assert queue.count("item.blocked = true") >= 2
    # Each parking path loops back to find the next non-blocked item.
    for parked in queue.split("item.blocked = true")[1:]:
        assert "continue;" in parked


def test_one_stuck_submission_does_not_block_the_ones_behind_it():
    """The processor used to stop at the head of the queue and return.

    A server-rejected item can never succeed, so everything queued behind it was
    never sent — and nothing in the UI could clear it.
    """
    body = JS.split("async function _processSubmitQueue()", 1)[1].split(
        "\nfunction _celebratePoints", 1
    )[0]
    # It walks past parked items instead of always taking index 0.
    assert re.search(r"_submitQueue\.findIndex\(\(it\) => !it\.blocked\b", body)
    assert "if (idx === -1) break;" in body
    assert "_submitQueue[idx]" in body
    assert "_submitQueue[0]" not in body
    # No path abandons the loop with the stuck item still at the front.
    assert "return;" not in body.split("try {", 1)[1], \
        "a parked item must be skipped, not returned from"
    assert body.count("item.blocked = true") == 2, \
        "both unreleasable paths park the item"
    # And the processing flag is released whatever happens.
    assert "} finally {" in body
    assert "_submitProcessing = false;" in body.split("} finally {", 1)[1]


def test_a_parked_submission_offers_a_way_out():
    """Retry, Export JSON and Discard: retry/export/removal for a stuck item."""
    assert 'data-act="retry"' in JS
    assert 'data-act="export"' in JS
    assert 'data-act="discard"' in JS

    action = JS.split("async function _pendingAction(action, key)", 1)[1].split(
        "\nfunction _renderPendingPanel", 1
    )[0]
    # Retry clears the parked state and restarts the backoff. It also drops the
    # old capability: only a fresh /judge rollback may mint authority to release.
    assert "item.blocked = false" in action
    assert "item.attempts = 0" in action
    assert "delete item.failure_stamp" in action
    assert "_processSubmitQueue()" in action

    # Export writes a local backup so the work survives a discard.
    assert 'action === "export"' in action
    export = JS.split("function _exportPendingSubmission(item)", 1)[1].split(
        "\nasync function _pendingAction", 1
    )[0]
    assert "judgement: item.payload" in export
    assert "link.download" in export
    # The release capability is not the validator's work and must not be backed up.
    assert "failure_stamp" not in export.split("const backup = {", 1)[1].split("};", 1)[0]

    # Discard confirms first and is the only path that drops the payload.
    assert "Discard this pending judgement?" in action
    assert "if (!ok) return;" in action
    assert "_submitQueue.splice(idx, 1)" in action

    panel = JS.split("function _renderPendingPanel()", 1)[1].split("\nfunction ", 1)[0]
    assert "it.blocked ? _pendingActions(it.key" in panel,         "controls appear only on items that need them"
