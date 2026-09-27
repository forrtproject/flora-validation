"""Database egress regressions.

Supabase bills every byte the database sends back — query results and the setup
traffic of every new connection alike. Each check here pins down one place that
used to re-download or reconnect when nothing required it:

- the FLoRA tab re-read every source row and every cached abstract on each
  preprint ruling;
- db() opened, and closed, a fresh connection for every block;
- two scheduler jobs opened a fresh connection every 5s and 10s to learn that
  their queues were empty;
- browser tabs kept polling while nobody was looking at them.

The checks marked with local_database need a real server and are opt-in like
tests/test_preparation_database.py: set FLORA_TEST_DATABASE_URL to an isolated
localhost PostgreSQL admin database.
"""
import csv
import inspect
import os
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import psycopg2
import pytest
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.extras import RealDictCursor

import bibliographic_helpers
import db_pool
import enrich_works
import extractor_maintenance
import final_export
import flora_public_api
import flora_service
import source_sync_runner
import transform_sources
from tests.test_preparation_database import add_source, local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


# ── the FLoRA tab's cached build inputs ───────────────────────────────────────

def _probe(**changes):
    fields = dict(n_source=10, max_updated="t1", n_ruled=0, n_flora=10, n_excluded=0,
                  registry_updated="r1", n_metadata="5", metadata_updated="m1",
                  references_updated="x1", exclusions="e1", aliases="a1",
                  dedup_decisions="d1")
    fields.update(changes)
    return flora_service._Signature(**fields)


@pytest.fixture
def counted(monkeypatch):
    """_current() with the database replaced by counters."""
    calls = {"load": 0, "metadata": 0, "build": 0}
    probe = {"value": _probe(), "files": (("seed", 1), ("manual", 1))}

    def load(cur):
        calls["load"] += 1
        return pd.DataFrame([{"record_id": "a"}])

    def load_metadata(cur):
        calls["metadata"] += 1
        return {"10.1/x": {"title": "T"}}

    def build(cur, verbose=True, review_issue=False, *, rows=None, metadata=None):
        calls["build"] += 1
        assert rows is not None and metadata is not None, "the tab must pass its cached inputs"
        return pd.DataFrame()

    monkeypatch.setattr(flora_service, "_signature", lambda cur: probe["value"])
    monkeypatch.setattr(bibliographic_helpers, "reference_files_version", lambda: probe["files"])
    monkeypatch.setattr(transform_sources, "load", load)
    monkeypatch.setattr(transform_sources, "build", build)
    monkeypatch.setattr(enrich_works, "load_metadata", load_metadata)
    flora_service.invalidate()
    yield calls, probe
    flora_service.invalidate()


def test_an_unchanged_probe_serves_the_cached_frame(counted):
    calls, _ = counted
    flora_service.dataset(None)
    flora_service.dataset(None)
    assert calls == {"load": 1, "metadata": 1, "build": 1}


@pytest.mark.parametrize("change", [
    {"dedup_decisions": "d2"},       # a preprint ruling
    {"exclusions": "e2"},            # an exclusion added
    {"aliases": "a2"},               # an outcome alias edited
    {"registry_updated": "r2", "n_flora": 11},   # a registry refresh
])
def test_a_small_table_change_rebuilds_without_re_reading_the_inputs(counted, change):
    calls, probe = counted
    flora_service.dataset(None)
    probe["value"] = _probe(**change)
    flora_service.dataset(None)
    assert calls == {"load": 1, "metadata": 1, "build": 2}


@pytest.mark.parametrize("change", [{"max_updated": "t2"}, {"n_source": 11}, {"n_ruled": 1},
                                    {"source_versions": "123"}])
def test_a_source_record_change_re_reads_only_the_source_rows(counted, change):
    calls, probe = counted
    flora_service.dataset(None)
    probe["value"] = _probe(**change)
    flora_service.dataset(None)
    assert calls == {"load": 2, "metadata": 1, "build": 2}


@pytest.mark.parametrize("change", [{"n_metadata": "6"}, {"metadata_updated": "m2"},
                                    {"references_updated": "x2"}, {"metadata_versions": "456"}])
def test_a_metadata_change_re_reads_only_the_metadata(counted, change):
    calls, probe = counted
    flora_service.dataset(None)
    probe["value"] = _probe(**change)
    flora_service.dataset(None)
    assert calls == {"load": 1, "metadata": 2, "build": 2}


def test_a_swapped_reference_file_re_reads_the_metadata_with_nothing_else_changed(counted):
    """The seed and the manual-reference workbook are merged into the metadata,
    so replacing either must show without waiting for a database change."""
    calls, probe = counted
    flora_service.dataset(None)
    probe["files"] = (("seed", 1), ("manual", 2))
    flora_service.dataset(None)
    assert calls == {"load": 1, "metadata": 2, "build": 2}


def test_a_late_committing_update_still_changes_the_signature(local_database):
    """NOW() is a transaction's start time. A slow transaction that began before a
    quick edit but commits after it lands with the older updated_at, so the
    maximum does not move; the row versions do."""
    with final_export.REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        reference = list(csv.DictReader(handle))
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            slow_id = add_source(cur, reference[0], "REPL-000001")
            quick_id = add_source(cur, reference[1], "REPL-000002")
    dsn = os.environ["DATABASE_URL"]
    slow, quick = psycopg2.connect(dsn), psycopg2.connect(dsn)
    try:
        with slow.cursor() as cur:
            cur.execute("SELECT 1")            # the slow transaction starts here
        time.sleep(0.05)
        with quick, quick.cursor() as cur:
            cur.execute("UPDATE source_records SET ref_o = 'quick', updated_at = NOW() "
                        "WHERE record_id = %s", (quick_id,))

        def probe():
            with local_database.cursor(cursor_factory=RealDictCursor) as cur:
                signature = flora_service._signature(cur)
            local_database.commit()
            return signature

        before = probe()
        with slow.cursor() as cur:
            cur.execute("UPDATE source_records SET ref_o = 'slow', updated_at = NOW() "
                        "WHERE record_id = %s", (slow_id,))
        slow.commit()
        after = probe()
    finally:
        slow.close()
        quick.close()
    assert after.max_updated == before.max_updated, "the scenario needs an unmoved maximum"
    assert after.rows_key() != before.rows_key()


def test_both_separately_cached_tables_carry_a_row_version_sum():
    probe = inspect.getsource(flora_service._signature)
    for table in ("source_records", "work_metadata"):
        assert f"SUM(xmin::text::bigint) FROM {table}" in probe


def test_invalidate_drops_the_cached_inputs_too(counted):
    calls, _ = counted
    flora_service.dataset(None)
    flora_service.invalidate()
    flora_service.dataset(None)
    assert calls == {"load": 2, "metadata": 2, "build": 2}


def test_prebuilt_inputs_give_the_same_frame_and_are_left_untouched(local_database, monkeypatch):
    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    with final_export.REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        reference = list(csv.DictReader(handle))
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            add_source(cur, reference[0], "REPL-000001")
            add_source(cur, reference[1], "REPL-000002")
    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        fresh = transform_sources.build(cur, verbose=False)
        rows = transform_sources.load(cur)
        untouched = rows.copy()
        metadata = enrich_works.load_metadata(cur)
        reused = transform_sources.build(cur, verbose=False, rows=rows, metadata=metadata)
    assert len(fresh) == 2
    pd.testing.assert_frame_equal(reused, fresh)
    pd.testing.assert_frame_equal(rows, untouched)


# ── db_pool ───────────────────────────────────────────────────────────────────

def test_a_stand_in_connection_is_never_pooled():
    # The suite's import stubs hand app.py MagicMock connections; they must not
    # end up in the idle list for a later test to borrow.
    pool = db_pool._Pool("postgresql://unused")
    conn = MagicMock()
    pool.put(conn, time.monotonic())
    assert pool._idle == []
    conn.close.assert_called()


@pytest.mark.parametrize("arrives,alive", [(None, True),
                                           ("SCHWERWIEGEND:  Verbindung wird abgebrochen\n", False)])
def test_any_message_on_an_idle_connection_counts_as_dead(monkeypatch, arrives, alive):
    """Recognised without reading its text, which a server may translate, and
    even when psycopg2's 50-entry notice list was already full."""
    monkeypatch.setattr(db_pool, "_reusable", lambda conn: True)   # only the poll decides
    conn = MagicMock()
    conn.notices = ["NOTICE:  left by the previous borrower\n"] * 50
    conn.poll.side_effect = lambda: arrives and conn.notices.append(arrives)
    assert db_pool._alive(conn) is alive


def test_the_idle_limit_is_read_when_a_pool_is_made_not_at_import(monkeypatch):
    # app.py imports db_pool before load_dotenv() runs.
    monkeypatch.delenv("DB_POOL_MAX_IDLE", raising=False)
    assert db_pool._Pool("postgresql://unused")._max_idle == db_pool.DEFAULT_MAX_IDLE
    monkeypatch.setenv("DB_POOL_MAX_IDLE", "7")
    assert db_pool._Pool("postgresql://unused")._max_idle == 7
    monkeypatch.setenv("DB_POOL_MAX_IDLE", "lots")
    with pytest.raises(ValueError, match="DB_POOL_MAX_IDLE"):
        db_pool._Pool("postgresql://unused")


@pytest.fixture
def pool_dsn(local_database):
    yield os.environ["DATABASE_URL"]
    db_pool.clear_all()


def _backend_pid(dsn):
    with db_pool.cursor(dsn) as cur:
        cur.execute("SELECT pg_backend_pid() AS pid")
        return cur.fetchone()["pid"]


def test_sequential_blocks_reuse_one_connection(pool_dsn):
    # Same server process every time: no new connection, so no new handshake.
    first = _backend_pid(pool_dsn)
    for _ in range(50):
        assert _backend_pid(pool_dsn) == first


@pytest.mark.parametrize("ending", ["commit", "open", "failed"])
def test_only_a_connection_between_transactions_is_pooled(pool_dsn, ending):
    pool = db_pool._Pool(pool_dsn)
    conn, opened = pool.get()
    with conn.cursor() as cur:
        if ending == "failed":
            with pytest.raises(psycopg2.errors.DivisionByZero):
                cur.execute("SELECT 1/0")
        else:
            cur.execute("SELECT 1")
    if ending == "commit":
        conn.commit()
    pool.put(conn, opened)
    try:
        assert [entry[0] for entry in pool._idle] == ([conn] if ending == "commit" else [])
        assert bool(conn.closed) == (ending != "commit")
    finally:
        pool.clear()


def test_a_cursor_kept_past_its_block_cannot_run_a_query(pool_dsn):
    """It would otherwise run inside whichever transaction borrows the connection next."""
    with db_pool.cursor(pool_dsn) as cur:
        cur.execute("SELECT 1")
    with pytest.raises(psycopg2.InterfaceError):
        cur.execute("SELECT 1")


class _GracefulCloseProxy:
    """Forwards to the test server and passes its close on as an ordinary FIN,
    the way a Linux host delivers it. On Windows the server's close tends to
    arrive as a reset instead, which hides the case this exists for: the FATAL
    message and the close arriving separately."""

    def __init__(self, host, port):
        self._target = (host, port)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._sockets = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(self._target)
            self._sockets += [client, upstream]
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=(source, sink), daemon=True).start()

    @staticmethod
    def _pump(source, sink):
        try:
            while data := source.recv(65536):
                sink.sendall(data)
        except OSError:
            pass
        try:
            sink.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def close(self):
        self._listener.close()
        for sock in self._sockets:
            try:
                sock.close()
            except OSError:
                pass


@pytest.fixture(params=["direct", "graceful close"])
def closing_dsn(request, pool_dsn):
    """The test database, reached directly and through _GracefulCloseProxy."""
    if request.param == "direct":
        yield pool_dsn
        return
    params = parse_dsn(pool_dsn)
    proxy = _GracefulCloseProxy(params.get("host") or "localhost", int(params.get("port") or 5432))
    try:
        yield make_dsn(pool_dsn, host="127.0.0.1", port=proxy.port)
    finally:
        db_pool.clear_all()
        proxy.close()


def _wait_until_gone(admin, pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_stat_activity WHERE pid = %s", (pid,))
            if cur.fetchone() is None:
                return
        time.sleep(0.05)
    pytest.fail(f"backend {pid} never exited")


def test_a_connection_the_server_closed_is_replaced_without_an_error(closing_dsn, local_database):
    victim = _backend_pid(closing_dsn)
    local_database.autocommit = True
    with local_database.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(%s)", (victim,))
    _wait_until_gone(local_database, victim)
    time.sleep(0.1)                     # let the close cross the proxy too
    assert _backend_pid(closing_dsn) != victim


def test_a_connection_ended_by_idle_session_timeout_is_replaced(closing_dsn, local_database):
    """What a server-side idle limit does to a pooled connection overnight."""
    local_database.autocommit = True
    with local_database.cursor() as cur:
        cur.execute("SELECT current_database()")
        name = cur.fetchone()[0]
        # Applies to sessions opened from now on: the pool's, not this one.
        cur.execute(sql.SQL("ALTER DATABASE {} SET idle_session_timeout = '300ms'")
                    .format(sql.Identifier(name)))
    first = _backend_pid(closing_dsn)
    _wait_until_gone(local_database, first)
    time.sleep(0.1)
    assert _backend_pid(closing_dsn) != first


def test_a_rolled_back_error_leaves_the_connection_reusable(pool_dsn):
    pid = _backend_pid(pool_dsn)
    with pytest.raises(psycopg2.errors.DivisionByZero):
        with db_pool.cursor(pool_dsn) as cur:
            cur.execute("SELECT 1/0")
    assert _backend_pid(pool_dsn) == pid


def test_a_connection_left_in_autocommit_is_not_handed_out_again(pool_dsn):
    with db_pool.cursor(pool_dsn) as cur:
        cur.connection.autocommit = True
        cur.execute("SELECT pg_backend_pid() AS pid")
        changed = cur.fetchone()["pid"]
    assert _backend_pid(pool_dsn) != changed


def test_nested_blocks_never_share_a_connection(pool_dsn):
    with db_pool.cursor(pool_dsn) as outer:
        outer.execute("SELECT pg_backend_pid() AS pid")
        with db_pool.cursor(pool_dsn) as inner:
            inner.execute("SELECT pg_backend_pid() AS pid")
            assert inner.fetchone()["pid"] != outer.fetchone()["pid"]


def test_the_public_api_returns_its_connection_in_the_default_mode(pool_dsn):
    with flora_public_api.readonly_database() as cur:
        cur.execute("SELECT pg_backend_pid() AS pid")
        public = cur.fetchone()["pid"]
    with db_pool.cursor(pool_dsn) as cur:
        cur.execute("SELECT pg_backend_pid() AS pid, "
                    "current_setting('transaction_read_only') AS read_only, "
                    "current_setting('transaction_isolation') AS isolation")
        row = cur.fetchone()
    assert row["pid"] == public, "the public API should reuse pooled connections"
    assert (row["read_only"], row["isolation"]) == ("off", "read committed")


def test_db_goes_through_the_pool():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    body = source[source.index("def db():"):]
    body = body[:body.index("\n\n\n")]
    assert "db_pool.cursor(DATABASE_URL)" in body
    assert "psycopg2.connect" not in body


# ── queue dispatchers ─────────────────────────────────────────────────────────

def test_an_empty_sync_queue_opens_no_dedicated_connection(monkeypatch):
    monkeypatch.setattr(source_sync_runner, "_has_queued_job", lambda url: False)
    monkeypatch.setattr(source_sync_runner.psycopg2, "connect",
                        lambda *a, **kw: pytest.fail("opened a connection for an empty queue"))
    source_sync_runner.run_queued("postgresql://unused")


def test_a_queued_sync_job_still_takes_its_lock_connection(monkeypatch):
    opened = []

    def connect(*args, **kwargs):
        opened.append(args)
        raise RuntimeError("stop before running anything")

    monkeypatch.setattr(source_sync_runner, "_has_queued_job", lambda url: True)
    monkeypatch.setattr(source_sync_runner.psycopg2, "connect", connect)
    source_sync_runner.run_queued("postgresql://unused")
    assert opened


def test_an_empty_maintenance_queue_skips_the_dispatcher(monkeypatch):
    monkeypatch.setattr(extractor_maintenance, "_has_pending_run", lambda url: False)
    monkeypatch.setattr(extractor_maintenance, "dispatch_queued_run",
                        lambda *a, **kw: pytest.fail("dispatched for an empty queue"))
    extractor_maintenance.run_queued("postgresql://unused")


def test_a_pending_maintenance_run_still_dispatches(monkeypatch, tmp_path):
    dispatched = []
    monkeypatch.setattr(extractor_maintenance, "_has_pending_run", lambda url: True)
    monkeypatch.setattr(extractor_maintenance, "dispatch_queued_run",
                        lambda url, data_dir: dispatched.append(url))
    extractor_maintenance.run_queued("postgresql://unused", data_dir=tmp_path)
    assert dispatched == ["postgresql://unused"]


def test_the_maintenance_precheck_asks_for_exactly_what_the_dispatcher_selects():
    """A narrower precheck would strand a 'running' row the dispatcher recovers."""
    precheck = inspect.getsource(extractor_maintenance._has_pending_run)
    selection = inspect.getsource(extractor_maintenance._prepare_durable_run)
    assert "status IN ('queued', 'running')" in precheck
    assert "status IN ('queued', 'running')" in selection


def test_the_prechecks_see_queued_work(local_database, pool_dsn):
    assert source_sync_runner._has_queued_job(pool_dsn) is False
    assert extractor_maintenance._has_pending_run(pool_dsn) is False
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            source_sync_runner.queue_run(cur, "egress-test")
    extractor_maintenance.queue_maintenance_run(pool_dsn, "sync", trigger="admin",
                                                requested_by="egress-test")
    assert source_sync_runner._has_queued_job(pool_dsn) is True
    assert extractor_maintenance._has_pending_run(pool_dsn) is True


# ── browser polling ───────────────────────────────────────────────────────────

APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def _js_function(signature):
    start = APP_JS.index(signature)
    return APP_JS[start:APP_JS.index("\n}\n", start)]


@pytest.mark.parametrize("signature", [
    "function startAssignmentsPoll()",
    "function openInbox()",
    "function startMaintenanceSystem()",
    "async function fetchMaintenanceRuns()",
    "async function fetchSourceSync()",
])
def test_browser_pollers_skip_hidden_tabs(signature):
    assert "_pageHidden()" in _js_function(signature)


def test_the_banner_is_polled_every_five_minutes():
    body = _js_function("function startMaintenanceSystem()")
    assert "5 * 60_000" in body
    assert "setInterval(_pollAdminBanner, 60_000)" not in body
    assert "clearInterval(_bann._pollInterval)" in body


def test_a_tab_shown_again_catches_up_exactly_the_pollers_that_missed_a_tick():
    """No 30-second grace period: a tick that fell into a short absence is still
    caught up, and an absence no tick fell into costs nothing."""
    assert '_missedWhileHidden.add("assignments")' in _js_function("function startAssignmentsPoll()")
    assert '_missedWhileHidden.add("banner")' in _js_function("function startMaintenanceSystem()")
    start = APP_JS.index('document.addEventListener("visibilitychange", () => {')
    listener = APP_JS[start:APP_JS.index("\n});\n", start)]
    assert '_missedWhileHidden.delete("assignments")' in listener and "refreshAssignments()" in listener
    assert '_missedWhileHidden.delete("banner")' in listener and "_pollAdminBanner()" in listener
    assert "_hiddenSince" not in APP_JS


@pytest.mark.parametrize("signature,timer", [
    ("async function fetchMaintenanceRuns()", "_maintenancePollTimer"),
    ("async function fetchSourceSync()", "_srcSyncPollTimer"),
])
def test_a_hidden_tab_re_arms_one_timer_chain(signature, timer):
    body = _js_function(signature)
    hidden = body[body.index("if (_pageHidden()"):]
    hidden = hidden[:hidden.index("return;")]
    assert f"clearTimeout({timer})" in hidden
