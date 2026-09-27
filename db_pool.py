"""db_pool.py — reuse PostgreSQL connections instead of opening one per query.

WHY
---
Every `with db()` used to open a fresh connection and close it again: TCP, TLS
(the server's certificate chain), the password exchange and a dozen parameter
messages, all before the first query. Supabase bills that setup traffic as egress
exactly like query results, and the app paid it on every API request, every
browser poll and every scheduler tick — tens of thousands of times a day while
nobody was doing anything.

This keeps a few idle connections per process and hands them out again. It never
blocks and never refuses: with none idle, a new connection is opened exactly as
before. A connection goes back on the idle list only when it is open, outside a
transaction, still in the default mode, and the list is not full; anything else
is closed, as every connection used to be.

WHAT MAY BE POOLED
------------------
Only connections used the way db() uses them: one transaction, committed or
rolled back. Code that sets session state — session advisory locks, set_session,
autocommit — opens its own connection with psycopg2.connect and never comes
through here, so a later borrower can never inherit a lock or a read-only mode.
"""
import os
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
import psycopg2.extras

# Idle connections kept per process (per database URL), unless DB_POOL_MAX_IDLE
# says otherwise. Busy connections are not capped: a burst opens what it needs
# and the surplus is closed when returned. Small on purpose: on Supabase's
# session-mode pooler every idle client holds one of a few server slots.
DEFAULT_MAX_IDLE = 2
# Reconnect rather than trust a connection unused for this long: a proxy or NAT
# on the way to the database may have dropped it without telling either end.
MAX_IDLE_SECONDS = 300
# Bounds one server backend's lifetime too, so its caches cannot grow for days.
MAX_AGE_SECONDS = 1800
# keepalives: keep an idle connection's NAT mapping alive, and notice a dead peer.
# tcp_user_timeout: a query sent into a connection the network dropped silently
# fails after 30s instead of waiting out TCP retransmission, ~15 minutes on
# Linux. It counts only unacknowledged data, so a slow query is not affected.
# (No effect where the OS lacks TCP_USER_TIMEOUT, e.g. Windows.)
_CONNECT_OPTIONS = {"keepalives": 1, "keepalives_idle": 60,
                    "keepalives_interval": 10, "keepalives_count": 3,
                    "tcp_user_timeout": 30_000}


def _max_idle() -> int:
    """Read when a pool is made, not at import: app.py imports this module
    before load_dotenv() runs, so an import-time read would miss .env."""
    raw = os.environ.get("DB_POOL_MAX_IDLE", "").strip()
    if not raw:
        return DEFAULT_MAX_IDLE
    try:
        return max(0, int(raw))
    except ValueError:
        raise ValueError(f"DB_POOL_MAX_IDLE must be a whole number, got {raw!r}") from None


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _reusable(conn) -> bool:
    """Open, between transactions, and in the mode db() hands out."""
    try:
        return (conn.closed == 0 and conn.autocommit is False
                and conn.info.transaction_status == psycopg2.extensions.TRANSACTION_STATUS_IDLE)
    except Exception:
        return False


def _alive(conn) -> bool:
    """Catches a connection the server closed while it sat idle (a restart or
    failover, an admin kill, idle_session_timeout). poll() only reads what is
    already on the socket: no round trip.

    A server ending a session sends a FATAL message, then closes. One poll() can
    read the message and stop before the close: libpq files it as a notice, and
    the connection still looks open until the next borrower's query hits the
    close. That is the usual order on Linux; on Windows the close tends to
    arrive as a reset, which the poll() itself reports. So any message that
    arrived while the connection sat idle counts as dead. Nothing else is sent
    to an idle session, and matching the text would break on a server whose
    messages are translated. The list is cleared first because psycopg2 keeps
    only the last 50 notices, and a full one would hide a new arrival.
    """
    try:
        conn.notices.clear()
        conn.poll()
    except Exception:
        return False
    if conn.notices:
        return False
    return _reusable(conn)


class _Pool:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._max_idle = _max_idle()
        self._lock = threading.Lock()
        # (connection, opened_at, returned_at), oldest return first.
        self._idle: list = []

    def _expired(self, now: float) -> list:
        """Pop idle entries past either limit. Caller holds the lock."""
        keep, gone = [], []
        for entry in self._idle:
            _, opened, returned = entry
            stale = now - returned >= MAX_IDLE_SECONDS or now - opened >= MAX_AGE_SECONDS
            (gone if stale else keep).append(entry)
        self._idle = keep
        return [conn for conn, _, _ in gone]

    def get(self):
        now = time.monotonic()
        while True:
            with self._lock:
                expired = self._expired(now)
                entry = self._idle.pop() if self._idle else None
            for conn in expired:
                _close(conn)
            if entry is None:
                break
            conn, opened, _ = entry
            if _alive(conn):
                return conn, opened
            # One dead means the server or the network went, and the rest of the
            # idle list went with it.
            _close(conn)
            self.clear()
        return psycopg2.connect(self._dsn, **_CONNECT_OPTIONS), time.monotonic()

    def put(self, conn, opened: float) -> None:
        now = time.monotonic()
        if _reusable(conn) and now - opened < MAX_AGE_SECONDS:
            with self._lock:
                if len(self._idle) < self._max_idle:
                    self._idle.append((conn, opened, now))
                    return
        elif getattr(conn, "closed", 0) not in (0, False):
            self.clear()
        _close(conn)

    def clear(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for conn, _, _ in idle:
            _close(conn)


_pools: dict = {}
_pools_lock = threading.Lock()


def _pool(dsn: str) -> _Pool:
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            pool = _pools[dsn] = _Pool(dsn)
        return pool


@contextmanager
def cursor(dsn: str, cursor_factory=psycopg2.extras.RealDictCursor):
    """One transaction on a pooled connection: commit on success, roll back on
    error — the contract db() always had."""
    pool = _pool(dsn)
    conn, opened = pool.get()
    cur = None
    try:
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except psycopg2.Error:
                pass    # the connection itself failed; the original error says why
            raise
    finally:
        # Closed as it always was when the connection closed with it: a cursor
        # kept past its block must fail, not run inside the transaction of
        # whoever borrows the connection next.
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        pool.put(conn, opened)


def clear_all() -> None:
    """Close every idle connection. For tests and for shutdown."""
    with _pools_lock:
        pools = list(_pools.values())
    for pool in pools:
        pool.clear()
