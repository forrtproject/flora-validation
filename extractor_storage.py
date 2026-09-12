"""Durable location and exact identity of the extractor CSV snapshots.

Part 1 of the maintenance pipeline imports ONE immutable CSV archive; Parts 2
and 3 must then reason about exactly those bytes. The pod-local, mutable
``data/extracted_latest.csv`` is not a safe stand-in for it: in Kubernetes a
replacement pod can carry an older bundled copy while the PostgreSQL run
history still reports the newer run as complete, so a run-ID gate alone would
let cleanup delete records that were imported from a snapshot it never read.

Two rules follow, and both live here so no stage can drift from them:

  * the archive lives in a data directory that deployments are expected to back
    with shared durable storage (``EXTRACTOR_DATA_DIR``), and
  * every stage addresses that archive by content digest, never by filename.
"""

from __future__ import annotations

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
    """Return the directory holding the snapshot archives.

    Deployments that replace pods must point ``EXTRACTOR_DATA_DIR`` at shared
    durable storage; otherwise each pod archives into its own ephemeral
    filesystem and downstream stages correctly refuse to run.
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
            f"{stage} snapshot {path} is not present on this host; the Part 1 "
            "archive must be on shared durable storage",
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
