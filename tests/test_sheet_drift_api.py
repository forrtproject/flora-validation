"""The Sheet edits routes in app.py, with the service replaced.

Route order (/source-records/sheet-drift must not be taken for a record id), the
admin requirement, how the service's refusals map to status codes, the audit
events of a keep and its withdrawal, and the CSV's headers.
"""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import security_events
import sheet_drift_service
from tests.test_dashboard_split import flora

ADMIN = {"id": 7, "handle": "luke", "trusted": True, "validator_id": None}
BASE = "/api/admin/source-records/sheet-drift"
KEPT = {"finding_id": 5, "record_id": "rec-1", "source": "replications", "sheet_row_id": "u-1",
        "sheet_column": "outcome", "kept_at": "2026-11-22T10:00:00Z", "kept_by": "luke",
        "kept_note": "the paper says failed", "kept_sheet_value": "successful"}


@pytest.fixture
def api(monkeypatch):
    cursor = object()

    @contextmanager
    def db():
        yield cursor

    monkeypatch.setattr(flora, "db", db)
    audit = Mock()
    monkeypatch.setattr(flora, "_audit", audit)
    calls = {}

    def record(name, result):
        def fake(*args, **kwargs):
            calls.setdefault(name, []).append(args)
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(sheet_drift_service, name, fake)

    record("list_findings", {"records": [], "total": 0, "marker": "drift list"})
    record("summary", {"counts": {"needs_review": 3}, "last_checks": []})
    record("export_csv", "﻿finding_id\n")
    record("keep", dict(KEPT))
    record("undo_keep", {**{k: KEPT[k] for k in ("finding_id", "record_id", "source",
                                                  "sheet_row_id", "sheet_column")},
                         "withdrawn_by": "luke",
                         "withdrawn": {"kept_at": KEPT["kept_at"], "kept_by": "luke",
                                       "kept_note": "the paper says failed",
                                       "kept_sheet_value": "successful"}})
    record("record_view", {"record_id": "rec-1", "fields": []})
    monkeypatch.setattr(flora.source_records_service, "get_record",
                        lambda *a, **kw: pytest.fail("taken for a record id"))
    flora.app.dependency_overrides[flora.current_admin] = lambda: dict(ADMIN)
    try:
        yield TestClient(flora.app), calls, audit, record, cursor
    finally:
        flora.app.dependency_overrides.clear()


def test_the_view_is_not_taken_for_a_record_id(api):
    client, calls, *_ = api
    response = client.get(BASE + "?state=all&source=replications&search=REPL-1&page=2&per_page=10")
    assert response.status_code == 200 and response.json()["marker"] == "drift list"
    [(cursor, filters)] = calls["list_findings"]
    assert filters == {"state": "all", "source": "replications", "search": "REPL-1",
                       "page": 2, "per_page": 10}
    assert client.get(BASE + "/summary").json()["counts"] == {"needs_review": 3}


def test_the_default_filter_is_needs_review(api):
    client, calls, *_ = api
    client.get(BASE)
    assert calls["list_findings"][0][1]["state"] == "needs_review"


def test_an_unknown_filter_is_a_client_error(api):
    client, _, _, record, _ = api
    record("list_findings", ValueError("Unknown state filter: nope"))
    response = client.get(BASE + "?state=nope")
    assert response.status_code == 400 and "Unknown state filter" in response.json()["detail"]


def test_every_route_needs_an_admin(api):
    client, calls, *_ = api
    flora.app.dependency_overrides.clear()
    assert client.get(BASE).status_code == 401
    assert client.get(BASE + "/summary").status_code == 401
    assert client.get(BASE + "/export.csv").status_code == 401
    assert client.post(BASE + "/5/keep", json={"note": "abc", "sheet_value": "x"}).status_code == 401
    assert client.delete(BASE + "/5/keep").status_code == 401
    assert "keep" not in calls and "undo_keep" not in calls


def test_the_csv_is_dated_and_never_cached(api):
    client, calls, *_ = api
    response = client.get(BASE + "/export.csv?state=all&source=replications")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith('attachment; filename="sheet_edits_')
    assert response.headers["content-disposition"].endswith('.csv"')
    assert calls["export_csv"][0][1]["state"] == "all"


def test_a_keep_is_audited_and_returns_the_record(api):
    client, calls, audit, _, cursor = api
    response = client.post(BASE + "/5/keep", json={"note": "the paper says failed",
                                                   "sheet_value": "successful"})
    assert response.status_code == 200
    assert response.json()["record"] == {"record_id": "rec-1", "fields": []}
    [(cur, finding_id, admin, note, sheet_value)] = calls["keep"]
    assert (cur, finding_id, admin["handle"], note, sheet_value) == \
        (cursor, 5, "luke", "the paper says failed", "successful")
    args, kwargs = audit.call_args
    assert args[1] == security_events.SOURCE_DRIFT_KEPT and kwargs["actor"]["handle"] == "luke"
    assert (kwargs["target_kind"], kwargs["target_id"]) == ("sheet_edit", 5)
    assert kwargs["detail"] == {"note": "the paper says failed", "source": "replications",
                                "sheet_row_id": "u-1", "sheet_column": "outcome",
                                "sheet_value": "successful"}


def test_withdrawing_a_keep_is_audited_with_the_note_it_had(api):
    client, calls, audit, *_ = api
    response = client.delete(BASE + "/5/keep")
    assert response.status_code == 200
    args, kwargs = audit.call_args
    assert args[1] == security_events.SOURCE_DRIFT_KEEP_WITHDRAWN
    assert kwargs["detail"]["note"] == "the paper says failed"


@pytest.mark.parametrize("error, status, words", [
    (sheet_drift_service.FindingNotFound(5), 404, "No such sheet edit"),
    (sheet_drift_service.StaleFinding("mixed"), 409, "The sheet changed"),
    (ValueError("Say why FLoRA's value is right: at least 3 characters."), 400, "at least 3"),
    (ValueError("This sheet edit is resolved"), 400, "resolved"),
])
def test_keep_refusals_map_to_status_codes(api, error, status, words):
    client, _, audit, record, _ = api
    record("keep", error)
    response = client.post(BASE + "/5/keep", json={"note": "x", "sheet_value": "y"})
    assert response.status_code == status and words in response.json()["detail"]
    audit.assert_not_called()


def test_undo_refusals_map_to_status_codes(api):
    client, _, audit, record, _ = api
    record("undo_keep", sheet_drift_service.FindingNotFound(5))
    assert client.delete(BASE + "/5/keep").status_code == 404
    record("undo_keep", ValueError("FLoRA's value is not kept for this sheet edit."))
    assert client.delete(BASE + "/5/keep").status_code == 400
    audit.assert_not_called()


def test_a_runaway_body_is_refused_before_the_service(api):
    client, calls, *_ = api
    assert client.post(BASE + "/5/keep", json={"note": "x" * 10_001}).status_code == 422
    assert client.post(BASE + "/not-a-number/keep", json={"note": "abc"}).status_code in (404, 405)
    assert "keep" not in calls


def test_both_events_are_known_actions():
    assert security_events.SOURCE_DRIFT_KEPT in security_events.ACTIONS
    assert security_events.SOURCE_DRIFT_KEEP_WITHDRAWN in security_events.ACTIONS


def test_the_editor_gets_the_box_and_survives_a_broken_one(monkeypatch):
    """GET /api/admin/flora/{id} adds sheet_drift, and still answers when it fails."""
    @contextmanager
    def db():
        yield "a cursor it cannot use"

    monkeypatch.setattr(flora, "db", db)
    monkeypatch.setattr(flora.flora_service, "get_record", lambda cur, fid: {"flora_id": fid})
    monkeypatch.setattr(flora.flora_service, "merged_sources", lambda cur, fid: [])
    monkeypatch.setattr(flora.flora_service, "editing", lambda cur, record: None)
    monkeypatch.setattr(flora.flora_service, "check_findings", lambda cur, record: [])
    monkeypatch.setattr(flora.flora_service, "check_runs", lambda cur: {})
    flora.app.dependency_overrides[flora.current_admin] = lambda: dict(ADMIN)
    try:
        client = TestClient(flora.app)
        body = client.get("/api/admin/flora/REPL-000001").json()
        assert body["flora_id"] == "REPL-000001" and "sheet_drift" not in body
        monkeypatch.setattr(sheet_drift_service, "for_flora_record",
                            lambda cur, record: {"fields": [], "rows": [], "last_checks": []})

        class Cursor:
            def execute(self, sql, params=None):
                pass

        @contextmanager
        def working():
            yield Cursor()
        monkeypatch.setattr(flora, "db", working)
        body = client.get("/api/admin/flora/REPL-000001").json()
        assert body["sheet_drift"] == {"fields": [], "rows": [], "last_checks": []}
    finally:
        flora.app.dependency_overrides.clear()
