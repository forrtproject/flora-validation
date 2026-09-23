"""Regressions for export identity, overlapping runs, and artifact failures."""
import csv
import hashlib
import io
import json
from pathlib import Path

import pandas as pd
from psycopg2.extras import RealDictCursor
import pytest

import final_export
import flora_registry
import flora_service
import flora_store
import prepare_flora
import source_sync_runner as runner
from tests.test_preparation_database import local_database, add_source  # noqa: F401
from tests.test_standalone_preparation_identity import add_unregistered_source
from tests.test_preparation_runner import Connection, frame, write_input


def test_browser_requires_registration_and_preserves_identity_after_correction(local_database):
    source_id = add_unregistered_source(local_database)
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            with pytest.raises(ValueError, match="Run the pipeline"):
                flora_service.export_csv(cur, {})
            flora_registry.refresh(cur, verbose=False)
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            first = next(csv.DictReader(io.StringIO(flora_service.export_csv(cur, {}).lstrip("\ufeff"))))
            cur.execute("UPDATE source_records SET doi_o='10.9999/corrected' WHERE record_id=%s", (source_id,))
            flora_registry.refresh(cur, verbose=False)
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            second = next(csv.DictReader(io.StringIO(flora_service.export_csv(cur, {}).lstrip("\ufeff"))))
    assert first["id"] == second["id"] == "REPL-700001"
    assert first["id_md5"] == second["id_md5"] == hashlib.md5(b"REPL-700001").hexdigest()


@pytest.mark.parametrize("initialized", [False, True])
def test_overlapping_preparation_cannot_revert_newer_snapshot(local_database, tmp_path, monkeypatch, initialized):
    add_unregistered_source(local_database)
    if initialized:
        assert prepare_flora.prepare(tmp_path / "initial", network_checks="none")["status"] != "failed"
    validate = prepare_flora.validate_flora_network.validate
    newer_report = None

    def interleaved_validation(*args, **kwargs):
        nonlocal newer_report
        result = validate(*args, **kwargs)
        if newer_report is None:
            newer_report = {}  # the nested run must not interleave again
            with final_export.REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
                reference = list(csv.DictReader(handle))[1]
            with local_database:
                with local_database.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("UPDATE work_metadata SET title='Corrected title' WHERE doi='10.9999/standalone-original'")
                    add_source(cur, reference, "REPL-700002")
            newer_report = prepare_flora.prepare(tmp_path / "newer", network_checks="none")
        return result

    monkeypatch.setattr(prepare_flora.validate_flora_network, "validate", interleaved_validation)
    older_report = prepare_flora.prepare(tmp_path / "older", network_checks="none")
    assert newer_report["status"] != "failed"
    assert older_report["status"] == "failed"
    assert "changed during this run" in older_report["errors"][0]
    assert not (tmp_path / "older" / "flora.csv").exists()
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            assert flora_store.export_csv(cur).encode("utf-8") == (tmp_path / "newer" / "flora.csv").read_bytes()
            cur.execute("SELECT title_o FROM flora_data WHERE id='REPL-700001'")
            assert cur.fetchone()["title_o"] == "Corrected title"
            cur.execute("SELECT COUNT(*) AS total FROM flora_data WHERE retired_at IS NULL")
            assert cur.fetchone()["total"] == 2


@pytest.mark.parametrize("failure", ["storage", "flora.csv", "flora_dataset_summary.md"])
def test_failed_publication_restores_csv_manifest_and_history(tmp_path, monkeypatch, failure):
    source = write_input(tmp_path / "input.csv")
    output = tmp_path / "release"
    prepare_flora.prepare(output, source, network_checks="none")
    names = ["flora.csv", "flora_release_manifest.json", "flora_release_notes.md",
             "flora_history.csv", "flora_dataset_summary.md"]
    previous = {name: (output / name).read_bytes() for name in names}
    second = frame(id="FLO-000002", id_md5=hashlib.md5(b"FLO-000002").hexdigest())
    write_input(source, pd.concat([frame(title_o="Corrected"), second]))
    monkeypatch.setattr(prepare_flora, "current_dataset_revision", lambda: None)

    def store(*args, **kwargs):
        if failure == "storage":
            raise RuntimeError("simulated database failure")
        return {"rows": 2}

    monkeypatch.setattr(prepare_flora, "store_dataset", store)
    replace = Path.replace

    def locked_replace(path, target):
        if Path(target) == output / failure and path.parent.name != "backups":
            raise PermissionError("simulated locked file")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", locked_replace)
    report = prepare_flora.prepare(output, source, network_checks="none", store_data=True)
    assert report["status"] == "failed"
    assert {name: (output / name).read_bytes() for name in names} == previous
    manifest = json.loads((output / "flora_release_manifest.json").read_text())
    assert manifest["sha256"] == hashlib.sha256(previous["flora.csv"]).hexdigest()
    if failure != "storage":
        assert report["storage"]["status"] == "committed"
        assert (output / report["recovery_csv"]).read_bytes() == source.read_bytes()


def test_locked_local_copy_keeps_successful_job_download(tmp_path, monkeypatch):
    source = write_input(tmp_path / "input.csv")
    conn = Connection()
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def run_stage(conn, job_id, name, command):
        if name == runner.FINAL_STAGE:
            prepare_flora.prepare(Path(command[-1]), source, network_checks="none")
        return True

    monkeypatch.setattr(runner, "_run_stage", run_stage)
    replace = Path.replace

    def locked_replace(path, target):
        if Path(target) == tmp_path / "output" / "flora.csv":
            assert any("SET artifact_csv=" in statement for statement, _ in conn.executed)
            raise PermissionError("simulated locked local copy")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", locked_replace)
    runner._execute(conn, "review-job")
    saved = [params for statement, params in conn.executed if "SET artifact_csv=" in statement]
    csv_text, report, _, _ = saved[-1]
    assert csv_text.encode("utf-8") == source.read_bytes()
    assert report.adapted["status"] == "needs_attention"
    assert any("Could not copy local artifact flora.csv" in warning for warning in report.adapted["warnings"])
    assert any(params == ("success", "review-job") for _, params in conn.executed)
    assert not list((tmp_path / "output").glob("flora-sync-*"))
