"""Rule FLoRA records duplicates of the record that keeps the report.

The batch is a CSV of absorbed_id, survivor_id and note, by published FLoRA id.
Each absorbed record's source row is ruled a duplicate of the survivor's source
row (source_records_service.resolve_duplicate, as Source Records → Duplicates
does), so the next build drops it and its published id is retired. Carry the
absorbed record's identifier to the survivor's alt_identifier_r with an
`append` row of a field batch (scripts/apply_field_corrections.py).

Default: validate the file. --check-live resolves every id against the live data
in a transaction it rolls back. --apply rules all rows in one transaction; a row
already ruled is skipped. The pipeline run that follows rebuilds FLoRA.

    python3 scripts/apply_duplicate_rulings.py data/corrections/fred_data_179_duplicates.csv
    python3 scripts/apply_duplicate_rulings.py BATCH --check-live
    python3 scripts/apply_duplicate_rulings.py BATCH --apply --admin-handle HANDLE
"""
import argparse
import csv
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_batch(path):
    from source_records_service import RULING_NOTE_LIMIT
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]
    absorbed = [row["absorbed_id"] for row in rows]
    survivors = {row["survivor_id"] for row in rows}
    if len(set(absorbed)) != len(absorbed) or survivors & set(absorbed):
        raise ValueError("A record is absorbed twice, or is both absorbed and kept")
    for row in rows:
        if not row["absorbed_id"] or not row["survivor_id"] or row["absorbed_id"] == row["survivor_id"]:
            raise ValueError(f"Two different records are required: {row}")
        if not 3 <= len(row["note"]) <= RULING_NOTE_LIMIT:
            raise ValueError(f"Every ruling needs a note of 3 to {RULING_NOTE_LIMIT} characters: {row['absorbed_id']}")
    return rows


def plan(cur, rows, lock=False):
    """Source record ids for each pair, or an error naming what does not resolve.
    With `lock`, both source rows of every pair stay locked until the transaction
    ends, so no ruling made meanwhile in Source Records is overwritten."""
    cur.execute("""
        SELECT f.export_id, f.primary_source_record_id::text AS primary_id,
               COALESCE(array_length(f.merged_source_record_ids, 1), 0) AS merged,
               f.retired_at, s.deleted_at, s.duplicate_status, s.duplicate_of::text AS duplicate_of
        FROM flora_records f JOIN source_records s ON s.record_id = f.primary_source_record_id
        WHERE f.export_id = ANY(%s)
    """ + (" FOR UPDATE OF s" if lock else ""),
                ([r["absorbed_id"] for r in rows] + [r["survivor_id"] for r in rows],))
    found = {r["export_id"]: dict(r) for r in cur.fetchall()}
    plans = []
    for row in rows:
        absorbed, survivor = found.get(row["absorbed_id"]), found.get(row["survivor_id"])
        if (not absorbed or not survivor or absorbed["retired_at"] or survivor["retired_at"]
                or absorbed["deleted_at"] or survivor["deleted_at"]):
            raise ValueError(f"Not a published record: {row['absorbed_id']} or {row['survivor_id']}")
        if absorbed["merged"]:
            raise ValueError(f"{row['absorbed_id']} combines several source rows: rule them in Source Records")
        if survivor["duplicate_status"] == "duplicate":
            raise ValueError(f"{row['survivor_id']} is itself ruled a duplicate")
        if absorbed["duplicate_status"] == "duplicate":
            if absorbed["duplicate_of"] != survivor["primary_id"]:
                raise ValueError(f"{row['absorbed_id']} is already ruled a duplicate of another record")
            continue
        if absorbed["duplicate_status"] == "distinct":
            raise ValueError(f"{row['absorbed_id']} was ruled distinct in Source Records: review that ruling")
        plans.append({**row, "record_id": absorbed["primary_id"], "duplicate_of": survivor["primary_id"]})
    return plans


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--check-live", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--admin-handle")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    rows = load_batch(args.batch)
    if not (args.check_live or args.apply):
        print(f"Validated {len(rows)} duplicate rulings; no database opened.")
        return
    if args.apply and not args.admin_handle:
        parser.error("--apply requires --admin-handle")
    from dotenv import load_dotenv
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import source_records_service
    load_dotenv(args.env_file)
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            plans = plan(cur, rows, lock=args.apply)
            if args.apply:
                for item in plans:
                    source_records_service.resolve_duplicate(
                        cur, item["record_id"], "duplicate", args.admin_handle,
                        item["duplicate_of"], item["note"])
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()
    print(json.dumps({"applied": args.apply, "rulings": len(plans),
                      "pairs": [[p["absorbed_id"], p["survivor_id"]] for p in plans]}, indent=2))


if __name__ == "__main__":
    main()
