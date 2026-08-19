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
       → returns coder_id, handle, onboarding/tier profile
```

This is the current identity lookup, not secure session authentication. Private
validator endpoints trust the returned client-supplied `coder_id`; email login does
not challenge mailbox ownership. This is acknowledged, deferred security debt. See
[PROJECT.md §19](PROJECT.md#19-deferred-security-work) for the approved replacement
with email challenges, hashed credentials, and opaque server-side sessions.

### Get next pairs (`GET /api/next-pairs`)

```text
Client → coder_id + mode/count/buffer options
       → unvalidated JOIN validation_queue JOIN record_metadata
       → find record not yet assigned to this validator, with a free human slot
       → assign slot (is_shown=TRUE, validator_id=X)
       → returns pair data + OA URL enrichment
```

### Submit judgment (`POST /api/judge`)

```text
Client → coder_id + record_id + type_check + original_check + outcome_check + corrected_*
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
| Checks agree + corrections agree, senior involved | Yes (sanity) | `validated` — humans always win |
| Checks agree + corrections agree, no senior | Yes (sanity) | `consensus_reached` — admin approval |
| Checks agree + corrections differ | No | `need_review` |
| Checks differ + LLM matches H1 | Yes (tiebreak) | `validated` with H1 verdict |
| Checks differ + LLM matches H2 | Yes (tiebreak) | `validated` with H2 verdict |
| Checks differ + 3-way split | Yes (tiebreak) | `need_review` |
| Checks differ + LLM error | Yes (tiebreak) | `need_review` |

When a winner is selected, the engine sets the coherent `unvalidated.final_*` shape,
including separate reproduction computation/robustness axes and their evidence.
Senior consensus may write `validated` immediately; other winners stop at
`consensus_reached` for admin approval.

---

## LLM Validator

`llm_validator.run_llm_validation(record, context)` calls Gemini Flash
(`gemini-3.1-flash-lite`) via the `google-genai` SDK.

- Prompts the model with the abstract + extracted metadata
- Default behaviour: "correct" when uncertain (conservative)
- Returns structured JSON with `type_check`, `original_check`, `outcome_check`,
  `corrected_*` fields, and `notes`
- Retries once on transient failure; returns `{"error": "..."}` on persistent failure

The LLM is an advisory sanity check when humans agree and a tiebreaker when they do
not. It does not receive or award validator points.

---

## Nightly Sync

`sync_csv.py` is scheduled via APScheduler at 2:00 AM UTC every night (started in
`app.py`). It:

1. Fetches `extracted.csv` from `GITHUB_REPO` / `GITHUB_BRANCH`
2. Saves a dated archive: `data/extracted_DD.MM.YYYY.csv`
3. Imports a temporary candidate with `csv_to_db.run_import()` — inserts new rows,
   refreshes existing metadata, and re-keys corrected pairs by
   `(work_id, original_rank)`
4. Promotes the candidate to `data/extracted_latest.csv` only after import succeeds;
   malformed downloads never replace the previous known-good latest file

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
| `ROUTING_RELEASE_ID` | No | Filter-engine release stored with each nightly-imported row |
| `ADMIN_PASSWORD` | Operationally required | First-run admin seed. Current code has an unsafe known fallback when omitted; see PROJECT.md §19 |

---

## Database Quick Reference

See [VALIDATION_DB_SCHEMA.md](VALIDATION_DB_SCHEMA.md) for full DDL and JSONB shapes.

| Table | Key columns |
| --- | --- |
| `validators` | `id`, `handle`, `vote_score`, `total_points`, `validator_tier` |
| `unvalidated` | `record_id`, `pair_id`, `validation_status`, `validator_1/2` JSONB, `llm_validator` JSONB |
| `validation_queue` | `queue_id`, `record_id`, `validator_slot`, `is_validated`, all check fields |
| `validated` | `record_id`, study_r/title_r, study_o/title_o, and final DOI/outcome/type values |
| `validated_record_merges` | explicit duplicate A→B audit link, admin, timestamp, resolution snapshot |
| `record_metadata` | `record_id`, provenance + extraction metadata |

---

## Fresh Deployment

> **Security warning:** set a unique `ADMIN_PASSWORD` before first start. The current
> application otherwise seeds a known fallback. This is an interim operational
> requirement until the fail-closed bootstrap/session redesign in PROJECT.md §19.

```bash
# 1. Apply schema
python -c "import psycopg2; conn=psycopg2.connect(DATABASE_URL); conn.cursor().execute(open('db_schema.sql').read()); conn.commit()"

# 2. Import initial data
python csv_to_db.py --input data/extracted.csv

# 3. Start server (scheduler starts automatically)
uvicorn app:app --host 0.0.0.0 --port 8000
```

---

## Authentication Boundary (deferred redesign)

Current validator authorization follows `coder_id` supplied by the browser. Current
admin authorization follows `X-Admin-Token = sha256(password + fixed suffix)`, with
plaintext passwords in `admins`. Neither is a revocable, independently random
session. Browser storage may also retain a validator personal code and an admin
password through the current profile/prefill paths.

The planned boundary is one server-derived principal per request: a random opaque
session token in a secure HttpOnly cookie, only the token hash in PostgreSQL, explicit
expiry/revocation, Argon2id credential hashes, email possession challenges, CSRF
protection, and no private endpoint accepting `coder_id` as authority. The full
rollout and acceptance criteria live in [PROJECT.md §19](PROJECT.md#19-deferred-security-work).

## Migrating from Old Schema

```bash
python db_migrate.py   # copies pairs/coders/judgements → new tables
uvicorn app:app --host 0.0.0.0 --port 8000
```
