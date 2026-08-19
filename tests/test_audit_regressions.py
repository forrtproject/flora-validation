"""Cross-layer regressions found during the reproduction-workflow audit."""
import json
import re
from pathlib import Path

import pandas as pd

from consensus_engine import _is_unsure
from csv_to_db import _refresh_extractor_fields
from db_migrate import step_migrate_pairs


ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def test_bootstrap_explicitly_replays_legacy_seed_and_fails_loudly():
    init = re.search(r"def init_db\(\):(.*?)\n\ninit_db\(\)", APP, re.S)
    assert init
    assert '"--allow-legacy-schema"' in init.group(1)
    assert "check=True" in init.group(1)
    assert "check=False" not in init.group(1)


def test_assignment_uncertainty_routes_to_review_and_does_not_get_bonus():
    assert 'else "need_review" if _request_is_unsure(req)' in APP
    assert '"unsure" in axis_checks.values()' in APP
    assert 'axis_checks.get("computation") == "correct"' in APP
    assert 'axis_checks.get("robustness") == "correct"' in APP
    assert _is_unsure({
        "additional_checks": {
            "reproduction_axis_checks": {"computation": "correct", "robustness": "unsure"}
        }
    })


def test_api_uses_one_strict_outcome_shape_boundary():
    assert APP.count("_validated_outcome_request(") >= 4
    assert "corrected_type is required when type_check is incorrect" in APP
    assert "Replication judgements must not include reproduction axes" in APP
    assert "A reproduction requires both outcome_computation and outcome_robustness" in APP
    assert "split_joined_outcome(joined)" in APP


def test_browser_uses_explicit_axis_actions_for_aggregate_outcome_check():
    aggregate = re.search(r"const reproOutcomeCheck = \(\) => \{(.*?)\n  \};", JS, re.S)
    assert aggregate
    body = aggregate.group(1)
    assert 'j.repro_computation_check === "correct"' in body
    assert 'j.repro_robustness_check === "correct"' in body
    assert "repro_computation ===" not in body
    assert "repro_robustness ===" not in body


def test_admin_type_conversion_has_dynamic_required_axis_controls():
    assert 'id="ar-repro-axes"' in JS
    assert '$("#ar-type-sel")?.addEventListener("change"' in JS
    assert 'classList.toggle("hidden", ev.target.value !== "reproduction")' in JS
    assert "Choose both reproduction axes before resolving this record." in JS
    assert "Choose a replication outcome before resolving this record." in JS


def test_admin_and_history_render_axis_values_with_paired_evidence():
    for token in (
        "reproAxisRow", "corrected_computational_quote",
        "corrected_computational_source", "corrected_robustness_quote",
        "corrected_robustness_source", "_historyAxisCard",
        "val_outcome_computation", "val_outcome_robustness",
    ):
        assert token in JS
    for token in (
        '"corrected_outcome_computation": row["corrected_outcome_computation"]',
        '"corrected_computational_quote": row["corrected_computational_quote"]',
        '"corrected_computational_source": row["corrected_computational_source"]',
        '"val_outcome_computation": row["val_outcome_computation"]',
        '"val_robustness_source": row["val_robustness_source"]',
    ):
        assert token in APP


def test_admin_comment_filter_is_supported_end_to_end():
    assert '"admin_comments":   "WHERE NULLIF(BTRIM(u.admin_notes), \'\') IS NOT NULL"' in APP
    assert '"admin_comments": c_admin_comments' in APP
    assert 'data-filter="admin_comments"' in HTML
    assert 'id="fc-admin-comments"' in HTML
    assert 'counts.admin_comments ?? 0' in JS


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
