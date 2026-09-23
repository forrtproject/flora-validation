"""The preprint duplicate review queue in the FLoRA tab.

Service functions run against a recording cursor with the cached build stubbed out.
The routes are compiled out of app.py without importing it, because its module-level
init_db() would touch a database (the approach of test_pipeline_artifact_api.py).
"""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import numpy as np
from pydantic import BaseModel, Field
import pytest

import flora_service
import preprint_dedup
import security_events
import transform_sources
from tests.test_preparation_database import local_database  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parents[1]


class RecordingCursor:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    def fetchall(self):
        out, self.results = self.results, []
        return out


def candidate(action="needs_review", doi_1="10.31234/osf.io/a", doi_2="10.1016/j.b"):
    """One entry of the build's dedup log, with the numpy scalars a frame yields."""
    return {
        "side": "replication", "doi_1": doi_1, "doi_2": doi_2,
        "title_1": "Reproduction of Ku & Zaroff", "title_2": "Reproduction of Ku & Zaroff",
        "title_sim": np.float64(1.0),
        "first_author_1": "parsons", "first_author_2": "sonmez",
        "year_1": "2022", "year_2": "2020",
        "is_preprint_1": np.bool_(True), "is_preprint_2": np.bool_(False),
        "doi_o_group": "10.1016/j.jenvp.2014.10.008",
        "source_display_id_1": "REPRO-000001", "source_display_id_2": "REPRO-000002",
        "type_1": "reproduction", "type_2": "reproduction",
        "outcome_1": "computationally reproducible, robust", "outcome_2": float("nan"),
        "url_1": None, "url_2": "https://osf.io/x",
        "resolution": "review: first authors differ", "applied_action": action,
        "doi_remove": None, "doi_keep": None,
    }


@pytest.fixture
def build_log(monkeypatch):
    """The dedup log the stubbed build returns; tests append to it."""
    log = []
    monkeypatch.setattr(flora_service, "_current", lambda cur: (None, log))
    return log


# ── the queue ─────────────────────────────────────────────────────────────────

def test_only_undecided_pairs_are_queued(build_log):
    build_log += [candidate(),
                  candidate("auto_keep_1", doi_1="10.1/c", doi_2="10.1/d"),
                  candidate("keep_both", doi_1="10.1/e", doi_2="10.1/f")]
    decided_at = datetime(2026, 9, 24, tzinfo=timezone.utc)
    cur = RecordingCursor([{"pair_key": "10.1/e||10.1/f", "action": "keep_both",
                            "decided_by": "hamid", "decided_at": decided_at}])
    review = flora_service.preprint_review(cur)
    assert review["total_pending"] == 2
    assert [p["applied_action"] for p in review["pending"]] == ["needs_review", "auto_keep_1"]
    assert review["decided"][0]["decided_by"] == "hamid"
    assert "FROM preprint_dedup_decisions" in cur.executed[0][0]


def test_a_queue_item_is_json_safe_and_shows_each_paper(build_log):
    """numpy scalars and NaN come out of the frame; the response layer refuses both."""
    item = flora_service._review_item(candidate(doi_1="10.17605/osf.io/v9ykq"))
    json.dumps(item, allow_nan=False)
    first, second = item["papers"]
    assert first["is_preprint"] is True and first["is_repository"] is True
    assert second["outcome"] is None and second["url"] == "https://osf.io/x"
    assert (first["position"], second["position"]) == (1, 2)
    assert item["pair_key"] == preprint_dedup.pair_key("10.17605/osf.io/v9ykq", "10.1016/j.b")


def test_the_grid_counts_pairs_awaiting_a_decision(monkeypatch):
    """The badge on the tab's review button comes with the grid payload."""
    from tests.test_flora_registry import _frame
    frame = _frame()
    frame.attrs["preprint_dedup_log"] = [candidate(), candidate("keep_both"),
                                         candidate("auto_keep_2")]
    monkeypatch.setattr(flora_service, "dataset", lambda cur: frame)
    out = flora_service.list_records(None, {}, page=1, per_page=50)
    assert out["counts"]["preprint_pending"] == 2


# ── ruling ────────────────────────────────────────────────────────────────────

def test_an_unknown_action_is_refused(build_log):
    build_log.append(candidate())
    with pytest.raises(ValueError, match="keep_1, keep_2 or keep_both"):
        flora_service.decide_preprint_pair(RecordingCursor(), "10.31234/osf.io/a",
                                           "10.1016/j.b", "drop_all", "hamid")


def test_a_pair_the_build_did_not_detect_cannot_be_ruled_on(build_log):
    build_log.append(candidate())
    with pytest.raises(flora_service.PairNotFound):
        flora_service.decide_preprint_pair(RecordingCursor(), "10.1/x", "10.1/y",
                                           "keep_1", "hamid")


def test_a_ruling_is_stored_in_the_candidates_own_order(build_log):
    """keep_1 must name the same paper in the table as in the candidates log,
    whichever order the client sent the DOIs in."""
    build_log.append(candidate())
    cur = RecordingCursor()
    result = flora_service.decide_preprint_pair(
        cur, "10.1016/J.B", "10.31234/osf.io/a", "keep_1", "hamid", "  same study  ")
    sql, params = cur.executed[0]
    assert sql.startswith("INSERT INTO preprint_dedup_decisions")
    assert "ON CONFLICT (pair_key) DO UPDATE" in sql
    assert params[2:5] == ("10.31234/osf.io/a", "10.1016/j.b", "keep_2")
    assert params[8:] == ("same study", "hamid")
    assert result["action"] == "keep_2"


def test_keep_both_does_not_depend_on_order(build_log):
    build_log.append(candidate())
    cur = RecordingCursor()
    flora_service.decide_preprint_pair(cur, "10.1016/j.b", "10.31234/osf.io/a",
                                       "keep_both", "hamid")
    assert cur.executed[0][1][4] == "keep_both"
    assert cur.executed[0][1][8] is None      # a blank note is stored as NULL


def test_a_pair_missing_a_doi_cannot_be_ruled_on(build_log):
    build_log.append(candidate(doi_2=None))
    with pytest.raises(ValueError, match="missing DOI"):
        flora_service.decide_preprint_pair(RecordingCursor(), "10.31234/osf.io/a", "",
                                           "keep_1", "hamid")


def test_withdrawing_a_ruling_that_does_not_exist_is_reported():
    with pytest.raises(flora_service.PairNotFound):
        flora_service.undo_preprint_decision(RecordingCursor(), "10.1/a||10.1/b")


def test_withdrawing_a_ruling_deletes_it_and_returns_it_for_the_audit_trail():
    ruling = {"action": "keep_1", "doi_1": "10.1/a", "doi_2": "10.1/b", "note": None,
              "decided_by": "hamid"}
    cur = RecordingCursor([ruling])
    assert flora_service.undo_preprint_decision(cur, "10.1/a||10.1/b") == ruling
    sql, params = cur.executed[0]
    assert sql.startswith("DELETE FROM preprint_dedup_decisions WHERE pair_key = %s")
    assert params == ("10.1/a||10.1/b",)


def test_the_cache_signature_sees_the_doi_order_of_a_ruling():
    """keep_1 names doi_1, so the same action stored the other way round is a
    different ruling and must rebuild the cached dataset."""
    from collections import defaultdict
    cur = RecordingCursor([defaultdict(lambda: None)])
    flora_service._signature(cur)
    sql = cur.executed[0][0]
    ruling_hash = sql[sql.index("concat_ws"):sql.index("dedup_decisions_signature")]
    assert all(column in ruling_hash for column in ("doi_1", "doi_2", "action", "side"))


# ── the transform reads the rulings ──────────────────────────────────────────

def test_rulings_are_skipped_while_the_table_does_not_exist_yet():
    """The nightly job can reach the database before the site has created it."""
    cur = RecordingCursor([{"present": False}])
    assert transform_sources.load_dedup_decisions(cur) == []
    assert len(cur.executed) == 1


def test_rulings_are_read_when_the_table_exists():
    ruling = {"side": "replication", "doi_1": "10.1/a", "doi_2": "10.1/b", "action": "keep_both"}
    cur = RecordingCursor([{"present": True}, ruling])
    assert transform_sources.load_dedup_decisions(cur) == [ruling]
    assert "FROM preprint_dedup_decisions" in cur.executed[1][0]


def test_the_schema_constrains_the_ruling_vocabulary():
    schema = (ROOT / "db_schema.sql").read_text(encoding="utf-8")
    table = schema[schema.index("CREATE TABLE IF NOT EXISTS preprint_dedup_decisions"):]
    table = table[:table.index(");")]
    assert "CHECK (action IN ('keep_1', 'keep_2', 'keep_both'))" in table
    assert set(re.findall(r"'(keep_\w+)'", table)) == preprint_dedup.VALID_ACTIONS


# ── routes ────────────────────────────────────────────────────────────────────

ROUTES = ("admin_flora_preprint_duplicates", "PreprintPairDecision",
          "admin_flora_preprint_decide", "admin_flora_preprint_undo")


@pytest.fixture
def review_api():
    app = FastAPI()

    class PairNotFound(LookupError):
        pass

    service = SimpleNamespace(preprint_review=Mock(), decide_preprint_pair=Mock(),
                              undo_preprint_decision=Mock(), PairNotFound=PairNotFound)

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
    audit = Mock()
    namespace = {"app": app, "Depends": Depends, "HTTPException": HTTPException,
                 "BaseModel": BaseModel, "Field": Field, "Request": Request,
                 "current_admin": current_admin, "db": db, "flora_service": service,
                 "security_events": security_events, "_audit": audit}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    app.dependency_overrides[current_admin] = lambda: {"handle": "test-admin"}
    return SimpleNamespace(app=app, client=TestClient(app), service=service,
                           current_admin=current_admin, audit=audit)


URL = "/api/admin/flora/preprint-duplicates"


def test_the_queue_requires_an_admin(review_api):
    review_api.app.dependency_overrides.clear()
    assert review_api.client.get(URL).status_code == 401
    assert review_api.client.post(URL + "/decision", json={
        "doi_1": "a", "doi_2": "b", "action": "keep_1"}).status_code == 401
    assert review_api.client.delete(URL + "/decision",
                                    params={"pair_key": "a||b"}).status_code == 401
    review_api.service.preprint_review.assert_not_called()
    review_api.service.decide_preprint_pair.assert_not_called()
    review_api.service.undo_preprint_decision.assert_not_called()


def test_a_ruling_is_recorded_under_the_admins_handle_and_audited(review_api):
    review_api.service.decide_preprint_pair.return_value = {
        "pair_key": "10.1/a||10.1/b", "action": "keep_both", "decided_by": "test-admin",
        "doi_1": "10.1/a", "doi_2": "10.1/b"}
    response = review_api.client.post(URL + "/decision", json={
        "doi_1": "10.1/a", "doi_2": "10.1/b", "action": "keep_both", "note": "n"})
    assert response.status_code == 200
    review_api.service.decide_preprint_pair.assert_called_once_with(
        "isolated-cursor", "10.1/a", "10.1/b", "keep_both", "test-admin", "n")
    args, kwargs = review_api.audit.call_args
    assert args[1] == security_events.FLORA_PREPRINT_RULED
    assert kwargs["target_id"] == "10.1/a||10.1/b"
    assert kwargs["detail"]["action"] == "keep_both" and kwargs["detail"]["note"] == "n"


def test_a_note_longer_than_the_page_allows_is_refused(review_api):
    response = review_api.client.post(URL + "/decision", json={
        "doi_1": "10.1/a", "doi_2": "10.1/b", "action": "keep_1", "note": "x" * 501})
    assert response.status_code == 422
    review_api.service.decide_preprint_pair.assert_not_called()


def test_ruling_errors_become_client_errors(review_api):
    body = {"doi_1": "10.1/a", "doi_2": "10.1/b", "action": "keep_1"}
    review_api.service.decide_preprint_pair.side_effect = review_api.service.PairNotFound()
    assert review_api.client.post(URL + "/decision", json=body).status_code == 404
    review_api.service.decide_preprint_pair.side_effect = ValueError("bad action")
    response = review_api.client.post(URL + "/decision", json=body)
    assert response.status_code == 400 and response.json()["detail"] == "bad action"


def test_withdrawing_passes_the_pair_key_through_intact(review_api):
    """Pair keys carry '/' and '||', so they travel as a query parameter."""
    key = "10.1016/j.b||10.31234/osf.io/a"
    review_api.service.undo_preprint_decision.return_value = {"action": "keep_1"}
    response = review_api.client.delete(URL + "/decision", params={"pair_key": key})
    assert response.status_code == 200
    review_api.service.undo_preprint_decision.assert_called_once_with("isolated-cursor", key)
    args, kwargs = review_api.audit.call_args
    assert args[1] == security_events.FLORA_PREPRINT_WITHDRAWN
    assert kwargs["target_id"] == key and kwargs["detail"] == {"action": "keep_1"}


def test_the_queue_route_is_declared_before_the_record_route():
    """/flora/{flora_id} would otherwise answer /flora/preprint-duplicates."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert source.index('@app.get("/api/admin/flora/preprint-duplicates")') < \
        source.index('@app.get("/api/admin/flora/{flora_id}")')


# ── the page ──────────────────────────────────────────────────────────────────

def test_every_review_element_the_script_uses_is_on_the_page():
    script = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\$\("#(flora-dedup[\w-]*)"\)', script))
    assert used, "the review script no longer looks up its elements"
    assert {i for i in used if f'id="{i}"' not in page} == set()


def test_entering_the_tab_returns_to_the_grid():
    """switchAdminTab() resets the tab on entry; a review view left open would hide
    the grid behind a screen the user did not ask for."""
    script = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    body = script[script.index("function resetFloraView()"):]
    body = body[:body.index("\n}\n")]
    assert "resetFloraDedupView()" in body


# ── end to end, on PostgreSQL (opt-in: FLORA_TEST_DATABASE_URL) ───────────────

def _seed_pairs(cur):
    """Two ambiguous pairs, as source rows plus the metadata enrichment joins in.

    * Two teams' reports on one original: identical templated titles, different
      first authors, both preprints -> held for review, both rows kept.
    * One team's preprint and its publication -> the preprint dropped automatically.
    """
    from tests.test_preparation_database import add_source
    rows = [
        ("REPL-000001", "10.1016/j.jenvp.2014.10.008", "10.31234/osf.io/aaaa1",
         "Reproduction (with author data): Ku & Zaroff (2014)", "Lena Parsons", "2022"),
        ("REPL-000002", "10.1016/j.jenvp.2014.10.008", "10.31234/osf.io/bbbb2",
         "Reproduction (with author data): Ku & Zaroff (2014)", "Deniz Sonmez", "2020"),
        ("REPL-000003", "10.1037/xge0000001", "10.31234/osf.io/pre01",
         "A registered replication of the Stroop effect", "Ana Fox", "2021"),
        ("REPL-000004", "10.1037/xge0000001", "10.1016/j.pub.2022",
         "A registered replication of the Stroop effect", "Ana Fox", "2022"),
    ]
    for display_id, doi_o, doi_r, title, author, year in rows:
        add_source(cur, {"doi_o": doi_o, "doi_r": doi_r, "outcome": "successful"}, display_id)
        cur.execute("INSERT INTO work_metadata (doi, title, authors, year) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (doi) DO NOTHING", (doi_r, title, author, year))
    for doi_o in ("10.1016/j.jenvp.2014.10.008", "10.1037/xge0000001"):
        cur.execute("INSERT INTO work_metadata (doi, title, authors, year) VALUES (%s, %s, %s, %s)",
                    (doi_o, "Original " + doi_o, "Orig Author", "2014"))


def _kept(cur):
    return set(flora_service.dataset(cur)["doi_r"].dropna())


def test_rulings_made_in_the_tab_change_the_dataset_and_the_report(local_database, tmp_path,
                                                                   monkeypatch):
    from psycopg2.extras import RealDictCursor
    import prepare_flora

    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    connection = local_database
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            _seed_pairs(cur)

    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        review = flora_service.preprint_review(cur)
        by_action = {p["applied_action"]: p for p in review["pending"]}
        assert set(by_action) == {"needs_review", "auto_keep_2"}
        assert by_action["needs_review"]["resolution"] == "review: first authors differ"
        assert _kept(cur) == {"10.31234/osf.io/aaaa1", "10.31234/osf.io/bbbb2",
                              "10.1016/j.pub.2022"}
        assert flora_service.list_records(cur, {})["counts"]["preprint_pending"] == 2
    connection.rollback()

    # "Different papers" brings the automatically dropped preprint back.
    auto = by_action["auto_keep_2"]
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            flora_service.decide_preprint_pair(cur, auto["papers"][0]["doi"],
                                               auto["papers"][1]["doi"], "keep_both", "hamid")
    # "Same paper, keep A" drops B before detection runs.
    held = by_action["needs_review"]
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            flora_service.decide_preprint_pair(cur, held["papers"][0]["doi"],
                                               held["papers"][1]["doi"], "keep_1", "hamid")
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        assert _kept(cur) == {"10.31234/osf.io/aaaa1", "10.31234/osf.io/pre01",
                              "10.1016/j.pub.2022"}
        review = flora_service.preprint_review(cur)
        assert review["total_pending"] == 0
        assert {d["action"] for d in review["decided"]} == {"keep_both", "keep_1"}
    connection.rollback()

    # Withdrawing the keep_1 ruling puts the pair back in the queue.
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            flora_service.undo_preprint_decision(cur, held["pair_key"])
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        assert flora_service.preprint_review(cur)["total_pending"] == 1
    connection.rollback()

    # The pipeline writes the log beside its output and warns about the open pair.
    report = prepare_flora.prepare(tmp_path, network_checks="none")
    assert (tmp_path / "preprint_dedup_candidates.csv").exists()
    diagnostic = report["diagnostics"]["preprint_dedup_candidates.csv"]
    assert diagnostic["rows"] == 1
    assert diagnostic["records"][0]["applied_action"] == "needs_review"
    assert any("preprint duplicate pairs awaiting a decision" in w for w in report["warnings"])


def _published(cur):
    """flora_data as the public API serves it: doi_r -> (id, active, alt_identifier_r)."""
    cur.execute("SELECT id, doi_r, alt_identifier_r, retired_at FROM flora_data")
    return {r["doi_r"]: (r["id"], r["retired_at"] is None, r["alt_identifier_r"])
            for r in cur.fetchall()}


def _active(published):
    return {doi for doi, (_, active, _) in published.items() if active}


def test_a_ruling_reaches_the_published_table_at_the_next_run_and_not_before(
        local_database, tmp_path, monkeypatch):
    from psycopg2.extras import RealDictCursor
    import prepare_flora

    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    connection, release = local_database, tmp_path / "release"
    a, b = "10.31234/osf.io/aaaa1", "10.31234/osf.io/bbbb2"
    preprint, published = "10.31234/osf.io/pre01", "10.1016/j.pub.2022"

    def run():
        report = prepare_flora.prepare(release, network_checks="none")
        assert report["status"] in {"success", "needs_attention"}, report["errors"]
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            table = _published(cur)
        connection.rollback()
        return report, table

    def rule(doi_1, doi_2, action):
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cur:
                flora_service.decide_preprint_pair(cur, doi_1, doi_2, action, "hamid")

    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            _seed_pairs(cur)
    report, first = run()
    assert _active(first) == {a, b, published}          # the preprint auto-dropped
    assert report["diagnostics"]["preprint_dedup_candidates.csv"]["rows"] == 2

    rule(preprint, published, "keep_both")              # different papers
    rule(a, b, "keep_1")                                # same paper, keep A
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        assert _published(cur) == first, "a ruling alone must not touch the published table"
    connection.rollback()

    report, second = run()
    assert _active(second) == {a, preprint, published}
    # Survivors keep the permanent IDs they were published under.
    assert second[a][0] == first[a][0] and second[published][0] == first[published][0]
    # The dropped report is retired, not deleted: its ID still resolves.
    assert second[b][0] == first[b][0] and second[b][1] is False
    # A human ruling is a confirmed one, so the survivor records the dropped DOI.
    assert b in (second[a][2] or "")
    # The revived preprint is published under an ID of its own.
    assert second[preprint][0] and second[preprint][0] not in {v[0] for v in first.values()}
    assert report["diagnostics"]["preprint_dedup_candidates.csv"]["rows"] == 0
    assert not any("awaiting a decision" in w for w in report["warnings"])

    # Withdrawing the ruling brings the report back under its first ID.
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            flora_service.undo_preprint_decision(cur, preprint_dedup.pair_key(a, b))
    report, third = run()
    assert _active(third) == {a, b, preprint, published}
    assert third[b][0] == first[b][0]
    assert report["diagnostics"]["preprint_dedup_candidates.csv"]["rows"] == 1


def test_both_review_screens_explain_every_choice_without_hovering():
    """Tooltips do not exist on touch screens, so each choice's consequence is also
    written out on the page."""
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    screens = {
        "flora-dedup-view": ["keep A / keep B", "keep both", "Undo", "When it takes effect"],
        "src-dup-view": ["Keep &mdash; distinct", "Duplicate of", "When it takes effect"],
    }
    for view, choices in screens.items():
        section = page[page.index(f'id="{view}"'):]
        legend = section[section.index('<dl class="flora-dedup-legend'):]
        legend = legend[:legend.index("</dl>")]
        assert [c for c in choices if c not in legend] == [], view
