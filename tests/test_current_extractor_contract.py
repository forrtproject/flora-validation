"""Regression coverage for the current flora-extractor Stage-3 contract."""
from pathlib import Path

import pandas as pd
import pytest

from consensus_engine import _resolve_final
from csv_to_db import (
    InputIdentityError,
    InputSchemaError,
    _CURRENT_EXTRACTED_COLUMNS,
    _build_metadata_row,
    _insert_metadata,
    _source_slot_key,
    _validate_csv_schema,
    _validate_resolved_identity,
)
from extractor_vocab import (
    CURRENT_REPRODUCTION_OUTCOMES,
    FLAWED_OUTCOME,
    KNOWN_METHODS,
    REPLICATION_OUTCOMES,
    RESOLVED_METHODS,
    derive_reproduction_outcome,
    normalize_outcome,
)


ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_current_link_method_contract_is_complete():
    assert {
        "grobid_ref_match",
        "same_author_year_title_overlap",
        "single_candidate_after_requery",
    } <= RESOLVED_METHODS
    assert {
        "unidentified_original",
        "keyed_link_disputed",
        "author_year_match_legacy",
        "not_a_replication",
        "prescreen_discard",
        "screen_disagreement",
    } <= KNOWN_METHODS - RESOLVED_METHODS


def test_current_outcome_labels_remain_canonical():
    assert normalize_outcome("descriptive") == "descriptive only"
    assert normalize_outcome("statistically_successful_but_flawed") == FLAWED_OUTCOME
    assert "descriptive only" in REPLICATION_OUTCOMES
    assert FLAWED_OUTCOME == "statistically successful but flawed"


def test_reproduction_grid_has_twelve_derived_values():
    assert len(CURRENT_REPRODUCTION_OUTCOMES) == 12
    assert derive_reproduction_outcome("technical failure", "robust") == \
        "technical failure, robust"
    assert derive_reproduction_outcome("technical failure", "cannot_be_determined") == \
        "cannot_be_determined"


def test_consensus_rederives_reproduction_outcome_after_axis_correction():
    record = {
        "type": "reproduction",
        "outcome": "computationally reproducible, robust",
        "outcome_computation": "computationally reproducible",
        "outcome_robustness": "robust",
        "doi_o": "10.1/o",
    }
    final = _resolve_final(record, {
        "corrected_outcome_computation": "computational issues",
        "corrected_outcome_robustness": "robustness challenges",
    })
    assert final["outcome"] == "computational issues, robustness challenges"


def test_manual_resolution_paths_rederive_and_persist_reproduction_axes():
    # Assignment, queued judgement, and admin resolution all use the same strict
    # validator; one-click approval independently re-derives before export.
    assert APP.count("_validated_outcome_request(") >= 4
    assert APP.count("derive_reproduction_outcome(") >= 2
    for field in (
        "final_outcome_computation  = %s",
        "final_computational_quote  = %s",
        "final_computational_source = %s",
        "final_outcome_robustness   = %s",
        "final_robustness_quote     = %s",
        "final_robustness_source    = %s",
    ):
        assert field in APP


def test_current_header_contract_rejects_missing_columns():
    columns = sorted(_CURRENT_EXTRACTED_COLUMNS | {"filter_status"})
    frame = pd.DataFrame(columns=columns)
    _validate_csv_schema(frame)
    with pytest.raises(InputSchemaError, match="study_o"):
        _validate_csv_schema(frame.drop(columns=["study_o"]))


def test_archived_header_requires_explicit_opt_in():
    _validate_csv_schema(pd.DataFrame(columns=["pair_id", "filter_status"]), allow_legacy=True)


def test_current_resolved_rows_require_engine_work_identity():
    row = {
        "doi_r": "10.1/rep",
        "title_r": "Replication",
        "oa_work_id_r": "",
        "openalex_id_r": "",
        "doi_o_verification": "verified",
        "oa_work_id_o": "W456",
    }
    with pytest.raises(InputIdentityError, match="engine lineage would be NULL"):
        _validate_resolved_identity(pd.DataFrame([row]))


def test_metadata_payload_keeps_lineage_and_provenance():
    row = pd.Series({
        "pair_id": "pair",
        "oa_work_id_r": "W2884670852",
        "screen_categories": "clearly_declared|self_retest",
        "outcome_reasoning": "The reported result matches.",
        "outcome_llm_model": "gemini-test",
        "bibtex_ref_o": "@article{o}",
        "bibtex_ref_r": "@article{r}",
    })
    built = _build_metadata_row("record", "pair", row, "release-42")
    assert built["work_id"] == 2884670852
    assert built["release_id"] == "release-42"
    for field in (
        "screen_categories", "outcome_reasoning", "outcome_llm_model",
        "bibtex_ref_o", "bibtex_ref_r",
    ):
        assert built[field] == row[field]


def test_metadata_uses_row_release_when_import_run_does_not_supply_one():
    row = pd.Series({"oa_work_id_r": "W123", "release_id": "row-release"})
    assert _build_metadata_row("record", "pair", row)["release_id"] == "row-release"
    assert _build_metadata_row(
        "record", "pair", row, "run-release"
    )["release_id"] == "run-release"


def test_metadata_insert_upserts_existing_records():
    class Cursor:
        def execute(self, sql, params):
            self.sql, self.params = sql, params

    cur = Cursor()
    row = _build_metadata_row(
        "record", "pair", pd.Series({"oa_work_id_r": "W123"}), "release-1"
    )
    _insert_metadata(cur, row)
    assert "ON CONFLICT (record_id) DO UPDATE" in cur.sql
    for field in (
        "work_id", "release_id", "screen_categories", "outcome_reasoning",
        "outcome_llm_model", "bibtex_ref_o", "bibtex_ref_r",
    ):
        assert field in cur.sql


def test_source_slot_survives_an_original_doi_rekey():
    before = {"work_id": 123, "original_rank": 2, "pair_id": "old"}
    after = {
        "oa_work_id_r": "https://openalex.org/W123",
        "original_rank": "2",
        "pair_id": "new",
    }
    assert _source_slot_key(before) == _source_slot_key(after) == (123, 2)


def test_database_declares_lineage_and_provenance_columns():
    for declaration in (
        "ADD COLUMN IF NOT EXISTS work_id            BIGINT",
        "ADD COLUMN IF NOT EXISTS release_id         TEXT",
        "ADD COLUMN IF NOT EXISTS screen_categories  TEXT",
        "ADD COLUMN IF NOT EXISTS outcome_reasoning  TEXT",
        "ADD COLUMN IF NOT EXISTS outcome_llm_model  TEXT",
        "ADD COLUMN IF NOT EXISTS bibtex_ref_o       TEXT",
        "ADD COLUMN IF NOT EXISTS bibtex_ref_r       TEXT",
    ):
        assert declaration in SCHEMA
