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
from tests.pg_fixtures import local_database  # noqa: F401

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
# FLoRA codes a reproduction where the Observatory sees a direct replication
# (10.1038/nature08637 in the PR's file).
REPRODUCTION = {**SAME, "flora_outcome": "computational issues, robustness challenges",
                "mo_outcome": "failure", "mo_replication_type": "direct"}
# Same original, and the Observatory's "reversal" is FLoRA's "failed".
AGREEING = {**SAME, "flora_outcome": "failed", "mo_outcome": "reversal"}


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


@pytest.mark.parametrize(("record", "flora", "disputed", "asked"), [
    (SAME, "replication", False, False),
    (FLORA_ONLY, None, False, True),     # cannot_be_determined: either type
    (NEITHER, None, False, True),        # pending: FLoRA found no original to code
    (REPRODUCTION, "reproduction", True, True),
    ({**SAME, "mo_replication_type": "reproduction"}, "replication", True, True),
    ({**REPRODUCTION, "mo_replication_type": "computational reproduction"}, "reproduction", False, False),
    ({**SAME, "flora_outcome": "not_a_replication"}, "not_validation", True, True),
])
def test_the_type_is_asked_when_floras_is_unknown_or_disputed(record, flora, disputed, asked):
    assert judging.flora_type(record) == flora
    assert judging.type_disputed(record) is disputed
    assert judging.ask_type(record) is asked


@pytest.mark.parametrize("flora_outcome", [
    # Every reproduction outcome FLoRA gives in the PR's file.
    "computational issues, robustness challenges", "not checked, robustness challenges",
    "computationally reproducible, robustness challenges", "not checked, robust",
    "not checked, not checked", "computational issues, robust",
])
def test_floras_reproduction_outcomes_are_reproductions_on_two_axes(flora_outcome):
    record = {**SAME, "flora_outcome": flora_outcome}
    assert judging.flora_type(record) == "reproduction"
    view = judging.public_view({**record, "record_id": uuid.uuid4(), "doi_r": "10.1/r"})
    assert f"{view['flora']['outcome_computation']}, {view['flora']['outcome_robustness']}" == flora_outcome
    # Never offered as a replication outcome: the axes ask it.
    assert flora_outcome not in judging.outcome_choices(record)


def test_outcomes_are_floras_vocabulary_and_nothing_else():
    from extractor_vocab import OUTCOME_COMPUTATION_VALUES, OUTCOME_ROBUSTNESS_VALUES, REPLICATION_OUTCOMES
    for choices, vocabulary in ((judging.REPLICATION_CHOICES, REPLICATION_OUTCOMES),
                                (judging.COMPUTATION_CHOICES, OUTCOME_COMPUTATION_VALUES),
                                (judging.ROBUSTNESS_CHOICES, OUTCOME_ROBUSTNESS_VALUES)):
        assert set(choices) == set(vocabulary) and len(choices) == len(vocabulary)
    assert judging.outcome_choices(SAME) == (*judging.REPLICATION_CHOICES, "cannot_tell")
    # Asked the type, "neither" there is "not a replication".
    assert judging.outcome_choices(MO_ONLY) == (
        *(c for c in judging.REPLICATION_CHOICES if c != "not_a_replication"), "cannot_tell")


@pytest.mark.parametrize(("record", "expected"), [
    (AGREEING, {"original": "both", "type": "replication", "outcome": "failed"}),
    (SAME, {"original": "both", "type": "replication", "outcome": None}),
    ({**DIFFERENT, "mo_outcome": "success"}, {"original": None, "type": "replication", "outcome": "successful"}),
    ({**SAME, "mo_outcome": "inconclusive", "flora_outcome": "mixed"},
     {"original": "both", "type": "replication", "outcome": None}),
    # The type is asked, so not pre-selected; the shared original still is.
    ({**SAME, "flora_outcome": "cannot_be_determined"}, {"original": "both", "type": None, "outcome": None}),
    # Nothing at all: FLoRA's outcome is pending.
    ({**SAME, "flora_outcome": "pending", "mo_outcome": "failure"}, {"original": None, "type": None, "outcome": None}),
    (MO_ONLY, {"original": None, "type": None, "outcome": None}),
    # The two disagree about the type: only the shared original.
    (REPRODUCTION, {"original": "both", "type": None, "outcome": None}),
    ({**AGREEING, "mo_replication_type": "reproduction"}, {"original": "both", "type": None, "outcome": None}),
    ({**REPRODUCTION, "mo_doi_o": "10.2000/b"}, {"original": None, "type": None, "outcome": None}),
])
def test_what_flora_and_the_observatory_agree_on_is_pre_selected(record, expected):
    assert judging.defaults(record) == expected
    view = judging.public_view({**record, "record_id": uuid.uuid4(), "doi_r": "10.1/r"})
    assert view["defaults"] == expected
    # A type is pre-selected exactly where it is not asked.
    assert (expected["type"] is None) is view["ask_type"]


def test_the_observatorys_outcome_in_floras_words_is_one_function():
    from adjudication import review
    assert review._observatory_outcome is judging._observatory_outcome
    assert [judging._observatory_outcome({"mo_outcome": o}) for o in
            ("success", "Failure", "reversal", "inconclusive", None)] == \
        ["successful", "failed", "failed", None, None]


@pytest.mark.parametrize("computation", [*judging.COMPUTATION_CHOICES, "cannot_tell"])
@pytest.mark.parametrize("robustness", [*judging.ROBUSTNESS_CHOICES, "cannot_tell"])
def test_a_reproduction_takes_any_pair_of_axes(computation, robustness):
    from extractor_vocab import derive_reproduction_outcome
    clean = judging.check_answer(REPRODUCTION, {
        "original_choice": "both", "type": " Reproduction ", "outcome": "ignored",
        "outcome_computation": computation.upper(), "outcome_robustness": robustness})
    assert (clean["type"], clean["outcome_computation"], clean["outcome_robustness"]) == \
        ("reproduction", computation, robustness)
    assert clean["outcome"] == derive_reproduction_outcome(computation, robustness)


def test_an_answer_from_a_page_without_the_type_question_is_typed_by_its_outcome():
    old = judging.check_answer(REPRODUCTION, {"original_choice": "both",
                                              "outcome": "computational issues, robustness challenges"})
    assert (old["type"], old["outcome_computation"], old["outcome_robustness"]) == \
        ("reproduction", "computational issues", "robustness challenges")
    assert judging.check_answer(SAME, {"original_choice": "both", "outcome": "failed"})["type"] == "replication"
    # "Not a replication" is the type 'not_validation', however it is given.
    for answer in ({"outcome": "not_a_replication"}, {"type": "replication", "outcome": "not_a_replication"},
                   {"type": "not_validation", "outcome": "failed"}):
        clean = judging.check_answer(SAME, {"original_choice": "both", **answer})
        assert (clean["type"], clean["outcome"], clean["outcome_computation"]) == \
            ("not_validation", "not_a_replication", None)


@pytest.mark.parametrize(("record", "answer", "message"), [
    (SAME, {"original_choice": "both", "type": "meta-analysis", "outcome": "failed"}, "replication or a reproduction"),
    (SAME, {"original_choice": "both", "type": "replication", "outcome": "computational issues, robust"}, "the outcome"),
    (REPRODUCTION, {"original_choice": "both", "type": "reproduction", "outcome_computation": "robust",
                    "outcome_robustness": "robust"}, "computation"),
    (REPRODUCTION, {"original_choice": "both", "type": "reproduction", "outcome_computation": "not checked",
                    "outcome_robustness": "computational issues"}, "robustness"),
    (REPRODUCTION, {"original_choice": "both", "type": "reproduction", "outcome_computation": "not checked"},
     "robustness"),
    (REPRODUCTION, {"original_choice": "both", "type": "reproduction", "outcome": "not checked, robust"},
     "computation"),
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
                     "type": "replication", "outcome": "failed", "outcome_computation": None,
                     "outcome_robustness": None, "note": None}


@pytest.mark.parametrize(("choice", "outcome", "note", "points"), [
    ("flora", "failed", "Checked the full text", 15),
    ("flora", "failed", None, 14),
    ("cannot_tell", "failed", None, 12),
    ("cannot_tell", "cannot_tell", None, 10),
])
def test_points_are_those_of_a_normal_judgement(choice, outcome, note, points):
    assert judging.points_for(10, {"original_choice": choice, "outcome": outcome, "note": note}) == points


@pytest.mark.parametrize(("answer", "points"), [
    ({"type": "reproduction", "outcome_computation": "computational issues",
      "outcome_robustness": "robustness challenges"}, 14),
    ({"type": "reproduction", "outcome_computation": "cannot_be_determined",
      "outcome_robustness": "cannot_tell"}, 13),
    ({"type": "reproduction", "outcome_computation": "cannot_tell", "outcome_robustness": "cannot_tell"}, 12),
    ({"type": "not_validation", "outcome": "not_a_replication"}, 14),
    ({"type": "replication", "outcome": "cannot_tell"}, 12),
])
def test_a_reproduction_earns_a_point_for_each_decided_axis(answer, points):
    clean = judging.check_answer(REPRODUCTION, {"original_choice": "both", **answer})
    assert judging.points_for(10, clean) == points


def test_pre_selected_answers_earn_the_same_and_are_recorded_as_kept():
    kept = judging.check_answer(AGREEING, {"original_choice": "both", "type": "replication", "outcome": "failed"})
    assert judging.prefilled(AGREEING, kept) == {"original_prefilled": True, "outcome_prefilled": True}
    assert judging.points_for(10, kept) == 14
    changed = judging.check_answer(AGREEING, {"original_choice": "neither", "suggested_doi_o": "10.5555/x",
                                              "type": "replication", "outcome": "mixed"})
    assert judging.prefilled(AGREEING, changed) == {"original_prefilled": False, "outcome_prefilled": False}
    assert judging.points_for(10, changed) == 14
    original_only = judging.check_answer(SAME, {"original_choice": "both", "outcome": "failed"})
    assert judging.prefilled(SAME, original_only) == {"original_prefilled": True, "outcome_prefilled": False}
    # The type is disputed: only the shared original was pre-selected.
    answer = judging.check_answer(REPRODUCTION, {"original_choice": "both", "type": "reproduction",
                                                 "outcome_computation": "computational issues",
                                                 "outcome_robustness": "robust"})
    assert judging.prefilled(REPRODUCTION, answer) == {"original_prefilled": True, "outcome_prefilled": False}
    # Nothing was pre-selected, so nothing was kept, whatever the answer.
    elsewhere = {**REPRODUCTION, "mo_doi_o": "10.2000/b"}
    answer = judging.check_answer(elsewhere, {"original_choice": "flora", "type": "reproduction",
                                              "outcome_computation": "computational issues",
                                              "outcome_robustness": "robust"})
    assert judging.prefilled(elsewhere, answer) == {"original_prefilled": False, "outcome_prefilled": False}


def test_validators_see_both_answers_but_never_the_raw_row():
    view = judging.public_view({**SAME, "record_id": uuid.uuid4(), "doi_r": "10.1/r",
                                "raw": {"secret": 1}, "flora_outcome_quote": "It failed."})
    assert "raw" not in view and "judgements" not in view
    assert view["flora"]["outcome_quote"] == "It failed."
    assert view["observatory"]["outcome"] == "success"
    assert view["original_choices"] == ["both", "neither", "cannot_tell"]
    assert (view["flora_type"], view["observatory_type"], view["type_disputed"], view["ask_type"]) == \
        ("replication", "replication", False, False)
    assert view["computation_choices"] == [*judging.COMPUTATION_CHOICES, "cannot_tell"]
    assert view["robustness_choices"] == [*judging.ROBUSTNESS_CHOICES, "cannot_tell"]
    view = judging.public_view({**REPRODUCTION, "record_id": uuid.uuid4(), "doi_r": "10.1/r"})
    assert (view["flora_type"], view["type_disputed"], view["ask_type"]) == ("reproduction", True, True)
    assert (view["flora"]["outcome_computation"], view["flora"]["outcome_robustness"]) == \
        ("computational issues", "robustness challenges")


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
                    "answer": {"original_choice": "neither", "suggested_doi_o": "10.5/x", "type": None,
                               "outcome": "failed", "outcome_computation": None,
                               "outcome_robustness": None, "note": None}}
    client = _client(cursor=_a_cursor)
    client.post(f"/api/disagreements/{record_id}/submit", json={
        "original_choice": "both", "type": "reproduction", "outcome_computation": "not checked",
        "outcome_robustness": "robust"})
    assert seen["answer"] == {"original_choice": "both", "suggested_doi_o": None, "type": "reproduction",
                              "outcome": None, "outcome_computation": "not checked",
                              "outcome_robustness": "robust", "note": None}


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
        cur.execute("SELECT validator_handle, original_choice, type, outcome, note, points, "
                    "original_prefilled, outcome_prefilled "
                    "FROM adjudication.judgements WHERE record_id = %s ORDER BY validator_handle", (x,))
        # Both name the same original, so "both" was pre-selected: ana kept it.
        assert [tuple(r.values()) for r in cur.fetchall()] == [
            ("ana", "both", "replication", "failed", "Read the full text", 15, True, False),
            ("ben", "cannot_tell", "replication", "successful", None, 12, False, False)]

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


def test_a_reproduction_and_pre_selected_answers_on_postgres(judging_db):
    run, conn = judging_db
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        repro = _record(cur, 1, REPRODUCTION)
        agreeing = _record(cur, 2, {**AGREEING, "kind": "different original", "mo_doi_o": "10.2000/b",
                                    "flora_doi_o": "10.1000/a"})
        a, b = _judge(cur, "ana", 1), _judge(cur, "ben", 2)

    view = run(judging.next_record, a)["record"]
    assert view["record_id"] == repro and view["type_disputed"] and view["ask_type"]
    assert view["defaults"] == {"original": "both", "type": None, "outcome": None}
    done = run(judging.submit, a, repro, {"original_choice": "both", "type": "reproduction",
                                          "outcome_computation": "computational issues",
                                          "outcome_robustness": "cannot_tell"})
    assert done["points"] == 13, "10 + 2 for the original + 1 for the one decided axis"

    view = run(judging.next_record, b)["record"]
    assert view["record_id"] == repro, "the half-judged record comes first"
    run(judging.skip, b, repro)
    view = run(judging.next_record, b)["record"]
    assert view["record_id"] == agreeing
    assert view["defaults"] == {"original": None, "type": "replication", "outcome": "failed"}
    done = run(judging.submit, b, agreeing, {"original_choice": "flora", "type": "replication",
                                             "outcome": "failed"})
    assert done["points"] == 14, "a pre-selected answer earns what any other does"

    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT validator_handle, type, outcome, outcome_computation, outcome_robustness, "
                    "original_prefilled, outcome_prefilled FROM adjudication.judgements "
                    "WHERE state = 'submitted' ORDER BY validator_handle")
        assert [tuple(r.values()) for r in cur.fetchall()] == [
            ("ana", "reproduction", "cannot_be_determined", "computational issues", "cannot_tell", True, False),
            ("ben", "replication", "failed", None, None, False, True)]
