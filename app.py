import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data.db"
CSV_PATH = ROOT / "extracted.csv"

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
                data_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS judgements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coder_id INTEGER NOT NULL,
                pair_id TEXT NOT NULL,
                type_judgement TEXT NOT NULL,
                original_judgement TEXT,
                outcome_judgement TEXT,
                comment TEXT,
                points INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(coder_id, pair_id)
            );
            """
        )
        if conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0] == 0:
            with open(CSV_PATH, newline="") as f:
                for row in csv.DictReader(f):
                    conn.execute(
                        "INSERT OR IGNORE INTO pairs(pair_id, data_json) VALUES (?, ?)",
                        (row["pair_id"], json.dumps(row)),
                    )
        conn.commit()


init_db()


class LoginRequest(BaseModel):
    name: str


class JudgeRequest(BaseModel):
    coder_id: int
    pair_id: str
    type_judgement: str
    original_judgement: str | None = None
    outcome_judgement: str | None = None
    comment: str | None = None


def points_for(req: JudgeRequest) -> int:
    if req.type_judgement == "false_positive":
        return 5
    pts = 10
    if req.original_judgement:
        pts += 5
    if req.outcome_judgement:
        pts += 5
    if req.comment and req.comment.strip():
        pts += 3
    return pts


def rank_for(conn, points: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) + 1 FROM (
            SELECT coder_id, SUM(points) AS p
            FROM judgements
            GROUP BY coder_id
            HAVING p > ?
        )
        """,
        (points,),
    ).fetchone()[0]


@app.post("/api/login")
def login(req: LoginRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Name required")
    with db() as conn:
        row = conn.execute("SELECT id, name FROM coders WHERE name = ?", (name,)).fetchone()
        if row:
            return {"coder_id": row["id"], "name": row["name"]}
        cur = conn.execute(
            "INSERT INTO coders(name, created_at) VALUES (?, ?)",
            (name, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return {"coder_id": cur.lastrowid, "name": name}


@app.get("/api/next-pair")
def next_pair(coder_id: int):
    with db() as conn:
        row = conn.execute(
            """
            SELECT p.pair_id, p.data_json,
                   (SELECT COUNT(*) FROM judgements j2 WHERE j2.pair_id = p.pair_id) AS judge_count
            FROM pairs p
            WHERE p.pair_id NOT IN (SELECT pair_id FROM judgements WHERE coder_id = ?)
            ORDER BY judge_count ASC, RANDOM()
            LIMIT 1
            """,
            (coder_id,),
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM judgements WHERE coder_id = ?", (coder_id,)
        ).fetchone()[0]
        if not row:
            return {"pair": None, "done": done, "total": total}
        return {
            "pair": json.loads(row["data_json"]),
            "judge_count": row["judge_count"],
            "done": done,
            "total": total,
        }


@app.post("/api/judge")
def judge(req: JudgeRequest):
    if req.type_judgement not in {"replication", "reproduction", "false_positive"}:
        raise HTTPException(400, "Invalid type judgement")
    pts = points_for(req)
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO judgements(coder_id, pair_id, type_judgement,
                    original_judgement, outcome_judgement, comment, points, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.coder_id,
                    req.pair_id,
                    req.type_judgement,
                    req.original_judgement,
                    req.outcome_judgement,
                    req.comment,
                    pts,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Already judged this pair")
        total = conn.execute(
            "SELECT COALESCE(SUM(points), 0) FROM judgements WHERE coder_id = ?",
            (req.coder_id,),
        ).fetchone()[0]
        return {"points_earned": pts, "total_points": total, "rank": rank_for(conn, total)}


@app.get("/api/leaderboard")
def leaderboard():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.name,
                   COALESCE(SUM(j.points), 0) AS points,
                   COUNT(j.id) AS pairs
            FROM coders c
            LEFT JOIN judgements j ON j.coder_id = c.id
            GROUP BY c.id, c.name
            ORDER BY points DESC, pairs DESC, c.name ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/stats")
def stats(coder_id: int):
    with db() as conn:
        done = conn.execute(
            "SELECT COUNT(*) FROM judgements WHERE coder_id = ?", (coder_id,)
        ).fetchone()[0]
        points = conn.execute(
            "SELECT COALESCE(SUM(points), 0) FROM judgements WHERE coder_id = ?",
            (coder_id,),
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
        return {"done": done, "points": points, "total": total, "rank": rank_for(conn, points)}


DOCS = ROOT / "docs"
app.mount("/", StaticFiles(directory=str(DOCS), html=True), name="docs")
