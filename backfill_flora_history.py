"""
backfill_flora_history.py — Fill in the FLoRA dataset's size for the days before this
site's pipeline measured it, from the flora.csv published on each of those days.

The public dataset page plots flora_dataset_history.total_rows. This pipeline first
recorded it on 13 Sep 2026; before that, the published FLoRA dataset was the
flora.csv that forrtproject/fred-data regenerates almost daily. The size on a past
day is the row count of the version published that day, from fred-data's git
history — a count of what was published, not today's rules applied to older records.

For each day from the validator's launch (its first account) to the day before the
first pipeline-measured day, a day with a new flora.csv version gets that day's last
version's total, replications and reproductions. The launch day itself gets the
version in force at its end. Days without a new version are left out: the page
carries the previous figure forward to them, as it does between pipeline runs.
Those rows are marked measured_by = 'flora.csv'. fred-data's history has since been
rewritten: output/flora.csv now has versions from 23 Oct 2026 on only.

--before-launch reaches back to the old fred-data pipeline, the flora.csv it
published at the repository root from 11 Nov 2025 until the 13 Jan 2026
restructure moved it to archive/. Its rows are marked measured_by = 'fred-data' and
go only to days before the launch. release/latest_data/flora.csv and
archive/flora.csv only ever held versions of the root file (the same git blobs), so
they are not read: counting them would repeat a version as a new count. Versions
before 13 Jan 2026 have no type column, so their replications and reproductions are
stored as NULL, not 0.

Only ever inserts. A day that already has a row (a pipeline count, a count this
script wrote earlier, a day seeded with source records) is never changed, so a
re-run cannot touch counts whose fred-data versions are gone.

A dry run unless given --apply.

Usage:
    git clone --no-checkout https://github.com/forrtproject/fred-data.git ../fred-data
    python backfill_flora_history.py --fred-data ../fred-data            # dry run
    python backfill_flora_history.py --fred-data ../fred-data --apply
    python backfill_flora_history.py --fred-data ../fred-data --since 2026-05-21 --until 2026-09-12
    python backfill_flora_history.py --fred-data ../fred-data --before-launch          # dry run
    python backfill_flora_history.py --fred-data ../fred-data --before-launch --apply

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
OLD_FLORA_CSV = "flora.csv"        # the old pipeline's, at the repository root
OLD = "fred-data"                  # measured_by for its counts


def versions(repo: str, path: str = FLORA_CSV) -> list:
    """Every published version of `path` as (UTC commit time, sha), oldest first.

    Each commit that added or changed the file, on every branch merged in: git's
    default history would follow the side of the 13 Jan 2026 merge that moved the
    root flora.csv away, and lose its last version. Merges are left out, since they
    only bring in a version already listed, and so is the commit that deleted it.

    Sorted by commit time rather than taken in log order: git lists a merge's
    parents topologically, which is not always chronological. Commits in the same
    second keep git's topological order (--topo-order lists a child before its
    parent), so the later of the two still counts as the day's last."""
    out = subprocess.run(["git", "-C", repo, "log", "--topo-order", "--full-history",
                          "--no-merges", "--diff-filter=AM", "--format=%ct %H", "--", path],
                         capture_output=True, text=True, check=True).stdout
    found = []
    for position, line in enumerate(out.splitlines()):
        stamp, sha = line.split()
        found.append((dt.datetime.fromtimestamp(int(stamp), dt.timezone.utc), -position, sha))
    return [(when, sha) for when, _, sha in sorted(found)]


def count(repo: str, sha: str, path: str = FLORA_CSV) -> tuple:
    """(records, replications, reproductions) in one flora.csv version. The type is
    in `type`, or `rep_type` in the old pipeline's last version; a version with
    neither gives (records, None, None): its split is unknown, not zero."""
    text = subprocess.run(["git", "-C", repo, "show", f"{sha}:{path}"],
                          capture_output=True, check=True).stdout.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    column = next((c for c in ("type", "rep_type") if c in (reader.fieldnames or ())), None)
    if column is None:
        return len(rows), None, None
    types = [(row.get(column) or "").strip().lower() for row in rows]
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


def takes_old_counts(cur) -> bool:
    """Whether the deployed schema takes the old pipeline's rows: 'fred-data' in the
    measured_by check. db_schema.sql widens it, and lets the split be NULL, when the
    site starts."""
    cur.execute("""SELECT 1 FROM pg_constraint
                   WHERE conrelid = to_regclass('flora_dataset_history')
                     AND conname = 'flora_dataset_history_measured_by_check'
                     AND pg_get_constraintdef(oid) LIKE '%fred-data%'""")
    return cur.fetchone() is not None


def taken(cur, days: list) -> set:
    """The days among `days` that already have a row, which a run leaves alone."""
    cur.execute("SELECT recorded_on FROM flora_dataset_history WHERE recorded_on = ANY(%s)",
                (list(days),))
    return {r["recorded_on"] for r in cur.fetchall()}


def write(cur, rows: list, measured_by: str = "flora.csv") -> int:
    """Insert (day, total, replications, reproductions) on days with no row yet.

    Never changes a row that exists: not a pipeline count, not a count this script
    wrote earlier (their fred-data versions are gone, so a re-run could only lose
    them), not a day seeded with source records. Never writes from the pipeline's
    first day on, not even to a day it skipped (that would put a flora.csv count
    between two pipeline counts). The old pipeline's counts go only to days before
    the launch, and nowhere while no launch is recorded."""
    first = first_pipeline_day(cur)
    launch = launch_day(cur) if measured_by == OLD else None
    written = 0
    for day, total, replications, reproductions in rows:
        if first and day >= first:
            continue
        if measured_by == OLD and (launch is None or day >= launch):
            continue
        cur.execute(
            """
            INSERT INTO flora_dataset_history
                (recorded_on, total_rows, replications, reproductions, source_rows, measured_by)
            VALUES (%s, %s, %s, %s,
                    (SELECT COUNT(*) FROM source_records
                     WHERE (first_seen_at AT TIME ZONE 'UTC')::date <= %s),
                    %s)
            ON CONFLICT (recorded_on) DO NOTHING
            """,
            (day, total, replications, reproductions, day, measured_by),
        )
        written += cur.rowcount
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fred-data", required=True,
                        help="a local clone of forrtproject/fred-data (--no-checkout is enough)")
    parser.add_argument("--since", type=dt.date.fromisoformat,
                        help="first day (default: the validator's launch, its first account; "
                             "with --before-launch, the old pipeline's first version)")
    parser.add_argument("--until", type=dt.date.fromisoformat,
                        help="last day (default and latest: the day before the pipeline's "
                             "first measurement; with --before-launch, the root flora.csv's last "
                             "day, before the launch)")
    parser.add_argument("--before-launch", action="store_true",
                        help="count the old pipeline's root flora.csv (Nov 2025 - Jan 2026) "
                             "for the days before the launch")
    parser.add_argument("--apply", action="store_true", help="write the rows; otherwise a dry run")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set in the environment or .env")
    path, measured_by = (OLD_FLORA_CSV, OLD) if args.before_launch else (FLORA_CSV, "flora.csv")
    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=not args.apply)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            launch, first = launch_day(cur), first_pipeline_day(cur)
            found = versions(args.fred_data, path)
            if args.before_launch:
                since = args.since or (found[0][0].date() if found else None)
                # The restructure moved the root flora.csv away the day of its last
                # version: no day after it had one, so a later --since gets no row.
                until = (min(args.until or dt.date.max, launch - dt.timedelta(days=1),
                             found[-1][0].date())
                         if launch and found else None)
            else:
                since = args.since or launch
                until = args.until or dt.datetime.now(dt.timezone.utc).date()
            if first and until:
                until = min(until, first - dt.timedelta(days=1))
            if since is None or until is None or since > until:
                print(f"Nothing to backfill (from {since} to {until}).")
                return 0
            chosen = plan(found, since, until)
            rows = [(day, *count(args.fred_data, sha, path)) for day, sha in chosen]
            kept = taken(cur, [day for day, _ in chosen])
            print(f"{path} sizes from {since} to {until}"
                  + (f" (the validator launched on {launch})" if args.before_launch else "")
                  + (f" (the pipeline measured from {first} on)" if first else "") + ":")
            for (day, sha), (_, total, reps, repros) in zip(chosen, rows):
                split = (f"({reps} replications, {repros} reproductions)" if reps is not None
                         else "(no type column)")
                print(f"  {day}  {total:>5} records  {split}  {sha[:7]}"
                      + ("  has a row: left alone" if day in kept else ""))
            if not args.apply:
                print(f"\n[dry run] {len(rows) - len(kept)} day(s) would be written. "
                      "Re-run with --apply.")
                return 0
            if not flora_registry.has_measured_by(cur):
                raise SystemExit("flora_dataset_history.measured_by is missing: deploy this "
                                 "version first (the app adds the column when it starts).")
            if args.before_launch and not takes_old_counts(cur):
                raise SystemExit("flora_dataset_history does not take 'fred-data' rows yet: "
                                 "deploy this version first (the app widens it when it starts).")
            written = write(cur, rows, measured_by)
        conn.commit()
        print(f"\n{written} day(s) written; days that already had a row were left alone.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
