import csv
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import psycopg2.errors
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

load_dotenv()
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "extracted.csv"
ONBOARDING_PATH = ROOT / "onboarding.json"
OA_CACHE_PATH = ROOT / "oa_cache.json"

VALID_TYPES = {"replication", "reproduction", "not_validation", "skip"}
CONFIRMING_TYPES = {"replication", "reproduction"}

DATABASE_URL = os.environ["DATABASE_URL"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(title="Flora Validator")


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
    with db() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pairs (
                pair_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                is_hard INTEGER NOT NULL DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS coders (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE,
                email TEXT UNIQUE,
                handle TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                onboarded_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS judgements (
                id SERIAL PRIMARY KEY,
                coder_id INTEGER NOT NULL,
                pair_id TEXT NOT NULL,
                type_judgement TEXT NOT NULL,
                original_judgement TEXT,
                outcome_judgement TEXT,
                comment TEXT,
                edited_abstract TEXT,
                edited_outcome_quote TEXT,
                hard_mode INTEGER NOT NULL DEFAULT 0,
                hard_mode_entry_json TEXT,
                points INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(coder_id, pair_id)
            )
        """)
        cur.execute("SELECT COUNT(*) AS n FROM pairs")
        if cur.fetchone()["n"] == 0:
            with open(CSV_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    is_hard = 0 if (row.get("abstract_r") or "").strip() else 1
                    cur.execute(
                        "INSERT INTO pairs(pair_id, data_json, is_hard) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (row["pair_id"], json.dumps(row), is_hard),
                    )


init_db()


def migrate_db():
    with db() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'coders' AND column_name = 'email'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE coders ADD COLUMN email TEXT")
            cur.execute("""
                CREATE UNIQUE INDEX coders_email_key ON coders(email)
                WHERE email IS NOT NULL
            """)
        cur.execute("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'coders' AND column_name = 'code'
        """)
        row = cur.fetchone()
        if row and row["is_nullable"] == "NO":
            cur.execute("ALTER TABLE coders ALTER COLUMN code DROP NOT NULL")


migrate_db()


def load_onboarding():
    with open(ONBOARDING_PATH) as f:
        return json.load(f)["pairs"]


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


def with_oa(pair: dict) -> dict:
    pair["oa_url_r"] = oa_url_for(pair.get("doi_r"))
    pair["oa_url_o"] = oa_url_for(pair.get("doi_o"))
    return pair


class LoginRequest(BaseModel):
    handle: str
    code: str | None = None
    email: str | None = None


class JudgeRequest(BaseModel):
    coder_id: int
    pair_id: str
    type_judgement: str
    original_judgement: str | None = None
    outcome_judgement: str | None = None
    comment: str | None = None
    edited_abstract: str | None = None
    edited_outcome_quote: str | None = None
    hard_mode: bool = False
    hard_mode_entry: dict | None = None


HANDLE_RE = re.compile(r"^[A-Za-z0-9._\-]{2,32}$")


def points_for(req: JudgeRequest) -> int:
    if req.type_judgement == "skip":
        return 0
    if req.hard_mode:
        pts = 25
        if req.comment and req.comment.strip():
            pts += 3
        return pts
    if req.type_judgement == "not_validation":
        return 10
    pts = 10
    if req.original_judgement:
        pts += 5
    if req.outcome_judgement:
        pts += 5
    if req.comment and req.comment.strip():
        pts += 3
    if (req.edited_abstract and req.edited_abstract.strip()) or (
        req.edited_outcome_quote and req.edited_outcome_quote.strip()
    ):
        pts += 2
    return pts


def rank_for(cur, points: int) -> int:
    cur.execute(
        """
        SELECT COUNT(*) + 1 AS rank FROM (
            SELECT coder_id
            FROM judgements
            WHERE type_judgement != 'skip'
            GROUP BY coder_id
            HAVING SUM(points) > %s
        ) sub
        """,
        (points,),
    )
    return cur.fetchone()["rank"]


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
                "SELECT id, code, email, handle, onboarded_at FROM coders WHERE email = %s",
                (email,),
            )
        else:
            cur.execute(
                "SELECT id, code, email, handle, onboarded_at FROM coders WHERE code = %s",
                (code,),
            )
        existing = cur.fetchone()
        if existing:
            if existing["handle"] != handle:
                method = "email" if use_email else "code"
                raise HTTPException(
                    400,
                    f"This {method} is already linked to handle '{existing['handle']}'. Use that handle.",
                )
            return {
                "coder_id": existing["id"],
                "code": existing["code"],
                "email": existing["email"],
                "handle": existing["handle"],
                "onboarded": bool(existing["onboarded_at"]),
            }
        cur.execute("SELECT 1 FROM coders WHERE handle = %s", (handle,))
        if cur.fetchone():
            raise HTTPException(400, "That handle is already taken.")
        if use_email:
            cur.execute(
                "INSERT INTO coders(email, handle, created_at) VALUES (%s, %s, %s) RETURNING id",
                (email, handle, datetime.now(timezone.utc).isoformat()),
            )
        else:
            cur.execute(
                "INSERT INTO coders(code, handle, created_at) VALUES (%s, %s, %s) RETURNING id",
                (code, handle, datetime.now(timezone.utc).isoformat()),
            )
        new_id = cur.fetchone()["id"]
        return {
            "coder_id": new_id,
            "code": req.code,
            "email": email if use_email else None,
            "handle": handle,
            "onboarded": False,
        }


@app.get("/api/next-pair")
def next_pair(coder_id: int, mode: str = "normal"):
    if mode not in {"normal", "hard"}:
        raise HTTPException(400, "mode must be normal or hard")
    is_hard = 1 if mode == "hard" else 0
    with db() as cur:
        cur.execute(
            """
            SELECT p.pair_id, p.data_json,
                   (SELECT COUNT(*) FROM judgements j2 WHERE j2.pair_id = p.pair_id) AS judge_count
            FROM pairs p
            WHERE p.is_hard = %s
              AND p.pair_id NOT IN (SELECT pair_id FROM judgements WHERE coder_id = %s)
            ORDER BY judge_count ASC, RANDOM()
            LIMIT 1
            """,
            (is_hard, coder_id),
        )
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM pairs WHERE is_hard = %s", (is_hard,))
        total = cur.fetchone()["n"]
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM judgements j
            JOIN pairs p ON p.pair_id = j.pair_id
            WHERE j.coder_id = %s AND p.is_hard = %s
            """,
            (coder_id, is_hard),
        )
        done = cur.fetchone()["n"]
        if not row:
            return {"pair": None, "done": done, "total": total}
        return {
            "pair": with_oa(json.loads(row["data_json"])),
            "judge_count": row["judge_count"],
            "done": done,
            "total": total,
        }


@app.post("/api/judge")
def judge(req: JudgeRequest):
    if req.type_judgement not in VALID_TYPES:
        raise HTTPException(400, "Invalid type judgement")
    pts = points_for(req)
    with db() as cur:
        try:
            cur.execute(
                """
                INSERT INTO judgements(coder_id, pair_id, type_judgement,
                    original_judgement, outcome_judgement, comment,
                    edited_abstract, edited_outcome_quote,
                    hard_mode, hard_mode_entry_json,
                    points, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    req.coder_id,
                    req.pair_id,
                    req.type_judgement,
                    req.original_judgement,
                    req.outcome_judgement,
                    req.comment,
                    req.edited_abstract,
                    req.edited_outcome_quote,
                    1 if req.hard_mode else 0,
                    json.dumps(req.hard_mode_entry) if req.hard_mode_entry else None,
                    pts,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(400, "Already judged this pair")
        cur.execute(
            """
            SELECT COALESCE(SUM(points), 0) AS total FROM judgements
            WHERE coder_id = %s AND type_judgement != 'skip'
            """,
            (req.coder_id,),
        )
        total = cur.fetchone()["total"]
        return {"points_earned": pts, "total_points": total, "rank": rank_for(cur, total)}


@app.get("/api/onboarding")
def onboarding_pairs():
    return {"pairs": [with_oa(p) for p in load_onboarding()]}


class OnboardingComplete(BaseModel):
    coder_id: int


@app.post("/api/onboarding/complete")
def onboarding_complete(req: OnboardingComplete):
    with db() as cur:
        cur.execute(
            "UPDATE coders SET onboarded_at = %s WHERE id = %s AND onboarded_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), req.coder_id),
        )
        if cur.rowcount == 0:
            cur.execute("SELECT onboarded_at FROM coders WHERE id = %s", (req.coder_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Coder not found")
        return {"onboarded": True}


@app.get("/api/leaderboard")
def leaderboard():
    with db() as cur:
        cur.execute(
            """
            SELECT c.handle AS name,
                   COALESCE(SUM(CASE WHEN j.type_judgement != 'skip' THEN j.points ELSE 0 END), 0) AS points,
                   SUM(CASE WHEN j.type_judgement != 'skip' THEN 1 ELSE 0 END) AS pairs
            FROM coders c
            LEFT JOIN judgements j ON j.coder_id = c.id
            GROUP BY c.id, c.handle
            ORDER BY points DESC, pairs DESC, c.handle ASC
            """
        )
        return [dict(r) for r in cur.fetchall()]


@app.get("/api/stats")
def stats(coder_id: int):
    with db() as cur:
        cur.execute(
            """
            SELECT
                COUNT(CASE WHEN type_judgement != 'skip' THEN 1 END) AS done,
                COALESCE(SUM(CASE WHEN type_judgement != 'skip' THEN points ELSE 0 END), 0) AS points,
                COUNT(CASE WHEN type_judgement = 'skip' THEN 1 END) AS skipped
            FROM judgements WHERE coder_id = %s
            """,
            (coder_id,),
        )
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM pairs WHERE is_hard = 0")
        normal_total = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM pairs WHERE is_hard = 1")
        hard_total = cur.fetchone()["n"]
        return {
            "done": row["done"],
            "points": row["points"],
            "skipped": row["skipped"],
            "total": normal_total + hard_total,
            "normal_total": normal_total,
            "hard_total": hard_total,
            "rank": rank_for(cur, row["points"]),
        }


DOCS = ROOT / "docs"
app.mount("/", StaticFiles(directory=str(DOCS), html=True), name="docs")
