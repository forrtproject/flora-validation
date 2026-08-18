"""Regression coverage for transaction, migration, and source-data integrity fixes."""
from pathlib import Path

import pytest

from backfill_oa_work_ids import _sync_validated_ids
from db_migrate import step_migrate_pairs
from source_records_service import update_record
from sync_sources import _build_row
from transform_sources import derive_outcome
from update_outcomes import _update_value


ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
MIGRATOR = (ROOT / "db_migrate.py").read_text(encoding="utf-8")
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")
ADMIN_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def _function_body(source: str, name: str, next_name: str) -> str:
    return source.split(f"def {name}(", 1)[1].split(f"def {next_name}(", 1)[0]


def test_normal_judgement_claim_is_locked_and_conditionally_completed():
    body = _function_body(APP, "judge", "skip_pair")
    assert "AND is_validated = FALSE\n            LIMIT 1\n            FOR UPDATE" in body
    assert "WHERE queue_id = %s AND is_validated = FALSE" in body
    assert "RETURNING queue_id" in body
    assert "This record was already submitted" in body


def test_assignment_is_locked_and_conditionally_completed():
    body = _function_body(APP, "assignment_judge", "judge")
    assert "AND status = 'open' FOR UPDATE" in body
    assert "WHERE id = %s AND status = 'open' RETURNING id" in body
    assert "This assignment was already submitted" in body


def test_admin_natural_key_collision_requires_an_explicit_audited_merge():
    body = _function_body(APP, "admin_resolve", "_source_filters")
    conflict_check = body.index("conflict = _validated_identity_conflict(")
    authoritative_write = body.index("INSERT INTO validated (")
    assert conflict_check < authoritative_write
    assert "req.merge_into_record_id != conflict_id" in body
    assert "INSERT INTO validated_record_merges" in body
    assert "DELETE FROM validated WHERE record_id = %s" in body
    assert "validation_status = 'rejected'" in body

    # The final write may lose a race after the locked check, but it must never
    # mutate the winning row. DO NOTHING turns that race into another 409.
    insert = body.split("INSERT INTO validated (", 1)[1]
    assert "DO NOTHING" in insert
    assert "RETURNING record_id" in insert
    assert "DO UPDATE SET" not in insert


def test_validated_duplicate_merge_has_a_durable_audit_table():
    assert "CREATE TABLE IF NOT EXISTS validated_record_merges" in SCHEMA
    assert "duplicate_record_id   UUID        NOT NULL UNIQUE" in SCHEMA
    assert "survivor_record_id    UUID        NOT NULL" in SCHEMA
    assert "resolution_snapshot   JSONB" in SCHEMA
    assert "CHECK (duplicate_record_id <> survivor_record_id)" in SCHEMA


def test_admin_ui_requires_confirmation_before_sending_merge_target():
    assert "error.detail = err.detail" in ADMIN_JS
    assert 'e.detail?.code === "validated_duplicate_conflict"' in ADMIN_JS
    assert 'label: "Merge A into B"' in ADMIN_JS
    assert "body.merge_into_record_id = e.detail.survivor_record_id" in ADMIN_JS
    assert "B stays unchanged and remains in validated exports" in ADMIN_JS
    assert "Merged duplicate" in ADMIN_JS
    assert '"duplicate_merge": duplicate_merge' in APP


def test_api_rejects_a_correction_when_type_was_marked_correct():
    body = _function_body(APP, "_validated_outcome_request", "_normalize_doi")
    assert "corrected_type must be blank when type_check is correct" in body
    assert 'target_type if req.type_check == "incorrect" else None' in APP


def test_legacy_migration_maps_coders_by_handle_and_routes_full_pairs_to_review():
    assert "JOIN validators v ON v.handle = c.handle" in MIGRATOR
    assert 'validator_id = validator["id"]' in MIGRATOR
    assert "validator_name IS NULL" in MIGRATOR
    assert "(record_id, old_coder_id)" in MIGRATOR
    assert 'summary_column = "validator_1" if validator_slot == "human_1" else "validator_2"' in MIGRATOR
    assert 'status = "need_review" if completed >= 2 else "validation_inprogress"' in MIGRATOR


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
    def __init__(self, current):
        self.current = current
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.current


def test_source_editor_rejects_unknown_reproduction_axis_before_update():
    cur = _EditCursor({"record_id": "id", "version": 1, "type": "reproduction"})
    with pytest.raises(ValueError, match="invalid outcome_robustness"):
        update_record(
            cur,
            "e8f69cc5-0788-4d9a-8457-90e4555e1159",
            {"outcome_robustness": "mostly robust"},
            1,
            "admin",
        )
    assert len(cur.calls) == 1


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
