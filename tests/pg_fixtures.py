"""Shared opt-in PostgreSQL fixtures and source-row helpers.

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database.
No production DSN or application .env value is used as a fallback.
"""
import os
import uuid

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
import pytest

import final_export
import flora_service


@pytest.fixture
def local_database(monkeypatch):
    configured = os.getenv("FLORA_TEST_DATABASE_URL")
    if not configured:
        pytest.skip("Set FLORA_TEST_DATABASE_URL to opt in to isolated PostgreSQL checks")
    parameters = parse_dsn(configured)
    hosts = [h.strip() for h in (parameters.get("host") or os.environ.get("PGHOST") or "").split(",") if h.strip()]
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
    name = "flora_pipeline_test_" + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(configured)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = make_dsn(configured, dbname=name)
    monkeypatch.setenv("DATABASE_URL", dsn)
    connection = psycopg2.connect(dsn)
    try:
        with connection:
            with connection.cursor() as cur:
                schema = (final_export.ROOT / "db_schema.sql").read_text(encoding="utf-8")
                cur.execute(schema)
                cur.execute(schema)
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


def add_unregistered_source(cur, display_id, doi_o, doi_r, outcome="successful", *, url_r=None):
    cur.execute("""INSERT INTO source_records
        (source, sheet_row_id, display_id, type, doi_o, doi_r, url_r, outcome, outcome_quote)
        VALUES ('replications', %s, %s, 'replication', %s, %s, %s, %s, 'quote')
        RETURNING record_id::text AS record_id""",
        (str(uuid.uuid4()), display_id, doi_o, doi_r, url_r, outcome))
    return cur.fetchone()["record_id"]
