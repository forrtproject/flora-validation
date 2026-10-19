"""
backfill_flora_history.py — Fill in the FLoRA dataset's size for the days before this
site's pipeline measured it, from the flora.csv published on each of those days.

The public dataset page plots flora_dataset_history.total_rows. This pipeline first
recorded it on 13 Sep 2026; before that, the published FLoRA dataset was the
flora.csv that forrtproject/fred-data regenerates almost daily. Its git history
keeps every version, so the size on a past day is the row count of the version
published that day — a count of what was published, not today's rules applied to
older records.

For each day from the validator's launch (its first account) to the day before the
first pipeline-measured day, a day with a new flora.csv version gets that day's last
version's total, replications and reproductions. The launch day itself gets the
version in force at its end. Days without a new version are left out: the page
carries the previous figure forward to them, as it does between pipeline runs.

Rows are marked measured_by = 'flora.csv'. A day the pipeline measured is never
overwritten, and a re-run replaces only rows this script wrote.

A dry run unless given --apply.

Usage:
    git clone --no-checkout https://github.com/forrtproject/fred-data.git ../fred-data
    python backfill_flora_history.py --fred-data ../fred-data            # dry run
    python backfill_flora_history.py --fred-data ../fred-data --apply
    python backfill_flora_history.py --fred-data ../fred-data --since 2026-05-21 --until 2026-09-12

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import csv
import datetime as dt
import io
import os
import subprocess

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import flora_registry
from console_encoding import use_utf8_output

load_dotenv()
use_utf8_output()

FLORA_CSV = "output/flora.csv"


def versions(repo: str) -> list:
    """Every published flora.csv as (UTC commit time, sha), oldest first.

    Sorted by commit time rather than taken in log order: git lists a merge's
    parents topologically, which is not always chronological. Commits in the same
    second keep git's topological order (--topo-order lists a child before its
    parent), so the later of the two still counts as the day's last."""
    out = subprocess.run(["git", "-C", repo, "log", "--topo-order", "--format=%ct %H",
                          "--", FLORA_CSV],
                         capture_output=True, text=True, check=True).stdout
    found = []
    for position, line in enumerate(out.splitlines()):
        stamp, sha = line.split()
        found.append((dt.datetime.fromtimestamp(int(stamp), dt.timezone.utc), -position, sha))
    return [(when, sha) for when, _, sha in sorted(found)]


def count(repo: str, sha: str) -> tuple:
    """(records, replications, reproductions) in one flora.csv version."""
    text = subprocess.run(["git", "-C", repo, "show", f"{sha}:{FLORA_CSV}"],
                          capture_output=True, check=True).stdout.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    types = [(row.get("type") or "").strip().lower() for row in rows]
    return len(rows), types.count("replication"), types.count("reproduction")


def plan(found: list, since: dt.date, until: dt.date) -> list:
    """The (day, sha) to record: the version in force at the end of `since`, then
    each later day up to `until` with a new version, taking that day's last one."""
    by_day = {}
    in_force = None
    for when, sha in found:
        day = when.date()
        if day <= since:
            in_force = sha
        elif day <= until:
            by_day[day] = sha                      # oldest first: the last one stays
    chosen = ([(since, in_force)] if in_force else []) + sorted(by_day.items())
    return chosen


def first_pipeline_day(cur):
    # Before the column is deployed, every recorded total is a pipeline run.
    pipeline = ("AND COALESCE(measured_by, 'pipeline') = 'pipeline'"
                if flora_registry.has_measured_by(cur) else "")
    cur.execute("SELECT MIN(recorded_on) AS day FROM flora_dataset_history "
                f"WHERE total_rows IS NOT NULL {pipeline}")
    return cur.fetchone()["day"]


def launch_day(cur):
    cur.execute("SELECT (MIN(created_at) AT TIME ZONE 'UTC')::date AS day FROM validators")
    return cur.fetchone()["day"]


def write(cur, rows: list) -> int:
    """Upsert (day, total, replications, reproductions). Never writes from the
    pipeline's first day on, not even to a day it skipped (that would put a flora.csv
    count between two pipeline counts); re-running replaces only this script's own rows."""
    first = first_pipeline_day(cur)
    written = 0
    for day, total, replications, reproductions in rows:
        if first and day >= first:
            continue
        cur.execute(
            """
            INSERT INTO flora_dataset_history
                (recorded_on, total_rows, replications, reproductions, source_rows, measured_by)
            VALUES (%s, %s, %s, %s,
                    (SELECT COUNT(*) FROM source_records
                     WHERE (first_seen_at AT TIME ZONE 'UTC')::date <= %s),
                    'flora.csv')
            ON CONFLICT (recorded_on) DO UPDATE SET
                total_rows = EXCLUDED.total_rows,
                replications = EXCLUDED.replications,
                reproductions = EXCLUDED.reproductions,
                measured_by = 'flora.csv',
                recorded_at = NOW()
            WHERE flora_dataset_history.total_rows IS NULL
               OR flora_dataset_history.measured_by = 'flora.csv'
            """,
            (day, total, replications, reproductions, day),
        )
        written += cur.rowcount
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fred-data", required=True,
                        help="a local clone of forrtproject/fred-data (--no-checkout is enough)")
    parser.add_argument("--since", type=dt.date.fromisoformat,
                        help="first day (default: the validator's launch, its first account)")
    parser.add_argument("--until", type=dt.date.fromisoformat,
                        help="last day (default and latest: the day before the pipeline's "
                             "first measurement)")
    parser.add_argument("--apply", action="store_true", help="write the rows; otherwise a dry run")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set in the environment or .env")
    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=not args.apply)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            since = args.since or launch_day(cur)
            first = first_pipeline_day(cur)
            until = args.until or dt.datetime.now(dt.timezone.utc).date()
            if first:
                until = min(until, first - dt.timedelta(days=1))
            if since is None or since > until:
                print(f"Nothing to backfill (from {since} to {until}).")
                return 0
            chosen = plan(versions(args.fred_data), since, until)
            rows = [(day, *count(args.fred_data, sha)) for day, sha in chosen]
            print(f"flora.csv sizes from {since} to {until}"
                  + (f" (the pipeline measured from {first} on)" if first else "") + ":")
            for (day, sha), (_, total, reps, repros) in zip(chosen, rows):
                print(f"  {day}  {total:>5} records  ({reps} replications, "
                      f"{repros} reproductions)  {sha[:7]}")
            if not args.apply:
                print(f"\n[dry run] {len(rows)} day(s) would be written. Re-run with --apply.")
                return 0
            if not flora_registry.has_measured_by(cur):
                raise SystemExit("flora_dataset_history.measured_by is missing: deploy this "
                                 "version first (the app adds the column when it starts).")
            written = write(cur, rows)
        conn.commit()
        print(f"\n{written} day(s) written; days the pipeline measured were left alone.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
