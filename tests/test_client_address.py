"""Which address the server believes, when the caller gets a say in the header.

The sign-in throttle counts per identifier OR per client address. A caller
spraying one password across many handles never accumulates against the
identifier clause, so the address clause is the only one left — and it is only
worth anything if the address cannot be chosen by the caller.
"""
import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


def _import_app():
    """Import app.py with the database and scheduler stubbed out.

    Importing app.py runs init_db() at module scope (app.py:486), which executes
    the whole of db_schema.sql against DATABASE_URL — the production database
    whenever .env is present. A bare import therefore applies live schema DDL from
    a test run. Mirrors tests/test_submission_failure_stamps.py.
    """
    os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
    os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")
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


flora = _import_app()


def _request(forwarded=None, peer="203.0.113.7"):
    """Only the two things _client_ip() reads."""
    class FakeRequest:
        headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
        client = type("Peer", (), {"host": peer})() if peer else None

    return FakeRequest()


def _with_hops(hops):
    """_client_ip() reading a specific trusted-hop count."""
    return patch.object(flora, "TRUSTED_PROXY_HOPS", hops)


# ---------------------------------------------------------------------------
# The attack the header enables
# ---------------------------------------------------------------------------

def test_a_caller_cannot_choose_their_own_address():
    # One proxy in front: it appends what it saw, so the caller's invention sits
    # to the LEFT of the truth. Reading the left is reading the attacker.
    with _with_hops(1):
        seen = flora._client_ip(_request("10.0.0.1, 198.51.100.22"))
    assert seen == "198.51.100.22"


def test_a_fresh_forged_address_per_attempt_no_longer_evades_the_throttle():
    # The real failure mode: vary the identifier so that clause never counts,
    # and vary the forged address so the address clause never counts either.
    with _with_hops(1):
        addresses = {
            flora._client_ip(_request(f"192.0.2.{n}, 198.51.100.22"))
            for n in range(1, 30)
        }
    assert addresses == {"198.51.100.22"}, (
        "every attempt must accumulate against the one address the proxy saw"
    )


def test_an_honest_caller_is_unaffected():
    # Nothing forged: the proxy's single entry is both leftmost and rightmost,
    # so the fix cannot change the address a real user is throttled under.
    with _with_hops(1):
        assert flora._client_ip(_request("198.51.100.22")) == "198.51.100.22"


# ---------------------------------------------------------------------------
# Hop configuration
# ---------------------------------------------------------------------------

def test_two_proxies_read_two_from_the_right():
    with _with_hops(2):
        assert flora._client_ip(
            _request("10.0.0.1, 198.51.100.22, 172.16.0.9")) == "198.51.100.22"


def test_zero_hops_ignores_the_header_entirely():
    with _with_hops(0):
        assert flora._client_ip(
            _request("10.0.0.1", peer="203.0.113.7")) == "203.0.113.7"


def test_a_chain_shorter_than_configured_is_not_trusted():
    # Header absent, or fewer entries than proxies. Falling back to the peer
    # over-throttles at worst; trusting a short chain would be a bypass.
    with _with_hops(2):
        assert flora._client_ip(_request("10.0.0.1", peer="203.0.113.7")) == "203.0.113.7"
        assert flora._client_ip(_request(None, peer="203.0.113.7")) == "203.0.113.7"


def test_no_peer_and_no_header_is_empty_not_an_error():
    with _with_hops(1):
        assert flora._client_ip(_request(None, peer=None)) == ""


# ---------------------------------------------------------------------------
# What reaches the audit log
# ---------------------------------------------------------------------------

def test_a_non_address_is_never_stored_as_one():
    with _with_hops(1):
        assert flora._client_ip(_request("not-an-ip", peer="203.0.113.7")) == "203.0.113.7"
        assert flora._client_ip(_request("'; DROP TABLE", peer="203.0.113.7")) == "203.0.113.7"


def test_ipv6_survives_intact():
    with _with_hops(1):
        assert flora._client_ip(_request("2001:db8::1")) == "2001:db8::1"


def test_the_value_is_bounded():
    # security_events.client_ip is a bounded column; a long forged entry must
    # not reach it even if it somehow parses.
    with _with_hops(1):
        assert len(flora._client_ip(_request("x" * 500, peer="y" * 500))) <= 64


def test_empty_entries_do_not_shift_the_count():
    # "a,,b" must not let a caller pad the chain to push the real entry out of
    # the trusted position.
    with _with_hops(1):
        assert flora._client_ip(_request("10.0.0.1, , ,198.51.100.22")) == "198.51.100.22"


def test_padding_the_chain_cannot_displace_the_trusted_entry():
    with _with_hops(1):
        chain = ", ".join(f"192.0.2.{n}" for n in range(1, 40)) + ", 198.51.100.22"
        assert flora._client_ip(_request(chain)) == "198.51.100.22"


# ---------------------------------------------------------------------------
# Configuration parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (None, 1),      # the documented deployment: one platform router
    ("1", 1),
    ("0", 0),
    ("3", 3),
    ("-2", 0),      # clamped; a negative index would read from the left
    ("banana", 1),  # unparseable falls back to the default, loudly
    ("", 1),
])
def test_hop_count_configuration(raw, expected):
    env = dict(os.environ)
    env.pop("TRUSTED_PROXY_HOPS", None)
    if raw is not None:
        env["TRUSTED_PROXY_HOPS"] = raw
    with patch.dict(os.environ, env, clear=True):
        assert flora._trusted_proxy_hops() == expected


def test_the_default_matches_the_documented_deployment():
    procfile = (flora.Path(__file__).resolve().parent.parent / "Procfile")
    assert "uvicorn" in procfile.read_text(encoding="utf-8")
    # One platform router in front of uvicorn, so one trusted hop.
    with patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                 if k != "TRUSTED_PROXY_HOPS"}, clear=True):
        assert flora._trusted_proxy_hops() == 1


def test_every_address_consumer_goes_through_one_function():
    source = (flora.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    # One reader of the header, so hardening it hardens the throttle and the
    # audit log together and neither can drift.
    assert source.count('"x-forwarded-for"') == 1
    assert source.count("def _client_ip") == 1
    assert source.count("_client_ip(request)") >= 3
