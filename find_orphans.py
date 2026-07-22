"""
find_orphans.py — Diagnose the gap between unvalidated rows in the DB and the
current extracted CSV.

The importer (csv_to_db.py) is append-only: it never deletes. So the DB can hold
rows that were resolved in a PAST extraction snapshot but have since dropped out
of, or been reclassified in, the current CSV. This script lists those "orphans".

Read-only. Does NOT modify the database.

Usage:
    python find_orphans.py --input data/extracted_latest.csv
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Same filter the importer uses to decide what counts as "resolved"
_RESOLVED_METHODS = {
    "author_year_match", "llm_abstract", "llm_fulltext",
    "single_candidate_after_requery", "title_pattern_match",
    "citation_context_match", "same_author_year_title_overlap",
}
_RESOLVED_STATUSES = {"replication", "reproduction"}


def main(csv_path: Path) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
    resolved = df[
        df["filter_status"].isin(_RESOLVED_STATUSES)
        & df["link_method"].isin(_RESOLVED_METHODS)
    ]
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
    args = parser.parse_args()
    main(args.input)
