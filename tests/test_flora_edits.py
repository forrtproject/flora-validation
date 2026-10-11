"""Changes are made to FLoRA records, never to source records (flora_edits.py).

No database: the rules on a single frame, a recording cursor for the change log,
and the routes compiled from app.py against a stub service. The database round
trip, including the write-through to flora_data, is in
tests/test_flora_edits_database.py.
"""
import ast
import inspect
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

import flora_edits
import flora_service
import source_records_service
import transform_sources
import validate_flora

ROOT = Path(__file__).resolve().parent.parent
ADMIN = {"id": 7, "handle": "luke"}


# ── what can be corrected ────────────────────────────────────────────────────

def test_identity_and_derived_values_are_never_editable():
    for record_type in ("replication", "reproduction"):
        fields = set(flora_edits.editable_fields(record_type))
        assert not fields & {"flora_id", "export_id", "source_record_id", "type", "source",
                             "doi_o_hash", "doi_r_hash", "oa_work_id_o", "oa_work_id_r",
                             "author_overlap", "author_overlap_pct"}


def test_a_reproduction_is_corrected_through_its_axes():
    """Its outcome, quote and quote source are derived from the axes."""
    reproduction = set(flora_edits.editable_fields("reproduction"))
    replication = set(flora_edits.editable_fields("replication"))
    assert not reproduction & {"outcome", "outcome_quote", "outcome_quote_source"}
    assert {"outcome_computation", "outcome_robustness"} <= reproduction
    assert not replication & set(flora_edits.AXIS_FIELDS)
    assert "outcome" in replication


def test_the_editor_is_told_the_published_column_names_and_vocabularies():
    spec = {f["field"]: f for f in flora_edits.field_spec("replication")}
    assert spec["ref_o"]["label"] == "apa_ref_o" and spec["ref_o"]["long"]
    assert "failed" in spec["outcome"]["choices"]
    assert spec["doi_o"]["choices"] is None and not spec["doi_o"]["long"]


def test_every_editable_field_is_a_column_of_the_built_frame():
    built = set(transform_sources.FLORA_COLUMNS + transform_sources.ENRICHMENT_COLUMNS)
    for record_type in ("replication", "reproduction"):
        assert not set(flora_edits.editable_fields(record_type)) - built


# ── validating a correction ──────────────────────────────────────────────────

@pytest.mark.parametrize("field, value, record_type, expected", [
    ("doi_o", " https://doi.org/10.1037/ABC ", "replication", "10.1037/abc"),
    ("doi_r", "", "replication", None),                       # deliberately blank
    ("outcome", "success", "replication", "successful"),       # retired spelling
    ("outcome_robustness", "robustness not checked", "reproduction", "not checked"),
    ("year_o", "1972", "replication", "1972"),
    ("url_r", "https://osf.io/abc", "replication", "https://osf.io/abc"),
    ("title_o", "A <i>clean</i>\ntitle", "replication", "A clean title"),
    ("author_o", '[{"family": "Smith"}]', "replication", '[{"family": "Smith"}]'),
    ("author_o", "Smith, J.; Jones, K.", "replication", "Smith, J.; Jones, K."),
])
def test_a_correction_is_stored_clean(field, value, record_type, expected):
    assert flora_edits.clean_value(field, value, record_type) == expected


@pytest.mark.parametrize("field, value, record_type, message", [
    ("doi_o", "ISBN 978-3", "replication", "must be a DOI"),
    ("outcome", "sort of worked", "replication", "outcome must be one of"),
    ("outcome", "failed", "reproduction", "cannot be corrected on a reproduction"),
    ("outcome_robustness", "robust", "replication", "cannot be corrected on a replication"),
    ("outcome_robustness", "mostly robust", "reproduction", "invalid outcome_robustness"),
    ("year_r", "0", "replication", "four-digit year"),
    ("url_o", "osf.io/abc", "replication", "web address"),
    ("author_r", '[{"family": "Smith"', "replication", "not valid JSON"),
    ("type", "reproduction", "replication", "cannot be corrected"),
])
def test_a_bad_correction_is_refused_with_the_reason(field, value, record_type, message):
    with pytest.raises(ValueError, match=message):
        flora_edits.clean_value(field, value, record_type)


# ── applying corrections to a build ──────────────────────────────────────────

def _frame():
    frame = pd.DataFrame([
        {"flora_id": "REPL-1", "export_id": "FLORA-000001", "source_record_id": "s1",
         "type": "replication", "source": "replications", "doi_o": "10.1/a",
         "title_o": "Old title", "year_o": 1972.0, "outcome": "failed",
         "author_o": '[{"family": "Smith"}]', "author_r": '[{"family": "Jones"}]'},
        {"flora_id": "REPR-1", "export_id": "FLORA-000002", "source_record_id": "s2",
         "type": "reproduction", "source": "reproductions", "doi_o": "10.1/b",
         "title_o": "Repro", "year_o": float("nan"), "outcome": "cannot_be_determined",
         "outcome_computation": None, "outcome_robustness": "robust",
         "outcome_computational_quote": None, "outcome_robustness_quote": "Robust.",
         "author_o": None, "author_r": None},
        {"flora_id": None, "export_id": None, "source_record_id": "s3", "type": "replication",
         "source": "validated", "doi_o": "10.1/c", "title_o": "Unregistered", "year_o": 2001.0,
         "outcome": "mixed", "author_o": None, "author_r": None},
    ])
    frame["author_overlap"] = pd.array([0, None, None], dtype="Int64")
    frame["author_overlap_pct"] = pd.array([0.0, None, None], dtype="Float64")
    return frame


def test_corrections_replace_the_derived_values_by_published_id():
    out = flora_edits.apply(None, _frame(), {
        "FLORA-000001": {"title_o": "New title", "year_o": "1975", "outcome": None},
        "FLORA-999999": {"title_o": "nowhere to go"},
    })
    row = out.set_index("source_record_id").loc["s1"]
    assert (row["title_o"], row["year_o"]) == ("New title", "1975")
    assert pd.isna(row["outcome"])                  # a blank correction blanks the field
    assert row["edited_fields"] == "outcome title_o year_o"
    assert out.attrs["flora_edits"] == {"FLORA-000001": {"title_o": "Old title", "year_o": "1972",
                                                         "outcome": "failed"}}
    assert out.set_index("source_record_id")["edited_fields"].isna()[["s2", "s3"]].all()


def test_the_input_frame_is_left_alone():
    frame = _frame()
    flora_edits.apply(None, frame, {"FLORA-000001": {"title_o": "New title"}})
    assert frame.loc[0, "title_o"] == "Old title" and "edited_fields" not in frame


def test_values_computed_from_a_corrected_field_follow_it():
    out = flora_edits.apply(None, _frame(), {
        "FLORA-000001": {"author_r": '[{"family": "Smith"}]'},
        "FLORA-000002": {"outcome_computation": "computationally reproducible",
                         "outcome_computational_quote": "It ran."},
    }).set_index("flora_id")
    assert out.loc["REPL-1", "author_overlap"] == 1
    assert out.loc["REPL-1", "author_overlap_pct"] == 100.0
    assert out.loc["REPR-1", "outcome"] == "computationally reproducible, robust"
    assert out.loc["REPR-1", "outcome_quote"] == "It ran. || Robust."


def test_a_correction_the_record_type_does_not_allow_is_ignored():
    """The type can change upstream after a correction was stored."""
    out = flora_edits.apply(None, _frame(), {"FLORA-000002": {"outcome": "failed"}})
    assert out.set_index("flora_id").loc["REPR-1", "outcome"] == "cannot_be_determined"
    assert out.attrs["flora_edits"] == {}


def test_an_identifier_correction_reports_the_value_from_before_the_metadata_join():
    frame = _frame()
    frame.attrs["identifier_edits"] = {"s1": {"doi_o": "10.1/original"}}
    out = flora_edits.apply(None, frame, {"FLORA-000001": {"doi_o": "10.1/a"}})
    assert out.attrs["flora_edits"]["FLORA-000001"] == {"doi_o": "10.1/original"}


def _identifiers(*records, primaries=()):
    return {"records": list(records), "primaries": set(primaries)}


def test_identifiers_go_to_the_row_of_the_records_primary_source():
    df = pd.DataFrame({"record_id": ["s1", "s2"], "doi_r": ["10.1/x", "10.1/y"],
                       "url_r": [None, None]})
    replaced = flora_edits.apply_identifiers(df, _identifiers(
        (["s2"], {"doi_r": "10.1/fixed", "url_r": "https://osf.io/r"}),
        (["gone"], {"doi_r": "10.1/z"}), primaries={"s1", "s2", "gone"}))
    assert df["doi_r"].tolist() == ["10.1/x", "10.1/fixed"]
    assert df["url_r"].tolist()[1] == "https://osf.io/r"
    assert replaced == {"s2": {"doi_r": "10.1/y", "url_r": None}}


def test_identifiers_follow_a_record_to_its_new_survivor():
    """A duplicate ruling changed the survivor; the registry still names the old
    primary until the refresh that follows this build."""
    df = pd.DataFrame({"record_id": ["s2", "s3"], "doi_r": ["10.1/y", "10.1/z"]})
    replaced = flora_edits.apply_identifiers(df, _identifiers(
        (["s1", "s2"], {"doi_r": "10.1/fixed"}), primaries={"s1", "s3"}))
    assert df["doi_r"].tolist() == ["10.1/fixed", "10.1/z"]
    assert replaced == {"s2": {"doi_r": "10.1/y"}}


def test_a_records_own_row_is_never_given_to_another_record():
    df = pd.DataFrame({"record_id": ["s2"], "doi_r": ["10.1/y"]})
    replaced = flora_edits.apply_identifiers(df, _identifiers(
        (["s1", "s2"], {"doi_r": "10.1/old-record"}),      # s2 was absorbed into it once
        (["s2"], {"doi_r": "10.1/own"}), primaries={"s1", "s2"}))
    assert df["doi_r"].tolist() == ["10.1/own"] and list(replaced) == ["s2"]


def test_a_placeholder_doi_is_reported_as_the_blank_it_is_published_as():
    df = pd.DataFrame({"record_id": ["s1"], "doi_o": ["DUMMY_123"]})
    replaced = flora_edits.apply_identifiers(df, _identifiers((["s1"], {"doi_o": "10.1/real"})))
    assert replaced == {"s1": {"doi_o": None}}


def test_an_empty_build_needs_no_database():
    assert flora_edits.apply(None, pd.DataFrame()).empty


# ── the change log ────────────────────────────────────────────────────────────

class _Cur:
    """A record's change log in memory: answers the editor's reads, appends rows."""

    def __init__(self, log=None, exclusions=()):
        self.log = list(log or [])                 # (change_id, corrections), oldest first
        self.exclusions = list(exclusions)
        self.executed = []
        self._result = None

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.executed.append((sql, params))
        if "MAX(change_id)" in sql:
            self._result = [{"v": self.log[-1][0] if self.log else 0}]
        elif sql.startswith("SELECT corrections FROM flora_record_changes"):
            wanted = params[0]
            self._result = [{"corrections": c} for i, c in self.log if i == wanted]
        elif "FROM transform_exclusions" in sql:
            self._result = self.exclusions
        elif sql.startswith("INSERT INTO flora_record_changes"):
            change_id = (self.log[-1][0] if self.log else 0) + 1
            self.log.append((change_id, params[6].adapted))
            self._result = [{"change_id": change_id}]
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def appended(self):
        return [params for sql, params in self.executed
                if sql.startswith("INSERT INTO flora_record_changes")]


RECORD = {"flora_id": "REPL-1", "export_id": "FLORA-000001", "type": "replication",
          "title_o": "Shown title", "outcome": "failed", "year_o": 1972.0,
          "doi_o": "not a doi at all"}


def test_a_save_is_one_row_of_the_log_with_a_note_on_each_change():
    cur = _Cur()
    result = flora_edits.save(cur, RECORD, {}, {"title_o": "Better title", "outcome": "mixed"},
                              [], 0, ADMIN, {"title_o": " typo ", "outcome": "per the paper",
                                             "doi_o": "a note on a field left as it was"})
    assert result == {"export_id": "FLORA-000001", "change_id": 1,
                      "changed_fields": ["outcome", "title_o"], "reverted_fields": [],
                      "version": 1}
    [row] = cur.appended()
    export_id, admin_id, handle, action, note, changes, corrections, restored_from, notes = row
    assert (export_id, admin_id, handle, action, note) == ("FLORA-000001", 7, "luke", "edit", None)
    assert changes.adapted == {"title_o": ["Shown title", "Better title"],
                               "outcome": ["failed", "mixed"]}
    assert notes.adapted == {"title_o": "typo", "outcome": "per the paper"}
    assert corrections.adapted == {"title_o": "Better title", "outcome": "mixed"}
    assert restored_from is None
    assert not any("source_records" in sql for sql, _ in cur.executed)


def test_a_change_may_go_without_a_note():
    cur = _Cur()
    flora_edits.save(cur, RECORD, {}, {"title_o": "Better title"}, [], 0, ADMIN)
    assert cur.appended()[0][8].adapted == {}


@pytest.mark.parametrize("notes, message", [
    ({"title_o": "x" * 501}, "longer than 500"),
    ({"title_o": 5}, "must be text"),
])
def test_a_note_that_cannot_be_kept_is_refused(notes, message):
    cur = _Cur()
    with pytest.raises(ValueError, match=message):
        flora_edits.save(cur, RECORD, {}, {"title_o": "Better title"}, [], 0, ADMIN, notes)
    assert cur.appended() == []


def test_untouched_fields_are_neither_validated_nor_logged():
    """The record shows a dirty DOI; sending it back unchanged must not fail."""
    cur = _Cur()
    result = flora_edits.save(cur, RECORD, {}, {"doi_o": "not a doi at all", "year_o": "1972"},
                              [], 0, ADMIN)
    assert result["change_id"] is None and cur.appended() == []


def test_a_stale_version_is_refused_before_anything_is_logged():
    cur = _Cur([(6, {}), (7, {"title_o": "Theirs"})])
    with pytest.raises(flora_edits.VersionConflict) as caught:
        flora_edits.save(cur, RECORD, {}, {"title_o": "Mine"}, [], 6, ADMIN)
    assert caught.value.current_version == 7 and cur.appended() == []


def test_typing_the_derived_value_back_drops_the_correction():
    cur = _Cur([(1, {"title_o": "Shown title"})])
    result = flora_edits.save(cur, RECORD, {"title_o": "Derived title"},
                              {"title_o": "Derived title"}, [], 1, ADMIN)
    assert result["reverted_fields"] == ["title_o"]
    [row] = cur.appended()
    assert row[5].adapted == {"title_o": ["Shown title", "Derived title"]}
    assert row[6].adapted == {}


@pytest.mark.parametrize("sent", [
    "Line one\nline two",                         # a textarea writes \r\n as \n
    "Line oneline two",                            # a one-line input drops the break
])
def test_line_breaks_alone_never_make_a_field_changed(sent):
    record = {**RECORD, "outcome_quote": "Line one\r\nline two"}
    cur = _Cur()
    result = flora_edits.save(cur, record, {}, {"outcome_quote": sent}, [], 0, ADMIN)
    assert result["change_id"] is None and cur.appended() == []


def test_dropping_a_correction_the_derived_value_has_caught_up_with_is_reported():
    """No value moves, but the field follows the source again from now on."""
    cur = _Cur([(1, {"title_o": "Same"})])
    result = flora_edits.save(cur, {**RECORD, "title_o": "Same"}, {"title_o": "Same"},
                              {}, ["title_o"], 1, ADMIN)
    assert result["reverted_fields"] == ["title_o"] and result["change_id"] == 2
    assert cur.appended()[0][5].adapted == {"title_o": ["Same", "Same"]}


def test_a_correction_onto_an_excluded_identifier_is_refused_with_the_reason():
    exclusion = {"doi_o": None, "doi_r": "10.1/not-a-replication", "url_r": None,
                 "reason": "commentary"}
    cur = _Cur(exclusions=[exclusion])
    with pytest.raises(ValueError, match=r"exclusion list.*\(commentary\)"):
        flora_edits.save(cur, RECORD, {}, {"doi_r": "https://doi.org/10.1/NOT-a-replication"},
                         [], 0, ADMIN)
    assert cur.appended() == []
    # Other fields are not checked against exclusions at all.
    flora_edits.save(_Cur(exclusions=[exclusion]), {**RECORD, "doi_r": "10.1/not-a-replication"},
                     {}, {"title_o": "Fine"}, [], 0, ADMIN)


@pytest.mark.parametrize("exclusion, identifiers, excluded", [
    ({"doi_o": None, "doi_r": "10.1/x", "url_r": None}, ("10.1/o", "10.1/X", None), True),
    ({"doi_o": "10.1/o", "doi_r": "10.1/x", "url_r": None}, ("10.1/o", "10.1/x", None), True),
    ({"doi_o": "10.1/o", "doi_r": "10.1/x", "url_r": None}, ("10.1/other", "10.1/x", None), False),
    ({"doi_o": None, "doi_r": None, "url_r": "https://OSF.io/abc/"}, ("10.1/o", None, "osf.io/abc"), True),
    ({"doi_o": None, "doi_r": "10.1/x", "url_r": None}, ("10.1/o", None, None), False),
])
def test_exclusions_match_as_the_build_matches_them(exclusion, identifiers, excluded):
    assert (transform_sources.matching_exclusion([exclusion], *identifiers) is not None) == excluded


def test_reverting_drops_only_a_correction_in_force():
    cur = _Cur([(1, {"title_o": "Shown title"})])
    result = flora_edits.save(cur, RECORD, {"title_o": None}, {}, ["title_o", "outcome"], 1, ADMIN)
    assert result["reverted_fields"] == ["title_o"]
    assert cur.appended()[0][5].adapted == {"title_o": ["Shown title", None]}


def test_a_restore_puts_back_the_state_right_after_that_change():
    shown = {**RECORD, "title_o": "Third", "outcome": "mixed"}
    derived = {"title_o": "Derived", "outcome": "successful"}
    cur = _Cur([(1, {"title_o": "First"}),
                (2, {"title_o": "Second", "outcome": "mixed"}),
                (3, {"title_o": "Third", "outcome": "mixed"})])
    result = flora_edits.restore(cur, shown, derived, 1, 3, ADMIN, "wrong paper")
    assert result["change_id"] == 4
    assert result["changed_fields"] == ["title_o"] and result["reverted_fields"] == ["outcome"]
    [row] = cur.appended()
    assert row[3:5] == ("restore", "wrong paper") and row[7] == 1 and row[8].adapted == {}
    assert row[5].adapted == {"title_o": ["Third", "First"], "outcome": ["mixed", "successful"]}
    assert row[6].adapted == {"title_o": "First"}


def test_restoring_the_original_drops_every_correction():
    cur = _Cur([(1, {"title_o": "First"})])
    result = flora_edits.restore(cur, {**RECORD, "title_o": "First"}, {"title_o": "Derived"},
                                 0, 1, ADMIN)
    assert result["reverted_fields"] == ["title_o"]
    assert cur.appended()[0][6].adapted == {} and cur.appended()[0][7] is None


def test_restoring_the_current_state_logs_nothing():
    cur = _Cur([(1, {"title_o": "First"})])
    result = flora_edits.restore(cur, {**RECORD, "title_o": "First"}, {"title_o": "Derived"},
                                 1, 1, ADMIN)
    assert result["change_id"] is None and cur.appended() == []


def test_a_change_of_another_record_cannot_be_restored():
    with pytest.raises(flora_edits.RecordNotFound):
        flora_edits.restore(_Cur([(1, {})]), RECORD, {}, 99, 1, ADMIN)


@pytest.mark.parametrize("fields, revert, message", [
    ({"outcome_robustness": "robust"}, [], "Cannot be corrected on a replication"),
    ({"flora_id": "REPL-2"}, [], "Cannot be corrected"),
    ({"title_o": "x"}, ["title_o"], "changed and reverted"),
])
def test_a_save_that_makes_no_sense_is_refused(fields, revert, message):
    cur = _Cur()
    with pytest.raises(ValueError, match=message):
        flora_edits.save(cur, RECORD, {}, fields, revert, 0, ADMIN)
    assert cur.executed == []


def test_a_row_without_a_published_id_cannot_be_changed():
    with pytest.raises(ValueError, match="no FLoRA id yet"):
        flora_edits.save(_Cur(), {**RECORD, "export_id": None}, {}, {"title_o": "x"}, [], 0, ADMIN)


def test_a_save_requires_the_version_it_was_loaded_at():
    with pytest.raises(ValueError, match="version is required"):
        flora_edits.save(_Cur(), RECORD, {}, {"title_o": "x"}, [], None, ADMIN)


def test_the_corrections_in_force_are_each_records_newest_row():
    cur = Mock()
    cur.fetchone.return_value = {"present": True}
    cur.fetchall.return_value = [{"export_id": "FLORA-000001", "corrections": {"title_o": "T"}},
                                 {"export_id": "FLORA-000002", "corrections": {}}]
    assert flora_edits.load(cur) == {"FLORA-000001": {"title_o": "T"}}
    sql = " ".join(cur.execute.call_args[0][0].split())
    assert "DISTINCT ON (export_id)" in sql and "ORDER BY export_id, change_id DESC" in sql


def test_corrections_are_skipped_while_the_table_does_not_exist_yet():
    """The nightly job can reach the database before the site has created it."""
    cur = Mock()
    cur.fetchone.return_value = {"present": False}
    assert flora_edits.load(cur) == {} and flora_edits.load_identifiers(cur) == {}
    assert flora_edits.identifier_values(cur, ("doi_o",)) == []
    assert cur.execute.call_count == 3


def test_the_log_is_append_only_and_tied_to_the_published_id():
    schema = (ROOT / "db_schema.sql").read_text(encoding="utf-8")
    table = schema[schema.index("CREATE TABLE IF NOT EXISTS flora_record_changes"):]
    assert "REFERENCES flora_records(export_id)" in table[:table.index(");")]
    assert "BEFORE UPDATE OR DELETE ON flora_record_changes" in table
    assert "BEFORE TRUNCATE ON flora_record_changes" in table
    assert "flora_record_overrides" not in schema and "flora_record_edits" not in schema


# ── the FLoRA tab ─────────────────────────────────────────────────────────────

def test_the_edited_filter_and_count_find_corrected_records():
    out = flora_edits.apply(None, _frame(), {"FLORA-000001": {"title_o": "New title"}})
    assert flora_service._apply_filters(out, {"edited": True})["flora_id"].tolist() == ["REPL-1"]
    assert flora_service.counts(out)["edited"] == 1
    assert flora_service._apply_filters(_frame(), {"edited": True}).empty


def test_the_cache_signature_sees_every_change():
    """Exactly which changes are visible: a count and the newest id can be matched by
    a different set after a save rolls back."""
    probe = inspect.getsource(flora_service._signature)
    assert "string_agg(change_id::text, ',' ORDER BY change_id))" in probe
    assert "FROM flora_record_changes) AS edits_signature" in probe
    assert "edits" in flora_service._Signature._fields


def test_changing_a_record_the_tab_does_not_have_is_not_found(monkeypatch):
    monkeypatch.setattr(flora_service, "dataset", lambda cur: _frame())
    with pytest.raises(flora_service.RecordNotFound):
        flora_service.update_record(None, "NOPE-1", {"title_o": "x"}, [], 0, ADMIN)
    with pytest.raises(flora_service.RecordNotFound):
        flora_service.restore_record(None, "NOPE-1", 0, 0, ADMIN)
    assert flora_service.editing(None, {"export_id": None}) is None


def test_a_change_is_written_through_with_what_follows_from_it(monkeypatch):
    """The record is read again after the change, so a title brought in by a
    corrected DOI is part of what reaches flora_data."""
    frames = iter([({"export_id": "FLORA-000001", "title_o": "Old"}, {}),
                   ({"export_id": "FLORA-000001", "title_o": "New"}, {"doi_o": "10.1/x"})])
    monkeypatch.setattr(flora_service, "_find", lambda cur, flora_id: next(frames))
    published = []
    monkeypatch.setattr(flora_edits, "publish",
                        lambda cur, before, after: published.append((before, after)) or True)
    monkeypatch.setattr(flora_edits, "save", lambda *a, **k: {"change_id": 3})
    result = flora_service.update_record(None, "REPL-1", {"doi_o": "10.1/x"}, [], 2, ADMIN)
    assert result == {"change_id": 3, "published": True, "warnings": []}
    assert [(b["title_o"], a["title_o"]) for b, a in published] == [("Old", "New")]


def test_a_corrected_doi_shared_with_another_record_is_flagged_not_merged(monkeypatch):
    frame = _frame()
    frame.loc[1, ["type", "doi_o", "doi_r", "url_r"]] = ["replication", "10.1/a", "10.1/r", None]
    frame.loc[0, ["doi_r", "url_r"]] = ["10.1/r", None]
    after = {**frame.iloc[0].to_dict()}
    monkeypatch.setattr(flora_service, "_find", lambda cur, flora_id: (after, {}))
    monkeypatch.setattr(flora_service, "dataset", lambda cur: frame)
    monkeypatch.setattr(flora_edits, "publish", lambda *a: True)
    monkeypatch.setattr(flora_edits, "save", lambda *a, **k: {
        "change_id": 3, "changed_fields": ["doi_r"], "reverted_fields": []})
    result = flora_service.update_record(None, "REPL-1", {"doi_r": "10.1/r"}, [], 2, ADMIN)
    [warning] = result["warnings"]
    assert warning.startswith("REPR-1 has the same original and replication identifiers")


def test_no_duplicate_warning_without_identifiers_or_for_other_fields():
    frame = _frame()
    frame["doi_o"], frame["doi_r"], frame["url_r"] = None, None, None
    frame["type"] = "replication"
    assert flora_edits.duplicate_warnings(frame, frame.iloc[0].to_dict()) == []


def test_nothing_is_written_through_when_nothing_was_logged(monkeypatch):
    monkeypatch.setattr(flora_service, "_find", lambda cur, flora_id: ({"export_id": "F"}, {}))
    monkeypatch.setattr(flora_edits, "publish", lambda *a: pytest.fail("nothing to publish"))
    monkeypatch.setattr(flora_edits, "save", lambda *a, **k: {"change_id": None})
    assert flora_service.update_record(None, "F", {}, [], 0, ADMIN)["published"] is False


def test_every_product_read_applies_the_corrections_after_the_ids():
    """The tab, the published CSV and the validation report must agree."""
    for source in (inspect.getsource(flora_service._current),
                   inspect.getsource(transform_sources.run),
                   inspect.getsource(validate_flora.load_dataset)):
        assert "flora_edits.apply(cur," in source
        assert source.index("attach_ids(cur") < source.index("flora_edits.apply(cur,")
    run = inspect.getsource(transform_sources.run)
    assert run.index("flora_edits.apply(cur,") < run.index("to_output_shape(out)")


def test_corrected_identifiers_come_after_every_duplicate_step_and_are_joined_again():
    """So a correction can never drop, merge or hide a record, and the metadata
    still follows a corrected DOI."""
    build = inspect.getsource(transform_sources.build)
    position = build.index("flora_edits.apply_identifiers(")
    for step in ("# 5 ── dedup", "merge_doi_pair_dups(", "preprint_dedup.resolve("):
        assert build.index(step) < position
    assert position < build.index("join_metadata(part, meta") < build.index("# 8 ── text cleaning")


def test_writing_through_moves_the_revision_the_public_api_caches_on():
    publish = inspect.getsource(flora_edits.publish)
    assert "UPDATE flora_data_metadata SET updated_at = NOW()" in publish
    assert "WHERE id = %s AND retired_at IS NULL" in publish


# ── source records stay as imported ──────────────────────────────────────────

def test_source_records_can_no_longer_be_edited():
    assert not hasattr(source_records_service, "update_record")
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '@app.patch("/api/admin/source-records' not in app_source
    assert '"/api/admin/source-records/vocabularies"' not in app_source


# ── routes ────────────────────────────────────────────────────────────────────

ROUTES = ("admin_flora_record", "FloraRecordUpdate", "_flora_change",
          "admin_flora_record_update", "FloraRecordRestore", "admin_flora_record_restore")


class _NotFound(LookupError):
    pass


class _Conflict(Exception):
    def __init__(self, current_version):
        super().__init__("conflict")
        self.current_version = current_version


@pytest.fixture
def flora_api():
    app = FastAPI()
    service = SimpleNamespace(get_record=Mock(), merged_sources=Mock(return_value=[]),
                              editing=Mock(), update_record=Mock(), restore_record=Mock(),
                              RecordNotFound=_NotFound, VersionConflict=_Conflict)

    def current_admin():
        raise HTTPException(401, "Admin sign-in required")

    @contextmanager
    def db():
        yield "isolated-cursor"

    source = ROOT / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in ROUTES]
    assert {node.name for node in nodes} == set(ROUTES)
    namespace = {"app": app, "Depends": Depends, "HTTPException": HTTPException,
                 "BaseModel": BaseModel, "Field": Field, "current_admin": current_admin,
                 "db": db, "flora_service": service}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    app.dependency_overrides[current_admin] = lambda: {"id": 7, "handle": "test-admin"}
    return SimpleNamespace(app=app, client=TestClient(app), service=service)


URL = "/api/admin/flora/REPL-000001"


def test_changing_requires_an_admin(flora_api):
    flora_api.app.dependency_overrides.clear()
    assert flora_api.client.patch(URL, json={"fields": {}, "version": 0}).status_code == 401
    assert flora_api.client.post(URL + "/restore", json={"change_id": 1, "version": 0}).status_code == 401
    flora_api.service.update_record.assert_not_called()
    flora_api.service.restore_record.assert_not_called()


def test_a_change_is_logged_under_the_admin_with_a_note_per_field(flora_api):
    flora_api.service.update_record.return_value = {"changed_fields": ["title_o"]}
    response = flora_api.client.patch(URL, json={
        "fields": {"title_o": "T"}, "revert": ["outcome"], "version": 3,
        "notes": {"title_o": "typo", "outcome": "back to the sheet"}})
    assert response.status_code == 200
    flora_api.service.update_record.assert_called_once_with(
        "isolated-cursor", "REPL-000001", {"title_o": "T"}, ["outcome"], 3,
        {"id": 7, "handle": "test-admin"}, {"title_o": "typo", "outcome": "back to the sheet"})


def test_a_restore_names_the_change_and_the_version(flora_api):
    flora_api.service.restore_record.return_value = {"change_id": 5}
    response = flora_api.client.post(URL + "/restore", json={"change_id": 2, "version": 4,
                                                              "note": "undo"})
    assert response.status_code == 200
    flora_api.service.restore_record.assert_called_once_with(
        "isolated-cursor", "REPL-000001", 2, 4, {"id": 7, "handle": "test-admin"}, "undo")
    assert flora_api.client.post(URL + "/restore", json={"change_id": -1, "version": 4}).status_code == 422


def test_a_change_without_its_version_is_refused(flora_api):
    assert flora_api.client.patch(URL, json={"fields": {"title_o": "T"}}).status_code == 422
    assert flora_api.client.post(URL + "/restore", json={"change_id": 1}).status_code == 422
    assert flora_api.client.patch(URL, json={"fields": {}, "version": 0,
                                             "notes": {"title_o": ["not", "text"]}}).status_code == 422
    flora_api.service.update_record.assert_not_called()


def test_change_errors_become_client_errors(flora_api):
    body = {"fields": {"title_o": "T"}, "version": 1}
    flora_api.service.update_record.side_effect = _NotFound()
    assert flora_api.client.patch(URL, json=body).status_code == 404
    flora_api.service.update_record.side_effect = _Conflict(5)
    response = flora_api.client.patch(URL, json=body)
    assert response.status_code == 409 and response.headers["X-Current-Version"] == "5"
    flora_api.service.update_record.side_effect = ValueError("year_o must be a four-digit year")
    response = flora_api.client.patch(URL, json=body)
    assert response.status_code == 400 and "four-digit" in response.json()["detail"]
    flora_api.service.restore_record.side_effect = _NotFound()
    assert flora_api.client.post(URL + "/restore", json={"change_id": 9, "version": 1}).status_code == 404


def test_the_detail_carries_what_the_editor_needs(flora_api):
    flora_api.service.get_record.return_value = {"flora_id": "REPL-000001"}
    flora_api.service.editing.return_value = {"version": 2, "fields": [], "history": []}
    body = flora_api.client.get(URL).json()
    assert body["editing"]["version"] == 2 and body["merged_sources"] == []


def test_an_empty_build_with_string_titles_drops_nothing_and_does_not_raise():
    """Every row excluded leaves str-typed empty columns, which & cannot combine."""
    empty = pd.DataFrame({"title_o": pd.Series([], dtype="str"),
                          "title_r": pd.Series([], dtype="str")})
    assert transform_sources.drop_untitled(empty).empty
