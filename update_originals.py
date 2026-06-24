"""
update_originals.py — Refresh the ORIGINAL-study references on rows already in
the DB, from a (new) extracted CSV. Matches by pair_id.

The nightly import (csv_to_db.py) is insert-only — it skips rows already in the
DB — so corrected original references in a new CSV never reach existing rows.
This script fills that gap.

What it touches (extracted source values only):
    unvalidated:      doi_o, study_o (CSV title_o), year_o, url_o, ref_o
    record_metadata:  authors_o

What it deliberately NEVER touches:
    final_* values, validator_1/2/llm blobs, validation_status, assignments —
    i.e. any human/consensus decision. This only refreshes the raw references.

Rules:
    - Only rows whose pair_id already exists in the DB are updated.
    - A field is overwritten only when the new CSV value is non-empty AND differs
      (so a blank CSV cell never wipes a good existing value).

Usage:
    python update_originals.py [csv_path]            # dry-run (default)
    python update_originals.py [csv_path] --apply     # write changes
    csv_path defaults to data/extracted_latest.csv
"""
import argparse
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
import pandas as pd
from dotenv import load_dotenv

from csv_to_db import _s, _derive_url_o

load_dotenv()

_DEFAULT_CSV = Path(__file__).parent / "data" / "extracted_latest.csv"

# unvalidated original-side columns ← CSV source
_UNVAL_FIELDS = {
    "doi_o":   lambda r: _s(r.get("doi_o")),
    "study_o": lambda r: _s(r.get("title_o")),
    "year_o":  lambda r: _s(r.get("year_o")),
    "url_o":   lambda r: _derive_url_o(r.get("doi_o")),
    "ref_o":   lambda r: _s(r.get("ref_o")),
}


def run(csv_path: Path, apply: bool) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    print(f"Reading {csv_path} …")
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
    if "pair_id" not in df.columns:
        raise ValueError("CSV has no pair_id column — cannot match rows.")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.pair_id, u.record_id::text AS record_id,
                       u.doi_o, u.study_o, u.year_o, u.url_o, u.ref_o,
                       m.authors_o
                FROM unvalidated u
                LEFT JOIN record_metadata m ON m.record_id = u.record_id
                WHERE u.pair_id IS NOT NULL
                """
            )
            current = {r["pair_id"]: r for r in cur.fetchall()}
            print(f"  Rows in DB:        {len(current)}")
            print(f"  Rows in CSV:       {len(df)}")

            field_changes = {f: 0 for f in list(_UNVAL_FIELDS) + ["authors_o"]}
            rows_changed = 0
            samples = []

            for _, row in df.iterrows():
                pid = _s(row.get("pair_id"))
                if not pid or pid not in current:
                    continue
                cur_row = current[pid]

                unval_set, diffs = {}, []
                for col, getter in _UNVAL_FIELDS.items():
                    new_v = getter(row)
                    old_v = _s(cur_row.get(col))
                    if new_v and new_v != old_v:
                        unval_set[col] = new_v
                        field_changes[col] += 1
                        diffs.append((col, old_v, new_v))

                new_authors = _s(row.get("authors_o"))
                old_authors = _s(cur_row.get("authors_o"))
                authors_changed = bool(new_authors and new_authors != old_authors)
                if authors_changed:
                    field_changes["authors_o"] += 1
                    diffs.append(("authors_o", old_authors, new_authors))

                if not unval_set and not authors_changed:
                    continue
                rows_changed += 1
                if len(samples) < 12:
                    samples.append((pid, diffs))

                if apply:
                    if unval_set:
                        sets = ", ".join(f"{c} = %s" for c in unval_set)
                        cur.execute(
                            f"UPDATE unvalidated SET {sets} WHERE pair_id = %s",
                            list(unval_set.values()) + [pid],
                        )
                    if authors_changed:
                        cur.execute(
                            "UPDATE record_metadata SET authors_o = %s WHERE record_id = %s",
                            (new_authors, cur_row["record_id"]),
                        )

        print(f"\n  Rows that {'changed' if apply else 'would change'}: {rows_changed}")
        for f, n in field_changes.items():
            if n:
                print(f"    {f:10} {n}")

        print("\n  Sample changes:")
        for pid, diffs in samples:
            print(f"    pair_id {pid}")
            for col, old, new in diffs:
                old_s = (old[:60] + "…") if len(old) > 60 else old
                new_s = (new[:60] + "…") if len(new) > 60 else new
                print(f"      {col}: {old_s!r}  ->  {new_s!r}")

        if apply:
            conn.commit()
            print("\n[applied] Changes committed.")
        else:
            conn.rollback()
            print("\n[dry-run] No changes written. Re-run with --apply to commit.")
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Refresh original-study references on existing DB rows from a CSV.")
    ap.add_argument("csv_path", nargs="?", default=str(_DEFAULT_CSV), help="Path to the extracted CSV")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = ap.parse_args()
    run(Path(args.csv_path), apply=args.apply)
