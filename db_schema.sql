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

    -- Restricted-access workflow: set when a hard-mode validator can't open the
    -- article. Pulls the record out of circulation until an admin assigns it.
    restricted_access       BOOLEAN     NOT NULL DEFAULT FALSE,
    restricted_reported_by  INTEGER     REFERENCES validators(id),
    restricted_reported_at  TIMESTAMPTZ,

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
    corrected_url_r         TEXT,

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

-- Add consensus_reached + rejected to validation_status enum (drop + recreate constraint)
ALTER TABLE unvalidated DROP CONSTRAINT IF EXISTS unvalidated_validation_status_check;
ALTER TABLE unvalidated ADD CONSTRAINT unvalidated_validation_status_check
    CHECK (validation_status IN (
        'unvalidated', 'validation_inprogress',
        'validated', 'need_review', 'consensus_reached', 'rejected'));

-- Add cannot_be_determined to the outcome enum (drop + recreate constraint).
-- Used for hard-mode records whose outcome can't be determined from the abstract.
ALTER TABLE unvalidated DROP CONSTRAINT IF EXISTS unvalidated_outcome_check;
ALTER TABLE unvalidated ADD CONSTRAINT unvalidated_outcome_check
    CHECK (outcome IN (
        'success', 'failure', 'mixed',
        'uninformative', 'descriptive', 'cannot_be_determined'));

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

-- Replication title/DOI correction (typographical errors)
ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS corrected_study_r TEXT;
-- Validator-suggested replication URL (advisory; admin promotes to final_url_r)
ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS corrected_url_r   TEXT;
ALTER TABLE unvalidated      ADD COLUMN IF NOT EXISTS final_study_r     TEXT;
ALTER TABLE unvalidated      ADD COLUMN IF NOT EXISTS final_doi_r       TEXT;
ALTER TABLE unvalidated      ADD COLUMN IF NOT EXISTS final_abstract_r  TEXT;
ALTER TABLE unvalidated      ADD COLUMN IF NOT EXISTS final_url_r       TEXT;

-- Restricted-access workflow (hard-mode "I cannot access this article")
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS restricted_access      BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS restricted_reported_by INTEGER REFERENCES validators(id);
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS restricted_reported_at TIMESTAMPTZ;

-- Admin → validator assignments (restricted-access records handed to someone
-- who can open the article). One open assignment per record.
CREATE TABLE IF NOT EXISTS assignments (
    id           SERIAL      PRIMARY KEY,
    record_id    UUID        NOT NULL UNIQUE REFERENCES unvalidated(record_id),
    validator_id INTEGER     NOT NULL REFERENCES validators(id),
    assigned_by  TEXT,
    status       TEXT        NOT NULL DEFAULT 'open',   -- 'open' | 'done'
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_assignments_validator ON assignments (validator_id, status);

-- Prefetch buffer + tiered locking.
--   started_at IS NULL  → buffered (prefetched, not opened): short lock
--   started_at IS NOT NULL → started (active pair): 5-day lock
ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
-- Treat any slot currently held (pre-migration) as started so the reaper
-- doesn't immediately drop it as a stale buffered claim.
UPDATE validation_queue SET started_at = COALESCE(shown_at, NOW())
 WHERE is_shown = TRUE AND is_validated = FALSE AND started_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_vq_reaper
    ON validation_queue (is_shown, is_validated, started_at, shown_at);

-- Forgot-handle rate limiting
ALTER TABLE validators ADD COLUMN IF NOT EXISTS forgot_requests_today INTEGER NOT NULL DEFAULT 0;
ALTER TABLE validators ADD COLUMN IF NOT EXISTS forgot_requests_date  DATE;

-- Add 'rejected' status for records both validators agree are not replications
ALTER TABLE unvalidated DROP CONSTRAINT IF EXISTS unvalidated_validation_status_check;
ALTER TABLE unvalidated ADD CONSTRAINT unvalidated_validation_status_check
    CHECK (validation_status IN (
        'unvalidated', 'validation_inprogress',
        'validated', 'need_review', 'consensus_reached', 'rejected'));

-- Store agreed corrected outcome quote so admin_approve can use it
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS final_outcome_quote TEXT;

-- Outcome-quote source: 'abstract' when the final quote is found in the abstract,
-- else 'full_text'. Computed at consensus; the admin can override in the review
-- panel (out_quote_source_by records the admin handle when manually set).
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS final_out_quote_source TEXT;
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS out_quote_source_by    TEXT;
ALTER TABLE validated   ADD COLUMN IF NOT EXISTS out_quote_source_by    TEXT;

-- Admin notes: track who saved the note and when
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS note_saved_by  TEXT;
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS note_saved_at  TIMESTAMPTZ;

-- Admin override flag: set when admin validates a previously-rejected record
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS admin_override BOOLEAN NOT NULL DEFAULT FALSE;

-- Drop obsolete level column superseded by validator_tier
ALTER TABLE validators DROP COLUMN IF EXISTS level;

-- Admin flag on individual validator judgements
ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS flagged BOOLEAN NOT NULL DEFAULT FALSE;

-- Last login timestamp for validators
ALTER TABLE validators ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

-- Site-wide admin broadcast banner (single row, id = 1)
CREATE TABLE IF NOT EXISTS site_banner (
    id          INTEGER     PRIMARY KEY DEFAULT 1,
    message     TEXT,
    active      BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO site_banner (id, message, active)
VALUES (1, NULL, FALSE) ON CONFLICT (id) DO NOTHING;

-- Flag reason stored alongside the flag (set when admin flags, cleared on unflag)
ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS flag_reason TEXT;

-- Validator inbox: system messages created when an admin flags a judgement with a reason
CREATE TABLE IF NOT EXISTS validator_messages (
    id           SERIAL      PRIMARY KEY,
    validator_id INTEGER     NOT NULL REFERENCES validators(id),
    subject      TEXT        NOT NULL,
    body         TEXT        NOT NULL,
    is_read      BOOLEAN     NOT NULL DEFAULT FALSE,
    sent_by      TEXT,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Phase 3: two-way messaging
-- 'outbound' = admin → validator  |  'inbound' = validator → admin (reply)
ALTER TABLE validator_messages ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'outbound';
-- links a reply to the message it responds to
ALTER TABLE validator_messages ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES validator_messages(id);
-- for inbound messages: has an admin read this reply yet?
ALTER TABLE validator_messages ADD COLUMN IF NOT EXISTS is_read_by_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Update screen: shown once per version to returning (onboarded) validators
ALTER TABLE validators ADD COLUMN IF NOT EXISTS last_seen_update INTEGER NOT NULL DEFAULT 0;

-- My Judgements: link flag messages back to the specific judgement that triggered them
ALTER TABLE validator_messages ADD COLUMN IF NOT EXISTS queue_id UUID REFERENCES validation_queue(queue_id);
