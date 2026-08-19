"""Tests for the reproduction outcome axes — the schema half of the codebook change.

Reproductions are coded on two INDEPENDENT axes that replace the single joined
string. Independent is the operative word: a reproduction can fail computationally
and still find the conclusion robust, so neither axis is derivable from the other,
and the joined string cannot represent both.

These columns land ahead of the validator UI on purpose. flora-extractor's writer
pushes the six fields "once this side can accept them", and it goes through
PostgREST, which rejects an entire insert naming a column the table lacks.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from extractor_vocab import (
    OUTCOME_AXES,
    OUTCOME_COMPUTATION_VALUES,
    OUTCOME_ROBUSTNESS_VALUES,
    VocabularyDriftError,
    check_csv_vocabulary,
)
from csv_to_db import _build_unvalidated_row

_SCHEMA = Path(__file__).resolve().parent.parent / "db_schema.sql"

_SIX_FIELDS = [
    "outcome_computation",
    "outcome_computational_quote",
    "out_quote_computational_source",
    "outcome_robustness",
    "outcome_robustness_quote",
    "out_quote_robust_source",
]


def _frame(**overrides):
    base = {
        "pair_id": ["a", "b"],
        "filter_status": ["reproduction", "reproduction"],
        "link_method": ["llm_references", "llm_title_search"],
        "outcome": ["computationally reproducible, robust", "computational issues, robust"],
        "outcome_computation": ["computationally reproducible", "computational issues"],
        "outcome_robustness": ["robust", "robust"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def test_computation_axis_matches_the_codebook():
    assert OUTCOME_COMPUTATION_VALUES == frozenset({
        "computationally reproducible", "computational issues", "technical failure",
        "not checked", "cannot_be_determined"})


def test_robustness_axis_matches_the_codebook():
    assert OUTCOME_ROBUSTNESS_VALUES == frozenset({
        "robust", "robustness challenges", "not checked", "cannot_be_determined"})


def test_technical_failure_is_declared_before_it_is_emitted():
    """It is in the codebook but the extractor has never written it. Declaring it
    early means the first row carrying it imports instead of tripping the drift
    check on an upstream change we already knew was coming."""
    assert "technical failure" in OUTCOME_COMPUTATION_VALUES


def test_the_grid_is_five_by_four():
    """Storage accepts an undetermined state in addition to the settled 4x3 grid.

    The validator presents four/three correction options and expresses the extra
    state through its separate "Can't tell" review action.
    """
    assert len(OUTCOME_COMPUTATION_VALUES) == 5
    assert len(OUTCOME_ROBUSTNESS_VALUES) == 4


def test_axes_share_the_not_checked_and_undetermined_values():
    """Either axis can be unassessed or undecidable, independently of the other."""
    for shared in ("not checked", "cannot_be_determined"):
        assert shared in OUTCOME_COMPUTATION_VALUES
        assert shared in OUTCOME_ROBUSTNESS_VALUES


# ---------------------------------------------------------------------------
# Python vocabulary vs the SQL constraints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("column,constraint", [
    ("outcome_computation", "unvalidated_outcome_computation_check"),
    ("outcome_robustness", "unvalidated_outcome_robustness_check"),
])
def test_axis_constraint_matches_the_python_vocabulary(column, constraint):
    """Same vocabulary written twice; a disagreement rolls back the whole import
    transaction rather than the offending row."""
    sql = _SCHEMA.read_text(encoding="utf-8")
    body = re.search(
        rf"ADD CONSTRAINT {constraint}\s*CHECK \({column} IS NULL OR {column} IN \((.*?)\)\);",
        sql, re.S,
    )
    assert body, f"could not locate {constraint} in db_schema.sql"
    assert set(re.findall(r"'([^']+)'", body.group(1))) == set(OUTCOME_AXES[column])


def test_schema_adds_all_six_columns_to_both_tables():
    sql = _SCHEMA.read_text(encoding="utf-8")
    block = re.search(r"FOREACH tbl IN ARRAY ARRAY\['unvalidated', 'validated'\].*?END \$\$;", sql, re.S)
    assert block, "could not locate the six-column migration"
    for field in _SIX_FIELDS:
        assert field in block.group(0), f"{field} missing from the migration"


def test_axis_columns_are_created_before_the_joined_outcome_backfill():
    """Existing deployments do not have these columns yet. PostgreSQL raises
    UndefinedColumn when the dynamic backfill executes before they are added,
    preventing the container from starting."""
    sql = _SCHEMA.read_text(encoding="utf-8")
    backfill = sql.index("FOR t IN SELECT * FROM (VALUES")
    targets = [
        ("unvalidated", "outcome_computation"),
        ("unvalidated", "outcome_robustness"),
        ("unvalidated", "final_outcome_computation"),
        ("unvalidated", "final_outcome_robustness"),
        ("validated", "outcome_computation"),
        ("validated", "outcome_robustness"),
        ("validation_queue", "corrected_outcome_computation"),
        ("validation_queue", "corrected_outcome_robustness"),
    ]
    for table, column in targets:
        migration = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}"
        assert sql.index(migration) < backfill, f"{table}.{column} is added after the backfill"


def test_schema_renames_the_field_but_not_its_evidence_columns():
    """The field is `outcome_computation`; the quote/source keep the adjective.
    That mix is the codebook's and the CSV's — matched deliberately, not tidied."""
    sql = _SCHEMA.read_text(encoding="utf-8")
    assert "RENAME COLUMN outcome_computational TO outcome_computation" in sql
    assert "outcome_computational_quote" in sql
    assert "out_quote_computational_source" in sql


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def test_axes_are_read_straight_from_the_csv():
    row = pd.Series({
        "type": "reproduction",
        "outcome": "cannot_be_determined",
        "outcome_computation": "cannot_be_determined",
        "outcome_robustness": "robustness challenges",
        "outcome_computational_quote": "the code did not run",
        "out_quote_computational_source": "discussion",
        "outcome_robustness_quote": "results shift under alternative specs",
        "out_quote_robust_source": "results",
    })
    built = _build_unvalidated_row("rec-1", "pair-1", row)
    # This is the exact shape of the upstream bug: the joined string collapsed to a
    # bare 'cannot_be_determined' and threw the robustness verdict away, while the
    # axis column kept it. The axes are stored and `outcome` is NULL — keeping the
    # flat label too would contradict the axis that has a real verdict.
    assert built["outcome"] == "cannot_be_determined"
    assert built["outcome_robustness"] == "robustness challenges"
    assert built["outcome_computation"] == "cannot_be_determined"
    assert built["outcome_robustness_quote"] == "results shift under alternative specs"
    assert built["out_quote_robust_source"] == "results"


def test_replications_store_blank_axis_verdicts_as_null():
    """The SQL vocabulary constraints accept NULL, not an empty string. Quote
    and source fields remain ordinary optional text, but the two coded verdicts
    must bind as SQL NULL or every replication rolls back the import."""
    row = pd.Series({"type": "replication", "outcome": "success"})
    built = _build_unvalidated_row("rec-2", "pair-2", row)
    assert built["outcome_computation"] is None
    assert built["outcome_robustness"] is None
    for field in (
        "outcome_computational_quote", "out_quote_computational_source",
        "outcome_robustness_quote", "out_quote_robust_source",
    ):
        assert built[field] == "", f"{field} should be blank on a replication"


def test_missing_axis_quote_is_a_legal_state():
    """2 of 25 reproduction rows carry neither quote. Import must not invent one
    or reject the row."""
    row = pd.Series({
        "type": "reproduction",
        "outcome_computation": "not checked",
        "outcome_robustness": "robust",
    })
    built = _build_unvalidated_row("rec-3", "pair-3", row)
    assert built["outcome_computational_quote"] == ""
    assert built["outcome_robustness_quote"] == ""
    assert built["outcome_computation"] == "not checked"


# ---------------------------------------------------------------------------
# Drift check
# ---------------------------------------------------------------------------

def test_current_axis_values_pass():
    check_csv_vocabulary(_frame())


def test_unknown_computation_value_raises():
    df = _frame(outcome_computation=["computationally reproducible", "vibes_based_reproduction"])
    with pytest.raises(VocabularyDriftError, match="vibes_based_reproduction"):
        check_csv_vocabulary(df)


def test_unknown_robustness_value_raises():
    df = _frame(outcome_robustness=["robust", "quite_wobbly"])
    with pytest.raises(VocabularyDriftError, match="quite_wobbly"):
        check_csv_vocabulary(df)


def test_technical_failure_passes_the_drift_check():
    """The value the extractor has not shipped yet must not block its own arrival."""
    df = _frame(outcome_computation=["technical failure", "not checked"])
    check_csv_vocabulary(df)


def test_a_csv_without_the_axis_columns_still_passes():
    """Archived CSVs predate the codebook change and carry neither column."""
    df = _frame().drop(columns=["outcome_computation", "outcome_robustness"])
    check_csv_vocabulary(df)


def test_axis_values_on_non_importable_rows_are_ignored():
    """A false_positive row never reaches the database, so its axes aren't ours
    to police — refusing there would block an import over discarded rows."""
    df = _frame(
        filter_status=["reproduction", "false_positive"],
        outcome_computation=["computationally reproducible", "whatever the extractor felt like"],
    )
    check_csv_vocabulary(df)
