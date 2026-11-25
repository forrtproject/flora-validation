"""Test-run environment, set before any test module is imported.

app.py and db_migrate.py read DATABASE_URL when imported, so without a .env the
suite could not even be collected. And with one, load_dotenv() would hand every
test the production database: importing app.py applies db_schema.sql to it
(init_db runs at import). A placeholder here comes first, and load_dotenv never
overrides a variable that is already set. Tests that need a real PostgreSQL
opt in with FLORA_TEST_DATABASE_URL (tests/pg_fixtures.py), which points
DATABASE_URL at a throwaway localhost database for that test only.

Keep this file stdlib + pytest only: .github/workflows/release-flora-zenodo.yml
runs pytest in a minimal environment with only requests, pyyaml, pandas, pytest.
"""
import importlib
import importlib.util
import ipaddress
import os
import re
import socket
import urllib.parse

import pytest

STUB_DATABASE_URL = "postgresql://stub/stub"
os.environ["DATABASE_URL"] = STUB_DATABASE_URL
os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})
_ORIG_CREATE_CONNECTION = socket.create_connection
_ORIG_SOCKET_CONNECT = socket.socket.connect
_ORIG_PSYCOPG2_CONNECT = None


def _is_loopback_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="ignore")
    host_str = str(host).strip().lower().rstrip(".")
    if host_str in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host_str).is_loopback
    except ValueError:
        return False


def _split_libpq_hosts(value) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _parse_dsn_with_libpq(dsn: str) -> dict[str, str] | None:
    """Use libpq's own parser via psycopg2.extensions.parse_dsn when available."""
    if importlib.util.find_spec("psycopg2") is None:
        return None
    ext = importlib.import_module("psycopg2.extensions")
    try:
        return dict(ext.parse_dsn(dsn))
    except Exception as exc:
        raise RuntimeError(f"Blocked unparseable PostgreSQL DSN in tests: {dsn!r}") from exc


def _extract_dsn_hosts(dsn: str | None, kwargs: dict | None = None) -> list[str]:
    """Extract all effective libpq host/hostaddr values from a URI or key=value DSN."""
    hosts: list[str] = []
    if dsn:
        if dsn == STUB_DATABASE_URL:
            hosts.append("stub")
        else:
            parsed_libpq = _parse_dsn_with_libpq(dsn)
            if parsed_libpq is not None:
                for key in ("host", "hostaddr"):
                    hosts.extend(_split_libpq_hosts(parsed_libpq.get(key)))
            elif dsn.lstrip().startswith(("postgresql://", "postgres://")):
                parsed = urllib.parse.urlparse(dsn)
                netloc = parsed.netloc
                if "@" in netloc:
                    netloc = netloc.rsplit("@", 1)[1]
                for entry in netloc.split(","):
                    entry = entry.strip()
                    if not entry:
                        continue
                    if entry.startswith("["):
                        host_part = entry.split("]", 1)[0].lstrip("[")
                    else:
                        host_part = entry.split(":", 1)[0]
                    if host_part:
                        hosts.append(host_part)
                qs = urllib.parse.parse_qs(parsed.query)
                for key in ("host", "hostaddr"):
                    for v in qs.get(key, []):
                        hosts.extend(_split_libpq_hosts(v))
            else:
                for key in ("host", "hostaddr"):
                    for m in re.finditer(rf"(?:^|\s){key}\s*=\s*(?:'([^']*)'|(\S+))", dsn):
                        val = m.group(1) if m.group(1) is not None else m.group(2)
                        hosts.extend(_split_libpq_hosts(val))
    if kwargs:
        for key in ("host", "hostaddr"):
            hosts.extend(_split_libpq_hosts(kwargs.get(key)))
    if not hosts:
        for env_key in ("PGHOST", "PGHOSTADDR"):
            hosts.extend(_split_libpq_hosts(os.environ.get(env_key)))
    return hosts


def _assert_safe_dsn(dsn: str | None = None, kwargs: dict | None = None) -> None:
    if dsn == STUB_DATABASE_URL and not kwargs:
        return
    hosts = _extract_dsn_hosts(dsn, kwargs)
    if hosts == ["stub"] and not kwargs:
        return
    for host in hosts:
        if host != "stub" and not _is_loopback_host(host):
            raise RuntimeError(
                f"Blocked non-local PostgreSQL connection target in tests: {host!r}"
            )


def _assert_safe_database_url() -> None:
    url = os.environ.get("DATABASE_URL", "")
    _assert_safe_dsn(url)


def _guarded_create_connection(address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) and address else None
    if not _is_loopback_host(host):
        raise RuntimeError(f"Blocked outbound network connection in tests: {address!r}")
    return _ORIG_CREATE_CONNECTION(address, *args, **kwargs)


def _guarded_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0] if isinstance(address, tuple) and address else None
        if not _is_loopback_host(host):
            raise RuntimeError(f"Blocked outbound socket connect in tests: {address!r}")
    return _ORIG_SOCKET_CONNECT(self, address)


# Install network and libpq guards at import time (before test collection imports any module).
_assert_safe_database_url()
socket.create_connection = _guarded_create_connection
socket.socket.connect = _guarded_connect


def _install_psycopg2_guard() -> None:
    """Guard real libpq connections (which bypass Python sockets) when psycopg2 is installed."""
    global _ORIG_PSYCOPG2_CONNECT
    if _ORIG_PSYCOPG2_CONNECT is not None:
        return
    if importlib.util.find_spec("psycopg2") is None:
        return
    pg = importlib.import_module("psycopg2")
    _ORIG_PSYCOPG2_CONNECT = pg.connect

    def _guarded_pg_connect(dsn=None, *args, **kwargs):
        _assert_safe_dsn(dsn, kwargs)
        return _ORIG_PSYCOPG2_CONNECT(dsn, *args, **kwargs)

    pg.connect = _guarded_pg_connect


_install_psycopg2_guard()


def pytest_collection_modifyitems(items):
    """Automatically tag every test using local_database with @pytest.mark.pg."""
    pg_marker = pytest.mark.pg
    for item in items:
        if "local_database" in getattr(item, "fixturenames", ()):
            item.add_marker(pg_marker)


@pytest.fixture(autouse=True)
def _check_safe_database_url():
    """Verify DATABASE_URL stays stub or loopback before every test."""
    _assert_safe_database_url()


@pytest.fixture(autouse=True, scope="session")
def _backfill_backups_outside_the_repository(tmp_path_factory):
    """backfill_outcome_agreement.py / auto_validate_waiting.py --apply saves a
    backup file first; a test run must not leave them in the repository's backups/ folder."""
    os.environ["OUTCOME_BACKFILL_BACKUP_DIR"] = str(tmp_path_factory.mktemp("backfill-backups"))
