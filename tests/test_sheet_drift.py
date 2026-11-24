"""sheet_drift: which imported rows changed in their sheet after the import.

Pure: rows in, findings out. What is an edit and what is only spelling, which
columns are compared, and the row-level cases (missing, duplicated UUIDs, the
missing-row cap) against the real sources.yml configurations.
"""
from pathlib import Path

import pytest
import yaml

import sheet_drift as sd

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
SOURCES = {s["key"]: s for s in REGISTRY["sources"]}
ACCEPTED = REGISTRY["accepted_values"]
REPL = SOURCES["replications"]

U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"
U3 = "33333333-3333-4333-8333-333333333333"


def _row(**cells):
    """A replications sheet row, every expected column present."""
    row = {column: "" for column in REPL["expected_columns"]}
    row.update({"id": U1, "doi_o": "10.1/orig", "doi_r": "10.1/rep", "outcome": "failed",
                "validation_status": "validated - chosen", "ref_r": "A report",
                "year": "2020"})
    row.update(cells)
    return row


def _imported(raw, record_id="rec-1", **columns):
    # FReD and SCORE key on flora_id; FReD's own `id` column holds DOIs.
    return {"record_id": record_id, "sheet_row_id": raw.get("flora_id") or raw.get("id"),
            "raw": dict(raw), **columns}


def _compare(now_rows, imported, cfg=REPL, header_row=0, header=None):
    return sd.compare_source(now_rows, imported, cfg, ACCEPTED, header_row, header)


# ── what is not an edit ───────────────────────────────────────────────────────

@pytest.mark.parametrize("column, before, after", [
    ("ref_r", "A report", "  A   report  "),
    ("ref_r", "A report", "A\r\nreport"),
    ("ref_r", "A report", "A report"),
    ("ref_r", "A report", "A re​port"),
    ("ref_r", "Amélie", "Amélie"),                  # NFD vs NFC
    ("doi_r", "10.1/rep", "https://doi.org/10.1/REP"),
    ("doi_r", "10.1/rep", "http://dx.doi.org/10.1/rep"),
    ("doi_r", "10.1/rep", "doi: 10.1/rep"),
    ("url_r", "https://osf.io/abc/", "http://www.OSF.io/abc"),
    ("outcome", "failed", "Failed"),
    ("year", "2020", "2020.0"),
    ("validation_status", "validated - chosen", "Validated - Chosen"),
])
def test_spelling_changes_are_not_edits(column, before, after):
    raw = _row(**{column: before})
    assert _compare([_row(**{column: after})], [_imported(raw)]).findings == []


def test_a_real_edit_keeps_both_values_verbatim():
    raw = _row(outcome="failed")
    [finding] = _compare([_row(outcome="  successful ")], [_imported(raw)]).findings
    assert (finding.kind, finding.sheet_column, finding.record_column) == ("edited", "outcome", "outcome")
    assert (finding.value_at_import, finding.value_in_sheet) == ("failed", "successful")
    assert (finding.record_id, finding.sheet_row_id, finding.sheet_row_number) == ("rec-1", U1, 2)


def test_a_cleared_cell_is_an_edit_stored_as_blank():
    [finding] = _compare([_row(doi_r="")], [_imported(_row())]).findings
    assert (finding.value_at_import, finding.value_in_sheet) == ("10.1/rep", None)


def test_the_mapped_column_is_named_but_the_header_is_the_key():
    [finding] = _compare([_row(year="2021")], [_imported(_row())]).findings
    assert (finding.sheet_column, finding.record_column) == ("year", "year_r")


# ── which columns are compared ────────────────────────────────────────────────

def test_promoted_column_absent_from_raw_falls_back_to_the_stored_column():
    raw = _row()
    del raw["alt_identifier_o"]                       # added to the sheet after import
    imported = _imported(raw, alt_identifier_o="ISBN 1")
    assert _compare([_row(alt_identifier_o="ISBN 1")], [imported]).findings == []
    [finding] = _compare([_row(alt_identifier_o="ISBN 2")], [imported]).findings
    assert (finding.value_at_import, finding.value_in_sheet) == ("ISBN 1", "ISBN 2")


def test_raw_missing_entirely_uses_the_stored_columns():
    imported = {"record_id": "rec-1", "sheet_row_id": U1, "raw": None,
                "doi_r": "10.1/rep", "outcome": "failed"}
    now = {"id": U1, "doi_r": "10.1/rep", "outcome": "mixed"}
    [finding] = _compare([now], [imported]).findings
    assert (finding.sheet_column, finding.value_at_import) == ("outcome", "failed")


def test_id_unnamed_notes_and_header_changes_are_ignored():
    raw = _row(Coder="ann", validator_notes="old note", **{"Unnamed: 20": "x"})
    raw["removed_since"] = "gone"
    now = _row(id=U1, Coder="bob", validator_notes="new note", **{"Unnamed: 20": "y"})
    now["added_since"] = "new"
    assert _compare([now], [_imported(raw)]).findings == []


def test_column_rule_names_the_record_column_and_rule():
    repro = SOURCES["reproductions"]
    assert sd.column_rule(REPL, "id") == (None, None)
    assert sd.column_rule(REPL, "Unnamed: 3") == (None, None)
    assert sd.column_rule(REPL, "Coder") == (None, None)
    assert sd.column_rule(REPL, "doi_o") == ("doi_o", "doi")
    assert sd.column_rule(REPL, "year") == ("year_r", "year")
    assert sd.column_rule(REPL, "validation_status") == ("validation_status", "casefold")
    assert sd.column_rule(repro, "outcome_computational") == \
        ("outcome_computation", "axis:outcome_computation")
    assert sd.column_rule(repro, "validation") == ("validation_status", "casefold")
    assert sd.column_rule(repro, "outcome_computational_quote") == ("outcome_computational_quote", "text")


# ── the validation column ─────────────────────────────────────────────────────

def test_one_accepted_status_to_another_is_an_edit():
    [finding] = _compare([_row(validation_status="validated - changed")], [_imported(_row())]).findings
    assert finding.kind == "edited"


def test_an_unaccepted_status_is_not_accepted_and_the_row_still_compared():
    comparison = _compare([_row(validation_status="rejected", outcome="mixed")], [_imported(_row())])
    kinds = {f.sheet_column: f.kind for f in comparison.findings}
    assert kinds == {"validation_status": "not_accepted", "outcome": "edited"}
    assert comparison.compared == {U1}


def test_score_2025_never_reports_not_accepted():
    score = SOURCES["score_2025"]
    raw = {"flora_id": U1, "ref_o": "R", "outcome": "failed", "validation": "validated - chosen"}
    now = dict(raw, validation="rejected")
    assert _compare([now], [_imported(raw)], cfg=score).findings == []


# ── reproductions and FReD ────────────────────────────────────────────────────

def test_reproduction_axes_compare_through_their_aliases():
    repro = SOURCES["reproductions"]
    raw = {"id": U1, "outcome_computational": "computationally successful",
           "outcome_robustness": "robust", "validation": "validated - chosen"}
    same = dict(raw, outcome_computational="computationally reproducible")
    assert _compare([same], [_imported(raw)], cfg=repro).findings == []
    edited = dict(raw, outcome_robustness="robustness challenges")
    [finding] = _compare([edited], [_imported(raw)], cfg=repro).findings
    assert (finding.sheet_column, finding.record_column) == ("outcome_robustness", "outcome_robustness")


def test_fred_counts_rows_after_its_banner_and_keys_on_flora_id():
    fred = SOURCES["fred_replication_success"]
    raw = {"flora_id": U2, "id": "10.1/a10.1/b", "outcome": "failed", "validation": "validated - chosen",
           "validator_notes": "x"}
    other = {"flora_id": U3, "id": "", "outcome": "failed", "validation": "validated - chosen"}
    now = [dict(other), dict(raw, id="changed", validator_notes="y", outcome="successful")]
    comparison = _compare(now, [_imported(raw)], cfg=fred, header_row=1)
    [finding] = comparison.findings
    assert (finding.sheet_column, finding.sheet_row_number) == ("outcome", 4)   # banner, header, U3, U2
    assert comparison.compared == {U2}


# ── rows ──────────────────────────────────────────────────────────────────────

def test_a_row_gone_from_the_sheet_is_missing():
    comparison = _compare([_row(id=U2)], [_imported(_row())])
    [finding] = comparison.findings
    assert (finding.kind, finding.sheet_column, finding.value_in_sheet) == ("missing", "", None)
    assert comparison.missing == {U1} and comparison.compared == set()


def test_a_duplicated_uuid_is_left_untouched_even_when_one_copy_is_not_accepted():
    now = [_row(outcome="mixed"), _row(validation_status="needs review")]
    comparison = _compare(now, [_imported(_row())])
    assert comparison.findings == []
    assert comparison.ambiguous == {U1}
    assert comparison.compared == set() and comparison.missing == set()
    assert any("more than once" in note for note in comparison.notes)


def test_the_missing_row_cap_holds_back_a_first_deployment_backlog():
    imported = [_imported(_row(id=f"{n:08d}-0000-4000-8000-000000000000"), record_id=f"r{n}")
                for n in range(100)]
    comparison = _compare([_row(id="ffffffff-0000-4000-8000-000000000000")], imported)
    assert comparison.missing_recorded is False
    assert len(comparison.missing) == 100
    assert not [f for f in comparison.findings if f.kind == "missing"]
    assert any("not recorded" in note for note in comparison.notes)
    # Under the cap they are recorded.
    few = _compare([_row(id=imported[0]["sheet_row_id"])], imported[:10])
    assert few.missing_recorded and len([f for f in few.findings if f.kind == "missing"]) == 9


def test_the_cap_is_five_percent_or_twenty():
    assert sd.missing_cap(10) == 20
    assert sd.missing_cap(1000) == 50


def test_row_numbers_follow_the_header_row():
    now = [_row(id=U2), _row(outcome="mixed")]
    [finding] = _compare(now, [_imported(_row())], header_row=1).findings
    assert finding.sheet_row_number == 4


def test_an_unchanged_sheet_gives_no_findings_twice():
    """Idempotent: a second check of the same rows finds exactly what the first did."""
    imported = [_imported(_row()), _imported(_row(id=U2, outcome="mixed"), record_id="rec-2")]
    now = [_row(outcome="successful"), _row(id=U2, outcome="mixed")]
    first = _compare(now, imported).findings
    second = _compare(now, imported).findings
    assert first == second and len(first) == 1


def test_no_rows_with_a_header_reports_every_row_missing():
    comparison = _compare([], [_imported(_row())], header=list(_row()))
    assert comparison.missing == {U1}
    assert "outcome" in comparison.columns and "id" not in comparison.columns


def test_rules_version_is_an_integer():
    assert isinstance(sd.RULES_VERSION, int) and sd.RULES_VERSION >= 1
