"""Behavioural regressions from the reproduction-workflow audit."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from consensus_engine import _is_unsure
from csv_to_db import _refresh_extractor_fields
from db_migrate import step_migrate_pairs
from tests.test_dashboard_split import flora


def test_unsure_detection_includes_reproduction_axis_checks():
    assert _is_unsure({
        "additional_checks": {
            "reproduction_axis_checks": {"computation": "correct", "robustness": "unsure"}
        }
    })
    assert not _is_unsure({
        "additional_checks": {
            "reproduction_axis_checks": {"computation": "correct", "robustness": "correct"}
        }
    })


def test_outcome_request_boundary_enforces_type_and_axis_shapes():
    def _req(**overrides):
        defaults = dict(
            type_check="correct",
            corrected_type=None,
            outcome_check="correct",
            corrected_outcome=None,
            corrected_outcome_computation=None,
            corrected_outcome_robustness=None,
            additional_checks=None,
        )
        return SimpleNamespace(**{**defaults, **overrides})

    # Type marked correct must not carry corrected_type
    with pytest.raises(HTTPException) as exc:
        flora._validated_outcome_request(_req(corrected_type="reproduction"), "replication", "failed")
    assert exc.value.status_code == 400

    # Type marked incorrect requires corrected_type
    with pytest.raises(HTTPException) as exc:
        flora._validated_outcome_request(_req(type_check="incorrect", corrected_type=None), "replication", "failed")
    assert exc.value.status_code == 400

    # Replication must not carry reproduction axes
    with pytest.raises(HTTPException) as exc:
        flora._validated_outcome_request(
            _req(outcome_check="incorrect", corrected_outcome="mixed",
                 corrected_outcome_computation="computationally reproducible"),
            "replication", "failed",
        )
    assert exc.value.status_code == 400

    # Changing type to reproduction requires both axes (no fallback from replication)
    with pytest.raises(HTTPException) as exc:
        flora._validated_outcome_request(
            _req(type_check="incorrect", corrected_type="reproduction",
                 outcome_check="incorrect",
                 corrected_outcome_computation="computationally reproducible",
                 corrected_outcome_robustness=None),
            "replication", "failed",
        )
    assert exc.value.status_code == 400


class _RecordingCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


def test_same_pair_refresh_updates_all_raw_extractor_fields_but_no_finals():
    cur = _RecordingCursor()
    row = pd.Series({
        "pair_id": "pair-1",
        "doi_r": "10.1/rep",
        "study_r": "3",
        "title_r": "Corrected extractor title",
        "oa_work_id_r": "W123",
        "doi_o": "10.1/original",
        "study_o": "2",
        "title_o": "Original",
        "oa_work_id_o": "W456",
        "type": "reproduction",
        "outcome": "computational issues, robust",
        "outcome_computation": "computational issues",
        "outcome_computational_quote": "code needed repairs",
        "out_quote_computational_source": "results",
        "outcome_robustness": "robust",
        "outcome_robustness_quote": "conclusion held",
        "out_quote_robust_source": "discussion",
    })

    _refresh_extractor_fields(cur, "record-1", row)

    assert len(cur.calls) == 3
    raw_sql, raw = cur.calls[0]
    for column in (
        "doi_r", "title_r", "oa_work_id_r", "doi_o", "title_o",
        "type", "outcome", "outcome_computation",
        "outcome_computational_quote", "out_quote_computational_source",
        "outcome_robustness", "outcome_robustness_quote",
        "out_quote_robust_source",
    ):
        assert column in raw_sql or column in cur.calls[1][0]
    assert "final_" not in raw_sql
    assert raw["outcome"] == "computational issues, robust"
    assert raw["oa_work_id_r"] == "W123"
    validated_sql = cur.calls[2][0]
    assert "oa_work_id_r" in validated_sql
    assert "outcome_computation" not in validated_sql


class _LegacyCursor:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self._one = None
        self._many = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "information_schema.columns" in sql:
            self._one = ("pair_id",)
        elif "SELECT pair_id FROM unvalidated" in sql:
            self._many = []
        elif "SELECT pair_id, data_json FROM pairs" in sql:
            self._many = [("legacy-pair", json.dumps(self.data))]

    def fetchone(self):
        value, self._one = self._one, None
        return value

    def fetchall(self):
        values, self._many = self._many, []
        return values


def test_old_database_migrator_splits_and_normalises_joined_reproduction_outcome():
    cur = _LegacyCursor({
        "type": "reproduction",
        "outcome": "computationally successful, robustness challenges",
        "doi_r": "10.1/rep",
        "title_r": "Replication",
        "title_o": "Original",
        "outcome_computational_quote": "ran with changes",
        "out_quote_computational_source": "results",
        "outcome_robustness_quote": "effect changed",
        "out_quote_robust_source": "discussion",
    })

    step_migrate_pairs(cur)

    insert = next(call for call in cur.calls if "INSERT INTO unvalidated" in call[0])
    params = insert[1]
    assert params[16] == "computationally reproducible, robustness challenges"
    assert params[19] == "computationally reproducible"
    assert params[22] == "robustness challenges"
    assert params[21] == "results"
    assert params[24] == "discussion"
