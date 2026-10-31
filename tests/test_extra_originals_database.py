"""Additional originals against a real PostgreSQL (opt-in).

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database, as
for tests/test_preparation_database.py; each test runs in a throwaway database
with db_schema.sql applied twice. Without it every test here is skipped.
"""
import json
import uuid

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

import csv_to_db
from tests.test_preparation_database import local_database  # noqa: F401


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def _record(cur, status="validated", **columns):
    fields = {"doi_r": f"10.9/{uuid.uuid4().hex[:8]}", "type": "replication",
              "outcome": "failed", "validation_status": status, "title_r": "A replication",
              "study_r": "1", "doi_o": "10.9/orig", "title_o": "The original", "study_o": "",
              "outcome_quote": "It did not replicate.", **columns}
    for key in ("validator_1", "validator_2", "llm_validator"):
        if isinstance(fields.get(key), dict):
            fields[key] = json.dumps(fields[key])
    cur.execute(f"INSERT INTO unvalidated ({', '.join(fields)}) "
                f"VALUES ({', '.join(['%s'] * len(fields))}) RETURNING record_id::text AS id",
                list(fields.values()))
    return cur.fetchone()["id"]


# ── phase 1: schema and the extractor sync ────────────────────────────────────

def test_an_added_original_needs_a_parent_and_no_pair_id(local_database):
    conn = local_database
    with conn, _cursor(conn) as cur:
        parent = _record(cur, pair_id="p-1")
        child = _record(cur, origin="admin_extra_original", parent_record_id=parent,
                        doi_r="10.9/x")
        assert child
    for bad in ({"origin": "admin_extra_original", "parent_record_id": parent, "pair_id": "p-2"},
                {"origin": "admin_extra_original"},
                {"origin": "extractor", "parent_record_id": parent},
                {"origin": "extractor", "inherits_outcome": True},
                {"origin": "somewhere"}):
        with pytest.raises(psycopg2.errors.CheckViolation):
            with conn, _cursor(conn) as cur:
                _record(cur, **bad)


def test_the_extractor_sync_never_sees_an_added_original(local_database):
    conn = local_database
    with conn, _cursor(conn) as cur:
        parent = _record(cur, pair_id="p-1")
        child = _record(cur, origin="admin_extra_original", parent_record_id=parent)
    with conn, conn.cursor() as cur:
        existing = csv_to_db._load_existing_pair_ids(cur)
        assert existing == {"p-1": parent}
        assert child not in csv_to_db._retire_state(cur, ["p-1", ""]).values()


def test_suggestions_need_a_paper(local_database):
    conn = local_database
    with conn, _cursor(conn) as cur:
        rec = _record(cur, pair_id="p-1")
        cur.execute("INSERT INTO extra_original_suggestions (record_id, title_o) VALUES (%s, 'T')",
                    (rec,))
    with pytest.raises(psycopg2.errors.CheckViolation):
        with conn, _cursor(conn) as cur:
            cur.execute("INSERT INTO extra_original_suggestions (record_id, note) "
                        "VALUES (%s, 'only a note')", (rec,))


# ── phase 2: suggestions ride on the judgement ────────────────────────────────

@pytest.fixture
def validator_api(local_database, monkeypatch):
    """The real app against the throwaway database, signed in as validator 'ana'."""
    import os

    import db_pool
    from fastapi.testclient import TestClient
    from tests.test_dashboard_split import flora
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    with local_database, _cursor(local_database) as cur:
        cur.execute("INSERT INTO validators (handle) VALUES ('ana') RETURNING id")
        coder_id = cur.fetchone()["id"]
    flora.app.dependency_overrides[flora.current_validator] = lambda: {"coder_id": coder_id}
    try:
        yield TestClient(flora.app), coder_id
    finally:
        flora.app.dependency_overrides.clear()
        db_pool.clear_all()


def _open_slot(conn, record_id, coder_id):
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO validation_queue (record_id, validator_slot, is_shown, "
                    "validator_id, validator_name) VALUES (%s, 'human_1', TRUE, %s, 'ana')",
                    (record_id, coder_id))


def _judgement(record_id, **fields):
    return {"record_id": record_id, "type_check": "correct", "original_check": "correct",
            "outcome_check": "correct", **fields}


def _suggestions(conn, record_id):
    with _cursor(conn) as cur:
        cur.execute("SELECT * FROM extra_original_suggestions WHERE record_id = %s "
                    "ORDER BY position", (record_id,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    return rows


def test_garbage_suggestions_never_cost_the_judgement(validator_api, local_database):
    client, coder_id = validator_api
    with local_database, _cursor(local_database) as cur:
        record_id = _record(cur, status="unvalidated", pair_id="p-1")
    _open_slot(local_database, record_id, coder_id)
    garbage = [{"doi_or_url": "Smith 2019", "title": "x" * 5000, "study": {"a": 1}},
               {"doi_or_url": "javascript:alert(1)"}, {"note": "only a note"}, 7, None,
               {"doi_or_url": "https://doi.org/10.1/A"}, {"doi_or_url": "10.1/a"}]
    response = client.post("/api/judge", json=_judgement(record_id, extra_originals=garbage))
    assert response.status_code == 200, response.text
    rows = _suggestions(local_database, record_id)
    assert [(r["doi_o"], r["url_o"]) for r in rows] == [(None, None), (None, None), ("10.1/A", None)]
    assert len(rows[0]["title_o"]) == 1000 and rows[0]["validator_name"] == "ana"
    assert rows[1]["title_o"] == "javascript:alert(1)"
    with _cursor(local_database) as cur:
        cur.execute("SELECT is_validated, points FROM validation_queue WHERE record_id = %s",
                    (record_id,))
        slot = cur.fetchone()
        cur.execute("SELECT validator_1 FROM unvalidated WHERE record_id = %s", (record_id,))
        summary = cur.fetchone()["validator_1"]
    local_database.commit()
    assert slot["is_validated"] and slot["points"] > 0
    assert summary["extra_original_count"] == 3

    # A retry is refused by the slot's one-shot guard and stores nothing more.
    again = client.post("/api/judge", json=_judgement(record_id, extra_originals=garbage))
    assert again.status_code in (400, 409)
    assert len(_suggestions(local_database, record_id)) == 3


def test_neither_type_stores_no_suggestions(validator_api, local_database):
    client, coder_id = validator_api
    with local_database, _cursor(local_database) as cur:
        record_id = _record(cur, status="unvalidated", pair_id="p-2")
    _open_slot(local_database, record_id, coder_id)
    response = client.post("/api/judge", json=_judgement(
        record_id, type_check="incorrect", original_check="incorrect", outcome_check="incorrect",
        corrected_type="not_validation", extra_originals=[{"title": "Another"}]))
    assert response.status_code == 200, response.text
    assert _suggestions(local_database, record_id) == []


def test_an_assignment_stores_suggestions_without_a_queue_row(validator_api, local_database):
    client, coder_id = validator_api
    with local_database, _cursor(local_database) as cur:
        record_id = _record(cur, status="unvalidated", pair_id="p-3", restricted_access=True)
        cur.execute("INSERT INTO assignments (record_id, validator_id, status) "
                    "VALUES (%s, %s, 'open')", (record_id, coder_id))
    response = client.post("/api/assignment-judge", json=_judgement(
        record_id, extra_originals=[{"doi_or_url": "10.5/b", "study": "2"}]))
    assert response.status_code == 200, response.text
    rows = _suggestions(local_database, record_id)
    assert [(r["doi_o"], r["study_o"], r["queue_id"]) for r in rows] == [("10.5/b", "2", None)]


# ── phase 3: the admin's view ─────────────────────────────────────────────────

@pytest.fixture
def admin_api(local_database, monkeypatch):
    """The real app against the throwaway database; `who` is the signed-in admin."""
    import os

    import db_pool
    from fastapi.testclient import TestClient
    from tests.test_dashboard_split import flora
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    who = {"id": 1, "handle": "sophie", "trusted": True, "validator_id": None}
    flora.app.dependency_overrides[flora.current_admin] = lambda: dict(who)
    try:
        yield TestClient(flora.app), who
    finally:
        flora.app.dependency_overrides.clear()
        db_pool.clear_all()


def _suggest(cur, record_id, validator_name, **fields):
    cur.execute("INSERT INTO validators (handle) VALUES (%s) "
                "ON CONFLICT DO NOTHING RETURNING id", (validator_name,))
    row = cur.fetchone()
    cols = {"record_id": record_id, "validator_name": validator_name,
            "validator_id": row["id"] if row else None, **fields}
    cur.execute(f"INSERT INTO extra_original_suggestions ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))}) RETURNING suggestion_id", list(cols.values()))
    return cur.fetchone()["suggestion_id"]


def test_the_admin_sees_suggestions_grouped_and_flagged(admin_api, local_database):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _record(cur, pair_id="p-1", doi_r="10.7/REP", doi_o="10.7/own", admin_checked=True)
        _record(cur, pair_id="p-2", doi_r="10.7/rep", doi_o="10.7/SIB", status="need_review")
        first = _suggest(cur, parent, "ana", doi_o="10.7/NEW", study_o="2")
        _suggest(cur, parent, "ben", doi_o="10.7/new", note="Study 3 too")
        _suggest(cur, parent, "ben", doi_o="10.7/sib")
        _suggest(cur, parent, "ana", doi_o="10.7/OWN")
    view = client.get(f"/api/admin/entries/{parent}/extra-originals").json()
    by_doi = {g["doi_o"].lower(): g for g in view["suggestions"]}
    assert [s["validator_name"] for s in by_doi["10.7/new"]["suggested_by"]] == ["ana", "ben"]
    assert by_doi["10.7/sib"]["coded_match"]["validation_status"] == "need_review"
    assert by_doi["10.7/own"]["is_own_original"] is True
    # Not published yet, so nothing can be added.
    assert view["parent"]["can_add"] is False
    assert view["parent"]["block_code"] == "parent_not_validated"

    entries = client.get("/api/admin/entries", params={"filter": "extra_originals"}).json()
    assert entries["counts"]["extra_originals"] == 1
    assert [e["record_id"] for e in entries["entries"]] == [parent]
    detail = client.get(f"/api/admin/entries/{parent}").json()
    assert detail["extra_originals_summary"] == {"open_suggestions": 3, "children": 0}   # by paper

    response = client.post(f"/api/admin/entries/{parent}/extra-originals/suggestions/{first}/dismiss")
    assert response.json() == {"dismissed": 2}
    with _cursor(local_database) as cur:
        cur.execute("SELECT action, admin_handle FROM admin_decisions WHERE record_id = %s", (parent,))
        assert [tuple(r.values()) for r in cur.fetchall()] == [("suggestion_dismissed", "sophie")]
    local_database.commit()


# ── phase 4: adding originals and keeping them in step ────────────────────────

def _published_parent(cur, judged=True, **columns):
    """An extractor record an admin approved: its row in unvalidated, its row in
    validated, and (for resolve) two judged human slots."""
    fields = {"pair_id": f"p-{uuid.uuid4().hex[:6]}", "doi_r": "10.7/rep", "study_r": "1",
              "title_r": "A replication", "doi_o": "10.7/own", "study_o": "", "title_o": "Own",
              "abstract_r": "We found no effect at all.", "admin_checked": True, **columns}
    record_id = _record(cur, **fields)
    cur.execute(
        """
        INSERT INTO validated (record_id, doi_r, study_r, title_r, abstract_r, doi_o, study_o,
                               title_o, type, outcome, outcome_quote, admin_approved)
        SELECT record_id, doi_r, study_r, title_r, abstract_r, doi_o, study_o, title_o,
               type, outcome, outcome_quote, TRUE
        FROM unvalidated WHERE record_id = %s
        """,
        (record_id,),
    )
    if judged:
        for slot in ("human_1", "human_2"):
            cur.execute("INSERT INTO validation_queue (record_id, validator_slot, is_shown, "
                        "is_validated, type_check, original_check, outcome_check) "
                        "VALUES (%s, %s, TRUE, TRUE, 'correct', 'correct', 'correct')",
                        (record_id, slot))
    return record_id


def _add(client, parent, *originals):
    return client.post(f"/api/admin/entries/{parent}/extra-originals",
                       json={"originals": list(originals)})


def _row(conn, table, record_id):
    with _cursor(conn) as cur:
        cur.execute(f"SELECT * FROM {table} WHERE record_id = %s", (record_id,))
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def _resolve(client, record_id, **fields):
    body = {"admin_name": "sophie", "type_check": "correct", "original_check": "correct",
            "outcome_check": "correct", **fields}
    return client.post(f"/api/admin/entries/{record_id}/resolve", json=body)


def test_an_added_original_is_a_validated_record_of_its_own(admin_api, local_database):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
        sid = _suggest(cur, parent, "ana", doi_o="10.7/new")
    response = _add(client, parent, {"doi_o": "10.7/new", "title_o": "New", "study_o": "",
                                     "from_suggestion_ids": [sid]})
    assert response.status_code == 200, response.text
    child = response.json()["created"][0]["record_id"]
    u = _row(local_database, "unvalidated", child)
    assert (u["origin"], str(u["parent_record_id"]), u["pair_id"]) == ("admin_extra_original", parent, None)
    assert (u["validation_status"], u["admin_checked"], u["admin_name"]) == ("validated", True, "sophie")
    assert u["inherits_outcome"] is True and u["final_outcome"] == "failed"
    v = _row(local_database, "validated", child)
    assert v["admin_approved"] is True and (v["doi_r"], v["study_r"], v["study_o"]) == ("10.7/rep", "1", "")
    with _cursor(local_database) as cur:
        cur.execute("SELECT status, resolved_record_id::text AS r FROM extra_original_suggestions")
        assert [tuple(r.values()) for r in cur.fetchall()] == [("accepted", child)]
        cur.execute("SELECT record_id::text AS r, action FROM admin_decisions ORDER BY decision_id")
        assert [tuple(r.values()) for r in cur.fetchall()] == [
            (child, "extra_original_added"), (parent, "extra_original_added")]
    local_database.commit()


def test_re_resolving_the_entry_keeps_its_added_originals_and_passes_the_outcome_on(admin_api, local_database):
    """Every re-publication deletes the entry's validated rows by record_id; an
    added original must survive that, and an inheriting one follows the outcome."""
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
    inheriting = _add(client, parent, {"doi_o": "10.7/a", "title_o": "A"}).json()["created"][0]["record_id"]
    own = _add(client, parent, {"doi_o": "10.7/b", "title_o": "B", "own_outcome": True,
                                "outcome": "mixed", "outcome_quote": "Partly."}).json()["created"][0]["record_id"]
    response = _resolve(client, parent, outcome_check="incorrect", corrected_outcome="successful",
                        corrected_outcome_quote="It replicated.")
    assert response.status_code == 200, response.text
    assert _row(local_database, "validated", parent)["outcome"] == "successful"
    assert _row(local_database, "validated", inheriting)["outcome"] == "successful"
    assert _row(local_database, "validated", inheriting)["outcome_quote"] == "It replicated."
    assert _row(local_database, "validated", own)["outcome"] == "mixed"

    # Its replication title follows a correction on the entry; study_r does not.
    response = _resolve(client, parent, corrected_title_r="A replication, corrected")
    assert response.status_code == 200, response.text
    assert _row(local_database, "validated", inheriting)["title_r"] == "A replication, corrected"
    assert _row(local_database, "unvalidated", own)["final_title_r"] == "A replication, corrected"


@pytest.mark.parametrize("original,code", [
    ({"doi_o": "10.7/OWN", "title_o": "Own"}, "extra_original_is_own"),
    ({"doi_o": "10.7/SIB", "title_o": "Sibling"}, "extra_original_coded"),
])
def test_an_original_already_coded_is_refused(admin_api, local_database, original, code):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
        _record(cur, status="need_review", pair_id="p-sib", doi_r="10.7/REP", doi_o="10.7/sib")
    response = _add(client, parent, original)
    assert response.status_code == 409 and response.json()["detail"]["code"] == code


def test_the_same_original_twice_is_refused_whatever_the_case_or_blank_study(admin_api, local_database):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
    assert _add(client, parent, {"doi_o": "10.7/new", "title_o": "New"}).status_code == 200
    for again in ({"doi_o": "10.7/NEW", "title_o": "New"},                    # DOI case
                  {"doi_o": "https://doi.org/10.7/new", "title_o": "Other title"},
                  {"doi_o": "10.7/new", "title_o": "New", "study_o": None}):   # NULL study
        response = _add(client, parent, again)
        assert response.status_code == 409, again
        assert response.json()["detail"]["code"] == "extra_original_published"
    book = {"title_o": "A Book", "ref_o": "Smith (1990). A Book."}
    assert _add(client, parent, book).status_code == 200
    assert _add(client, parent, {**book, "title_o": "a book"}).status_code == 409


def test_a_second_original_without_a_doi_is_refused_since_flora_would_publish_one_record(admin_api, local_database):
    """transform_sources.dedup_keys merges rows sharing type | doi_o | doi_r | url_r, so
    two DOI-less originals of one replication would reach FLoRA as one record. The
    guard reads the real _family query: the entry's own original, an added one."""
    client, _ = admin_api
    book = {"title_o": "A Book", "ref_o": "Smith (1990). A Book."}
    other = {"title_o": "Another Book", "ref_o": "Jones (1985). Another Book."}
    with local_database, _cursor(local_database) as cur:
        bookless = _published_parent(cur, doi_o="", title_o="Own book", doi_r="10.7/nodoi-parent")
        with_doi = _published_parent(cur, doi_r="10.7/doi-parent")
        sibling = _published_parent(cur, doi_r="10.7/sib-parent")
        batch = _published_parent(cur, doi_r="10.7/batch-parent")
        _record(cur, status="need_review", pair_id="p-nodoi-sib", doi_r="10.7/SIB-PARENT",
                doi_o="", title_o="Sibling book")

    # The entry's own original has no DOI.
    response = _add(client, bookless, book)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "extra_original_no_doi" and detail["existing_record_id"] == bookless
    assert _add(client, bookless, {"doi_o": "10.7/withdoi", "title_o": "Has a DOI"}).status_code == 200

    # An added original has none: the first is fine, the second is not.
    first = _add(client, with_doi, book)
    assert first.status_code == 200, first.text
    child = first.json()["created"][0]["record_id"]
    response = _add(client, with_doi, other)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "extra_original_no_doi"
    assert response.json()["detail"]["existing_record_id"] == child
    # A DOI-less extractor record of the same replication counts too.
    response = _add(client, sibling, {"doi_o": "10.7/x", "title_o": "X"}, book)
    assert response.status_code == 409 and response.json()["detail"]["index"] == 1
    assert response.json()["detail"]["code"] == "extra_original_no_doi"
    # Two in one request: the second DOI-less one is the one refused.
    response = _add(client, batch, {"doi_o": "10.7/x", "title_o": "X"}, book, other)
    assert response.status_code == 409 and response.json()["detail"]["index"] == 2

    # A removed one no longer counts.
    assert client.post(f"/api/admin/entries/{child}/extra-original/remove").status_code == 200
    assert _add(client, with_doi, other).status_code == 200


def test_only_a_published_admin_reviewed_entry_takes_originals(admin_api, local_database):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        auto = _published_parent(cur, admin_checked=False)
        pending = _record(cur, status="need_review", pair_id="p-x", doi_r="10.7/other")
        parent = _published_parent(cur, doi_r="10.7/third")
    assert _add(client, auto, {"doi_o": "10.7/n", "title_o": "N"}).json()["detail"]["code"] == "parent_not_admin_checked"
    assert _add(client, pending, {"doi_o": "10.7/n", "title_o": "N"}).json()["detail"]["code"] == "parent_not_validated"
    child = _add(client, parent, {"doi_o": "10.7/n", "title_o": "N"}).json()["created"][0]["record_id"]
    assert _add(client, child, {"doi_o": "10.7/m", "title_o": "M"}).json()["detail"]["code"] == "extra_original_on_child"


def test_an_admin_does_not_add_their_own_suggestion(admin_api, local_database):
    client, who = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
        sid = _suggest(cur, parent, "sophie-as-validator", doi_o="10.7/new")
        cur.execute("SELECT validator_id FROM extra_original_suggestions WHERE suggestion_id = %s", (sid,))
        who["validator_id"] = cur.fetchone()["validator_id"]
    response = _add(client, parent, {"doi_o": "10.7/new", "title_o": "New", "from_suggestion_ids": [sid]})
    assert response.status_code == 403
    assert _add(client, parent, {"doi_o": "10.7/new", "title_o": "New"}).status_code == 200


def test_rejecting_the_entry_takes_its_added_originals_with_it(admin_api, local_database):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
    child = _add(client, parent, {"doi_o": "10.7/a", "title_o": "A"}).json()["created"][0]["record_id"]
    response = _resolve(client, parent, type_check="incorrect", corrected_type="not_validation",
                        original_check="incorrect", outcome_check="incorrect")
    assert response.status_code == 200, response.text
    assert _row(local_database, "validated", child) is None
    assert _row(local_database, "unvalidated", child)["validation_status"] == "rejected"


def test_approving_onto_an_added_original_is_refused_not_merged(admin_api, local_database):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
    child = _add(client, parent, {"doi_o": "10.7/a", "title_o": "A"}).json()["created"][0]["record_id"]
    with local_database, _cursor(local_database) as cur:
        later = _record(cur, status="consensus_reached", pair_id="p-later", doi_r="10.7/rep",
                        study_r="1", title_r="A replication", doi_o="10.7/a", title_o="A")
    response = client.post(f"/api/admin/entries/{later}/approve")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conflicts_with_added_original"
    assert response.json()["detail"]["added_record_id"] == child


def test_removing_an_added_original_reopens_its_suggestion(admin_api, local_database):
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
        sid = _suggest(cur, parent, "ana", doi_o="10.7/new")
    child = _add(client, parent, {"doi_o": "10.7/new", "title_o": "New",
                                  "from_suggestion_ids": [sid]}).json()["created"][0]["record_id"]
    assert client.post(f"/api/admin/entries/{child}/extra-original/remove").status_code == 200
    assert _row(local_database, "validated", child) is None
    with _cursor(local_database) as cur:
        cur.execute("SELECT status FROM extra_original_suggestions WHERE suggestion_id = %s", (sid,))
        assert cur.fetchone()["status"] == "open"
    local_database.commit()
    assert client.post(f"/api/admin/entries/{parent}/extra-original/remove").status_code == 409


def test_an_added_original_is_never_served_judged_or_counted_as_validator_work(admin_api, local_database):
    import consensus_engine
    import sync_validated
    from tests.test_dashboard_split import flora
    client, _ = admin_api
    with local_database, _cursor(local_database) as cur:
        parent = _published_parent(cur)
    before = client.get("/api/admin/dashboard").json()["pipeline"]
    child = _add(client, parent, {"doi_o": "10.7/a", "title_o": "A"}).json()["created"][0]["record_id"]
    after = client.get("/api/admin/dashboard").json()["pipeline"]
    assert after["validated"] == before["validated"] and after["total"] == before["total"]
    assert after["admin_extra_originals"] == 1
    with local_database, _cursor(local_database) as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM unvalidated u WHERE {flora._SERVABLE_SQL} "
                    "AND u.record_id = %s", (child,))
        assert cur.fetchone()["n"] == 0
        consensus_engine.evaluate_consensus(cur, child)            # no slots: nothing happens
        sync_validated.sync(cur, dry_run=False)
        cur.execute("SELECT display_id, doi_o FROM source_records WHERE source = 'validated' "
                    "AND raw->>'record_id' = %s", (child,))
        row = cur.fetchone()
    assert row["display_id"].startswith("VAL-") and row["doi_o"] == "10.7/a"
    assert _row(local_database, "unvalidated", child)["validation_status"] == "validated"
    counts = client.get("/api/admin/entries", params={"filter": "auto_validated"}).json()["counts"]
    assert counts["auto_validated"] == 0


def test_an_added_original_reaches_flora_on_its_own_and_leaves_cleanly(admin_api, local_database, monkeypatch):
    """Downstream: the real sync, transform and registry on a parent and its child."""
    import flora_registry
    import sync_validated
    import transform_sources
    from tests.test_dashboard_split import flora   # noqa: F401  (app under the throwaway database)
    monkeypatch.setattr("bibliographic_helpers.request", lambda *a, **kw: None)
    client, _ = admin_api
    conn = local_database

    def published():
        with conn, _cursor(conn) as cur:
            sync_validated.sync(cur, dry_run=False)
            flora_registry.refresh(cur, verbose=False)
        with _cursor(conn) as cur:
            frame = transform_sources.to_output_shape(
                flora_registry.attach_ids(cur, transform_sources.build(cur, verbose=False, metadata={})))
        conn.commit()
        return {r.doi_o: (r.id, r.outcome, r.outcome_quote) for r in frame.itertuples()}

    with conn, _cursor(conn) as cur:
        parent = _published_parent(cur)
    before = published()
    child = _add(client, parent, {"doi_o": "10.7/a", "title_o": "A"}).json()["created"][0]["record_id"]
    both = published()
    assert both["10.7/own"] == before["10.7/own"]                       # the entry keeps its id
    assert both["10.7/a"][0] != both["10.7/own"][0]                      # the original has its own
    assert both["10.7/a"][1:] == ("failed", "It did not replicate.")     # complete, outcome inherited
    assert client.post(f"/api/admin/entries/{child}/extra-original/remove").status_code == 200
    with _cursor(conn) as cur:                                           # retired at commit, before any sync
        cur.execute("SELECT deleted_at IS NOT NULL AS gone FROM source_records "
                    "WHERE raw->>'record_id' = %s", (child,))
        assert [r["gone"] for r in cur.fetchall()] == [True]
    conn.commit()
    assert published() == {"10.7/own": before["10.7/own"]}
