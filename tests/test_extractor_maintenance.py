"""Regression coverage for the fail-fast extractor maintenance pipeline."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from extractor_maintenance import (
    MaintenancePrerequisiteError,
    PendingRun,
    StageAttempt,
    _acquire_pipeline_lock,
    _load_prerequisites,
    _prepare_durable_run,
    dispatch_queued_run,
    run_pipeline,
    run_scheduled,
)
from extractor_storage import SnapshotIntegrityError
from csv_to_db import require_maintenance_gate


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_snapshot_database(monkeypatch):
    """The snapshot copy talks to PostgreSQL; unit tests record the calls instead."""
    import extractor_maintenance as em

    calls = {"store": [], "restore": [], "prune": []}
    monkeypatch.setattr(
        em, "store_snapshot",
        lambda url, path, sha, **kw: calls["store"].append((Path(path).name, sha, kw)) or True,
    )
    monkeypatch.setattr(
        em, "restore_snapshot",
        lambda url, sha, dest: calls["restore"].append((sha, Path(dest).name)) or False,
    )
    monkeypatch.setattr(
        em, "prune_snapshots",
        lambda url, keep, protect=(): calls["prune"].append((keep, protect)) or 0,
    )
    return calls


SNAPSHOT_BYTES = (
    b"pair_id,paper_type,link_method,doi_r\n"
    b"abc,replication,llm_references,10.1/x\n"
)
SNAPSHOT_SHA256 = hashlib.sha256(SNAPSHOT_BYTES).hexdigest()
SOURCE_COMMIT = "d7f55d98b7994109a12701c935b21fcc9dd14968"


class ScriptedRunner:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        script = Path(command[1]).name
        returncode, output = self.responses[script]
        if script == "sync_csv.py":
            report_path = Path(command[command.index("--report-json") + 1])
            if not report_path.exists() or not report_path.stat().st_size:
                run_id = command[command.index("--maintenance-run-id") + 1]
                succeeded = returncode == 0
                data_dir = Path(command[command.index("--data-dir") + 1])
                archive_file = f"extracted_20260901T000000Z_{run_id[:8]}.csv"
                if succeeded:
                    write_snapshot(data_dir / archive_file)
                report_path.write_text(
                    json.dumps(
                        {
                            "success": succeeded,
                            "status": "success" if succeeded else "error",
                            "warning_codes": [],
                            "error_code": None if succeeded else "extractor_pipeline_error",
                            "maintenance_run_id": run_id,
                            "archive_file": archive_file if succeeded else None,
                            "archive_sha256": SNAPSHOT_SHA256 if succeeded else None,
                            "archive_verified": succeeded,
                            "download_completed": succeeded,
                            "validation_completed": succeeded,
                            "import_completed": succeeded,
                            "promotion_completed": succeeded,
                            "promotion_verified": succeeded,
                            "part1_completed": succeeded,
                            "source_commit": SOURCE_COMMIT if succeeded else None,
                        }
                    ),
                    encoding="utf-8",
                )
        if script == "csv_to_db.py" and returncode == 0:
            summary = Path(command[command.index("--retire-summary-json") + 1])
            summary.write_text(json.dumps({"status": "applied", "retire": 2,
                                           "flag": 1}), encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode, stdout=output)


class ReportingRunner(ScriptedRunner):
    def __init__(self, responses, sync_report):
        super().__init__(responses)
        self.sync_report = sync_report

    def __call__(self, command, **kwargs):
        if Path(command[1]).name == "sync_csv.py":
            report_path = Path(command[command.index("--report-json") + 1])
            run_id = command[command.index("--maintenance-run-id") + 1]
            data_dir = Path(command[command.index("--data-dir") + 1])
            archive_file = f"extracted_20260901T000000Z_{run_id[:8]}.csv"
            write_snapshot(data_dir / archive_file)
            completed = {
                "success": True,
                "status": "success",
                "warning_codes": [],
                "error_code": None,
                "maintenance_run_id": run_id,
                "archive_file": archive_file,
                "archive_sha256": SNAPSHOT_SHA256,
                "archive_verified": True,
                "download_completed": True,
                "validation_completed": True,
                "import_completed": True,
                "promotion_completed": True,
                "promotion_verified": True,
                "part1_completed": True,
            }
            completed.update(self.sync_report)
            report_path.write_text(json.dumps(completed), encoding="utf-8")
        return super().__call__(command, **kwargs)


def write_snapshot(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SNAPSHOT_BYTES)
    return path


def _script_order(runner: ScriptedRunner) -> list[str]:
    return [Path(command[1]).name for command in runner.commands]


def _stage_input(runner: ScriptedRunner, script: str) -> tuple[str, str]:
    for command in runner.commands:
        if Path(command[1]).name == script:
            return (
                command[command.index("--input") + 1],
                command[command.index("--expect-sha256") + 1],
            )
    raise AssertionError(f"{script} was never run")


def test_app_scheduler_runs_the_full_pipeline_instead_of_sync_alone():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    scheduler = app_source.split("def _start_scheduler()", 1)[1].split(
        "_start_scheduler()", 1
    )[0]
    assert "run_scheduled" in scheduler
    assert "scheduler.add_job(" in scheduler
    assert "run_scheduled" in scheduler
    assert "run_queued" in scheduler
    assert "sync_once" not in scheduler


def test_scheduler_uses_explicit_utc_and_backfill_is_not_independent():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    scheduler = app_source.split("def _start_scheduler()", 1)[1].split(
        "_start_scheduler()", 1
    )[0]
    cron_lines = [line for line in scheduler.splitlines() if "CronTrigger(" in line]
    assert cron_lines
    assert all('timezone="UTC"' in line for line in cron_lines)
    assert "scheduler.add_job(_backfill_oa_work_ids" not in scheduler

    maintenance_source = (ROOT / "extractor_maintenance.py").read_text(encoding="utf-8")
    scheduled = maintenance_source.split("def run_scheduled(", 1)[1].split(
        "\n\nif __name__", 1
    )[0]
    assert "run_openalex_backfill=True" in scheduled


def test_manual_route_only_persists_work_for_the_durable_dispatcher():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    route = app_source.split("def admin_start_maintenance(", 1)[1].split(
        "# ---------------------------------------------------------------------------\n"
        "# Nightly CSV sync scheduler",
        1,
    )[0]
    assert "queue_maintenance_run(" in route
    assert "background_tasks" not in route
    assert ".add_task(" not in route


def test_dispatcher_executes_the_persisted_request_not_process_memory(tmp_path):
    pending = PendingRun(
        run_id="durable-run",
        trigger="admin",
        requested_stage="find",
        requested_by="hamid",
    )
    calls = []
    with patch(
        "extractor_maintenance._prepare_durable_run", return_value=pending
    ), patch(
        "extractor_maintenance.run_pipeline",
        side_effect=lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    ):
        dispatched = dispatch_queued_run(
            "postgresql://test",
            data_dir=tmp_path,
            log_path=tmp_path / "maintenance.log",
        )

    assert dispatched is True
    assert calls[0][1]["run_id"] == "durable-run"
    assert calls[0][1]["requested_stage"] == "find"
    assert calls[0][1]["requested_by"] == "hamid"


def test_scheduled_dispatch_preserves_the_sequenced_backfill(tmp_path):
    pending = PendingRun(
        run_id="nightly-run",
        trigger="scheduled",
        requested_stage="full",
        requested_by=None,
    )
    calls = []
    with patch(
        "extractor_maintenance._prepare_durable_run", return_value=pending
    ), patch(
        "extractor_maintenance.run_pipeline",
        side_effect=lambda *args, **kwargs: calls.append(kwargs) or True,
    ):
        assert dispatch_queued_run("postgresql://test", data_dir=tmp_path) is True

    assert calls[0]["run_openalex_backfill"] is True
    assert "apply_cleanup" not in calls[0]


def test_run_scheduled_requests_backfill_inside_the_locked_pipeline():
    with patch("extractor_maintenance.run_pipeline", return_value=True) as pipeline:
        run_scheduled("postgresql://test")
    assert pipeline.call_args.kwargs["run_openalex_backfill"] is True


def test_orphan_cleanup_is_gone_from_every_layer():
    """Records leave only through the retire stage; nothing deletes by absence."""
    import extractor_maintenance as em

    assert not (ROOT / "cleanup_orphans.py").exists()
    assert em._STAGES_BY_REQUEST == {
        "full": ["sync_csv", "find_orphans"],
        "sync": ["sync_csv"],
        "find": ["find_orphans"],
    }
    with pytest.raises(ValueError, match="unknown maintenance stage"):
        em.queue_maintenance_run("postgresql://test", "cleanup", trigger="admin")
    with pytest.raises(ValueError, match="unknown maintenance stage"):
        run_pipeline(requested_stage="cleanup")

    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'stage: Literal["full", "sync", "find"] = "full"' in app_source
    assert "confirm_cleanup" not in app_source
    for path in ("docs/app.js", "docs/index.html"):
        frontend = (ROOT / path).read_text(encoding="utf-8")
        assert 'data-stage="cleanup"' not in frontend
        assert "confirm_cleanup" not in frontend
        assert "admin-maintenance-badge" not in frontend


class _RecoveryCursor:
    rowcount = 1

    def __init__(self, row):
        self.calls = []
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        row, self._row = self._row, None
        return row


class _RecoveryConnection:
    def __init__(self, row):
        self.cur = _RecoveryCursor(row)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


def test_recovery_requeues_a_run_whose_worker_disappeared():
    connection = _RecoveryConnection(("crashed-run", "admin", "full", "hamid", "running"))
    with patch(
        "extractor_maintenance._acquire_pipeline_lock", return_value=connection
    ), patch("extractor_maintenance._release_pipeline_lock") as release:
        selected = _prepare_durable_run("postgresql://test")

    assert selected == PendingRun("crashed-run", "admin", "full", "hamid")
    update_sql, update_params = connection.cur.calls[1]
    assert "status = 'queued'" in update_sql
    assert json.loads(update_params[0]) == {"recovered_after_worker_exit": True}
    release.assert_called_once_with(connection)


def test_recovery_closes_a_queued_run_of_the_removed_cleanup_stage():
    """A cleanup request queued before the upgrade would otherwise block every run."""
    connection = _RecoveryConnection(("old-cleanup", "admin", "cleanup", "hamid", "queued"))
    with patch(
        "extractor_maintenance._acquire_pipeline_lock", return_value=connection
    ), patch("extractor_maintenance._release_pipeline_lock"):
        selected = _prepare_durable_run("postgresql://test")

    assert selected is None
    update_sql, update_params = connection.cur.calls[1]
    # Closed as failed, unless an old cleanup had already committed its deletions.
    assert "cleanup_receipt'->>'committed' = 'true'" in update_sql
    assert "ELSE 'failed'" in update_sql
    assert "no longer exists" in update_params[0]
    assert update_params[1] == "old-cleanup"


def test_routine_full_run_stops_after_sync_and_read_only_report(tmp_path):
    runner = ScriptedRunner(
        {
            "sync_csv.py": (0, "sync report\n"),
            "find_orphans.py": (0, "orphan report\n"),
        }
    )
    log_path = tmp_path / "maintenance.log"

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=log_path,
        runner=runner,
    )

    assert succeeded is True
    assert _script_order(runner) == ["sync_csv.py", "find_orphans.py"]
    log = log_path.read_text(encoding="utf-8")
    assert "sync report" in log
    assert "orphan report" in log
    assert "cleanup_orphans.py" not in log
    assert "sync_csv=SUCCESS find_orphans=SUCCESS" in log


def test_nightly_backfill_is_sequenced_inside_the_pipeline_lock(tmp_path):
    runner = ScriptedRunner(
        {
            "sync_csv.py": (0, "sync ok\n"),
            "backfill_oa_work_ids.py": (1, "OpenAlex temporarily unavailable\n"),
            "find_orphans.py": (0, "find ok\n"),
        }
    )
    log_path = tmp_path / "maintenance.log"

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=log_path,
        runner=runner,
        run_openalex_backfill=True,
    )

    assert succeeded is True
    assert _script_order(runner) == [
        "sync_csv.py",
        "backfill_oa_work_ids.py",
        "find_orphans.py",
    ]
    log = log_path.read_text(encoding="utf-8")
    assert "backfill_oa_work_ids] WARNING" in log
    assert "[pipeline] WARNING" in log


def test_sync_failure_skips_the_read_only_orphan_report_and_logs_why(tmp_path):
    runner = ScriptedRunner({"sync_csv.py": (1, "schema drift\n")})
    log_path = tmp_path / "maintenance.log"

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=log_path,
        runner=runner,
    )

    assert succeeded is False
    assert _script_order(runner) == ["sync_csv.py"]
    log = log_path.read_text(encoding="utf-8")
    assert "[find_orphans] SKIPPED — Part 1 failed or was not fully verified" in log
    assert "retire_superseded] START" not in log
    assert "sync_csv=FAILED find_orphans=SKIPPED" in log


def test_orphan_report_failure_is_logged_and_stops_the_run(tmp_path):
    runner = ScriptedRunner(
        {
            "sync_csv.py": (0, "sync complete\n"),
            "find_orphans.py": (2, "database unavailable\n"),
        }
    )
    log_path = tmp_path / "maintenance.log"

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=log_path,
        runner=runner,
    )

    assert succeeded is False
    assert _script_order(runner) == ["sync_csv.py", "find_orphans.py"]
    log = log_path.read_text(encoding="utf-8")
    assert "retire_superseded] START" not in log
    assert "sync_csv=SUCCESS find_orphans=FAILED" in log


@pytest.mark.parametrize(
    ("requested_stage", "expected_script"),
    [
        ("sync", "sync_csv.py"),
        ("find", "find_orphans.py"),
    ],
)
def test_admin_can_run_each_stage_individually(tmp_path, requested_stage, expected_script):
    runner = ScriptedRunner({expected_script: (0, "stage complete\n")})
    # The report is always bound to a snapshot; unaudited runs use the
    # promoted CSV and still hand its digest to the child process.
    write_snapshot(tmp_path / "data" / "extracted_latest.csv")

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=tmp_path / "maintenance.log",
        requested_stage=requested_stage,
        runner=runner,
    )

    assert succeeded is True
    assert _script_order(runner) == [expected_script]


def _verified_sync_attempt(run_id="sync-good"):
    return StageAttempt(
        run_id=run_id,
        status="success",
        stage_status={"sync_csv": "SUCCESS"},
        safety_report={
            "maintenance_run_id": run_id,
            "archive_file": "extracted_20260901T000000Z_syncgood.csv",
            "archive_sha256": SNAPSHOT_SHA256,
            "archive_verified": True,
            "download_completed": True,
            "validation_completed": True,
            "import_completed": True,
            "promotion_completed": True,
            "promotion_verified": True,
            "part1_completed": True,
        },
    )


def test_manual_find_uses_the_newest_part1_attempt_and_rejects_incomplete_promotion():
    newest_failed = StageAttempt(
        run_id="sync-failed",
        status="failed",
        stage_status={"sync_csv": "FAILED"},
        safety_report={
            "maintenance_run_id": "sync-failed",
            "import_completed": True,
            "promotion_completed": False,
            "part1_completed": False,
        },
    )

    with patch(
        "extractor_maintenance._latest_stage_attempt",
        return_value=newest_failed,
    ), pytest.raises(MaintenancePrerequisiteError, match="newest Part 1"):
        _load_prerequisites("postgresql://test")


def test_manual_find_inherits_the_verified_sync_and_its_snapshot():
    with patch(
        "extractor_maintenance._latest_stage_attempt",
        return_value=_verified_sync_attempt(),
    ):
        statuses, gate = _load_prerequisites("postgresql://test")

    assert statuses == {"sync_csv": "SUCCESS"}
    assert gate["source_sync_run_id"] == "sync-good"
    assert gate["part1_completed"] is True
    assert gate["archive_sha256"] == SNAPSHOT_SHA256


def test_pipeline_does_not_run_manual_find_when_prerequisite_gate_blocks(tmp_path):
    runner = ScriptedRunner({"find_orphans.py": (0, "must not run\n")})
    finished = []
    lock_connection = object()

    with patch(
        "extractor_maintenance._acquire_pipeline_lock",
        return_value=lock_connection,
    ), patch(
        "extractor_maintenance._mark_run_started",
    ), patch(
        "extractor_maintenance._load_prerequisites",
        side_effect=MaintenancePrerequisiteError("Part 1 is incomplete"),
    ), patch(
        "extractor_maintenance._update_run_progress",
    ), patch(
        "extractor_maintenance._finish_run",
        side_effect=lambda *_args, **kwargs: finished.append(kwargs),
    ), patch(
        "extractor_maintenance._release_pipeline_lock",
    ):
        succeeded = run_pipeline(
            data_dir=tmp_path / "data",
            log_path=tmp_path / "maintenance.log",
            requested_stage="find",
            runner=runner,
            database_url="postgresql://test",
            run_id="find-current",
        )

    assert succeeded is False
    assert runner.commands == []
    assert finished[0]["status"] == "blocked"
    assert finished[0]["stage_status"] == {"find_orphans": "BLOCKED"}


def test_zero_exit_without_run_scoped_part1_proof_still_blocks_later_stages(tmp_path):
    runner = ReportingRunner(
        {"sync_csv.py": (0, "claimed success\n")},
        {"promotion_verified": False, "part1_completed": False},
    )

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=tmp_path / "maintenance.log",
        runner=runner,
    )

    assert succeeded is False
    assert _script_order(runner) == ["sync_csv.py"]
    log = (tmp_path / "maintenance.log").read_text(encoding="utf-8")
    assert "part1_completion_unverified" in log
    assert "find_orphans=SKIPPED" in log
    assert "retire_superseded] START" not in log


class _GateCursor:
    """One extractor_maintenance_runs row, as the retire gate reads it."""

    def __init__(self, report):
        self.report = report

    def execute(self, *_args):
        pass

    def fetchone(self):
        return ("running", {"sync_csv": "SUCCESS", "find_orphans": "SUCCESS"}, self.report)


_VERIFIED_RUN = {
    "part1_completed": True,
    "part2_completed": True,
    "source_sync_run_id": "sync-good",
    "source_find_run_id": "find-good",
    "archive_sha256": SNAPSHOT_SHA256,
}


def test_the_retire_gate_accepts_its_own_verified_run():
    require_maintenance_gate(_GateCursor(dict(_VERIFIED_RUN)), "run-current", SNAPSHOT_SHA256)


def test_parent_history_updates_merge_instead_of_replacing_the_report():
    source = (ROOT / "extractor_maintenance.py").read_text(encoding="utf-8")
    for function_name, following in (
        ("_update_run_progress", "_latest_stage_attempt"),
        ("_finish_run", "_prepare_durable_run"),
    ):
        body = source.split(f"def {function_name}(", 1)[1].split(
            f"def {following}(", 1
        )[0]
        assert "safety_report = safety_report || %s::jsonb" in body


def test_the_retire_gate_rejects_a_snapshot_the_run_never_imported():
    """The run-ID gate passes; the bytes this pod read are still the wrong ones."""
    stale_sha256 = hashlib.sha256(b"an older bundled CSV").hexdigest()
    with pytest.raises(RuntimeError, match="different snapshot"):
        require_maintenance_gate(_GateCursor(dict(_VERIFIED_RUN)), "run-current", stale_sha256)


def test_the_retire_gate_rejects_a_run_with_no_recorded_snapshot():
    report = {key: value for key, value in _VERIFIED_RUN.items() if key != "archive_sha256"}
    with pytest.raises(RuntimeError, match="Parts 1 and 2 are not verified"):
        require_maintenance_gate(_GateCursor(report), "run-current", SNAPSHOT_SHA256)


def test_the_retire_gate_rejects_a_missing_audited_run():
    with pytest.raises(RuntimeError, match="audited maintenance run"):
        require_maintenance_gate(None, None, SNAPSHOT_SHA256)


def test_the_retire_gate_rejects_a_run_without_a_snapshot_digest():
    with pytest.raises(RuntimeError, match="digest"):
        require_maintenance_gate(None, "run-current", None)


def test_snapshot_safety_block_skips_the_orphan_report(tmp_path):
    runner = ReportingRunner(
        {"sync_csv.py": (1, "BLOCKED excessive_resolved_removal\n")},
        {
            "status": "blocked",
            "error_code": "excessive_resolved_removal",
            "previous_resolved_count": 100,
            "candidate_resolved_count": 80,
            "removed_count": 20,
            "removed_percent": 20,
            "warning_codes": [],
        },
    )
    log_path = tmp_path / "maintenance.log"

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=log_path,
        runner=runner,
    )

    assert succeeded is False
    assert _script_order(runner) == ["sync_csv.py"]
    log = log_path.read_text(encoding="utf-8")
    assert "[pipeline] BLOCKED" in log
    assert "find_orphans=SKIPPED" in log
    assert "retire_superseded] START" not in log


def test_new_pair_id_warning_does_not_stop_later_stages(tmp_path):
    runner = ReportingRunner(
        {
            "sync_csv.py": (0, "new IDs imported\n"),
            "find_orphans.py": (0, "report complete\n"),
        },
        {
            "status": "warning",
            "error_code": None,
            "warning_codes": ["new_resolved_pair_ids"],
            "added_count": 3,
            "added_pair_ids": ["a", "b", "c"],
        },
    )
    log_path = tmp_path / "maintenance.log"

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=log_path,
        runner=runner,
    )

    assert succeeded is True
    assert _script_order(runner) == ["sync_csv.py", "find_orphans.py"]
    assert "[pipeline] WARNING" in log_path.read_text(encoding="utf-8")


def test_postgres_process_lock_is_held_until_every_stage_and_history_finish(tmp_path):
    events = []

    class LockAwareRunner(ScriptedRunner):
        def __call__(self, command, **kwargs):
            assert events[:3] == ["reserved", "locked", "started"]
            assert "released" not in events
            events.append(Path(command[1]).name)
            return super().__call__(command, **kwargs)

    runner = LockAwareRunner(
        {
            "sync_csv.py": (0, "ok\n"),
            "find_orphans.py": (0, "ok\n"),
            "csv_to_db.py": (0, "ok\n"),
        }
    )
    lock_connection = object()

    with patch(
        "extractor_maintenance.queue_maintenance_run",
        side_effect=lambda *_args, **_kwargs: events.append("reserved") or "run-1",
    ), patch(
        "extractor_maintenance._baseline_expectation",
        return_value={},
    ), patch(
        "extractor_maintenance._acquire_pipeline_lock",
        side_effect=lambda *_args, **_kwargs: events.append("locked") or lock_connection,
    ), patch(
        "extractor_maintenance._mark_run_started",
        side_effect=lambda *_args: events.append("started"),
    ), patch(
        "extractor_maintenance._update_run_progress",
        side_effect=lambda *_args, **_kwargs: events.append("progress"),
    ), patch(
        "extractor_maintenance._finish_run",
        side_effect=lambda *_args, **_kwargs: events.append("finished"),
    ), patch(
        "extractor_maintenance._release_pipeline_lock",
        side_effect=lambda *_args: events.append("released"),
    ):
        succeeded = run_pipeline(
            data_dir=tmp_path / "data",
            log_path=tmp_path / "maintenance.log",
            runner=runner,
            database_url="postgresql://test",
        )

    assert succeeded is True
    assert events == [
        "reserved",
        "locked",
        "started",
        "sync_csv.py",
        "progress",
        "find_orphans.py",
        "progress",
        # The automatic retire applies inside the same lock, never beside it.
        "csv_to_db.py",
        "progress",
        "finished",
        "released",
    ]


def test_stage_timeout_fails_fast_and_skips_later_stages(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("EXTRACTOR_STAGE_TIMEOUT_SECONDS", "17")

    def timeout_runner(command, **kwargs):
        calls.append((Path(command[1]).name, kwargs["timeout"]))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="hung output\n")

    log_path = tmp_path / "maintenance.log"
    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=log_path,
        runner=timeout_runner,
    )

    assert succeeded is False
    assert calls == [("sync_csv.py", 17)]
    log = log_path.read_text(encoding="utf-8")
    assert "FAILED timeout=17s" in log
    assert "find_orphans=SKIPPED" in log
    assert "retire_superseded] START" not in log


def test_setup_failure_marks_history_failed_before_releasing_process_lock(tmp_path):
    events = []
    lock_connection = object()

    with patch(
        "extractor_maintenance.queue_maintenance_run", return_value="run-setup"
    ), patch(
        "extractor_maintenance._acquire_pipeline_lock", return_value=lock_connection
    ), patch(
        "extractor_maintenance._mark_run_started"
    ), patch(
        "extractor_maintenance.tempfile.NamedTemporaryFile",
        side_effect=OSError("temporary storage unavailable"),
    ), patch(
        "extractor_maintenance._finish_run",
        side_effect=lambda *_args, **kwargs: events.append(("finished", kwargs["status"])),
    ), patch(
        "extractor_maintenance._release_pipeline_lock",
        side_effect=lambda *_args: events.append(("released", None)),
    ):
        succeeded = run_pipeline(
            data_dir=tmp_path / "data",
            log_path=tmp_path / "maintenance.log",
            database_url="postgresql://test",
        )

    assert succeeded is False
    assert events == [("finished", "failed"), ("released", None)]


def test_late_dispatcher_loser_cannot_overwrite_a_completed_run(tmp_path):
    """A second poller may acquire the lock after the winner already finished."""
    with patch(
        "extractor_maintenance._acquire_pipeline_lock", return_value=object()
    ), patch(
        "extractor_maintenance._mark_run_started",
        side_effect=RuntimeError("maintenance run already completed"),
    ), patch("extractor_maintenance._finish_run") as finish, patch(
        "extractor_maintenance._release_pipeline_lock"
    ) as release:
        succeeded = run_pipeline(
            data_dir=tmp_path / "data",
            log_path=tmp_path / "maintenance.log",
            database_url="postgresql://test",
            run_id="already-finished",
        )

    assert succeeded is False
    finish.assert_not_called()
    release.assert_called_once()


def test_durable_recovery_occurs_only_while_advisory_lock_is_held():
    source = (ROOT / "extractor_maintenance.py").read_text(encoding="utf-8")
    recovery_body = source.split("def _prepare_durable_run(", 1)[1].split(
        "def dispatch_queued_run(", 1
    )[0]
    assert "conn = _acquire_pipeline_lock(database_url)" in recovery_body
    assert recovery_body.index("_acquire_pipeline_lock") < recovery_body.index(
        "status == \"running\""
    )
    assert "status = 'queued'" in recovery_body
    assert "pg_try_advisory_lock" in source
    assert "pg_advisory_unlock" in source


def test_reserved_run_retries_a_brief_advisory_lock_race():
    class FakeCursor:
        def __init__(self):
            self.results = iter([(False,), (True,)])
            self.execute_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            self.execute_count += 1

        def fetchone(self):
            return next(self.results)

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.commits = 0
            self.closed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed = True

    connection = FakeConnection()
    with patch("extractor_maintenance.psycopg2.connect", return_value=connection), \
         patch("extractor_maintenance.time.sleep") as sleep:
        acquired = _acquire_pipeline_lock("postgresql://test", wait_seconds=1)

    assert acquired is connection
    assert connection.cursor_instance.execute_count == 2
    assert connection.commits == 2
    assert connection.closed is False
    sleep.assert_called_once()


def _find_gate_patches(gate):
    """Patch out every database round-trip a manual report run makes."""
    return (
        patch("extractor_maintenance._acquire_pipeline_lock", return_value=object()),
        patch("extractor_maintenance._mark_run_started"),
        patch("extractor_maintenance._update_run_progress"),
        patch("extractor_maintenance._release_pipeline_lock"),
        patch("extractor_maintenance._load_prerequisites", return_value=(
            {"sync_csv": "SUCCESS"},
            gate,
        )),
    )


def _verified_find_gate(archive_file):
    return {
        "prerequisite_gate": "passed",
        "source_sync_run_id": "sync-good",
        "part1_completed": True,
        "archive_file": archive_file,
        "archive_sha256": SNAPSHOT_SHA256,
    }


def test_orphan_stages_read_the_archived_snapshot_not_the_promoted_csv(tmp_path):
    """extracted_latest.csv is mutable and pod-local; the archive is neither."""
    runner = ScriptedRunner(
        {
            "sync_csv.py": (0, "sync report\n"),
            "find_orphans.py": (0, "orphan report\n"),
        }
    )
    data_dir = tmp_path / "data"
    # A stale promoted CSV must not be what the delete list is computed from.
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "extracted_latest.csv").write_bytes(b"stale,bundled,csv\n")

    succeeded = run_pipeline(
        data_dir=data_dir,
        log_path=tmp_path / "maintenance.log",
        runner=runner,
    )

    assert succeeded is True
    for script in ("find_orphans.py",):
        stage_input, expected_sha256 = _stage_input(runner, script)
        assert Path(stage_input).name != "extracted_latest.csv"
        assert expected_sha256 == SNAPSHOT_SHA256
        assert Path(stage_input).read_bytes() == SNAPSHOT_BYTES


def test_replacement_pod_without_the_archive_or_a_database_copy_cannot_report(
        tmp_path, _no_snapshot_database):
    """The reviewer's Pod A/Pod B case: the run-ID gate passes, the bytes are gone."""
    runner = ScriptedRunner({"find_orphans.py": (0, "must not run\n")})
    data_dir = tmp_path / "data"
    archive_file = "extracted_20260901T000000Z_poda0001.csv"
    # Pod B has an older bundled CSV but not Pod A's archive.
    write_snapshot(data_dir / "extracted_latest.csv")
    finished = []

    acquire, started, progress, release, prerequisites = _find_gate_patches(
        _verified_find_gate(archive_file),
    )
    with acquire, started, progress, release, prerequisites, patch(
        "extractor_maintenance._finish_run",
        side_effect=lambda *_args, **kwargs: finished.append(kwargs),
    ):
        succeeded = run_pipeline(
            data_dir=data_dir,
            log_path=tmp_path / "maintenance.log",
            requested_stage="find",
            runner=runner,
            database_url="postgresql://test",
            run_id="find-current",
        )

    assert succeeded is False
    assert runner.commands == []
    # It asked the database first, and the database had nothing.
    assert _no_snapshot_database["restore"] == [(SNAPSHOT_SHA256, archive_file)]
    assert finished[0]["status"] == "blocked"
    assert finished[0]["safety_report"]["error_code"] == "snapshot_archive_unavailable"
    log = (tmp_path / "maintenance.log").read_text(encoding="utf-8")
    assert "database holds no copy" in log


def test_replacement_pod_restores_the_archive_from_the_database(tmp_path, monkeypatch):
    import extractor_maintenance as em

    runner = ScriptedRunner({"find_orphans.py": (0, "report\n")})
    data_dir = tmp_path / "data"
    archive_file = "extracted_20260901T000000Z_poda0001.csv"

    def restore(_url, sha256, dest):
        assert sha256 == SNAPSHOT_SHA256
        write_snapshot(dest)
        return True

    monkeypatch.setattr(em, "restore_snapshot", restore)
    finished = []
    acquire, started, progress, release, prerequisites = _find_gate_patches(
        _verified_find_gate(archive_file),
    )
    with acquire, started, progress, release, prerequisites, patch(
        "extractor_maintenance._finish_run",
        side_effect=lambda *_args, **kwargs: finished.append(kwargs),
    ):
        succeeded = run_pipeline(
            data_dir=data_dir,
            log_path=tmp_path / "maintenance.log",
            requested_stage="find",
            runner=runner,
            database_url="postgresql://test",
            run_id="find-current",
        )

    assert succeeded is True
    stage_input, expected_sha256 = _stage_input(runner, "find_orphans.py")
    assert Path(stage_input).name == archive_file
    assert expected_sha256 == SNAPSHOT_SHA256
    assert finished[0]["stage_status"] == {"sync_csv": "SUCCESS", "find_orphans": "SUCCESS"}


def test_archive_with_different_bytes_cannot_be_reported_on(tmp_path, _no_snapshot_database):
    """A same-named archive holding other content is still the wrong snapshot,
    and the database copy never overwrites it."""
    runner = ScriptedRunner({"find_orphans.py": (0, "must not run\n")})
    data_dir = tmp_path / "data"
    archive_file = "extracted_20260901T000000Z_poda0001.csv"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / archive_file).write_bytes(b"pair_id\ndifferent\n")
    finished = []

    acquire, started, progress, release, prerequisites = _find_gate_patches(
        _verified_find_gate(archive_file),
    )
    with acquire, started, progress, release, prerequisites, patch(
        "extractor_maintenance._finish_run",
        side_effect=lambda *_args, **kwargs: finished.append(kwargs),
    ):
        succeeded = run_pipeline(
            data_dir=data_dir,
            log_path=tmp_path / "maintenance.log",
            requested_stage="find",
            runner=runner,
            database_url="postgresql://test",
            run_id="find-current",
        )

    assert succeeded is False
    assert runner.commands == []
    assert _no_snapshot_database["restore"] == []
    assert finished[0]["safety_report"]["error_code"] == "snapshot_archive_mismatch"


def test_part1_without_a_recorded_archive_never_reaches_the_orphan_stages(tmp_path):
    runner = ReportingRunner(
        {"sync_csv.py": (0, "claimed success\n")},
        {"archive_file": None, "archive_sha256": None, "archive_verified": False},
    )

    succeeded = run_pipeline(
        data_dir=tmp_path / "data",
        log_path=tmp_path / "maintenance.log",
        runner=runner,
    )

    assert succeeded is False
    assert _script_order(runner) == ["sync_csv.py"]
    log = (tmp_path / "maintenance.log").read_text(encoding="utf-8")
    assert "part1_completion_unverified" in log
    assert "find_orphans=SKIPPED" in log
    assert "retire_superseded] START" not in log


def test_sync_receives_the_recorded_baseline_from_run_history(tmp_path):
    """sync_csv cannot tell an empty volume from a first deployment; the DB can."""
    runner = ScriptedRunner({"sync_csv.py": (0, "ok\n")})

    with patch("extractor_maintenance._acquire_pipeline_lock", return_value=object()),          patch("extractor_maintenance._mark_run_started"),          patch("extractor_maintenance._update_run_progress"),          patch("extractor_maintenance._finish_run"),          patch("extractor_maintenance._release_pipeline_lock"),          patch(
             "extractor_maintenance._baseline_expectation",
             return_value={
                 "baseline_file": "extracted_20260831T000000Z_prev0001.csv",
                 "baseline_sha256": SNAPSHOT_SHA256,
                 "require_baseline": True,
             },
         ):
        run_pipeline(
            data_dir=tmp_path / "data",
            log_path=tmp_path / "maintenance.log",
            requested_stage="sync",
            runner=runner,
            database_url="postgresql://test",
            run_id="sync-current",
        )

    command = runner.commands[0]
    assert command[command.index("--baseline-file") + 1] == (
        "extracted_20260831T000000Z_prev0001.csv"
    )
    assert command[command.index("--baseline-sha256") + 1] == SNAPSHOT_SHA256
    assert "--require-baseline" in command


def test_snapshot_verification_rejects_a_file_that_changed_underneath_it(tmp_path):
    from extractor_storage import require_snapshot

    snapshot = write_snapshot(tmp_path / "extracted_20260901T000000Z_run00001.csv")
    assert require_snapshot(snapshot, SNAPSHOT_SHA256, stage="test") == SNAPSHOT_SHA256

    snapshot.write_bytes(b"replaced after verification")
    with pytest.raises(SnapshotIntegrityError) as raised:
        require_snapshot(snapshot, SNAPSHOT_SHA256, stage="test")
    assert raised.value.code == "snapshot_archive_mismatch"

    snapshot.unlink()
    with pytest.raises(SnapshotIntegrityError) as raised:
        require_snapshot(snapshot, SNAPSHOT_SHA256, stage="test")
    assert raised.value.code == "snapshot_archive_unavailable"


def test_sync_report_satisfies_the_orchestrator_gate_it_feeds(tmp_path):
    """The two modules must agree on the report keys the gate reads.

    sync_csv.py writes the report and extractor_maintenance.py verifies it; a
    renamed key on either side would silently downgrade Part 1 to FAILED, or
    worse, pass an unverified snapshot to the retire stage.
    """
    from unittest.mock import MagicMock
    from extractor_maintenance import _part1_report_is_verified, _resolve_run_snapshot
    from sync_csv import sync_once

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=SNAPSHOT_BYTES)

    with patch("sync_csv.requests.get", return_value=response),          patch("sync_csv.run_import"):
        succeeded = sync_once(
            data_dir=data_dir,
            report_path=report_path,
            maintenance_run_id="run-contract",
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert _part1_report_is_verified(report, "run-contract") is True

    snapshot_path, snapshot_sha256 = _resolve_run_snapshot(data_dir, report)
    assert snapshot_path.read_bytes() == SNAPSHOT_BYTES
    assert snapshot_sha256 == SNAPSHOT_SHA256
    # Same run, different pod: the archive is gone and the gate must not pass.
    snapshot_path.unlink()
    with pytest.raises(SnapshotIntegrityError):
        _resolve_run_snapshot(data_dir, report)


# ---------------------------------------------------------------------------
# Manual stage chaining: Sync -> Find, one stage at a time
# ---------------------------------------------------------------------------

def test_a_find_run_cannot_be_mistaken_for_the_newest_sync():
    """stage_status records inherited stages as well as performed ones.

    A manual Find inherits "sync_csv": "SUCCESS" from the sync it was gated
    against. Searching for the newest run whose stage_status merely mentions
    sync_csv therefore found the Find run, whose safety_report carries no
    maintenance_run_id of its own — so the next stage that needed the newest
    sync was refused with "the newest Part 1 attempt did not complete import
    and verified promotion".
    """
    import extractor_maintenance as em

    assert em._REQUESTS_PERFORMING == {
        "sync_csv": ("full", "sync"),
        "find_orphans": ("full", "find"),
    }

    source = (ROOT / "extractor_maintenance.py").read_text(encoding="utf-8")
    lookup = source.split("def _latest_stage_attempt(", 1)[1].split("\ndef ", 1)[0]
    assert "requested_stage = ANY(%s)" in lookup, \
        "the query must exclude runs that only inherited the stage"
    assert lookup.count("requested_stage = ANY(%s)") == 2, \
        "both branches of the lookup need the filter"

    promoted = source.split("def _latest_promoted_snapshot(", 1)[1].split("\ndef ", 1)[0]
    assert "requested_stage IN ('full', 'sync')" in promoted, \
        "a Find run carries the gate's archive fields too"


def test_the_performing_map_is_derived_from_the_stage_table():
    """Two hand-maintained copies would drift; one is derived from the other."""
    import extractor_maintenance as em

    for stage, requests in em._REQUESTS_PERFORMING.items():
        for request in requests:
            assert stage in em._STAGES_BY_REQUEST[request]
        for request, stages in em._STAGES_BY_REQUEST.items():
            if stage in stages:
                assert request in requests, f"{request} runs {stage} but is missing"


def _audited_run(tmp_path, runner, finished, **kwargs):
    """run_pipeline with durable history stubbed; *finished* receives _finish_run's kwargs."""
    with patch("extractor_maintenance.queue_maintenance_run", return_value="run-1"), \
         patch("extractor_maintenance._baseline_expectation", return_value={}), \
         patch("extractor_maintenance._acquire_pipeline_lock", return_value=object()), \
         patch("extractor_maintenance._mark_run_started"), \
         patch("extractor_maintenance._update_run_progress"), \
         patch("extractor_maintenance._finish_run",
               side_effect=lambda *_a, **kw: finished.update(kw)), \
         patch("extractor_maintenance._release_pipeline_lock"):
        return run_pipeline(data_dir=tmp_path / "data", log_path=tmp_path / "m.log",
                            runner=runner, database_url="postgresql://test", **kwargs)


def test_auto_retire_is_on_unless_switched_off(monkeypatch):
    from extractor_maintenance import _auto_retire_enabled

    monkeypatch.delenv("EXTRACTOR_AUTO_RETIRE", raising=False)
    assert _auto_retire_enabled() is True
    monkeypatch.setenv("EXTRACTOR_AUTO_RETIRE", "0")
    assert _auto_retire_enabled() is False


def test_the_scheduled_retire_applies_the_manifest_of_the_imported_commit(tmp_path):
    runner = ScriptedRunner({"sync_csv.py": (0, ""), "find_orphans.py": (0, ""),
                             "csv_to_db.py": (0, "")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished, trigger="scheduled") is True

    retire = runner.commands[-1]
    assert Path(retire[1]).name == "csv_to_db.py"
    find_input, _ = _stage_input(runner, "find_orphans.py")
    assert retire[retire.index("--input") + 1] == find_input
    assert retire[retire.index("--retire-commit") + 1] == SOURCE_COMMIT
    assert retire[retire.index("--maintenance-run-id") + 1] == "run-1"
    assert "--apply" in retire
    assert finished["stage_status"]["retire_superseded"] == "SUCCESS"
    assert finished["safety_report"]["retire"]["retire"] == 2


@pytest.mark.parametrize(("exit_code", "stage", "warning"), [
    (3, "BLOCKED", "retire_cap_exceeded"),
    (1, "FAILED", "retire_failed"),
])
def test_a_refused_or_failed_retire_is_a_warning_not_a_failed_sync(
        tmp_path, exit_code, stage, warning):
    runner = ScriptedRunner({"sync_csv.py": (0, ""), "find_orphans.py": (0, ""),
                             "csv_to_db.py": (exit_code, "")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished) is True
    assert finished["stage_status"]["retire_superseded"] == stage
    assert warning in finished["safety_report"]["warning_codes"]
    assert finished["status"] == "warning"


def test_no_retire_without_the_commit_the_csv_was_read_at(tmp_path):
    runner = ReportingRunner({"sync_csv.py": (0, ""), "find_orphans.py": (0, "")},
                             {"source_commit": None})
    finished = {}
    assert _audited_run(tmp_path, runner, finished) is True
    assert _script_order(runner) == ["sync_csv.py", "find_orphans.py"]
    assert finished["stage_status"]["retire_superseded"] == "SKIPPED"
    assert "retire_source_commit_unknown" in finished["safety_report"]["warning_codes"]


def test_no_retire_after_a_failed_sync(tmp_path):
    runner = ScriptedRunner({"sync_csv.py": (1, "")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished) is False
    assert _script_order(runner) == ["sync_csv.py"]
    assert finished["stage_status"]["retire_superseded"] == "SKIPPED"


def test_off_switch_and_single_stage_requests_never_retire(tmp_path, monkeypatch):
    runner = ScriptedRunner({"sync_csv.py": (0, ""), "find_orphans.py": (0, "")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished, requested_stage="sync") is True
    assert "retire_superseded" not in finished["stage_status"]
    monkeypatch.setenv("EXTRACTOR_AUTO_RETIRE", "off")
    assert _audited_run(tmp_path, runner, finished) is True
    assert "csv_to_db.py" not in _script_order(runner)


# ---------------------------------------------------------------------------
# The database copy of each imported snapshot (extractor_snapshots)
# ---------------------------------------------------------------------------

def test_a_successful_sync_keeps_its_snapshot_in_the_database(tmp_path, _no_snapshot_database):
    runner = ScriptedRunner({"sync_csv.py": (0, ""), "find_orphans.py": (0, ""),
                             "csv_to_db.py": (0, "")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished) is True

    (name, sha256, extra), = _no_snapshot_database["store"]
    assert name.startswith("extracted_20260901T000000Z_")
    assert sha256 == SNAPSHOT_SHA256
    assert extra == {"source_commit": SOURCE_COMMIT, "run_id": "run-1"}
    # Pruning never takes the snapshot this run just imported.
    assert _no_snapshot_database["prune"] == [(10, (SNAPSHOT_SHA256,))]
    assert "snapshot_store_failed" not in (finished["safety_report"].get("warning_codes") or [])


def test_a_failed_snapshot_copy_is_a_warning_not_a_failed_sync(tmp_path, monkeypatch):
    import extractor_maintenance as em

    def refuse(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(em, "store_snapshot", refuse)
    runner = ScriptedRunner({"sync_csv.py": (0, ""), "find_orphans.py": (0, ""),
                             "csv_to_db.py": (0, "")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished) is True
    assert finished["stage_status"]["retire_superseded"] == "SUCCESS"
    assert "snapshot_store_failed" in finished["safety_report"]["warning_codes"]
    assert finished["status"] == "warning"


def test_a_failed_sync_stores_nothing(tmp_path, _no_snapshot_database):
    finished = {}
    assert _audited_run(tmp_path, ScriptedRunner({"sync_csv.py": (1, "")}), finished) is False
    assert _no_snapshot_database["store"] == []


def test_the_baseline_is_restored_from_the_database_before_the_sync(tmp_path, monkeypatch):
    """A redeploy emptied the directory; the database copy puts the baseline back
    where the sync looks for it, before the sync starts."""
    import extractor_maintenance as em

    baseline_file = "extracted_20260831T000000Z_prev0001.csv"
    events = []

    def restore(_url, sha256, dest):
        events.append(("restore", Path(dest).name))
        write_snapshot(dest)
        return True

    class OrderedRunner(ScriptedRunner):
        def __call__(self, command, **kwargs):
            events.append(("run", Path(command[1]).name))
            if Path(command[1]).name == "sync_csv.py":
                assert (tmp_path / "data" / baseline_file).read_bytes() == SNAPSHOT_BYTES
            return super().__call__(command, **kwargs)

    monkeypatch.setattr(em, "restore_snapshot", restore)
    with patch("extractor_maintenance._acquire_pipeline_lock", return_value=object()), \
         patch("extractor_maintenance._mark_run_started"), \
         patch("extractor_maintenance._update_run_progress"), \
         patch("extractor_maintenance._finish_run"), \
         patch("extractor_maintenance._release_pipeline_lock"), \
         patch("extractor_maintenance._baseline_expectation", return_value={
             "baseline_file": baseline_file,
             "baseline_sha256": SNAPSHOT_SHA256,
             "require_baseline": True,
         }):
        run_pipeline(data_dir=tmp_path / "data", log_path=tmp_path / "m.log",
                     requested_stage="sync", runner=OrderedRunner({"sync_csv.py": (0, "")}),
                     database_url="postgresql://test", run_id="sync-current")

    assert events[:2] == [("restore", baseline_file), ("run", "sync_csv.py")]
    assert "restored the last import" in (tmp_path / "m.log").read_text(encoding="utf-8")


def test_a_baseline_already_on_disk_is_not_fetched_again(tmp_path, _no_snapshot_database):
    baseline_file = "extracted_20260831T000000Z_prev0001.csv"
    write_snapshot(tmp_path / "data" / baseline_file)
    with patch("extractor_maintenance._acquire_pipeline_lock", return_value=object()), \
         patch("extractor_maintenance._mark_run_started"), \
         patch("extractor_maintenance._update_run_progress"), \
         patch("extractor_maintenance._finish_run"), \
         patch("extractor_maintenance._release_pipeline_lock"), \
         patch("extractor_maintenance._baseline_expectation", return_value={
             "baseline_file": baseline_file, "baseline_sha256": SNAPSHOT_SHA256,
         }):
        run_pipeline(data_dir=tmp_path / "data", log_path=tmp_path / "m.log",
                     requested_stage="sync", runner=ScriptedRunner({"sync_csv.py": (0, "")}),
                     database_url="postgresql://test", run_id="sync-current")
    assert _no_snapshot_database["restore"] == []


# ---------------------------------------------------------------------------
# What the admin panel reads: stage durations and the orphan summary
# ---------------------------------------------------------------------------

class SummaryRunner(ScriptedRunner):
    def __call__(self, command, **kwargs):
        if Path(command[1]).name == "find_orphans.py":
            summary = Path(command[command.index("--summary-json") + 1])
            summary.write_text(json.dumps({"orphan_count": 7, "unvalidated_count": 2}),
                               encoding="utf-8")
        return super().__call__(command, **kwargs)


def test_the_run_records_stage_durations_and_the_orphan_summary(tmp_path):
    runner = SummaryRunner({"sync_csv.py": (0, ""), "find_orphans.py": (0, ""),
                            "csv_to_db.py": (0, "")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished) is True
    report = finished["safety_report"]
    assert set(report["stage_seconds"]) == {"sync_csv", "find_orphans", "retire_superseded"}
    assert all(isinstance(value, float) for value in report["stage_seconds"].values())
    assert report["orphan_report"] == {"orphan_count": 7, "unvalidated_count": 2}
    # The scratch summary file does not outlive the run.
    summary_path = Path(runner.commands[1][runner.commands[1].index("--summary-json") + 1])
    assert not summary_path.exists()


def test_a_failed_orphan_report_skips_the_retire_stage(tmp_path):
    """The retire gate needs the report's success; without it nothing is retired."""
    runner = ScriptedRunner({"sync_csv.py": (0, ""), "find_orphans.py": (2, "database down\n")})
    finished = {}
    assert _audited_run(tmp_path, runner, finished) is False
    assert _script_order(runner) == ["sync_csv.py", "find_orphans.py"]
    assert finished["stage_status"]["find_orphans"] == "FAILED"
    assert finished["stage_status"]["retire_superseded"] == "SKIPPED"
    assert finished["status"] == "failed"


def test_a_failed_database_restore_names_its_cause(tmp_path, monkeypatch):
    import extractor_maintenance as em

    def broken(*_args):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(em, "restore_snapshot", broken)
    gate = _verified_find_gate("extracted_20260901T000000Z_poda0001.csv")
    with pytest.raises(SnapshotIntegrityError) as raised:
        em._resolve_run_snapshot(tmp_path, gate, "postgresql://test")
    assert raised.value.code == "snapshot_archive_unavailable"
    assert "restoring it from the database failed: connection refused" in str(raised.value)
    # Without a database, nothing claims the database was asked.
    with pytest.raises(SnapshotIntegrityError) as raised:
        em._resolve_run_snapshot(tmp_path, gate)
    assert str(raised.value).endswith("is not present on this host")
