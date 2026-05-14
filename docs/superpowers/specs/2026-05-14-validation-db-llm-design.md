# FLoRA Validation — Backend DB & LLM Validator Design

**Date:** 2026-05-14  
**Approach:** Option B — Full Schema Migration  
**Status:** Approved, ready for implementation planning

---

## Overview

Replace the current simple `pairs / coders / judgements` schema in `app.py` with a proper validation schema. Add a consensus engine, an LLM validator (Gemini Flash), and a nightly CSV sync from the extractor GitHub repo. The frontend API contract (endpoints + JSON shapes) is unchanged.

---

## 1. Database Schema

### 1.1 `validators` (replaces `coders`)

```sql
CREATE TABLE validators (
    id                  SERIAL      PRIMARY KEY,
    email               TEXT        UNIQUE,
    code                TEXT        UNIQUE,
    handle              TEXT        UNIQUE NOT NULL,
    level               INTEGER     NOT NULL DEFAULT 1,
    vote_score          INTEGER     NOT NULL DEFAULT 10,
    total_judgements    INTEGER     NOT NULL DEFAULT 0,
    accuracy_score      FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    onboarded_at        TIMESTAMPTZ
);
```

Vote score lookup (manually set per level upgrade):

| Level | Vote score |
|---|---|
| 1 | 10 |
| 2 | 20 |
| 3 | 30 |
| LLM | 15 (fixed, stored in llm_validator JSONB) |

---

### 1.2 `unvalidated`

One row per resolved `(doi_r, doi_o)` pair. Validator summaries stored as JSONB (3 columns) instead of 30+ flat columns.

```sql
CREATE TABLE unvalidated (
    record_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pair_id             TEXT        UNIQUE,

    -- Display columns
    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,
    doi_o               TEXT,
    study_o             TEXT,
    year_o              TEXT,
    url_o               TEXT,
    ref_o               TEXT,
    type                TEXT        CHECK (type IN ('replication', 'reproduction')),
    outcome             TEXT        CHECK (outcome IN (
                                        'success', 'failure', 'mixed',
                                        'uninformative', 'descriptive')),
    outcome_quote       TEXT,
    out_quote_source    TEXT,

    -- Workflow
    validation_status   TEXT        NOT NULL DEFAULT 'unvalidated'
                                    CHECK (validation_status IN (
                                        'unvalidated', 'validation_inprogress',
                                        'validated', 'need_review')),
    is_tiebreaker       BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Validator summaries (JSONB — see Section 1.5 for shape)
    validator_1         JSONB,
    validator_2         JSONB,
    llm_validator       JSONB,

    -- Consensus-resolved final values
    final_doi_o         TEXT,
    final_study_o       TEXT,
    final_outcome       TEXT,
    final_type          TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

### 1.3 `validation_queue`

Three rows per `record_id` — one slot each for `human_1`, `human_2`, `llm`.

```sql
CREATE TABLE validation_queue (
    queue_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id           UUID        NOT NULL REFERENCES unvalidated(record_id),
    validator_slot      TEXT        NOT NULL
                                    CHECK (validator_slot IN ('human_1', 'human_2', 'llm')),

    is_shown            BOOLEAN     NOT NULL DEFAULT FALSE,
    is_validated        BOOLEAN     NOT NULL DEFAULT FALSE,

    validator_id        INTEGER     REFERENCES validators(id),
    validator_name      TEXT,

    type_check          TEXT        CHECK (type_check     IN ('correct', 'incorrect')),
    original_check      TEXT        CHECK (original_check IN ('correct', 'incorrect')),
    outcome_check       TEXT        CHECK (outcome_check  IN ('correct', 'incorrect')),

    corrected_doi_o     TEXT,
    corrected_study_o   TEXT,
    corrected_outcome   TEXT,
    corrected_type      TEXT,

    additional_checks   JSONB,
    validator_notes     TEXT,
    shown_at            TIMESTAMPTZ,
    validated_at        TIMESTAMPTZ,

    UNIQUE (record_id, validator_slot)
);
```

---

### 1.4 `validated`

Final consensus records. Contains only the authoritative validated values — never the original extracted values separately. If validators agreed with the extraction, the values match `unvalidated`; if they corrected, the corrected values are stored directly.

```sql
CREATE TABLE validated (
    validated_record_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id           UUID        NOT NULL REFERENCES unvalidated(record_id),

    -- Replication paper (never changes during validation)
    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,

    -- Original study (final consensus value — corrected or original)
    doi_o               TEXT,
    study_o             TEXT,
    year_o              TEXT,
    url_o               TEXT,
    ref_o               TEXT,

    -- Classification (final consensus value)
    type                TEXT,
    outcome             TEXT,
    outcome_quote       TEXT,
    out_quote_source    TEXT,

    validated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (doi_r, study_r, doi_o, study_o)
);
```

---

### 1.5 `record_metadata`

Supplementary extraction data. Unchanged from existing schema doc.

```sql
CREATE TABLE record_metadata (
    metadata_id             UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id               UUID    NOT NULL UNIQUE REFERENCES unvalidated(record_id),
    pair_id                 TEXT,
    filter_status           TEXT,
    filter_method           TEXT,
    filter_evidence         TEXT,
    filter_confidence       TEXT,
    original_match_type     TEXT,
    original_match_confidence TEXT,
    link_method             TEXT,
    link_evidence           TEXT,
    link_confidence         TEXT,
    link_llm_model          TEXT,
    outcome_confidence      TEXT,
    authors_r               TEXT,
    authors_o               TEXT,
    journal_r               TEXT,
    openalex_id_r           TEXT,
    source                  TEXT,
    original_rank           INTEGER,
    n_originals             INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

### 1.6 JSONB Shapes

**Human validator (`validator_1`, `validator_2`):**
```json
{
  "validator_id": 42,
  "validator_name": "alice",
  "vote_score": 10,
  "validated_at": "2026-05-14T09:00:00Z",
  "type_check": "correct",
  "original_check": "incorrect",
  "outcome_check": "correct",
  "corrected_doi_o": "10.xxxx/corrected",
  "corrected_study_o": null,
  "corrected_outcome": null,
  "corrected_type": null,
  "notes": "Wrong original linked"
}
```

**LLM validator (`llm_validator`):**
```json
{
  "model": "gemini-2.0-flash",
  "validated_at": "2026-05-14T09:01:00Z",
  "context": "sanity_check",
  "vote_score": 15,
  "type_check": "correct",
  "original_check": "correct",
  "outcome_check": "incorrect",
  "corrected_outcome": "failure",
  "corrected_doi_o": null,
  "corrected_type": null,
  "notes": "Abstract states 'failed to replicate' but outcome coded as success"
}
```

`context` is either `"sanity_check"` (both humans agreed, LLM runs for safety) or `"tiebreaker"` (humans disagreed).

---

## 2. Consensus Engine (`consensus_engine.py`)

Runs synchronously after every human `POST /api/judge`. Checks whether both human slots are complete; if so, evaluates and resolves.

### 2.1 Agreement Definition

Two validators agree when all of the following match:
- `type_check`
- `original_check`
- `outcome_check`
- `corrected_doi_o` (both null, or both the same non-null value)
- `corrected_study_o` (same)
- `corrected_outcome` (same)
- `corrected_type` (same)

### 2.2 State Machine

```
After second human submits:

Both agree, no corrections
  → LLM sanity check
    → LLM agrees or disagrees: validated (humans win; LLM disagreement logged in llm_validator JSONB)
    → LLM call errors: validated anyway (humans win; error logged in llm_validator JSONB)

Both agree, same corrections
  → LLM sanity check → validated (corrections applied; LLM error does not block)

Both agree, different corrections
  → need_review (no LLM call)

Humans disagree
  → LLM tiebreaker (is_tiebreaker = TRUE)
    → LLM + H1 agree (all 3 checks match) → H1 verdict wins; apply corrections if consistent, else need_review
    → LLM + H2 agree (all 3 checks match) → H2 verdict wins; apply corrections if consistent, else need_review
    → 3-way split or LLM call error       → need_review
```

### 2.3 Correction Resolution

When validators agree on corrections, the consensus engine resolves final values before writing to `validated`:

| Field | Resolution rule |
|---|---|
| `doi_o` | Use `corrected_doi_o` if both validators provided matching corrections, else original |
| `study_o` | Use `corrected_study_o` if matching, else original |
| `outcome` | Use `corrected_outcome` if matching, else original |
| `type` | Use `corrected_type` if matching, else original |

Resolved values written to both `validated` (as the single authoritative record) and back to `unvalidated.final_*` for quick reference.

### 2.4 V2 Hook

When the reputation system is active, skip the LLM sanity check if both validators have `level >= 2`. The `consensus_engine.py` will have a `SKIP_LLM_ABOVE_LEVEL = None` config constant (set to `2` in V2).

---

## 3. LLM Validator (`llm_validator.py`)

### Model
Gemini Flash (`gemini-2.0-flash`) via the Google Generative AI SDK. Single call per record, ~400 input tokens.

### Input
- Replication paper abstract
- Extracted original study: title, doi, authors, year
- Extracted type (replication/reproduction)
- Extracted outcome category + supporting quote

### Prompt Strategy
Structured prompt instructing Gemini to:
- Answer only from the provided abstract and metadata (no web search)
- Default to `"correct"` when uncertain (conservative bias)
- Return a JSON object only (no prose)

### Output (parsed from response)
```json
{
  "type_check": "correct | incorrect",
  "original_check": "correct | incorrect",
  "outcome_check": "correct | incorrect",
  "corrected_outcome": "success | failure | mixed | uninformative | descriptive | null",
  "corrected_doi_o": "string | null",
  "corrected_type": "replication | reproduction | null",
  "notes": "brief reasoning string"
}
```

### Error Handling
- If the LLM call fails (timeout, malformed JSON, API error): log the error, write `llm_validator = {"error": "...", "context": "..."}` to `unvalidated`, proceed with human consensus only (treat as if LLM was skipped).
- Retry once before giving up.

---

## 4. Nightly CSV Sync (`sync_csv.py`)

### Schedule
APScheduler `CronTrigger` — fires at **02:00 AM** daily, started at `app.py` startup.

### Steps
1. Fetch `extracted.csv` from `forrtproject/flora-extractor` on branch `feature/extract` via the GitHub raw content API (`api.github.com/repos/.../contents/...`)
2. Save **permanent dated copy**: `data/extracted_DD.MM.YYYY.csv`
3. Save **latest symlink**: `data/extracted_latest.csv` (overwritten each night)
4. Run import logic (same as `csv_to_db.py`): filter to resolved rows, skip already-imported `pair_id`s
5. Log: rows fetched, inserted, skipped, any errors

### Storage Layout
```
data/
├── extracted_14.05.2026.csv   ← permanent dated archive
├── extracted_15.05.2026.csv
└── extracted_latest.csv       ← overwritten nightly
```

### Environment Variables
```
GEMINI_API_KEY=...
GITHUB_TOKEN=...          # read-only fine-grained PAT
GITHUB_REPO=forrtproject/flora-extractor
GITHUB_BRANCH=feature/extract
```

---

## 5. API Compatibility

All existing frontend endpoints stay. Only the DB queries inside `app.py` change.

| Endpoint | Internal change |
|---|---|
| `POST /api/login` | Reads/writes `validators` instead of `coders` |
| `GET /api/next-pair` | Reads `unvalidated` + `record_metadata`; returns `pair_id` from `unvalidated.pair_id` + `record_id` |
| `POST /api/judge` | Writes to `validation_queue`; triggers consensus engine; updates `unvalidated` JSONB cols and `validators.total_judgements` |
| `GET /api/stats` | Reads from `validators` |
| `GET /api/leaderboard` | Reads from `validators` |
| `GET /api/onboarding` | Unchanged |
| `POST /api/onboarding/complete` | Writes to `validators.onboarded_at` |

`GET /api/next-pair` response gains one new field (`record_id`) that the current frontend ignores.

---

## 6. New Files

| File | Purpose |
|---|---|
| `db_schema.sql` | Full SQL for deploying all 5 tables from scratch |
| `db_migrate.sql` | Migration SQL for existing deployments (rename `coders`→`validators`, drop old tables, add new) |
| `consensus_engine.py` | Post-judgement consensus logic |
| `llm_validator.py` | Gemini Flash validator module |
| `sync_csv.py` | Nightly GitHub fetch + import |
| `csv_to_db.py` | Updated to use new schema (already exists, needs minor updates) |
| `app.py` | Updated DB queries + APScheduler startup |

---

## 7. Out of Scope (V2)

- Reputation-weighted voting (vote_score factored into tiebreaker math beyond simple majority)
- Automatic level upgrades based on accuracy_score
- Skipping LLM sanity check for high-level validators (`SKIP_LLM_ABOVE_LEVEL` hook is stubbed)
- Admin UI for resolving `need_review` records
