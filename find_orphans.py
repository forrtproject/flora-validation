"""
find_orphans.py — Summarise the records the current extracted CSV no longer ships.

The importer (csv_to_db.py) never deletes, so the database holds records that
were resolved in a PAST extraction snapshot but have since dropped out of, or
been reclassified in, the current CSV. This script counts those "orphans".

Read-only. Does NOT modify the database, and nothing deletes on the strength of
it: records leave the database only through the retire stage
(csv_to_db.py --retire), which acts on the pairs flora-extractor names in its
data/retired_pairs.csv. Most orphans are expected — the extractor withholds the
works that are already in the validation tables — so the report groups them by
status instead of listing every one, and lists only those still 'unvalidated'.

The report is bound to the same immutable Part 1 archive the retire stage reads:
pass --expect-sha256 and this script refuses any other file.

Usage:
    python find_orphans.py --input data/extracted_<run>.csv --expect-sha256 <hex>
"""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

from extractor_storage import require_snapshot
# Same "resolved" definition the importer uses — see extractor_vocab.py.
from extractor_vocab import resolved_mask as _resolved_mask

load_dotenv()

# Enough to recognise a pattern in the log without burying the rest of the run.
_LISTED_LIMIT = 50


def summarise(rows: list, csv_pair_ids: set) -> dict:
    """Counts of the records *csv_pair_ids* does not cover. Pure."""
    orphans = [r for r in rows if (r[1] or "").strip() not in csv_pair_ids]
    by_status = Counter(str(r[4] or "unknown") for r in orphans)
    # Status alone: an 'unvalidated' record may still have been shown or skipped,
    # which the retire stage checks before it removes anything.
    unvalidated = sorted((r for r in orphans if r[4] == "unvalidated"),
                         key=lambda r: r[2] or "")
    return {
        "records_in_database": len(rows),
        "csv_resolved_pair_ids": len(csv_pair_ids),
        "orphan_count": len(orphans),
        "orphans_by_status": dict(by_status.most_common()),
        "unvalidated_count": len(unvalidated),
        "other_status_count": len(orphans) - len(unvalidated),
        "unvalidated_sample": [
            {"record_id": str(r[0]), "doi_r": r[2] or "", "doi_o": r[3] or ""}
            for r in unvalidated[:_LISTED_LIMIT]
        ],
    }


def main(csv_path: Path, expect_sha256: str | None = None,
         summary_json: Path | None = None) -> dict:
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

    summary = summarise(rows, csv_pair_ids)
    print(f"Records in the database:        {summary['records_in_database']:>6,}")
    print(f"Resolved pair IDs in this CSV:  {summary['csv_resolved_pair_ids']:>6,}")
    print(f"Records this CSV does not list: {summary['orphan_count']:>6,}")
    for status, count in summary["orphans_by_status"].items():
        print(f"  {status:<28} {count:>6,}")
    print(f"  status other than unvalidated: {summary['other_status_count']:>4,}  (kept)")
    print(f"  still unvalidated:             {summary['unvalidated_count']:>4,}  "
          "(the retire stage removes those the extractor lists and nobody touched)")

    if summary["unvalidated_sample"]:
        shown = len(summary["unvalidated_sample"])
        print()
        print(f"Still unvalidated ({shown:,} of {summary['unvalidated_count']:,} shown):")
        for item in summary["unvalidated_sample"]:
            print(f"  {item['record_id']:38}  {item['doi_r']}  ->  {item['doi_o']}")

    if summary_json is not None:
        summary_json.write_text(json.dumps(summary), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Summarise the database records the current CSV no longer ships"
    )
    parser.add_argument(
        "--input", type=Path, default=Path("data/extracted_latest.csv"),
        help="Path to the current extracted CSV (default: data/extracted_latest.csv)",
    )
    parser.add_argument(
        "--expect-sha256",
        default=None,
        help="Refuse to report unless --input has exactly this sha256",
    )
    parser.add_argument("--summary-json", type=Path, default=None,
                        help=argparse.SUPPRESS)  # extractor_maintenance.py reads it
    args = parser.parse_args()
    main(args.input, expect_sha256=args.expect_sha256, summary_json=args.summary_json)
