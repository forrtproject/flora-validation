"""
cleanup_orphans.py — Delete stale 'unvalidated' rows that are no longer in the
current extracted CSV.

The importer (csv_to_db.py) is append-only, so the DB accumulates rows that were
resolved in a PAST snapshot but have since dropped out of the current CSV. This
script removes those orphans — but ONLY when they are safe to remove:

  RETENTION RULE: an orphan is retained when ANY of these is true
    - an admin excluded it (validation_status = 'rejected'),
    - at least one validator submitted a judgement,
    - validation completed (validation_status = 'validated'), or
    - a final row exists in the validated table.

Skips, admin notes/admin_checked, restricted-access flags, and assignments alone
are operational context rather than retention decisions. In particular, an
assignment-only row may say validation_inprogress but has no submitted judgement;
that status alone does not protect it. When an otherwise untouched orphan is
deleted, its dependent skip/assignment/message rows are deleted in the same
transaction.

Apply mode freezes writes to the affected tables during its safety scan and
deletes, so a concurrent claim/skip/judgement cannot change that decision halfway
through the transaction. If validation is already writing, cleanup aborts instead
of waiting and can be retried in the next maintenance window.

Deletion is bound to ONE immutable snapshot, not to a filename. --apply requires
--expect-sha256, the digest of the archive that Part 1 actually imported, and
that digest must also match the one PostgreSQL recorded for the maintenance run.
A pod holding an older data/extracted_latest.csv therefore cannot delete rows
that a different pod imported from a newer CSV, even though the run-ID gate on
its own would pass.

Dry-run by default. Pass --apply to actually delete (inside one transaction).

Usage:
    python cleanup_orphans.py --input data/extracted_latest.csv           # preview
    python extractor_maintenance.py --stage cleanup                       # delete
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

from extractor_storage import require_snapshot

# Same "resolved" definition the importer uses — see extractor_vocab.py. Sharing
# it matters most here: this script DELETES on the strength of that definition,
# so a stale copy would classify live records as orphans.
from extractor_vocab import check_csv_vocabulary, resolved_mask as _resolved_mask

load_dotenv()


# The write surface validation and source-record removal share. Taken before a
# delete list is computed so a concurrent claim/skip/judgement cannot change the
# decision halfway through; NOWAIT aborts instead of risking a lock-order deadlock.
# Shared with csv_to_db.py --retire, which removes records by the same route.
WRITE_SURFACE_LOCK_SQL = """
    LOCK TABLE unvalidated, validation_queue, validated,
               validation_skips, submission_failure_releases,
               record_metadata,
               assignments, validator_messages
    IN EXCLUSIVE MODE NOWAIT
"""


def _current_resolved_pair_ids(csv_path: Path) -> set:
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
    # Refuse to compute a delete list from a CSV we can't fully read: an
    # unrecognised link_method would shrink the "still current" set and turn
    # live records into apparent orphans.
    check_csv_vocabulary(df)
    resolved = df[_resolved_mask(df)]
    return {p.strip() for p in resolved["pair_id"] if p.strip()}


_RETAINED_VALIDATION_STATUSES = frozenset({"rejected", "excluded", "validated"})


def _is_deletable_orphan(
    status: str,
    has_judgement: bool,
    has_validated_record: bool = False,
) -> bool:
    """Implement the authoritative retention rule for a CSV orphan.

    ``excluded`` is accepted as a forward-compatible spelling even though the
    current database stores the admin-facing Excluded state as ``rejected``.
    Statuses caused only by operational workflow (for example
    ``validation_inprogress`` after assignment) are deliberately not retained.
    """
    return not (
        str(status or "").strip().lower() in _RETAINED_VALIDATION_STATUSES
        or bool(has_judgement)
        or bool(has_validated_record)
    )


def delete_source_records(cur, ids: list[str]) -> dict[str, int]:
    """Delete source records and every dependent row, children first.

    The caller owns the transaction, the write-surface lock and the decision that
    each id is safe to remove; this only knows the foreign-key order. Returns the
    per-table row counts for the caller's receipt.
    """
    counts = {}
    # Messages point at queue rows and may form parent/reply threads.
    # Remove the complete thread whenever any member belongs to a
    # queue slot for a deleted record.
    cur.execute(
        """
        WITH target_threads AS (
            SELECT DISTINCT COALESCE(vm.parent_id, vm.id) AS root_id
            FROM validator_messages vm
            JOIN validation_queue q ON q.queue_id = vm.queue_id
            WHERE q.record_id = ANY(%s::uuid[])
        )
        DELETE FROM validator_messages vm
        USING target_threads t
        WHERE vm.id = t.root_id OR vm.parent_id = t.root_id
        """,
        (ids,),
    )
    counts["validator_messages"] = cur.rowcount
    for table, statement in (
        ("submission_failure_releases",
         "DELETE FROM submission_failure_releases WHERE record_id = ANY(%s::uuid[])"),
        ("validation_skips",
         "DELETE FROM validation_skips WHERE record_id = ANY(%s::uuid[])"),
        ("assignments", "DELETE FROM assignments WHERE record_id = ANY(%s::uuid[])"),
        ("validation_queue",
         "DELETE FROM validation_queue WHERE record_id = ANY(%s::uuid[])"),
        ("record_metadata",
         "DELETE FROM record_metadata WHERE record_id = ANY(%s::uuid[])"),
        ("unvalidated", "DELETE FROM unvalidated WHERE record_id = ANY(%s::uuid[])"),
    ):
        cur.execute(statement, (ids,))
        counts[table] = cur.rowcount
    return counts


def _require_maintenance_gate(
    cur,
    maintenance_run_id: str | None,
    snapshot_sha256: str | None = None,
) -> None:
    """Allow deletion only from a run with committed Part 1/2 completion.

    The run ID proves that a sync and report succeeded *somewhere*; it says
    nothing about which bytes this process just read. PostgreSQL therefore also
    stores the archive digest of that run, and deletion proceeds only when the
    file on this host is that exact archive.
    """
    if not maintenance_run_id:
        raise RuntimeError(
            "--apply requires an audited maintenance run; use "
            "extractor_maintenance.py --stage cleanup"
        )
    if not snapshot_sha256:
        raise RuntimeError(
            "--apply requires --expect-sha256, the digest of the archived "
            "snapshot imported by Part 1 of this maintenance run"
        )
    cur.execute(
        """
        SELECT status, stage_status, safety_report
        FROM extractor_maintenance_runs
        WHERE run_id = %s
        """,
        (maintenance_run_id,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"maintenance run {maintenance_run_id} does not exist")
    status, stage_status, safety_report = row
    stage_status = stage_status if isinstance(stage_status, dict) else {}
    safety_report = safety_report if isinstance(safety_report, dict) else {}
    recorded_sha256 = str(safety_report.get("archive_sha256") or "")
    approved = (
        status == "running"
        and stage_status.get("sync_csv") == "SUCCESS"
        and stage_status.get("find_orphans") == "SUCCESS"
        and safety_report.get("part1_completed") is True
        and safety_report.get("part2_completed") is True
        and bool(safety_report.get("source_sync_run_id"))
        and bool(safety_report.get("source_find_run_id"))
        and bool(recorded_sha256)
    )
    if not approved:
        raise RuntimeError(
            "orphan cleanup blocked: Parts 1 and 2 are not verified for this "
            f"maintenance run ({maintenance_run_id})"
        )
    if recorded_sha256 != snapshot_sha256:
        raise RuntimeError(
            "orphan cleanup blocked: this host read a different snapshot than "
            f"maintenance run {maintenance_run_id} imported "
            f"(recorded sha256={recorded_sha256}, read {snapshot_sha256}); the "
            "Part 1 archive must be on shared durable storage"
        )
    print(
        "Maintenance gate verified: "
        f"sync={safety_report['source_sync_run_id']} "
        f"find={safety_report['source_find_run_id']} "
        f"snapshot sha256={recorded_sha256}"
    )


def _record_cleanup_receipt(
    cur,
    maintenance_run_id: str | None,
    snapshot_sha256: str | None,
    *,
    orphan_count: int,
    kept_count: int,
    deleted_records: list[dict],
    deleted_counts: dict[str, int],
) -> dict:
    """Persist the deletion receipt in the deletion transaction itself.

    The parent orchestrator can crash after this transaction commits. Recording
    Part 3 from the parent process would therefore leave a gap in which rows are
    gone but their authoritative audit is not. This update is executed by the
    cleanup child on the same connection and transaction as every DELETE.
    """
    if not maintenance_run_id or not snapshot_sha256:
        raise RuntimeError("an audited cleanup receipt requires run and snapshot IDs")
    receipt = {
        "committed": True,
        "maintenance_run_id": str(maintenance_run_id),
        "snapshot_sha256": snapshot_sha256,
        "committed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "orphan_count": orphan_count,
        "kept_count": kept_count,
        "delete_candidate_count": len(deleted_records),
        "deleted_counts": deleted_counts,
        # Keep exact identities, not a sample: this JSON is the durable audit
        # evidence used when the parent process dies before finalization.
        "deleted_records": deleted_records,
    }
    patch = {"cleanup_receipt": receipt, "part3_completed": True}
    cur.execute(
        """
        UPDATE extractor_maintenance_runs
        SET stage_status = jsonb_set(
                stage_status, '{cleanup_orphans}', '"COMMITTED"'::jsonb, TRUE
            ),
            safety_report = safety_report || %s::jsonb
        WHERE run_id = %s AND status = 'running'
        """,
        (json.dumps(patch), str(maintenance_run_id)),
    )
    if cur.rowcount != 1:
        raise RuntimeError(
            f"maintenance run {maintenance_run_id} is no longer running; "
            "rolling back orphan deletion"
        )
    return receipt


def main(
    csv_path: Path,
    apply: bool,
    maintenance_run_id: str | None = None,
    expect_sha256: str | None = None,
) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    if apply and not expect_sha256:
        raise RuntimeError(
            "--apply requires --expect-sha256, the digest of the archived "
            "snapshot imported by Part 1 of this maintenance run"
        )
    # Verified before the delete list is computed, so an unverifiable file never
    # reaches the retention rule at all.
    snapshot_sha256 = (
        require_snapshot(csv_path, expect_sha256, stage="orphan cleanup")
        if expect_sha256
        else None
    )

    csv_pair_ids = _current_resolved_pair_ids(csv_path)

    conn = psycopg2.connect(database_url)
    try:
        with conn:  # one transaction; commits on clean exit, rolls back on error
            with conn.cursor() as cur:
                if apply:
                    _require_maintenance_gate(
                        cur,
                        maintenance_run_id,
                        snapshot_sha256,
                    )
                    # Freeze the write surface used by validation and this
                    # cleanup before computing the delete list. This closes the
                    # check-then-delete race where a concurrent skip could add a
                    # validation_skips FK after being classified as safe and roll
                    # back the whole cleanup. NOWAIT makes an already-active
                    # validator abort this maintenance run instead of risking a
                    # lock-order deadlock; new writes wait once all locks are held.
                    cur.execute(WRITE_SURFACE_LOCK_SQL)
                cur.execute(
                    """
                    SELECT u.record_id, u.pair_id, u.doi_r, u.validation_status,
                           (COALESCE(BOOL_OR(q.is_validated), FALSE)
                            OR u.validator_1 IS NOT NULL
                            OR u.validator_2 IS NOT NULL) AS has_judgement,
                           EXISTS (
                               SELECT 1 FROM validated v
                               WHERE v.record_id = u.record_id
                           ) AS has_validated_record,
                           (SELECT COUNT(*) FROM validation_skips s
                            WHERE s.record_id = u.record_id) AS skip_count
                    FROM unvalidated u
                    LEFT JOIN validation_queue q ON q.record_id = u.record_id
                    GROUP BY u.record_id, u.pair_id, u.doi_r, u.validation_status
                    """
                )
                all_rows = cur.fetchall()

                orphans = [r for r in all_rows if (r[1] or "").strip() not in csv_pair_ids]
                safe, unsafe = [], []
                for (
                    rec_id,
                    pair_id,
                    doi_r,
                    status,
                    has_judgement,
                    has_validated_record,
                    skip_count,
                ) in orphans:
                    if _is_deletable_orphan(
                        status,
                        has_judgement,
                        has_validated_record,
                    ):
                        safe.append((rec_id, pair_id, doi_r, skip_count))
                    else:
                        unsafe.append(
                            (
                                rec_id,
                                pair_id,
                                doi_r,
                                status,
                                has_judgement,
                                has_validated_record,
                                skip_count,
                            )
                        )

                print(f"Orphans found:        {len(orphans)}")
                print(f"  safe to delete:     {len(safe)}")
                print(f"  kept by retention rule: {len(unsafe)}")
                print()

                for (
                    rec_id,
                    pair_id,
                    doi_r,
                    status,
                    has_judgement,
                    has_validated_record,
                    skip_count,
                ) in unsafe:
                    print(
                        f"  KEEP  {rec_id}  status={status}  "
                        f"has_judgement={has_judgement}  "
                        f"has_validated_record={has_validated_record}  "
                        f"skips={skip_count}  "
                        f"pair_id={pair_id}  {doi_r}"
                    )
                for rec_id, pair_id, doi_r, skip_count in safe:
                    print(
                        f"  {'DELETE' if apply else 'WOULD DELETE'}  {rec_id}  "
                        f"skips_to_delete={skip_count}  pair_id={pair_id}  {doi_r}"
                    )

                if not safe:
                    if apply:
                        _record_cleanup_receipt(
                            cur,
                            maintenance_run_id,
                            snapshot_sha256,
                            orphan_count=len(orphans),
                            kept_count=len(unsafe),
                            deleted_records=[],
                            deleted_counts={
                                "unvalidated": 0,
                                "record_metadata": 0,
                                "validation_queue": 0,
                                "validation_skips": 0,
                                "submission_failure_releases": 0,
                                "assignments": 0,
                                "validator_messages": 0,
                            },
                        )
                        print("\nNothing to delete. Atomic cleanup receipt recorded.")
                        return
                    print("\nNothing to delete.")
                    return

                if not apply:
                    print("\n[dry-run] No changes made. Re-run with --apply to delete.")
                    return

                ids = [str(rec_id) for rec_id, _, _, _ in safe]
                counts = delete_source_records(cur, ids)
                messages_deleted = counts["validator_messages"]
                submission_failures_deleted = counts["submission_failure_releases"]
                skips_deleted = counts["validation_skips"]
                assignments_deleted = counts["assignments"]
                q_deleted = counts["validation_queue"]
                m_deleted = counts["record_metadata"]
                u_deleted = counts["unvalidated"]
                if u_deleted != len(safe):
                    raise RuntimeError(
                        f"cleanup selected {len(safe)} records but deleted {u_deleted}; "
                        "rolling back instead of writing an incomplete audit"
                    )
                receipt_records = [
                    {
                        "record_id": str(rec_id),
                        "pair_id": pair_id,
                        "doi_r": doi_r,
                        "skip_count": int(skip_count or 0),
                    }
                    for rec_id, pair_id, doi_r, skip_count in safe
                ]
                _record_cleanup_receipt(
                    cur,
                    maintenance_run_id,
                    snapshot_sha256,
                    orphan_count=len(orphans),
                    kept_count=len(unsafe),
                    deleted_records=receipt_records,
                    deleted_counts={
                        "unvalidated": u_deleted,
                        "record_metadata": m_deleted,
                        "validation_queue": q_deleted,
                        "validation_skips": skips_deleted,
                        "submission_failure_releases": submission_failures_deleted,
                        "assignments": assignments_deleted,
                        "validator_messages": messages_deleted,
                    },
                )
                print(
                    f"\nDeleted: {u_deleted} unvalidated, {m_deleted} metadata, "
                    f"{q_deleted} queue slots, {skips_deleted} skips, "
                    f"{submission_failures_deleted} submission-failure audits, "
                    f"{assignments_deleted} assignments, {messages_deleted} messages. "
                    "Atomic receipt recorded; committing."
                )
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete stale orphan rows safely")
    parser.add_argument("--input", type=Path, default=Path("data/extracted_latest.csv"))
    parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry-run)")
    parser.add_argument(
        "--maintenance-run-id",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--expect-sha256",
        default=None,
        help="Digest of the Part 1 archive; required with --apply",
    )
    args = parser.parse_args()
    main(
        args.input,
        apply=args.apply,
        maintenance_run_id=args.maintenance_run_id,
        expect_sha256=args.expect_sha256,
    )
