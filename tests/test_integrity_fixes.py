"""Regression coverage for transaction, migration, and source-data integrity fixes."""
from pathlib import Path

import pytest

from backfill_oa_work_ids import _sync_validated_ids
from db_migrate import step_migrate_pairs
import flora_edits
from sync_sources import _build_row
from transform_sources import derive_outcome
from update_outcomes import _update_value


ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")


def _source_cfg(record_type="reproduction"):
    return {
        "promoted": ["outcome_computation", "outcome_robustness"],
        "column_map": {},
        "id_column": "id",
        "type_label": record_type,
    }


def test_source_sync_canonicalises_reproduction_axes():
    row = _build_row(
        {
            "id": "e8f69cc5-0788-4d9a-8457-90e4555e1159",
            "outcome_computation": "computationally successful",
            "outcome_robustness": "robustness not checked",
        },
        _source_cfg(),
        "reproductions",
    )
    assert row["outcome_computation"] == "computationally reproducible"
    assert row["outcome_robustness"] == "not checked"


def test_source_sync_rejects_unknown_reproduction_axis():
    with pytest.raises(ValueError, match="invalid outcome_computation"):
        _build_row(
            {
                "id": "e8f69cc5-0788-4d9a-8457-90e4555e1159",
                "outcome_computation": "probably reproducible",
                "outcome_robustness": "robust",
            },
            _source_cfg(),
            "reproductions",
        )


class _EditCursor:
    """Answers the editor's reads: an empty change log."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(" ".join(sql.split()))

    def fetchone(self):
        return {"v": 0}

    def fetchall(self):
        return []


def test_flora_editor_rejects_unknown_reproduction_axis_before_any_write():
    cur = _EditCursor()
    record = {"export_id": "FLORA-000001", "type": "reproduction", "outcome_robustness": "robust"}
    with pytest.raises(ValueError, match="invalid outcome_robustness"):
        flora_edits.save(cur, record, {}, {"outcome_robustness": "mostly robust"}, [], 0,
                         {"handle": "admin"})
    assert not [sql for sql in cur.calls if sql.startswith(("INSERT", "DELETE", "UPDATE"))]


def test_transform_flags_unknown_axis_instead_of_silently_publishing_it():
    problems = {"unknown_alias": [], "bad_alias": []}
    result = derive_outcome(
        {
            "display_id": "REPR-000001",
            "type": "reproduction",
            "outcome_computation": "probably reproducible",
            "outcome_robustness": "robust",
        },
        {},
        problems,
    )
    assert result == "cannot_be_determined"
    assert problems["invalid_axis"] == [
        ("REPR-000001", "outcome_computation", "probably reproducible")
    ]


def test_source_record_table_enforces_both_axis_vocabularies():
    assert "source_records_outcome_computation_check" in SCHEMA
    assert "source_records_outcome_robustness_check" in SCHEMA
    assert "'technical failure', 'not checked', 'cannot_be_determined'" in SCHEMA
    assert "'robust', 'robustness challenges', 'not checked'" in SCHEMA


def test_outcome_refresh_binds_blank_axes_as_sql_null():
    assert _update_value("outcome_computation", "") is None
    assert _update_value("outcome_robustness", None) is None
    assert _update_value("outcome_computation", "computationally successful") \
        == "computationally reproducible"


class _BackfillCursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self.rowcount = 1


def test_openalex_backfill_copies_ids_to_matching_validated_rows():
    cur = _BackfillCursor()
    assert _sync_validated_ids(cur) == 2
    assert len(cur.calls) == 2
    for sql, _ in cur.calls:
        assert "UPDATE validated v" in sql
        assert "FROM unvalidated u" in sql
        assert "u.record_id = v.record_id" in sql
        assert "IS NOT DISTINCT FROM" in sql


class _MalformedPairCursor:
    def __init__(self, payload):
        self.payload = payload
        self._one = None
        self._many = []

    def execute(self, sql, params=None):
        if "information_schema.columns" in sql:
            self._one = ("pair_id",)
        elif "SELECT pair_id FROM unvalidated" in sql:
            self._many = []
        elif "SELECT pair_id, data_json FROM pairs" in sql:
            self._many = [("broken-pair", self.payload)]

    def fetchone(self):
        value, self._one = self._one, None
        return value

    def fetchall(self):
        values, self._many = self._many, []
        return values


@pytest.mark.parametrize("payload", ["{not-json", "[]"])
def test_legacy_migration_fails_loudly_on_malformed_pair_json(payload):
    with pytest.raises(ValueError, match="broken-pair.*malformed data_json"):
        step_migrate_pairs(_MalformedPairCursor(payload))
