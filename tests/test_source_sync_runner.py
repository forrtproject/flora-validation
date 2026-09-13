"""Tests for source_sync_runner — the queue behind the "Run sync now" button.

The button persists a queued row and returns; a scheduler drains the queue under a
PostgreSQL advisory lock. The rules worth pinning down are the ones that keep a
click from being lost, duplicated, or blocking every later click.

The database is faked here. The queue SQL is exercised against the real schema by
the end-to-end check in the session that built this, not by these tests.
"""
import subprocess

import pytest

import source_sync_runner as ssr


class FakeCursor:
    """Minimal stand-in: records SQL and replays queued results."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    def fetchall(self):
        out, self.results = self.results, []
        return out


# ── queueing ──────────────────────────────────────────────────────────────────

def test_queue_run_returns_the_new_job_id():
    cur = FakeCursor([None, {"job_id": "job-1"}])   # no active job, then the insert
    assert ssr.queue_run(cur, "hamid") == "job-1"


def test_queue_run_refuses_while_one_is_active():
    cur = FakeCursor([{"job_id": "already-running"}])
    with pytest.raises(ssr.SyncRunConflict) as exc:
        ssr.queue_run(cur, "hamid")
    assert exc.value.active_job_id == "already-running"


def test_conflict_does_not_insert():
    """The refusal must happen before the INSERT, or a second click queues a job
    that then runs the moment the first finishes."""
    cur = FakeCursor([{"job_id": "x"}])
    with pytest.raises(ssr.SyncRunConflict):
        ssr.queue_run(cur, "hamid")
    assert not any("INSERT" in sql for sql, _ in cur.executed)


def test_both_active_statuses_block_a_new_run():
    """A job still 'queued' blocks too — not only a 'running' one."""
    assert set(ssr.ACTIVE_STATUSES) == {"queued", "running"}


def test_active_status_check_is_parameterised():
    cur = FakeCursor([None])
    ssr.active_job_id(cur)
    sql, params = cur.executed[0]
    assert "ANY(%s)" in sql
    assert params == (ssr.ACTIVE_STATUSES,)


def test_queue_run_stamps_who_asked():
    cur = FakeCursor([None, {"job_id": "job-1"}])
    ssr.queue_run(cur, "hamid", trigger="admin")
    sql, params = cur.executed[-1]
    assert "INSERT INTO source_sync_jobs" in sql
    assert params[0] == "admin" and params[1] == "hamid"
    assert "hamid" in params[2]          # seeded into the log


# ── listing ───────────────────────────────────────────────────────────────────

def test_recent_jobs_clamps_the_limit():
    for asked, expected in ((9999, 50), (0, 1), (10, 10)):
        cur = FakeCursor([])
        ssr.recent_jobs(cur, asked)
        assert cur.executed[0][1] == (expected,)


def test_recent_jobs_returns_a_tail_not_the_whole_log():
    """A full sync log is hundreds of lines; sending every one of them on a 3s poll
    would be the bulk of the response."""
    cur = FakeCursor([])
    ssr.recent_jobs(cur)
    sql = cur.executed[0][0]
    assert "right(log_text" in sql
    assert "length(log_text)" in sql


def test_job_detail_returns_none_when_missing():
    assert ssr.job_detail(FakeCursor([]), "nope") is None


# ── stages ────────────────────────────────────────────────────────────────────

def test_all_scripts_run_and_in_order():
    """Order is load-bearing: flora_registry reads the rows the two syncs land, so
    running it first would assign ids against yesterday's data."""
    names = [name for name, _ in ssr.STAGES]
    commands = [cmd[-1] for _, cmd in ssr.STAGES]
    assert commands == ["sync_sources.py", "sync_exclusions.py", "sync_validated.py",
                        "enrich_works.py", "flora_registry.py"]
    assert names == ["entry sheets", "exclusions", "validated records",
                     "metadata", "flora ids"]


def test_stages_run_as_subprocesses_not_imports():
    """Both scripts call sys.exit and hold module state; importing them into the
    web process would leak that between runs."""
    for _, command in ssr.STAGES:
        assert command[0].endswith(("python", "python.exe", "python3")) or "python" in command[0]


def test_a_failing_stage_does_not_stop_the_next_one(monkeypatch):
    """sync_validated reads only the database, so an unshared sheet must not also
    stop our own records refreshing. Mirrors `if: always()` in the workflow."""
    calls = []

    class _Conn:
        def cursor(self):
            class _C:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
                def execute(self_inner, *a, **k): pass
            return _C()
        def commit(self): pass

    def fake_run(command, **kwargs):
        calls.append(command[-1])
        return subprocess.CompletedProcess(command, 1, stdout="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ssr._execute(_Conn(), "job-1")
    assert calls == ["sync_sources.py", "sync_exclusions.py", "sync_validated.py",
                     "enrich_works.py", "flora_registry.py"]


def test_stage_timeout_is_long_enough_for_a_real_sync():
    """The replications sheet is ~10MB and gate 1 retries three times with backoff."""
    assert ssr.STAGE_TIMEOUT_SECONDS >= 10 * 60


def test_subprocess_env_forces_utf8(monkeypatch):
    """Both scripts print non-ASCII progress glyphs; a cp1252 child aborts on them."""
    seen = {}

    class _Conn:
        def cursor(self):
            class _C:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
                def execute(self_inner, *a, **k): pass
            return _C()
        def commit(self): pass

    def fake_run(command, **kwargs):
        seen.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ssr._run_stage(_Conn(), "job-1", "entry sheets", ["python", "sync_sources.py"])
    assert seen["PYTHONIOENCODING"] == "utf-8"
    assert seen["PYTHONUTF8"] == "1"


# ── locking and recovery ──────────────────────────────────────────────────────

def test_lock_id_differs_from_the_extractor_pipeline():
    """Sharing an id would make the two pipelines block each other for no reason."""
    from extractor_maintenance import PIPELINE_ADVISORY_LOCK_ID
    assert ssr.SYNC_ADVISORY_LOCK_ID != PIPELINE_ADVISORY_LOCK_ID


def test_run_queued_never_raises(monkeypatch):
    """An exception escaping the scheduler job kills it, and the queue then stops
    draining with no sign of why."""
    def boom(*a, **k):
        raise RuntimeError("database gone")
    monkeypatch.setattr(ssr.psycopg2, "connect", boom)
    ssr.run_queued("postgres://nowhere")      # must not raise


def test_reaper_targets_both_active_statuses():
    cur = FakeCursor([])
    ssr.reap_stale_jobs(cur, 60)
    sql, params = cur.executed[0]
    assert "status IN ('queued', 'running')" in sql
    assert params == (60,)
    assert "status='failed'" in sql
