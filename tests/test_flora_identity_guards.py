"""Published identities survive data edits and cannot be deleted or recycled.

These integration checks use only the explicitly opted-in localhost database
fixture, which creates and removes a disposable database for each test.
"""
import uuid

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

import flora_registry
from tests.test_preparation_database import local_database  # noqa: F401


def seed_identity(connection, *, published=True):
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""INSERT INTO source_records
                (source, sheet_row_id, display_id, type, doi_o, doi_r)
                VALUES ('replications', %s, 'REPL-700001', 'replication',
                        '10.9999/original', '10.9999/repeat')
                RETURNING record_id::text AS id""", (str(uuid.uuid4()),))
            source_id = cur.fetchone()["id"]
            cur.execute("""INSERT INTO flora_records
                (flora_id, primary_source_record_id, export_id, export_position)
                VALUES ('REPL-700001', %s, %s, %s)
                RETURNING flora_record_id::text AS id""",
                (source_id, "FLORA-000001" if published else None,
                 1 if published else None))
            registry_id = cur.fetchone()["id"]
    return source_id, registry_id


@pytest.mark.parametrize("table,column,value", [
    ("source_records", "record_id", str(uuid.uuid4())),
    ("source_records", "display_id", "REPL-700002"),
    ("flora_records", "flora_record_id", str(uuid.uuid4())),
    ("flora_records", "flora_id", "REPL-700002"),
    ("flora_records", "export_id", "FLORA-000002"),
    ("flora_records", "export_id", None),
    ("flora_records", "export_position", 2),
    ("flora_records", "export_position", None),
])
def test_assigned_identifiers_and_positions_cannot_change(local_database, table, column, value):
    source_id, registry_id = seed_identity(local_database)
    # The table and column names above are fixed test data, never user input.
    with pytest.raises(psycopg2.Error):
        with local_database:
            with local_database.cursor() as cur:
                cur.execute(f"UPDATE {table} SET {column} = %s", (value,))
    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT s.record_id::text AS source_id, s.display_id,
                              f.flora_record_id::text AS registry_id,
                              f.flora_id, f.export_id, f.export_position
                       FROM source_records s JOIN flora_records f
                         ON f.primary_source_record_id = s.record_id""")
        assert dict(cur.fetchone()) == {
            "source_id": source_id, "display_id": "REPL-700001",
            "registry_id": registry_id, "flora_id": "REPL-700001",
            "export_id": "FLORA-000001", "export_position": 1,
        }


@pytest.mark.parametrize("statement", [
    "DELETE FROM source_records",
    "DELETE FROM flora_records",
    "TRUNCATE source_records CASCADE",
    "TRUNCATE flora_records CASCADE",
])
def test_issued_identities_cannot_be_removed(local_database, statement):
    seed_identity(local_database)
    with pytest.raises(psycopg2.Error):
        with local_database:
            with local_database.cursor() as cur:
                cur.execute(statement)
    with local_database.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM flora_records WHERE export_id='FLORA-000001'")
        assert cur.fetchone()[0] == 1


def test_initial_publication_assignment_is_allowed_then_pinned(local_database):
    seed_identity(local_database, published=False)
    with local_database:
        with local_database.cursor() as cur:
            cur.execute("""UPDATE flora_records
                           SET export_id='FLORA-000001', export_position=1""")
            # Idempotent refreshes must remain valid after assignment.
            cur.execute("""UPDATE flora_records
                           SET export_id='FLORA-000001', export_position=1""")
    with pytest.raises(psycopg2.Error):
        with local_database:
            with local_database.cursor() as cur:
                cur.execute("UPDATE flora_records SET export_id='FLORA-000002'")


def test_corrections_survivor_changes_and_retirement_keep_original_id(local_database):
    seed_identity(local_database)
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("UPDATE source_records SET doi_o='10.9999/corrected'")
            cur.execute("""INSERT INTO source_records
                (source, sheet_row_id, display_id, type)
                VALUES ('replications', %s, 'REPL-700002', 'replication')
                RETURNING record_id::text AS id""", (str(uuid.uuid4()),))
            new_source = cur.fetchone()["id"]
            cur.execute("""UPDATE flora_records
                           SET primary_source_record_id=%s, retired_at=NOW()""",
                        (new_source,))
            cur.execute("UPDATE flora_records SET retired_at=NULL")
            cur.execute("SELECT export_id, export_position, retired_at FROM flora_records")
            assert dict(cur.fetchone()) == {
                "export_id": "FLORA-000001", "export_position": 1, "retired_at": None,
            }


def test_multiple_survivor_changes_and_return_preserve_one_identity(local_database, monkeypatch):
    original_source, registry_id = seed_identity(local_database)
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            extra_sources = []
            for number in (2, 3):
                cur.execute("""INSERT INTO source_records
                    (source, sheet_row_id, display_id, type)
                    VALUES ('replications', %s, %s, 'replication')
                    RETURNING record_id::text AS id""",
                    (str(uuid.uuid4()), f"REPL-70000{number}"))
                extra_sources.append(cur.fetchone()["id"])
            second, third = extra_sources
            cur.execute("UPDATE flora_records SET merged_source_record_ids=%s::uuid[]",
                        ([second],))

    monkeypatch.setattr(flora_registry, "backfill_source_history", lambda *a, **kw: None)
    monkeypatch.setattr(flora_registry, "record_history", lambda *a, **kw: None)
    monkeypatch.setattr(flora_registry, "_mint_ids",
                        lambda *a, **kw: pytest.fail("A survivor change must not mint an ID"))

    # The original A disappears, B promotes C, then C disappears and A returns.
    # Current merged contributors alone cannot retain A through this sequence.
    for source_id, merged in [(second, [third]), (third, []), (original_source, [])]:
        frame = pd.DataFrame({"source_record_id": [source_id]})
        frame.attrs["merged_record_ids"] = {source_id: merged}
        monkeypatch.setattr(flora_registry.transform_sources, "build",
                            lambda *a, _frame=frame, **kw: _frame)
        with local_database:
            with local_database.cursor(cursor_factory=RealDictCursor) as cur:
                stats = flora_registry.refresh(cur, verbose=False)
                assert stats["assigned"] == 0
                assert stats["rematched"] == 1
                cur.execute("""SELECT flora_record_id::text AS registry_id,
                                      export_id, export_position,
                                      primary_source_record_id::text AS source_id
                               FROM flora_records""")
                assert dict(cur.fetchone()) == {
                    "registry_id": registry_id, "export_id": "FLORA-000001",
                    "export_position": 1, "source_id": source_id,
                }

    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT historical_source_record_ids::text[] AS history FROM flora_records")
        assert set(cur.fetchone()["history"]) == {original_source, second, third}
