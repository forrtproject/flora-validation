"""
update_originals.py — Refresh the ORIGINAL-study references on rows already in
the DB, from a (new) extracted CSV. Matches by pair_id.

The nightly import (csv_to_db.py) is insert-only — it skips rows already in the
DB — so corrected original references in a new CSV never reach existing rows.
This script fills that gap.

What it touches (extracted source values only):
    unvalidated:      doi_o, study_o (CSV title_o), year_o, url_o, ref_o
    record_metadata:  authors_o, doi_o_verification

What it deliberately NEVER touches:
    final_* values, validator_1/2/llm blobs, validation_status, assignments —
    i.e. any human/consensus decision. This only refreshes the raw references.

Rules:
    - Only rows whose pair_id already exists in the DB are updated.
    - A field is overwritten only when the new CSV value is non-empty AND differs
      (so a blank CSV cell never wipes a good existing value) — EXCEPT doi_o,
      which the extractor can legitimately correct TO blank (a book/chapter/
      pre-DOI paper the extractor has now verified has no DOI, replacing an
      earlier wrong guess). That blank is trusted only when this CSV pull's row
      carries doi_o_verification == 'no_doi' — an explicit verification, not
      just an empty cell — so a genuinely incomplete pull still can't wipe a
      good DOI.

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

from csv_to_db import _s, _url_o, _flag_ambiguous_doi_o_titles, _note_ambiguous_original

load_dotenv()

_DEFAULT_CSV = Path(__file__).parent / "data" / "extracted_latest.csv"

# unvalidated original-side columns ← CSV source
_UNVAL_FIELDS = {
    "doi_o":   lambda r: _s(r.get("doi_o")),
    "study_o": lambda r: _s(r.get("title_o")),
    "year_o":  lambda r: _s(r.get("year_o")),
    # CSV url_o preferred (DOI-less originals carry an OpenAlex link there),
    # else derived from doi_o — same rule as the import (csv_to_db._url_o).
    "url_o":   lambda r: _url_o(r),
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
                       u.doi_r, u.study_r,
                       u.doi_o, u.study_o, u.year_o, u.url_o, u.ref_o,
                       m.authors_o, m.doi_o_verification
                FROM unvalidated u
                LEFT JOIN record_metadata m ON m.record_id = u.record_id
                WHERE u.pair_id IS NOT NULL
                """
            )
            current = {r["pair_id"]: r for r in cur.fetchall()}
            print(f"  Rows in DB:        {len(current)}")
            print(f"  Rows in CSV:       {len(df)}")

            field_changes = {f: 0 for f in list(_UNVAL_FIELDS) + ["authors_o", "doi_o_verification"]}
            rows_changed = 0
            samples = []
            post_state = {}

            for _, row in df.iterrows():
                pid = _s(row.get("pair_id"))
                if not pid or pid not in current:
                    continue
                cur_row = current[pid]

                unval_set, diffs = {}, []

                # doi_o_verification computed first: doi_o's own blank-is-valid
                # exception (below) depends on it.
                new_doi_verif = _s(row.get("doi_o_verification"))
                old_doi_verif = _s(cur_row.get("doi_o_verification"))
                doi_verif_changed = bool(new_doi_verif and new_doi_verif != old_doi_verif)
                if doi_verif_changed:
                    field_changes["doi_o_verification"] += 1
                    diffs.append(("doi_o_verification", old_doi_verif, new_doi_verif))

                for col, getter in _UNVAL_FIELDS.items():
                    new_v = getter(row)
                    old_v = _s(cur_row.get(col))
                    # doi_o's blank IS a real correction when the extractor has
                    # verified there's no DOI — trust it even though new_v is
                    # empty. Every other field keeps the "blank never overwrites"
                    # safety rule, since a blank cell there is far more likely a
                    # missing-data gap in this CSV pull than genuine intent.
                    allow_blank = col == "doi_o" and new_doi_verif == "no_doi"
                    if (new_v or allow_blank) and new_v != old_v:
                        unval_set[col] = new_v
                        field_changes[col] += 1
                        diffs.append((col, old_v, new_v))

                new_authors = _s(row.get("authors_o"))
                old_authors = _s(cur_row.get("authors_o"))
                authors_changed = bool(new_authors and new_authors != old_authors)
                if authors_changed:
                    field_changes["authors_o"] += 1
                    diffs.append(("authors_o", old_authors, new_authors))

                meta_changed = authors_changed or doi_verif_changed
                if not unval_set and not meta_changed:
                    continue
                rows_changed += 1
                # Post-update view of this record, for the ambiguity check below —
                # a record only becomes DOI-less here, so it must be judged on
                # what it will look like AFTER this run, not before.
                post_state[pid] = {
                    "key": pid,
                    "record_id": cur_row["record_id"],
                    "doi_r": cur_row["doi_r"],
                    "study_r": cur_row["study_r"],
                    "doi_o": unval_set.get("doi_o", _s(cur_row.get("doi_o"))),
                    "study_o": unval_set.get("study_o", _s(cur_row.get("study_o"))),
                }
                if len(samples) < 12:
                    samples.append((pid, diffs))

                if apply:
                    if unval_set:
                        sets = ", ".join(f"{c} = %s" for c in unval_set)
                        cur.execute(
                            f"UPDATE unvalidated SET {sets} WHERE pair_id = %s",
                            list(unval_set.values()) + [pid],
                        )
                    if meta_changed:
                        meta_sets, meta_params = [], []
                        if authors_changed:
                            meta_sets.append("authors_o = %s"); meta_params.append(new_authors)
                        if doi_verif_changed:
                            meta_sets.append("doi_o_verification = %s"); meta_params.append(new_doi_verif)
                        meta_params.append(cur_row["record_id"])
                        cur.execute(
                            f"UPDATE record_metadata SET {', '.join(meta_sets)} WHERE record_id = %s",
                            meta_params,
                        )

            # A record only ever BECOMES DOI-less here, so flag against the state
            # this run leaves behind: changed rows as they will be, every other DB
            # row as it already is (a newly-blank doi_o must be compared with the
            # replication's untouched originals too, not just the ones in this CSV).
            ambiguous = _flag_ambiguous_doi_o_titles([
                post_state.get(pid, {
                    "key": pid, "record_id": r["record_id"],
                    "doi_r": r["doi_r"], "study_r": r["study_r"],
                    "doi_o": _s(r.get("doi_o")), "study_o": _s(r.get("study_o")),
                })
                for pid, r in current.items()
            ])
            # Only report/annotate records this run actually touched — pre-existing
            # ambiguity elsewhere isn't this run's business (and was flagged at import).
            newly_ambiguous = {pid: why for pid, why in ambiguous.items() if pid in post_state}
            if newly_ambiguous and apply:
                for pid, why in newly_ambiguous.items():
                    _note_ambiguous_original(cur, post_state[pid]["record_id"], why)

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

        if newly_ambiguous:
            print(f"\n  ⚠  {len(newly_ambiguous)} record(s) now have a DOI-less original with a "
                  f"blank or duplicate title.")
            print(f"     {'Flagged in admin_notes for review' if apply else 'Would be flagged in admin_notes'}. pair_ids:")
            for pid in list(newly_ambiguous)[:20]:
                print(f"       {pid}")
            if len(newly_ambiguous) > 20:
                print(f"       … and {len(newly_ambiguous) - 20} more")

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
