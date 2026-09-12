"""Server-side sessions for validators and administrators.

Identity used to be whatever the caller said it was: validator endpoints read a
`coder_id` out of the request body, and the admin bearer token was a pure
function of the password, so it never expired and could not be revoked. Both are
replaced by the same mechanism here — a random token issued by the server,
stored only as a digest, bound to one principal, with an expiry and a revocation
timestamp.

The raw token is delivered in a cookie and never appears in a URL or a request
body, so it cannot leak through history, logs, or a Referer header. Only the
digest reaches the database, the same discipline as auth_links and the
submission-failure stamps.

This module holds the primitives and the SQL. The FastAPI dependencies that turn
a request into a principal live in app.py, next to the endpoints they guard.
"""

from __future__ import annotations

import hashlib
import os
import secrets

# Name is deliberately unrelated to the framework: nothing should be able to
# guess it from stack fingerprinting alone.
COOKIE_NAME = "flora_session"

_TOKEN_BYTES = 32

# Two lifetimes per principal. "Stay signed in on this device" is the honest
# version of remembering a credential: nothing is stored in the browser that a
# script could read, the cookie is HttpOnly, and the server can revoke it. A
# password kept in localStorage could do none of that.
#
# Left unticked -- the right choice on a shared or public machine -- the session
# is short enough that walking away is not a lasting exposure.
VALIDATOR_TTL_DAYS = 30
VALIDATOR_SHORT_TTL_HOURS = 12
ADMIN_TTL_DAYS = 30
ADMIN_SHORT_TTL_HOURS = 12

KIND_VALIDATOR = "validator"
KIND_ADMIN = "admin"


def new_token() -> str:
    """Return a fresh raw session token. Never stored; only hashed or set."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def cookie_is_secure() -> bool:
    """Whether to mark the cookie Secure.

    On by default: a session cookie sent over plain HTTP is readable in transit.
    Local development over http://localhost would never receive the cookie at
    all, so it can be switched off explicitly there and nowhere else.
    """
    return os.environ.get("SESSION_COOKIE_INSECURE", "").strip().lower() not in {
        "1", "true", "yes",
    }


def cookie_kwargs(max_age_seconds: int) -> dict:
    """Cookie attributes for a session.

    HttpOnly keeps the token away from page scripts, so an injected script
    cannot read it. SameSite=Lax stops another site from driving a
    state-changing request with the cookie attached, which is the other half of
    the CSRF defence alongside the Origin check in app.py.
    """
    return {
        "httponly": True,
        "secure": cookie_is_secure(),
        "samesite": "lax",
        "max_age": max_age_seconds,
        "path": "/",
    }


def ttl_seconds(kind: str, remember: bool = False) -> int:
    """Lifetime for a new session, in seconds."""
    if kind == KIND_VALIDATOR:
        return (VALIDATOR_TTL_DAYS * 24 * 3600 if remember
                else VALIDATOR_SHORT_TTL_HOURS * 3600)
    if kind == KIND_ADMIN:
        return (ADMIN_TTL_DAYS * 24 * 3600 if remember
                else ADMIN_SHORT_TTL_HOURS * 3600)
    raise ValueError(f"unknown session principal kind: {kind}")


def create(cur, kind: str, principal_id: int, user_agent: str | None = None,
           remember: bool = False) -> str:
    """Open a session and return its raw token.

    Every login mints an independent session, so two people signing in with the
    same credential do not share one, and revoking either leaves the other
    alone — the property the derived admin token could never have.
    """
    seconds = ttl_seconds(kind, remember)
    raw_token = new_token()
    cur.execute(
        """
        INSERT INTO sessions
            (principal_kind, principal_id, token_hash, expires_at, user_agent)
        VALUES (%s, %s, %s, NOW() + (%s * INTERVAL '1 second'), %s)
        """,
        (kind, principal_id, token_digest(raw_token), seconds, (user_agent or "")[:300]),
    )
    return raw_token


def lookup(cur, raw_token: str) -> dict | None:
    """Return the live session for a token, refreshing last_used_at.

    A single statement claims and touches the row: expiry and revocation are
    evaluated by the database at the moment of use, so there is no window
    between checking a session and acting on it.
    """
    if not raw_token:
        return None
    cur.execute(
        """
        UPDATE sessions SET last_used_at = NOW()
        WHERE token_hash = %s
          AND revoked_at IS NULL
          AND expires_at > NOW()
        RETURNING session_id, principal_kind, principal_id, expires_at
        """,
        (token_digest(raw_token),),
    )
    return cur.fetchone()


def revoke(cur, raw_token: str) -> bool:
    """Revoke one session. Used by logout."""
    if not raw_token:
        return False
    cur.execute(
        "UPDATE sessions SET revoked_at = NOW() "
        "WHERE token_hash = %s AND revoked_at IS NULL",
        (token_digest(raw_token),),
    )
    return cur.rowcount > 0


def revoke_all_for(cur, kind: str, principal_id: int) -> int:
    """Revoke every live session for one principal.

    Called when a password changes or an account is removed: the point of a
    session store is that those actions take effect immediately rather than
    waiting out a token's lifetime.
    """
    cur.execute(
        "UPDATE sessions SET revoked_at = NOW() "
        "WHERE principal_kind = %s AND principal_id = %s AND revoked_at IS NULL",
        (kind, principal_id),
    )
    return cur.rowcount


def delete_expired(cur, keep_days: int = 30) -> int:
    """Drop long-dead rows so the table does not grow without bound."""
    cur.execute(
        "DELETE FROM sessions "
        "WHERE expires_at < NOW() - (%s * INTERVAL '1 day')",
        (keep_days,),
    )
    return cur.rowcount
