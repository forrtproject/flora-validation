"""
cleanup_orphans.py — Delete stale 'unvalidated' rows that are no longer in the
current extracted CSV.

The importer (csv_to_db.py) is append-only, so the DB accumulates rows that were
resolved in a PAST snapshot but have since dropped out of the current CSV. This
script removes those orphans — but ONLY when they are safe to remove:

  GUARD: a row is deleted only if BOTH hold
    - validation_status = 'unvalidated'  (no consensus/validation reached), AND
    - it has no submitted judgement in validation_queue (is_validated = FALSE for
      every slot).

Anything with real validator work is left untouched and reported.

Dry-run by default. Pass --apply to actually delete (inside one transaction).

Usage:
    python cleanup_orphans.py --input data/extracted_latest.csv           # preview
    python cleanup_orphans.py --input data/extracted_latest.csv --apply   # delete
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

_RESOLVED_METHODS = {"author_year_match", "llm_abstract", "llm_fulltext"}
_RESOLVED_STATUSES = {"replication", "reproduction"}


def _current_resolved_pair_ids(csv_path: Path) -> set:
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
    resolved = df[
        df["filter_status"].isin(_RESOLVED_STATUSES)
        & df["link_method"].isin(_RESOLVED_METHODS)
    ]
    return {p.strip() for p in resolved["pair_id"] if p.strip()}


def main(csv_path: Path, apply: bool) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    csv_pair_ids = _current_resolved_pair_ids(csv_path)

    conn = psycopg2.connect(database_url)
    try:
        with conn:  # one transaction; commits on clean exit, rolls back on error
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.record_id, u.pair_id, u.doi_r, u.validation_status,
                           COALESCE(BOOL_OR(q.is_validated), FALSE) AS has_judgement
                    FROM unvalidated u
                    LEFT JOIN validation_queue q ON q.record_id = u.record_id
                    GROUP BY u.record_id, u.pair_id, u.doi_r, u.validation_status
                    """
                )
                all_rows = cur.fetchall()

                orphans = [r for r in all_rows if (r[1] or "").strip() not in csv_pair_ids]
                safe, unsafe = [], []
                for rec_id, pair_id, doi_r, status, has_judgement in orphans:
                    if status == "unvalidated" and not has_judgement:
                        safe.append((rec_id, doi_r))
                    else:
                        unsafe.append((rec_id, doi_r, status, has_judgement))

                print(f"Orphans found:        {len(orphans)}")
                print(f"  safe to delete:     {len(safe)}")
                print(f"  kept (has work):    {len(unsafe)}")
                print()

                for rec_id, doi_r, status, has_judgement in unsafe:
                    print(f"  KEEP  {rec_id}  status={status}  has_judgement={has_judgement}  {doi_r}")
                for rec_id, doi_r in safe:
                    print(f"  {'DELETE' if apply else 'WOULD DELETE'}  {rec_id}  {doi_r}")

                if not safe:
                    print("\nNothing to delete.")
                    return

                if not apply:
                    print("\n[dry-run] No changes made. Re-run with --apply to delete.")
                    return

                ids = [str(rec_id) for rec_id, _ in safe]
                cur.execute("DELETE FROM validation_queue WHERE record_id = ANY(%s::uuid[])", (ids,))
                q_deleted = cur.rowcount
                cur.execute("DELETE FROM record_metadata WHERE record_id = ANY(%s::uuid[])", (ids,))
                m_deleted = cur.rowcount
                cur.execute("DELETE FROM unvalidated WHERE record_id = ANY(%s::uuid[])", (ids,))
                u_deleted = cur.rowcount
                print(
                    f"\nDeleted: {u_deleted} unvalidated, {m_deleted} metadata, "
                    f"{q_deleted} queue slots. Committing."
                )
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete stale orphan rows safely")
    parser.add_argument("--input", type=Path, default=Path("data/extracted_latest.csv"))
    parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry-run)")
    args = parser.parse_args()
    main(args.input, apply=args.apply)
