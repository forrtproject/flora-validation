"""Tests for sync_validated — projecting our own validated records into the grid.

The interesting rules are all about what this sync is allowed to overwrite. Unlike
the sheet syncs it is not insert-only (the data is ours and can legitimately change),
but it must never overwrite a row a human has reviewed in the grid, and it must not
claim a sheet provenance the rows do not have.
"""
import pytest

import sync_validated as sv
from source_records_service import LIST_COLUMNS


def _validated_row(**over):
    row = {
        "validated_record_id": "7aea6582-1111-4222-8333-444455556666",
        "record_id": "0000aaaa-1111-4222-8333-444455556666",
        "type": "replication",
        "ref_o": "Smith (2001)", "doi_o": "10.1/o", "url_o": "",
        "ref_r": "Jones (2020)", "doi_r": "10.2/r", "url_r": "https://osf.io/x",
        "abstract_r": "abstract", "year_r": "2020", "study_o": "1",
        "outcome": "successful", "outcome_quote": "q", "out_quote_source": "abstract",
        "outcome_computation": None, "outcome_computational_quote": None,
        "out_quote_computational_source": None,
        "outcome_robustness": None, "outcome_robustness_quote": None,
        "out_quote_robust_source": None,
        "alt_identifier_r": None,
        "title_o": "Original title", "title_r": "Replication title",
        "admin_approved": True, "validated_at": "2026-09-01T00:00:00Z",
        "original_key": "10.1/o",
    }
    row.update(over)
    return row


# ── identity and mapping ──────────────────────────────────────────────────────

def test_identity_is_the_validated_record_uuid():
    row = sv._build_row(_validated_row())
    assert row["source"] == "validated"
    assert row["sheet_row_id"] == "7aea6582-1111-4222-8333-444455556666"


def test_direct_columns_are_copied():
    row = sv._build_row(_validated_row())
    assert row["doi_o"] == "10.1/o"
    assert row["doi_r"] == "10.2/r"
    assert row["url_r"] == "https://osf.io/x"
    assert row["year_r"] == "2020"
    assert row["outcome"] == "successful"


def test_blank_strings_become_null():
    """'' and NULL must not be two different spellings of absent in the grid."""
    row = sv._build_row(_validated_row(url_o="", doi_o="   "))
    assert row["url_o"] is None
    assert row["doi_o"] is None


def test_validation_status_is_never_invented():
    """That column holds the sheet coders' vocabulary. These rows never passed
    through a sheet, so claiming one of its values would fake a provenance."""
    row = sv._build_row(_validated_row())
    assert "validation_status" not in sv.SYNCED_COLUMNS
    assert row.get("validation_status") is None


def test_oa_work_ids_are_left_to_the_backfill():
    """Setting them here would fight trg_clear_stale_source_oa_work_id, which
    clears them whenever the DOI beside them is written."""
    assert "oa_work_id_o" not in sv.SYNCED_COLUMNS
    assert "oa_work_id_r" not in sv.SYNCED_COLUMNS


# ── the two outcome vocabularies ──────────────────────────────────────────────

def test_replication_keeps_outcome_and_drops_reproduction_axes():
    row = sv._build_row(_validated_row(outcome_computation="computational issues"))
    assert row["outcome"] == "successful"
    assert row["outcome_computation"] is None
    assert row["outcome_robustness"] is None


def test_reproduction_keeps_axes_and_drops_the_joined_outcome():
    """validated.outcome holds the two axes joined for display
    ("computationally reproducible, robust"). source_records keeps them apart, so
    the joined form would be a third spelling of data already present."""
    row = sv._build_row(_validated_row(
        type="reproduction",
        outcome="computationally reproducible, robust",
        outcome_computation="computationally reproducible",
        outcome_robustness="robust",
    ))
    assert row["outcome"] is None
    assert row["outcome_quote"] is None
    assert row["out_quote_source"] is None
    assert row["outcome_computation"] == "computationally reproducible"
    assert row["outcome_robustness"] == "robust"


def test_missing_type_defaults_to_replication():
    """source_records.type is NOT NULL with a CHECK; a NULL here would abort the
    whole sync on one bad row."""
    assert sv._build_row(_validated_row(type=None))["type"] == "replication"


# ── raw ───────────────────────────────────────────────────────────────────────

def test_raw_keeps_fields_source_records_has_no_column_for():
    payload = sv._raw_payload(_validated_row())
    for field in ("title_o", "title_r", "admin_approved", "original_key", "record_id"):
        assert field in payload


def test_raw_stringifies_every_value():
    """raw is JSONB; a datetime or UUID would not serialise."""
    payload = sv._raw_payload(_validated_row())
    assert all(isinstance(v, str) for v in payload.values())
    assert payload["admin_approved"] == "True"


def test_raw_renders_none_as_empty_string():
    payload = sv._raw_payload(_validated_row(alt_identifier_r=None))
    assert payload["alt_identifier_r"] == ""


# ── refresh contract ──────────────────────────────────────────────────────────

def test_every_direct_column_is_also_refreshed():
    """A column copied on insert but absent from SYNCED_COLUMNS would be written
    once and then silently never updated again."""
    missing = [c for c in sv.DIRECT_COLUMNS if c not in sv.SYNCED_COLUMNS]
    assert not missing


def test_synced_columns_all_exist_on_the_grid_or_are_structural():
    grid = {c.split(" AS ")[-1].strip() for c in LIST_COLUMNS}
    structural = {"raw", "type", "abstract_r", "url_o", "outcome_quote",
                  "out_quote_source", "study_o", "alt_identifier_r",
                  "outcome_computational_quote", "out_quote_computational_source",
                  "outcome_robustness_quote", "out_quote_robust_source"}
    unknown = [c for c in sv.SYNCED_COLUMNS if c not in grid and c not in structural]
    assert not unknown, f"not a source_records column: {unknown}"


@pytest.mark.parametrize("prefix_bits", [("VAL", "validated")])
def test_source_key_and_prefix_do_not_collide_with_the_sheets(prefix_bits):
    import yaml
    from pathlib import Path
    registry = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "sources.yml").read_text(encoding="utf-8")
    )
    prefix, key = prefix_bits
    assert key not in {s["key"] for s in registry["sources"]}
    assert prefix not in {s["display_prefix"] for s in registry["sources"]}
