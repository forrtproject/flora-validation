"""Source Records rows of records that are no longer validated.

sync_validated.py copies every validated record into Source Records, which feeds
the FLoRA build. A record that leaves the validated set — sent back, rejected,
merged — used to keep its row there, still feeding FLoRA. Now its row is marked
deleted, keeps its row and display id, and no longer feeds anything.

The database checks are opt-in like tests/test_preparation_database.py
(FLORA_TEST_DATABASE_URL).
"""
import json
import uuid

import pytest
from psycopg2.extras import RealDictCursor

import enrich_works
import source_records_service as grid
import sync_validated
import transform_sources
from tests.test_auto_validation import _judge, _pair, _record, _validator, admin  # noqa: F401
from tests.test_preparation_database import local_database  # noqa: F401


def _validate(cur, record_id, title="A replication"):
    """Publish the record: a row in validated, as approval or consensus writes."""
    cur.execute("SELECT doi_r FROM unvalidated WHERE record_id = %s", (record_id,))
    doi_r = cur.fetchone()["doi_r"]
    cur.execute(
        "INSERT INTO validated (record_id, doi_r, study_r, title_r, doi_o, study_o, title_o, "
        "type, outcome) VALUES (%s, %s, '1', %s, '10.9/orig', '1', 'The original', "
        "'replication', 'failed') RETURNING validated_record_id::text AS id",
        (record_id, doi_r, title),
    )
    return cur.fetchone()["id"]


def _sync(conn, dry_run=False):
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        return sync_validated.sync(cur, dry_run)


def _rows(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT display_id, sheet_row_id, deleted_at, deleted_reason, "
                    "raw->>'record_id' AS record_id FROM source_records "
                    "WHERE source = 'validated' ORDER BY display_id")
        rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    return rows


def _published(conn):
    """The rows the FLoRA build reads."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        frame = transform_sources.load(cur)
    conn.commit()
    return sorted(frame["display_id"]) if len(frame) else []


def test_a_record_that_leaves_the_validated_set_is_marked_deleted_not_removed(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record_id = _record(cur, status="validated", admin_checked=True)
        _validate(cur, record_id)
    _sync(local_database)
    assert _published(local_database) == ["VAL-000001"]

    with local_database, local_database.cursor() as cur:        # rejected, say
        cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
    [row] = _rows(local_database)
    assert row["display_id"] == "VAL-000001" and row["deleted_at"] is not None
    assert row["deleted_reason"] == "The record is no longer validated."
    assert _published(local_database) == []
    assert _sync(local_database)["deleted"] == 0                   # already marked


def test_an_edit_replaces_the_row_and_the_old_one_is_marked_deleted_naming_the_new(local_database):
    """Every admin edit deletes the validated row and writes a new one."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record_id = _record(cur, status="validated", admin_checked=True)
        _validate(cur, record_id)
    _sync(local_database)
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
        _validate(cur, record_id, title="A corrected title")
    # Still validated at commit: the old row keeps feeding FLoRA until the sync.
    assert [r["deleted_at"] for r in _rows(local_database)] == [None]

    assert _sync(local_database, dry_run=True)["deleted"] == 1    # counted, not written
    assert [r["deleted_at"] for r in _rows(local_database)] == [None]
    stats = _sync(local_database)
    assert (stats["inserted"], stats["deleted"]) == (1, 1)
    old, new = _rows(local_database)
    assert (old["display_id"], new["display_id"]) == ("VAL-000001", "VAL-000002")
    assert old["deleted_reason"] == ("Replaced by VAL-000002, the newer row for the "
                                     "same validated record.")
    assert new["deleted_at"] is None
    assert _published(local_database) == ["VAL-000002"]


def test_a_reviewed_row_is_marked_deleted_too(local_database):
    """Review protects a row's values from the sync, not its record's status."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record_id = _record(cur, status="validated", admin_checked=True)
        validated_id = _validate(cur, record_id)
    _sync(local_database)
    with local_database, local_database.cursor() as cur:
        cur.execute("UPDATE source_records SET reviewed_at = NOW(), reviewed_by = 'luke' "
                    "WHERE sheet_row_id = %s", (validated_id,))
        cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
    assert _rows(local_database)[0]["deleted_at"] is not None


def test_sending_an_auto_validated_entry_back_takes_it_out_of_flora(admin, local_database):
    client, _ = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record_id = _record(cur, status="validated", auto_validated_rule="trusted")
        _validate(cur, record_id)
    _sync(local_database)
    assert client.post(f"/api/admin/entries/{record_id}/flag-review", json={}).status_code == 200
    assert _rows(local_database)[0]["deleted_at"] is not None
    assert _published(local_database) == []


def test_the_grid_lists_deleted_rows_apart_and_never_edits_them(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        gone = _record(cur, status="validated", admin_checked=True)
        live = _record(cur, status="validated", admin_checked=True)
        _validate(cur, gone)
        _validate(cur, live)
    _sync(local_database)
    # Published in one transaction, so which got VAL-000001 is not fixed.
    display = {r["record_id"]: r["display_id"] for r in _rows(local_database)}
    with local_database, local_database.cursor() as cur:
        cur.execute("DELETE FROM validated WHERE record_id = %s", (gone,))
        # The same paper as the live row: a deleted copy is not a duplicate.
        cur.execute("UPDATE source_records SET content_fingerprint = 'same-paper' "
                    "WHERE source = 'validated'")

    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        listed = grid.list_records(cur, {})
        deleted = grid.list_records(cur, {"deleted": "only"})
        groups = grid.duplicate_groups(cur, unresolved_only=False)
        [row] = [r for r in deleted["records"]]
        with pytest.raises(ValueError, match="deleted"):
            grid.update_record(cur, row["record_id"], {"outcome": "successful"},
                               row["version"], "luke")
        with pytest.raises(ValueError, match="deleted"):
            grid.resolve_duplicate(cur, row["record_id"], "distinct", "luke")
    local_database.rollback()
    assert [r["display_id"] for r in listed["records"]] == [display[live]]
    assert listed["records"][0]["is_duplicate"] is False
    assert (row["display_id"], listed["counts"]["deleted"], listed["counts"]["all_records"]) == \
        (display[gone], 1, 1)
    assert listed["counts"]["flagged"] == 0 and groups["total"] == 0


def test_enrichment_follows_what_flora_uses(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        gone = _record(cur, status="validated", admin_checked=True, doi_r="10.9/gone")
        _validate(cur, gone)
    _sync(local_database)
    with local_database, local_database.cursor() as cur:
        cur.execute("DELETE FROM validated WHERE record_id = %s", (gone,))
    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        dois = enrich_works.dois_in_product(cur)
    local_database.commit()
    assert "10.9/gone" not in dois


# ── the self-approval rule and a senior reject ────────────────────────────────

def test_a_senior_may_carry_through_their_own_senior_reject(admin, local_database):
    """A senior reject fills both slots with the senior's own reject by design; the
    same person in both slots any other way is not agreement."""
    client, who = admin
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        sophie = _validator(cur, "Sophie", tier=2)
        senior_reject = _record(cur, status="rejected")
        for slot in ("human_1", "human_2"):
            _judge(cur, senior_reject, slot, sophie, type_check="incorrect",
                   corrected_type="not_validation", original_check="incorrect",
                   outcome_check="incorrect",
                   additional_checks=json.dumps({"senior_reject": True}))
        twice = _pair(cur, sophie, sophie, status="rejected")
    who["validator_id"] = sophie
    detail = client.get(f"/api/admin/entries/{senior_reject}").json()["self_approval"]
    assert (detail["mine"], detail["allowed"]) == (True, True)
    assert client.get(f"/api/admin/entries/{twice}").json()["self_approval"]["allowed"] is False
