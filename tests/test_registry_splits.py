"""A duplicate group that splits must not give two rows one published identity."""
from unittest.mock import Mock

import pandas as pd
import pytest

import final_export
import flora_registry


def run_refresh(monkeypatch, source_order, existing):
    frame = pd.DataFrame({"source_record_id": source_order})
    frame.attrs["merged_record_ids"] = {}
    frame.attrs["dedup_key"] = {}
    monkeypatch.setattr(flora_registry.transform_sources, "build", lambda *args, **kwargs: frame)
    monkeypatch.setattr(flora_registry, "_mint_ids", lambda cur, ids, taken: {sid: "NEW-" + sid for sid in ids})
    monkeypatch.setattr(flora_registry, "backfill_source_history", Mock())
    monkeypatch.setattr(flora_registry, "record_history", Mock())
    monkeypatch.setattr(final_export, "register_order", Mock())
    batches = []
    monkeypatch.setattr(flora_registry.psycopg2.extras, "execute_batch",
                        lambda cur, sql, rows: batches.append((sql, rows)))
    cursor = Mock()
    cursor.fetchall.return_value = existing
    result = flora_registry.refresh(cursor, verbose=False)
    updates = [row for sql, rows in batches if "SET primary_source_record_id" in sql for row in rows]
    inserts = [row for sql, rows in batches if "INSERT INTO flora_records" in sql for row in rows]
    retirements = [row for sql, rows in batches if "SET retired_at = NOW()" in sql for row in rows]
    return result, updates, inserts, retirements


def record(primary="source-a", merged=None):
    return {"flora_record_id": "registry-a", "flora_id": "REPL-000001",
            "primary_source_record_id": primary,
            "merged_source_record_ids": merged if merged is not None else ["source-b"],
            "retired_at": None, "export_id": "FLORA-000001", "export_position": 1}


@pytest.mark.parametrize("order", [["source-a", "source-b"], ["source-b", "source-a"]])
def test_split_preserves_primary_identity_regardless_input_order(monkeypatch, order):
    original = record()
    result, updates, inserts, retirements = run_refresh(monkeypatch, order, [original])
    assert result["assigned"] == 1
    assert result["rematched"] == 0
    assert updates == [("source-a", [], None, "registry-a")]
    assert inserts == [("NEW-source-b", "source-b", [], None)]
    assert retirements == []
    assert original["export_id"] == "FLORA-000001"
    assert original["export_position"] == 1


def test_split_without_old_primary_claims_its_identity_only_once(monkeypatch):
    result, updates, inserts, _ = run_refresh(
        monkeypatch, ["source-b", "source-c"], [record(merged=["source-b", "source-c"])])
    assert result["assigned"] == 1
    assert result["rematched"] == 1
    assert updates == [("source-b", [], None, "registry-a")]
    assert inserts == [("NEW-source-c", "source-c", [], None)]


def test_changed_survivor_still_keeps_the_registered_identity(monkeypatch):
    result, updates, inserts, retirements = run_refresh(monkeypatch, ["source-b"], [record()])
    assert result["rematched"] == 1
    assert result["assigned"] == 0
    assert updates == [("source-b", [], None, "registry-a")]
    assert inserts == []
    assert retirements == []


def test_distinct_primary_is_reserved_even_when_old_group_also_claims_it(monkeypatch):
    old = record(merged=["source-b", "source-c"])
    current = dict(record(primary="source-b", merged=[]),
                   flora_record_id="registry-b", flora_id="REPL-000002",
                   export_id="FLORA-000002", export_position=2)
    result, updates, inserts, _ = run_refresh(monkeypatch, ["source-c", "source-b"], [old, current])
    assert result["assigned"] == 0
    assert result["rematched"] == 1
    assert updates == [("source-c", [], None, "registry-a"),
                       ("source-b", [], None, "registry-b")]
    assert inserts == []


def test_returning_former_primary_recovers_identity_from_historical_provenance(monkeypatch):
    old = dict(record(primary="source-b", merged=[]),
               historical_source_record_ids=["source-a"])
    result, updates, inserts, retirements = run_refresh(monkeypatch, ["source-a"], [old])
    assert result["assigned"] == 0
    assert result["rematched"] == 1
    assert updates == [("source-a", [], None, "registry-a")]
    assert inserts == []
    assert retirements == []


def test_historical_match_checks_other_claimants_when_first_is_reserved(monkeypatch):
    first = dict(record(primary="source-b", merged=[]),
                 historical_source_record_ids=["source-a"])
    second = dict(record(primary="source-c", merged=[]),
                  historical_source_record_ids=["source-a"],
                  flora_record_id="registry-b", flora_id="REPL-000002")
    result, updates, inserts, _ = run_refresh(
        monkeypatch, ["source-a", "source-b"], [first, second])
    assert result["assigned"] == 0
    assert result["rematched"] == 1
    assert updates == [("source-a", [], None, "registry-b"),
                       ("source-b", [], None, "registry-a")]
    assert inserts == []


def test_historical_split_cannot_assign_the_same_identity_twice(monkeypatch):
    old = dict(record(primary="source-b", merged=[]),
               historical_source_record_ids=["source-a", "source-c"])
    result, updates, inserts, _ = run_refresh(
        monkeypatch, ["source-a", "source-c"], [old])
    assert result["assigned"] == 1
    assert result["rematched"] == 1
    assert updates == [("source-a", [], None, "registry-a")]
    assert inserts == [("NEW-source-c", "source-c", [], None)]
