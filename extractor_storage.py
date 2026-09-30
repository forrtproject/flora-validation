"""Durable location and exact identity of the extractor CSV snapshots.

Part 1 of the maintenance pipeline imports ONE immutable CSV archive; the orphan
report and the retire stage must then reason about exactly those bytes. The
pod-local, mutable ``data/extracted_latest.csv`` is not a safe stand-in for it:
in Kubernetes a replacement pod can carry an older bundled copy while the
PostgreSQL run history still reports the newer run as complete, so a run-ID gate
alone would let a stage act on a snapshot it never read.

Three rules follow, and all live here so no stage can drift from them:

  * the archive is written to a working directory (``EXTRACTOR_DATA_DIR``),
  * every snapshot the sync imports is also kept in the database
    (``extractor_snapshots``), because a redeploy can wipe that directory and
    the removal guard needs the last import's exact bytes, and
  * every stage addresses a snapshot by content digest, never by filename.
"""

from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_CHUNK_BYTES = 1024 * 1024


class SnapshotIntegrityError(RuntimeError):
    """A file does not carry the exact snapshot bytes a stage was bound to."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def resolve_data_dir() -> Path:
    """Return the working directory for the snapshot archives.

    It need not survive a redeploy: a missing snapshot is restored from the
    database copy (``restore_snapshot``) and verified by digest before use.
    """
    configured = os.environ.get("EXTRACTOR_DATA_DIR", "").strip()
    if not configured:
        return ROOT / "data"
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matches(path: Path | None, expected_sha256: str | None) -> bool:
    """Report whether ``path`` currently holds the expected snapshot bytes."""
    if path is None or not expected_sha256:
        return False
    path = Path(path)
    if not path.is_file():
        return False
    return sha256_file(path) == expected_sha256


def require_snapshot(path: Path, expected_sha256: str | None, *, stage: str) -> str:
    """Return the digest of ``path``, proving it is the bound snapshot first.

    Raises rather than returning a verdict: every caller of this function is
    about to read a delete list out of the file, so an unverified snapshot has
    no safe fallback behaviour.
    """
    path = Path(path)
    if not expected_sha256:
        raise SnapshotIntegrityError(
            "snapshot_digest_missing",
            f"{stage} requires the sha256 of the archived Part 1 snapshot",
            {"stage": stage, "path": str(path)},
        )
    if not path.is_file():
        raise SnapshotIntegrityError(
            "snapshot_archive_unavailable",
            f"{stage} snapshot {path} is not present on this host",
            {"stage": stage, "path": str(path), "expected_sha256": expected_sha256},
        )
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise SnapshotIntegrityError(
            "snapshot_archive_mismatch",
            f"{stage} snapshot {path} does not match the archived Part 1 "
            "snapshot for this run",
            {
                "stage": stage,
                "path": str(path),
                "expected_sha256": expected_sha256,
                "actual_sha256": actual,
            },
        )
    return actual


# ---------------------------------------------------------------------------
# The database copy: extractor_snapshots (db_schema.sql)
# ---------------------------------------------------------------------------
# A redeploy that replaces the container empties EXTRACTOR_DATA_DIR; on Railway
# that blocked every nightly sync from 2026-09-13 to 2026-09-30, because the
# removal guard could no longer read the snapshot it had to compare against.
# The database outlives the container and moves with the data to a new server,
# so every imported snapshot is kept there as well, gzip-compressed and keyed by
# its sha256. Both directions verify the digest, so neither copy is trusted
# blindly.

def _run_uuid(run_id: str | None) -> str | None:
    import uuid

    try:
        return str(uuid.UUID(str(run_id))) if run_id else None
    except ValueError:
        return None


def store_snapshot(
    database_url: str,
    path: Path,
    expected_sha256: str,
    *,
    source_commit: str | None = None,
    run_id: str | None = None,
) -> bool:
    """Keep the snapshot at *path* in the database; True when newly stored.

    Idempotent: a digest that is already stored is left as it is, so the nightly
    run of an unchanged CSV adds nothing.
    """
    import psycopg2

    content = Path(path).read_bytes()
    actual = sha256_bytes(content)
    if actual != expected_sha256:
        raise SnapshotIntegrityError(
            "snapshot_archive_mismatch",
            f"{path} changed before it could be stored in the database",
            {"path": str(path), "expected_sha256": expected_sha256,
             "actual_sha256": actual},
        )
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO extractor_snapshots
                    (sha256, archive_file, source_commit, byte_size, content_gzip,
                     first_run_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (sha256) DO NOTHING
                """,
                (
                    expected_sha256,
                    Path(path).name,
                    source_commit,
                    len(content),
                    psycopg2.Binary(gzip.compress(content, mtime=0)),
                    _run_uuid(run_id),
                ),
            )
            stored = cur.rowcount == 1
            if not stored:
                # Imported again: it is recent again, whatever pruning goes by.
                cur.execute(
                    "UPDATE extractor_snapshots SET stored_at = NOW() WHERE sha256 = %s",
                    (expected_sha256,),
                )
        conn.commit()
    finally:
        conn.close()
    return stored


def restore_snapshot(database_url: str, expected_sha256: str, dest: Path) -> bool:
    """Write the stored snapshot with this digest to *dest*.

    Returns False when the database holds no such snapshot. Never replaces an
    existing file: a file that is present but wrong is evidence, and the caller's
    digest check reports it as a mismatch instead.
    """
    import psycopg2

    dest = Path(dest)
    if dest.exists():
        return matches(dest, expected_sha256)
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_gzip FROM extractor_snapshots WHERE sha256 = %s",
                (expected_sha256,),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row:
        return False
    content = gzip.decompress(bytes(row[0]))
    actual = sha256_bytes(content)
    if actual != expected_sha256:
        raise SnapshotIntegrityError(
            "snapshot_archive_mismatch",
            f"the database copy of snapshot {expected_sha256} does not hash to it",
            {"expected_sha256": expected_sha256, "actual_sha256": actual},
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = dest.with_name(f".{dest.name}.restoring")
    staged.write_bytes(content)
    os.replace(staged, dest)
    return matches(dest, expected_sha256)


DEFAULT_SNAPSHOTS_KEPT = 10


def snapshots_kept() -> int:
    """EXTRACTOR_SNAPSHOTS_KEPT: how many snapshots the database holds (min 2).

    A snapshot is ~4 MB compressed. Only the newest one is needed (it is the next
    run's baseline); the rest are history, and every older one is still a commit
    in flora-extractor's git history (``source_commit``).
    """
    try:
        configured = int(os.environ.get("EXTRACTOR_SNAPSHOTS_KEPT",
                                        str(DEFAULT_SNAPSHOTS_KEPT)))
    except ValueError:
        configured = DEFAULT_SNAPSHOTS_KEPT
    return max(2, configured)


def prune_snapshots(database_url: str, keep: int, protect: tuple = ()) -> int:
    """Delete all but the newest *keep* stored snapshots; returns how many went.

    Digests in *protect* (this run's snapshot and baseline) are never deleted.
    """
    import psycopg2

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM extractor_snapshots
                WHERE sha256 <> ALL(%s)
                  AND sha256 NOT IN (
                      SELECT sha256 FROM extractor_snapshots
                      ORDER BY stored_at DESC
                      LIMIT %s
                  )
                """,
                (list(protect), max(2, int(keep))),
            )
            removed = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return removed
