# FLoRA Validation — Architecture Overview

## System Components

```text
flora-validation/
├── app.py               FastAPI server — all HTTP endpoints
├── consensus_engine.py  Decision logic: agree → validate; disagree → LLM tiebreak
├── llm_validator.py     Gemini Flash validator (sanity check & tiebreaker)
├── csv_to_db.py         Imports extracted.csv rows into the database
├── sync_csv.py          Nightly GitHub sync — downloads & imports latest CSV
├── db_schema.sql        DDL for fresh deployments (idempotent)
├── db_migrate.py        Migrates old pairs/coders/judgements schema to new schema
├── data/                extracted_latest.csv + dated archives
├── tests/               pytest test suite
│   ├── test_llm_validator.py
│   ├── test_consensus_engine.py
│   └── test_sync_csv.py
└── docs/                Frontend static files + documentation
```

---

## Request Flow

### Login (`POST /api/login`)

```text
Client → handle + email/code
       → validators table lookup / insert
       → returns coder_id, handle, onboarded flag
```

### Get next pair (`GET /api/next-pair`)

```text
Client → coder_id
       → unvalidated JOIN validation_queue JOIN record_metadata
       → find record not yet assigned to this validator, with a free human slot
       → assign slot (is_shown=TRUE, validator_id=X)
       → returns pair data + OA URL enrichment
```

### Submit judgment (`POST /api/judge`)

```text
Client → coder_id + pair_id + type_check + original_check + outcome_check + corrected_*
       → update validation_queue (is_validated=TRUE, store checks)
       → write JSONB summary → unvalidated.validator_1 / validator_2
       → update validators.total_points / total_judgements
       → call consensus_engine.evaluate_consensus()
       → returns points_earned, total_points, rank
```

---

## Consensus Engine

`consensus_engine.evaluate_consensus(cur, record_id)` is called after every human
submission. It reads both completed human rows from `validation_queue` and applies
the following decision tree:

| Condition | LLM called? | Outcome |
| --- | --- | --- |
| Checks agree + corrections agree | Yes (sanity) | `validated` — humans always win |
| Checks agree + corrections differ | No | `need_review` |
| Checks differ + LLM matches H1 | Yes (tiebreak) | `validated` with H1 verdict |
| Checks differ + LLM matches H2 | Yes (tiebreak) | `validated` with H2 verdict |
| Checks differ + 3-way split | Yes (tiebreak) | `need_review` |
| Checks differ + LLM error | Yes (tiebreak) | `need_review` |

When `validated`, the engine also writes to the `validated` table and sets
`unvalidated.final_doi_o / final_study_o / final_outcome / final_type`.

---

## LLM Validator

`llm_validator.run_llm_validation(record, context)` calls Gemini Flash
(`gemini-2.0-flash`) via the `google-genai` SDK.

- Prompts the model with the abstract + extracted metadata
- Default behaviour: "correct" when uncertain (conservative)
- Returns structured JSON with `type_check`, `original_check`, `outcome_check`,
  `corrected_*` fields, and `notes`
- Retries once on transient failure; returns `{"error": "..."}` on persistent failure

**Vote score**: LLM always votes with `vote_score = 15` (between Lvl1=10 and Lvl3=30).

---

## Nightly Sync

`sync_csv.py` is scheduled via APScheduler at 2:00 AM UTC every night (started in
`app.py`). It:

1. Fetches `extracted.csv` from `GITHUB_REPO` / `GITHUB_BRANCH`
2. Saves a dated archive: `data/extracted_DD.MM.YYYY.csv`
3. Overwrites `data/extracted_latest.csv`
4. Calls `csv_to_db.run_import()` — skips rows already in DB by `pair_id`

To run manually: `python sync_csv.py`

---

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `GITHUB_REPO` | No | Source repo for CSV (default: `forrtproject/flora-extractor`) |
| `GITHUB_BRANCH` | No | Branch name (default: `main`) |
| `GITHUB_TOKEN` | No | Personal access token for private repos |

---

## Database Quick Reference

See [VALIDATION_DB_SCHEMA.md](VALIDATION_DB_SCHEMA.md) for full DDL and JSONB shapes.

| Table | Key columns |
| --- | --- |
| `validators` | `id`, `handle`, `vote_score`, `total_points`, `level` |
| `unvalidated` | `record_id`, `pair_id`, `validation_status`, `validator_1/2` JSONB, `llm_validator` JSONB |
| `validation_queue` | `queue_id`, `record_id`, `validator_slot`, `is_validated`, all check fields |
| `validated` | `record_id`, final doi_o/study_o/outcome/type (consensus values only) |
| `record_metadata` | `record_id`, provenance + extraction metadata |

---

## Fresh Deployment

```bash
# 1. Apply schema
python -c "import psycopg2; conn=psycopg2.connect(DATABASE_URL); conn.cursor().execute(open('db_schema.sql').read()); conn.commit()"

# 2. Import initial data
python csv_to_db.py --input data/extracted.csv

# 3. Start server (scheduler starts automatically)
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Migrating from Old Schema

```bash
python db_migrate.py   # copies pairs/coders/judgements → new tables
uvicorn app:app --host 0.0.0.0 --port 8000
```
