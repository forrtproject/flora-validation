"""entry_checks.py — the FLoRA entry checks, run by the website over the stored dataset.

The pipeline (prepare_flora.py) runs validate_flora and validate_flora_network on
every build and writes their reports beside flora.csv, and it still does. This runs
the same checks over the stored dataset (flora_data, read as flora_store.export_csv
publishes it) and keeps every finding in the database, by FLoRA record:

    flora_check_findings   one row per finding, keyed on validate_flora.finding_key
    flora_check_runs       one row per run of either part

A finding stays open until the value it flagged changes. The checks only report:
nothing here changes a FLoRA record. A person's "Not a problem" (dismissed_at, from
the FLoRA tab: dismiss, undismiss) is kept with the finding's key, and no run
touches it.

Two parts, both run by the website's scheduler (app._start_scheduler, every five
minutes, run_due), on whichever pod takes the advisory lock:

    structural  validate_flora, whenever the stored dataset's revision has moved:
                a pipeline run stored a snapshot, or a correction was saved. It
                checks every record, so a finding it no longer makes is resolved.
    network     validate_flora_network, once a UTC day from 04:00, after the
                03:00 nightly pipeline has stored the dataset, within a 45-minute
                budget. It shares the pipeline's cache (link_checks,
                original_doi_lookups). A budget leaves targets unasked, so it
                resolves only what it answered (_network_resolved).

Usage:
    python entry_checks.py --structural
    python entry_checks.py --network [--minutes 45]
    python entry_checks.py --due          # which parts are due; runs nothing

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import io
import json
import os
import sys
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor, execute_values

import db_pool
import flora_store
import prepare_flora
import validate_flora
import validate_flora_network as network
from pipeline_logging import start as start_logging
from validate_flora import POSSIBLE_PROBLEM, PROBLEM, _text

STRUCTURAL, NETWORK = "structural", "network"
PARTS = (STRUCTURAL, NETWORK)
# Session-level, one per part, held by the one pod running that part. 7_342_025_091
# to _096 are taken: the extractor pipeline, the source sync, the schema bootstrap,
# and adjudication's three (bootstrap, importer, judging). Apart, so the nightly
# network run (up to 45 minutes) never makes a correction wait for its structural
# check: the parts store different findings, and each reads its own snapshot.
LOCK_IDS = {STRUCTURAL: 7_342_025_097, NETWORK: 7_342_025_098}
# The network part's budget. It runs in the web process's scheduler, not under a
# stage timeout, so it can take longer than the website pipeline's 15 minutes
# (source_sync_runner.NETWORK_MINUTES); what is left waits for the next day.
NETWORK_MINUTES = 45
# The nightly pipeline starts at 03:00 UTC and stores the dataset; from this hour
# the day's network run checks what it stored.
NETWORK_HOUR_UTC = 4
# A structural run that failed is tried again after this long, not on every tick:
# a failure that repeats would otherwise add a failed run every five minutes.
RETRY_AFTER = timedelta(hours=1)
# A network run still 'running' this long after it started has not outlived its
# budget but died (a replaced pod leaves its row 'running'), so it stops blocking the
# day's run; it is marked abandoned when the next run takes the lock. Not longer, or
# a run lost to a deploy would cost the day; not shorter than the budget plus the
# retraction download and the last request.
RUNNING_STALE_AFTER = timedelta(minutes=NETWORK_MINUTES + 15)

RETRACTION_CHECKS = ("retracted", "expression_of_concern")
TARGET_CHECKS = {"doi_not_registered": "dois", "dead_link": "urls"}
ORIGINAL_CHECK = "original_doi_found"
ABANDONED = "abandoned: the process running it ended before it finished"

# What the FLoRA tab calls each check: the heading its report section has. A finding
# stores only the code; a code no check has any more is shown as itself.
CHECK_HEADINGS = {
    **{code: heading for code, _level, _name, heading, _fn, _description in validate_flora.CHECKS},
    **{code: heading for code, _level, heading, _selector in network.CHECKS},
}
# A "Not a problem" needs a reason: the next person to read the finding has only the
# comment to go on. The upper bound keeps it a comment.
DISMISS_NOTE_MIN, DISMISS_NOTE_MAX = 3, 1000


class FindingNotFound(LookupError):
    """No finding has this key."""


def _log(message: str) -> None:
    print(f"[entry-checks] {message}", flush=True)


# ── the stored dataset ────────────────────────────────────────────────────────

def revision_text(revision) -> "str | None":
    """flora_store.dataset_revision as flora_check_runs stores it. The timestamp is
    written in UTC, so two connections with different time zones agree."""
    if revision is None:
        return None
    digest, updated_at, watermark = revision
    return json.dumps([digest, updated_at.astimezone(timezone.utc).isoformat(), int(watermark)])


def load_stored_dataset(conn):
    """(frame, revision text) of the stored dataset, or (None, None) before any.

    The frame is the published CSV (flora_store.export_csv) read back as the
    pipeline reads its own (prepare_flora.read_export), so the checks see the values
    they see in a pipeline run. Both come from one snapshot (REPEATABLE READ): a
    correction saved in between would otherwise be in the rows and not in the
    revision, and the change would never be checked.
    """
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            revision = revision_text(flora_store.dataset_revision(cur))
            if revision is None:
                return None, None
            text = flora_store.export_csv(cur)
    frame = prepare_flora.read_export(io.BytesIO(text.encode("utf-8")))
    if "flora_id" not in frame.columns:
        # Findings are kept by FLoRA record; without the IDs none could be.
        raise ValueError("The stored dataset has no flora_id column")
    return frame, revision


def _records(frame) -> dict:
    """{FLoRA ID: row} for the stored dataset's records."""
    return {_text(row.get("flora_id")): row for row in frame.to_dict("records")
            if _text(row.get("flora_id"))}


# ── runs ──────────────────────────────────────────────────────────────────────

def _start_run(conn, part) -> int:
    # Committed at once, so a run that dies leaves a row saying so.
    with conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO flora_check_runs (part, status) VALUES (%s, 'running') "
                        "RETURNING run_id", (part,))
            return cur.fetchone()[0]


def _finish_run(cur, run_id, status, revision, counts, notes) -> None:
    cur.execute("""UPDATE flora_check_runs
                      SET status = %s, finished_at = NOW(), dataset_revision = %s,
                          counts = %s::jsonb, notes = %s::jsonb
                    WHERE run_id = %s""",
                (status, revision, json.dumps(counts), json.dumps(notes), run_id))


def _fail_run(conn, run_id, error: str) -> None:
    try:
        conn.rollback()
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE flora_check_runs SET status = 'failed', finished_at = NOW(), "
                            "error = %s WHERE run_id = %s", (error[-4000:], run_id))
    except Exception:                                          # noqa: BLE001
        # The connection itself failed. The row stays 'running' until the next run
        # under the lock marks it 'abandoned' (_abandon_unfinished).
        _log("could not record the failed run:")
        traceback.print_exc()


def _abandon_unfinished(conn, part) -> int:
    """Mark the part's runs left 'running' by a process that ended mid-run. Only the
    holder of the part's lock runs it, so while it holds it no other run of that part
    can still be going. 'abandoned', not 'failed': a replaced pod is no reason to
    wait RETRY_AFTER."""
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE flora_check_runs SET status = 'abandoned', finished_at = NOW(), "
                        "error = %s WHERE status = 'running' AND part = %s", (ABANDONED, part))
            return cur.rowcount


# ── storing findings ──────────────────────────────────────────────────────────

def _items(report) -> list:
    """The findings of a validate_flora or validate_flora_network report, one per key
    (one INSERT cannot upsert a key twice; the checks do not repeat one)."""
    items = {}
    for result in report["results"]:
        for item in result["items"]:
            items.setdefault(item["key"], item)
    return list(items.values())


def _upsert(cur, part, items, run_id) -> dict:
    """Store this run's findings: new ones open, ones seen before refreshed and, if
    they had been resolved, re-opened. first_seen_at and a dismissal are kept."""
    cur.execute("""SELECT finding_key, resolved_at IS NOT NULL AS resolved, level, flora_ids,
                          column_name, value, detail
                     FROM flora_check_findings WHERE finding_key = ANY(%s)""",
                ([item["key"] for item in items],))
    before = {row["finding_key"]: row for row in cur.fetchall()}
    counts = {"new": 0, "reopened": 0, "changed": 0, "unchanged": 0}
    for item in items:
        prior = before.get(item["key"])
        if prior is None:
            counts["new"] += 1
        elif prior["resolved"]:
            counts["reopened"] += 1
        elif ((prior["level"], sorted(prior["flora_ids"]), prior["column_name"], prior["value"],
               prior["detail"]) != (item["level"], sorted(item["flora_ids"]), item["column"],
                                    item["value"], item["detail"])):
            # Same key, other words: a dead link's reason, or a value's spacing.
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1
    if items:
        execute_values(cur, """
            INSERT INTO flora_check_findings
                (finding_key, part, check_code, level, flora_ids, column_name, value, detail,
                 last_run_id)
            VALUES %s
            ON CONFLICT (finding_key) DO UPDATE SET
                part = EXCLUDED.part, check_code = EXCLUDED.check_code,
                level = EXCLUDED.level, flora_ids = EXCLUDED.flora_ids,
                column_name = EXCLUDED.column_name, value = EXCLUDED.value,
                detail = EXCLUDED.detail, last_seen_at = NOW(),
                last_run_id = EXCLUDED.last_run_id,
                resolved_at = NULL, resolved_run_id = NULL
            """, [(item["key"], part, item["check"], item["level"], sorted(item["flora_ids"]),
                   item["column"], item["value"], item["detail"], run_id) for item in items],
            template="(%s, %s, %s, %s, %s::text[], %s, %s, %s, %s)", page_size=500)
    return counts


def _resolve(cur, keys, run_id) -> int:
    if not keys:
        return 0
    cur.execute("""UPDATE flora_check_findings SET resolved_at = NOW(), resolved_run_id = %s
                    WHERE resolved_at IS NULL AND finding_key = ANY(%s)""",
                (run_id, sorted(keys)))
    return cur.rowcount


def _resolve_retired(cur, part, present_ids, run_id) -> int:
    """Resolve the part's open findings whose FLoRA records are all gone from the
    stored dataset (retired). No run can check them again. Only the run's own part:
    the other part may have checked a newer snapshot, whose new records this one's
    snapshot does not know."""
    cur.execute("""UPDATE flora_check_findings SET resolved_at = NOW(), resolved_run_id = %s
                    WHERE resolved_at IS NULL AND part = %s AND cardinality(flora_ids) > 0
                      AND NOT flora_ids && %s::text[]""",
                (run_id, part, sorted(present_ids)))
    return cur.rowcount


def _open(cur, part) -> list:
    cur.execute("""SELECT finding_key, check_code, flora_ids, column_name, value
                     FROM flora_check_findings WHERE part = %s AND resolved_at IS NULL""",
                (part,))
    return [dict(row) for row in cur.fetchall()]


def _level_counts(items) -> dict:
    by_check = Counter(item["check"] for item in items)
    return {"findings": len(items),
            PROBLEM: sum(1 for item in items if item["level"] == PROBLEM),
            POSSIBLE_PROBLEM: sum(1 for item in items if item["level"] == POSSIBLE_PROBLEM),
            "by_check": dict(sorted(by_check.items()))}


def _result(run_id, part, status, revision, counts, notes) -> dict:
    return {"run_id": run_id, "part": part, "status": status, "dataset_revision": revision,
            "counts": counts, "notes": notes}


# ── the structural part ───────────────────────────────────────────────────────

def run_structural(conn) -> dict:
    """validate_flora over the stored dataset, every finding stored.

    It checks every record every time, so an open structural finding it did not make
    is resolved. Records a failed run and raises.
    """
    run_id = _start_run(conn, STRUCTURAL)
    try:
        frame, revision = load_stored_dataset(conn)
        if frame is None:
            raise LookupError("No dataset is stored yet: run the pipeline first")
        # No suppressions: those hide a finding from the pipeline's report file.
        # Here every finding is kept, and a person dismisses one on its record.
        items = _items(validate_flora.validate(frame))
        seen = {item["key"] for item in items}
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                counts = {"records": len(frame), **_level_counts(items),
                          **_upsert(cur, STRUCTURAL, items, run_id)}
                counts["retired"] = _resolve_retired(cur, STRUCTURAL, set(_records(frame)), run_id)
                counts["resolved"] = _resolve(
                    cur, {f["finding_key"] for f in _open(cur, STRUCTURAL)} - seen, run_id)
                _finish_run(cur, run_id, "finished", revision, counts, [])
        return _result(run_id, STRUCTURAL, "finished", revision, counts, [])
    except Exception:
        _fail_run(conn, run_id, traceback.format_exc())
        raise


# ── the network part ──────────────────────────────────────────────────────────

def _value_gone(finding, records) -> bool:
    """None of the finding's records still holds the value in that column: each
    is retired or now holds another value, so the finding cannot be found again."""
    return all(_text((records.get(flora_id) or {}).get(finding["column_name"]))
               != (finding["value"] or "") for flora_id in finding["flora_ids"])


def _original_settled(row, lookups) -> bool:
    """Whether this run settled N4 for one record: it is retired, it is no longer an
    original without a DOI (it has a doi_o now), or its lookup was answered."""
    if row is None:
        return True
    query = network.lookup_query(row)
    if query is None:
        return True
    return (lookups or {}).get(network.query_key(query)) == network.OK


def _network_resolved(open_findings, records, report, answered) -> set:
    """The keys of open network findings this run settled without finding them again.

    Only what was checked again is resolved. A target or lookup deferred by the
    budget, or one that could not be checked, keeps its finding open.
      - Retraction Watch: when the download was checked (it covers every DOI).
      - DOIs and links: when the target was answered this run, cached success
        included (a failure found again is stored under its current key), or no
        record carries it as a target any more.
      - N4: when every one of its records is settled (_original_settled).
    A value gone from all of a finding's records also resolves it: the value it
    flagged changed, which needs no request to know.
    """
    retractions_ran = report.get("coverage", {}).get("retractions", {}).get("status") == "checked"
    codes = {code for code, *_ in network.CHECKS}
    resolved = set()
    for finding in open_findings:
        check = finding["check_code"]
        if check in RETRACTION_CHECKS:
            settled = retractions_ran or _value_gone(finding, records)
        elif check in TARGET_CHECKS:
            targets = answered.get(TARGET_CHECKS[check])
            settled = (targets is not None and (targets.get(finding["value"]) in (network.OK, network.FAILED)
                                                or finding["value"] not in targets)) \
                or _value_gone(finding, records)
        elif check == ORIGINAL_CHECK:
            settled = all(_original_settled(records.get(flora_id), answered.get("originals"))
                          for flora_id in finding["flora_ids"])
        else:
            # A check validate_flora_network no longer makes: nothing will find it again.
            settled = check not in codes
        if settled:
            resolved.add(finding["finding_key"])
    return resolved


def _answer_counts(answered) -> dict:
    return {selector: {"ok": sum(1 for a in answers.values() if a == network.OK),
                       "failed": sum(1 for a in answers.values() if a == network.FAILED),
                       "unanswered": sum(1 for a in answers.values() if a is None)}
            for selector, answers in answered.items()}


def run_network(conn, minutes=NETWORK_MINUTES) -> dict:
    """validate_flora_network over the stored dataset within `minutes` (0: no
    budget), every finding stored, and only what it checked again resolved.

    Records a failed run and raises.
    """
    run_id = _start_run(conn, NETWORK)
    try:
        frame, revision = load_stored_dataset(conn)
        if frame is None:
            raise LookupError("No dataset is stored yet: run the pipeline first")
        answered = {}
        autocommit = conn.autocommit
        # Each cache entry commits as it is learnt, as in the pipeline's run: a run
        # cut short keeps its answers, and the pipeline's checks see them at once.
        conn.autocommit = True
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                network.ensure_cache(cur)
                report = network.validate(cur, frame, minutes=minutes, answered=answered)
        finally:
            conn.autocommit = autocommit
        items = _items(report)
        seen = {item["key"] for item in items}
        records = _records(frame)
        status = "incomplete" if report["status"] == "incomplete" else "finished"
        notes = list(report["notices"])
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                counts = {"records": len(frame), **_level_counts(items),
                          **_upsert(cur, NETWORK, items, run_id)}
                counts["retired"] = _resolve_retired(cur, NETWORK, set(records), run_id)
                open_findings = [f for f in _open(cur, NETWORK) if f["finding_key"] not in seen]
                counts["resolved"] = _resolve(
                    cur, _network_resolved(open_findings, records, report, answered), run_id)
                counts["kept_open"] = len(open_findings) - counts["resolved"]
                counts["answered"] = _answer_counts(answered)
                counts["could_not_check"] = sum(len(v) for v in report["unchecked"].values())
                counts["coverage"] = report["coverage"]
                _finish_run(cur, run_id, status, revision, counts, notes)
        return _result(run_id, NETWORK, status, revision, counts, notes)
    except Exception:
        _fail_run(conn, run_id, traceback.format_exc())
        raise


# ── the schedule ──────────────────────────────────────────────────────────────

def due(cur, now=None) -> list:
    """The parts due now, structural first. Nothing is due before a dataset is stored.

    Structural: the stored dataset's revision differs from the one the last finished
    structural run checked, unless a structural run failed since, under RETRY_AFTER
    ago. Network: from NETWORK_HOUR_UTC, when no network run has started this UTC day
    (a failed one included: the next day's run is soon enough, and the cache keeps
    what it learnt). Not counted: an abandoned run, and a 'running' one older than
    RUNNING_STALE_AFTER (its process died): a deploy mid-run costs an hour, not the day.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    revision = revision_text(flora_store.dataset_revision(cur))
    if revision is None:
        return []
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cur.execute(
        """
        WITH checked AS (
            SELECT run_id, dataset_revision FROM flora_check_runs
             WHERE part = 'structural' AND status = 'finished'
             ORDER BY run_id DESC LIMIT 1)
        SELECT (SELECT dataset_revision FROM checked) AS checked_revision,
               EXISTS (SELECT 1 FROM flora_check_runs
                        WHERE part = 'structural' AND status = 'failed' AND started_at > %s
                          AND run_id > COALESCE((SELECT run_id FROM checked), 0)) AS failed_recently,
               EXISTS (SELECT 1 FROM flora_check_runs
                        WHERE part = 'network' AND started_at >= %s
                          AND (status IN ('finished', 'incomplete', 'failed')
                               OR (status = 'running' AND started_at > %s))) AS network_today
        """, (now - RETRY_AFTER, day, now - RUNNING_STALE_AFTER))
    row = cur.fetchone()
    parts = []
    if row["checked_revision"] != revision and not row["failed_recently"]:
        parts.append(STRUCTURAL)
    if now >= day.replace(hour=NETWORK_HOUR_UTC) and not row["network_today"]:
        parts.append(NETWORK)
    return parts


def _try_lock(conn, part) -> bool:
    autocommit = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_IDS[part],))
            return bool(cur.fetchone()[0])
    finally:
        conn.autocommit = autocommit


def _unlock(conn, part) -> None:
    conn.rollback()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_IDS[part],))


def _run_part(conn, part, network_minutes) -> bool:
    """Under the part's lock: run it if it is still due. A failed run is logged (its
    run row has the error). Returns whether it ran."""
    _abandon_unfinished(conn, part)
    # Asked again under the lock: another pod may have run it since this one asked.
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if part not in due(cur):
                return False
    try:
        result = run_structural(conn) if part == STRUCTURAL else run_network(conn, network_minutes)
        _log(f"{part} run {result['run_id']} {result['status']}: "
             f"{json.dumps({k: v for k, v in result['counts'].items() if not isinstance(v, dict)})}")
    except Exception:                                          # noqa: BLE001
        _log(f"the {part} run failed:")
        traceback.print_exc()
    return True


def run_due(database_url: "str | None" = None, network_minutes: float = NETWORK_MINUTES,
            parts=PARTS) -> list:
    """Scheduler entry point: run those of `parts` that are due, each on one pod.
    Returns the parts it ran (failed ones included: their runs record why).

    The scheduler has one job per part, so a long network run never holds up the
    structural one. Never raises: an exception escaping here would end the
    scheduler's job, and the checks would stop with no sign of why.
    """
    database_url = database_url or os.environ.get("DATABASE_URL")
    conn = None
    ran = []
    try:
        # Asked every five minutes on every pod, and nearly always nothing is due: a
        # pooled connection answers that. The lock's own connection is opened only
        # when there is work for it (source_sync_runner.run_queued does the same).
        try:
            with db_pool.cursor(database_url) as cur:
                wanted = [part for part in due(cur) if part in parts]
        except psycopg2.OperationalError as exc:
            # A database this pod cannot reach (in the tests, the stub DATABASE_URL):
            # one line, not a traceback every five minutes.
            reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            _log(f"database unavailable, checks not run: {reason}")
            return []
        if not wanted:
            return []
        conn = psycopg2.connect(database_url)
        for part in wanted:
            if not _try_lock(conn, part):
                continue                   # another pod is running this part
            try:
                if _run_part(conn, part, network_minutes):
                    ran.append(part)
            finally:
                _unlock(conn, part)
        return ran
    except Exception:                                          # noqa: BLE001
        _log("error:")
        traceback.print_exc()
        return ran
    finally:
        if conn is not None:
            conn.close()


# ── reading, for the FLoRA tab ────────────────────────────────────────────────

def _named(row) -> dict:
    """A finding or a count by check, with the heading of its check."""
    row = dict(row)
    row["check_heading"] = CHECK_HEADINGS.get(row["check_code"], row["check_code"])
    return row


def _tables_exist(cur) -> bool:
    """False in a database init_db() has not reached (as flora_edits._table_exists):
    the FLoRA tab then shows no findings instead of failing."""
    cur.execute("SELECT to_regclass('flora_check_findings') IS NOT NULL "
                "AND to_regclass('flora_check_runs') IS NOT NULL AS present")
    return cur.fetchone()["present"]


def open_by_record(cur) -> dict:
    """{FLoRA ID: {"problem": n, "possible_problem": n, "checks": {code: n}}} for the
    open findings nobody has marked "Not a problem": the FLoRA tab's filters, counts
    and list chips, from one query per request. A finding about several records
    counts for each. Empty while the tables do not exist yet."""
    if not _tables_exist(cur):
        return {}
    cur.execute(
        """
        SELECT flora_id, level, check_code, COUNT(*) AS n
          FROM flora_check_findings, unnest(flora_ids) AS flora_id
         WHERE resolved_at IS NULL AND dismissed_at IS NULL
         GROUP BY flora_id, level, check_code
        """)
    records = {}
    for row in cur.fetchall():
        record = records.setdefault(row["flora_id"], {PROBLEM: 0, POSSIBLE_PROBLEM: 0, "checks": {}})
        record[row["level"]] += row["n"]
        record["checks"][row["check_code"]] = row["n"]
    return records


def findings_for(cur, flora_id: str) -> list:
    """The open findings about one FLoRA record, dismissed ones included, each with
    the run that last found it: problems first, and within a level the ones that
    still need review before the dismissed ones."""
    if not _tables_exist(cur):
        return []
    cur.execute(
        """
        SELECT f.finding_key, f.part, f.check_code, f.level, f.flora_ids, f.column_name,
               f.value, f.detail, f.first_seen_at, f.last_seen_at,
               f.dismissed_at, f.dismissed_by, f.dismiss_note,
               r.run_id AS last_run_id, r.started_at AS last_run_started_at,
               r.finished_at AS last_run_finished_at, r.status AS last_run_status
          FROM flora_check_findings f
          LEFT JOIN flora_check_runs r ON r.run_id = f.last_run_id
         WHERE f.flora_ids @> ARRAY[%s]::text[] AND f.resolved_at IS NULL
         ORDER BY f.level <> 'problem', f.dismissed_at IS NOT NULL, f.check_code, f.finding_key
        """, (flora_id,))
    return [_named(row) for row in cur.fetchall()]


def last_runs(cur) -> dict:
    """{part: its latest run, whatever its status, or None before any}."""
    if not _tables_exist(cur):
        return {part: None for part in PARTS}
    cur.execute(
        """
        SELECT DISTINCT ON (part) run_id, part, started_at, finished_at, status,
               dataset_revision, counts, notes, error
          FROM flora_check_runs ORDER BY part, run_id DESC
        """)
    runs = {row["part"]: dict(row) for row in cur.fetchall()}
    return {part: runs.get(part) for part in PARTS}


def summary(cur) -> dict:
    """Open findings counted per check and level (dismissed ones apart), and the
    last run of each part."""
    cur.execute(
        """
        SELECT part, check_code, level,
               COUNT(*) FILTER (WHERE dismissed_at IS NULL) AS open,
               COUNT(*) FILTER (WHERE dismissed_at IS NOT NULL) AS dismissed
          FROM flora_check_findings WHERE resolved_at IS NULL
         GROUP BY part, check_code, level
         ORDER BY level <> 'problem', part, check_code
        """)
    checks = [_named(row) for row in cur.fetchall()]
    return {"checks": checks,
            "open": {level: sum(c["open"] for c in checks if c["level"] == level)
                     for level in (PROBLEM, POSSIBLE_PROBLEM)},
            "dismissed": sum(c["dismissed"] for c in checks),
            "last_runs": last_runs(cur)}


# ── "Not a problem", from the FLoRA tab ───────────────────────────────────────

def _locked(cur, finding_key: str) -> dict:
    """The finding, locked until the transaction ends: two people marking it at once
    are answered in turn, the second told the first already did."""
    cur.execute("""SELECT finding_key, check_code, level, flora_ids, resolved_at,
                          dismissed_at, dismissed_by, dismiss_note
                     FROM flora_check_findings WHERE finding_key = %s FOR UPDATE""",
                (finding_key,))
    row = cur.fetchone()
    if row is None:
        raise FindingNotFound(finding_key)
    return dict(row)


def dismiss(cur, finding_key: str, admin: dict, note: str) -> dict:
    """Mark a finding "Not a problem": who (the admin's handle, as flora_edits logs a
    change), when, and the required comment saying why. It belongs to the finding's
    key, so a run that resolves and later re-opens the finding keeps it; a changed
    value is a new finding, which needs its own review."""
    text = (note or "").strip()
    if len(text) < DISMISS_NOTE_MIN:
        raise ValueError(f"Say why this is not a problem: at least {DISMISS_NOTE_MIN} characters.")
    if len(text) > DISMISS_NOTE_MAX:
        raise ValueError(f"The comment is longer than {DISMISS_NOTE_MAX:,} characters.")
    finding = _locked(cur, finding_key)
    if finding["dismissed_at"] is not None:
        raise ValueError(f"{finding['dismissed_by']} already marked this finding not a problem.")
    if finding["resolved_at"] is not None:
        # The record changed while it was open: there is nothing left to judge.
        raise ValueError("This finding is resolved: the value it flagged has changed.")
    cur.execute("""UPDATE flora_check_findings
                      SET dismissed_at = NOW(), dismissed_by = %s, dismiss_note = %s
                    WHERE finding_key = %s
                RETURNING finding_key, check_code, level, flora_ids,
                          dismissed_at, dismissed_by, dismiss_note""",
                (admin["handle"], text, finding_key))
    return _named(cur.fetchone())


def undismiss(cur, finding_key: str, admin: dict) -> dict:
    """Withdraw a "Not a problem": the finding needs review again. Returns the
    dismissal withdrawn, as the row keeps none once it is cleared."""
    finding = _locked(cur, finding_key)
    if finding["dismissed_at"] is None:
        raise ValueError("This finding is not marked not a problem.")
    cur.execute("""UPDATE flora_check_findings
                      SET dismissed_at = NULL, dismissed_by = NULL, dismiss_note = NULL
                    WHERE finding_key = %s""", (finding_key,))
    return _named({"finding_key": finding_key, "check_code": finding["check_code"],
                   "level": finding["level"], "flora_ids": finding["flora_ids"],
                   "withdrawn_by": admin["handle"],
                   "withdrawn": {"dismissed_at": finding["dismissed_at"],
                                 "dismissed_by": finding["dismissed_by"],
                                 "dismiss_note": finding["dismiss_note"]}})


# ── the command line ──────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    part = parser.add_mutually_exclusive_group(required=True)
    part.add_argument("--structural", action="store_true",
                      help="run the structural checks over the stored dataset now")
    part.add_argument("--network", action="store_true",
                      help="run the network checks over the stored dataset now")
    part.add_argument("--due", action="store_true",
                      help="say which parts the scheduler would run now; runs nothing")
    parser.add_argument("--minutes", type=float,
                        help=f"the network checks' budget (default {NETWORK_MINUTES}; 0: none)")
    args = parser.parse_args(argv)
    if args.minutes is not None and not args.network:
        parser.error("--minutes goes with --network")
    if args.minutes is not None and args.minutes < 0:
        parser.error("--minutes must be zero (no budget) or positive")
    start_logging("entry-checks")
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    conn = psycopg2.connect(database_url)
    try:
        if args.due:
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    parts = due(cur)
            print(", ".join(parts) if parts else "nothing due")
            return 0
        part = STRUCTURAL if args.structural else NETWORK
        if not _try_lock(conn, part):
            print(f"The {part} checks are running elsewhere (the lock is held); try again "
                  "when that run ends.", file=sys.stderr)
            return 1
        try:
            _abandon_unfinished(conn, part)
            if part == STRUCTURAL:
                result = run_structural(conn)
            else:
                result = run_network(conn, NETWORK_MINUTES if args.minutes is None else args.minutes)
        finally:
            _unlock(conn, part)
        print(json.dumps(result, indent=2, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
