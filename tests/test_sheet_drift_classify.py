"""sheet_drift_service.classify and friends: what a sheet edit means for FLoRA now.

Pure: a finding, its source row and one FLoRA record in, a state out. Every state
and every account of where FLoRA's value comes from; the build's own rules (aliases,
publisher metadata, a filled-in abstract, an original's landing page, a url_r that
restates the DOI); keeps that still hold and keeps the sheet has moved past; zero
or several FLoRA records; and the CSV's shape.
"""
import csv
import io
from datetime import datetime, timezone

import pytest

import sheet_drift_service as svc

U1 = "11111111-1111-4111-8111-111111111111"
ALIASES = {"successful": "successful", "failed": "failed", "mixed": "mixed",
           "uninformative": "uninformative", "unclear": "cannot_be_determined"}
WHEN = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)


def finding(**fields):
    base = {"finding_id": 1, "record_id": "rec-1", "source": "replications", "sheet_row_id": U1,
            "sheet_column": "outcome", "record_column": "outcome", "kind": "edited",
            "value_at_import": "failed", "value_in_sheet": "successful", "sheet_row_number": 245,
            "first_seen_at": WHEN, "last_seen_at": WHEN, "resolved_at": None,
            "kept_at": None, "kept_by": None, "kept_note": None, "kept_sheet_value": None}
    return {**base, **fields}


def source_row(**fields):
    raw = {"id": U1, "outcome": "failed", "doi_o": "10.1/o", "doi_r": "10.1/r", "ref_r": "My report",
           "abstract_r": "", "url_o": "", "url_r": "", "year": "2019"}
    base = {"record_id": "rec-1", "display_id": "REPL-000847", "source": "replications",
            "type": "replication", "raw": raw, "outcome": "failed", "doi_o": "10.1/o",
            "doi_r": "10.1/r", "ref_r": "My report", "abstract_r": None, "url_o": None,
            "url_r": None, "year_r": "2019", "duplicate_status": None, "deleted_at": None}
    return {**base, **fields}


def target(role="primary", corrected=None, corrections=None, **row):
    frame = {"flora_id": "REPL-000847", "export_id": "FLORA-000847", "type": "replication",
             "outcome": "failed", "doi_o": "10.1/o", "doi_r": "10.1/r",
             "url_o": "https://doi.org/10.1/o", "url_r": None, "ref_r": "My report",
             "abstract_r": None, "year_r": "2019", "source_display_id": "REPL-000100"}
    frame.update(row)
    return {"flora_id": frame["flora_id"], "export_id": frame["export_id"], "role": role,
            "version": 4, "row": frame, "corrected": corrected or {},
            "corrections": corrections or {}, "primary_display_id": frame["source_display_id"]}


def classify(f=None, record=None, t=None):
    return svc.classify(f or finding(), record or source_row(), t or target(), ALIASES)


# ── the states ────────────────────────────────────────────────────────────────

def test_pending_when_flora_still_has_the_imported_value():
    out = classify()
    assert (out["state"], out["suggested_action"]) == ("pending", "use_sheet_value")
    assert (out["flora_source"], out["flora_source_text"]) == ("import", "as imported")
    assert (out["sheet_value_for_flora"], out["sheet_value_error"]) == ("successful", None)
    assert (out["flora_id"], out["export_id"], out["version"]) == ("REPL-000847", "FLORA-000847", 4)


def test_matches_when_flora_already_has_the_sheets_value():
    assert classify(t=target(outcome="successful"))["state"] == "matches"


def test_a_correction_that_differs_from_both_says_who_and_why():
    t = target(outcome="mixed", corrected={"outcome": "failed"},
               corrections={"outcome": {"by": "luke", "at": WHEN, "note": "checked the paper"}})
    out = classify(t=t)
    assert (out["state"], out["suggested_action"], out["flora_source"]) == ("conflict", "decide", "correction")
    assert out["flora_source_text"] == "corrected by luke, 3 Nov 2026: “checked the paper”"


def test_a_correction_to_the_imported_value_is_still_a_decision():
    t = target(outcome="failed", corrected={"outcome": "mixed"},
               corrections={"outcome": {"by": "luke", "at": WHEN, "note": ""}})
    assert classify(t=t)["state"] == "conflict"


def test_an_old_source_edit_is_a_decision():
    record = source_row(outcome="mixed")            # raw says failed: edited by hand
    out = classify(record=record, t=target(outcome="mixed"))
    assert (out["state"], out["flora_source"]) == ("conflict", "source_edit")


def test_a_source_edit_is_read_from_the_cells_the_database_extracted():
    """_load() reads only the finding's own cell of raw and of the stored row."""
    edited = finding(raw_has=True, raw_value="failed", record_value="mixed")
    out = classify(edited, record=source_row(raw=None), t=target(outcome="mixed"))
    assert (out["state"], out["flora_source"]) == ("conflict", "source_edit")
    same = finding(raw_has=True, raw_value="failed", record_value=" failed ")
    assert classify(same, record=source_row(raw=None))["state"] == "pending"
    absent = finding(raw_has=False, raw_value=None, record_value="mixed")
    assert classify(absent, record=source_row(raw=None), t=target(outcome="mixed"))["flora_source"] != "source_edit"


def test_a_merged_row_names_the_row_flora_takes_the_value_from():
    out = classify(t=target(role="merged", outcome="uninformative"))
    assert (out["state"], out["flora_source"]) == ("conflict", "other_row")
    assert "REPL-000100" in out["flora_source_text"]
    # Even with the imported value: FLoRA reads the other row, not this one, and says
    # so rather than "as imported" beside "Changed in both places".
    out = classify(t=target(role="merged"))
    assert (out["state"], out["flora_source"]) == ("conflict", "other_row")


def test_a_build_difference_is_a_decision():
    out = classify(t=target(outcome="uninformative"))
    assert (out["state"], out["flora_source"]) == ("conflict", "build")


def test_publisher_metadata_needs_nothing_unless_a_person_changed_it():
    f = finding(sheet_column="ref_r", record_column="ref_r", value_at_import="My report",
                value_in_sheet="My report, corrected")
    out = classify(f, t=target(ref_r="Fox, A. (2020). The replication report. Journal, 1, 2."))
    assert (out["state"], out["flora_source"]) == ("metadata", "metadata")
    # No metadata: FLoRA has the sheet's reference as imported.
    assert classify(f)["state"] == "pending"
    # The meta-paper note the build appends is not a difference.
    noted = target(ref_r="My report Individual report available at https://osf.io/x")
    assert classify(f, t=noted)["state"] == "pending"


def test_year_follows_the_metadata_rule():
    f = finding(sheet_column="year", record_column="year_r", value_at_import="2019",
                value_in_sheet="2020.0")
    assert classify(f, t=target(year_r=2018.0))["state"] == "metadata"
    out = classify(f, t=target(year_r=2019.0))
    assert (out["state"], out["sheet_value_for_flora"]) == ("pending", "2020")


def test_an_abstract_the_metadata_filled_is_taken_from_the_sheet():
    f = finding(sheet_column="abstract_r", record_column="abstract_r", value_at_import=None,
                value_in_sheet="The sheet's abstract.")
    out = classify(f, t=target(abstract_r="The publisher's abstract."))
    assert (out["state"], out["sheet_value_for_flora"]) == ("pending", "The sheet's abstract.")
    imported = finding(sheet_column="abstract_r", record_column="abstract_r",
                       value_at_import="Old.", value_in_sheet="New.")
    assert classify(imported, t=target(abstract_r="Other."))["state"] == "conflict"


def test_an_originals_landing_page_counts_as_blank():
    f = finding(sheet_column="url_o", record_column="url_o", value_at_import=None,
                value_in_sheet="https://doi.org/10.1/o")
    assert classify(f)["state"] == "matches"
    other = finding(sheet_column="url_o", record_column="url_o", value_at_import=None,
                    value_in_sheet="https://osf.io/abc")
    out = classify(other)
    assert (out["state"], out["sheet_value_for_flora"]) == ("pending", "https://osf.io/abc")


def test_a_url_r_that_restates_the_doi_counts_as_blank():
    f = finding(sheet_column="url_r", record_column="url_r", value_at_import=None,
                value_in_sheet="https://doi.org/10.1/R")
    assert classify(f)["state"] == "matches"
    out = classify(f, t=target(url_r="https://osf.io/kept"))
    assert (out["sheet_value_for_flora"], out["sheet_value_error"]) == (None, None)


def test_outcomes_compare_through_outcome_alias():
    assert classify(finding(value_in_sheet="unclear"), t=target(outcome="cannot_be_determined"))["state"] == "matches"
    assert classify(finding(value_in_sheet="Successful"))["sheet_value_for_flora"] == "successful"
    out = classify(finding(value_in_sheet="worked, mostly"))
    assert out["state"] == "pending" and out["sheet_value_for_flora"] is None
    assert "outcome_alias" in out["sheet_value_error"]


def test_identifier_order_and_separators_are_not_edits():
    f = finding(sheet_column="alt_identifier_r", record_column="alt_identifier_r",
                value_at_import="a", value_in_sheet="b, A")
    assert classify(f, t=target(alt_identifier_r="a; b"))["state"] == "matches"


def test_a_rewritten_cos_quote_is_still_the_imported_one():
    shorthand = "yes marked in Replicate column"
    f = finding(sheet_column="outcome_quote", record_column="outcome_quote",
                value_at_import=shorthand, value_in_sheet="A real quote.")
    rewritten = svc.rewrite_one(shorthand)
    assert rewritten != shorthand
    assert classify(f, t=target(outcome_quote=rewritten))["state"] == "pending"


def test_reproduction_axes_and_their_aliases():
    f = finding(sheet_column="outcome_robustness", record_column="outcome_robustness",
                value_at_import="robust", value_in_sheet="robustness not checked")
    t = target(type="reproduction", outcome_robustness="robust")
    out = classify(f, source_row(type="reproduction", raw={"outcome_robustness": "robust"},
                                 outcome_robustness="robust"), t)
    assert (out["state"], out["sheet_value_for_flora"]) == ("pending", "not checked")


# ── keeps ─────────────────────────────────────────────────────────────────────

def test_a_keep_stands_while_the_sheet_says_what_it_said():
    kept = finding(kept_at=WHEN, kept_by="luke", kept_note="paper says failed",
                   kept_sheet_value=" successful ")
    assert classify(kept)["state"] == "kept"
    assert svc._kept_info(kept)["current"] is True


def test_a_keep_the_sheet_moved_past_shows_the_state_underneath():
    stale = finding(kept_at=WHEN, kept_by="luke", kept_note="x", kept_sheet_value="mixed")
    assert classify(stale)["state"] == "pending"
    assert svc._kept_info(stale) == {"kept_at": WHEN, "kept_by": "luke", "note": "x",
                                     "sheet_value": "mixed", "current": False}


# ── row-level kinds, fields FLoRA does not have, a failed build ───────────────

def test_a_status_no_longer_accepted_needs_a_decision_or_a_keep():
    f = finding(kind="not_accepted", sheet_column="validation_status",
                record_column="validation_status", value_at_import="validated - chosen",
                value_in_sheet="rejected")
    assert (classify(f)["state"], classify(f)["suggested_action"]) == ("not_validated", "decide_status")
    kept = dict(f, kept_at=WHEN, kept_by="luke", kept_sheet_value="rejected")
    assert classify(kept)["state"] == "kept"


def test_a_row_gone_from_the_sheet():
    gone = finding(kind="missing", sheet_column="", record_column=None, value_at_import=None,
                   value_in_sheet=None, reentered_as=[])
    assert (classify(gone)["state"], classify(gone)["suggested_action"]) == ("row_removed", "decide_missing")
    again = dict(gone, reentered_as=[{"record_id": "x", "display_id": "REPL-000900"}])
    assert classify(again)["suggested_action"] == "review_duplicates"


def test_columns_flora_does_not_have_are_no_field():
    status = finding(sheet_column="validation_status", record_column="validation_status",
                     value_at_import="validated - chosen", value_in_sheet="validated - changed")
    assert classify(status)["state"] == "no_field"
    study = finding(sheet_column="study_o", record_column="study_o", value_in_sheet="2")
    assert classify(study)["state"] == "no_field"


def test_a_record_whose_type_was_changed_has_no_outcome_field():
    assert classify(t=target(type="reproduction"))["state"] == "no_field"


def test_a_failed_build_is_unknown_not_an_error():
    t = target()
    t["row"] = None
    assert classify(t=t)["state"] == "unknown"


# ── fields over several records ───────────────────────────────────────────────

def test_a_field_shows_its_worst_record():
    two = [target(outcome="successful"), target(outcome="mixed", flora_id="REPL-000900", role="merged")]
    field = svc._field(finding(), source_row(), two, ALIASES)
    assert [t["state"] for t in field["targets"]] == ["matches", "conflict"]
    assert (field["state"], field["counted"]) == ("conflict", True)
    assert (field["flora_field"], field["flora_column"]) == ("outcome", "outcome")


def test_a_field_with_no_flora_record_is_not_in_flora():
    field = svc._field(finding(), source_row(), [], ALIASES)
    assert (field["state"], field["counted"], field["targets"]) == ("not_in_flora", False, [])


def test_a_resolved_field_is_resolved():
    field = svc._field(finding(resolved_at=WHEN), source_row(), [target()], ALIASES)
    assert field["state"] == "resolved" and field["targets"] == []


def test_flora_column_uses_the_published_name():
    f = finding(sheet_column="ref_o", record_column="ref_o", value_at_import="a", value_in_sheet="b")
    assert svc._field(f, source_row(), [target()], ALIASES)["flora_column"] == "apa_ref_o"


def test_not_in_flora_reasons():
    rows = {"dup": source_row(duplicate_status="duplicate", duplicate_of="rec-9")}
    reason = svc._not_in_flora(rows["dup"], [], [], {"rec-9": "REPL-000009"})
    assert (reason["reason"], reason["survivor_display_id"], reason["suggested_action"]) == \
        ("duplicate", "REPL-000009", "check_survivor")
    excluded = svc._not_in_flora(source_row(), [], [{"doi_r": "10.1/R", "reason": "not a study"}], {})
    assert excluded["reason"] == "excluded" and "not a study" in excluded["text"]
    assert svc._not_in_flora(source_row(), [{"retired": True}], [], {})["reason"] == "retired"
    assert svc._not_in_flora(source_row(), [], [], {})["reason"] == "not_built"


# ── sheet values as FLoRA stores them ─────────────────────────────────────────

@pytest.mark.parametrize("field, rule, value, expected", [
    ("year_r", "year", "2020.0", ("2020", None)),
    ("doi_r", "doi", "https://doi.org/10.1/ABC", ("10.1/abc", None)),
    ("doi_r", "doi", None, (None, None)),
    ("outcome", "outcome", "unclear", ("cannot_be_determined", None)),
])
def test_sheet_value_for_flora(field, rule, value, expected):
    ctx = svc._context(field, {"doi_r": "10.1/r"}, {}, ALIASES)
    assert svc.sheet_value_for_flora(field, rule, value, "replication", ctx) == expected


def test_a_sheet_value_flora_would_refuse_says_why():
    ctx = svc._context("doi_r", {}, {}, ALIASES)
    value, error = svc.sheet_value_for_flora("doi_r", "doi", "not a doi", "replication", ctx)
    assert value is None and "DOI" in error


# ── filters and counts ────────────────────────────────────────────────────────

def _record(*states, resolved=0):
    fields = [{"state": s, "resolved_at": None} for s in states]
    fields += [{"state": "resolved", "resolved_at": WHEN}] * resolved
    return {"fields": fields}


def test_filters_by_the_unresolved_fields():
    pending, done, gone = _record("pending", "matches"), _record("kept"), _record(resolved=1)
    assert [svc._matches(r, "needs_review") for r in (pending, done, gone)] == [True, False, False]
    assert [svc._matches(r, "nothing_to_do") for r in (pending, done, gone)] == [False, True, False]
    assert [svc._matches(r, "resolved") for r in (pending, done, gone)] == [False, False, True]
    assert [svc._matches(r, "all") for r in (pending, done, gone)] == [True, True, True]
    counts = svc._counts([pending, done, gone])
    assert (counts["needs_review"], counts["nothing_to_do"], counts["resolved"]) == (1, 1, 1)
    assert counts["fields"] == {"pending": 1, "matches": 1, "kept": 1}
    assert counts["needs_review_fields"] == 1


def test_a_record_shows_its_worst_field():
    shown = svc._shown({"fields": [{"state": "matches", "resolved_at": None},
                                   {"state": "row_removed", "resolved_at": None}]}, "open")
    assert shown["state"] == "row_removed"


# ── the CSV ───────────────────────────────────────────────────────────────────

def test_csv_columns_are_the_agent_contract():
    assert svc.CSV_COLUMNS == [
        "finding_id", "display_id", "flora_id", "flora_role", "flora_version", "source",
        "sheet_label", "sheet_row_id", "sheet_row_number", "kind", "sheet_column", "flora_field",
        "flora_column", "value_at_import", "value_in_sheet", "flora_value", "flora_value_source",
        "state", "state_explained", "suggested_action", "sheet_value_for_flora",
        "first_seen_at", "last_seen_at", "kept_by", "kept_at", "kept_note", "decision",
        "new_flora_value", "decision_note"]


def test_the_csv_has_a_bom_newlines_and_escaped_notes_only(monkeypatch):
    field = svc._field(finding(value_in_sheet="=1+1", kept_at=WHEN, kept_by="luke",
                               kept_note="=HYPERLINK(x)", kept_sheet_value="=1+1"),
                       source_row(), [target(), target(flora_id="REPL-000900", role="merged")], ALIASES)
    record = {"display_id": "REPL-000847", "source": "replications", "source_label": "Replications",
              "sheet_row_id": U1, "sheet_row_number": 245, "not_in_flora": None, "fields": [field]}
    monkeypatch.setattr(svc, "tables_exist", lambda cur: True)
    monkeypatch.setattr(svc, "_filtered", lambda cur, filters: ([record], {}, None))
    text = svc.export_csv(None, {})
    assert text.startswith("﻿") and "\r" not in text
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert len(rows) == 2                                     # one per FLoRA record
    assert rows[0]["value_in_sheet"] == "=1+1"                # data: exactly as stored
    assert rows[0]["kept_note"] == "'=HYPERLINK(x)"           # a note: defused
    assert rows[0]["state"] == "kept" and rows[1]["flora_role"] == "merged"
    assert rows[0]["decision"] == rows[0]["new_flora_value"] == rows[0]["decision_note"] == ""
    assert rows[0]["first_seen_at"] == WHEN.isoformat()
