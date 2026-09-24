"""Run extractor synchronization and orphan maintenance under one audit system.

Maintenance is deliberately sequenced:

1. ``sync_csv.py`` downloads, validates, imports, and promotes the candidate CSV.
2. Scheduled full runs enrich missing OpenAlex IDs while the same lock is held.
3. ``find_orphans.py`` writes a complete read-only orphan report.
4. ``csv_to_db.py --retire`` (full runs, EXTRACTOR_AUTO_RETIRE, on by default)
   retires the records flora-extractor names in data/retired_pairs.csv, read at
   the commit the sync imported: untouched records are archived in
   retired_records then deleted, touched ones only flagged, under a
   EXTRACTOR_MAX_RETIRE_PERCENT cap. It is the one stage an unattended run may
   delete in, because it acts on what the extractor stated, not on absence.
5. ``cleanup_orphans.py --apply`` is a separate, explicitly requested manual
   stage. Routine full/nightly runs never run it.

The orphan report and cleanup read the immutable archive the sync imported —
identified by sha256, not by the mutable ``extracted_latest.csv`` — so a pod
carrying an older CSV cannot satisfy the run-ID gate and then delete against
different bytes.

A failed maintenance stage prevents every later destructive stage from running;
OpenAlex enrichment is non-destructive and becomes a warning on failure. Admins
may also launch one stage at a time, but standalone orphan report/cleanup requests
require the newest persisted prerequisite completion. Every run is appended to
one UTF-8 file and, when a database URL is supplied, retained in
``extractor_maintenance_runs`` for the admin panel.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Callable, Literal
from uuid import uuid4

import psycopg2
import psycopg2.errors
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from extractor_storage import (
    SnapshotIntegrityError,
    require_snapshot,
    resolve_data_dir,
    sha256_file,
)


load_dotenv()
use_utf8_output()

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = resolve_data_dir()
DEFAULT_LOG_PATH = ROOT / "logs" / "extractor_maintenance.log"
# A fixed, application-specific PostgreSQL advisory-lock key. Session-level
# advisory locks are released automatically if the process, connection, or pod
# dies, making PostgreSQL the authoritative cross-worker process mutex.
PIPELINE_ADVISORY_LOCK_ID = 7_342_025_091
DEFAULT_STAGE_TIMEOUT_SECONDS = 2 * 60 * 60
DEFAULT_LOCK_WAIT_SECONDS = 15

RequestedStage = Literal["full", "sync", "find", "cleanup"]
StageRunner = Callable[..., subprocess.CompletedProcess[str]]

_STAGES_BY_REQUEST: dict[RequestedStage, list[str]] = {
    # "full" means the complete routine refresh. Destructive cleanup is kept
    # out of this bundle so neither cron nor a convenient admin button can turn
    # a sequence of small upstream omissions into automatic cumulative loss.
    "full": ["sync_csv", "find_orphans"],
    "sync": ["sync_csv"],
    "find": ["find_orphans"],
    "cleanup": ["cleanup_orphans"],
}

# Which requests actually RUN a given stage. stage_status cannot answer this on
# its own: a manual Find inherits "sync_csv": "SUCCESS" from the sync it was
# gated against, so searching for runs whose stage_status merely mentions
# sync_csv finds the Find run and mistakes it for the newest sync. Derived from
# the table above so the two cannot drift apart.
_REQUESTS_PERFORMING: dict[str, tuple[RequestedStage, ...]] = {
    stage: tuple(request for request, stages in _STAGES_BY_REQUEST.items()
                 if stage in stages)
    for stage in ("sync_csv", "find_orphans", "cleanup_orphans")
}


@dataclass(frozen=True)
class StageResult:
    success: bool
    returncode: int
    output: str


@dataclass(frozen=True)
class StageAttempt:
    run_id: str
    status: str
    stage_status: dict[str, str]
    safety_report: dict


@dataclass(frozen=True)
class PendingRun:
    """A durable maintenance reservation selected by the dispatcher."""

    run_id: str
    trigger: Literal["scheduled", "admin", "cli"]
    requested_stage: RequestedStage
    requested_by: str | None


class MaintenanceRunConflict(RuntimeError):
    """Another scheduled or manual maintenance run is already active."""

    def __init__(self, active_run_id: str | None):
        self.active_run_id = active_run_id
        suffix = f" ({active_run_id})" if active_run_id else ""
        super().__init__(f"another extractor maintenance run is already active{suffix}")


class MaintenancePrerequisiteError(RuntimeError):
    """A manual downstream stage has no verified upstream completion."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def _initial_stage_status(requested_stage: RequestedStage) -> dict[str, str]:
    return {stage: "PENDING" for stage in _STAGES_BY_REQUEST[requested_stage]}


class _RunLog:
    """Write one run to the combined file while retaining its database copy."""

    def __init__(self, log_file: IO[str]):
        self.log_file = log_file
        self.capture = io.StringIO()

    def write(self, value: str) -> int:
        if not self.log_file.closed:
            self.log_file.write(value)
        self.capture.write(value)
        return len(value)

    def flush(self) -> None:
        if not self.log_file.closed:
            self.log_file.flush()

    def getvalue(self) -> str:
        return self.capture.getvalue()


def _configured_log_path() -> Path:
    configured = os.environ.get("EXTRACTOR_MAINTENANCE_LOG", "").strip()
    if not configured:
        return DEFAULT_LOG_PATH
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stage_timeout_seconds() -> int:
    try:
        configured = int(
            os.environ.get(
                "EXTRACTOR_STAGE_TIMEOUT_SECONDS",
                str(DEFAULT_STAGE_TIMEOUT_SECONDS),
            )
        )
    except ValueError:
        return DEFAULT_STAGE_TIMEOUT_SECONDS
    return max(1, configured)


def _lock_wait_seconds() -> int:
    try:
        configured = int(
            os.environ.get(
                "EXTRACTOR_LOCK_WAIT_SECONDS",
                str(DEFAULT_LOCK_WAIT_SECONDS),
            )
        )
    except ValueError:
        return DEFAULT_LOCK_WAIT_SECONDS
    return max(0, configured)


def _auto_retire_enabled() -> bool:
    """EXTRACTOR_AUTO_RETIRE: end the full routine by retiring what the extractor
    withdrew (``csv_to_db.py --retire``, applied). On unless set to 0/false/no/off.
    """
    value = os.environ.get("EXTRACTOR_AUTO_RETIRE", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _emit(log_file: IO[str], message: str) -> None:
    value = message if message.endswith("\n") else message + "\n"
    sys.stdout.write(value)
    sys.stdout.flush()
    log_file.write(value)
    log_file.flush()


def _run_stage(
    log_file: IO[str],
    stage: str,
    command: list[str],
    runner: StageRunner,
) -> StageResult:
    display_command = " ".join(
        Path(part).name if index < 2 else part for index, part in enumerate(command)
    )
    _emit(log_file, f"[{_timestamp()}] [{stage}] START {display_command}")
    started = time.monotonic()
    child_env = dict(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    timeout_seconds = _stage_timeout_seconds()

    try:
        completed = runner(
            command,
            cwd=ROOT,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if output:
            _emit(log_file, output.rstrip("\n"))
        elapsed = time.monotonic() - started
        _emit(
            log_file,
            f"[{_timestamp()}] [{stage}] FAILED timeout="
            f"{timeout_seconds}s ({elapsed:.1f}s)",
        )
        return StageResult(False, 124, output)
    except Exception:
        output = traceback.format_exc().rstrip()
        _emit(log_file, output)
        elapsed = time.monotonic() - started
        _emit(log_file, f"[{_timestamp()}] [{stage}] FAILED to start ({elapsed:.1f}s)")
        return StageResult(False, -1, output)

    output = completed.stdout or ""
    if output:
        _emit(log_file, output.rstrip("\n"))

    elapsed = time.monotonic() - started
    if completed.returncode == 0:
        _emit(log_file, f"[{_timestamp()}] [{stage}] SUCCESS ({elapsed:.1f}s)")
        return StageResult(True, completed.returncode, output)

    _emit(
        log_file,
        f"[{_timestamp()}] [{stage}] FAILED exit_code={completed.returncode} ({elapsed:.1f}s)",
    )
    return StageResult(False, completed.returncode, output)


def _mark_skipped(log_file: IO[str], stage: str, reason: str) -> None:
    _emit(log_file, f"[{_timestamp()}] [{stage}] SKIPPED \N{EM DASH} {reason}")


def _summary(log_file: IO[str], statuses: dict[str, str], overall: str) -> None:
    rendered = " ".join(f"{name}={status}" for name, status in statuses.items())
    _emit(log_file, f"[{_timestamp()}] [pipeline] {overall.upper()} {rendered}")
    _emit(log_file, "=" * 88)


def _active_run_id(conn) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id
            FROM extractor_maintenance_runs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def _acquire_pipeline_lock(database_url: str, *, wait_seconds: int = 0):
    """Acquire the session-level mutex, optionally retrying for a brief race."""
    conn = psycopg2.connect(database_url)
    try:
        deadline = time.monotonic() + max(0, wait_seconds)
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    (PIPELINE_ADVISORY_LOCK_ID,),
                )
                acquired = bool(cur.fetchone()[0])
            conn.commit()
            if acquired:
                return conn
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                active_run_id = _active_run_id(conn)
                conn.commit()
                raise MaintenanceRunConflict(active_run_id)
            time.sleep(min(0.1, remaining))
    except Exception:
        conn.close()
        raise


def _release_pipeline_lock(conn) -> None:
    """Release the process mutex; closing is a second automatic safeguard."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_unlock(%s)",
                (PIPELINE_ADVISORY_LOCK_ID,),
            )
        conn.commit()
    finally:
        conn.close()


def queue_maintenance_run(
    database_url: str,
    requested_stage: RequestedStage,
    *,
    trigger: Literal["scheduled", "admin", "cli"],
    requested_by: str | None = None,
) -> str:
    """Reserve one run only while the process-level advisory mutex is free."""
    if requested_stage not in {"full", "sync", "find", "cleanup"}:
        raise ValueError(f"unknown maintenance stage: {requested_stage}")

    # Reserve while holding the same lock used by the actual pipeline. Existing
    # queued work is deliberately never aged out here: it is durable work for
    # the dispatcher, not an in-process callback that may safely be forgotten.
    # Abandoned running rows are recovered separately, and only after acquiring
    # this advisory lock proves that their former worker is no longer alive.
    conn = _acquire_pipeline_lock(database_url)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO extractor_maintenance_runs
                        (trigger, requested_stage, requested_by, status, stage_status)
                    VALUES (%s, %s, %s, 'queued', %s::jsonb)
                    RETURNING run_id
                    """,
                    (
                        trigger,
                        requested_stage,
                        requested_by,
                        json.dumps(_initial_stage_status(requested_stage)),
                    ),
                )
                run_id = str(cur.fetchone()[0])
                conn.commit()
                return run_id
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise MaintenanceRunConflict(_active_run_id(conn))
    finally:
        _release_pipeline_lock(conn)


def _mark_run_started(database_url: str, run_id: str) -> None:
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extractor_maintenance_runs
                SET status = 'running', started_at = NOW()
                WHERE run_id = %s AND status = 'queued'
                """,
                (run_id,),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"maintenance run {run_id} is not queued")
        conn.commit()
    finally:
        conn.close()


def _update_run_progress(
    database_url: str,
    run_id: str,
    *,
    stage_status: dict[str, str],
    safety_report: dict,
    log_text: str,
) -> None:
    """Commit each stage result before the next child process can start."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extractor_maintenance_runs
                SET stage_status = %s::jsonb,
                    -- cleanup_orphans may have committed its receipt in the
                    -- deletion transaction between parent progress writes.
                    -- Merge instead of replacing so that receipt can never be
                    -- erased by the parent process's older in-memory report.
                    safety_report = safety_report || %s::jsonb,
                    log_text = %s
                WHERE run_id = %s AND status = 'running'
                """,
                (
                    json.dumps(stage_status),
                    json.dumps(safety_report),
                    log_text,
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"maintenance run {run_id} is not running")
        conn.commit()
    finally:
        conn.close()


def _latest_stage_attempt(
    database_url: str,
    stage: str,
    *,
    source_sync_run_id: str | None = None,
) -> StageAttempt | None:
    """Return the newest persisted attempt, including failed/PENDING attempts.

    Restricted to runs that were ASKED to perform this stage. A manual Find
    inherits the gating sync's "sync_csv": "SUCCESS" into its own stage_status,
    so matching on that key alone made the Find run look like the newest sync
    and blocked every subsequent cleanup.
    """
    performed_by = list(_REQUESTS_PERFORMING[stage])
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            if source_sync_run_id is None:
                cur.execute(
                    """
                    SELECT run_id::text, status, stage_status, safety_report
                    FROM extractor_maintenance_runs
                    WHERE stage_status ? %s
                      AND requested_stage = ANY(%s)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (stage, performed_by),
                )
            else:
                cur.execute(
                    """
                    SELECT run_id::text, status, stage_status, safety_report
                    FROM extractor_maintenance_runs
                    WHERE stage_status ? %s
                      AND requested_stage = ANY(%s)
                      AND safety_report->>'source_sync_run_id' = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (stage, performed_by, source_sync_run_id),
                )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row:
        return None
    return StageAttempt(
        run_id=str(row[0]),
        status=str(row[1]),
        stage_status=row[2] if isinstance(row[2], dict) else {},
        safety_report=row[3] if isinstance(row[3], dict) else {},
    )


def _part1_report_is_verified(report: dict, run_id: str) -> bool:
    required_flags = (
        "download_completed",
        "validation_completed",
        "import_completed",
        "promotion_completed",
        "promotion_verified",
        # Without a verified archive there is nothing for Parts 2/3 to bind to,
        # so an otherwise complete Part 1 still cannot approve deletion.
        "archive_verified",
        "part1_completed",
    )
    return (
        str(report.get("maintenance_run_id") or "") == str(run_id)
        and all(report.get(flag) is True for flag in required_flags)
        and bool(report.get("archive_file"))
        and bool(report.get("archive_sha256"))
    )


def _resolve_run_snapshot(data_dir: Path, report: dict) -> tuple[Path, str]:
    """Return the archived Part 1 snapshot the orphan stages must read.

    The run history names the archive and its digest; this proves the file is
    actually here and unchanged. A replacement pod without the shared volume
    fails here instead of silently falling back to its own extracted_latest.csv.
    """
    archive_file = str(report.get("archive_file") or "")
    archive_sha256 = str(report.get("archive_sha256") or "")
    if not archive_file or not archive_sha256:
        raise SnapshotIntegrityError(
            "snapshot_archive_unrecorded",
            "the maintenance run recorded no archived snapshot for Part 1; "
            "orphan reporting and cleanup have nothing to bind to",
            {"archive_file": archive_file or None, "archive_sha256": archive_sha256 or None},
        )
    snapshot_path = data_dir / Path(archive_file).name
    require_snapshot(snapshot_path, archive_sha256, stage="orphan maintenance")
    return snapshot_path, archive_sha256


def _resolve_unaudited_snapshot(data_dir: Path) -> tuple[Path, str]:
    """Bind stages to the promoted CSV when no run history is available.

    Only reachable without a database URL, where nothing can be audited anyway;
    the digest still travels with the file so each child verifies what it reads.
    """
    snapshot_path = data_dir / "extracted_latest.csv"
    if not snapshot_path.is_file():
        raise SnapshotIntegrityError(
            "snapshot_archive_unavailable",
            f"no promoted snapshot at {snapshot_path}; run CSV synchronization first",
            {"path": str(snapshot_path)},
        )
    return snapshot_path, sha256_file(snapshot_path)


def _latest_promoted_snapshot(database_url: str) -> dict:
    """Archive identity of the newest verified promotion, if there is one."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT safety_report->>'archive_file',
                       safety_report->>'archive_sha256'
                FROM extractor_maintenance_runs
                WHERE stage_status->>'sync_csv' = 'SUCCESS'
                  -- Same reason as _latest_stage_attempt: a Find run carries an
                  -- inherited sync_csv status and the gate's archive fields.
                  AND requested_stage IN ('full', 'sync')
                  AND safety_report->'part1_completed' = 'true'::jsonb
                  AND safety_report->>'archive_sha256' IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row:
        return {}
    return {"baseline_file": row[0], "baseline_sha256": row[1]}


def _database_holds_records(database_url: str) -> bool:
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM unvalidated)")
            return bool(cur.fetchone()[0])
    except psycopg2.errors.UndefinedTable:
        # Schema not applied yet — genuinely nothing to protect.
        return False
    finally:
        conn.close()


def _baseline_expectation(database_url: str | None) -> dict:
    """Tell Part 1 which snapshot its removal guard must compare against.

    sync_csv.py cannot work this out for itself: it sees only the local file
    system, where an empty volume is indistinguishable from a first deployment.
    """
    if not database_url:
        return {}
    expectation = _latest_promoted_snapshot(database_url)
    expectation["require_baseline"] = _database_holds_records(database_url)
    return expectation


def _part1_is_verified(attempt: StageAttempt) -> bool:
    return (
        attempt.stage_status.get("sync_csv") == "SUCCESS"
        and _part1_report_is_verified(attempt.safety_report, attempt.run_id)
    )


def _part2_is_verified(
    attempt: StageAttempt,
    source_sync_run_id: str,
    archive_sha256: str,
) -> bool:
    report = attempt.safety_report
    return (
        attempt.stage_status.get("find_orphans") == "SUCCESS"
        and report.get("part2_completed") is True
        and str(report.get("source_sync_run_id") or "") == source_sync_run_id
        # The admin approved deletion after reading a report about THESE bytes.
        and str(report.get("archive_sha256") or "") == archive_sha256
    )


def _load_prerequisites(
    database_url: str,
    requested_stage: Literal["find", "cleanup"],
) -> tuple[dict[str, str], dict]:
    """Load verified upstream stages for an individually requested stage."""
    sync_attempt = _latest_stage_attempt(database_url, "sync_csv")
    if sync_attempt is None:
        raise MaintenancePrerequisiteError(
            "Part 1 has never completed; run CSV synchronization first",
            {"required_stage": "sync_csv", "latest_run_id": None},
        )
    if not _part1_is_verified(sync_attempt):
        raise MaintenancePrerequisiteError(
            "the newest Part 1 attempt did not complete import and verified promotion",
            {
                "required_stage": "sync_csv",
                "latest_run_id": sync_attempt.run_id,
                "latest_run_status": sync_attempt.status,
                "latest_stage_status": sync_attempt.stage_status.get("sync_csv"),
            },
        )

    inherited = {"sync_csv": "SUCCESS"}
    # The archive identity travels with the gate so this run's own history row
    # records which bytes it was authorised to act on.
    archive_sha256 = str(sync_attempt.safety_report.get("archive_sha256") or "")
    gate = {
        "prerequisite_gate": "passed",
        "source_sync_run_id": sync_attempt.run_id,
        "part1_completed": True,
        "archive_file": sync_attempt.safety_report.get("archive_file"),
        "archive_sha256": archive_sha256,
    }
    if requested_stage == "find":
        return inherited, gate

    find_attempt = _latest_stage_attempt(
        database_url,
        "find_orphans",
        source_sync_run_id=sync_attempt.run_id,
    )
    if find_attempt is None:
        raise MaintenancePrerequisiteError(
            "Part 2 has not completed for the newest verified CSV synchronization",
            {
                **gate,
                "required_stage": "find_orphans",
                "latest_run_id": None,
            },
        )
    if not _part2_is_verified(find_attempt, sync_attempt.run_id, archive_sha256):
        raise MaintenancePrerequisiteError(
            "the newest Part 2 attempt for this CSV baseline was not successful",
            {
                **gate,
                "required_stage": "find_orphans",
                "latest_run_id": find_attempt.run_id,
                "latest_run_status": find_attempt.status,
                "latest_stage_status": find_attempt.stage_status.get("find_orphans"),
            },
        )

    inherited["find_orphans"] = "SUCCESS"
    gate["source_find_run_id"] = find_attempt.run_id
    gate["part2_completed"] = True
    return inherited, gate


def _finish_run(
    database_url: str,
    run_id: str,
    *,
    status: Literal["success", "warning", "blocked", "failed"],
    stage_status: dict[str, str],
    safety_report: dict,
    log_text: str,
) -> None:
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extractor_maintenance_runs
                SET status = %s,
                    finished_at = NOW(),
                    stage_status = %s::jsonb,
                    safety_report = safety_report || %s::jsonb,
                    log_text = %s
                WHERE run_id = %s AND status = 'running'
                """,
                (
                    status,
                    json.dumps(stage_status),
                    json.dumps(safety_report),
                    log_text,
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"maintenance run {run_id} is no longer owned by this worker"
                )
        conn.commit()
    finally:
        conn.close()


def _load_cleanup_receipt(database_url: str, run_id: str) -> dict | None:
    """Return the receipt cleanup committed atomically with its deletions."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT safety_report->'cleanup_receipt'
                FROM extractor_maintenance_runs
                WHERE run_id = %s AND status = 'running'
                """,
                (run_id,),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row or not isinstance(row[0], dict):
        return None
    return row[0]


def _prepare_durable_run(database_url: str) -> PendingRun | None:
    """Select queued work and recover a run whose worker process disappeared.

    A ``running`` row alone is not evidence that work is alive. The session-level
    advisory lock is: PostgreSQL releases it when a process, connection, or pod
    dies. This function therefore changes a running row only while it owns that
    lock. If cleanup already committed its atomic receipt, recovery finalizes the
    row instead of repeating deletion; otherwise the same run is safely requeued.
    """
    try:
        conn = _acquire_pipeline_lock(database_url)
    except MaintenanceRunConflict:
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id::text, trigger, requested_stage, requested_by,
                       status, stage_status, safety_report
                FROM extractor_maintenance_runs
                WHERE status IN ('queued', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE
                """
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None

            run_id, trigger, requested_stage, requested_by, status = row[:5]
            stage_status = row[5] if isinstance(row[5], dict) else {}
            safety_report = row[6] if isinstance(row[6], dict) else {}

            if status == "running":
                receipt = safety_report.get("cleanup_receipt")
                if isinstance(receipt, dict) and receipt.get("committed") is True:
                    # The destructive transaction is authoritative. A crash in
                    # the tiny parent-finalization gap must not rerun it or lose
                    # the report that proves what was deleted.
                    recovered_stages = dict(stage_status)
                    recovered_stages["cleanup_orphans"] = "SUCCESS"
                    warning_codes = safety_report.get("warning_codes") or []
                    recovered_status = "warning" if warning_codes else "success"
                    recovery_patch = {
                        "part3_completed": True,
                        "recovered_after_cleanup_commit": True,
                    }
                    cur.execute(
                        """
                        UPDATE extractor_maintenance_runs
                        SET status = %s,
                            finished_at = NOW(),
                            stage_status = %s::jsonb,
                            safety_report = safety_report || %s::jsonb,
                            log_text = log_text || %s
                        WHERE run_id = %s AND status = 'running'
                        """,
                        (
                            recovered_status,
                            json.dumps(recovered_stages),
                            json.dumps(recovery_patch),
                            f"\n[{_timestamp()}] [pipeline] RECOVERED - cleanup receipt "
                            "was already committed; no stage was repeated\n",
                            run_id,
                        ),
                    )
                    conn.commit()
                    return None

                cur.execute(
                    """
                    UPDATE extractor_maintenance_runs
                    SET status = 'queued',
                        started_at = NULL,
                        finished_at = NULL,
                        safety_report = safety_report || %s::jsonb,
                        log_text = log_text || %s
                    WHERE run_id = %s AND status = 'running'
                    """,
                    (
                        json.dumps({"recovered_after_worker_exit": True}),
                        f"\n[{_timestamp()}] [pipeline] RECOVERED - previous worker "
                        "exited without a committed cleanup receipt; run requeued\n",
                        run_id,
                    ),
                )
            conn.commit()
            return PendingRun(
                run_id=str(run_id),
                trigger=trigger,
                requested_stage=requested_stage,
                requested_by=requested_by,
            )
    finally:
        _release_pipeline_lock(conn)


def dispatch_queued_run(
    database_url: str,
    data_dir: Path = DEFAULT_DATA_DIR,
    log_path: Path | None = None,
) -> bool:
    """Execute the durable queued run, if any.

    Every web worker may poll this function. The PostgreSQL advisory lock inside
    :func:`run_pipeline` chooses one executor, so duplicate pollers are harmless.
    The reservation remains in PostgreSQL across response completion and pod
    replacement; it is never dependent on FastAPI's in-process task queue.
    """
    pending = _prepare_durable_run(database_url)
    if pending is None:
        return False
    try:
        run_pipeline(
            data_dir=data_dir,
            log_path=log_path,
            requested_stage=pending.requested_stage,
            trigger=pending.trigger,
            requested_by=pending.requested_by,
            database_url=database_url,
            run_id=pending.run_id,
            # A scheduled reservation may be claimed by this durable dispatcher
            # instead of the Cron callback that created it. Preserve the same
            # non-destructive policy on both execution paths.
            apply_cleanup=pending.trigger != "scheduled",
            run_openalex_backfill=(
                pending.trigger == "scheduled" and pending.requested_stage == "full"
            ),
        )
    except MaintenanceRunConflict:
        # Another poller won the race after selection. It now owns both the
        # advisory lock and this same durable row; there is nothing to repair.
        return False
    return True


def run_queued(database_url: str | None = None, data_dir: Path = DEFAULT_DATA_DIR) -> None:
    """APScheduler polling entry point for durable admin maintenance requests."""
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for maintenance dispatch")
    try:
        dispatch_queued_run(database_url, data_dir=data_dir)
    except Exception:
        print("[extractor_maintenance] Durable dispatcher failed:")
        traceback.print_exc()


def _read_safety_report(path: Path) -> dict:
    try:
        if path.exists() and path.stat().st_size:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def run_pipeline(
    data_dir: Path = DEFAULT_DATA_DIR,
    log_path: Path | None = None,
    *,
    apply_cleanup: bool = True,
    runner: StageRunner | None = None,
    requested_stage: RequestedStage = "full",
    trigger: Literal["scheduled", "admin", "cli"] = "cli",
    requested_by: str | None = None,
    database_url: str | None = None,
    run_id: str | None = None,
    run_openalex_backfill: bool = False,
) -> bool:
    """Run a full or individual maintenance stage and return its success."""
    if requested_stage not in {"full", "sync", "find", "cleanup"}:
        raise ValueError(f"unknown maintenance stage: {requested_stage}")

    # This is the final invariant, independent of which scheduler/dispatcher
    # entry point invoked us: unattended work may report orphan candidates but
    # can never pass --apply to cleanup. (The retire stage below is the one
    # exception, bounded by the extractor's manifest and its own cap.)
    if trigger == "scheduled":
        apply_cleanup = False

    selected = _STAGES_BY_REQUEST[requested_stage]
    statuses = _initial_stage_status(requested_stage)
    safety_report: dict = {}
    final_status: Literal["success", "warning", "blocked", "failed"] = "failed"
    succeeded = False
    run_log: _RunLog | None = None
    report_path: Path | None = None
    retire_summary_path: Path | None = None
    snapshot_bound: tuple[Path, str] | None = None
    lock_conn = None
    history_owned = False
    setup_error = "pipeline failed before logging started"

    try:
        runner = runner or subprocess.run

        if database_url:
            if run_id is None:
                # Reserve the history row while briefly proving that no live
                # pipeline owns the process lock.
                run_id = queue_maintenance_run(
                    database_url,
                    requested_stage,
                    trigger=trigger,
                    requested_by=requested_by,
                )
            try:
                # This dedicated connection stays open until every child stage
                # has exited and history has been finalized.
                lock_conn = _acquire_pipeline_lock(
                    database_url,
                    wait_seconds=_lock_wait_seconds(),
                )
            except MaintenanceRunConflict:
                # Another poller may already be starting this same durable row.
                # Never turn queued work into a failure merely because this
                # process lost the executor-election race.
                raise
            _mark_run_started(database_url, run_id)
            # A second dispatcher can select the same queued row before the
            # first one acquires the process lock. If the first finishes during
            # our lock wait, _mark_run_started rejects its now-terminal row.
            # Claim ownership only after this transition succeeds so the loser
            # can never overwrite the winner's completed history in `finally`.
            history_owned = True

        run_id = run_id or str(uuid4())
        data_dir = Path(data_dir).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = Path(log_path or _configured_log_path()).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        report_handle = tempfile.NamedTemporaryFile(
            prefix="flora_sync_report_", suffix=".json", delete=False
        )
        report_handle.close()
        report_path = Path(report_handle.name)

        sync_command = [
            sys.executable,
            str(ROOT / "sync_csv.py"),
            "--data-dir",
            str(data_dir),
            "--report-json",
            str(report_path),
            "--maintenance-run-id",
            str(run_id),
        ]
        auto_retire = requested_stage == "full" and _auto_retire_enabled()
        if auto_retire:
            statuses["retire_superseded"] = "PENDING"
            summary_handle = tempfile.NamedTemporaryFile(
                prefix="flora_retire_", suffix=".json", delete=False
            )
            summary_handle.close()
            retire_summary_path = Path(summary_handle.name)

        if "sync_csv" in selected:
            baseline = _baseline_expectation(database_url)
            if baseline.get("baseline_file"):
                sync_command += ["--baseline-file", str(baseline["baseline_file"])]
            if baseline.get("baseline_sha256"):
                sync_command += ["--baseline-sha256", str(baseline["baseline_sha256"])]
            if baseline.get("require_baseline"):
                sync_command.append("--require-baseline")

        # Built only once a snapshot has been resolved and verified, so no code
        # path can hand the orphan stages an unbound file.
        find_command: list[str] | None = None
        cleanup_command: list[str] | None = None
        backfill_command = [sys.executable, str(ROOT / "backfill_oa_work_ids.py")]

        def bind_orphan_stages(snapshot_path: Path, snapshot_sha256: str) -> None:
            nonlocal find_command, cleanup_command, snapshot_bound
            find_command = [
                sys.executable,
                str(ROOT / "find_orphans.py"),
                "--input",
                str(snapshot_path),
                "--expect-sha256",
                snapshot_sha256,
            ]
            cleanup_command = [
                sys.executable,
                str(ROOT / "cleanup_orphans.py"),
                "--input",
                str(snapshot_path),
                "--expect-sha256",
                snapshot_sha256,
                "--maintenance-run-id",
                str(run_id),
            ]
            if apply_cleanup:
                cleanup_command.append("--apply")
            snapshot_bound = (snapshot_path, snapshot_sha256)

        succeeded = True
        with log_path.open("a", encoding="utf-8", newline="") as combined_log:
            run_log = _RunLog(combined_log)
            cleanup_mode = (
                "not-selected"
                if "cleanup_orphans" not in selected
                else "apply" if apply_cleanup else "preview"
            )
            _emit(run_log, "=" * 88)
            _emit(
                run_log,
                f"[{_timestamp()}] [pipeline] START run_id={run_id} trigger={trigger} "
                f"requested_stage={requested_stage} data_dir={data_dir} "
                f"cleanup_mode={cleanup_mode}",
            )

            def persist_progress() -> None:
                if database_url and history_owned:
                    _update_run_progress(
                        database_url,
                        str(run_id),
                        stage_status=statuses,
                        safety_report=safety_report,
                        log_text=run_log.getvalue(),
                    )

            # Individually requested Part 2/3 operations inherit only verified
            # prerequisites. The newest attempt wins: an incomplete new sync
            # cannot be bypassed by falling back to an older successful one.
            if requested_stage in {"find", "cleanup"}:
                gate: dict = {}
                try:
                    if database_url:
                        inherited, gate = _load_prerequisites(
                            database_url,
                            requested_stage,
                        )
                        statuses = {**inherited, **statuses}
                        snapshot_path, snapshot_sha256 = _resolve_run_snapshot(
                            data_dir,
                            gate,
                        )
                    else:
                        snapshot_path, snapshot_sha256 = _resolve_unaudited_snapshot(
                            data_dir
                        )
                    bind_orphan_stages(snapshot_path, snapshot_sha256)
                    safety_report.update(gate)
                    _emit(
                        run_log,
                        f"[{_timestamp()}] [pipeline] prerequisite gate PASSED "
                        f"source_sync_run_id={gate.get('source_sync_run_id')}"
                        + (
                            f" source_find_run_id={gate['source_find_run_id']}"
                            if gate.get("source_find_run_id")
                            else ""
                        )
                        + f" snapshot={snapshot_path.name} sha256={snapshot_sha256}",
                    )
                    persist_progress()
                except (MaintenancePrerequisiteError, SnapshotIntegrityError) as exc:
                    succeeded = False
                    statuses[selected[-1]] = "BLOCKED"
                    error_code = (
                        "prerequisite_stage_incomplete"
                        if isinstance(exc, MaintenancePrerequisiteError)
                        else exc.code
                    )
                    safety_report = {
                        "success": False,
                        "status": "blocked",
                        "warning_codes": [],
                        "error_code": error_code,
                        "message": str(exc),
                        **gate,
                        **exc.details,
                        # Last word: several detail payloads carry the gate's own
                        # "passed" marker from the stage that did clear.
                        "prerequisite_gate": "blocked",
                    }
                    _emit(
                        run_log,
                        f"[{_timestamp()}] [pipeline] BLOCKED {error_code}: {exc}",
                    )
                    persist_progress()

            if succeeded and "sync_csv" in selected:
                result = _run_stage(run_log, "sync_csv", sync_command, runner)
                safety_report = _read_safety_report(report_path)
                sync_completed = result.success and _part1_report_is_verified(
                    safety_report,
                    str(run_id),
                )
                if sync_completed:
                    # Resolve the archive before Part 1 counts as SUCCESS: an
                    # import whose snapshot is unreadable here cannot authorise
                    # deletion, however cleanly the child process exited.
                    try:
                        snapshot_path, snapshot_sha256 = _resolve_run_snapshot(
                            data_dir,
                            safety_report,
                        )
                        bind_orphan_stages(snapshot_path, snapshot_sha256)
                        _emit(
                            run_log,
                            f"[{_timestamp()}] [sync_csv] snapshot bound "
                            f"{snapshot_path.name} sha256={snapshot_sha256}",
                        )
                    except SnapshotIntegrityError as exc:
                        sync_completed = False
                        safety_report.update(
                            {
                                "success": False,
                                "status": "blocked",
                                "error_code": exc.code,
                                "message": str(exc),
                                "part1_completed": False,
                                **exc.details,
                            }
                        )
                        _emit(
                            run_log,
                            f"[{_timestamp()}] [sync_csv] BLOCKED {exc.code}: {exc}",
                        )
                statuses["sync_csv"] = "SUCCESS" if sync_completed else "FAILED"
                if sync_completed:
                    safety_report["source_sync_run_id"] = str(run_id)
                else:
                    succeeded = False
                    if result.success and safety_report.get("error_code") is None:
                        safety_report.update(
                            {
                                "success": False,
                                "status": "blocked",
                                "error_code": "part1_completion_unverified",
                                "message": (
                                    "sync process exited successfully but did not prove "
                                    "import, promotion, and post-promotion verification "
                                    "for this maintenance run"
                                ),
                                "part1_completed": False,
                            }
                        )
                        _emit(
                            run_log,
                            f"[{_timestamp()}] [sync_csv] BLOCKED "
                            "part1_completion_unverified",
                        )
                    if "find_orphans" in selected:
                        reason = (
                            "Part 1 failed or was not fully verified; import and CSV "
                            "promotion are not an approved cleanup baseline"
                        )
                        statuses["find_orphans"] = "SKIPPED"
                        _mark_skipped(run_log, "find_orphans", reason)
                        if "cleanup_orphans" in selected:
                            statuses["cleanup_orphans"] = "SKIPPED"
                            _mark_skipped(
                                run_log,
                                "cleanup_orphans",
                                reason + "; no deletion was attempted",
                            )
                persist_progress()

            # The nightly OpenAlex enrichment used to run from an independent
            # 02:30 scheduler entry and could overlap a slow extractor pipeline.
            # It is now sequenced inside the same PostgreSQL-locked operation.
            # A lookup failure is non-destructive and remains a visible warning;
            # it does not invalidate the CSV snapshot or orphan calculation.
            if succeeded and run_openalex_backfill and requested_stage == "full":
                result = _run_stage(
                    run_log,
                    "backfill_oa_work_ids",
                    backfill_command,
                    runner,
                )
                safety_report["openalex_backfill_completed"] = result.success
                if not result.success:
                    warning_codes = list(safety_report.get("warning_codes") or [])
                    if "openalex_backfill_failed" not in warning_codes:
                        warning_codes.append("openalex_backfill_failed")
                    safety_report["warning_codes"] = warning_codes
                    _emit(
                        run_log,
                        f"[{_timestamp()}] [backfill_oa_work_ids] WARNING - "
                        "the locked pipeline will continue; retry the enrichment later",
                    )
                persist_progress()

            if succeeded and "find_orphans" in selected:
                if find_command is None:
                    raise RuntimeError(
                        "orphan reporting was never bound to a verified snapshot"
                    )
                result = _run_stage(run_log, "find_orphans", find_command, runner)
                statuses["find_orphans"] = "SUCCESS" if result.success else "FAILED"
                safety_report["part2_completed"] = result.success
                if result.success:
                    safety_report["source_find_run_id"] = str(run_id)
                else:
                    succeeded = False
                    if "cleanup_orphans" in selected:
                        statuses["cleanup_orphans"] = "SKIPPED"
                        _mark_skipped(
                            run_log,
                            "cleanup_orphans",
                            "find_orphans failed; no deletion was attempted",
                        )
                persist_progress()

            # Retire what the extractor withdrew — the one stage an unattended run
            # may delete in, and only within csv_to_db.run_retire's limits: the
            # manifest's pair ids, untouched records only (archived first), a
            # cap on the count, and the manifest read at the commit this run's
            # CSV came from. It runs inside this run's lock (the child proves it
            # through the run history rather than taking the lock again). Its
            # outcome is a warning, never a failed sync: the import stands.
            if auto_retire:
                retire_warning = None
                source_commit = safety_report.get("source_commit")
                if not succeeded:
                    statuses["retire_superseded"] = "SKIPPED"
                    _mark_skipped(run_log, "retire_superseded",
                                  "an earlier stage did not succeed")
                elif not (database_url and history_owned):
                    statuses["retire_superseded"] = "SKIPPED"
                    _mark_skipped(run_log, "retire_superseded",
                                  "no audited run history to gate the apply on")
                elif snapshot_bound is None or not source_commit:
                    statuses["retire_superseded"] = "SKIPPED"
                    retire_warning = "retire_source_commit_unknown"
                    _mark_skipped(run_log, "retire_superseded",
                                  "the sync did not record the extractor commit it "
                                  "read, so the manifest cannot be matched to it")
                else:
                    retire_command = [
                        sys.executable, str(ROOT / "csv_to_db.py"),
                        "--input", str(snapshot_bound[0]),
                        "--retire", "github",
                        "--retire-commit", str(source_commit),
                        "--apply",
                        "--maintenance-run-id", str(run_id),
                        "--retire-summary-json", str(retire_summary_path),
                    ]
                    result = _run_stage(run_log, "retire_superseded", retire_command,
                                        runner)
                    try:
                        summary = json.loads(
                            retire_summary_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        summary = None
                    safety_report["retire"] = summary
                    if result.success and summary:
                        statuses["retire_superseded"] = "SUCCESS"
                    elif result.returncode == 3:
                        statuses["retire_superseded"] = "BLOCKED"
                        retire_warning = "retire_cap_exceeded"
                    else:
                        statuses["retire_superseded"] = "FAILED"
                        retire_warning = "retire_failed"
                if retire_warning:
                    warning_codes = list(safety_report.get("warning_codes") or [])
                    if retire_warning not in warning_codes:
                        warning_codes.append(retire_warning)
                    safety_report["warning_codes"] = warning_codes
                persist_progress()

            if succeeded and "cleanup_orphans" in selected:
                if cleanup_command is None:
                    raise RuntimeError(
                        "orphan cleanup was never bound to a verified snapshot"
                    )
                result = _run_stage(run_log, "cleanup_orphans", cleanup_command, runner)
                cleanup_succeeded = result.success
                if database_url and history_owned and apply_cleanup:
                    receipt = _load_cleanup_receipt(database_url, str(run_id))
                    receipt_committed = bool(
                        isinstance(receipt, dict) and receipt.get("committed") is True
                    )
                    if receipt_committed:
                        safety_report["cleanup_receipt"] = receipt
                        cleanup_succeeded = True
                        if not result.success:
                            warning_codes = list(safety_report.get("warning_codes") or [])
                            if "cleanup_child_exit_after_commit" not in warning_codes:
                                warning_codes.append("cleanup_child_exit_after_commit")
                            safety_report["warning_codes"] = warning_codes
                            _emit(
                                run_log,
                                f"[{_timestamp()}] [cleanup_orphans] WARNING - child "
                                "reported failure after PostgreSQL committed the deletion receipt",
                            )
                    else:
                        cleanup_succeeded = False
                        safety_report.update(
                            {
                                "success": False,
                                "status": "error",
                                "error_code": "cleanup_receipt_missing",
                                "message": (
                                    "cleanup exited without an atomic PostgreSQL receipt; "
                                    "deletion is not recorded as complete"
                                ),
                            }
                        )
                        _emit(
                            run_log,
                            f"[{_timestamp()}] [cleanup_orphans] FAILED - atomic "
                            "cleanup receipt is missing",
                        )
                if apply_cleanup:
                    statuses["cleanup_orphans"] = (
                        "SUCCESS" if cleanup_succeeded else "FAILED"
                    )
                    safety_report["part3_completed"] = cleanup_succeeded
                else:
                    statuses["cleanup_orphans"] = (
                        "DRY_RUN" if cleanup_succeeded else "FAILED"
                    )
                    safety_report["part3_completed"] = False
                    safety_report["cleanup_preview_completed"] = cleanup_succeeded
                    safety_report["cleanup_requires_manual_start"] = True
                    if cleanup_succeeded:
                        _emit(
                            run_log,
                            f"[{_timestamp()}] [cleanup_orphans] PREVIEW ONLY - "
                            "no rows were deleted; an admin must start and confirm "
                            "a manual cleanup run",
                        )
                succeeded = cleanup_succeeded
                persist_progress()

            warning_codes = safety_report.get("warning_codes") or []
            safety_block = safety_report.get("error_code") in {
                "empty_resolved_snapshot",
                "excessive_resolved_removal",
                "part1_completion_unverified",
                "prerequisite_stage_incomplete",
                "baseline_snapshot_unavailable",
                "missing_local_baseline",
                "snapshot_archive_unrecorded",
                "snapshot_archive_unavailable",
                "snapshot_archive_mismatch",
                "snapshot_digest_missing",
                "invalid_removal_percent_configuration",
            }
            if not succeeded:
                final_status = "blocked" if safety_block else "failed"
            elif warning_codes:
                final_status = "warning"
            else:
                final_status = "success"
            _summary(run_log, statuses, final_status)
    except MaintenanceRunConflict:
        raise
    except Exception:
        succeeded = False
        final_status = "failed"
        setup_error = traceback.format_exc().rstrip()
        if run_log is not None:
            _emit(run_log, setup_error)
            _summary(run_log, statuses, final_status)
        else:
            sys.stderr.write(setup_error + "\n")
    finally:
        if report_path is not None:
            report_path.unlink(missing_ok=True)
        if retire_summary_path is not None:
            retire_summary_path.unlink(missing_ok=True)
        try:
            if database_url and history_owned:
                try:
                    _finish_run(
                        database_url,
                        run_id,
                        status=final_status,
                        stage_status=statuses,
                        safety_report=safety_report,
                        log_text=run_log.getvalue() if run_log else setup_error,
                    )
                except Exception:
                    succeeded = False
                    traceback.print_exc()
        finally:
            if lock_conn is not None:
                _release_pipeline_lock(lock_conn)

    return succeeded


def run_scheduled(database_url: str | None = None) -> None:
    """APScheduler entry point with durable history and single-run exclusion."""
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for locked scheduled maintenance")
    try:
        succeeded = run_pipeline(
            trigger="scheduled",
            database_url=database_url,
            apply_cleanup=False,
            run_openalex_backfill=True,
        )
    except MaintenanceRunConflict as exc:
        print(f"[extractor_maintenance] Scheduled run skipped: {exc}")
        return
    if not succeeded:
        raise RuntimeError(
            f"Extractor maintenance failed; see {_configured_log_path()} or the admin panel"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Run the non-destructive CSV sync/report routine or explicitly select "
            "the separate guarded-cleanup stage"
        )
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument(
        "--stage",
        choices=("full", "sync", "find", "cleanup"),
        default="full",
        help=(
            "Run routine sync+report ('full') or one stage; only 'cleanup' can "
            "delete records"
        ),
    )
    parser.add_argument(
        "--dry-run-cleanup",
        action="store_true",
        help="With --stage cleanup, preview without --apply",
    )
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required to enforce single-process maintenance")
    succeeded = run_pipeline(
        data_dir=args.data_dir,
        log_path=args.log,
        apply_cleanup=not args.dry_run_cleanup,
        requested_stage=args.stage,
        database_url=database_url,
    )
    raise SystemExit(0 if succeeded else 1)
