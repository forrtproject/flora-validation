"""sync_validated.py — project our own validated records into source_records.

The Source Records grid is the one place where everything feeding the FLoRA dataset
can be reviewed side by side. Until now it held only the Google entry sheets, so the
records this project validates itself were invisible there — including the ones the
entry sheets already cover. This script puts them in the same grid, under the source
key `validated` with a `VAL-` prefix, and the existing duplicate detector then flags
that overlap without any extra machinery.

WHY THIS IS NOT INSERT-ONLY
---------------------------
`sync_sources.py` never updates a row: an entry sheet is external, edits to it are
unattributed, and the database is deliberately authoritative once a row lands.

None of that applies here. `validated` is our own table, inside the same database,
and a validated record can legitimately change — an admin re-opens one, a merge
resolves two records into one. A permanently stale copy in the grid would be a bug,
not a safeguard. So this sync refreshes a row when its source record changes.

WHAT IT WILL NOT OVERWRITE
--------------------------
A row a human has reviewed in the grid (`reviewed_at IS NOT NULL`) is never updated.
Refreshing it would silently discard a reviewer's correction, which is the one thing
insert-only was protecting against. Those rows are counted and reported instead, so a
divergence is visible rather than resolved by whoever wrote last.

Usage:
    python sync_validated.py
    python sync_validated.py --dry-run

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import json
import os
import sys

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging

load_dotenv()
use_utf8_output()

SOURCE_KEY = "validated"
DISPLAY_PREFIX = "VAL"

# Straight column copies: same name on `validated` and on `source_records`.
DIRECT_COLUMNS = [
    "ref_o", "doi_o", "url_o",
    "ref_r", "doi_r", "url_r",
    "abstract_r",
    "year_r",
    "study_o",
    "outcome", "outcome_quote", "out_quote_source",
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
    "alt_identifier_r",
]

# Written on insert and kept in step on refresh. validation_status is deliberately
# absent: it is the *sheet's* coder vocabulary ("validated - chosen"), and these rows
# never passed through a sheet. Reusing one of those values would claim a provenance
# that does not exist, so it stays NULL and `source = 'validated'` carries the meaning.
# oa_work_id_o/r are also absent — the OpenAlex backfill owns those, and setting them
# here would fight trg_clear_stale_source_oa_work_id on every DOI refresh.
SYNCED_COLUMNS = DIRECT_COLUMNS + ["type", "raw"]


def _s(v) -> "str | None":
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _raw_payload(row: dict) -> dict:
    """The whole validated record, stringified, exactly as `raw` holds a sheet row.

    Includes the fields source_records has no column for — title_o, title_r,
    admin_approved, original_key, record_id, validated_at — so a reviewer can see
    them and a later backfill can promote one without re-deriving anything.
    """
    return {k: ("" if v is None else str(v)) for k, v in row.items()}


def _build_row(v: dict) -> dict:
    row = {col: _s(v.get(col)) for col in DIRECT_COLUMNS}
    row["source"] = SOURCE_KEY
    row["sheet_row_id"] = str(v["validated_record_id"])
    row["type"] = v.get("type") or "replication"

    if row["type"] == "reproduction":
        # `validated.outcome` holds the two axes joined into one display string
        # ("computationally reproducible, robustness challenges"). source_records
        # keeps them unmerged in their own columns, which are copied above, so the
        # joined form would be a third spelling of data already present.
        row["outcome"] = None
        row["outcome_quote"] = None
        row["out_quote_source"] = None
    else:
        row["outcome_computation"] = None
        row["outcome_robustness"] = None

    row["raw"] = Json(_raw_payload(v))
    return row


def _next_display_id(cur, prefix: str) -> str:
    cur.execute(
        """
        INSERT INTO source_display_counters (source, last_value) VALUES (%s, 1)
        ON CONFLICT (source) DO UPDATE SET last_value = source_display_counters.last_value + 1
        RETURNING last_value
        """,
        (SOURCE_KEY,),
    )
    return f"{prefix}-{cur.fetchone()['last_value']:06d}"


def _fetch_validated(cur) -> list:
    cur.execute("SELECT * FROM validated ORDER BY validated_at, validated_record_id")
    return [dict(r) for r in cur.fetchall()]


def sync(cur, dry_run: bool) -> dict:
    records = _fetch_validated(cur)
    print(f"  validated records: {len(records)}")

    cur.execute(
        """
        SELECT sheet_row_id, display_id, reviewed_at IS NOT NULL AS reviewed
        FROM source_records WHERE source = %s
        """,
        (SOURCE_KEY,),
    )
    existing = {r["sheet_row_id"]: r for r in cur.fetchall()}

    inserted = updated = unchanged = protected = 0

    # Built once from SYNCED_COLUMNS rather than written out: a column added above
    # and forgotten here would refresh on insert but silently never update.
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in SYNCED_COLUMNS)
    differs = " OR ".join(
        f"source_records.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in DIRECT_COLUMNS + ["type"]
    )
    cols = ["source", "sheet_row_id", "display_id", *SYNCED_COLUMNS]
    placeholders = ", ".join(f"%({c})s" for c in cols)

    upsert = f"""
        INSERT INTO source_records ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (source, sheet_row_id) DO UPDATE
           SET {set_clause},
               version = source_records.version + 1,
               updated_at = NOW()
         WHERE source_records.reviewed_at IS NULL
           AND ({differs})
        RETURNING (xmax = 0) AS was_insert
    """

    for v in records:
        row = _build_row(v)
        sid = row["sheet_row_id"]
        prior = existing.get(sid)

        if prior and prior["reviewed"]:
            # Reported, never resolved silently: a reviewer's correction outranks a
            # refresh, and a divergence someone should look at is not the sync's to
            # decide.
            protected += 1
            continue

        if dry_run:
            if not prior:
                inserted += 1
            else:
                unchanged += 1   # a dry run cannot tell without writing
            continue

        display_id = prior["display_id"] if prior else _next_display_id(cur, DISPLAY_PREFIX)
        cur.execute(upsert, {**row, "display_id": display_id})
        result = cur.fetchone()
        if result is None:
            # The WHERE on DO UPDATE matched nothing: same content, nothing to do.
            unchanged += 1
        elif result["was_insert"]:
            inserted += 1
        else:
            updated += 1

    return {
        "fetched": len(records),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "protected": protected,
    }


def _record_run(cur, stats: dict, status: str = "ok", failure: str = None) -> None:
    """Same table the sheet syncs write to, so the admin panel's sync log shows this
    run alongside them without a second code path."""
    notes = (
        f"{stats.get('updated', 0)} refreshed, "
        f"{stats.get('protected', 0)} left alone (reviewed in the grid)"
    ) if failure is None else None
    cur.execute(
        """
        INSERT INTO source_sync_runs (
            source, status, finished_at, failure_reason,
            rows_fetched, rows_accepted, rows_inserted, rows_existing, rows_skipped, notes
        ) VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            SOURCE_KEY, status, failure,
            stats.get("fetched"), stats.get("fetched"),
            stats.get("inserted"), stats.get("unchanged"),
            stats.get("protected"), notes,
        ),
    )


def main() -> int:
    start_logging("sync-validated")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change; write nothing")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    print("=== validated -> source_records ===")
    if args.dry_run:
        print("[dry-run] nothing will be written\n")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                stats = sync(cur, args.dry_run)
            except Exception as exc:
                conn.rollback()
                with conn.cursor(cursor_factory=RealDictCursor) as c2:
                    _record_run(c2, {}, status="failed", failure=str(exc))
                conn.commit()
                print(f"  FAILED - {exc}")
                return 1

            if not args.dry_run:
                _record_run(cur, stats)
                conn.commit()
    finally:
        conn.close()

    print(f"\n  inserted:  {stats['inserted']}")
    print(f"  refreshed: {stats['updated']}")
    print(f"  unchanged: {stats['unchanged']}")
    if stats["protected"]:
        print(f"  left alone: {stats['protected']} (reviewed in the grid — refresh would "
              f"discard a reviewer's correction)")
    print("\n=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
