"""The optional API derivative must preserve the primary CSV and row identities."""
import json

import pandas as pd

import filter_flora
import prepare_flora
from tests.test_preparation_runner import frame, write_input


def test_offline_filter_preserves_order_ids_and_logs_each_local_removal(monkeypatch):
    monkeypatch.setattr(filter_flora, "fetch_retractions", lambda: (_ for _ in ()).throw(
        AssertionError("Offline filtering used the network")))
    rows = pd.DataFrame([
        {"id": "keep-one", "doi_o": "10.1234/o1", "doi_r": "10.1234/r1"},
        {"id": "bad", "doi_o": "10.1234/o2", "doi_r": "https://osf.io/paper"},
        {"id": "boyce", "doi_o": "10.1234/o3", "doi_r": filter_flora.BOYCE_DOI.upper()},
        {"id": "keep-two", "doi_o": "10.1234/o4", "doi_r": "https://hdl.handle.net/1234/report"},
    ])
    kept, log, report = filter_flora.filter_dataset(rows, use_network=False)
    assert kept["id"].tolist() == ["keep-one", "keep-two"]
    assert log["reason"].tolist() == ["invalid_identifier", "boyce_exclusion"]
    assert report["status"] == "incomplete"
    assert report["removed_rows"] == 2
    assert [stage["status"] for stage in report["stages"]][-2:] == ["skipped", "skipped"]
    assert rows["id"].tolist() == ["keep-one", "bad", "boyce", "keep-two"]


def test_retracted_replication_and_osf_registration_are_removed_in_order(monkeypatch):
    monkeypatch.setattr(filter_flora.time, "sleep", lambda _: None)
    rows = pd.DataFrame([
        {"doi_o": "10.1234/original", "doi_r": "10.1234/retracted"},
        {"doi_o": "10.1234/original", "doi_r": "10.17605/osf.io/reg12"},
        {"doi_o": "10.1234/retracted", "doi_r": "10.31234/osf.io/paper"},
    ])
    kept, log, report = filter_flora.filter_dataset(
        rows, retractions={"10.1234/retracted": "Nature: Retraction; Reason: test"},
        lookup_registration=lambda guid: "registrations" if guid == "reg12" else "preprints")
    assert report["status"] == "passed"
    assert kept["doi_r"].tolist() == ["10.31234/osf.io/paper"]
    assert log["reason"].tolist() == ["retracted", "osf_registration"]
    # R's API filter removes retracted doi_r only; original retractions are
    # reported by network validation, not removed by this separate derivative.
    assert kept.iloc[0]["doi_o"] == "10.1234/retracted"


def test_failed_osf_lookups_retain_affected_rows_and_report_incomplete(monkeypatch):
    monkeypatch.setattr(filter_flora.time, "sleep", lambda _: None)

    def failure(guid):
        raise TimeoutError("OSF unavailable")

    kept, log, report = filter_flora.filter_dataset(
        pd.DataFrame([{"doi_o": "10.1234/o", "doi_r": "10.31234/osf.io/paper"}]),
        retractions={}, lookup_registration=failure)
    assert len(kept) == 1 and log.empty
    assert report["status"] == "incomplete"
    assert report["osf_checks"]["failed"] == 1


def test_missing_retraction_database_blocks_derivative(monkeypatch):
    def failure():
        raise TimeoutError("Crossref unavailable")

    monkeypatch.setattr(filter_flora, "fetch_retractions", failure)
    kept, _, report = filter_flora.filter_dataset(frame())
    assert kept is None
    assert report["status"] == "failed"
    assert report["output_rows"] is None


def test_preparation_can_write_api_derivative_without_changing_primary(tmp_path):
    input_path = write_input(tmp_path / "input.csv", frame(doi_r=filter_flora.BOYCE_DOI))
    output_dir = tmp_path / "release"
    report = prepare_flora.prepare(output_dir, input_path, network_checks="none", api_filter=True)
    assert report["status"] == "needs_attention"
    assert report["api_filter"]["removed_rows"] == 1
    assert report["api_filter"]["status"] == "incomplete"
    assert (output_dir / "flora.csv").read_bytes() == input_path.read_bytes()
    assert pd.read_csv(output_dir / "flora_filtered.csv").empty
    saved = json.loads((output_dir / "flora_filter_report.json").read_text())
    assert saved["stages"][-1]["status"] == "skipped"
