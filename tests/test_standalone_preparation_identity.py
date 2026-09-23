"""Standalone preparation pins identity before any record reaches the final CSV."""
import csv
import hashlib
import os

import pandas as pd
from psycopg2.extensions import make_dsn
from psycopg2.extras import RealDictCursor
import pytest

import final_export
import flora_registry
import prepare_flora
import transform_sources
from tests.test_preparation_database import add_source, local_database  # noqa: F401


def add_unregistered_source(connection, *, reference=False):
    with final_export.REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    if not reference:
        row = dict(row, doi_o="10.9999/standalone-original", doi_r="10.9999/standalone-repeat")
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            source_id = add_source(cur, row, "REPL-700001")
            for side, title in [("o", "An original study"), ("r", "A replication study")]:
                cur.execute("""INSERT INTO work_metadata (doi, title, authors, year)
                               VALUES (%s, %s, 'Smith, A.', '2020')""",
                            (row[f"doi_{side}"], title))
    return source_id


@pytest.mark.parametrize("reference,expected_id", [(False, "REPL-700001"), (True, "FLORA-000001")])
def test_standalone_preparation_pins_unregistered_identity_before_storing(
        local_database, tmp_path, monkeypatch, reference, expected_id):
    source_id = add_unregistered_source(local_database, reference=reference)
    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    build = transform_sources.build
    builds = []

    def counted_build(*args, **kwargs):
        builds.append(1)
        return build(*args, **kwargs)

    monkeypatch.setattr(transform_sources, "build", counted_build)
    report = prepare_flora.prepare(tmp_path, network_checks="none")
    assert report["status"] in {"success", "needs_attention"}
    assert len(builds) == 1, "Preparation must register its exact frame without rebuilding"
    frame = pd.read_csv(tmp_path / "flora.csv", dtype=str, encoding="utf-8-sig")
    assert frame["id"].tolist() == [expected_id]
    expected_hash = hashlib.md5(expected_id.encode("utf-8")).hexdigest()
    assert frame["id_md5"].tolist() == [expected_hash]

    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            # A subsequent correction and scheduled refresh must preserve the
            # first publication identity rather than replace a provisional ID.
            cur.execute("UPDATE source_records SET doi_o='10.9999/corrected' WHERE record_id=%s",
                        (source_id,))
            flora_registry.refresh(cur, verbose=False)
            cur.execute("SELECT export_id FROM flora_records")
            assert cur.fetchone()["export_id"] == expected_id
            cur.execute("SELECT id, id_md5 FROM flora_data")
            assert dict(cur.fetchone()) == {"id": expected_id, "id_md5": expected_hash}


def test_stats_only_does_not_register_records_or_write_files(local_database, tmp_path, monkeypatch):
    add_unregistered_source(local_database)
    monkeypatch.setenv("DATABASE_URL", make_dsn(os.environ["DATABASE_URL"],
                                               options="-c default_transaction_read_only=on"))
    output = tmp_path / "preview.csv"
    transform_sources.run(output, stats_only=True)
    assert not output.exists()
    with local_database.cursor() as cur:
        for table in ("flora_records", "flora_data", "flora_dataset_history"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            assert cur.fetchone()[0] == 0


def test_registry_assignment_failure_rolls_back_before_export(local_database, tmp_path, monkeypatch):
    add_unregistered_source(local_database)

    def fail_assignment(*args, **kwargs):
        raise ValueError("Publication assignment failed")

    monkeypatch.setattr(final_export, "register_order", fail_assignment)
    output = tmp_path / "flora.csv"
    with pytest.raises(ValueError, match="Publication assignment failed"):
        transform_sources.run(output)
    assert not output.exists()
    with local_database.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM flora_records")
        assert cur.fetchone()[0] == 0
