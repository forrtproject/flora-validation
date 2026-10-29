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

    def __init__(self, rows, has_password_column=True, links=None,
                 email_update_raises=False):
        self.rows = rows
        self.links = links if links is not None else []
        self.has_password_column = has_password_column
        # Stands in for a concurrent redemption claiming the same address
        # between the NOT EXISTS check and the write.
        self.email_update_raises = email_update_raises
        self.executed = []
        self.events = []
        self._one = None
        self._all = []
        self.rowcount = 0

    # -- auth_links -------------------------------------------------------
    def _links_sql(self, sql, params):
        if "SET status = 'revoked'" in sql:
            # _issue now names the subject kind too, so links for validators and
            # admins cannot collide on the same id. Redemption passes only the
            # subject: it retires every purpose at once.
            if len(params) == 1:
                subject_id, purpose = params[0], None
            else:
                subject_kind, subject_id, purpose = params
            for link in self.links:
                if (link["subject_id"] == subject_id
                        and purpose in (None, link["purpose"])
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
                                 "purpose": link["purpose"],
                                 "email": link["email"]}
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

        if sql.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            return
        if "UPDATE admins SET email = %s WHERE id = %s AND email IS NULL" in sql:
            if self.email_update_raises:
                import psycopg2.errors
                raise psycopg2.errors.UniqueViolation("duplicate email")
            email, row_id = params[0], params[1]
            taken = any((r.get("email") or "").lower() == email.lower()
                        and r["id"] != row_id for r in self.rows)
            row = next((r for r in self.rows
                        if r["id"] == row_id and r.get("email") is None), None)
            if row and not taken:
                row["email"] = email
                self._one = {"id": row_id}
            else:
                self._one = None
            return
        if "SELECT 1 FROM admins WHERE lower(email) = %s AND id <> %s" in sql:
            email, row_id = params
            self._one = next(
                ({"?column?": 1} for r in self.rows
                 if (r.get("email") or "").lower() == email and r["id"] != row_id),
                None,
            )
            return
        if "FROM admins a LEFT JOIN validators v" in sql:
            self._all = [{
                "id": r["id"], "handle": r["handle"], "trusted": r.get("trusted"),
                "email": r.get("email"), "has_email": r.get("email") is not None,
                "has_password": r.get("password_hash") is not None,
            } for r in self.rows]
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


# The fake numbers new accounts from 1, which is TRUSTED_ADMIN's id too, so link
# tests send as a trusted admin whose own row is never the one acted on.
SENDER = {"id": 100, "handle": "Hamid", "trusted": True}


def _resend(app_module, cursor, admin_id, email=None, caller=SENDER, sender=None):
    """Ask for a link. `sender` is a mock Resend client; without one, mail is off."""
    req = app_module.AdminLinkResendRequest(admin_id=admin_id, email=email)
    with _patch_db(app_module, cursor), \
         patch.object(app_module, "RESEND_API_KEY", "re_live_key" if sender else ""), \
         patch.object(app_module, "resend", sender or MagicMock()):
        return app_module.resend_admin_invite(req, _fake_request(), admin=caller)


def _existing_admin(admin_id=2, handle="luke", email=None, has_password=True):
    # Never verified by these paths, only tested for presence.
    return {"id": admin_id, "handle": handle, "email": email, "trusted": False,
            "password_hash": "$argon2id$existing" if has_password else None}


def _audited(cursor, action):
    import json
    return [json.loads(params[-1]) for params in cursor.events if params[0] == action]


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

    again = _resend(app_module, cursor, 1)
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


def test_redeeming_any_link_retires_the_accounts_other_links(app_module):
    """An invite left pending when a password was set elsewhere dies with the reset."""
    from fastapi import HTTPException

    cursor = FakeAdminCursor([])
    invite = _create_admin(app_module, cursor)["invite_url"].split("invite=")[1]
    # A password set from the CLI, which issues no link and revokes none.
    cursor.rows[0]["password_hash"] = admin_auth.hash_password("set-from-the-cli")

    resent = _resend(app_module, cursor, 1)
    assert resent["purpose"] == "admin_reset"
    reset = resent["link_url"].split("reset=")[1]

    with _patch_db(app_module, cursor):
        app_module.redeem_auth_link(
            MagicMock(token=reset, password="a-brand-new-password"),
            _fake_request(),
        )
        with pytest.raises(HTTPException):
            app_module.redeem_auth_link(
                MagicMock(token=invite, password="someone-elses-password"),
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


# ---------------------------------------------------------------------------
# Sending another administrator a reset link or a fresh invitation
# ---------------------------------------------------------------------------

def test_a_reset_link_goes_to_the_email_on_file_and_names_the_sender(app_module):
    cursor = FakeAdminCursor([_existing_admin(email="luke@example.org")])
    sender = MagicMock()

    # The same address in another case and with stray spaces is not a mismatch.
    result = _resend(app_module, cursor, 2, email=" Luke@Example.org ", sender=sender)

    assert result["emailed"] is True and "link_url" not in result
    assert result["purpose"] == auth_links.PURPOSE_RESET
    assert result["email"] == "luke@example.org"
    sent = sender.Emails.send.call_args[0][0]
    assert sent["to"] == ["luke@example.org"]
    # The recipient never asked, so the email says who did.
    assert "Hamid sent you a link" in sent["html"]
    assert "Hamid sent you a link" in sent["text"]
    assert cursor.links[0]["email"] == "luke@example.org"
    assert _audited(cursor, "admin.invite_resent") == [{
        "purpose": auth_links.PURPOSE_RESET, "email": "luke@example.org",
        "email_supplied": False,
    }]


def test_a_link_cannot_be_redirected_away_from_the_email_on_file(app_module):
    """Otherwise any trusted admin could reset someone else's password."""
    from fastapi import HTTPException

    cursor = FakeAdminCursor([_existing_admin(email="luke@example.org")])
    sender = MagicMock()
    with pytest.raises(HTTPException) as raised:
        _resend(app_module, cursor, 2, email="attacker@example.org", sender=sender)

    assert raised.value.status_code == 400
    assert cursor.links == []
    assert sender.Emails.send.call_count == 0
    assert cursor.rows[0]["email"] == "luke@example.org"


@pytest.mark.parametrize("email", [None, "", "   "])
def test_an_account_with_no_email_needs_one_supplied(app_module, email):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([_existing_admin()])
    with pytest.raises(HTTPException) as raised:
        _resend(app_module, cursor, 2, email=email)
    assert raised.value.status_code == 400
    assert cursor.links == []


@pytest.mark.parametrize("email", ["not-an-email", "luke@localhost", "two@@example.org"])
def test_a_supplied_email_must_look_like_one(app_module, email):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([_existing_admin()])
    with pytest.raises(HTTPException) as raised:
        _resend(app_module, cursor, 2, email=email)
    assert raised.value.status_code == 400
    assert cursor.links == []


def test_a_supplied_email_another_admin_uses_is_refused(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([
        _existing_admin(),
        _existing_admin(admin_id=3, handle="rohan", email="taken@example.org"),
    ])
    with pytest.raises(HTTPException) as raised:
        _resend(app_module, cursor, 2, email="Taken@Example.org")
    assert raised.value.status_code == 409
    assert cursor.links == []


def test_a_supplied_email_goes_on_the_link_but_not_yet_on_the_account(app_module):
    """Unproven until redeemed, so a typo stays correctable by sending again."""
    cursor = FakeAdminCursor([_existing_admin()])
    sender = MagicMock()

    first = _resend(app_module, cursor, 2, email=" Luke.Typo@Example.org", sender=sender)
    assert first["email"] == "luke.typo@example.org"
    assert sender.Emails.send.call_args[0][0]["to"] == ["luke.typo@example.org"]
    assert cursor.rows[0]["email"] is None

    # Resending to the corrected address kills the link sent to the typo.
    second = _resend(app_module, cursor, 2, email="luke@example.org", sender=sender)
    assert second["email"] == "luke@example.org"
    assert [l["status"] for l in cursor.links] == ["revoked", "pending"]
    assert cursor.links[1]["email"] == "luke@example.org"
    assert cursor.rows[0]["email"] is None
    assert _audited(cursor, "admin.invite_resent")[-1] == {
        "purpose": auth_links.PURPOSE_RESET, "email": "luke@example.org",
        "email_supplied": True,
    }


def test_redeeming_a_link_records_the_address_it_was_sent_to(app_module):
    cursor = FakeAdminCursor([_existing_admin(has_password=False)])
    result = _resend(app_module, cursor, 2, email="luke@example.org")
    assert result["purpose"] == auth_links.PURPOSE_INVITE
    assert "warning" in result
    token = result["link_url"].split("invite=")[1]

    with _patch_db(app_module, cursor):
        app_module.redeem_auth_link(
            MagicMock(token=token, password="a-brand-new-password"), _fake_request(),
        )

    assert cursor.rows[0]["email"] == "luke@example.org"
    assert admin_auth.verify_password(cursor.rows[0]["password_hash"],
                                      "a-brand-new-password")
    assert _audited(cursor, "admin.password_set")[-1]["email_recorded"] is True


@pytest.mark.parametrize("race", [False, True])
def test_a_taken_address_never_costs_the_password_change(app_module, race):
    """Claimed by another admin before redemption: skipped, not fatal."""
    cursor = FakeAdminCursor([_existing_admin()], email_update_raises=race)
    token = _resend(app_module, cursor, 2, email="luke@example.org")["link_url"] \
        .split("reset=")[1]
    if not race:
        cursor.rows.append(_existing_admin(admin_id=3, handle="rohan",
                                           email="luke@example.org"))

    with _patch_db(app_module, cursor):
        redeemed = app_module.redeem_auth_link(
            MagicMock(token=token, password="a-brand-new-password"), _fake_request(),
        )

    assert redeemed["handle"] == "luke"
    assert cursor.rows[0]["email"] is None
    assert admin_auth.verify_password(cursor.rows[0]["password_hash"],
                                      "a-brand-new-password")
    assert _audited(cursor, "admin.password_set")[-1]["email_recorded"] is False
    if race:
        assert any(sql == "ROLLBACK TO SAVEPOINT admin_redeemed_email"
                   for sql, _ in cursor.executed)


def test_redeeming_never_overwrites_an_address_on_file(app_module):
    cursor = FakeAdminCursor([_existing_admin(email="luke@example.org")])
    token = _resend(app_module, cursor, 2)["link_url"].split("reset=")[1]
    cursor.links[0]["email"] = "somewhere-else@example.org"

    with _patch_db(app_module, cursor):
        app_module.redeem_auth_link(
            MagicMock(token=token, password="a-brand-new-password"), _fake_request(),
        )
    assert cursor.rows[0]["email"] == "luke@example.org"


def test_nobody_sends_a_link_for_their_own_account(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([_existing_admin(admin_id=1, handle="Hamid",
                                              email="hamid@example.org")])
    with pytest.raises(HTTPException) as raised:
        _resend(app_module, cursor, 1, caller=TRUSTED_ADMIN)
    assert raised.value.status_code == 400
    assert cursor.links == []


def test_only_a_trusted_admin_may_send_a_link(app_module):
    from fastapi import HTTPException

    cursor = FakeAdminCursor([_existing_admin(admin_id=3, email="luke@example.org")])
    with pytest.raises(HTTPException) as raised:
        _resend(app_module, cursor, 3, caller=PLAIN_ADMIN)
    assert raised.value.status_code == 403
    assert cursor.links == []


def test_only_a_trusted_admin_sees_admin_email_addresses(app_module):
    cursor = FakeAdminCursor([
        _existing_admin(email="luke@example.org"),
        _existing_admin(admin_id=3, handle="rohan", has_password=False),
    ])
    with _patch_db(app_module, cursor):
        trusted = app_module.list_admins(admin=SENDER)["admins"]
        plain = app_module.list_admins(admin=PLAIN_ADMIN)["admins"]

    assert [a["email"] for a in trusted] == ["luke@example.org", None]
    assert all("email" not in a for a in plain)
    for listing in (trusted, plain):
        assert [(a["has_email"], a["has_password"]) for a in listing] == [
            (True, True), (False, False),
        ]
        assert all("password_hash" not in a for a in listing)


def test_the_reset_email_keeps_its_wording_when_nobody_is_named():
    from email_templates import admin_reset_email

    plain = admin_reset_email("luke", "https://example.org/?reset=t", 2)
    assert plain["text"].startswith("Set a new password for luke.")
    assert "Tell the team" in plain["html"]
    assert "sent you a link" not in plain["text"]

    named = admin_reset_email("luke", "https://example.org/?reset=t", 2,
                              requested_by="Hamid")
    assert named["text"].startswith("Hamid sent you a link to set a new password for luke.")
    assert "Check with Hamid" in named["html"]
    assert "Didn’t ask for this" not in named["text"]


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
