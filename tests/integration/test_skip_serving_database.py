"""A skipped record goes to the end of that validator's queue, against a real PostgreSQL
(opt-in).

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database, as for
tests/test_preparation_database.py. Without it every test here is skipped.

Serving breaks ties at random, so a record that must come last usually has a finished
judgement here: without the skip it would come first.
"""
import os
import uuid

import psycopg2
import pytest

import consensus_engine
from tests.test_dashboard_split import flora
from tests.integration.test_decided_records import JUDGE, env, judged, one, record, run, validator  # noqa: F401
from tests.pg_fixtures import local_database  # noqa: F401


def serve(client, count=1, mode="normal", buffered=False):
    response = client.post("/api/next-pairs", params={"count": count, "mode": mode, "buffered_only": buffered})
    assert response.status_code == 200, response.text
    pairs = response.json()["pairs"]
    assert not any("last_skipped_at" in pair for pair in pairs)
    return pairs


def ids(pairs):
    return [pair["record_id"] for pair in pairs]


def marked(pairs):
    return [(pair["record_id"], pair["skipped_before"]) for pair in pairs]


def skip(client, record_id, reason="prefer_another"):
    response = client.post("/api/skip", json={"record_id": record_id, "reason_code": reason, "comment": "why"})
    assert response.status_code == 200, response.text
    return response.json()["skip_summary"]


def skipped(conn, record_id, coder, minutes=5, reason="prefer_another"):
    run(conn, "INSERT INTO validation_skips (record_id, validator_id, reason_code, skipped_at) "
              "VALUES (%s, %s, %s, NOW() - %s * INTERVAL '1 minute')", record_id, coder, reason, minutes)


def half_judged(conn, **columns):
    record_id = record(conn, **columns)
    judged(conn, record_id, validator(conn, uuid.uuid4().hex[:8]), "human_1")
    return record_id


def test_a_half_judged_record_does_not_come_straight_back(env):
    conn, client, state = env
    state["coder"] = validator(conn, "ana")
    half = half_judged(conn)
    others = {record(conn) for _ in range(3)}
    assert ids(serve(client)) == [half]
    skip(client, half)
    got = serve(client, 4)
    assert set(ids(got[:3])) == others
    assert marked(got[3:]) == [(half, True)]


def test_skipped_records_return_longest_ago_first(env):
    conn, client, state = env
    ana = state["coder"] = validator(conn, "ana")
    early, late, again, fresh = record(conn), half_judged(conn), record(conn), record(conn)
    skipped(conn, early, ana, 30)
    skipped(conn, late, ana, 20)
    skipped(conn, again, ana, 40)
    skipped(conn, again, ana, 10)
    assert ids(serve(client, 4, buffered=True)) == [fresh, early, late, again]


def test_skipping_it_again_moves_it_to_the_end(env):
    conn, client, state = env
    ana = state["coder"] = validator(conn, "ana")
    first, second = record(conn), record(conn)
    skipped(conn, first, ana, 30)
    skipped(conn, second, ana, 20)
    assert ids(serve(client)) == [first]
    skip(client, first)
    assert ids(serve(client, 2)) == [second, first]


def test_a_skip_is_this_validators_own(env):
    conn, client, state = env
    ana, bob = validator(conn, "ana"), validator(conn, "bob")
    half, other = half_judged(conn), record(conn)
    skipped(conn, half, ana)
    state["coder"] = bob
    assert marked(serve(client, buffered=True)) == [(half, False)]
    run(conn, "UPDATE validation_queue SET validator_id = NULL, is_shown = FALSE WHERE validator_id = %s", bob)
    state["coder"] = ana
    assert marked(serve(client, 2, buffered=True)) == [(other, False), (half, True)]


def test_a_historical_submission_failed_is_not_a_skip(env):
    conn, client, state = env
    ana = state["coder"] = validator(conn, "ana")
    half, _ = half_judged(conn), record(conn)
    skipped(conn, half, ana, reason="submission_failed")
    assert marked(serve(client)) == [(half, False)]
    resumed = serve(client)
    assert resumed[0]["resumed"] and marked(resumed) == [(half, False)]


def test_a_lapsed_hold_is_not_a_skip(env):
    conn, client, state = env
    ana = state["coder"] = validator(conn, "ana")
    half, _ = half_judged(conn), record(conn)
    assert ids(serve(client, buffered=True)) == [half]
    run(conn, "UPDATE validation_queue SET shown_at = NOW() - INTERVAL '46 minutes' WHERE validator_id = %s", ana)
    flora._reap_stale_slots()
    assert marked(serve(client, buffered=True)) == [(half, False)]


def test_a_failed_submission_release_is_not_a_skip(env, monkeypatch):
    conn, client, state = env
    state["coder"] = validator(conn, "ana")
    half, _ = half_judged(conn), record(conn)
    assert ids(serve(client)) == [half]
    with monkeypatch.context() as broken:
        broken.setattr(consensus_engine, "evaluate_consensus", lambda *_: 1 / 0)
        failed = client.post("/api/judge", json={"record_id": half, "submission_id": str(uuid.uuid4()), **JUDGE})
    assert failed.status_code == 500, failed.text
    released = client.post("/api/submission-failures/release",
                           json={"failure_stamp": failed.json()["detail"]["failure_stamp"]})
    assert released.json()["released"] is True, released.text
    again = serve(client)
    assert not again[0]["resumed"] and marked(again) == [(half, False)]


@pytest.mark.parametrize("share, skipped_outcome", [(100, "failed"), (0, "successful")])
def test_pool_priority_does_not_lift_a_skipped_record(env, share, skipped_outcome):
    """Whichever pool is drawn first, its only record left is the skipped one."""
    conn, client, state = env
    ana, bob = validator(conn, "ana"), validator(conn, "bob")
    pools = {outcome: record(conn, outcome=outcome) for outcome in ("failed", "successful")}
    # The seeded rule has a year range, which these records are outside.
    run(conn, "UPDATE serving_config SET enabled = TRUE, priority_outcome = 'failed', priority_share = %s, "
              "priority_year_min = NULL, priority_year_max = NULL", share)
    last = pools.pop(skipped_outcome)
    skipped(conn, last, ana)
    (first,) = pools.values()
    state["coder"] = bob   # skipped nothing, so the pool drawn first is the skipped record's
    assert marked(serve(client, 2, buffered=True)) == [(last, False), (first, False)]
    state["coder"] = ana
    assert marked(serve(client, 2, buffered=True)) == [(first, False), (last, True)]


def test_each_mode_puts_its_own_skips_last(env):
    conn, client, state = env
    ana = state["coder"] = validator(conn, "ana")
    for mode, outcome in (("normal", "failed"), ("hard", "cannot_be_determined")):
        last, first = half_judged(conn, outcome=outcome), record(conn, outcome=outcome)
        skipped(conn, last, ana)
        assert marked(serve(client, 3, mode, buffered=True)) == [(first, False), (last, True)]


def test_the_only_record_left_comes_back_marked(env):
    conn, client, state = env
    state["coder"] = validator(conn, "ana")
    only = record(conn)
    for skipped_before in (False, True):
        served, resumed = serve(client), serve(client)
        assert [served[0]["resumed"], resumed[0]["resumed"]] == [False, True]
        assert marked(served) == marked(resumed) == [(only, skipped_before)]
        skip(client, only)


def test_a_locked_record_does_not_end_the_session_with_only_skips_left(env):
    conn, client, state = env
    ana = state["coder"] = validator(conn, "ana")
    last, locked = record(conn), record(conn)
    skipped(conn, last, ana)
    other = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with other.cursor() as cur:
            cur.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE", (locked,))
            assert marked(serve(client, 2, buffered=True)) == [(last, True)]
    finally:
        other.rollback()
        other.close()


def test_an_inaccessible_skip_is_not_served_even_last(env):
    conn, client, state = env
    state["coder"] = validator(conn, "ana")
    only = record(conn)
    assert ids(serve(client)) == [only]
    skip(client, only, "inaccessible")
    assert serve(client) == []


def test_a_record_two_validators_skipped_still_gets_both_judgements(env):
    conn, client, state = env
    coders = [validator(conn, "ana"), validator(conn, "bob")]
    only = record(conn)
    for coder in coders:
        skipped(conn, only, coder)
    for coder in coders:
        state["coder"] = coder
        assert marked(serve(client)) == [(only, True)]
        answer = client.post("/api/judge", json={"record_id": only, **JUDGE})
        assert answer.status_code == 200, answer.text
    assert one(conn, "SELECT COUNT(DISTINCT validator_id) AS n FROM validation_queue WHERE record_id = %s "
                     "AND is_validated AND validator_slot IN ('human_1', 'human_2')", only)["n"] == 2


def test_repeat_skips_by_one_validator_count_once(env):
    conn, client, state = env
    state["coder"] = validator(conn, "ana")
    only = record(conn)
    for _ in range(2):
        assert ids(serve(client)) == [only]
        summary = skip(client, only, "data_quality")
    assert summary == {"event_count": 2, "validator_count": 1, "issue_validator_count": 1,
                       "review_required": False}
