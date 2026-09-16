"""Output ownership and durable recovery of failed publications."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, current_thread

import pandas as pd
from psycopg2.extras import RealDictCursor
import pytest

import filter_flora
import flora_store
from output_lock import output_directory_lock
import prepare_flora
import source_sync_runner as runner
from tests.test_preparation_database import local_database  # noqa: F401
from tests.test_standalone_preparation_identity import add_unregistered_source
from tests.test_preparation_runner import frame, write_input


def test_same_folder_preparations_keep_candidates_and_reports_together(tmp_path, monkeypatch):
    first = write_input(tmp_path / "first.csv")
    second = write_input(tmp_path / "second.csv", pd.concat([
        frame(title_o="New title"),
        frame(id="FLO-000002", id_md5=hashlib.md5(b"FLO-000002").hexdigest()),
    ]))
    output = tmp_path / "shared"
    first_validated, release_first, second_started, second_read = (Event() for _ in range(4))
    validate = prepare_flora.validate_flora_network.validate
    read = prepare_flora.read_export
    publish = prepare_flora.publish_release
    publications = []

    def paused_validate(*args, **kwargs):
        result = validate(*args, **kwargs)
        if current_thread().name.startswith("first"):
            first_validated.set()
            assert release_first.wait(10)
        return result

    def track_read(path):
        if current_thread().name.startswith("second"):
            second_read.set()
        return read(path)

    def capture_publication(candidate, staging, destination):
        publish(candidate, staging, destination)
        payload = (destination / "flora.csv").read_bytes()
        manifest = json.loads((destination / "flora_release_manifest.json").read_text())
        assert manifest["sha256"] == hashlib.sha256(payload).hexdigest()
        assert manifest["rows"] == len(pd.read_csv(destination / "flora.csv"))
        publications.append(payload)

    def run_second():
        second_started.set()
        return prepare_flora.prepare(output, second, network_checks="none")

    monkeypatch.setattr(prepare_flora.validate_flora_network, "validate", paused_validate)
    monkeypatch.setattr(prepare_flora, "read_export", track_read)
    monkeypatch.setattr(prepare_flora, "publish_release", capture_publication)
    with ThreadPoolExecutor(1, thread_name_prefix="first") as first_pool, ThreadPoolExecutor(1, thread_name_prefix="second") as second_pool:
        first_run = first_pool.submit(prepare_flora.prepare, output, first, "none")
        try:
            assert first_validated.wait(10)
            second_run = second_pool.submit(run_second)
            assert second_started.wait(10)
            assert not second_read.wait(0.2), "Second writer entered an owned output directory"
        finally:
            release_first.set()
        assert first_run.result(timeout=10)["rows"] == 1
        assert second_run.result(timeout=10)["rows"] == 2
    assert publications == [first.read_bytes(), second.read_bytes()]
    report = json.loads((output / prepare_flora.REPORT_JSON).read_text())
    assert report["rows"] == 2 and report["status"] != "failed"


def test_output_lock_is_shared_between_processes_and_released_on_exit(tmp_path):
    script = """
import sys
from output_lock import output_directory_lock
print('started', flush=True)
with output_directory_lock(sys.argv[1]):
    print('acquired', flush=True)
"""
    with output_directory_lock(tmp_path):
        child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert child.stdout.readline().strip() == "started"
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.2)
        except BaseException:
            child.kill()
            child.communicate()
            raise
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, stderr
    assert stdout.strip() == "acquired"


@pytest.mark.parametrize("fallback_candidate", [False, True])
def test_failed_website_publication_retains_exact_committed_recovery(local_database, tmp_path, monkeypatch, fallback_candidate):
    add_unregistered_source(local_database)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            job_id = runner.queue_run(cur, "recovery-test")
    replace, write_bytes = Path.replace, Path.write_bytes
    workspace = []

    def locked_replace(path, target):
        if path.name == ".flora.candidate.csv":
            raise PermissionError("simulated locked publication")
        return replace(path, target)

    def recovery_write(path, data):
        if fallback_candidate and path.name == "flora_committed_recovery.csv":
            raise PermissionError("simulated recovery copy failure")
        return write_bytes(path, data)

    def stage(conn, job, name, command):
        if name == runner.FINAL_STAGE:
            workspace.append(Path(command[-1]))
            result = prepare_flora.prepare(workspace[-1], network_checks="none")
            assert result["storage"]["status"] == "committed"
            return False
        return True

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(Path, "write_bytes", recovery_write)
    monkeypatch.setattr(runner, "_run_stage", stage)
    runner._execute(local_database, job_id)
    assert not workspace[0].exists()
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            recovery = runner.job_artifact(cur, job_id, "recovery.csv")
            report = runner.job_artifact(cur, job_id, "report.json")
            assert recovery == flora_store.export_csv(cur)
            assert hashlib.sha256(recovery.encode("utf-8")).hexdigest() == report["release"]["sha256"]
            assert report["status"] == "failed"
            assert report["recovery_artifact"] == "recovery.csv"
            assert runner.job_artifact(cur, job_id, "flora.csv") is None
            detail = runner.job_detail(cur, job_id)
            assert detail["status"] == "failed" and detail["has_recovery_csv"] and not detail["has_csv"]
            assert runner.recent_jobs(cur)[0]["has_recovery_csv"]
            assert "Download recovery CSV" in runner.job_artifact(cur, job_id, "report.md")


def test_workspace_is_preserved_when_artifact_storage_raises(tmp_path):
    with pytest.raises(RuntimeError, match="Pipeline artifacts retained at"):
        with runner._run_workspace(tmp_path) as directory:
            recovery = directory / "flora_committed_recovery.csv"
            recovery.write_bytes(b"committed bytes")
            raise RuntimeError("database unavailable during artifact storage")
    assert recovery.read_bytes() == b"committed bytes"


@pytest.mark.parametrize("failure", ["network", "invalid_input"])
def test_failed_filter_removes_previous_derivative(tmp_path, monkeypatch, failure):
    source = write_input(tmp_path / "input.csv")
    output = tmp_path / "filtered"
    filter_flora.run_filter(source, output, use_network=False)
    assert (output / "flora_filtered.csv").exists()
    if failure == "network":
        def unavailable():
            raise TimeoutError("Retraction Watch unavailable")
        monkeypatch.setattr(filter_flora, "fetch_retractions", unavailable)
        report = filter_flora.run_filter(source, output)
        assert report["status"] == "failed"
        assert json.loads((output / "flora_filter_report.json").read_text())["output_rows"] is None
    else:
        source.write_text("bad,input\n1,2\n")
        with pytest.raises(ValueError, match="requires doi_o and doi_r"):
            filter_flora.run_filter(source, output)
    assert not (output / "flora_filtered.csv").exists()


def test_filter_can_read_its_own_derivative_before_replacing_it(tmp_path):
    source = write_input(tmp_path / "flora_filtered.csv")
    expected = pd.read_csv(source).to_dict("records")
    filter_flora.run_filter(source, tmp_path, use_network=False)
    assert pd.read_csv(source).to_dict("records") == expected
