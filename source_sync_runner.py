"""source_sync_runner.py — run the entry-sheet sync from the admin panel.

The **Run sync now** button queues a row here; a scheduler job on every pod polls
that queue, and a PostgreSQL advisory lock elects exactly one executor. This is the
same shape as extractor_maintenance.py, for the same reasons:

- The sync takes a minute or two. An HTTP request cannot hold that open, so the
  button returns a job id immediately and the panel polls for the log.
- A queued row is durable. If the pod that accepted the click dies, a replacement
  picks the job up instead of the click being silently lost.
- The advisory lock is what makes "one at a time" true across pods, rather than
  only within one process.

The stages run as SUBPROCESSES, not imports. Both scripts call sys.exit, parse
argv, and hold module-level state; importing them into a long-lived web process
would leak that state between runs, and a hard crash in either would take the web
server down with it.

WHAT THIS DOES NOT GUARD
------------------------
The nightly GitHub Action runs the same two scripts with its own database
connection and cannot see this lock. That overlap is tolerated rather than
prevented: `sync_sources.py` is insert-only with ON CONFLICT DO NOTHING,
`sync_validated.py` upserts by a unique key, and display ids come from an atomic
counter — so a concurrent run duplicates effort but not data. Preventing it
outright would mean the Action taking the same advisory lock, which is a change to
make if the two ever start colliding in practice.
"""
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parent

# Distinct from extractor_maintenance's PIPELINE_ADVISORY_LOCK_ID: the two
# pipelines touch different tables and must not block each other.
SYNC_ADVISORY_LOCK_ID = 7_342_025_092

# Generous: the replications sheet alone is ~10MB and gate 1 retries three times
# with backoff. A stage that genuinely hangs still has to end, or the lock is held
# until the pod restarts.
STAGE_TIMEOUT_SECONDS = 30 * 60

STAGES = [
    ("entry sheets", [sys.executable, "sync_sources.py"]),
    # Before anything reads transform_exclusions. Cheap: one small sheet.
    ("exclusions", [sys.executable, "sync_exclusions.py"]),
    ("validated records", [sys.executable, "sync_validated.py"]),
    # Bibliographic metadata for any DOI not cached yet. Only new DOIs are
    # fetched, so this is a no-op on most runs.
    ("metadata", [sys.executable, "enrich_works.py"]),
    # Assigns stable ids to whatever the transform now produces. Last, because it
    # reads the rows the stages above just landed.
    ("flora ids", [sys.executable, "flora_registry.py"]),
]

ACTIVE_STATUSES = ["queued", "running"]


class SyncRunConflict(RuntimeError):
    """A run is already queued or in flight."""

    def __init__(self, active_job_id: str = None):
        super().__init__("a sync run is already active")
        self.active_job_id = active_job_id


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── queueing ──────────────────────────────────────────────────────────────────

def active_job_id(cur) -> "str | None":
    cur.execute(
        """
        SELECT job_id::text AS job_id FROM source_sync_jobs
        WHERE status = ANY(%s)
        ORDER BY created_at LIMIT 1
        """,
        (ACTIVE_STATUSES,),
    )
    row = cur.fetchone()
    return row["job_id"] if row else None


def queue_run(cur, requested_by: str, trigger: str = "admin") -> str:
    """Persist a queued job. Raises SyncRunConflict if one is already active.

    The check and the insert share the caller's transaction, so two clicks landing
    together cannot both pass the check — the second sees the first's row or waits
    on it, rather than both queueing.
    """
    existing = active_job_id(cur)
    if existing:
        raise SyncRunConflict(existing)

    cur.execute(
        """
        INSERT INTO source_sync_jobs (trigger, requested_by, status, log_text)
        VALUES (%s, %s, 'queued', %s)
        RETURNING job_id::text AS job_id
        """,
        (trigger, requested_by, f"[{_timestamp()}] queued by {requested_by}\n"),
    )
    return cur.fetchone()["job_id"]


def recent_jobs(cur, limit: int = 10) -> list:
    """Newest first. log_text is truncated here — the panel shows a tail and asks
    for the whole thing only when someone opens it."""
    cur.execute(
        """
        SELECT job_id::text AS job_id, trigger, requested_by, status,
               created_at, started_at, finished_at,
               right(log_text, 4000) AS log_tail,
               length(log_text) AS log_length
        FROM source_sync_jobs
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (max(1, min(int(limit), 50)),),
    )
    return [dict(r) for r in cur.fetchall()]


def job_detail(cur, job_id: str) -> "dict | None":
    cur.execute(
        """
        SELECT job_id::text AS job_id, trigger, requested_by, status,
               created_at, started_at, finished_at, log_text
        FROM source_sync_jobs WHERE job_id = %s
        """,
        (job_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ── execution ─────────────────────────────────────────────────────────────────

def _append_log(conn, job_id: str, text: str) -> None:
    """Committed as it goes, so the panel can follow a run in progress instead of
    receiving everything at the end."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE source_sync_jobs SET log_text = log_text || %s WHERE job_id = %s",
            (text, job_id),
        )
    conn.commit()


def _run_stage(conn, job_id: str, name: str, command: list) -> bool:
    _append_log(conn, job_id, f"\n[{_timestamp()}] ── {name} ──\n")
    child_env = dict(os.environ)
    # Both scripts print non-ASCII progress glyphs; a cp1252 child aborts on them.
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=ROOT, env=child_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            check=False, timeout=STAGE_TIMEOUT_SECONDS,
        )
        output, code = completed.stdout or "", completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output += f"\nTIMED OUT after {STAGE_TIMEOUT_SECONDS}s\n"
        code = 124
    except Exception:
        output, code = traceback.format_exc(), 1

    elapsed = time.monotonic() - started
    _append_log(
        conn, job_id,
        output.rstrip("\n") + f"\n[{_timestamp()}] {name}: exit {code} ({elapsed:.1f}s)\n",
    )
    return code == 0


def _execute(conn, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE source_sync_jobs SET status='running', started_at=NOW() WHERE job_id=%s",
            (job_id,),
        )
    conn.commit()

    ok = True
    for name, command in STAGES:
        # Stages are independent on purpose: sync_validated.py reads only the
        # database, so a sheet that failed its gates must not also stop our own
        # records from refreshing. Mirrors `if: always()` in the workflow.
        if not _run_stage(conn, job_id, name, command):
            ok = False

    status = "success" if ok else "failed"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE source_sync_jobs SET status=%s, finished_at=NOW() WHERE job_id=%s",
            (status, job_id),
        )
    conn.commit()
    _append_log(conn, job_id, f"[{_timestamp()}] run {status}\n")


def run_queued(database_url: str) -> None:
    """Scheduler entry point. Takes the lock, runs at most one queued job, returns.

    Never raises: an exception escaping here would kill the scheduler job and the
    queue would stop draining with no sign of why.
    """
    conn = None
    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_ADVISORY_LOCK_ID,))
            if not cur.fetchone()[0]:
                conn.commit()
                return          # another pod is executing; nothing to do here
        conn.commit()

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT job_id::text AS job_id FROM source_sync_jobs
                    WHERE status = 'queued' ORDER BY created_at LIMIT 1
                    """
                )
                row = cur.fetchone()
            conn.commit()
            if not row:
                return

            job_id = row["job_id"]
            try:
                _execute(conn, job_id)
            except Exception:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE source_sync_jobs
                        SET status='failed', finished_at=NOW(),
                            log_text = log_text || %s
                        WHERE job_id=%s
                        """,
                        (f"\nrunner crashed:\n{traceback.format_exc()}\n", job_id),
                    )
                conn.commit()
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (SYNC_ADVISORY_LOCK_ID,))
            conn.commit()
    except Exception:
        print("[source-sync] dispatcher error:")
        traceback.print_exc()
    finally:
        if conn is not None:
            conn.close()


def reap_stale_jobs(cur, older_than_minutes: int = 60) -> int:
    """A job left 'running' by a pod that died would block the queue forever, since
    queue_run treats it as active. Older than the stage timeout means nobody is
    working on it any more."""
    cur.execute(
        """
        UPDATE source_sync_jobs
        SET status='failed', finished_at=NOW(),
            log_text = log_text || '\nabandoned: no runner finished this job\n'
        WHERE status IN ('queued', 'running')
          AND created_at < NOW() - make_interval(mins => %s)
        """,
        (older_than_minutes,),
    )
    return cur.rowcount
