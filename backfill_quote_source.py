"""
backfill_quote_source.py — Populate `out_quote_source` on rows already in the
`validated` table, using the same abstract-detection rule as the consensus engine:

    'abstract'  — the (normalised) outcome_quote is contained in the abstract
    'full_text' — it isn't
    (left as-is) — there is no outcome_quote to place

By default it only fills rows where `out_quote_source` IS NULL, so existing values
(from the CSV import or an admin override) are never clobbered. Pass --recompute-all
to also refresh non-NULL rows — but admin-locked rows (out_quote_source_by IS NOT
NULL) are always skipped.

Usage:
    python backfill_quote_source.py                 # dry-run, NULLs only
    python backfill_quote_source.py --apply         # write (NULLs only)
    python backfill_quote_source.py --recompute-all # dry-run, refresh all non-locked
    python backfill_quote_source.py --recompute-all --apply
"""
import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from consensus_engine import quote_source_for

load_dotenv()


def run(apply: bool, recompute_all: bool) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT record_id::text AS record_id, outcome_quote, abstract_r,
                       out_quote_source, out_quote_source_by
                FROM validated
                """
            )
            rows = cur.fetchall()
            print(f"  Rows in validated: {len(rows)}")

            changed = 0
            counts = {"abstract": 0, "full_text": 0}
            samples = []
            for r in rows:
                if r.get("out_quote_source_by"):          # admin-locked → never touch
                    continue
                if r.get("out_quote_source") and not recompute_all:
                    continue                               # already set, not recomputing

                new_src = quote_source_for(r.get("outcome_quote"), r.get("abstract_r"))
                if new_src is None:                        # no quote → leave it
                    continue
                if new_src == (r.get("out_quote_source") or None):
                    continue                               # no change

                changed += 1
                counts[new_src] += 1
                if len(samples) < 12:
                    samples.append((r["record_id"], r.get("out_quote_source"), new_src))

                if apply:
                    cur.execute(
                        "UPDATE validated SET out_quote_source = %s WHERE record_id = %s",
                        (new_src, r["record_id"]),
                    )

        print(f"\n  Rows that {'changed' if apply else 'would change'}: {changed}")
        for k, n in counts.items():
            if n:
                print(f"    → {k:9} {n}")
        print("\n  Sample changes (record_id: old -> new):")
        for rid, old, new in samples:
            print(f"    {rid}: {old!r} -> {new!r}")

        if apply:
            conn.commit()
            print("\n[applied] Changes committed.")
        else:
            conn.rollback()
            print("\n[dry-run] No changes written. Re-run with --apply to commit.")
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill out_quote_source on validated rows.")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    ap.add_argument("--recompute-all", action="store_true",
                    help="Also refresh non-NULL rows (admin-locked rows are still skipped)")
    args = ap.parse_args()
    run(apply=args.apply, recompute_all=args.recompute_all)
