import csv
import io
import ipaddress
import json
import logging
import os
import random
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Literal
from uuid import UUID

import psycopg2
import psycopg2.extras
import psycopg2.errors
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import (
    Body, Depends, FastAPI, HTTPException, Request,
    Response,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import hashlib
import hmac
import resend
import flora_service
from flora_public_api import create_router as create_flora_api_router, is_public_read_request
import source_records_service
import source_sync_runner
from email_templates import forgot_handle_email
import auth_links
import security_events
import sessions
from admin_auth import hash_password, verify_password
from email_templates import (
    admin_invite_email,
    admin_reset_email,
    validator_signin_notice_email,
)
from extractor_storage import resolve_data_dir
from static_assets import fingerprinted_index
from extractor_vocab import (
    REPLICATION_OUTCOMES,
    derive_reproduction_outcome,
    normalize_axis_value,
    normalize_outcome,
    split_joined_outcome,
)

load_dotenv()

ROOT = Path(__file__).parent
SCHEMA_PATH = ROOT / "db_schema.sql"
ONBOARDING_PATH = ROOT / "onboarding.json"
OA_CACHE_PATH = ROOT / "oa_cache.json"
# Snapshot archives must survive pod replacement, so the directory is
# configurable (EXTRACTOR_DATA_DIR) and expected to be shared durable storage.
DATA_DIR = resolve_data_dir()

DATABASE_URL = os.environ["DATABASE_URL"]
# Bootstrap credentials for the very first administrator. The handle is not a
# secret and may live in code; the password must not, because this repository is
# public and a literal here would be a published admin credential. There is
# deliberately NO fallback value: an empty admins table with no configured
# password makes startup fail loudly instead of seeding a known account.
ADMIN_HANDLE     = os.getenv("ADMIN_HANDLE", "flora_muenster")
ADMIN_PASSWORD   = os.getenv("ADMIN_PASSWORD", "")
RESEND_API_KEY   = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM       = os.getenv("EMAIL_FROM", "Flora Validator <noreply@forrt.org>")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9._\-]{2,32}$")

VALID_CHECKS = {"correct", "incorrect"}

SKIP_COMMENT_REQUIRED_CODES = {"eligibility_unclear", "data_quality", "other"}
# What a skip from the pre-reason-picker frontend means. It is a real reason
# code, not a sentinel: it never requires a comment and never counts toward the
# issue-based escalation below, so a mid-deployment skip cannot distort the
# admin Skipped queue.
LEGACY_SKIP_REASON = "prefer_another"
# A record enters the Skipped admin panel after the sixth distinct validator,
# matching the agreed "more than five" rule. Two independent issue reports are
# enough regardless of the record's overall skip count.
SKIP_DISTINCT_VALIDATOR_THRESHOLD = 5
SKIP_ISSUE_VALIDATOR_THRESHOLD = 2

# Capability stamps authorise exactly one automatic release after the server has
# confirmed that a queued judgement could not be saved. The raw stamp is returned
# once to the browser; only its SHA-256 digest is stored in PostgreSQL.
SUBMISSION_FAILURE_STAMP_TTL_MINUTES = max(
    1, int(os.getenv("SUBMISSION_FAILURE_STAMP_TTL_MINUTES", "30"))
)

logger = logging.getLogger(__name__)

CURRENT_UPDATE_VERSION = 1  # bump when docs/updates.json content changes


# ---------------------------------------------------------------------------
# Sessions: the server decides who the caller is
# ---------------------------------------------------------------------------

# Requests that change state must originate from our own page. SameSite=Lax on
# the cookie already blocks the cross-site form post; this is the second lock,
# because SameSite is a browser default that a caller could be running without.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _allowed_origins() -> set[str]:
    configured = os.environ.get("ALLOWED_ORIGINS", "")
    origins = {o.strip().rstrip("/") for o in configured.split(",") if o.strip()}
    origins.add(auth_links.app_base_url())
    return origins


def _is_cross_site(request: Request) -> bool:
    """Whether a state-changing request came from somewhere other than our page."""
    if request.method in _SAFE_METHODS:
        return False
    # Sent by current browsers and unambiguous, so it is checked first and needs
    # no configuration to be correct.
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site:
        return fetch_site not in {"same-origin", "same-site", "none"}
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin:
        # No Origin and no Sec-Fetch-Site means a non-browser client — curl, a
        # script, a server. Those carry no ambient cookie, so they are not what
        # CSRF is about.
        return False
    return origin not in _allowed_origins()


# A human retrying a forgotten password stays well inside these; a script does
# not. The window is short enough that a genuine lockout clears itself.
LOGIN_MAX_FAILURES = 8
LOGIN_WINDOW_MINUTES = 15


def _trusted_proxy_hops() -> int:
    """How many reverse proxies we run sit in front of this app.

    The default of 1 matches the documented deployment (`Procfile`: a single
    platform router in front of uvicorn). Set it to 0 when nothing proxies this
    app, so `X-Forwarded-For` is ignored entirely and only the peer address
    counts. Raising it above the real number of proxies costs accuracy, not
    safety: the address resolves to a proxy rather than to a caller-chosen value.
    """
    raw = os.getenv("TRUSTED_PROXY_HOPS", "1")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        logger.warning("TRUSTED_PROXY_HOPS=%r is not a number; assuming 1", raw)
        return 1


TRUSTED_PROXY_HOPS = _trusted_proxy_hops()


def _client_ip(request: Request) -> str:
    """The client address, trusting only the part a proxy we run vouched for.

    `X-Forwarded-For` is a list that grows left to right: each proxy APPENDS the
    address it actually saw. So with one proxy in front of us an honest request
    arrives as "<client>", and a request from a caller who sent the header
    themselves arrives as "<whatever they typed>, <client>". Reading the leftmost
    entry — the obvious choice, and the one this used to make — therefore reads a
    value the caller chose, and lets them present a different "address" on every
    request. That is not merely cosmetic in the audit log: it is the clause the
    sign-in throttle falls back on once the identifier varies per attempt too, so
    a spray across accounts could never accumulate against anything.

    Counting from the RIGHT instead reads the entry our own proxy wrote. Honest
    callers are unaffected — with one hop the two readings are the same value.
    A chain shorter than the configured hop count is not trusted at all: either
    no proxy is in front of us (local runs, where the peer address is the truth)
    or the deployment is misconfigured, and falling back to the direct peer fails
    towards over-throttling rather than towards a bypass.
    """
    direct = (request.client.host if request.client else "")
    if TRUSTED_PROXY_HOPS:
        chain = [part.strip() for part
                 in request.headers.get("x-forwarded-for", "").split(",")
                 if part.strip()]
        if len(chain) >= TRUSTED_PROXY_HOPS:
            candidate = chain[-TRUSTED_PROXY_HOPS]
            try:
                # Keep junk out of security_events, and out of the throttle's
                # equality test, whatever a proxy or caller put on the wire.
                ipaddress.ip_address(candidate)
                return candidate[:64]
            except ValueError:
                logger.warning("Ignoring malformed X-Forwarded-For entry %r", candidate[:64])
    return direct[:64]


def _throttle_login(identifier: str, request: Request) -> None:
    """Refuse further attempts once recent failures pile up."""
    ip = _client_ip(request)
    with db() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM login_attempts
            WHERE succeeded = FALSE
              AND attempted_at > NOW() - (%s * INTERVAL '1 minute')
              AND (identifier = %s OR (client_ip = %s AND client_ip <> ''))
            """,
            (LOGIN_WINDOW_MINUTES, (identifier or "").lower(), ip),
        )
        if cur.fetchone()["n"] >= LOGIN_MAX_FAILURES:
            raise HTTPException(
                429,
                "Too many sign-in attempts. Wait a few minutes and try again.",
            )


def _record_login_attempt(identifier: str, request: Request, succeeded: bool) -> None:
    try:
        with db() as cur:
            cur.execute(
                "INSERT INTO login_attempts (identifier, client_ip, succeeded) "
                "VALUES (%s, %s, %s)",
                ((identifier or "").lower()[:200], _client_ip(request), succeeded),
            )
    except Exception:
        # Never let bookkeeping turn a valid sign-in into an error.
        logger.exception("Could not record a login attempt")


def _audit(cur, action: str, request: Request, *, actor: dict | None = None,
           actor_kind: str = "admin", **fields) -> None:
    """Record a privileged action with the caller the server actually resolved."""
    security_events.record(
        cur, action,
        actor_kind=actor_kind if actor else "system",
        actor_id=(actor or {}).get("id") or (actor or {}).get("coder_id"),
        actor_handle=(actor or {}).get("handle"),
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        **fields,
    )


def _session_token(request: Request) -> str:
    return request.cookies.get(sessions.COOKIE_NAME, "")


def _principal(request: Request, kind: str) -> dict | None:
    """Resolve the session cookie to a live principal of the expected kind."""
    raw_token = _session_token(request)
    if not raw_token:
        return None
    with db() as cur:
        session = sessions.lookup(cur, raw_token)
        if not session or session["principal_kind"] != kind:
            return None
        if kind == sessions.KIND_VALIDATOR:
            cur.execute(
                "SELECT id AS coder_id, handle, validator_tier, onboarded_at, "
                "last_seen_update FROM validators WHERE id = %s",
                (session["principal_id"],),
            )
        else:
            cur.execute(
                "SELECT id, handle, trusted FROM admins WHERE id = %s",
                (session["principal_id"],),
            )
        return cur.fetchone()


def current_validator(request: Request) -> dict:
    """The signed-in validator, or 401. Replaces client-supplied coder_id."""
    validator = _principal(request, sessions.KIND_VALIDATOR)
    if validator is None:
        raise HTTPException(401, "Please sign in again")
    return validator


def current_admin(request: Request) -> dict:
    """The signed-in administrator, or 401."""
    admin = _principal(request, sessions.KIND_ADMIN)
    if admin is None:
        raise HTTPException(401, "Unauthorized")
    return admin


def current_trusted_admin(request: Request) -> dict:
    admin = current_admin(request)
    if not admin["trusted"]:
        raise HTTPException(403, "Only trusted admins can manage admin accounts")
    return admin


def _begin_session(response: Response, kind: str, principal_id: int,
                   request: Request, remember: bool = False) -> None:
    """Open a session and attach its cookie to the response.

    `remember` only lengthens the session. Nothing is written to the browser
    beyond the HttpOnly cookie, so "remembered" never means a credential is
    sitting on the machine where a script could read it.
    """
    with db() as cur:
        raw_token = sessions.create(
            cur, kind, principal_id, request.headers.get("user-agent"), remember
        )
    response.set_cookie(
        sessions.COOKIE_NAME,
        raw_token,
        **sessions.cookie_kwargs(sessions.ttl_seconds(kind, remember)),
    )


# The bearer token derived from the stored password hash used to live here,
# along with _require_admin/_require_trusted_admin. Sessions replaced it: a
# derived token could not expire or be revoked, and every admin sharing a
# password shared one. Removed rather than left unused so it cannot be wired
# back up by accident -- current_admin above is the only way in.


def _migrate_admin_passwords():
    """Hash any plaintext admin password in place, then drop the old column.

    Every admin keeps the password they already use: the value stored on the
    row is what gets hashed, so nothing is reset and nobody has to be emailed.
    The plaintext column is dropped only once every row carries a hash, so a
    failure part-way through leaves credentials recoverable rather than lost.
    """
    with db() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'admins' AND column_name = 'password'
            """
        )
        if not cur.fetchone():
            return  # already migrated on an earlier start

        cur.execute("SELECT id, handle, password, password_hash FROM admins")
        migrated = 0
        for row in cur.fetchall():
            if row["password_hash"]:
                continue
            if not row["password"]:
                # Nothing to carry over. Leaving the hash NULL is the safe
                # outcome: the account exists but cannot be signed into.
                logger.warning(
                    "Admin %r has no password to migrate; it cannot sign in "
                    "until one is set",
                    row["handle"],
                )
                continue
            cur.execute(
                "UPDATE admins SET password_hash = %s WHERE id = %s",
                (hash_password(row["password"]), row["id"]),
            )
            migrated += 1

        cur.execute(
            "SELECT COUNT(*) AS n FROM admins "
            "WHERE password IS NOT NULL AND password_hash IS NULL"
        )
        if cur.fetchone()["n"]:
            logger.error(
                "Admin password migration incomplete; keeping the plaintext "
                "column so that no credential is lost"
            )
            return

        cur.execute("ALTER TABLE admins DROP COLUMN IF EXISTS password")
        if migrated:
            logger.warning(
                "Hashed %d administrator password(s) and removed the plaintext "
                "column. The passwords themselves are unchanged, but existing "
                "admin tokens are now invalid -- each admin signs in once more.",
                migrated,
            )
            security_events.record(
                cur, security_events.ADMIN_PASSWORDS_MIGRATED,
                actor_kind="system", detail={"accounts": migrated},
            )


def _seed_admin_if_empty():
    """Create the bootstrap administrator from the environment, or fail closed.

    Seeding a hard-coded default is what made every previous install reachable
    by anyone who read this public repository, so with no ADMIN_PASSWORD
    configured the application refuses to start rather than creating an account
    whose credential is published.
    """
    with db() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM admins")
        if cur.fetchone()["n"]:
            return
        if not ADMIN_PASSWORD:
            raise RuntimeError(
                "No administrator exists and ADMIN_PASSWORD is not set. Set "
                "ADMIN_PASSWORD (and optionally ADMIN_HANDLE, default "
                f"{ADMIN_HANDLE!r}) in the environment and start again. It is "
                "never given a default value because this repository is public."
            )
        cur.execute(
            "INSERT INTO admins (handle, password_hash, trusted) "
            "VALUES (%s, %s, TRUE)",
            (ADMIN_HANDLE, hash_password(ADMIN_PASSWORD)),
        )
        logger.warning("Created bootstrap administrator %r", ADMIN_HANDLE)
        security_events.record(
            cur, security_events.ADMIN_BOOTSTRAPPED, actor_kind="system",
            target_kind="admin", target_label=ADMIN_HANDLE,
        )

app = FastAPI(title="Flora Validator")


@app.middleware("http")
async def block_cross_site_writes(request: Request, call_next):
    """Refuse state-changing requests that did not originate from our page.

    In middleware rather than in the auth dependencies so that no route can
    quietly opt out: logout, sign-in redemption and any future endpoint are
    covered without having to remember. Together with SameSite=Lax on the
    session cookie this is the CSRF defence — one from the browser, one from
    the server, so neither has to be trusted alone.
    """
    if _is_cross_site(request) and not is_public_read_request(request):
        return JSONResponse(
            {"detail": "Cross-site requests are not accepted"}, status_code=403
        )
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        # Authenticated answers must not sit in a shared or browser cache where
        # the next person on the machine could read them back.
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

@contextmanager
def db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Same key family as extractor_maintenance / source_sync_runner.
SCHEMA_INIT_ADVISORY_LOCK_ID = 7_342_025_093


def init_db():
    """Apply db_schema.sql (idempotent) and seed from extracted_latest.csv if DB is empty.

    Every uvicorn worker and every pod runs this at import. Two sessions replaying
    the same DDL at once take AccessExclusiveLocks in different orders, and
    PostgreSQL aborts one with DeadlockDetected, killing that worker. A
    session-level advisory lock makes them take turns; it is held on a connection
    of its own because the seed below runs csv_to_db.py in a subprocess that must
    not queue behind this process's transactions. PostgreSQL drops the lock if the
    process dies mid-bootstrap."""
    lock_conn = psycopg2.connect(DATABASE_URL)
    lock_conn.autocommit = True
    try:
        with lock_conn.cursor() as lock_cur:
            lock_cur.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_INIT_ADVISORY_LOCK_ID,))

        with db() as cur:
            cur.execute(SCHEMA_PATH.read_text())

        # Seed unvalidated table from latest CSV if it has never been loaded
        with db() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM unvalidated")
            if cur.fetchone()["n"] == 0:
                latest_csv = DATA_DIR / "extracted_latest.csv"
                if latest_csv.exists():
                    import subprocess, sys
                    subprocess.run(
                        [sys.executable, str(ROOT / "csv_to_db.py"), "--input", str(latest_csv),
                         "--allow-legacy-schema"],
                        # The bundled seed is an intentional archived snapshot. It
                        # predates the current Stage-3 columns, so opt into legacy
                        # replay explicitly, but never ignore an actual import error:
                        # an empty app that appears healthy is worse than a failed
                        # deployment with an actionable traceback.
                        check=True,
                    )
    finally:
        # Closing the session releases the advisory lock.
        lock_conn.close()


init_db()
_migrate_admin_passwords()
_seed_admin_if_empty()


# ---------------------------------------------------------------------------
# OpenAlex URL cache
# ---------------------------------------------------------------------------

_OA_CACHE: dict[str, dict] | None = None


def oa_url_for(doi: str | None) -> str | None:
    global _OA_CACHE
    if _OA_CACHE is None:
        try:
            _OA_CACHE = json.loads(OA_CACHE_PATH.read_text())
        except FileNotFoundError:
            _OA_CACHE = {}
    if not doi:
        return None
    return (_OA_CACHE.get(doi.strip()) or {}).get("oa_url")


# A replication paper may target several originals. The extractor codes one row
# per (replication, original) pair, so Gate II serves a validator exactly one of
# them — and a paper that replicates a dominant original alongside secondary ones
# is legitimately coded as any subset of that set. That makes "is this the right
# original?" unanswerable from a single row: coding the dominant original alone,
# or all three, is correct, while a fourth paper that was never replicated is not,
# and the three cases are indistinguishable until the validator sees the set.

# A runaway guard, not an expected ceiling: comfortably past any real paper, so a
# truncated set means the data is wrong rather than the paper being large.
_CODED_ORIGINALS_LIMIT = 100

# Two predicates, for two different reasons.
#
# Rejected rows are not part of the coded set. A duplicate resolved by an admin is
# deliberately retained in unvalidated as 'rejected' (see validated_record_merges)
# carrying the SAME original as its survivor, so listing both showed one original
# twice and inflated the count; not-a-validation and wrong-original rejections are
# dead pairs for the same reason. Filtering is not anchoring — the status gates the
# WHERE clause and never reaches the payload, which carries no validation_status
# and no judgement fields at all: a sibling's verdict would anchor the second
# validator against the two-human consensus design.
#
# The DOI is matched case-insensitively. DOI names are case-insensitive by spec and
# doi_r is only whitespace-stripped on import (csv_to_db._s), so an exact match
# could split one paper's coded set and hide originals — the failure this feature
# exists to prevent. Scoped deliberately to this lookup: the UNIQUE (doi_r, …) pair
# identity is still exact, and widening that is a separate decision. lower() on both
# sides rather than str.lower() in Python so the predicate and the functional index
# agree on collation; the index expression must match or the planner ignores it.
_CODED_ORIGINALS_WHERE = (
    "lower(u.doi_r) = lower(%s) AND u.validation_status <> 'rejected'")


def _coded_originals_sort_key(r):
    """Mirror of the SQL ordering, for restoring natural order in Python after the
    row under judgement has been pinned into the result window."""
    rank, year, title = r["original_rank"], r["year_o"], r["title_o"]
    return (
        rank is None, rank if rank is not None else 0,
        year is None, year if year is not None else "",
        title is None, title if title is not None else "",
        str(r["record_id"]),
    )


def _coded_originals(cur, doi_r, record_id) -> tuple[list[dict], int]:
    """Every original coded for the same replication paper, this one included.

    Returns (originals, total). `total` counts the whole coded set even when the
    list was truncated, so the UI never reports a smaller set than exists.

    Returns ([], 0) for a replication with no DOI: '' is not an identity, and
    grouping on it would pull every DOI-less replication into one set. Such a
    record simply shows no coded set, exactly as it did before.
    """
    doi = (doi_r or "").strip()
    if not doi:
        return [], 0
    here = str(record_id or "")
    cur.execute(
        f"""
        SELECT u.record_id, u.doi_o, u.study_o, u.title_o, u.year_o,
               u.url_o, u.oa_work_id_o, u.study_r, rm.authors_o,
               rm.original_rank,
               -- Window functions run before LIMIT, so this is the true size of
               -- the coded set rather than the size of the truncated window.
               COUNT(*) OVER () AS coded_total
        FROM unvalidated u
        LEFT JOIN record_metadata rm ON rm.record_id = u.record_id
        WHERE {_CODED_ORIGINALS_WHERE}
        -- The row under judgement is pinned into the window first: truncation must
        -- never drop it, or Gate II highlights nothing while instructing the
        -- validator to judge the highlighted original. Compared as text so a
        -- str or UUID record_id both work and a malformed one cannot raise.
        -- Natural order is restored in Python immediately below.
        ORDER BY (u.record_id::text = %s) DESC,
                 rm.original_rank NULLS LAST, u.year_o NULLS LAST,
                 u.title_o, u.record_id
        LIMIT %s
        """,
        (doi, here, _CODED_ORIGINALS_LIMIT),
    )
    rows = cur.fetchall()
    total = rows[0]["coded_total"] if rows else 0
    out = []
    for r in sorted(rows, key=_coded_originals_sort_key):
        out.append({
            "record_id":    str(r["record_id"]),
            "doi_o":        r["doi_o"],
            "study_o":      r["study_o"],
            "study_r":      r["study_r"],
            "title_o":      r["title_o"],
            "year_o":       r["year_o"],
            "url_o":        r["url_o"],
            "oa_work_id_o": r["oa_work_id_o"],
            "authors_o":    r["authors_o"],
            "oa_url_o":     oa_url_for(r["doi_o"]),
            "is_current":   str(r["record_id"]) == here,
        })
    return out, total


def _enrich_pair(pair: dict, cur=None) -> dict:
    """Add OA URLs and the one remaining legacy frontend alias.

    With a cursor, also attaches `coded_originals` — every original coded for this
    replication paper (see _coded_originals). Callers without one keep the old
    payload shape; the frontend renders the set only when it has more than one.
    """
    pair = dict(pair)
    pair["oa_url_r"] = oa_url_for(pair.get("doi_r"))
    pair["oa_url_o"] = oa_url_for(pair.get("doi_o"))
    # Titles are first-class fields. Never fall back to study_* here: those are
    # Stage-3 within-paper study numbers, not display text.
    pair.setdefault("title_r", "")
    pair.setdefault("title_o", "")
    pair.setdefault("outcome_phrase", pair.get("outcome_quote", ""))
    if cur is not None:
        pair["coded_originals"], pair["coded_originals_total"] = _coded_originals(
            cur, pair.get("doi_r"), pair.get("record_id"))
    return pair


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ClaimCodeRequest(BaseModel):
    handle: str = Field(max_length=128)
    remember: bool = False
    code: str = Field(max_length=128)
    email: str = Field(max_length=254)


class LoginRequest(BaseModel):
    handle: str = Field(max_length=128)
    # "Stay signed in on this device". Controls how long the session lasts, not
    # whether anything is stored in the browser: no credential ever is.
    remember: bool = False
    # No `code` field. The personal-code path was a second, weaker way in: the
    # code was chosen by the user, stored in plaintext and handed back in the
    # login profile. Sign-in is the emailed link only.
    email: str | None = Field(default=None, max_length=254)


class JudgeRequest(BaseModel):
    record_id: str                      # unvalidated.record_id (UUID)
    # Browser-generated idempotency identity for durable/background submissions.
    # Synchronous and older clients may omit it, but then the server cannot issue
    # an automatic-release capability if saving fails.
    submission_id: UUID | None = None
    type_check: str                     # "correct" | "incorrect"
    original_check: str                 # "correct" | "incorrect"
    outcome_check: str                  # "correct" | "incorrect"
    corrected_title_r: str | None = None
    corrected_study_r: str | None = None  # legacy client alias for corrected_title_r
    corrected_url_r: str | None = None
    corrected_doi_o: str | None = None
    corrected_title_o: str | None = None
    corrected_study_o: str | None = None  # legacy client alias for corrected_title_o
    corrected_outcome: str | None = None
    corrected_type: str | None = None
    corrected_outcome_quote: str | None = None
    corrected_abstract: str | None = None
    # Reproductions: two independently coded axes, each with its own evidence. A
    # validator judges them separately — one quote cannot justify two judgements —
    # so a correction on one axis must not overwrite the other's.
    corrected_outcome_computation: str | None = None
    corrected_computational_quote: str | None = None
    corrected_computational_source: str | None = None
    corrected_outcome_robustness: str | None = None
    corrected_robustness_quote: str | None = None
    corrected_robustness_source: str | None = None
    doi_r_published: str | None = None      # preprint replications: DOI of the published article
    validator_notes: str | None = None
    additional_checks: dict | None = None   # e.g. {"was_unsure_original": true}


class SkipRequest(BaseModel):
    record_id: str
    # Optional so a rolling deployment stays safe. A browser still running the
    # previous frontend posts no reason_code, and rejecting it with 422 would
    # strand that validator: the old page clears its local draft BEFORE calling
    # /api/skip, so a failed release destroys unsent work and leaves the record
    # claimed. The old skip button offered no choice and its dialog described
    # exactly one intent — "you would prefer a fresh one" — so LEGACY_SKIP_REASON
    # records that same meaning rather than inventing a judgement about it.
    # Remove the default only once no old page can still be open.
    reason_code: Literal[
        "prefer_another",
        "inaccessible",
        "eligibility_unclear",
        "data_quality",
        "interpretation_unclear",
        "other",
    ] = LEGACY_SKIP_REASON
    comment: str | None = None


class SubmissionFailureReleaseRequest(BaseModel):
    failure_stamp: str


class SeniorRejectRequest(BaseModel):
    record_id: str
    validator_notes: str | None = None


class ForgotHandleRequest(BaseModel):
    email: str


class AdminLoginRequest(BaseModel):
    remember: bool = False
    # Bounded so an unauthenticated caller cannot make the server hash a
    # megabyte with Argon2, or write one into the audit trail.
    handle: str = Field(max_length=128)
    password: str = Field(max_length=1024)


class FlagQueueRequest(BaseModel):
    reason: str = ""


class AdminMessageRequest(BaseModel):
    validator_id: int | None = None   # required unless broadcast=True
    subject: str
    body: str
    broadcast: bool = False           # send to every validator


class AdminReplyRequest(BaseModel):
    body: str


class ReplyRequest(BaseModel):
    body: str


class AdminResolveRequest(BaseModel):
    admin_name: str
    type_check: str
    original_check: str
    outcome_check: str
    corrected_doi_o: str | None = None
    corrected_title_o: str | None = None
    corrected_study_o: str | None = None  # legacy client alias
    corrected_outcome: str | None = None
    corrected_type: str | None = None
    corrected_outcome_quote: str | None = None
    corrected_title_r: str | None = None
    corrected_study_r: str | None = None  # legacy client alias
    corrected_doi_r: str | None = None
    corrected_url_r: str | None = None
    corrected_abstract_r: str | None = None
    # A named source ('abstract', 'discussion', 'results', 'abstract|discussion', …)
    # or 'full_text'. None/'' means auto-detect from the quote against the abstract.
    out_quote_source: str | None = None
    # Reproduction axes, edited separately because they are coded separately. Absent
    # from the form on a replication, so None means "leave the stored value alone".
    corrected_outcome_computation: str | None = None
    corrected_computational_quote: str | None = None
    corrected_computational_source: str | None = None
    corrected_outcome_robustness: str | None = None
    corrected_robustness_quote: str | None = None
    corrected_robustness_source: str | None = None
    # None = unchanged; '' = clear the stored value; anything else = set it.
    doi_r_published: str | None = None
    alt_identifier_r: str | None = None
    admin_notes: str | None = None
    # Set only by the second, explicitly confirmed duplicate-resolution request.
    # The server accepts it only when this exact record currently conflicts with
    # the named authoritative validated record.
    merge_into_record_id: str | None = None


class ServingConfigRequest(BaseModel):
    enabled: bool = False
    priority_outcome: str | None = None      # 'failed' | 'successful' | 'mixed' | None
    priority_year_min: int | None = None
    priority_year_max: int | None = None
    priority_share: int = 70                  # 0–100


class MaintenanceRunRequest(BaseModel):
    stage: Literal["full", "sync", "find", "cleanup"] = "full"
    # Deletion is deliberately isolated to the cleanup stage. Require explicit
    # acknowledgement even when a caller bypasses the browser confirmation.
    confirm_cleanup: bool = False


def _requested_title(req, side: str) -> str | None:
    """Read the title correction, accepting the pre-split API name during rollout."""
    value = getattr(req, f"corrected_title_{side}", None)
    if value is not None:
        return value
    return getattr(req, f"corrected_study_{side}", None)


def _request_additional_checks(req) -> dict:
    checks = getattr(req, "additional_checks", None)
    return checks if isinstance(checks, dict) else {}


def _request_is_unsure(req) -> bool:
    checks = _request_additional_checks(req)
    axis_checks = checks.get("reproduction_axis_checks")
    axis_unsure = isinstance(axis_checks, dict) and "unsure" in axis_checks.values()
    return bool(
        checks.get("was_unsure_original")
        or checks.get("was_unsure_outcome")
        or axis_unsure
    )


def _requested_reproduction_axes(req) -> tuple[str | None, str | None]:
    """Validate and canonicalise axis corrections, including legacy joined input.

    Older clients sent ``corrected_outcome='computation, robustness'``. Translate
    that shape at the API boundary, while rejecting a conflict between a joined
    correction and an explicitly supplied axis instead of silently choosing one.
    """
    try:
        computation = normalize_axis_value(
            "outcome_computation", getattr(req, "corrected_outcome_computation", None)
        )
        robustness = normalize_axis_value(
            "outcome_robustness", getattr(req, "corrected_outcome_robustness", None)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    joined = getattr(req, "corrected_outcome", None)
    legacy_computation, legacy_robustness = split_joined_outcome(joined)
    if legacy_computation and computation and legacy_computation != computation:
        raise HTTPException(400, "corrected_outcome conflicts with corrected_outcome_computation")
    if legacy_robustness and robustness and legacy_robustness != robustness:
        raise HTTPException(400, "corrected_outcome conflicts with corrected_outcome_robustness")
    return computation or legacy_computation, robustness or legacy_robustness


def _validated_outcome_request(req, base_type: str | None, base_outcome: str | None,
                               base_computation: str | None = None,
                               base_robustness: str | None = None) -> tuple[str, str | None, str | None, str | None]:
    """Return a coherent ``(type, outcome, computation, robustness)`` request.

    The effective values may fall back to the stored record when the caller says a
    field is unchanged. Type changes never get that fallback: moving between the
    replication and reproduction vocabularies requires a complete target-shape
    judgement, preventing a joined reproduction label from surviving as a
    replication outcome (and vice versa).
    """
    corrected_type = (req.corrected_type or "").strip() or None
    if req.type_check == "incorrect" and not corrected_type:
        raise HTTPException(400, "corrected_type is required when type_check is incorrect")
    if req.type_check != "incorrect" and corrected_type:
        raise HTTPException(400, "corrected_type must be blank when type_check is correct")
    target_type = (
        corrected_type
        if req.type_check == "incorrect" and corrected_type
        else base_type
    )
    if target_type == "not_validation":
        return target_type, None, None, None
    if target_type not in {"replication", "reproduction"}:
        raise HTTPException(400, "corrected_type must be replication, reproduction, or not_validation")

    requested_computation, requested_robustness = _requested_reproduction_axes(req)
    type_changed = target_type != base_type

    if target_type == "reproduction":
        joined = getattr(req, "corrected_outcome", None)
        joined_computation, joined_robustness = split_joined_outcome(joined)
        if joined and not (joined_computation and joined_robustness):
            normalised_joined = normalize_outcome(joined)
            if normalised_joined not in {None, "cannot_be_determined", "not_a_replication"}:
                raise HTTPException(
                    400,
                    "A reproduction corrected_outcome must be a valid joined axis value",
                )
        try:
            computation = requested_computation or normalize_axis_value(
                "outcome_computation", base_computation
            )
            robustness = requested_robustness or normalize_axis_value(
                "outcome_robustness", base_robustness
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not computation or not robustness:
            raise HTTPException(
                400,
                "A reproduction requires both outcome_computation and outcome_robustness",
            )
        return (
            target_type,
            derive_reproduction_outcome(computation, robustness),
            computation,
            robustness,
        )

    # Replications have one categorical outcome and no reproduction axes. Valid
    # axis fields on this request would still be the wrong data shape, so reject
    # them just as firmly as an arbitrary axis string.
    if requested_computation or requested_robustness:
        raise HTTPException(400, "Replication judgements must not include reproduction axes")
    requested_outcome = normalize_outcome(req.corrected_outcome)
    if requested_outcome and requested_outcome not in REPLICATION_OUTCOMES:
        raise HTTPException(400, f"Invalid replication outcome: {requested_outcome}")
    if type_changed or req.outcome_check == "incorrect":
        outcome = requested_outcome
    else:
        outcome = requested_outcome or normalize_outcome(base_outcome)
    if not outcome or outcome not in REPLICATION_OUTCOMES or outcome == "not_a_replication":
        raise HTTPException(
            400,
            "A replication requires a valid replication outcome; choose the corrected outcome",
        )
    return target_type, outcome, None, None


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def _normalize_doi(doi: str | None) -> str | None:
    """Bare DOI: strip a doi.org resolver prefix / 'doi:' and whitespace.
    Returns None for empty input so '' keeps its 'clear the value' meaning."""
    if doi is None:
        return None
    return re.sub(r'(?i)^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*', '', doi.strip()) or None


def _points_for(req: JudgeRequest, vote_score: int) -> int:
    """Calculate points for a submission. Base = validator's vote_score."""
    pts = vote_score
    checks = _request_additional_checks(req)
    if req.original_check == "correct" and not checks.get("was_unsure_original"):
        pts += 2
    axis_checks = checks.get("reproduction_axis_checks")
    axes_affirmed = (
        not isinstance(axis_checks, dict)
        or axis_checks.get("computation") == "correct"
        and axis_checks.get("robustness") == "correct"
    )
    if (req.outcome_check == "correct" and not checks.get("was_unsure_outcome")
            and axes_affirmed):
        pts += 2
    if req.validator_notes and req.validator_notes.strip():
        pts += 1
    return pts



# ---------------------------------------------------------------------------
# Login / onboarding endpoints
# ---------------------------------------------------------------------------

@app.post("/api/login")
def login(req: LoginRequest, request: Request, response: Response):
    """Sign in with a handle and the email address on that account.

    DELIBERATE PRODUCT DECISION, recorded so it is not mistaken for an
    oversight: the account owner is NOT asked to prove they hold the mailbox.
    Handle plus email is enough, and the email that follows is a notice after
    the fact. Handles are public on the leaderboard and academic addresses are
    often guessable, so anyone who knows both can obtain a real session for
    that validator. See docs/PROJECT.md section 19.

    Both values must still match the same account, so a wrong pairing is
    refused rather than silently creating a duplicate.
    """
    handle = req.handle.strip()
    if not HANDLE_RE.match(handle):
        raise HTTPException(400, "Handle must be 2-32 chars: letters, digits, . _ -")
    email = (req.email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter the email address for your account")
    # Still throttled: without a mailbox round-trip this is the only thing
    # standing between a guessed address and a working session.
    _throttle_login(email, request)

    with db() as cur:
        cur.execute(
            "SELECT id, handle, onboarded_at, validator_tier, last_seen_update "
            "FROM validators WHERE email = %s",
            (email,),
        )
        existing = cur.fetchone()
        is_new = existing is None

        if existing and existing["handle"] != handle:
            _record_login_attempt(email, request, False)
            raise HTTPException(
                400,
                "This email is already registered. Please use the correct username.",
            )

        if is_new:
            cur.execute("SELECT email FROM validators WHERE handle = %s", (handle,))
            taken = cur.fetchone()
            # Both answers below disclose something about an account the
            # caller does not own, so both count against the throttle. The
            # first is the more sensitive: it identifies accounts that still
            # hold a personal code, exactly the list worth guessing against
            # /api/login/claim-code. Unrecorded, either could be probed
            # without limit.
            if taken and not taken["email"]:
                # An account from before email sign-in. Saying so is safe --
                # handles are public on the leaderboard -- and it is the only
                # message that tells this person what to actually do.
                _record_login_attempt(email, request, False)
                raise HTTPException(
                    400,
                    "This account was created with a personal code. Use "
                    "\u201cSigned up with a personal code?\u201d below to add "
                    "your email address \u2014 you only need to do it once.",
                )
            if taken:
                _record_login_attempt(email, request, False)
                raise HTTPException(400, "That handle is already taken.")
            try:
                cur.execute(
                    "INSERT INTO validators(email, handle) VALUES (%s, %s) "
                    "RETURNING id, handle, onboarded_at, validator_tier, "
                    "last_seen_update",
                    (email, handle),
                )
                existing = cur.fetchone()
            except psycopg2.errors.UniqueViolation:
                # A concurrent first-time sign-in claimed this handle or email
                # between the checks above and this insert. Retrying resolves
                # it: the row now exists, so the next attempt either signs them
                # in or reports the real conflict. Without this the loser of the
                # race got a 500.
                raise HTTPException(
                    409,
                    "Someone just registered these details. Please try again.",
                )
        else:
            cur.execute(
                "UPDATE validators SET last_login_at = NOW() WHERE id = %s",
                (existing["id"],),
            )

    _record_login_attempt(email, request, True)
    _begin_session(response, sessions.KIND_VALIDATOR, existing["id"], request,
                   req.remember)
    _notify_sign_in(existing["handle"], email, is_new)
    return {
        "coder_id": existing["id"],
        "handle": existing["handle"],
        "onboarded": bool(existing["onboarded_at"]),
        "validator_tier": existing["validator_tier"],
        "last_seen_update": existing["last_seen_update"],
        "update_version": CURRENT_UPDATE_VERSION,
    }


@app.post("/api/login/claim-code")
def claim_code_account(req: ClaimCodeRequest, request: Request, response: Response):
    """One-time migration for an account created before email sign-in.

    The personal code is that account's existing credential, so presenting it is
    how its owner proves the account is theirs. Once an address is attached the
    code is cleared: keeping both would leave the weaker of the two as a
    permanent second way in.

    Only accounts with no email can be claimed. Without that restriction this
    would be a takeover path — knowing somebody's code would let an attacker
    point their account at a new mailbox.
    """
    handle = req.handle.strip()
    code = (req.code or "").strip()
    email = (req.email or "").strip().lower()
    if not HANDLE_RE.match(handle):
        raise HTTPException(400, "Enter the username for your account")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter the email address you want to use")
    if len(code) < 8:
        raise HTTPException(400, "Enter all four parts of your personal code")
    _throttle_login(handle, request)

    with db() as cur:
        # FOR UPDATE, because everything below is a check-then-act on this row.
        # Without the lock two people presenting the same code concurrently both
        # pass the "not yet claimed" test, both get a session, and whichever
        # writes last decides which mailbox now owns the account.
        cur.execute(
            "SELECT id, handle, code, email, onboarded_at, validator_tier, "
            "last_seen_update FROM validators WHERE handle = %s FOR UPDATE",
            (handle,),
        )
        account = cur.fetchone()
        # One message for every failure: a different answer for "no such user"
        # and "wrong code" would turn this into a way to test handles.
        wrong = HTTPException(
            400, "That username and personal code do not match an account."
        )
        if not account or not account["code"] or account["email"]:
            _record_login_attempt(handle, request, False)
            raise wrong
        # Case-insensitive: the code is four remembered fragments, and whether
        # somebody capitalised their street name is not a security boundary.
        if not hmac.compare_digest(account["code"].strip().lower(), code.lower()):
            _record_login_attempt(handle, request, False)
            raise wrong

        cur.execute("SELECT 1 FROM validators WHERE email = %s", (email,))
        if cur.fetchone():
            raise HTTPException(
                400,
                "That email address is already used by another account.",
            )
        # Conditional on the row still being unclaimed, so even if the lock
        # above were ever lost the write cannot silently repoint an account.
        cur.execute(
            "UPDATE validators SET email = %s, code = NULL "
            "WHERE id = %s AND email IS NULL",
            (email, account["id"]),
        )
        if cur.rowcount == 0:
            _record_login_attempt(handle, request, False)
            raise wrong

    _record_login_attempt(handle, request, True)
    with db() as cur:
        _audit(cur, security_events.VALIDATOR_CODE_CLAIMED, request,
               actor={"id": account["id"], "handle": account["handle"]},
               actor_kind="validator", target_kind="validator",
               target_id=account["id"], target_label=account["handle"],
               detail={"email": email})
    _begin_session(response, sessions.KIND_VALIDATOR, account["id"], request,
                   req.remember)
    _notify_sign_in(account["handle"], email, False)
    logger.warning("Validator %r exchanged a personal code for an email", handle)
    return {
        "coder_id": account["id"],
        "handle": account["handle"],
        "onboarded": bool(account["onboarded_at"]),
        "validator_tier": account["validator_tier"],
        "last_seen_update": account["last_seen_update"],
        "update_version": CURRENT_UPDATE_VERSION,
    }


def _notify_sign_in(handle: str, email: str, is_new: bool) -> None:
    """Tell the account owner that someone signed in.

    Best-effort by design: the session already exists, so a mail failure must
    not turn a successful sign-in into an error. It is also why this is only a
    notice and not a control -- it cannot prevent anything.
    """
    if not RESEND_API_KEY:
        return
    when = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    try:
        template = validator_signin_notice_email(handle, when, is_new)
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": EMAIL_FROM, "to": [email],
            "subject": template["subject"], "html": template["html"],
            "text": template["text"],
        })
    except Exception:
        logger.exception("Could not send a sign-in notice to %s", handle)


@app.get("/api/onboarding")
def onboarding_pairs():
    with open(ONBOARDING_PATH) as f:
        pairs = json.load(f)["pairs"]
    return {"pairs": [_enrich_pair(p) for p in pairs]}


@app.post("/api/onboarding/complete")
def onboarding_complete(validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            "UPDATE validators SET onboarded_at = NOW(), last_seen_update = %s WHERE id = %s AND onboarded_at IS NULL",
            (CURRENT_UPDATE_VERSION, coder_id),
        )
        if cur.rowcount == 0:
            cur.execute("SELECT onboarded_at FROM validators WHERE id = %s", (coder_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Validator not found")
    return {"onboarded": True}


@app.post("/api/update-seen")
def mark_update_seen(validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            "UPDATE validators SET last_seen_update = %s WHERE id = %s",
            (CURRENT_UPDATE_VERSION, coder_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Validator not found")
    return {"ok": True}


@app.get("/api/my-judgements")
def get_my_judgements(validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute("SELECT id FROM validators WHERE id = %s", (coder_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Validator not found")
        cur.execute(
            """
            SELECT
                vq.queue_id,
                vq.record_id,
                vq.type_check,
                vq.original_check,
                vq.outcome_check,
                vq.corrected_doi_o,
                vq.corrected_title_o,
                vq.corrected_outcome,
                vq.corrected_outcome_computation,
                vq.corrected_outcome_robustness,
                vq.corrected_type,
                vq.corrected_title_r,
                vq.corrected_url_r,
                vq.points,
                vq.validated_at,
                vq.flagged,
                vq.flag_reason,
                u.title_r,
                u.study_r,
                u.doi_r,
                u.year_r,
                u.outcome         AS extracted_outcome,
                u.outcome_computation AS extracted_outcome_computation,
                u.outcome_robustness  AS extracted_outcome_robustness,
                u.validation_status,
                vm.id             AS msg_id,
                vm.body           AS msg_body,
                vm.sent_at        AS msg_sent_at,
                vm.is_read        AS msg_is_read
            FROM validation_queue vq
            JOIN unvalidated u ON u.record_id = vq.record_id
            LEFT JOIN LATERAL (
                SELECT id, body, sent_at, is_read
                FROM validator_messages
                WHERE queue_id = vq.queue_id AND direction = 'outbound'
                ORDER BY sent_at DESC
                LIMIT 1
            ) vm ON true
            WHERE vq.validator_id = %s AND vq.is_validated = TRUE
            ORDER BY vq.validated_at DESC NULLS LAST
            LIMIT 100
            """,
            (coder_id,),
        )
        rows = cur.fetchall()
    judgements = []
    for r in rows:
        judgements.append({
            "queue_id":          str(r["queue_id"]),
            "record_id":         str(r["record_id"]),
            "type_check":        r["type_check"],
            "original_check":    r["original_check"],
            "outcome_check":     r["outcome_check"],
            "corrected_doi_o":   r["corrected_doi_o"],
            "corrected_title_o": r["corrected_title_o"],
            "corrected_study_o": r["corrected_title_o"],  # legacy response alias
            "corrected_outcome": r["corrected_outcome"],
            "corrected_outcome_computation": r["corrected_outcome_computation"],
            "corrected_outcome_robustness": r["corrected_outcome_robustness"],
            "corrected_type":    r["corrected_type"],
            "corrected_title_r": r["corrected_title_r"],
            "corrected_study_r": r["corrected_title_r"],  # legacy response alias
            "corrected_url_r":   r["corrected_url_r"],
            "points":            r["points"],
            "validated_at":      r["validated_at"].isoformat() if r["validated_at"] else None,
            "flagged":           bool(r["flagged"]),
            "flag_reason":       r["flag_reason"],
            "title_r":           r["title_r"],
            "study_r":           r["study_r"],
            "doi_r":             r["doi_r"],
            "year_r":            r["year_r"],
            "extracted_outcome": r["extracted_outcome"],
            "extracted_outcome_computation": r["extracted_outcome_computation"],
            "extracted_outcome_robustness": r["extracted_outcome_robustness"],
            "validation_status": r["validation_status"],
            "msg_id":            r["msg_id"],
            "msg_body":          r["msg_body"],
            "msg_sent_at":       r["msg_sent_at"].isoformat() if r["msg_sent_at"] else None,
            "msg_is_read":       bool(r["msg_is_read"]) if r["msg_is_read"] is not None else None,
        })
    return {"judgements": judgements}


@app.get("/api/my-judgements/{queue_id}")
def get_my_judgement_detail(queue_id: str,
                            validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            """
            SELECT
                vq.queue_id,
                vq.record_id,
                vq.validator_slot,
                vq.type_check,
                vq.original_check,
                vq.outcome_check,
                vq.corrected_doi_o,
                vq.corrected_title_o,
                vq.corrected_outcome,
                vq.corrected_outcome_quote,
                vq.corrected_outcome_computation,
                vq.corrected_computational_quote,
                vq.corrected_computational_source,
                vq.corrected_outcome_robustness,
                vq.corrected_robustness_quote,
                vq.corrected_robustness_source,
                vq.corrected_abstract,
                vq.corrected_type,
                vq.corrected_title_r,
                vq.corrected_url_r,
                vq.additional_checks,
                vq.validator_notes,
                vq.points,
                vq.validated_at,
                vq.flagged,
                vq.flag_reason,
                u.study_r        AS raw_study_r,
                u.title_r        AS raw_title_r,
                u.doi_r          AS raw_doi_r,
                u.year_r         AS raw_year_r,
                u.abstract_r     AS raw_abstract_r,
                u.doi_o          AS raw_doi_o,
                u.study_o        AS raw_study_o,
                u.title_o        AS raw_title_o,
                u.year_o         AS raw_year_o,
                u.url_o          AS raw_url_o,
                u.oa_work_id_o   AS raw_oa_work_id_o,
                u.type           AS extracted_type,
                u.outcome        AS extracted_outcome,
                u.outcome_quote  AS extracted_outcome_quote,
                u.outcome_computation            AS extracted_outcome_computation,
                u.outcome_computational_quote    AS extracted_computational_quote,
                u.out_quote_computational_source AS extracted_computational_source,
                u.outcome_robustness             AS extracted_outcome_robustness,
                u.outcome_robustness_quote       AS extracted_robustness_quote,
                u.out_quote_robust_source        AS extracted_robustness_source,
                u.validation_status,
                v.validated_record_id,
                v.study_r        AS val_study_r,
                v.title_r        AS val_title_r,
                v.doi_r          AS val_doi_r,
                v.year_r         AS val_year_r,
                v.abstract_r     AS val_abstract_r,
                v.doi_o          AS val_doi_o,
                v.study_o        AS val_study_o,
                v.title_o        AS val_title_o,
                v.year_o         AS val_year_o,
                v.url_o          AS val_url_o,
                v.oa_work_id_o   AS val_oa_work_id_o,
                v.type           AS val_type,
                v.outcome        AS val_outcome,
                v.outcome_quote  AS val_outcome_quote,
                v.outcome_computation            AS val_outcome_computation,
                v.outcome_computational_quote    AS val_computational_quote,
                v.out_quote_computational_source AS val_computational_source,
                v.outcome_robustness             AS val_outcome_robustness,
                v.outcome_robustness_quote       AS val_robustness_quote,
                v.out_quote_robust_source        AS val_robustness_source,
                v.admin_approved AS val_admin_approved,
                v.validated_at   AS val_validated_at
            FROM validation_queue vq
            JOIN unvalidated u ON u.record_id = vq.record_id
            LEFT JOIN validated v ON v.record_id = vq.record_id
            WHERE vq.queue_id = %s AND vq.validator_id = %s
            """,
            (queue_id, coder_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Judgement not found")
        # Fetch full message thread for this queue_id (outbound + replies), oldest first
        cur.execute(
            """
            SELECT id, body, sent_at, direction, is_read, parent_id
            FROM validator_messages
            WHERE queue_id = %s
            ORDER BY sent_at ASC
            """,
            (queue_id,),
        )
        thread = [
            {
                "id":        m["id"],
                "body":      m["body"],
                "sent_at":   m["sent_at"].isoformat() if m["sent_at"] else None,
                "direction": m["direction"],
                "is_read":   bool(m["is_read"]),
                "parent_id": m["parent_id"],
            }
            for m in cur.fetchall()
        ]
    has_validated = row["validated_record_id"] is not None
    return {
        "queue_id":               str(row["queue_id"]),
        "record_id":              str(row["record_id"]),
        "validator_slot":         row["validator_slot"],
        "type_check":             row["type_check"],
        "original_check":         row["original_check"],
        "outcome_check":          row["outcome_check"],
        "corrected_doi_o":        row["corrected_doi_o"],
        "corrected_title_o":      row["corrected_title_o"],
        "corrected_study_o":      row["corrected_title_o"],  # legacy response alias
        "corrected_outcome":      row["corrected_outcome"],
        "corrected_outcome_quote":row["corrected_outcome_quote"],
        "corrected_outcome_computation": row["corrected_outcome_computation"],
        "corrected_computational_quote": row["corrected_computational_quote"],
        "corrected_computational_source": row["corrected_computational_source"],
        "corrected_outcome_robustness": row["corrected_outcome_robustness"],
        "corrected_robustness_quote": row["corrected_robustness_quote"],
        "corrected_robustness_source": row["corrected_robustness_source"],
        "corrected_abstract":     row["corrected_abstract"],
        "corrected_type":         row["corrected_type"],
        "corrected_title_r":      row["corrected_title_r"],
        "corrected_study_r":      row["corrected_title_r"],  # legacy response alias
        "corrected_url_r":        row["corrected_url_r"],
        "additional_checks":      row["additional_checks"],
        "validator_notes":        row["validator_notes"],
        "points":                 row["points"],
        "validated_at":           row["validated_at"].isoformat() if row["validated_at"] else None,
        "flagged":                bool(row["flagged"]),
        "flag_reason":            row["flag_reason"],
        "validation_status":      row["validation_status"],
        # Raw extracted values (always present)
        "study_r":                row["raw_study_r"],
        "title_r":                row["raw_title_r"],
        "doi_r":                  row["raw_doi_r"],
        "year_r":                 row["raw_year_r"],
        "abstract_r":             row["raw_abstract_r"],
        "doi_o":                  row["raw_doi_o"],
        "study_o":                row["raw_study_o"],
        "title_o":                row["raw_title_o"],
        "year_o":                 row["raw_year_o"],
        "url_o":                  row["raw_url_o"],
        "oa_work_id_o":           row["raw_oa_work_id_o"],
        "extracted_type":         row["extracted_type"],
        "extracted_outcome":      row["extracted_outcome"],
        "outcome_quote":          row["extracted_outcome_quote"],
        "outcome_computation":    row["extracted_outcome_computation"],
        "outcome_computational_quote": row["extracted_computational_quote"],
        "out_quote_computational_source": row["extracted_computational_source"],
        "outcome_robustness":     row["extracted_outcome_robustness"],
        "outcome_robustness_quote": row["extracted_robustness_quote"],
        "out_quote_robust_source": row["extracted_robustness_source"],
        # Final validated consensus (null if record not yet fully validated)
        "has_validated":          has_validated,
        "val_study_r":            row["val_study_r"],
        "val_title_r":            row["val_title_r"],
        "val_doi_r":              row["val_doi_r"],
        "val_year_r":             row["val_year_r"],
        "val_abstract_r":         row["val_abstract_r"],
        "val_doi_o":              row["val_doi_o"],
        "val_study_o":            row["val_study_o"],
        "val_title_o":            row["val_title_o"],
        "val_year_o":             row["val_year_o"],
        "val_url_o":              row["val_url_o"],
        "val_oa_work_id_o":       row["val_oa_work_id_o"],
        "val_type":               row["val_type"],
        "val_outcome":            row["val_outcome"],
        "val_outcome_quote":      row["val_outcome_quote"],
        "val_outcome_computation": row["val_outcome_computation"],
        "val_computational_quote": row["val_computational_quote"],
        "val_computational_source": row["val_computational_source"],
        "val_outcome_robustness": row["val_outcome_robustness"],
        "val_robustness_quote": row["val_robustness_quote"],
        "val_robustness_source": row["val_robustness_source"],
        "val_admin_approved":     bool(row["val_admin_approved"]) if row["val_admin_approved"] is not None else False,
        "val_validated_at":       row["val_validated_at"].isoformat() if row["val_validated_at"] else None,
        # Full message thread (outbound from team + validator replies)
        "messages":               thread,
    }


# ---------------------------------------------------------------------------
# Validation workflow endpoints
# ---------------------------------------------------------------------------

# Columns selected for a servable pair (kept in one place for reuse).
_PAIR_SELECT = """
    u.record_id, u.pair_id,
    u.doi_r, u.study_r, u.title_r, u.year_r, u.url_r, u.ref_r, u.abstract_r,
    u.doi_o, u.study_o, u.title_o, u.year_o, u.url_o, u.ref_o, u.oa_work_id_o,
    u.type, u.outcome, u.outcome_quote, u.out_quote_source,
    -- Reproductions are coded on two independent axes, each with its own evidence.
    -- Served alongside the replication shape; `type` decides which the UI renders.
    u.outcome_computation, u.outcome_computational_quote, u.out_quote_computational_source,
    u.outcome_robustness, u.outcome_robustness_quote, u.out_quote_robust_source,
    rm.authors_r, rm.authors_o, rm.journal_r, rm.openalex_id_r,
    (SELECT COUNT(*) FROM validation_queue vq2
     WHERE vq2.record_id = u.record_id AND vq2.is_validated = TRUE) AS judge_count
"""


# A record is "hard" when its outcome is undeterminable or it has no abstract.
# These earn double points and are served only in hard mode.
_HARD_COND = "(u.outcome = 'cannot_be_determined' OR u.abstract_r IS NULL OR u.abstract_r = '')"


def _mode_sql(mode: str) -> str:
    """SQL predicate (over alias u) selecting the pool for a serving mode."""
    return _HARD_COND if mode == "hard" else f"NOT {_HARD_COND}"


def _record_is_hard(cur, record_id) -> bool:
    """Whether a record falls in the hard pool (for double-points scoring)."""
    cur.execute(
        f"SELECT {_HARD_COND} AS is_hard FROM unvalidated u WHERE u.record_id = %s",
        (record_id,),
    )
    row = cur.fetchone()
    return bool(row and row["is_hard"])


def _fetch_pair_row(cur, record_id):
    """Load a single record's servable fields by record_id."""
    cur.execute(
        f"SELECT {_PAIR_SELECT} FROM unvalidated u "
        f"LEFT JOIN record_metadata rm ON rm.record_id = u.record_id "
        f"WHERE u.record_id = %s",
        (record_id,),
    )
    return cur.fetchone()


_PRIORITY_OUTCOMES = {"failed", "successful", "mixed"}


def _serving_config(cur) -> dict:
    """The single serving_config row (or safe defaults if the table is empty)."""
    cur.execute(
        "SELECT enabled, priority_outcome, priority_year_min, priority_year_max, priority_share "
        "FROM serving_config WHERE id = 1"
    )
    row = cur.fetchone()
    return dict(row) if row else {
        "enabled": False, "priority_outcome": None,
        "priority_year_min": None, "priority_year_max": None, "priority_share": 70,
    }


def _priority_predicate(cfg: dict):
    """A SQL boolean over alias u (never NULL) that is TRUE for records matching the
    priority rule, plus its params — or None when priority serving isn't active.
    Year is parsed safely so non-numeric year_r values never raise a cast error."""
    if not cfg.get("enabled") or cfg.get("priority_outcome") not in _PRIORITY_OUTCOMES:
        return None
    clauses = ["COALESCE(u.final_outcome, u.outcome) = %s"]
    params: list = [cfg["priority_outcome"]]
    ymin, ymax = cfg.get("priority_year_min"), cfg.get("priority_year_max")
    if ymin is not None and ymax is not None:
        # year_r is stored inconsistently (e.g. '2020', '2020.0', ' 2020'); trim, then
        # take the leading 4 digits so every numeric format parses, and non-numeric
        # years just fall out as NULL (no cast error).
        clauses.append(
            "(CASE WHEN btrim(u.year_r) ~ '^[0-9]{4}' "
            "THEN substring(btrim(u.year_r) FROM '^[0-9]{4}')::int END) BETWEEN %s AND %s"
        )
        params += [ymin, ymax]
    return f"(({' AND '.join(clauses)}) IS TRUE)", params


def _select_pair_candidate(cur, coder_id: int, mode: str, extra_where: str = "", extra_params: tuple = ()):
    """Pick one servable record for this validator, optionally narrowed by extra_where."""
    cur.execute(
        f"""
        SELECT {_PAIR_SELECT}
        FROM unvalidated u
        LEFT JOIN record_metadata rm ON rm.record_id = u.record_id
        WHERE u.validation_status IN ('unvalidated', 'validation_inprogress')
          AND u.restricted_access IS NOT TRUE
          AND {_mode_sql(mode)}
          {extra_where}
          AND u.record_id NOT IN (
              SELECT record_id FROM validation_queue WHERE validator_id = %s
          )
          AND EXISTS (
              SELECT 1 FROM validation_queue vq
              WHERE vq.record_id = u.record_id
                AND vq.validator_slot IN ('human_1', 'human_2')
                AND vq.validator_id IS NULL
          )
        ORDER BY judge_count DESC, RANDOM()
        LIMIT 1
        """,
        (*extra_params, coder_id),
    )
    return cur.fetchone()


def _claim_one_pair(cur, coder_id: int, started: bool, mode: str = "normal"):
    """Claim one free human slot for this validator, within the given mode's pool.
       started=True  → active pair (5-day lock, started_at set)
       started=False → buffered prefetch (short lock, started_at NULL)
    Returns an enriched pair dict (with queue_id + judge_count) or None.

    When priority serving is enabled, each claim draws from the priority pool with
    probability = priority_share, else from the rest — falling back to the other
    pool if the chosen one is empty, so a validator is never blocked."""
    cfg = _serving_config(cur)
    pred = _priority_predicate(cfg)
    if pred:
        sql, prm = pred
        prm = tuple(prm)
        if random.random() < (cfg["priority_share"] / 100.0):
            attempts = [(f"AND {sql}", prm), (f"AND NOT {sql}", prm)]
        else:
            attempts = [(f"AND NOT {sql}", prm), (f"AND {sql}", prm)]
    else:
        attempts = []
    attempts.append(("", ()))   # final fallback: the whole pool

    row = None
    for extra_where, extra_params in attempts:
        row = _select_pair_candidate(cur, coder_id, mode, extra_where, extra_params)
        if row:
            break
    if not row:
        return None
    record_id = row["record_id"]
    started_sql = "NOW()" if started else "NULL"
    cur.execute(
        f"""
        UPDATE validation_queue
        SET is_shown = TRUE, validator_id = %s, validator_name = (
            SELECT handle FROM validators WHERE id = %s
        ), shown_at = NOW(), started_at = {started_sql}
        WHERE queue_id = (
            SELECT queue_id FROM validation_queue
            WHERE record_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND validator_id IS NULL
            ORDER BY validator_slot
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING queue_id
        """,
        (coder_id, coder_id, record_id),
    )
    claimed = cur.fetchone()
    if not claimed:
        return None  # lost a race — caller can try again
    cur.execute(
        "UPDATE unvalidated SET validation_status = 'validation_inprogress' "
        "WHERE record_id = %s AND validation_status = 'unvalidated'",
        (record_id,),
    )
    pair = _enrich_pair(dict(row), cur)
    pair["queue_id"]    = str(claimed["queue_id"])
    pair["judge_count"] = row["judge_count"]
    return pair


# POST, not GET: this claims queue rows. A state-changing GET can be driven
# by any page that can make the browser fetch a URL, and is fair game for
# prefetchers and caches besides.
@app.post("/api/next-pairs")
def next_pairs(count: int = 3, buffered_only: bool = False,
               mode: str = "normal",
               validator: dict = Depends(current_validator)):
    """Batch-claim pairs for the client prefetch buffer, within a serving mode.
       Default: first pair is the active (started) one — a resumed pair if the
       validator already has one *in this mode*, else a freshly started claim —
       and the rest are buffered. buffered_only=True returns only buffered top-ups."""
    coder_id = validator["coder_id"]
    if mode not in {"normal", "hard"}:
        raise HTTPException(400, "mode must be normal or hard")
    count = max(1, min(count, 5))
    out = []
    with db() as cur:
        if not buffered_only:
            # Resume an already-active pair, but only if it belongs to the
            # requested mode's pool (so switching modes doesn't drag the parked
            # pair from the other mode back in — it stays locked & resumable).
            cur.execute(
                f"""
                SELECT vq.queue_id, vq.record_id
                FROM validation_queue vq
                JOIN unvalidated u ON u.record_id = vq.record_id
                WHERE vq.validator_id = %s AND vq.is_shown = TRUE AND vq.is_validated = FALSE
                  AND vq.started_at IS NOT NULL
                  AND vq.validator_slot IN ('human_1', 'human_2')
                  AND {_mode_sql(mode)}
                LIMIT 1
                """,
                (coder_id,),
            )
            resume = cur.fetchone()
            if resume:
                cur.execute(
                    "UPDATE validation_queue SET shown_at = NOW() WHERE queue_id = %s",
                    (resume["queue_id"],),
                )
                row = _fetch_pair_row(cur, resume["record_id"])
                if row:
                    pair = _enrich_pair(dict(row), cur)
                    pair["queue_id"]    = str(resume["queue_id"])
                    pair["judge_count"] = row["judge_count"]
                    pair["started"]     = True
                    pair["resumed"]     = True
                    out.append(pair)
            if not out:
                active = _claim_one_pair(cur, coder_id, started=True, mode=mode)
                if active:
                    active["started"] = True
                    active["resumed"] = False
                    out.append(active)

        # Top up the rest as buffered prefetch.
        while len(out) < count:
            buf = _claim_one_pair(cur, coder_id, started=False, mode=mode)
            if not buf:
                break
            buf["started"] = False
            buf["resumed"] = False
            out.append(buf)

        cur.execute(
            """
            SELECT COUNT(*) AS done FROM validation_queue
            WHERE validator_id = %s AND is_validated = TRUE
              AND validator_slot IN ('human_1', 'human_2')
            """,
            (coder_id,),
        )
        done = cur.fetchone()["done"]
        cur.execute(
            "SELECT COUNT(*) AS total FROM unvalidated WHERE validation_status NOT IN ('validated', 'rejected')"
        )
        total = cur.fetchone()["total"]

    return {"pairs": out, "done": done, "total": total}


@app.post("/api/pairs/{queue_id}/start")
def start_pair(queue_id: str,
               validator: dict = Depends(current_validator)):
    """Promote a buffered slot to the active 'started' pair (5-day lock)."""
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            """
            UPDATE validation_queue
            SET started_at = NOW(), shown_at = NOW()
            WHERE queue_id = %s AND validator_id = %s AND is_validated = FALSE
            RETURNING queue_id
            """,
            (queue_id, coder_id),
        )
        if not cur.fetchone():
            raise HTTPException(404, "This pair is no longer assigned to you")
    return {"ok": True}


# Public, unauthenticated: the dataset-size page is linked from the sign-in screen
# so anyone can see how the FLoRA collection is growing. Aggregate counts only —
# no identifiers, no references, no reviewer information.
@app.get("/api/flora/history")
def public_flora_history():
    with db() as cur:
        return flora_service.history(cur)


@app.get("/api/health")
def health():
    """Lightweight liveness check (no DB) used by the client keep-warm ping and
    any external uptime pinger to keep the instance from cold-starting."""
    return {"status": "ok"}


class RestrictedRequest(BaseModel):
    record_id: str


def _release_skipped_slot(cur, record_id: str, validator_id: int):
    """Release one live human claim and return its stable queue identity."""
    cur.execute(
        """
        UPDATE validation_queue
        SET validator_id = NULL, validator_name = NULL,
            is_shown = FALSE, shown_at = NULL, started_at = NULL
        WHERE record_id = %s
          AND validator_id = %s
          AND validator_slot IN ('human_1', 'human_2')
          AND is_validated = FALSE
        RETURNING queue_id, validator_slot
        """,
        (record_id, validator_id),
    )
    return cur.fetchone()


def _restore_status_after_skip(cur, record_id: str) -> None:
    """Return an otherwise idle record to unvalidated after its claim is released."""
    cur.execute(
        """
        UPDATE unvalidated
        SET validation_status = 'unvalidated', updated_at = NOW()
        WHERE record_id = %s
          AND validation_status = 'validation_inprogress'
          AND NOT EXISTS (
              SELECT 1 FROM validation_queue
              WHERE record_id = %s
                AND (
                  (validator_id IS NOT NULL AND is_validated = FALSE)
                  OR is_validated = TRUE
                )
          )
        """,
        (record_id, record_id),
    )


def _insert_skip_event(
    cur,
    record_id: str,
    validator_id: int,
    queue_id,
    reason_code: str,
    comment: str | None,
) -> None:
    cur.execute(
        """
        INSERT INTO validation_skips
            (record_id, validator_id, queue_id, reason_code, comment)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (record_id, validator_id, queue_id, reason_code, comment),
    )


def _skip_summary(cur, record_id: str) -> dict:
    cur.execute(
        """
        SELECT COUNT(*)::int AS event_count,
               COUNT(DISTINCT validator_id)::int AS validator_count,
               COUNT(DISTINCT validator_id) FILTER (
                   WHERE reason_code IN ('eligibility_unclear', 'data_quality')
               )::int AS issue_validator_count
        FROM validation_skips
        WHERE record_id = %s
        """,
        (record_id,),
    )
    row = cur.fetchone()
    validator_count = row["validator_count"]
    issue_validator_count = row["issue_validator_count"]
    return {
        "event_count": row["event_count"],
        "validator_count": validator_count,
        "issue_validator_count": issue_validator_count,
        "review_required": (
            validator_count > SKIP_DISTINCT_VALIDATOR_THRESHOLD
            or issue_validator_count >= SKIP_ISSUE_VALIDATOR_THRESHOLD
        ),
    }


def _failure_message(detail) -> str:
    """Return a short, client-safe message from an HTTPException detail."""
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if message:
            return str(message)[:1000]
        return "The judgement was rejected by the server."
    if isinstance(detail, list):
        return "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                         for item in detail)[:1000]
    return str(detail or "The judgement could not be saved.")[:1000]


def _issue_submission_failure_stamp(
    req: JudgeRequest,
    coder_id: int,
    failure_code: str,
    failure_message: str,
) -> dict | None:
    """Issue a one-time release capability for this exact failed submission.

    A stamp is issued only while the requested validator still owns an unfinished
    human slot. Lock order matches judgement/skip: unvalidated, queue, then the
    failure audit row. Repeated server failures for one submission rotate the raw
    stamp, invalidating any older copy.
    """
    if req.submission_id is None:
        return None

    raw_stamp = secrets.token_urlsafe(32)
    stamp_hash = hashlib.sha256(raw_stamp.encode("utf-8")).hexdigest()
    submission_id = str(req.submission_id)
    safe_code = str(failure_code or "judge_error")[:100]
    safe_message = str(failure_message or "The judgement could not be saved.")[:1000]

    with db() as cur:
        cur.execute(
            "SELECT record_id FROM unvalidated WHERE record_id = %s FOR UPDATE",
            (req.record_id,),
        )
        record = cur.fetchone()
        if not record:
            return None

        cur.execute(
            """
            SELECT queue_id, record_id, validator_id
            FROM validation_queue
            WHERE record_id = %s
              AND validator_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND is_validated = FALSE
            ORDER BY validator_slot
            LIMIT 1
            FOR UPDATE
            """,
            (req.record_id, coder_id),
        )
        slot = cur.fetchone()
        if not slot:
            return None

        cur.execute(
            """
            SELECT failure_id, submission_id, queue_id, record_id, validator_id
            FROM submission_failure_releases
            WHERE submission_id = %s
            FOR UPDATE
            """,
            (submission_id,),
        )
        existing = cur.fetchone()

        if existing:
            same_owner = (
                str(existing["queue_id"]) == str(slot["queue_id"])
                and str(existing["record_id"]) == str(record["record_id"])
                and existing["validator_id"] == coder_id
            )
            if not same_owner:
                # A submission UUID is an immutable identity. Never let re-use
                # transfer a release capability to another record or validator.
                return None
            cur.execute(
                """
                UPDATE submission_failure_releases
                SET status = 'save_failed',
                    stamp_hash = %s,
                    failure_code = %s,
                    failure_message = %s,
                    failed_at = NOW(),
                    expires_at = NOW() + (%s * INTERVAL '1 minute'),
                    released_at = NULL
                WHERE failure_id = %s
                RETURNING failure_id, expires_at
                """,
                (
                    stamp_hash,
                    safe_code,
                    safe_message,
                    SUBMISSION_FAILURE_STAMP_TTL_MINUTES,
                    existing["failure_id"],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO submission_failure_releases
                    (submission_id, queue_id, record_id, validator_id,
                     status, stamp_hash, failure_code, failure_message, expires_at)
                VALUES (%s, %s, %s, %s, 'save_failed', %s, %s, %s,
                        NOW() + (%s * INTERVAL '1 minute'))
                RETURNING failure_id, expires_at
                """,
                (
                    submission_id,
                    slot["queue_id"],
                    record["record_id"],
                    coder_id,
                    stamp_hash,
                    safe_code,
                    safe_message,
                    SUBMISSION_FAILURE_STAMP_TTL_MINUTES,
                ),
            )

        issued = cur.fetchone()
        return {
            "failure_stamp": raw_stamp,
            "failure_id": str(issued["failure_id"]),
            "submission_id": submission_id,
            "release_status": "save_failed",
            "expires_at": issued["expires_at"].isoformat(),
        }


def _try_issue_submission_failure_stamp(
    req: JudgeRequest,
    coder_id: int,
    failure_code: str,
    failure_message: str,
) -> dict | None:
    """Do not replace the original save error if capability persistence fails."""
    try:
        return _issue_submission_failure_stamp(
            req, coder_id, failure_code, failure_message
        )
    except Exception:
        logger.exception("Could not persist a judgement submission-failure stamp")
        return None


def _housekeeping() -> None:
    """Drop dead sessions, links and attempt rows so the tables stay small."""
    try:
        with db() as cur:
            sessions.delete_expired(cur)
            auth_links.expire_elapsed(cur)
            cur.execute(
                "DELETE FROM login_attempts "
                "WHERE attempted_at < NOW() - INTERVAL '7 days'"
            )
            security_events.prune(cur)
            # A job left queued or running by a pod that died would block every
            # later click, since queue_run() treats it as active.
            abandoned = source_sync_runner.reap_stale_jobs(cur)
            if abandoned:
                logger.warning("Reaped %s abandoned sync job(s)", abandoned)
    except Exception:
        logger.exception("Session housekeeping failed")


def _close_failure_row_after_retry(cur, submission_id) -> int:
    """Mark an open failure row as resolved because its retry just committed.

    Called on the judge success path, inside the same transaction as the
    judgement: if the judgement rolls back so does this, and the row correctly
    stays open. Nothing else in the system can know this — the browser simply
    drops the queued item and never tells the server — so without it the row
    survived until the reaper marked it 'expired', which reads as "the validator
    never recovered their work" when the opposite happened.
    """
    if submission_id is None:
        return 0
    cur.execute(
        """
        UPDATE submission_failure_releases
        SET status = 'saved_after_retry', released_at = NOW()
        WHERE submission_id = %s AND status = 'save_failed'
        """,
        (str(submission_id),),
    )
    return cur.rowcount


def _expire_submission_failure_stamps(cur) -> int:
    """Materialise elapsed capability TTLs as an explicit audit state."""
    cur.execute(
        """
        UPDATE submission_failure_releases
        SET status = 'expired'
        WHERE status = 'save_failed' AND expires_at <= NOW()
        """
    )
    return cur.rowcount


def _server_controlled_submission_failure(handler):
    """Decorate /judge so only a server-observed failure can mint a stamp.

    A release capability is an authorisation to throw a validator's completed
    judgement away and hand the record to someone else. It must therefore mean
    exactly one thing: the server tried to persist this judgement and could
    not. A deliberate 4xx is the opposite — a decision about the request, taken
    with the transaction rolled back and nothing written — and the browser
    treats every 4xx as terminal and spends whatever stamp it is handed. Minting
    one for an ordinary "type_check must be 'correct' or 'incorrect'" would let
    a correctable client bug silently discard real work.

    Slot-gone responses (409 "already submitted", 400 "Already judged this
    record") need no capability either: the browser recognises them and closes
    the pending item on its own, because the server has already released it.
    """
    @wraps(handler)
    def guarded(req: JudgeRequest, *args, **kwargs):
        # The session dependency has already resolved by the time the wrapped
        # handler is called, so the stamp is bound to the server's identity for
        # this caller rather than to anything the request claimed.
        validator = kwargs.get("validator") or {}
        coder_id = validator.get("coder_id")
        try:
            return handler(req, *args, **kwargs)
        except HTTPException as exc:
            if exc.status_code < 500:
                raise
            # A deliberate 5xx is still a server-side failure, so it keeps the
            # recovery path that an unhandled exception gets below.
            message = _failure_message(exc.detail)
            original_code = (
                exc.detail.get("code")
                if isinstance(exc.detail, dict) and exc.detail.get("code")
                else f"judge_http_{exc.status_code}"
            )
            stamp = _try_issue_submission_failure_stamp(
                req, coder_id, original_code, message
            )
            if not stamp:
                raise
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": "judgement_save_failed",
                    "message": message,
                    **stamp,
                },
            ) from exc
        except Exception as exc:
            logger.exception("Judgement submission failed before commit")
            message = "The server could not save this judgement."
            stamp = _try_issue_submission_failure_stamp(
                req, coder_id, "internal_save_error", message
            )
            if not stamp:
                raise
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "judgement_save_failed",
                    "message": message,
                    **stamp,
                },
            ) from exc

    return guarded


@app.post("/api/restricted")
def report_restricted(req: RestrictedRequest,
                      validator: dict = Depends(current_validator)):
    """Hard-mode validator can't access the article → flag the record for the
    admin restricted-access queue and release their slot. No points awarded."""
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            "SELECT record_id FROM unvalidated WHERE record_id = %s FOR UPDATE",
            (req.record_id,),
        )
        if not cur.fetchone():
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        cur.execute(
            """
            UPDATE unvalidated
            SET restricted_access      = TRUE,
                restricted_reported_by = %s,
                restricted_reported_at = NOW()
            WHERE record_id = %s
            """,
            (coder_id, req.record_id),
        )
        # The hard-mode access report is also a skip event when it releases a
        # normal queue claim. Assignment-only reports retain their old behavior.
        released = _release_skipped_slot(cur, req.record_id, coder_id)
        summary = None
        if released:
            _insert_skip_event(
                cur, req.record_id, coder_id, released["queue_id"],
                "inaccessible", None,
            )
            _restore_status_after_skip(cur, req.record_id)
            cur.execute(
                "UPDATE validators SET skipped_count = skipped_count + 1 WHERE id = %s",
                (coder_id,),
            )
            summary = _skip_summary(cur, req.record_id)
    return {"ok": True, "skip_summary": summary}


# ---------------------------------------------------------------------------
# Assignments (restricted records handed to a validator with access)
# ---------------------------------------------------------------------------

@app.get("/api/my-assignments")
def my_assignments(validator: dict = Depends(current_validator)):
    """Open assignments for a validator (for the in-game Assignments panel)."""
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            """
            SELECT a.record_id, a.assigned_at, a.assigned_by,
                   u.study_r, u.title_r, u.doi_r, u.year_r, u.outcome
            FROM assignments a
            JOIN unvalidated u ON u.record_id = a.record_id
            WHERE a.validator_id = %s AND a.status = 'open'
              AND u.validation_status NOT IN ('validated', 'rejected')
            ORDER BY a.assigned_at DESC
            """,
            (coder_id,),
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d["record_id"] = str(d["record_id"])
            d["assigned_at"] = d["assigned_at"].isoformat() if d["assigned_at"] else None
            rows.append(d)
    return {"assignments": rows}


@app.get("/api/assignment/{record_id}")
def get_assignment(record_id: str,
                   validator: dict = Depends(current_validator)):
    """Fetch the full pair for an assignment (must be assigned to this validator)."""
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            "SELECT 1 FROM assignments WHERE record_id = %s AND validator_id = %s AND status = 'open'",
            (record_id, coder_id),
        )
        if not cur.fetchone():
            raise HTTPException(404, "This record is not assigned to you")
        row = _fetch_pair_row(cur, record_id)
        if not row:
            raise HTTPException(404, "Record not found")
        pair = _enrich_pair(dict(row), cur)
        pair["judge_count"] = row["judge_count"]
    return {"pair": pair}


@app.post("/api/assignment-judge")
def assignment_judge(req: JudgeRequest,
                     validator: dict = Depends(current_validator)):
    """Submit an assignment validation. A single trusted validator with access
    resolves a definite record directly (awaiting admin approval), while any
    explicit uncertainty goes to need_review. Assignments earn double points and
    clear the restricted flag when closed."""
    coder_id = validator["coder_id"]
    for chk in (req.type_check, req.original_check, req.outcome_check):
        if chk not in VALID_CHECKS:
            raise HTTPException(400, "checks must be 'correct' or 'incorrect'")

    corrected_title_r = _requested_title(req, "r")
    corrected_title_o = _requested_title(req, "o")

    with db() as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (req.record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        rec = dict(row)

        cur.execute(
            "SELECT id FROM assignments WHERE record_id = %s AND validator_id = %s "
            "AND status = 'open' FOR UPDATE",
            (req.record_id, coder_id),
        )
        assignment = cur.fetchone()
        if not assignment:
            raise HTTPException(404, "No open assignment for you on this record")
        assignment_id = assignment["id"]

        cur.execute(
            "SELECT id, handle, vote_score, total_points FROM validators WHERE id = %s",
            (coder_id,),
        )
        validator = cur.fetchone()
        if not validator:
            raise HTTPException(404, "Validator not found")

        base_type = rec.get("final_type") or rec["type"]
        base_outcome = rec.get("final_outcome") or rec.get("outcome")
        base_computation = (
            rec.get("final_outcome_computation")
            if rec.get("final_outcome_computation") is not None
            else rec.get("outcome_computation")
        )
        base_robustness = (
            rec.get("final_outcome_robustness")
            if rec.get("final_outcome_robustness") is not None
            else rec.get("outcome_robustness")
        )
        final_type, final_outcome, final_computation, final_robustness = _validated_outcome_request(
            req, base_type, base_outcome, base_computation, base_robustness
        )
        is_not_val = final_type == "not_validation"
        pts = _points_for(req, validator["vote_score"]) * 2   # assignments are double
        new_status = (
            "rejected" if is_not_val
            else "need_review" if _request_is_unsure(req)
            else "consensus_reached"
        )

        # Final values: validator's corrections, then any prior correction (final_*),
        # then the raw extracted value — never revert past an existing correction.
        final_computational_quote = (
            req.corrected_computational_quote
            if req.corrected_computational_quote is not None
            else rec.get("final_computational_quote")
            if rec.get("final_computational_quote") is not None
            else rec.get("outcome_computational_quote")
        )
        final_computational_source = (
            req.corrected_computational_source
            if req.corrected_computational_source is not None
            else rec.get("final_computational_source")
            if rec.get("final_computational_source") is not None
            else rec.get("out_quote_computational_source")
        )
        final_robustness_quote = (
            req.corrected_robustness_quote
            if req.corrected_robustness_quote is not None
            else rec.get("final_robustness_quote")
            if rec.get("final_robustness_quote") is not None
            else rec.get("outcome_robustness_quote")
        )
        final_robustness_source = (
            req.corrected_robustness_source
            if req.corrected_robustness_source is not None
            else rec.get("final_robustness_source")
            if rec.get("final_robustness_source") is not None
            else rec.get("out_quote_robust_source")
        )
        if final_type != "reproduction":
            final_computational_quote = None
            final_computational_source = None
            final_robustness_quote = None
            final_robustness_source = None
        final_title_r  = corrected_title_r             or rec.get("final_title_r")        or rec["title_r"]
        final_url_r    = req.corrected_url_r          or rec.get("final_url_r")          or rec["url_r"]
        final_abstract = req.corrected_abstract       or rec.get("final_abstract_r")     or rec["abstract_r"]
        # doi_o has a legitimate blank state (see admin_resolve) — the assignment
        # flow currently never sends '' (a blank client-side input collapses to
        # null before submission), but a prior deliberate clear stored on the
        # record must still survive here rather than reverting via `or`.
        final_doi_o    = req.corrected_doi_o if req.corrected_doi_o is not None else (
            rec["final_doi_o"] if rec.get("final_doi_o") is not None else rec["doi_o"])
        final_title_o  = corrected_title_o             or rec.get("final_title_o")        or rec["title_o"]
        final_quote    = req.corrected_outcome_quote  or rec.get("final_outcome_quote")  or rec["outcome_quote"]
        final_doi_pub  = _normalize_doi(req.doi_r_published) or rec.get("doi_r_published")

        summary = {
            "validator_id":   coder_id,
            "validator_name": validator["handle"],
            "is_assignment":  True,
            "type_check":     req.type_check,
            "original_check": req.original_check,
            "outcome_check":  req.outcome_check,
            "corrected_doi_o": req.corrected_doi_o,
            "corrected_title_o": corrected_title_o,
            "corrected_outcome": req.corrected_outcome,
            "corrected_type": final_type if req.type_check == "incorrect" else None,
            "corrected_outcome_quote": req.corrected_outcome_quote,
            "corrected_abstract": req.corrected_abstract,
            # Reproduction axes. Captured here so an assignment-resolved
            # reproduction keeps its judgement; promoting them to final_* columns
            # rides along with the consensus work.
            "corrected_outcome_computation": final_computation,
            "corrected_computational_quote": req.corrected_computational_quote,
            "corrected_computational_source": req.corrected_computational_source,
            "corrected_outcome_robustness": final_robustness,
            "corrected_robustness_quote": req.corrected_robustness_quote,
            "corrected_robustness_source": req.corrected_robustness_source,
            "corrected_title_r": corrected_title_r,
            "corrected_url_r": req.corrected_url_r,
            "doi_r_published": _normalize_doi(req.doi_r_published),
            "additional_checks": req.additional_checks,
            "validator_notes": req.validator_notes or "",
            "points": pts,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }

        cur.execute(
            """
            UPDATE unvalidated SET
                validation_status   = %s,
                final_type          = %s,
                final_outcome       = %s,
                final_title_r       = %s,
                final_url_r         = %s,
                final_abstract_r    = %s,
                final_doi_o         = %s,
                final_title_o       = %s,
                final_outcome_quote = %s,
                final_outcome_computation  = %s,
                final_computational_quote  = %s,
                final_computational_source = %s,
                final_outcome_robustness   = %s,
                final_robustness_quote     = %s,
                final_robustness_source    = %s,
                doi_r_published     = %s,
                validator_1         = %s,
                restricted_access   = FALSE,
                updated_at          = NOW()
            WHERE record_id = %s
            """,
            (new_status, final_type, final_outcome, final_title_r, final_url_r,
             final_abstract, final_doi_o, final_title_o, final_quote,
             final_computation, final_computational_quote, final_computational_source,
             final_robustness, final_robustness_quote, final_robustness_source, final_doi_pub,
             json.dumps(summary), req.record_id),
        )
        cur.execute(
            "UPDATE assignments SET status = 'done', completed_at = NOW() "
            "WHERE id = %s AND status = 'open' RETURNING id",
            (assignment_id,),
        )
        if not cur.fetchone():
            raise HTTPException(409, "This assignment was already submitted")
        cur.execute(
            "UPDATE validators SET total_points = total_points + %s, "
            "total_judgements = total_judgements + 1 WHERE id = %s RETURNING total_points",
            (pts, coder_id),
        )
        new_total = cur.fetchone()["total_points"]
    return {"points_earned": pts, "total_points": new_total}


@app.post("/api/judge")
@_server_controlled_submission_failure
def judge(req: JudgeRequest,
          validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    if req.type_check not in VALID_CHECKS:
        raise HTTPException(400, "type_check must be 'correct' or 'incorrect'")
    if req.original_check not in VALID_CHECKS:
        raise HTTPException(400, "original_check must be 'correct' or 'incorrect'")
    if req.outcome_check not in VALID_CHECKS:
        raise HTTPException(400, "outcome_check must be 'correct' or 'incorrect'")

    corrected_title_r = _requested_title(req, "r")
    corrected_title_o = _requested_title(req, "o")

    with db() as cur:
        # Look up the record and the validator
        cur.execute(
            "SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE",
            (req.record_id,),
        )
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        record_id = rec["record_id"]
        rec = dict(rec)
        target_type, target_outcome, target_computation, target_robustness = _validated_outcome_request(
            req,
            rec.get("type"),
            rec.get("outcome"),
            rec.get("outcome_computation"),
            rec.get("outcome_robustness"),
        )
        corrected_outcome = (
            target_outcome
            if target_type == "replication"
            and (req.corrected_outcome or target_type != rec.get("type") or req.outcome_check == "incorrect")
            else None
        )

        cur.execute(
            "SELECT id, handle, vote_score, total_points, total_judgements, validator_tier FROM validators WHERE id = %s",
            (coder_id,),
        )
        validator = cur.fetchone()
        if not validator:
            raise HTTPException(404, "Validator not found")

        # Find the slot assigned to this validator
        cur.execute(
            """
            SELECT queue_id, validator_slot
            FROM validation_queue
            WHERE record_id = %s
              AND validator_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND is_validated = FALSE
            LIMIT 1
            FOR UPDATE
            """,
            (record_id, coder_id),
        )
        slot_row = cur.fetchone()
        if not slot_row:
            raise HTTPException(400, "No open slot found for this validator on this record")

        queue_id = slot_row["queue_id"]
        validator_slot = slot_row["validator_slot"]
        pts = _points_for(req, validator["vote_score"])
        # Hard-pool records (undeterminable outcome / no abstract) earn double.
        if _record_is_hard(cur, record_id):
            pts *= 2

        # Record the judgment in validation_queue
        try:
            cur.execute(
                """
                UPDATE validation_queue SET
                    is_validated = TRUE,
                    type_check = %s,
                    original_check = %s,
                    outcome_check = %s,
                    corrected_doi_o = %s,
                    corrected_title_o = %s,
                    corrected_outcome = %s,
                    corrected_type = %s,
                    corrected_outcome_quote = %s,
                    corrected_abstract = %s,
                    corrected_outcome_computation = %s,
                    corrected_computational_quote = %s,
                    corrected_computational_source = %s,
                    corrected_outcome_robustness = %s,
                    corrected_robustness_quote = %s,
                    corrected_robustness_source = %s,
                    corrected_title_r = %s,
                    corrected_url_r = %s,
                    doi_r_published = %s,
                    validator_notes = %s,
                    additional_checks = %s,
                    points = %s,
                    validated_at = NOW()
                WHERE queue_id = %s AND is_validated = FALSE
                RETURNING queue_id
                """,
                (
                    req.type_check,
                    req.original_check,
                    req.outcome_check,
                    req.corrected_doi_o,
                    corrected_title_o,
                    corrected_outcome,
                    target_type if req.type_check == "incorrect" else None,
                    req.corrected_outcome_quote,
                    req.corrected_abstract,
                    target_computation,
                    req.corrected_computational_quote,
                    req.corrected_computational_source,
                    target_robustness,
                    req.corrected_robustness_quote,
                    req.corrected_robustness_source,
                    corrected_title_r,
                    req.corrected_url_r,
                    _normalize_doi(req.doi_r_published),
                    req.validator_notes,
                    json.dumps(req.additional_checks) if req.additional_checks else None,
                    pts,
                    queue_id,
                ),
            )
            if not cur.fetchone():
                raise HTTPException(409, "This record was already submitted")
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(400, "Already judged this record")

        # Build JSONB summary for unvalidated
        summary = {
            "validator_id": coder_id,
            "validator_name": validator["handle"],
            "validator_tier": validator["validator_tier"],
            "vote_score": validator["vote_score"],
            "type_check": req.type_check,
            "original_check": req.original_check,
            "outcome_check": req.outcome_check,
            "corrected_doi_o": req.corrected_doi_o,
            "corrected_title_o": corrected_title_o,
            "corrected_outcome": corrected_outcome,
            "corrected_type": target_type if req.type_check == "incorrect" else None,
            "corrected_outcome_quote": req.corrected_outcome_quote,
            "corrected_abstract": req.corrected_abstract,
            "corrected_outcome_computation": target_computation,
            "corrected_computational_quote": req.corrected_computational_quote,
            "corrected_computational_source": req.corrected_computational_source,
            "corrected_outcome_robustness": target_robustness,
            "corrected_robustness_quote": req.corrected_robustness_quote,
            "corrected_robustness_source": req.corrected_robustness_source,
            "corrected_title_r": corrected_title_r,
            "corrected_url_r": req.corrected_url_r,
            "doi_r_published": _normalize_doi(req.doi_r_published),
            "additional_checks": req.additional_checks,
            "validator_notes": req.validator_notes or "",
            "points": pts,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }

        jsonb_col = "validator_1" if validator_slot == "human_1" else "validator_2"
        cur.execute(
            f"UPDATE unvalidated SET {jsonb_col} = %s WHERE record_id = %s",
            (json.dumps(summary), record_id),
        )

        # Update validator totals atomically
        cur.execute(
            """
            UPDATE validators
            SET total_points = total_points + %s, total_judgements = total_judgements + 1
            WHERE id = %s
            RETURNING total_points
            """,
            (pts, coder_id),
        )
        new_total = cur.fetchone()["total_points"]

        # Trigger consensus engine now that a slot is complete
        from consensus_engine import evaluate_consensus
        evaluate_consensus(cur, record_id)

        cur.execute("SELECT COUNT(*) + 1 AS rank FROM validators WHERE total_points > %s", (new_total,))
        rank = cur.fetchone()["rank"]

        # This submission may have failed earlier and been retried. Close that
        # row now, while we are the only party who knows the retry worked.
        _close_failure_row_after_retry(cur, req.submission_id)

        return {"points_earned": pts, "total_points": new_total, "rank": rank}


@app.post("/api/skip")
def skip_pair(req: SkipRequest,
              validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    comment = (req.comment or "").strip() or None
    if comment and len(comment) > 1000:
        raise HTTPException(422, "Skip comments must be 1000 characters or fewer")
    if req.reason_code in SKIP_COMMENT_REQUIRED_CODES and not comment:
        raise HTTPException(422, "Please add a short explanation for this skip reason")

    with db() as cur:
        # Serialize releases for this record. Without the row lock, two validators
        # can each see the other's active slot before clearing their own and leave
        # validation_status incorrectly stuck at validation_inprogress.
        cur.execute(
            "SELECT record_id FROM unvalidated WHERE record_id = %s FOR UPDATE",
            (req.record_id,),
        )
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        record_id = rec["record_id"]

        # Release and log in the same transaction. Retried/double-clicked requests
        # cannot manufacture history or inflate counts after the claim is gone.
        released = _release_skipped_slot(cur, record_id, coder_id)
        if not released:
            raise HTTPException(409, "This record is no longer assigned to you")

        _insert_skip_event(
            cur, record_id, coder_id, released["queue_id"],
            req.reason_code, comment,
        )

        # Access problems use the existing restricted queue instead of repeatedly
        # serving a paper that validators cannot open.
        if req.reason_code == "inaccessible":
            cur.execute(
                """
                UPDATE unvalidated
                SET restricted_access = TRUE,
                    restricted_reported_by = %s,
                    restricted_reported_at = NOW(),
                    updated_at = NOW()
                WHERE record_id = %s
                """,
                (coder_id, record_id),
            )

        _restore_status_after_skip(cur, record_id)

        cur.execute(
            "UPDATE validators SET skipped_count = skipped_count + 1 WHERE id = %s",
            (coder_id,),
        )
        summary = _skip_summary(cur, record_id)

    # reason_recorded tells a NEW frontend talking to THIS backend that the
    # reason and comment were actually stored. The previous release returned
    # only {"skipped": true}, so during a rolling deployment the page can tell
    # the difference and avoid promising a validator that context was saved by
    # a pod that never received it.
    return {"skipped": True, "reason_recorded": True, "skip_summary": summary}


@app.post("/api/submission-failures/release")
def release_failed_submission(req: SubmissionFailureReleaseRequest):
    """Consume a server-issued capability and release exactly its failed slot.

    This path is intentionally separate from /api/skip: it writes no skip event,
    does not increment skipped_count, and accepts no client-supplied identity.
    The random stamp is the scoped capability and is useful only once.
    """
    raw_stamp = req.failure_stamp.strip()
    if not 20 <= len(raw_stamp) <= 200:
        raise HTTPException(422, "Invalid submission-failure stamp")
    stamp_hash = hashlib.sha256(raw_stamp.encode("utf-8")).hexdigest()

    result = None
    stamp_expired = False
    with db() as cur:
        # Read identity without locking first, then acquire locks in the same
        # global order used by /judge and /skip: record -> queue -> audit row.
        cur.execute(
            """
            SELECT failure_id, record_id, queue_id, validator_id
            FROM submission_failure_releases
            WHERE stamp_hash = %s
            """,
            (stamp_hash,),
        )
        identity = cur.fetchone()
        if not identity:
            raise HTTPException(404, "Unknown submission-failure stamp")

        cur.execute(
            "SELECT record_id FROM unvalidated WHERE record_id = %s FOR UPDATE",
            (identity["record_id"],),
        )
        if not cur.fetchone():
            raise HTTPException(410, "The record for this failure no longer exists")

        cur.execute(
            """
            SELECT queue_id, record_id, validator_id, validator_slot, is_validated
            FROM validation_queue
            WHERE queue_id = %s
            FOR UPDATE
            """,
            (identity["queue_id"],),
        )
        slot = cur.fetchone()

        # Re-check the digest after taking the parent locks. A later failed retry
        # may have rotated the stamp while this request was waiting.
        cur.execute(
            """
            SELECT failure_id, submission_id, queue_id, record_id, validator_id,
                   status, expires_at, released_at
            FROM submission_failure_releases
            WHERE failure_id = %s AND stamp_hash = %s
            FOR UPDATE
            """,
            (identity["failure_id"], stamp_hash),
        )
        failure = cur.fetchone()
        if not failure:
            raise HTTPException(409, "This submission-failure stamp was replaced")

        if failure["status"] != "save_failed":
            result = {
                "released": failure["status"] == "released",
                "status": failure["status"],
                "already_consumed": True,
                "submission_id": str(failure["submission_id"]),
            }
            stamp_expired = failure["status"] == "expired"
        elif failure["expires_at"] <= datetime.now(timezone.utc):
            cur.execute(
                """
                UPDATE submission_failure_releases
                SET status = 'expired'
                WHERE failure_id = %s AND status = 'save_failed'
                """,
                (failure["failure_id"],),
            )
            result = {
                "released": False,
                "status": "expired",
                "submission_id": str(failure["submission_id"]),
            }
            stamp_expired = True
        else:
            slot_is_owned = (
                slot
                and str(slot["record_id"]) == str(failure["record_id"])
                and str(slot["queue_id"]) == str(failure["queue_id"])
                and slot["validator_id"] == failure["validator_id"]
                and slot["validator_slot"] in ("human_1", "human_2")
                and not slot["is_validated"]
            )

            if not slot_is_owned:
                # A lost response may mean /judge actually committed. Record that
                # the capability was consumed, but never clear a changed slot.
                cur.execute(
                    """
                    UPDATE submission_failure_releases
                    SET status = 'slot_closed', released_at = NOW()
                    WHERE failure_id = %s AND status = 'save_failed'
                    """,
                    (failure["failure_id"],),
                )
                result = {
                    "released": False,
                    "status": "slot_closed",
                    "submission_id": str(failure["submission_id"]),
                }
            else:
                cur.execute(
                    """
                    UPDATE validation_queue
                    SET validator_id = NULL, validator_name = NULL,
                        is_shown = FALSE, shown_at = NULL, started_at = NULL
                    WHERE queue_id = %s
                      AND record_id = %s
                      AND validator_id = %s
                      AND validator_slot IN ('human_1', 'human_2')
                      AND is_validated = FALSE
                    RETURNING queue_id
                    """,
                    (
                        failure["queue_id"],
                        failure["record_id"],
                        failure["validator_id"],
                    ),
                )
                if not cur.fetchone():
                    raise HTTPException(409, "The failed submission slot changed")

                _restore_status_after_skip(cur, str(failure["record_id"]))
                cur.execute(
                    """
                    UPDATE submission_failure_releases
                    SET status = 'released', released_at = NOW()
                    WHERE failure_id = %s AND status = 'save_failed'
                    """,
                    (failure["failure_id"],),
                )
                result = {
                    "released": True,
                    "status": "released",
                    "submission_id": str(failure["submission_id"]),
                }

    if stamp_expired:
        raise HTTPException(
            410,
            detail={
                "code": "submission_failure_stamp_expired",
                "message": "The automatic-release stamp expired; the judgement remains pending.",
            },
        )
    return result


@app.post("/api/senior-reject")
def senior_reject(req: SeniorRejectRequest,
                  validator: dict = Depends(current_validator)):
    """Senior validators (tier >= 2) can immediately reject a record as not a replication
    without waiting for a second validator or LLM."""
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            "SELECT id, handle, vote_score, total_points, total_judgements, validator_tier FROM validators WHERE id = %s",
            (coder_id,),
        )
        validator = cur.fetchone()
        if not validator:
            raise HTTPException(404, "Validator not found")
        if validator["validator_tier"] < 2:
            raise HTTPException(403, "Only senior validators (tier 2) can use this feature")

        # Match /judge and /skip lock ordering: record row first, queue row next.
        cur.execute(
            "SELECT record_id FROM unvalidated WHERE record_id = %s FOR UPDATE",
            (req.record_id,),
        )
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        record_id = rec["record_id"]

        cur.execute(
            """
            SELECT queue_id, validator_slot FROM validation_queue
            WHERE record_id = %s AND validator_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND is_validated = FALSE
            LIMIT 1
            """,
            (record_id, coder_id),
        )
        slot_row = cur.fetchone()
        if not slot_row:
            raise HTTPException(400, "No open slot found for this validator on this record")

        pts = validator["vote_score"]

        cur.execute(
            """
            UPDATE validation_queue SET
                is_validated      = TRUE,
                type_check        = 'incorrect',
                original_check    = 'incorrect',
                outcome_check     = 'incorrect',
                corrected_type    = 'not_validation',
                validator_notes   = %s,
                additional_checks = '{"senior_reject": true}'::jsonb,
                points            = %s,
                validated_at      = NOW()
            WHERE queue_id = %s
            """,
            (req.validator_notes, pts, slot_row["queue_id"]),
        )

        summary = json.dumps({
            "validator_id":   coder_id,
            "validator_name": validator["handle"],
            "vote_score":     validator["vote_score"],
            "type_check":     "incorrect",
            "original_check": "incorrect",
            "outcome_check":  "incorrect",
            "corrected_type": "not_validation",
            "validator_notes": req.validator_notes or "",
            "points":         pts,
            "senior_reject":  True,
            "validated_at":   datetime.now(timezone.utc).isoformat(),
        })
        jsonb_col = "validator_1" if slot_row["validator_slot"] == "human_1" else "validator_2"
        cur.execute(
            f"UPDATE unvalidated SET {jsonb_col} = %s, validation_status = 'rejected', updated_at = NOW() WHERE record_id = %s",
            (summary, record_id),
        )
        # Rejected → must not remain in the authoritative export table.
        cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))

        # Fill the partner's still-open human slot with the senior's own reject so
        # both rows carry the senior's credentials — the record shows two matching
        # judgements and no late submission can reopen it. An already-submitted
        # partner slot is left intact (its real judgement is preserved; the
        # consensus senior-reject guard keeps the record rejected either way).
        partner_slot = "human_2" if slot_row["validator_slot"] == "human_1" else "human_1"
        cur.execute(
            """
            UPDATE validation_queue SET
                validator_id      = %s,
                validator_name    = %s,
                is_shown          = TRUE,
                is_validated      = TRUE,
                type_check        = 'incorrect',
                original_check    = 'incorrect',
                outcome_check     = 'incorrect',
                corrected_type    = 'not_validation',
                validator_notes   = %s,
                additional_checks = '{"senior_reject": true}'::jsonb,
                points            = 0,
                validated_at      = NOW()
            WHERE record_id = %s AND validator_slot = %s AND is_validated = FALSE
            RETURNING queue_id
            """,
            (coder_id, validator["handle"], req.validator_notes, record_id, partner_slot),
        )
        if cur.fetchone():
            # We filled the partner slot — mirror the senior's summary into its
            # JSONB column too, so both validator_1/validator_2 are populated.
            partner_col = "validator_2" if partner_slot == "human_2" else "validator_1"
            cur.execute(
                f"UPDATE unvalidated SET {partner_col} = %s WHERE record_id = %s",
                (summary, record_id),
            )

        cur.execute(
            "UPDATE validators SET total_points = total_points + %s, total_judgements = total_judgements + 1 WHERE id = %s",
            (pts, coder_id),
        )

    return {"rejected": True, "points_earned": pts}


# ---------------------------------------------------------------------------
# Stats and leaderboard
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def stats(validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            """
            SELECT total_points AS points,
                   total_judgements AS done,
                   skipped_count AS skipped
            FROM validators WHERE id = %s
            """,
            (coder_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Validator not found")

        cur.execute(
            "SELECT COUNT(*) AS total FROM unvalidated WHERE validation_status NOT IN ('validated', 'rejected')"
        )
        total = cur.fetchone()["total"]

        cur.execute(
            "SELECT COUNT(*) + 1 AS rank FROM validators WHERE total_points > %s",
            (row["points"],),
        )
        rank = cur.fetchone()["rank"]

        return {
            "done": row["done"],
            "points": row["points"],
            "skipped": row["skipped"],
            "total": total,
            "rank": rank,
        }


@app.get("/api/leaderboard")
def leaderboard():
    with db() as cur:
        cur.execute(
            """
            SELECT handle AS name,
                   total_points AS points,
                   total_judgements AS pairs
            FROM validators
            ORDER BY total_points DESC, total_judgements DESC, handle ASC
            """
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Forgot handle
# ---------------------------------------------------------------------------

@app.post("/api/forgot-handle")
def forgot_handle(req: ForgotHandleRequest):
    if not RESEND_API_KEY:
        raise HTTPException(503, "Email service not configured")

    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")

    with db() as cur:
        cur.execute(
            "SELECT id, handle, forgot_requests_today, forgot_requests_date FROM validators WHERE email = %s",
            (email,),
        )
        validator = cur.fetchone()

        # Always return success — don't reveal whether email exists
        if not validator:
            return {"sent": True}

        from datetime import date
        today = date.today()
        last_date = validator["forgot_requests_date"]
        count = validator["forgot_requests_today"] if last_date == today else 0

        if count >= 2:
            return {"sent": True}  # silently drop — don't reveal email exists

        # Send email via Resend
        resend.api_key = RESEND_API_KEY
        tmpl = forgot_handle_email(validator["handle"])
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [email],
            "subject": tmpl["subject"],
            "html": tmpl["html"],
            "text": tmpl["text"],
        })

        cur.execute(
            """
            UPDATE validators
            SET forgot_requests_today = %s,
                forgot_requests_date  = %s
            WHERE id = %s
            """,
            (count + 1, today, validator["id"]),
        )

    return {"sent": True}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.get("/api/admin/stats")
def admin_stats(admin: dict = Depends(current_admin)):

    with db() as cur:
        cur.execute(
            """
            SELECT
                v.id,
                v.handle,
                v.email,
                v.validator_tier,
                v.total_judgements,
                v.total_points,
                v.created_at::date AS joined,
                v.last_login_at,
                COUNT(vq.queue_id)  AS timed_count,
                ROUND(AVG(
                    EXTRACT(EPOCH FROM (vq.validated_at - vq.shown_at)) / 60
                )::numeric, 1)     AS avg_min,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (vq.validated_at - vq.shown_at)) / 60
                )::numeric, 1)     AS median_min,
                ROUND(MIN(
                    EXTRACT(EPOCH FROM (vq.validated_at - vq.shown_at)) / 60
                )::numeric, 1)     AS min_min,
                ROUND(MAX(
                    EXTRACT(EPOCH FROM (vq.validated_at - vq.shown_at)) / 60
                )::numeric, 1)     AS max_min,
                (SELECT COUNT(*) FROM validation_queue fq
                 WHERE fq.validator_id = v.id AND fq.flagged = TRUE) AS flagged_count,
                (SELECT COUNT(DISTINCT aq.record_id)
                 FROM validation_queue aq
                 JOIN unvalidated au ON au.record_id = aq.record_id
                 WHERE aq.validator_id   = v.id
                   AND aq.is_validated   = TRUE
                   AND aq.validator_slot IN ('human_1', 'human_2')
                   AND au.validation_status = 'validated') AS approved_count
            FROM validators v
            LEFT JOIN validation_queue vq
                ON  vq.validator_id   = v.id
                AND vq.is_validated   = TRUE
                AND vq.validator_slot IN ('human_1', 'human_2')
                AND vq.shown_at       IS NOT NULL
                AND vq.validated_at   IS NOT NULL
                AND EXTRACT(EPOCH FROM (vq.validated_at - vq.shown_at)) BETWEEN 10 AND 5400
            GROUP BY v.id, v.handle, v.email, v.validator_tier, v.total_judgements, v.total_points, v.created_at, v.last_login_at
            ORDER BY v.validator_tier DESC, v.total_judgements DESC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if r["joined"]:
                r["joined"] = str(r["joined"])
            if r["last_login_at"]:
                r["last_login_at"] = r["last_login_at"].isoformat()

        # Overall summary
        cur.execute(
            """
            SELECT
                COUNT(*)  AS total_validators,
                SUM(total_judgements) AS total_judgements,
                (SELECT COUNT(*) FROM unvalidated WHERE validation_status = 'validated')  AS total_validated,
                (SELECT COUNT(*) FROM unvalidated WHERE validation_status = 'need_review') AS total_review
            FROM validators
            WHERE total_judgements > 0
            """
        )
        summary = dict(cur.fetchone())

    return {"validators": rows, "summary": summary}


def _confusion(pairs):
    """Build a confusion matrix {labels, grid} from (row_value, col_value) pairs.
    Rows and cols share the same label space (a square matrix)."""
    labels = sorted({str(x) for p in pairs for x in p if x not in (None, "")})
    idx = {l: i for i, l in enumerate(labels)}
    grid = [[0] * len(labels) for _ in labels]
    for a, b in pairs:
        a, b = (str(a) if a not in (None, "") else None), (str(b) if b not in (None, "") else None)
        if a in idx and b in idx:
            grid[idx[a]][idx[b]] += 1
    return {"labels": labels, "grid": grid}


def _as_dict(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return v or {}


@app.get("/api/admin/serving-config")
def get_serving_config(admin: dict = Depends(current_admin)):
    with db() as cur:
        cur.execute(
            "SELECT enabled, priority_outcome, priority_year_min, priority_year_max, "
            "priority_share, updated_by, updated_at FROM serving_config WHERE id = 1"
        )
        row = cur.fetchone()
    return dict(row) if row else {
        "enabled": False, "priority_outcome": None,
        "priority_year_min": None, "priority_year_max": None, "priority_share": 70,
    }


@app.put("/api/admin/serving-config")
def put_serving_config(req: ServingConfigRequest, admin: dict = Depends(current_admin)):
    admin_handle = admin["handle"]
    outcome = req.priority_outcome if req.priority_outcome in _PRIORITY_OUTCOMES else None
    share = max(0, min(100, req.priority_share))
    ymin, ymax = req.priority_year_min, req.priority_year_max
    if ymin is not None and ymax is not None and ymin > ymax:
        ymin, ymax = ymax, ymin
    with db() as cur:
        cur.execute(
            """
            INSERT INTO serving_config
                (id, enabled, priority_outcome, priority_year_min, priority_year_max,
                 priority_share, updated_by, updated_at)
            VALUES (1, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                enabled           = EXCLUDED.enabled,
                priority_outcome  = EXCLUDED.priority_outcome,
                priority_year_min = EXCLUDED.priority_year_min,
                priority_year_max = EXCLUDED.priority_year_max,
                priority_share    = EXCLUDED.priority_share,
                updated_by        = EXCLUDED.updated_by,
                updated_at        = NOW()
            """,
            (req.enabled, outcome, ymin, ymax, share, admin_handle),
        )
    return {"saved": True}


@app.get("/api/admin/serving-config/preview")
def preview_serving_config(outcome: str = "", year_min: int | None = None,
                           year_max: int | None = None, admin: dict = Depends(current_admin)):
    """Count how the proposed rule would split the currently-servable pool."""
    base = ("FROM unvalidated u WHERE u.validation_status IN ('unvalidated', 'validation_inprogress') "
            "AND u.restricted_access IS NOT TRUE")
    pred = _priority_predicate({
        "enabled": True, "priority_outcome": outcome or None,
        "priority_year_min": year_min, "priority_year_max": year_max, "priority_share": 70,
    })
    with db() as cur:
        cur.execute(f"SELECT COUNT(*) AS n {base}")
        total = cur.fetchone()["n"]
        match = 0
        if pred:
            sql, prm = pred
            cur.execute(f"SELECT COUNT(*) AS n {base} AND {sql}", tuple(prm))
            match = cur.fetchone()["n"]
    return {"pool_total": total, "priority_match": match, "rest": total - match}


@app.get("/api/admin/dashboard")
def admin_dashboard(admin: dict = Depends(current_admin)):

    with db() as cur:
        # Pipeline: status distribution + misc flags
        cur.execute("""
            SELECT
                COUNT(*)                                                              AS total,
                COUNT(*) FILTER (WHERE validation_status = 'unvalidated')            AS unvalidated,
                COUNT(*) FILTER (WHERE validation_status = 'validation_inprogress')  AS in_progress,
                COUNT(*) FILTER (WHERE validation_status = 'consensus_reached')      AS consensus_reached,
                COUNT(*) FILTER (WHERE validation_status = 'need_review')            AS need_review,
                COUNT(*) FILTER (WHERE validation_status = 'validated')              AS validated,
                COUNT(*) FILTER (WHERE validation_status = 'rejected')               AS rejected,
                COUNT(*) FILTER (WHERE is_tiebreaker = TRUE)                         AS tiebreakers,
                COUNT(*) FILTER (WHERE admin_override = TRUE)                        AS admin_overrides
            FROM unvalidated
        """)
        pipeline = dict(cur.fetchone())

        # Outcome distribution from validated table
        cur.execute("""
            SELECT outcome, COUNT(*) AS n
            FROM validated
            WHERE outcome IS NOT NULL
            GROUP BY outcome
        """)
        outcomes_raw = {r["outcome"]: int(r["n"]) for r in cur.fetchall()}
        outcomes = {
            "successful":    outcomes_raw.get("successful", 0),
            "failed":        outcomes_raw.get("failed", 0),
            "mixed":         outcomes_raw.get("mixed", 0),
            "uninformative": outcomes_raw.get("uninformative", 0),
            "descriptive only": outcomes_raw.get("descriptive only", 0),
        }

        # Correction counts per field across all human-validated queue entries
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE type_check     = 'incorrect')                AS type_corrections,
                COUNT(*) FILTER (WHERE original_check = 'incorrect')                AS original_corrections,
                COUNT(*) FILTER (WHERE outcome_check  = 'incorrect')                AS outcome_corrections,
                COUNT(*) FILTER (WHERE corrected_title_r IS NOT NULL
                                   AND corrected_title_r <> '')                     AS title_corrections
            FROM validation_queue
            WHERE is_validated = TRUE
              AND validator_slot IN ('human_1', 'human_2')
        """)
        corrections = dict(cur.fetchone())

        # Inter-validator agreement rate
        cur.execute("""
            SELECT
                COUNT(*) AS records_with_2,
                COUNT(*) FILTER (WHERE
                    q1.type_check     = q2.type_check     AND
                    q1.original_check = q2.original_check AND
                    q1.outcome_check  = q2.outcome_check
                ) AS full_agreements
            FROM (
                SELECT record_id, type_check, original_check, outcome_check
                FROM validation_queue
                WHERE validator_slot = 'human_1' AND is_validated = TRUE
            ) q1
            JOIN (
                SELECT record_id, type_check, original_check, outcome_check
                FROM validation_queue
                WHERE validator_slot = 'human_2' AND is_validated = TRUE
            ) q2 USING (record_id)
        """)
        agree_row = dict(cur.fetchone())

        # Active validators + total judgements
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE total_judgements > 0) AS active_validators,
                COALESCE(SUM(total_judgements), 0)           AS total_judgements
            FROM validators
        """)
        vrow = dict(cur.fetchone())

        # View A — Validator vs Validator (records with both human slots filled).
        cur.execute("""
            SELECT validation_status, type, outcome, validator_1, validator_2
            FROM unvalidated
            WHERE validator_1 IS NOT NULL AND validator_2 IS NOT NULL
        """)
        a_rows = cur.fetchall()

        # View B — Pipeline (extracted) vs Final, over validated records.
        cur.execute("""
            SELECT type, outcome, final_type, final_outcome, doi_o, final_doi_o
            FROM unvalidated
            WHERE validation_status = 'validated'
        """)
        b_rows = cur.fetchall()

    # ----- View A: each validator's effective decision, then disagreements -----
    def _choice(v, check_key, corrected_key, extracted):
        return v[corrected_key] if v.get(check_key) == "incorrect" and v.get(corrected_key) else extracted

    a_type, a_orig, a_out = [], [], []
    a_counts = {d: {"validated": 0, "unvalidated": 0} for d in ("type", "original", "outcome")}
    for r in a_rows:
        v1, v2 = _as_dict(r["validator_1"]), _as_dict(r["validator_2"])
        grp = "validated" if r["validation_status"] == "validated" else "unvalidated"
        t1, t2 = _choice(v1, "type_check", "corrected_type", r["type"]),    _choice(v2, "type_check", "corrected_type", r["type"])
        o1, o2 = _choice(v1, "outcome_check", "corrected_outcome", r["outcome"]), _choice(v2, "outcome_check", "corrected_outcome", r["outcome"])
        g1, g2 = v1.get("original_check"), v2.get("original_check")
        a_type.append((t1, t2)); a_orig.append((g1, g2)); a_out.append((o1, o2))
        if t1 != t2: a_counts["type"]["validated" if grp == "validated" else "unvalidated"] += 1
        if g1 != g2: a_counts["original"]["validated" if grp == "validated" else "unvalidated"] += 1
        if o1 != o2: a_counts["outcome"]["validated" if grp == "validated" else "unvalidated"] += 1

    # ----- View B: extracted vs final -----
    b_type, b_out = [], []
    b_counts = {"type": 0, "outcome": 0, "original": 0}
    for r in b_rows:
        et, ft = r["type"],    (r["final_type"]    or r["type"])
        eo, fo = r["outcome"], (r["final_outcome"] or r["outcome"])
        b_type.append((et, ft)); b_out.append((eo, fo))
        if et != ft: b_counts["type"] += 1
        if eo != fo: b_counts["outcome"] += 1
        if r["final_doi_o"] is not None and r["final_doi_o"] != r["doi_o"]: b_counts["original"] += 1

    disagreements = {
        "validator": {
            "total_records": len(a_rows),
            "type":     {**a_counts["type"],     "matrix": _confusion(a_type)},
            "original": {**a_counts["original"], "matrix": _confusion(a_orig)},
            "outcome":  {**a_counts["outcome"],  "matrix": _confusion(a_out)},
        },
        "pipeline": {
            "total_validated": len(b_rows),
            "type":     {"count": b_counts["type"],     "matrix": _confusion(b_type)},
            "outcome":  {"count": b_counts["outcome"],  "matrix": _confusion(b_out)},
            "original": {"count": b_counts["original"]},
        },
    }

    records_with_2  = int(agree_row["records_with_2"]  or 0)
    full_agreements = int(agree_row["full_agreements"] or 0)
    agreement_rate  = round(full_agreements / records_with_2, 3) if records_with_2 > 0 else None

    return {
        "pipeline": {k: int(v) for k, v in pipeline.items()},
        "outcomes":    outcomes,
        "corrections": {k: int(v or 0) for k, v in corrections.items()},
        "quality": {
            "total_judgements":        int(vrow["total_judgements"]),
            "active_validators":       int(vrow["active_validators"]),
            "records_with_2_validators": records_with_2,
            "full_agreements":         full_agreements,
            "agreement_rate":          agreement_rate,
        },
        "disagreements": disagreements,
    }


# ---------------------------------------------------------------------------
# Site banner (public read, admin write)
# ---------------------------------------------------------------------------

@app.get("/api/banner")
def get_site_banner():
    """Public — returns the active admin broadcast banner, if any."""
    with db() as cur:
        cur.execute("SELECT message, active FROM site_banner WHERE id = 1")
        row = cur.fetchone()
    if row and row["active"] and row["message"]:
        return {"active": True, "message": str(row["message"])}
    return {"active": False, "message": None}


class BannerRequest(BaseModel):
    message: str
    active: bool = True


@app.post("/api/admin/banner")
def set_site_banner(req: BannerRequest, admin: dict = Depends(current_admin)):
    handle = admin["handle"]
    msg = req.message.strip() if req.message else None
    with db() as cur:
        cur.execute(
            """
            INSERT INTO site_banner (id, message, active, updated_by, updated_at)
            VALUES (1, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                message    = EXCLUDED.message,
                active     = EXCLUDED.active,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            """,
            (msg, req.active, handle),
        )
    return {"ok": True}


class SetTierRequest(BaseModel):
    tier: int


@app.get("/api/admin/validators")
def admin_list_validators(admin: dict = Depends(current_admin)):
    with db() as cur:
        cur.execute("SELECT id, handle, email FROM validators ORDER BY handle")
        rows = [dict(r) for r in cur.fetchall()]
    return {"validators": rows}


@app.get("/api/admin/restricted")
def admin_restricted(admin: dict = Depends(current_admin)):
    """Records flagged 'I cannot access this article', with reporter + current
    assignment (if any), for the admin Restricted-access queue."""
    with db() as cur:
        cur.execute(
            """
            SELECT u.record_id, u.study_r, u.title_r, u.doi_r, u.year_r, u.outcome,
                   u.restricted_reported_at,
                   rv.handle AS reporter_handle,
                   a.validator_id AS assignee_id,
                   av.handle      AS assignee_handle,
                   a.status       AS assignment_status
            FROM unvalidated u
            LEFT JOIN validators  rv ON rv.id = u.restricted_reported_by
            LEFT JOIN assignments a  ON a.record_id = u.record_id
            LEFT JOIN validators  av ON av.id = a.validator_id
            WHERE u.restricted_access = TRUE
              AND u.validation_status NOT IN ('validated', 'rejected')
            ORDER BY u.restricted_reported_at DESC NULLS LAST
            """
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d["record_id"] = str(d["record_id"])
            d["restricted_reported_at"] = d["restricted_reported_at"].isoformat() if d["restricted_reported_at"] else None
            rows.append(d)
    return {"records": rows}


class AssignRequest(BaseModel):
    record_id: str
    validator_id: int


@app.post("/api/admin/assign")
def admin_assign(req: AssignRequest, request: Request,
                 admin: dict = Depends(current_admin)):
    """Assign a restricted record to a validator (reassign replaces)."""
    admin_handle = admin["handle"]
    with db() as cur:
        cur.execute("SELECT 1 FROM unvalidated WHERE record_id = %s", (req.record_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Record not found")
        cur.execute("SELECT 1 FROM validators WHERE id = %s", (req.validator_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Validator not found")
        cur.execute(
            """
            INSERT INTO assignments (record_id, validator_id, assigned_by, status, assigned_at, completed_at)
            VALUES (%s, %s, %s, 'open', NOW(), NULL)
            ON CONFLICT (record_id) DO UPDATE
              SET validator_id = EXCLUDED.validator_id,
                  assigned_by  = EXCLUDED.assigned_by,
                  status       = 'open',
                  assigned_at  = NOW(),
                  completed_at = NULL
            """,
            (req.record_id, req.validator_id, admin_handle),
        )
        _audit(cur, security_events.VALIDATOR_ASSIGNED, request, actor=admin,
               target_kind="validator", target_id=req.validator_id,
               detail={"record_id": str(req.record_id)})
    return {"ok": True}


@app.get("/api/admin/validators/{validator_id}/flagged")
def admin_validator_flagged(validator_id: int, admin: dict = Depends(current_admin)):
    with db() as cur:
        cur.execute("SELECT id, handle FROM validators WHERE id = %s", (validator_id,))
        v = cur.fetchone()
        if not v:
            raise HTTPException(404, "Validator not found")
        cur.execute(
            """
            SELECT
                vq.queue_id,
                vq.record_id::text,
                vq.flag_reason,
                vq.validated_at,
                u.study_r,
                u.title_r,
                u.doi_r,
                u.year_r,
                u.outcome,
                u.validation_status
            FROM validation_queue vq
            JOIN unvalidated u ON u.record_id = vq.record_id
            WHERE vq.validator_id = %s AND vq.flagged = TRUE
            ORDER BY vq.validated_at DESC NULLS LAST
            """,
            (validator_id,),
        )
        rows = cur.fetchall()
    items = []
    for r in rows:
        d = dict(r)
        if d["validated_at"]:
            d["validated_at"] = d["validated_at"].isoformat()
        items.append(d)
    return {"handle": v["handle"], "items": items}


@app.post("/api/admin/validators/{validator_id}/set-tier")
def admin_set_tier(validator_id: int, req: SetTierRequest, request: Request,
                   admin: dict = Depends(current_admin)):
    if req.tier not in (0, 1, 2):
        raise HTTPException(400, "tier must be 0, 1, or 2")
    with db() as cur:
        cur.execute(
            "UPDATE validators SET validator_tier = %s WHERE id = %s RETURNING handle, validator_tier",
            (req.tier, validator_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Validator not found")
        _audit(cur, security_events.VALIDATOR_TIER_CHANGED, request, actor=admin,
               target_kind="validator", target_id=validator_id,
               target_label=row["handle"], detail={"tier": row["validator_tier"]})
    return {"handle": row["handle"], "validator_tier": row["validator_tier"]}


@app.get("/api/admin/security-events")
def admin_security_events(
    action: str | None = None,
    days: int = 30,
    limit: int = 200,
    admin: dict = Depends(current_admin),
):
    """Recent privileged actions, newest first.

    Read-only on purpose: nothing in the application edits or deletes an event
    except the retention prune, so an admin cannot tidy away their own trail
    through the API.
    """
    days = max(1, min(int(days), security_events.RETENTION_DAYS))
    limit = max(1, min(int(limit), 1000))
    clauses = ["occurred_at > NOW() - (%s * INTERVAL '1 day')"]
    params: list = [days]
    if action:
        if action not in security_events.ACTIONS:
            raise HTTPException(400, "Unknown action filter")
        clauses.append("action = %s")
        params.append(action)
    params.append(limit)
    with db() as cur:
        cur.execute(
            f"""
            SELECT event_id, occurred_at, action, actor_kind, actor_handle,
                   target_kind, target_id, target_label, client_ip, detail
            FROM security_events
            WHERE {' AND '.join(clauses)}
            ORDER BY occurred_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {
        "events": rows,
        "days": days,
        "retention_days": security_events.RETENTION_DAYS,
        "actions": sorted(security_events.ACTIONS),
    }


@app.get("/api/admin/admins")
def list_admins(admin: dict = Depends(current_admin)):
    with db() as cur:
        cur.execute("SELECT id, handle, trusted, created_at::date AS joined FROM admins ORDER BY id")
        return {"admins": [dict(r) for r in cur.fetchall()]}


class AdminCreateRequest(BaseModel):
    handle: str
    # The inviting admin never chooses the new admin's password: an invitation
    # link is emailed and the recipient sets it themselves, so the credential
    # exists only in that person's head.
    email: str


class AuthLinkRedeemRequest(BaseModel):
    token: str = Field(max_length=256)
    password: str = Field(max_length=1024)


def _send_auth_link(handle: str, email: str, raw_token: str, purpose: str,
                    inviter: str | None) -> bool:
    """Mail the link. Returns False when email is not configured or fails."""
    if not RESEND_API_KEY:
        return False
    url = auth_links.build_url(raw_token, purpose)
    hours = auth_links.ttl_hours(purpose)
    template = (
        admin_invite_email(handle, url, hours, inviter)
        if purpose == auth_links.PURPOSE_INVITE
        else admin_reset_email(handle, url, hours)
    )
    try:
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [email],
            "subject": template["subject"],
            "html": template["html"],
            "text": template["text"],
        })
        return True
    except Exception:
        # The link is already committed; report that it was not delivered so the
        # caller can hand it over another way rather than silently stranding the
        # invitee.
        logger.exception("Could not email a %s link to %s", purpose, handle)
        return False


@app.post("/api/admin/admins")
def create_admin(req: AdminCreateRequest, request: Request,
                 admin: dict = Depends(current_admin)):
    if not admin["trusted"]:
        raise HTTPException(403, "Only trusted admins can manage admin accounts")
    inviter = admin["handle"]
    handle = (req.handle or "").strip()
    email = (req.email or "").strip().lower()
    if not handle or not email:
        raise HTTPException(400, "Handle and email are required")
    if not HANDLE_RE.match(handle):
        raise HTTPException(400, "Handle must be 2-32 chars: letters, digits, . _ -")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")

    with db() as cur:
        try:
            # password_hash stays NULL: the account exists but cannot be signed
            # into until the invitee redeems their link and sets one.
            cur.execute(
                "INSERT INTO admins (handle, email, password_hash) "
                "VALUES (%s, %s, NULL) RETURNING id, handle",
                (handle, email),
            )
            row = cur.fetchone()
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(409, "That handle or email is already registered")
        raw_token = auth_links.issue_for_admin(
            cur, row["id"], email, auth_links.PURPOSE_INVITE, inviter
        )
        _audit(cur, security_events.ADMIN_INVITED, request, actor=admin,
               target_kind="admin", target_id=row["id"],
               target_label=row["handle"], detail={"email": email})

    emailed = _send_auth_link(
        row["handle"], email, raw_token, auth_links.PURPOSE_INVITE, inviter
    )
    response = {
        "id": row["id"],
        "handle": row["handle"],
        "email": email,
        "invite_emailed": emailed,
        "expires_in_hours": auth_links.ttl_hours(auth_links.PURPOSE_INVITE),
    }
    if not emailed:
        # Only ever returned to the trusted admin who just created the account,
        # and only when we could not deliver it. Without this the invitee is
        # stranded whenever RESEND_API_KEY is unset.
        response["invite_url"] = auth_links.build_url(
            raw_token, auth_links.PURPOSE_INVITE
        )
        response["warning"] = (
            "Email could not be sent. Give this single-use link to the new "
            "administrator yourself; it is not shown again."
        )
    return response


@app.post("/api/admin/invite/resend")
def resend_admin_invite(request: Request,
                        admin_id: int = Body(..., embed=True),
                        admin: dict = Depends(current_admin)):
    """Issue a fresh invitation, replacing any outstanding one."""
    if not admin["trusted"]:
        raise HTTPException(403, "Only trusted admins can manage admin accounts")
    inviter = admin["handle"]
    with db() as cur:
        cur.execute(
            "SELECT id, handle, email, password_hash FROM admins WHERE id = %s",
            (admin_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Administrator not found")
        if not row["email"]:
            raise HTTPException(400, "That administrator has no email address on file")
        purpose = (
            auth_links.PURPOSE_INVITE if not row["password_hash"]
            else auth_links.PURPOSE_RESET
        )
        raw_token = auth_links.issue_for_admin(
            cur, row["id"], row["email"], purpose, inviter
        )
        _audit(cur, security_events.ADMIN_INVITE_RESENT, request, actor=admin,
               target_kind="admin", target_id=row["id"],
               target_label=row["handle"], detail={"purpose": purpose})

    emailed = _send_auth_link(
        row["handle"], row["email"], raw_token, purpose, inviter
    )
    result = {
        "handle": row["handle"],
        "purpose": purpose,
        "emailed": emailed,
        "expires_in_hours": auth_links.ttl_hours(purpose),
    }
    if not emailed:
        result["link_url"] = auth_links.build_url(raw_token, purpose)
    return result


@app.get("/api/admin/auth-link/{token}")
def describe_auth_link(token: str):
    """Report whether a link is live, so the page can name the account.

    Public by necessity — the recipient has no session yet. It reveals only the
    handle the token already belongs to, and only while the token is valid.
    """
    with db() as cur:
        auth_links.expire_elapsed(cur)
        cur.execute(
            """
            SELECT l.purpose, l.status, a.handle
            FROM auth_links l JOIN admins a ON a.id = l.subject_id
            WHERE l.token_hash = %s AND l.subject_kind = 'admin'
            """,
            (auth_links.token_digest(token),),
        )
        row = cur.fetchone()
    if not row or row["status"] != "pending":
        raise HTTPException(404, "This link is no longer valid")
    return {
        "handle": row["handle"],
        "purpose": row["purpose"],
        "min_password_length": auth_links.MIN_PASSWORD_LENGTH,
    }


@app.post("/api/admin/auth-link/redeem")
def redeem_auth_link(req: AuthLinkRedeemRequest, request: Request):
    """Spend a one-time link to set an administrator password."""
    rejection = auth_links.password_rejection_reason(req.password)
    if rejection:
        raise HTTPException(422, rejection)

    with db() as cur:
        auth_links.expire_elapsed(cur)
        # Claim the row and mark it spent in the same statement: two redemptions
        # racing cannot both pass a check-then-update.
        cur.execute(
            """
            UPDATE auth_links
            SET status = 'used', used_at = NOW()
            WHERE token_hash = %s AND subject_kind = 'admin' AND status = 'pending'
            RETURNING subject_id, purpose
            """,
            (auth_links.token_digest(req.token),),
        )
        link = cur.fetchone()
        if not link:
            raise HTTPException(404, "This link is no longer valid")
        cur.execute(
            "UPDATE admins SET password_hash = %s WHERE id = %s RETURNING handle",
            (hash_password(req.password), link["subject_id"]),
        )
        admin = cur.fetchone()
        if not admin:
            raise HTTPException(404, "This link is no longer valid")
        # A new password must end every session opened with the old one. That
        # immediacy is the point of a session store; a derived token could only
        # ever be waited out.
        revoked = sessions.revoke_all_for(
            cur, sessions.KIND_ADMIN, link["subject_id"]
        )
        _audit(cur, security_events.ADMIN_PASSWORD_SET, request,
               actor={"id": link["subject_id"], "handle": admin["handle"]},
               target_kind="admin", target_id=link["subject_id"],
               target_label=admin["handle"],
               detail={"purpose": link["purpose"], "sessions_revoked": revoked})
    logger.warning(
        "Administrator %r set a password via a %s link; %d session(s) revoked",
        admin["handle"], link["purpose"], revoked,
    )
    return {"handle": admin["handle"], "purpose": link["purpose"]}


@app.delete("/api/admin/admins/{admin_id}")
def delete_admin(admin_id: int, request: Request,
                 admin: dict = Depends(current_admin)):
    if not admin["trusted"]:
        raise HTTPException(403, "Only trusted admins can manage admin accounts")
    calling_handle = admin["handle"]
    with db() as cur:
        cur.execute("SELECT handle FROM admins WHERE id = %s", (admin_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Admin not found")
        if row["handle"] == calling_handle:
            raise HTTPException(400, "Cannot delete your own account")
        cur.execute(
            "DELETE FROM admins WHERE id = %s AND (SELECT COUNT(*) FROM admins) > 1",
            (admin_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(400, "Cannot delete the last admin account")
        # Same transaction as the delete, so a refused delete rolls this back
        # too: a removed admin must never keep a live session.
        revoked = sessions.revoke_all_for(cur, sessions.KIND_ADMIN, admin_id)
        _audit(cur, security_events.ADMIN_DELETED, request, actor=admin,
               target_kind="admin", target_id=admin_id,
               target_label=row["handle"],
               detail={"sessions_revoked": revoked})
    return {"deleted": row["handle"]}


@app.post("/api/admin/admins/{admin_id}/toggle-trusted")
def toggle_admin_trusted(admin_id: int, request: Request,
                         admin: dict = Depends(current_admin)):
    if not admin["trusted"]:
        raise HTTPException(403, "Only trusted admins can manage admin accounts")
    calling_handle = admin["handle"]
    with db() as cur:
        cur.execute("SELECT handle FROM admins WHERE id = %s", (admin_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Admin not found")
        if row["handle"] == calling_handle:
            raise HTTPException(400, "Cannot change your own trusted status")
        cur.execute(
            "UPDATE admins SET trusted = NOT trusted WHERE id = %s RETURNING trusted",
            (admin_id,),
        )
        updated = cur.fetchone()
        _audit(cur, security_events.ADMIN_TRUST_CHANGED, request, actor=admin,
               target_kind="admin", target_id=admin_id,
               target_label=row["handle"], detail={"trusted": updated["trusted"]})
    return {"handle": row["handle"], "trusted": updated["trusted"]}


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, request: Request, response: Response):
    _throttle_login(req.handle, request)
    with db() as cur:
        cur.execute(
            "SELECT id, handle, password_hash, trusted FROM admins WHERE handle = %s",
            (req.handle,),
        )
        row = cur.fetchone()
    # verify_password fails closed on a missing or unreadable hash, so an
    # account with no credential set cannot be signed into. The error text is
    # deliberately identical for an unknown handle and a wrong password.
    if not row or not verify_password(row["password_hash"], req.password):
        _record_login_attempt(req.handle, request, False)
        with db() as cur:
            _audit(cur, security_events.ADMIN_SIGN_IN_FAILED, request,
                   target_kind="admin", target_label=req.handle)
        raise HTTPException(401, "Invalid handle or password")
    _record_login_attempt(req.handle, request, True)
    _begin_session(response, sessions.KIND_ADMIN, row["id"], request,
                   req.remember)
    with db() as cur:
        _audit(cur, security_events.ADMIN_SIGNED_IN, request, actor=row)
    return {"handle": row["handle"], "trusted": row["trusted"]}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    """End this session. Idempotent, and never reveals whether one existed."""
    admin = _principal(request, sessions.KIND_ADMIN)
    with db() as cur:
        sessions.revoke(cur, _session_token(request))
        if admin:
            _audit(cur, security_events.ADMIN_SIGNED_OUT, request, actor=admin)
    response.delete_cookie(sessions.COOKIE_NAME, path="/")
    return {"signed_out": True}


@app.get("/api/me")
def whoami(request: Request):
    """Report who the cookie belongs to, so the page can restore its state."""
    validator = _principal(request, sessions.KIND_VALIDATOR)
    if validator:
        # Must carry every field /api/login returns: startup() replaces the
        # stored profile with this object wholesale, so anything missing here is
        # destroyed rather than merely absent. Omitting last_seen_update made
        # routeAfterLogin() compare 0 < update_version on every single reload
        # and re-show the "What's new" interstitial forever.
        return {"kind": "validator", "validator": {
            "coder_id": validator["coder_id"],
            "handle": validator["handle"],
            "validator_tier": validator["validator_tier"],
            "onboarded": bool(validator["onboarded_at"]),
            "last_seen_update": validator["last_seen_update"],
            "update_version": CURRENT_UPDATE_VERSION,
        }}
    admin = _principal(request, sessions.KIND_ADMIN)
    if admin:
        return {"kind": "admin",
                "admin": {"handle": admin["handle"], "trusted": admin["trusted"]}}
    return {"kind": None}


# Agreement %: between the two HUMAN validators only — the share of the 3 checks
# (type/original/outcome) on which V1 and V2 gave the same answer. NULL until both
# humans have submitted. The LLM is deliberately not a voter here: a clean human
# consensus must never read as 0% just because the LLM dissents. LLM dissent is
# surfaced separately (llm_dissent below). Computed in SQL so it's sortable.
def _agree_field(f):
    return f"(u.validator_1->>'{f}' = u.validator_2->>'{f}')"

_AGREEMENT_SQL = (
    "(CASE WHEN u.validator_1 IS NOT NULL AND u.validator_2 IS NOT NULL "
    "THEN round(100.0 * ("
    f"(CASE WHEN {_agree_field('type_check')} THEN 1 ELSE 0 END) + "
    f"(CASE WHEN {_agree_field('original_check')} THEN 1 ELSE 0 END) + "
    f"(CASE WHEN {_agree_field('outcome_check')} THEN 1 ELSE 0 END)"
    ") / 3.0)::int ELSE NULL END)"
)

# LLM dissent: comma-separated list of checks where the LLM ran cleanly, the two
# humans agree on an answer, and the LLM contradicts it. NULL when there is none —
# shown as a marker next to the agreement % so the signal the old 3-way metric
# carried ("the LLM is the odd one out") isn't lost.
def _llm_dissent_field(f, label):
    return (
        "(CASE WHEN u.llm_validator IS NOT NULL "
        "AND NOT jsonb_exists(u.llm_validator, 'error') "
        f"AND u.validator_1->>'{f}' = u.validator_2->>'{f}' "
        f"AND u.llm_validator->>'{f}' IS NOT NULL "
        f"AND u.llm_validator->>'{f}' <> u.validator_1->>'{f}' "
        f"THEN '{label}' END)"
    )

_LLM_DISSENT_SQL = (
    "NULLIF(concat_ws(', ', "
    f"{_llm_dissent_field('type_check', 'type')}, "
    f"{_llm_dissent_field('original_check', 'original')}, "
    f"{_llm_dissent_field('outcome_check', 'outcome')}"
    "), '')"
)

# Records shown in the Skipped admin panel. Repeated events by one validator are
# retained for audit but never satisfy either escalation rule on their own.
_SKIP_PANEL_SQL = f"""
EXISTS (
    SELECT 1
    FROM validation_skips vs
    WHERE vs.record_id = u.record_id
    GROUP BY vs.record_id
    HAVING COUNT(DISTINCT vs.validator_id) > {SKIP_DISTINCT_VALIDATOR_THRESHOLD}
       OR COUNT(DISTINCT vs.validator_id) FILTER (
              WHERE vs.reason_code IN ('eligibility_unclear', 'data_quality')
          ) >= {SKIP_ISSUE_VALIDATOR_THRESHOLD}
)
""".strip()

# Whitelist of sortable columns → safe SQL expression (never interpolate raw input).
_ENTRIES_SORT = {
    "study":       "COALESCE(u.final_title_r, u.title_r)",
    "type":        "COALESCE(u.final_type, u.type)",
    "outcome":     "COALESCE(u.final_outcome, u.outcome)",
    "status":      "u.validation_status",
    "validators":  "(SELECT COUNT(*) FROM validation_queue vq WHERE vq.record_id = u.record_id AND vq.is_validated = TRUE)",
    "agreement":   _AGREEMENT_SQL,
    "approved_by": "u.admin_name",
    "skips":       "(SELECT COUNT(DISTINCT vs.validator_id) FROM validation_skips vs WHERE vs.record_id = u.record_id)",
}


@app.get("/api/admin/entries")
def admin_entries(
    filter: str = "all",
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    sort: str = "",
    dir: str = "desc",
    admin: dict = Depends(current_admin),
):

    sort_col = _ENTRIES_SORT.get(sort)
    direction = "ASC" if str(dir).lower() == "asc" else "DESC"
    if sort_col:
        order_by = f"ORDER BY {sort_col} {direction} NULLS LAST, u.updated_at DESC"
    elif filter == "skipped":
        order_by = (
            "ORDER BY skip_issue_validator_count DESC, "
            "skip_validator_count DESC, latest_skip_at DESC"
        )
    else:
        order_by = (
            "ORDER BY CASE u.validation_status "
            "WHEN 'need_review' THEN 0 WHEN 'consensus_reached' THEN 1 "
            "WHEN 'validation_inprogress' THEN 2 WHEN 'unvalidated' THEN 3 "
            "WHEN 'validated' THEN 4 WHEN 'rejected' THEN 5 ELSE 6 END, "
            "u.updated_at DESC"
        )

    base_where = {
        "all":              "",
        "pending_approval": "WHERE u.validation_status = 'consensus_reached'",
        "needs_review":     "WHERE u.validation_status = 'need_review'",
        "admin_comments":   "WHERE NULLIF(BTRIM(u.admin_notes), '') IS NOT NULL",
        "skipped":          f"WHERE {_SKIP_PANEL_SQL}",
        "llm_errors":       "WHERE u.llm_validator IS NOT NULL AND (u.llm_validator)::jsonb ? 'error'",
        "validated":        "WHERE u.validation_status = 'validated'",
        "rejected":         "WHERE u.validation_status = 'rejected'",
        "admin_checked":    "WHERE u.admin_checked = TRUE",
        # Advisory flags never change validation_status, so without a filter a
        # flagged record is invisible until someone happens to open it.
        "quality_flagged":  "WHERE jsonb_array_length(COALESCE(u.quality_flags, '[]'::jsonb)) > 0",
    }.get(filter, "")

    search = search.strip()
    if search:
        connector = "AND" if base_where else "WHERE"
        where = f"{base_where} {connector} (u.title_r ILIKE %s OR u.study_r ILIKE %s OR u.doi_r ILIKE %s)"
        search_param = f"%{search}%"
        search_args = (search_param, search_param, search_param)
    else:
        where = base_where
        search_args = ()

    offset = (page - 1) * per_page

    with db() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM unvalidated u {where}", search_args)
        total = cur.fetchone()["n"]

        cur.execute(
            f"""
            SELECT
                u.record_id::text,
                u.pair_id,
                u.study_r,
                u.title_r,
                u.final_title_r,
                u.year_r,
                u.doi_r,
                u.type,
                u.outcome,
                u.final_type,
                u.final_outcome,
                u.validation_status,
                u.is_tiebreaker,
                u.admin_checked,
                u.admin_name,
                u.admin_notes,
                u.note_saved_by,
                (u.validator_1 IS NOT NULL)::boolean AS has_v1,
                (u.validator_2 IS NOT NULL)::boolean AS has_v2,
                (u.llm_validator IS NOT NULL)::boolean AS has_llm,
                u.validator_1->>'validator_name' AS v1_handle,
                u.validator_2->>'validator_name' AS v2_handle,
                (u.llm_validator IS NOT NULL AND (u.llm_validator)::jsonb ? 'error')::boolean AS has_llm_error,
                jsonb_array_length(COALESCE(u.quality_flags, '[]'::jsonb)) AS quality_flag_count,
                {_AGREEMENT_SQL} AS agreement_pct,
                {_LLM_DISSENT_SQL} AS llm_dissent,
                (SELECT COUNT(*) FROM validation_queue vq
                 WHERE vq.record_id = u.record_id AND vq.is_validated = TRUE) AS validator_count,
                (SELECT COUNT(*) FROM validation_queue vq
                 JOIN validators tv ON tv.id = vq.validator_id AND tv.validator_tier >= 1
                 WHERE vq.record_id = u.record_id
                   AND vq.is_validated = TRUE
                   AND vq.validator_slot IN ('human_1', 'human_2')) AS trusted_validator_count,
                (SELECT COUNT(*) FROM validation_skips vs
                 WHERE vs.record_id = u.record_id) AS skip_event_count,
                (SELECT COUNT(DISTINCT vs.validator_id) FROM validation_skips vs
                 WHERE vs.record_id = u.record_id) AS skip_validator_count,
                (SELECT COUNT(DISTINCT vs.validator_id) FROM validation_skips vs
                 WHERE vs.record_id = u.record_id
                   AND vs.reason_code IN ('eligibility_unclear', 'data_quality')) AS skip_issue_validator_count,
                (SELECT MAX(vs.skipped_at) FROM validation_skips vs
                 WHERE vs.record_id = u.record_id) AS latest_skip_at
            FROM unvalidated u
            {where}
            {order_by}
            LIMIT %s OFFSET %s
            """,
            search_args + (per_page, offset),
        )
        entries = []
        for row in cur.fetchall():
            entry = dict(row)
            entry["skip_high_frequency"] = (
                entry["skip_validator_count"] > SKIP_DISTINCT_VALIDATOR_THRESHOLD
            )
            entry["skip_issue_reports"] = (
                entry["skip_issue_validator_count"] >= SKIP_ISSUE_VALIDATOR_THRESHOLD
            )
            entry["skip_review_required"] = (
                entry["skip_high_frequency"] or entry["skip_issue_reports"]
            )
            entries.append(entry)

        # Count badges for each filter tab
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated")
        c_all = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'consensus_reached'")
        c_pending = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'need_review'")
        c_review = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE NULLIF(BTRIM(admin_notes), '') IS NOT NULL")
        c_admin_comments = cur.fetchone()["n"]
        cur.execute(f"SELECT COUNT(*) AS n FROM unvalidated u WHERE {_SKIP_PANEL_SQL}")
        c_skipped = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE llm_validator IS NOT NULL AND (llm_validator)::jsonb ? 'error'")
        c_llm = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'validated'")
        c_validated = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE admin_checked = TRUE")
        c_admin = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'rejected'")
        c_rejected = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated "
                    "WHERE jsonb_array_length(COALESCE(quality_flags, '[]'::jsonb)) > 0")
        c_quality = cur.fetchone()["n"]

    return {
        "entries": entries,
        "total": total,
        "page": page,
        "per_page": per_page,
        "counts": {
            "all": c_all,
            "pending_approval": c_pending,
            "needs_review": c_review,
            "admin_comments": c_admin_comments,
            "skipped": c_skipped,
            "llm_errors": c_llm,
            "validated": c_validated,
            "rejected": c_rejected,
            "admin_checked": c_admin,
            "quality_flagged": c_quality,
        },
    }


@app.get("/api/admin/entries/{record_id}")
def admin_entry_detail(record_id: str, admin: dict = Depends(current_admin)):

    with db() as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Record not found")

        # No cursor: renderAdminDetail() does not show the coded set, and
        # preloadAdminDetails() fetches 15 entries at a time — passing one here
        # buys 15 sibling lookups and 15 payloads per preload that nobody reads.
        record = _enrich_pair(dict(row))
        record["record_id"] = str(record["record_id"])

        # psycopg2 already deserialises JSONB to dicts; guard for string fallback
        for field in ("validator_1", "validator_2", "llm_validator", "quality_flags"):
            val = record.get(field)
            if isinstance(val, str):
                record[field] = json.loads(val)

        cur.execute(
            "SELECT * FROM validation_queue WHERE record_id = %s ORDER BY validator_slot",
            (record_id,),
        )
        queue_slots = []
        for r in cur.fetchall():
            s = dict(r)
            s["queue_id"] = str(s["queue_id"])
            s["record_id"] = str(s["record_id"])
            queue_slots.append(s)

        # Skip events are append-only and independent from the reusable queue
        # slots. Validator identities are exposed only through this admin API.
        cur.execute(
            """
            SELECT vs.skip_id::text,
                   vs.queue_id::text,
                   vs.validator_id,
                   v.handle AS validator_name,
                   vs.reason_code,
                   vs.comment,
                   vs.skipped_at
            FROM validation_skips vs
            JOIN validators v ON v.id = vs.validator_id
            WHERE vs.record_id = %s
            ORDER BY vs.skipped_at DESC, vs.skip_id DESC
            """,
            (record_id,),
        )
        skip_history = [dict(r) for r in cur.fetchall()]
        skip_summary = _skip_summary(cur, record_id)
        skip_summary["high_frequency"] = (
            skip_summary["validator_count"] > SKIP_DISTINCT_VALIDATOR_THRESHOLD
        )
        skip_summary["issue_reports"] = (
            skip_summary["issue_validator_count"] >= SKIP_ISSUE_VALIDATOR_THRESHOLD
        )

        # Automatic save recovery is a separate audit stream: it must be visible
        # to admins without being presented or counted as a voluntary Skip.
        cur.execute(
            """
            SELECT sfr.failure_id::text,
                   sfr.submission_id::text,
                   sfr.queue_id::text,
                   sfr.validator_id,
                   v.handle AS validator_name,
                   sfr.status,
                   sfr.failure_code,
                   sfr.failure_message,
                   sfr.failed_at,
                   sfr.expires_at,
                   sfr.released_at
            FROM submission_failure_releases sfr
            JOIN validators v ON v.id = sfr.validator_id
            WHERE sfr.record_id = %s
            ORDER BY sfr.failed_at DESC, sfr.failure_id DESC
            """,
            (record_id,),
        )
        submission_failure_history = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT duplicate_record_id::text AS duplicate_record_id,
                   survivor_record_id::text AS survivor_record_id,
                   merged_by, merged_at
            FROM validated_record_merges
            WHERE duplicate_record_id = %s
            """,
            (record_id,),
        )
        duplicate_merge = cur.fetchone()
        duplicate_merge = dict(duplicate_merge) if duplicate_merge else None

        # Track record for the two human cards: lifetime judgement count (from
        # validators, so assignment work counts too) and how many of their
        # judgements an admin has flagged. Keyed by validator id (as a string —
        # JSON object keys always are).
        human_ids = {s.get("validator_id") for s in queue_slots
                     if s.get("validator_slot") in ("human_1", "human_2")}
        for key in ("validator_1", "validator_2"):
            human_ids.add((record.get(key) or {}).get("validator_id"))
        human_ids = [i for i in human_ids if i]

        validator_stats = {}
        if human_ids:
            cur.execute(
                """
                SELECT v.id AS validator_id,
                       v.total_judgements AS judged,
                       (SELECT COUNT(*) FROM validation_queue vq
                        WHERE vq.validator_id = v.id AND vq.flagged) AS flags
                FROM validators v
                WHERE v.id = ANY(%s)
                """,
                (human_ids,),
            )
            for r in cur.fetchall():
                validator_stats[str(r["validator_id"])] = {
                    "judged": r["judged"], "flags": r["flags"],
                }

    # Detect abstract-only conflict
    import re as _re
    def _norm(t): return _re.sub(r'[^a-z0-9]', '', (t or "").lower())

    v1, v2 = record.get("validator_1") or {}, record.get("validator_2") or {}
    correction_fields = ["corrected_doi_o", "corrected_title_o", "corrected_outcome", "corrected_type", "corrected_title_r", "corrected_url_r"]
    check_fields      = ["type_check", "original_check", "outcome_check"]

    checks_agree      = all(v1.get(f) == v2.get(f) for f in check_fields)
    corrections_agree = (
        all(v1.get(f) == v2.get(f) for f in correction_fields)
        # same normalized compare as consensus: a resolver-link paste is not a conflict
        and (_normalize_doi(v1.get("doi_r_published")) or "").lower()
            == (_normalize_doi(v2.get("doi_r_published")) or "").lower()
    )
    abstracts_differ  = _norm(v1.get("corrected_abstract")) != _norm(v2.get("corrected_abstract"))

    abstract_only_conflict = (
        record.get("validation_status") == "need_review"
        and checks_agree
        and corrections_agree
        and abstracts_differ
        and bool(v1) and bool(v2)
    )

    return {"record": record, "queue_slots": queue_slots,
            "abstract_only_conflict": abstract_only_conflict,
            "validator_stats": validator_stats,
            "duplicate_merge": duplicate_merge,
            "skip_history": skip_history,
            "skip_summary": skip_summary,
            "submission_failure_history": submission_failure_history}


@app.post("/api/admin/queue/{queue_id}/flag")
def admin_flag_queue(queue_id: str, req: FlagQueueRequest | None = None, admin: dict = Depends(current_admin)):
    admin_handle = admin["handle"]
    reason = req.reason.strip() if req and req.reason else ""
    with db() as cur:
        cur.execute(
            "UPDATE validation_queue SET flagged = NOT flagged WHERE queue_id = %s RETURNING flagged, validator_id, record_id",
            (queue_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Queue entry not found")
        now_flagged = bool(row["flagged"])
        if now_flagged:
            cur.execute(
                "UPDATE validation_queue SET flag_reason = %s WHERE queue_id = %s",
                (reason or None, queue_id),
            )
            if row["validator_id"] and reason:
                paper_lines = ""
                cur.execute("SELECT title_r, study_r, doi_r FROM unvalidated WHERE record_id = %s", (row["record_id"],))
                paper = cur.fetchone()
                if paper:
                    if paper["title_r"]:
                        paper_lines += f"\nPaper: {paper['title_r']}"
                    if paper["study_r"]:
                        paper_lines += f"\nStudy number: {paper['study_r']}"
                    if paper["doi_r"]:
                        paper_lines += f"\nDOI: {paper['doi_r']}"
                body_text = f"One of your judgements was flagged by the review team.{paper_lines}\n\nReason: {reason}"
                cur.execute(
                    """
                    INSERT INTO validator_messages (validator_id, subject, body, sent_by, queue_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (row["validator_id"], "Your judgement was flagged", body_text, admin_handle, queue_id),
                )
        else:
            cur.execute("UPDATE validation_queue SET flag_reason = NULL WHERE queue_id = %s", (queue_id,))
    return {"flagged": now_flagged}


@app.get("/api/messages")
def get_validator_messages(validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute("SELECT id FROM validators WHERE id = %s", (coder_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Validator not found")
        cur.execute(
            """
            SELECT id, subject, body, is_read, sent_by, sent_at, direction, parent_id, queue_id
            FROM validator_messages
            WHERE validator_id = %s
            ORDER BY sent_at ASC
            LIMIT 200
            """,
            (coder_id,),
        )
        msgs = [dict(r) for r in cur.fetchall()]
    return {"messages": msgs}


@app.post("/api/messages/{msg_id}/read")
def mark_message_read(msg_id: int,
                      validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    with db() as cur:
        cur.execute(
            "UPDATE validator_messages SET is_read = TRUE WHERE id = %s AND validator_id = %s",
            (msg_id, coder_id),
        )
    return {"ok": True}


@app.post("/api/messages/{parent_id}/reply")
def reply_to_message(parent_id: int, req: ReplyRequest,
                     validator: dict = Depends(current_validator)):
    coder_id = validator["coder_id"]
    body_text = req.body.strip()
    if not body_text:
        raise HTTPException(400, "Reply cannot be empty")
    with db() as cur:
        cur.execute(
            "SELECT validator_id, subject, direction, queue_id FROM validator_messages WHERE id = %s",
            (parent_id,),
        )
        parent = cur.fetchone()
        if not parent:
            raise HTTPException(404, "Message not found")
        if parent["validator_id"] != coder_id:
            raise HTTPException(403, "Not your message")
        if parent["direction"] == "inbound":
            raise HTTPException(400, "Cannot reply to a reply")
        subject = parent["subject"]
        if not subject.startswith("Re: "):
            subject = f"Re: {subject}"
        cur.execute(
            """
            INSERT INTO validator_messages
                (validator_id, subject, body, direction, parent_id, queue_id, is_read, is_read_by_admin)
            VALUES (%s, %s, %s, 'inbound', %s, %s, TRUE, FALSE)
            RETURNING id
            """,
            (coder_id, subject, body_text, parent_id, parent["queue_id"]),
        )
        new_id = cur.fetchone()["id"]
    return {"ok": True, "id": new_id}


@app.get("/api/admin/messages")
def list_admin_conversations(admin: dict = Depends(current_admin)):
    with db() as cur:
        cur.execute(
            """
            WITH thread_stats AS (
                SELECT
                    COALESCE(parent_id, id) AS root_id,
                    MAX(sent_at) AS last_activity,
                    SUM(CASE WHEN direction = 'inbound' AND is_read_by_admin = FALSE
                             THEN 1 ELSE 0 END)::int AS unread_count
                FROM validator_messages
                GROUP BY root_id
            ),
            thread_last AS (
                SELECT DISTINCT ON (COALESCE(parent_id, id))
                    COALESCE(parent_id, id) AS root_id,
                    body                    AS last_body,
                    direction               AS last_direction
                FROM validator_messages
                ORDER BY COALESCE(parent_id, id), sent_at DESC
            )
            SELECT
                r.id                               AS thread_id,
                r.validator_id,
                v.handle                           AS validator_handle,
                COALESCE(u.title_r, r.subject)     AS subject,
                r.sent_by                          AS admin_name,
                r.queue_id,
                ts.last_activity,
                ts.unread_count,
                tl.last_body,
                tl.last_direction
            FROM validator_messages r
            JOIN validators v ON v.id = r.validator_id
            LEFT JOIN validation_queue vq ON vq.queue_id = r.queue_id
            LEFT JOIN unvalidated u ON u.record_id = vq.record_id
            JOIN thread_stats ts ON ts.root_id = r.id
            JOIN thread_last tl ON tl.root_id = r.id
            WHERE r.parent_id IS NULL
            ORDER BY ts.last_activity DESC
            """
        )
        rows = cur.fetchall()
    conversations = []
    for r in rows:
        d = dict(r)
        preview = (d["last_body"] or "")[:80]
        if len(d["last_body"] or "") > 80:
            preview += "…"
        d["preview"] = preview
        del d["last_body"]
        conversations.append(d)
    return {"conversations": conversations}


@app.get("/api/admin/thread/{thread_id}")
def get_admin_thread(thread_id: int, mark_read: bool = False, admin: dict = Depends(current_admin)):
    with db() as cur:
        if mark_read:
            cur.execute(
                """
                UPDATE validator_messages
                SET is_read_by_admin = TRUE
                WHERE (id = %s OR parent_id = %s)
                  AND direction = 'inbound' AND is_read_by_admin = FALSE
                """,
                (thread_id, thread_id),
            )
        cur.execute(
            """
            SELECT id, subject, body, is_read, sent_by, sent_at,
                   direction, parent_id, queue_id
            FROM validator_messages
            WHERE id = %s OR parent_id = %s
            ORDER BY sent_at ASC
            """,
            (thread_id, thread_id),
        )
        msgs = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """
            SELECT v.handle,
                   COALESCE(u.title_r, vm.subject) AS subject,
                   vm.sent_by AS admin_name
            FROM validator_messages vm
            JOIN validators v ON v.id = vm.validator_id
            LEFT JOIN validation_queue vq ON vq.queue_id = vm.queue_id
            LEFT JOIN unvalidated u ON u.record_id = vq.record_id
            WHERE vm.id = %s
            """,
            (thread_id,),
        )
        meta = cur.fetchone()
    return {
        "messages": msgs,
        "handle":     meta["handle"]     if meta else "",
        "subject":    meta["subject"]    if meta else "",
        "admin_name": meta["admin_name"] if meta else "",
    }


@app.post("/api/admin/thread/{thread_id}/reply")
def admin_reply_to_thread(thread_id: int, req: AdminReplyRequest, admin: dict = Depends(current_admin)):
    admin_handle = admin["handle"]
    body_text = req.body.strip()
    if not body_text:
        raise HTTPException(400, "Reply cannot be empty")
    with db() as cur:
        cur.execute(
            "SELECT validator_id, subject, queue_id FROM validator_messages WHERE id = %s AND parent_id IS NULL",
            (thread_id,),
        )
        root = cur.fetchone()
        if not root:
            raise HTTPException(404, "Thread not found")
        cur.execute(
            """
            INSERT INTO validator_messages
                (validator_id, subject, body, direction, parent_id, queue_id,
                 sent_by, is_read, is_read_by_admin)
            VALUES (%s, %s, %s, 'outbound', %s, %s, %s, FALSE, TRUE)
            RETURNING id, sent_at
            """,
            (root["validator_id"], root["subject"], body_text,
             thread_id, root["queue_id"], admin_handle),
        )
        row = cur.fetchone()
    return {"ok": True, "id": row["id"], "sent_at": row["sent_at"].isoformat()}


@app.get("/api/admin/messages/{validator_id}")
def get_admin_conversation(validator_id: int, mark_read: bool = False, admin: dict = Depends(current_admin)):
    with db() as cur:
        if mark_read:
            cur.execute(
                """
                UPDATE validator_messages
                SET is_read_by_admin = TRUE
                WHERE validator_id = %s AND direction = 'inbound' AND is_read_by_admin = FALSE
                """,
                (validator_id,),
            )
        cur.execute(
            """
            SELECT id, subject, body, is_read, sent_by, sent_at,
                   direction, parent_id, is_read_by_admin
            FROM validator_messages
            WHERE validator_id = %s
            ORDER BY sent_at ASC
            """,
            (validator_id,),
        )
        msgs = [dict(r) for r in cur.fetchall()]
    return {"messages": msgs}


@app.post("/api/admin/message")
def admin_send_message(req: AdminMessageRequest, admin: dict = Depends(current_admin)):
    admin_handle = admin["handle"]
    subject = req.subject.strip()
    body_text = req.body.strip()
    if not subject or not body_text:
        raise HTTPException(400, "Subject and body are required")
    with db() as cur:
        if req.broadcast:
            cur.execute("SELECT id FROM validators")
            ids = [r["id"] for r in cur.fetchall()]
            if not ids:
                raise HTTPException(404, "No validators to message")
            cur.executemany(
                """
                INSERT INTO validator_messages (validator_id, subject, body, sent_by)
                VALUES (%s, %s, %s, %s)
                """,
                [(vid, subject, body_text, admin_handle) for vid in ids],
            )
            return {"ok": True, "broadcast": True, "sent": len(ids)}

        if req.validator_id is None:
            raise HTTPException(400, "validator_id is required")
        cur.execute("SELECT id FROM validators WHERE id = %s", (req.validator_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Validator not found")
        cur.execute(
            """
            INSERT INTO validator_messages (validator_id, subject, body, sent_by)
            VALUES (%s, %s, %s, %s)
            """,
            (req.validator_id, subject, body_text, admin_handle),
        )
    return {"ok": True}


@app.post("/api/admin/entries/{record_id}/approve")
def admin_approve(record_id: str, admin: dict = Depends(current_admin)):
    admin_handle = admin["handle"]

    with db() as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s AND validation_status = 'consensus_reached'", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Record not found or not awaiting approval")
        rec = dict(row)

        cur.execute(
            """
            UPDATE unvalidated SET
                validation_status = 'validated',
                admin_checked     = TRUE,
                admin_name        = %s,
                updated_at        = NOW()
            WHERE record_id = %s
            """,
            (admin_handle, record_id),
        )

        # Read work ids post-trigger (approve doesn't change DOIs, but stay uniform
        # with resolve so a stale work id can never reach the validated row).
        cur.execute("SELECT oa_work_id_o, oa_work_id_r FROM unvalidated WHERE record_id = %s", (record_id,))
        _wid = cur.fetchone() or {}

        approved_type = rec.get("final_type") or rec["type"]
        if approved_type == "reproduction":
            try:
                approved_computation = normalize_axis_value(
                    "outcome_computation",
                    rec.get("final_outcome_computation") or rec.get("outcome_computation"),
                )
                approved_robustness = normalize_axis_value(
                    "outcome_robustness",
                    rec.get("final_outcome_robustness") or rec.get("outcome_robustness"),
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not approved_computation or not approved_robustness:
                raise HTTPException(400, "Resolve both reproduction axes before approval")
            approved_outcome = derive_reproduction_outcome(
                approved_computation, approved_robustness
            )
            approved_computational_quote = (
                rec.get("final_computational_quote") or rec.get("outcome_computational_quote")
            )
            approved_computational_source = (
                rec.get("final_computational_source") or rec.get("out_quote_computational_source")
            )
            approved_robustness_quote = (
                rec.get("final_robustness_quote") or rec.get("outcome_robustness_quote")
            )
            approved_robustness_source = (
                rec.get("final_robustness_source") or rec.get("out_quote_robust_source")
            )
        else:
            approved_outcome = normalize_outcome(rec.get("final_outcome") or rec["outcome"])
            if approved_type != "replication" or approved_outcome not in REPLICATION_OUTCOMES \
                    or approved_outcome == "not_a_replication":
                raise HTTPException(400, "Resolve a valid replication outcome before approval")
            approved_computation = approved_robustness = None
            approved_computational_quote = approved_computational_source = None
            approved_robustness_quote = approved_robustness_source = None

        # Drop any prior row for this record (the natural key is mutable, so a
        # correction could otherwise leave a stale duplicate under the old key).
        cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
        cur.execute(
            """
            INSERT INTO validated (
                record_id, doi_r, study_r, title_r, year_r, url_r, ref_r, abstract_r,
                doi_o, study_o, title_o, year_o, url_o, ref_o,
                oa_work_id_o, oa_work_id_r,
                type, outcome, outcome_quote, out_quote_source,
                outcome_computation, outcome_computational_quote, out_quote_computational_source,
                outcome_robustness, outcome_robustness_quote, out_quote_robust_source,
                doi_r_published, alt_identifier_r, admin_approved
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s, TRUE)
            ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o) DO UPDATE SET
            -- original_key is doi_o, or oa_work_id_o when the original has no
            -- registered DOI (books, chapters, pre-DOI papers). doi_o is '' for
            -- ALL of those, so keying on it merged distinct originals that also
            -- shared a title. See db_schema.sql.
                record_id        = EXCLUDED.record_id,
                title_r          = EXCLUDED.title_r,
                year_r           = EXCLUDED.year_r,
                url_r            = EXCLUDED.url_r,
                ref_r            = EXCLUDED.ref_r,
                abstract_r       = EXCLUDED.abstract_r,
                title_o          = EXCLUDED.title_o,
                year_o           = EXCLUDED.year_o,
                url_o            = EXCLUDED.url_o,
                ref_o            = EXCLUDED.ref_o,
                oa_work_id_o     = EXCLUDED.oa_work_id_o,
                oa_work_id_r     = EXCLUDED.oa_work_id_r,
                type             = EXCLUDED.type,
                outcome          = EXCLUDED.outcome,
                outcome_quote    = EXCLUDED.outcome_quote,
                out_quote_source = EXCLUDED.out_quote_source,
                outcome_computation            = EXCLUDED.outcome_computation,
                outcome_computational_quote    = EXCLUDED.outcome_computational_quote,
                out_quote_computational_source = EXCLUDED.out_quote_computational_source,
                outcome_robustness             = EXCLUDED.outcome_robustness,
                outcome_robustness_quote       = EXCLUDED.outcome_robustness_quote,
                out_quote_robust_source        = EXCLUDED.out_quote_robust_source,
                doi_r_published  = EXCLUDED.doi_r_published,
                alt_identifier_r = EXCLUDED.alt_identifier_r,
                admin_approved   = TRUE,
                validated_at     = NOW()
            """,
            (
                record_id,
                rec["doi_r"], rec["study_r"], rec.get("final_title_r") or rec["title_r"], rec["year_r"], rec.get("final_url_r") or rec["url_r"], rec["ref_r"], rec.get("final_abstract_r") or rec["abstract_r"],
                # doi_o has a legitimate blank state (see admin_resolve) — a prior
                # deliberate clear ('') must not fall through to the raw doi_o here.
                rec["final_doi_o"] if rec.get("final_doi_o") is not None else rec["doi_o"],
                rec["study_o"], rec.get("final_title_o") or rec["title_o"],
                rec["year_o"], rec["url_o"], rec["ref_o"],
                _wid.get("oa_work_id_o"), _wid.get("oa_work_id_r"),
                approved_type,
                approved_outcome,
                rec.get("final_outcome_quote") or rec["outcome_quote"],
                rec.get("final_out_quote_source") or rec.get("out_quote_source"),
                # Reproduction axes: consensus value if one was reached, else what the
                # extractor coded. Approving must not blank a coded axis.
                approved_computation,
                approved_computational_quote,
                approved_computational_source,
                approved_robustness,
                approved_robustness_quote,
                approved_robustness_source,
                rec.get("doi_r_published"), rec.get("alt_identifier_r"),
            ),
        )

    return {"approved": True, "record_id": record_id}


@app.post("/api/admin/entries/{record_id}/flag-review")
def admin_flag_review(record_id: str, req: dict = Body(default={}), admin: dict = Depends(current_admin)):
    """Move a consensus_reached record back to need_review for further scrutiny."""
    admin_handle = admin["handle"]
    with db() as cur:
        cur.execute("SELECT validation_status FROM unvalidated WHERE record_id = %s", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        if row["validation_status"] not in ("consensus_reached", "need_review"):
            raise HTTPException(400, "Only pending-approval records can be flagged for review")

        notes = (req.get("admin_notes") or "").strip() or None
        cur.execute(
            """
            UPDATE unvalidated SET
                validation_status = 'need_review',
                admin_name        = %s,
                admin_notes       = COALESCE(%s, admin_notes),
                updated_at        = NOW()
            WHERE record_id = %s
            """,
            (admin_handle, notes, record_id),
        )

    return {"flagged": True, "record_id": record_id}


class AdminNoteRequest(BaseModel):
    note: str | None = None


@app.post("/api/admin/entries/{record_id}/note")
def admin_save_note(record_id: str, req: AdminNoteRequest, admin: dict = Depends(current_admin)):
    """Save or update a persistent admin note on an entry. Visible to all admins."""
    admin_handle = admin["handle"]
    with db() as cur:
        cur.execute("SELECT record_id FROM unvalidated WHERE record_id = %s", (record_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Record not found")
        cur.execute(
            """
            UPDATE unvalidated
            SET admin_notes   = %s,
                note_saved_by = %s,
                note_saved_at = NOW()
            WHERE record_id = %s
            """,
            (req.note or None, admin_handle, record_id),
        )
    return {"saved": True}


def _validated_identity_conflict(
    cur, record_id: str, *, doi_r, study_r, title_r,
    doi_o, oa_work_id_o, study_o, title_o,
):
    """Lock and return the other validated row with this proposed identity."""
    candidate_original_key = (doi_o if doi_o not in (None, "") else oa_work_id_o) or ""
    cur.execute(
        """
        SELECT v.record_id::text AS record_id,
               v.doi_r, v.study_r, v.title_r,
               v.doi_o, v.original_key, v.study_o, v.title_o,
               v.type, v.outcome
        FROM validated v
        WHERE v.record_id <> %s
          AND v.doi_r IS NOT DISTINCT FROM %s
          AND v.study_r IS NOT DISTINCT FROM %s
          AND v.title_r IS NOT DISTINCT FROM %s
          AND v.original_key IS NOT DISTINCT FROM %s
          AND v.study_o IS NOT DISTINCT FROM %s
          AND v.title_o IS NOT DISTINCT FROM %s
        LIMIT 1
        FOR UPDATE OF v
        """,
        (record_id, doi_r, study_r, title_r, candidate_original_key, study_o, title_o),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _validated_duplicate_detail(record_id: str, conflict: dict) -> dict:
    """Structured 409 payload consumed by the admin duplicate-merge dialog."""
    return {
        "code": "validated_duplicate_conflict",
        "message": (
            "This resolution matches an existing validated record. "
            "Confirm an explicit duplicate merge; no record has been overwritten."
        ),
        "duplicate_record_id": record_id,
        "survivor_record_id": conflict["record_id"],
        "survivor": {
            key: conflict.get(key) for key in (
                "doi_r", "study_r", "title_r", "doi_o", "original_key",
                "study_o", "title_o", "type", "outcome",
            )
        },
    }


@app.post("/api/admin/entries/{record_id}/resolve")
def admin_resolve(record_id: str, req: AdminResolveRequest, admin: dict = Depends(current_admin)):
    admin_handle = admin["handle"]

    if req.type_check not in VALID_CHECKS:
        raise HTTPException(400, "type_check must be 'correct' or 'incorrect'")
    if req.original_check not in VALID_CHECKS:
        raise HTTPException(400, "original_check must be 'correct' or 'incorrect'")
    if req.outcome_check not in VALID_CHECKS:
        raise HTTPException(400, "outcome_check must be 'correct' or 'incorrect'")

    corrected_title_r = _requested_title(req, "r")
    corrected_title_o = _requested_title(req, "o")

    with db() as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        rec = dict(row)

        # A merged duplicate is retained in unvalidated for provenance. Prevent a
        # later resolve request from accidentally reviving it as another validated
        # record; repeating the confirmed request is harmless and idempotent.
        cur.execute(
            "SELECT survivor_record_id::text AS survivor_record_id "
            "FROM validated_record_merges WHERE duplicate_record_id = %s",
            (record_id,),
        )
        prior_merge = cur.fetchone()
        if prior_merge:
            survivor_id = prior_merge["survivor_record_id"]
            if req.merge_into_record_id == survivor_id:
                return {
                    "resolved": True, "merged": True, "already_merged": True,
                    "record_id": record_id, "survivor_record_id": survivor_id,
                }
            raise HTTPException(409, detail={
                "code": "record_already_merged",
                "message": "This record has already been merged into another validated record.",
                "duplicate_record_id": record_id,
                "survivor_record_id": survivor_id,
            })
        base_type = rec.get("final_type") or rec["type"]
        base_outcome = rec.get("final_outcome") or rec.get("outcome")
        base_computation = (
            rec.get("final_outcome_computation")
            if rec.get("final_outcome_computation") is not None
            else rec.get("outcome_computation")
        )
        base_robustness = (
            rec.get("final_outcome_robustness")
            if rec.get("final_outcome_robustness") is not None
            else rec.get("outcome_robustness")
        )
        final_type, final_outcome, final_computation, final_robustness = _validated_outcome_request(
            req, base_type, base_outcome, base_computation, base_robustness
        )

        # Only enforce 2-validator requirement for records still in progress
        if rec["validation_status"] not in ("need_review", "consensus_reached", "rejected"):
            cur.execute(
                "SELECT COUNT(*) AS n FROM validation_queue WHERE record_id = %s AND is_validated = TRUE",
                (record_id,),
            )
            if cur.fetchone()["n"] < 2:
                raise HTTPException(400, "Cannot resolve a record with fewer than 2 validator submissions")

        # When a field isn't re-corrected this pass, keep any prior admin/consensus
        # correction (final_*) — never silently revert to the raw extracted value.
        # Original DOI can be a legitimate blank (books, chapters, pre-DOI papers,
        # or an admin deliberately clearing a wrong DOI-less-original mismatch) —
        # '' is a real correction here, not "no correction submitted", so it can't
        # use the truthy-`or` pattern the other fields use. None = untouched this
        # pass; falls back through the prior stored correction (even if '') before
        # the raw extracted value, so a deliberate clear survives a later
        # re-resolve instead of reverting to the wrong DOI.
        if req.original_check == "incorrect" and req.corrected_doi_o is not None:
            final_doi_o = req.corrected_doi_o.strip()
        else:
            final_doi_o = rec.get("final_doi_o")
            if final_doi_o is None:
                final_doi_o = rec["doi_o"]
        final_title_o   = corrected_title_o if req.original_check == "incorrect" and corrected_title_o else (rec.get("final_title_o") or rec["title_o"])
        final_computational_quote = (
            req.corrected_computational_quote
            if req.corrected_computational_quote is not None
            else rec.get("final_computational_quote")
            if rec.get("final_computational_quote") is not None
            else rec.get("outcome_computational_quote")
        )
        final_computational_source = (
            req.corrected_computational_source
            if req.corrected_computational_source is not None
            else rec.get("final_computational_source")
            if rec.get("final_computational_source") is not None
            else rec.get("out_quote_computational_source")
        )
        final_robustness_quote = (
            req.corrected_robustness_quote
            if req.corrected_robustness_quote is not None
            else rec.get("final_robustness_quote")
            if rec.get("final_robustness_quote") is not None
            else rec.get("outcome_robustness_quote")
        )
        final_robustness_source = (
            req.corrected_robustness_source
            if req.corrected_robustness_source is not None
            else rec.get("final_robustness_source")
            if rec.get("final_robustness_source") is not None
            else rec.get("out_quote_robust_source")
        )
        if final_type != "reproduction":
            final_computational_quote = None
            final_computational_source = None
            final_robustness_quote = None
            final_robustness_source = None
        final_outcome_q = req.corrected_outcome_quote if req.corrected_outcome_quote else (rec.get("final_outcome_quote") or rec["outcome_quote"])
        final_title_r    = corrected_title_r if corrected_title_r else rec.get("final_title_r") or rec["title_r"]
        final_doi_r      = req.corrected_doi_r      if req.corrected_doi_r      else rec.get("final_doi_r")      or rec["doi_r"]
        final_url_r      = req.corrected_url_r      if req.corrected_url_r      else rec.get("final_url_r")      or rec["url_r"]
        final_abstract_r = req.corrected_abstract_r if req.corrected_abstract_r else rec.get("final_abstract_r") or rec["abstract_r"]
        # These two can be deliberately cleared: None = untouched, '' = clear.
        final_doi_r_pub  = _normalize_doi(req.doi_r_published) if req.doi_r_published  is not None else rec.get("doi_r_published")
        final_alt_ids    = (req.alt_identifier_r.strip() or None) if req.alt_identifier_r is not None else rec.get("alt_identifier_r")

        # Outcome-quote source: honour an explicit admin choice, otherwise (re)detect
        # from the final quote against the final abstract, falling back to any stored value.
        from consensus_engine import quote_source_for
        from extractor_vocab import normalize_quote_source
        # Any named source, not just abstract/full_text: the extractor names the
        # section a quote came from (discussion, results, …) and pipe-joins several
        # when it spans them. Restricting this to the two computed values meant a
        # granular stored value matched no option in the admin form, so opening and
        # saving a record silently replaced it with an auto-detected guess.
        _explicit_src = normalize_quote_source(req.out_quote_source)
        if _explicit_src:
            final_src, final_src_by = _explicit_src, admin_handle
        else:
            final_src = (quote_source_for(final_outcome_q, final_abstract_r)
                         or rec.get("final_out_quote_source") or rec.get("out_quote_source"))
            final_src_by = None

        # Admin confirmed this is not a replication → reject it, never insert into FLoRA
        if final_type == "not_validation":
            cur.execute(
                """
                UPDATE unvalidated SET
                    admin_checked       = TRUE,
                    admin_name          = %s,
                    admin_notes         = COALESCE(%s, admin_notes),
                    validation_status   = 'rejected',
                    updated_at          = NOW()
                WHERE record_id = %s
                """,
                (admin_handle, req.admin_notes, record_id),
            )
            # Rejected → must not remain in the authoritative export table.
            cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
            return {"resolved": True, "rejected": True, "record_id": record_id}

        was_rejected = rec["validation_status"] == "rejected"
        cur.execute(
            """
            UPDATE unvalidated SET
                admin_checked       = TRUE,
                admin_name          = %s,
                admin_notes         = COALESCE(%s, admin_notes),
                validation_status   = 'validated',
                final_type          = %s,
                final_doi_o         = %s,
                final_title_o       = %s,
                final_outcome       = %s,
                final_outcome_quote = %s,
                final_title_r       = %s,
                final_doi_r         = %s,
                final_url_r         = %s,
                final_abstract_r    = %s,
                final_out_quote_source = %s,
                out_quote_source_by = %s,
                doi_r_published     = %s,
                alt_identifier_r    = %s,
                admin_override      = %s,
                -- Reproduction axes. The validated request has already filled a
                -- complete reproduction pair or cleared the whole shape for a
                -- replication, so the six final fields remain coherent with type.
                final_outcome_computation  = %s,
                final_computational_quote  = %s,
                final_computational_source = %s,
                final_outcome_robustness   = %s,
                final_robustness_quote     = %s,
                final_robustness_source    = %s,
                updated_at          = NOW()
            WHERE record_id = %s
            """,
            (admin_handle, req.admin_notes, final_type, final_doi_o, final_title_o, final_outcome, final_outcome_q, final_title_r, final_doi_r, final_url_r, final_abstract_r, final_src, final_src_by, final_doi_r_pub, final_alt_ids, was_rejected,
             final_computation, final_computational_quote, final_computational_source,
             final_robustness, final_robustness_quote, final_robustness_source,
             record_id),
        )

        # The UPDATE above fires the DOI trigger, which NULLs a work id whose DOI just
        # changed. Re-read the post-trigger values so the validated row never carries a
        # work id that points at the old paper.
        #
        # The reproduction axes come back in the same read: the UPDATE COALESCEd the
        # form's values over the stored ones, so this is where the resolved pair is
        # assembled. Recomputing it in Python would duplicate that precedence rule.
        cur.execute(
            "SELECT oa_work_id_o, oa_work_id_r, "
            "final_outcome_computation, final_computational_quote, final_computational_source, "
            "final_outcome_robustness, final_robustness_quote, final_robustness_source "
            "FROM unvalidated WHERE record_id = %s", (record_id,))
        _wid = cur.fetchone() or {}

        # Resolving A to B's natural key must never use an upsert: that would write
        # A's data into B while keeping B's record_id. First report a structured
        # conflict. The UI may then repeat this same request with B's id as an
        # explicit merge target, at which point A is retired and linked to B.
        conflict = _validated_identity_conflict(
            cur, record_id,
            doi_r=final_doi_r, study_r=rec["study_r"], title_r=final_title_r,
            doi_o=final_doi_o, oa_work_id_o=_wid.get("oa_work_id_o"),
            study_o=rec["study_o"], title_o=final_title_o,
        )
        if conflict:
            conflict_id = conflict["record_id"]
            if req.merge_into_record_id != conflict_id:
                raise HTTPException(
                    409, detail=_validated_duplicate_detail(record_id, conflict)
                )

            snapshot = {
                "doi_r": final_doi_r,
                "study_r": rec["study_r"],
                "title_r": final_title_r,
                "doi_o": final_doi_o,
                "oa_work_id_o": _wid.get("oa_work_id_o"),
                "study_o": rec["study_o"],
                "title_o": final_title_o,
                "type": final_type,
                "outcome": final_outcome,
                "admin_notes": req.admin_notes,
            }
            cur.execute(
                """
                INSERT INTO validated_record_merges (
                    duplicate_record_id, survivor_record_id, merged_by,
                    resolution_snapshot
                ) VALUES (%s, %s, %s, %s::jsonb)
                """,
                (record_id, conflict_id, admin_handle, json.dumps(snapshot)),
            )
            # B remains the authoritative validated row. A and all of its source
            # metadata/judgements remain available under unvalidated for audit.
            cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
            cur.execute(
                """
                UPDATE unvalidated
                   SET validation_status = 'rejected',
                       admin_checked = TRUE,
                       admin_name = %s,
                       admin_override = FALSE,
                       updated_at = NOW()
                 WHERE record_id = %s
                """,
                (admin_handle, record_id),
            )
            cur.execute(
                """
                UPDATE assignments
                   SET status = 'done', completed_at = COALESCE(completed_at, NOW())
                 WHERE record_id = %s AND status = 'open'
                """,
                (record_id,),
            )
            return {
                "resolved": True, "merged": True,
                "record_id": record_id, "survivor_record_id": conflict_id,
            }

        if req.merge_into_record_id:
            raise HTTPException(409, detail={
                "code": "duplicate_merge_target_changed",
                "message": (
                    "The proposed identity no longer matches the selected merge target. "
                    "Nothing was merged."
                ),
                "duplicate_record_id": record_id,
                "survivor_record_id": req.merge_into_record_id,
            })

        # Drop any prior row for this record (the natural key is mutable, so a
        # correction could otherwise leave a stale duplicate under the old key).
        cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
        cur.execute(
            """
            INSERT INTO validated (
                record_id, doi_r, study_r, title_r, year_r, url_r, ref_r, abstract_r,
                doi_o, study_o, title_o, year_o, url_o, ref_o,
                oa_work_id_o, oa_work_id_r,
                type, outcome, outcome_quote, out_quote_source, out_quote_source_by,
                outcome_computation, outcome_computational_quote, out_quote_computational_source,
                outcome_robustness, outcome_robustness_quote, out_quote_robust_source,
                doi_r_published, alt_identifier_r, admin_approved
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s, TRUE)
            -- A concurrent resolve may create the same identity after the locked
            -- conflict check. Never overwrite it: leave this insert empty, detect
            -- its row-count below, and return the same explicit-merge conflict.
            ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o)
            DO NOTHING
            RETURNING record_id
            """,
            (
                record_id,
                final_doi_r, rec["study_r"], final_title_r, rec["year_r"], final_url_r, rec["ref_r"], final_abstract_r,
                final_doi_o, rec["study_o"], final_title_o, rec["year_o"], rec["url_o"], rec["ref_o"],
                _wid.get("oa_work_id_o"), _wid.get("oa_work_id_r"),
                final_type, final_outcome, final_outcome_q, final_src, final_src_by,
                _wid.get("final_outcome_computation"), _wid.get("final_computational_quote"),
                _wid.get("final_computational_source"), _wid.get("final_outcome_robustness"),
                _wid.get("final_robustness_quote"), _wid.get("final_robustness_source"),
                final_doi_r_pub, final_alt_ids,
            ),
        )
        if not cur.fetchone():
            conflict = _validated_identity_conflict(
                cur, record_id,
                doi_r=final_doi_r, study_r=rec["study_r"], title_r=final_title_r,
                doi_o=final_doi_o, oa_work_id_o=_wid.get("oa_work_id_o"),
                study_o=rec["study_o"], title_o=final_title_o,
            )
            if conflict:
                raise HTTPException(
                    409, detail=_validated_duplicate_detail(record_id, conflict)
                )
            raise HTTPException(
                409, "The validated identity changed concurrently; retry the resolution."
            )

    return {"resolved": True, "rejected": False, "record_id": record_id}


# ---------------------------------------------------------------------------
# Source records — entry-sheet datatable (see sources.yml, sync_sources.py)
# ---------------------------------------------------------------------------
# Routes stay thin on purpose: all SQL and business logic lives in
# source_records_service, which has no FastAPI types in it, so the Lambda
# handlers planned for the next phase can call the identical functions.

def _source_filters(
    type: str = "", status: str = "", outcome: str = "",
    search: str = "", reviewed: str = "", flagged: bool = False,
    source: str = "",
) -> dict:
    return {
        "type": type, "status": status, "outcome": outcome,
        "search": search, "reviewed": reviewed, "flagged": flagged,
        "source": source,
    }


@app.get("/api/admin/source-records")
def admin_source_records(
    type: str = "", status: str = "", outcome: str = "",
    search: str = "", reviewed: str = "", flagged: bool = False,
    source: str = "",
    sort: str = "", dir: str = "asc",
    page: int = 1, per_page: int = 50,
    admin: dict = Depends(current_admin),
):
    filters = _source_filters(type, status, outcome, search, reviewed, flagged, source)
    with db() as cur:
        return source_records_service.list_records(
            cur, filters, sort=sort, direction=dir, page=page, per_page=per_page
        )


@app.get("/api/admin/source-records/export.csv")
def admin_source_records_export(
    type: str = "", status: str = "", outcome: str = "",
    search: str = "", reviewed: str = "", flagged: bool = False,
    source: str = "",
    admin: dict = Depends(current_admin),
):
    """Every row matching the current filter, not just the current page."""
    filters = _source_filters(type, status, outcome, search, reviewed, flagged, source)
    with db() as cur:
        columns, rows = source_records_service.export_rows(cur, filters)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="source_records.csv"'},
    )


@app.get("/api/admin/source-records/sync-status")
def admin_source_sync_status(admin: dict = Depends(current_admin)):
    """Feeds the freshness banner above the grid."""
    with db() as cur:
        return source_records_service.sync_status(cur)


# ---------------------------------------------------------------------------
# FLoRA tab — the prepared product, not the records behind it.
#
# Derived from source_records on demand by transform_sources.build(), so it can
# never drift from what the Source Records tab shows. flora_service caches the
# frame until the underlying tables actually change.
# ---------------------------------------------------------------------------

def _flora_filters(type: str = "", source: str = "", outcome: str = "",
                   search: str = "", unregistered: bool = False) -> dict:
    return {"type": type, "source": source, "outcome": outcome,
            "search": search, "unregistered": unregistered}


@app.get("/api/admin/flora")
def admin_flora_records(
    type: str = "", source: str = "", outcome: str = "",
    search: str = "", unregistered: bool = False,
    sort: str = "", dir: str = "asc",
    page: int = 1, per_page: int = 50,
    admin: dict = Depends(current_admin),
):
    filters = _flora_filters(type, source, outcome, search, unregistered)
    with db() as cur:
        return flora_service.list_records(
            cur, filters, sort=sort, direction=dir, page=page, per_page=per_page
        )


@app.get("/api/admin/flora/stats")
def admin_flora_stats(admin: dict = Depends(current_admin)):
    """Headline numbers plus when the id registry last ran."""
    with db() as cur:
        return flora_service.stats(cur)


@app.get("/api/admin/flora/export.csv")
def admin_flora_export(
    type: str = "", source: str = "", outcome: str = "",
    search: str = "", unregistered: bool = False,
    admin: dict = Depends(current_admin),
):
    """Current rows matching the filter, in the canonical release order.

    Omit filters for the complete dataset. Retained per-run artifacts have their
    own endpoint so later edits cannot change an earlier run's downloaded file.
    """
    filters = _flora_filters(type, source, outcome, search, unregistered)
    try:
        with db() as cur:
            body = flora_service.export_csv(cur, filters)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="flora_{stamp}.csv"'},
    )


# Preprint duplicates: pairs that may be one paper under two DOIs, which the build
# could not settle. Declared before /flora/{flora_id}, which would otherwise take
# "preprint-duplicates" for a record id.

@app.get("/api/admin/flora/preprint-duplicates")
def admin_flora_preprint_duplicates(admin: dict = Depends(current_admin)):
    """Pairs awaiting a ruling, and the rulings already made."""
    with db() as cur:
        return flora_service.preprint_review(cur)


class PreprintPairDecision(BaseModel):
    doi_1: str = Field(max_length=512)
    doi_2: str = Field(max_length=512)
    action: str                       # 'keep_1' | 'keep_2' | 'keep_both'
    note: str = Field("", max_length=500)


@app.post("/api/admin/flora/preprint-duplicates/decision")
def admin_flora_preprint_decide(req: PreprintPairDecision, request: Request,
                                admin: dict = Depends(current_admin)):
    """Rule on one pair. The FLoRA tab reflects it at once; the published CSV at
    the next pipeline run."""
    with db() as cur:
        try:
            result = flora_service.decide_preprint_pair(
                cur, req.doi_1, req.doi_2, req.action, admin["handle"], req.note)
        except flora_service.PairNotFound:
            raise HTTPException(404, "This pair is no longer detected. Refresh the list.")
        except ValueError as e:
            raise HTTPException(400, str(e))
        # The table keeps only the current ruling; the audit trail keeps each one.
        _audit(cur, security_events.FLORA_PREPRINT_RULED, request, actor=admin,
               target_kind="preprint_pair", target_id=result["pair_key"],
               detail={"action": result["action"], "doi_1": result["doi_1"],
                       "doi_2": result["doi_2"], "note": req.note or None})
    return result


@app.delete("/api/admin/flora/preprint-duplicates/decision")
def admin_flora_preprint_undo(pair_key: str, request: Request,
                              admin: dict = Depends(current_admin)):
    """Withdraw a ruling; the pair goes back to the default rules."""
    with db() as cur:
        try:
            withdrawn = flora_service.undo_preprint_decision(cur, pair_key)
        except flora_service.PairNotFound:
            raise HTTPException(404, "No ruling is stored for this pair.")
        _audit(cur, security_events.FLORA_PREPRINT_WITHDRAWN, request, actor=admin,
               target_kind="preprint_pair", target_id=pair_key, detail=withdrawn)
    return {"status": "withdrawn", "pair_key": pair_key}


@app.get("/api/admin/flora/{flora_id}")
def admin_flora_record(flora_id: str, admin: dict = Depends(current_admin)):
    """One full row, every column, for the detail panel."""
    with db() as cur:
        record = flora_service.get_record(cur, flora_id)
        if record is None:
            raise HTTPException(404, "No such FLoRA record")
        # The source rows dedup collapsed into this one. Shown so a reviewer can see
        # what was folded in rather than having to reconstruct it from the grid.
        record["merged_sources"] = flora_service.merged_sources(cur, flora_id)
    return record


# ---------------------------------------------------------------------------
# Manual entry-sheet sync. The button queues a job; a scheduler on every pod drains
# the queue under a PostgreSQL advisory lock, so the work happens out of band and
# only one run is ever in flight. See source_sync_runner.py.
# ---------------------------------------------------------------------------

@app.get("/api/admin/source-sync/status")
def admin_source_sync_status_panel(limit: int = 10, admin: dict = Depends(current_admin)):
    """Everything the sync panel renders: recent runs of the button, and the
    per-source results of the last sync (which the nightly Action writes too)."""
    with db() as cur:
        jobs = source_sync_runner.recent_jobs(cur, limit)
        per_source = source_records_service.sync_status(cur)
        active = source_sync_runner.active_job_id(cur)
    return {"jobs": jobs, "active": bool(active), "active_job_id": active, **per_source}


@app.get("/api/admin/source-sync/jobs/{job_id}")
def admin_source_sync_job(job_id: str, admin: dict = Depends(current_admin)):
    """The complete log for one run, for when the tail is not enough."""
    try:
        job_id = str(UUID(job_id))
    except ValueError:
        raise HTTPException(404, "No such pipeline run")
    with db() as cur:
        job = source_sync_runner.job_detail(cur, job_id)
    if job is None:
        raise HTTPException(404, "No such sync job")
    return job


@app.get("/api/admin/source-sync/jobs/{job_id}/artifacts/{artifact}")
def admin_source_sync_artifact(
    job_id: str, artifact: str, admin: dict = Depends(current_admin),
):
    """Download the exact CSV or report retained by an individual pipeline run."""
    media_types = {
        "flora.csv": "text/csv; charset=utf-8",
        "recovery.csv": "text/csv; charset=utf-8",
        "report.json": "application/json",
        "report.md": "text/markdown; charset=utf-8",
    }
    if artifact not in media_types:
        raise HTTPException(404, "No such pipeline artifact")
    try:
        job_id = str(UUID(job_id))
    except ValueError:
        raise HTTPException(404, "No such pipeline run")
    with db() as cur:
        body = source_sync_runner.job_artifact(cur, job_id, artifact)
    if body is None:
        raise HTTPException(404, "This run has no retained artifact. Review the run log or run the pipeline again.")
    if artifact == "report.json":
        body = json.dumps(body, indent=2, ensure_ascii=False) if not isinstance(body, str) else body
    filename = "flora.csv" if artifact == "flora.csv" else f"flora_{job_id[:8]}_{artifact}"
    return Response(
        content=body,
        media_type=media_types[artifact],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/admin/source-sync/dispatch", status_code=202)
def admin_source_sync_dispatch(request: Request, admin: dict = Depends(current_admin)):
    """Queue the complete FLoRA pipeline; the panel polls for its log and artifacts."""
    try:
        with db() as cur:
            job_id = source_sync_runner.queue_run(cur, admin["handle"], trigger="admin")
            _audit(cur, security_events.SOURCE_SYNC_DISPATCHED, request, actor=admin,
                   target_kind="source_sync_job", target_id=job_id, detail={"trigger": "admin"})
    except source_sync_runner.SyncRunConflict as exc:
        raise HTTPException(
            409,
            detail={
                "message": "A sync run is already active",
                "active_job_id": exc.active_job_id,
            },
        )
    return {"status": "queued", "job_id": job_id}


@app.get("/api/admin/source-records/duplicates")
def admin_source_duplicates(unresolved_only: bool = True, admin: dict = Depends(current_admin)):
    """Papers appearing under more than one sheet UUID, grouped for comparison."""
    with db() as cur:
        return source_records_service.duplicate_groups(cur, unresolved_only)


class DuplicateResolution(BaseModel):
    status: str                       # 'distinct' | 'duplicate'
    duplicate_of: str | None = None


@app.post("/api/admin/source-records/{record_id}/duplicate")
def admin_resolve_duplicate(record_id: str, req: DuplicateResolution,
                            admin: dict = Depends(current_admin)):
    handle = admin["handle"]
    with db() as cur:
        try:
            return source_records_service.resolve_duplicate(
                cur, record_id, req.status, handle, req.duplicate_of
            )
        except source_records_service.RecordNotFound:
            raise HTTPException(404, "Record not found")
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.get("/api/admin/source-records/vocabularies")
def admin_source_vocabularies(admin: dict = Depends(current_admin)):
    """Dropdown options for the review panel, derived from the stored rows so a
    new value appearing upstream needs no code change."""
    with db() as cur:
        return source_records_service.field_vocabularies(cur)


class SourceRecordUpdate(BaseModel):
    fields: dict = {}
    version: int          # required: an absent version would skip the concurrency check
    note: str = ""


@app.patch("/api/admin/source-records/{record_id}")
def admin_source_record_update(
    record_id: str,
    req: SourceRecordUpdate,
    type: str = "", status: str = "", outcome: str = "",
    search: str = "", reviewed: str = "", flagged: bool = False,
    source: str = "",
    sort: str = "", dir: str = "asc",
    admin: dict = Depends(current_admin),
):
    """Save a review. Stamps reviewer + timestamp even when nothing changed, and
    returns the next record in the active filter so 'Save & next' is one trip."""
    handle = admin["handle"]
    filters = _source_filters(type, status, outcome, search, reviewed, flagged, source)
    with db() as cur:
        # Computed BEFORE the save: stamping reviewed_at can move this row out of
        # its own filter (the "not reviewed" queue is exactly that case), and then
        # there would be no next id to advance to.
        try:
            neighbours = source_records_service.neighbours(
                cur, record_id, filters, sort, dir
            )
        except source_records_service.RecordNotFound:
            raise HTTPException(404, "Record not found")

        try:
            result = source_records_service.update_record(
                cur, record_id, req.fields or {}, req.version, handle, req.note
            )
        except source_records_service.RecordNotFound:
            raise HTTPException(404, "Record not found")
        except source_records_service.VersionConflict as e:
            # Detail is a plain string so it survives the client's `err.detail`
            # unwrapping; the structured bits go in dedicated headers.
            raise HTTPException(
                409, "This record was changed by someone else.",
                headers={
                    "X-Current-Version": str(e.current.get("version") or ""),
                    "X-Reviewed-By": str(e.current.get("reviewed_by") or ""),
                },
            )
        except ValueError as e:
            raise HTTPException(400, str(e))

        result["neighbours"] = neighbours
        return result


@app.get("/api/admin/source-records/{record_id}")
def admin_source_record_detail(
    record_id: str,
    type: str = "", status: str = "", outcome: str = "",
    search: str = "", reviewed: str = "", flagged: bool = False,
    source: str = "",
    sort: str = "", dir: str = "asc",
    admin: dict = Depends(current_admin),
):
    """Full record for the review panel. The filter params are passed through so
    prev/next walk the queue the reviewer is actually looking at."""
    filters = _source_filters(type, status, outcome, search, reviewed, flagged, source)
    with db() as cur:
        try:
            return source_records_service.get_record(
                cur, record_id, filters, sort=sort, direction=dir
            )
        except source_records_service.RecordNotFound:
            raise HTTPException(404, "Record not found")


# ---------------------------------------------------------------------------
# Extractor maintenance history and manual controls
# ---------------------------------------------------------------------------

@app.get("/api/admin/maintenance/runs")
def admin_maintenance_runs(
    days: int = 7,
    limit: int = 100,
    admin: dict = Depends(current_admin),
):
    """Return at least one week of run summaries for the admin operations tab."""
    days = max(7, min(days, 90))
    limit = max(1, min(limit, 250))
    with db() as cur:
        cur.execute(
            """
            SELECT run_id::text, trigger, requested_stage, requested_by, status,
                   created_at, started_at, finished_at, stage_status,
                   (safety_report #- '{cleanup_receipt,deleted_records}') AS safety_report,
                   RIGHT(log_text, 2000) AS log_tail
            FROM extractor_maintenance_runs
            WHERE created_at >= NOW() - make_interval(days => %s)
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (days, limit),
        )
        runs = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT COUNT(*) FILTER (
                       WHERE status IN ('warning', 'blocked', 'failed')
                   ) AS attention_count,
                   COUNT(*) AS run_count
            FROM extractor_maintenance_runs
            WHERE created_at >= NOW() - make_interval(days => %s)
            """,
            (days,),
        )
        totals = dict(cur.fetchone())
    from sync_csv import RemovalPercentConfigurationError, parse_max_removal_percent

    removal_config_error = None
    try:
        removal_limit = parse_max_removal_percent()
    except RemovalPercentConfigurationError as exc:
        # Never display a plausible fallback that the sync process will not
        # actually use. The same parser is authoritative in both places.
        removal_limit = None
        removal_config_error = str(exc)
    return {
        "days": days,
        "runs": runs,
        "max_removal_percent": removal_limit,
        "removal_config_error": removal_config_error,
        **totals,
    }


@app.get("/api/admin/maintenance/runs/{run_id}")
def admin_maintenance_run_detail(
    run_id: UUID,
    admin: dict = Depends(current_admin),
):
    """Return the complete retained log for one maintenance run."""
    with db() as cur:
        cur.execute(
            """
            SELECT run_id::text, trigger, requested_stage, requested_by, status,
                   created_at, started_at, finished_at, stage_status, safety_report,
                   log_text
            FROM extractor_maintenance_runs
            WHERE run_id = %s
            """,
            (str(run_id),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Maintenance run not found")
    return dict(row)


@app.post("/api/admin/maintenance/run", status_code=202)
def admin_start_maintenance(
    req: MaintenanceRunRequest,
    request: Request,
    admin: dict = Depends(current_admin),
):
    """Queue the non-destructive full routine or one explicit maintenance stage."""
    admin_handle = admin["handle"]
    if req.stage == "cleanup" and not req.confirm_cleanup:
        raise HTTPException(
            400,
            "Explicit cleanup confirmation is required for cleanup runs",
        )

    from extractor_maintenance import (
        MaintenanceRunConflict,
        queue_maintenance_run,
    )

    try:
        run_id = queue_maintenance_run(
            DATABASE_URL,
            req.stage,
            trigger="admin",
            requested_by=admin_handle,
        )
    except MaintenanceRunConflict as exc:
        raise HTTPException(
            409,
            detail={
                "message": "Another extractor maintenance run is already active",
                "active_run_id": exc.active_run_id,
            },
        )

    with db() as cur:
        _audit(cur, security_events.MAINTENANCE_STARTED, request, actor=admin,
               target_kind="maintenance_run", target_id=run_id,
               detail={"stage": req.stage, "confirm_cleanup": req.confirm_cleanup})
    return {"run_id": run_id, "status": "queued", "stage": req.stage}


# ---------------------------------------------------------------------------
# Nightly CSV sync scheduler
# ---------------------------------------------------------------------------

def _retry_tiebreakers() -> None:
    """Re-run consensus on tiebreaker records whose LLM call actually errored earlier.

    Only records whose stored llm_validator carries an 'error' key are retried —
    those are the ones stuck by a transient LLM failure. Records that reached
    need_review with a *successful* LLM (genuine 3-way ambiguity) are left for a
    human/admin: re-running them would waste paid LLM calls nightly and could flip
    their status purely from LLM nondeterminism."""
    from consensus_engine import evaluate_consensus
    try:
        with db() as cur:
            cur.execute("""
                SELECT record_id FROM unvalidated
                WHERE validation_status = 'need_review' AND is_tiebreaker = TRUE
                  AND llm_validator IS NOT NULL AND llm_validator ? 'error'
            """)
            ids = [str(r["record_id"]) for r in cur.fetchall()]
        print(f"[retry_tiebreakers] Found {len(ids)} stuck tiebreaker record(s)")
        for record_id in ids:
            with db() as cur:
                evaluate_consensus(cur, record_id)
            print(f"[retry_tiebreakers] Re-evaluated {record_id}")
    except Exception:
        import traceback
        print("[retry_tiebreakers] ERROR:")
        traceback.print_exc()


def _reap_stale_slots() -> None:
    """Release abandoned slots and return their records to the pool.
       Tiered: buffered (started_at IS NULL) after 45 min, started after 5 days.
       Replaces the old inline cleanup so locks free up regardless of traffic."""
    try:
        with db() as cur:
            # Judge and skip lock unvalidated before validation_queue. Take the
            # same order here to avoid a scheduler/request deadlock.
            cur.execute(
                """
                SELECT u.record_id
                FROM unvalidated u
                WHERE EXISTS (
                    SELECT 1
                    FROM validation_queue vq
                    WHERE vq.record_id = u.record_id
                      AND vq.is_validated = FALSE AND vq.is_shown = TRUE
                      AND vq.validator_slot IN ('human_1', 'human_2')
                      AND (
                            (vq.started_at IS NULL AND vq.shown_at < NOW() - INTERVAL '45 minutes')
                         OR (vq.started_at IS NOT NULL AND vq.started_at < NOW() - INTERVAL '5 days')
                      )
                )
                ORDER BY u.record_id
                FOR UPDATE OF u
                """
            )
            stale_record_ids = [str(row["record_id"]) for row in cur.fetchall()]
            if not stale_record_ids:
                expired_stamps = _expire_submission_failure_stamps(cur)
                if expired_stamps:
                    print(f"[reaper] expired {expired_stamps} submission-failure stamp(s)")
                return

            cur.execute(
                """
                UPDATE validation_queue
                SET validator_id = NULL, validator_name = NULL,
                    is_shown = FALSE, shown_at = NULL, started_at = NULL
                WHERE record_id = ANY(%s::uuid[])
                  AND is_validated = FALSE AND is_shown = TRUE
                  AND validator_slot IN ('human_1', 'human_2')
                  AND (
                        (started_at IS NULL     AND shown_at   < NOW() - INTERVAL '45 minutes')
                     OR (started_at IS NOT NULL AND started_at < NOW() - INTERVAL '5 days')
                  )
                """,
                (stale_record_ids,),
            )
            released_slots = cur.rowcount
            if released_slots:
                cur.execute(
                    """
                    UPDATE unvalidated u
                    SET validation_status = 'unvalidated'
                    WHERE u.record_id = ANY(%s::uuid[])
                      AND validation_status = 'validation_inprogress'
                      AND NOT EXISTS (
                          SELECT 1 FROM validation_queue vq
                          WHERE vq.record_id = u.record_id
                            AND (
                                  (vq.validator_id IS NOT NULL AND vq.is_validated = FALSE)
                               OR vq.is_validated = TRUE
                            )
                      )
                    """,
                    (stale_record_ids,),
                )
                print(f"[reaper] released {released_slots} stale slot(s)")
            expired_stamps = _expire_submission_failure_stamps(cur)
            if expired_stamps:
                print(f"[reaper] expired {expired_stamps} submission-failure stamp(s)")
    except Exception:
        import traceback
        print("[reaper] ERROR:")
        traceback.print_exc()


def _start_scheduler() -> None:
    from extractor_maintenance import run_queued, run_scheduled
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_scheduled,
        CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="extractor_maintenance",
        max_instances=1,
        coalesce=True,
        kwargs={"database_url": DATABASE_URL},
    )
    # Admin requests persist only a queued row. Every pod polls that durable
    # queue; the PostgreSQL advisory lock elects exactly one executor and lets a
    # replacement pod recover work whose original process disappeared.
    scheduler.add_job(
        run_queued,
        IntervalTrigger(seconds=10),
        id="extractor_maintenance_dispatcher",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
        kwargs={"database_url": DATABASE_URL, "data_dir": DATA_DIR},
    )
    # Same durable-queue pattern as the extractor dispatcher above: the click only
    # persists a row, and whichever pod wins the advisory lock runs it. A 5s poll
    # keeps the button feeling immediate without meaningful load — the query is one
    # indexed lookup that almost always returns nothing.
    scheduler.add_job(
        source_sync_runner.run_queued,
        IntervalTrigger(seconds=5),
        id="source_sync_dispatcher",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
        kwargs={"database_url": DATABASE_URL},
    )
    scheduler.add_job(
        _retry_tiebreakers,
        CronTrigger(hour=0, minute=22, timezone="UTC"),
    )
    scheduler.add_job(_reap_stale_slots, IntervalTrigger(minutes=2))
    # Hourly is plenty: nothing depends on dead rows disappearing promptly,
    # only on the tables not growing forever.
    scheduler.add_job(_housekeeping, IntervalTrigger(hours=1))
    scheduler.start()


_start_scheduler()


# ---------------------------------------------------------------------------
# Static files (frontend)
# ---------------------------------------------------------------------------

DOCS = ROOT / "docs"

# The page is served here rather than by the mount so its asset links can be
# content-fingerprinted (see static_assets.py). Without that, a rolling
# deployment can leave a browser running the previous app.js against the
# current API.
@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def serve_index():
    return Response(
        fingerprinted_index(DOCS),
        media_type="text/html; charset=utf-8",
        # no-cache still permits a 304; it only forbids reusing the document
        # without asking. A cached index.html would otherwise go on naming the
        # old asset URLs, and the fingerprint could never take effect.
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# Public lookup POSTs only read the committed prepared dataset. Their exact
# paths are exempt from cross-site write blocking; admin routes stay protected.
app.include_router(create_flora_api_router())

# Registered last: the explicit routes above take precedence over the mount.
app.mount("/", StaticFiles(directory=str(DOCS), html=True), name="docs")
