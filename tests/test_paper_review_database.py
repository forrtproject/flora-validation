"""The paper review against a real PostgreSQL (opt-in).

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database, as for
tests/test_preparation_database.py. Without it every test here is skipped.
"""
import json
import os
import uuid
from unittest.mock import patch

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

from tests.test_preparation_database import local_database  # noqa: F401

DOI = "10.9/paper"
QUOTE = "Neither study replicated."


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def _record(cur, n, *, status="unvalidated", doi_r=DOI, outcome="failed", quote=QUOTE, **columns):
    cur.execute(
        """INSERT INTO unvalidated (pair_id, doi_r, type, outcome, outcome_quote, validation_status,
                                    title_r, abstract_r, doi_o, title_o, study_o, year_o, study_r)
           VALUES (%s, %s, %s, %s, %s, %s, 'The paper', %s, %s, %s, '', '2001', '')
           RETURNING record_id::text AS id""",
        (f"p-{uuid.uuid4().hex[:8]}", doi_r, columns.get("type", "replication"), outcome, quote, status,
         columns.get("abstract", "We replicated two studies. Neither study replicated."),
         f"10.9/o{n}", f"Original {n}"))
    record_id = cur.fetchone()["id"]
    for slot in ("human_1", "human_2", "llm"):
        cur.execute("INSERT INTO validation_queue (record_id, validator_slot) VALUES (%s, %s)",
                    (record_id, slot))
    return record_id


def _validator(cur, handle):
    cur.execute("INSERT INTO validators (handle) VALUES (%s) RETURNING id", (handle,))
    return cur.fetchone()["id"]


def _hold(cur, record_id, coder_id, started=True):
    cur.execute(
        """UPDATE validation_queue SET validator_id = %s, is_shown = TRUE,
                  started_at = CASE WHEN %s THEN NOW() END
           WHERE queue_id = (SELECT queue_id FROM validation_queue
                             WHERE record_id = %s AND validator_slot IN ('human_1', 'human_2')
                               AND validator_id IS NULL ORDER BY validator_slot LIMIT 1)""",
        (coder_id, started, record_id))


@pytest.fixture
def api(local_database, monkeypatch):
    import db_pool
    from fastapi.testclient import TestClient
    from tests.test_dashboard_split import flora
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("PAPER_REVIEW_ENABLED", "1")
    who = {"coder_id": None}
    flora.app.dependency_overrides[flora.current_validator] = lambda: dict(who)
    flora.app.dependency_overrides[flora.current_admin] = lambda: {"handle": "boss", "id": 1}
    with patch("consensus_engine.run_llm_validation",
               return_value={"error": "no LLM in tests"}):
        try:
            yield TestClient(flora.app), who
        finally:
            flora.app.dependency_overrides.clear()
            db_pool.clear_all()


def _judge(client, record_id, *, group, shared=False, original="correct", outcome="correct", **fields):
    checks = {"paper_review": {"outcome_group": group, "outcome_shared": shared, "originals": 2}}
    checks.update(fields.pop("checks", {}))
    return client.post("/api/judge", json={
        "record_id": record_id, "type_check": "correct", "original_check": original,
        "outcome_check": outcome, "additional_checks": checks, **fields})


def test_the_claim_reserves_open_records_and_lists_the_rest_for_context(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        bob = _validator(cur, "bob")
        cat = _validator(cur, "cat")
        served = _record(cur, 1)
        free = _record(cur, 2)
        full = _record(cur, 3)
        finished = _record(cur, 4, status="validated")
        hard = _record(cur, 5, outcome="cannot_be_determined")
        rejected = _record(cur, 6, status="rejected")
        other_paper = _record(cur, 7, doi_r="10.9/other")
        _hold(cur, served, ana)
        _hold(cur, full, bob)
        _hold(cur, full, cat)

    response = client.post("/api/paper-review/claim", json={"record_id": served})
    assert response.status_code == 200, response.text
    originals = {o["record_id"]: o for o in response.json()["originals"]}
    assert set(originals) == {served, free, full, finished, hard}
    assert rejected not in originals and other_paper not in originals
    assert {i for i, o in originals.items() if o["judgeable"]} == {served, free}
    # Context rows say nothing about why, or about what anyone answered.
    assert "outcome" not in originals[full] and "queue_id" not in originals[full]
    assert originals[free]["outcome_phrase"] == QUOTE

    with local_database, _cursor(local_database) as cur:
        cur.execute("""SELECT record_id::text AS id, validator_id, started_at FROM validation_queue
                       WHERE validator_id = %s""", (ana,))
        held = {r["id"]: r for r in cur.fetchall()}
        cur.execute("SELECT validation_status FROM unvalidated WHERE record_id = %s", (free,))
        assert cur.fetchone()["validation_status"] == "validation_inprogress"
    assert set(held) == {served, free} and all(r["started_at"] for r in held.values())

    # Repeating it (a reload, a resumed draft) takes nothing more.
    again = client.post("/api/paper-review/claim", json={"record_id": served}).json()
    assert [o["queue_id"] for o in again["originals"] if o["judgeable"]] == [
        o["queue_id"] for o in response.json()["originals"] if o["judgeable"]]


def test_a_buffered_sibling_is_kept_and_promoted(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        served, buffered = _record(cur, 1), _record(cur, 2)
        _hold(cur, served, ana)
        _hold(cur, buffered, ana, started=False)
    client.post("/api/paper-review/claim", json={"record_id": served})
    with local_database, _cursor(local_database) as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM validation_queue
                       WHERE validator_id = %s AND record_id = %s AND started_at IS NOT NULL""",
                    (ana, buffered))
        assert cur.fetchone()["n"] == 1


def test_one_validator_cannot_hold_both_slots_of_a_record(api, local_database):
    """The browser's buffer top-up can run alongside a claim. A record judged twice by
    one person would pass as two independent validators."""
    from tests.test_dashboard_split import flora
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        record = _record(cur, 1)
        _hold(cur, record, ana, started=False)
    with local_database, _cursor(local_database) as cur:
        assert flora._claim_free_slot(cur, ana, record, True) is None
        cur.execute("SELECT COUNT(*) AS n FROM validation_queue WHERE record_id = %s AND validator_id = %s",
                    (record, ana))
        assert cur.fetchone()["n"] == 1


def test_a_buffered_sibling_the_table_cannot_answer_stays_out_of_it(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        served = _record(cur, 1)
        repro = _record(cur, 2, type="reproduction", outcome=None)
        hard = _record(cur, 3, outcome="cannot_be_determined")
        for record in (served, repro, hard):
            _hold(cur, record, ana, started=False)
    originals = client.post("/api/paper-review/claim", json={"record_id": served}).json()["originals"]
    assert {o["record_id"]: o["judgeable"] for o in originals} == {served: True, repro: False, hard: False}
    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT started_at FROM validation_queue WHERE record_id = %s AND validator_id = %s", (repro, ana))
        assert cur.fetchone()["started_at"] is None


def test_a_locked_record_is_passed_over_rather_than_waited_for(api, local_database):
    """A buffer top-up takes several records in random order, so it must never block."""
    from tests.test_dashboard_split import flora
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        record = _record(cur, 1)
    other = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with other.cursor() as locker:
            locker.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE", (record,))
            with local_database, _cursor(local_database) as cur:
                assert flora._claim_free_slot(cur, ana, record, False) is None
    finally:
        other.rollback()
        other.close()


def test_only_a_validator_holding_the_record_can_open_its_paper(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        record = _record(cur, 1)
        repro = _record(cur, 2, doi_r="10.9/repro", type="reproduction", outcome=None)
        _hold(cur, repro, who["coder_id"])
    assert client.post("/api/paper-review/claim", json={"record_id": record}).status_code == 409
    assert client.post("/api/paper-review/claim", json={"record_id": repro}).status_code == 409


def test_two_validators_claiming_at_once_never_share_a_slot(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ids = [_validator(cur, h) for h in ("ana", "bob", "cat")]
        first, second = _record(cur, 1), _record(cur, 2)
        for coder in ids:
            if coder != ids[2]:
                _hold(cur, first, coder)
    who["coder_id"] = ids[0]
    client.post("/api/paper-review/claim", json={"record_id": first})
    who["coder_id"] = ids[1]
    client.post("/api/paper-review/claim", json={"record_id": first})
    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT COUNT(DISTINCT validator_id) AS n FROM validation_queue "
                    "WHERE record_id = %s AND validator_id IS NOT NULL", (second,))
        assert cur.fetchone()["n"] == 2
    # A third validator finds the second record full.
    with local_database, _cursor(local_database) as cur:
        third = _record(cur, 3)
        _hold(cur, third, ids[2])
    who["coder_id"] = ids[2]
    originals = client.post("/api/paper-review/claim", json={"record_id": third}).json()["originals"]
    assert {o["record_id"]: o["judgeable"] for o in originals} == {first: False, second: False, third: True}


def test_points_pay_the_base_once_and_each_shared_check_once(api, local_database):
    """A 4-original paper with one shared check: 10 + 4×2 + 2 = 20, whatever the answers."""
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        records = [_record(cur, n) for n in range(4)]
        for record in records:
            _hold(cur, record, ana)
    earned = [
        _judge(client, records[0], group="g", shared=True).json()["points_earned"],
        _judge(client, records[1], group="g", shared=True).json()["points_earned"],
        _judge(client, records[2], group="g", shared=True,
               checks={"was_unsure_original": True}, original="incorrect").json()["points_earned"],
        _judge(client, records[3], group="g", shared=True, original="incorrect",
               corrected_doi_o="10.9/right").json()["points_earned"],
    ]
    assert earned == [14, 2, 2, 2]
    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT total_points, total_judgements FROM validators WHERE id = %s", (ana,))
        row = cur.fetchone()
    assert (row["total_points"], row["total_judgements"]) == (20, 4)


def test_a_disagreement_pays_the_same_as_an_agreement(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        agreeing, correcting = _record(cur, 1, doi_r="10.9/a"), _record(cur, 2, doi_r="10.9/b")
        _hold(cur, agreeing, who["coder_id"])
        _hold(cur, correcting, who["coder_id"])
    assert _judge(client, agreeing, group="a").json()["points_earned"] == 14
    assert _judge(client, correcting, group="b", outcome="incorrect",
                  corrected_outcome="successful").json()["points_earned"] == 14


def test_a_note_pays_once_per_paper_and_no_outcome_pays_no_check(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        first, second = _record(cur, 1), _record(cur, 2)
        _hold(cur, first, who["coder_id"])
        _hold(cur, second, who["coder_id"])
    assert _judge(client, first, group="g", validator_notes="See table 2").json()["points_earned"] == 15
    assert _judge(client, second, group="none", original="incorrect", outcome="not_applicable",
                  validator_notes="See table 2").json()["points_earned"] == 2


def test_a_paper_rejected_as_neither_type_pays_its_base_once(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        first, second = _record(cur, 1), _record(cur, 2)
        _hold(cur, first, who["coder_id"])
        _hold(cur, second, who["coder_id"])
    fields = dict(original="incorrect", outcome="incorrect")
    body = {"type_check": "incorrect", "corrected_type": "not_validation"}
    assert _judge(client, first, group="none:a", **fields, **body).json()["points_earned"] == 10
    assert _judge(client, second, group="none:b", **fields, **body).json()["points_earned"] == 0


def test_no_outcome_is_stored_and_nothing_else_may_use_it(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        record = _record(cur, 1)
        _hold(cur, record, who["coder_id"])
    no_outcome = {"record_id": record, "type_check": "correct", "outcome_check": "not_applicable"}
    for refused in ({"original_check": "correct"},
                    {"original_check": "incorrect", "corrected_doi_o": "10.9/right"},
                    {"original_check": "incorrect", "corrected_title_o": "The right one"},
                    {"original_check": "incorrect", "additional_checks": {"was_unsure_original": True}},
                    {"original_check": "incorrect", "type_check": "incorrect",
                     "corrected_type": "not_validation"}):
        assert client.post("/api/judge", json={**no_outcome, **refused}).status_code == 400, refused
    response = client.post("/api/judge", json={
        **no_outcome, "original_check": "incorrect", "corrected_outcome": "successful",
        "corrected_outcome_quote": "left over from an earlier answer"})
    assert response.status_code == 200, response.text
    with local_database, _cursor(local_database) as cur:
        cur.execute("""SELECT outcome_check, corrected_outcome, corrected_outcome_quote
                       FROM validation_queue WHERE record_id = %s AND is_validated""", (record,))
        assert dict(cur.fetchone()) == {"outcome_check": "not_applicable", "corrected_outcome": None,
                                        "corrected_outcome_quote": None}
        cur.execute("SELECT validator_1->>'outcome_check' AS check FROM unvalidated WHERE record_id = %s",
                    (record,))
        assert cur.fetchone()["check"] == "not_applicable"


def test_a_reproduction_can_be_wrong_paper_with_no_axes(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        record = _record(cur, 1, type="reproduction", outcome=None)
        _hold(cur, record, who["coder_id"])
    response = client.post("/api/judge", json={
        "record_id": record, "type_check": "correct", "original_check": "incorrect",
        "outcome_check": "not_applicable"})
    assert response.status_code == 200, response.text


def _two_wrong(client, who, record, coders):
    for coder in coders:
        who["coder_id"] = coder
        response = client.post("/api/judge", json={
            "record_id": record, "type_check": "correct", "original_check": "incorrect",
            "outcome_check": "not_applicable"})
        assert response.status_code == 200, response.text


def test_two_wrong_papers_wait_for_an_admin_who_can_only_remove_them(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana, bob = _validator(cur, "ana"), _validator(cur, "bob")
        wrong, sibling = _record(cur, 1), _record(cur, 2)
        for coder in (ana, bob):
            _hold(cur, wrong, coder)
    _two_wrong(client, who, wrong, (ana, bob))

    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT validation_status, final_outcome, final_doi_o FROM unvalidated WHERE record_id = %s",
                    (wrong,))
        row = dict(cur.fetchone())
    assert row == {"validation_status": "need_review", "final_outcome": None, "final_doi_o": None}

    detail = client.get(f"/api/admin/entries/{wrong}").json()
    assert detail["original_removal"] is True
    assert {o["record_id"] for o in detail["paper_originals"]} == {wrong, sibling}
    listed = client.get("/api/admin/entries", params={"filter": "original_removal"}).json()
    assert [e["record_id"] for e in listed["entries"]] == [wrong]
    assert listed["counts"]["original_removal"] == 1

    assert client.post(f"/api/admin/entries/{sibling}/remove-original").status_code == 409
    assert client.post(f"/api/admin/entries/{wrong}/remove-original").status_code == 200
    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT validation_status FROM unvalidated WHERE record_id = %s", (wrong,))
        assert cur.fetchone()["validation_status"] == "rejected"
        cur.execute("SELECT validation_status FROM unvalidated WHERE record_id = %s", (sibling,))
        assert cur.fetchone()["validation_status"] == "unvalidated"
        cur.execute("SELECT COUNT(*) AS n FROM validated WHERE record_id = %s", (wrong,))
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT action FROM admin_decisions WHERE record_id = %s", (wrong,))
        assert cur.fetchone()["action"] == "original_removed"
    # Repeating it is refused rather than logged again.
    assert client.post(f"/api/admin/entries/{wrong}/remove-original").status_code == 409


def test_a_replacement_keeps_the_record_out_of_the_removal_path(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana, bob = _validator(cur, "ana"), _validator(cur, "bob")
        record = _record(cur, 1)
        for coder in (ana, bob):
            _hold(cur, record, coder)
    who["coder_id"] = ana
    client.post("/api/judge", json={"record_id": record, "type_check": "correct",
                                    "original_check": "incorrect", "outcome_check": "not_applicable"})
    who["coder_id"] = bob
    client.post("/api/judge", json={"record_id": record, "type_check": "correct", "original_check": "incorrect",
                                    "outcome_check": "correct", "corrected_doi_o": "10.9/right"})
    detail = client.get(f"/api/admin/entries/{record}").json()
    assert detail["original_removal"] is False


def test_the_audit_filter_finds_shared_checks(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        shared, alone = _record(cur, 1), _record(cur, 2, doi_r="10.9/alone")
        _hold(cur, shared, who["coder_id"])
        _hold(cur, alone, who["coder_id"])
    _judge(client, shared, group="g", shared=True)
    _judge(client, alone, group="h", shared=False)
    listed = client.get("/api/admin/entries", params={"filter": "shared_outcome"}).json()
    assert [e["record_id"] for e in listed["entries"]] == [shared]
    assert listed["counts"]["shared_outcome"] == 1


def test_the_outcome_check_constraint_refuses_anything_else(local_database):
    conn = local_database
    with conn, _cursor(conn) as cur:
        record = _record(cur, 1)
    with pytest.raises(psycopg2.errors.CheckViolation):
        with conn, _cursor(conn) as cur:
            cur.execute("UPDATE validation_queue SET outcome_check = 'maybe' WHERE record_id = %s", (record,))
