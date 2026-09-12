"""
sync_csv.py — Nightly sync of extracted.csv from the flora-extractor GitHub repo.

Downloads the latest extracted.csv, creates an immutable UTC/run-ID archive, imports a staged
candidate, then atomically promotes it to extracted_latest.csv on success. The
archive and its sha256 are reported to the caller: they, not the mutable
extracted_latest.csv, are what the orphan report and cleanup stages are bound to.

Run as the first stage of the APScheduler extractor-maintenance job (see
``extractor_maintenance.py`` and ``app.py``). The command-line entry point
routes standalone requests through that audited orchestrator as well:
    python sync_csv.py

The standalone command exits non-zero when download/import fails so the
extractor maintenance pipeline can stop before orphan reporting or deletion.
"""
import argparse
import json
import math
import os
import re
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import requests
from dotenv import load_dotenv
from console_encoding import use_utf8_output

from csv_to_db import run_import
from extractor_storage import matches, resolve_data_dir, sha256_bytes, sha256_file
from extractor_vocab import check_csv_vocabulary, resolved_mask

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

_DEFAULT_DATA_DIR = resolve_data_dir()

_GITHUB_REPO = os.environ.get("GITHUB_REPO", "forrtproject/flora-extractor")
_GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
_CSV_FILE_PATH = "data/extracted.csv"
_ROUTING_RELEASE_ID = os.environ.get("ROUTING_RELEASE_ID", "")
_MAX_REMOVAL_PERCENT_ENV = "EXTRACTOR_MAX_REMOVAL_PERCENT"
_DEFAULT_MAX_REMOVAL_PERCENT = 10.0
_REPORT_ID_SAMPLE_LIMIT = 100


class RemovalPercentConfigurationError(ValueError):
    """The configured resolved-record removal threshold is unsafe."""


def parse_max_removal_percent(value: object | None = None) -> float:
    """Return a finite removal percentage in the inclusive range 0..100.

    When *value* is omitted, read ``EXTRACTOR_MAX_REMOVAL_PERCENT`` at call
    time. Keeping environment access out of module initialization makes this
    parser directly testable and lets a sync emit a structured, fail-closed
    configuration error instead of crashing while importing the module.
    """
    configured = (
        os.environ.get(
            _MAX_REMOVAL_PERCENT_ENV,
            str(_DEFAULT_MAX_REMOVAL_PERCENT),
        )
        if value is None
        else value
    )
    error = (
        f"{_MAX_REMOVAL_PERCENT_ENV} must be a finite number from 0 through "
        f"100; got {configured!r}"
    )
    if isinstance(configured, bool):
        raise RemovalPercentConfigurationError(error)
    try:
        percentage = float(configured)
    except (TypeError, ValueError) as exc:
        raise RemovalPercentConfigurationError(error) from exc
    if not math.isfinite(percentage) or not 0.0 <= percentage <= 100.0:
        raise RemovalPercentConfigurationError(error)
    return percentage


@dataclass(frozen=True)
class SnapshotComparison:
    previous_resolved_count: int | None
    candidate_resolved_count: int
    added_pair_ids: list[str]
    removed_pair_ids: list[str]
    removed_percent: float

    def as_report(self) -> dict:
        return {
            "previous_resolved_count": self.previous_resolved_count,
            "candidate_resolved_count": self.candidate_resolved_count,
            "added_count": len(self.added_pair_ids),
            "removed_count": len(self.removed_pair_ids),
            "removed_percent": round(self.removed_percent, 4),
            "added_pair_ids": self.added_pair_ids[:_REPORT_ID_SAMPLE_LIMIT],
            "removed_pair_ids": self.removed_pair_ids[:_REPORT_ID_SAMPLE_LIMIT],
            "added_ids_truncated": len(self.added_pair_ids) > _REPORT_ID_SAMPLE_LIMIT,
            "removed_ids_truncated": len(self.removed_pair_ids) > _REPORT_ID_SAMPLE_LIMIT,
        }


class SnapshotSafetyError(RuntimeError):
    """Candidate is readable but unsafe to promote as the cleanup baseline."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _build_url(repo: str, branch: str, file_path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"


def _fetch_csv(url: str) -> bytes:
    """Download CSV from URL. Raises RuntimeError on non-200 status."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Authorization": f"token {token}"} if token else {}
    response = requests.get(url, headers=headers, timeout=60)
    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub returned {response.status_code} for {url}: {response.text[:200]}"
        )
    return response.content


def _archive_token(maintenance_run_id: str | None) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "", maintenance_run_id or "")
    return normalized[:8] or uuid4().hex[:8]


def _save_csv(
    content: bytes,
    data_dir: Path,
    maintenance_run_id: str | None = None,
) -> tuple[Path, Path]:
    """Immutably archive the download and return candidate/archive paths.

    The caller promotes this path only after run_import succeeds, so malformed
    input cannot replace the last known-good extracted_latest.csv.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    archive_stem = (
        f"extracted_{now.strftime('%Y%m%dT%H%M%SZ')}_"
        f"{_archive_token(maintenance_run_id)}"
    )
    collision = 1
    while True:
        suffix = "" if collision == 1 else f"_{collision}"
        archive_path = data_dir / f"{archive_stem}{suffix}.csv"
        try:
            # Exclusive creation is the final overwrite guard. The timestamp/run
            # token should already be unique, but a retried run in the same second
            # receives _2, _3, ... instead of replacing historical evidence.
            with archive_path.open("xb") as archive:
                archive.write(content)
            break
        except FileExistsError:
            collision += 1

    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=".extracted_candidate_", suffix=".csv",
        dir=data_dir, delete=False,
    ) as staged:
        staged.write(content)
        candidate_path = Path(staged.name)

    return candidate_path, archive_path


def _promote_csv(candidate_path: Path, data_dir: Path) -> Path:
    """Atomically replace extracted_latest.csv with an imported candidate."""
    latest_path = data_dir / "extracted_latest.csv"
    os.replace(candidate_path, latest_path)
    return latest_path


def _verify_promoted_csv(latest_path: Path, expected_content: bytes) -> None:
    """Verify promotion directly, without relying on a separately stored hash."""
    if latest_path.read_bytes() != expected_content:
        raise RuntimeError(
            "promoted extracted_latest.csv does not match the downloaded candidate"
        )


def _verify_archive(archive_path: Path, content: bytes) -> str:
    """Return the archive digest after reading it back off durable storage.

    Downstream stages are bound to this digest, so it has to describe what
    actually landed on disk — hashing the in-memory download would still
    "verify" a truncated or silently failed write.
    """
    expected = sha256_bytes(content)
    actual = sha256_file(archive_path)
    if actual != expected:
        raise RuntimeError(
            f"archived snapshot {archive_path} does not match the download "
            f"(expected sha256={expected}, found {actual})"
        )
    return actual


def _resolve_baseline(
    data_dir: Path,
    baseline_file: str | None,
    baseline_sha256: str | None,
    require_baseline: bool,
) -> Path | None:
    """Return the snapshot the removal guard must compare the candidate against.

    extracted_latest.csv is pod-local and mutable, so it is trusted only while
    it still carries the bytes of the last verified sync; the run's own archive
    is preferred because it is immutable. Anything else — an empty volume, an
    older bundled copy — is a LOST baseline, never a first deployment: silently
    comparing against nothing switches the removal guard off at exactly the
    moment the database is most exposed.
    """
    latest_path = data_dir / "extracted_latest.csv"
    if baseline_sha256:
        archive_path = data_dir / Path(baseline_file).name if baseline_file else None
        for candidate in (archive_path, latest_path):
            if matches(candidate, baseline_sha256):
                return candidate
        raise SnapshotSafetyError(
            "baseline_snapshot_unavailable",
            "the archived snapshot of the last verified sync is not readable on "
            f"this host (expected {baseline_file or '<unrecorded file>'} "
            f"sha256={baseline_sha256}); refusing to re-baseline the removal guard",
            {
                "expected_baseline_file": baseline_file,
                "expected_baseline_sha256": baseline_sha256,
            },
        )
    if require_baseline:
        if latest_path.is_file() and latest_path.stat().st_size:
            return latest_path
        raise SnapshotSafetyError(
            "missing_local_baseline",
            "the database already holds imported records but this host has no "
            "readable extracted_latest.csv baseline; a first-deployment "
            "comparison would disable the removal guard",
            {"expected_baseline_file": None, "expected_baseline_sha256": None},
        )
    return latest_path if latest_path.is_file() else None


def _resolved_pair_ids(csv_path: Path) -> set[str]:
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
    check_csv_vocabulary(df)
    if "pair_id" not in df.columns:
        raise SnapshotSafetyError(
            "extractor_pipeline_error",
            "CSV has no pair_id column; snapshot comparison is impossible",
        )
    resolved = df[resolved_mask(df)]
    return {str(value).strip() for value in resolved["pair_id"] if str(value).strip()}


def _compare_snapshots(
    candidate_path: Path,
    previous_path: Path | None,
    *,
    max_removal_percent: object | None = None,
) -> SnapshotComparison:
    removal_limit = parse_max_removal_percent(max_removal_percent)
    # ``None`` reaches here only for a genuine first deployment; a baseline that
    # is merely missing from this pod is rejected in _resolve_baseline.
    has_previous = previous_path is not None and previous_path.exists()
    previous_ids = _resolved_pair_ids(previous_path) if has_previous else set()
    try:
        candidate_ids = _resolved_pair_ids(candidate_path)
    except pd.errors.EmptyDataError:
        comparison = SnapshotComparison(
            len(previous_ids) if has_previous else None,
            0,
            [],
            sorted(previous_ids),
            100.0 if previous_ids else 0.0,
        )
        raise SnapshotSafetyError(
            "empty_resolved_snapshot",
            "candidate CSV is empty; treating this as an extractor pipeline error",
            comparison.as_report(),
        )
    # A first deployment has no baseline, so every row is not presented as a
    # "new ID" warning. Additions become meaningful after the first promotion.
    added = sorted(candidate_ids - previous_ids) if has_previous else []
    removed = sorted(previous_ids - candidate_ids)
    removed_percent = (len(removed) / len(previous_ids) * 100.0) if previous_ids else 0.0
    comparison = SnapshotComparison(
        len(previous_ids) if has_previous else None,
        len(candidate_ids),
        added,
        removed,
        removed_percent,
    )
    if not candidate_ids:
        raise SnapshotSafetyError(
            "empty_resolved_snapshot",
            "candidate contains zero resolved pair_ids; treating this as an extractor pipeline error",
            comparison.as_report(),
        )
    if removed_percent > removal_limit:
        raise SnapshotSafetyError(
            "excessive_resolved_removal",
            f"resolved pair_id removal is {removed_percent:.2f}% "
            f"({len(removed)} of {len(previous_ids)}), above the "
            f"{removal_limit:.2f}% limit",
            comparison.as_report(),
        )
    return comparison


def _write_sync_report(report_path: Path | None, report: dict) -> None:
    if report_path is None:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def sync_once(
    data_dir: Path = _DEFAULT_DATA_DIR,
    report_path: Path | None = None,
    maintenance_run_id: str | None = None,
    baseline_file: str | None = None,
    baseline_sha256: str | None = None,
    require_baseline: bool = False,
) -> bool:
    """Download, archive, import, and promote one extractor snapshot.

    Returns ``True`` only after import and atomic promotion both succeed. The
    function keeps logging failures instead of raising because it is also used
    as a background job; callers that need fail-fast behavior can inspect the
    return value (the command-line entry point converts it to an exit code).
    """
    url = _build_url(_GITHUB_REPO, _GITHUB_BRANCH, _CSV_FILE_PATH)
    candidate_path = None
    report = {
        "success": False,
        "status": "error",
        "warning_codes": [],
        "error_code": None,
        "message": None,
        "maintenance_run_id": maintenance_run_id,
        "archive_file": None,
        # Identity of the immutable snapshot Parts 2 and 3 must read. Without
        # it the run history proves only that *some* CSV was imported.
        "archive_sha256": None,
        "archive_bytes": None,
        "archive_verified": False,
        "baseline_file": None,
        "baseline_sha256": None,
        "expected_baseline_file": baseline_file,
        "expected_baseline_sha256": baseline_sha256,
        "baseline_required": require_baseline,
        "download_completed": False,
        "validation_completed": False,
        "import_completed": False,
        "promotion_completed": False,
        "promotion_verified": False,
        "part1_completed": False,
        "max_removal_percent": None,
    }
    try:
        max_removal_percent = parse_max_removal_percent()
        report["max_removal_percent"] = max_removal_percent
        print(f"[sync_csv] Fetching {url} …")
        content = _fetch_csv(url)
        report["download_completed"] = True
        candidate_path, archive_path = _save_csv(
            content,
            data_dir,
            maintenance_run_id=maintenance_run_id,
        )
        report["archive_file"] = archive_path.name
        report["archive_bytes"] = len(content)
        report["archive_sha256"] = _verify_archive(archive_path, content)
        report["archive_verified"] = True
        print(
            f"[sync_csv] Archived {len(content)} bytes → {archive_path} "
            f"(sha256={report['archive_sha256']}); staged candidate → {candidate_path}"
        )
        baseline_path = _resolve_baseline(
            data_dir,
            baseline_file,
            baseline_sha256,
            require_baseline,
        )
        if baseline_path is not None:
            report["baseline_file"] = baseline_path.name
            report["baseline_sha256"] = baseline_sha256 or sha256_file(baseline_path)
        comparison = _compare_snapshots(
            candidate_path,
            baseline_path,
            max_removal_percent=max_removal_percent,
        )
        report["validation_completed"] = True
        report.update(comparison.as_report())
        previous_label = (
            str(comparison.previous_resolved_count)
            if comparison.previous_resolved_count is not None
            else "none"
        )
        print(
            "[sync_csv] Snapshot safety: "
            f"previous={previous_label} "
            f"candidate={comparison.candidate_resolved_count} "
            f"added={len(comparison.added_pair_ids)} "
            f"removed={len(comparison.removed_pair_ids)} "
            f"removed_percent={comparison.removed_percent:.2f}%"
        )
        if comparison.added_pair_ids:
            report["warning_codes"].append("new_resolved_pair_ids")
            sample = ", ".join(comparison.added_pair_ids[:10])
            print(
                f"[sync_csv] WARNING new_resolved_pair_ids: "
                f"{len(comparison.added_pair_ids)} new resolved pair_id(s); sample: {sample}"
            )
        run_import(candidate_path, release_id=_ROUTING_RELEASE_ID)
        report["import_completed"] = True
        latest_path = _promote_csv(candidate_path, data_dir)
        candidate_path = None  # os.replace moved it to latest_path
        report["promotion_completed"] = True
        _verify_promoted_csv(latest_path, content)
        report["promotion_verified"] = True
        report["part1_completed"] = True
        print(f"[sync_csv] Import complete; promotion verified → {latest_path}")
        report["success"] = True
        report["status"] = "warning" if report["warning_codes"] else "success"
        report["message"] = "candidate imported and promoted"
        return True
    except RemovalPercentConfigurationError as exc:
        report["status"] = "error"
        report["error_code"] = "invalid_removal_percent_configuration"
        report["message"] = str(exc)
        print(f"[sync_csv] CONFIGURATION ERROR: {exc}")
        return False
    except SnapshotSafetyError as exc:
        report.update(exc.details)
        report["status"] = "error" if exc.code == "empty_resolved_snapshot" else "blocked"
        if exc.code in {"baseline_snapshot_unavailable", "missing_local_baseline"}:
            report["validation_completed"] = False
        report["error_code"] = exc.code
        report["message"] = str(exc)
        print(f"[sync_csv] BLOCKED {exc.code}: {exc}")
        return False
    except Exception:
        report["error_code"] = "extractor_pipeline_error"
        report["message"] = "download, validation, import, or promotion failed"
        print("[sync_csv] ERROR during sync:")
        traceback.print_exc()
        return False
    finally:
        _write_sync_report(report_path, report)
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download, import, and atomically promote the extractor CSV"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_DEFAULT_DATA_DIR,
        help="Directory containing extracted_latest.csv and timestamped archives",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="Optional path for a structured sync safety report",
    )
    parser.add_argument(
        "--maintenance-run-id",
        default=None,
        help=argparse.SUPPRESS,
    )
    # The orchestrator owns the run history, so it — not this process — knows
    # which snapshot the last verified sync promoted and whether the database
    # already holds imported records.
    parser.add_argument("--baseline-file", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--baseline-sha256", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--require-baseline",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.maintenance_run_id:
        succeeded = sync_once(
            data_dir=args.data_dir,
            report_path=args.report_json,
            maintenance_run_id=args.maintenance_run_id,
            baseline_file=args.baseline_file,
            baseline_sha256=args.baseline_sha256,
            require_baseline=args.require_baseline,
        )
    else:
        # A direct command must still create durable history and hold the same
        # process lock as scheduled/admin runs. This prevents an untracked
        # import-committed/promotion-failed state from bypassing later gates.
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            parser.error("DATABASE_URL is required for audited CSV synchronization")
        from extractor_maintenance import run_pipeline

        succeeded = run_pipeline(
            data_dir=args.data_dir,
            requested_stage="sync",
            trigger="cli",
            database_url=database_url,
        )
    raise SystemExit(0 if succeeded else 1)
