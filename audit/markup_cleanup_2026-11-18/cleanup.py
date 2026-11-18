"""Strip markup from the rows the nightly import no longer refreshes.

Since 85341b3 the import cleans title_r, title_o and abstract_r of every pair the
extractor still ships. Three kinds of value keep their tags:

  - unvalidated rows of pairs the extractor no longer ships
  - unvalidated final_title_r / final_title_o / final_abstract_r, written once at
    consensus and never touched by the import
  - validated title_r / title_o / abstract_r, copied from those final values

This runs the same transform_sources.clean_text over them (titles on one line,
abstracts keeping their paragraph breaks), so a stored value reads as a freshly
imported one does. Only values holding a tag are touched.

  check     lists every value that would change and writes the plan to plan.json
            beside this script. Writes nothing to the database.
  apply     changes one record at a time: its unvalidated and validated rows, and
            an entry in the admin audit log (security_events) in the admin's name,
            in one transaction. A value that changed since it was read leaves the
            record alone. Safe to run again.
  rollback  undoes records from the log (apply_log.jsonl beside this script), each
            in one transaction with its own audit entry: all of them, or those named
            with --record. A value that changed since apply is left as it is.

A record is skipped whole if its cleaned validated row would take the identity
(doi_r, study_r, title_r, doi_o or original_key, study_o, title_o) of another
validated row: that is a duplicate for an admin to merge, not to write over.

These are the validation tables, which the FLoRA tab does not show: its records
are rebuilt from them and cleaned on the way, so FLoRA titles read the same before
and after. Reads DATABASE_URL from the environment or the repository's .env.
Refuses to write between 02:00 and 05:00 UTC, while the nightly jobs run.

See README.md beside this script for what was applied and when.

usage: python audit/markup_cleanup_2026-11-18/cleanup.py check
       python audit/markup_cleanup_2026-11-18/cleanup.py apply --admin HANDLE
       python audit/markup_cleanup_2026-11-18/cleanup.py rollback --admin HANDLE [--record ID ...]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402
import psycopg2.errors  # noqa: E402
import psycopg2.extras  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from transform_sources import clean_text  # noqa: E402

TAG = r"</?[A-Za-z!?][^<>]*>"
TITLE_COLUMNS = {"title_r", "title_o", "final_title_r", "final_title_o"}
COLUMNS = {
    "unvalidated": ["title_r", "title_o", "abstract_r",
                    "final_title_r", "final_title_o", "final_abstract_r"],
    "validated": ["title_r", "title_o", "abstract_r"],
}
ROW_KEY = {"unvalidated": "record_id", "validated": "validated_record_id"}
PLAN = HERE / "plan.json"
DEFAULT_LOG = HERE / "apply_log.jsonl"
QUIET_HOURS_UTC = range(2, 5)
AGENT = "html-strip-cleanup"
CLEANED, RESTORED = "validation.markup_cleaned", "validation.markup_restored"

IDENTITY_SQL = """
    SELECT v.record_id::text
    FROM validated v
    WHERE v.validated_record_id <> %(row_key)s::uuid
      AND ((v.doi_r, v.study_r, v.title_r, v.doi_o, v.study_o, v.title_o)
           IS NOT DISTINCT FROM (%(doi_r)s, %(study_r)s, %(title_r)s,
                                 %(doi_o)s, %(study_o)s, %(title_o)s)
        OR (v.doi_r, v.study_r, v.title_r, v.original_key, v.study_o, v.title_o)
           IS NOT DISTINCT FROM (%(doi_r)s, %(study_r)s, %(title_r)s,
                                 %(original_key)s, %(study_o)s, %(title_o)s))
    LIMIT 1
"""


class Skip(Exception):
    """This record is left as it is; the message says why."""


def connect():
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (environment or .env).")
    return psycopg2.connect(url, application_name=AGENT,
                            cursor_factory=psycopg2.extras.RealDictCursor)


def cleaned(column, value):
    """What the import would store for this value now."""
    if column in TITLE_COLUMNS:
        return clean_text(value, one_line=True) or ""
    return clean_text(value) or ""


def plan(cur):
    """Every value the cleanup would change, grouped by record."""
    records = {}
    for table, columns in COLUMNS.items():
        key = ROW_KEY[table]
        where = " OR ".join(f"{c} ~ %(tag)s" for c in columns)
        cur.execute(f"SELECT {key}::text AS row_key, record_id::text AS record_id, "
                    f"{', '.join(columns)} FROM {table} WHERE {where}", {"tag": TAG})
        for row in cur.fetchall():
            for column in columns:
                old = row[column]
                if not old or not re.search(TAG, old):
                    continue
                new = cleaned(column, old)
                # Nothing but markup: blanking a title is not this script's call.
                if new == old or not new:
                    continue
                records.setdefault(row["record_id"], []).append(
                    {"table": table, "row_key": row["row_key"], "column": column,
                     "old": old, "new": new})
    return dict(sorted(records.items()))


def identity_clash(cur, row_key, current, changes):
    """The record_id of another validated row this one would duplicate, or None."""
    proposed = dict(current)
    proposed.update({c["column"]: c["new"] for c in changes})
    cur.execute(IDENTITY_SQL, {**proposed, "row_key": row_key})
    hit = cur.fetchone()
    return hit["record_id"] if hit else None


def validated_rows(cur, row_keys, lock):
    if not row_keys:
        return {}
    cur.execute(
        "SELECT validated_record_id::text AS row_key, doi_r, study_r, title_r, doi_o,"
        " original_key, study_o, title_o, abstract_r FROM validated"
        " WHERE validated_record_id = ANY(%s::uuid[])" + (" FOR UPDATE" if lock else ""),
        (list(row_keys),),
    )
    return {r["row_key"]: r for r in cur.fetchall()}


def short(value, limit=160):
    value = value.replace("\n", "\\n")
    return value if len(value) <= limit else value[:limit] + "…"


def admin_named(cur, handle):
    cur.execute("SELECT id, handle FROM admins WHERE lower(handle) = lower(%s)", (handle,))
    admin = cur.fetchone()
    if admin is None:
        sys.exit(f"No admin with the handle {handle!r}.")
    return admin


def audit(cur, action, admin, record_id, changes):
    """The admin audit log entry for one record, in the record's own transaction.
    Titles are kept whole; the full values of every change are in the log file."""
    title = next((c["new"] if action == CLEANED else c["old"] for c in changes
                  if c["column"] in TITLE_COLUMNS), None)
    detail = {"script": AGENT, "changes": [
        {"table": c["table"], "column": c["column"],
         "from": short(c["old" if action == CLEANED else "new"], 300),
         "to": short(c["new" if action == CLEANED else "old"], 300)} for c in changes]}
    cur.execute(
        """
        INSERT INTO security_events (action, actor_kind, actor_id, actor_handle,
                                     target_kind, target_id, target_label, user_agent, detail)
        VALUES (%s, 'admin', %s, %s, 'record', %s, %s, %s, %s::jsonb)
        """,
        (action, admin["id"], admin["handle"], record_id,
         title[:256] if title else None, AGENT, json.dumps(detail, ensure_ascii=False)),
    )


def check(conn):
    with conn, conn.cursor() as cur:
        records = plan(cur)
        clashes = {}
        for record_id, changes in records.items():
            by_row = {}
            for c in changes:
                if c["table"] == "validated":
                    by_row.setdefault(c["row_key"], []).append(c)
            current = validated_rows(cur, by_row, lock=False)
            for row_key, row_changes in by_row.items():
                other = identity_clash(cur, row_key, current[row_key], row_changes)
                if other:
                    clashes[record_id] = other
        conn.rollback()
    values = sum(len(c) for c in records.values())
    for record_id, changes in records.items():
        note = f"   SKIPPED: would duplicate validated record {clashes[record_id]}" \
            if record_id in clashes else ""
        print(f"{record_id}{note}")
        for c in changes:
            print(f"  {c['table']}.{c['column']}")
            print(f"    old: {short(c['old'])}")
            print(f"    new: {short(c['new'])}")
    print(f"\n{values} values in {len(records)} records would change; "
          f"{len(clashes)} records would be skipped as duplicates.")
    PLAN.write_text(json.dumps({"made_at": datetime.now(timezone.utc).isoformat(),
                                "records": records, "skipped_duplicates": clashes},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Plan written to {PLAN}")


def refuse_quiet_hours():
    hour = datetime.now(timezone.utc).hour
    if hour in QUIET_HOURS_UTC:
        sys.exit(f"It is {hour:02d}:xx UTC: the nightly import and FLoRA release run "
                 "between 02:00 and 05:00 UTC. Run this after 05:00 UTC.")


def log_line(log, entry):
    log.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.flush()
    os.fsync(log.fileno())


def apply(conn, log_path, handle):
    refuse_quiet_hours()
    with conn, conn.cursor() as cur:
        admin = admin_named(cur, handle)
        records = plan(cur)
    done = skipped = 0
    with open(log_path, "a", encoding="utf-8") as log:
        for record_id, changes in records.items():
            try:
                with conn, conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '5s'")
                    written = apply_record(cur, record_id, changes, admin)
                    for entry in written:
                        log_line(log, entry)
                done += 1
                print(f"applied  {record_id}: {len(written)} values")
            except Skip as reason:
                skipped += 1
                print(f"skipped  {record_id}: {reason}")
            except psycopg2.errors.LockNotAvailable:
                skipped += 1
                print(f"skipped  {record_id}: row locked by someone else; run again later")
    print(f"\n{done} records changed by {admin['handle']}, {skipped} skipped. Log: {log_path}")


def apply_record(cur, record_id, changes, admin):
    """Write one record's changes and their audit entry; raise Skip to leave the
    whole record alone."""
    now = datetime.now(timezone.utc).isoformat()
    by_row = {}
    for c in changes:
        by_row.setdefault((c["table"], c["row_key"]), []).append(c)

    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s::uuid FOR UPDATE",
                (record_id,))
    unvalidated = cur.fetchone()
    validated = validated_rows(
        cur, {k for t, k in by_row if t == "validated"}, lock=True)

    for (table, row_key), row_changes in by_row.items():
        current = unvalidated if table == "unvalidated" else validated.get(row_key)
        if current is None:
            raise Skip(f"{table} row {row_key} is gone")
        for c in row_changes:
            if current[c["column"]] != c["old"]:
                raise Skip(f"{table}.{c['column']} changed since it was read")
        if table == "validated":
            other = identity_clash(cur, row_key, current, row_changes)
            if other:
                raise Skip(f"would duplicate validated record {other}")

    written = []
    for (table, row_key), row_changes in by_row.items():
        sets = ", ".join(f"{c['column']} = %s" for c in row_changes)
        if table == "unvalidated":
            sets += ", updated_at = NOW()"
        cur.execute(f"UPDATE {table} SET {sets} WHERE {ROW_KEY[table]} = %s::uuid",
                    [c["new"] for c in row_changes] + [row_key])
        if cur.rowcount != 1:
            raise Skip(f"{table} row {row_key} updated {cur.rowcount} rows")
        written += [{"time": now, "admin": admin["handle"], "record_id": record_id, **c}
                    for c in row_changes]
    audit(cur, CLEANED, admin, record_id, changes)
    return written


def rollback(conn, log_path, handle, only):
    refuse_quiet_hours()
    with conn, conn.cursor() as cur:
        admin = admin_named(cur, handle)
    by_record = {}
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            by_record.setdefault(entry["record_id"], []).append(entry)
    if only:
        missing = sorted(set(only) - set(by_record))
        if missing:
            sys.exit(f"Not in the log: {', '.join(missing)}")
        by_record = {r: by_record[r] for r in only}
    restored = left = 0
    for record_id, entries in by_record.items():
        undone = []
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            # Newest first, so a value applied twice goes back to what came before both.
            for entry in reversed(entries):
                table, column = entry["table"], entry["column"]
                cur.execute(f"SELECT {column} AS value FROM {table} "
                            f"WHERE {ROW_KEY[table]} = %s::uuid FOR UPDATE",
                            (entry["row_key"],))
                row = cur.fetchone()
                if row is not None and row["value"] == entry["old"]:
                    continue                    # undone already, by an earlier rollback
                if row is None or row["value"] != entry["new"]:
                    left += 1
                    print(f"left     {record_id} {table}.{column}: changed since apply")
                    continue
                cur.execute(f"UPDATE {table} SET {column} = %s"
                            + (", updated_at = NOW()" if table == "unvalidated" else "")
                            + f" WHERE {ROW_KEY[table]} = %s::uuid",
                            (entry["old"], entry["row_key"]))
                undone.append(entry)
            if undone:
                audit(cur, RESTORED, admin, record_id, undone)
        restored += len(undone)
        if undone:
            print(f"restored {record_id}: {len(undone)} values")
    print(f"\n{restored} values restored by {admin['handle']}, {left} left alone.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=("check", "apply", "rollback"))
    parser.add_argument("--admin", help="your admin handle; required to apply or roll back")
    parser.add_argument("--record", action="append", default=[],
                        help="rollback only this record_id (repeatable)")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    args = parser.parse_args()
    if args.mode != "check" and not args.admin:
        parser.error("apply and rollback need --admin with your admin handle")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    conn = connect()
    try:
        if args.mode == "check":
            check(conn)
        elif args.mode == "apply":
            apply(conn, args.log, args.admin)
        else:
            rollback(conn, args.log, args.admin, args.record)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
