"""A decided record stays decided, against a real PostgreSQL (opt-in).

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database, as for
tests/test_preparation_database.py. Without it every test here is skipped.
"""
import json
import os
import threading
import uuid
from unittest.mock import patch

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import RealDictCursor

import consensus_engine
import db_pool
from tests.test_dashboard_split import flora
from tests.test_preparation_database import local_database  # noqa: F401

RESOLVE = {"admin_name": "boss", "type_check": "correct", "original_check": "correct",
           "outcome_check": "correct"}
JUDGE = {"type_check": "correct", "original_check": "correct", "outcome_check": "correct"}
AGREES = {"type_check": "correct", "original_check": "correct", "outcome_check": "correct"}


def C(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


@pytest.fixture
def env(local_database, monkeypatch):
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    state = {"coder": None}
    admin = {"id": 1, "handle": "boss", "trusted": True, "validator_id": None}
    flora.app.dependency_overrides[flora.current_admin] = lambda: dict(admin)
    flora.app.dependency_overrides[flora.current_validator] = lambda: {"coder_id": state["coder"]}
    with patch("consensus_engine.run_llm_validation", return_value=dict(AGREES)):
        try:
            yield local_database, TestClient(flora.app), state
        finally:
            flora.app.dependency_overrides.clear()
            db_pool.clear_all()


def validator(conn, handle, tier=0):
    with conn, C(conn) as cur:
        cur.execute("INSERT INTO validators (handle, validator_tier) VALUES (%s, %s) RETURNING id",
                    (handle, tier))
        return cur.fetchone()["id"]


def record(conn, status="unvalidated", **columns):
    fields = {"pair_id": uuid.uuid4().hex[:10], "doi_r": f"10.9/{uuid.uuid4().hex[:8]}",
              "type": "replication", "outcome": "failed", "validation_status": status,
              "title_r": "Rep", "study_r": "1", "doi_o": "10.9/orig", "title_o": "Orig",
              "study_o": "", "outcome_quote": "q", "abstract_r": "abstract text", **columns}
    with conn, C(conn) as cur:
        cur.execute(f"INSERT INTO unvalidated ({','.join(fields)}) "
                    f"VALUES ({','.join(['%s'] * len(fields))}) RETURNING record_id::text AS id",
                    list(fields.values()))
        record_id = cur.fetchone()["id"]
        for slot in ("human_1", "human_2"):
            cur.execute("INSERT INTO validation_queue (record_id, validator_slot) VALUES (%s, %s)",
                        (record_id, slot))
    return record_id


def one(conn, sql, *params):
    with conn, C(conn) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def run(conn, sql, *params):
    with conn, C(conn) as cur:
        cur.execute(sql, params)


def hold(conn, record_id, coder, slot="human_1"):
    run(conn, "UPDATE validation_queue SET validator_id = %s, validator_name = 'x', is_shown = TRUE, "
              "shown_at = NOW(), started_at = NOW() WHERE record_id = %s AND validator_slot = %s",
        coder, record_id, slot)


def judged(conn, record_id, coder, slot, outcome_check="correct"):
    summary = json.dumps({"validator_id": coder, "type_check": "correct", "original_check": "correct",
                          "outcome_check": outcome_check})
    run(conn, "UPDATE validation_queue SET validator_id = %s, validator_name = 'j', is_shown = TRUE, "
              "is_validated = TRUE, validated_at = NOW(), type_check = 'correct', "
              "original_check = 'correct', outcome_check = %s WHERE record_id = %s AND validator_slot = %s",
        coder, outcome_check, record_id, slot)
    column = "validator_1" if slot == "human_1" else "validator_2"
    run(conn, f"UPDATE unvalidated SET {column} = %s::jsonb WHERE record_id = %s", summary, record_id)


def assign(conn, record_id, coder):
    run(conn, "INSERT INTO assignments (record_id, validator_id, assigned_by) VALUES (%s, %s, 'x')",
        record_id, coder)


def status(conn, record_id):
    return one(conn, "SELECT validation_status AS s FROM unvalidated WHERE record_id = %s", record_id)["s"]


def published(conn, record_id):
    return one(conn, "SELECT COUNT(*) AS n FROM validated WHERE record_id = %s", record_id)["n"]


def open_slots(conn, record_id):
    return one(conn, "SELECT COUNT(*) AS n FROM validation_queue WHERE record_id = %s "
                     "AND validator_id IS NOT NULL AND NOT is_validated", record_id)["n"]


def assignment_status(conn, record_id):
    return one(conn, "SELECT status FROM assignments WHERE record_id = %s", record_id)["status"]


def child_of(conn, parent, status_="validated"):
    with conn, C(conn) as cur:
        cur.execute("""INSERT INTO unvalidated (doi_r, type, outcome, validation_status, title_r, doi_o,
                                                title_o, study_o, study_r, origin, parent_record_id)
                       VALUES ('10.9/rep', 'replication', 'failed', %s, 'Rep', '10.9/added', 'Added', '',
                               '1', 'admin_extra_original', %s)
                       RETURNING record_id::text AS id""", (status_, parent))
        child = cur.fetchone()["id"]
        cur.execute("""INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o, title_o,
                                              type, outcome)
                       VALUES (%s, '10.9/rep', '1', 'Rep', '10.9/added', '', 'Added', 'replication', 'failed')""",
                    (child,))
    return child


def resolved(conn, client, *validators_):
    """A record two validators judged and an admin resolved, with an open assignment."""
    first, second, assignee = validators_
    record_id = record(conn, "need_review")
    judged(conn, record_id, first, "human_1")
    judged(conn, record_id, second, "human_2")
    assign(conn, record_id, assignee)
    response = client.post(f"/api/admin/entries/{record_id}/resolve", json=RESOLVE)
    assert response.status_code == 200, response.text
    return record_id


# ── C1: an assignment on a decided record ─────────────────────────────────────

@pytest.mark.parametrize("verdict", [
    {},                                                                      # "looks right"
    {"type_check": "incorrect", "original_check": "incorrect", "outcome_check": "incorrect",
     "corrected_type": "not_validation"},                                    # "neither type"
])
def test_a_late_assignment_cannot_change_a_decided_record(env, verdict):
    """An admin closes R, the assignee submits: R kept its validated row while its
    status moved on, and a rejected R stayed published."""
    conn, client, st = env
    first, second, assignee = (validator(conn, h) for h in "abc")
    record_id = resolved(conn, client, first, second, assignee)
    before = one(conn, "SELECT validation_status, final_outcome, validator_1 FROM unvalidated WHERE record_id = %s",
                 record_id)
    st["coder"] = assignee
    response = client.post("/api/assignment-judge", json={"record_id": record_id, **JUDGE, **verdict})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "record_already_decided"
    assert "already been decided" in detail["message"] and "no points" in detail["message"]
    assert one(conn, "SELECT validation_status, final_outcome, validator_1 FROM unvalidated WHERE record_id = %s",
               record_id) == before
    assert published(conn, record_id) == 1
    assert one(conn, "SELECT total_points AS p FROM validators WHERE id = %s", assignee)["p"] == 0


def test_an_assignment_waits_for_a_decision_in_progress(env):
    """The record lock is what makes the status check safe."""
    conn, client, st = env
    assignee = validator(conn, "a")
    record_id = record(conn, "need_review", restricted_access=True)
    assign(conn, record_id, assignee)
    st["coder"] = assignee
    other = psycopg2.connect(os.environ["DATABASE_URL"])
    answer = {}
    try:
        with other.cursor() as admin:
            admin.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE", (record_id,))
            call = threading.Thread(target=lambda: answer.update(response=client.post(
                "/api/assignment-judge", json={"record_id": record_id, **JUDGE})))
            call.start()
            call.join(1.5)
            assert "response" not in answer, "the assignment read the record without its lock"
            admin.execute("UPDATE unvalidated SET validation_status = 'validated' WHERE record_id = %s",
                          (record_id,))
        other.commit()
    finally:
        other.rollback()
        other.close()
    call.join(10)
    assert answer["response"].status_code == 409


def test_an_assignment_that_rejects_takes_everything_published_with_it(env):
    """A validated entry re-keyed to review is open again and still published."""
    conn, client, st = env
    assignee = validator(conn, "a")
    record_id = record(conn, "need_review", admin_checked=True)
    run(conn, """INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o, title_o, type, outcome)
                 VALUES (%s, '10.9/old', '1', 'Rep', '10.9/orig', '', 'Orig', 'replication', 'failed')""", record_id)
    child = child_of(conn, record_id)
    assign(conn, record_id, assignee)
    st["coder"] = assignee
    response = client.post("/api/assignment-judge", json={
        "record_id": record_id, "type_check": "incorrect", "corrected_type": "not_validation",
        "original_check": "incorrect", "outcome_check": "incorrect"})
    assert response.status_code == 200, response.text
    assert status(conn, record_id) == "rejected" and published(conn, record_id) == 0
    assert status(conn, child) == "rejected" and published(conn, child) == 0


def test_a_late_validator_cannot_overwrite_an_assignment(env):
    conn, client, st = env
    holder, assignee = validator(conn, "h"), validator(conn, "a")
    record_id = record(conn, "validation_inprogress", restricted_access=True)
    hold(conn, record_id, holder)
    assign(conn, record_id, assignee)
    st["coder"] = assignee
    assert client.post("/api/assignment-judge", json={"record_id": record_id, **JUDGE}).status_code == 200
    assert open_slots(conn, record_id) == 0
    st["coder"] = holder
    late = client.post("/api/judge", json={"record_id": record_id, **JUDGE})
    assert late.status_code == 400 and "No open slot" in late.text
    assert one(conn, "SELECT validator_1->>'is_assignment' AS a FROM unvalidated WHERE record_id = %s",
               record_id)["a"] == "true"


def test_a_decided_record_cannot_be_assigned(env):
    conn, client, st = env
    coder = validator(conn, "a")
    for closed in ("validated", "rejected"):
        record_id = record(conn, closed)
        response = client.post("/api/admin/assign", json={"record_id": record_id, "validator_id": coder})
        assert response.status_code == 409, closed
        assert "already been decided" in response.text
        assert one(conn, "SELECT COUNT(*) AS n FROM assignments WHERE record_id = %s", record_id)["n"] == 0
    open_record = record(conn, "need_review")
    assert client.post("/api/admin/assign", json={"record_id": open_record, "validator_id": coder}).status_code == 200


@pytest.mark.parametrize("how, start", [
    ("resolve", "need_review"), ("reject", "need_review"), ("approve", "consensus_reached"),
    ("remove_original", "need_review"),
])
def test_a_decision_closes_the_assignment_and_the_open_slots(env, how, start):
    conn, client, st = env
    judge, holder, assignee = (validator(conn, h) for h in "abc")
    record_id = record(conn, start)
    if how == "remove_original":
        wrong = {"type_check": "correct", "original_check": "incorrect", "outcome_check": "not_applicable"}
        run(conn, "UPDATE unvalidated SET validator_1 = %s::jsonb, validator_2 = %s::jsonb WHERE record_id = %s",
            json.dumps(wrong), json.dumps(wrong), record_id)
        response = None
    else:
        judged(conn, record_id, judge, "human_1")
    hold(conn, record_id, holder, "human_2")
    assign(conn, record_id, assignee)
    url = f"/api/admin/entries/{record_id}"
    if how == "resolve":
        response = client.post(f"{url}/resolve", json=RESOLVE)
    elif how == "reject":
        response = client.post(f"{url}/resolve", json={**RESOLVE, "type_check": "incorrect",
                                                       "corrected_type": "not_validation"})
    elif how == "approve":
        response = client.post(f"{url}/approve")
    else:
        response = client.post(f"{url}/remove-original")
    assert response.status_code == 200, response.text
    assert assignment_status(conn, record_id) == "done"
    assert open_slots(conn, record_id) == 0


# ── C2: a late judgement on a decided record ──────────────────────────────────

def test_a_late_judgement_after_an_admin_resolve_changes_nothing(env):
    """A nightly re-key left a slot held; the admin decided; the validator's browser
    then sent the judgement. It flipped the record and left the old row published."""
    conn, client, st = env
    first, late = validator(conn, "a"), validator(conn, "b")
    record_id = record(conn, "need_review")
    judged(conn, record_id, first, "human_1")
    hold(conn, record_id, late, "human_2")
    response = client.post(f"/api/admin/entries/{record_id}/resolve", json={
        **RESOLVE, "outcome_check": "incorrect", "corrected_outcome": "successful"})
    assert response.status_code == 200, response.text
    before = one(conn, "SELECT validation_status, final_outcome FROM unvalidated WHERE record_id = %s", record_id)
    st["coder"] = late
    answer = client.post("/api/judge", json={"record_id": record_id, **JUDGE})
    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "record_already_decided"
    assert one(conn, "SELECT validation_status, final_outcome FROM unvalidated WHERE record_id = %s", record_id) == before
    assert before == {"validation_status": "validated", "final_outcome": "successful"}
    assert one(conn, "SELECT outcome FROM validated WHERE record_id = %s", record_id)["outcome"] == "successful"


def test_a_slot_left_open_on_a_decided_record_by_an_older_release_is_refused(env):
    conn, client, st = env
    first, late = validator(conn, "a"), validator(conn, "b")
    record_id = record(conn, "validated", final_outcome="successful")
    judged(conn, record_id, first, "human_1")
    hold(conn, record_id, late, "human_2")
    st["coder"] = late
    assert client.post("/api/judge", json={"record_id": record_id, **JUDGE}).status_code == 409
    assert one(conn, "SELECT final_outcome FROM unvalidated WHERE record_id = %s", record_id)["final_outcome"] == "successful"
    assert one(conn, "SELECT is_validated FROM validation_queue WHERE record_id = %s AND validator_id = %s",
               record_id, late)["is_validated"] is False


def test_a_retried_answer_that_landed_is_not_told_it_was_lost(env):
    """The last judgement validated the record and its response was lost. The retry is
    told there is no open slot, as before, not that the answer was not saved."""
    conn, client, st = env
    first, second = validator(conn, "a"), validator(conn, "b")
    record_id = record(conn, "validated", final_outcome="successful")
    judged(conn, record_id, first, "human_1")
    judged(conn, record_id, second, "human_2")
    st["coder"] = second
    answer = client.post("/api/judge", json={"record_id": record_id, **JUDGE})
    assert answer.status_code == 400 and "No open slot" in answer.json()["detail"], answer.text


@pytest.mark.parametrize("closed", ["validated", "rejected"])
def test_a_slot_left_open_on_a_decided_record_is_not_served_again(env, closed):
    """The refused submission keeps its slot, so /next-pairs resumed the same decided
    record on every load until the reaper's 5 days. It is freed instead, and the
    validator is given the next record."""
    conn, client, st = env
    stale, other = validator(conn, "a"), validator(conn, "b")
    decided = record(conn, closed)
    hold(conn, decided, stale)
    free = record(conn, "unvalidated")
    st["coder"] = stale
    served = client.post("/api/next-pairs?count=1").json()["pairs"]
    assert [p["record_id"] for p in served] == [free]
    assert open_slots(conn, decided) == 0
    assert status(conn, decided) == closed


@pytest.mark.parametrize("closed", ["validated", "rejected"])
def test_evaluate_consensus_leaves_a_decided_record_alone(env, closed):
    conn, client, st = env
    first, second = validator(conn, "a"), validator(conn, "b")
    record_id = record(conn, closed, final_outcome="mixed")
    judged(conn, record_id, first, "human_1")
    judged(conn, record_id, second, "human_2")
    with conn, C(conn) as cur:
        consensus_engine.evaluate_consensus(cur, record_id)
    assert one(conn, "SELECT validation_status, final_outcome FROM unvalidated WHERE record_id = %s", record_id) == {
        "validation_status": closed, "final_outcome": "mixed"}


def test_a_held_slot_on_a_rekeyed_record_is_still_judged(env):
    """A validated record re-keyed to need_review keeps admin_checked and is open again:
    the guard reads the status, so this judgement still lands and consensus still runs."""
    conn, client, st = env
    first, late = validator(conn, "a"), validator(conn, "b")
    record_id = record(conn, "need_review", admin_checked=True)
    judged(conn, record_id, first, "human_1")
    hold(conn, record_id, late, "human_2")
    st["coder"] = late
    response = client.post("/api/judge", json={"record_id": record_id, **JUDGE})
    assert response.status_code == 200, response.text
    assert status(conn, record_id) == "consensus_reached"


def test_a_rekeyed_tiebreaker_with_admin_checked_is_still_settled(env):
    conn, client, st = env
    first, second = validator(conn, "a"), validator(conn, "b")
    record_id = record(conn, "need_review", admin_checked=True, is_tiebreaker=True,
                       llm_validator=json.dumps({"error": "x"}))
    judged(conn, record_id, first, "human_1", "correct")
    judged(conn, record_id, second, "human_2", "incorrect")
    with patch("consensus_engine.run_llm_validation", return_value=dict(AGREES)):
        flora._retry_tiebreakers()
    assert status(conn, record_id) == "consensus_reached"


def test_auto_validate_waiting_still_publishes(env, monkeypatch, capsys):
    """It works on consensus_reached and never goes through the guard."""
    import auto_validate_waiting
    conn, client, st = env
    first, second = validator(conn, "a", tier=1), validator(conn, "b", tier=1)
    record_id = record(conn, "consensus_reached", final_outcome="failed", final_type="replication",
                       final_doi_o="10.9/orig", final_title_o="Orig", llm_validator=json.dumps(AGREES))
    judged(conn, record_id, first, "human_1")
    judged(conn, record_id, second, "human_2")
    auto_validate_waiting.run(apply=True)
    assert status(conn, record_id) == "validated" and published(conn, record_id) == 1


# ── C3: the nightly tiebreaker retry ──────────────────────────────────────────

def _stuck_tiebreaker(conn):
    first, second = validator(conn, "a"), validator(conn, "b")
    record_id = record(conn, "need_review", is_tiebreaker=True, llm_validator=json.dumps({"error": "x"}))
    judged(conn, record_id, first, "human_1", "correct")
    judged(conn, record_id, second, "human_2", "incorrect")
    return record_id


def test_an_admin_decision_during_the_gemini_call_stands(env):
    conn, client, st = env
    record_id = _stuck_tiebreaker(conn)
    calls = []

    def gemini(record, context):
        calls.append(context)
        response = client.post(f"/api/admin/entries/{record_id}/resolve", json={
            **RESOLVE, "outcome_check": "incorrect", "corrected_outcome": "mixed"})
        assert response.status_code == 200, response.text
        return dict(AGREES)

    with patch("consensus_engine.run_llm_validation", gemini):
        flora._retry_tiebreakers()
    assert calls == ["tiebreaker"]
    row = one(conn, "SELECT validation_status, final_outcome, llm_validator FROM unvalidated WHERE record_id = %s",
              record_id)
    assert row == {"validation_status": "validated", "final_outcome": "mixed", "llm_validator": {"error": "x"}}
    assert one(conn, "SELECT outcome FROM validated WHERE record_id = %s", record_id)["outcome"] == "mixed"


def test_the_retry_does_not_hold_the_row_during_the_gemini_call(env):
    conn, client, st = env
    record_id = _stuck_tiebreaker(conn)
    locked_out = []

    def gemini(record, context):
        other = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with other.cursor() as cur:
                cur.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE NOWAIT", (record_id,))
        except psycopg2.errors.LockNotAvailable:
            locked_out.append(True)
        finally:
            other.rollback()
            other.close()
        return dict(AGREES)

    with patch("consensus_engine.run_llm_validation", gemini):
        flora._retry_tiebreakers()
    assert locked_out == []
    assert status(conn, record_id) == "consensus_reached"


def test_the_retry_uses_the_answer_it_asked_for_and_asks_once(env):
    conn, client, st = env
    record_id = _stuck_tiebreaker(conn)
    calls = []

    def gemini(record, context):
        calls.append(context)
        return {**AGREES, "notes": "the retry's answer"}

    with patch("consensus_engine.run_llm_validation", gemini):
        flora._retry_tiebreakers()
    assert calls == ["tiebreaker"]
    assert one(conn, "SELECT llm_validator->>'notes' AS n FROM unvalidated WHERE record_id = %s",
               record_id)["n"] == "the retry's answer"


# ── C4: two duplicates published at once ──────────────────────────────────────

SHARED = dict(doi_r="10.9/same", doi_o="10.9/o", title_r="T", title_o="TO", study_r="1", study_o="")


def _valid_final(record_id_shared=None):
    return {"final_type": "replication", "final_outcome": "failed", "final_title_r": "T",
            "final_doi_o": "10.9/o", "final_title_o": "TO"}


def test_two_duplicates_approved_at_once_never_leave_a_record_without_its_row(env):
    """Both pass the identity check, then both publish. The upsert handed the row to
    the second and left the first validated with no row, as seen in production."""
    conn, client, st = env
    first = record(conn, "consensus_reached", **SHARED)
    second = record(conn, "consensus_reached", **SHARED)
    real = flora._validated_identity_conflict
    first_checked, second_done = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        found = real(*args, **kwargs)
        if str(args[1]) == first:
            first_checked.set()
            second_done.wait(10)
        return found

    flora._validated_identity_conflict = paused
    answers = {}
    try:
        racer = threading.Thread(target=lambda: answers.update(
            first=client.post(f"/api/admin/entries/{first}/approve")))
        racer.start()
        assert first_checked.wait(10)
        answers["second"] = client.post(f"/api/admin/entries/{second}/approve")
        second_done.set()
        racer.join(15)
    finally:
        flora._validated_identity_conflict = real
    assert answers["second"].status_code == 200, answers["second"].text
    assert answers["first"].status_code == 409
    assert "Mark as Resolved to merge" in answers["first"].text
    assert (status(conn, first), published(conn, first)) == ("consensus_reached", 0)
    assert (status(conn, second), published(conn, second)) == ("validated", 1)


def test_a_duplicate_that_lost_the_race_goes_to_an_admin_unattended(env):
    """The rule applied and the check saw nothing, yet the row was taken by then."""
    conn, client, st = env
    first, second = validator(conn, "a", tier=1), validator(conn, "b", tier=1)
    holder = record(conn, "validated", **SHARED)
    run(conn, """INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o, title_o, type, outcome)
                 VALUES (%s, '10.9/same', '1', 'T', '10.9/o', '', 'TO', 'replication', 'failed')""", holder)
    record_id = record(conn, "validation_inprogress", **SHARED)
    judged(conn, record_id, first, "human_1")
    judged(conn, record_id, second, "human_2")
    with patch("consensus_engine.identity_taken", return_value=False), conn, C(conn) as cur:
        consensus_engine.evaluate_consensus(cur, record_id)
    row = one(conn, "SELECT validation_status, auto_validated_rule FROM unvalidated WHERE record_id = %s", record_id)
    assert row == {"validation_status": "consensus_reached", "auto_validated_rule": None}
    assert published(conn, record_id) == 0
    assert one(conn, "SELECT record_id::text AS id FROM validated WHERE doi_r = '10.9/same'")["id"] == holder


def test_a_refused_publish_keeps_the_row_the_record_already_had(env):
    """A record re-keyed to review stays published until an admin decides, so a refused
    publish must put back the row it removed to make way."""
    conn, client, st = env
    holder = record(conn, "validated", **SHARED)
    run(conn, """INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o, title_o, type, outcome)
                 VALUES (%s, '10.9/same', '1', 'T', '10.9/o', '', 'TO', 'replication', 'failed')""", holder)
    record_id = record(conn, "need_review", doi_r="10.9/same", doi_o="10.9/o", title_r="T", title_o="TO",
                       study_r="1", study_o="")
    run(conn, """INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o, title_o, type, outcome)
                 VALUES (%s, '10.9/old', '1', 'Old', '10.9/o', '', 'TO', 'replication', 'failed')""", record_id)
    final = {"title_r": "T", "doi_o": "10.9/o", "title_o": "TO", "type": "replication", "outcome": "failed"}
    with conn, C(conn) as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
        assert consensus_engine._insert_validated(cur, dict(cur.fetchone()), final) is False
    assert one(conn, "SELECT doi_r FROM validated WHERE record_id = %s", record_id)["doi_r"] == "10.9/old"


def test_auto_validate_waiting_leaves_a_duplicate_that_lost_the_race(env, capsys):
    import auto_validate_waiting
    conn, client, st = env
    first, second = validator(conn, "a", tier=1), validator(conn, "b", tier=1)
    holder = record(conn, "validated", **SHARED)
    run(conn, """INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o, title_o, type, outcome)
                 VALUES (%s, '10.9/same', '1', 'T', '10.9/o', '', 'TO', 'replication', 'failed')""", holder)
    record_id = record(conn, "consensus_reached", llm_validator=json.dumps(AGREES), **SHARED, **_valid_final())
    judged(conn, record_id, first, "human_1")
    judged(conn, record_id, second, "human_2")
    with patch("auto_validate_waiting.ce.identity_taken", return_value=False):
        auto_validate_waiting.run(apply=True)
    assert (status(conn, record_id), published(conn, record_id)) == ("consensus_reached", 0)
    assert one(conn, "SELECT auto_validated_rule AS r FROM unvalidated WHERE record_id = %s", record_id)["r"] is None
    assert "1 duplicate(s)" in capsys.readouterr().out


# ── C6: a record with no outcome ──────────────────────────────────────────────

def test_a_record_with_no_outcome_and_an_abstract_is_in_the_normal_pool(env):
    conn, client, st = env
    coder = validator(conn, "a")
    record_id = record(conn, outcome=None)
    with conn, C(conn) as cur:
        assert flora._select_pair_candidate(cur, coder, "hard") is None
        assert str(flora._select_pair_candidate(cur, coder, "normal")["record_id"]) == record_id
        assert flora._record_is_hard(cur, record_id) is False


def test_the_hard_pool_is_otherwise_what_it_was(env):
    conn, client, st = env
    coder = validator(conn, "a")
    undeterminable = record(conn, outcome="cannot_be_determined")
    no_abstract = record(conn, abstract_r=None)
    blank_abstract = record(conn, abstract_r="")
    neither = record(conn, outcome=None, abstract_r=None)
    with conn, C(conn) as cur:
        assert flora._select_pair_candidate(cur, coder, "normal") is None
        for record_id in (undeterminable, no_abstract, blank_abstract, neither):
            assert flora._record_is_hard(cur, record_id) is True


def test_the_paper_table_agrees_on_what_is_hard(env, monkeypatch):
    import paper_review
    monkeypatch.setenv("PAPER_REVIEW_ENABLED", "1")
    conn, client, st = env
    coder = st["coder"] = validator(conn, "a")
    served = record(conn, doi_r="10.9/paper")
    no_outcome = record(conn, doi_r="10.9/paper", outcome=None)
    hold(conn, served, coder)
    originals = client.post("/api/paper-review/claim", json={"record_id": served}).json()["originals"]
    assert {o["record_id"]: o["judgeable"] for o in originals} == {served: True, no_outcome: True}
    with conn, C(conn) as cur:
        cur.execute(f"SELECT {paper_review._HARD_RECORD} AS hard FROM unvalidated u WHERE u.record_id = %s", (no_outcome,))
        assert cur.fetchone()["hard"] is False
