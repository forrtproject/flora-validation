"""Tests for the multi-spreadsheet registry in sources.yml and the gates that read it.

Three traps this covers, all found while adding the "Validating FReD replication
success" spreadsheet as a second document:

1. Its grids open with a banner row, so the real header is the *second* row. Parsed
   at row 0, every column comes back named `Unnamed: N` and gate 4 reports the whole
   sheet missing rather than the header being off by one.
2. That sheet has a column literally named `id` which holds doi_o + doi_r
   concatenated, while the row UUID lives in `flora_id`. Keying on `id` would fail
   the UUID check once per row instead of failing the source once.
3. It is a different spreadsheet, so `document` has to be resolvable per source
   without the original sources repeating the default.
"""
from pathlib import Path

import pytest
import yaml

import sync_sources as ss

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
SOURCES = {s["key"]: s for s in REGISTRY["sources"]}

BANNER_CSV = (
    b",,should include success criterion\n"
    b"flora_id,outcome,notes\n"
    b"26d3adab-589d-4a49-ba0d-52b1c6f0a1d2,successful,fine\n"
)


def _resolve_document(cfg):
    """The same fallback sync_source() applies when building the fetch URL."""
    return cfg.get("document") or REGISTRY["document"]


# ── header_row ────────────────────────────────────────────────────────────────

def test_header_row_skips_the_banner():
    df = ss._parse(BANNER_CSV, 1)
    assert list(df.columns) == ["flora_id", "outcome", "notes"]
    assert len(df) == 1


def test_banner_row_is_taken_as_the_header_without_header_row():
    df = ss._parse(BANNER_CSV, 0)
    assert "flora_id" not in df.columns
    with pytest.raises(ss.GateFailure, match="missing expected column"):
        ss._assert_columns(df, ["flora_id"])


def test_header_row_defaults_to_zero_for_sheets_without_a_banner():
    df = ss._parse(b"flora_id,outcome\nc0ffee,successful\n")
    assert list(df.columns) == ["flora_id", "outcome"]


# ── per-source document ───────────────────────────────────────────────────────

def test_original_sources_fall_back_to_the_default_document():
    for key in ("replications", "reproductions"):
        assert "document" not in SOURCES[key]
        assert _resolve_document(SOURCES[key]) == REGISTRY["document"]


def test_fred_sources_override_the_document():
    fred = REGISTRY["fred_document"]
    assert fred != REGISTRY["document"]
    for key in ("fred_replication_success", "fred_replication_success_backup", "score_2025"):
        assert _resolve_document(SOURCES[key]) == fred


def test_a_gid_may_repeat_across_documents():
    """984458430 is `reproductions` in one spreadsheet and the FReD tab in the
    other. Identity is (source, sheet_row_id), so this is legal — but only if the
    two resolve to different documents."""
    a, b = SOURCES["reproductions"], SOURCES["fred_replication_success"]
    assert a["gid"] == b["gid"]
    assert _resolve_document(a) != _resolve_document(b)


# ── id_column guard ───────────────────────────────────────────────────────────

def test_id_column_must_be_covered_by_gate_4():
    cfg = dict(SOURCES["fred_replication_success"])
    cfg["id_column"] = "id"          # present in the sheet, but holds DOIs
    with pytest.raises(ss.GateFailure, match="id_column"):
        ss._validate_registry(cfg)


def test_fred_sources_key_on_flora_id_not_id():
    for key in ("fred_replication_success", "fred_replication_success_backup", "score_2025"):
        assert SOURCES[key]["id_column"] == "flora_id"


# ── whole-registry invariants ─────────────────────────────────────────────────

def test_every_source_passes_registry_validation():
    for cfg in REGISTRY["sources"]:
        ss._validate_registry(cfg)


def test_source_keys_and_display_prefixes_are_unique():
    keys = [s["key"] for s in REGISTRY["sources"]]
    prefixes = [s["display_prefix"] for s in REGISTRY["sources"]]
    assert len(keys) == len(set(keys))
    assert len(prefixes) == len(set(prefixes))


def test_no_two_sources_point_at_the_same_tab():
    tabs = [(_resolve_document(s), s["gid"]) for s in REGISTRY["sources"]]
    assert len(tabs) == len(set(tabs))


def test_mapped_promoted_columns_exist_on_the_sheet():
    """A promoted column must be reachable: either it is a sheet column already, or
    column_map renames a sheet column onto it. Otherwise it inserts as a permanent
    NULL that insert-only can never backfill."""
    for cfg in REGISTRY["sources"]:
        reachable = {(cfg.get("column_map") or {}).get(c, c) for c in cfg["expected_columns"]}
        missing = [c for c in cfg["promoted"] if c not in reachable]
        assert not missing, f"{cfg['key']}: promoted but not on the sheet: {missing}"


# ── curated sheet with no validation column ───────────────────────────────────

def test_score_2025_declares_an_empty_validation_column():
    """Explicitly present and empty, not omitted — 'this sheet has no gate' must be
    distinguishable from a deleted key."""
    cfg = SOURCES["score_2025"]
    assert "validation_column" in cfg
    assert not cfg.get("validation_column")


def test_gated_sources_name_a_validation_column_they_actually_have():
    for key in ("replications", "reproductions", "fred_replication_success"):
        cfg = SOURCES[key]
        assert cfg["validation_column"] in cfg["expected_columns"]


def test_backup_tab_stays_disabled():
    """It is ~98.5% the same studies as fred_replication_success with entirely
    different UUIDs, so nothing dedupes it. Insert-only makes that permanent."""
    assert SOURCES["fred_replication_success_backup"]["enabled"] is False
