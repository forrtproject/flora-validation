# FLoRA Validation Database Schema

This document explains the PostgreSQL database used by the FLoRA validation
workflow. `db_schema.sql` is the executable authority; SQL snippets here are
abridged and may omit later idempotent migration columns.

---

## Overview

After Stage 3 produces `data/extracted.csv`, `csv_to_db.py` loads only resolved
replication/reproduction rows. The ten current methods and archived aliases are
centralized in `extractor_vocab.py`; see `CSV_SCHEMA.md` for the full boundary
contract.

The validation workflow is centered on these five tables (the repository also
owns admin, assignment, source-record, messaging, and configuration tables):

| Table | Purpose |
| --- | --- |
| `validators` | Registered validators with level, points, and accuracy tracking |
| `unvalidated` | One row per resolved (doi_r, doi_o) pair; tracks validation progress |
| `validation_queue` | Three rows per record (human_1, human_2, llm); individual validator slots |
| `validated` | Final consensus records — contains only authoritative validated values |
| `record_metadata` | Supplementary extraction data from extracted.csv |

---

## Unique Identifier

Every record in `unvalidated` gets a **random UUID4** (`record_id`) generated at
import time. This is the primary key linking all tables.

The original `pair_id` (MD5 hash from `extracted.csv`) is stored in both `unvalidated`
and `record_metadata` for API backward-compatibility and provenance tracing.

---

## Table Definitions

### `validators`

One row per registered validator.

```sql
CREATE TABLE validators (
    id                  SERIAL      PRIMARY KEY,
    email               TEXT        UNIQUE,
    code                TEXT        UNIQUE,
    handle              TEXT        UNIQUE NOT NULL,
    vote_score          INTEGER     NOT NULL DEFAULT 10,  -- points weight per vote
    validator_tier      INTEGER     NOT NULL DEFAULT 0,   -- 0=regular, 1=trusted, 2=senior
    total_judgements    INTEGER     NOT NULL DEFAULT 0,
    total_points        INTEGER     NOT NULL DEFAULT 0,
    skipped_count       INTEGER     NOT NULL DEFAULT 0,
    accuracy_score      FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    onboarded_at        TIMESTAMPTZ,
    last_login_at       TIMESTAMPTZ,
    last_seen_update    INTEGER     NOT NULL DEFAULT 0
);
```

**Current tiers:**

| `validator_tier` | Role | Extra authority |
| --- | --- | --- |
| 0 | Regular validator | Normal queue |
| 1 | Trusted validator | Eligible for trusted assignment/consensus paths |
| 2 | Senior validator | May use immediate senior rejection |

`vote_score` controls points and is independent of `validator_tier`. The obsolete
`level` column is dropped by the idempotent schema migration.

> **Deferred authentication warning:** `code` is currently plaintext and private
> validator routes trust a browser-supplied `coder_id`. The approved redesign adds
> hashed credentials and server-side sessions; see
> [PROJECT.md §19](PROJECT.md#19-deferred-security-work). Do not treat this table's
> integer primary key as proof of identity.

---

### `unvalidated`

One row per resolved `(doi_r, doi_o)` pair. Validator summaries are stored as JSONB
columns instead of 30+ flat columns.

```sql
CREATE TABLE unvalidated (
    record_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pair_id             TEXT        UNIQUE,        -- MD5 from extracted.csv

    -- Replication paper (`study_r` is the within-paper study number)
    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    title_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,

    -- Original paper (`study_o` is the within-paper study number)
    doi_o               TEXT,
    study_o             TEXT,
    title_o             TEXT,
    year_o              TEXT,
    url_o               TEXT,     -- derived: https://doi.org/{doi_o}
    ref_o               TEXT,

    -- Classification
    type                TEXT        CHECK (type IN ('replication', 'reproduction')),
    outcome             TEXT        CHECK (outcome IN (
                                        'success', 'failure', 'mixed',
                                        'uninformative', 'descriptive')),
    outcome_quote       TEXT,
    out_quote_source    TEXT,

    -- Workflow state
    validation_status   TEXT        NOT NULL DEFAULT 'unvalidated'
                                    CHECK (validation_status IN (
                                        'unvalidated', 'validation_inprogress',
                                        'validated', 'need_review')),
    is_tiebreaker       BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Validator summaries (JSONB — see JSONB Shapes section below)
    validator_1         JSONB,    -- null until human_1 slot is completed
    validator_2         JSONB,    -- null until human_2 slot is completed
    llm_validator       JSONB,    -- null until LLM runs

    -- Consensus-resolved final values (written at validation time)
    final_doi_o         TEXT,
    final_title_o       TEXT,
    final_outcome       TEXT,
    final_type          TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### JSONB Shapes

**`validator_1` / `validator_2`** (written by `POST /api/judge`):

```json
{
  "validator_id": 42,
  "validator_name": "handle",
  "vote_score": 10,
  "type_check": "correct",
  "original_check": "correct",
  "outcome_check": "incorrect",
  "corrected_doi_o": null,
  "corrected_title_o": null,
  "corrected_outcome": "failure",
  "corrected_type": null,
  "validator_notes": "Abstract clearly states failure",
  "points": 15,
  "validated_at": "2026-05-14T08:00:00+00:00"
}
```

**`llm_validator`** (written by `consensus_engine.py`):

```json
{
  "model": "gemini-2.0-flash",
  "context": "sanity_check",
  "vote_score": 15,
  "type_check": "correct",
  "original_check": "correct",
  "outcome_check": "incorrect",
  "corrected_outcome": "failure",
  "corrected_doi_o": null,
  "corrected_type": null,
  "notes": "Abstract says 'failed to replicate'",
  "validated_at": "2026-05-14T08:00:00+00:00"
}
```

On LLM error, the shape is:

```json
{
  "model": "gemini-2.0-flash",
  "context": "tiebreaker",
  "vote_score": 15,
  "error": "API timeout",
  "validated_at": "2026-05-14T08:00:00+00:00"
}
```

---

### `validation_queue`

Three rows per `record_id` — one per validator slot (`human_1`, `human_2`, `llm`).

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
    corrected_title_o   TEXT,
    corrected_outcome   TEXT,
    corrected_type      TEXT,

    additional_checks   JSONB,  -- extensible: {"was_unsure": true, "not_validation": true}

    validator_notes     TEXT,
    points              INTEGER     NOT NULL DEFAULT 0,
    shown_at            TIMESTAMPTZ,
    validated_at        TIMESTAMPTZ,

    UNIQUE (record_id, validator_slot)
);
```

---

### `validated`

Final consensus records. Contains **only** authoritative validated values — no
side-by-side original/corrected columns. If validators agreed with the extraction,
values match `unvalidated`; if they corrected a field, the corrected value is stored.

```sql
CREATE TABLE validated (
    validated_record_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id           UUID        NOT NULL REFERENCES unvalidated(record_id),

    -- Replication paper
    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    title_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,

    -- Original paper (final consensus value)
    doi_o               TEXT,
    study_o             TEXT,
    title_o             TEXT,
    year_o              TEXT,
    url_o               TEXT,
    ref_o               TEXT,

    -- Classification (final consensus value)
    type                TEXT,
    outcome             TEXT,
    outcome_quote       TEXT,
    out_quote_source    TEXT,

    validated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (doi_r, study_r, title_r, original_key, study_o, title_o)
);
```

> **Audit trail**: to see what changed during validation, compare `unvalidated.doi_o`
> with `validated.doi_o` for the same `record_id`.

---

### `validated_record_merges`

An admin resolution that would collide with another row's validated natural key does
not overwrite that row. The server first returns a conflict and requires a second,
explicit **Merge A into B** request. B remains the authoritative validated row; A is
retained in `unvalidated` with its metadata and judgements but removed from validated
output.

```sql
CREATE TABLE validated_record_merges (
    merge_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    duplicate_record_id   UUID        NOT NULL UNIQUE REFERENCES unvalidated(record_id),
    survivor_record_id    UUID        NOT NULL REFERENCES unvalidated(record_id),
    merged_by             TEXT        NOT NULL,
    merged_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolution_snapshot   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    CHECK (duplicate_record_id <> survivor_record_id)
);
```

The unique duplicate id makes the operation idempotent. `resolution_snapshot` records
the identity/classification the admin was attempting to publish; it does not replace
the complete A-side source and judgement history still attached to
`duplicate_record_id`.

---

### `admins` and authentication state

`admins` currently stores `password TEXT` and `trusted BOOLEAN`. Admin bearer tokens
are deterministic hashes of the plaintext password rather than independent session
rows. A known password fallback may seed a fresh database when `ADMIN_PASSWORD` is
missing. This section documents current behavior, not an acceptable target design.
The credential-hash, bootstrap, revocable-session, and CSRF migration is tracked in
[PROJECT.md §19](PROJECT.md#19-deferred-security-work).

---

### `record_metadata`

Supplementary extraction data not shown in the main UI.

```sql
CREATE TABLE record_metadata (
    metadata_id             UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id               UUID    NOT NULL UNIQUE REFERENCES unvalidated(record_id),
    pair_id                 TEXT,
    work_id                 BIGINT,
    release_id              TEXT,

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
    screen_categories       TEXT,

    outcome_confidence      TEXT,
    outcome_reasoning       TEXT,
    outcome_llm_model       TEXT,
    doi_o_verification      TEXT,
    pdf_source              TEXT,
    parse_method            TEXT,

    authors_r               TEXT,
    authors_o               TEXT,
    journal_r               TEXT,
    openalex_id_r           TEXT,
    source                  TEXT,
    bibtex_ref_o            TEXT,
    bibtex_ref_r            TEXT,

    original_rank           INTEGER,
    n_originals             INTEGER,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## Consensus State Machine

After each human validator submits, `consensus_engine.evaluate_consensus()` runs:

```text
Both humans complete
        │
        ├─ Checks agree AND corrections agree?
        │       YES → LLM sanity check (humans always win regardless of LLM verdict)
        │             → validation_status = 'validated'
        │             → INSERT into validated (final values from consensus)
        │
        ├─ Checks agree BUT corrections differ?
        │       → No LLM called
        │       → validation_status = 'need_review'
        │
        └─ Checks differ (humans disagree)?
                → LLM tiebreaker (context = 'tiebreaker')
                ├─ LLM matches H1 only → validated with H1 verdict
                ├─ LLM matches H2 only → validated with H2 verdict
                └─ 3-way split or LLM error → need_review
```

### Validation Status Values

| Status | Meaning |
| --- | --- |
| `unvalidated` | Record imported; no validator has started |
| `validation_inprogress` | At least one human slot assigned |
| `validated` | Consensus reached; record in `validated` table |
| `need_review` | Disagreement; requires core team adjudication |

---

## Nightly Sync

`sync_csv.py` downloads the latest `extracted.csv` from GitHub nightly at 2:00 AM UTC:

1. Fetches from `https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/data/extracted.csv`
2. Archives to `data/extracted_DD.MM.YYYY.csv`
3. Overwrites `data/extracted_latest.csv`
4. Calls `csv_to_db.run_import()` to insert new rows, refresh metadata for existing
   `pair_id`s, and safely re-key a corrected pair by `(work_id, original_rank)`

APScheduler starts the job when `app.py` loads.

---

## Import Script

```bash
python csv_to_db.py --input data/extracted.csv --release-id <routing-release>
```

Safe to re-run: existing records are metadata-refreshed. If an upstream correction
changes `pair_id`, the stable source slot is re-keyed; validator-touched rows are
routed to `need_review`. Duplicate input IDs and ambiguous slots fail the import.

Required environment variables:

```env
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

---

## Migration from Old Schema

If the database has the old `pairs` / `coders` / `judgements` schema, run:

```bash
python db_migrate.py
```

This copies all data into the new tables. The script is idempotent and safe to re-run.

---

## API Field Mapping (Frontend Compatibility)

The `GET /api/next-pair` response exposes the study number and paper title as
separate fields. Only `outcome_phrase` remains a frontend alias:

| Old frontend field | New DB column | Note |
| --- | --- | --- |
| `study_r` | `study_r` | Within-paper replication study number(s) |
| `title_r` | `title_r` | Replication paper title |
| `study_o` | `study_o` | Within-paper original study number(s) |
| `title_o` | `title_o` | Original paper title |
| `outcome_phrase` | `outcome_quote` | Both returned in next-pair response |
| `coder_id` | `validators.id` | API still uses `coder_id` key name |
| `pair_id` | `unvalidated.pair_id` | Same MD5, same field name |

The `POST /api/judge` endpoint uses the new field names:
`type_check`, `original_check`, `outcome_check`, `corrected_*`.
