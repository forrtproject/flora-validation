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
counter. Final preparation separately captures the prepared-table revision before
building and checks it under the materialization lock. If another run publishes
in between, the older candidate is rejected and must be rebuilt; it cannot revert
the newer snapshot. The queue lock does not need to span the Action's source sync.
"""
import os
import json
import hashlib
from contextlib import contextmanager
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from output_lock import output_directory_lock

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
FINAL_STAGE = "final CSV and reports"

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
               length(log_text) AS log_length,
               artifact_csv IS NOT NULL AS has_csv,
               recovery_csv IS NOT NULL AS has_recovery_csv,
               (report_json IS NOT NULL AND report_markdown IS NOT NULL) AS has_report,
               report_json->>'status' AS report_status,
               report_json->'rows' AS report_rows,
               report_json->'warning_count' AS warning_count,
               report_json->'failure_count' AS failure_count
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
               created_at, started_at, finished_at, log_text,
               artifact_csv IS NOT NULL AS has_csv,
               recovery_csv IS NOT NULL AS has_recovery_csv,
               (report_json IS NOT NULL AND report_markdown IS NOT NULL) AS has_report,
               report_json->>'status' AS report_status,
               report_json->'rows' AS report_rows,
               report_json->'warning_count' AS warning_count,
               report_json->'failure_count' AS failure_count
        FROM source_sync_jobs WHERE job_id = %s
        """,
        (job_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def job_artifact(cur, job_id: str, artifact: str):
    """An immutable run snapshot, available from any web pod.

    The column is selected exclusively from this allowlist; an HTTP filename is
    never interpolated into SQL or resolved against the filesystem.
    """
    column = {"flora.csv": "artifact_csv", "recovery.csv": "recovery_csv", "report.json": "report_json",
              "report.md": "report_markdown"}.get(artifact)
    if column is None:
        return None
    cur.execute(f"SELECT {column} AS artifact FROM source_sync_jobs WHERE job_id = %s",
                (job_id,))
    row = cur.fetchone()
    return row["artifact"] if row else None


def _persist_artifacts(conn, job_id, report, csv_text=None, *, recovery_csv=None):
    from prepare_flora import render_report
    report["warning_count"] = len(report.get("warnings", []))
    report["failure_count"] = len(report.get("errors", []))
    markdown = render_report(report)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE source_sync_jobs SET artifact_csv=%s, report_json=%s, "
            "report_markdown=%s WHERE job_id=%s",
            (csv_text, Json(report), markdown, job_id),
        )
        if recovery_csv is not None:
            cur.execute("UPDATE source_sync_jobs SET recovery_csv=%s WHERE job_id=%s",
                        (recovery_csv, job_id))
    conn.commit()


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


@contextmanager
def _run_workspace(output_root):
    directory = Path(tempfile.mkdtemp(prefix="flora-sync-", dir=output_root))
    try:
        yield directory
    except Exception as exc:
        # A database outage must not destroy the only copy of a committed CSV.
        # The runner records this path in the job log for local recovery.
        raise RuntimeError(f"Pipeline artifacts retained at {directory}") from exc
    else:
        shutil.rmtree(directory)


def _execute(conn, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE source_sync_jobs SET status='running', started_at=NOW() WHERE job_id=%s",
            (job_id,),
        )
    conn.commit()

    ok = True
    stage_results = []
    for name, command in STAGES:
        # Stages are independent on purpose: sync_validated.py reads only the
        # database, so a sheet that failed its gates must not also stop our own
        # records from refreshing. Mirrors `if: always()` in the workflow.
        passed = _run_stage(conn, job_id, name, command)
        stage_results.append({"name": name, "status": "passed" if passed else "failed"})
        if not passed:
            ok = False

    from prepare_flora import REPORT_JSON, timestamp, write_report
    report = {"schema_version": 1, "generated_at": timestamp(),
              "mode": "database_pipeline", "status": "failed", "rows": 0,
              "stages": stage_results, "warnings": [], "errors": []}
    csv_text = None
    artifacts_persisted = False
    if ok:
        output_root = ROOT / "output"
        output_root.mkdir(parents=True, exist_ok=True)
        # Each run gets fresh paths. A timed-out or failed process can never make
        # an earlier export available under this job's download URL.
        with _run_workspace(output_root) as directory:
            output_dir = Path(directory)
            command = [sys.executable, "prepare_flora.py", "--output-dir", str(output_dir)]
            if os.environ.get("FLORA_API_FILTER", "").strip().lower() in {"1", "true", "yes"}:
                command.append("--api-filter")
            ok = _run_stage(conn, job_id, FINAL_STAGE, command)
            report_path = output_dir / REPORT_JSON
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["stages"] = stage_results + report.get("stages", [])
            csv_path = output_dir / "flora.csv"
            # The report and its completed state are required as well as exit 0.
            # A mocked/no-op command or interrupted process must not pass a job.
            ok = bool(ok and csv_path.exists()
                      and report.get("status") in {"success", "needs_attention"})
            if ok:
                csv_text = csv_path.read_bytes().decode("utf-8")
                report["finished_at"] = timestamp()
                _persist_artifacts(conn, job_id, report, csv_text)
                artifacts_persisted = True
                # Also leave the conventional CLI artifacts on this executor.
                # Downloads are already durable. A local copy error must not
                # discard them or hide a successfully committed dataset.
                artifact_name = "preparation report"
                try:
                    write_report(output_dir, report)
                    with output_directory_lock(output_root):
                        for artifact in output_dir.iterdir():
                            if artifact.is_file() and not artifact.name.startswith("."):
                                artifact_name = artifact.name
                                destination = output_root / artifact.name
                                temporary = destination.with_name(destination.name + ".tmp")
                                temporary.write_bytes(artifact.read_bytes())
                                temporary.replace(destination)
                except OSError as exc:
                    report.setdefault("warnings", []).append(
                        f"Could not copy local artifact {artifact_name}: {exc}. "
                        "The job's CSV and report remain available for download.")
                    report["status"] = "needs_attention"
                    artifacts_persisted = False  # save the additional warning
            else:
                report["status"] = "failed"
                recovery_csv = None
                if (report.get("storage", {}).get("status") == "committed"
                        and report.get("recovery_csv") in {"flora_committed_recovery.csv", ".flora.candidate.csv"}):
                    payload = (output_dir / report["recovery_csv"]).read_bytes()
                    if hashlib.sha256(payload).hexdigest() != report.get("release", {}).get("sha256"):
                        raise ValueError("Recovery CSV does not match the committed candidate's manifest")
                    recovery_csv = payload.decode("utf-8")
                    report["recovery_csv"] = report["recovery_artifact"] = "recovery.csv"
                detail = ("The committed CSV is retained as the recovery download."
                          if recovery_csv is not None else "No CSV is available for this run.")
                report.setdefault("errors", []).append(
                    "Final preparation did not complete with a current CSV and report. " + detail)
                report["finished_at"] = timestamp()
                # Save the committed recovery bytes before the workspace closes.
                _persist_artifacts(conn, job_id, report, recovery_csv=recovery_csv)
                artifacts_persisted = True
    else:
        report["errors"] = [f"{stage['name']} failed" for stage in stage_results
                            if stage["status"] == "failed"]
        report["stages"].append({"name": FINAL_STAGE, "status": "skipped",
                                  "detail": "An upstream stage failed."})
        _append_log(conn, job_id, f"\n[{_timestamp()}] {FINAL_STAGE}: skipped; "
                    "an upstream stage failed, so no current CSV was produced.\n")
    report["finished_at"] = timestamp()
    if not artifacts_persisted:
        _persist_artifacts(conn, job_id, report, csv_text)
    if report.get("warnings"):
        _append_log(conn, job_id, f"[{_timestamp()}] preparation needs attention: "
                    + " ".join(report["warnings"]) + "\n")

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
                failure = traceback.format_exc()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE source_sync_jobs
                        SET status='failed', finished_at=NOW(),
                            log_text = log_text || %s
                        WHERE job_id=%s
                        """,
                        (f"\nrunner crashed:\n{failure}\n", job_id),
                    )
                conn.commit()
                from prepare_flora import timestamp
                _persist_artifacts(conn, job_id, {
                    "schema_version": 1, "generated_at": timestamp(),
                    "finished_at": timestamp(), "mode": "database_pipeline",
                    "status": "failed", "rows": 0, "stages": [], "warnings": [],
                    "errors": ["The pipeline runner crashed; see the job log for details."],
                })
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
    queue_run treats it as active. The advisory lock check protects a live runner
    even when several long stages make its total duration exceed this threshold.
    """
    cur.execute(
        """
        UPDATE source_sync_jobs
        SET status='failed', finished_at=NOW(),
            log_text = log_text || '\nabandoned: no runner finished this job\n'
        WHERE status IN ('queued', 'running')
          AND created_at < NOW() - make_interval(mins => %s)
          AND pg_try_advisory_xact_lock(7342025092)
        """,
        (older_than_minutes,),
    )
    return cur.rowcount
