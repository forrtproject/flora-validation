"""
backfill_quality_flags.py — Run the row-local data-quality checks over records that
already finished consensus, and store the results in unvalidated.quality_flags.

New records are flagged automatically when consensus completes (see
consensus_engine._update_status). This script is for everything that finished
before the checks existed.

Flags are advisory. This script NEVER changes validation_status: a record that was
validated stays validated, and an admin decides from the review panel whether a
flagged record should be excluded. Nothing here reopens settled work.

Safe to re-run. Flags are recomputed from current values every time, so a record
fixed since the last run comes back clean rather than keeping a stale flag.

Usage:
    python backfill_quality_flags.py                # apply to every finished record
    python backfill_quality_flags.py --dry-run      # report only, write nothing
    python backfill_quality_flags.py --status validated   # limit to one status
    python backfill_quality_flags.py --limit 500    # cap rows processed
"""
import argparse
import json
import os
import sys
from collections import Counter

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from console_encoding import use_utf8_output

from record_checks import check_record

load_dotenv()

# This script's --help and its flag labels carry non-ASCII glyphs (— …), which a
# cp1252 Windows console cannot encode: print() would abort the run, potentially
# after the UPDATE has already been committed. See console_encoding.py.
use_utf8_output()

# Records a validator or the LLM has finished with. 'unvalidated' and
# 'validation_inprogress' are deliberately excluded: their values are still the raw
# extractor output, so flagging them would report problems that consensus may yet
# resolve through a correction.
FINISHED_STATUSES = ("validated", "consensus_reached", "need_review", "rejected")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--status", action="append", choices=FINISHED_STATUSES,
                    help="limit to this validation_status (repeatable)")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after this many records")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    statuses = tuple(args.status) if args.status else FINISHED_STATUSES

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = """
                SELECT * FROM unvalidated
                WHERE validation_status = ANY(%s)
                ORDER BY updated_at DESC
            """
            params: list = [list(statuses)]
            if args.limit:
                sql += " LIMIT %s"
                params.append(args.limit)
            cur.execute(sql, params)
            rows = cur.fetchall()

        print(f"Checking {len(rows)} record(s) in status {', '.join(statuses)}")

        flagged = 0
        cleared = 0
        counts: Counter = Counter()
        updates: list[tuple[str, str]] = []

        for row in rows:
            record = dict(row)
            before = record.get("quality_flags") or []
            if isinstance(before, str):
                try:
                    before = json.loads(before)
                except ValueError:
                    before = []
            flags = check_record(record)
            for f in flags:
                counts[f["code"]] += 1
            if flags:
                flagged += 1
            elif before:
                # Previously flagged, now clean — the flag must go, or the panel
                # keeps warning about something that has already been fixed.
                cleared += 1
            updates.append((json.dumps(flags), str(record["record_id"])))

        print(f"  {flagged} record(s) with at least one problem")
        if cleared:
            print(f"  {cleared} record(s) whose earlier flags no longer apply")
        for code, n in counts.most_common():
            print(f"    {code}: {n}")

        if args.dry_run:
            print("\n--dry-run: nothing written")
            conn.rollback()
            return 0

        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                UPDATE unvalidated
                   SET quality_flags = %s::jsonb,
                       quality_checked_at = NOW()
                 WHERE record_id = %s::uuid
                """,
                updates,
                page_size=200,
            )
        conn.commit()
        print(f"\nWrote flags for {len(updates)} record(s). "
              "validation_status was not changed for any of them.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
