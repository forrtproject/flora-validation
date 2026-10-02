"""The adjudication feature (fred-data PR #143), phase 1: its isolation.

The promise is that this feature can fail in any way without affecting the rest
of the app. These tests hold it to that: the switch, a setup that never raises,
a schema that touches nothing outside its own PostgreSQL schema, an app that
starts when the feature breaks, and a status route that reports instead of
failing.
"""
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import adjudication
from adjudication import bootstrap
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "adjudication" / "schema.sql").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The switch and the setup
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("value", "on"), [
    (None, False), ("", False), ("0", False), ("off", False), ("no", False),
    ("1", True), ("true", True), (" ON ", True), ("yes", True),
])
def test_the_feature_is_off_unless_switched_on(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv("ADJUDICATION_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ADJUDICATION_ENABLED", value)
    assert adjudication.feature_enabled() is on


def test_switched_off_it_never_touches_the_database(monkeypatch):
    monkeypatch.delenv("ADJUDICATION_ENABLED", raising=False)
    connect = MagicMock(side_effect=AssertionError("must not connect"))
    assert adjudication.setup("postgresql://x", connect=connect) == adjudication.SetupStatus(False, False)
    connect.assert_not_called()


def test_a_database_it_cannot_reach_leaves_the_feature_off(monkeypatch):
    monkeypatch.setenv("ADJUDICATION_ENABLED", "1")
    status = adjudication.setup("postgresql://x", connect=MagicMock(side_effect=OSError("refused")))
    assert (status.enabled, status.ready) == (True, False)
    assert "refused" in status.error


def test_failing_sql_is_rolled_back_and_reported_never_raised(monkeypatch):
    monkeypatch.setenv("ADJUDICATION_ENABLED", "1")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = [None, None, None, RuntimeError("syntax error at or near")]
    status = adjudication.setup("postgresql://x", connect=MagicMock(return_value=conn))
    assert status.ready is False and "syntax error" in status.error
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    conn.close.assert_called_once()


def test_setup_bounds_its_waits_and_serialises_across_pods(monkeypatch):
    monkeypatch.setenv("ADJUDICATION_ENABLED", "1")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    status = adjudication.setup("postgresql://x", connect=MagicMock(return_value=conn))
    assert status == adjudication.SetupStatus(True, True)
    sent = [call.args[0] for call in cur.execute.call_args_list]
    assert sent[0] == "SET LOCAL lock_timeout = '5s'"
    assert sent[1] == "SET LOCAL statement_timeout = '30s'"
    assert sent[2] == "SELECT pg_advisory_xact_lock(%s)"
    assert cur.execute.call_args_list[2].args[1] == (bootstrap.ADVISORY_LOCK_ID,)
    assert sent[3] == SCHEMA
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_failed_setup_is_retried_once_a_minute_without_a_restart():
    clock, results = _Clock(), [adjudication.SetupStatus(True, False, "LockNotAvailable"),
                                adjudication.SetupStatus(True, True)]
    calls = []

    def run_setup(url):
        calls.append(url)
        return results[len(calls) - 1]

    feature = adjudication.Feature("postgresql://x", run_setup=run_setup, clock=clock,
                                   spawn=lambda work: work())
    assert feature.status().ready is False and len(calls) == 1
    clock.now += 59
    assert feature.status().ready is False and len(calls) == 1      # not yet
    clock.now += 1
    assert feature.status().ready is True and len(calls) == 2       # retried, recovered
    clock.now += 3600
    assert feature.status().ready is True and len(calls) == 2       # ready: never again


def test_a_switched_off_feature_is_never_set_up_again():
    clock, calls = _Clock(), []
    feature = adjudication.Feature("postgresql://x", clock=clock,
                                   run_setup=lambda url: calls.append(url) or adjudication.SetupStatus(False, False))
    clock.now += 10_000
    assert feature.status().enabled is False and len(calls) == 1


def test_the_retry_runs_in_the_background_and_only_once_at_a_time():
    clock, calls, spawned = _Clock(), [], []
    feature = adjudication.Feature("postgresql://x", clock=clock, spawn=spawned.append,
                                   run_setup=lambda url: calls.append(url) or adjudication.SetupStatus(True, False, "x"))
    clock.now += 60
    # The request that finds a retry due answers at once, with the last state.
    assert feature.status().error == "x" and len(spawned) == 1 and len(calls) == 1
    clock.now += 120
    assert feature.status().error == "x" and len(spawned) == 1, "one retry at a time"
    spawned[0]()                             # the background retry runs and fails again
    assert len(calls) == 2
    assert feature.status().error == "x" and len(spawned) == 2, "due again, retried again"


def test_setup_gives_up_on_a_database_it_cannot_reach_quickly(monkeypatch):
    monkeypatch.setenv("ADJUDICATION_ENABLED", "1")
    connect = MagicMock(side_effect=OSError("timeout expired"))
    assert adjudication.setup("postgresql://x", connect=connect).ready is False
    assert connect.call_args.kwargs == {"connect_timeout": bootstrap.CONNECT_TIMEOUT}


def test_its_locks_are_not_ones_the_app_already_uses():
    from adjudication import importer, judging
    skip = {".venv", "node_modules", "adjudication", "tests", ".git"}
    sources = [p for p in ROOT.rglob("*.py") if not skip & set(p.relative_to(ROOT).parts)]
    used = {int(n.replace("_", "")) for p in sources
            for n in re.findall(r"\b\d{1,3}(?:_\d{3})+\b", p.read_text(encoding="utf-8", errors="ignore"))}
    assert 7_342_025_093 in used, "the scan finds the app's own locks"
    mine = {bootstrap.ADVISORY_LOCK_ID, importer.IMPORT_LOCK_ID, judging.JUDGING_LOCK_ID}
    assert len(mine) == 3 and not mine & used


# ---------------------------------------------------------------------------
# The schema touches nothing outside its own PostgreSQL schema
# ---------------------------------------------------------------------------

def _statements(sql: str) -> list[str]:
    without_comments = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def test_every_object_it_creates_is_in_the_adjudication_schema():
    statements = _statements(SCHEMA)
    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS adjudication"
    for statement in statements[1:]:
        head = " ".join(statement.split()[:8])
        # Later phases upgrade the live tables in place: ADD COLUMN IF NOT EXISTS.
        if head.startswith("ALTER TABLE"):
            assert re.match(r"ALTER TABLE adjudication\.\w+ ADD COLUMN IF NOT EXISTS ", head + " "), head
            continue
        assert re.match(r"CREATE (TABLE|INDEX) IF NOT EXISTS \S+ ", head + " "), head
        target = re.search(r"(?:TABLE IF NOT EXISTS|ON) (\S+)", statement).group(1)
        assert target.startswith("adjudication."), head


def test_it_never_alters_drops_or_references_the_main_tables():
    body = re.sub(r"--[^\n]*", "", SCHEMA)
    # The only DELETE allowed is the in-schema cascade of a foreign key, and the
    # only ALTER an in-place upgrade of one of the feature's own tables.
    allowed = re.sub(r"ALTER TABLE adjudication\.\w+ ADD COLUMN IF NOT EXISTS", "",
                     body.replace("ON DELETE CASCADE", ""))
    assert not re.search(r"\b(ALTER|DROP|TRUNCATE|DELETE|UPDATE|INSERT|GRANT)\b",
                         allowed, re.I)
    for target in re.findall(r"REFERENCES\s+(\S+)", body):
        assert target.startswith("adjudication."), f"a link out of the schema: {target}"


def test_the_main_schema_file_knows_nothing_about_it():
    """db_schema.sql runs at every start and a failure there stops the app."""
    assert "adjudication" not in (ROOT / "db_schema.sql").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The app starts whatever happens to the feature
# ---------------------------------------------------------------------------

def test_app_py_loads_it_inside_one_safety_net_before_the_static_mount():
    block = APP.split("# Adjudication of the FLoRA / Observatory disagreements", 1)[1]
    block = block.split('app.mount("/", StaticFiles', 1)[0]
    assert "\ntry:\n" in block
    assert "import adjudication" in block.split("except Exception:", 1)[0]
    assert "adjudication.Feature(DATABASE_URL)" in block
    assert "status=_adjudication.status" in block
    assert "except Exception:" in block
    assert APP.index("import adjudication") > APP.index("\ninit_db()\n")


_IMPORT_APP = """
import os, sys
from unittest.mock import MagicMock, patch
os.environ.update(DATABASE_URL="postgresql://stub/stub",
                  ADMIN_PASSWORD="bootstrap-password-for-import",
                  ADJUDICATION_ENABLED="1")
sys.path.insert(0, {root!r})
{breakage}
cursor = MagicMock()
cursor.fetchone.return_value = {{"n": 1}}
cursor.fetchall.return_value = []
connection = MagicMock()
connection.cursor.return_value = cursor
with patch("psycopg2.connect", return_value=connection), \\
     patch("apscheduler.schedulers.background.BackgroundScheduler.start"):
    import app
def walk(routes):
    # FastAPI 0.141 keeps an included router as one _IncludedRouter entry.
    for r in routes:
        yield getattr(r, "path", "")
        inner = getattr(getattr(r, "original_router", None), "routes", None)
        if inner:
            yield from walk(inner)
paths = set(walk(app.app.routes))
print("STARTED", "/api/me" in paths, "/api/admin/disagreements/status" in paths)
"""


@pytest.mark.parametrize(("breakage", "route_expected"), [
    # setup() is documented never to raise; if setting up failed anyway.
    ("import adjudication\nadjudication.Feature = lambda *a, **k: 1 / 0", False),
    # The package cannot even be imported (a broken deploy, a syntax error).
    ("sys.modules['adjudication'] = None", False),
    # The router cannot be built.
    ("import adjudication\nadjudication.create_router = lambda **k: (_ for _ in ()).throw(RuntimeError('x'))", False),
    # Nothing broken: the feature loads alongside the app.
    ("", True),
])
def test_the_app_starts_whatever_goes_wrong_in_the_feature(breakage, route_expected):
    script = _IMPORT_APP.format(root=str(ROOT), breakage=breakage)
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=240,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr[-2000:]
    assert f"STARTED True {route_expected}" in result.stdout, result.stdout[-2000:]


# ---------------------------------------------------------------------------
# The status route reports instead of failing
# ---------------------------------------------------------------------------

def _client(status, cursor=None, signed_in=True):
    def current_admin():
        if not signed_in:
            raise HTTPException(401, "Unauthorized")
        return {"id": 1, "handle": "Hamid"}

    app = FastAPI()
    app.include_router(adjudication.create_router(
        current_admin=current_admin, current_validator=lambda: {"coder_id": 7, "validator_tier": 1},
        status=lambda: status,
        cursor=cursor or (lambda: (_ for _ in ()).throw(AssertionError("no query expected")))))
    return TestClient(app)


class _Cursor:
    def __init__(self, row=None, error=None):
        self.row, self.error = row, error

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql):
        assert "adjudication.records" in sql
        self.last = sql
        if self.error:
            raise self.error

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [{"kind": "different original", "n": 50}, {"kind": "we found no original", "n": 54}]


def test_status_needs_an_admin():
    response = _client(adjudication.SetupStatus(True, True), signed_in=False).get(
        "/api/admin/disagreements/status")
    assert response.status_code == 401


def test_status_when_switched_off_does_not_query():
    body = _client(adjudication.SetupStatus(False, False)).get("/api/admin/disagreements/status").json()
    assert {k: body[k] for k in ("enabled", "ready", "error", "counts")} == \
        {"enabled": False, "ready": False, "error": None, "counts": None}
    assert body["source"].startswith("forrtproject/fred-data@55d6f04:")


def test_status_when_ready_counts_the_work():
    row = {"records": 159, "open": 150, "awaiting_approval": 6, "approved": 2,
           "published": 1, "judgements": 20}
    body = _client(adjudication.SetupStatus(True, True), _Cursor(row)).get(
        "/api/admin/disagreements/status").json()
    assert body["ready"] is True and body["counts"] == row
    assert body["by_kind"] == {"different original": 50, "we found no original": 54}
    assert body["source_url"].startswith("https://raw.githubusercontent.com/forrtproject/fred-data/55d6f04")


def test_status_when_the_tables_cannot_be_read_says_so():
    body = _client(adjudication.SetupStatus(True, True), _Cursor(error=RuntimeError("relation missing"))).get(
        "/api/admin/disagreements/status").json()
    assert body["ready"] is False and body["counts"] is None
    assert "relation missing" in body["error"]


def test_status_reports_a_failed_setup():
    body = _client(adjudication.SetupStatus(True, False, "LockNotAvailable: timeout")).get(
        "/api/admin/disagreements/status").json()
    assert {k: body[k] for k in ("enabled", "ready", "error", "counts")} == \
        {"enabled": True, "ready": False, "error": "LockNotAvailable: timeout", "counts": None}


# ---------------------------------------------------------------------------
# Against a real PostgreSQL, when one is offered (FLORA_TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

def test_the_schema_applies_twice_and_drops_cleanly_on_postgres(local_database, monkeypatch):  # noqa: F811
    """On a throwaway database of its own (local_database: localhost only), never
    on whatever FLORA_TEST_DATABASE_URL names: it drops the schema."""
    import psycopg2

    url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("ADJUDICATION_ENABLED", "1")
    assert adjudication.setup(url).ready
    assert adjudication.setup(url).ready
    conn = psycopg2.connect(url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'adjudication' ORDER BY 1")
            assert [r[0] for r in cur.fetchall()] == ["final", "judgements", "records"]
            cur.execute("DROP SCHEMA adjudication CASCADE")
    finally:
        conn.close()
