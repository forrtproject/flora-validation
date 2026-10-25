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


@pytest.mark.parametrize("year_r,exit_code,heading", [
    ("1500", 1, "Implausible years"),                       # a Problem
    ("1900", 0, "Replication year before original year"),   # a Possible problem only
])
def test_structural_cli_validates_input_file_without_database(tmp_path, monkeypatch,
                                                              year_r, exit_code, heading):
    """--fail-on-issues fails on Problems; a Possible problem is still reported."""
    input_path = write_input(tmp_path / "input.csv", frame(year_r=year_r))
    report_path = tmp_path / "structural.md"
    monkeypatch.setattr(structural, "start_logging", lambda *a: None)
    monkeypatch.setattr(structural.psycopg2, "connect",
                        lambda *a, **kw: pytest.fail("file validation connected to DB"))
    monkeypatch.setattr(structural.sys, "argv", ["validate_flora.py", "--input", str(input_path),
                                               "--output", str(report_path), "--fail-on-issues"])
    assert structural.main() == exit_code
    assert heading in report_path.read_text(encoding="utf-8")


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


def test_the_warning_counts_each_level(tmp_path):
    input_path = write_input(tmp_path / "input.csv", frame(year_r="1500", type="extension"))
    report = preparation.prepare(tmp_path / "release", input_path, network_checks="none")
    assert "Structural validation found 2 problems and 1 possible problem." in report["warnings"]
    stage = next(s for s in report["stages"] if s["name"] == "structural validation")
    assert stage == {"name": "structural validation", "status": "needs_attention",
                     "detail": "2 problems and 1 possible problem"}


def test_release_files_say_who_commits_the_repository_copy(tmp_path):
    """They once said nothing went to GitHub; the workflows commit output/."""
    report = preparation.prepare(tmp_path / "release", write_input(tmp_path / "input.csv"),
                                 network_checks="none")
    assert report["status"] != "failed", report["errors"]
    notes = (tmp_path / "release" / "flora_release_notes.md").read_text(encoding="utf-8")
    markdown = (tmp_path / "release" / preparation.REPORT_MARKDOWN).read_text(encoding="utf-8")
    for text in (notes, markdown):
        assert f"Publication: {release_helpers.PUBLICATION}" in text
        assert "no OSF or GitHub release" not in text and "no external release" not in text


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
    assert any("left out for a missing title" in warning for warning in report["warnings"])
    # One record, worded for one.
    assert any(warning.startswith("1 record left out") for warning in report["warnings"])
    assert "flora_export_log.csv: 1 record left out" in preparation.render_report(report)


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
    assert not (output_dir / "flora_export_log.csv").exists(), \
        "an earlier build's log is not published beside this flora.csv"


def test_a_build_failing_before_validation_leaves_no_earlier_validation_report(tmp_path, monkeypatch):
    """output/ holds the last release's reports; the nightly job uploads what is
    there as the night's, so a failed build must not leave the old ones."""
    import transform_sources
    output_dir = tmp_path / "release"
    output_dir.mkdir()
    for name in preparation.VALIDATION_FILES:
        (output_dir / name).write_text("the last release's\n", encoding="utf-8")

    def broken(output, **kwargs):
        raise RuntimeError("simulated transform failure")

    monkeypatch.setattr(transform_sources, "run", broken)
    report = preparation.prepare(output_dir, network_checks="none", store_data=False)
    assert report["status"] == "failed"
    assert not any((output_dir / name).exists() for name in preparation.VALIDATION_FILES)
    assert (output_dir / preparation.REPORT_JSON).exists(), "the failure report is this run's"


def test_an_existing_csv_run_leaves_the_build_logs_alone(tmp_path):
    """Only a database build writes them, so only a database build replaces them."""
    output_dir = tmp_path / "release"
    output_dir.mkdir()
    (output_dir / "flora_export_log.csv").write_text("doi_o,reason\nold,old\n")
    preparation.prepare(output_dir, write_input(tmp_path / "input.csv"), network_checks="none")
    assert (output_dir / "flora_export_log.csv").exists()


def test_history_replaces_same_utc_day_instead_of_adding_duplicate(tmp_path):
    preparation.record_local_history(tmp_path, frame())
    preparation.record_local_history(tmp_path, pd.concat([frame(), frame()]))
    history = pd.read_csv(tmp_path / "flora_history.csv")
    assert len(history) == 1 and history.iloc[0]["total"] == 2


def fake_network(monkeypatch, retractions=None, problem=None):
    monkeypatch.setattr(network, "check_retractions", lambda data: (retractions or [], problem))
    monkeypatch.setattr(network, "fresh_targets", lambda cur, kind: set())
    monkeypatch.setattr(network, "doi_registration", lambda doi: (network.OK, "registered"))
    monkeypatch.setattr(network, "check_link", lambda url: (network.OK, "HTTP 200"))
    monkeypatch.setattr(network, "remember", lambda *a: None)
    monkeypatch.setattr(network, "fresh_lookups", lambda cur: {})
    monkeypatch.setattr(network, "lookup_original_doi", lambda query: (network.OK, None, "answered"))
    monkeypatch.setattr(network, "remember_lookup", lambda *a: None)
    monkeypatch.setattr(network.time, "sleep", lambda delay: None)


def test_limited_network_validation_is_explicitly_incomplete(monkeypatch):
    fake_network(monkeypatch)
    report = network.validate(None, frame(), checks="all", limit=1)
    assert report["status"] == "incomplete"
    # "unchecked": the targets that could not be checked, none here.
    assert report["coverage"]["dois"] == {"targets": 2, "cached": 0, "checked": 1, "deferred": 1,
                                          "unchecked": 0}
    assert report["issue_count"] == 0
    text = network.render_validation(report)
    assert "1 deferred" in text
    assert "**Retracted papers:** none" in text and "[x]" not in text and "[ ]" not in text


def test_an_empty_check_after_findings_is_not_listed_as_one_of_them():
    """After a list and a blank line, a "- " line continues that list in Markdown."""
    row = pd.Series({"flora_id": "FLO-000001", "doi_o": "10.1/o", "doi_r": "10.1/a"})
    item = {**structural.finding("doi_not_registered", [row], "doi_r", "10.1/a",
                                 "doi_r is not registered at doi.org"), "level": "problem"}
    report = {"rows": 2, "notices": [], "unchecked": {}, "suppressed": 0, "results": [
        {"code": "doi_not_registered", "level": "problem", "heading": "DOIs not registered at doi.org",
         "items": [item], "passed": False},
        {"code": "dead_link", "level": "problem", "heading": "Dead links", "items": [], "passed": True}]}
    text = network.render(report)
    assert "— `10.1/a`\n\n**Dead links:** none" in text
    assert "- Dead links" not in text


def test_an_unavailable_retraction_source_is_a_status_line_not_a_finding(monkeypatch):
    """A check that could not run found nothing: it must not be counted as a
    finding, nor be something a suppression could hide."""
    fake_network(monkeypatch, problem="HTTPError: HTTP Error 502: Bad Gateway")
    report = network.validate(None, frame(), suppressions={("Retracted papers", "SKIPPED")})
    assert report["status"] == "incomplete" and report["issue_count"] == 0
    assert report["notices"] == ["Retraction check did not run: HTTPError: HTTP Error 502: Bad Gateway"]
    assert report["coverage"]["retractions"]["status"] == "did not run"
    assert not any(r["code"] in ("retracted", "expression_of_concern") for r in report["results"])
    text = network.render_validation(report)
    assert "**Retraction check did not run: HTTPError: HTTP Error 502: Bad Gateway.**" in text
    assert "Retracted papers" not in text


@pytest.mark.parametrize("network,warning", [
    ({"status": "skipped"}, "Network validation skipped."),
    ({"status": "needs_attention", "problems": 3, "possible_problems": 1, "notices": []},
     "Network validation needs review: 3 problems and 1 possible problem."),
    ({"status": "incomplete", "problems": 0, "possible_problems": 0,
      "notices": ["Retraction check did not run: HTTPError: HTTP Error 502: Bad Gateway"]},
     "Network validation incomplete. Retraction check did not run: HTTPError: HTTP Error 502: "
     "Bad Gateway."),
])
def test_the_network_warning_counts_each_level_and_says_why_a_run_is_incomplete(network, warning):
    assert preparation.network_warning(network) == warning


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


def test_the_website_run_bounds_its_network_checks(monkeypatch, tmp_path):
    """Its final stage shares STAGE_TIMEOUT_SECONDS with the build and the storing:
    a cold cache must defer network checks, not get the stage killed."""
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    commands = []
    monkeypatch.setattr(runner, "_run_stage",
                        lambda conn, job, name, command: commands.append(command) or True)
    runner._execute(Connection(), "job-5")
    final = commands[-1]
    assert final[final.index("--network-minutes") + 1] == str(runner.NETWORK_MINUTES)
    assert final[-2] == "--output-dir", "the output directory stays last"
    assert 0 < runner.NETWORK_MINUTES * 60 <= runner.STAGE_TIMEOUT_SECONDS / 2


@pytest.mark.parametrize("argv,minutes", [([], 0), (["--network-minutes", "15"], 15)])
def test_the_cli_passes_the_network_budget_on(monkeypatch, tmp_path, argv, minutes):
    seen = []
    monkeypatch.setattr(preparation, "prepare", lambda *args: seen.append(args) or {
        "status": "success", "rows": 0, "warning_count": 0, "failure_count": 0})
    monkeypatch.setattr("pipeline_logging.start", lambda *a: None)
    monkeypatch.setattr("sys.argv", ["prepare_flora.py", "--output-dir", str(tmp_path), *argv])
    assert preparation.main() == 0
    assert seen[0][-1] == minutes


def test_the_network_budget_reaches_the_checks(monkeypatch, tmp_path):
    class Connection:
        autocommit = False

        def cursor(self, **kwargs):
            return None

        def close(self):
            pass
    seen = []

    def validate(cur, frame, checks, limit, suppressions, minutes=0):
        seen.append(minutes)
        return network.validate(None, frame, checks="none")
    monkeypatch.setattr(preparation.psycopg2, "connect", lambda *a, **kw: Connection())
    monkeypatch.setattr(network, "ensure_cache", lambda cur: None)
    monkeypatch.setattr(network, "validate", validate)
    preparation.prepare(tmp_path / "release", write_input(tmp_path / "input.csv"), network_minutes=15)
    assert seen == [15]


def test_a_negative_network_budget_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_logging.start", lambda *a: None)
    monkeypatch.setattr("sys.argv", ["prepare_flora.py", "--output-dir", str(tmp_path),
                                     "--network-minutes", "-1"])
    with pytest.raises(SystemExit):
        preparation.main()


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
