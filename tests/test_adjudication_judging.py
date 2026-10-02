"""The adjudication feature, phase 3: Trusted and Senior validators judge the rows."""
import uuid
from contextlib import contextmanager

import psycopg2
import psycopg2.errors
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg2.extras import RealDictCursor

import adjudication
from adjudication import judging
from tests.test_preparation_database import local_database  # noqa: F401

SAME = {"kind": "same original, different outcome", "flora_doi_o": "10.1/orig",
        "mo_doi_o": "https://doi.org/10.1/ORIG", "flora_outcome": "failed", "mo_outcome": "success"}
DIFFERENT = {"kind": "different original", "flora_doi_o": "10.1000/a", "mo_doi_o": "10.2000/b",
             "flora_outcome": "successful", "mo_outcome": "failure"}
FLORA_ONLY = {"kind": "MO names no original DOI", "flora_doi_o": "10.1/a", "mo_doi_o": None,
              "flora_outcome": "cannot_be_determined", "mo_outcome": "success"}
MO_ONLY = {"kind": "we found no original", "flora_doi_o": None, "mo_doi_o": "10.1/b",
           "flora_outcome": "pending", "mo_outcome": "inconclusive"}
NEITHER = {"kind": "we found no original", "flora_doi_o": None, "mo_doi_o": None,
           "flora_outcome": "pending", "mo_outcome": "success"}


# ---------------------------------------------------------------------------
# What each record asks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("record", "choices"), [
    (SAME, ("both", "neither", "cannot_tell")),
    (DIFFERENT, ("flora", "observatory", "both", "neither", "cannot_tell")),
    (FLORA_ONLY, ("flora", "neither", "cannot_tell")),
    (MO_ONLY, ("observatory", "neither", "cannot_tell")),
    (NEITHER, ("neither", "cannot_tell")),
])
def test_each_record_offers_only_the_answers_that_make_sense(record, choices):
    assert judging.original_choices(record) == choices
    assert judging.doi_required(record) is (record is NEITHER)


@pytest.mark.parametrize("flora_outcome", [
    # Every reproduction outcome FLoRA gives in the PR's file.
    "computational issues, robustness challenges", "not checked, robustness challenges",
    "computationally reproducible, robustness challenges", "not checked, robust",
    "not checked, not checked", "computational issues, robust",
])
def test_floras_own_reproduction_outcome_can_be_confirmed(flora_outcome):
    choices = judging.outcome_choices({**SAME, "flora_outcome": flora_outcome})
    assert flora_outcome in choices
    assert choices[-1] == "cannot_tell"


def test_outcomes_are_floras_vocabulary_and_nothing_else():
    from extractor_vocab import REPLICATION_OUTCOMES
    assert set(judging.REPLICATION_CHOICES) == set(REPLICATION_OUTCOMES)
    assert judging.outcome_choices(MO_ONLY) == (*judging.REPLICATION_CHOICES, "cannot_tell")


@pytest.mark.parametrize(("record", "answer", "message"), [
    (SAME, {"original_choice": "flora", "outcome": "failed"}, "which original"),
    (DIFFERENT, {"original_choice": "flora", "outcome": "success"}, "the outcome"),
    (DIFFERENT, {"original_choice": "flora", "outcome": "failed", "suggested_doi_o": "10.3000/c"}, "goes with"),
    (DIFFERENT, {"original_choice": "neither", "outcome": "failed", "suggested_doi_o": "not a doi"}, "DOI"),
    (DIFFERENT, {"original_choice": "neither", "outcome": "failed", "suggested_doi_o": "doi:10.1000/A"}, "FLoRA's original"),
    (DIFFERENT, {"original_choice": "neither", "outcome": "failed", "suggested_doi_o": "10.2000/b"}, "Observatory's"),
    (NEITHER, {"original_choice": "neither", "outcome": "failed"}, "Give the DOI"),
    (DIFFERENT, {"original_choice": "flora", "outcome": "failed", "note": "x" * 2001}, "longer"),
])
def test_an_answer_that_does_not_fit_the_record_is_refused(record, answer, message):
    with pytest.raises(judging.Refused, match=message) as refused:
        judging.check_answer(record, answer)
    assert refused.value.status == 422


def test_an_answer_is_cleaned():
    clean = judging.check_answer(NEITHER, {"original_choice": "neither", "outcome": " Failed ",
                                           "suggested_doi_o": "https://doi.org/10.5555/XYZ", "note": "  "})
    assert clean == {"original_choice": "neither", "suggested_doi_o": "10.5555/xyz",
                     "outcome": "failed", "note": None}


@pytest.mark.parametrize(("choice", "outcome", "note", "points"), [
    ("flora", "failed", "Checked the full text", 15),
    ("flora", "failed", None, 14),
    ("cannot_tell", "failed", None, 12),
    ("cannot_tell", "cannot_tell", None, 10),
])
def test_points_are_those_of_a_normal_judgement(choice, outcome, note, points):
    assert judging.points_for(10, {"original_choice": choice, "outcome": outcome, "note": note}) == points


def test_validators_see_both_answers_but_never_the_raw_row():
    view = judging.public_view({**SAME, "record_id": uuid.uuid4(), "doi_r": "10.1/r",
                                "raw": {"secret": 1}, "flora_outcome_quote": "It failed."})
    assert "raw" not in view and "judgements" not in view
    assert view["flora"]["outcome_quote"] == "It failed."
    assert view["observatory"]["outcome"] == "success"
    assert view["original_choices"] == ["both", "neither", "cannot_tell"]


def test_its_lock_is_its_own():
    from adjudication import bootstrap, importer
    assert len({judging.JUDGING_LOCK_ID, bootstrap.ADVISORY_LOCK_ID, importer.IMPORT_LOCK_ID}) == 3


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------

def _client(tier=1, ready=True, cursor=None):
    app = FastAPI()
    app.include_router(adjudication.create_router(
        current_admin=lambda: {"id": 1, "handle": "Admin"},
        current_validator=lambda: {"coder_id": 7, "handle": "Sam", "validator_tier": tier},
        status=lambda: adjudication.SetupStatus(True, ready, None if ready else "LockNotAvailable"),
        cursor=cursor or _no_cursor))
    return TestClient(app)


@contextmanager
def _no_cursor():
    raise AssertionError("no query expected")
    yield


@contextmanager
def _a_cursor():
    yield object()


def test_regular_validators_are_told_no_without_a_query():
    assert _client(tier=0).get("/api/disagreements/summary").json() == {"available": False}
    response = _client(tier=0).post("/api/disagreements/next")
    assert response.status_code == 403
    assert "Trusted and Senior" in response.json()["detail"]


def test_before_setup_the_summary_says_no_and_the_work_waits():
    assert _client(ready=False).get("/api/disagreements/summary").json() == {"available": False}
    assert _client(ready=False).post("/api/disagreements/next").status_code == 503


def test_a_summary_that_cannot_be_read_says_no_rather_than_failing():
    @contextmanager
    def broken():
        raise psycopg2.OperationalError("server closed the connection")
        yield

    assert _client(cursor=broken).get("/api/disagreements/summary").json() == {"available": False}


def test_the_summary_counts_what_is_left(monkeypatch):
    monkeypatch.setattr(judging, "progress", lambda cur, v: {"left": 4, "judged": 2})
    assert _client(cursor=_a_cursor).get("/api/disagreements/summary").json() == \
        {"available": True, "left": 4, "judged": 2}


def test_a_record_id_that_is_not_one_is_not_found():
    response = _client().post("/api/disagreements/not-a-uuid/skip")
    assert response.status_code == 404


@pytest.mark.parametrize(("error", "status", "detail"), [
    (judging.Refused(409, "You have already judged this record"), 409, "already judged"),
    (judging.Refused(422, "Choose the outcome"), 422, "Choose the outcome"),
    (psycopg2.errors.LockNotAvailable("lock timeout"), 503, "try again"),
])
def test_refusals_come_back_as_plain_messages(monkeypatch, error, status, detail):
    def submit(cur, validator, record_id, answer):
        raise error

    monkeypatch.setattr(judging, "submit", submit)
    response = _client(cursor=_a_cursor).post(
        f"/api/disagreements/{uuid.uuid4()}/submit",
        json={"original_choice": "flora", "outcome": "failed"})
    assert response.status_code == status
    assert detail in response.json()["detail"]


def test_the_submitted_answer_reaches_the_rules(monkeypatch):
    seen = {}

    def submit(cur, validator, record_id, answer):
        seen.update(validator=validator["handle"], record_id=record_id, answer=answer)
        return {"points": 15}

    monkeypatch.setattr(judging, "submit", submit)
    record_id = uuid.uuid4()
    response = _client(cursor=_a_cursor).post(
        f"/api/disagreements/{str(record_id).upper()}/submit",
        json={"original_choice": "neither", "suggested_doi_o": "10.5/x", "outcome": "failed"})
    assert response.json() == {"points": 15}
    assert seen == {"validator": "Sam", "record_id": str(record_id),
                    "answer": {"original_choice": "neither", "suggested_doi_o": "10.5/x",
                               "outcome": "failed", "note": None}}


# ---------------------------------------------------------------------------
# Against a real PostgreSQL with the app's own schema (FLORA_TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

@pytest.fixture
def judging_db(local_database, monkeypatch):  # noqa: F811
    import os
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


def _record(cur, n, shape, **extra):
    fields = {"import_key": f"key-{n}", "imported_from": "test", "doi_r": f"10.9/rep-{n}",
              "title_r": f"Replication {n}", "raw": "{}", **shape, **extra}
    cur.execute(f"INSERT INTO adjudication.records ({', '.join(fields)}) "
                f"VALUES ({', '.join(['%s'] * len(fields))}) RETURNING record_id::text AS id",
                list(fields.values()))
    return cur.fetchone()["id"]


def _judge(cur, handle, tier):
    cur.execute("INSERT INTO validators (handle, validator_tier) VALUES (%s, %s) RETURNING id",
                (handle, tier))
    return {"coder_id": cur.fetchone()["id"], "handle": handle, "validator_tier": tier}


def test_two_judges_per_record_with_points_on_postgres(judging_db):
    run, conn = judging_db
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        z = _record(cur, 3, NEITHER)                 # imported first, served last
        x = _record(cur, 1, SAME)
        y = _record(cur, 2, DIFFERENT)
        a, b, c, d, e, f = (_judge(cur, h, t) for h, t in
                            [("ana", 1), ("ben", 2), ("cleo", 1), ("dev", 1), ("eli", 2), ("fay", 1)])

    assert run(judging.progress, a) == {"left": 3, "judged": 0}
    first = run(judging.next_record, a)
    assert first["record"]["record_id"] == x, "the kind the analysis samples in full comes first"
    assert first["points_base"] == 10 and first["left"] == 3
    assert run(judging.next_record, a)["record"]["record_id"] == x, "reopening keeps the same claim"
    assert run(judging.next_record, b)["record"]["record_id"] == x, "a second place on the same record"
    assert run(judging.next_record, c)["record"]["record_id"] == y, "both places on x are taken"

    with pytest.raises(judging.Refused, match="first") as refused:
        run(judging.submit, a, y, {"original_choice": "flora", "outcome": "failed"})
    assert refused.value.status == 409

    done = run(judging.submit, a, x, {"original_choice": "both", "outcome": "failed",
                                      "note": "Read the full text"})
    assert (done["points"], done["total_points"], done["record_complete"]) == (15, 15, False)
    with pytest.raises(judging.Refused, match="already judged"):
        run(judging.submit, a, x, {"original_choice": "both", "outcome": "failed"})

    done = run(judging.submit, b, x, {"original_choice": "cannot_tell", "outcome": "successful"})
    assert (done["points"], done["record_complete"]) == (12, True)
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT status FROM adjudication.records WHERE record_id = %s", (x,))
        assert cur.fetchone()["status"] == "awaiting_approval"
        cur.execute("SELECT handle, total_points, total_judgements FROM validators "
                    "WHERE id IN (%s, %s) ORDER BY handle", (a["coder_id"], b["coder_id"]))
        assert [tuple(r.values()) for r in cur.fetchall()] == [("ana", 15, 1), ("ben", 12, 1)]
        cur.execute("SELECT validator_handle, original_choice, outcome, note, points "
                    "FROM adjudication.judgements WHERE record_id = %s ORDER BY validator_handle", (x,))
        assert [tuple(r.values()) for r in cur.fetchall()] == [
            ("ana", "both", "failed", "Read the full text", 15),
            ("ben", "cannot_tell", "successful", None, 12)]

    # A skip frees the place and is never served to that validator again.
    assert run(judging.skip, c, y) == {"left": 1, "judged": 0}
    assert run(judging.next_record, c)["record"]["record_id"] == z
    with pytest.raises(judging.Refused, match="Give the DOI"):
        run(judging.submit, c, z, {"original_choice": "neither", "outcome": "failed"})

    # A lapsed claim: its place may be taken, and once both are, it is let go.
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE adjudication.judgements SET claimed_at = NOW() - INTERVAL '2 hours' "
                    "WHERE validator_id = %s", (c["coder_id"],))
    assert run(judging.next_record, d)["record"]["record_id"] == y
    assert run(judging.next_record, e)["record"]["record_id"] == y
    assert run(judging.next_record, f)["record"]["record_id"] == z, "cleo's claim on z has lapsed"
    run(judging.submit, f, z, {"original_choice": "neither", "suggested_doi_o": "10.7777/found",
                               "outcome": "mixed"})
    # Still free (one submission), so cleo's lapsed claim is still good.
    assert run(judging.next_record, c)["record"]["record_id"] == z
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE adjudication.judgements SET claimed_at = NOW() - INTERVAL '2 hours' "
                    "WHERE validator_id = %s", (c["coder_id"],))
    run(judging.next_record, a)                      # ana takes z's last place
    nothing = run(judging.next_record, c)
    assert nothing["record"] is None and nothing["left"] == 0
    with pytest.raises(judging.Refused, match="first"):
        run(judging.submit, c, z, {"original_choice": "cannot_tell", "outcome": "cannot_tell"})
    with conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM adjudication.judgements WHERE validator_id = %s "
                    "AND state = 'claimed'", (c["coder_id"],))
        assert cur.fetchone()[0] == 0, "the lost claim is let go"
