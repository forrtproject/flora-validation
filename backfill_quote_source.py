"""
backfill_quote_source.py — Populate `out_quote_source` on rows already in the
`validated` table, using the same abstract-detection rule as the consensus engine:

    'abstract'  — the (normalised) outcome_quote is contained in the abstract
    'full_text' — it isn't
    (left as-is) — there is no outcome_quote to place

By default it only fills rows where `out_quote_source` IS NULL, so existing values
(from the CSV import or an admin override) are never clobbered. Pass --recompute-all
to also refresh values that are themselves computed results: 'abstract' and
'full_text', in any spelling normalize_quote_source maps to those. Admin-locked rows
(out_quote_source_by IS NOT NULL) are always skipped.

--recompute-all never replaces a named section or a compound value ('discussion',
'abstract|discussion'), locked or not. Those come from the extractor or an admin, the
check can only answer 'abstract' or 'full_text', and a missing lock does not mean
nobody looked: resolving a record without changing its source records no name.

Usage:
    python backfill_quote_source.py                 # dry-run, NULLs only
    python backfill_quote_source.py --apply         # write (NULLs only)
    python backfill_quote_source.py --recompute-all # dry-run, refresh computed values
    python backfill_quote_source.py --recompute-all --apply
"""
import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from console_encoding import use_utf8_output

from consensus_engine import quote_source_for
from extractor_vocab import normalize_quote_source

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

# All the check can answer, so the only stored values it may replace.
COMPUTED_SOURCES = ("abstract", "full_text")


def recomputed_source(row: dict, recompute_all: bool = False) -> str | None:
    """The source to write to this validated row, or None to leave it alone."""
    if row.get("out_quote_source_by"):             # admin-locked → never touch
        return None
    stored = row.get("out_quote_source")
    if stored and not recompute_all:               # already set, not recomputing
        return None
    named = normalize_quote_source(stored)
    if named and named not in COMPUTED_SOURCES:    # a section or compound → not ours
        return None
    new_src = quote_source_for(row.get("outcome_quote"), row.get("abstract_r"))
    if new_src is None or new_src == (stored or None):
        return None                                # no quote, or no change
    return new_src


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
                new_src = recomputed_source(r, recompute_all)
                if new_src is None:
                    continue

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
                    help="Also refresh computed values, abstract and full_text (named sections "
                         "and admin-locked rows are still skipped)")
    args = ap.parse_args()
    run(apply=args.apply, recompute_all=args.recompute_all)
