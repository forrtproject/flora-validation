import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data.db"
CSV_PATH = ROOT / "extracted.csv"
ONBOARDING_PATH = ROOT / "onboarding.json"
OA_CACHE_PATH = ROOT / "oa_cache.json"

VALID_TYPES = {"replication", "reproduction", "not_validation", "skip"}
CONFIRMING_TYPES = {"replication", "reproduction"}

app = FastAPI(title="Flora Validator")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pairs (
                pair_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                is_hard INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS coders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                handle TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                onboarded_at TEXT
            );
            CREATE TABLE IF NOT EXISTS judgements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            );
            """
        )
        if conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0] == 0:
            with open(CSV_PATH, newline="") as f:
                for row in csv.DictReader(f):
                    is_hard = 0 if (row.get("abstract_r") or "").strip() else 1
                    conn.execute(
                        "INSERT OR IGNORE INTO pairs(pair_id, data_json, is_hard) VALUES (?, ?, ?)",
                        (row["pair_id"], json.dumps(row), is_hard),
                    )
        conn.commit()


init_db()


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
    """Decorate a pair dict with oa_url_r / oa_url_o (None if gated/unknown)."""
    pair["oa_url_r"] = oa_url_for(pair.get("doi_r"))
    pair["oa_url_o"] = oa_url_for(pair.get("doi_o"))
    return pair


class LoginRequest(BaseModel):
    email: EmailStr
    handle: str


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
        # Hard-mode entries reward research effort: high base, plus notes/edits bonus.
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


def rank_for(conn, points: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) + 1 FROM (
            SELECT coder_id, SUM(points) AS p
            FROM judgements
            WHERE type_judgement != 'skip'
            GROUP BY coder_id
            HAVING p > ?
        )
        """,
        (points,),
    ).fetchone()[0]


@app.post("/api/login")
def login(req: LoginRequest):
    email = req.email.strip().lower()
    handle = req.handle.strip()
    if not HANDLE_RE.match(handle):
        raise HTTPException(400, "Handle must be 2–32 chars: letters, digits, . _ -")
    with db() as conn:
        by_email = conn.execute(
            "SELECT id, email, handle, onboarded_at FROM coders WHERE email = ?", (email,)
        ).fetchone()
        if by_email:
            if by_email["handle"] != handle:
                raise HTTPException(
                    400,
                    f"This email is already linked to handle '{by_email['handle']}'. Use that handle.",
                )
            return {
                "coder_id": by_email["id"],
                "email": by_email["email"],
                "handle": by_email["handle"],
                "onboarded": bool(by_email["onboarded_at"]),
            }
        by_handle = conn.execute(
            "SELECT 1 FROM coders WHERE handle = ?", (handle,)
        ).fetchone()
        if by_handle:
            raise HTTPException(400, "That handle is already taken by another email.")
        cur = conn.execute(
            "INSERT INTO coders(email, handle, created_at) VALUES (?, ?, ?)",
            (email, handle, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return {
            "coder_id": cur.lastrowid,
            "email": email,
            "handle": handle,
            "onboarded": False,
        }


@app.get("/api/next-pair")
def next_pair(coder_id: int, mode: str = "normal"):
    if mode not in {"normal", "hard"}:
        raise HTTPException(400, "mode must be normal or hard")
    is_hard = 1 if mode == "hard" else 0
    with db() as conn:
        row = conn.execute(
            """
            SELECT p.pair_id, p.data_json,
                   (SELECT COUNT(*) FROM judgements j2 WHERE j2.pair_id = p.pair_id) AS judge_count
            FROM pairs p
            WHERE p.is_hard = ?
              AND p.pair_id NOT IN (SELECT pair_id FROM judgements WHERE coder_id = ?)
            ORDER BY judge_count ASC, RANDOM()
            LIMIT 1
            """,
            (is_hard, coder_id),
        ).fetchone()
        total = conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE is_hard = ?", (is_hard,)
        ).fetchone()[0]
        done = conn.execute(
            """
            SELECT COUNT(*) FROM judgements j
            JOIN pairs p ON p.pair_id = j.pair_id
            WHERE j.coder_id = ? AND p.is_hard = ?
            """,
            (coder_id, is_hard),
        ).fetchone()[0]
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
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO judgements(coder_id, pair_id, type_judgement,
                    original_judgement, outcome_judgement, comment,
                    edited_abstract, edited_outcome_quote,
                    hard_mode, hard_mode_entry_json,
                    points, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Already judged this pair")
        total = conn.execute(
            """
            SELECT COALESCE(SUM(points), 0) FROM judgements
            WHERE coder_id = ? AND type_judgement != 'skip'
            """,
            (req.coder_id,),
        ).fetchone()[0]
        return {"points_earned": pts, "total_points": total, "rank": rank_for(conn, total)}


@app.get("/api/onboarding")
def onboarding_pairs():
    return {"pairs": [with_oa(p) for p in load_onboarding()]}


class OnboardingComplete(BaseModel):
    coder_id: int


@app.post("/api/onboarding/complete")
def onboarding_complete(req: OnboardingComplete):
    with db() as conn:
        cur = conn.execute(
            "UPDATE coders SET onboarded_at = ? WHERE id = ? AND onboarded_at IS NULL",
            (datetime.utcnow().isoformat(), req.coder_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT onboarded_at FROM coders WHERE id = ?", (req.coder_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Coder not found")
        return {"onboarded": True}


@app.get("/api/leaderboard")
def leaderboard():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.handle AS name,
                   COALESCE(SUM(CASE WHEN j.type_judgement != 'skip' THEN j.points ELSE 0 END), 0) AS points,
                   SUM(CASE WHEN j.type_judgement != 'skip' THEN 1 ELSE 0 END) AS pairs
            FROM coders c
            LEFT JOIN judgements j ON j.coder_id = c.id
            GROUP BY c.id, c.handle
            ORDER BY points DESC, pairs DESC, c.handle ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/stats")
def stats(coder_id: int):
    with db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(CASE WHEN type_judgement != 'skip' THEN 1 END) AS done,
                COALESCE(SUM(CASE WHEN type_judgement != 'skip' THEN points ELSE 0 END), 0) AS points,
                COUNT(CASE WHEN type_judgement = 'skip' THEN 1 END) AS skipped
            FROM judgements WHERE coder_id = ?
            """,
            (coder_id,),
        ).fetchone()
        normal_total = conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE is_hard = 0"
        ).fetchone()[0]
        hard_total = conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE is_hard = 1"
        ).fetchone()[0]
        return {
            "done": row["done"],
            "points": row["points"],
            "skipped": row["skipped"],
            "total": normal_total + hard_total,
            "normal_total": normal_total,
            "hard_total": hard_total,
            "rank": rank_for(conn, row["points"]),
        }


DOCS = ROOT / "docs"
app.mount("/", StaticFiles(directory=str(DOCS), html=True), name="docs")
