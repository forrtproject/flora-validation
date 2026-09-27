"""Agreeing with a replication's outcome while disagreeing with its quote.

Validators used to say that by choosing "Mischaracterised" and re-picking the
extracted category, which the admin card showed as "✗ semih suggests: failed" on a
failed record, which split consensus against a validator who clicked "Looks right",
and which cost the agreement bonus. Now:

- the outcome step offers "Right outcome, better quote" and greys the extracted
  category out of the correction list;
- the API stores a same-category "correction" as agreement plus a disputed quote;
- backfill_outcome_agreement.py applies that reading to judgements saved before;
- a better outcome quote earns a point;
- the admin card names what a suggestion replaces ("was failed").

The backfill checks marked with local_database need a real server and are opt-in
like tests/test_preparation_database.py (FLORA_TEST_DATABASE_URL).
"""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from psycopg2.extras import RealDictCursor

import backfill_outcome_agreement as backfill
import consensus_engine
import db_pool
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _import_app():
    """Import app.py with the database and scheduler stubbed out: init_db() runs at
    import and would otherwise apply db_schema.sql to whatever DATABASE_URL names."""
    os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
    os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")
    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 1}
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor
    with patch("psycopg2.connect", return_value=connection), patch(
        "apscheduler.schedulers.background.BackgroundScheduler.start"
    ):
        import app
    return app


flora = _import_app()


def _request(**fields):
    base = dict(record_id="00000000-0000-0000-0000-000000000001",
                type_check="correct", original_check="correct", outcome_check="incorrect",
                corrected_outcome="failed",
                corrected_outcome_quote="We failed to replicate the effect (d = 0.02).",
                additional_checks={"quote_not_in_abstract": True, "shown_outcome": "failed"})
    base.update(fields)
    return flora.JudgeRequest(**base)


# ── the API reads a same-category "correction" as agreement ───────────────────

@pytest.mark.parametrize("requested,stored", [
    ("failed", "failed"),
    ("Failed", "failed"),                 # case
    ("successful", "success"),            # a retired stored spelling
])
def test_the_shown_category_as_a_correction_is_stored_as_agreement(requested, stored):
    req = flora._same_outcome_as_agreement(
        _request(corrected_outcome=requested,
                 additional_checks={"quote_not_in_abstract": True, "shown_outcome": stored}),
        "replication", stored)
    assert req.outcome_check == "correct"
    assert req.corrected_outcome is None
    assert req.corrected_outcome_quote == "We failed to replicate the effect (d = 0.02)."
    assert req.additional_checks == {"quote_not_in_abstract": True, "shown_outcome": stored,
                                     "outcome_quote_disputed": True}


def test_a_correction_made_against_an_outcome_since_changed_stays_a_correction():
    """The page showed "successful"; the validator said "failed"; a nightly import
    has since changed the record to "failed" too. That was a correction."""
    req = _request(corrected_outcome="failed",
                   additional_checks={"shown_outcome": "successful"})
    assert flora._same_outcome_as_agreement(req, "replication", "failed") is req


def test_without_a_shown_outcome_nothing_is_read_as_agreement():
    """An older client cannot say what it showed; it is stored as sent."""
    req = _request(additional_checks={"quote_not_in_abstract": True})
    assert flora._same_outcome_as_agreement(req, "replication", "failed") is req


@pytest.mark.parametrize("change", [
    {"corrected_outcome": "successful"},                          # a real correction
    {"outcome_check": "correct", "corrected_outcome": None},      # already agreement
    {"type_check": "incorrect", "corrected_type": "replication"}, # a type change
])
def test_anything_else_is_left_as_sent(change):
    req = _request(**change)
    assert flora._same_outcome_as_agreement(req, "replication", "failed") is req


def test_reproductions_are_left_as_sent():
    """Axis judgements have their own same-value handling; this rule is the
    replication outcome's."""
    req = _request(corrected_outcome="computationally reproducible, robust")
    assert flora._same_outcome_as_agreement(
        req, "reproduction", "computationally reproducible, robust") is req


def test_both_judge_endpoints_apply_the_rule_before_validating_the_outcome():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    for endpoint in ("def assignment_judge(", "def judge("):
        body = source[source.index(endpoint):]
        body = body[:body.index("\n@app.")]
        assert body.index("_same_outcome_as_agreement(req") < body.index("_validated_outcome_request(")
        assert "improved_evidence=_improved_outcome_evidence(req, " in body


def test_consensus_counts_it_as_agreeing_with_looks_right():
    looks_right = {"type_check": "correct", "original_check": "correct",
                   "outcome_check": "correct", "corrected_outcome": None}
    old = {**looks_right, "outcome_check": "incorrect", "corrected_outcome": "failed"}
    new = flora._same_outcome_as_agreement(
        _request(corrected_outcome="failed"), "replication", "failed")
    new = {**looks_right, "outcome_check": new.outcome_check,
           "corrected_outcome": new.corrected_outcome}
    assert not consensus_engine._checks_agree(old, looks_right)
    assert consensus_engine._checks_agree(new, looks_right)
    assert consensus_engine._corrections_agree(new, looks_right)


# ── points ────────────────────────────────────────────────────────────────────

def test_the_better_quote_flow_no_longer_pays_less_than_agreeing():
    raw = _request(additional_checks={"shown_outcome": "failed"})
    read = flora._same_outcome_as_agreement(raw, "replication", "failed")
    record = {"outcome_quote": "Extracted sentence."}
    agree = flora._points_for(_request(outcome_check="correct", corrected_outcome=None,
                                       corrected_outcome_quote=None, additional_checks=None), 10)
    before = flora._points_for(raw, 10)
    after = flora._points_for(read, 10, improved_evidence=flora._improved_outcome_evidence(
        read, record, "replication"))
    assert before < agree < after
    assert after == agree + 1


@pytest.mark.parametrize("quote,record,earns", [
    ("A better sentence.", {"outcome_quote": "Extracted sentence."}, True),
    ("Extracted sentence.", {"outcome_quote": "Extracted sentence."}, False),   # unchanged
    ("Extracted sentence", {"outcome_quote": "Extracted sentence."}, False),    # a full stop
    ("extracted  SENTENCE.", {"outcome_quote": "Extracted sentence."}, False),  # case, spacing
    ("  ", {"outcome_quote": "Extracted sentence."}, False),                    # blank
    (None, {"outcome_quote": "Extracted sentence."}, False),
    # Compared with the extracted quote the screen showed, not an earlier final_*.
    ("Earlier fix.", {"outcome_quote": "Extracted.", "final_outcome_quote": "Earlier fix."}, True),
])
def test_only_reworded_outcome_evidence_earns_the_point(quote, record, earns):
    req = _request(corrected_outcome_quote=quote)
    assert flora._improved_outcome_evidence(req, record, "replication") is earns


def test_a_better_reproduction_axis_quote_earns_it_too():
    req = _request(corrected_outcome_quote=None,
                   corrected_computational_quote="The code ran and matched Table 2.")
    assert flora._improved_outcome_evidence(req, {"outcome_computational_quote": "Ran."},
                                            "reproduction")


@pytest.mark.parametrize("target_type,fields", [
    ("not_validation", {"corrected_outcome_quote": "Edited before switching type."}),
    ("reproduction", {"corrected_outcome_quote": "Edited before switching type."}),
    ("replication", {"corrected_outcome_quote": None,
                     "corrected_computational_quote": "Edited before switching type."}),
])
def test_evidence_left_over_from_another_type_earns_nothing(target_type, fields):
    req = _request(**fields)
    assert not flora._improved_outcome_evidence(
        req, {"outcome_quote": "x", "outcome_computational_quote": "y"}, target_type)


# ── "Can't tell" ──────────────────────────────────────────────────────────────

def _cant_tell(**fields):
    return _request(corrected_outcome=None, corrected_outcome_quote=None,
                    additional_checks={"was_unsure_outcome": True}, **fields)


def test_cant_tell_on_a_replication_outcome_is_accepted_and_keeps_the_outcome():
    """It used to be read as a correction with no value and refused with a 400."""
    assert flora._validated_outcome_request(_cant_tell(), "replication", "failed") == (
        "replication", "failed", None, None)


def test_cant_tell_still_needs_an_outcome_after_a_type_change():
    req = _cant_tell(type_check="incorrect", corrected_type="replication")
    with pytest.raises(flora.HTTPException) as refused:
        flora._validated_outcome_request(req, "reproduction", "computationally reproducible, robust")
    assert refused.value.status_code == 400


def test_cant_tell_stores_no_suggested_outcome():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    body = source[source.index("def judge("):]
    body = body[:body.index("\n@app.")]
    start = body.index("corrected_outcome = (")
    stored = body[start:body.index("cur.execute(", start)]
    assert "_unsure_without_correction(req)" in stored


# ── assignments: shown and judged against the same outcome ────────────────────

_DECIDED = {"type": "replication", "outcome": "failed", "outcome_quote": "Extracted.",
            "final_type": None, "final_outcome": "successful", "final_outcome_quote": "",
            "outcome_computation": None, "final_outcome_computation": None}


def test_an_assignment_shows_the_earlier_decision_not_the_extracted_value():
    """Extracted "failed", decided "successful": showing "failed" greyed it out of
    "Mischaracterised" while "Looks right" kept "successful" — no way back."""
    shown = flora._effective_record(_DECIDED)
    assert shown["outcome"] == "successful"
    assert shown["type"] == "replication"          # a blank decision falls back
    # So does a blank decided quote, as assignment_judge's storage always has:
    # showing it blank while "Looks right" stored the extracted one was a mismatch.
    assert shown["outcome_quote"] == "Extracted."
    assert not any(key.startswith("final_") for key in shown)


@pytest.mark.parametrize("field,final,decided,expected", [
    ("title_r", "final_title_r", "Fixed title", "Fixed title"),
    ("title_r", "final_title_r", "", "Extracted title"),          # blank falls back
    ("url_r", "final_url_r", "https://doi.org/10.1/x", "https://doi.org/10.1/x"),
    ("abstract_r", "final_abstract_r", "Pasted abstract.", "Pasted abstract."),
    ("title_o", "final_title_o", "Fixed original", "Fixed original"),
    ("doi_o", "final_doi_o", "", ""),                             # a cleared DOI stays cleared
    ("doi_o", "final_doi_o", None, "10.1/extracted"),
    ("outcome_robustness_quote", "final_robustness_quote", "", ""),  # an emptied axis quote too
])
def test_every_value_an_assignment_stores_is_the_one_it_showed(field, final, decided, expected):
    rec = {"title_r": "Extracted title", "url_r": "https://example.org/r",
           "abstract_r": "Extracted abstract.", "title_o": "Extracted original",
           "doi_o": "10.1/extracted", "outcome_robustness_quote": "Extracted axis quote.",
           final: decided}
    assert flora._effective_record(rec)[field] == expected


def test_looks_right_keeps_and_mischaracterised_can_restore_what_was_extracted():
    shown = flora._effective_record(_DECIDED)
    keep = _request(outcome_check="correct", corrected_outcome=None, additional_checks=None)
    assert flora._validated_outcome_request(keep, shown["type"], shown["outcome"])[1] == "successful"
    restore = _request(corrected_outcome="failed", additional_checks={"shown_outcome": "successful"})
    restore = flora._same_outcome_as_agreement(restore, shown["type"], shown["outcome"])
    assert restore.outcome_check == "incorrect"      # a real correction, kept as one
    assert flora._validated_outcome_request(restore, shown["type"], shown["outcome"])[1] == "failed"


def test_both_assignment_endpoints_use_the_same_effective_record():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    fetch = source[source.index("def get_assignment("):]
    fetch = fetch[:fetch.index("\n@app.")]
    assert "_enrich_pair(_effective_record(" in fetch
    judge = source[source.index("def assignment_judge("):]
    judge = judge[:judge.index("\n@app.")]
    assert "shown = _effective_record(rec)" in judge
    assert "_same_outcome_as_agreement(req, base_type, base_outcome)" in judge
    assert "_improved_outcome_evidence(req, shown, final_type)" in judge
    # Every stored fallback is the shown value, never a second reading of rec.
    finals = judge[judge.index("final_computational_quote = ("):judge.index("summary = {")]
    assert 'rec.get("final_' not in finals and 'rec["' not in finals


def test_a_shown_outcome_that_is_not_text_is_a_400_not_a_crash():
    """A crash would be a 500, which /judge's failure guard answers with a stamp."""
    req = _request(additional_checks={"shown_outcome": 3})
    with pytest.raises(flora.HTTPException) as refused:
        flora._same_outcome_as_agreement(req, "replication", "failed")
    assert refused.value.status_code == 400


def test_cant_tell_on_a_record_with_no_extracted_outcome_is_cannot_be_determined():
    """Hard-pool records can have none; "Can't tell" used to be refused with a 400."""
    req = _request(corrected_outcome=None, additional_checks={"was_unsure_outcome": True})
    assert flora._validated_outcome_request(req, "replication", None)[1] == "cannot_be_determined"


def test_a_quote_typed_before_choosing_cant_tell_earns_no_point():
    req = _request(corrected_outcome=None, corrected_outcome_quote="A reworded sentence.",
                   additional_checks={"was_unsure_outcome": True})
    assert not flora._improved_outcome_evidence(req, {"outcome_quote": "Extracted."}, "replication")


def test_the_dashboard_does_not_count_cant_tell_as_a_correction():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    counts = source[source.index("AS type_corrections"):source.index("AS title_corrections")]
    assert "was_unsure_original" in counts and "was_unsure_outcome" in counts


# ── the backfill's rule matches the API's ─────────────────────────────────────

@pytest.mark.parametrize("judgement,record_type,record_outcome,matches", [
    ({"type_check": "correct", "outcome_check": "incorrect", "corrected_outcome": "failed"},
     "replication", "failed", True),
    ({"type_check": "correct", "outcome_check": "incorrect", "corrected_outcome": "successful"},
     "replication", "success", True),
    ({"type_check": "correct", "outcome_check": "incorrect", "corrected_outcome": "mixed"},
     "replication", "failed", False),
    ({"type_check": "incorrect", "outcome_check": "incorrect", "corrected_outcome": "failed"},
     "replication", "failed", False),
    ({"type_check": "correct", "outcome_check": "correct", "corrected_outcome": None},
     "replication", "failed", False),
    ({"type_check": "correct", "outcome_check": "incorrect", "corrected_outcome": "failed"},
     "reproduction", "failed", False),
])
def test_backfill_rule(judgement, record_type, record_outcome, matches):
    assert backfill.is_same_outcome_correction(judgement, record_type, record_outcome) is matches


def test_backfill_conversion_keeps_the_quote_and_marks_the_change():
    judgement = {"outcome_check": "incorrect", "corrected_outcome": "failed",
                 "corrected_outcome_quote": "Better.", "additional_checks": '{"x": 1}'}
    out = backfill.converted(judgement)
    assert (out["outcome_check"], out["corrected_outcome"]) == ("correct", None)
    assert out["corrected_outcome_quote"] == "Better."
    assert out["additional_checks"] == {"x": 1, "outcome_quote_disputed": True,
                                        "outcome_agreement_backfilled": True}
    assert backfill.converted(judgement, unverified=True)["additional_checks"][
        "outcome_agreement_unverified"] is True


@pytest.mark.parametrize("checks,verdict", [
    ({"shown_outcome": "failed"}, "verified"),
    ({"shown_outcome": "Failed "}, "verified"),                  # as sent, not canonical
    ({"shown_outcome": "successful"}, "changed"),                # an import moved it since
    ({}, "unverifiable"),                                        # before shown_outcome existed
    (None, "unverifiable"),
    ('{"shown_outcome": "failed"}', "verified"),                 # a JSON string, as stored
    ({"shown_outcome": 3}, "unverifiable"),                      # not text: no evidence, no crash
])
def test_backfill_asks_whether_the_judgement_recorded_todays_outcome(checks, verdict):
    assert backfill.saw_outcome({"additional_checks": checks}, "failed") == verdict


# ── the backfill against a real database ──────────────────────────────────────

def _seed(conn, *, status="need_review", admin_checked=False, assignment=False,
          outcome="failed", legacy_shown="failed", agree_shown="failed", tiebreaker_error=False,
          doi_r="10.9999/egress"):
    """A replication whose extracted outcome is `outcome`, with one legacy
    "Mischaracterised → failed" judgement (human_1) and one "Looks right"
    (human_2), each recording `*_shown` as the outcome its page showed (None: an
    older client that recorded nothing); or, for an assignment, only the JSON copy."""
    def checks(shown):
        return {"shown_outcome": shown} if shown is not None else None

    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("INSERT INTO validators (handle) VALUES ('semih'), ('luke') "
                    "ON CONFLICT (handle) DO NOTHING")
        cur.execute("SELECT id, handle FROM validators WHERE handle IN ('semih', 'luke')")
        ids = {r["handle"]: r["id"] for r in cur.fetchall()}
        legacy = {"validator_id": ids["semih"], "validator_name": "semih",
                  "type_check": "correct", "original_check": "correct",
                  "outcome_check": "incorrect", "corrected_outcome": "failed",
                  "corrected_outcome_quote": "We failed to replicate.",
                  "additional_checks": checks(legacy_shown), "is_assignment": assignment}
        agree = {"validator_id": ids["luke"], "validator_name": "luke",
                 "type_check": "correct", "original_check": "correct",
                 "outcome_check": "correct", "corrected_outcome": None,
                 "additional_checks": checks(agree_shown)}
        cur.execute(
            """INSERT INTO unvalidated (doi_r, type, outcome, validation_status, admin_checked,
                                        is_tiebreaker, llm_validator, validator_1, validator_2)
               VALUES (%s, 'replication', %s, %s, %s, %s, %s, %s, %s)
               RETURNING record_id::text AS record_id""",
            (doi_r, outcome, status, admin_checked, tiebreaker_error,
             json.dumps({"error": "timeout"}) if tiebreaker_error else None,
             json.dumps(legacy), None if assignment else json.dumps(agree)))
        record_id = cur.fetchone()["record_id"]
        if not assignment:
            for slot, j in (("human_1", legacy), ("human_2", agree)):
                cur.execute(
                    """INSERT INTO validation_queue
                         (record_id, validator_slot, is_shown, is_validated, validator_id,
                          validator_name, type_check, original_check, outcome_check,
                          corrected_outcome, corrected_outcome_quote, additional_checks)
                       VALUES (%s, %s, TRUE, TRUE, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (record_id, slot, j["validator_id"], j["validator_name"], j["type_check"],
                     j["original_check"], j["outcome_check"], j["corrected_outcome"],
                     j.get("corrected_outcome_quote"),
                     json.dumps(j["additional_checks"]) if j["additional_checks"] else None))
    return record_id


def _state(conn, record_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT validator_slot, outcome_check, corrected_outcome, "
                    "corrected_outcome_quote, additional_checks FROM validation_queue "
                    "WHERE record_id = %s ORDER BY validator_slot", (record_id,))
        queue = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT validation_status, validator_1, validator_2 FROM unvalidated "
                    "WHERE record_id = %s", (record_id,))
        record = dict(cur.fetchone())
    conn.commit()
    return queue, record


def _now_agree(out):
    """The records the run listed under 'need_review records that now agree' —
    not the conversion list above it, which names the same records."""
    section = out.split("need_review records that now agree:", 1)[1]
    count, *lines = section.strip().splitlines()
    return int(count), [line.strip() for line in lines if line.startswith("    ")]


@pytest.fixture
def offline_llm(monkeypatch):
    monkeypatch.setattr(consensus_engine, "run_llm_validation",
                        lambda record, context: {"error": "offline in tests"})


def test_backfill_dry_run_writes_nothing(local_database, capsys):
    record_id = _seed(local_database)
    before = _state(local_database, record_id)
    backfill.run(apply=False, reevaluate=False)
    assert _state(local_database, record_id) == before
    out = capsys.readouterr().out
    assert "validation_queue judgements to convert: 1" in out
    assert _now_agree(out) == (1, [record_id])


def test_backfill_converts_both_stores_and_lists_the_record(local_database, capsys):
    record_id = _seed(local_database)
    backfill.run(apply=True, reevaluate=False)
    queue, record = _state(local_database, record_id)
    semih = queue[0]
    assert (semih["outcome_check"], semih["corrected_outcome"]) == ("correct", None)
    assert semih["corrected_outcome_quote"] == "We failed to replicate."
    assert semih["additional_checks"]["outcome_quote_disputed"] is True
    assert record["validator_1"]["outcome_check"] == "correct"
    assert record["validator_1"]["corrected_outcome"] is None
    assert record["validation_status"] == "need_review"          # not re-evaluated
    assert _now_agree(capsys.readouterr().out) == (1, [record_id])
    # Idempotent: a second run converts nothing, but still lists the record.
    backfill.run(apply=True, reevaluate=False)
    out = capsys.readouterr().out
    assert "validation_queue judgements to convert: 0" in out
    assert _now_agree(out) == (1, [record_id])


def test_apply_then_reevaluate_in_a_later_run_still_settles_the_record(local_database, offline_llm):
    """The script's own advice: --apply first, --reevaluate after looking at the list."""
    record_id = _seed(local_database)
    backfill.run(apply=True, reevaluate=False)
    backfill.run(apply=True, reevaluate=True)
    assert _state(local_database, record_id)[1]["validation_status"] == "consensus_reached"


def test_a_record_an_admin_sent_back_for_review_is_not_reevaluated(local_database, capsys,
                                                                   offline_llm):
    """admin_flag_review sets need_review with the admin's name, not admin_checked."""
    record_id = _seed(local_database)
    with local_database, local_database.cursor() as cur:
        cur.execute("UPDATE unvalidated SET admin_name = 'adminX' WHERE record_id = %s",
                    (record_id,))
    backfill.run(apply=True, reevaluate=True)
    assert _now_agree(capsys.readouterr().out) == (0, [])
    assert _state(local_database, record_id)[1]["validation_status"] == "need_review"


def test_backfill_converts_an_assignment_that_lives_only_in_json(local_database):
    record_id = _seed(local_database, status="consensus_reached", assignment=True)
    backfill.run(apply=True, reevaluate=False)
    _, record = _state(local_database, record_id)
    assert record["validator_1"]["outcome_check"] == "correct"
    assert record["validator_1"]["is_assignment"] is True


def test_an_unverifiable_assignment_is_listed_before_include_unverified_converts_it(
        local_database, capsys):
    """An assignment has no queue row, so the unverifiable list — built from queue
    rows — left it out, and --include-unverified converted it unseen."""
    record_id = _seed(local_database, status="consensus_reached", assignment=True,
                      legacy_shown=None)
    backfill.run(apply=False, reevaluate=False)
    dry = capsys.readouterr().out
    assert "unverifiable, stored copy only:       1   → left as is" in dry
    listed = next(line for line in dry.splitlines() if record_id in line)
    assert "validator_1" in listed and "semih" in listed and "(assignment)" in listed
    assert _state(local_database, record_id)[1]["validator_1"]["outcome_check"] == "incorrect"

    backfill.run(apply=True, reevaluate=False)
    assert _state(local_database, record_id)[1]["validator_1"]["outcome_check"] == "incorrect"

    backfill.run(apply=True, reevaluate=False, include_unverified=True)
    assert "unverifiable, stored copy only:       1   → converting" in capsys.readouterr().out
    converted = _state(local_database, record_id)[1]["validator_1"]
    assert converted["outcome_check"] == "correct"
    assert converted["additional_checks"]["outcome_agreement_unverified"] is True


def test_a_stored_copy_is_not_listed_twice_beside_its_queue_row(local_database, capsys):
    record_id = _seed(local_database, legacy_shown=None)
    backfill.run(apply=False, reevaluate=False)
    dry = capsys.readouterr().out
    assert "unverifiable, stored copy only" not in dry
    assert sum(record_id in line for line in dry.splitlines()) == 1


def test_backfill_leaves_admin_checked_records_off_the_reevaluation_list(local_database, capsys):
    record_id = _seed(local_database, admin_checked=True)
    backfill.run(apply=False, reevaluate=False)
    assert _now_agree(capsys.readouterr().out) == (0, [])
    assert _state(local_database, record_id)[0][0]["outcome_check"] == "incorrect"


def test_backfill_reevaluate_settles_the_record(local_database, offline_llm):
    record_id = _seed(local_database)
    backfill.run(apply=True, reevaluate=True)
    assert _state(local_database, record_id)[1]["validation_status"] == "consensus_reached"


# ── only judgements that recorded today's outcome ─────────────────────────────

def test_a_genuine_correction_the_extractor_later_matched_is_left_alone(
        local_database, capsys, offline_llm):
    """Shown "successful", corrected to "failed", then an import changed the record
    to "failed" as well. It matches the pattern today, but it was a correction, and
    the "Looks right" beside it agreed with "successful"."""
    record_id = _seed(local_database, outcome="failed",
                      legacy_shown="successful", agree_shown="successful")
    backfill.run(apply=True, reevaluate=True)
    out = capsys.readouterr().out
    assert "shown another outcome (left as is):   1" in out
    assert "validation_queue judgements to convert: 0" in out
    assert _now_agree(out) == (0, [])
    queue, record = _state(local_database, record_id)
    assert (queue[0]["outcome_check"], queue[0]["corrected_outcome"]) == ("incorrect", "failed")
    assert record["validator_1"]["outcome_check"] == "incorrect"
    assert record["validation_status"] == "need_review"


def test_judgements_that_recorded_nothing_are_listed_not_converted(local_database, capsys, offline_llm):
    record_id = _seed(local_database, legacy_shown=None, agree_shown=None)
    backfill.run(apply=True, reevaluate=True)
    out = capsys.readouterr().out
    assert "unverifiable (nothing recorded):      1" in out
    assert f"      {record_id}  human_1" in out           # listed for a person to check
    assert "validation_queue judgements to convert: 0" in out
    assert _state(local_database, record_id)[0][0]["outcome_check"] == "incorrect"


def test_include_unverified_converts_them_but_never_reevaluates(local_database, capsys, offline_llm):
    record_id = _seed(local_database, legacy_shown=None, agree_shown=None)
    backfill.run(apply=True, reevaluate=True, include_unverified=True)
    out = capsys.readouterr().out
    assert "validation_queue judgements to convert: 1" in out
    assert _now_agree(out) == (0, [])
    queue, record = _state(local_database, record_id)
    assert queue[0]["outcome_check"] == "correct"
    assert queue[0]["additional_checks"]["outcome_agreement_unverified"] is True
    assert record["validator_1"]["additional_checks"]["outcome_agreement_unverified"] is True
    assert record["validation_status"] == "need_review"


def test_reevaluation_needs_both_judgements_to_have_recorded_todays_outcome(
        local_database, capsys, offline_llm):
    """The legacy judgement recorded today's outcome; the "Looks right" beside it
    recorded nothing and may have agreed with a different one."""
    record_id = _seed(local_database, agree_shown=None)
    backfill.run(apply=True, reevaluate=True)
    out = capsys.readouterr().out
    assert "validation_queue judgements to convert: 1" in out
    assert _now_agree(out) == (0, [])
    assert _state(local_database, record_id)[1]["validation_status"] == "need_review"


def test_a_judgement_changed_while_the_script_ran_is_left_alone(local_database, capsys, monkeypatch):
    """Each write is conditional on the judgement being as it was read."""
    record_id = _seed(local_database)
    read = backfill._json_copies

    def stale(cur):
        # What the script "read": an older version of the copy than the one stored.
        return [(rid, col, {**judgement, "validator_notes": "an older version"}, verdict)
                for rid, col, judgement, verdict in read(cur)]

    monkeypatch.setattr(backfill, "_json_copies", stale)
    backfill.run(apply=True, reevaluate=False)
    out = capsys.readouterr().out
    assert "stored JSON copies to convert:          0" in out
    assert "changed while this ran (left as is):    1" in out
    assert _state(local_database, record_id)[1]["validator_1"]["outcome_check"] == "incorrect"


# ── the app's nightly tiebreaker retry ────────────────────────────────────────

def test_the_tiebreaker_retry_leaves_converted_records_to_the_script(local_database, monkeypatch):
    """_retry_tiebreakers re-runs consensus on need_review records whose LLM
    tiebreaker errored — without the script's checks, so it must not settle one
    the script converted (and, for an unverifiable one, promised never to)."""
    converted = _seed(local_database, legacy_shown=None, agree_shown=None,
                      tiebreaker_error=True, doi_r="10.9999/converted")
    untouched = _seed(local_database, legacy_shown=None, agree_shown=None,
                      tiebreaker_error=True, doi_r="10.9999/untouched")
    with local_database, local_database.cursor() as cur:   # a real split: nothing to convert
        cur.execute("UPDATE validation_queue SET corrected_outcome = 'mixed' "
                    "WHERE record_id = %s AND validator_slot = 'human_1'", (untouched,))
        cur.execute("UPDATE unvalidated SET validator_1 = jsonb_set(validator_1, "
                    "'{corrected_outcome}', '\"mixed\"') WHERE record_id = %s", (untouched,))
    backfill.run(apply=True, reevaluate=False, include_unverified=True)
    retried = []
    monkeypatch.setattr(consensus_engine, "evaluate_consensus",
                        lambda cur, record_id: retried.append(str(record_id)))
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    try:
        flora._retry_tiebreakers()
    finally:
        db_pool.clear_all()
    assert converted not in retried
    assert untouched in retried


# ── both judge endpoints, end to end ──────────────────────────────────────────

@pytest.fixture
def api(local_database, monkeypatch, offline_llm):
    """The real app against the throwaway database, signed in as semih."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("INSERT INTO validators (handle) VALUES ('semih') RETURNING id")
        coder_id = cur.fetchone()["id"]
    flora.app.dependency_overrides[flora.current_validator] = lambda: {"coder_id": coder_id}
    try:
        yield TestClient(flora.app), coder_id
    finally:
        flora.app.dependency_overrides.clear()
        db_pool.clear_all()


def _record(conn, **columns):
    fields = {"doi_r": "10.9999/e2e", "type": "replication", "outcome": "failed",
              "outcome_quote": "We found no effect.", "abstract_r": "We found no effect.",
              "title_r": "A replication", **columns}
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"INSERT INTO unvalidated ({', '.join(fields)}) "
                    f"VALUES ({', '.join(['%s'] * len(fields))}) RETURNING record_id::text AS id",
                    list(fields.values()))
        return cur.fetchone()["id"]


def _judge_payload(record_id, **fields):
    return {"record_id": record_id, "type_check": "correct", "original_check": "correct",
            "outcome_check": "correct", **fields}


def _slot(conn, record_id, coder_id):
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO validation_queue (record_id, validator_slot, is_shown, "
                    "validator_id, validator_name) VALUES (%s, 'human_1', TRUE, %s, 'semih')",
                    (record_id, coder_id))


def _stored(conn, record_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT outcome_check, corrected_outcome, additional_checks, points "
                    "FROM validation_queue WHERE record_id = %s AND validator_slot = 'human_1'",
                    (record_id,))
        row = dict(cur.fetchone())
    conn.commit()
    return row


def test_judge_reads_the_shown_category_as_agreement(api, local_database):
    client, coder_id = api
    record_id = _record(local_database)
    _slot(local_database, record_id, coder_id)
    response = client.post("/api/judge", json=_judge_payload(
        record_id, outcome_check="incorrect", corrected_outcome="failed",
        corrected_outcome_quote="We found no effect (d = 0.02, 95% CI -0.1 to 0.1).",
        additional_checks={"shown_outcome": "failed"}))
    assert response.status_code == 200, response.text
    row = _stored(local_database, record_id)
    assert (row["outcome_check"], row["corrected_outcome"]) == ("correct", None)
    assert row["additional_checks"]["outcome_quote_disputed"] is True
    # vote_score 10 + original 2 + outcome agreement 2 + reworded quote 1
    assert row["points"] == 15


def test_judge_keeps_a_correction_made_against_an_outcome_since_changed(api, local_database):
    client, coder_id = api
    record_id = _record(local_database)
    _slot(local_database, record_id, coder_id)
    response = client.post("/api/judge", json=_judge_payload(
        record_id, outcome_check="incorrect", corrected_outcome="failed",
        additional_checks={"shown_outcome": "successful"}))
    assert response.status_code == 200, response.text
    assert _stored(local_database, record_id)["outcome_check"] == "incorrect"


def test_judge_accepts_cant_tell(api, local_database):
    client, coder_id = api
    record_id = _record(local_database)
    _slot(local_database, record_id, coder_id)
    response = client.post("/api/judge", json=_judge_payload(
        record_id, outcome_check="incorrect", additional_checks={"was_unsure_outcome": True}))
    assert response.status_code == 200, response.text
    assert _stored(local_database, record_id)["corrected_outcome"] is None


def test_judge_refuses_a_shown_outcome_that_is_not_text(api, local_database):
    client, coder_id = api
    record_id = _record(local_database)
    _slot(local_database, record_id, coder_id)
    response = client.post("/api/judge", json=_judge_payload(
        record_id, additional_checks={"shown_outcome": 3}))
    assert response.status_code == 400


def _assign(conn, record_id, coder_id):
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO assignments (record_id, validator_id, status) "
                    "VALUES (%s, %s, 'open')", (record_id, coder_id))


def _decided(conn, record_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT final_outcome, final_outcome_quote, final_title_r FROM unvalidated "
                    "WHERE record_id = %s", (record_id,))
        row = dict(cur.fetchone())
    conn.commit()
    return row


def test_an_assignment_shows_and_keeps_the_earlier_decision(api, local_database):
    client, coder_id = api
    record_id = _record(local_database, final_outcome="successful", final_outcome_quote="",
                        final_title_r="A corrected title")
    _assign(local_database, record_id, coder_id)
    pair = client.get(f"/api/assignment/{record_id}").json()["pair"]
    assert pair["outcome"] == "successful"
    assert pair["outcome_phrase"] == "We found no effect."     # a blank decision falls back
    assert pair["title_r"] == "A corrected title"
    response = client.post("/api/assignment-judge", json=_judge_payload(
        record_id, additional_checks={"shown_outcome": pair["outcome"]}))
    assert response.status_code == 200, response.text
    # "Looks right" stored exactly what the screen showed.
    assert _decided(local_database, record_id) == {
        "final_outcome": "successful", "final_outcome_quote": "We found no effect.",
        "final_title_r": "A corrected title"}


def test_an_assignment_can_restore_the_extracted_outcome(api, local_database):
    client, coder_id = api
    record_id = _record(local_database, final_outcome="successful")
    _assign(local_database, record_id, coder_id)
    pair = client.get(f"/api/assignment/{record_id}").json()["pair"]
    response = client.post("/api/assignment-judge", json=_judge_payload(
        record_id, outcome_check="incorrect", corrected_outcome="failed",
        additional_checks={"shown_outcome": pair["outcome"]}))
    assert response.status_code == 200, response.text
    assert _decided(local_database, record_id)["final_outcome"] == "failed"


@pytest.mark.parametrize("decided_doi", ["10.9999/corrected-original", ""])
def test_assignment_highlight_matches_the_effective_original(api, local_database, decided_doi):
    client, coder_id = api
    record_id = _record(
        local_database, doi_o="10.9999/wrong-original", title_o="Wrong original",
        url_o="https://doi.org/10.9999/wrong-original", oa_work_id_o="W123",
        year_o="1990", final_doi_o=decided_doi, final_title_o="Correct original")
    sibling_id = _record(local_database, doi_o="10.9999/sibling", title_o="Other original",
                         final_doi_o="10.9999/sibling-correction", final_title_o="Other decision")
    _assign(local_database, record_id, coder_id)
    response = client.get(f"/api/assignment/{record_id}")
    assert response.status_code == 200, response.text
    pair = response.json()["pair"]
    assert pair["coded_originals_total"] == 2
    current, = [original for original in pair["coded_originals"] if original["is_current"]]
    assert current["record_id"] == record_id
    assert current["doi_o"] == pair["doi_o"] == decided_doi
    assert current["title_o"] == pair["title_o"] == "Correct original"
    for field in ("url_o", "oa_work_id_o", "year_o", "authors_o"):
        assert current[field] is None
        assert pair[field] is None
    sibling, = [original for original in pair["coded_originals"] if not original["is_current"]]
    assert sibling["record_id"] == sibling_id
    assert sibling["doi_o"] == "10.9999/sibling"
    assert sibling["title_o"] == "Other original"


@pytest.mark.parametrize("shown_type", ["replication", "reproduction"])
def test_assignment_history_keeps_its_effective_baseline(api, local_database, shown_type):
    client, coder_id = api
    is_repro = shown_type == "reproduction"
    record_id = _record(
        local_database, type="replication" if is_repro else "reproduction",
        outcome="failed" if is_repro else "cannot_be_determined",
        outcome_computation=None if is_repro else "cannot_be_determined",
        outcome_robustness=None if is_repro else "cannot_be_determined",
        final_type=shown_type,
        final_outcome="computationally reproducible, robust" if is_repro else "successful",
        final_outcome_computation="computationally reproducible" if is_repro else None,
        final_outcome_robustness="robust" if is_repro else None,
        final_computational_quote="The computation reproduced." if is_repro else None,
        final_robustness_quote="" if is_repro else None,
        final_doi_o="", doi_o="10.9999/wrong-original",
        final_title_o="Correct original", title_o="Wrong original",
        final_title_r="Correct title", final_outcome_quote="The effective quote.")
    _assign(local_database, record_id, coder_id)
    pair = client.get(f"/api/assignment/{record_id}").json()["pair"]
    response = client.post("/api/assignment-judge", json=_judge_payload(
        record_id, additional_checks={"shown_outcome": pair["outcome"]}))
    assert response.status_code == 200, response.text
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT validator_1 FROM unvalidated WHERE record_id = %s", (record_id,))
        summary = cur.fetchone()["validator_1"]
        baseline = summary["shown_record"]
        assert baseline == {field: pair.get(field) for field, _, _ in flora._EFFECTIVE_FIELDS}
        assert baseline["type"] == shown_type
        assert baseline["doi_o"] == ""
        assert baseline["title_o"] == "Correct original"
        assert summary["type_check"] == "correct"
        assert summary["corrected_type"] is None
        # Future imports/decisions must not change what this agreement refers to.
        cur.execute("UPDATE unvalidated SET title_o = 'Later import', "
                    "final_title_o = 'Later decision' WHERE record_id = %s", (record_id,))
        cur.execute("SELECT validator_1 FROM unvalidated WHERE record_id = %s", (record_id,))
        assert cur.fetchone()["validator_1"]["shown_record"] == baseline


# ── the validator screen and the admin card ───────────────────────────────────

APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def _js_function(signature):
    start = APP_JS.index(signature)
    return APP_JS[start:APP_JS.index("\n}\n", start)]


def test_the_outcome_step_offers_right_outcome_better_quote():
    assert 'data-outcome="correct" data-quote-fix="1"' in APP_JS
    assert ">Right outcome, better quote</button>" in APP_JS
    assert "outcome_quote_fix: false" in _js_function("function blankJudgement()")


def test_choosing_it_opens_the_quote_editor_and_waits_for_a_reworded_quote():
    on_choice = _js_function("function onChoice(btn)")
    assert "state.judgement.outcome_quote_fix = !!btn.dataset.quoteFix" in on_choice
    assert '#edit-quote-btn")?.click()' in on_choice
    ready = _js_function("function updateSubmitState(pairBody)")
    assert "(!j.outcome_quote_fix || _hasBetterQuote(j, state.currentPair))" in ready
    better = _js_function("function _hasBetterQuote(j, p)")
    assert "_quoteWords(j.edited_outcome_quote) !== _quoteWords(p?.outcome_phrase)" in better


def test_a_click_on_another_choice_while_editing_lands_and_counts():
    """The blur that closed the editor moved the page before the click arrived, so
    "Mischaracterised" was lost and "Right outcome, better quote" went out."""
    assert "quote-edit-open" not in APP_JS             # no lock to refuse the click
    on_choice = _js_function("function onChoice(btn)")
    assert on_choice.index('dispatchEvent(new CustomEvent("quote:commit"))') < on_choice.index(
        "parent.querySelectorAll")
    editor = APP_JS[APP_JS.index('const editQuoteBtn = container.querySelector("#edit-quote-btn");'):]
    editor = editor[:editor.index("\n  }\n")]
    assert 'addEventListener("mousedown"' in editor and "e.preventDefault()" in editor
    assert '_saveQuote({ answer: false });' in editor          # blur
    assert 'addEventListener("quote:commit", () => _saveQuote({ answer: false }))' in editor
    # Only the explicit save completes the choice or asks for more.
    assert "if (answer && gate3 && state.judgement.outcome_quote_fix)" in editor


def test_every_button_below_an_open_editor_gets_its_click():
    """Every button and link keeps the focus in the textarea until its click: the
    save button itself (Safari and macOS Firefox do not focus a clicked button, so
    the blur-save closed the editor its click then reopened), Submit, the notes."""
    editor = APP_JS[APP_JS.index('const editQuoteBtn = container.querySelector("#edit-quote-btn");'):]
    editor = editor[:editor.index("\n  }\n")]
    assert 'e.target.closest("button, a")' in editor
    # Submit commits the open edit before its guards, as the inline inputs are.
    submit = _js_function("async function guardedSubmit()")
    assert submit.index('dispatchEvent(new CustomEvent("quote:commit"))') < submit.index(
        "You have an unsaved quote edit")


def test_a_side_effect_save_that_loses_the_better_quote_reopens_the_gate():
    editor = APP_JS[APP_JS.index("const _saveQuote = ({ answer = true } = {}) => {"):]
    editor = editor[:editor.index("\n    };\n")]
    tail = editor[editor.index("if (answer && gate3 && state.judgement.outcome_quote_fix)"):]
    assert "} else if (gate3 && state.judgement.outcome_quote_fix &&" in tail
    assert tail.count("_reopenGate(gate3)") == 2


def test_enter_on_a_keyboard_focused_choice_chooses_it():
    """Tab to a choice + Enter used to submit the previous answer instead."""
    handler = APP_JS[APP_JS.index('document.addEventListener("keydown", (e) => {\n  const onb'):]
    handler = handler[:handler.index("\n});\n")]
    assert 'matches(":focus-visible")' in handler
    assert handler.index(":focus-visible") < handler.index('$("#submit-btn")')


def test_a_record_with_no_extracted_outcome_offers_no_agreement():
    """The server refuses "Looks right" with nothing to agree with (a 400 the
    validator saw only after moving on); "Can't tell" stores cannot_be_determined."""
    mode = _js_function("function _applyOutcomeMode(pairBody)")
    assert "b.disabled = reclassifiedToReplication || noOutcome;" in mode
    ready = _js_function("function updateSubmitState(pairBody)")
    assert '(j.outcome !== "correct" || !!_canonicalOutcome(state.currentPair?.outcome))' in ready
    assert '"No outcome extracted"' in APP_JS


def test_the_chip_never_claims_a_better_quote_that_is_not_there():
    label = _js_function("function getAnswerLabel(btn)")
    assert '"Right outcome · quote needed"' in label
    on_choice = _js_function("function onChoice(btn)")
    assert "_reopenGate(gate);" in on_choice


def test_evidence_is_sent_only_with_the_type_it_evidences():
    payload = APP_JS[APP_JS.index("const payload = {"):]
    payload = payload[:payload.index("};")]
    assert "corrected_outcome_quote: isReplication ?" in payload
    for field in ("computational_quote", "computational_source",
                  "robustness_quote", "robustness_source"):
        assert f"corrected_{field}:" in payload and "isReproduction ?" in payload


def test_the_submission_says_the_outcome_was_agreed_and_the_quote_disputed():
    submit = APP_JS[APP_JS.index("const addl = {};"):APP_JS.index("const payload = {")]
    assert "addl.outcome_quote_disputed = true" in submit
    assert 'j.outcome === "correct"' in submit


def test_every_replication_judgement_records_the_outcome_it_showed():
    """What the server's guard and any later backfill compare against: the record's
    outcome can change under an open page."""
    submit = APP_JS[APP_JS.index("const addl = {};"):APP_JS.index("const payload = {")]
    assert "addl.shown_outcome = p.outcome;" in submit


def test_the_extracted_category_is_greyed_out_of_the_correction_list():
    mode = _js_function("function _applyOutcomeMode(pairBody)")
    assert '#outcome-correction [data-correct-outcome]' in mode
    assert "_canonicalOutcome(b.dataset.correctOutcome) === extracted" in mode
    assert "b.disabled = same" in mode
    assert "reclassifiedToReplication ? null" in mode       # nothing to match after a type change


def test_a_verified_draft_with_a_same_category_correction_resumes_as_the_new_choice():
    replay = _js_function("function _replayGates(card, draft)")
    assert "sameAsExtracted" in replay and 'click("[data-quote-fix]")' in replay


def test_the_admin_card_names_what_a_suggestion_replaces():
    card = APP_JS[APP_JS.index("const humanCard = "):]
    card = card[:card.index("const typeCorr")]
    assert '<span class="chk-was-tag">was</span>' in card
    assert "✎ improved the quote" in card and "outcome_quote_disputed" in card
    assert "couldn't tell" in card


def test_the_admin_card_describes_what_the_validator_was_shown():
    """An import can change the record's outcome, and an assignment shows an
    earlier decision over it: "was Successful ✗ suggests: Successful" otherwise."""
    card = APP_JS[APP_JS.index("const humanCard = "):]
    card = card[:card.index("const typeCorr")]
    assert "const shownOutcome = v.is_assignment && v.shown_record" in card
    assert "v.additional_checks?.shown_outcome || shown.outcome" in card
    assert 'shortRow("Outcome", fmtOutcome(shownOutcome)' in APP_JS
    # "couldn't tell" is the replication outcome's; a reproduction's axis rows say it.
    assert 'judgedType === "replication" && !!v.additional_checks?.was_unsure_outcome' in card
    # A punctuation-only edit is not called an improvement.
    assert "_quoteWords(v.corrected_outcome_quote) !== _quoteWords(shown.outcome_quote)" in card


def test_other_views_no_longer_call_cant_tell_a_correction():
    summary = APP_JS[APP_JS.index("const outS = summarize("):]
    summary = summary[:summary.index("if (outS)")]
    assert '"the outcome is unclear"' in summary and "was_unsure_outcome" in summary
    detail = _js_function("function _detailCheckRow(label, extracted, checkVal, corrected, unsure = false)")
    assert "? Couldn't tell" in detail
