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
import ipaddress
import os
import socket
import urllib.parse

import pytest

STUB_DATABASE_URL = "postgresql://stub/stub"
os.environ["DATABASE_URL"] = STUB_DATABASE_URL
os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})
_ORIG_CREATE_CONNECTION = socket.create_connection
_ORIG_SOCKET_CONNECT = socket.socket.connect


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


def _assert_safe_database_url() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if url == STUB_DATABASE_URL:
        return
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    if not _is_loopback_host(host):
        raise AssertionError(
            f"Refusing to run tests with non-local DATABASE_URL host: {host!r}"
        )


@pytest.fixture(autouse=True, scope="session")
def _guard_database_url_and_network():
    """Never touch a non-local database or open outbound network sockets in tests."""
    _assert_safe_database_url()

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

    socket.create_connection = _guarded_create_connection
    socket.socket.connect = _guarded_connect
    try:
        yield
    finally:
        socket.create_connection = _ORIG_CREATE_CONNECTION
        socket.socket.connect = _ORIG_SOCKET_CONNECT


@pytest.fixture(autouse=True, scope="session")
def _backfill_backups_outside_the_repository(tmp_path_factory):
    """backfill_outcome_agreement.py / auto_validate_waiting.py --apply saves a
    backup file first; a test run must not leave them in the repository's backups/ folder."""
    os.environ["OUTCOME_BACKFILL_BACKUP_DIR"] = str(tmp_path_factory.mktemp("backfill-backups"))
