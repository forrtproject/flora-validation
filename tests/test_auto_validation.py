"""Auto-validation, the self-approval rule and the admin decision log.

Luke's rules: two agreeing validators skip admin review when the AI sanity check
agrees too and one of them is Trusted (or Senior), or both have more than 19
entries approved by an admin and fewer than 3 flags. Auto-validated entries are
marked with the rule, listed under their own filter, and can be sent back.

Josefina's rule: an admin who validated an entry decides it only when the other
validator agreed with them. Admin accounts are linked to the person's validator
account so that can be known; every decision is logged with what it changed.

The database checks are opt-in like tests/test_preparation_database.py
(FLORA_TEST_DATABASE_URL).
"""
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from psycopg2.extras import RealDictCursor

import auto_validate_waiting
import consensus_engine
import db_pool
from tests.test_dashboard_split import flora
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

AGREEING_AI = {"type_check": "correct", "original_check": "correct",
               "outcome_check": "correct", "context": "sanity_check"}


# ── seeding ───────────────────────────────────────────────────────────────────

def _validator(cur, handle, tier=0):
    cur.execute("INSERT INTO validators (handle, validator_tier) VALUES (%s, %s) RETURNING id",
                (handle, tier))
    return cur.fetchone()["id"]


def _record(cur, status="need_review", **columns):
    fields = {"doi_r": f"10.9/{uuid.uuid4().hex[:8]}", "type": "replication",
              "outcome": "failed", "validation_status": status, "title_r": "A replication",
              "study_r": "1", "doi_o": "10.9/orig", "title_o": "The original", "study_o": "1",
              "outcome_quote": "It did not replicate.", **columns}
    for key in ("validator_1", "validator_2", "llm_validator"):
        if isinstance(fields.get(key), dict):
            fields[key] = json.dumps(fields[key])
    cur.execute(f"INSERT INTO unvalidated ({', '.join(fields)}) "
                f"VALUES ({', '.join(['%s'] * len(fields))}) RETURNING record_id::text AS id",
                list(fields.values()))
    return cur.fetchone()["id"]


def _judge(cur, record_id, slot, validator_id, flagged=False, **columns):
    fields = {"type_check": "correct", "original_check": "correct", "outcome_check": "correct",
              **columns}
    names = ["record_id", "validator_slot", "is_shown", "is_validated", "validator_id",
             "flagged", *fields]
    cur.execute(f"INSERT INTO validation_queue ({', '.join(names)}) "
                f"VALUES ({', '.join(['%s'] * len(names))})",
                (record_id, slot, True, True, validator_id, flagged, *fields.values()))


def _pair(cur, v1, v2, status="need_review", second=None, **record):
    """A record judged by v1 and v2 (agreeing unless `second` changes v2's)."""
    record_id = _record(cur, status=status,
                        validator_1={"validator_id": v1, "validator_name": f"v{v1}"},
                        validator_2={"validator_id": v2, "validator_name": f"v{v2}"}, **record)
    _judge(cur, record_id, "human_1", v1)
    _judge(cur, record_id, "human_2", v2, **(second or {}))
    return record_id


def _state(conn, record_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT validation_status, auto_validated_rule, auto_validated_at, "
                    "admin_checked FROM unvalidated WHERE record_id = %s", (record_id,))
        row = dict(cur.fetchone())
        cur.execute("SELECT title_r FROM validated WHERE record_id = %s", (record_id,))
        published = cur.fetchone()
    conn.commit()
    return row, published


def _evaluate(conn, record_id, ai=AGREEING_AI):
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur, \
            patch("consensus_engine.run_llm_validation", return_value=ai):
        consensus_engine.evaluate_consensus(cur, record_id)


# ── consensus applies the rules ───────────────────────────────────────────────

def test_a_trusted_validator_s_agreement_is_validated_and_marked(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        trusted, regular = _validator(cur, "kat", tier=1), _validator(cur, "sam")
        record_id = _pair(cur, trusted, regular)
    _evaluate(local_database, record_id)
    row, published = _state(local_database, record_id)
    assert (row["validation_status"], row["auto_validated_rule"]) == ("validated", "trusted")
    assert row["auto_validated_at"] is not None and not row["admin_checked"]
    assert published is not None


def test_without_the_ai_or_a_trusted_validator_an_admin_approves(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        trusted, a, b = _validator(cur, "kat", tier=1), _validator(cur, "sam"), _validator(cur, "ali")
        ai_failed = _pair(cur, trusted, a)
        nobody_trusted = _pair(cur, a, b)
    _evaluate(local_database, ai_failed, ai={"error": "timeout"})
    _evaluate(local_database, nobody_trusted)
    for record_id in (ai_failed, nobody_trusted):
        row, published = _state(local_database, record_id)
        assert (row["validation_status"], row["auto_validated_rule"], published) == \
            ("consensus_reached", None, None)


def test_experience_counts_admin_approvals_only_and_flags_ever(local_database):
    """More than 19 entries approved by an admin, fewer than 3 flags. Entries the
    rules validated are not approvals, or the rules would feed themselves."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        a, b, c = _validator(cur, "ana"), _validator(cur, "ben"), _validator(cur, "cai")
        for i in range(20):
            for v in (a, b, c):
                done = _record(cur, status="validated", admin_checked=v != c)
                _judge(cur, done, "human_1", v, flagged=(v == b and i < 3))
        experienced = _pair(cur, a, a)                     # stats only; one person twice
        with_flags = _pair(cur, a, b)
        auto_only = _pair(cur, a, c)
        counts = {name: consensus_engine.auto_validation_stats(cur, rid) for name, rid in
                  (("experienced", experienced), ("with_flags", with_flags), ("auto_only", auto_only))}
    assert counts == {"experienced": (0, 2), "with_flags": (0, 1), "auto_only": (0, 1)}


# ── the admin panel ───────────────────────────────────────────────────────────

@pytest.fixture
def admin(local_database, monkeypatch):
    """The real app against the throwaway database; `who` is the signed-in admin."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    who = {"id": 1, "handle": "sophie", "trusted": True, "validator_id": None}
    flora.app.dependency_overrides[flora.current_admin] = lambda: dict(who)
    try:
        yield TestClient(flora.app), who
    finally:
        flora.app.dependency_overrides.clear()
        db_pool.clear_all()


def test_auto_validated_entries_have_their_own_filter_and_count(admin, local_database):
    client, _ = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        auto = _record(cur, status="validated", auto_validated_rule="trusted")
        _record(cur, status="validated", admin_checked=True)
    listed = client.get("/api/admin/entries?filter=auto_validated").json()
    assert [e["record_id"] for e in listed["entries"]] == [auto]
    assert listed["entries"][0]["auto_validated"] is True
    assert listed["entries"][0]["auto_validated_rule"] == "trusted"
    assert listed["counts"]["auto_validated"] == 1
    assert client.get("/api/admin/dashboard").json()["pipeline"]["auto_validated"] == 1


def test_an_auto_validated_entry_can_be_sent_back(admin, local_database):
    client, _ = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        trusted, regular = _validator(cur, "kat", tier=1), _validator(cur, "sam")
        record_id = _pair(cur, trusted, regular)
    _evaluate(local_database, record_id)
    response = client.post(f"/api/admin/entries/{record_id}/flag-review", json={})
    assert response.status_code == 200, response.text
    row, published = _state(local_database, record_id)
    assert (row["validation_status"], published) == ("need_review", None)
    detail = client.get(f"/api/admin/entries/{record_id}").json()
    assert [(d["action"], d["status_before"], d["status_after"]) for d in detail["decisions"]] == \
        [("sent_back", "validated", "need_review")]


def test_an_admin_approved_entry_cannot_be_sent_back_this_way(admin, local_database):
    client, _ = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record_id = _record(cur, status="validated", admin_checked=True)
    assert client.post(f"/api/admin/entries/{record_id}/flag-review", json={}).status_code == 400


def test_an_admin_who_validated_an_entry_decides_it_only_if_the_other_agreed(admin, local_database):
    client, who = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        sophie, luke = _validator(cur, "Sophie"), _validator(cur, "Luke")
        disagreed = _pair(cur, sophie, luke, status="consensus_reached", is_tiebreaker=True,
                          second={"outcome_check": "incorrect", "corrected_outcome": "successful"})
        agreed = _pair(cur, sophie, luke, status="consensus_reached", final_type="replication",
                       final_outcome="failed")
    who["validator_id"] = sophie

    blocked = client.post(f"/api/admin/entries/{disagreed}/approve")
    assert blocked.status_code == 403 and "another admin" in blocked.json()["detail"]
    resolve = client.post(f"/api/admin/entries/{disagreed}/resolve", json={
        "admin_name": "sophie", "type_check": "correct", "original_check": "correct",
        "outcome_check": "correct"})
    assert resolve.status_code == 403
    detail = client.get(f"/api/admin/entries/{disagreed}").json()
    assert detail["self_approval"] == {"linked": True, "mine": True, "allowed": False,
                                       "reason": flora.SELF_APPROVAL_MESSAGE}

    assert client.post(f"/api/admin/entries/{agreed}/approve").status_code == 200
    assert client.get(f"/api/admin/entries/{agreed}").json()["self_approval"]["allowed"] is True

    who["validator_id"] = None                                 # not linked: nothing to check
    assert client.post(f"/api/admin/entries/{disagreed}/approve").status_code == 200


def test_agreeing_with_yourself_is_not_agreement(admin, local_database):
    """The same person in both slots is not two people agreeing — unless it is a
    senior reject, which fills both by design (tests/test_deleted_source_rows.py)."""
    client, who = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        sophie = _validator(cur, "Sophie", tier=2)
        record_id = _pair(cur, sophie, sophie, status="rejected")
    who["validator_id"] = sophie
    assert client.get(f"/api/admin/entries/{record_id}").json()["self_approval"]["allowed"] is False
    resolve = client.post(f"/api/admin/entries/{record_id}/resolve", json={
        "admin_name": "sophie", "type_check": "correct", "original_check": "correct",
        "outcome_check": "correct"})
    assert resolve.status_code == 403


def test_an_assignment_judged_alone_is_not_self_approved(admin, local_database):
    client, who = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        sophie = _validator(cur, "Sophie")
        record_id = _record(cur, status="consensus_reached", final_type="replication",
                            final_outcome="failed",
                            validator_1={"validator_id": sophie, "is_assignment": True})
    who["validator_id"] = sophie
    assert client.post(f"/api/admin/entries/{record_id}/approve").status_code == 403
    # Two others agreeing does not make it someone else's decision: her own
    # judgement is on it too, and nobody agreed with that one.
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        luke, kat = _validator(cur, "Luke"), _validator(cur, "kat")
        _judge(cur, record_id, "human_1", luke)
        _judge(cur, record_id, "human_2", kat)
    assert client.post(f"/api/admin/entries/{record_id}/approve").status_code == 403


def test_the_list_marks_my_entries_and_can_hide_them(admin, local_database):
    client, who = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        sophie, luke, kat = _validator(cur, "Sophie"), _validator(cur, "Luke"), _validator(cur, "kat")
        mine = _pair(cur, sophie, luke)
        theirs = _pair(cur, luke, kat)
        # No stored copy yet, or only one: compared with NULL, these used to vanish.
        unjudged = _record(cur, status="unvalidated")
        one_copy = _record(cur, validator_1={"validator_id": luke})
    assert client.get("/api/admin/entries").json()["viewer_linked"] is False
    who["validator_id"] = sophie
    listed = client.get("/api/admin/entries").json()
    assert listed["viewer_linked"] is True
    assert {e["record_id"]: e["mine"] for e in listed["entries"]} == {
        mine: True, theirs: False, unjudged: False, one_copy: False}
    hidden = client.get("/api/admin/entries?hide_mine=true").json()
    assert sorted(e["record_id"] for e in hidden["entries"]) == sorted([theirs, unjudged, one_copy])
    assert hidden["total"] == 3


def test_every_decision_is_logged_with_what_it_changed(admin, local_database):
    client, _ = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        a, b = _validator(cur, "ana"), _validator(cur, "ben")
        record_id = _pair(cur, a, b, second={"outcome_check": "incorrect",
                                             "corrected_outcome": "successful"})
    response = client.post(f"/api/admin/entries/{record_id}/resolve", json={
        "admin_name": "sophie", "type_check": "correct", "original_check": "correct",
        "outcome_check": "incorrect", "corrected_outcome": "successful"})
    assert response.status_code == 200, response.text
    [decision] = client.get(f"/api/admin/entries/{record_id}").json()["decisions"]
    assert (decision["action"], decision["admin_handle"]) == ("resolved", "sophie")
    assert (decision["status_before"], decision["status_after"]) == ("need_review", "validated")
    # Compared as published: the extracted "failed" was replaced...
    assert decision["changes"]["outcome"] == ["failed", "successful"]
    # ...while values the resolve only copied into final_* did not change.
    for unchanged in ("type", "title_r", "title_o", "doi_o", "doi_r", "outcome_quote"):
        assert unchanged not in decision["changes"], unchanged


# ── linking admin accounts to validator accounts ──────────────────────────────

def test_admins_are_linked_to_their_validator_account(admin, local_database):
    client, who = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        sophie_v, luke_v = _validator(cur, "Sophie"), _validator(cur, "Luke")
        cur.execute("INSERT INTO validators (handle, email) VALUES ('s_work', 'sophie@x.org') "
                    "RETURNING id")
        work_v = cur.fetchone()["id"]
        cur.execute("INSERT INTO admins (id, handle, trusted, email) VALUES "
                    "(1, 'hamid', TRUE, NULL), (2, 'Luuke', TRUE, NULL), "
                    "(3, 'sophie', TRUE, 'sophie@x.org')")
    listed = {a["handle"]: a for a in client.get("/api/admin/admins").json()["admins"]}
    # The same email outranks the same handle, even beside a validator with no email.
    assert listed["sophie"]["suggested_validator_id"] == work_v
    assert listed["Luuke"]["suggested_validator_id"] is None             # nothing to suggest

    assert client.post("/api/admin/admins/2/validator",
                       json={"validator_id": luke_v}).status_code == 200
    listed = {a["handle"]: a for a in client.get("/api/admin/admins").json()["admins"]}
    assert (listed["Luuke"]["validator_handle"], listed["Luuke"]["suggested_validator_id"]) == \
        ("Luke", None)
    # Never on your own account, linking or unlinking: either could lift the rule
    # from your own entries.
    for target in (sophie_v, None):
        assert client.post("/api/admin/admins/1/validator",
                           json={"validator_id": target}).status_code == 400
    assert client.post("/api/admin/admins/2/validator",
                       json={"validator_id": 99999}).status_code == 404
    who["trusted"] = False
    assert client.post("/api/admin/admins/3/validator",
                       json={"validator_id": sophie_v}).status_code == 403
    with local_database.cursor() as cur:
        cur.execute("SELECT action, target_label FROM security_events")
        assert cur.fetchall() == [("admin.validator_linked", "Luuke")]
    local_database.commit()


# ── the entries already waiting ───────────────────────────────────────────────

def test_the_waiting_entries_are_settled_once_by_the_same_rules(local_database, capsys):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        trusted, a = _validator(cur, "kat", tier=1), _validator(cur, "sam")
        waiting = dict(status="consensus_reached", llm_validator=AGREEING_AI,
                       final_type="replication", final_outcome="failed",
                       title_r="Old title", final_title_r="Agreed title")
        passes = _pair(cur, trusted, a, **waiting)
        tiebreak = _pair(cur, trusted, a, **{**waiting, "is_tiebreaker": True})
        noted = _pair(cur, trusted, a, **{**waiting, "admin_notes": "waiting on Luke"})
        untrusted = _pair(cur, a, a, **waiting)
        ai_disagreed = _pair(cur, trusted, a, **{**waiting, "llm_validator": {
            **AGREEING_AI, "outcome_check": "incorrect"}})

    auto_validate_waiting.run(apply=False)
    dry = capsys.readouterr().out
    assert "would be auto-validated:               1" in dry and passes in dry
    assert _state(local_database, passes)[0]["validation_status"] == "consensus_reached"

    auto_validate_waiting.run(apply=True)
    assert "saved as they are now" in capsys.readouterr().out
    row, published = _state(local_database, passes)
    assert (row["validation_status"], row["auto_validated_rule"]) == ("validated", "trusted")
    assert published["title_r"] == "Agreed title"          # what consensus stored, as approval would
    for record_id in (tiebreak, noted, untrusted, ai_disagreed):
        assert _state(local_database, record_id)[0]["validation_status"] == "consensus_reached"


# ── duplicates, and publishing what approval publishes ────────────────────────

DUPLICATE = dict(doi_r="10.9/dup", final_type="replication", final_outcome="failed")


def test_nothing_takes_over_another_entry_s_published_row(admin, local_database):
    """The validated upsert replaces a row with the same identity: auto-validating
    or approving a duplicate unpublished the other entry, with nothing to show it."""
    client, _ = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        trusted, a, b = _validator(cur, "kat", tier=1), _validator(cur, "sam"), _validator(cur, "ali")
        first = _pair(cur, a, b, status="consensus_reached", **DUPLICATE)
        by_rule = _pair(cur, trusted, a, **DUPLICATE)
        by_admin = _pair(cur, a, b, status="consensus_reached", **DUPLICATE)
    assert client.post(f"/api/admin/entries/{first}/approve").status_code == 200

    _evaluate(local_database, by_rule)
    assert _state(local_database, by_rule)[0]["validation_status"] == "consensus_reached"
    blocked = client.post(f"/api/admin/entries/{by_admin}/approve")
    assert blocked.status_code == 409 and "Mark as Resolved" in blocked.json()["detail"]
    row, published = _state(local_database, first)
    assert (row["validation_status"], published is not None) == ("validated", True)


def test_the_waiting_pass_publishes_what_approval_would(local_database, admin):
    """A reproduction that reached consensus before the axis quotes were stored has
    no final quotes or axes: approval falls back to the extracted ones, so must
    the pass. Duplicates are left for an admin, one of two waiting included."""
    client, _ = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        trusted, a = _validator(cur, "kat", tier=1), _validator(cur, "sam")
        repro = dict(status="consensus_reached", llm_validator=AGREEING_AI, type="reproduction",
                     outcome="computationally reproducible, robust", final_type="reproduction",
                     outcome_computation="computationally reproducible",
                     outcome_robustness="robust",
                     outcome_computational_quote="The code ran.",
                     outcome_robustness_quote="The result held.")
        by_pass = _pair(cur, trusted, a, **repro)
        by_admin = _pair(cur, trusted, a, **repro)
        twin_1 = _pair(cur, trusted, a, status="consensus_reached", llm_validator=AGREEING_AI,
                       **DUPLICATE)
        twin_2 = _pair(cur, trusted, a, status="consensus_reached", llm_validator=AGREEING_AI,
                       **DUPLICATE)
    assert client.post(f"/api/admin/entries/{by_admin}/approve").status_code == 200

    auto_validate_waiting.run(apply=True)
    columns = ("type, outcome, outcome_computation, outcome_computational_quote, "
               "out_quote_computational_source, outcome_robustness, outcome_robustness_quote, "
               "outcome_quote, out_quote_source, title_r, doi_o, title_o")
    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT record_id::text AS id, {columns} FROM validated "
                    "WHERE record_id::text = ANY(%s)", ([by_pass, by_admin],))
        rows = {r.pop("id"): r for r in cur.fetchall()}
    local_database.commit()
    assert rows[by_pass] == rows[by_admin]
    assert rows[by_pass]["outcome_computational_quote"] == "The code ran."
    twins = [_state(local_database, rid)[0]["validation_status"] for rid in (twin_1, twin_2)]
    assert sorted(twins) == ["consensus_reached", "validated"]


# ── the page ──────────────────────────────────────────────────────────────────

def test_the_page_shows_and_filters_auto_validated_and_own_entries():
    assert 'data-filter="auto_validated"' in INDEX and 'id="fc-auto-validated"' in INDEX
    assert 'id="admin-hide-mine"' in INDEX
    assert '{ text: "Auto-validated", cls: "status-auto" }' in APP_JS
    assert 'e.mine           ? \'<span class="admin-flag flag-mine"' in APP_JS
    assert 'id="admin-send-back-btn"' in APP_JS
    assert "selfApproval.mine && !selfApproval.allowed" in APP_JS
    assert '/admins/${btn.dataset.id}/validator' in APP_JS
