# FLoRA Validation — Setup & Running Guide

This guide covers everything needed to run the validation backend from scratch,
including how to obtain each required API key and how to connect Supabase as your
hosted PostgreSQL database.

---

## About Supabase and psycopg2

The validation server uses **psycopg2** to talk to PostgreSQL directly — not the
Supabase Python client. This is actually better for you:

- psycopg2 speaks native PostgreSQL. Supabase is just PostgreSQL in the cloud.
- You connect with a standard `DATABASE_URL` connection string, which Supabase
  provides in its dashboard.
- Nothing changes on the Supabase side. You keep your free-tier project.
- The same code works with any other Postgres host (Neon, Railway, local) if you
  ever switch providers.

The old code used `supabase-py` (a REST wrapper). The new code goes directly to
Postgres — faster, more reliable, full SQL support.

---

## Prerequisites

- Python 3.10 or later
- A Supabase account (free) — [supabase.com](https://supabase.com)
- A Google AI Studio account (free) — [aistudio.google.com](https://aistudio.google.com)
- A GitHub account (needed only if the source repo is private)

---

## Step 1 — Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 2 — Get Your API Keys

### A. Supabase `DATABASE_URL`

Supabase gives you a hosted PostgreSQL database for free (up to 500 MB, no credit
card needed).

1. Go to [supabase.com](https://supabase.com) and sign in.
2. Create a new project (or open an existing one).
3. In the left sidebar click **Settings → Database**.
4. Scroll to **Connection String**, select the **URI** tab.
5. Copy the string — it looks like:
   ```
   postgresql://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
   ```
6. Replace `[YOUR-PASSWORD]` with the database password you set when creating the
   project. If you forgot it, go to Settings → Database → Reset database password.

> **Free tier limits**: 500 MB storage, unlimited reads/writes, project pauses after
> 1 week of inactivity (resume with one click in the dashboard). More than enough
> for the validation workflow.

> **Important**: Use the **URI** (not the "Connection pooling" string) unless you
> specifically need PgBouncer. The plain URI works fine for this app.

---

### B. Gemini API Key (`GEMINI_API_KEY`)

The LLM validator uses Gemini Flash for sanity checks and tiebreaking.

1. Go to [aistudio.google.com](https://aistudio.google.com) and sign in with a
   Google account.
2. Click **Get API key** in the top-left panel.
3. Click **Create API key** → select your Google Cloud project (or create one).
4. Copy the key — it starts with `AIza...`.

> **Free tier**: 15 requests per minute, 1 million tokens per day — enough for the
> validation workflow. No credit card required.

---

### C. GitHub Token (`GITHUB_TOKEN`) — Optional

Only needed if the source repo (`forrtproject/flora-extractor`) is **private**.
If it is public, leave `GITHUB_TOKEN` blank.

1. Go to GitHub → click your profile photo → **Settings**.
2. Left sidebar → **Developer settings → Personal access tokens → Fine-grained tokens**.
3. Click **Generate new token**.
4. Set a name (e.g. "flora-sync"), set expiration, select the repo under
   **Repository access**.
5. Under **Permissions**, enable **Contents: Read-only**.
6. Click **Generate token** and copy it.

---

## Step 3 — Configure Environment Variables

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
# Required — from Supabase dashboard (Settings → Database → URI)
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres

# Required — from Google AI Studio
GEMINI_API_KEY=AIzaSy...

# Optional — only needed if the source repo is private
GITHUB_TOKEN=github_pat_...

# Source repo for nightly CSV sync (leave as-is unless you've forked it)
GITHUB_REPO=forrtproject/flora-extractor
GITHUB_BRANCH=feature/extract
```

---

## Step 4 — Initialise the Database

### Fresh install (no existing data)

The server creates the tables automatically on first start via `db_schema.sql`.
You can also run it manually:

```bash
python - << 'EOF'
import os, psycopg2
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.cursor().execute(Path("db_schema.sql").read_text())
conn.commit(); conn.close()
print("Schema applied.")
EOF
```

### Migrating from the old schema (pairs / coders / judgements)

If your Supabase project already has data from the old version of the app:

```bash
python db_migrate.py
```

This is safe to re-run. It copies all existing pairs, coders, and judgements into
the new tables without deleting anything.

---

## Step 5 — Load Initial Data

If you have an `extracted.csv` locally:

```bash
# Preview what will be imported (no DB writes)
python csv_to_db.py --input data/extracted.csv --dry-run

# Import
python csv_to_db.py --input data/extracted.csv
```

If you don't have a local CSV, run the nightly sync manually to pull from GitHub:

```bash
python sync_csv.py
```

This downloads the latest CSV to `data/extracted_latest.csv` and imports it.

---

## Step 6 — Start the Server

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

The server will:
1. Apply the schema (idempotent — safe even if tables already exist)
2. Seed from `data/extracted_latest.csv` if the database is empty
3. Start the APScheduler background job (nightly sync at 2:00 AM UTC)
4. Serve the frontend from the `docs/` folder

Open your browser at `http://localhost:8000`.

For production (no auto-reload):

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2
```

---

## Nightly CSV Sync

The server automatically pulls the latest `extracted.csv` from GitHub at 2:00 AM UTC
every night. It saves:

- `data/extracted_DD.MM.YYYY.csv` — dated archive
- `data/extracted_latest.csv` — always the latest

New rows are imported automatically. Rows already in the database (matched by
`pair_id`) are skipped.

To trigger a manual sync at any time:

```bash
python sync_csv.py
```

---

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/login` | Register or log in a validator |
| `GET` | `/api/onboarding` | Get onboarding pairs |
| `POST` | `/api/onboarding/complete` | Mark onboarding done |
| `GET` | `/api/next-pair?coder_id=X` | Get the next unvalidated pair |
| `POST` | `/api/judge` | Submit a validation judgment |
| `GET` | `/api/stats?coder_id=X` | Get validator stats + rank |
| `GET` | `/api/leaderboard` | Get all validators sorted by points |

Full architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
Full schema: [VALIDATION_DB_SCHEMA.md](VALIDATION_DB_SCHEMA.md)

---

## Running Tests

```bash
python -m pytest tests/ -v
```

All tests mock the database and LLM — no live connections required.

---

## Troubleshooting

**`OperationalError: could not connect to server`**
: Check your `DATABASE_URL`. For Supabase, make sure you used the URI tab (not the
pooler string), and that the password in the URL matches your database password.
Supabase projects on the free tier pause after 7 days of inactivity — resume from
the dashboard.

**`KeyError: 'GEMINI_API_KEY'`**
: Make sure you ran `cp .env.example .env` and filled in the key. The `.env` file
must be in the project root (same folder as `app.py`).

**`ModuleNotFoundError: No module named 'google.genai'`**
: Run `pip install google-genai>=1.0`. The old `google-generativeai` package is
deprecated and will not work.

**LLM validation always returns errors**
: Test your key with `python -c "from google import genai; c = genai.Client(api_key='YOUR_KEY'); print(c.models.generate_content('gemini-2.0-flash', 'hi').text)"`.
Free tier has rate limits — if you're getting quota errors, the validator will
fall back to `need_review` status (humans still win).

**Supabase free tier project paused**
: Open [supabase.com/dashboard](https://supabase.com/dashboard), click your project,
and click **Restore project**. Takes about 30 seconds.
