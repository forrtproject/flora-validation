# FLoRA Validation — complete technical reference

The human-validation layer of the [FORRT](https://forrt.org) FLoRA project. Replication
and reproduction studies extracted by an upstream pipeline arrive here as unverified
claims; volunteers check them in a gamified web app; a consensus engine and an LLM
resolve the results; validated records are exported back to the FLoRA dataset.

This document describes the system as it actually is. Where an older doc in this folder
disagrees, this one is current — see [§17](#17-documentation-map).

---

## Contents

1. [What this is](#1-what-this-is)
2. [Quick start](#2-quick-start)
3. [Stack and layout](#3-stack-and-layout)
4. [The record lifecycle](#4-the-record-lifecycle)
5. [The validator app](#5-the-validator-app)
6. [Scoring, tiers, and the leaderboard](#6-scoring-tiers-and-the-leaderboard)
7. [The consensus engine](#7-the-consensus-engine)
8. [The LLM validator](#8-the-llm-validator)
9. [The admin panel](#9-the-admin-panel)
10. [Database schema](#10-database-schema)
11. [API reference](#11-api-reference)
12. [Scheduled work](#12-scheduled-work)
13. [Scripts](#13-scripts)
14. [Local development](#14-local-development)
15. [Deployment](#15-deployment)
16. [Operations and troubleshooting](#16-operations-and-troubleshooting)
17. [Documentation map](#17-documentation-map)
18. [Design decisions and gotchas](#18-design-decisions-and-gotchas)

---

## 1. What this is

Upstream, a separate project (`forrtproject/flora-extractor`) searches the literature,
filters false positives, and extracts for each candidate paper: which original study it
replicates, and what the outcome was. That extraction is machine-made and imperfect.

This repo is the layer that makes it trustworthy:

- Volunteers ("validators") are shown one **paper pair** at a time — a replication and
  the original it claims to replicate — and answer three questions.
- **Two humans** must judge every record before anything is decided.
- A **consensus engine** compares their answers. An **LLM** acts as sanity check or
  tiebreaker, but never overrules agreeing humans.
- Records that reach consensus are **approved by an admin** and written to `validated`.
- A nightly job exports `validated` to CSV, which the FLoRA R pipeline consumes.

There is also a **Source Records** tab that ingests the FLoRA entry sheets directly —
a parallel, simpler path documented separately in
[SOURCE_RECORDS.md](SOURCE_RECORDS.md).

### Live scale

| | |
|---|---|
| Records imported (`unvalidated`) | **1,818** |
| — awaiting validation | 1,228 |
| — in progress | 143 |
| — needs review | 140 |
| — consensus reached, awaiting admin | 15 |
| — validated | 239 |
| — rejected | 53 |
| Human judgements submitted | **1,034** (587 slot 1 + 447 slot 2) |
| Exported `validated` records | **238** |
| Registered validators | **34** (30 regular, 3 trusted, 1 senior) |
| Admins | 6 |
| Entry-sheet source records | **1,653** |

---

## 2. Quick start

```bash
git clone https://github.com/forrtproject/flora-validation
cd flora-validation

python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

cp .env.example .env            # then fill in DATABASE_URL and GEMINI_API_KEY

python -c "import app"          # creates/updates all tables via init_db()
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. The admin panel is the same app — sign in with an admin
handle and password from the `admins` table.

To load data: `python csv_to_db.py --input data/extracted_latest.csv --dry-run` first,
then without `--dry-run`.

---

## 3. Stack and layout

**Backend** — FastAPI on Python 3.12, psycopg2 against PostgreSQL (Supabase in
production). Raw SQL throughout; there is no ORM. A `prisma/` folder and a `src/`
folder exist but are **empty vestiges of an abandoned Next.js scaffold** — ignore them.

**Frontend** — one HTML file, one JS file, one CSS file. No framework, no build step.
`docs/` is served directly by FastAPI's `StaticFiles` mount at `/`.

**LLM** — Google Gemini (`gemini-3.1-flash-lite`) via `google-genai`.

**Scheduler** — APScheduler, started in-process when `app.py` is imported.

```
app.py                     3,134 lines — every HTTP endpoint (59 of them)
consensus_engine.py          352     — the decision tree
llm_validator.py             118     — Gemini prompt and parsing
source_records_service.py    613     — Source Records data layer (no FastAPI types)
sync_sources.py              492     — entry-sheet ingest
transform_sources.py         325     — entry-sheet → FLoRA dataset
csv_to_db.py                 272     — extracted.csv → unvalidated
export_validated.py          253     — validated → CSV for the R pipeline
db_schema.sql                806     — all tables, idempotent
email_templates.py           174     — Resend transactional email

docs/index.html               ~590   — all five screens
docs/app.js                 ~6,190   — the whole frontend
docs/style.css              ~4,920

tests/                       28 tests, all passing
.github/workflows/           daily-export.yml · sync-sources.yml
```

### A caution about the database

The production database contains **26 tables, but only 17 belong to this repo.** The
nine `engine_*` tables (`engine_verdicts` alone has 40,052 rows) are written by a
different application sharing the same Postgres instance. Nothing here references them.
Do not assume the database is exclusively this app's — in particular, never run a
blanket `DROP` or a schema-wide migration.

---

## 4. The record lifecycle

```
  extracted.csv                      (upstream flora-extractor repo)
      │  csv_to_db.py — imports only rows where
      │  filter_status ∈ {replication, reproduction}
      │  AND link_method ∈ {7 resolved methods}
      ▼
  unvalidated              ─────────────────────────┐
      │  + record_metadata (extraction provenance)   │
      │  + 3 × validation_queue (human_1, human_2, llm)
      │                                              │
      │  validators claim slots via /api/next-pairs  │
      ▼                                              │
  validation_queue                                   │
      │  each human submits 3 checks + corrections   │
      │  second submission triggers…                 │
      ▼                                              │
  consensus_engine.evaluate_consensus()              │
      │                                              │
      ├─ rejected ──────────────────────────────────►│
      ├─ need_review ───────────► admin resolves ────┤
      ├─ consensus_reached ─────► admin approves ────┤
      └─ validated (senior agreement, auto)          │
      ▼                                              │
  validated  ◄───────────────────────────────────────┘
      │  export_validated.py, nightly 04:00 UTC
      ▼
  data/validated_export.csv  →  committed  →  FLoRA R pipeline
```

### Import rules (`csv_to_db.py`)

Only **resolved** rows are imported. A row qualifies when:

- `filter_status` is `replication` or `reproduction`, **and**
- `link_method` is one of: `author_year_match`, `llm_abstract`, `llm_fulltext`,
  `single_candidate_after_requery`, `title_pattern_match`, `citation_context_match`,
  `same_author_year_title_overlap`

Everything else — `false_positive`, `no_original_found`, `target_pending`, `api_error` —
is skipped and counted in the run output.

Deduplication is by `pair_id` (an MD5 from the upstream CSV). Re-running is safe.

One translation happens at the boundary: the extractor emits `success`/`failure`, this
app uses `successful`/`failed`. Exact-match only, so reproduction labels like
`computationally successful, robust` pass through untouched.

---

## 5. The validator app

Five screens, all in `docs/index.html`, switched by JS — there is no router.

| Screen | Purpose |
|---|---|
| `login-screen` | Handle + email, or handle + personal code |
| `update-screen` | "What's new" gate, shown when `updates.json` version is newer than the validator's `last_seen_update` |
| `onboarding-screen` | 5 calibration examples with known answers |
| `game-screen` | The main validation loop |
| `admin-screen` | The admin panel ([§9](#9-the-admin-panel)) |

### Login

No passwords, no JWT, no OAuth. A validator supplies a **handle** plus either an
**email** or a **four-part personal code**. The row in `validators` is created on first
use and looked up thereafter. Handles are `2–32` chars, `[A-Za-z0-9._-]`.

`/api/forgot-handle` emails a reminder via Resend, rate-limited per day by
`forgot_requests_today` / `forgot_requests_date`.

### Onboarding

Five pairs from `docs/onboarding.json` with expected answers. It calibrates judgement
before real records are served, and stamps `onboarded_at`.

### The validation loop

The client keeps a **prefetch buffer**. `/api/next-pairs` batch-claims up to 5 pairs:
the first is *active* (started, locked to this validator), the rest are buffered so the
next card renders instantly.

A validator answers three questions in one progressive form:

1. **Type check** — is this a replication / reproduction / neither?
2. **Original matching** — does the linked original study match? (correct / wrong / can't tell)
3. **Outcome coding** — is the outcome judgement right? (correct / mischaracterised / can't tell)

Any answer can carry a correction: `corrected_doi_o`, `corrected_study_o`,
`corrected_outcome`, `corrected_type`, `corrected_study_r`, `corrected_url_r`,
`corrected_outcome_quote`, `corrected_abstract`.

**"Can't tell" is not a hard vote.** It is recorded in `additional_checks` as
`was_unsure_original` / `was_unsure_outcome`, and the consensus engine routes any record
with an unsure answer straight to `need_review` rather than treating it as `incorrect`.

### Modes

- **Normal mode** — records with an abstract
- **Hard mode** — records with no abstract, where the validator must open the paper

A validator's active pair is scoped to its mode, so switching modes parks the other
pair rather than dragging it along.

### Restricted access

A hard-mode validator who cannot open the article reports it. The record is pulled out
of circulation (`restricted_access = TRUE`) until an admin assigns it to someone with
access.

### Assignments

Admins can assign a specific record to a specific validator. Assignments appear in a
dedicated banner and are worth **double points**.

### Messages

A two-way inbox between validators and admins, threaded (`validator_messages`, 72 rows
live). Admins get a badge count; validators get an inbox icon.

---

## 6. Scoring, tiers, and the leaderboard

### Points

```python
points = validator.vote_score
       + 2  if original_check == "correct"
       + 2  if outcome_check  == "correct"
       + 1  if validator_notes is non-empty
```

Assignment submissions are worth **double**. Skips score zero and increment
`skipped_count`.

`vote_score` is per-validator and stored on the row. The LLM's notional weight is 15
(`_LLM_VOTE_SCORE` in `llm_validator.py`).

### Tiers (`validators.validator_tier`)

| Tier | Name | Effect |
|---:|---|---|
| 0 | Regular | Standard validator (30 live) |
| 1 | Trusted | Agreements weighted higher in admin stats (3 live) |
| 2 | Senior | **Agreement auto-validates** without admin review; can use senior-reject (1 live) |

Senior powers are enforced server-side — `/api/senior-reject` rejects any caller with
`validator_tier < 2`.

**Senior reject is authoritative.** Once a senior has used it, neither the second human
nor the LLM can overturn it; the record becomes `rejected` regardless of submission
order. It is detected by a `senior_reject` marker in `additional_checks`, distinct from
an ordinary `not_validation` answer.

### Serving priority

`serving_config` lets admins bias which records are served — currently: enabled, favour
`outcome = failed`, years 2011–2021, at a 70% share. `/api/admin/serving-config/preview`
shows how many records a proposed config would match before saving it.

### Leaderboard

Top 10 by `total_points`, recalculated after each submission, shown in a sidebar.

---

## 7. The consensus engine

`consensus_engine.evaluate_consensus(cur, record_id)` runs after **every** human
submission and returns immediately if fewer than two humans have completed.

### Pre-checks, in order

1. **Senior reject present?** → `rejected`. Nothing else is consulted.
2. **Either human unsure?** (`was_unsure_original` / `was_unsure_outcome`) → `need_review`.

### The decision tree

```
checks agree AND corrections agree
├── both said "not_validation"
│     └── LLM sanity check
│           ├── LLM agrees → rejected
│           └── LLM disagrees → need_review        (LLM thinks it IS a replication)
└── otherwise
      └── LLM sanity check (advisory only)
            ├── a senior was involved → validated        (auto, no admin step)
            └── otherwise            → consensus_reached (admin must approve)

checks agree AND corrections differ
└── need_review                                    (no LLM call — humans already
                                                    agree on the judgement, they
                                                    just wrote different fixes)

checks differ
└── LLM tiebreaker
      ├── LLM errored          → need_review (is_tiebreaker = TRUE)
      ├── matches exactly one human
      │     ├── that human said "not_validation" → need_review
      │     └── otherwise → consensus_reached with that human's corrections
      └── matches both or neither → need_review
```

**Humans always win.** The LLM in branch 1 is advisory — its verdict is stored in
`unvalidated.llm_validator` for admin visibility but does not change the outcome.

### Resolving final values

When a winner is chosen, `_resolve_final` builds the authoritative record:

- Each field takes the winner's correction, falling back to the extracted value.
- **`outcome_quote`**: if validators edited it, the **longest** edit wins — most context,
  and deterministic.
- **`abstract_r`**: same rule, longest edit wins.
- **`out_quote_source`**: recomputed against the *final* (possibly corrected) abstract.
  If the normalised quote is contained in the normalised abstract → `abstract`,
  otherwise → `full_text`. If validators did not touch the quote, the existing source is
  kept without re-checking.

Normalisation for these comparisons lowercases and strips everything non-alphanumeric,
so light formatting edits do not register as disagreements.

---

## 8. The LLM validator

`llm_validator.run_llm_validation(record, context)` where context is `sanity_check` or
`tiebreaker`.

- Model: **`gemini-3.1-flash-lite`**
- Input: the replication abstract plus the extracted metadata — **nothing else**. The
  prompt explicitly instructs the model to use no external knowledge.
- Output: JSON with `type_check`, `original_check`, `outcome_check`, plus reasoning.
- **Defaults to "correct" when uncertain** — conservative by design, so the LLM rarely
  manufactures a disagreement.

Errors are captured rather than raised: the verdict dict carries an `error` key, the
engine treats it as "matches nobody", and the record goes to `need_review`. A nightly
job retries records whose LLM call genuinely errored.

---

## 9. The admin panel

Sign in at the same URL with a handle and password from `admins`. Auth is an
`X-Admin-Token` header — `sha256(password + ":flora-admin-v1")` — validated by
`_require_admin()`, which returns the admin's handle for stamping. Some operations
(creating/deleting admins) additionally require `trusted = TRUE`.

Eight tabs:

| Tab | What it does |
|---|---|
| **Entries** | Every record, filterable by All / Pending Approval / Needs Review / Validated / Excluded. Shows validator handles, agreement %, LLM errors. Approve, flag for review, add notes, resolve conflicts. |
| **Source Records** | The entry-sheet table — see [SOURCE_RECORDS.md](SOURCE_RECORDS.md) |
| **Validator Stats** | Per-validator throughput, accuracy, flagged judgements; set tier |
| **Admins** | Add/remove admins, toggle trusted, set the site banner |
| **Dashboard** | Aggregate progress and throughput |
| **Pool Priority** | Edit `serving_config` with a live preview of how many records match |
| **Restricted access** | Records reported as inaccessible; assign them to specific validators |
| **Messages** | Threaded inbox with validators |

### Approval

`consensus_reached` records wait for an admin. Approving writes the row to `validated`
and stamps `admin_approved`. Flagging sends it to `need_review`. Every admin action is
attributed by handle.

---

## 10. Database schema

All definitions are in [`db_schema.sql`](../db_schema.sql), which is **idempotent** and
re-executed by `init_db()` on **every application start**. Anything you add there must
survive repeated execution — use `IF NOT EXISTS`, `CREATE OR REPLACE`,
`ON CONFLICT DO NOTHING`, and guard `ALTER … RENAME` inside a `DO $$ … $$` block.
A non-idempotent statement takes the server down on the next restart.

### Core tables

**`validators`** (16 cols) — `handle`, `email`, `code`, `vote_score`, `validator_tier`,
`total_points`, `total_judgements`, `skipped_count`, `accuracy_score`, `onboarded_at`,
`last_login_at`, `last_seen_update`, forgot-handle rate limiting.

**`unvalidated`** (46 cols) — one row per resolved pair. Display columns for both sides
(`doi_r`/`study_r`/`year_r`/`url_r`/`ref_r`/`abstract_r` and the `_o` equivalents),
classification (`type`, `outcome`, `outcome_quote`, `out_quote_source`), workflow state
(`validation_status`, `is_tiebreaker`, `restricted_access`, `admin_checked`,
`admin_override`), the three validator summaries as JSONB (`validator_1`,
`validator_2`, `llm_validator`), and the consensus-resolved `final_*` columns.

`validation_status` ∈ `unvalidated`, `validation_inprogress`, `validated`,
`need_review`, `consensus_reached`, `rejected`.

**`validation_queue`** (26 cols) — exactly three rows per record: `human_1`, `human_2`,
`llm`. Holds the three checks, all corrections, `additional_checks` JSONB, notes,
points, `flagged`/`flag_reason`, and the `is_shown` / `started_at` / `validated_at`
timing that drives claiming and reaping. `UNIQUE (record_id, validator_slot)`.

**`validated`** (22 cols) — final consensus records, `UNIQUE (doi_r, study_r, doi_o, study_o)`.
This is what gets exported.

**`record_metadata`** (24 cols) — upstream extraction provenance: filter and link method,
evidence, confidence, model, author lists, OpenAlex ids, `original_rank`/`n_originals`
for multi-original papers. Not shown in the validation UI.

### Supporting tables

`admins` · `assignments` · `validator_messages` · `site_banner` · `serving_config`

### Source Records tables

`source_records` · `source_record_edits` · `source_sync_runs` ·
`source_display_counters` · `outcome_alias` · `reproduction_outcome_map` ·
`transform_exclusions` — documented in [SOURCE_RECORDS.md](SOURCE_RECORDS.md#7-database-schema).

### Triggers

- `trg_clear_stale_oa_work_id` on `unvalidated` — nulls the OpenAlex work id when its
  DOI changes, so the backfill re-fetches for the correct paper
- `trg_clear_stale_source_oa_work_id` on `source_records` — same guard
- `trg_set_source_content_fingerprint` on `source_records` — recomputes the duplicate
  fingerprint on insert and on any DOI/URL change

---

## 11. API reference

59 endpoints. All admin routes require `X-Admin-Token`.

### Validator

```
POST /api/login                        handle + (email | code)
GET  /api/onboarding                   the 5 calibration pairs
POST /api/onboarding/complete
POST /api/update-seen                  dismiss the "what's new" gate
GET  /api/next-pairs                   batch-claim (count, mode, buffered_only)
POST /api/pairs/{queue_id}/start
POST /api/judge                        submit a judgement
POST /api/skip
POST /api/senior-reject                tier ≥ 2 only
POST /api/restricted                   report an inaccessible article
GET  /api/my-judgements                own history
GET  /api/my-judgements/{queue_id}
GET  /api/my-assignments
GET  /api/assignment/{record_id}
POST /api/assignment-judge             double points
GET  /api/stats · /api/leaderboard · /api/banner · /api/health
GET  /api/messages · POST /api/messages/{id}/read · /api/messages/{id}/reply
POST /api/forgot-handle
```

### Admin

```
POST   /api/admin/login
GET    /api/admin/stats · /dashboard · /validators
GET    /api/admin/entries                        list + filter + search + sort
GET    /api/admin/entries/{record_id}
POST   /api/admin/entries/{record_id}/approve · /flag-review · /note · /resolve
POST   /api/admin/queue/{queue_id}/flag
GET    /api/admin/serving-config · PUT · GET /preview
GET    /api/admin/restricted · POST /api/admin/assign
GET    /api/admin/validators/{id}/flagged · POST /{id}/set-tier
GET    /api/admin/admins · POST · DELETE /{id} · POST /{id}/toggle-trusted
POST   /api/admin/banner
GET    /api/admin/messages · /thread/{id} · POST /thread/{id}/reply · /message
```

### Source Records

```
GET   /api/admin/source-records                  list + counts
GET   /api/admin/source-records/export.csv
GET   /api/admin/source-records/sync-status
GET   /api/admin/source-records/duplicates
GET   /api/admin/source-records/vocabularies
GET   /api/admin/source-records/{record_id}
PATCH /api/admin/source-records/{record_id}
POST  /api/admin/source-records/{record_id}/duplicate
```

**Route ordering matters.** Literal paths (`export.csv`, `sync-status`, `duplicates`,
`vocabularies`) are declared *before* `{record_id}`, because FastAPI matches in
declaration order. New literal paths must go above the parameterised ones.

---

## 12. Scheduled work

### In-process (APScheduler, starts with the app)

| Job | Schedule | What |
|---|---|---|
| `sync_csv.sync_once` | 02:00 UTC | Pull `extracted.csv` from the extractor repo and import new rows |
| `_backfill_oa_work_ids` | 02:30 UTC | Fill missing OpenAlex work ids from DOIs |
| `_retry_tiebreakers` | 00:22 UTC | Re-run consensus on records whose LLM call errored |
| `_reap_stale_slots` | every 2 min | Release queue slots claimed but abandoned, so records return to circulation |

The reaper matters: without it, a validator who closes the tab mid-record would lock
that record indefinitely.

### GitHub Actions

| Workflow | Schedule | What |
|---|---|---|
| `sync-sources.yml` | 03:00 UTC | Entry-sheet sync + FLoRA dataset build |
| `daily-export.yml` | 04:00 UTC | `export_validated.py`, commits `data/validated_export.csv`, `data/needs_manual_refs.csv`, `oa_ref_cache.json` |

Both need the `DATABASE_URL` secret. The hour gap is deliberate so they never contend.

---

## 13. Scripts

| Script | Purpose |
|---|---|
| `csv_to_db.py` | Import `extracted.csv` → `unvalidated` + `record_metadata` + 3 queue slots. `--dry-run` supported |
| `export_validated.py` | `validated` → `data/validated_export.csv`, plus `needs_manual_refs.csv` listing entries whose identifiers CrossRef/DataCite cannot resolve. Replaces references with OpenAlex data where available, cached in `oa_ref_cache.json` |
| `sync_csv.py` | Fetch `extracted.csv` from the extractor repo (nightly job calls `sync_once`) |
| `sync_sources.py` | Entry-sheet ingest → `source_records` |
| `transform_sources.py` | `source_records` → FLoRA column set |
| `backfill_oa_work_ids.py` | Fill `oa_work_id_o/_r` from DOIs via OpenAlex |
| `backfill_quote_source.py` | Populate `out_quote_source` on existing rows |
| `fetch_oa.py` | Unpaywall open-access status for every DOI |
| `update_originals.py` | Refresh original-study references on existing rows |
| `update_outcomes.py` | Update outcome classification from a newer `extracted.csv` |
| `find_orphans.py` | Diagnose rows in the DB no longer present in the CSV |
| `cleanup_orphans.py` | Delete those rows |
| `build_static.py` | Generate `docs/pairs.json`, `hard_pairs.json`, `onboarding.json` for the static GitHub Pages demo |
| `db_migrate.py` | Migrate an older schema forward |
| `db_reset.py` | **Destructive** — wipes everything except `validators` |

---

## 14. Local development

### Environment

```bash
DATABASE_URL=postgresql://postgres:PASSWORD@db.PROJECT.supabase.co:5432/postgres
GEMINI_API_KEY=AIzaSy...
GITHUB_TOKEN=                       # only if the extractor repo is private
GITHUB_REPO=forrtproject/flora-extractor
GITHUB_BRANCH=feature/extract
ADMIN_PASSWORD=                     # fallback admin password
RESEND_API_KEY=                     # transactional email
EMAIL_FROM="Flora Validator <noreply@forrt.org>"
OPENALEX_MAILTO=                    # OpenAlex/Unpaywall polite-pool contact
```

### Running

```bash
uvicorn app:app --reload            # app + frontend on :8000
```

`init_db()` runs `db_schema.sql` on import, so schema changes apply on restart.

The scheduler starts automatically. When running several local instances against the
same database, be aware they will all run the nightly jobs.

### Tests

```bash
python -m pytest tests/ -q          # 28 tests
```

Coverage is on the logic that is hardest to reason about: `test_consensus_engine.py`
(the full decision tree), `test_llm_validator.py` (prompt/parse/error handling),
`test_sync_csv.py`. They use mocked cursors and do not need a database.

### Frontend

No build step. Edit `docs/app.js` / `docs/style.css` and reload. Check syntax with
`node --check docs/app.js`.

---

## 15. Deployment

`Procfile`: `web: uvicorn app:app --host 0.0.0.0 --port $PORT` · `runtime.txt`:
`python-3.12`. Any Procfile-aware host works; production runs on Supabase for the
database.

Only `DATABASE_URL` is strictly required to boot. Without `GEMINI_API_KEY` the LLM
paths record an error and route records to `need_review` — degraded, not broken.

There is also a **static demo mode**: `build_static.py` writes JSON fixtures into
`docs/`, which can be served from GitHub Pages with no backend at all.

---

## 16. Operations and troubleshooting

**Server won't start after a schema edit.** `init_db()` executes all of
`db_schema.sql` on import. A non-idempotent statement (a bare `ALTER … RENAME`, a
`CREATE TABLE` without `IF NOT EXISTS`) fails on the second run. Guard it and restart.

**Records stuck in `validation_inprogress`.** A validator claimed a slot and vanished.
`_reap_stale_slots` releases these every 2 minutes; check the scheduler is running.

**A record sat at `consensus_reached` for weeks.** That status means *humans agreed,
admin has not approved*. It is a queue for people, not a bug — Entries → Pending Approval.

**Lots of `need_review`.** Expected. It is the destination for unsure answers,
conflicting corrections, three-way splits, and LLM errors. 140 live.

**LLM errors.** `_retry_tiebreakers` re-runs them nightly. Filter Entries by LLM errors
to see the backlog. Check `GEMINI_API_KEY` and the free-tier rate limit.

**Export is missing references.** `export_validated.py` also writes
`data/needs_manual_refs.csv` — entries whose DOI/URL cannot be resolved by
CrossRef/DataCite and which the R pipeline's title filter would otherwise drop. Fill in
the blank columns and add them to `manual_references.xlsx` upstream.

**Entry-sheet sync problems** — see
[SOURCE_RECORDS.md §12](SOURCE_RECORDS.md#12-troubleshooting).

---

## 17. Documentation map

| Doc | Status |
|---|---|
| **PROJECT.md** (this file) | Current |
| [SOURCE_RECORDS.md](SOURCE_RECORDS.md) | Current — the entry-sheet tab and pipeline |
| [SETUP.md](SETUP.md) | Mostly current — environment and Supabase setup |
| [CSV_SCHEMA.md](CSV_SCHEMA.md) | Reference for the upstream `extracted.csv` columns |
| [ARCHITECTURE.md](ARCHITECTURE.md) | ⚠ **Stale** (May 2026) |
| [VALIDATION_DB_SCHEMA.md](VALIDATION_DB_SCHEMA.md) | ⚠ **Stale** (May 2026) |
| [STAGE4_VALIDATE.md](STAGE4_VALIDATE.md) | ⚠ **Stale** (May 2026) |
| [README.md](README.md) | Describes the whole four-stage FLoRA Extractor project, of which this repo is stage 4 |

**On the stale docs:** all three date from 2026-05-14 and predate the validator tier
system, assignments, serving priority, restricted access, messages, admin trust levels,
and Source Records. Most visibly, they describe a `validators.level` column that
**does not exist** — the real column is `validator_tier`, with different semantics.
Treat them as history; verify against `db_schema.sql` before relying on anything in them.

---

## 18. Design decisions and gotchas

**Two humans before anything is decided.** No record is resolved on one judgement, ever.
This is the core quality guarantee and everything else is built around it.

**Humans always beat the LLM.** When both humans agree, the LLM is a sanity check whose
verdict is recorded for admin visibility but changes nothing. The LLM only decides when
humans conflict — and then only by matching exactly one of them.

**"Can't tell" is not "incorrect".** Collapsing the two would let uncertainty
masquerade as a judgement. Any unsure answer routes to `need_review`.

**Corrections differing is enough to block.** If two validators agree on *what is wrong*
but write different fixes, the record goes to review without an LLM call. The engine
will not pick a fix on its own.

**Longest edit wins for quotes and abstracts.** Deterministic and reproducible, and
longer text carries more context. Not a quality judgement — just a tie-break rule you
can predict.

**Raw SQL, no ORM.** Deliberate. The queries are the interesting part of this app,
especially claiming and consensus, and an ORM would obscure them. It also means
`db_schema.sql` is the single source of truth for the schema — there are no migration
files to reconcile.

**The frontend has no framework.** One HTML, one JS, one CSS, no build. Contributors are
researchers as often as engineers; a `npm install` between them and a fix is a real cost.

**`docs/` is both the frontend and the documentation folder.** FastAPI mounts it at `/`,
so the Markdown files are also web-served. Slightly unusual, and it means the static
mount must stay the *last* route registered — anything added after it is unreachable.

**Nine tables in the database are not ours.** See [§3](#3-stack-and-layout).

**The Source Records path is deliberately simpler.** It has no consensus, no LLM, and no
two-human requirement, because its input is already human-validated in the Google
Sheets. Do not model new work on it if the input is machine-extracted — that is what the
main pipeline is for.
