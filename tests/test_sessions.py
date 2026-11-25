"""Behavioural tests for session tokens, cookie settings, CSRF guards, and identity API contracts."""
import os
from unittest.mock import MagicMock, patch

import pytest

import sessions


# ---------------------------------------------------------------------------
# Token and cookie primitives
# ---------------------------------------------------------------------------

def test_tokens_are_random_and_stored_only_as_digests():
    first, second = sessions.new_token(), sessions.new_token()
    assert first != second
    assert len(first) >= 40
    digest = sessions.token_digest(first)
    assert digest == sessions.token_digest(first)
    assert first not in digest


def test_cookie_keeps_the_token_away_from_page_scripts():
    kwargs = sessions.cookie_kwargs(3600)
    assert kwargs["httponly"] is True, "a readable cookie can be stolen by injection"
    assert kwargs["samesite"] == "lax", "blocks the cross-site form post"
    assert kwargs["secure"] is True
    assert kwargs["path"] == "/"


def test_secure_can_only_be_disabled_deliberately(monkeypatch):
    monkeypatch.delenv("SESSION_COOKIE_INSECURE", raising=False)
    assert sessions.cookie_is_secure() is True
    monkeypatch.setenv("SESSION_COOKIE_INSECURE", "1")
    assert sessions.cookie_is_secure() is False
    monkeypatch.setenv("SESSION_COOKIE_INSECURE", "no")
    assert sessions.cookie_is_secure() is True


def test_an_unremembered_session_is_short_lived():
    """Leaving the box unticked is the shared-machine choice, so it must matter."""
    for kind in (sessions.KIND_ADMIN, sessions.KIND_VALIDATOR):
        short = sessions.ttl_seconds(kind)
        remembered = sessions.ttl_seconds(kind, remember=True)
        assert short == 12 * 3600, kind
        assert remembered == 30 * 24 * 3600, kind
        assert short < remembered
    with pytest.raises(ValueError):
        sessions.ttl_seconds("something_else")


def test_lookup_rejects_an_empty_token_without_touching_the_database():
    cursor = MagicMock()
    assert sessions.lookup(cursor, "") is None
    assert cursor.execute.call_count == 0


def test_lookup_requires_the_row_to_be_live():
    """Expiry and revocation are evaluated by the database, not in Python."""
    cursor = MagicMock()
    sessions.lookup(cursor, "some-token")
    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "revoked_at IS NULL" in sql
    assert "expires_at > NOW()" in sql
    assert sql.startswith("UPDATE sessions SET last_used_at = NOW()")


# ---------------------------------------------------------------------------
# Cross-site protection & live OpenAPI identity contracts
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app_module():
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


def _request(method="POST", **headers):
    request = MagicMock()
    request.method = method
    request.headers = {k.replace("_", "-"): v for k, v in headers.items()}
    return request


@pytest.mark.parametrize("fetch_site", ["same-origin", "same-site", "none"])
def test_same_site_writes_are_allowed(app_module, fetch_site):
    assert app_module._is_cross_site(_request(sec_fetch_site=fetch_site)) is False


def test_cross_site_writes_are_blocked(app_module):
    assert app_module._is_cross_site(_request(sec_fetch_site="cross-site")) is True


def test_a_foreign_origin_is_blocked(app_module, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://validation.forrt.org")
    assert app_module._is_cross_site(_request(origin="https://evil.example")) is True
    assert app_module._is_cross_site(
        _request(origin="https://validation.forrt.org")
    ) is False


def test_reads_are_never_blocked(app_module):
    for method in ("GET", "HEAD", "OPTIONS"):
        assert app_module._is_cross_site(
            _request(method, sec_fetch_site="cross-site")
        ) is False


def test_a_headerless_client_is_not_the_csrf_threat(app_module):
    """curl and scripts carry no ambient cookie, so they are not what this stops."""
    assert app_module._is_cross_site(_request()) is False


def test_openapi_enforces_server_side_identity_and_post_for_next_pairs(app_module):
    """Verified against the live FastAPI OpenAPI schema, not source greps."""
    schema = app_module.app.openapi()
    paths = schema.get("paths", {})
    assert "post" in paths["/api/next-pairs"]
    assert "get" not in paths["/api/next-pairs"]
    assert "/api/login/redeem" not in paths
    for path, ops in paths.items():
        for method, op in ops.items():
            param_names = {p.get("name") for p in op.get("parameters", [])}
            assert "coder_id" not in param_names, (path, method)
            assert "x-admin-token" not in {n.lower() for n in param_names if n}, (path, method)
    for model_name, model in schema.get("components", {}).get("schemas", {}).items():
        props = model.get("properties", {})
        assert "coder_id" not in props, model_name
    assert "code" not in schema["components"]["schemas"]["LoginRequest"]["properties"]
