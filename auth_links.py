"""One-time authentication links: invitations, recovery, and later sign-in.

A link is a random secret mailed to a proven address and spendable exactly once.
The raw token exists only in the email and in the URL the recipient opens; the
database stores nothing but its SHA-256 digest, so a dump yields no working
link. That is the same discipline as the submission-failure stamps in app.py,
for the same reason: possession of the secret is the whole authorisation.

This module holds the token primitives so they can be tested without a
database. The SQL that issues and spends a link lives in app.py alongside the
transaction it belongs to.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from urllib.parse import quote

# 32 bytes from the system CSPRNG. token_urlsafe is what the submission-failure
# stamps already use; matching it keeps one notion of "unguessable" in the code.
_TOKEN_BYTES = 32

# An invitation has to survive a working day and a forwarded email, but it is
# still a credential in an inbox. Recovery is deliberately shorter: the account
# already exists, so the window to abuse a stolen link matters more.
INVITE_TTL_HOURS = 48
RESET_TTL_HOURS = 2

PURPOSE_INVITE = "admin_invite"
PURPOSE_RESET = "admin_reset"
_TTL_BY_PURPOSE = {
    PURPOSE_INVITE: INVITE_TTL_HOURS,
    PURPOSE_RESET: RESET_TTL_HOURS,
}

# Passwords chosen through a link are the real administrator credential, so the
# floor is higher than a throwaway. Length is the only rule worth enforcing
# mechanically; composition rules mostly push people towards predictable shapes.
MIN_PASSWORD_LENGTH = 12


def app_base_url() -> str:
    """Return the public origin used to build link URLs."""
    configured = os.environ.get("APP_BASE_URL", "").strip()
    return (configured or "https://validation.forrt.org").rstrip("/")


def new_token() -> str:
    """Return a fresh raw token. Never stored; only ever hashed or mailed."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(raw_token: str) -> str:
    """Return the digest stored in auth_links.token_hash."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def digests_match(left: str, right: str) -> bool:
    """Compare two digests without leaking their contents through timing."""
    return hmac.compare_digest(left or "", right or "")


def ttl_hours(purpose: str) -> int:
    try:
        return _TTL_BY_PURPOSE[purpose]
    except KeyError:
        raise ValueError(f"unknown auth-link purpose: {purpose}") from None


def build_url(raw_token: str, purpose: str = PURPOSE_INVITE) -> str:
    """Return the URL a recipient opens.

    The single-page app reads these query parameters at startup; `quote` keeps a
    token safe in a URL even though token_urlsafe already avoids the characters
    that would need it.
    """
    param = "invite" if purpose == PURPOSE_INVITE else "reset"
    return f"{app_base_url()}/?{param}={quote(raw_token, safe='')}"


def issue_for_admin(cur, admin_id: int, email: str, purpose: str,
                    issued_by: str | None = None) -> str:
    """Revoke any live link for this admin and purpose, then mint a replacement.

    Superseding rather than accumulating is what makes a link single-use in
    practice: re-inviting somebody must not leave the earlier email spendable.
    Only the digest is written, so the raw token returned here is the only copy
    that exists outside the recipient's inbox.

    Takes a cursor rather than opening its own connection so the caller decides
    the transaction — the API issues inside the same one that creates the
    account, and the CLI inside its own.
    """
    return _issue(cur, "admin", admin_id, email, purpose, issued_by)


def _issue(cur, subject_kind: str, subject_id: int, email: str, purpose: str,
           issued_by: str | None) -> str:
    cur.execute(
        """
        UPDATE auth_links SET status = 'revoked'
        WHERE subject_kind = %s AND subject_id = %s
          AND purpose = %s AND status = 'pending'
        """,
        (subject_kind, subject_id, purpose),
    )
    raw_token = new_token()
    cur.execute(
        """
        INSERT INTO auth_links
            (purpose, subject_kind, subject_id, email, token_hash, status,
             expires_at, issued_by)
        VALUES (%s, %s, %s, %s, %s, 'pending',
                NOW() + (%s * INTERVAL '1 hour'), %s)
        """,
        (purpose, subject_kind, subject_id, email, token_digest(raw_token),
         ttl_hours(purpose), issued_by),
    )
    return raw_token


def expire_elapsed(cur) -> int:
    """Materialise elapsed TTLs so status reflects reality, not just age."""
    cur.execute(
        "UPDATE auth_links SET status = 'expired' "
        "WHERE status = 'pending' AND expires_at <= NOW()"
    )
    return cur.rowcount


def password_rejection_reason(password: str) -> str | None:
    """Return why a chosen password is unacceptable, or None if it is fine."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if password.strip() != password:
        return "Password must not start or end with a space"
    return None
