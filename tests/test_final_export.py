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
    assert len(actual) == len(original) == 2624
    assert [{column: row[column] for column in columns} for row in actual] == original
    assert len({row["id"] for row in actual}) == len(actual)
    # Each row keeps its original number; removed rows leave gaps.
    kept = [position for position in range(1, 2915) if position not in export.removed_positions()]
    for position, row in zip(kept, actual, strict=True):
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


def test_removed_reference_positions_are_listed_as_integers_only():
    lines = export.REMOVED_POSITIONS.read_text(encoding="utf-8").split()
    assert all(line.isdigit() for line in lines)
    removed = export.removed_positions()
    assert len(lines) == len(removed) == 290
    assert 1662 in removed and 1942 not in removed
    assert max(removed) <= 2914


def test_reference_positions_skip_the_removed_ones():
    exact, pairs, size = export.reference_index()
    removed = export.removed_positions()
    identities = sorted(exact.values(), key=lambda identity: identity[1])
    assert [position for _, position in identities] == [
        position for position in range(1, 2915) if position not in removed]
    assert all(identifier == f"FLORA-{position:06d}" for identifier, position in identities)
    assert not {position for _, position in identities} & removed
    assert {identity for candidates in pairs.values() for _, identity in candidates} <= set(exact.values())


def test_reference_size_is_the_original_snapshot_size():
    _, rows = reference_rows()
    exact, _, size = export.reference_index()
    assert len(rows) == len(exact) == 2624
    assert size == 2914 == len(rows) + len(export.removed_positions())


def test_record_at_original_position_1942_keeps_its_id():
    _, rows = reference_rows()
    row = rows[1661]                                   # row 1662 of the file, after removals
    assert export.doi(row["doi_o"]) == "10.1037/a0012833"
    assert export.url(row["url_r"]).endswith("ctvej")
    assert export.reference_match(row) == ("FLORA-001942", 1942)
    assert export.with_identity(pd.DataFrame([row]))["id"].tolist() == ["FLORA-001942"]


def register(monkeypatch, rows, records):
    """register_order over an in-memory registry with no stored dataset; its updates."""
    class Cursor:
        def execute(self, sql): self.statement = sql
        def fetchall(self): return [] if "FROM flora_data" in self.statement else records
    updates = []
    monkeypatch.setattr("psycopg2.extras.execute_batch", lambda cur, sql, values: updates.extend(values))
    export.register_order(Cursor(), pd.DataFrame(rows))
    return updates


def test_registering_the_record_at_position_1942_gives_flora_001942(monkeypatch):
    _, rows = reference_rows()
    updates = register(monkeypatch, [dict(rows[1661], source_record_id="fred")],
                       [{"sid": "fred", "flora_id": "FRED-000914", "export_id": None, "export_position": None}])
    assert updates == [("FLORA-001942", 1942, "FRED-000914")]


def test_no_removed_position_or_id_is_ever_assigned(monkeypatch):
    _, rows = reference_rows()
    removed = export.removed_positions()
    # Every reference row registered at once, plus newcomers, one with a legacy
    # FLORA- id of a removed record.
    frame = [dict(row, source_record_id=f"ref-{index}") for index, row in enumerate(rows)]
    records = [{"sid": f"ref-{index}", "flora_id": f"REPL-{index:06d}", "export_id": None,
                "export_position": None} for index in range(len(rows))]
    for name, flora_id in [("new-a", "REPL-900001"), ("new-b", "FLORA-001662")]:
        frame.append(dict(rows[0], source_record_id=name, doi_o=f"10.9999/{name}"))
        records.append({"sid": name, "flora_id": flora_id, "export_id": None, "export_position": None})
    updates = register(monkeypatch, frame, records)
    assert len(updates) == len(rows) + 2
    positions = [position for _, position, _ in updates]
    assert not set(positions) & removed
    assert not {export_id for export_id, _, _ in updates} & {f"FLORA-{p:06d}" for p in removed}
    assert updates[-2:] == [("REPL-900001", 2915, "REPL-900001"), ("NEW-new-b", 2916, "FLORA-001662")]
