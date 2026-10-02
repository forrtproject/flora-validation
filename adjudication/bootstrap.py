"""Switch the adjudication feature on and prepare its schema, without ever failing.

app.py calls setup() once at import, after its own init_db(). Whatever goes
wrong here (the switch is off, the database refuses, the SQL fails, a lock is
held) ends in a SetupStatus that says so, never in an exception: the feature
stays off and the rest of the app starts as if it did not exist.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psycopg2

ENABLED_ENV = "ADJUDICATION_ENABLED"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# Serialises the schema across workers and pods, as SCHEMA_INIT_ADVISORY_LOCK_ID
# does for db_schema.sql; a transaction lock, so it is released with the commit.
ADVISORY_LOCK_ID = 7_342_025_094
# A start must never hang on this feature: give up on a held lock or a slow
# statement and leave the feature off instead.
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "30s"
CONNECT_TIMEOUT = 10      # seconds
_ERROR_LIMIT = 300


@dataclass(frozen=True)
class SetupStatus:
    enabled: bool          # ADJUDICATION_ENABLED is on
    ready: bool            # its schema is in place and the feature can be used
    error: str | None = None


def feature_enabled() -> bool:
    """ADJUDICATION_ENABLED: off unless set to 1/true/yes/on."""
    return os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _failed(exc: BaseException) -> SetupStatus:
    print("[adjudication] setup failed; the feature stays off and the rest of the "
          "app is unaffected:")
    traceback.print_exception(exc)
    message = f"{type(exc).__name__}: {exc}".strip()
    return SetupStatus(enabled=True, ready=False, error=message[:_ERROR_LIMIT])


def setup(database_url: str, *, connect: Callable = psycopg2.connect) -> SetupStatus:
    """Apply adjudication/schema.sql when the feature is switched on."""
    if not feature_enabled():
        return SetupStatus(enabled=False, ready=False)
    try:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn = connect(database_url, connect_timeout=CONNECT_TIMEOUT)
    except Exception as exc:
        return _failed(exc)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_ID,))
            cur.execute(sql)
        conn.commit()
        return SetupStatus(enabled=True, ready=True)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return _failed(exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="adjudication-setup", daemon=True).start()


class Feature:
    """The feature's setup state for the life of the process.

    A setup that failed (a brief database outage at start, a lock held during a
    deploy) is tried again when the feature is next asked about, at most once a
    minute, so the feature recovers without a restart. The retry runs in the
    background: the request that notices it is due answers at once with the last
    known state. A feature that is switched off, or already ready, is never set
    up again.
    """

    RETRY_SECONDS = 60

    def __init__(self, database_url: str, *, run_setup: Callable = setup,
                 clock: Callable[[], float] = time.monotonic,
                 spawn: Callable[[Callable[[], None]], None] = _in_background):
        self._database_url = database_url
        self._run_setup = run_setup
        self._clock = clock
        self._spawn = spawn
        self._lock = threading.Lock()
        self._retrying = False
        self._status = run_setup(database_url)
        self._tried_at = clock()

    def status(self) -> SetupStatus:
        state = self._status
        if not state.enabled or state.ready:
            return state
        with self._lock:
            if self._retrying or self._clock() - self._tried_at < self.RETRY_SECONDS:
                return self._status
            self._retrying = True
            self._tried_at = self._clock()
        self._spawn(self._retry)
        return self._status

    def _retry(self) -> None:
        try:
            self._status = self._run_setup(self._database_url)
        finally:
            with self._lock:
                self._retrying = False
