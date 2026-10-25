"""Tests for entry_checks: the checks run by the website over the stored dataset,
their findings kept in the database by FLoRA record.

Each test stores a small dataset in a throwaway PostgreSQL database
(FLORA_TEST_DATABASE_URL, as tests/test_preparation_database.py) and runs the real
checks over it. Nothing touches the network: every request function of
validate_flora_network is replaced by a fake that answers as told.
"""
import hashlib
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import psycopg2
import pytest
from apscheduler.triggers.interval import IntervalTrigger
from psycopg2.extras import RealDictCursor

import db_pool
import entry_checks
import final_export
import flora_store
import validate_flora
import validate_flora_network as network
from tests.test_preparation_database import add_source, local_database  # noqa: F401

OK, FAILED, UNCHECKED = network.OK, network.FAILED, network.UNCHECKED


@pytest.fixture
def db(local_database):  # noqa: F811
    yield local_database
    # Before the database is dropped: the pooled run_due checks hold connections.
    db_pool.clear_all()


def _row(n, **over):
    """A FLoRA record no check flags; `over` adds what a test wants found."""
    identifier = f"TEST-{n:06d}"
    row = {column: None for column in flora_store.CSV_COLUMNS}
    row.update(id=identifier, id_md5=hashlib.md5(identifier.encode()).hexdigest(),
               flora_id=f"REPL-{n:06d}", doi_o=f"10.1000/o{n}", doi_r=f"10.1000/r{n}",
               title_o=f"Original {n}", title_r=f"Replication {n}", type="replication",
               source="validated", outcome="successful", year_o="2011", year_r="2015",
               apa_ref_o=f"Smith {n} (2011)", apa_ref_r=f"Jones {n} (2015)")
    row.update(over)
    return row


def _store(conn, *rows):
    """Store these records as a pipeline run does (flora_store.materialize)."""
    frame = pd.DataFrame(rows, columns=[*flora_store.CSV_COLUMNS, "flora_id"])
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            flora_store.materialize(cur, frame)


def _query(conn, sql, params=()):
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()] if cur.description else cur.rowcount


def _findings(conn):
    return {row["finding_key"]: row for row in _query(
        conn, "SELECT * FROM flora_check_findings ORDER BY finding_key")}


def _open(conn):
    return {key for key, row in _findings(conn).items() if row["resolved_at"] is None}


def _due(conn, now=None):
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return entry_checks.due(cur, now)


def _network_ran_today(conn):
    """A network run started now, so a test of the structural part is not also
    given a network run by the hour it happens to run at."""
    _query(conn, "INSERT INTO flora_check_runs (part, status, finished_at) "
                 "VALUES ('network', 'finished', NOW())")


TYPE_KEY = validate_flora.finding_key("invalid_type", ["REPL-000002"], "type", "extension")


# ── the schema ────────────────────────────────────────────────────────────────

def test_the_schema_applies_again_and_keeps_what_is_stored(db):
    _store(db, _row(1), _row(2, type="extension"))
    entry_checks.run_structural(db)
    schema = (final_export.ROOT / "db_schema.sql").read_text(encoding="utf-8")
    with db:
        with db.cursor() as cur:
            cur.execute(schema)
            cur.execute(schema)
    assert set(_findings(db)) == {TYPE_KEY}
    indexes = {row["indexname"] for row in _query(
        db, "SELECT indexname FROM pg_indexes WHERE tablename = 'flora_check_findings'")}
    assert {"idx_flora_check_findings_records", "idx_flora_check_findings_open"} <= indexes
    with pytest.raises(psycopg2.errors.CheckViolation):
        _query(db, "INSERT INTO flora_check_findings (finding_key, part, check_code, level, "
                   "flora_ids) VALUES ('x', 'structural', 'x', 'warning', '{}')")


# ── the structural part ───────────────────────────────────────────────────────

def test_findings_are_stored_by_flora_record_and_a_rerun_changes_nothing(db):
    _store(db, _row(1), _row(2, type="extension"), _row(3, year_o="1500"))
    first = entry_checks.run_structural(db)
    stored = _findings(db)
    assert TYPE_KEY in stored and len(stored) == first["counts"]["findings"] >= 2
    finding = stored[TYPE_KEY]
    assert (finding["part"], finding["check_code"], finding["level"], finding["flora_ids"],
            finding["column_name"], finding["value"]) == (
        "structural", "invalid_type", "problem", ["REPL-000002"], "type", "extension")
    assert finding["resolved_at"] is None and finding["last_run_id"] == first["run_id"]
    assert first["counts"]["new"] == len(stored)

    second = entry_checks.run_structural(db)
    again = _findings(db)
    assert set(again) == set(stored), "no duplicates"
    counts = second["counts"]
    assert (counts["new"], counts["reopened"], counts["changed"], counts["resolved"],
            counts["retired"], counts["unchanged"]) == (0, 0, 0, 0, 0, len(stored))
    for key, row in again.items():
        assert row["first_seen_at"] == stored[key]["first_seen_at"]
        assert row["last_seen_at"] > stored[key]["last_seen_at"]
        assert row["last_run_id"] == second["run_id"]
        assert {k: v for k, v in row.items() if k not in ("last_seen_at", "last_run_id")} == \
            {k: v for k, v in stored[key].items() if k not in ("last_seen_at", "last_run_id")}
    runs = _query(db, "SELECT part, status, dataset_revision, finished_at FROM flora_check_runs")
    assert [(r["part"], r["status"]) for r in runs] == [("structural", "finished")] * 2
    assert all(r["dataset_revision"] and r["finished_at"] for r in runs)


def test_a_changed_value_resolves_and_its_return_reopens_keeping_first_seen_and_dismissal(db):
    _store(db, _row(1), _row(2, type="extension"))
    entry_checks.run_structural(db)
    first_seen = _findings(db)[TYPE_KEY]["first_seen_at"]
    _query(db, "UPDATE flora_check_findings SET dismissed_at = NOW(), dismissed_by = 'editor', "
               "dismiss_note = 'a deliberate value' WHERE finding_key = %s", (TYPE_KEY,))

    _store(db, _row(1), _row(2))                       # corrected
    fixed = entry_checks.run_structural(db)
    row = _findings(db)[TYPE_KEY]
    assert fixed["counts"]["resolved"] == 1
    assert row["resolved_at"] is not None and row["resolved_run_id"] == fixed["run_id"]

    _store(db, _row(1), _row(2, type="extension"))     # and back
    back = entry_checks.run_structural(db)
    row = _findings(db)[TYPE_KEY]
    assert back["counts"]["reopened"] == 1 and back["counts"]["new"] == 0
    assert row["resolved_at"] is None and row["resolved_run_id"] is None
    assert row["first_seen_at"] == first_seen
    assert (row["dismissed_by"], row["dismiss_note"]) == ("editor", "a deliberate value")
    assert row["dismissed_at"] is not None


def test_a_correction_is_checked_because_it_moves_the_revision(db):
    _store(db, _row(1), _row(2, type="extension"))
    _network_ran_today(db)
    entry_checks.run_structural(db)
    assert _due(db) == []
    # What flora_edits.publish does to the stored row.
    _query(db, "UPDATE flora_data SET type = 'replication', updated_at = NOW() WHERE id = 'TEST-000002'")
    _query(db, "UPDATE flora_data_metadata SET updated_at = NOW() WHERE singleton")
    assert _due(db) == ["structural"]
    result = entry_checks.run_structural(db)
    assert result["counts"]["resolved"] == 1 and _open(db) == set()


def _retire_setup(conn):
    _store(conn, _row(1, type="extension"), _row(2, type="extension"))
    entry_checks.run_structural(conn)
    # Network findings of the two records: one of record 2 alone, one of both.
    for key, ids in (("net-2", ["REPL-000002"]), ("net-both", ["REPL-000001", "REPL-000002"])):
        _query(conn, "INSERT INTO flora_check_findings (finding_key, part, check_code, level, "
                     "flora_ids, column_name, value, detail) VALUES (%s, 'network', 'dead_link', "
                     "'problem', %s, 'url_r', 'https://gone.example/x', 'url_r is dead')", (key, ids))
    _store(conn, _row(1, type="extension"))            # record 2 retires
    return validate_flora.finding_key("invalid_type", ["REPL-000001"], "type", "extension")


def test_a_structural_run_resolves_the_findings_of_retired_records(db):
    kept = _retire_setup(db)
    result = entry_checks.run_structural(db)
    assert result["counts"]["retired"] == 1 and result["counts"]["resolved"] == 0
    assert _open(db) == {kept, "net-2", "net-both"}, \
        "a record still stored keeps its finding open; the network part's are its own"


def test_a_network_run_resolves_the_network_findings_of_retired_records(db, fake_network):
    """Only its own part's: a structural run may have stored a newer snapshot, whose
    new records the network run's older snapshot does not know."""
    kept = _retire_setup(db)
    result = entry_checks.run_network(db, minutes=0)
    assert result["counts"]["retired"] == 1
    # net-both is resolved as well, but as a value gone from its records, not retired.
    assert _open(db) == {kept, TYPE_KEY}


def test_a_failed_run_is_recorded_and_raised(db, monkeypatch):
    _store(db, _row(1))
    monkeypatch.setattr(validate_flora, "validate", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        entry_checks.run_structural(db)
    run = _query(db, "SELECT status, error, finished_at FROM flora_check_runs")[0]
    assert run["status"] == "failed" and "boom" in run["error"] and run["finished_at"]


# ── the network part ──────────────────────────────────────────────────────────

class FakeNetwork:
    """Every request validate_flora_network makes, answered from these dicts. A
    target not named is answered as fine; `cost` seconds of a fake clock pass per
    link checked and per lookup, for the budget."""

    def __init__(self, monkeypatch):
        self.dois, self.links, self.lookups, self.notices = {}, {}, {}, {}
        self.clock, self.cost, self.asked = [0.0], 0.0, []
        monkeypatch.setattr(network.time, "monotonic", lambda: self.clock[0])
        monkeypatch.setattr(network.time, "sleep", lambda delay: None)
        monkeypatch.setattr(network, "retraction_notices", self._notices)
        monkeypatch.setattr(network, "doi_registration",
                            lambda doi: self.dois.get(doi, (OK, "registered")))
        monkeypatch.setattr(network, "check_link", self._link)
        monkeypatch.setattr(network, "lookup_original_doi", self._lookup)

    def _notices(self):
        if isinstance(self.notices, Exception):
            raise self.notices
        return self.notices

    def _link(self, url):
        self.asked.append(url)
        self.clock[0] += self.cost
        return self.links.get(url, (OK, "HTTP 200"))

    def _lookup(self, query):
        self.asked.append(query[0])
        self.clock[0] += self.cost
        return self.lookups.get(query[0], (OK, None, "answered"))


@pytest.fixture
def fake_network(monkeypatch):
    return FakeNetwork(monkeypatch)


def _match(doi):
    return OK, {"doi": doi, "title": "A paper", "author": "", "year": ""}, "answered"


def _key(check, ids, column, value):
    return validate_flora.finding_key(check, ids, column, value)


LINK = [f"https://host{n}.example/paper" for n in range(4)]


def test_a_budget_resolves_only_what_it_answered(db, fake_network):
    _store(db, _row(1, url_r=LINK[1]), _row(2, url_r=LINK[2]), _row(3, url_r=LINK[3]),
           _row(4, doi_o=None, title_o="Lost original"), _row(5, doi_o=None, title_o="Second original"))
    fake_network.links = {url: (FAILED, "HTTP 404") for url in LINK}
    fake_network.lookups = {"Lost original": _match("10.1000/lost"),
                            "Second original": _match("10.1000/second")}
    first = entry_checks.run_network(db, minutes=0)
    dead = {n: _key("dead_link", [f"REPL-{n:06d}"], "url_r", LINK[n]) for n in (1, 2, 3)}
    lost = _key("original_doi_found", ["REPL-000004"], "doi_o", "10.1000/lost")
    second = _key("original_doi_found", ["REPL-000005"], "doi_o", "10.1000/second")
    assert first["status"] == "finished" and _open(db) == {*dead.values(), lost, second}

    # Next day: the lookups have expired, record 5 now has its DOI, and the budget
    # covers two link checks. Link 1 is reachable, link 2 cannot be checked, link 3
    # and every lookup are deferred.
    _query(db, "DELETE FROM original_doi_lookups")
    _store(db, _row(1, url_r=LINK[1]), _row(2, url_r=LINK[2]), _row(3, url_r=LINK[3]),
           _row(4, doi_o=None, title_o="Lost original"), _row(5, title_o="Second original"))
    fake_network.links = {LINK[1]: (OK, "HTTP 200"), LINK[2]: (UNCHECKED, "timed out"),
                          LINK[3]: (FAILED, "HTTP 404")}
    fake_network.cost, fake_network.asked = 60.0, []
    later = entry_checks.run_network(db, minutes=2)
    assert fake_network.asked == [LINK[1], LINK[2]], "the budget stopped there"
    assert later["status"] == "incomplete"
    assert later["counts"]["resolved"] == 2 and later["counts"]["kept_open"] == 3
    assert _open(db) == {dead[2], dead[3], lost}, "link 1 reachable; record 5 has a doi_o"
    assert later["counts"]["answered"]["urls"] == {"ok": 1, "failed": 0, "unanswered": 2}
    assert any("budget ran out" in note for note in later["notes"])

    # With time for everything: link 2 reachable, link 3 still dead, no match for 4.
    fake_network.links = {LINK[2]: (OK, "HTTP 200"), LINK[3]: (FAILED, "HTTP 404")}
    fake_network.lookups, fake_network.cost = {}, 0.0
    last = entry_checks.run_network(db, minutes=0)
    assert last["status"] == "finished" and _open(db) == {dead[3]}
    assert last["counts"]["unchanged"] == 1 and last["counts"]["resolved"] == 2


def test_a_value_changed_resolves_its_finding_without_a_request(db, fake_network, monkeypatch):
    _store(db, _row(1, url_r=LINK[1]))
    fake_network.links = {LINK[1]: (FAILED, "HTTP 404")}
    entry_checks.run_network(db, minutes=0)
    assert _open(db) == {_key("dead_link", ["REPL-000001"], "url_r", LINK[1])}
    _store(db, _row(1, url_r=LINK[2]))                 # corrected to another link

    def slow_download():
        fake_network.clock[0] += 120                   # the whole minute's budget
        return {}
    monkeypatch.setattr(network, "retraction_notices", slow_download)
    fake_network.asked = []
    result = entry_checks.run_network(db, minutes=1)
    assert fake_network.asked == [] and result["status"] == "incomplete", "the new link waits"
    assert result["counts"]["resolved"] == 1 and _open(db) == set()


def test_a_retraction_check_that_did_not_run_resolves_nothing(db, fake_network):
    _store(db, _row(1), _row(2))
    day = datetime(2020, 1, 1).date()
    fake_network.notices = {"10.1000/o1": [("retraction", day)],
                            "10.1000/r2": [("expression of concern", day)]}
    entry_checks.run_network(db, minutes=0)
    flagged = {_key("retracted", ["REPL-000001"], "doi_o", "10.1000/o1"),
               _key("expression_of_concern", ["REPL-000002"], "doi_r", "10.1000/r2")}
    assert _open(db) == flagged

    fake_network.notices = network.RetractionDataError("the download names 3 DOIs")
    failed = entry_checks.run_network(db, minutes=0)
    assert failed["status"] == "incomplete" and failed["counts"]["resolved"] == 0
    assert _open(db) == flagged
    assert any("Retraction check did not run" in note for note in failed["notes"])

    fake_network.notices = {}                           # both lifted
    lifted = entry_checks.run_network(db, minutes=0)
    assert lifted["counts"]["resolved"] == 2 and _open(db) == set()


def test_a_dois_registration_answered_this_run_resolves_it(db, fake_network):
    _store(db, _row(1))
    fake_network.dois = {"10.1000/r1": (FAILED, "not registered at doi.org")}
    entry_checks.run_network(db, minutes=0)
    key = _key("doi_not_registered", ["REPL-000001"], "doi_r", "10.1000/r1")
    assert _open(db) == {key}
    fake_network.dois = {"10.1000/r1": (UNCHECKED, "HTTP 503")}
    assert entry_checks.run_network(db, minutes=0)["counts"]["resolved"] == 0
    assert _open(db) == {key}
    fake_network.dois = {}
    assert entry_checks.run_network(db, minutes=0)["counts"]["resolved"] == 1
    assert _open(db) == set()


def test_a_network_run_shares_the_pipelines_cache(db, fake_network):
    _store(db, _row(1, url_r=LINK[1]))
    entry_checks.run_network(db, minutes=0)
    cached = {row["target"] for row in _query(db, "SELECT target FROM link_checks")}
    assert {"10.1000/o1", "10.1000/r1", LINK[1]} <= cached
    fake_network.asked = []
    entry_checks.run_network(db, minutes=0)
    assert fake_network.asked == [], "answered from the cache"


# ── due ───────────────────────────────────────────────────────────────────────

def _at(hour, minute=0, day=25):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def test_nothing_is_due_before_a_dataset_is_stored(db):
    assert _due(db, _at(5)) == []


def test_due_follows_the_revision_and_the_day(db):
    _store(db, _row(1))
    assert _due(db, _at(3, 59)) == ["structural"]
    assert _due(db, _at(4)) == ["structural", "network"]
    entry_checks.run_structural(db)
    assert _due(db, _at(3)) == []
    _query(db, "INSERT INTO flora_check_runs (part, status, started_at) "
               "VALUES ('network', 'failed', %s)", (_at(4, 5),))
    assert _due(db, _at(23)) == [], "a network run started today, even a failed one"
    assert _due(db, _at(3, day=26)) == []
    assert _due(db, _at(4, day=26)) == ["network"]


def test_a_new_snapshot_and_a_correction_each_move_the_revision(db):
    _store(db, _row(1))
    entry_checks.run_structural(db)
    assert _due(db, _at(1)) == []
    _store(db, _row(1, type="extension"))              # a pipeline run stores a snapshot
    assert _due(db, _at(1)) == ["structural"]
    entry_checks.run_structural(db)
    assert _due(db, _at(1)) == []
    # A saved correction: its change row moves the revision's watermark.
    with db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            record_id = add_source(cur, {"doi_o": "10.1000/o1"}, "REPL-000001")
            cur.execute("INSERT INTO flora_records (flora_id, primary_source_record_id, export_id, "
                        "export_position) SELECT 'REPL-000001', %s, id, export_position "
                        "FROM flora_data WHERE id = 'TEST-000001'", (record_id,))
            cur.execute("INSERT INTO flora_record_changes (export_id, admin_handle, action) "
                        "VALUES ('TEST-000001', 'editor', 'edit')")
    assert _due(db, _at(1)) == ["structural"]


def test_a_failed_structural_run_is_tried_again_after_an_hour(db):
    _store(db, _row(1))
    _query(db, "INSERT INTO flora_check_runs (part, status, started_at, finished_at) "
               "VALUES ('structural', 'failed', %s, %s)", (_at(2), _at(2)))
    assert _due(db, _at(2, 30)) == []
    assert _due(db, _at(3, 1)) == ["structural"]


# ── run_due, the scheduler's entry point ──────────────────────────────────────

def test_run_due_runs_what_is_due_once(db):
    _store(db, _row(1), _row(2, type="extension"))
    _network_ran_today(db)
    url = entry_checks.os.environ["DATABASE_URL"]
    assert entry_checks.run_due(url) == ["structural"]
    assert _open(db) == {TYPE_KEY}
    assert entry_checks.run_due(url) == [], "nothing is due any more"


def test_an_idle_tick_opens_no_connection_of_its_own(db, monkeypatch):
    _store(db, _row(1))
    _network_ran_today(db)
    entry_checks.run_structural(db)
    url = entry_checks.os.environ["DATABASE_URL"]
    assert entry_checks.run_due(url) == []             # the pool now holds an idle one
    monkeypatch.setattr(entry_checks.psycopg2, "connect",
                        lambda *a, **kw: pytest.fail("opened a connection with nothing due"))
    assert entry_checks.run_due(url) == []


def test_two_pods_at_once_run_the_checks_once(db, monkeypatch):
    _store(db, _row(1))
    _network_ran_today(db)
    started, release, calls = threading.Event(), threading.Event(), []

    def slow_run(conn):
        calls.append("structural")
        started.set()
        assert release.wait(10)
        return {"run_id": 0, "status": "finished", "counts": {}}
    monkeypatch.setattr(entry_checks, "run_structural", slow_run)
    url = entry_checks.os.environ["DATABASE_URL"]
    results = []
    first = threading.Thread(target=lambda: results.append(entry_checks.run_due(url)))
    first.start()
    assert started.wait(10)
    try:
        assert entry_checks.run_due(url) == [], "the other pod holds the lock"
    finally:
        release.set()
        first.join(10)
    assert results == [["structural"]] and calls == ["structural"]


def test_a_long_network_run_does_not_hold_up_a_structural_one(db, monkeypatch):
    _store(db, _row(1, type="extension"))
    monkeypatch.setattr(entry_checks, "datetime", MagicMock(now=lambda tz=None: _at(5)))
    holder = psycopg2.connect(entry_checks.os.environ["DATABASE_URL"])
    try:
        with holder.cursor() as cur:               # the network part, mid-run on another pod
            cur.execute("SELECT pg_advisory_lock(%s)", (entry_checks.LOCK_IDS["network"],))
        url = entry_checks.os.environ["DATABASE_URL"]
        assert entry_checks.run_due(url, parts=("structural",)) == ["structural"]
        assert entry_checks.run_due(url, parts=("network",)) == [], "the network lock is held"
    finally:
        holder.close()
    assert _open(db) == {validate_flora.finding_key("invalid_type", ["REPL-000001"], "type", "extension")}


def test_run_due_runs_only_the_parts_it_is_given(db, fake_network, monkeypatch):
    _store(db, _row(1))
    monkeypatch.setattr(entry_checks, "datetime", MagicMock(now=lambda tz=None: _at(5)))
    url = entry_checks.os.environ["DATABASE_URL"]
    assert entry_checks.run_due(url, parts=("network",)) == ["network"]
    assert [r["part"] for r in _query(db, "SELECT part FROM flora_check_runs")] == ["network"]


def test_run_due_never_raises(db, monkeypatch, capsys):
    _store(db, _row(1))
    _network_ran_today(db)
    url = entry_checks.os.environ["DATABASE_URL"]
    monkeypatch.setattr(validate_flora, "validate", MagicMock(side_effect=RuntimeError("boom")))
    assert entry_checks.run_due(url) == ["structural"], "tried, and recorded as failed"
    assert "the structural run failed" in capsys.readouterr().out
    assert [r["status"] for r in _query(db, "SELECT status FROM flora_check_runs "
                                            "WHERE part = 'structural'")] == ["failed"]
    assert entry_checks.run_due(url) == [], "not again for an hour"

    monkeypatch.setattr(entry_checks, "due", MagicMock(side_effect=RuntimeError("worse")))
    assert entry_checks.run_due(url) == []
    assert "[entry-checks] error:" in capsys.readouterr().out


def test_an_unreachable_database_fails_quietly(capsys):
    # A closed local port: refused at once, with no lookup on the network.
    assert entry_checks.run_due("postgresql://postgres@127.0.0.1:1/none") == []
    out = capsys.readouterr()
    assert "[entry-checks] database unavailable" in out.out
    assert "Traceback" not in out.out + out.err


def test_a_run_left_running_by_a_dead_process_is_marked_abandoned(db):
    _store(db, _row(1))
    _network_ran_today(db)
    _query(db, "INSERT INTO flora_check_runs (part, status) VALUES ('structural', 'running')")
    assert entry_checks.run_due(entry_checks.os.environ["DATABASE_URL"]) == ["structural"]
    runs = _query(db, "SELECT status, error FROM flora_check_runs WHERE part = 'structural' "
                      "ORDER BY run_id")
    assert [(r["status"], r["error"]) for r in runs] == [
        ("abandoned", entry_checks.ABANDONED), ("finished", None)]


def test_a_run_of_the_other_part_is_not_abandoned_by_this_one(db):
    _store(db, _row(1))
    _query(db, "INSERT INTO flora_check_runs (part, status) VALUES ('network', 'running')")
    entry_checks.run_structural(db)
    entry_checks._abandon_unfinished(db, "structural")
    assert [r["status"] for r in _query(db, "SELECT status FROM flora_check_runs "
                                            "WHERE part = 'network'")] == ["running"]


def test_a_network_run_lost_to_a_deploy_does_not_cost_the_day(db):
    _store(db, _row(1))
    entry_checks.run_structural(db)

    def network_run(status, started):
        _query(db, "DELETE FROM flora_check_runs WHERE part = 'network'")
        _query(db, "INSERT INTO flora_check_runs (part, status, started_at) VALUES ('network', %s, %s)",
               (status, started))

    network_run("running", _at(4, 10))
    assert _due(db, _at(4, 40)) == [], "still running, within its budget and slack"
    assert _due(db, _at(5, 10)) == ["network"], "its process died: the row is stale"
    network_run("abandoned", _at(4, 10))
    assert _due(db, _at(4, 15)) == ["network"], "abandoned (marked by the next run): run again"
    for status in ("finished", "incomplete", "failed"):
        network_run(status, _at(4, 10))
        assert _due(db, _at(9)) == [], status


# ── reading, for the FLoRA tab ────────────────────────────────────────────────

def test_findings_for_a_record_and_the_summary(db):
    _store(db, _row(1), _row(2, type="extension", year_r="1500"), _row(3, type="extension"))
    run = entry_checks.run_structural(db)
    other = validate_flora.finding_key("invalid_type", ["REPL-000003"], "type", "extension")
    _query(db, "UPDATE flora_check_findings SET dismissed_at = NOW(), dismissed_by = 'editor' "
               "WHERE finding_key = %s", (other,))
    with db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            mine = entry_checks.findings_for(cur, "REPL-000002")
            theirs = entry_checks.findings_for(cur, "REPL-000003")
            overview = entry_checks.summary(cur)
    assert TYPE_KEY in {f["finding_key"] for f in mine}
    assert all("REPL-000002" in f["flora_ids"] for f in mine)
    assert all(f["last_run_id"] == run["run_id"] and f["last_run_status"] == "finished" for f in mine)
    assert [f["finding_key"] for f in theirs] == [other] and theirs[0]["dismissed_by"] == "editor"
    invalid_type = next(c for c in overview["checks"] if c["check_code"] == "invalid_type")
    assert (invalid_type["open"], invalid_type["dismissed"]) == (1, 1)
    assert overview["dismissed"] == 1
    assert overview["open"]["problem"] == sum(
        c["open"] for c in overview["checks"] if c["level"] == "problem")
    assert overview["last_runs"]["structural"]["run_id"] == run["run_id"]
    assert overview["last_runs"]["network"] is None


# ── the command line ──────────────────────────────────────────────────────────

@pytest.fixture
def cli(monkeypatch):
    # The pipeline log would otherwise take this test process's output.
    monkeypatch.setattr(entry_checks, "start_logging", lambda component: None)
    return entry_checks.main


def test_the_command_line_says_what_is_due_and_runs_a_part(db, cli, capsys):
    _store(db, _row(1), _row(2, type="extension"))
    _network_ran_today(db)
    assert cli(["--due"]) == 0 and capsys.readouterr().out.strip() == "structural"
    assert cli(["--structural"]) == 0
    assert '"status": "finished"' in capsys.readouterr().out
    assert _open(db) == {TYPE_KEY}
    assert cli(["--due"]) == 0 and capsys.readouterr().out.strip() == "nothing due"


def test_the_command_line_runs_the_network_part_with_a_budget(db, cli, fake_network, capsys):
    _store(db, _row(1))
    assert cli(["--network", "--minutes", "0"]) == 0
    assert '"part": "network"' in capsys.readouterr().out


def test_the_command_line_refuses_minutes_without_network(cli):
    with pytest.raises(SystemExit):
        cli(["--structural", "--minutes", "5"])
    with pytest.raises(SystemExit):
        cli([])


def test_the_command_line_waits_for_a_run_elsewhere(db, cli, capsys):
    _store(db, _row(1))
    holder = psycopg2.connect(entry_checks.os.environ["DATABASE_URL"])
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (entry_checks.LOCK_IDS["structural"],))
        assert cli(["--structural"]) == 1
        assert "running elsewhere" in capsys.readouterr().err
    finally:
        holder.close()
    assert _query(db, "SELECT * FROM flora_check_runs") == []


# ── the website's scheduler ───────────────────────────────────────────────────

def _import_app():
    """app.py with the database and scheduler stubbed out: init_db() runs at import
    and would otherwise apply db_schema.sql to whatever DATABASE_URL names."""
    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 1}
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor
    with patch("psycopg2.connect", return_value=connection), patch(
        "apscheduler.schedulers.background.BackgroundScheduler.start"
    ):
        import app
    return app


def test_the_website_scheduler_runs_each_part_every_five_minutes_in_its_own_job():
    app = _import_app()
    schedulers = []
    with patch("apscheduler.schedulers.background.BackgroundScheduler.start",
               lambda self, *a, **kw: schedulers.append(self)):
        app._start_scheduler()
    jobs = {job.id: job for job in schedulers[0].get_jobs() if job.id.startswith("entry_checks")}
    assert set(jobs) == {"entry_checks_structural", "entry_checks_network"}
    for part, job in ((job.kwargs["parts"][0], job) for job in jobs.values()):
        assert job.id == f"entry_checks_{part}"
        assert job.func is entry_checks.run_due
        assert isinstance(job.trigger, IntervalTrigger)
        assert job.trigger.interval == timedelta(minutes=5)
        assert (job.max_instances, job.coalesce) == (1, True)
        assert job.kwargs == {"database_url": app.DATABASE_URL, "parts": (part,)}


def test_its_locks_are_ones_nothing_else_uses():
    import re
    used = [int(n.replace("_", "")) for path in final_export.ROOT.rglob("*.py")
            if not {".venv", "node_modules", "tests", ".git"} & set(path.relative_to(final_export.ROOT).parts)
            for n in re.findall(r"\b7_?342_?025_?\d{3}\b", path.read_text(encoding="utf-8", errors="ignore"))]
    assert len(set(entry_checks.LOCK_IDS.values())) == 2
    for lock_id in entry_checks.LOCK_IDS.values():
        assert used.count(lock_id) == 1, "named once, in entry_checks.py"
