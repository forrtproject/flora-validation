"""Opt-in PostgreSQL integration tests; create and remove our own test database.

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database.
No production DSN or application .env value is used as a fallback.
"""
import csv
import hashlib
import io
import json
import os
import uuid
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.extras import RealDictCursor
import pytest

import final_export
import flora_registry
import flora_service
import flora_store
import prepare_flora
import source_sync_runner
import transform_sources


from tests.pg_fixtures import add_source, local_database  # noqa: F401


def test_database_migration_build_correction_append_and_download(local_database, tmp_path, monkeypatch):
    connection = local_database
    # Every metadata lookup must be satisfied by the supplied local caches.
    monkeypatch.setattr("bibliographic_helpers.request", lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    with final_export.REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        reference = list(csv.DictReader(handle))
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            add_source(cur, reference[2], "REPL-000001")
            original_sid = add_source(cur, reference[0], "REPL-000002")
            flora_registry.refresh(cur, verbose=False)
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        frame = transform_sources.to_output_shape(flora_registry.attach_ids(cur, transform_sources.build(cur, verbose=False)))
        assert frame["id"].tolist() == ["FLORA-000001", "FLORA-000003"]
        assert frame["title_o"].notna().all() and frame["title_r"].notna().all()
        assert frame["source"].tolist() == ["replications", "replications"]
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("UPDATE source_records SET doi_o='10.9999/corrected-original' WHERE record_id=%s", (original_sid,))
            # A new display ID sorts earlier; publication order must still append.
            fresh = dict(reference[1], doi_o="10.9999/new-original", doi_r="10.9999/new-replication")
            add_source(cur, fresh, "REPL-000000")
            flora_registry.refresh(cur, verbose=False)
    report = prepare_flora.prepare(tmp_path, network_checks="none")
    assert report["status"] in {"success", "needs_attention"}
    final = pd.read_csv(tmp_path / "flora.csv", encoding="utf-8-sig", dtype=str)
    assert final["id"].tolist() == ["FLORA-000001", "FLORA-000003", "REPL-000000"]
    assert final["id_md5"].tolist() == [hashlib.md5(identifier.encode()).hexdigest() for identifier in final["id"]]
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        web_csv = flora_service.export_csv(cur, {})
        assert web_csv.encode("utf-8") == (tmp_path / "flora.csv").read_bytes()
        assert flora_store.export_csv(cur) == web_csv
        # The data version a racing commit compares: this store, as flora_data has it.
        manifest = json.loads((tmp_path / "flora_release_manifest.json").read_text(encoding="utf-8"))
        _, stored_at, watermark = flora_store.dataset_revision(cur)
        assert manifest["data_version"] == {"stored_at": stored_at.isoformat(),
                                            "change_watermark": watermark}
        assert report["release"]["data_version"] == manifest["data_version"]
        job_id = source_sync_runner.queue_run(cur, "integration-test")
    connection.commit()
    source_sync_runner._persist_artifacts(connection, job_id, report, web_csv)
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        assert source_sync_runner.job_artifact(cur, job_id, "flora.csv") == web_csv
        saved_report = source_sync_runner.job_artifact(cur, job_id, "report.json")
        assert saved_report["release"]["sha256"] == hashlib.sha256(web_csv.encode("utf-8")).hexdigest()
        assert source_sync_runner.job_detail(cur, job_id)["has_report"] is True
