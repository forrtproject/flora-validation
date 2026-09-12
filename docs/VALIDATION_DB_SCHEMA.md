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

The validation workflow is centered on these seven tables (the repository also
owns admin, assignment, source-record, messaging, and configuration tables):

| Table | Purpose |
| --- | --- |
| `validators` | Registered validators with level, points, and accuracy tracking |
| `unvalidated` | One row per resolved (doi_r, doi_o) pair; tracks validation progress |
| `validation_queue` | Three rows per record (human_1, human_2, llm); individual validator slots |
| `validation_skips` | Append-only reason/comment history for released validator claims |
| `submission_failure_releases` | Server-controlled audit and one-time capability for failed background saves |
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

### `validation_skips`

One immutable event per successful Skip action. The history is separate from
`validation_queue` because releasing a claim clears and reuses that queue slot.

```sql
CREATE TABLE validation_skips (
    skip_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id     UUID        NOT NULL REFERENCES unvalidated(record_id),
    validator_id  INTEGER     NOT NULL REFERENCES validators(id),
    queue_id      UUID        REFERENCES validation_queue(queue_id),
    reason_code   TEXT        NOT NULL CHECK (reason_code IN (
                                  'prefer_another', 'inaccessible',
                                  'eligibility_unclear', 'data_quality',
                                  'interpretation_unclear', 'other',
                                  'submission_failed')),
    comment       TEXT        CHECK (comment IS NULL OR char_length(comment) <= 1000),
    skipped_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`submission_failed` is retained in the database constraint only for historical
rows written by the former recovery design. `/api/skip` no longer accepts it and
new automatic releases never write to this table.

The admin Skipped panel counts **distinct validators**, not events. It includes a
record after more than five distinct validators skip for any reason, or after two
distinct validators report eligibility/data-quality concerns. The detail API joins
`validator_id` to `validators.handle`; names and comments are admin-only.

---

### `submission_failure_releases`

One audit row per browser-generated `submission_id`. `/api/judge` can create or
rotate the capability only after the server observes a pre-commit save failure and
verifies that the same validator still owns the unfinished slot. The raw random
stamp is returned to the browser once; PostgreSQL stores only its SHA-256 digest.

```sql
CREATE TABLE submission_failure_releases (
    failure_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id   UUID NOT NULL UNIQUE,
    queue_id        UUID NOT NULL REFERENCES validation_queue(queue_id) ON DELETE CASCADE,
    record_id       UUID NOT NULL REFERENCES unvalidated(record_id) ON DELETE CASCADE,
    validator_id    INTEGER NOT NULL REFERENCES validators(id),
    status          TEXT NOT NULL CHECK (status IN
                        ('save_failed', 'released', 'slot_closed', 'expired')),
    stamp_hash      TEXT NOT NULL UNIQUE CHECK (char_length(stamp_hash) = 64),
    failure_code    TEXT NOT NULL,
    failure_message TEXT,
    failed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    released_at     TIMESTAMPTZ
);
```

`POST /api/submission-failures/release` accepts only the opaque stamp, not a
client-supplied validator or record identity. It locks record, queue, then audit
row and can clear only the bound unfinished slot. `released` and `slot_closed`
consume the capability; `expired` requires a new server-confirmed failure before
release can be attempted again. These rows do not contribute to `skipped_count`
or the admin Skipped panel. Admin record detail exposes the safe audit fields but
never `stamp_hash`. The two-minute stale-slot reaper marks elapsed `save_failed`
rows as `expired`, even if no browser attempts to consume them.

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
are deterministic hashes of the stored Argon2id hash rather than independent session
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

`extractor_maintenance.run_scheduled()` starts nightly at 02:00 UTC and records
each run in:

```sql
CREATE TABLE extractor_maintenance_runs (
    run_id UUID PRIMARY KEY,
    trigger TEXT,             -- scheduled | admin | cli
    requested_stage TEXT,     -- full | sync | find | cleanup
    requested_by TEXT,
    status TEXT,              -- queued/running/success/warning/blocked/failed
    created_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    stage_status JSONB,
    safety_report JSONB,
    log_text TEXT
);
```

The partial unique index `uq_extractor_maintenance_one_active` permits only one
queued/running history reservation. A session-level PostgreSQL advisory lock is
held by `extractor_maintenance.py` for the complete child-process lifetime, so
only one live operation can execute across all app workers. History is not
automatically purged, and the admin API returns at least the previous seven days.
Manual HTTP 202 responses commit the queued row only; a ten-second dispatcher
poll executes it, so pod shutdown after the response cannot lose the request.
If a running worker disappears, another lock holder requeues the same run.

For a routine `full` run (the nightly job and the primary admin action):

1. Fetches from `https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/data/extracted.csv`
2. Exclusively archives to
   `data/extracted_YYYYMMDDTHHMMSSZ_<run-id>.csv` (collision suffixes preserve
   every same-second retry)
3. Compares unique resolved IDs against the baseline the orchestrator names from
   run history — the previous run's archive, verified by sha256 — and blocks when
   that baseline is missing or stale while `unvalidated` is populated
4. Blocks an empty/zero-resolved candidate as an extractor error
5. Blocks when removed resolved IDs are more than 10% of the previous set
   (`EXTRACTOR_MAX_REMOVAL_PERCENT` overrides the threshold and must be finite
   and within 0 through 100)
6. Records newly added resolved IDs as a warning but allows the import
7. Calls `csv_to_db.run_import()` to insert/refresh/re-key rows
8. Atomically promotes the candidate only after import succeeds, verifies the
   promoted bytes, reads the archive back to record `archive_file`/`archive_sha256`,
   and records all Part 1 completion flags for that `run_id`
9. Runs read-only orphan reporting against that exact archived snapshot and stops.

Routine and scheduled runs never select `cleanup_orphans.py`, even when the
omission is below the 10% synchronization threshold. Deletion requires a separate
manual `cleanup` request with explicit confirmation. That request rechecks the
persisted Part 1/2 run IDs and archive digest, then writes exact deleted identities
and per-table counts to `safety_report.cleanup_receipt` in the same transaction as
the DELETE statements. Crash recovery can therefore finalize a committed cleanup
without repeating it.

Any sync failure or safety block skips orphan reporting, so the old latest CSV
cannot become a mass-deletion baseline. Admins can launch the non-destructive
Sync + Report operation or each individual stage from the Extractor Pipeline tab
and inspect the complete retained log.
Standalone Part 2/3 requests are linked through `safety_report.source_sync_run_id`
and `source_find_run_id`. They use only the newest prerequisite attempt and block
when it is incomplete, even if an older run succeeded. The run IDs are paired with
`safety_report.archive_file` and `archive_sha256`: the stage resolves that archive
on `EXTRACTOR_DATA_DIR` and verifies its digest before reading it, so a pod without
the shared volume blocks rather than acting on a different snapshot.

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
