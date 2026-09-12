"""
find_orphans.py — Diagnose the gap between unvalidated rows in the DB and the
current extracted CSV.

The importer (csv_to_db.py) is append-only: it never deletes. So the DB can hold
rows that were resolved in a PAST extraction snapshot but have since dropped out
of, or been reclassified in, the current CSV. This script lists those "orphans".

Read-only. Does NOT modify the database.

The report is the evidence an admin reads before approving cleanup, so it is
bound to the same immutable Part 1 archive cleanup will delete against: pass
--expect-sha256 and this script refuses any other file.

Usage:
    python find_orphans.py --input data/extracted_<run>.csv --expect-sha256 <hex>
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

from extractor_storage import require_snapshot
# Same "resolved" definition the importer uses — see extractor_vocab.py.
from extractor_vocab import resolved_mask as _resolved_mask

load_dotenv()


def main(csv_path: Path, expect_sha256: str | None = None) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    if expect_sha256:
        require_snapshot(csv_path, expect_sha256, stage="orphan report")
        print(f"Snapshot verified: {csv_path.name} sha256={expect_sha256}")

    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
    resolved = df[_resolved_mask(df)]
    csv_pair_ids = {p.strip() for p in resolved["pair_id"] if p.strip()}

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT record_id, pair_id, doi_r, doi_o, validation_status "
                "FROM unvalidated"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    orphans = [r for r in rows if (r[1] or "").strip() not in csv_pair_ids]

    print(f"DB unvalidated rows:        {len(rows)}")
    print(f"CSV resolved pair_ids:      {len(csv_pair_ids)}")
    print(f"Orphans (in DB, not in CSV): {len(orphans)}")
    print()

    if not orphans:
        print("No orphans — DB and CSV are in sync.")
        return

    print(f"{'record_id':38}  {'status':14}  doi_r  ->  doi_o")
    print("-" * 100)
    for record_id, pair_id, doi_r, doi_o, status in sorted(orphans, key=lambda r: r[2] or ""):
        print(f"{str(record_id):38}  {status:14}  {doi_r}  ->  {doi_o}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List DB rows missing from the current CSV")
    parser.add_argument(
        "--input", type=Path, default=Path("data/extracted_latest.csv"),
        help="Path to the current extracted CSV (default: data/extracted_latest.csv)",
    )
    parser.add_argument(
        "--expect-sha256",
        default=None,
        help="Refuse to report unless --input has exactly this sha256",
    )
    args = parser.parse_args()
    main(args.input, expect_sha256=args.expect_sha256)
