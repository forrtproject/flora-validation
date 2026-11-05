"""db_schema.sql turns row-level security on, on every boot, against a real PostgreSQL
(opt-in, as tests/test_preparation_database.py)."""
import re
import uuid

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

import final_export
from tests.test_preparation_database import local_database  # noqa: F401

SCHEMA = (final_export.ROOT / "db_schema.sql").read_text(encoding="utf-8")


def _step():
    return re.search(r"DO \$\$\nDECLARE\n    open_table regclass;.*?END\n\$\$;", SCHEMA, re.S).group(0)


def _security(cur, table):
    cur.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,))
    return tuple(cur.fetchone().values())


def test_every_table_of_ours_has_it_and_no_table_is_forced(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c
                       JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                         AND pg_get_userbyid(c.relowner) = current_user""")
        rows = [dict(r) for r in cur.fetchall()]
    assert len(rows) > 20
    assert [r["relname"] for r in rows if not r["relrowsecurity"]] == []
    assert [r["relname"] for r in rows if r["relforcerowsecurity"]] == []


def test_the_step_replays_and_finds_a_table_added_later(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("CREATE TABLE added_later (id INT)")
        assert _security(cur, "added_later") == (False, False)
        cur.execute(SCHEMA)
        assert _security(cur, "added_later") == (True, False)
        cur.execute(SCHEMA)                               # and again, doing nothing
        assert _security(cur, "added_later") == (True, False)


def test_another_owners_table_and_an_extensions_table_are_left_alone(local_database):
    """Other owners' tables would make ALTER TABLE fail for a non-superuser, so a boot
    as the app's own role is run here as well."""
    app_role, other_role = f"app_{uuid.uuid4().hex[:8]}", f"other_{uuid.uuid4().hex[:8]}"
    conn = local_database
    try:
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            for role in (app_role, other_role):
                cur.execute(f'CREATE ROLE "{role}" NOSUPERUSER')
            cur.execute(f'GRANT ALL ON SCHEMA public TO "{app_role}"')
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            cur.execute(f'CREATE TABLE theirs (id INT); ALTER TABLE theirs OWNER TO "{other_role}"')
            cur.execute(f'CREATE TABLE ours (id INT); ALTER TABLE ours OWNER TO "{app_role}"')
            cur.execute(f'CREATE TABLE of_extension (id INT); ALTER TABLE of_extension OWNER TO "{app_role}"')
            cur.execute("ALTER EXTENSION pg_trgm ADD TABLE of_extension")

        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'SET LOCAL ROLE "{app_role}"')
            cur.execute(_step())                          # must not raise
            cur.execute("INSERT INTO ours VALUES (1)")    # owners bypass it
            cur.execute("SELECT COUNT(*) AS n FROM ours")
            assert cur.fetchone()["n"] == 1

        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert _security(cur, "ours") == (True, False)
            assert _security(cur, "theirs") == (False, False)
            assert _security(cur, "of_extension") == (False, False)
            cur.execute(SCHEMA)                           # as the superuser that does not own them
            assert _security(cur, "theirs") == (False, False)
    finally:
        with conn, conn.cursor() as cur:
            cur.execute("ALTER EXTENSION pg_trgm DROP TABLE of_extension")
            cur.execute("DROP TABLE IF EXISTS theirs, ours, of_extension")
            cur.execute(f'REVOKE ALL ON SCHEMA public FROM "{app_role}"')
            cur.execute(f'DROP ROLE IF EXISTS "{app_role}"')
            cur.execute(f'DROP ROLE IF EXISTS "{other_role}"')
