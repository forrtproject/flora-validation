"""publish_flora.py and the Publish FLoRA workflow: the stored dataset's repository copy.

The writes run against PostgreSQL in the opt-in database tests
(FLORA_TEST_DATABASE_URL), with the complete supplied 2,914-row snapshot.
"""
import hashlib
import json
from pathlib import Path

import pytest
from psycopg2.extras import Json, RealDictCursor

import final_export
import flora_store
import publish_flora
import validate_flora
import validate_flora_network
from prepare_flora import read_export
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-flora.yml"


def _stored(cur, tmp_path):
    source = tmp_path / "reference.csv"
    final_export.replay_reference(source)
    flora_store.materialize(cur, source)
    return source


def test_the_repository_copy_is_the_stored_dataset(local_database, tmp_path):
    output = tmp_path / "output"
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        source = _stored(cur, tmp_path)
        result = publish_flora.publish(cur, output)
        publish_flora.publish(cur, output)                 # a second run the same day
    published = (output / "flora.csv").read_bytes()
    assert published == source.read_bytes()
    manifest = json.loads((output / "flora_release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(published).hexdigest() == result["sha256"]
    assert manifest["rows"] == result["rows"] == 2914
    history = (output / "flora_history.csv").read_text(encoding="utf-8").splitlines()
    assert len(history) == 2 and history[1].split(",")[1] == "2914", "one row per day"
    assert result["report"] == "no run given"
    assert not (output / "flora_preparation_report.json").exists()
    assert sorted(p.name for p in output.iterdir() if p.name.startswith(".")) == [
        ".flora-preparation.lock"], "no candidate or staging folder left behind"


def _report(sha256, source=None):
    """A run's stored report; with `source`, carrying real validation results of it."""
    report = {"generated_at": "2026-10-19T09:00:00+00:00", "status": "success",
              "mode": "database_pipeline", "rows": 2914,
              "release": {"filename": "flora.csv", "sha256": sha256},
              "stages": [{"name": "entry sheets", "status": "passed"}],
              "warnings": [], "errors": []}
    if source is not None:
        frame = read_export(source)
        report["structural"] = validate_flora.validate(frame, validate_flora.load_suppressions())
        report["network"] = validate_flora_network.validate(None, frame, checks="none")
    return report


def _job(cur, status, report):
    cur.execute("INSERT INTO source_sync_jobs (trigger, requested_by, status, report_json) "
                "VALUES ('admin', 'hamid', %s, %s) RETURNING job_id::text AS id",
                (status, Json(report)))
    return cur.fetchone()["id"]


def test_the_runs_report_comes_with_the_file_it_built(local_database, tmp_path):
    output = tmp_path / "output"
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        source = _stored(cur, tmp_path)
        built = _report(hashlib.sha256(source.read_bytes()).hexdigest(), source)
        job_id = _job(cur, "success", built)
        failed_id = _job(cur, "failed", built)
        assert publish_flora.publish(cur, output, job_id)["report"] == "included"
        assert publish_flora.run_report(cur, failed_id) is None, "only a successful run's report"
    stored_report = json.loads((output / "flora_preparation_report.json").read_text(encoding="utf-8"))
    assert stored_report["mode"] == "database_pipeline"
    assert "entry sheets: passed" in (output / "flora_preparation_report.md").read_text(encoding="utf-8")
    assert (output / "flora_validation.md").exists()
    assert (output / "flora_network_validation.md").read_text(encoding="utf-8") == \
        validate_flora_network.render_validation(built["network"])
    assert (output / "flora_network_validation.csv").exists()


def test_a_report_about_another_file_is_not_published_beside_this_one(local_database, tmp_path):
    """A correction saved after the run changes the stored bytes: its report, and
    the previous build's files, would describe a flora.csv that is not there."""
    output = tmp_path / "output"
    output.mkdir()
    for name in ("flora_preparation_report.md", "flora_validation.md", "dup_outcome_conflicts.csv"):
        (output / name).write_text("an earlier build\n", encoding="utf-8")
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        _stored(cur, tmp_path)
        job_id = _job(cur, "success", _report("0" * 64))
        result = publish_flora.publish(cur, output, job_id)
    assert result["report"] == "the run built a different flora.csv"
    assert sorted(result["removed"]) == ["dup_outcome_conflicts.csv", "flora_preparation_report.md",
                                         "flora_validation.md"]
    assert not any((output / name).exists() for name in publish_flora.BUILD_FILES)
    assert (output / "flora.csv").exists()


def test_nothing_is_written_before_a_dataset_is_stored(local_database, tmp_path):
    output = tmp_path / "output"
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        with pytest.raises(ValueError, match="run the pipeline first"):
            publish_flora.publish(cur, output)
    assert not output.exists()


# ── the workflow ──────────────────────────────────────────────────────────────

def test_the_workflow_commits_only_a_changed_flora_csv():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "contents: write" in workflow
    assert "python publish_flora.py --output-dir output" in workflow
    assert "bash .github/scripts/commit_and_push.sh" in workflow
    assert "output/flora.csv $(cat .github/release-files.txt)" in workflow


def test_it_runs_after_every_nightly_sync():
    """Once a day: GitHub starts it when the nightly job ends, whatever its result,
    so a failed night still publishes the stored dataset. Otherwise only by hand."""
    import yaml
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True]                    # YAML 1.1 reads the key `on` as True
    assert triggers["workflow_run"] == {"workflows": ["Sync entry sheets"], "types": ["completed"]}
    nightly = yaml.safe_load((WORKFLOW.parent / "sync-sources.yml").read_text(encoding="utf-8"))
    assert nightly["name"] == "Sync entry sheets", "the trigger names the nightly job by its name"
    assert set(triggers) == {"workflow_run", "workflow_dispatch"}
    assert triggers["workflow_dispatch"]["inputs"]["job_id"]["required"] is False


def test_the_run_id_never_reaches_the_script_through_an_expression():
    """${{ inputs.job_id }} inside `run:` would let a crafted input run shell code."""
    import yaml
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "JOB_ID: ${{ inputs.job_id }}" in workflow
    steps = yaml.safe_load(workflow)["jobs"]["publish"]["steps"]
    scripts = [step["run"] for step in steps if "run" in step]
    assert len(scripts) == 3, "every step's script is checked, `run: |` and `run: >-` alike"
    for script in scripts:
        assert "${{" not in script
