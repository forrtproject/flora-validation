"""The stored product changes only after a valid complete candidate exists."""
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

import prepare_flora
import transform_sources


@pytest.fixture(autouse=True)
def mock_revision(monkeypatch):
    monkeypatch.setattr(prepare_flora, "current_dataset_revision", lambda: None)


def write_candidate(path, valid=True):
    pd.DataFrame([{
        "id": "FLORA-000001", "id_md5": hashlib.md5(b"FLORA-000001").hexdigest() if valid else "bad",
        "doi_o": "10.1234/original", "doi_r": "10.1234/replication",
        "title_o": "Original", "title_r": "Replication", "type": "replication",
        "source": "replications", "outcome": "successful",
    }]).to_csv(path, index=False)


def test_database_preparation_persists_checked_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(transform_sources, "run", lambda path, **kw: write_candidate(path))
    saved = []
    def store(path, *, expected_revision):
        assert expected_revision is None
        saved.append(path.read_bytes())
        return {"rows": 1, "inserted": 1}
    monkeypatch.setattr(prepare_flora, "store_dataset", store)
    report = prepare_flora.prepare(tmp_path, network_checks="none")
    assert report["storage"]["rows"] == 1
    assert saved == [(tmp_path / "flora.csv").read_bytes()]
    assert report["storage"]["status"] == "committed"
    assert report["stages"][-2]["name"] == "store prepared FLoRA table"
    assert report["stages"][-1]["name"] == "publish CSV artifact"


def test_invalid_identity_never_reaches_stored_table(tmp_path, monkeypatch):
    monkeypatch.setattr(transform_sources, "run", lambda path, **kw: write_candidate(path, valid=False))
    store = Mock(side_effect=AssertionError("Invalid snapshot must not be stored"))
    monkeypatch.setattr(prepare_flora, "store_dataset", store)
    assert prepare_flora.prepare(tmp_path, network_checks="none")["status"] == "failed"
    store.assert_not_called()


def test_existing_csv_does_not_write_database_without_opt_in(tmp_path, monkeypatch):
    source = tmp_path / "input.csv"
    write_candidate(source)
    store = Mock(return_value={"rows": 1})
    monkeypatch.setattr(prepare_flora, "store_dataset", store)
    prepare_flora.prepare(tmp_path / "files", source, network_checks="none")
    store.assert_not_called()
    prepare_flora.prepare(tmp_path / "stored", source, network_checks="none", store_data=True)
    store.assert_called_once()


def test_failed_table_write_does_not_replace_previous_csv(tmp_path, monkeypatch):
    original = b"previous completed CSV"
    (tmp_path / "flora.csv").write_bytes(original)
    monkeypatch.setattr(transform_sources, "run", lambda path, **kw: write_candidate(path))
    monkeypatch.setattr(prepare_flora, "store_dataset", Mock(side_effect=RuntimeError("database unavailable")))
    report = prepare_flora.prepare(tmp_path, network_checks="none")
    assert report["status"] == "failed"
    assert "store prepared FLoRA table" in report["errors"][0]
    assert (tmp_path / "flora.csv").read_bytes() == original


def test_locked_csv_reports_committed_database_and_keeps_recovery_file(tmp_path, monkeypatch):
    (tmp_path / "flora.csv").write_bytes(b"previous CSV")
    monkeypatch.setattr(transform_sources, "run", lambda path, **kw: write_candidate(path))
    stored = []
    def store(path, **kwargs):
        stored.append(path.read_bytes())
        return {"rows": 1}
    monkeypatch.setattr(prepare_flora, "store_dataset", store)
    original_replace = Path.replace
    def replace(path, target):
        if path.name == ".flora.candidate.csv":
            raise PermissionError("CSV is open in another application")
        return original_replace(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    report = prepare_flora.prepare(tmp_path, network_checks="none")
    assert report["status"] == "failed"
    assert report["storage"]["status"] == "committed"
    assert report["errors"][0].startswith("publish CSV artifact:")
    assert (tmp_path / "flora.csv").read_bytes() == b"previous CSV"
    assert (tmp_path / report["recovery_csv"]).read_bytes() == stored[0]
    assert not (tmp_path / ".flora.candidate.csv").exists()
