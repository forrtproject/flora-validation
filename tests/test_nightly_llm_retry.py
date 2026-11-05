"""The 00:22 UTC job: the tiebreaker retry, then every other open LLM error.

A tiebreaker's answer decides its outcome, so _retry_tiebreakers re-runs consensus
on it (tested in tests/test_outcome_quote_agreement.py). On every other open record
the answer is shown to the admin, so _retry_llm_errors asks Gemini again and saves
only the answer. An advisory lock lets one process run the job.

The checks marked with local_database need a real server and are opt-in like
tests/test_preparation_database.py (FLORA_TEST_DATABASE_URL).
"""
import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from apscheduler.triggers.cron import CronTrigger
from psycopg2.extras import RealDictCursor

import consensus_engine
import db_pool
import llm_validator
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _import_app():
    """Import app.py with the database and scheduler stubbed out: init_db() runs at
    import and would otherwise apply db_schema.sql to whatever DATABASE_URL names."""
    os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
    os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")
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


flora = _import_app()

ERROR = {"error": "timeout", "context": "sanity_check", "model": "gemini", "vote_score": 15,
         "validated_at": "2026-10-31T00:22:00+00:00"}
CLEAN = {"type_check": "correct", "original_check": "correct", "outcome_check": "correct",
         "context": "sanity_check", "model": "gemini", "vote_score": 15,
         "validated_at": "2026-11-01T00:22:00+00:00", "notes": ""}


# ── without a database ────────────────────────────────────────────────────────

def test_one_job_runs_both_retries_at_00_22():
    schedulers = []
    with patch("apscheduler.schedulers.background.BackgroundScheduler.start",
               lambda self, *a, **kw: schedulers.append(self)):
        flora._start_scheduler()
    jobs = schedulers[0].get_jobs()
    nightly = [job for job in jobs if job.func is flora._nightly_llm_retry]
    assert len(nightly) == 1 and nightly[0].id == "nightly_llm_retry"
    trigger = nightly[0].trigger
    assert isinstance(trigger, CronTrigger) and str(trigger.timezone) == "UTC"
    fields = {f.name: str(f) for f in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("0", "22")
    # Not scheduled on their own as well: only under the job's lock.
    assert not [job for job in jobs if job.func in (flora._retry_tiebreakers, flora._retry_llm_errors)]


def test_its_lock_is_one_nothing_else_uses():
    used = [int(n.replace("_", "")) for path in ROOT.rglob("*.py")
            if not {".venv", "node_modules", "tests", ".git"} & set(path.relative_to(ROOT).parts)
            for n in re.findall(r"\b7_?342_?025_?\d{3}\b", path.read_text(encoding="utf-8", errors="ignore"))]
    assert used.count(flora.NIGHTLY_LLM_RETRY_LOCK_ID) == 1, "named once, in app.py"


# ── against a real database ───────────────────────────────────────────────────

def _insert(conn, *, status="need_review", tiebreaker=False, llm=ERROR, doi_r="10.9999/retry"):
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO unvalidated (doi_r, type, outcome, abstract_r, validation_status,
                                        is_tiebreaker, llm_validator, final_outcome,
                                        quality_flags)
               VALUES (%s, 'replication', 'failed', 'We found no effect.', %s, %s, %s,
                       'failed', '[]'::jsonb)
               RETURNING record_id::text AS record_id""",
            (doi_r, status, tiebreaker, json.dumps(llm) if llm is not None else None))
        return cur.fetchone()["record_id"]


def _row(conn, record_id):
    """The whole row as JSON: everything the retry must leave alone, and the answer."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_jsonb(u) FROM unvalidated u WHERE record_id = %s", (record_id,))
        row = cur.fetchone()[0]
    conn.commit()
    return row


def _without_answer(row):
    return {k: v for k, v in row.items() if k != "llm_validator"}


@pytest.fixture
def app_db(local_database, monkeypatch):
    """The app's own connections pointed at the throwaway database; consensus and
    the LLM fail the test unless a test replaces them."""
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setattr(consensus_engine, "evaluate_consensus",
                        lambda cur, record_id, llm_answer=None: pytest.fail("consensus re-run"))
    monkeypatch.setattr(llm_validator, "run_llm_validation",
                        lambda record, context: pytest.fail("Gemini called"))
    monkeypatch.setattr(consensus_engine, "run_llm_validation",
                        lambda record, context: pytest.fail("Gemini called"))
    try:
        yield local_database
    finally:
        db_pool.clear_all()


def test_it_retries_only_open_records_that_are_not_tiebreakers_and_hold_an_error(
        app_db, monkeypatch):
    conn = app_db
    retried = {
        "need_review": _insert(conn, doi_r="10.9999/a"),
        "consensus_reached": _insert(conn, status="consensus_reached", doi_r="10.9999/b"),
        "no_context": _insert(conn, llm={"error": "timeout"}, doi_r="10.9999/c"),
        "tiebreaker_context": _insert(conn, llm={**ERROR, "context": "tiebreaker"},
                                      doi_r="10.9999/d"),
    }
    left = {
        "tiebreaker": _insert(conn, tiebreaker=True, doi_r="10.9999/e"),
        "validated": _insert(conn, status="validated", doi_r="10.9999/f"),
        "rejected": _insert(conn, status="rejected", doi_r="10.9999/g"),
        "unvalidated": _insert(conn, status="unvalidated", doi_r="10.9999/h"),
        "clean": _insert(conn, llm=CLEAN, doi_r="10.9999/i"),
        "none": _insert(conn, llm=None, doi_r="10.9999/j"),
    }
    before = {rid: _row(conn, rid) for rid in [*retried.values(), *left.values()]}
    calls = {}

    def ask(record, context):
        calls[str(record["record_id"])] = context
        return {**CLEAN, "context": context}

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    flora._retry_llm_errors()

    assert calls == {retried["need_review"]: "sanity_check",
                     retried["consensus_reached"]: "sanity_check",
                     retried["no_context"]: "sanity_check",
                     retried["tiebreaker_context"]: "tiebreaker"}
    for rid in retried.values():
        after = _row(conn, rid)
        assert after["llm_validator"] == {**CLEAN, "context": calls[rid]}
        # Only the answer is written: status, tiebreaker, final values, flags.
        assert _without_answer(after) == _without_answer(before[rid])
    for rid in left.values():
        assert _row(conn, rid) == before[rid]
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM validated")
        assert cur.fetchone()[0] == 0
    conn.commit()


def test_an_error_stays_when_gemini_fails_again(app_db, monkeypatch, capsys):
    record_id = _insert(app_db)
    before = _row(app_db, record_id)
    monkeypatch.setattr(llm_validator, "run_llm_validation",
                        lambda record, context: {**ERROR, "error": "failed again"})
    flora._retry_llm_errors()
    assert _row(app_db, record_id) == before
    assert "Gemini failed again, error kept" in capsys.readouterr().out


def test_one_record_failing_does_not_stop_the_rest(app_db, monkeypatch):
    first = _insert(app_db, doi_r="10.9999/first")
    second = _insert(app_db, doi_r="10.9999/second")

    def ask(record, context):
        if str(record["record_id"]) == min(first, second):
            raise RuntimeError("boom")
        return CLEAN

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    flora._retry_llm_errors()
    assert _row(app_db, min(first, second))["llm_validator"] == ERROR
    assert _row(app_db, max(first, second))["llm_validator"] == CLEAN


@pytest.mark.parametrize("change", [
    "llm_validator = '{\"outcome_check\": \"correct\", \"context\": \"sanity_check\"}'::jsonb",
    "llm_validator = '{\"error\": \"a newer failure\"}'::jsonb",
    "is_tiebreaker = TRUE",
    "validation_status = 'validated'",
], ids=["a_submission_stored_an_answer", "a_newer_error", "now_a_tiebreaker", "validated"])
def test_a_row_changed_while_gemini_answered_is_left_alone(app_db, monkeypatch, capsys, change):
    record_id = _insert(app_db)

    def ask(record, context):
        # No row lock is held across the call: this would fail at once if it were.
        with app_db, app_db.cursor() as cur:
            cur.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE NOWAIT",
                        (record_id,))
            cur.execute(f"UPDATE unvalidated SET {change} WHERE record_id = %s", (record_id,))
        return CLEAN

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    flora._retry_llm_errors()
    assert _row(app_db, record_id)["llm_validator"] != CLEAN
    assert "changed while Gemini answered, left as is" in capsys.readouterr().out


def test_the_job_runs_the_tiebreaker_retry_then_the_other_errors(app_db, monkeypatch):
    tiebreaker = _insert(app_db, tiebreaker=True, doi_r="10.9999/tie")
    other = _insert(app_db, doi_r="10.9999/other")
    order = []
    monkeypatch.setattr(consensus_engine, "run_llm_validation", lambda record, context: {"error": "again"})
    monkeypatch.setattr(consensus_engine, "evaluate_consensus",
                        lambda cur, record_id, llm_answer=None: order.append(("consensus", str(record_id))))

    def ask(record, context):
        order.append(("answer", str(record["record_id"])))
        return CLEAN

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    flora._nightly_llm_retry()
    assert order == [("consensus", tiebreaker), ("answer", other)]
    assert _row(app_db, other)["llm_validator"] == CLEAN
    # The lock is released after the run: the next night can take it.
    with app_db.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (flora.NIGHTLY_LLM_RETRY_LOCK_ID,))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT pg_advisory_unlock(%s)", (flora.NIGHTLY_LLM_RETRY_LOCK_ID,))
    app_db.commit()


def test_a_process_that_does_not_win_the_lock_skips_the_job(app_db, monkeypatch, capsys):
    tiebreaker = _insert(app_db, tiebreaker=True, doi_r="10.9999/tie")
    other = _insert(app_db, doi_r="10.9999/other")
    before = {rid: _row(app_db, rid) for rid in (tiebreaker, other)}
    with app_db.cursor() as cur:                      # another process is running it
        cur.execute("SELECT pg_advisory_lock(%s)", (flora.NIGHTLY_LLM_RETRY_LOCK_ID,))
    app_db.commit()
    try:
        flora._nightly_llm_retry()                    # the fixture fails any LLM or consensus call
    finally:
        with app_db.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (flora.NIGHTLY_LLM_RETRY_LOCK_ID,))
        app_db.commit()
    assert "Another process is running the retry; skipped here" in capsys.readouterr().out
    assert {rid: _row(app_db, rid) for rid in (tiebreaker, other)} == before
