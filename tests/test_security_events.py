"""Privileged actions leave a durable, bounded, admin-only audit record."""
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import security_events
from tests.test_dashboard_split import flora


def test_an_unknown_action_is_refused_rather_than_silently_unfindable():
    cursor = MagicMock()
    with pytest.raises(ValueError, match="unknown security action"):
        security_events.record(cursor, "admin.did_a_thing")
    assert cursor.execute.call_count == 0


def test_a_broken_audit_write_never_fails_the_action():
    """An admin action must not be blocked by its own bookkeeping."""
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("table is gone")
    security_events.record(cursor, security_events.ADMIN_DELETED)  # must not raise


def test_events_are_written_on_the_callers_cursor():
    """So an action that rolls back takes its event with it."""
    cursor = MagicMock()
    security_events.record(
        cursor, security_events.ADMIN_DELETED, actor_kind="admin",
        actor_handle="Hamid", target_label="rohan",
    )
    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert sql.startswith("INSERT INTO security_events")


def test_the_trail_is_admin_only():
    client = TestClient(flora.app)
    resp = client.get("/api/admin/security-events")
    assert resp.status_code in (401, 403)


def test_the_trail_outlives_the_throttling_table():
    assert security_events.RETENTION_DAYS == 365


def test_audit_fields_are_bounded_at_the_sink():
    cursor = MagicMock()
    huge = "A" * 200_000
    security_events.record(
        cursor, security_events.ADMIN_SIGN_IN_FAILED,
        actor_handle=huge, target_kind=huge, target_id=huge,
        target_label=huge, client_ip=huge, user_agent=huge,
    )
    params = cursor.execute.call_args[0][1]
    for value in params:
        if isinstance(value, str):
            assert len(value) <= 4000, f"unbounded field of {len(value)} chars"
    _, _, _, actor_handle, target_kind, target_id, target_label, ip, ua, _ = params
    assert len(actor_handle) == security_events.MAX_HANDLE
    assert len(target_label) == security_events.MAX_LABEL
    assert len(target_id) == security_events.MAX_ID
    assert len(ip) == security_events.MAX_IP
    assert len(ua) == security_events.MAX_USER_AGENT


def test_an_oversized_detail_blob_is_replaced_by_a_summary():
    cursor = MagicMock()
    security_events.record(
        cursor, security_events.ADMIN_DELETED, detail={"x": "y" * 100_000},
    )
    detail = json.loads(cursor.execute.call_args[0][1][-1])
    assert detail["truncated"] is True
    assert detail["bytes"] > security_events.MAX_DETAIL_CHARS


@pytest.mark.parametrize(
    ("model_name", "payload"),
    [
        ("AdminLoginRequest", {"handle": "a" * 5000, "password": "pw"}),
        ("AdminLoginRequest", {"handle": "admin", "password": "p" * 5000}),
        ("LoginRequest", {"handle": "a" * 5000, "email": "a@example.org"}),
        ("LoginRequest", {"handle": "user", "email": "e" * 5000 + "@example.org"}),
        ("ClaimCodeRequest", {"handle": "a" * 5000, "code": "1234", "email": "a@example.org"}),
        ("ClaimCodeRequest", {"handle": "user", "code": "c" * 5000, "email": "a@example.org"}),
        ("ClaimCodeRequest", {"handle": "user", "code": "1234", "email": "e" * 5000 + "@example.org"}),
        ("AuthLinkRedeemRequest", {"token": "t" * 5000, "password": "long-enough-password"}),
        ("AuthLinkRedeemRequest", {"token": "tok", "password": "p" * 5000}),
    ],
)
def test_credentials_are_bounded_before_argon2_sees_them(model_name, payload):
    model = getattr(flora, model_name)
    with pytest.raises(ValidationError):
        model(**payload)


def test_an_admin_password_is_never_inferred_from_the_email_field():
    """A password containing '@' must never be routed through the validator login flow."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "docs" / "app.js").read_text(encoding="utf-8")
    assert '!fieldVal.includes("@")' not in js
    assert "adminLogin(handle, fieldVal)" not in js
    login = js.split("async function doLogin()", 1)[1].split("\nasync function", 1)[0]
    assert "adminLogin(" not in login, "doLogin must not reach the admin endpoint"
