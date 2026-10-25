"""The entry checks' findings in the FLoRA tab: the Problems and Possible problems
filters and counts, the record view's findings, and "Not a problem".

The findings are real: a small dataset stored in a throwaway PostgreSQL database
(FLORA_TEST_DATABASE_URL, as tests/test_entry_checks.py) and checked by
entry_checks.run_structural. The tab's frame is that dataset, handed to
flora_service in place of a build from source records. The routes and the cross-site
middleware are compiled from app.py on their own, as in test_flora_edits and
test_flora_public_api: importing app.py would initialise a database.
"""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

import entry_checks
import flora_public_api
import flora_service
import flora_store
import security_events
import validate_flora
from tests.test_entry_checks import TYPE_KEY, _row, _store, db  # noqa: F401
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
YEAR_KEY = validate_flora.finding_key("year_order", ["REPL-000003"], "year_r", "2011")
ROWS = [_row(1), _row(2, type="extension"), _row(3, year_o="2015", year_r="2011")]


def _frame(rows=ROWS):
    """The tab's frame for these rows, as dataset() gives it: the published
    columns with the FLoRA ID."""
    return pd.DataFrame(rows, columns=[*flora_store.CSV_COLUMNS, "flora_id"])


def _checked(conn, rows=ROWS):
    _store(conn, *rows)
    return entry_checks.run_structural(conn)


# ── filters and counts ────────────────────────────────────────────────────────

FINDINGS = {"REPL-000002": {"problem": 1, "possible_problem": 0, "checks": {"invalid_type": 1}},
            "REPL-000003": {"problem": 0, "possible_problem": 2, "checks": {"year_order": 2}},
            "REPL-000009": {"problem": 3, "possible_problem": 0, "checks": {"invalid_url": 3}}}


@pytest.mark.parametrize("filters, expected", [
    ({"problems": True}, ["REPL-000002"]),
    ({"possible_problems": True}, ["REPL-000003"]),
    ({"check": "year_order"}, ["REPL-000003"]),
    ({"check": "invalid_url"}, []),             # a record the tab does not have
    ({"problems": True, "possible_problems": True}, []),
    ({"problems": True, "type": "extension"}, ["REPL-000002"]),
])
def test_the_findings_filters_find_the_records_flagged(filters, expected):
    out = flora_service._apply_filters(_frame(), filters, FINDINGS)
    assert out["flora_id"].tolist() == expected


def test_the_counts_are_records_not_findings_and_only_the_tabs():
    counts = flora_service.counts(_frame(), FINDINGS)
    assert (counts["problems"], counts["possible_problems"]) == (1, 1)


def test_without_findings_nothing_is_flagged():
    """Bare frames, as most tests hand the tab, and a database without the table."""
    assert flora_service._apply_filters(_frame(), {"problems": True}).empty
    assert flora_service._apply_filters(_frame(), {"check": "year_order"}).empty
    assert (flora_service.counts(_frame())["problems"], flora_service.counts(_frame())["possible_problems"]) == (0, 0)
    assert flora_service._open_findings(None) == {}


def test_a_page_reads_the_findings_once_for_its_filter_counts_and_chips(db, monkeypatch):  # noqa: F811
    _checked(db)
    monkeypatch.setattr(flora_service, "dataset", lambda cur: _frame())
    reads = []
    real = entry_checks.open_by_record
    monkeypatch.setattr(entry_checks, "open_by_record", lambda cur: reads.append(1) or real(cur))
    with db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            page = flora_service.list_records(cur, {"problems": True})
    assert len(reads) == 1
    assert [r["flora_id"] for r in page["records"]] == ["REPL-000002"]
    assert page["records"][0]["open_findings"] == {"problem": 1, "possible_problem": 0}
    assert (page["counts"]["problems"], page["counts"]["possible_problems"]) == (1, 1)
    with db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            every = {r["flora_id"]: r["open_findings"] for r in flora_service.list_records(cur, {})["records"]}
    assert every == {"REPL-000001": None, "REPL-000002": {"problem": 1, "possible_problem": 0},
                     "REPL-000003": {"problem": 0, "possible_problem": 1}}


def test_the_filtered_export_honours_the_findings_filters(db, monkeypatch):  # noqa: F811
    _checked(db)
    frame = _frame().assign(export_id=lambda f: f["id"], export_position=range(1, 4))
    monkeypatch.setattr(flora_service, "dataset", lambda cur: frame)
    with db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            text = flora_service.export_csv(cur, {"possible_problems": True})
    assert "REPL-000003" in text and "REPL-000002" not in text


# ── the routes ────────────────────────────────────────────────────────────────

ROUTES = ("_flora_filters", "admin_flora_records", "admin_flora_record",
          "admin_flora_checks_summary", "FindingDismissal", "FindingKey", "_finding_audit",
          "admin_flora_finding_dismiss", "admin_flora_finding_undismiss",
          "_is_cross_site", "block_cross_site_writes")
ADMIN = {"id": 7, "handle": "test-admin"}


@pytest.fixture
def api(db, monkeypatch):  # noqa: F811
    """The routes over the throwaway database, with the real flora_service and
    entry_checks; only the build from source records is replaced by the frame."""
    app = FastAPI()
    frame = {"rows": ROWS}
    monkeypatch.setattr(flora_service, "dataset", lambda cur: _frame(frame["rows"]))
    # The record route's other parts come from the build and the change log, which
    # these tests do not have.
    monkeypatch.setattr(flora_service, "get_record", lambda cur, flora_id: next(
        ({"flora_id": r["flora_id"], "export_id": r["id"]} for r in frame["rows"]
         if flora_id in (r["flora_id"], r["id"])), None))
    monkeypatch.setattr(flora_service, "merged_sources", lambda cur, flora_id: [])
    monkeypatch.setattr(flora_service, "editing", lambda cur, record: None)

    def current_admin():
        raise HTTPException(401, "Admin sign-in required")

    @contextmanager
    def database():
        with db:
            with db.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur

    source = ROOT / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
             and node.name in ROUTES]
    assert {node.name for node in nodes} == set(ROUTES)
    audit = Mock()
    namespace = {"app": app, "Depends": Depends, "HTTPException": HTTPException,
                 "BaseModel": BaseModel, "Field": Field, "Request": Request,
                 "JSONResponse": JSONResponse, "current_admin": current_admin, "db": database,
                 "flora_service": flora_service, "security_events": security_events,
                 "_audit": audit, "_SAFE_METHODS": frozenset({"GET", "HEAD", "OPTIONS"}),
                 "_allowed_origins": lambda: {"https://flora.example"},
                 "is_public_read_request": flora_public_api.is_public_read_request}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    app.dependency_overrides[current_admin] = lambda: ADMIN
    return SimpleNamespace(app=app, client=TestClient(app), audit=audit, frame=frame, db=db)


DISMISS = "/api/admin/flora/findings/dismiss"


def _mark(api, key=TYPE_KEY, note="extension is right here", **headers):
    return api.client.post(DISMISS, json={"finding_key": key, "note": note}, headers=headers)


def _undo(api, key=TYPE_KEY, **headers):
    return api.client.request("DELETE", DISMISS, json={"finding_key": key}, headers=headers)


def _stored(api, key=TYPE_KEY):
    with api.db:
        with api.db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM flora_check_findings WHERE finding_key = %s", (key,))
            return dict(cur.fetchone())


def test_every_findings_route_requires_an_admin(api):
    _checked(api.db)
    api.app.dependency_overrides.clear()
    assert api.client.get("/api/admin/flora/checks/summary").status_code == 401
    assert api.client.get("/api/admin/flora/REPL-000002").status_code == 401
    assert api.client.get("/api/admin/flora?problems=true").status_code == 401
    assert _mark(api).status_code == 401
    assert _undo(api).status_code == 401
    assert _stored(api)["dismissed_at"] is None


def test_a_cross_site_mark_or_undo_is_refused(api):
    _checked(api.db)
    assert _mark(api, **{"sec-fetch-site": "cross-site"}).status_code == 403
    assert _mark(api, origin="https://evil.example").status_code == 403
    assert _stored(api)["dismissed_at"] is None
    assert _mark(api, **{"sec-fetch-site": "same-origin"}).status_code == 200
    assert _undo(api, **{"sec-fetch-site": "cross-site"}).status_code == 403
    assert _stored(api)["dismissed_by"] == "test-admin"
    api.audit.assert_called_once()


def test_a_mark_and_its_undo_go_through_the_routes_and_into_the_audit_trail(api):
    _checked(api.db)
    response = _mark(api, note="  extension is right here  ")
    assert response.status_code == 200
    body = response.json()
    assert (body["dismissed_by"], body["dismiss_note"], body["check_heading"]) == (
        "test-admin", "extension is right here", "Invalid type values")
    args, kwargs = api.audit.call_args
    assert args[1] == security_events.FLORA_FINDING_DISMISSED and kwargs["actor"] == ADMIN
    assert (kwargs["target_kind"], kwargs["target_id"], kwargs["target_label"]) == (
        "check_finding", TYPE_KEY, "Invalid type values")
    assert kwargs["detail"] == {"note": "extension is right here", "check": "invalid_type",
                                "flora_ids": ["REPL-000002"]}

    record = api.client.get("/api/admin/flora/REPL-000002").json()
    [finding] = record["check_findings"]
    assert (finding["finding_key"], finding["level"], finding["check_heading"], finding["column_name"],
            finding["value"], finding["dismissed_by"]) == (
        TYPE_KEY, "problem", "Invalid type values", "type", "extension", "test-admin")
    assert record["check_runs"]["structural"]["status"] == "finished"
    assert record["check_runs"]["network"] is None

    undone = _undo(api)
    assert undone.status_code == 200 and undone.json()["withdrawn"]["dismiss_note"] == "extension is right here"
    args, kwargs = api.audit.call_args
    assert args[1] == security_events.FLORA_FINDING_UNDISMISSED
    assert kwargs["detail"]["note"] == "extension is right here"
    assert _stored(api)["dismissed_at"] is None


@pytest.mark.parametrize("body, status, message", [
    ({"finding_key": TYPE_KEY, "note": ""}, 400, "at least 3 characters"),
    ({"finding_key": TYPE_KEY}, 400, "at least 3 characters"),
    ({"finding_key": TYPE_KEY, "note": "x" * 1001}, 400, "longer than 1,000 characters"),
    ({"finding_key": TYPE_KEY + "x", "note": "a reason"}, 404, "No such finding"),
    ({"finding_key": "", "note": "a reason"}, 422, None),
    ({"note": "a reason"}, 422, None),
])
def test_a_bad_mark_is_a_client_error_that_says_why(api, body, status, message):
    _checked(api.db)
    response = api.client.post(DISMISS, json=body)
    assert response.status_code == status
    if message:
        assert message in response.json()["detail"]
    assert _stored(api)["dismissed_at"] is None
    api.audit.assert_not_called()


def test_a_second_mark_an_undo_of_nothing_and_an_unknown_undo_are_refused(api):
    _checked(api.db)
    undo = _undo(api)
    assert undo.status_code == 400 and "not marked not a problem" in undo.json()["detail"]
    assert _undo(api, TYPE_KEY + "x").status_code == 404
    assert _mark(api).status_code == 200
    again = _mark(api, note="another reason")
    assert again.status_code == 400 and "test-admin already marked" in again.json()["detail"]
    assert _stored(api)["dismiss_note"] == "extension is right here"


def test_a_key_with_bars_and_any_text_travels_intact(api):
    """A key is check|IDs|column|value; the value can hold anything, "|" and "/" too."""
    rows = [_row(1), _row(2, type="ext|en/sion & <draft>?x=1#y")]
    _checked(api.db, rows)
    api.frame["rows"] = rows
    [finding] = api.client.get("/api/admin/flora/REPL-000002").json()["check_findings"]
    assert finding["finding_key"] == "invalid_type|REPL-000002|type|ext|en/sion & <draft>?x=1#y"
    assert _mark(api, finding["finding_key"]).status_code == 200
    assert _stored(api, finding["finding_key"])["dismissed_by"] == "test-admin"


def test_a_mark_made_in_the_tab_survives_a_run_that_resolves_and_reopens_the_finding(api):
    _checked(api.db)
    assert _mark(api).status_code == 200
    listed = api.client.get("/api/admin/flora?problems=true").json()
    assert listed["records"] == [] and listed["counts"]["problems"] == 0

    _checked(api.db, [_row(1), _row(2), ROWS[2]])                  # corrected
    assert api.client.get("/api/admin/flora/REPL-000002").json()["check_findings"] == []
    _checked(api.db)                                                # and back
    [finding] = api.client.get("/api/admin/flora/REPL-000002").json()["check_findings"]
    assert (finding["finding_key"], finding["dismissed_by"], finding["dismiss_note"]) == (
        TYPE_KEY, "test-admin", "extension is right here")
    assert api.client.get("/api/admin/flora?problems=true").json()["counts"]["problems"] == 0


def test_the_list_filters_counts_and_chips_through_the_route(api):
    _checked(api.db)
    problems = api.client.get("/api/admin/flora?problems=true").json()
    assert [r["flora_id"] for r in problems["records"]] == ["REPL-000002"]
    assert problems["records"][0]["open_findings"] == {"problem": 1, "possible_problem": 0}
    assert (problems["counts"]["problems"], problems["counts"]["possible_problems"]) == (1, 1)
    possible = api.client.get("/api/admin/flora?possible_problems=true").json()
    assert [r["flora_id"] for r in possible["records"]] == ["REPL-000003"]
    by_check = api.client.get("/api/admin/flora?check=year_order").json()
    assert [r["flora_id"] for r in by_check["records"]] == ["REPL-000003"]


def test_the_summary_names_each_check(api):
    run = _checked(api.db)
    summary = api.client.get("/api/admin/flora/checks/summary").json()
    by_code = {c["check_code"]: c for c in summary["checks"]}
    assert by_code["invalid_type"]["check_heading"] == "Invalid type values"
    assert by_code["year_order"]["level"] == "possible_problem"
    assert summary["open"] == {"problem": 1, "possible_problem": 1}
    assert summary["last_runs"]["structural"]["run_id"] == run["run_id"]
    assert YEAR_KEY in {f["finding_key"] for f in
                        api.client.get("/api/admin/flora/REPL-000003").json()["check_findings"]}


def test_the_record_and_list_still_work_without_the_findings_tables(api):
    # A database init_db() has not reached has no findings tables yet.
    with api.db:
        with api.db.cursor() as cur:
            cur.execute("DROP TABLE flora_check_findings, flora_check_runs CASCADE")
    record = api.client.get("/api/admin/flora/REPL-000003")
    assert record.status_code == 200
    assert record.json()["check_findings"] == []
    assert record.json()["check_runs"] == {"structural": None, "network": None}
    with api.db:
        with api.db.cursor(cursor_factory=RealDictCursor) as cur:
            listing = flora_service.list_records(cur, {"problems": True})
    assert listing["total"] == 0 and listing["counts"]["problems"] == 0


def test_the_findings_routes_are_declared_before_the_record_routes():
    """So that /flora/{flora_id} cannot take "checks" or "findings" for a record id."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    record = source.index('@app.get("/api/admin/flora/{flora_id}")')
    for route in ('@app.get("/api/admin/flora/checks/summary")',
                  '@app.post("/api/admin/flora/findings/dismiss")',
                  '@app.delete("/api/admin/flora/findings/dismiss")'):
        assert source.index(route) < record, route
