"""Password storage for administrator accounts.

`admins.password` held the password itself, so anyone who could read the
database — a dump, a backup, the hosting dashboard — could read every admin
credential, and `_make_token()` derived the bearer token straight from it, which
made the token brute-forceable from a guessable password.

Passwords are stored as Argon2id hashes instead. Existing passwords keep
working: the migration hashes the value already on the row, so an admin signs in
with exactly what they used before and nothing is reset.

This is credential storage only. It is not a session system: tokens remain
derived rather than issued, so they still cannot expire or be revoked
individually. That is the separate work described in docs/PROJECT.md §19.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Library defaults track the current OWASP guidance; pinning our own numbers
# here would freeze them at whatever was reasonable on the day this was written.
_HASHER = PasswordHasher()

# Every Argon2 encoded hash starts with the variant marker. A stored value that
# does not is a legacy plaintext password that predates this module.
_ARGON2_PREFIX = "$argon2"


def hash_password(password: str) -> str:
    """Return an Argon2id hash. Each call salts independently."""
    if not password:
        raise ValueError("refusing to hash an empty administrator password")
    return _HASHER.hash(password)


def is_hashed(stored: str | None) -> bool:
    """Report whether a stored credential has already been migrated."""
    return bool(stored) and stored.startswith(_ARGON2_PREFIX)


def verify_password(stored: str | None, password: str) -> bool:
    """Check a password against a stored Argon2id hash.

    Returns False rather than raising for every rejection, including a stored
    value that is missing or unreadable: an account with no usable credential
    must fail closed, never fall through to another branch.
    """
    if not stored or not password:
        return False
    try:
        return _HASHER.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    """Report whether a valid hash uses outdated parameters."""
    try:
        return _HASHER.check_needs_rehash(stored)
    except (InvalidHashError, ValueError):
        return False
