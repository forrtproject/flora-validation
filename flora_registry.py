"""flora_registry.py — stable, citable ids for rows of the FLoRA product.

The id a FLoRA row carries is the source record's own display_id (REPL-000397,
FRED-000001, VAL-000012), copied once and pinned — one namespace shared with the
Source Records tab, and stable even when the dedup survivor changes underneath it.

`transform_sources.build()` is a pure function of source_records: it derives
outcomes, cleans DOIs and deduplicates on every run. Reproducible, but anonymous —
nothing in a produced row can be cited, and nothing links it back to the record it
came from. This module supplies both, and is the only thing that writes to
`flora_records`.

IDENTITY FOLLOWS PROVENANCE, NOT CONTENT
----------------------------------------
A FLoRA row is "the same record" when it derives from the same source_records row.
Not when its doi_o|doi_r key matches — that key changes the moment a reviewer
corrects a DOI, which would silently mint a new id for a record that has not
changed. source_records ids are UUIDs on an insert-only table, so they are the one
thing in the system that genuinely does not move.

THE SURVIVOR CAN CHANGE
-----------------------
The transform collapses duplicates and keeps the first row by display_id. A
reviewer ruling that survivor a duplicate promotes a different row to survivor —
same paper, same FLoRA record, different primary source id. So matching falls back
to any overlap between the row's source ids (survivor + absorbed) and those an
existing flora_record already claims, and re-points the record rather than issuing
a new id.

RETIREMENT, NEVER DELETION
--------------------------
A row that stops appearing (excluded, ruled a duplicate, merged away) is stamped
`retired_at`. Ids are never reused and rows are never deleted: a published id must
keep resolving to what it meant, even when the record behind it is gone.

Usage:
    python flora_registry.py            # refresh
    python flora_registry.py --dry-run  # report, write nothing
"""
import argparse
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
import transform_sources

load_dotenv()
use_utf8_output()

def _mint_ids(cur, source_ids: list, taken: set) -> dict:
    """flora_id for each new row: the source record's own display_id, pinned.

    There is no separate FLORA- series. The id a FLoRA row carries is the id already
    visible in the Source Records tab — REPL-000397, FRED-000001, VAL-000012 — so
    there is one namespace to learn instead of two, and tracing a published row back
    needs no lookup table.

    "Pinned" is the whole point: the id is copied ONCE, at first assignment, and then
    lives in flora_records. It does not follow the primary source record afterwards.
    If a reviewer rules the survivor a duplicate and a different row is promoted, the
    record keeps the id it was published under and only `primary_source_record_id`
    moves. Reading display_id live instead would change 207 rows' identity on a
    reviewer's decision, which is what an id must never do.

    Collisions are possible but rare, and only via that same route: record R pins
    REPL-000001 and later re-points elsewhere, then REPL-000001 returns as its own
    row (a reviewer ruled it 'distinct'). The display_id is then already spoken for,
    so the newcomer takes a -R2 suffix rather than colliding on the unique index or
    silently stealing an id someone may have cited.
    """
    if not source_ids:
        return {}
    cur.execute(
        "SELECT record_id::text AS record_id, display_id FROM source_records "
        "WHERE record_id = ANY(%s::uuid[])",
        (source_ids,),
    )
    display = {r["record_id"]: r["display_id"] for r in cur.fetchall()}

    minted = {}
    for source_id in source_ids:
        base = display.get(source_id) or f"UNKNOWN-{source_id[:8]}"
        candidate, n = base, 1
        while candidate in taken:
            n += 1
            candidate = f"{base}-R{n}"
        taken.add(candidate)
        minted[source_id] = candidate
    return minted


def _load_existing(cur) -> tuple:
    """Returns (by_primary, by_any_source). The second maps EVERY source id a
    record claims — survivor and absorbed alike — so a changed survivor is still
    recognised as the same record."""
    cur.execute(
        """
        SELECT flora_record_id::text AS flora_record_id, flora_id,
               primary_source_record_id::text AS primary_source_record_id,
               -- ::text[] is load-bearing. psycopg2 has no uuid[] parser registered,
               -- so a bare uuid[] arrives as the raw '{a,b}' STRING — and iterating
               -- that yields single characters, silently producing a by_any index of
               -- punctuation. Every survivor change then looked like a brand-new
               -- record and minted a fresh id.
               merged_source_record_ids::text[] AS merged_source_record_ids,
               retired_at
        FROM flora_records
        """
    )
    by_primary, by_any = {}, {}
    for row in cur.fetchall():
        record = dict(row)
        by_primary[record["primary_source_record_id"]] = record
        for sid in [record["primary_source_record_id"], *(record["merged_source_record_ids"] or [])]:
            by_any.setdefault(str(sid), record)
    return by_primary, by_any


def backfill_source_history(cur, verbose: bool = True) -> int:
    """Seed past days from source_records.first_seen_at.

    Genuine history, not a reconstruction: source_records is insert-only and every
    row carries the timestamp it first landed, so the cumulative count on any past
    date is exactly what the table held that day.

    `total_rows` is left NULL for these days on purpose. The product's size then
    cannot be recovered — the exclusion and dedup rules that yield today's number
    did not exist — and inventing a curve would be worse than showing none.

    ON CONFLICT DO NOTHING: a day we actually recorded is never overwritten by a
    seeded estimate.
    """
    cur.execute(
        """
        INSERT INTO flora_dataset_history (recorded_on, source_rows)
        SELECT day, SUM(COUNT(*)) OVER (ORDER BY day)
        FROM (
            SELECT first_seen_at::date AS day FROM source_records
        ) t
        GROUP BY day
        ORDER BY day
        ON CONFLICT (recorded_on) DO NOTHING
        """
    )
    seeded = cur.rowcount
    if verbose and seeded:
        print(f"  history: seeded {seeded} past day(s) from source_records")
    return seeded


def record_history(cur, frame, verbose: bool = True) -> dict:
    """One row per day for the dataset-size chart, upserted.

    Recorded here rather than in transform_sources because that script is a pure
    function of the database and writes nothing back — and because refresh() has
    already built the frame, so this costs a single INSERT rather than a second
    two-second build.
    """
    total = len(frame)
    replications = int((frame["type"] == "replication").sum()) if total else 0
    reproductions = int((frame["type"] == "reproduction").sum()) if total else 0

    cur.execute("SELECT COUNT(*) AS n FROM source_records")
    source_rows = cur.fetchone()["n"]

    cur.execute(
        """
        INSERT INTO flora_dataset_history
            (recorded_on, total_rows, replications, reproductions, source_rows)
        VALUES (CURRENT_DATE, %s, %s, %s, %s)
        ON CONFLICT (recorded_on) DO UPDATE SET
            total_rows = EXCLUDED.total_rows,
            replications = EXCLUDED.replications,
            reproductions = EXCLUDED.reproductions,
            source_rows = EXCLUDED.source_rows,
            recorded_at = NOW()
        """,
        (total, replications, reproductions, source_rows),
    )
    if verbose:
        print(f"  history: {total} row(s) recorded for today "
              f"({replications} replication, {reproductions} reproduction)")
    return {"total_rows": total, "replications": replications,
            "reproductions": reproductions, "source_rows": source_rows}


def refresh(cur, dry_run: bool = False, verbose: bool = True) -> dict:
    def say(*args):
        if verbose:
            print(*args)

    frame = transform_sources.build(cur, verbose=False)
    if frame.empty:
        say("  transform produced no rows — nothing to register")
        return {"rows": 0, "assigned": 0, "rematched": 0, "unchanged": 0, "retired": 0}

    merged_map = frame.attrs.get("merged_record_ids", {})
    key_map = frame.attrs.get("dedup_key", {})
    by_primary, by_any = _load_existing(cur)

    assigned = rematched = unchanged = 0
    seen_flora_records = set()
    to_create, to_update = [], []

    for source_id in frame["source_record_id"]:
        merged = [str(m) for m in (merged_map.get(source_id) or [])]
        existing = by_primary.get(source_id)
        if existing is None:
            # Survivor changed: find a record already claiming any of these ids.
            for candidate_id in [source_id, *merged]:
                if candidate_id in by_any:
                    existing = by_any[candidate_id]
                    break
            if existing is not None:
                rematched += 1

        if existing is None:
            to_create.append((source_id, merged, key_map.get(source_id)))
            assigned += 1
        else:
            seen_flora_records.add(existing["flora_record_id"])
            to_update.append((existing["flora_record_id"], source_id, merged,
                              key_map.get(source_id)))
            unchanged += 1

    retired = [r["flora_record_id"] for r in by_primary.values()
               if r["flora_record_id"] not in seen_flora_records and r["retired_at"] is None]

    say(f"  transform rows:   {len(frame)}")
    say(f"  new ids to issue: {assigned}")
    say(f"  re-matched after a survivor change: {rematched}")
    say(f"  already registered: {unchanged - rematched}")
    say(f"  to retire: {len(retired)}")

    if dry_run:
        say("\n[dry-run] nothing written")
        return {"rows": len(frame), "assigned": assigned, "rematched": rematched,
                "unchanged": unchanged, "retired": len(retired)}

    if to_create:
        # Every id ever issued, live or retired — a retired one is still spoken for,
        # because it may already have been published.
        taken = {r["flora_id"] for r in by_primary.values()}
        minted = _mint_ids(cur, [source_id for source_id, _, _ in to_create], taken)
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO flora_records
                (flora_id, primary_source_record_id, merged_source_record_ids, dedup_key)
            VALUES (%s, %s, %s::uuid[], %s)
            """,
            [(minted[source_id], source_id, merged, key)
             for source_id, merged, key in to_create],
        )

    if to_update:
        # last_seen_at moves every run; retired_at clears because a row that came
        # back is live again and must keep the id it was published under.
        psycopg2.extras.execute_batch(
            cur,
            """
            UPDATE flora_records
               SET primary_source_record_id = %s,
                   merged_source_record_ids = %s::uuid[],
                   dedup_key = %s,
                   last_seen_at = NOW(),
                   retired_at = NULL
             WHERE flora_record_id = %s
            """,
            [(source_id, merged, key, flora_record_id)
             for flora_record_id, source_id, merged, key in to_update],
        )

    if retired:
        psycopg2.extras.execute_batch(
            cur,
            "UPDATE flora_records SET retired_at = NOW() WHERE flora_record_id = %s",
            [(rid,) for rid in retired],
        )

    backfill_source_history(cur, verbose=verbose)
    record_history(cur, frame, verbose=verbose)

    return {"rows": len(frame), "assigned": assigned, "rematched": rematched,
            "unchanged": unchanged, "retired": len(retired)}


def attach_ids(cur, frame):
    """Add the registered flora_id to an already-built frame, without writing.

    The read path (the FLoRA tab and its export) uses this: it must never assign an
    id as a side effect of someone opening a page. A row with no id yet shows blank
    until the next refresh.
    """
    if frame.empty:
        return frame
    cur.execute(
        "SELECT primary_source_record_id::text AS sid, flora_id FROM flora_records"
    )
    mapping = {r["sid"]: r["flora_id"] for r in cur.fetchall()}
    frame = frame.copy()
    frame["flora_id"] = frame["source_record_id"].map(mapping)
    return frame


def main() -> int:
    start_logging("flora-registry")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change; write nothing")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    print("=== FLoRA record registry ===")
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            stats = refresh(cur, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\n  registered: {stats['rows']} row(s); "
          f"{stats['assigned']} new id(s), {stats['retired']} retired")
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
