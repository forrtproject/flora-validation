import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import psycopg2.errors
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import hashlib
import resend
from email_templates import forgot_handle_email

load_dotenv()

ROOT = Path(__file__).parent
SCHEMA_PATH = ROOT / "db_schema.sql"
ONBOARDING_PATH = ROOT / "onboarding.json"
OA_CACHE_PATH = ROOT / "oa_cache.json"
DATA_DIR = ROOT / "data"

DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_PASSWORD   = os.getenv("ADMIN_PASSWORD", "flora-admin-2025")
RESEND_API_KEY   = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM       = os.getenv("EMAIL_FROM", "Flora Validator <noreply@forrt.org>")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9._\-]{2,32}$")

VALID_CHECKS = {"correct", "incorrect"}

CURRENT_UPDATE_VERSION = 1  # bump when docs/updates.json content changes


def _make_token(password: str) -> str:
    return hashlib.sha256(f"{password}:flora-admin-v1".encode()).hexdigest()


def _require_admin(token: str) -> str:
    """Validate admin token and return the matching admin handle."""
    with db() as cur:
        cur.execute("SELECT handle, password FROM admins")
        for row in cur.fetchall():
            if token == _make_token(row["password"]):
                return row["handle"]
    raise HTTPException(401, "Unauthorized")


def _require_trusted_admin(token: str) -> str:
    """Validate token and require the admin to be trusted. Returns handle."""
    with db() as cur:
        cur.execute("SELECT handle, password, trusted FROM admins")
        for row in cur.fetchall():
            if token == _make_token(row["password"]):
                if not row["trusted"]:
                    raise HTTPException(403, "Only trusted admins can manage admin accounts")
                return row["handle"]
    raise HTTPException(401, "Unauthorized")


def _seed_admin_if_empty():
    """On first run, create the default admin from ADMIN_PASSWORD env var."""
    with db() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM admins")
        if cur.fetchone()["n"] == 0:
            cur.execute(
                "INSERT INTO admins (handle, password, trusted) VALUES (%s, %s, TRUE)",
                ("admin", ADMIN_PASSWORD),
            )

app = FastAPI(title="Flora Validator")


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


def init_db():
    """Apply db_schema.sql (idempotent) and seed from extracted_latest.csv if DB is empty."""
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
                    [sys.executable, str(ROOT / "csv_to_db.py"), "--input", str(latest_csv)],
                    check=False,
                )


init_db()
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


def _enrich_pair(pair: dict) -> dict:
    """Add OA URLs and legacy field aliases for frontend compatibility."""
    pair = dict(pair)
    pair["oa_url_r"] = oa_url_for(pair.get("doi_r"))
    pair["oa_url_o"] = oa_url_for(pair.get("doi_o"))
    # Aliases: frontend still uses title_r / title_o / outcome_phrase
    pair.setdefault("title_r", pair.get("study_r", ""))
    pair.setdefault("title_o", pair.get("study_o", ""))
    pair.setdefault("outcome_phrase", pair.get("outcome_quote", ""))
    return pair


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    handle: str
    code: str | None = None
    email: str | None = None


class JudgeRequest(BaseModel):
    coder_id: int                       # maps to validators.id
    record_id: str                      # unvalidated.record_id (UUID)
    type_check: str                     # "correct" | "incorrect"
    original_check: str                 # "correct" | "incorrect"
    outcome_check: str                  # "correct" | "incorrect"
    corrected_study_r: str | None = None
    corrected_url_r: str | None = None
    corrected_doi_o: str | None = None
    corrected_study_o: str | None = None
    corrected_outcome: str | None = None
    corrected_type: str | None = None
    corrected_outcome_quote: str | None = None
    corrected_abstract: str | None = None
    validator_notes: str | None = None


class OnboardingComplete(BaseModel):
    coder_id: int


class SkipRequest(BaseModel):
    coder_id: int
    record_id: str


class SeniorRejectRequest(BaseModel):
    coder_id: int
    record_id: str
    validator_notes: str | None = None


class ForgotHandleRequest(BaseModel):
    email: str


class AdminLoginRequest(BaseModel):
    handle: str
    password: str


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
    coder_id: int
    body: str


class UpdateSeenRequest(BaseModel):
    coder_id: int


class AdminResolveRequest(BaseModel):
    admin_name: str
    type_check: str
    original_check: str
    outcome_check: str
    corrected_doi_o: str | None = None
    corrected_study_o: str | None = None
    corrected_outcome: str | None = None
    corrected_type: str | None = None
    corrected_outcome_quote: str | None = None
    corrected_study_r: str | None = None
    corrected_doi_r: str | None = None
    corrected_url_r: str | None = None
    corrected_abstract_r: str | None = None
    admin_notes: str | None = None


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def _points_for(req: JudgeRequest, vote_score: int) -> int:
    """Calculate points for a submission. Base = validator's vote_score."""
    pts = vote_score
    if req.original_check == "correct":
        pts += 2
    if req.outcome_check == "correct":
        pts += 2
    if req.validator_notes and req.validator_notes.strip():
        pts += 1
    return pts



# ---------------------------------------------------------------------------
# Login / onboarding endpoints
# ---------------------------------------------------------------------------

@app.post("/api/login")
def login(req: LoginRequest):
    handle = req.handle.strip()
    if not HANDLE_RE.match(handle):
        raise HTTPException(400, "Handle must be 2–32 chars: letters, digits, . _ -")

    use_email = bool(req.email and req.email.strip())
    use_code = bool(req.code and req.code.strip())
    if not use_email and not use_code:
        raise HTTPException(400, "Provide either an email or a personal code")

    if use_email:
        email = req.email.strip().lower()
        if not EMAIL_RE.match(email):
            raise HTTPException(400, "Invalid email address")
    else:
        code = req.code.strip()
        if len(code) < 4:
            raise HTTPException(400, "Code too short — fill in all four parts")

    with db() as cur:
        if use_email:
            cur.execute(
                "SELECT id, code, email, handle, onboarded_at, validator_tier, last_seen_update FROM validators WHERE email = %s",
                (email,),
            )
        else:
            cur.execute(
                "SELECT id, code, email, handle, onboarded_at, validator_tier, last_seen_update FROM validators WHERE code = %s",
                (code,),
            )
        existing = cur.fetchone()

        if existing:
            if existing["handle"] != handle:
                method = "email" if use_email else "code"
                raise HTTPException(
                    400,
                    f"This {method} is already registered. Please use the correct username.",
                )
            cur.execute(
                "UPDATE validators SET last_login_at = NOW() WHERE id = %s",
                (existing["id"],),
            )
            return {
                "coder_id": existing["id"],
                "code": existing["code"],
                "email": existing["email"],
                "handle": existing["handle"],
                "onboarded": bool(existing["onboarded_at"]),
                "validator_tier": existing["validator_tier"],
                "last_seen_update": existing["last_seen_update"],
                "update_version": CURRENT_UPDATE_VERSION,
            }

        cur.execute("SELECT 1 FROM validators WHERE handle = %s", (handle,))
        if cur.fetchone():
            raise HTTPException(400, "That handle is already taken.")

        if use_email:
            cur.execute(
                "INSERT INTO validators(email, handle) VALUES (%s, %s) RETURNING id",
                (email, handle),
            )
        else:
            cur.execute(
                "INSERT INTO validators(code, handle) VALUES (%s, %s) RETURNING id",
                (code, handle),
            )
        new_id = cur.fetchone()["id"]
        return {
            "coder_id": new_id,
            "code": req.code,
            "email": email if use_email else None,
            "handle": handle,
            "onboarded": False,
            "validator_tier": 0,
            "last_seen_update": 0,
            "update_version": CURRENT_UPDATE_VERSION,
        }


@app.get("/api/onboarding")
def onboarding_pairs():
    with open(ONBOARDING_PATH) as f:
        pairs = json.load(f)["pairs"]
    return {"pairs": [_enrich_pair(p) for p in pairs]}


@app.post("/api/onboarding/complete")
def onboarding_complete(req: OnboardingComplete):
    with db() as cur:
        cur.execute(
            "UPDATE validators SET onboarded_at = NOW(), last_seen_update = %s WHERE id = %s AND onboarded_at IS NULL",
            (CURRENT_UPDATE_VERSION, req.coder_id),
        )
        if cur.rowcount == 0:
            cur.execute("SELECT onboarded_at FROM validators WHERE id = %s", (req.coder_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Validator not found")
    return {"onboarded": True}


@app.post("/api/update-seen")
def mark_update_seen(req: UpdateSeenRequest):
    with db() as cur:
        cur.execute(
            "UPDATE validators SET last_seen_update = %s WHERE id = %s",
            (CURRENT_UPDATE_VERSION, req.coder_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Validator not found")
    return {"ok": True}


@app.get("/api/my-judgements")
def get_my_judgements(coder_id: int):
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
                vq.corrected_study_o,
                vq.corrected_outcome,
                vq.corrected_type,
                vq.corrected_study_r,
                vq.corrected_url_r,
                vq.points,
                vq.validated_at,
                vq.flagged,
                vq.flag_reason,
                u.study_r         AS title_r,
                u.doi_r,
                u.year_r,
                u.outcome         AS extracted_outcome,
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
            "corrected_study_o": r["corrected_study_o"],
            "corrected_outcome": r["corrected_outcome"],
            "corrected_type":    r["corrected_type"],
            "corrected_study_r": r["corrected_study_r"],
            "corrected_url_r":   r["corrected_url_r"],
            "points":            r["points"],
            "validated_at":      r["validated_at"].isoformat() if r["validated_at"] else None,
            "flagged":           bool(r["flagged"]),
            "flag_reason":       r["flag_reason"],
            "title_r":           r["title_r"],
            "doi_r":             r["doi_r"],
            "year_r":            r["year_r"],
            "extracted_outcome": r["extracted_outcome"],
            "validation_status": r["validation_status"],
            "msg_id":            r["msg_id"],
            "msg_body":          r["msg_body"],
            "msg_sent_at":       r["msg_sent_at"].isoformat() if r["msg_sent_at"] else None,
            "msg_is_read":       bool(r["msg_is_read"]) if r["msg_is_read"] is not None else None,
        })
    return {"judgements": judgements}


@app.get("/api/my-judgements/{queue_id}")
def get_my_judgement_detail(queue_id: str, coder_id: int):
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
                vq.corrected_study_o,
                vq.corrected_outcome,
                vq.corrected_outcome_quote,
                vq.corrected_abstract,
                vq.corrected_type,
                vq.corrected_study_r,
                vq.corrected_url_r,
                vq.additional_checks,
                vq.validator_notes,
                vq.points,
                vq.validated_at,
                vq.flagged,
                vq.flag_reason,
                u.study_r        AS raw_study_r,
                u.doi_r          AS raw_doi_r,
                u.year_r         AS raw_year_r,
                u.abstract_r     AS raw_abstract_r,
                u.doi_o          AS raw_doi_o,
                u.study_o        AS raw_study_o,
                u.year_o         AS raw_year_o,
                u.type           AS extracted_type,
                u.outcome        AS extracted_outcome,
                u.outcome_quote  AS extracted_outcome_quote,
                u.validation_status,
                v.validated_record_id,
                v.study_r        AS val_study_r,
                v.doi_r          AS val_doi_r,
                v.year_r         AS val_year_r,
                v.abstract_r     AS val_abstract_r,
                v.doi_o          AS val_doi_o,
                v.study_o        AS val_study_o,
                v.year_o         AS val_year_o,
                v.type           AS val_type,
                v.outcome        AS val_outcome,
                v.outcome_quote  AS val_outcome_quote,
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
        "corrected_study_o":      row["corrected_study_o"],
        "corrected_outcome":      row["corrected_outcome"],
        "corrected_outcome_quote":row["corrected_outcome_quote"],
        "corrected_abstract":     row["corrected_abstract"],
        "corrected_type":         row["corrected_type"],
        "corrected_study_r":      row["corrected_study_r"],
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
        "doi_r":                  row["raw_doi_r"],
        "year_r":                 row["raw_year_r"],
        "abstract_r":             row["raw_abstract_r"],
        "doi_o":                  row["raw_doi_o"],
        "study_o":                row["raw_study_o"],
        "year_o":                 row["raw_year_o"],
        "extracted_type":         row["extracted_type"],
        "extracted_outcome":      row["extracted_outcome"],
        "outcome_quote":          row["extracted_outcome_quote"],
        # Final validated consensus (null if record not yet fully validated)
        "has_validated":          has_validated,
        "val_study_r":            row["val_study_r"],
        "val_doi_r":              row["val_doi_r"],
        "val_year_r":             row["val_year_r"],
        "val_abstract_r":         row["val_abstract_r"],
        "val_doi_o":              row["val_doi_o"],
        "val_study_o":            row["val_study_o"],
        "val_year_o":             row["val_year_o"],
        "val_type":               row["val_type"],
        "val_outcome":            row["val_outcome"],
        "val_outcome_quote":      row["val_outcome_quote"],
        "val_admin_approved":     bool(row["val_admin_approved"]) if row["val_admin_approved"] is not None else False,
        "val_validated_at":       row["val_validated_at"].isoformat() if row["val_validated_at"] else None,
        # Full message thread (outbound from team + validator replies)
        "messages":               thread,
    }


# ---------------------------------------------------------------------------
# Validation workflow endpoints
# ---------------------------------------------------------------------------

@app.get("/api/next-pair")
def next_pair(coder_id: int, mode: str = "normal"):
    if mode not in {"normal", "hard"}:
        raise HTTPException(400, "mode must be normal or hard")

    with db() as cur:
        # Resume check: if this validator is already mid-pair, return it immediately
        # (must run before the 30-min cleanup so we don't accidentally release their slot)
        cur.execute(
            """
            SELECT vq.queue_id, vq.record_id
            FROM validation_queue vq
            WHERE vq.validator_id = %s
              AND vq.is_shown     = TRUE
              AND vq.is_validated = FALSE
              AND vq.validator_slot IN ('human_1', 'human_2')
            LIMIT 1
            """,
            (coder_id,),
        )
        resume_slot = cur.fetchone()

        if resume_slot:
            # Reset the timer so they get a fresh 30-minute window
            cur.execute(
                "UPDATE validation_queue SET shown_at = NOW() WHERE queue_id = %s",
                (resume_slot["queue_id"],),
            )
            cur.execute(
                """
                SELECT u.record_id, u.pair_id,
                       u.doi_r, u.study_r, u.year_r, u.url_r, u.ref_r, u.abstract_r,
                       u.doi_o, u.study_o, u.year_o, u.url_o, u.ref_o,
                       u.type, u.outcome, u.outcome_quote, u.out_quote_source,
                       rm.authors_r, rm.authors_o, rm.journal_r, rm.openalex_id_r,
                       (SELECT COUNT(*) FROM validation_queue vq2
                        WHERE vq2.record_id = u.record_id AND vq2.is_validated = TRUE
                       ) AS judge_count
                FROM unvalidated u
                LEFT JOIN record_metadata rm ON rm.record_id = u.record_id
                WHERE u.record_id = %s
                """,
                (resume_slot["record_id"],),
            )
            row = cur.fetchone()
            if row:
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
                return {
                    "pair":        _enrich_pair(dict(row)),
                    "judge_count": row["judge_count"],
                    "done":        done,
                    "total":       total,
                    "resumed":     True,
                }

        # NOTE: stale-slot release is handled by the background reaper
        # (_reap_stale_slots), not inline here. Tiered locks: buffered slots
        # (started_at IS NULL) expire in 45 min, started slots in 5 days.

        # Count how many the validator has already completed
        cur.execute(
            """
            SELECT COUNT(*) AS done
            FROM validation_queue
            WHERE validator_id = %s
              AND is_validated = TRUE
              AND validator_slot IN ('human_1', 'human_2')
            """,
            (coder_id,),
        )
        done = cur.fetchone()["done"]

        cur.execute(
            "SELECT COUNT(*) AS total FROM unvalidated WHERE validation_status NOT IN ('validated', 'rejected')"
        )
        total = cur.fetchone()["total"]

        # Find a record that:
        # 1. Is not yet fully validated
        # 2. This validator has not already been assigned to
        # 3. Has a free human slot
        cur.execute(
            """
            SELECT u.record_id, u.pair_id,
                   u.doi_r, u.study_r, u.year_r, u.url_r, u.ref_r, u.abstract_r,
                   u.doi_o, u.study_o, u.year_o, u.url_o, u.ref_o,
                   u.type, u.outcome, u.outcome_quote, u.out_quote_source,
                   rm.authors_r, rm.authors_o, rm.journal_r, rm.openalex_id_r,
                   (SELECT COUNT(*) FROM validation_queue vq2
                    WHERE vq2.record_id = u.record_id AND vq2.is_validated = TRUE
                   ) AS judge_count
            FROM unvalidated u
            LEFT JOIN record_metadata rm ON rm.record_id = u.record_id
            WHERE u.validation_status IN ('unvalidated', 'validation_inprogress')
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
            (coder_id,),
        )
        row = cur.fetchone()

        if not row:
            return {"pair": None, "done": done, "total": total}

        record_id = row["record_id"]

        # Claim the first free slot atomically — FOR UPDATE SKIP LOCKED prevents
        # two concurrent requests from grabbing the same slot simultaneously.
        cur.execute(
            """
            UPDATE validation_queue
            SET is_shown = TRUE, validator_id = %s, validator_name = (
                SELECT handle FROM validators WHERE id = %s
            ), shown_at = NOW(), started_at = NOW()
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
        if not cur.fetchone():
            # Slot was claimed by a concurrent request — return nothing so
            # the client retries and gets the next available pair.
            return {"pair": None, "done": done, "total": total}

        # Update unvalidated status to inprogress
        cur.execute(
            """
            UPDATE unvalidated
            SET validation_status = 'validation_inprogress'
            WHERE record_id = %s AND validation_status = 'unvalidated'
            """,
            (record_id,),
        )

        pair = _enrich_pair(dict(row))
        return {
            "pair": pair,
            "judge_count": row["judge_count"],
            "done": done,
            "total": total,
        }


# Columns selected for a servable pair (kept in one place for reuse).
_PAIR_SELECT = """
    u.record_id, u.pair_id,
    u.doi_r, u.study_r, u.year_r, u.url_r, u.ref_r, u.abstract_r,
    u.doi_o, u.study_o, u.year_o, u.url_o, u.ref_o,
    u.type, u.outcome, u.outcome_quote, u.out_quote_source,
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


def _claim_one_pair(cur, coder_id: int, started: bool, mode: str = "normal"):
    """Claim one free human slot for this validator, within the given mode's pool.
       started=True  → active pair (5-day lock, started_at set)
       started=False → buffered prefetch (short lock, started_at NULL)
    Returns an enriched pair dict (with queue_id + judge_count) or None."""
    cur.execute(
        f"""
        SELECT {_PAIR_SELECT}
        FROM unvalidated u
        LEFT JOIN record_metadata rm ON rm.record_id = u.record_id
        WHERE u.validation_status IN ('unvalidated', 'validation_inprogress')
          AND u.restricted_access IS NOT TRUE
          AND {_mode_sql(mode)}
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
        (coder_id,),
    )
    row = cur.fetchone()
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
    pair = _enrich_pair(dict(row))
    pair["queue_id"]    = str(claimed["queue_id"])
    pair["judge_count"] = row["judge_count"]
    return pair


@app.get("/api/next-pairs")
def next_pairs(coder_id: int, count: int = 3, buffered_only: bool = False, mode: str = "normal"):
    """Batch-claim pairs for the client prefetch buffer, within a serving mode.
       Default: first pair is the active (started) one — a resumed pair if the
       validator already has one *in this mode*, else a freshly started claim —
       and the rest are buffered. buffered_only=True returns only buffered top-ups."""
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
                    pair = _enrich_pair(dict(row))
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


class StartPairRequest(BaseModel):
    coder_id: int


@app.post("/api/pairs/{queue_id}/start")
def start_pair(queue_id: str, req: StartPairRequest):
    """Promote a buffered slot to the active 'started' pair (5-day lock)."""
    with db() as cur:
        cur.execute(
            """
            UPDATE validation_queue
            SET started_at = NOW(), shown_at = NOW()
            WHERE queue_id = %s AND validator_id = %s AND is_validated = FALSE
            RETURNING queue_id
            """,
            (queue_id, req.coder_id),
        )
        if not cur.fetchone():
            raise HTTPException(404, "This pair is no longer assigned to you")
    return {"ok": True}


@app.get("/api/health")
def health():
    """Lightweight liveness check (no DB) used by the client keep-warm ping and
    any external uptime pinger to keep the instance from cold-starting."""
    return {"status": "ok"}


class RestrictedRequest(BaseModel):
    coder_id: int
    record_id: str


@app.post("/api/restricted")
def report_restricted(req: RestrictedRequest):
    """Hard-mode validator can't access the article → flag the record for the
    admin restricted-access queue and release their slot. No points awarded."""
    with db() as cur:
        cur.execute("SELECT record_id FROM unvalidated WHERE record_id = %s", (req.record_id,))
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
            (req.coder_id, req.record_id),
        )
        # Release the reporter's slot so the record isn't stuck mid-progress.
        cur.execute(
            """
            UPDATE validation_queue
            SET validator_id = NULL, validator_name = NULL,
                is_shown = FALSE, shown_at = NULL, started_at = NULL
            WHERE record_id = %s AND validator_id = %s
              AND validator_slot IN ('human_1', 'human_2') AND is_validated = FALSE
            """,
            (req.record_id, req.coder_id),
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Assignments (restricted records handed to a validator with access)
# ---------------------------------------------------------------------------

@app.get("/api/my-assignments")
def my_assignments(coder_id: int):
    """Open assignments for a validator (for the in-game Assignments panel)."""
    with db() as cur:
        cur.execute(
            """
            SELECT a.record_id, a.assigned_at, a.assigned_by,
                   u.study_r, u.doi_r, u.year_r, u.outcome
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
def get_assignment(record_id: str, coder_id: int):
    """Fetch the full pair for an assignment (must be assigned to this validator)."""
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
        pair = _enrich_pair(dict(row))
        pair["judge_count"] = row["judge_count"]
    return {"pair": pair}


@app.post("/api/assignment-judge")
def assignment_judge(req: JudgeRequest):
    """Submit an assignment validation. A single trusted validator with access
    resolves the record directly to consensus_reached (awaiting admin approval),
    earning double points. Clears the restricted flag and closes the assignment."""
    for chk in (req.type_check, req.original_check, req.outcome_check):
        if chk not in VALID_CHECKS:
            raise HTTPException(400, "checks must be 'correct' or 'incorrect'")

    with db() as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (req.record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        rec = dict(row)

        cur.execute(
            "SELECT id FROM assignments WHERE record_id = %s AND validator_id = %s AND status = 'open'",
            (req.record_id, req.coder_id),
        )
        if not cur.fetchone():
            raise HTTPException(404, "No open assignment for you on this record")

        cur.execute(
            "SELECT id, handle, vote_score, total_points FROM validators WHERE id = %s",
            (req.coder_id,),
        )
        validator = cur.fetchone()
        if not validator:
            raise HTTPException(404, "Validator not found")

        pts = _points_for(req, validator["vote_score"]) * 2   # assignments are double

        is_not_val = req.corrected_type == "not_validation"
        new_status = "rejected" if is_not_val else "consensus_reached"

        # Final values: validator's corrections, falling back to extracted.
        final_type     = req.corrected_type          or rec["type"]
        final_outcome  = req.corrected_outcome        or rec["outcome"]
        final_study_r  = req.corrected_study_r        or rec["study_r"]
        final_url_r    = req.corrected_url_r          or rec["url_r"]
        final_abstract = req.corrected_abstract       or rec["abstract_r"]
        final_doi_o    = req.corrected_doi_o          or rec["doi_o"]
        final_study_o  = req.corrected_study_o        or rec["study_o"]
        final_quote    = req.corrected_outcome_quote  or rec["outcome_quote"]

        summary = {
            "validator_id":   req.coder_id,
            "validator_name": validator["handle"],
            "is_assignment":  True,
            "type_check":     req.type_check,
            "original_check": req.original_check,
            "outcome_check":  req.outcome_check,
            "corrected_doi_o": req.corrected_doi_o,
            "corrected_study_o": req.corrected_study_o,
            "corrected_outcome": req.corrected_outcome,
            "corrected_type": req.corrected_type,
            "corrected_outcome_quote": req.corrected_outcome_quote,
            "corrected_abstract": req.corrected_abstract,
            "corrected_study_r": req.corrected_study_r,
            "corrected_url_r": req.corrected_url_r,
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
                final_study_r       = %s,
                final_url_r         = %s,
                final_abstract_r    = %s,
                final_doi_o         = %s,
                final_study_o       = %s,
                final_outcome_quote = %s,
                validator_1         = %s,
                restricted_access   = FALSE,
                updated_at          = NOW()
            WHERE record_id = %s
            """,
            (new_status, final_type, final_outcome, final_study_r, final_url_r,
             final_abstract, final_doi_o, final_study_o, final_quote,
             json.dumps(summary), req.record_id),
        )
        cur.execute(
            "UPDATE assignments SET status = 'done', completed_at = NOW() "
            "WHERE record_id = %s AND validator_id = %s",
            (req.record_id, req.coder_id),
        )
        cur.execute(
            "UPDATE validators SET total_points = total_points + %s, "
            "total_judgements = total_judgements + 1 WHERE id = %s RETURNING total_points",
            (pts, req.coder_id),
        )
        new_total = cur.fetchone()["total_points"]
    return {"points_earned": pts, "total_points": new_total}


@app.post("/api/judge")
def judge(req: JudgeRequest):
    if req.type_check not in VALID_CHECKS:
        raise HTTPException(400, "type_check must be 'correct' or 'incorrect'")
    if req.original_check not in VALID_CHECKS:
        raise HTTPException(400, "original_check must be 'correct' or 'incorrect'")
    if req.outcome_check not in VALID_CHECKS:
        raise HTTPException(400, "outcome_check must be 'correct' or 'incorrect'")

    with db() as cur:
        # Look up the record and the validator
        cur.execute(
            "SELECT record_id FROM unvalidated WHERE record_id = %s",
            (req.record_id,),
        )
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        record_id = rec["record_id"]

        cur.execute(
            "SELECT id, handle, vote_score, total_points, total_judgements, validator_tier FROM validators WHERE id = %s",
            (req.coder_id,),
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
            """,
            (record_id, req.coder_id),
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
                    corrected_study_o = %s,
                    corrected_outcome = %s,
                    corrected_type = %s,
                    corrected_outcome_quote = %s,
                    corrected_abstract = %s,
                    corrected_study_r = %s,
                    corrected_url_r = %s,
                    validator_notes = %s,
                    points = %s,
                    validated_at = NOW()
                WHERE queue_id = %s
                """,
                (
                    req.type_check,
                    req.original_check,
                    req.outcome_check,
                    req.corrected_doi_o,
                    req.corrected_study_o,
                    req.corrected_outcome,
                    req.corrected_type,
                    req.corrected_outcome_quote,
                    req.corrected_abstract,
                    req.corrected_study_r,
                    req.corrected_url_r,
                    req.validator_notes,
                    pts,
                    queue_id,
                ),
            )
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(400, "Already judged this record")

        # Build JSONB summary for unvalidated
        summary = {
            "validator_id": req.coder_id,
            "validator_name": validator["handle"],
            "validator_tier": validator["validator_tier"],
            "vote_score": validator["vote_score"],
            "type_check": req.type_check,
            "original_check": req.original_check,
            "outcome_check": req.outcome_check,
            "corrected_doi_o": req.corrected_doi_o,
            "corrected_study_o": req.corrected_study_o,
            "corrected_outcome": req.corrected_outcome,
            "corrected_type": req.corrected_type,
            "corrected_outcome_quote": req.corrected_outcome_quote,
            "corrected_abstract": req.corrected_abstract,
            "corrected_study_r": req.corrected_study_r,
            "corrected_url_r": req.corrected_url_r,
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
            (pts, req.coder_id),
        )
        new_total = cur.fetchone()["total_points"]

        # Trigger consensus engine now that a slot is complete
        from consensus_engine import evaluate_consensus
        evaluate_consensus(cur, record_id)

        cur.execute("SELECT COUNT(*) + 1 AS rank FROM validators WHERE total_points > %s", (new_total,))
        rank = cur.fetchone()["rank"]

        return {"points_earned": pts, "total_points": new_total, "rank": rank}


@app.post("/api/skip")
def skip_pair(req: SkipRequest):
    with db() as cur:
        cur.execute("SELECT record_id FROM unvalidated WHERE record_id = %s", (req.record_id,))
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(404, f"record_id '{req.record_id}' not found")
        record_id = rec["record_id"]

        # Release the queue slot so another validator can claim this pair
        cur.execute(
            """
            UPDATE validation_queue
            SET validator_id = NULL, validator_name = NULL,
                is_shown = FALSE, shown_at = NULL, started_at = NULL
            WHERE record_id = %s
              AND validator_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND is_validated = FALSE
            """,
            (record_id, req.coder_id),
        )

        # Revert status only if no slot is active (being worked on) and none is already done
        cur.execute(
            """
            UPDATE unvalidated
            SET validation_status = 'unvalidated'
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

        cur.execute(
            "UPDATE validators SET skipped_count = skipped_count + 1 WHERE id = %s",
            (req.coder_id,),
        )

    return {"skipped": True}


@app.post("/api/senior-reject")
def senior_reject(req: SeniorRejectRequest):
    """Senior validators (tier >= 2) can immediately reject a record as not a replication
    without waiting for a second validator or LLM."""
    with db() as cur:
        cur.execute(
            "SELECT id, handle, vote_score, total_points, total_judgements, validator_tier FROM validators WHERE id = %s",
            (req.coder_id,),
        )
        validator = cur.fetchone()
        if not validator:
            raise HTTPException(404, "Validator not found")
        if validator["validator_tier"] < 2:
            raise HTTPException(403, "Only senior validators (tier 2) can use this feature")

        cur.execute("SELECT record_id FROM unvalidated WHERE record_id = %s", (req.record_id,))
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
            (record_id, req.coder_id),
        )
        slot_row = cur.fetchone()
        if not slot_row:
            raise HTTPException(400, "No open slot found for this validator on this record")

        pts = validator["vote_score"]

        cur.execute(
            """
            UPDATE validation_queue SET
                is_validated   = TRUE,
                type_check     = 'incorrect',
                original_check = 'incorrect',
                outcome_check  = 'incorrect',
                corrected_type = 'not_validation',
                validator_notes = %s,
                points         = %s,
                validated_at   = NOW()
            WHERE queue_id = %s
            """,
            (req.validator_notes, pts, slot_row["queue_id"]),
        )

        summary = json.dumps({
            "validator_id":   req.coder_id,
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

        cur.execute(
            "UPDATE validators SET total_points = total_points + %s, total_judgements = total_judgements + 1 WHERE id = %s",
            (pts, req.coder_id),
        )

    return {"rejected": True, "points_earned": pts}


# ---------------------------------------------------------------------------
# Stats and leaderboard
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def stats(coder_id: int):
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
def admin_stats(x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)

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
                 WHERE fq.validator_id = v.id AND fq.flagged = TRUE) AS flagged_count
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


@app.get("/api/admin/dashboard")
def admin_dashboard(x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)

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
            "success":       outcomes_raw.get("success", 0),
            "failure":       outcomes_raw.get("failure", 0),
            "mixed":         outcomes_raw.get("mixed", 0),
            "uninformative": outcomes_raw.get("uninformative", 0),
            "descriptive":   outcomes_raw.get("descriptive", 0),
        }

        # Correction counts per field across all human-validated queue entries
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE type_check     = 'incorrect')                AS type_corrections,
                COUNT(*) FILTER (WHERE original_check = 'incorrect')                AS original_corrections,
                COUNT(*) FILTER (WHERE outcome_check  = 'incorrect')                AS outcome_corrections,
                COUNT(*) FILTER (WHERE corrected_study_r IS NOT NULL
                                   AND corrected_study_r <> '')                     AS title_corrections
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
        if r["final_doi_o"] and r["final_doi_o"] != r["doi_o"]: b_counts["original"] += 1

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
def set_site_banner(req: BannerRequest, x_admin_token: str = Header(...)):
    handle = _require_admin(x_admin_token)
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
def admin_list_validators(x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
    with db() as cur:
        cur.execute("SELECT id, handle, email FROM validators ORDER BY handle")
        rows = [dict(r) for r in cur.fetchall()]
    return {"validators": rows}


@app.get("/api/admin/restricted")
def admin_restricted(x_admin_token: str = Header(...)):
    """Records flagged 'I cannot access this article', with reporter + current
    assignment (if any), for the admin Restricted-access queue."""
    _require_admin(x_admin_token)
    with db() as cur:
        cur.execute(
            """
            SELECT u.record_id, u.study_r, u.doi_r, u.year_r, u.outcome,
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
def admin_assign(req: AssignRequest, x_admin_token: str = Header(...)):
    """Assign a restricted record to a validator (reassign replaces)."""
    admin_handle = _require_admin(x_admin_token)
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
    return {"ok": True}


@app.get("/api/admin/validators/{validator_id}/flagged")
def admin_validator_flagged(validator_id: int, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
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
def admin_set_tier(validator_id: int, req: SetTierRequest, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
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
    return {"handle": row["handle"], "validator_tier": row["validator_tier"]}


@app.get("/api/admin/admins")
def list_admins(x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
    with db() as cur:
        cur.execute("SELECT id, handle, trusted, created_at::date AS joined FROM admins ORDER BY id")
        return {"admins": [dict(r) for r in cur.fetchall()]}


class AdminCreateRequest(BaseModel):
    handle: str
    password: str


@app.post("/api/admin/admins")
def create_admin(req: AdminCreateRequest, x_admin_token: str = Header(...)):
    _require_trusted_admin(x_admin_token)
    if not req.handle or not req.password:
        raise HTTPException(400, "Handle and password are required")
    with db() as cur:
        try:
            cur.execute(
                "INSERT INTO admins (handle, password) VALUES (%s, %s) RETURNING id, handle",
                (req.handle, req.password),
            )
            row = cur.fetchone()
        except Exception:
            raise HTTPException(409, "Handle already exists")
    return {"id": row["id"], "handle": row["handle"]}


@app.delete("/api/admin/admins/{admin_id}")
def delete_admin(admin_id: int, x_admin_token: str = Header(...)):
    calling_handle = _require_trusted_admin(x_admin_token)
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
    return {"deleted": row["handle"]}


@app.post("/api/admin/admins/{admin_id}/toggle-trusted")
def toggle_admin_trusted(admin_id: int, x_admin_token: str = Header(...)):
    calling_handle = _require_trusted_admin(x_admin_token)
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
    return {"handle": row["handle"], "trusted": updated["trusted"]}


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    with db() as cur:
        cur.execute("SELECT handle, password, trusted FROM admins WHERE handle = %s", (req.handle,))
        row = cur.fetchone()
    if not row or row["password"] != req.password:
        raise HTTPException(401, "Invalid handle or password")
    return {"token": _make_token(row["password"]), "handle": row["handle"], "trusted": row["trusted"]}


# Agreement %: across V1, V2, and the LLM (when it ran without error), the share
# of the 3 checks (type/original/outcome) on which everyone gave the same answer.
# NULL when fewer than 2 validators exist. Computed in SQL so it's sortable.
def _agree_field(f):
    return (
        "(SELECT COUNT(DISTINCT v) FROM (VALUES "
        f"(u.validator_1->>'{f}'), (u.validator_2->>'{f}'), "
        f"(CASE WHEN jsonb_exists(u.llm_validator, 'error') THEN NULL ELSE u.llm_validator->>'{f}' END)"
        ") AS t(v) WHERE v IS NOT NULL) <= 1"
    )

_AGREEMENT_VOTERS = (
    "((u.validator_1 IS NOT NULL)::int + (u.validator_2 IS NOT NULL)::int "
    "+ (u.llm_validator IS NOT NULL AND NOT jsonb_exists(u.llm_validator, 'error'))::int)"
)
_AGREEMENT_SQL = (
    f"(CASE WHEN {_AGREEMENT_VOTERS} >= 2 THEN round(100.0 * ("
    f"(CASE WHEN {_agree_field('type_check')} THEN 1 ELSE 0 END) + "
    f"(CASE WHEN {_agree_field('original_check')} THEN 1 ELSE 0 END) + "
    f"(CASE WHEN {_agree_field('outcome_check')} THEN 1 ELSE 0 END)"
    ") / 3.0)::int ELSE NULL END)"
)

# Whitelist of sortable columns → safe SQL expression (never interpolate raw input).
_ENTRIES_SORT = {
    "study":       "u.study_r",
    "type":        "COALESCE(u.final_type, u.type)",
    "outcome":     "COALESCE(u.final_outcome, u.outcome)",
    "status":      "u.validation_status",
    "validators":  "(SELECT COUNT(*) FROM validation_queue vq WHERE vq.record_id = u.record_id AND vq.is_validated = TRUE)",
    "agreement":   _AGREEMENT_SQL,
    "approved_by": "u.admin_name",
}


@app.get("/api/admin/entries")
def admin_entries(
    filter: str = "all",
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    sort: str = "",
    dir: str = "desc",
    x_admin_token: str = Header(...),
):
    _require_admin(x_admin_token)

    sort_col = _ENTRIES_SORT.get(sort)
    direction = "ASC" if str(dir).lower() == "asc" else "DESC"
    if sort_col:
        order_by = f"ORDER BY {sort_col} {direction} NULLS LAST, u.updated_at DESC"
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
        "llm_errors":       "WHERE u.llm_validator IS NOT NULL AND (u.llm_validator)::jsonb ? 'error'",
        "validated":        "WHERE u.validation_status = 'validated'",
        "rejected":         "WHERE u.validation_status = 'rejected'",
        "admin_checked":    "WHERE u.admin_checked = TRUE",
    }.get(filter, "")

    search = search.strip()
    if search:
        connector = "AND" if base_where else "WHERE"
        where = f"{base_where} {connector} (u.study_r ILIKE %s OR u.doi_r ILIKE %s)"
        search_param = f"%{search}%"
        search_args = (search_param, search_param)
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
                {_AGREEMENT_SQL} AS agreement_pct,
                (SELECT COUNT(*) FROM validation_queue vq
                 WHERE vq.record_id = u.record_id AND vq.is_validated = TRUE) AS validator_count,
                (SELECT COUNT(*) FROM validation_queue vq
                 JOIN validators tv ON tv.id = vq.validator_id AND tv.validator_tier >= 1
                 WHERE vq.record_id = u.record_id
                   AND vq.is_validated = TRUE
                   AND vq.validator_slot IN ('human_1', 'human_2')) AS trusted_validator_count
            FROM unvalidated u
            {where}
            {order_by}
            LIMIT %s OFFSET %s
            """,
            search_args + (per_page, offset),
        )
        entries = [dict(r) for r in cur.fetchall()]

        # Count badges for each filter tab
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated")
        c_all = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'consensus_reached'")
        c_pending = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'need_review'")
        c_review = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE llm_validator IS NOT NULL AND (llm_validator)::jsonb ? 'error'")
        c_llm = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'validated'")
        c_validated = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE admin_checked = TRUE")
        c_admin = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE validation_status = 'rejected'")
        c_rejected = cur.fetchone()["n"]

    return {
        "entries": entries,
        "total": total,
        "page": page,
        "per_page": per_page,
        "counts": {
            "all": c_all,
            "pending_approval": c_pending,
            "needs_review": c_review,
            "llm_errors": c_llm,
            "validated": c_validated,
            "rejected": c_rejected,
            "admin_checked": c_admin,
        },
    }


@app.get("/api/admin/entries/{record_id}")
def admin_entry_detail(record_id: str, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)

    with db() as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Record not found")

        record = _enrich_pair(dict(row))
        record["record_id"] = str(record["record_id"])

        # psycopg2 already deserialises JSONB to dicts; guard for string fallback
        for field in ("validator_1", "validator_2", "llm_validator"):
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

    # Detect abstract-only conflict
    import re as _re
    def _norm(t): return _re.sub(r'[^a-z0-9]', '', (t or "").lower())

    v1, v2 = record.get("validator_1") or {}, record.get("validator_2") or {}
    correction_fields = ["corrected_doi_o", "corrected_study_o", "corrected_outcome", "corrected_type", "corrected_study_r", "corrected_url_r"]
    check_fields      = ["type_check", "original_check", "outcome_check"]

    checks_agree      = all(v1.get(f) == v2.get(f) for f in check_fields)
    corrections_agree = all(v1.get(f) == v2.get(f) for f in correction_fields)
    abstracts_differ  = _norm(v1.get("corrected_abstract")) != _norm(v2.get("corrected_abstract"))

    abstract_only_conflict = (
        record.get("validation_status") == "need_review"
        and checks_agree
        and corrections_agree
        and abstracts_differ
        and bool(v1) and bool(v2)
    )

    return {"record": record, "queue_slots": queue_slots, "abstract_only_conflict": abstract_only_conflict}


@app.post("/api/admin/queue/{queue_id}/flag")
def admin_flag_queue(queue_id: str, req: FlagQueueRequest | None = None, x_admin_token: str = Header(...)):
    admin_handle = _require_admin(x_admin_token)
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
                cur.execute("SELECT study_r, doi_r FROM unvalidated WHERE record_id = %s", (row["record_id"],))
                paper = cur.fetchone()
                if paper:
                    if paper["study_r"]:
                        paper_lines += f"\nStudy: {paper['study_r']}"
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
def get_validator_messages(coder_id: int):
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
def mark_message_read(msg_id: int, coder_id: int):
    with db() as cur:
        cur.execute(
            "UPDATE validator_messages SET is_read = TRUE WHERE id = %s AND validator_id = %s",
            (msg_id, coder_id),
        )
    return {"ok": True}


@app.post("/api/messages/{parent_id}/reply")
def reply_to_message(parent_id: int, req: ReplyRequest):
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
        if parent["validator_id"] != req.coder_id:
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
            (req.coder_id, subject, body_text, parent_id, parent["queue_id"]),
        )
        new_id = cur.fetchone()["id"]
    return {"ok": True, "id": new_id}


@app.get("/api/admin/messages")
def list_admin_conversations(x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
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
                COALESCE(u.study_r, r.subject)     AS subject,
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
def get_admin_thread(thread_id: int, mark_read: bool = False, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
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
                   COALESCE(u.study_r, vm.subject) AS subject,
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
def admin_reply_to_thread(thread_id: int, req: AdminReplyRequest, x_admin_token: str = Header(...)):
    admin_handle = _require_admin(x_admin_token)
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
def get_admin_conversation(validator_id: int, mark_read: bool = False, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
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
def admin_send_message(req: AdminMessageRequest, x_admin_token: str = Header(...)):
    admin_handle = _require_admin(x_admin_token)
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
def admin_approve(record_id: str, x_admin_token: str = Header(...)):
    admin_handle = _require_admin(x_admin_token)

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

        cur.execute(
            """
            INSERT INTO validated (
                record_id, doi_r, study_r, year_r, url_r, ref_r, abstract_r,
                doi_o, study_o, year_o, url_o, ref_o,
                type, outcome, outcome_quote, out_quote_source, admin_approved
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE)
            ON CONFLICT (doi_r, study_r, doi_o, study_o) DO UPDATE SET
                admin_approved = TRUE,
                validated_at   = NOW()
            """,
            (
                record_id,
                rec["doi_r"], rec.get("final_study_r") or rec["study_r"], rec["year_r"], rec.get("final_url_r") or rec["url_r"], rec["ref_r"], rec.get("final_abstract_r") or rec["abstract_r"],
                rec.get("final_doi_o") or rec["doi_o"],
                rec.get("final_study_o") or rec["study_o"],
                rec["year_o"], rec["url_o"], rec["ref_o"],
                rec.get("final_type") or rec["type"],
                rec.get("final_outcome") or rec["outcome"],
                rec.get("final_outcome_quote") or rec["outcome_quote"], rec.get("out_quote_source"),
            ),
        )

    return {"approved": True, "record_id": record_id}


@app.post("/api/admin/entries/{record_id}/restore")
def admin_restore(record_id: str, x_admin_token: str = Header(...)):
    """Return a rejected record to the validation pool so two fresh validators can review it."""
    _require_admin(x_admin_token)
    with db() as cur:
        cur.execute("SELECT validation_status FROM unvalidated WHERE record_id = %s", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        if row["validation_status"] != "rejected":
            raise HTTPException(400, "Only rejected records can be restored to the pool")

        # Release all queue slots so fresh validators can claim them
        cur.execute(
            """
            UPDATE validation_queue
            SET validator_id = NULL, validator_name = NULL,
                is_shown = FALSE, shown_at = NULL,
                is_validated = FALSE,
                type_check = NULL, original_check = NULL, outcome_check = NULL,
                corrected_type = NULL, corrected_doi_o = NULL, corrected_study_o = NULL,
                corrected_outcome = NULL, corrected_study_r = NULL,
                validator_notes = NULL, points = NULL, validated_at = NULL
            WHERE record_id = %s AND validator_slot IN ('human_1', 'human_2')
            """,
            (record_id,),
        )

        cur.execute(
            """
            UPDATE unvalidated SET
                validation_status = 'unvalidated',
                validator_1 = NULL, validator_2 = NULL,
                llm_validator = NULL,
                updated_at = NOW()
            WHERE record_id = %s
            """,
            (record_id,),
        )

    return {"restored": True, "record_id": record_id}


@app.post("/api/admin/entries/{record_id}/flag-review")
def admin_flag_review(record_id: str, req: dict = Body(default={}), x_admin_token: str = Header(...)):
    """Move a consensus_reached record back to need_review for further scrutiny."""
    admin_handle = _require_admin(x_admin_token)
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
def admin_save_note(record_id: str, req: AdminNoteRequest, x_admin_token: str = Header(...)):
    """Save or update a persistent admin note on an entry. Visible to all admins."""
    admin_handle = _require_admin(x_admin_token)
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


@app.post("/api/admin/entries/{record_id}/resolve")
def admin_resolve(record_id: str, req: AdminResolveRequest, x_admin_token: str = Header(...)):
    admin_handle = _require_admin(x_admin_token)

    if req.type_check not in VALID_CHECKS:
        raise HTTPException(400, "type_check must be 'correct' or 'incorrect'")
    if req.original_check not in VALID_CHECKS:
        raise HTTPException(400, "original_check must be 'correct' or 'incorrect'")
    if req.outcome_check not in VALID_CHECKS:
        raise HTTPException(400, "outcome_check must be 'correct' or 'incorrect'")

    with db() as cur:
        cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        rec = dict(row)

        # Only enforce 2-validator requirement for records still in progress
        if rec["validation_status"] not in ("need_review", "consensus_reached", "rejected"):
            cur.execute(
                "SELECT COUNT(*) AS n FROM validation_queue WHERE record_id = %s AND is_validated = TRUE",
                (record_id,),
            )
            if cur.fetchone()["n"] < 2:
                raise HTTPException(400, "Cannot resolve a record with fewer than 2 validator submissions")

        final_type      = req.corrected_type      if req.type_check     == "incorrect" and req.corrected_type      else rec["type"]
        final_doi_o     = req.corrected_doi_o     if req.original_check == "incorrect" and req.corrected_doi_o     else rec["doi_o"]
        final_study_o   = req.corrected_study_o   if req.original_check == "incorrect" and req.corrected_study_o   else rec["study_o"]
        final_outcome   = req.corrected_outcome   if req.outcome_check  == "incorrect" and req.corrected_outcome   else rec["outcome"]
        final_outcome_q = req.corrected_outcome_quote if req.corrected_outcome_quote else rec["outcome_quote"]
        final_study_r    = req.corrected_study_r    if req.corrected_study_r    else rec.get("final_study_r")    or rec["study_r"]
        final_doi_r      = req.corrected_doi_r      if req.corrected_doi_r      else rec.get("final_doi_r")      or rec["doi_r"]
        final_url_r      = req.corrected_url_r      if req.corrected_url_r      else rec.get("final_url_r")      or rec["url_r"]
        final_abstract_r = req.corrected_abstract_r if req.corrected_abstract_r else rec.get("final_abstract_r") or rec["abstract_r"]

        # Admin confirmed this is not a replication → reject it, never insert into FLoRA
        if final_type == "not_validation":
            cur.execute(
                """
                UPDATE unvalidated SET
                    admin_checked       = TRUE,
                    admin_name          = %s,
                    admin_notes         = %s,
                    validation_status   = 'rejected',
                    updated_at          = NOW()
                WHERE record_id = %s
                """,
                (admin_handle, req.admin_notes, record_id),
            )
            return {"resolved": True, "rejected": True, "record_id": record_id}

        was_rejected = rec["validation_status"] == "rejected"
        cur.execute(
            """
            UPDATE unvalidated SET
                admin_checked       = TRUE,
                admin_name          = %s,
                admin_notes         = %s,
                validation_status   = 'validated',
                final_type          = %s,
                final_doi_o         = %s,
                final_study_o       = %s,
                final_outcome       = %s,
                final_outcome_quote = %s,
                final_study_r       = %s,
                final_doi_r         = %s,
                final_url_r         = %s,
                final_abstract_r    = %s,
                admin_override      = %s,
                updated_at          = NOW()
            WHERE record_id = %s
            """,
            (admin_handle, req.admin_notes, final_type, final_doi_o, final_study_o, final_outcome, final_outcome_q, final_study_r, final_doi_r, final_url_r, final_abstract_r, was_rejected, record_id),
        )

        cur.execute(
            """
            INSERT INTO validated (
                record_id, doi_r, study_r, year_r, url_r, ref_r, abstract_r,
                doi_o, study_o, year_o, url_o, ref_o,
                type, outcome, outcome_quote, out_quote_source, admin_approved
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE)
            ON CONFLICT (doi_r, study_r, doi_o, study_o) DO UPDATE SET
                doi_r         = EXCLUDED.doi_r,
                study_r       = EXCLUDED.study_r,
                abstract_r    = EXCLUDED.abstract_r,
                doi_o         = EXCLUDED.doi_o,
                study_o       = EXCLUDED.study_o,
                type          = EXCLUDED.type,
                outcome       = EXCLUDED.outcome,
                outcome_quote = EXCLUDED.outcome_quote,
                admin_approved = TRUE,
                validated_at  = NOW()
            """,
            (
                record_id,
                final_doi_r, final_study_r, rec["year_r"], final_url_r, rec["ref_r"], final_abstract_r,
                final_doi_o, final_study_o, rec["year_o"], rec["url_o"], rec["ref_o"],
                final_type, final_outcome, final_outcome_q, rec.get("out_quote_source"),
            ),
        )

    return {"resolved": True, "rejected": False, "record_id": record_id}


# ---------------------------------------------------------------------------
# Nightly CSV sync scheduler
# ---------------------------------------------------------------------------

def _retry_tiebreakers() -> None:
    """Re-run consensus on need_review tiebreaker records (LLM may have failed earlier)."""
    from consensus_engine import evaluate_consensus
    try:
        with db() as cur:
            cur.execute("""
                SELECT record_id FROM unvalidated
                WHERE validation_status = 'need_review' AND is_tiebreaker = TRUE
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
            cur.execute(
                """
                UPDATE validation_queue
                SET validator_id = NULL, validator_name = NULL,
                    is_shown = FALSE, shown_at = NULL, started_at = NULL
                WHERE is_validated = FALSE AND is_shown = TRUE
                  AND validator_slot IN ('human_1', 'human_2')
                  AND (
                        (started_at IS NULL     AND shown_at   < NOW() - INTERVAL '45 minutes')
                     OR (started_at IS NOT NULL AND started_at < NOW() - INTERVAL '5 days')
                  )
                """
            )
            if cur.rowcount:
                cur.execute(
                    """
                    UPDATE unvalidated u
                    SET validation_status = 'unvalidated'
                    WHERE validation_status = 'validation_inprogress'
                      AND NOT EXISTS (
                          SELECT 1 FROM validation_queue vq
                          WHERE vq.record_id = u.record_id
                            AND vq.validator_id IS NOT NULL
                            AND vq.is_validated = FALSE
                      )
                    """
                )
                print(f"[reaper] released {cur.rowcount} stale slot(s)")
    except Exception:
        import traceback
        print("[reaper] ERROR:")
        traceback.print_exc()


def _start_scheduler() -> None:
    from sync_csv import sync_once
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(sync_once, CronTrigger(hour=2, minute=0))
    scheduler.add_job(_retry_tiebreakers, CronTrigger(hour=00, minute=22))
    scheduler.add_job(_reap_stale_slots, IntervalTrigger(minutes=2))
    scheduler.start()


_start_scheduler()


# ---------------------------------------------------------------------------
# Static files (frontend)
# ---------------------------------------------------------------------------

DOCS = ROOT / "docs"
app.mount("/", StaticFiles(directory=str(DOCS), html=True), name="docs")
