"""Privileged actions leave a durable, queryable record."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import security_events

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")


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
    # It joins whatever transaction the caller opened: the module must never
    # open a connection or commit one of its own.
    source = Path(security_events.__file__).read_text(encoding="utf-8")
    assert "connect(" not in source
    assert ".commit()" not in source


def test_the_table_is_append_only_in_practice():
    assert "CREATE TABLE IF NOT EXISTS security_events" in SCHEMA
    # Only the retention prune may remove rows, and nothing updates them.
    assert "UPDATE security_events" not in APP
    deletes = [l for l in APP.splitlines() if "DELETE FROM security_events" in l]
    assert deletes == [], "app.py must not delete events; prune lives in the module"
    source = Path(security_events.__file__).read_text(encoding="utf-8")
    prune = source.split("def prune(", 1)[1]
    assert "DELETE FROM security_events" in prune
    assert source.count("DELETE FROM security_events") == 1, "only the prune may delete"


def test_the_actor_is_the_server_s_view_not_a_request_claim():
    helper = APP.split("def _audit(", 1)[1].split("\ndef ", 1)[0]
    # The actor comes from the resolved principal passed in, and the address
    # from the connection, never from the body.
    assert "_client_ip(request)" in helper
    assert 'actor_kind if actor else "system"' in helper


@pytest.mark.parametrize("action", [
    security_events.ADMIN_SIGNED_IN,
    security_events.ADMIN_SIGN_IN_FAILED,
    security_events.ADMIN_SIGNED_OUT,
    security_events.ADMIN_INVITED,
    security_events.ADMIN_DELETED,
    security_events.ADMIN_TRUST_CHANGED,
    security_events.ADMIN_PASSWORD_SET,
    security_events.ADMIN_BOOTSTRAPPED,
    security_events.VALIDATOR_TIER_CHANGED,
    security_events.VALIDATOR_CODE_CLAIMED,
    security_events.VALIDATOR_ASSIGNED,
    security_events.MAINTENANCE_STARTED,
])
def test_every_privileged_action_is_actually_recorded(action):
    const = next(k for k, v in vars(security_events).items()
                 if isinstance(v, str) and v == action and k.isupper())
    assert f"security_events.{const}" in APP, f"{action} is defined but never recorded"


def test_the_trail_is_admin_only_and_read_only():
    endpoint = APP.split("def admin_security_events(", 1)[1].split("@app.", 1)[0]
    assert "admin: dict = Depends(current_admin)" in endpoint
    assert "SELECT" in endpoint and "INSERT" not in endpoint and "DELETE" not in endpoint
    # A bad filter must not reach the query.
    assert "Unknown action filter" in endpoint


def test_the_trail_outlives_the_throttling_table():
    """login_attempts exists to throttle; this exists to explain, much later."""
    assert security_events.RETENTION_DAYS == 365
    assert "security_events.prune(cur)" in APP


# ---------------------------------------------------------------------------
# The admin sign-in field bug
# ---------------------------------------------------------------------------

def test_an_admin_password_is_never_inferred_from_the_email_field():
    """A password containing '@' used to be posted to the validator endpoint.

    It was then stored in validators.email in plaintext and opened a validator
    session under the admin's handle.
    """
    assert '!fieldVal.includes("@")' not in JS
    assert "adminLogin(handle, fieldVal)" not in JS
    login = JS.split("async function doLogin()", 1)[1].split("\nasync function", 1)[0]
    assert "adminLogin(" not in login, "doLogin must not reach the admin endpoint"


def test_admins_sign_in_through_a_real_password_field():
    assert 'id="admin-signin-password"' in HTML
    assert 'type="password"' in HTML.split('id="admin-signin-password"', 1)[0][-120:] \
        or 'type="password"' in HTML.split('id="admin-signin-password"', 1)[1][:120]
    assert 'autocomplete="current-password"' in HTML
    assert "submitAdminSignIn" in JS
    # The password is never persisted, only the handle.
    submit = JS.split("async function submitAdminSignIn(", 1)[1].split("\n}", 1)[0]
    assert 'rememberLogin(handle, "")' in submit


def test_the_hidden_trigger_has_a_reachable_alternative():
    """A triple-click gesture cannot be the only way to an admin login."""
    assert 'get("admin")' in JS, "?admin=1 fallback is missing"
    assert "clicks >= 3" in JS


# ---------------------------------------------------------------------------
# Attacker-controlled data reaching the audit table
# ---------------------------------------------------------------------------

def test_audit_fields_are_bounded_at_the_sink():
    """A failed sign-in writes the attempted handle, so it is attacker-supplied.

    Before this, one unauthenticated request could put 200,000 characters into
    security_events.target_label.
    """
    from unittest.mock import MagicMock

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
    from unittest.mock import MagicMock
    import json

    cursor = MagicMock()
    security_events.record(
        cursor, security_events.ADMIN_DELETED, detail={"x": "y" * 100_000},
    )
    detail = json.loads(cursor.execute.call_args[0][1][-1])
    assert detail["truncated"] is True
    assert detail["bytes"] > security_events.MAX_DETAIL_CHARS


def test_credentials_are_bounded_before_argon2_sees_them():
    """An unbounded password would let anyone make the server hash a megabyte."""
    for model, fields in [
        ("AdminLoginRequest", ["handle", "password"]),
        ("LoginRequest", ["handle", "email"]),
        ("ClaimCodeRequest", ["handle", "code", "email"]),
        ("AuthLinkRedeemRequest", ["token", "password"]),
    ]:
        body = APP.split(f"class {model}(BaseModel):", 1)[1].split("\nclass ", 1)[0]
        for field in fields:
            line = next((l for l in body.splitlines()
                         if l.strip().startswith(f"{field}:")), None)
            assert line, f"{model}.{field} not found"
            assert "max_length" in line, f"{model}.{field} is unbounded"
