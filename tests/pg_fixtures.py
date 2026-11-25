"""Shared opt-in PostgreSQL fixtures and source-row helpers.

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database.
No production DSN or application .env value is used as a fallback.
"""
import atexit
import csv
import os
import uuid

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.extras import RealDictCursor
import pytest

import final_export
import flora_service


def _validated_pg_admin_url() -> str:
    configured = os.getenv("FLORA_TEST_DATABASE_URL")
    if not configured:
        pytest.skip("Set FLORA_TEST_DATABASE_URL to opt in to isolated PostgreSQL checks")
    parameters = parse_dsn(configured)
    hosts = [
        h.strip()
        for h in (parameters.get("host") or os.environ.get("PGHOST") or "").split(",")
        if h.strip()
    ]
    assert hosts and all(h in {"127.0.0.1", "localhost", "::1"} for h in hosts), (
        f"Non-loopback PG host: {parameters.get('host') or os.environ.get('PGHOST')!r}"
    )
    hostaddrs = [
        h.strip()
        for h in (parameters.get("hostaddr") or os.environ.get("PGHOSTADDR") or "").split(",")
        if h.strip()
    ]
    if hostaddrs:
        assert all(h in {"127.0.0.1", "::1"} for h in hostaddrs), (
            f"Non-loopback PG hostaddr: {parameters.get('hostaddr') or os.environ.get('PGHOSTADDR')!r}"
        )
    return configured


_TEMPLATE_DBS: dict[str, str] = {}


def _cleanup_template_dbs() -> None:
    for configured, tpl_name in list(_TEMPLATE_DBS.items()):
        _TEMPLATE_DBS.pop(configured, None)
        try:
            admin = psycopg2.connect(configured)
            admin.autocommit = True
            try:
                with admin.cursor() as cur:
                    cur.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(tpl_name)
                        )
                    )
            finally:
                admin.close()
        except Exception:
            pass


atexit.register(_cleanup_template_dbs)


def _ensure_template_db(configured: str) -> str:
    """Apply db_schema.sql twice once per process into a template DB for fast cloning."""
    tpl_name = _TEMPLATE_DBS.get(configured)
    if tpl_name is not None:
        return tpl_name
    tpl_name = "flora_pipeline_tpl_" + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(configured)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(tpl_name)))
        tpl_dsn = make_dsn(configured, dbname=tpl_name)
        tpl_conn = psycopg2.connect(tpl_dsn)
        try:
            with tpl_conn:
                with tpl_conn.cursor() as cur:
                    schema = (final_export.ROOT / "db_schema.sql").read_text(encoding="utf-8")
                    cur.execute(schema)
                    cur.execute(schema)
        finally:
            tpl_conn.close()
    except Exception:
        with admin.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(tpl_name))
            )
        raise
    finally:
        admin.close()
    _TEMPLATE_DBS[configured] = tpl_name
    return tpl_name


@pytest.fixture
def local_database(monkeypatch):
    configured = _validated_pg_admin_url()
    tpl_name = _ensure_template_db(configured)
    name = "flora_pipeline_test_" + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(configured)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(tpl_name)
            )
        )
    dsn = make_dsn(configured, dbname=name)
    monkeypatch.setenv("DATABASE_URL", dsn)
    connection = psycopg2.connect(dsn)
    try:
        yield connection
    finally:
        flora_service.invalidate()
        connection.close()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


def add_source(cur, row, display_id):
    def value(column):
        return final_export.text(row.get(column)) or None
    cur.execute("""INSERT INTO source_records
        (source, sheet_row_id, display_id, type, doi_o, doi_r, url_o, url_r,
         ref_o, ref_r, outcome, outcome_quote, out_quote_source)
        VALUES ('replications', %s, %s, 'replication', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING record_id::text AS record_id""",
        (str(uuid.uuid4()), display_id, value("doi_o"), value("doi_r"), value("url_o"), value("url_r"),
         value("apa_ref_o"), value("apa_ref_r"), value("outcome"), value("outcome_quote"), value("outcome_quote_source")))
    return cur.fetchone()["record_id"]


def add_unregistered_source(connection, *, reference=False):
    with final_export.REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    if not reference:
        row = dict(row, doi_o="10.9999/standalone-original", doi_r="10.9999/standalone-repeat")
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            source_id = add_source(cur, row, "REPL-700001")
            for side, title in [("o", "An original study"), ("r", "A replication study")]:
                cur.execute(
                    """INSERT INTO work_metadata (doi, title, authors, year)
                       VALUES (%s, %s, 'Smith, A.', '2020')""",
                    (row[f"doi_{side}"], title),
                )
    return source_id
