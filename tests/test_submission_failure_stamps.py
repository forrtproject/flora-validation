"""A release capability may only follow a server-observed persistence failure.

The stamp minted by /judge authorises the browser to discard a validator's
completed judgement and hand the record to someone else. These tests drive the
decorator with every failure the handler can raise, because the distinction is
control flow rather than text: a deliberate 4xx must pass through untouched.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def app_module():
    """Import app.py with the database and scheduler stubbed out.

    Importing app.py runs init_db() and starts APScheduler, so both are replaced
    for the duration. Nothing in these tests reaches a real database.
    """
    os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 1}
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor

    with patch("psycopg2.connect", return_value=connection), patch(
        "apscheduler.schedulers.background.BackgroundScheduler.start"
    ):
        import app

    return app


@pytest.fixture
def guard(app_module):
    """Return (run_handler, minted) where minted records stamp requests."""
    minted = []

    def fake_stamp(req, coder_id, failure_code, failure_message):
        minted.append({"code": failure_code, "message": failure_message,
                       "coder_id": coder_id})
        return {
            "failure_stamp": "stamp-secret",
            "failure_id": "11111111-1111-1111-1111-111111111111",
            "submission_id": "22222222-2222-2222-2222-222222222222",
            "release_status": "save_failed",
            "expires_at": "2026-09-02T12:00:00",
        }

    def run(error):
        def handler(_req, **_kwargs):
            raise error

        guarded = app_module._server_controlled_submission_failure(handler)
        with patch.object(
            app_module, "_try_issue_submission_failure_stamp", fake_stamp
        ):
            with pytest.raises(Exception) as raised:
                # The session dependency has already resolved by this point, so
                # the wrapped handler receives the validator as a keyword.
                guarded(MagicMock(), validator={"coder_id": 7})
        return raised.value, minted

    return run


CLIENT_ERRORS = [
    # Field validation the browser can correct and resubmit.
    (400, "type_check must be 'correct' or 'incorrect'"),
    (400, "original_check must be 'correct' or 'incorrect'"),
    (400, "outcome_check must be 'correct' or 'incorrect'"),
    # Slot-gone answers: the server has already released the record, and the
    # browser closes the pending item from the message alone.
    (400, "No open slot found for this validator on this record"),
    (400, "Already judged this record"),
    (409, "This record was already submitted"),
    (409, "This assignment was already submitted"),
    (404, "record_id 'x' not found"),
    (404, "Validator not found"),
]


@pytest.mark.parametrize(("status", "message"), CLIENT_ERRORS)
def test_client_errors_never_mint_a_release_capability(app_module, guard, status, message):
    from fastapi import HTTPException

    error, minted = guard(HTTPException(status, message))

    assert minted == [], f"{status} {message!r} must not authorise a release"
    assert isinstance(error, HTTPException)
    assert error.status_code == status
    # Passed through unchanged: no stamp, and the original message survives so
    # the browser can still recognise a slot-gone response.
    assert error.detail == message


def test_unhandled_exception_still_mints_a_capability(app_module, guard):
    from fastapi import HTTPException

    error, minted = guard(RuntimeError("connection reset mid-commit"))

    assert len(minted) == 1
    assert minted[0]["code"] == "internal_save_error"
    # The stamp is bound to the server's identity for this caller.
    assert minted[0]["coder_id"] == 7
    assert isinstance(error, HTTPException)
    assert error.status_code == 500
    assert error.detail["code"] == "judgement_save_failed"
    assert error.detail["failure_stamp"] == "stamp-secret"


def test_deliberate_server_error_still_mints_a_capability(app_module, guard):
    """A 5xx is a server-side failure, so recovery is still offered."""
    from fastapi import HTTPException

    error, minted = guard(HTTPException(503, "database is starting up"))

    assert len(minted) == 1
    assert error.status_code == 503
    assert error.detail["code"] == "judgement_save_failed"
    assert error.detail["failure_stamp"] == "stamp-secret"


def test_a_failed_stamp_write_never_masks_the_original_error(app_module):
    """If the capability cannot be persisted, the real error must surface."""

    def handler(_req, **_kwargs):
        raise RuntimeError("disk full")

    guarded = app_module._server_controlled_submission_failure(handler)
    with patch.object(
        app_module, "_try_issue_submission_failure_stamp", lambda *_a: None
    ):
        with pytest.raises(RuntimeError, match="disk full"):
            guarded(MagicMock(), validator={"coder_id": 7})


def test_successful_submissions_are_untouched(app_module):
    def handler(_req, **_kwargs):
        return {"points_earned": 3, "total_points": 42}

    guarded = app_module._server_controlled_submission_failure(handler)
    assert guarded(MagicMock(), validator={"coder_id": 7}) == {
        "points_earned": 3, "total_points": 42
    }


# ---------------------------------------------------------------------------
# A retry that succeeds must not leave false failure history
# ---------------------------------------------------------------------------

def test_a_successful_retry_closes_its_own_failure_row(app_module):
    """Only /api/judge can know the retry worked, so only it can close the row.

    The browser drops the queued item and never tells the server. Without this
    the row stayed 'save_failed' until the reaper marked it 'expired' — history
    claiming the validator lost work they had actually saved.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app.py").read_text(
        encoding="utf-8"
    )
    closer = source.split("def _close_failure_row_after_retry(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "SET status = 'saved_after_retry'" in closer
    assert "WHERE submission_id = %s AND status = 'save_failed'" in closer, \
        "it must only close a row that is still open"
    assert "if submission_id is None:" in closer, "older clients send no id"

    judge = source.split("def judge(", 1)[1].split("@app.post", 1)[0]
    assert "_close_failure_row_after_retry(cur, req.submission_id)" in judge
    # Inside the judgement's own transaction: a rolled-back judgement must
    # leave the failure row open.
    assert judge.index("_close_failure_row_after_retry") < judge.index(
        'return {"points_earned"'
    )
    assert "with db() as cur:" in judge


def test_the_reaper_only_expires_rows_that_are_still_open(app_module):
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app.py").read_text(
        encoding="utf-8"
    )
    reaper = source.split("def _expire_submission_failure_stamps(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "WHERE status = 'save_failed'" in reaper, \
        "a resolved row must never be reclassified as expired"


def test_the_new_state_is_declared_everywhere_it_is_read():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    schema = (root / "db_schema.sql").read_text(encoding="utf-8")
    js = (root / "docs" / "app.js").read_text(encoding="utf-8")
    readme = (root / "docs" / "README.md").read_text(encoding="utf-8")

    # Both the fresh CREATE TABLE and the migration for existing databases.
    assert schema.count("'saved_after_retry'") >= 2
    assert "submission_failure_releases_status_check" in schema
    # An unlabelled status renders as a raw enum in the admin ledger.
    assert "saved_after_retry:" in js
    assert "`saved_after_retry`" in readme
