-- db_schema.sql
-- Run against a fresh PostgreSQL database to create all tables.
-- Safe to re-run (all statements are idempotent via IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS validators (
    id                  SERIAL      PRIMARY KEY,
    email               TEXT        UNIQUE,
    code                TEXT        UNIQUE,
    handle              TEXT        UNIQUE NOT NULL,
    level               INTEGER     NOT NULL DEFAULT 1,
    vote_score          INTEGER     NOT NULL DEFAULT 10,
    total_judgements    INTEGER     NOT NULL DEFAULT 0,
    total_points        INTEGER     NOT NULL DEFAULT 0,
    skipped_count       INTEGER     NOT NULL DEFAULT 0,
    accuracy_score      FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    onboarded_at        TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS validators_email_key
    ON validators(email) WHERE email IS NOT NULL;

-- One row per resolved (doi_r, doi_o) pair.
-- Validator summaries stored as JSONB instead of 30+ flat columns.
CREATE TABLE IF NOT EXISTS unvalidated (
    record_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pair_id             TEXT        UNIQUE,        -- MD5 from extracted.csv, for API lookup

    -- Replication paper display columns
    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,

    -- Original study display columns
    doi_o               TEXT,
    study_o             TEXT,
    year_o              TEXT,
    url_o               TEXT,
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

    -- Validator summaries (JSONB — see docs/VALIDATION_DB_SCHEMA.md for shape)
    validator_1         JSONB,      -- null until human_1 slot is completed
    validator_2         JSONB,      -- null until human_2 slot is completed
    llm_validator       JSONB,      -- null until LLM runs

    -- Consensus-resolved final values (written at validation time)
    final_doi_o         TEXT,
    final_study_o       TEXT,
    final_outcome       TEXT,
    final_type          TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Three rows per record_id: human_1, human_2, llm.
CREATE TABLE IF NOT EXISTS validation_queue (
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

    -- Filled only when the corresponding check = 'incorrect'
    corrected_doi_o         TEXT,
    corrected_study_o       TEXT,
    corrected_outcome       TEXT,
    corrected_type          TEXT,
    corrected_outcome_quote TEXT,
    corrected_abstract      TEXT,

    -- Extensible: {"was_unsure_original": true, "not_validation": true, …}
    additional_checks   JSONB,

    validator_notes     TEXT,
    points              INTEGER     NOT NULL DEFAULT 0,
    shown_at            TIMESTAMPTZ,
    validated_at        TIMESTAMPTZ,

    UNIQUE (record_id, validator_slot)
);

-- Final consensus records — contains only authoritative validated values.
-- If validators agreed with extraction, values match unvalidated; if corrected, stores corrections.
CREATE TABLE IF NOT EXISTS validated (
    validated_record_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id           UUID        NOT NULL REFERENCES unvalidated(record_id),

    -- Replication paper (never changes during validation)
    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,

    -- Original study (final consensus value)
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

-- Supplementary extraction data from extracted.csv not shown in the main UI.
CREATE TABLE IF NOT EXISTS record_metadata (
    metadata_id             UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id               UUID    NOT NULL UNIQUE REFERENCES unvalidated(record_id),

    pair_id                 TEXT,   -- MD5 from extracted.csv (provenance)

    -- Stage 2 filter info
    filter_status           TEXT,
    filter_method           TEXT,
    filter_evidence         TEXT,
    filter_confidence       TEXT,

    -- Stage 3 match-type info
    original_match_type     TEXT,
    original_match_confidence TEXT,

    -- Stage 3 linking info
    link_method             TEXT,
    link_evidence           TEXT,
    link_confidence         TEXT,
    link_llm_model          TEXT,

    -- Outcome detail
    outcome_confidence      TEXT,

    -- Bibliographic info
    authors_r               TEXT,
    authors_o               TEXT,
    journal_r               TEXT,
    openalex_id_r           TEXT,
    source                  TEXT,

    -- Multi-original bookkeeping
    original_rank           INTEGER,
    n_originals             INTEGER,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add consensus_reached to validation_status enum (drop + recreate constraint)
ALTER TABLE unvalidated DROP CONSTRAINT IF EXISTS unvalidated_validation_status_check;
ALTER TABLE unvalidated ADD CONSTRAINT unvalidated_validation_status_check
    CHECK (validation_status IN (
        'unvalidated', 'validation_inprogress',
        'validated', 'need_review', 'consensus_reached'));

-- Admin approval column on validated table
ALTER TABLE validated ADD COLUMN IF NOT EXISTS admin_approved BOOLEAN NOT NULL DEFAULT FALSE;

-- Admin columns (idempotent — safe to re-run on existing databases)
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS admin_checked BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS admin_name    TEXT;
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS admin_notes   TEXT;

-- Validator tier: 0 = regular, 1 = trusted, 2 = senior (senior implies trusted)
ALTER TABLE validators ADD COLUMN IF NOT EXISTS validator_tier INTEGER NOT NULL DEFAULT 0;

-- Migrate existing trusted/senior booleans into validator_tier (safe on re-run — skipped if columns already dropped)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'validators' AND column_name = 'senior') THEN
    UPDATE validators SET validator_tier = 2 WHERE senior = TRUE;
    UPDATE validators SET validator_tier = 1 WHERE trusted = TRUE AND senior = FALSE;
  END IF;
END $$;

ALTER TABLE validators DROP COLUMN IF EXISTS trusted;
ALTER TABLE validators DROP COLUMN IF EXISTS senior;

-- Named admin accounts (multiple admins with individual handles)
CREATE TABLE IF NOT EXISTS admins (
    id         SERIAL PRIMARY KEY,
    handle     TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Trusted admin flag (only trusted admins can add/remove admin accounts)
ALTER TABLE admins ADD COLUMN IF NOT EXISTS trusted BOOLEAN NOT NULL DEFAULT FALSE;

-- Replication title correction (typographical errors)
ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS corrected_study_r TEXT;
ALTER TABLE unvalidated      ADD COLUMN IF NOT EXISTS final_study_r     TEXT;

-- Forgot-handle rate limiting
ALTER TABLE validators ADD COLUMN IF NOT EXISTS forgot_requests_today INTEGER NOT NULL DEFAULT 0;
ALTER TABLE validators ADD COLUMN IF NOT EXISTS forgot_requests_date  DATE;
