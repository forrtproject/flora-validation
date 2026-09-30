"""The database copy of each imported extractor CSV, and the orphan summary.

A Railway redeploy emptied EXTRACTOR_DATA_DIR and every nightly sync blocked
from 2026-09-13 to 2026-09-30, because the removal guard could no longer read
the last import's CSV. extractor_snapshots keeps it in the database instead.
"""
import gzip
import hashlib

import pytest

import extractor_storage
from extractor_storage import (
    SnapshotIntegrityError,
    prune_snapshots,
    restore_snapshot,
    snapshots_kept,
    store_snapshot,
)
from find_orphans import summarise

CONTENT = b"pair_id,paper_type,link_method,doi_r\nabc,replication,llm_references,10.1/x\n"
SHA256 = hashlib.sha256(CONTENT).hexdigest()


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rowcount = 0
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.db.statements.append((" ".join(sql.split()), params))
        if sql.lstrip().startswith("INSERT"):
            sha256 = params[0]
            self.rowcount = 0 if sha256 in self.db.rows else 1
            # psycopg2.Binary wraps the bytes it will send.
            self.db.rows.setdefault(sha256, bytes(getattr(params[4], "adapted", params[4])))
        elif sql.lstrip().startswith("SELECT"):
            stored = self.db.rows.get(params[0])
            self._row = (memoryview(stored),) if stored is not None else None
        elif sql.lstrip().startswith("DELETE"):
            self.rowcount = self.db.pruned

    def fetchone(self):
        return self._row


class FakeDatabase:
    def __init__(self):
        self.rows = {}
        self.statements = []
        self.commits = 0
        self.pruned = 0

    def connect(self, _url):
        db = self

        class Connection:
            def cursor(self):
                return FakeCursor(db)

            def commit(self):
                db.commits += 1

            def close(self):
                pass

        return Connection()


@pytest.fixture
def database(monkeypatch):
    import psycopg2

    db = FakeDatabase()
    monkeypatch.setattr(psycopg2, "connect", db.connect)
    return db


def test_a_snapshot_is_stored_compressed_once(tmp_path, database):
    path = tmp_path / "extracted_20260930T191256Z_cd9302ef.csv"
    path.write_bytes(CONTENT)
    run_id = "cd9302ef-2130-4844-b344-2909c3f1db4a"

    assert store_snapshot("postgresql://x", path, SHA256, source_commit="d7d239d", run_id=run_id)
    assert gzip.decompress(database.rows[SHA256]) == CONTENT
    _sql, params = database.statements[0]
    assert params[:4] == (SHA256, path.name, "d7d239d", len(CONTENT))
    assert params[5] == run_id
    # The nightly run of an unchanged CSV adds nothing, but counts as recent.
    assert store_snapshot("postgresql://x", path, SHA256) is False
    assert len(database.rows) == 1
    assert database.statements[-1] == (
        "UPDATE extractor_snapshots SET stored_at = NOW() WHERE sha256 = %s", (SHA256,))


def test_a_run_id_that_is_not_a_uuid_is_not_stored_as_one(tmp_path, database):
    path = tmp_path / "extracted.csv"
    path.write_bytes(CONTENT)
    store_snapshot("postgresql://x", path, SHA256, run_id="run-1")
    assert database.statements[0][1][5] is None


def test_a_file_that_changed_is_never_stored(tmp_path, database):
    path = tmp_path / "extracted.csv"
    path.write_bytes(b"something else")
    with pytest.raises(SnapshotIntegrityError) as raised:
        store_snapshot("postgresql://x", path, SHA256)
    assert raised.value.code == "snapshot_archive_mismatch"
    assert database.rows == {}


def test_a_stored_snapshot_is_restored_byte_for_byte(tmp_path, database):
    database.rows[SHA256] = gzip.compress(CONTENT)
    dest = tmp_path / "data" / "extracted_20260912T142657Z_88d4c0c8.csv"

    assert restore_snapshot("postgresql://x", SHA256, dest) is True
    assert dest.read_bytes() == CONTENT
    assert not list(dest.parent.glob(".*.restoring"))


def test_restore_reports_a_snapshot_the_database_does_not_hold(tmp_path, database):
    dest = tmp_path / "extracted.csv"
    assert restore_snapshot("postgresql://x", SHA256, dest) is False
    assert not dest.exists()


def test_a_corrupted_database_copy_is_refused(tmp_path, database):
    database.rows[SHA256] = gzip.compress(b"not the snapshot")
    dest = tmp_path / "extracted.csv"
    with pytest.raises(SnapshotIntegrityError):
        restore_snapshot("postgresql://x", SHA256, dest)
    assert not dest.exists()


def test_restore_never_overwrites_a_file_that_is_already_there(tmp_path, database):
    database.rows[SHA256] = gzip.compress(CONTENT)
    dest = tmp_path / "extracted.csv"
    dest.write_bytes(b"evidence of something wrong")
    assert restore_snapshot("postgresql://x", SHA256, dest) is False
    assert dest.read_bytes() == b"evidence of something wrong"
    assert database.statements == []


def test_pruning_keeps_the_newest_and_the_protected_snapshots(database):
    database.pruned = 3
    assert prune_snapshots("postgresql://x", 10, ("a" * 64,)) == 3
    sql, params = database.statements[0]
    assert "ORDER BY stored_at DESC LIMIT %s" in sql
    assert params == (["a" * 64], 10)
    prune_snapshots("postgresql://x", 0)
    assert database.statements[1][1][1] == 2, "never fewer than two"


@pytest.mark.parametrize(("value", "expected"), [
    (None, 10), ("25", 25), ("1", 2), ("lots", 10),
])
def test_how_many_snapshots_are_kept(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("EXTRACTOR_SNAPSHOTS_KEPT", raising=False)
    else:
        monkeypatch.setenv("EXTRACTOR_SNAPSHOTS_KEPT", value)
    assert snapshots_kept() == expected
    assert extractor_storage.DEFAULT_SNAPSHOTS_KEPT == 10


def test_the_orphan_summary_counts_instead_of_listing_everything():
    rows = [
        ("r1", "shipped", "10.1/a", "10.1/o", "unvalidated"),
        ("r2", "gone-1", "10.1/b", "10.1/p", "validated"),
        ("r3", "gone-2", "10.1/c", "10.1/q", "unvalidated"),
        ("r4", "gone-3", "10.1/d", "10.1/r", "need_review"),
        ("r5", None, "10.1/e", "10.1/s", "validated"),
    ]
    summary = summarise(rows, {"shipped"})
    assert summary["records_in_database"] == 5
    assert summary["orphan_count"] == 4
    assert summary["orphans_by_status"] == {"validated": 2, "unvalidated": 1, "need_review": 1}
    assert summary["unvalidated_count"] == 1
    assert summary["other_status_count"] == 3
    assert summary["unvalidated_sample"] == [
        {"record_id": "r3", "doi_r": "10.1/c", "doi_o": "10.1/q"},
    ]


def test_the_orphan_summary_lists_at_most_fifty_unvalidated():
    rows = [(f"r{i}", f"gone-{i}", f"10.1/{i:03d}", "", "unvalidated") for i in range(80)]
    summary = summarise(rows, set())
    assert summary["unvalidated_count"] == 80
    assert len(summary["unvalidated_sample"]) == 50
