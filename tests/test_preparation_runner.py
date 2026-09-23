"""Final artifact correctness, failure isolation, and honest validation coverage."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import prepare_flora as preparation
import release_helpers
import source_sync_runner as runner
import validate_flora_network as network
import validate_flora as structural


def frame(**changes):
    identifier = "FLO-000001"
    row = {"id": identifier, "id_md5": hashlib.md5(identifier.encode()).hexdigest(),
           "doi_o": "10.1234/original", "doi_r": "10.1234/replication",
           "title_o": "Original", "title_r": "Replication",
           "type": "replication", "source": "validated", "outcome": "successful",
           "year_o": "2020", "year_r": "2021"}
    row.update(changes)
    return pd.DataFrame([row])


def write_input(path, data=None):
    (frame() if data is None else data).to_csv(path, index=False, encoding="utf-8-sig",
                                            lineterminator="\n")
    return path


def test_existing_csv_is_validated_without_rebuild_and_preserved_byte_for_byte(tmp_path, monkeypatch):
    input_path = write_input(tmp_path / "input.csv")
    monkeypatch.setattr(preparation.psycopg2, "connect",
                        lambda *a, **kw: pytest.fail("offline preparation connected to DB"))
    report = preparation.prepare(tmp_path / "release", input_path, network_checks="none")
    assert report["status"] == "needs_attention"
    assert report["network"]["status"] == "skipped"
    assert (tmp_path / "release" / "flora.csv").read_bytes() == input_path.read_bytes()
    assert report["release"]["sha256"] == hashlib.sha256(input_path.read_bytes()).hexdigest()
    assert report["release"]["published"] is False
    assert not report["structural"]["has_issues"]
    saved = json.loads((tmp_path / "release" / preparation.REPORT_JSON).read_text())
    assert saved["rows"] == 1 and saved["warning_count"] == 1
    assert "Network validation status: skipped" in (
        tmp_path / "release" / preparation.REPORT_MARKDOWN).read_text()


def test_structural_cli_validates_input_file_without_database(tmp_path, monkeypatch):
    input_path = write_input(tmp_path / "input.csv", frame(year_r="1900"))
    report_path = tmp_path / "structural.md"
    monkeypatch.setattr(structural, "start_logging", lambda *a: None)
    monkeypatch.setattr(structural.psycopg2, "connect",
                        lambda *a, **kw: pytest.fail("file validation connected to DB"))
    monkeypatch.setattr(structural.sys, "argv", ["validate_flora.py", "--input", str(input_path),
                                               "--output", str(report_path), "--fail-on-issues"])
    assert structural.main() == 1
    assert "Replication year before original year" in report_path.read_text()


@pytest.mark.parametrize("change", [{"id": ""}, {"id_md5": "bad"}])
def test_bad_identity_cannot_produce_a_release(tmp_path, change):
    input_path = write_input(tmp_path / "input.csv", frame(**change))
    report = preparation.prepare(tmp_path / "release", input_path, network_checks="none")
    assert report["status"] == "failed"
    assert report["failure_count"] == 1
    assert not (tmp_path / "release" / "flora.csv").exists()
    assert (tmp_path / "release" / preparation.REPORT_JSON).exists()


def test_duplicate_ids_block_export():
    with pytest.raises(ValueError, match="unique"):
        preparation.validate_identity(pd.concat([frame(), frame()], ignore_index=True))


def test_identity_columns_must_be_first():
    data = frame()
    with pytest.raises(ValueError, match="first two"):
        preparation.validate_identity(data[list(reversed(data.columns))])


def test_failed_preparation_retains_previous_release_but_reports_failure(tmp_path):
    destination = tmp_path / "release"
    destination.mkdir()
    previous = destination / "flora.csv"
    previous.write_bytes(b"previous successful file")
    bad_input = write_input(tmp_path / "invalid.csv", frame(id_md5="incorrect"))
    report = preparation.prepare(destination, bad_input, network_checks="none")
    assert report["status"] == "failed"
    assert previous.read_bytes() == b"previous successful file"
    assert not (destination / ".flora.candidate.csv").exists()


def test_structural_issues_keep_exact_csv_and_report_findings(tmp_path):
    input_path = write_input(tmp_path / "input.csv", frame(year_r="1900"))
    report = preparation.prepare(tmp_path / "release", input_path, network_checks="none")
    assert report["status"] == "needs_attention"
    assert report["structural"]["has_issues"]
    assert (tmp_path / "release" / "flora.csv").read_bytes() == input_path.read_bytes()


def test_current_transform_diagnostics_are_embedded_in_report(tmp_path, monkeypatch):
    import transform_sources

    def build(output, **kwargs):
        write_input(output)
        pd.DataFrame([{"doi_o": "10.1234/missing", "reason": "missing_title_r"}]).to_csv(
            output.parent / "flora_export_log.csv", index=False)

    monkeypatch.setattr(transform_sources, "run", build)
    report = preparation.prepare(tmp_path / "release", network_checks="none", store_data=False)
    diagnostic = report["diagnostics"]["flora_export_log.csv"]
    assert diagnostic["rows"] == 1
    assert diagnostic["records"][0]["reason"] == "missing_title_r"
    assert any("title is missing" in warning for warning in report["warnings"])


def test_only_undecided_preprint_pairs_are_reported(tmp_path, monkeypatch):
    """The candidates log records every detected pair; a pair an admin already
    ruled on needs no one's attention, so it must not raise a warning."""
    import preprint_dedup
    import transform_sources

    def pair(n, action):
        return {"side": "replication", "doi_1": f"10.1/a{n}", "doi_2": f"10.1/b{n}",
                "applied_action": action, "resolution": action}

    def build(output, **kwargs):
        write_input(output)
        preprint_dedup.write_candidates(
            [pair(1, "needs_review"), pair(2, "auto_keep_1"), pair(3, "keep_both")],
            output.parent / "preprint_dedup_candidates.csv")

    monkeypatch.setattr(transform_sources, "run", build)
    report = preparation.prepare(tmp_path / "release", network_checks="none", store_data=False)
    diagnostic = report["diagnostics"]["preprint_dedup_candidates.csv"]
    assert diagnostic["rows"] == 2
    assert {r["applied_action"] for r in diagnostic["records"]} == {"needs_review", "auto_keep_1"}
    assert any(w.startswith("2 preprint duplicate pairs awaiting a decision")
               for w in report["warnings"])


def test_unchanged_old_transform_log_is_not_reported_as_current(tmp_path, monkeypatch):
    import transform_sources
    output_dir = tmp_path / "release"
    output_dir.mkdir()
    (output_dir / "flora_export_log.csv").write_text("doi_o,reason\nold,old\n")
    monkeypatch.setattr(transform_sources, "run", lambda output, **kwargs: write_input(output))
    report = preparation.prepare(output_dir, network_checks="none", store_data=False)
    assert report["diagnostics"] == {}


def test_history_replaces_same_utc_day_instead_of_adding_duplicate(tmp_path):
    preparation.record_local_history(tmp_path, frame())
    preparation.record_local_history(tmp_path, pd.concat([frame(), frame()]))
    history = pd.read_csv(tmp_path / "flora_history.csv")
    assert len(history) == 1 and history.iloc[0]["total"] == 2


def fake_network(monkeypatch, retractions=None):
    monkeypatch.setattr(network, "check_retractions", lambda data: retractions or [])
    monkeypatch.setattr(network, "fresh_targets", lambda cur, kind: set())
    monkeypatch.setattr(network, "_head_ok", lambda url: True)
    monkeypatch.setattr(network, "remember", lambda *a: None)
    monkeypatch.setattr(network.time, "sleep", lambda delay: None)


def test_limited_network_validation_is_explicitly_incomplete(monkeypatch):
    fake_network(monkeypatch)
    report = network.validate(None, frame(), checks="all", limit=1)
    assert report["status"] == "incomplete"
    assert report["coverage"]["dois"] == {"targets": 2, "cached": 0, "checked": 1, "deferred": 1}
    assert report["issue_count"] == 0
    assert "1 deferred" in network.render_validation(report)


def test_unavailable_retraction_source_cannot_be_hidden_by_suppression(monkeypatch):
    issue = "SKIPPED: could not download the Retraction Watch database (timeout)"
    fake_network(monkeypatch, [issue])
    report = network.validate(None, frame(), suppressions={("Retracted papers", "SKIPPED")})
    assert report["status"] == "incomplete"
    assert report["sections"]["Retracted papers"] == [issue]
    assert report["coverage"]["retractions"]["status"] == "skipped"


def test_network_complete_clean_run_passes(monkeypatch):
    fake_network(monkeypatch)
    report = network.validate(None, frame())
    assert report["status"] == "passed"
    assert report["coverage"]["dois"]["checked"] == 2


class Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))


class Connection:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return Cursor(self)

    def commit(self):
        pass


def saved_artifacts(conn):
    records = [params for sql, params in conn.executed if "SET artifact_csv=" in sql]
    assert len(records) == 1
    csv_text, json_wrapper, markdown, job_id = records[0]
    return csv_text, json_wrapper.adapted, markdown, job_id


def test_failed_upstream_run_has_failure_report_and_no_csv(monkeypatch):
    stages = []

    def run_stage(conn, job_id, name, command):
        stages.append(name)
        return name != "entry sheets"

    monkeypatch.setattr(runner, "_run_stage", run_stage)
    conn = Connection()
    runner._execute(conn, "job-1")
    csv_text, report, markdown, _ = saved_artifacts(conn)
    assert stages == [name for name, _ in runner.STAGES]
    assert csv_text is None
    assert report["status"] == "failed"
    assert report["stages"][-1]["status"] == "skipped"
    assert "entry sheets failed" in markdown


def test_exit_zero_without_current_artifacts_is_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "_run_stage", lambda *a: True)
    conn = Connection()
    runner._execute(conn, "job-2")
    csv_text, report, _, _ = saved_artifacts(conn)
    assert csv_text is None and report["status"] == "failed"
    assert any(params == ("failed", "job-2") for _, params in conn.executed)


def test_successful_job_retains_exact_csv_and_reports_in_shared_database(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    input_path = write_input(tmp_path / "input.csv")

    def run_stage(conn, job_id, name, command):
        if name == runner.FINAL_STAGE:
            preparation.prepare(Path(command[-1]), input_path, network_checks="none")
        return True

    monkeypatch.setattr(runner, "_run_stage", run_stage)
    conn = Connection()
    runner._execute(conn, "job-3")
    csv_text, report, markdown, _ = saved_artifacts(conn)
    assert csv_text.encode("utf-8") == input_path.read_bytes()
    assert report["status"] == "needs_attention"
    assert len(report["stages"]) == len(runner.STAGES) + 5
    assert "entry sheets: passed" in markdown
    assert (tmp_path / "output" / "flora.csv").read_bytes() == input_path.read_bytes()
    assert any(params == ("success", "job-3") for _, params in conn.executed)


@pytest.mark.parametrize("artifact", ["../../.env", "artifact_csv; DROP TABLE", "report.csv"])
def test_artifact_download_rejects_unknown_names_before_query(artifact):
    class NeverQuery:
        def execute(self, *args):
            pytest.fail("unrecognised artifact reached SQL")
    assert runner.job_artifact(NeverQuery(), "job", artifact) is None


@pytest.mark.parametrize("bump,expected", [("major", "2.0.0"), ("minor", "1.3.0"), ("patch", "1.2.4")])
def test_release_version_increments(bump, expected):
    assert release_helpers.increment_version("1.2.3", bump) == expected


def test_invalid_release_version_is_rejected():
    with pytest.raises(ValueError):
        release_helpers.increment_version("draft")
