"""Administrator credentials are stored as Argon2id hashes, never in plaintext.

The sample passwords here are deliberately nonsense. This repository is public,
so a string that anybody might actually use as an administrator password does
not belong in it, even as a fixture.

The migration must preserve every existing password — admins sign in with what
they already use — while making the stored form unreadable and making a stored
plaintext value impossible to authenticate against.
"""
import contextlib
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import admin_auth
import auth_links


# ---------------------------------------------------------------------------
# The hashing primitives, exercised for real
# ---------------------------------------------------------------------------

def test_hash_is_argon2id_and_independently_salted():
    first = admin_auth.hash_password("not-a-real-password-7Kq")
    second = admin_auth.hash_password("not-a-real-password-7Kq")

    assert first.startswith("$argon2id$")
    assert first != second, "each hash must carry its own salt"
    assert admin_auth.verify_password(first, "not-a-real-password-7Kq")
    assert admin_auth.verify_password(second, "not-a-real-password-7Kq")


def test_the_original_password_keeps_working():
    """The whole point of hashing in place: nobody has to change anything."""
    stored = admin_auth.hash_password("not-a-real-password-7Kq")
    assert admin_auth.verify_password(stored, "not-a-real-password-7Kq") is True
    assert admin_auth.verify_password(stored, "replicationsmatter") is False
    assert admin_auth.verify_password(stored, "not-a-real-password-7Kq ") is False


def test_a_plaintext_row_can_never_authenticate():
    """A row missed by the migration must fail closed, not fall back.

    Without this, an un-migrated row would still accept its own password and
    the migration would be optional in practice.
    """
    assert admin_auth.verify_password("not-a-real-password-7Kq", "not-a-real-password-7Kq") is False
    assert admin_auth.is_hashed("not-a-real-password-7Kq") is False
    assert admin_auth.is_hashed(admin_auth.hash_password("x")) is True


@pytest.mark.parametrize(
    ("stored", "password"),
    [
        (None, "not-a-real-password-7Kq"),
        ("", "not-a-real-password-7Kq"),
        ("$argon2id$not-a-real-hash", "not-a-real-password-7Kq"),
        (admin_auth.hash_password("not-a-real-password-7Kq"), ""),
        (admin_auth.hash_password("not-a-real-password-7Kq"), None),
    ],
)
def test_unusable_credentials_are_rejected_without_raising(stored, password):
    assert admin_auth.verify_password(stored, password) is False


def test_an_empty_password_is_never_hashed():
    with pytest.raises(ValueError):
        admin_auth.hash_password("")


# ---------------------------------------------------------------------------
# The migration, seeding, and login paths in app.py
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


class FakeAdminCursor:
    """Answers the statements the admin credential paths actually issue."""

    def __init__(self, rows, has_password_column=True, links=None):
        self.rows = rows
        self.links = links if links is not None else []
        self.has_password_column = has_password_column
        self.executed = []
        self.events = []
        self._one = None
        self._all = []
        self.rowcount = 0

    # -- auth_links -------------------------------------------------------
    def _links_sql(self, sql, params):
        if "SET status = 'revoked'" in sql:
            # _issue now names the subject kind too, so links for validators and
            # admins cannot collide on the same id.
            subject_kind, subject_id, purpose = params
            for link in self.links:
                if (link["subject_id"] == subject_id
                        and link["purpose"] == purpose
                        and link["status"] == "pending"):
                    link["status"] = "revoked"
            return True
        if "INSERT INTO auth_links" in sql:
            purpose, subject_kind, subject_id, email, token_hash, hours, issued_by = params
            self.links.append({
                "purpose": purpose, "subject_id": subject_id, "email": email,
                "token_hash": token_hash, "status": "pending",
                "expires_at": _now() + timedelta(hours=hours),
                "used_at": None, "issued_by": issued_by,
            })
            return True
        if "SET status = 'expired'" in sql:
            for link in self.links:
                if link["status"] == "pending" and link["expires_at"] <= _now():
                    link["status"] = "expired"
            return True
        if "SET status = 'used'" in sql:
            token_hash = params[0]
            for link in self.links:
                if link["token_hash"] == token_hash and link["status"] == "pending":
                    link["status"] = "used"
                    link["used_at"] = _now()
                    self._one = {"subject_id": link["subject_id"],
                                 "purpose": link["purpose"]}
                    return True
            self._one = None
            return True
        if "FROM auth_links l JOIN admins a" in sql:
            token_hash = params[0]
            link = next(
                (l for l in self.links if l["token_hash"] == token_hash), None
            )
            if link is None:
                self._one = None
                return True
            admin = next(
                (r for r in self.rows if r["id"] == link["subject_id"]), None
            )
            self._one = {"purpose": link["purpose"], "status": link["status"],
                         "handle": admin["handle"] if admin else None}
            return True
        return False

    def execute(self, sql, params=None):
        sql = " ".join(str(sql).split())
        self.executed.append((sql, params))

        if "security_events" in sql:
            self.events.append(params)
            return

        if "login_attempts" in sql:
            # Throttling bookkeeping: no failures recorded in these tests.
            self._one = {"n": 0}
            return

        if "auth_links" in sql:
            if self._links_sql(sql, params):
                return

        if "UPDATE admins SET password_hash = %s WHERE id = %s RETURNING handle" in sql:
            new_hash, row_id = params
            row = next((r for r in self.rows if r["id"] == row_id), None)
            if row:
                row["password_hash"] = new_hash
            self._one = {"handle": row["handle"]} if row else None
            return
        if "SELECT id, handle, email, password_hash FROM admins WHERE id" in sql:
            self._one = next((r for r in self.rows if r["id"] == params[0]), None)
            return
        if "INSERT INTO admins (handle, email, password_hash)" in sql:
            if any(r["handle"] == params[0] for r in self.rows):
                import psycopg2.errors
                raise psycopg2.errors.UniqueViolation("duplicate handle")
            row = {"id": len(self.rows) + 1, "handle": params[0],
                   "email": params[1], "password_hash": None, "trusted": False}
            self.rows.append(row)
            self._one = row
            return

        if "information_schema.columns" in sql and "'password'" in sql:
            self._one = {"n": 1} if self.has_password_column else None
        elif "SELECT id, handle, password, password_hash FROM admins" in sql:
            self._all = list(self.rows)
        elif "UPDATE admins SET password_hash" in sql:
            new_hash, row_id = params
            for row in self.rows:
                if row["id"] == row_id:
                    row["password_hash"] = new_hash
        elif "WHERE password IS NOT NULL AND password_hash IS NULL" in sql:
            self._one = {"n": sum(
                1 for r in self.rows if r.get("password") and not r.get("password_hash")
            )}
        elif "DROP COLUMN IF EXISTS password" in sql:
            self.has_password_column = False
            for row in self.rows:
                row.pop("password", None)
        elif "COUNT(*) AS n FROM admins" in sql:
            self._one = {"n": len(self.rows)}
        elif "password_hash, trusted FROM admins WHERE handle" in sql:
            self._one = next(
                (r for r in self.rows if r["handle"] == params[0]), None
            )
        elif "password_hash, trusted FROM admins" in sql:
            self._all = list(self.rows)
        elif "INSERT INTO admins" in sql:
            # The bootstrap insert passes TRUE as a SQL literal, not a param.
            trusted = params[2] if len(params) > 2 else "TRUE)" in sql.upper()
            row = {"id": len(self.rows) + 1, "handle": params[0],
                   "password_hash": params[1], "trusted": trusted}
            self.rows.append(row)
            self._one = row

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


@pytest.fixture(scope="module")
def app_module():
    """Import app.py with the database and scheduler stubbed out."""
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


@contextlib.contextmanager
def _fake_db(cursor):
    yield cursor


def _patch_db(app_module, cursor):
    return patch.object(app_module, "db", lambda: _fake_db(cursor))


# Identity now arrives as a resolved session principal rather than a header.
TRUSTED_ADMIN = {"id": 1, "handle": "Hamid", "trusted": True}
PLAIN_ADMIN = {"id": 2, "handle": "Rohan", "trusted": False}


def _fake_request():
    """A Request stand-in for endpoints that only read headers off it."""
    request = MagicMock()
    request.headers.get.return_value = "pytest"
    return request


def test_migration_hashes_each_password_then_drops_the_column(app_module):
    cursor = FakeAdminCursor([
        {"id": 1, "handle": "Hamid", "password": "hamid-secret", "password_hash": None},
        {"id": 2, "handle": "Luke", "password": "luke-secret", "password_hash": None},
    ])

    with _patch_db(app_module, cursor):
        app_module._migrate_admin_passwords()

    assert cursor.has_password_column is False
    for row, original in zip(cursor.rows, ["hamid-secret", "luke-secret"]):
        assert "password" not in row, "plaintext column must be gone"
        assert admin_auth.is_hashed(row["password_hash"])
        # The password each admin already uses still signs them in.
        assert admin_auth.verify_password(row["password_hash"], original)

    # Two different admins with the same password would not share a hash.
    assert cursor.rows[0]["password_hash"] != cursor.rows[1]["password_hash"]


def test_migration_keeps_plaintext_when_a_row_could_not_be_hashed(app_module):
    """A half-finished migration must not destroy the remaining credentials."""
    cursor = FakeAdminCursor([
        {"id": 1, "handle": "Hamid", "password": "hamid-secret", "password_hash": None},
        {"id": 2, "handle": "Ghost", "password": "unhashable", "password_hash": None},
    ])
    real_hash = admin_auth.hash_password

    def hash_only_the_first(password):
        if password == "unhashable":
            return None  # simulate a row the migration could not complete
        return real_hash(password)

    with _patch_db(app_module, cursor), patch.object(
        app_module, "hash_password", hash_only_the_first
    ):
        app_module._migrate_admin_passwords()

    assert cursor.has_password_column is True, "column must survive a partial run"
    assert cursor.rows[1]["password"] == "unhashable"


def test_migration_is_a_no_op_once_the_column_is_gone(app_module):
    cursor = FakeAdminCursor([], has_password_column=False)

    with _patch_db(app_module, cursor):
        app_module._migrate_admin_passwords()

    assert len(cursor.executed) == 1, "should stop after the column check"


def test_an_account_with_no_password_is_left_unusable(app_module):
    cursor = FakeAdminCursor([
        {"id": 1, "handle": "NoCred", "password": None, "password_hash": None},
    ])

    with _patch_db(app_module, cursor):
        app_module._migrate_admin_passwords()

    assert cursor.rows[0]["password_hash"] is None
    assert admin_auth.verify_password(cursor.rows[0]["password_hash"], "anything") is False


def test_bootstrap_refuses_to_seed_a_default_account(app_module):
    """No ADMIN_PASSWORD must fail loudly, never seed a published credential."""
    cursor = FakeAdminCursor([])

    with _patch_db(app_module, cursor), patch.object(app_module, "ADMIN_PASSWORD", ""):
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD is not set"):
            app_module._seed_admin_if_empty()

    assert cursor.rows == [], "nothing may be created without a configured password"


def test_bootstrap_stores_a_hash_under_the_configured_handle(app_module):
    cursor = FakeAdminCursor([])

    with _patch_db(app_module, cursor), \
         patch.object(app_module, "ADMIN_PASSWORD", "not-a-real-password-7Kq"), \
         patch.object(app_module, "ADMIN_HANDLE", "flora_muenster"):
        app_module._seed_admin_if_empty()

    created = cursor.rows[0]
    assert created["handle"] == "flora_muenster"
    assert created["trusted"] is True
    assert admin_auth.is_hashed(created["password_hash"])
    assert created["password_hash"] != "not-a-real-password-7Kq"
    assert admin_auth.verify_password(created["password_hash"], "not-a-real-password-7Kq")


def test_bootstrap_does_nothing_when_an_admin_already_exists(app_module):
    cursor = FakeAdminCursor([{"id": 1, "handle": "Hamid", "password_hash": "x"}])

    with _patch_db(app_module, cursor), \
         patch.object(app_module, "ADMIN_PASSWORD", "not-a-real-password-7Kq"):
        app_module._seed_admin_if_empty()

    assert len(cursor.rows) == 1


def test_login_verifies_against_the_hash(app_module):
    from fastapi import HTTPException

    stored = admin_auth.hash_password("not-a-real-password-7Kq")
    cursor = FakeAdminCursor([
        {"id": 1, "handle": "flora_muenster", "password_hash": stored, "trusted": True},
    ])
    request = MagicMock(handle="flora_muenster", password="not-a-real-password-7Kq")

    from fastapi import Response

    with _patch_db(app_module, cursor):
        response = Response()
        result = app_module.admin_login(request, _fake_request(), response)
    assert result["handle"] == "flora_muenster"
    assert result["trusted"] is True
    # No bearer token in the body any more: the session arrives as a cookie the
    # page cannot read, and the response carries no credential of its own.
    assert "token" not in result
    cookie = response.headers.get("set-cookie", "")
    assert "flora_session=" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie

    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException) as raised:
            app_module.admin_login(
                MagicMock(handle="flora_muenster", password="wrong"),
                _fake_request(), Response(),
            )
    assert raised.value.status_code == 401

    # An unknown handle gives the same answer, so handles cannot be enumerated.
    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException) as unknown:
            app_module.admin_login(
                MagicMock(handle="nobody", password="whatever"),
                _fake_request(), Response(),
            )
    assert unknown.value.detail == raised.value.detail


def test_an_account_without_a_password_cannot_be_logged_into(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([
        {"id": 1, "handle": "invited", "password_hash": None, "trusted": False},
    ])

    from fastapi import Response

    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException) as raised:
            app_module.admin_login(
                MagicMock(handle="invited", password=""),
                _fake_request(), Response(),
            )
    assert raised.value.status_code == 401


def test_the_derived_bearer_token_is_gone(app_module):
    """It could not expire or be revoked, and equal passwords shared one."""
    for name in ("_make_token", "_admin_for_token", "_require_admin",
                 "_require_trusted_admin"):
        assert not hasattr(app_module, name), f"{name} is still callable"


# ---------------------------------------------------------------------------
# Administrator invitations
# ---------------------------------------------------------------------------

def _create_admin(app_module, cursor, handle="newadmin", email="new@example.org"):
    with _patch_db(app_module, cursor), \
         patch.object(app_module, "RESEND_API_KEY", ""):
        return app_module.create_admin(
            MagicMock(handle=handle, email=email), _fake_request(),
            admin=TRUSTED_ADMIN,
        )


def test_an_invited_admin_has_no_password_until_they_choose_one(app_module):
    """The inviter never picks the credential, so it exists only for its owner."""
    cursor = FakeAdminCursor([])

    result = _create_admin(app_module, cursor)

    created = cursor.rows[0]
    assert created["password_hash"] is None
    assert created["email"] == "new@example.org"
    # No password means no usable credential at all.
    assert admin_auth.verify_password(created["password_hash"], "anything") is False
    assert len(cursor.links) == 1
    assert cursor.links[0]["purpose"] == auth_links.PURPOSE_INVITE
    assert cursor.links[0]["status"] == "pending"
    # Only the digest is stored; the raw token is in the URL and nowhere else.
    assert result["invite_url"].split("invite=")[1] not in cursor.links[0]["token_hash"]


def test_the_invite_link_is_handed_back_only_when_email_fails(app_module):
    cursor = FakeAdminCursor([])
    result = _create_admin(app_module, cursor)
    assert result["invite_emailed"] is False
    assert "invite_url" in result and "warning" in result

    cursor2 = FakeAdminCursor([])
    with _patch_db(app_module, cursor2), \
         patch.object(app_module, "RESEND_API_KEY", "re_live_key"), \
         patch.object(app_module, "resend") as sender:
        emailed = app_module.create_admin(
            MagicMock(handle="mailed", email="mailed@example.org"),
            _fake_request(), admin=TRUSTED_ADMIN,
        )
    assert emailed["invite_emailed"] is True
    assert "invite_url" not in emailed, "the secret must not travel when it was mailed"
    assert sender.Emails.send.call_count == 1
    assert sender.Emails.send.call_args[0][0]["to"] == ["mailed@example.org"]


def test_redeeming_an_invite_sets_the_password_and_spends_the_link(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    result = _create_admin(app_module, cursor)
    token = result["invite_url"].split("invite=")[1]

    with _patch_db(app_module, cursor):
        redeemed = app_module.redeem_auth_link(
                MagicMock(token=token, password="a-brand-new-password"),
                _fake_request(),
            )

    assert redeemed["handle"] == "newadmin"
    stored = cursor.rows[0]["password_hash"]
    assert admin_auth.is_hashed(stored)
    assert admin_auth.verify_password(stored, "a-brand-new-password")
    assert cursor.links[0]["status"] == "used"

    # Spent exactly once: a replayed link is dead even though it was valid.
    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException) as replay:
            app_module.redeem_auth_link(
                MagicMock(token=token, password="another-valid-password"),
                _fake_request(),
            )
    assert replay.value.status_code == 404
    assert admin_auth.verify_password(stored, "a-brand-new-password")


def test_a_weak_password_is_refused_without_spending_the_link(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    token = _create_admin(app_module, cursor)["invite_url"].split("invite=")[1]

    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException) as raised:
            app_module.redeem_auth_link(
                MagicMock(token=token, password="short"),
                _fake_request(),
            )

    assert raised.value.status_code == 422
    assert cursor.links[0]["status"] == "pending", "a rejected attempt must not burn it"


def test_an_expired_link_cannot_be_redeemed(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    token = _create_admin(app_module, cursor)["invite_url"].split("invite=")[1]
    cursor.links[0]["expires_at"] = _now() - timedelta(seconds=1)

    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException) as raised:
            app_module.redeem_auth_link(
                MagicMock(token=token, password="a-brand-new-password"),
                _fake_request(),
            )
    assert raised.value.status_code == 404
    assert cursor.links[0]["status"] == "expired"
    assert cursor.rows[0]["password_hash"] is None


def test_reissuing_an_invite_kills_the_previous_one(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    first = _create_admin(app_module, cursor)["invite_url"].split("invite=")[1]

    with _patch_db(app_module, cursor), \
         patch.object(app_module, "RESEND_API_KEY", ""):
        again = app_module.resend_admin_invite(_fake_request(), admin_id=1,
                                       admin=TRUSTED_ADMIN)
    second = again["link_url"].split("invite=")[1]

    assert first != second
    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException):
            app_module.redeem_auth_link(
                MagicMock(token=first, password="a-brand-new-password"),
                _fake_request(),
            )
        app_module.redeem_auth_link(
                MagicMock(token=second, password="a-brand-new-password"),
                _fake_request(),
            )
    assert admin_auth.verify_password(
        cursor.rows[0]["password_hash"], "a-brand-new-password"
    )


def test_describe_reveals_only_a_live_links_handle(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    token = _create_admin(app_module, cursor)["invite_url"].split("invite=")[1]

    with _patch_db(app_module, cursor):
        described = app_module.describe_auth_link(token)
    assert described["handle"] == "newadmin"
    assert described["purpose"] == auth_links.PURPOSE_INVITE

    with _patch_db(app_module, cursor):
        with pytest.raises(HTTPException) as unknown:
            app_module.describe_auth_link("not-a-real-token")
    assert unknown.value.status_code == 404

    with _patch_db(app_module, cursor):
        app_module.redeem_auth_link(
                MagicMock(token=token, password="a-brand-new-password"),
                _fake_request(),
            )
        with pytest.raises(HTTPException) as spent:
            app_module.describe_auth_link(token)
    assert spent.value.status_code == 404


def test_a_duplicate_handle_is_rejected(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    _create_admin(app_module, cursor)
    with pytest.raises(HTTPException) as raised:
        _create_admin(app_module, cursor)
    assert raised.value.status_code == 409


def test_only_a_trusted_admin_may_invite(app_module):
    """Trust is read off the session principal, not off a header."""
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    with _patch_db(app_module, cursor), \
         patch.object(app_module, "RESEND_API_KEY", ""):
        with pytest.raises(HTTPException) as raised:
            app_module.create_admin(
                MagicMock(handle="sneaky", email="s@example.org"),
                _fake_request(), admin=PLAIN_ADMIN,
            )
    assert raised.value.status_code == 403
    assert cursor.rows == []


def test_no_code_path_writes_a_plaintext_admin_password():
    from pathlib import Path

    app_source = (Path(__file__).resolve().parent.parent / "app.py").read_text(
        encoding="utf-8"
    )
    assert "flora-admin-2025" not in app_source
    # The only reads of the legacy column live in the migration that removes it.
    migration = app_source.split("def _migrate_admin_passwords()", 1)[1].split(
        "def _seed_admin_if_empty()", 1
    )[0]
    outside = app_source.replace(migration, "")
    assert 'row["password"]' not in outside
    assert "INSERT INTO admins (handle, password)" not in app_source
    # Invitations must never let the inviter choose someone else's password.
    create = app_source.split("def create_admin(", 1)[1].split("@app.post", 1)[0]
    assert "req.password" not in create
    # The SQL wraps across lines, so match the part that carries the meaning.
    assert "VALUES (%s, %s, NULL)" in create


# ---------------------------------------------------------------------------
# One-time link primitives
# ---------------------------------------------------------------------------

def test_tokens_are_random_and_only_their_digest_is_storable():
    first, second = auth_links.new_token(), auth_links.new_token()
    assert first != second
    assert len(first) >= 40

    digest = auth_links.token_digest(first)
    assert digest == auth_links.token_digest(first), "digest must be stable"
    assert first not in digest, "the raw token must not be recoverable"
    assert auth_links.digests_match(digest, auth_links.token_digest(first))
    assert not auth_links.digests_match(digest, auth_links.token_digest(second))
    assert not auth_links.digests_match(digest, "")


def test_recovery_links_are_shorter_lived_than_invitations():
    """An existing account is a bigger prize, so its window is smaller."""
    assert auth_links.ttl_hours(auth_links.PURPOSE_INVITE) == 48
    assert auth_links.ttl_hours(auth_links.PURPOSE_RESET) == 2
    assert (auth_links.ttl_hours(auth_links.PURPOSE_RESET)
            < auth_links.ttl_hours(auth_links.PURPOSE_INVITE))
    with pytest.raises(ValueError):
        auth_links.ttl_hours("something_else")


def test_link_urls_carry_the_token_and_respect_the_configured_origin(monkeypatch):
    token = auth_links.new_token()
    monkeypatch.setenv("APP_BASE_URL", "https://staging.example.org/")

    invite = auth_links.build_url(token, auth_links.PURPOSE_INVITE)
    reset = auth_links.build_url(token, auth_links.PURPOSE_RESET)

    # A trailing slash in the setting must not produce a double slash.
    assert invite.startswith("https://staging.example.org/?invite=")
    assert reset.startswith("https://staging.example.org/?reset=")
    assert "//?" not in invite

    monkeypatch.delenv("APP_BASE_URL")
    assert auth_links.build_url(token).startswith("https://validation.forrt.org/?invite=")


@pytest.mark.parametrize(
    ("password", "accepted"),
    [
        ("a-brand-new-password", True),
        ("exactlytwelve", True),
        ("short", False),
        ("", False),
        (None, False),
        (" leading-and-trailing ", False),
    ],
)
def test_chosen_passwords_must_clear_one_shared_rule(password, accepted):
    assert (auth_links.password_rejection_reason(password) is None) is accepted


def test_the_cli_enforces_the_same_password_rule_as_the_endpoint():
    from pathlib import Path

    cli = (Path(__file__).resolve().parent.parent / "admin_password.py").read_text(
        encoding="utf-8"
    )
    assert "auth_links.password_rejection_reason(password)" in cli
    assert "MIN_PASSWORD_LENGTH = 12" not in cli, "the rule must not be duplicated"
