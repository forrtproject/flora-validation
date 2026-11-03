"""backfill_llm_answers.py: which records it fills, and how it writes.

The classification must pick exactly the records on which evaluate_consensus now
asks the LLM only to store its answer, so it is checked against evaluate_consensus
itself. The checks marked with local_database need a real server and are opt-in
like tests/test_preparation_database.py (FLORA_TEST_DATABASE_URL).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

import backfill_llm_answers as backfill
import consensus_engine as ce
import llm_validator
from tests.test_consensus_engine import BASE_RECORD, H1_AGREE, LLM_ERROR, _written
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent

PLAIN = {k: v for k, v in H1_AGREE.items() if k != "validator_slot"}

# Judgements as validators submit them, to pair up.
JUDGEMENTS = {
    "plain": {},
    "outcome_failed": {"outcome_check": "incorrect", "corrected_outcome": "failed"},
    "outcome_mixed": {"outcome_check": "incorrect", "corrected_outcome": "mixed"},
    "not_validation": {"type_check": "incorrect", "corrected_type": "not_validation"},
    "stale_type": {"corrected_type": "reproduction"},           # type_check correct: ignored
    "doi_o_corrected": {"corrected_doi_o": "10.1000/other"},
    "published_link": {"doi_r_published": "https://doi.org/10.1234/P"},
    "published_bare": {"doi_r_published": "10.1234/p"},
    "abstract": {"corrected_abstract": "We replicated X."},
    "abstract_reformatted": {"corrected_abstract": "we replicated x"},
    "unsure_original": {"original_check": "incorrect",
                        "additional_checks": {"was_unsure_original": True}},
    "unsure_axis_json": {"additional_checks": json.dumps(
        {"reproduction_axis_checks": {"computation": "unsure"}})},
    "quote_flagged": {"additional_checks": {"quote_not_in_abstract": True}},
    "quote_flagged_json": {"additional_checks": json.dumps({"quote_not_in_abstract": True})},
    "unsure_and_quote": {"additional_checks": {"was_unsure_outcome": True,
                                               "quote_not_in_abstract": True}},
    "markers_false": {"additional_checks": {"was_unsure_original": False,
                                            "quote_not_in_abstract": False}},
    "senior_reject": {"type_check": "incorrect", "corrected_type": "not_validation",
                      "additional_checks": {"senior_reject": True}},
    "senior_reject_json": {"additional_checks": json.dumps(
        {"senior_reject": True, "was_unsure_outcome": True})},
    "senior_reject_false": {"additional_checks": {"senior_reject": False,
                                                  "quote_not_in_abstract": True}},
    "unreadable_checks": {"additional_checks": "not json"},
}


def _h(slot, name_or_fields):
    fields = JUDGEMENTS[name_or_fields] if isinstance(name_or_fields, str) else name_or_fields
    return {**PLAIN, **fields, "validator_slot": slot}


def _pair(first, second):
    return _h("human_1", first), _h("human_2", second)


# ── which way a pair of judgements goes ───────────────────────────────────────

@pytest.mark.parametrize("first, second, case", [
    ("unsure_original", "plain", backfill.CANT_TELL),
    ("plain", "unsure_axis_json", backfill.CANT_TELL),
    ("quote_flagged", "plain", backfill.QUOTE_FLAGGED),
    ("plain", "quote_flagged_json", backfill.QUOTE_FLAGGED),
    ("doi_o_corrected", "plain", backfill.CORRECTIONS_DIFFER),
    ("published_link", "plain", backfill.CORRECTIONS_DIFFER),
    ("outcome_failed", "outcome_mixed", backfill.CORRECTIONS_DIFFER),
])
def test_each_of_the_three_cases_is_selected(first, second, case):
    assert backfill.case_of(*_pair(first, second)) == case
    assert case in backfill.STORE_ONLY_CASES


@pytest.mark.parametrize("first, second, case", [
    ("senior_reject", "plain", backfill.SENIOR_REJECT),
    ("plain", "senior_reject_json", backfill.SENIOR_REJECT),       # with "Can't tell" too
    ("senior_reject_false", "plain", backfill.SENIOR_REJECT),      # the key is the marker
    ("plain", "plain", backfill.AGREEMENT),
    ("not_validation", "not_validation", backfill.AGREEMENT),
    ("published_link", "published_bare", backfill.AGREEMENT),
    ("abstract", "abstract_reformatted", backfill.AGREEMENT),
    ("stale_type", "plain", backfill.AGREEMENT),
    ("markers_false", "unreadable_checks", backfill.AGREEMENT),
    ("plain", "outcome_failed", backfill.CHECKS_DIFFER),
    ("not_validation", "plain", backfill.CHECKS_DIFFER),
])
def test_senior_rejects_agreements_and_tiebreakers_are_not_selected(first, second, case):
    assert backfill.case_of(*_pair(first, second)) == case
    assert case not in backfill.STORE_ONLY_CASES


@pytest.mark.parametrize("first, second, case", [
    ("unsure_and_quote", "doi_o_corrected", backfill.CANT_TELL),
    ("quote_flagged", "unsure_original", backfill.CANT_TELL),
    ("quote_flagged", "doi_o_corrected", backfill.QUOTE_FLAGGED),
    ("unsure_original", "outcome_failed", backfill.CANT_TELL),     # before the tiebreaker
    ("quote_flagged_json", "outcome_failed", backfill.QUOTE_FLAGGED),
    ("senior_reject_json", "quote_flagged", backfill.SENIOR_REJECT),
])
def test_cases_are_tested_in_evaluate_consensus_order(first, second, case):
    assert backfill.case_of(*_pair(first, second)) == case


RECORD = {**BASE_RECORD, "llm_validator": None}


def _has_key(additional_checks, key):
    """What `additional_checks ? key` answers on the stored jsonb object."""
    if isinstance(additional_checks, str):
        try:
            additional_checks = json.loads(additional_checks)
        except ValueError:
            return False                     # cannot be stored as jsonb at all
    return isinstance(additional_checks, dict) and key in additional_checks


def _consensus_cursor(h1, h2):
    """A cursor answering evaluate_consensus as the database would: the two
    judgements, the record, the senior-reject count from the judgements, two
    Senior and experienced validators (so an agreeing answer would auto-validate),
    and no duplicate."""
    cur = MagicMock()
    cur.fetchall.return_value = [h1, h2]
    seniors = sum(_has_key(h.get("additional_checks"), "senior_reject") for h in (h1, h2))
    answers = iter([dict(RECORD), {"n": seniors},
                    {"trusted_count": 2, "experienced_count": 2}])
    cur.fetchone.side_effect = lambda: next(answers, None)
    return cur


def _answer_agreeing_with(h):
    return {**{f: h[f] for f in ce._CHECK_FIELDS}, "corrected_outcome": None,
            "corrected_doi_o": None, "corrected_type": None, "context": "sanity_check",
            "model": "gemini-test", "vote_score": 15,
            "validated_at": "2026-11-01T00:00:00+00:00", "notes": ""}


def _only_stores_the_answer(h1, h2):
    """Does evaluate_consensus ask the LLM here only to store its answer? Asked
    with an answer agreeing with each validator and with an error: every time it
    must make one sanity-check call and send the record to review, not as a
    tiebreaker, with that answer stored and nothing resolved or published."""
    for answer in (_answer_agreeing_with(h1), _answer_agreeing_with(h2), LLM_ERROR):
        cur = _consensus_cursor(h1, h2)
        with patch("consensus_engine.run_llm_validation", return_value=answer) as llm:
            ce.evaluate_consensus(cur, RECORD["record_id"])
        written = _written(cur) or {}
        if not (llm.call_count == 1
                and llm.call_args.kwargs.get("context") == "sanity_check"
                and written.get("validation_status") == "need_review"
                and written.get("is_tiebreaker") is False
                and "llm_validator" in written
                and json.loads(written["llm_validator"]) == answer
                and "final_outcome" not in written
                and "auto_validated_rule" not in written
                and "INSERT INTO validated" not in str(cur.execute.call_args_list)):
            return False
    return True


def test_selected_exactly_where_evaluate_consensus_only_stores_the_answer():
    """Every pair of the judgements above, both ways round: the script picks a pair
    exactly when evaluate_consensus makes a store-only LLM call on it."""
    mismatches, seen = [], set()
    for first in JUDGEMENTS:
        for second in JUDGEMENTS:
            h1, h2 = _pair(first, second)
            case = backfill.case_of(h1, h2)
            seen.add(case)
            if (case in backfill.STORE_ONLY_CASES) != _only_stores_the_answer(h1, h2):
                mismatches.append((first, second, case))
    assert mismatches == []
    assert seen == set(backfill.STORE_ONLY_CASES) | set(backfill.OTHER_CASES)


def test_a_record_needs_both_judgements_read_in_slot_order():
    """The survey groups judgements by record: one judgement is not a pair."""
    records = [{"record_id": rid, "validation_status": status, "updated_at": n,
                "final_title_r": None, "title_r": rid}
               for n, (rid, status) in enumerate([("a", "need_review"), ("b", "need_review"),
                                                  ("c", "validated"), ("d", "need_review")])]
    rows = [{"record_id": "a", **_h("human_1", "unsure_original")},
            {"record_id": "a", **_h("human_2", "plain")},
            {"record_id": "b", **_h("human_1", "quote_flagged")},          # one judgement
            {"record_id": "c", **_h("human_1", "plain")},
            {"record_id": "c", **_h("human_2", "doi_o_corrected")},
            {"record_id": "d", **_h("human_1", "plain")},
            {"record_id": "d", **_h("human_2", "plain")}]
    cur = MagicMock()
    cur.fetchall.side_effect = [records, rows]
    found = backfill.survey(cur)
    assert [(r["record_id"], r["case"]) for r in found] == [
        ("a", backfill.CANT_TELL), ("d", backfill.AGREEMENT), ("c", backfill.CORRECTIONS_DIFFER)]
    judgements_sql = cur.execute.call_args_list[1].args[0]
    assert "is_validated = TRUE" in judgements_sql
    assert "validator_slot IN ('human_1', 'human_2')" in judgements_sql
    assert "ORDER BY record_id, validator_slot" in judgements_sql


def test_apply_needs_a_gemini_key_before_touching_the_database(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Without .env (CI) run() would stop at the DATABASE_URL check first.
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: pytest.fail("connected"))
    with pytest.raises(EnvironmentError, match="GEMINI_API_KEY"):
        backfill.run(apply=True)


def test_importing_it_does_not_import_the_app():
    """app.py runs init_db on DATABASE_URL when imported."""
    env = {**os.environ, "DATABASE_URL": "postgresql://stub/stub"}
    out = subprocess.run(
        [sys.executable, "-c", "import sys, backfill_llm_answers; print('app' in sys.modules)"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# ── against a real database ───────────────────────────────────────────────────

CLEAN = {"type_check": "correct", "original_check": "correct", "outcome_check": "correct",
         "context": "sanity_check", "model": "gemini", "vote_score": 15,
         "validated_at": "2026-11-01T00:00:00+00:00", "notes": ""}
ERROR = {"error": "timeout", "context": "sanity_check", "model": "gemini", "vote_score": 15,
         "validated_at": "2026-11-01T00:00:00+00:00"}


@pytest.fixture
def db(local_database, monkeypatch, tmp_path):
    """The throwaway database; Gemini and consensus fail the test unless a test
    replaces them. Backups go to a temporary folder."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-never-used")
    monkeypatch.setenv("OUTCOME_BACKFILL_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(llm_validator, "run_llm_validation",
                        lambda record, context: pytest.fail("Gemini called"))
    monkeypatch.setattr(ce, "evaluate_consensus",
                        lambda cur, record_id: pytest.fail("consensus re-run"))
    return local_database


def _seed(conn, first, second=None, *, status="need_review", llm=None, title="A replication"):
    """A record judged `first` (human_1) and `second` (human_2; None: not yet)."""
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO unvalidated (doi_r, type, outcome, abstract_r, title_r,
                                        validation_status, llm_validator, quality_flags)
               VALUES ('10.9999/' || gen_random_uuid(), 'replication', 'failed',
                       'We found no effect.', %s, %s, %s, '[]'::jsonb)
               RETURNING record_id::text AS record_id""",
            (title, status, json.dumps(llm) if llm is not None else None))
        record_id = cur.fetchone()["record_id"]
        for slot, name in (("human_1", first), ("human_2", second)):
            if name is None:
                continue
            fields = {k: v for k, v in _h(slot, name).items() if v is not None}
            if isinstance(fields.get("additional_checks"), dict):
                fields["additional_checks"] = json.dumps(fields["additional_checks"])
            names = ["record_id", "is_shown", "is_validated", *fields]
            cur.execute(f"INSERT INTO validation_queue ({', '.join(names)}) "
                        f"VALUES ({', '.join(['%s'] * len(names))})",
                        (record_id, True, True, *fields.values()))
    return record_id


def _row(conn, record_id):
    with conn.cursor() as cur:
        cur.execute("SELECT to_jsonb(u) FROM unvalidated u WHERE record_id = %s", (record_id,))
        row = cur.fetchone()[0]
    conn.commit()
    return row


def _everything(conn):
    """Every row of unvalidated and validation_queue, and the validated count."""
    with conn.cursor() as cur:
        cur.execute("SELECT record_id::text, to_jsonb(u) FROM unvalidated u")
        records = dict(cur.fetchall())
        cur.execute("SELECT queue_id::text, to_jsonb(q) FROM validation_queue q")
        queue = dict(cur.fetchall())
        cur.execute("SELECT COUNT(*) FROM validated")
        published = cur.fetchone()[0]
    conn.commit()
    return records, queue, published


def _without_answer(row):
    return {k: v for k, v in row.items() if k != "llm_validator"}


def _open_transactions(conn):
    """Other sessions on this database inside a transaction."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'")
        n = cur.fetchone()[0]
    conn.commit()
    return n


def _mixed(conn):
    """One record of each kind, by name."""
    return {
        "cant_tell": _seed(conn, "unsure_original", "plain", title="Cant tell open"),
        "quote_flagged": _seed(conn, "plain", "quote_flagged", status="consensus_reached"),
        "corrections_differ": _seed(conn, "doi_o_corrected", "plain"),
        "closed_cant_tell": _seed(conn, "plain", "unsure_original", status="validated"),
        "closed_quote_flagged": _seed(conn, "quote_flagged", "plain", status="rejected"),
        "senior_reject": _seed(conn, "senior_reject", "unsure_original", status="rejected"),
        "agreement": _seed(conn, "plain", "plain", status="consensus_reached"),
        "checks_differ": _seed(conn, "plain", "outcome_failed"),
        "one_judgement": _seed(conn, "unsure_original"),
        "answered": _seed(conn, "unsure_original", "plain", llm=ERROR),
        "waiting": _seed(conn, "unsure_original", "plain", status="validation_inprogress"),
    }


def _backups(tmp_path):
    return sorted((tmp_path / "backups").glob("*")) if (tmp_path / "backups").exists() else []


def test_the_dry_run_counts_lists_and_writes_nothing(db, monkeypatch, capsys, tmp_path):
    ids = _mixed(db)
    before = _everything(db)
    # A row lock held elsewhere: the dry run must not wait on it.
    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=2000")
    other = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with other.cursor() as cur:
            cur.execute("SELECT 1 FROM unvalidated FOR UPDATE")
        assert backfill.run(apply=False) is None
        out = capsys.readouterr().out
        assert backfill.run(apply=False, include_closed=True) is None
        closed_out = capsys.readouterr().out
    finally:
        other.rollback()
        other.close()

    assert _everything(db) == before
    assert _backups(tmp_path) == []
    for label, opened, closed in (("Can't tell", 1, 1), ("quote flagged", 1, 1),
                                  ("corrections differ", 1, 0), ("one of these cases", 3, 2)):
        assert re.search(rf"{re.escape(label)}\s+{opened}\s+{closed}\n", out), label
    assert re.search(r"other records, none of these:\s+2\s+1\b", out)
    assert re.search(r"senior reject \(final, no LLM\)\s+0\s+1\n", out)
    assert "Gemini calls --apply would make: 3\n" in out
    assert "Gemini calls --apply would make: 5\n" in closed_out
    for name in ("cant_tell", "quote_flagged", "corrections_differ"):
        assert ids[name] in out
    assert "Cant tell open" in out
    for name in ("closed_cant_tell", "closed_quote_flagged", "senior_reject", "agreement",
                 "checks_differ", "one_judgement", "answered", "waiting"):
        assert ids[name] not in out
    assert ids["closed_cant_tell"] in closed_out and ids["closed_quote_flagged"] in closed_out
    assert "Dry run — nothing written" in out


def test_apply_fills_only_the_selected_records_and_only_their_answer(
        db, monkeypatch, capsys, tmp_path):
    ids = _mixed(db)
    before_records, before_queue, _ = _everything(db)
    calls, whole_rows, open_during_calls = [], [], []

    def ask(record, context):
        # Noted, not asserted here: the script counts an exception as a failed call.
        calls.append((record["record_id"], context))
        whole_rows.append(sorted(record) == sorted(_row(db, record["record_id"])))
        open_during_calls.append(_open_transactions(db))
        return {**CLEAN, "notes": record["record_id"]}

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    result = backfill.run(apply=True)

    selected = [ids["cant_tell"], ids["quote_flagged"], ids["corrections_differ"]]
    assert sorted(calls) == sorted((rid, "sanity_check") for rid in selected)
    assert whole_rows == [True] * 3                 # the SELECT * row, as consensus passes it
    assert open_during_calls == [0] * 3, "a transaction was open while Gemini answered"
    assert (result["filled"], result["errored"], result["skipped"]) == (3, 0, 0)
    after_records, after_queue, published = _everything(db)
    for rid, before in before_records.items():
        if rid in selected:
            assert after_records[rid]["llm_validator"] == {**CLEAN, "notes": rid}
            assert _without_answer(after_records[rid]) == _without_answer(before)
        else:
            assert after_records[rid] == before
    assert after_queue == before_queue and published == 0

    # The backup lists the filled ids, in the order filled, and undoes the run.
    [backup] = _backups(tmp_path)
    assert re.fullmatch(r"llm_backfill_\d{8}T\d{6}Z(_\d+)?\.json", backup.name)
    assert backup == result["backup"]
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert saved["filled_record_ids"] == [rid for rid, _ in calls]
    assert saved["answers"] == {rid: {**CLEAN, "notes": rid} for rid in selected}
    with db, db.cursor() as cur:
        for rid, answer in saved["answers"].items():
            cur.execute("UPDATE unvalidated SET llm_validator = NULL "
                        "WHERE record_id = %s AND llm_validator = %s::jsonb",
                        (rid, json.dumps(answer)))
            assert cur.rowcount == 1
    assert _everything(db)[0] == before_records
    assert "Filled 3; Gemini failed on 0" in capsys.readouterr().out


def test_a_failed_call_writes_nothing_and_a_re_run_asks_again(db, monkeypatch, capsys):
    errors = _seed(db, "unsure_original", "plain")
    raises = _seed(db, "quote_flagged", "plain")
    works = _seed(db, "doi_o_corrected", "plain")
    before = {rid: _row(db, rid) for rid in (errors, raises, works)}

    def ask(record, context):
        if record["record_id"] == errors:
            return ERROR
        if record["record_id"] == raises:
            raise RuntimeError("boom")
        return CLEAN

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    result = backfill.run(apply=True)
    assert (result["filled"], result["errored"], result["skipped"]) == (1, 2, 0)
    assert _row(db, errors) == before[errors] and _row(db, raises) == before[raises]
    assert _row(db, works)["llm_validator"] == CLEAN
    assert json.loads(result["backup"].read_text(encoding="utf-8"))["filled_record_ids"] == [works]
    out = capsys.readouterr().out
    assert "Gemini failed, nothing written: timeout" in out
    assert "RuntimeError: boom" in out

    monkeypatch.setattr(llm_validator, "run_llm_validation", lambda record, context: CLEAN)
    result = backfill.run(apply=True)
    assert (result["filled"], result["errored"]) == (2, 0)
    assert _row(db, errors)["llm_validator"] == CLEAN == _row(db, raises)["llm_validator"]


@pytest.mark.parametrize("change", [
    "llm_validator = '{\"outcome_check\": \"correct\", \"context\": \"sanity_check\"}'::jsonb",
    "llm_validator = '{\"error\": \"the submission failed too\"}'::jsonb",
    "validation_status = 'validated'",
], ids=["answered_meanwhile", "errored_meanwhile", "closed_meanwhile"])
def test_a_record_changed_meanwhile_is_left_as_it_is(db, monkeypatch, capsys, change):
    first = _seed(db, "unsure_original", "plain")
    second = _seed(db, "quote_flagged", "plain")
    calls = []

    def ask(record, context):
        calls.append(record["record_id"])
        # Both change while Gemini answers the first. No lock is held: NOWAIT.
        with db, db.cursor() as cur:
            cur.execute("SELECT 1 FROM unvalidated FOR UPDATE NOWAIT")
            cur.execute(f"UPDATE unvalidated SET {change}")
        return CLEAN

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    result = backfill.run(apply=True)
    for rid in (first, second):
        assert _row(db, rid)["llm_validator"] != CLEAN
    assert len(calls) == 1                                  # the second is not asked
    assert (result["filled"], result["errored"], result["skipped"]) == (0, 0, 2)
    assert json.loads(result["backup"].read_text(encoding="utf-8"))["filled_record_ids"] == []
    out = capsys.readouterr().out
    assert "changed while Gemini answered, left as is" in out
    assert "changed meanwhile, left as is" in out


def test_open_only_leaves_a_closed_record_and_include_closed_fills_it(db, monkeypatch):
    opened = _seed(db, "unsure_original", "plain")
    validated = _seed(db, "plain", "doi_o_corrected", status="validated")
    rejected = _seed(db, "quote_flagged", "plain", status="rejected")
    closed_before = {rid: _row(db, rid) for rid in (validated, rejected)}
    calls = []

    def ask(record, context):
        calls.append(record["record_id"])
        return CLEAN

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    assert backfill.run(apply=True)["filled"] == 1
    assert calls == [opened]
    assert {rid: _row(db, rid) for rid in (validated, rejected)} == closed_before

    result = backfill.run(apply=True, include_closed=True)
    assert result["filled"] == 2
    assert sorted(calls[1:]) == sorted([validated, rejected])
    for rid in (validated, rejected):
        after = _row(db, rid)
        assert after["llm_validator"] == CLEAN
        assert _without_answer(after) == _without_answer(closed_before[rid])


def test_limit_handles_at_most_n_records(db, monkeypatch, capsys):
    records = [_seed(db, "unsure_original", "plain") for _ in range(3)]
    calls = []

    def ask(record, context):
        calls.append(record["record_id"])
        return CLEAN

    monkeypatch.setattr(llm_validator, "run_llm_validation", ask)
    backfill.run(apply=False, limit=2)
    assert "Gemini calls --apply would make: 2 (--limit; 3 in scope)" in capsys.readouterr().out
    assert calls == []

    result = backfill.run(apply=True, limit=2)
    assert result["filled"] == 2 and len(calls) == 2
    filled = json.loads(result["backup"].read_text(encoding="utf-8"))["filled_record_ids"]
    assert filled == calls
    assert [rid for rid in records if _row(db, rid)["llm_validator"] is None] == \
        [rid for rid in records if rid not in calls]

    result = backfill.run(apply=True, limit=2)
    assert result["filled"] == 1 and len(calls) == 3
    assert all(_row(db, rid)["llm_validator"] == CLEAN for rid in records)
