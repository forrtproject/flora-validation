"""Sheet edits end to end against PostgreSQL (opt-in, FLORA_TEST_DATABASE_URL).

The schema applies twice and keeps row-level security on; a finding's lifecycle
across checks (new, unchanged, resolved, re-opened with its first sighting and its
decision kept); "Keep FLoRA value" and its withdrawal; and the view following the
FLoRA record: a correction makes a pending sheet edit a decision, and correcting it
to the sheet's value leaves nothing to do.
"""
import csv
import hashlib
import io
from pathlib import Path

import pytest
import yaml
from psycopg2.extras import Json, RealDictCursor

import flora_service
import sheet_drift_service as svc
import sync_sources as ss
from tests.integration.test_flora_edits_database import _pipeline, offline  # noqa: F401
from tests.pg_fixtures import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
REPL = next(s for s in REGISTRY["sources"] if s["key"] == "replications")
U1 = "11111111-1111-4111-8111-111111111111"
ORIGINAL, REPORT = "10.9999/original.1", "10.9999/report.1"
LUKE = {"id": None, "handle": "luke"}


def _sheet_row(**cells):
    row = {column: "" for column in REPL["expected_columns"]}
    row.update({"id": U1, "doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "failed",
                "outcome_quote": "It did not replicate.", "validation_status": "validated - chosen",
                "ref_o": "The original", "ref_r": "The report"})
    row.update(cells)
    return row


def _payload(**cells) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=REPL["expected_columns"])
    writer.writeheader()
    writer.writerow(_sheet_row(**cells))
    return out.getvalue().encode("utf-8")


def _check(connection, payload: bytes, digest=None):
    """One sync's sheet-edit check, on the sync's own (tuple) cursor."""
    df = ss._parse(payload)
    with connection, connection.cursor() as cur:
        return ss._drift_check(cur, dict(REPL), REGISTRY, df,
                               digest or hashlib.sha256(payload).hexdigest(), None, False)


def _rows(connection, sql="SELECT * FROM sheet_drift_findings ORDER BY finding_id", *params):
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


@pytest.fixture
def imported(local_database, offline):  # noqa: F811
    """One imported replication with its raw row, built into FLoRA."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        raw = _sheet_row()
        cur.execute(
            """INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o, doi_r,
                   ref_o, ref_r, outcome, outcome_quote, validation_status, raw)
               VALUES ('replications', %s, 'REPL-000001', 'replication', %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING record_id::text AS record_id""",
            (U1, ORIGINAL, REPORT, raw["ref_o"], raw["ref_r"], raw["outcome"], raw["outcome_quote"],
             raw["validation_status"], Json(raw)))
        record_id = cur.fetchone()["record_id"]
        for doi, title in ((ORIGINAL, "The original study"), (REPORT, "The replication report")):
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, title))
    _pipeline(local_database)
    return local_database, record_id


def test_the_tables_apply_twice_with_row_level_security(local_database):
    rows = _rows(local_database, "SELECT relname, relrowsecurity FROM pg_class "
                                 "WHERE relname IN ('sheet_drift_runs', 'sheet_drift_findings') ORDER BY relname")
    assert rows == [{"relname": "sheet_drift_findings", "relrowsecurity": True},
                    {"relname": "sheet_drift_runs", "relrowsecurity": True}]


def test_a_finding_lives_across_checks(imported):
    connection, record_id = imported
    edited = _payload(outcome="successful")

    first = _check(connection, edited)
    assert (first["status"], first["new"], first["open"]) == ("checked", 1, 1)
    [finding] = _rows(connection)
    assert (finding["kind"], finding["sheet_column"], finding["value_at_import"],
            finding["value_in_sheet"], finding["sheet_row_number"]) == \
        ("edited", "outcome", "failed", "successful", 2)
    assert str(finding["record_id"]) == record_id

    # The same payload under the same rules is not checked again.
    assert _check(connection, edited)["status"] == "not_due"
    # Checked again anyway (another digest), it finds the same thing: no flood.
    again = _check(connection, edited, digest="0" * 64)
    assert (again["new"], again["unchanged"], again["open"]) == (0, 1, 1)

    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        svc.keep(cur, finding["finding_id"], LUKE, "the paper says it failed", "successful")

    back = _check(connection, _payload())
    assert (back["resolved"], back["open"]) == (1, 0)
    [resolved] = _rows(connection)
    assert resolved["resolved_at"] is not None and resolved["resolved_drift_run_id"] is not None

    reopened = _check(connection, edited)
    assert reopened["reopened"] == 1
    [row] = _rows(connection)
    assert row["resolved_at"] is None
    assert row["first_seen_at"] == finding["first_seen_at"]
    assert (row["kept_by"], row["kept_sheet_value"]) == ("luke", "successful")

    runs = _rows(connection, "SELECT counts, rules_version FROM sheet_drift_runs ORDER BY drift_run_id")
    assert len(runs) == 4 and runs[-1]["counts"]["reopened"] == 1


def test_a_row_gone_from_the_sheet_and_back(imported):
    connection, _ = imported
    other = _payload(id="22222222-2222-4222-8222-222222222222")
    assert _check(connection, other)["missing"] == 1
    [gone] = _rows(connection)
    assert (gone["kind"], gone["sheet_column"]) == ("missing", "")
    assert _check(connection, _payload())["resolved"] == 1


def test_the_view_follows_the_flora_record(imported):
    connection, record_id = imported
    _check(connection, _payload(outcome="successful"))

    def view(state="needs_review"):
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            return svc.list_findings(cur, {"state": state})

    listed = view()
    assert listed["counts"]["needs_review"] == 1 and listed["flora_error"] is None
    [record] = listed["records"]
    assert (record["display_id"], record["source_label"], record["sheet_row_number"]) == \
        ("REPL-000001", "Replications", 2)
    [field] = record["fields"]
    [target] = field["targets"]
    assert (field["state"], target["state"], target["flora_value"], target["sheet_value_for_flora"]) == \
        ("pending", "pending", "failed", "successful")
    flora_id, version = target["flora_id"], target["version"]

    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        flora_service.update_record(cur, flora_id, {"outcome": "mixed"}, [], version, LUKE,
                                    {"outcome": "read the paper again"})
    [field] = view()["records"][0]["fields"]
    [target] = field["targets"]
    assert (target["state"], target["flora_source"]) == ("conflict", "correction")
    assert "luke" in target["flora_source_text"] and "read the paper again" in target["flora_source_text"]

    # Keep FLoRA's value: refused for a stale page, then kept, then withdrawn.
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        with pytest.raises(svc.StaleFinding):
            svc.keep(cur, field["finding_id"], LUKE, "mixed is right", "something else")
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        svc.keep(cur, field["finding_id"], LUKE, "mixed is right", "successful")
        assert svc.record_view(cur, record_id)["fields"][0]["state"] == "kept"
    assert view()["counts"]["needs_review"] == 0
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        with pytest.raises(ValueError, match="already kept"):
            svc.keep(cur, field["finding_id"], LUKE, "again", "successful")
        undone = svc.undo_keep(cur, field["finding_id"], LUKE)
    assert undone["withdrawn"]["kept_note"] == "mixed is right"
    assert view()["counts"]["needs_review"] == 1

    # The editor's box for the same record.
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        record = flora_service.get_record(cur, flora_id)
        box = svc.editor_box(cur, record)
    assert [f["state"] for f in box["fields"]] == ["conflict"]
    assert box["rows"][0]["display_id"] == "REPL-000001"

    # Corrected to the sheet's value: nothing left to do.
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        current = flora_service.editing(cur, flora_service.get_record(cur, flora_id))["version"]
        flora_service.update_record(cur, flora_id, {"outcome": "successful"}, [], current, LUKE,
                                    {"outcome": "Taken from Replications row 2"})
    listed = view("nothing_to_do")
    assert listed["records"][0]["fields"][0]["state"] == "matches"
    assert view()["counts"]["needs_review"] == 0
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        with pytest.raises(ValueError, match="Nothing to decide"):
            svc.keep(cur, field["finding_id"], LUKE, "no need", "successful")
        text = svc.export_csv(cur, {"state": "all"})
    [row] = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert (row["state"], row["flora_value"], row["value_in_sheet"]) == ("matches", "successful", "successful")


def test_an_old_source_edit_is_named_from_the_database(imported):
    """The cell edited by hand in Source Records before corrections moved to FLoRA:
    read from raw and the stored row in SQL, not by downloading either whole."""
    connection, record_id = imported
    _check(connection, _payload(outcome="successful"))
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("UPDATE source_records SET outcome = 'mixed', updated_at = NOW() WHERE record_id = %s",
                    (record_id,))
    flora_service.invalidate()
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        [target] = svc.record_view(cur, record_id)["fields"][0]["targets"]
    assert (target["state"], target["flora_source"], target["flora_value"]) == \
        ("conflict", "source_edit", "mixed")
