"""The paper review against a real PostgreSQL (opt-in).

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
    assert set(held) == {served, free}
    # Only the record the validator was served is started: the rest are short holds.
    assert held[served]["started_at"] and held[free]["started_at"] is None

    # Repeating it (a reload, a resumed draft) takes nothing more.
    again = client.post("/api/paper-review/claim", json={"record_id": served}).json()
    assert [o["queue_id"] for o in again["originals"] if o["judgeable"]] == [
        o["queue_id"] for o in response.json()["originals"] if o["judgeable"]]


def _age_holds(cur, minutes, *record_ids):
    cur.execute("""UPDATE validation_queue SET shown_at = NOW() - make_interval(mins => %s)
                   WHERE record_id = ANY(%s::uuid[]) AND validator_id IS NOT NULL""",
                (minutes, list(record_ids)))


def _holder(cur, record_id):
    cur.execute("""SELECT array_agg(validator_id) AS who FROM validation_queue
                   WHERE record_id = %s AND validator_id IS NOT NULL""", (record_id,))
    return cur.fetchone()["who"] or []


def test_a_buffered_sibling_is_kept_on_its_short_hold(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        served, buffered = _record(cur, 1), _record(cur, 2)
        _hold(cur, served, ana)
        _hold(cur, buffered, ana, started=False)
        _age_holds(cur, 40, buffered)
    client.post("/api/paper-review/claim", json={"record_id": served})
    with local_database, _cursor(local_database) as cur:
        cur.execute("""SELECT started_at, shown_at > NOW() - INTERVAL '1 minute' AS fresh
                       FROM validation_queue WHERE validator_id = %s AND record_id = %s""",
                    (ana, buffered))
        assert dict(cur.fetchone()) == {"started_at": None, "fresh": True}


def test_an_abandoned_paper_frees_itself_and_the_validator_can_come_back(api, local_database):
    """One click used to hold every slot for 5 days. Now the reaper takes the holds
    back, the served record keeps its lock, and it reopens the table."""
    from tests.test_dashboard_split import flora
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        served, second, third = _record(cur, 1), _record(cur, 2), _record(cur, 3)
        _hold(cur, served, ana)
    assert client.post("/api/paper-review/claim", json={"record_id": served}).status_code == 200
    with local_database, _cursor(local_database) as cur:
        _age_holds(cur, 46, second, third)
    flora._reap_stale_slots()
    with local_database, _cursor(local_database) as cur:
        assert _holder(cur, second) == [] and _holder(cur, third) == []
        assert _holder(cur, served) == [ana]
        cur.execute("SELECT validation_status FROM unvalidated WHERE record_id = %s", (second,))
        assert cur.fetchone()["validation_status"] == "unvalidated"

    pairs = client.post("/api/next-pairs", params={"count": 1}).json()["pairs"]
    assert [str(p["record_id"]) for p in pairs] == [served] and pairs[0]["resumed"] is True
    again = client.post("/api/paper-review/claim", json={"record_id": served}).json()["originals"]
    assert all(o["judgeable"] for o in again) and len(again) == 3


def test_a_refresh_keeps_an_open_table_alive_and_says_what_is_held(api, local_database):
    from tests.test_dashboard_split import flora
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        bob = _validator(cur, "bob")
        served, kept, forgotten, judged = (_record(cur, n) for n in range(1, 5))
        foreign = _record(cur, 5, doi_r="10.9/other")
        _hold(cur, served, ana)
        _hold(cur, foreign, bob, started=False)
    client.post("/api/paper-review/claim", json={"record_id": served})
    with local_database, _cursor(local_database) as cur:
        cur.execute("""UPDATE validation_queue SET is_validated = TRUE WHERE record_id = %s
                       AND validator_id = %s""", (judged, ana))
        _age_holds(cur, 44, kept, forgotten, foreign)

    response = client.post("/api/paper-review/refresh", json={"record_ids": [served, kept, foreign, judged]})
    assert response.status_code == 200, response.text
    assert sorted(response.json()["held"]) == sorted([served, kept])

    with local_database, _cursor(local_database) as cur:
        cur.execute("""UPDATE validation_queue SET shown_at = shown_at - INTERVAL '2 minutes'
                       WHERE record_id = ANY(%s::uuid[]) AND validator_id IS NOT NULL""",
                    ([kept, forgotten, foreign],))
    flora._reap_stale_slots()
    with local_database, _cursor(local_database) as cur:
        assert _holder(cur, kept) == [ana]
        assert _holder(cur, forgotten) == []      # not in the refresh, so it lapsed
        assert _holder(cur, foreign) == []        # someone else's hold is not ours to renew
    # The table learns that the row has gone.
    assert sorted(client.post("/api/paper-review/refresh",
                              json={"record_ids": [kept, forgotten]}).json()["held"]) == [kept]


def test_a_refresh_never_waits(api, local_database):
    """It runs on a timer beside everything else that touches these rows."""
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        record = _record(cur, 1)
        _hold(cur, record, ana, started=False)
    other = psycopg2.connect(os.environ["DATABASE_URL"])
    answer = {}
    try:
        with other.cursor() as locker:
            locker.execute("SELECT 1 FROM validation_queue WHERE record_id = %s AND validator_id = %s FOR UPDATE",
                           (record, ana))
            call = threading.Thread(target=lambda: answer.update(response=client.post(
                "/api/paper-review/refresh", json={"record_ids": [record]})), daemon=True)
            call.start()
            call.join(6)
            assert "response" in answer, "the refresh waited for a row lock"
    finally:
        other.rollback()
        other.close()
    assert answer["response"].json()["held"] == [record]


def test_the_refresh_is_off_with_the_flag_and_refuses_junk(api, monkeypatch):
    client, _ = api
    assert client.post("/api/paper-review/refresh", json={"record_ids": ["nope"]}).status_code == 422
    too_many = [str(uuid.uuid4()) for _ in range(51)]
    assert client.post("/api/paper-review/refresh", json={"record_ids": too_many}).status_code == 422
    monkeypatch.delenv("PAPER_REVIEW_ENABLED")
    assert client.post("/api/paper-review/refresh", json={"record_ids": []}).status_code == 404


def test_a_held_row_on_a_short_hold_is_judged_like_any_other(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        served, sibling = _record(cur, 1), _record(cur, 2)
        _hold(cur, served, ana)
    client.post("/api/paper-review/claim", json={"record_id": served})
    response = _judge(client, sibling, group=served)
    assert response.status_code == 200, response.text


def test_a_slot_reaped_and_retaken_cannot_be_submitted_over_the_new_holder(api, local_database):
    """The server half of "never lose answers silently": the table sees this through
    the refresh; a judgement that still gets here is refused, not stored on a slot
    that belongs to someone else."""
    from tests.test_dashboard_split import flora
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        bob, cat = _validator(cur, "bob"), _validator(cur, "cat")
        served, sibling = _record(cur, 1), _record(cur, 2)
        _hold(cur, served, ana)
    client.post("/api/paper-review/claim", json={"record_id": served})
    with local_database, _cursor(local_database) as cur:
        _age_holds(cur, 46, sibling)
    flora._reap_stale_slots()
    with local_database, _cursor(local_database) as cur:
        _hold(cur, sibling, bob, started=False)
        _hold(cur, sibling, cat, started=False)
    assert client.post("/api/paper-review/refresh", json={"record_ids": [served, sibling]}).json()["held"] == [served]
    originals = client.post("/api/paper-review/claim", json={"record_id": served}).json()["originals"]
    assert {o["record_id"]: o["judgeable"] for o in originals} == {served: True, sibling: False}
    refused = _judge(client, sibling, group=served)
    assert refused.status_code == 400 and "No open slot" in refused.text


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


def _during_gemini(client, who, judge_as, record, note=None):
    """Run a judgement in the background, stopped inside its Gemini call: it holds the
    record's row lock there, as a slow Gemini does. Returns (release, thread)."""
    inside, release = threading.Event(), threading.Event()

    def slow_gemini(*_, **__):
        inside.set()
        release.wait(20)
        return {"error": "no LLM in tests"}

    def run():
        with patch("consensus_engine.run_llm_validation", side_effect=slow_gemini):
            checks = {"paper_review": note} if note else None
            client.post("/api/judge", json={"record_id": record, "type_check": "correct",
                                            "original_check": "correct", "outcome_check": "correct",
                                            "additional_checks": checks})

    who["coder_id"] = judge_as
    thread = threading.Thread(target=run)
    thread.start()
    assert inside.wait(10), "the judgement never reached Gemini"
    return release, thread


def _claim_in_time(client, record, seconds=6):
    """The claim's response, or None if it was still waiting after `seconds`."""
    answer = {}
    claim = threading.Thread(target=lambda: answer.update(
        response=client.post("/api/paper-review/claim", json={"record_id": record})), daemon=True)
    claim.start()
    claim.join(seconds)
    return claim, answer.get("response")


def test_a_claim_does_not_wait_behind_another_validators_gemini_call(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana, bob, cat = (_validator(cur, h) for h in ("ana", "bob", "cat"))
        busy, mine, free = _record(cur, 1), _record(cur, 2), _record(cur, 3)
        _hold(cur, busy, bob)
        _hold(cur, busy, cat)
        _hold(cur, mine, ana)
        cur.execute("""UPDATE validation_queue SET is_validated = TRUE, type_check = 'correct',
                              original_check = 'correct', outcome_check = 'correct'
                       WHERE record_id = %s AND validator_id = %s""", (busy, cat))
    release, judging = _during_gemini(client, who, bob, busy)
    who["coder_id"] = ana
    try:
        claim, response = _claim_in_time(client, mine)
    finally:
        release.set()
        judging.join(20)
    claim.join(20)
    assert response is not None, "the claim waited for Gemini"
    assert response.status_code == 200, response.text
    judgeable = {o["record_id"]: o["judgeable"] for o in response.json()["originals"]}
    assert judgeable == {busy: False, mine: True, free: True}


def test_a_claim_does_not_wait_behind_this_validators_own_gemini_call(api, local_database):
    """Submitting a table sends one judgement per record while the next paper opens."""
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana, bob = _validator(cur, "ana"), _validator(cur, "bob")
        first, next_paper, free = _record(cur, 1), _record(cur, 2, doi_r="10.9/next"), _record(cur, 3, doi_r="10.9/next")
        _hold(cur, first, ana)
        _hold(cur, first, bob)
        _hold(cur, next_paper, ana)
        cur.execute("""UPDATE validation_queue SET is_validated = TRUE, type_check = 'correct',
                              original_check = 'correct', outcome_check = 'correct'
                       WHERE record_id = %s AND validator_id = %s""", (first, bob))
    release, judging = _during_gemini(client, who, ana, first,
                                      note={"outcome_group": first, "outcome_shared": False, "originals": 1})
    try:
        claim, response = _claim_in_time(client, next_paper)
    finally:
        release.set()
        judging.join(20)
    claim.join(20)
    assert response is not None, "the claim waited for this validator's own judgement"
    assert {o["record_id"]: o["judgeable"] for o in response.json()["originals"]} == {next_paper: True, free: True}


def test_a_record_lost_to_a_race_does_not_end_the_session(api, local_database, monkeypatch):
    """One locked candidate must not show the done screen while another is free. The
    flag does not matter: this is ordinary serving."""
    from tests.test_dashboard_split import flora
    monkeypatch.delenv("PAPER_REVIEW_ENABLED")
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        locked, free = _record(cur, 1, doi_r="10.9/a"), _record(cur, 2, doi_r="10.9/b")
    real = flora._select_pair_candidate
    tried = []

    def locked_first(cur, coder_id, mode, extra_where="", extra_params=()):
        """RANDOM() would hide the bug half the time: the locked record comes up first."""
        if not tried:
            tried.append(locked)
            cur.execute(f"SELECT {flora._PAIR_SELECT} FROM unvalidated u "
                        "LEFT JOIN record_metadata rm ON rm.record_id = u.record_id "
                        "WHERE u.record_id = %s", (locked,))
            return cur.fetchone()
        tried.append(extra_where)
        return real(cur, coder_id, mode, extra_where, extra_params)

    other = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with other.cursor() as locker:
            locker.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE", (locked,))
            monkeypatch.setattr(flora, "_select_pair_candidate", locked_first)
            pairs = client.post("/api/next-pairs", params={"count": 1}).json()["pairs"]
    finally:
        other.rollback()
        other.close()
    assert [str(p["record_id"]) for p in pairs] == [free]


def test_the_claim_loop_ends_when_every_candidate_is_refused(api, local_database, monkeypatch):
    """_claim_free_slot also says no when this validator already holds a slot, which a
    candidate query cannot see in a race. The loop must still end, and rolls the
    priority pools once."""
    from tests.test_dashboard_split import flora
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        records = {_record(cur, n, doi_r=f"10.9/{n}") for n in range(3)}
        cur.execute("UPDATE serving_config SET enabled = TRUE, priority_outcome = 'failed', priority_share = 50")
    refused, rolls = [], []
    monkeypatch.setattr(flora, "_claim_free_slot",
                        lambda cur, coder, record, started: refused.append(str(record)))
    monkeypatch.setattr(flora.random, "random", lambda: rolls.append(1) or 0.1)
    with local_database, _cursor(local_database) as cur:
        assert flora._claim_one_pair(cur, ana, started=False) is None
    assert sorted(refused) == sorted(records)   # each tried once, then no candidate left
    assert len(rolls) == 1


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
        _judge(client, records[0], group=records[0], shared=True).json()["points_earned"],
        _judge(client, records[1], group=records[0], shared=True).json()["points_earned"],
        _judge(client, records[2], group=records[0], shared=True,
               checks={"was_unsure_original": True}, original="incorrect").json()["points_earned"],
        _judge(client, records[3], group=records[0], shared=True, original="incorrect",
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
    assert _judge(client, agreeing, group=agreeing).json()["points_earned"] == 14
    assert _judge(client, correcting, group=correcting, outcome="incorrect",
                  corrected_outcome="successful").json()["points_earned"] == 14


def test_a_note_pays_once_per_paper_and_no_outcome_pays_no_check(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        who["coder_id"] = _validator(cur, "ana")
        first, second = _record(cur, 1), _record(cur, 2)
        _hold(cur, first, who["coder_id"])
        _hold(cur, second, who["coder_id"])
    assert _judge(client, first, group=first, validator_notes="See table 2").json()["points_earned"] == 15
    assert _judge(client, second, group=f"none:{second}", original="incorrect", outcome="not_applicable",
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
    assert _judge(client, first, group=f"none:{first}", **fields, **body).json()["points_earned"] == 10
    assert _judge(client, second, group=f"none:{second}", **fields, **body).json()["points_earned"] == 0


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


OTHER_ABSTRACT = "We propose to repeat a survey of sleep habits among students next year."
# paper_originals as it was before the paper's copies were compared.
ORIGINAL_FIELDS = {"record_id", "title_o", "final_title_o", "year_o", "doi_o", "final_doi_o", "outcome",
                   "final_outcome", "validation_status", "authors_o", "original_rank", "original_checks",
                   "outcome_checks", "shared_check", "is_current"}


def _set_columns(cur, record_id, **columns):
    assignments = ", ".join(f"{name} = %s" for name in columns)
    cur.execute(f"UPDATE unvalidated SET {assignments} WHERE record_id = %s", (*columns.values(), record_id))


def test_the_detail_names_what_the_originals_would_publish_differently(api, local_database):
    client, _ = api
    with local_database, _cursor(local_database) as cur:
        first = _record(cur, 1, status="consensus_reached")
        second = _record(cur, 2, status="validated", abstract=OTHER_ABSTRACT)
        _set_columns(cur, first, url_r="https://doi.org/10.9/paper/")
        _set_columns(cur, second, url_r="http://dx.doi.org/10.9/PAPER", final_url_r="https://osf.io/abc")

    detail = client.get(f"/api/admin/entries/{first}").json()
    assert detail["paper_differs_on"] == ["url_r", "abstract_r"]
    assert {o["record_id"] for o in detail["paper_originals"]} == {first, second}
    # Compared, then dropped: the rows keep their shape, and no copy travels with them.
    assert [set(o) for o in detail["paper_originals"]] == [ORIGINAL_FIELDS, ORIGINAL_FIELDS]
    sent = json.dumps(detail["paper_originals"])
    assert OTHER_ABSTRACT not in sent and "osf.io" not in sent

    # The same link in other clothes, once the correction is gone.
    with local_database, _cursor(local_database) as cur:
        _set_columns(cur, second, final_url_r=None, final_abstract_r=QUOTE)
        _set_columns(cur, first, abstract_r=f"Abstract: {QUOTE}")
    assert client.get(f"/api/admin/entries/{second}").json()["paper_differs_on"] == []


@pytest.mark.parametrize("status", ["rejected", "need_review"])
def test_a_sibling_not_on_its_way_to_flora_differs_from_nothing(api, local_database, status):
    client, _ = api
    with local_database, _cursor(local_database) as cur:
        first = _record(cur, 1, status="consensus_reached")
        second = _record(cur, 2, status="validated")
        sibling = _record(cur, 3, status=status, abstract=OTHER_ABSTRACT)
        _set_columns(cur, sibling, url_r="https://osf.io/abc")

    detail = client.get(f"/api/admin/entries/{first}").json()
    assert len(detail["paper_originals"]) == 3, "it is still listed"
    assert detail["paper_differs_on"] == []


def test_a_merged_duplicate_repeats_its_survivor_and_stops_counting(api, local_database):
    client, _ = api
    with local_database, _cursor(local_database) as cur:
        survivor = _record(cur, 1, status="validated")
        cur.execute(
            """INSERT INTO validated (record_id, doi_r, study_r, title_r, abstract_r, doi_o, study_o,
                                      title_o, type, outcome, outcome_quote, admin_approved)
               SELECT record_id, doi_r, study_r, title_r, abstract_r, doi_o, study_o, title_o,
                      type, outcome, outcome_quote, TRUE
               FROM unvalidated WHERE record_id = %s""",
            (survivor,))
        duplicate = _record(cur, 1, status="consensus_reached", abstract=OTHER_ABSTRACT)
    assert client.get(f"/api/admin/entries/{survivor}").json()["paper_differs_on"] == ["abstract_r"]

    merged = client.post(f"/api/admin/entries/{duplicate}/resolve", json={
        "admin_name": "boss", "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
        "merge_into_record_id": survivor})
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged"] is True

    detail = client.get(f"/api/admin/entries/{survivor}").json()
    assert {o["record_id"]: o["validation_status"] for o in detail["paper_originals"]} == {
        survivor: "validated", duplicate: "rejected"}
    assert detail["paper_differs_on"] == []


def _publish(cur, record_id):
    cur.execute(
        """INSERT INTO validated (record_id, doi_r, study_r, title_r, url_r, abstract_r, doi_o, study_o,
                                  title_o, type, outcome, outcome_quote, admin_approved)
           SELECT record_id, doi_r, study_r, title_r, url_r, abstract_r, doi_o, study_o, title_o,
                  type, outcome, outcome_quote, TRUE
           FROM unvalidated WHERE record_id = %s""",
        (record_id,))


def test_a_validated_record_counts_with_the_copy_it_published(api, local_database):
    """An import rewrites the extracted value and leaves the published row alone."""
    client, _ = api
    with local_database, _cursor(local_database) as cur:
        published = _record(cur, 1, status="validated")
        waiting = _record(cur, 2, status="consensus_reached")
        for record in (published, waiting):
            _set_columns(cur, record, url_r="https://osf.io/published")
        _publish(cur, published)
        _set_columns(cur, published, url_r="https://osf.io/reimported", abstract_r=OTHER_ABSTRACT)
    detail = client.get(f"/api/admin/entries/{waiting}").json()
    assert detail["paper_differs_on"] == []
    assert len(detail["paper_originals"]) == 2

    with local_database, _cursor(local_database) as cur:
        _set_columns(cur, waiting, url_r="https://osf.io/reimported")
    assert client.get(f"/api/admin/entries/{waiting}").json()["paper_differs_on"] == ["url_r"]


def test_an_old_published_row_does_not_speak_for_a_record_still_waiting(api, local_database):
    """Approving replaces that row, so what counts is what approving would publish."""
    client, _ = api
    with local_database, _cursor(local_database) as cur:
        reopened = _record(cur, 1, status="consensus_reached")
        other = _record(cur, 2, status="consensus_reached")
        _publish(cur, reopened)
        _set_columns(cur, reopened, url_r="https://osf.io/new")
        _set_columns(cur, other, url_r="https://osf.io/other")
    assert client.get(f"/api/admin/entries/{other}").json()["paper_differs_on"] == ["url_r"]


def test_a_paper_of_one_record_has_nothing_to_differ_on(api, local_database):
    client, _ = api
    with local_database, _cursor(local_database) as cur:
        alone = _record(cur, 1, status="consensus_reached")
        no_doi = _record(cur, 2, status="consensus_reached", doi_r="")
    for record in (alone, no_doi):
        detail = client.get(f"/api/admin/entries/{record}").json()
        assert (detail["paper_originals"], detail["paper_differs_on"]) == ([], [])


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


def test_removing_an_original_refuses_while_added_originals_are_live(api, local_database):
    """The screen says the paper's other originals stay as they are, so an added one
    is never taken along: the admin removes it first, and nothing changes until then."""
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana, bob = _validator(cur, "ana"), _validator(cur, "bob")
        wrong = _record(cur, 1)
        for coder in (ana, bob):
            _hold(cur, wrong, coder)
    _two_wrong(client, who, wrong, (ana, bob))
    with local_database, _cursor(local_database) as cur:
        cur.execute("""INSERT INTO unvalidated (doi_r, type, outcome, validation_status, title_r,
                                                doi_o, title_o, study_o, study_r, origin, parent_record_id)
                       VALUES (%s, 'replication', 'failed', 'validated', 'The paper', '10.9/added',
                               'Added', '', '', 'admin_extra_original', %s)
                       RETURNING record_id::text AS id""", (DOI, wrong))
        child = cur.fetchone()["id"]
        cur.execute("""INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o,
                                              title_o, type, outcome)
                       VALUES (%s, %s, '', 'The paper', '10.9/added', '', 'Added', 'replication', 'failed')""",
                    (child, DOI))

    assert client.get(f"/api/admin/entries/{wrong}").json()["extra_originals_summary"]["children"] == 1
    refused = client.post(f"/api/admin/entries/{wrong}/remove-original")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "original_removal_blocked_by_added_originals"
    assert "Remove them first" in refused.json()["detail"]["message"]
    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT record_id::text AS id, validation_status FROM unvalidated "
                    "WHERE record_id = ANY(%s::uuid[])", ([wrong, child],))
        assert {r["id"]: r["validation_status"] for r in cur.fetchall()} == {
            wrong: "need_review", child: "validated"}
        cur.execute("SELECT COUNT(*) AS n FROM validated WHERE record_id = %s", (child,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT COUNT(*) AS n FROM admin_decisions WHERE record_id = ANY(%s::uuid[])",
                    ([wrong, child],))
        assert cur.fetchone()["n"] == 0

    # Once the added original has gone, the original can go too.
    with local_database, _cursor(local_database) as cur:
        cur.execute("UPDATE unvalidated SET validation_status = 'rejected' WHERE record_id = %s", (child,))
        cur.execute("DELETE FROM validated WHERE record_id = %s", (child,))
    assert client.post(f"/api/admin/entries/{wrong}/remove-original").status_code == 200


def test_the_rollback_check_lists_no_outcome_records_past_review(local_database):
    """The query in docs/PROJECT.md is what a rollback relies on, so it is run here."""
    import re
    from pathlib import Path
    project = (Path(__file__).resolve().parent.parent / "docs" / "PROJECT.md").read_text(encoding="utf-8")
    query = re.search(r"```sql\n(-- rollback-check.*?);\n```", project, re.S).group(1)
    with local_database, _cursor(local_database) as cur:
        waiting = _record(cur, 1, status="consensus_reached")
        published = _record(cur, 2, status="validated")
        in_review = _record(cur, 3, status="need_review")
        plain = _record(cur, 4, status="consensus_reached")
        cur.execute("""UPDATE validation_queue SET is_validated = TRUE, type_check = 'correct',
                              original_check = 'incorrect', outcome_check = 'not_applicable'
                       WHERE record_id = ANY(%s::uuid[]) AND validator_slot IN ('human_1', 'human_2')""",
                    ([waiting, published, in_review],))
        cur.execute("""UPDATE validation_queue SET is_validated = TRUE, type_check = 'correct',
                              original_check = 'correct', outcome_check = 'correct'
                       WHERE record_id = %s AND validator_slot IN ('human_1', 'human_2')""", (plain,))
        cur.execute(query)
        assert {str(r["record_id"]) for r in cur.fetchall()} == {waiting, published}


def _rekey(conn, record, *, doi_o="10.9/corrected", doi_r=DOI, title_o="Corrected original"):
    """The nightly import takes a new pair at this record's work and rank."""
    import pandas as pd
    import csv_to_db
    row = pd.Series({"doi_r": doi_r, "doi_o": doi_o, "title_o": title_o, "title_r": "The paper", "study_r": "",
                     "study_o": "", "type": "replication", "outcome": "failed", "outcome_phrase": QUOTE,
                     "abstract_r": "We replicated two studies."})
    with conn, _cursor(conn) as cur:
        cur.execute("""SELECT u.record_id::text AS record_id, u.pair_id, u.validation_status,
                              EXISTS (SELECT 1 FROM validation_queue q WHERE q.record_id = u.record_id
                                      AND (q.is_shown OR q.is_validated)) AS has_validator_activity
                       FROM unvalidated u WHERE u.record_id = %s""", (record,))
        existing = dict(cur.fetchone())
    with conn, conn.cursor() as cur:   # the import's plain cursor
        csv_to_db._refresh_rekeyed_record(
            cur, existing, csv_to_db._build_unvalidated_row(record, f"new-{uuid.uuid4().hex[:8]}", row))


def _removed(api, conn):
    client, who = api
    with conn, _cursor(conn) as cur:
        ana, bob = _validator(cur, "ana"), _validator(cur, "bob")
        record = _record(cur, 1)
        for coder in (ana, bob):
            _hold(cur, record, coder)
    _two_wrong(client, who, record, (ana, bob))
    assert client.post(f"/api/admin/entries/{record}/remove-original").status_code == 200
    return record, ana, bob


def _status(conn, record):
    with conn, _cursor(conn) as cur:
        cur.execute("SELECT validation_status FROM unvalidated WHERE record_id = %s", (record,))
        return cur.fetchone()["validation_status"]


def test_a_corrected_original_after_a_removal_is_validated_from_the_start(api, local_database):
    client, who = api
    record, ana, bob = _removed(api, local_database)
    _rekey(local_database, record)

    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record,))
        row = dict(cur.fetchone())
        cur.execute("""SELECT validator_id, is_shown, is_validated, original_check, outcome_check, started_at
                       FROM validation_queue WHERE record_id = %s AND validator_slot IN ('human_1', 'human_2')""",
                    (record,))
        slots = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT queue_rows, reason, superseded_by, pair_id, unvalidated_row FROM retired_records WHERE record_id = %s", (record,))
        archived = dict(cur.fetchone())
        cur.execute("SELECT action, status_before, status_after, changes FROM admin_decisions WHERE record_id = %s ORDER BY decision_id", (record,))
        decisions = [dict(r) for r in cur.fetchall()]
    assert (row["validation_status"], row["doi_o"], row["title_o"]) == ("unvalidated", "10.9/corrected", "Corrected original")
    assert row["validator_1"] is None and row["validator_2"] is None and row["llm_validator"] is None
    assert row["admin_checked"] is False and row["admin_name"] is None and row["final_doi_o"] is None
    assert "Re-opened by the import" in row["admin_notes"]
    assert slots == [dict(validator_id=None, is_shown=False, is_validated=False, original_check=None,
                          outcome_check=None, started_at=None)] * 2

    # Nothing about the old original is lost.
    assert archived["reason"] == "original_replaced" and archived["superseded_by"] == row["pair_id"]
    assert archived["pair_id"] != row["pair_id"] and archived["unvalidated_row"]["doi_o"] == "10.9/o1"
    assert sorted(q["outcome_check"] for q in archived["queue_rows"] if q["is_validated"]) == ["not_applicable"] * 2
    assert [d["action"] for d in decisions] == ["original_removed", "reopened_by_import"]
    assert (decisions[1]["status_before"], decisions[1]["status_after"]) == ("rejected", "unvalidated")
    assert decisions[1]["changes"]["doi_o"] == ["10.9/o1", "10.9/corrected"]

    # It is served again, to the people who judged the old original too.
    who["coder_id"] = ana
    pairs = client.post("/api/next-pairs", params={"count": 1}).json()["pairs"]
    assert [str(p["record_id"]) for p in pairs] == [record]
    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT total_judgements FROM validators WHERE id = %s", (ana,))
        assert cur.fetchone()["total_judgements"] == 1    # what they did still counts


def test_a_rejected_paper_stays_rejected_when_its_original_changes(api, local_database):
    """"Neither a replication nor a reproduction" is about the paper."""
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana, bob = _validator(cur, "ana"), _validator(cur, "bob")
        record = _record(cur, 1, status="need_review")
    response = client.post(f"/api/admin/entries/{record}/resolve", json={
        "admin_name": "boss", "type_check": "incorrect", "corrected_type": "not_validation",
        "original_check": "correct", "outcome_check": "correct"})
    assert response.status_code == 200, response.text
    _rekey(local_database, record)
    assert _status(local_database, record) == "rejected"
    with local_database, _cursor(local_database) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM retired_records WHERE record_id = %s", (record,))
        assert cur.fetchone()["n"] == 0


def test_a_later_rejection_outranks_an_earlier_removal(api, local_database):
    record, _, _ = _removed(api, local_database)
    with local_database, _cursor(local_database) as cur:
        cur.execute("""INSERT INTO admin_decisions (record_id, admin_handle, action, status_before, status_after)
                       VALUES (%s, 'boss', 'rejected', 'validated', 'rejected')""", (record,))
    _rekey(local_database, record)
    assert _status(local_database, record) == "rejected"


def test_a_removed_original_that_is_shipped_again_stays_removed(api, local_database):
    """A corrected replication DOI re-keys the record too, with the same wrong original."""
    record, _, _ = _removed(api, local_database)
    _rekey(local_database, record, doi_o="10.9/O1", doi_r="10.9/corrected-replication", title_o="Original 1")
    assert _status(local_database, record) == "rejected"


def test_a_reopened_record_rejected_again_unlogged_stays_rejected(api, local_database):
    """A senior's reject, or an agreed "not a replication", leaves no decision row, so
    the old removal was still the newest one and the next re-key reopened the record."""
    record, _, _ = _removed(api, local_database)
    _rekey(local_database, record)
    assert _status(local_database, record) == "unvalidated"
    senior = json.dumps({"type_check": "incorrect", "original_check": "incorrect", "outcome_check": "incorrect",
                         "corrected_type": "not_validation", "senior_reject": True,
                         "additional_checks": {"senior_reject": True}})
    with local_database, _cursor(local_database) as cur:
        cur.execute("UPDATE unvalidated SET validation_status = 'rejected', validator_1 = %s::jsonb "
                    "WHERE record_id = %s", (senior, record))
    _rekey(local_database, record, doi_o="10.9/another", title_o="Another original")
    assert _status(local_database, record) == "rejected"


def test_a_removed_original_with_no_doi_is_told_apart_by_its_title(api, local_database):
    record, _, _ = _removed(api, local_database)
    with local_database, _cursor(local_database) as cur:
        cur.execute("UPDATE unvalidated SET doi_o = '' WHERE record_id = %s", (record,))
    _rekey(local_database, record, doi_o="", title_o="Original 1")
    assert _status(local_database, record) == "rejected"
    _rekey(local_database, record, doi_o="", title_o="A different book")
    assert _status(local_database, record) == "unvalidated"


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
    _judge(client, shared, group=shared, shared=True)
    _judge(client, alone, group=alone, shared=False)
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


def _plain_points(client, who, cur_factory, coder):
    """What an ordinary judgement of a record pays, for comparison."""
    with cur_factory() as cur:
        record = _record(cur, 90, doi_r=f"10.9/{uuid.uuid4().hex[:6]}")
        _hold(cur, record, coder)
    who["coder_id"] = coder
    return client.post("/api/judge", json={"record_id": record, "type_check": "correct", "original_check": "correct",
                                           "outcome_check": "correct"}).json()["points_earned"]


@pytest.mark.parametrize("group", ["made-up", "none:someone-else", "00000000-0000-4000-8000-000000000000", "OTHER_PAPER"])
def test_a_note_the_table_cannot_have_written_earns_ordinary_points(api, local_database, group):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        real, faked = _record(cur, 1), _record(cur, 2)
        elsewhere = _record(cur, 3, doi_r="10.9/elsewhere")
        _hold(cur, real, ana)
        _hold(cur, faked, ana)

    def factory():
        return _Txn(local_database)
    plain = _plain_points(client, who, factory, ana)
    assert _judge(client, real, group=real).status_code == 200
    # Past the paper's base score a real note would pay 2 or 4, so the gap shows.
    earned = _judge(client, faked, group=elsewhere if group == "OTHER_PAPER" else group).json()["points_earned"]
    assert earned == plain


def test_a_note_earns_nothing_special_with_the_flag_off(api, local_database, monkeypatch):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        first, second = _record(cur, 1), _record(cur, 2)
        _hold(cur, first, ana)
        _hold(cur, second, ana)
    monkeypatch.delenv("PAPER_REVIEW_ENABLED")
    plain = _judge(client, first, group=first).json()["points_earned"]
    assert _judge(client, second, group=first).json()["points_earned"] == plain
    assert plain != 2 and plain != 4


def test_a_note_on_a_record_the_table_cannot_open_earns_ordinary_points(api, local_database):
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        first, hard = _record(cur, 1), _record(cur, 2, abstract="")
        _hold(cur, first, ana)
        _hold(cur, hard, ana)

    def factory():
        return _Txn(local_database)
    plain = _plain_points(client, who, factory, ana)
    _judge(client, first, group=first)
    assert _judge(client, hard, group=first).json()["points_earned"] == plain * 2   # the hard pool doubles


class _Txn:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.cur = _cursor(self.conn)
        return self.cur

    def __exit__(self, kind, *_):
        self.conn.commit() if kind is None else self.conn.rollback()
        self.cur.close()


def test_a_malformed_record_id_is_the_callers_error(api):
    client, _ = api
    response = client.post("/api/paper-review/claim", json={"record_id": "not-a-uuid"})
    assert response.status_code == 422


@pytest.mark.parametrize("change", ["validation_status = 'validated'", "validation_status = 'rejected'",
                                    "validation_status = 'need_review'", "restricted_access = TRUE"])
def test_no_slot_is_taken_on_a_record_that_closed_after_it_was_picked(api, local_database, change):
    from tests.test_dashboard_split import flora
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana = who["coder_id"] = _validator(cur, "ana")
        record = _record(cur, 1)
        cur.execute(f"UPDATE unvalidated SET {change} WHERE record_id = %s", (record,))
    with local_database, _cursor(local_database) as cur:
        assert flora._claim_free_slot(cur, ana, record, False) is None
        assert _holder(cur, record) == []


def test_no_outcome_is_left_out_of_the_agreement_figures(api, local_database):
    """Two validators who agree an original is wrong have not agreed on an outcome."""
    client, who = api
    with local_database, _cursor(local_database) as cur:
        ana, bob = _validator(cur, "ana"), _validator(cur, "bob")
        wrong, agreed, split = _record(cur, 1), _record(cur, 2), _record(cur, 3)
        for record in (wrong, agreed, split):
            for coder in (ana, bob):
                _hold(cur, record, coder)
    _two_wrong(client, who, wrong, (ana, bob))
    for coder, outcome in ((ana, "correct"), (bob, "correct")):
        who["coder_id"] = coder
        client.post("/api/judge", json={"record_id": agreed, "type_check": "correct", "original_check": "correct",
                                        "outcome_check": outcome})
    for coder, original, outcome in ((ana, "correct", "correct"), (bob, "incorrect", "not_applicable")):
        who["coder_id"] = coder
        client.post("/api/judge", json={"record_id": split, "type_check": "correct", "original_check": original,
                                        "outcome_check": outcome})

    quality = client.get("/api/admin/dashboard").json()["quality"]
    assert quality["records_with_2_validators"] == 3
    assert quality["full_agreements"] == 1 and quality["agreement_rate"] == 1.0   # one record compared
    assert quality["records_compared"] == 1     # the figure the card's "1 of N records" is out of

    entries = {e["record_id"]: e for e in client.get("/api/admin/entries", params={"filter": "all"}).json()["entries"]}
    assert entries[wrong]["agreement_pct"] == 100     # scored on type and original
    assert entries[agreed]["agreement_pct"] == 100
    assert entries[split]["agreement_pct"] == 50
