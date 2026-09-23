import csv
import hashlib
import io

import pandas as pd
import pytest

import final_export as export
import flora_service
import transform_sources


def reference_rows():
    with export.REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def test_reference_replay_preserves_every_cell_order_and_hash(tmp_path):
    path = tmp_path / "flora.csv"
    report = export.replay_reference(path)
    columns, original = reference_rows()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        actual = list(reader)
        assert reader.fieldnames == ["id", "id_md5"] + columns
    assert len(actual) == len(original) == 2914
    assert [{column: row[column] for column in columns} for row in actual] == original
    assert len({row["id"] for row in actual}) == len(actual)
    for position, row in enumerate(actual, 1):
        assert row["id"] == f"FLORA-{position:06d}"
        assert row["id_md5"] == hashlib.md5(row["id"].encode()).hexdigest()
    assert report["live_sources_fetched"] is False


def test_reordered_inputs_follow_reference_with_new_records_last():
    _, rows = reference_rows()
    new = dict(rows[0], doi_o="10.9999/new-original", doi_r="10.9999/new-replication",
               flora_id="REPL-999999")
    frame = pd.DataFrame([new, rows[4], rows[1], rows[0]])
    result = export.with_identity(frame)
    assert result["id"].tolist() == ["FLORA-000001", "FLORA-000002", "FLORA-000005", "REPL-999999"]


def test_pinned_identity_survives_doi_correction_and_source_reordering():
    _, rows = reference_rows()
    corrected = dict(rows[0], doi_o="10.9999/corrected", export_id="FLORA-000001", export_position=1)
    new_a = dict(rows[1], doi_o="10.9999/new-a", export_id="REPL-900001", export_position=2915)
    new_b = dict(rows[2], doi_o="10.9999/new-b", export_id="REPL-000001", export_position=2916)
    out = export.with_identity(pd.DataFrame([new_b, new_a, corrected]))
    assert out["id"].tolist() == ["FLORA-000001", "REPL-900001", "REPL-000001"]


def test_distinct_report_urls_are_never_merged_by_doi_pair():
    _, rows = reference_rows()
    grouped = {}
    for row in rows:
        row_key = export.key(row)
        grouped.setdefault(row_key[:3], []).append(row)
    group = next(group for group in grouped.values() if len(group) > 1)
    assert len({export.reference_match(row) for row in group}) == len(group)
    changed = dict(group[0], url_r="https://osf.io/new-distinct-report")
    assert export.reference_match(changed) is None


def test_duplicate_publication_ids_block_export():
    row = {"doi_o": "10.9999/o", "doi_r": "10.9999/r", "type": "replication", "flora_id": "same"}
    with pytest.raises(ValueError, match="Duplicate publication IDs"):
        export.with_identity(pd.DataFrame([row, row]))


def test_csv_preserves_unicode_quotes_and_embedded_newlines():
    frame = pd.DataFrame({"quote": ['A "quote"\nwith café'], "missing": [None]})
    output = export.csv_text(frame)
    assert output.startswith("\ufeff")
    parsed = list(csv.DictReader(io.StringIO(output.lstrip("\ufeff"))))
    assert parsed == [{"quote": 'A "quote"\nwith café', "missing": "NA"}]


def test_browser_download_matches_pipeline_and_drops_blank_titles(monkeypatch):
    frame = pd.DataFrame([
        {"doi_o": "10.9999/o1", "doi_r": "10.9999/r1", "type": "replication", "title_o": "Original", "title_r": "Repeat", "flora_id": "ONE", "export_id": "ONE", "export_position": 2915},
        {"doi_o": "10.9999/o2", "doi_r": "10.9999/r2", "type": "replication", "title_o": "  ", "title_r": "Repeat", "flora_id": "TWO"},
    ])
    monkeypatch.setattr(flora_service, "dataset", lambda cur: frame)
    result = flora_service.export_csv(None, {})
    expected = export.csv_text(transform_sources.to_output_shape(frame.iloc[:1]))
    assert result == expected


def test_register_reserves_retired_positions_and_pins_only_new_ids(monkeypatch):
    _, rows = reference_rows()
    records = [
        {"sid": "retired", "flora_id": "OLD", "export_id": "OLD", "export_position": 4000},
        {"sid": "baseline", "flora_id": "REPL-3", "export_id": None, "export_position": None},
        {"sid": "new", "flora_id": "REPL-1", "export_id": None, "export_position": None},
    ]
    class Cursor:
        def __init__(self): self.statements = []
        def execute(self, sql): self.statements.append(sql)
        def fetchall(self): return [] if "FROM flora_data" in self.statements[-1] else records
    cur = Cursor()
    updates = []
    monkeypatch.setattr("psycopg2.extras.execute_batch", lambda cur, sql, values: updates.extend(values))
    frame = pd.DataFrame([dict(rows[0], source_record_id="baseline"),
                          dict(rows[1], source_record_id="new", doi_o="10.9999/new")])
    export.register_order(cur, frame)
    assert updates == [("FLORA-000001", 1, "REPL-3"), ("REPL-1", 4001, "REPL-1")]
    assert cur.statements[0].startswith("LOCK TABLE")


def test_new_legacy_id_cannot_take_a_reference_identity(monkeypatch):
    _, rows = reference_rows()
    class Cursor:
        def execute(self, sql): self.statement = sql
        def fetchall(self):
            if "FROM flora_data" in self.statement:
                return []
            return [{"sid": "new", "flora_id": "FLORA-000001", "export_id": None, "export_position": None},
                    {"sid": "reference", "flora_id": "REPL-000001", "export_id": None, "export_position": None}]
    updates = []
    monkeypatch.setattr("psycopg2.extras.execute_batch", lambda cur, sql, values: updates.extend(values))
    frame = pd.DataFrame([dict(rows[0], source_record_id="new", doi_o="10.9999/new"),
                          dict(rows[0], source_record_id="reference")])
    export.register_order(Cursor(), frame)
    assert updates == [("NEW-new", 2915, "FLORA-000001"), ("FLORA-000001", 1, "REPL-000001")]
