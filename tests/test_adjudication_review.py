"""The adjudication feature, phases 4 and 5: admin approval, publishing to FLoRA
through Source Records, withdrawing, and the CSV exports."""
import csv
import io
import os
import uuid
from contextlib import contextmanager

import psycopg2
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from psycopg2.extras import RealDictCursor

import adjudication
from adjudication import export, judging, review
from tests.test_adjudication_judging import DIFFERENT, NEITHER, SAME, _judge, _record
from tests.test_preparation_database import local_database  # noqa: F401


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _j(choice, outcome, suggested=None):
    return {"original_choice": choice, "outcome": outcome, "suggested_doi_o": suggested}


@pytest.mark.parametrize(("judgements", "verdict"), [
    ([], None),
    ([_j("flora", "failed")], None),
    ([_j("flora", "failed"), _j("flora", "failed")], "agree"),
    ([_j("flora", "failed"), _j("flora", "mixed")], "disagree"),
    ([_j("neither", "failed", "10.5555/a"), _j("neither", "failed", "10.5555/b")], "disagree"),
    ([_j("cannot_tell", "failed"), _j("cannot_tell", "failed")], "disagree"),
])
def test_two_judges_agree_only_on_the_same_decided_answer(judgements, verdict):
    assert review.agreement(judgements) == verdict


@pytest.mark.parametrize(("record", "doi", "outcome", "basis"), [
    (DIFFERENT, "10.1000/A", "failed", "flora"),
    (DIFFERENT, "10.2000/b", "failed", "observatory"),
    (DIFFERENT, "10.9999/c", "failed", "admin"),
    (DIFFERENT, None, "not_a_replication", "admin"),
    (SAME, "10.1/orig", "failed", "flora"),           # FLoRA said failed
    (SAME, "10.1/orig", "successful", "observatory"),  # the Observatory said success
    (SAME, "10.1/orig", "mixed", "admin"),
    ({**SAME, "flora_outcome": "successful", "mo_outcome": "reversal"}, "10.1/orig", "failed", "observatory"),
    ({**SAME, "mo_outcome": "inconclusive", "flora_outcome": "mixed"}, "10.1/orig", "uninformative", "admin"),
])
def test_the_basis_says_whose_answer_was_approved(record, doi, outcome, basis):
    assert review.basis_for(record, doi, outcome) == basis


@pytest.mark.parametrize(("decision", "message"), [
    ({"doi_o": "10.1000/a", "outcome": "cannot_tell"}, "vocabulary"),
    ({"doi_o": "10.1000/a", "outcome": ""}, "vocabulary"),
    ({"doi_o": "nope", "outcome": "failed"}, "DOI"),
    ({"doi_o": "10.1000/a", "outcome": "failed", "admin_note": "x" * 2001}, "admin_note"),
])
def test_a_decision_outside_floras_vocabulary_is_refused(decision, message):
    with pytest.raises(judging.Refused, match=message):
        review.check_decision(decision)


def test_a_decision_is_cleaned():
    assert review.check_decision({"doi_o": "https://doi.org/10.1000/A", "outcome": " Failed ",
                                  "title_o": " T ", "outcome_quote": "", "quote_source": None}) == {
        "doi_o": "10.1000/a", "title_o": "T", "outcome": "failed", "outcome_quote": None,
        "quote_source": None, "admin_note": None}


class _AliasCursor:
    def __init__(self, aliases):
        self.aliases, self.row = aliases, None

    def execute(self, sql, params):
        assert "outcome_alias" in sql
        canonical = self.aliases.get(params[0])
        self.row = None if canonical is None else {"canonical_value": canonical}

    def fetchone(self):
        return self.row


ALIASES = {"failed": "failed", "successful": "successful", "unclear": "cannot_be_determined"}


@pytest.mark.parametrize(("final", "problem"), [
    ({"doi_o": "10.1000/a", "outcome": "failed"}, None),
    ({"doi_o": "10.1000/a", "outcome": "not checked, robust"}, None),
    ({"doi_o": None, "outcome": "failed"}, "needs the original's DOI"),
    ({"doi_o": "10.1000/a", "outcome": "not_a_replication"}, "not a replication"),
    # In production's outcome_alias only as a target, never as a raw value: the
    # build would refuse the whole export.
    ({"doi_o": "10.1000/a", "outcome": "cannot_be_determined"}, "would stop the export"),
])
def test_only_what_the_flora_build_accepts_can_be_published(final, problem):
    found = review.publish_problem(_AliasCursor(ALIASES), final)
    assert (found is None) if problem is None else (problem in found)


def test_a_reproduction_is_published_on_its_two_axes():
    record = {**SAME, "record_id": uuid.uuid4(), "doi_r": "10.9/r", "imported_from": "x",
              "abstract_r": "A", "year_r": "2020"}
    final = {"doi_o": "10.1/orig", "outcome": "computational issues, robust", "outcome_quote": "Q",
             "quote_source": "abstract", "basis": "flora", "approved_by": "Hamid", "approved_at": "t"}
    row = review.source_row(record, final, "ana; ben")
    assert (row["type"], row["outcome"], row["outcome_quote"]) == ("reproduction", None, None)
    assert (row["outcome_computation"], row["outcome_robustness"]) == ("computational issues", "robust")
    assert '"judges": "ana; ben"' in row["raw"] and '"outcome_quote": "Q"' in row["raw"]
    final["outcome"] = "failed"
    row = review.source_row(record, final, "")
    assert (row["type"], row["outcome"], row["outcome_quote"], row["out_quote_source"]) == \
        ("replication", "failed", "Q", "abstract")
    assert row["outcome_computation"] is None and row["source"] == "adjudicated"


def test_typed_notes_cannot_run_as_spreadsheet_formulas():
    text = export._csv(("doi_o", "title_o", "judge_1_note", "judge_2_note", "admin_note"), [{
        "doi_o": "10.1000/a", "title_o": "-Minus in a title stays", "judge_1_note": '=HYPERLINK("x")',
        "judge_2_note": "Fine as it is", "admin_note": "@SUM(1)"}])
    assert text.startswith("﻿")
    row = next(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert row == {"doi_o": "10.1000/a", "title_o": "-Minus in a title stays",
                   "judge_1_note": "'=HYPERLINK(\"x\")", "judge_2_note": "Fine as it is",
                   "admin_note": "'@SUM(1)"}


def test_the_final_csv_starts_with_the_flora_builds_columns():
    import transform_sources
    assert list(export.FLORA_COLUMNS) == list(transform_sources.FLORA_COLUMNS)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _client(ready=True, admin=True, cursor=None):
    def current_admin():
        if not admin:
            raise HTTPException(401, "Unauthorized")
        return {"id": 2, "handle": "Hamid"}

    app = FastAPI()
    app.include_router(adjudication.create_router(
        current_admin=current_admin,
        current_validator=lambda: {"coder_id": 7, "handle": "Sam", "validator_tier": 1},
        status=lambda: adjudication.SetupStatus(True, ready, None if ready else "LockNotAvailable"),
        cursor=cursor or _a_cursor))
    return TestClient(app)


@contextmanager
def _a_cursor():
    yield object()


@pytest.mark.parametrize("path", [
    "/api/admin/disagreements/records",
    f"/api/admin/disagreements/records/{uuid.uuid4()}",
    "/api/admin/disagreements/export/final.csv",
])
def test_review_needs_an_admin_and_a_set_up_feature(path):
    assert _client(admin=False).get(path).status_code == 401
    assert _client(ready=False).get(path).status_code == 503


def test_the_list_is_filtered_by_status(monkeypatch):
    seen = []
    monkeypatch.setattr(review, "list_records", lambda cur, status: seen.append(status) or {"records": []})
    client = _client()
    client.get("/api/admin/disagreements/records")
    client.get("/api/admin/disagreements/records?status=approved")
    assert seen == [None, "approved"]


def test_actions_carry_the_admin(monkeypatch):
    seen = []
    for name in ("approve", "undo_approval", "publish", "withdraw"):
        monkeypatch.setattr(review, name, lambda cur, rid, admin, *rest, _n=name:
                            seen.append((_n, admin["handle"], rest)) or {"ok": _n})
    rid = uuid.uuid4()
    client = _client()
    assert client.post(f"/api/admin/disagreements/records/{rid}/approve",
                       json={"doi_o": "10.1000/a", "outcome": "failed"}).json() == {"ok": "approve"}
    for action, name in (("undo", "undo_approval"), ("publish", "publish"), ("withdraw", "withdraw")):
        assert client.post(f"/api/admin/disagreements/records/{rid}/{action}").json() == {"ok": name}
    assert seen[0] == ("approve", "Hamid", ({"doi_o": "10.1000/a", "title_o": None, "outcome": "failed",
                                             "outcome_quote": None, "quote_source": None,
                                             "admin_note": None},))
    assert [s[0] for s in seen] == ["approve", "undo_approval", "publish", "withdraw"]


def test_a_failed_setup_is_described_to_admins_only():
    app = FastAPI()
    app.include_router(adjudication.create_router(
        current_admin=lambda: {"id": 2, "handle": "Hamid"},
        current_validator=lambda: {"coder_id": 7, "handle": "Sam", "validator_tier": 1},
        status=lambda: adjudication.SetupStatus(True, False, 'connection to server at "db.example" failed'),
        cursor=_a_cursor))
    client = TestClient(app)
    validator = client.post("/api/disagreements/next")
    assert validator.status_code == 503 and "db.example" not in validator.json()["detail"]
    admin = client.get("/api/admin/disagreements/records")
    assert admin.status_code == 503 and "db.example" in admin.json()["detail"]


def test_a_refused_publish_says_why(monkeypatch):
    def publish(cur, rid, admin):
        raise judging.Refused(409, "FLoRA needs the original's DOI; this answer has none.")

    monkeypatch.setattr(review, "publish", publish)
    response = _client().post(f"/api/admin/disagreements/records/{uuid.uuid4()}/publish")
    assert response.status_code == 409 and "original's DOI" in response.json()["detail"]


def test_exports_are_csv_downloads(monkeypatch):
    monkeypatch.setattr(export, "export_csv", lambda cur, name: f"﻿col\n{name}\n")
    response = _client().get("/api/admin/disagreements/export/judgements.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="disagreements-judgements.csv"' in response.headers["content-disposition"]
    assert response.text.endswith("judgements\n")
    assert _client().get("/api/admin/disagreements/export/secrets.csv").status_code == 404


# ---------------------------------------------------------------------------
# Against a real PostgreSQL with the app's own schema (FLORA_TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

@pytest.fixture
def review_db(local_database, monkeypatch):  # noqa: F811
    dsn = os.environ["DATABASE_URL"]
    monkeypatch.setenv("ADJUDICATION_ENABLED", "1")
    assert adjudication.setup(dsn).ready

    def run(work, *args):
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                result = work(cur, *args)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return run, local_database


ADMIN = {"id": 1, "handle": "Hamid"}


def _judged(run, conn, n, shape, answers, **extra):
    """A record with two submitted judgements."""
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        record_id = _record(cur, n, shape, **extra)
        judges = [_judge(cur, f"judge-{n}-{i}", 1) for i in range(len(answers))]
    for judge, answer in zip(judges, answers):
        assert run(judging.next_record, judge)["record"]["record_id"] == record_id
        run(judging.submit, judge, record_id, answer)
    return record_id


def _source(conn, record_id):
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM source_records WHERE source = 'adjudicated' AND sheet_row_id = %s",
                    (record_id,))
        return cur.fetchone()


def test_approve_publish_withdraw_and_publish_again_on_postgres(review_db):
    import transform_sources
    run, conn = review_db
    agree = {"original_choice": "flora", "outcome": "failed"}
    x = _judged(run, conn, 1, DIFFERENT, [agree, {**agree, "note": "Checked"}],
                abstract_r="An abstract.", year_r="2019", flora_outcome_quote="It failed.")
    waiting = _judged(run, conn, 2, NEITHER, [{"original_choice": "cannot_tell", "outcome": "failed"}])

    listed = run(review.list_records, None)["records"]
    assert [r["record_id"] for r in listed] == [x, waiting], "awaiting approval comes first"
    assert listed[0]["agreement"] == "agree" and len(listed[0]["judgements"]) == 2
    assert [r["handle"] for r in listed[0]["judgements"]] == ["judge-1-0", "judge-1-1"]

    with pytest.raises(judging.Refused, match="two judgements"):
        run(review.approve, waiting, ADMIN, {"doi_o": "10.5555/x", "outcome": "failed"})
    with pytest.raises(judging.Refused, match="Only an approved"):
        run(review.publish, x, ADMIN)

    detail = run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "title_o": "Original A",
                                              "outcome": "failed", "outcome_quote": "It failed.",
                                              "quote_source": "abstract"})
    assert detail["record"]["status"] == "approved"
    assert detail["final"]["basis"] == "flora" and detail["final"]["approved_by"] == "Hamid"
    assert detail["publish_problem"] is None
    assert [j["handle"] for j in detail["judgements"]] == ["judge-1-0", "judge-1-1"]

    detail = run(review.publish, x, ADMIN)
    assert detail["record"]["status"] == "published"
    assert detail["final"]["published_record_id"] == "ADJ-000001"
    row = _source(conn, x)
    assert (row["display_id"], row["type"], row["doi_o"], row["doi_r"], row["outcome"]) == \
        ("ADJ-000001", "replication", "10.1000/a", "10.9/rep-1", "failed")
    assert (row["outcome_quote"], row["out_quote_source"], row["abstract_r"], row["year_r"]) == \
        ("It failed.", "abstract", "An abstract.", "2019")
    assert row["raw"]["judges"] == "judge-1-0; judge-1-1" and row["raw"]["approved_by"] == "Hamid"
    assert row["content_fingerprint"], "the duplicate detector sees it"

    # The FLoRA build takes it, and its outcome raises no problem.
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        frame = transform_sources.load(cur)
        aliases, _ = transform_sources.load_rules(cur)
    mine = frame[frame["source"] == "adjudicated"].to_dict("records")
    assert [r["display_id"] for r in mine] == ["ADJ-000001"]
    problems = {"unknown_alias": [], "bad_alias": [], "invalid_axis": []}
    assert transform_sources.derive_outcome(mine[0], aliases, problems) == "failed"
    assert problems == {"unknown_alias": [], "bad_alias": [], "invalid_axis": []}
    import validate_flora
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        built = transform_sources.build(cur, verbose=False)
    row = built[built["source_display_id"] == "ADJ-000001"].to_dict("records")
    assert len(row) == 1 and (row[0]["source"], row[0]["outcome"]) == ("adjudicated", "failed")
    assert not validate_flora._check_sources(built), "the nightly check knows the source"

    with pytest.raises(judging.Refused, match="Withdraw it"):
        run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "outcome": "mixed"})
    detail = run(review.withdraw, x, ADMIN)
    assert detail["record"]["status"] == "approved" and detail["final"]["withdrawn_by"] == "Hamid"
    row = _source(conn, x)
    assert row["deleted_at"] is not None and "Hamid" in row["deleted_reason"]
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        frame = transform_sources.load(cur)
    assert frame.empty or "ADJ-000001" not in set(frame["display_id"]), "out of the build"

    # Changed and published again: the same row and display id come back.
    run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "outcome": "mixed"})
    detail = run(review.publish, x, ADMIN)
    row = _source(conn, x)
    assert (row["display_id"], row["outcome"], row["deleted_at"], row["outcome_quote"]) == \
        ("ADJ-000001", "mixed", None, None)
    assert row["version"] >= 3
    with conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM source_records WHERE source = 'adjudicated'")
        assert cur.fetchone()[0] == 1

    # An answer that has been in FLoRA keeps its history: no undo.
    run(review.withdraw, x, ADMIN)
    with pytest.raises(judging.Refused, match="has been in FLoRA as ADJ-000001"):
        run(review.undo_approval, x, ADMIN)
    assert run(review.detail, x)["final"]["published_by"] == "Hamid"

    # A reviewer's edit in Source Records is never overwritten, nor withdrawn.
    run(review.publish, x, ADMIN)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE source_records SET reviewed_at = NOW(), reviewed_by = 'sophie' "
                    "WHERE source = 'adjudicated'")
    with pytest.raises(judging.Refused, match="reviewed in Source Records"):
        run(review.withdraw, x, ADMIN)
    assert _source(conn, x)["deleted_at"] is None
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE source_records SET deleted_at = NOW() WHERE source = 'adjudicated'")
        cur.execute("UPDATE adjudication.records SET status = 'approved' WHERE record_id = %s", (x,))
    with pytest.raises(judging.Refused, match="reviewed in Source Records"):
        run(review.publish, x, ADMIN)


def test_undo_before_anything_was_published_on_postgres(review_db):
    run, conn = review_db
    agree = {"original_choice": "flora", "outcome": "failed"}
    x = _judged(run, conn, 1, DIFFERENT, [agree, agree])
    run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "outcome": "failed"})
    detail = run(review.undo_approval, x, ADMIN)
    assert detail["record"]["status"] == "awaiting_approval" and detail["final"] is None


def test_answers_stay_hidden_until_both_are_in_and_judges_do_not_approve_their_own_on_postgres(review_db):
    run, conn = review_db
    # Judged in full first: a new judge is served a half-judged record before others.
    two = _judged(run, conn, 2, DIFFERENT, [{"original_choice": "flora", "outcome": "failed"}] * 2)
    one = _judged(run, conn, 1, DIFFERENT, [{"original_choice": "flora", "outcome": "failed", "note": "n"}])
    listed = run(review.list_records, "open")["records"][0]
    assert listed["judgements"] == [{"handle": "judge-1-0", "tier": 1}]
    detail = run(review.detail, one)["judgements"][0]
    assert {"original_choice", "outcome", "note", "points"}.isdisjoint(detail)
    assert detail["handle"] == "judge-1-0"
    csv_rows = list(csv.DictReader(io.StringIO(run(export.export_csv, "judgements").lstrip("\ufeff"))))
    open_row = next(r for r in csv_rows if r["adjudication_record_id"] == one)
    assert (open_row["judge_1_handle"], open_row["judge_1_outcome"], open_row["judge_1_note"]) == \
        ("judge-1-0", "", "")

    # An admin account linked to one of the judges cannot approve that record.
    with conn, conn.cursor() as cur:
        cur.execute("SELECT validator_id FROM adjudication.judgements WHERE record_id = %s LIMIT 1", (two,))
        judge_id = cur.fetchone()[0]
    with pytest.raises(judging.Refused, match="judged this record yourself"):
        run(review.approve, two, {**ADMIN, "validator_id": judge_id}, {"doi_o": "10.1000/a", "outcome": "failed"})
    assert run(review.approve, two, {**ADMIN, "validator_id": None},
               {"doi_o": "10.1000/a", "outcome": "failed"})["record"]["status"] == "approved"


def test_a_pair_flora_already_holds_is_flagged_before_publishing_on_postgres(review_db):
    run, conn = review_db
    agree = {"original_choice": "flora", "outcome": "failed"}
    x = _judged(run, conn, 1, DIFFERENT, [agree, agree])
    with conn, conn.cursor() as cur:
        # The website's own record of the same pair, with a DOI spelled differently.
        cur.execute("INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o, doi_r, outcome) "
                    "VALUES ('validated', 'v1', 'VAL-000001', 'replication', 'https://doi.org/10.1000/A', "
                    "'10.9/REP-1', 'successful')")
        # A different original for the same replication: listed, not a conflict.
        cur.execute("INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o, doi_r, outcome) "
                    "VALUES ('replications', 'r1', 'REPL-000001', 'replication', '10.5555/other', "
                    "'10.9/rep-1', 'mixed')")
    detail = run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "outcome": "failed"})
    assert [r["display_id"] for r in detail["in_flora"]] == ["REPL-000001", "VAL-000001"]
    assert [r["display_id"] for r in detail["pair_conflicts"]] == ["VAL-000001"]
    # A reproduction answer is a different FLoRA row type: no conflict.
    detail = run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "outcome": "not checked, robust"})
    assert detail["pair_conflicts"] == []


def test_an_outcome_the_build_would_refuse_is_never_published_on_postgres(review_db):
    run, conn = review_db
    answer = {"original_choice": "flora", "outcome": "cannot_be_determined"}
    x = _judged(run, conn, 1, DIFFERENT, [answer, answer])
    detail = run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "outcome": "cannot_be_determined"})
    assert "would stop the export" in detail["publish_problem"]
    with pytest.raises(judging.Refused, match="would stop the export"):
        run(review.publish, x, ADMIN)
    assert _source(conn, x) is None

    # A reproduction goes in on its two axes.
    detail = run(review.approve, x, ADMIN, {"doi_o": "10.1000/a", "outcome": "not checked, robust"})
    assert detail["publish_problem"] is None
    run(review.publish, x, ADMIN)
    row = _source(conn, x)
    assert (row["type"], row["outcome"], row["outcome_computation"], row["outcome_robustness"]) == \
        ("reproduction", None, "not checked", "robust")


def test_what_flora_already_holds_is_shown_on_postgres(review_db):
    run, conn = review_db
    answer = {"original_choice": "flora", "outcome": "failed"}
    x = _judged(run, conn, 1, DIFFERENT, [answer, answer])
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o, doi_r, "
                    "outcome_computation, outcome_robustness) VALUES ('reproductions', 'r1', "
                    "'REPRO-000009', 'reproduction', '10.1000/a', '10.9/REP-1', 'not checked', 'robust')")
    rows = run(review.detail, x)["in_flora"]
    assert [(r["display_id"], r["source"], r["deleted"]) for r in rows] == [("REPRO-000009", "reproductions", False)]


def test_the_csv_exports_on_postgres(review_db):
    run, conn = review_db
    agree = {"original_choice": "flora", "outcome": "failed"}
    x = _judged(run, conn, 1, DIFFERENT, [agree, {"original_choice": "observatory", "outcome": "mixed",
                                                  "note": "Reads as mixed, \"not\" failed"}])
    _judged(run, conn, 2, NEITHER, [])
    run(review.approve, x, ADMIN, {"doi_o": "10.2000/b", "title_o": "B", "outcome": "mixed"})
    run(review.publish, x, ADMIN)

    final = list(csv.DictReader(io.StringIO(run(export.export_csv, "final").lstrip("﻿"))))
    assert len(final) == 1
    assert list(final[0])[:len(export.FLORA_COLUMNS)] == list(export.FLORA_COLUMNS)
    assert {k: final[0][k] for k in ("doi_o", "doi_r", "outcome", "type", "source", "basis",
                                      "published_record_id", "judges")} == {
        "doi_o": "10.2000/b", "doi_r": "10.9/rep-1", "outcome": "mixed", "type": "replication",
        "source": "adjudicated", "basis": "observatory", "published_record_id": "ADJ-000001",
        "judges": "judge-1-0; judge-1-1"}

    rows = list(csv.DictReader(io.StringIO(run(export.export_csv, "judgements").lstrip("﻿"))))
    assert len(rows) == 2
    judged = next(r for r in rows if r["adjudication_record_id"] == x)
    assert (judged["judge_1_handle"], judged["judge_1_original_choice"], judged["judge_2_note"]) == \
        ("judge-1-0", "flora", 'Reads as mixed, "not" failed')
    assert (judged["agreement"], judged["final_outcome"], judged["final_basis"]) == \
        ("disagree", "mixed", "observatory")
    other = next(r for r in rows if r["adjudication_record_id"] != x)
    assert other["judge_1_handle"] == "" and other["status"] == "open"
