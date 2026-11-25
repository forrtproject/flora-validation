import csv

import pytest

import flora_edits
import flora_service
from scripts.apply_field_corrections import load_batch, plan, verify

COLUMNS = ["id", "field", "action", "before", "value", "note", "evidence_url"]


def _batch(tmp_path, rows):
    path = tmp_path / "batch.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({"note": "Evidence-based note", "evidence_url": "https://example.org",
                             "before": "", "value": "", "action": "set", **row})
    return load_batch(path)


@pytest.fixture
def record(monkeypatch):
    current = {"export_id": "FLORA-1", "type": "replication", "doi_r": None,
               "url_r": "https://openalex.org/W7120759706", "alt_identifier_r": "10.1/a"}
    monkeypatch.setattr(flora_service, "get_record", lambda cur, rid: current if rid == "FLORA-1" else None)
    monkeypatch.setattr(flora_edits, "version", lambda cur, export_id: 7)
    return current


def test_the_committed_batch_is_valid():
    rows = load_batch("data/corrections/fred_data_179_fields.csv")
    assert {row["id"] for row in rows} >= {"FLORA-002628", "FLORA-002632", "VAL-000497"}


def test_one_save_per_record_with_its_notes_and_version(tmp_path, record):
    rows = _batch(tmp_path, [
        {"id": "FLORA-1", "field": "doi_r", "value": "10.11606/d.5.2024.tde-01082025-155126"},
        {"id": "FLORA-1", "field": "url_r", "before": "https://openalex.org/W7120759706"},
        {"id": "FLORA-1", "field": "alt_identifier_r", "action": "append", "value": "10.2/b"},
    ])
    [item] = plan(None, rows)
    assert item["record_id"] == "FLORA-1" and item["version"] == 7
    assert item["fields"] == {"doi_r": "10.11606/d.5.2024.tde-01082025-155126", "url_r": None,
                              "alt_identifier_r": "10.1/a, 10.2/b"}
    assert set(item["notes"]) == set(item["fields"])


def test_a_record_that_moved_is_refused(tmp_path, record):
    rows = _batch(tmp_path, [{"id": "FLORA-1", "field": "url_r", "before": "https://openalex.org/W1",
                              "value": "https://example.org/report"}])
    with pytest.raises(ValueError, match="review the record"):
        plan(None, rows)


def test_reapplying_changes_nothing(tmp_path, record):
    record.update(url_r=None, doi_r="10.1/X")
    rows = _batch(tmp_path, [
        {"id": "FLORA-1", "field": "url_r", "before": "https://openalex.org/W7120759706"},
        {"id": "FLORA-1", "field": "doi_r", "value": "10.1/x"},
        {"id": "FLORA-1", "field": "alt_identifier_r", "action": "append", "value": "10.1/A"},
    ])
    assert plan(None, rows) == []


@pytest.mark.parametrize("row,message", [
    ({"id": "FLORA-1", "field": "type", "value": "reproduction"}, "Not an editable field"),
    ({"id": "FLORA-1", "field": "doi_r", "action": "append", "value": "10.1/x"}, "append adds"),
    ({"id": "FLORA-1", "field": "doi_r", "action": "replace"}, "Unknown action"),
    ({"id": "FLORA-1", "field": "doi_r", "note": ""}, "note and its evidence"),
])
def test_malformed_batches_are_refused(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        _batch(tmp_path, [row])


def test_verify_compares_published_values(monkeypatch):
    import flora_store
    monkeypatch.setattr(flora_store, "get_record", lambda cur, rid: {"doi_r": "10.1/X", "url_r": None})
    verify(None, [{"record_id": "A", "fields": {"doi_r": "10.1/x", "url_r": None}}])
    with pytest.raises(ValueError, match="not published"):
        verify(None, [{"record_id": "A", "fields": {"url_r": "https://example.org"}}])
