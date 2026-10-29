"""
backfill_flora_history.py — Fill in the FLoRA dataset's size for the days before this
site's pipeline measured it, from the flora.csv published on each of those days.

The public dataset page plots flora_dataset_history.total_rows. This pipeline first
recorded it on 13 Sep 2026; before that, the published FLoRA dataset was the
flora.csv that forrtproject/fred-data regenerates almost daily. The size on a past
day is the row count of the version published that day — a count of what was
published, not today's rules applied to older records. Each stretch has its own
source, and a mode of this script for it:

    11 Nov 2025 - 13 Jan 2026   the old pipeline's root flora.csv    --before-launch
    14 Jan - 19 Jan 2026        nothing was published: no counts
    20 Jan - 15 Mar 2026        output/flora.csv, a pull request's   --pull-snapshots
                                kept history
    16 Mar - 20 May 2026        fred-data's README.md update log     --readme
    21 May - 12 Sep 2026        output/flora.csv, git history        (the default)

By default, for each day from the validator's launch (its first account) to the day
before the first pipeline-measured day, a day with a new output/flora.csv version
gets that day's last version's total, replications and reproductions. The launch day
itself gets the version in force at its end. Days without a new version are left
out: the page carries the previous figure forward to them, as it does between
pipeline runs. Those rows are marked measured_by = 'flora.csv'. Days are UTC days of
the commit time. fred-data's history has since been rewritten: output/flora.csv now
has versions from 23 Oct 2026 on only.

--before-launch reaches back to the old fred-data pipeline, the flora.csv it
published at the repository root from 11 Nov 2025 until the 13 Jan 2026
restructure moved it to archive/. Its rows are marked measured_by = 'fred-data' and
go only to days before the launch. release/latest_data/flora.csv and
archive/flora.csv only ever held versions of the root file (the same git blobs), so
they are not read: counting them would repeat a version as a new count. Versions
before 13 Jan 2026 have no type column, so their replications and reproductions are
stored as NULL, not 0. output/flora.csv first appeared on 20 Jan 2026, so 14-19 Jan
had no published file and get no count.

Between the two, output/flora.csv's own early versions are gone from fred-data's
branches, but two records of them survive. Both modes count the same file as the
default, so their rows are marked measured_by = 'flora.csv' too, and they go only to
days before the launch:

--readme reads the update log in fred-data's README.md, the table
| Date | Total | Replications | Reproductions | between <!-- FLORA_UPDATE_LOG_START -->
and <!-- FLORA_UPDATE_LOG_END -->: a row for each day fred-data's job ran, from
16 Mar 2026 on. Each row before the launch is that day's count; a day the log
lacks (25-28 Apr 2026) gets no row and the page carries the day before to it. On
the days it shares with the git history it gives the same figures. It reads
<fred-data>/README.md, or README.md at HEAD in a --no-checkout clone, and stops if
the markers or the table are missing.

--pull-snapshots counts the output/flora.csv versions reachable from fred-data's
refs/pull/102/head (17 Mar 2026), which GitHub keeps although the branches were
rewritten: from the file's first version (20 Jan 2026) to the day before the
README log's first day, dated like the default mode. Those versions hold records
that were deleted on purpose, so the ref is never fetched into the --fred-data
clone: it goes into a temporary bare repository, without file contents until
count() reads a version, and that repository is deleted when the run ends. Only
counts leave it. --pull-remote and --pull-ref name another repository or ref.

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
    python backfill_flora_history.py --fred-data ../fred-data --readme                 # dry run
    python backfill_flora_history.py --fred-data ../fred-data --readme --apply
    python backfill_flora_history.py --fred-data ../fred-data --pull-snapshots         # dry run
    python backfill_flora_history.py --fred-data ../fred-data --pull-snapshots --apply

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import contextlib
import csv
import datetime as dt
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile

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

README = "README.md"               # fred-data's, with the update log
LOG_START = "<!-- FLORA_UPDATE_LOG_START -->"
LOG_END = "<!-- FLORA_UPDATE_LOG_END -->"
LOG_HEADER = ["Date", "Total", "Replications", "Reproductions"]

# output/flora.csv's versions from 20 Jan to 17 Mar 2026 are gone from fred-data's
# branches; GitHub still serves this pull request's head, and its history has them.
PULL_REMOTE = "https://github.com/forrtproject/fred-data.git"
PULL_REF = "refs/pull/102/head"


def versions(repo: str, path: str = FLORA_CSV, rev: str = "HEAD") -> list:
    """Every published version of `path` in the history of `rev`, as (UTC commit
    time, sha), oldest first.

    Each commit that added or changed the file, on every branch merged in: git's
    default history would follow the side of the 13 Jan 2026 merge that moved the
    root flora.csv away, and lose its last version. Merges are left out, since they
    only bring in a version already listed, and so is the commit that deleted it.

    Sorted by commit time rather than taken in log order: git lists a merge's
    parents topologically, which is not always chronological. Commits in the same
    second keep git's topological order (--topo-order lists a child before its
    parent), so the later of the two still counts as the day's last."""
    out = subprocess.run(["git", "-C", repo, "log", "--topo-order", "--full-history",
                          "--no-merges", "--diff-filter=AM", "--format=%ct %H", rev, "--", path],
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


@contextlib.contextmanager
def fetched(remote: str = PULL_REMOTE, ref: str = PULL_REF):
    """A temporary bare repository holding `ref` of `remote` (under the same name),
    deleted on leaving, whatever happens inside.

    Fetched without file contents: versions() reads commits and trees only, and
    count() brings in each version it reads, into this repository alone. Those
    versions hold records that were deleted on purpose, so they never go into the
    --fred-data clone, and nothing of them but counts outlives the run."""
    repo = tempfile.mkdtemp(prefix="fred-data-pull-")
    try:
        def git(*args):
            done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
            if done.returncode:
                raise SystemExit(f"git {args[0]} {ref} from {remote} failed: {done.stderr.strip()}")

        git("init", "-q", "--bare")
        # No background maintenance: it could outlive the run and hold the directory.
        git("config", "gc.auto", "0")
        git("config", "maintenance.auto", "false")
        git("remote", "add", "origin", remote)
        git("fetch", "-q", "--no-tags", "--filter=blob:none", "origin", f"+{ref}:{ref}")
        yield repo
    finally:
        _remove_tree(repo)


def _remove_tree(path: str) -> None:
    """Delete `path` whatever its files' attributes: git writes pack and object files
    read-only, which makes a plain rmtree fail on Windows. Never raises, so it cannot
    hide the error that ended the run; the last resort is to say where the directory
    is, as it holds old versions."""
    def writable(func, target, *_):
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)
    for _ in range(3):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=writable)
            else:
                shutil.rmtree(path, onerror=writable)
        except OSError:
            continue
        if not os.path.exists(path):
            return
    print(f"warning: could not delete {path}: delete it by hand "
          "(it holds old fred-data versions).", file=sys.stderr)


def readme_text(repo: str) -> str:
    """fred-data's README.md: the checked-out file, or HEAD's in a --no-checkout clone."""
    path = os.path.join(repo, README)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    shown = subprocess.run(["git", "-C", repo, "show", f"HEAD:{README}"], capture_output=True)
    if shown.returncode:
        raise SystemExit(f"{path} is missing, and so is {README} at the clone's HEAD.")
    return shown.stdout.decode("utf-8-sig")


def update_log(text: str) -> list:
    """(day, total, replications, reproductions) for each row of the update log in
    fred-data's README.md, oldest first: the | Date | Total | Replications |
    Reproductions | table between LOG_START and LOG_END. ValueError if the markers or
    the table are missing, a row does not read, or a day comes twice."""
    start, end = text.find(LOG_START), text.find(LOG_END)
    if start < 0 or end < start:
        raise ValueError(f"no update log: {LOG_START} ... {LOG_END} not found")
    header, rows = False, {}
    for line in text[start + len(LOG_START):end].splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not header:
            header = cells == LOG_HEADER
            continue
        if all(c and set(c) <= set("-:") for c in cells):
            continue                                       # the |------|---:| line
        try:
            day = dt.date.fromisoformat(cells[0])
            total, replications, reproductions = (int(c.replace(",", "")) for c in cells[1:])
        except ValueError:
            raise ValueError(f"update log row does not read: {line}") from None
        if day in rows:
            raise ValueError(f"update log has {day} twice")
        rows[day] = (day, total, replications, reproductions)
    if not header:
        raise ValueError("update log has no | " + " | ".join(LOG_HEADER) + " | table")
    if not rows:
        raise ValueError("update log table has no rows")
    return sorted(rows.values())


def readme_log(repo: str) -> list:
    """update_log() of the clone's README.md, stopping the run with the reason if it
    does not read."""
    try:
        return update_log(readme_text(repo))
    except ValueError as error:
        raise SystemExit(f"{os.path.join(repo, README)}: {error}") from None


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


def write(cur, rows: list, measured_by: str = "flora.csv", before_launch: bool = False) -> int:
    """Insert (day, total, replications, reproductions) on days with no row yet.

    Never changes a row that exists: not a pipeline count, not a count this script
    wrote earlier (their fred-data versions are gone, so a re-run could only lose
    them), not a day seeded with source records. Never writes from the pipeline's
    first day on, not even to a day it skipped (that would put a flora.csv count
    between two pipeline counts). The old pipeline's counts, and any `before_launch`
    ones (the README log's, the pull request's), go only to days before the launch,
    and nowhere while no launch is recorded."""
    first = first_pipeline_day(cur)
    before_launch = before_launch or measured_by == OLD
    launch = launch_day(cur) if before_launch else None
    written = 0
    for day, total, replications, reproductions in rows:
        if first and day >= first:
            continue
        if before_launch and (launch is None or day >= launch):
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


def _day_before(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=1)


def counts(args, launch, first) -> tuple:
    """(heading, since, until, rows) for the mode `args` asks for. Each row is (day,
    total, replications, reproductions, where it was read). since or until is None,
    or since > until, when there is nothing to backfill."""
    latest = _day_before(first) if first else dt.date.max   # never the pipeline's days
    before = _day_before(launch) if launch else None        # the last day before the launch
    if args.readme:
        log = readme_log(args.fred_data)
        since = args.since or log[0][0]
        until = min(args.until or dt.date.max, before, latest) if before else None
        rows = [(*row, "README") for row in log if until and since <= row[0] <= until]
        return f"{README} update log", since, until, rows
    if args.pull_snapshots:
        heading = f"{FLORA_CSV} sizes in {args.pull_ref}"
        # Up to the README log, which takes over from its first day.
        log = readme_log(args.fred_data)
        until = (min(args.until or dt.date.max, _day_before(log[0][0]), before, latest)
                 if before else None)
        if until is None or (args.since and args.since > until):
            return heading, args.since, until, []
        with fetched(args.pull_remote, args.pull_ref) as repo:
            found = versions(repo, FLORA_CSV, args.pull_ref)
            since = args.since or (found[0][0].date() if found else None)
            if since is None or since > until:
                return heading, since, until, []
            return heading, since, until, [(day, *count(repo, sha), sha[:7])
                                           for day, sha in plan(found, since, until)]
    path = OLD_FLORA_CSV if args.before_launch else FLORA_CSV
    found = versions(args.fred_data, path)
    if args.before_launch:
        since = args.since or (found[0][0].date() if found else None)
        # The restructure moved the root flora.csv away the day of its last
        # version: no day after it had one, so a later --since gets no row.
        until = (min(args.until or dt.date.max, before, found[-1][0].date())
                 if before and found else None)
    else:
        since = args.since or launch
        until = args.until or dt.datetime.now(dt.timezone.utc).date()
    if until:
        until = min(until, latest)
    if since is None or until is None or since > until:
        return f"{path} sizes", since, until, []
    return f"{path} sizes", since, until, [(day, *count(args.fred_data, sha, path), sha[:7])
                                           for day, sha in plan(found, since, until)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fred-data", required=True,
                        help="a local clone of forrtproject/fred-data (--no-checkout is enough); "
                             "--readme and --pull-snapshots read only its README.md")
    parser.add_argument("--since", type=dt.date.fromisoformat,
                        help="first day (default: the validator's launch, its first account; "
                             "with --before-launch or --pull-snapshots, the first version; "
                             "with --readme, the log's first day)")
    parser.add_argument("--until", type=dt.date.fromisoformat,
                        help="last day (default and latest: the day before the pipeline's "
                             "first measurement; with --before-launch, the root flora.csv's last "
                             "day, before the launch; with --readme, the day before the launch; "
                             "with --pull-snapshots, the day before the README log's first day)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--before-launch", action="store_true",
                      help="count the old pipeline's root flora.csv (Nov 2025 - Jan 2026) "
                           "for the days before the launch")
    mode.add_argument("--readme", action="store_true",
                      help="take the counts in the update log of fred-data's README.md "
                           "(16 Mar - 20 May 2026) for the days before the launch")
    mode.add_argument("--pull-snapshots", action="store_true",
                      help=f"count the {FLORA_CSV} versions reachable from --pull-ref (20 Jan - "
                           "15 Mar 2026) for the days before the README log; the ref is fetched "
                           "into a temporary repository, deleted afterwards, never into --fred-data")
    parser.add_argument("--pull-remote", default=PULL_REMOTE,
                        help="with --pull-snapshots: the repository to fetch from (default: %(default)s)")
    parser.add_argument("--pull-ref", default=PULL_REF,
                        help="with --pull-snapshots: the ref whose history to count (default: %(default)s)")
    parser.add_argument("--apply", action="store_true", help="write the rows; otherwise a dry run")
    args = parser.parse_args()
    if not args.pull_snapshots and (args.pull_remote, args.pull_ref) != (PULL_REMOTE, PULL_REF):
        parser.error("--pull-remote and --pull-ref go with --pull-snapshots")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set in the environment or .env")
    measured_by = OLD if args.before_launch else "flora.csv"
    before_launch = args.before_launch or args.readme or args.pull_snapshots
    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=not args.apply)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            launch, first = launch_day(cur), first_pipeline_day(cur)
            heading, since, until, rows = counts(args, launch, first)
            if since is None or until is None or since > until:
                print(f"Nothing to backfill (from {since} to {until}).")
                return 0
            kept = taken(cur, [row[0] for row in rows])
            print(f"{heading} from {since} to {until}"
                  + (f" (the validator launched on {launch})" if before_launch else "")
                  + (f" (the pipeline measured from {first} on)" if first else "") + ":")
            for day, total, reps, repros, where in rows:
                split = (f"({reps} replications, {repros} reproductions)" if reps is not None
                         else "(no type column)")
                print(f"  {day}  {total:>5} records  {split}  {where}"
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
            written = write(cur, [row[:4] for row in rows], measured_by, before_launch)
        conn.commit()
        print(f"\n{written} day(s) written; days that already had a row were left alone.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
