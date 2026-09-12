"""admin_password.py — recover administrator access from the command line.

Passwords are Argon2id hashes, so a lost one cannot be read back out of the
database. This is the way back in when nobody can sign in: an operator with
access to the server sets a password directly.

It is deliberately a local command rather than an HTTP endpoint. Anything
reachable over the network that resets an admin password is a way in for
whoever finds it; requiring shell access to the host keeps the recovery path
as privileged as the thing it recovers.

Usage:
    python admin_password.py --list
    python admin_password.py --handle flora_muenster            # prompts
    python admin_password.py --handle flora_muenster --from-env # ADMIN_PASSWORD
    python admin_password.py --handle newadmin --create --trusted
    python admin_password.py --handle newadmin --invite         # print a link
"""

import argparse
import getpass
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import auth_links
from admin_auth import hash_password

load_dotenv()


def _connect():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL must be set in the environment or .env")
    return psycopg2.connect(database_url)


def _read_password(from_env: bool) -> str:
    if from_env:
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not password:
            raise SystemExit("--from-env given but ADMIN_PASSWORD is not set")
    else:
        # getpass keeps the password out of the shell history and off the screen.
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords did not match")
    # One rule for chosen passwords, shared with the link-redemption endpoint.
    rejection = auth_links.password_rejection_reason(password)
    if rejection:
        raise SystemExit(rejection)
    return password


def list_admins(cur) -> None:
    auth_links.expire_elapsed(cur)
    cur.execute(
        """
        SELECT a.handle, a.trusted, a.password_hash IS NOT NULL AS has_password,
               a.created_at::date AS joined,
               EXISTS (SELECT 1 FROM auth_links l
                       WHERE l.subject_kind = 'admin' AND l.subject_id = a.id
                         AND l.status = 'pending') AS link_pending
        FROM admins a ORDER BY a.id
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("No administrators exist. Create one with --create.")
        return
    print(f"{'handle':24} {'trusted':>8} {'password set':>13} {'link open':>10}  joined")
    for row in rows:
        print(
            f"{row['handle']:24} {str(row['trusted']):>8} "
            f"{str(row['has_password']):>13} {str(row['link_pending']):>10}  {row['joined']}"
        )


def set_password(cur, handle: str, password: str, create: bool, trusted: bool) -> None:
    cur.execute("SELECT id FROM admins WHERE handle = %s", (handle,))
    existing = cur.fetchone()
    if existing:
        if create:
            raise SystemExit(f"Administrator {handle!r} already exists")
        cur.execute(
            "UPDATE admins SET password_hash = %s WHERE id = %s",
            (hash_password(password), existing["id"]),
        )
        print(f"Password updated for {handle!r}.")
        return
    if not create:
        raise SystemExit(
            f"No administrator named {handle!r}. Pass --create to add one, "
            "or run --list to see existing handles."
        )
    cur.execute(
        "INSERT INTO admins (handle, password_hash, trusted) VALUES (%s, %s, %s)",
        (handle, hash_password(password), trusted),
    )
    print(f"Created administrator {handle!r} (trusted={trusted}).")


def issue_link(cur, handle: str, email: str | None) -> None:
    """Mint a one-time link so the holder can set their own password.

    Printed rather than emailed: this command is the fallback for when email is
    unavailable or nobody can sign in, so it must not depend on Resend.
    """
    cur.execute("SELECT id, email, password_hash FROM admins WHERE handle = %s", (handle,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"No administrator named {handle!r}. Run --list to see handles.")
    address = email or row["email"]
    if not address:
        raise SystemExit(
            f"{handle!r} has no email on file. Pass --email to record one."
        )
    if email and email != row["email"]:
        cur.execute("UPDATE admins SET email = %s WHERE id = %s", (email, row["id"]))
    # An account that has never had a password is being invited; one that has is
    # recovering. The distinction only changes the wording and the lifetime.
    purpose = (
        auth_links.PURPOSE_INVITE if not row["password_hash"]
        else auth_links.PURPOSE_RESET
    )
    raw_token = auth_links.issue_for_admin(cur, row["id"], address, purpose, "cli")
    hours = auth_links.ttl_hours(purpose)
    print(f"\n{purpose} link for {handle!r} <{address}>")
    print(f"Valid once, for {hours} hours. Give it to that person directly:\n")
    print(f"  {auth_links.build_url(raw_token, purpose)}\n")
    print("Any earlier outstanding link for this account has been revoked.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set or create an administrator password"
    )
    parser.add_argument("--handle", help="Administrator handle to act on")
    parser.add_argument("--list", action="store_true", help="List administrators and exit")
    parser.add_argument("--create", action="store_true", help="Create the handle if absent")
    parser.add_argument(
        "--trusted",
        action="store_true",
        help="With --create, allow this admin to manage other admins",
    )
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="Take the password from ADMIN_PASSWORD instead of prompting",
    )
    parser.add_argument(
        "--invite",
        action="store_true",
        help="Print a one-time link instead of setting a password directly",
    )
    parser.add_argument("--email", help="Email address to record for this admin")
    args = parser.parse_args()

    if not args.list and not args.handle:
        parser.error("give --handle, or --list to see existing administrators")
    if args.invite and args.from_env:
        parser.error("--invite mints a link; it does not take a password")

    conn = _connect()
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    try:
        # One transaction: it commits on a clean exit and rolls back on error,
        # so a failed hash or a bad handle never leaves a half-written account.
        with conn, conn.cursor() as cur:
            if args.list:
                list_admins(cur)
                return
            if args.invite:
                if args.create:
                    if not args.email:
                        raise SystemExit("--create --invite also needs --email")
                    cur.execute(
                        "INSERT INTO admins (handle, email, password_hash, trusted) "
                        "VALUES (%s, %s, NULL, %s)",
                        (args.handle, args.email, args.trusted),
                    )
                    print(f"Created administrator {args.handle!r} with no password yet.")
                issue_link(cur, args.handle, args.email)
                return
            password = _read_password(args.from_env)
            set_password(cur, args.handle, password, args.create, args.trusted)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
