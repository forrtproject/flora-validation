"""
update_outcomes.py — Update outcome classification for unvalidated records from extracted.csv.

Use this when the extraction logic has been improved and you want to push corrected
outcome/type/quote values into the database — but only for records no validator has
touched yet (validation_status = 'unvalidated').

Records already in progress, under review, validated, or rejected are never touched.

Fields updated (when changed):
  outcome, type, outcome_quote, out_quote_source

Fields never touched:
  doi_r, study_r, doi_o, study_o, abstract_r — structural/bibliographic fields
  that validators may have already corrected via the admin interface.

Usage:
    python update_outcomes.py --input data/extracted.csv
    python update_outcomes.py --input data/extracted.csv --dry-run

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Only update records still in this status — anything else is hands-off
_SAFE_STATUS = "unvalidated"

# Fields from the CSV that we allow updating, mapped csv_column → db_column
_UPDATE_FIELDS = {
    "outcome":          "outcome",
    "type":             "type",
    "outcome_phrase":   "outcome_quote",
    "out_quote_source": "out_quote_source",
}


def _s(val) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return str(val).strip()


def run_update(csv_path: Path, dry_run: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    print(f"Reading {csv_path} …")
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")

    # Keep only rows that have a pair_id
    df = df[df["pair_id"].str.strip() != ""].copy()
    print(f"  Rows with pair_id:  {len(df)}")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # Load ALL pair_ids in the DB so we can distinguish
            # "genuinely new" from "exists but busy (non-unvalidated)"
            cur.execute(
                "SELECT pair_id, validation_status FROM unvalidated WHERE pair_id IS NOT NULL"
            )
            all_db_statuses = {r["pair_id"]: r["validation_status"] for r in cur.fetchall()}

            # Subset: only unvalidated rows with no validator activity in the queue
            # (no human slot has been shown or submitted yet)
            cur.execute(
                """
                SELECT pair_id, outcome, type, outcome_quote, out_quote_source
                FROM unvalidated u
                WHERE u.validation_status = %s
                  AND u.pair_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM validation_queue vq
                    WHERE vq.record_id = u.record_id
                      AND vq.validator_slot IN ('human_1', 'human_2')
                      AND (vq.is_shown = TRUE OR vq.is_validated = TRUE)
                  )
                """,
                (_SAFE_STATUS,),
            )
            db_rows = {r["pair_id"]: dict(r) for r in cur.fetchall()}
            print(f"  Safe to update (unvalidated, no validator activity): {len(db_rows)}")

            updated      = 0
            unchanged    = 0
            not_in_db    = 0   # truly new — not in DB at all
            skipped_busy = 0   # in DB but status is not unvalidated, OR a validator has already seen it

            changes = []  # collect all updates before applying

            for _, row in df.iterrows():
                pair_id = _s(row.get("pair_id"))
                if pair_id not in db_rows:
                    if pair_id in all_db_statuses:
                        skipped_busy += 1   # exists but not unvalidated — hands off
                    else:
                        not_in_db += 1      # genuinely new record
                    continue

                db = db_rows[pair_id]
                updates = {}

                for csv_col, db_col in _UPDATE_FIELDS.items():
                    new_val = _s(row.get(csv_col))
                    old_val = _s(db.get(db_col))
                    if new_val != old_val:
                        updates[db_col] = new_val

                if not updates:
                    unchanged += 1
                    continue

                changes.append((pair_id, updates))

            # Report what will change
            print(f"\n  Will update:        {len(changes)}")
            print(f"  Already correct:    {unchanged}")
            print(f"  Not in DB:          {not_in_db}  (new rows — run csv_to_db.py to import)")
            print(f"  Skipped (validator already active or record busy): {skipped_busy}")

            if dry_run:
                print("\n[dry-run] Sample of changes (first 10):")
                for pair_id, updates in changes[:10]:
                    print(f"  pair_id={pair_id}")
                    for col, val in updates.items():
                        old = _s(db_rows[pair_id].get(col))
                        print(f"    {col}: {repr(old)} → {repr(val)}")
                print("\n[dry-run] No changes written.")
                return

            if not changes:
                print("\nNothing to update.")
                return

            # Apply updates
            for pair_id, updates in changes:
                set_clause = ", ".join(f"{col} = %s" for col in updates)
                values = list(updates.values()) + [pair_id]
                cur.execute(
                    f"""
                    UPDATE unvalidated u
                    SET {set_clause}
                    WHERE pair_id = %s
                      AND validation_status = %s
                      AND NOT EXISTS (
                        SELECT 1 FROM validation_queue vq
                        WHERE vq.record_id = u.record_id
                          AND vq.validator_slot IN ('human_1', 'human_2')
                          AND (vq.is_shown = TRUE OR vq.is_validated = TRUE)
                      )
                    """,
                    values + [_SAFE_STATUS],
                )
                updated += cur.rowcount

            conn.commit()
            print(f"\nDone. Updated: {updated} records.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Update outcome classification for unvalidated records from extracted.csv"
    )
    parser.add_argument(
        "--input", type=Path, default=Path("data/extracted.csv"),
        help="Path to extracted.csv (default: data/extracted.csv)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing to the database.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    run_update(args.input, dry_run=args.dry_run)
