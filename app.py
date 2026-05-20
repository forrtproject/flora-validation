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
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
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


def _admin_token() -> str:
    return hashlib.sha256(f"{ADMIN_PASSWORD}:flora-admin-v1".encode()).hexdigest()


def _require_admin(token: str):
    if token != _admin_token():
        raise HTTPException(401, "Unauthorized")

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
    pair_id: str                        # looks up unvalidated.pair_id
    type_check: str                     # "correct" | "incorrect"
    original_check: str                 # "correct" | "incorrect"
    outcome_check: str                  # "correct" | "incorrect"
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
    pair_id: str


class ForgotHandleRequest(BaseModel):
    email: str


class AdminLoginRequest(BaseModel):
    password: str


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


def _rank_for(cur, validator_id: int) -> int:
    """Return 1-based rank among all validators by total_points."""
    cur.execute(
        "SELECT total_points FROM validators WHERE id = %s",
        (validator_id,),
    )
    row = cur.fetchone()
    my_points = row["total_points"] if row else 0
    cur.execute(
        "SELECT COUNT(*) + 1 AS rank FROM validators WHERE total_points > %s",
        (my_points,),
    )
    return cur.fetchone()["rank"]


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
                "SELECT id, code, email, handle, onboarded_at FROM validators WHERE email = %s",
                (email,),
            )
        else:
            cur.execute(
                "SELECT id, code, email, handle, onboarded_at FROM validators WHERE code = %s",
                (code,),
            )
        existing = cur.fetchone()

        if existing:
            if existing["handle"] != handle:
                method = "email" if use_email else "code"
                raise HTTPException(
                    400,
                    f"This {method} is already registered. Please use the correct handle.",
                )
            return {
                "coder_id": existing["id"],
                "code": existing["code"],
                "email": existing["email"],
                "handle": existing["handle"],
                "onboarded": bool(existing["onboarded_at"]),
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
            "UPDATE validators SET onboarded_at = NOW() WHERE id = %s AND onboarded_at IS NULL",
            (req.coder_id,),
        )
        if cur.rowcount == 0:
            cur.execute("SELECT onboarded_at FROM validators WHERE id = %s", (req.coder_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Validator not found")
    return {"onboarded": True}


# ---------------------------------------------------------------------------
# Validation workflow endpoints
# ---------------------------------------------------------------------------

@app.get("/api/next-pair")
def next_pair(coder_id: int, mode: str = "normal"):
    if mode not in {"normal", "hard"}:
        raise HTTPException(400, "mode must be normal or hard")

    with db() as cur:
        # Release any slots shown more than 30 minutes ago without a submission
        cur.execute(
            """
            UPDATE validation_queue
            SET validator_id = NULL, validator_name = NULL,
                is_shown = FALSE, shown_at = NULL
            WHERE is_validated = FALSE
              AND is_shown = TRUE
              AND shown_at < NOW() - INTERVAL '30 minutes'
              AND validator_slot IN ('human_1', 'human_2')
            """
        )
        if cur.rowcount:
            # Reset unvalidated status for any records that now have no active slots
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
            "SELECT COUNT(*) AS total FROM unvalidated WHERE validation_status != 'validated'"
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

        # Assign this validator to the first free human slot
        cur.execute(
            """
            UPDATE validation_queue
            SET is_shown = TRUE, validator_id = %s, validator_name = (
                SELECT handle FROM validators WHERE id = %s
            ), shown_at = NOW()
            WHERE queue_id = (
                SELECT queue_id FROM validation_queue
                WHERE record_id = %s
                  AND validator_slot IN ('human_1', 'human_2')
                  AND validator_id IS NULL
                ORDER BY validator_slot
                LIMIT 1
            )
            """,
            (coder_id, coder_id, record_id),
        )

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
            "SELECT record_id FROM unvalidated WHERE pair_id = %s",
            (req.pair_id,),
        )
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(404, f"pair_id '{req.pair_id}' not found")
        record_id = rec["record_id"]

        cur.execute(
            "SELECT id, handle, vote_score, total_points, total_judgements FROM validators WHERE id = %s",
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
            "validator_notes": req.validator_notes or "",
            "points": pts,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }

        jsonb_col = "validator_1" if validator_slot == "human_1" else "validator_2"
        cur.execute(
            f"UPDATE unvalidated SET {jsonb_col} = %s WHERE record_id = %s",
            (json.dumps(summary), record_id),
        )

        # Update validator totals
        new_total = validator["total_points"] + pts
        new_judgements = validator["total_judgements"] + 1
        cur.execute(
            "UPDATE validators SET total_points = %s, total_judgements = %s WHERE id = %s",
            (new_total, new_judgements, req.coder_id),
        )

        # Trigger consensus engine now that a slot is complete
        from consensus_engine import evaluate_consensus
        evaluate_consensus(cur, record_id)

        cur.execute("SELECT COUNT(*) + 1 AS rank FROM validators WHERE total_points > %s", (new_total,))
        rank = cur.fetchone()["rank"]

        return {"points_earned": pts, "total_points": new_total, "rank": rank}


@app.post("/api/skip")
def skip_pair(req: SkipRequest):
    with db() as cur:
        cur.execute("SELECT record_id FROM unvalidated WHERE pair_id = %s", (req.pair_id,))
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(404, f"pair_id '{req.pair_id}' not found")
        record_id = rec["record_id"]

        # Release the queue slot so another validator can claim this pair
        cur.execute(
            """
            UPDATE validation_queue
            SET validator_id = NULL, validator_name = NULL,
                is_shown = FALSE, shown_at = NULL
            WHERE record_id = %s
              AND validator_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND is_validated = FALSE
            """,
            (record_id, req.coder_id),
        )

        # If no validated slots remain, revert status to unvalidated
        cur.execute(
            """
            UPDATE unvalidated
            SET validation_status = 'unvalidated'
            WHERE record_id = %s
              AND validation_status = 'validation_inprogress'
              AND NOT EXISTS (
                  SELECT 1 FROM validation_queue
                  WHERE record_id = %s
                    AND validator_id IS NOT NULL
                    AND is_validated = FALSE
              )
            """,
            (record_id, record_id),
        )

        cur.execute(
            "UPDATE validators SET skipped_count = skipped_count + 1 WHERE id = %s",
            (req.coder_id,),
        )

    return {"skipped": True}


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
            "SELECT COUNT(*) AS total FROM unvalidated WHERE validation_status != 'validated'"
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
            raise HTTPException(429, "Maximum 2 reminder emails per day. Please try again tomorrow.")

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
                v.trusted,
                v.total_judgements,
                v.total_points,
                v.created_at::date AS joined,
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
                )::numeric, 1)     AS max_min
            FROM validators v
            LEFT JOIN validation_queue vq
                ON  vq.validator_id   = v.id
                AND vq.is_validated   = TRUE
                AND vq.validator_slot IN ('human_1', 'human_2')
                AND vq.shown_at       IS NOT NULL
                AND vq.validated_at   IS NOT NULL
                AND EXTRACT(EPOCH FROM (vq.validated_at - vq.shown_at)) BETWEEN 10 AND 5400
            GROUP BY v.id, v.handle, v.total_judgements, v.total_points, v.created_at
            ORDER BY v.trusted DESC, v.total_judgements DESC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if r["joined"]:
                r["joined"] = str(r["joined"])

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


@app.post("/api/admin/validators/{validator_id}/toggle-trust")
def admin_toggle_trust(validator_id: int, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
    with db() as cur:
        cur.execute(
            "UPDATE validators SET trusted = NOT trusted WHERE id = %s RETURNING handle, trusted",
            (validator_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Validator not found")
    return {"handle": row["handle"], "trusted": row["trusted"]}


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(401, "Invalid admin password")
    return {"token": _admin_token()}


@app.get("/api/admin/entries")
def admin_entries(
    filter: str = "all",
    page: int = 1,
    per_page: int = 50,
    x_admin_token: str = Header(...),
):
    _require_admin(x_admin_token)

    where = {
        "all":              "",
        "pending_approval": "WHERE u.validation_status = 'consensus_reached'",
        "needs_review":     "WHERE u.validation_status = 'need_review'",
        "llm_errors":       "WHERE u.llm_validator IS NOT NULL AND (u.llm_validator)::jsonb ? 'error'",
        "validated":        "WHERE u.validation_status = 'validated'",
        "admin_checked":    "WHERE u.admin_checked = TRUE",
    }.get(filter, "")

    offset = (page - 1) * per_page

    with db() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM unvalidated u {where}")
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
                u.validation_status,
                u.is_tiebreaker,
                u.admin_checked,
                u.admin_name,
                (u.validator_1 IS NOT NULL)::boolean AS has_v1,
                (u.validator_2 IS NOT NULL)::boolean AS has_v2,
                (u.llm_validator IS NOT NULL)::boolean AS has_llm,
                (u.llm_validator IS NOT NULL AND (u.llm_validator)::jsonb ? 'error')::boolean AS has_llm_error,
                (SELECT COUNT(*) FROM validation_queue vq
                 WHERE vq.record_id = u.record_id AND vq.is_validated = TRUE) AS validator_count,
                (SELECT COUNT(*) FROM validation_queue vq
                 JOIN validators tv ON tv.id = vq.validator_id AND tv.trusted = TRUE
                 WHERE vq.record_id = u.record_id
                   AND vq.is_validated = TRUE
                   AND vq.validator_slot IN ('human_1', 'human_2')) AS trusted_validator_count
            FROM unvalidated u
            {where}
            ORDER BY
                CASE u.validation_status
                    WHEN 'need_review'          THEN 0
                    WHEN 'validation_inprogress' THEN 1
                    WHEN 'unvalidated'           THEN 2
                    WHEN 'validated'             THEN 3
                END,
                u.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (per_page, offset),
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
    correction_fields = ["corrected_doi_o", "corrected_study_o", "corrected_outcome", "corrected_type"]
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


@app.post("/api/admin/entries/{record_id}/approve")
def admin_approve(record_id: str, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)

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
                admin_name        = 'admin',
                updated_at        = NOW()
            WHERE record_id = %s
            """,
            (record_id,),
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
                rec["doi_r"], rec["study_r"], rec["year_r"], rec["url_r"], rec["ref_r"], rec["abstract_r"],
                rec.get("final_doi_o") or rec["doi_o"],
                rec.get("final_study_o") or rec["study_o"],
                rec["year_o"], rec["url_o"], rec["ref_o"],
                rec.get("final_type") or rec["type"],
                rec.get("final_outcome") or rec["outcome"],
                rec["outcome_quote"], rec.get("out_quote_source"),
            ),
        )

    return {"approved": True, "record_id": record_id}


@app.post("/api/admin/entries/{record_id}/resolve")
def admin_resolve(record_id: str, req: AdminResolveRequest, x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)

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

        final_type      = req.corrected_type      if req.type_check     == "incorrect" and req.corrected_type      else rec["type"]
        final_doi_o     = req.corrected_doi_o     if req.original_check == "incorrect" and req.corrected_doi_o     else rec["doi_o"]
        final_study_o   = req.corrected_study_o   if req.original_check == "incorrect" and req.corrected_study_o   else rec["study_o"]
        final_outcome   = req.corrected_outcome   if req.outcome_check  == "incorrect" and req.corrected_outcome   else rec["outcome"]
        final_outcome_q = req.corrected_outcome_quote if req.corrected_outcome_quote else rec["outcome_quote"]

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
                updated_at          = NOW()
            WHERE record_id = %s
            """,
            (req.admin_name, req.admin_notes, final_type, final_doi_o, final_study_o, final_outcome, record_id),
        )

        cur.execute(
            """
            INSERT INTO validated (
                record_id, doi_r, study_r, year_r, url_r, ref_r, abstract_r,
                doi_o, study_o, year_o, url_o, ref_o,
                type, outcome, outcome_quote, out_quote_source, admin_approved
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE)
            ON CONFLICT (doi_r, study_r, doi_o, study_o) DO UPDATE SET
                type          = EXCLUDED.type,
                outcome       = EXCLUDED.outcome,
                outcome_quote = EXCLUDED.outcome_quote,
                admin_approved = TRUE,
                validated_at  = NOW()
            """,
            (
                record_id,
                rec["doi_r"], rec["study_r"], rec["year_r"], rec["url_r"], rec["ref_r"], rec["abstract_r"],
                final_doi_o, final_study_o, rec["year_o"], rec["url_o"], rec["ref_o"],
                final_type, final_outcome, final_outcome_q, rec.get("out_quote_source"),
            ),
        )

    return {"resolved": True, "record_id": record_id}


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


def _start_scheduler() -> None:
    from sync_csv import sync_once
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(sync_once, CronTrigger(hour=2, minute=0))
    scheduler.add_job(_retry_tiebreakers, CronTrigger(hour=12, minute=22))
    scheduler.start()


_start_scheduler()


# ---------------------------------------------------------------------------
# Static files (frontend)
# ---------------------------------------------------------------------------

DOCS = ROOT / "docs"
app.mount("/", StaticFiles(directory=str(DOCS), html=True), name="docs")
