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
                                        'successful', 'failed', 'mixed',
                                        'uninformative', 'descriptive', 'cannot_be_determined',
                                        'computationally successful, robust',
                                        'computationally successful, robustness challenges',
                                        'computation not checked, robust',
                                        'computation not checked, robustness challenges',
                                        'computational issues, robustness challenges')),
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

-- Outcome enum: includes cannot_be_determined (hard-mode records whose outcome can't
-- be determined from the abstract) and the reproduction labels.
--
-- The replication labels were renamed success -> successful and failure -> failed so the
-- stored vocabulary matches what the FLoRA export publishes. The constraint is dropped
-- first so the rename below can run, then re-added with the new vocabulary. All of this
-- executes in one transaction (init_db runs the whole file), so it is atomic; the UPDATEs
-- are idempotent and match zero rows once the rename has been applied.
ALTER TABLE unvalidated DROP CONSTRAINT IF EXISTS unvalidated_outcome_check;

UPDATE unvalidated      SET outcome          = 'successful' WHERE outcome          = 'success';
UPDATE unvalidated      SET outcome          = 'failed'     WHERE outcome          = 'failure';
UPDATE unvalidated      SET final_outcome    = 'successful' WHERE final_outcome    = 'success';
UPDATE unvalidated      SET final_outcome    = 'failed'     WHERE final_outcome    = 'failure';
UPDATE validated        SET outcome          = 'successful' WHERE outcome          = 'success';
UPDATE validated        SET outcome          = 'failed'     WHERE outcome          = 'failure';
UPDATE validation_queue SET corrected_outcome = 'successful' WHERE corrected_outcome = 'success';
UPDATE validation_queue SET corrected_outcome = 'failed'     WHERE corrected_outcome = 'failure';

-- The same label is mirrored inside the validator/LLM JSONB summaries.
UPDATE unvalidated SET validator_1 = jsonb_set(validator_1, '{corrected_outcome}', '"successful"')
    WHERE validator_1->>'corrected_outcome' = 'success';
UPDATE unvalidated SET validator_1 = jsonb_set(validator_1, '{corrected_outcome}', '"failed"')
    WHERE validator_1->>'corrected_outcome' = 'failure';
UPDATE unvalidated SET validator_2 = jsonb_set(validator_2, '{corrected_outcome}', '"successful"')
    WHERE validator_2->>'corrected_outcome' = 'success';
UPDATE unvalidated SET validator_2 = jsonb_set(validator_2, '{corrected_outcome}', '"failed"')
    WHERE validator_2->>'corrected_outcome' = 'failure';
UPDATE unvalidated SET llm_validator = jsonb_set(llm_validator, '{corrected_outcome}', '"successful"')
    WHERE llm_validator->>'corrected_outcome' = 'success';
UPDATE unvalidated SET llm_validator = jsonb_set(llm_validator, '{corrected_outcome}', '"failed"')
    WHERE llm_validator->>'corrected_outcome' = 'failure';

ALTER TABLE unvalidated ADD CONSTRAINT unvalidated_outcome_check
    CHECK (outcome IN (
        'successful', 'failed', 'mixed',
        'uninformative', 'descriptive', 'cannot_be_determined',
        'computationally successful, robust',
        'computationally successful, robustness challenges',
        'computation not checked, robust',
        'computation not checked, robustness challenges',
        'computational issues, robustness challenges'));

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

-- Pool serving priority (single-row config). Lets admins bias which records are
-- served for validation — e.g. prioritise 'failed' replications from 2011–2021.
-- Disabled by default → serving stays fully random, exactly like before.
CREATE TABLE IF NOT EXISTS serving_config (
    id                INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled           BOOLEAN     NOT NULL DEFAULT FALSE,
    priority_outcome  TEXT,                              -- 'failed' | 'successful' | 'mixed' | NULL
    priority_year_min INTEGER,
    priority_year_max INTEGER,
    priority_share    INTEGER     NOT NULL DEFAULT 70 CHECK (priority_share BETWEEN 0 AND 100),
    updated_by        TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Seed the single row with the "failed · 2011–2021 · 70%" preset PRE-FILLED but
-- DISABLED, so turning it on applies that preset immediately.
INSERT INTO serving_config (id, enabled, priority_outcome, priority_year_min, priority_year_max, priority_share)
VALUES (1, FALSE, 'failed', 2011, 2021, 70)
ON CONFLICT (id) DO NOTHING;

-- OpenAlex work IDs (bare 'W…') carried alongside the DOIs for both the original (_o)
-- and replication (_r) papers. Populated from the extractor CSV going forward, and
-- backfilled from OpenAlex by DOI for existing rows (backfill_oa_work_ids.py).
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS oa_work_id_o TEXT;
ALTER TABLE unvalidated ADD COLUMN IF NOT EXISTS oa_work_id_r TEXT;
ALTER TABLE validated   ADD COLUMN IF NOT EXISTS oa_work_id_o TEXT;
ALTER TABLE validated   ADD COLUMN IF NOT EXISTS oa_work_id_r TEXT;

-- Published-article DOI for preprint replications (validator-suggested, admin-verified)
-- and free-form alternative identifiers (admin-curated during review; comma separated —
-- another DOI, a different version, a meta paper). alt_identifier_r mirrors the
-- source_records column of the same name.
ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS doi_r_published  TEXT;
ALTER TABLE unvalidated      ADD COLUMN IF NOT EXISTS doi_r_published  TEXT;
ALTER TABLE validated        ADD COLUMN IF NOT EXISTS doi_r_published  TEXT;
ALTER TABLE unvalidated      ADD COLUMN IF NOT EXISTS alt_identifier_r TEXT;
ALTER TABLE validated        ADD COLUMN IF NOT EXISTS alt_identifier_r TEXT;

-- DOI-less originals (books, chapters, pre-DOI papers): the extractor marks them
-- 'no_doi' in doi_o_verification and ships doi_o = '' with identity in
-- oa_work_id_o / title_o / url_o (an OpenAlex link). NOTE: doi_o stays '' (never
-- NULL) on these rows — the validated natural key and its ON CONFLICT clause rely
-- on equality, and Postgres treats NULLs as never-equal in unique constraints.
ALTER TABLE record_metadata ADD COLUMN IF NOT EXISTS doi_o_verification TEXT;

-- Normalise empty strings to NULL. The seed/backfill below (and the trigger) key on
-- IS NULL, so a stray '' would be treated as 'already filled' and never get an id.
UPDATE unvalidated SET oa_work_id_o = NULL WHERE oa_work_id_o = '';
UPDATE unvalidated SET oa_work_id_r = NULL WHERE oa_work_id_r = '';
UPDATE validated   SET oa_work_id_o = NULL WHERE oa_work_id_o = '';
UPDATE validated   SET oa_work_id_r = NULL WHERE oa_work_id_r = '';

-- Seed the replication work ID from the extractor's record_metadata.openalex_id_r
-- (a full 'https://openalex.org/W…' URL → keep just the bare 'W…'). Idempotent: only
-- fills rows that don't already have one.
--
-- IMPORTANT: this schema re-runs on every startup, and record_metadata.openalex_id_r is
-- extraction-time data that is NEVER updated on a correction. So we must NOT re-seed a
-- replication whose DOI was corrected — the trigger deliberately NULLed its work id, and
-- re-seeding here would restore the STALE id for the old DOI. Rows with a changed doi_r
-- are left for backfill_oa_work_ids.py, which fetches by the corrected DOI.
UPDATE unvalidated u
   SET oa_work_id_r = substring(m.openalex_id_r FROM 'W[0-9]+')
  FROM record_metadata m
 WHERE m.record_id = u.record_id
   AND u.oa_work_id_r IS NULL
   AND (u.final_doi_r IS NULL OR u.final_doi_r = u.doi_r)
   AND m.openalex_id_r ~ 'W[0-9]+';

-- Option A: clear a stale work ID whenever the paper's effective DOI changes (a
-- correction), so the backfill re-fetches it for the new DOI. Centralised in a trigger
-- so every write path (consensus, admin resolve, assignment) is covered automatically.
CREATE OR REPLACE FUNCTION clear_stale_oa_work_id() RETURNS trigger AS $$
BEGIN
    IF COALESCE(NEW.final_doi_o, NEW.doi_o) IS DISTINCT FROM COALESCE(OLD.final_doi_o, OLD.doi_o) THEN
        NEW.oa_work_id_o := NULL;
    END IF;
    IF COALESCE(NEW.final_doi_r, NEW.doi_r) IS DISTINCT FROM COALESCE(OLD.final_doi_r, OLD.doi_r) THEN
        NEW.oa_work_id_r := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_clear_stale_oa_work_id ON unvalidated;
CREATE TRIGGER trg_clear_stale_oa_work_id
    BEFORE UPDATE ON unvalidated
    FOR EACH ROW EXECUTE FUNCTION clear_stale_oa_work_id();

-- Backfill EXISTING validated rows' work IDs from their unvalidated source (by
-- record_id). Rows validated AFTER the feature set these at insert time; this covers
-- rows validated before the feature existed, so the export (which reads validated) is
-- never blank for a paper whose id we already know. Idempotent: only fills NULLs, and
-- only from a non-NULL source (a source that was NULLed by a DOI correction is left for
-- the backfill to re-fetch, then this copies the corrected id on the next run).
UPDATE validated v
   SET oa_work_id_r = u.oa_work_id_r
  FROM unvalidated u
 WHERE u.record_id = v.record_id
   AND v.oa_work_id_r IS NULL
   AND u.oa_work_id_r IS NOT NULL;
UPDATE validated v
   SET oa_work_id_o = u.oa_work_id_o
  FROM unvalidated u
 WHERE u.record_id = v.record_id
   AND v.oa_work_id_o IS NULL
   AND u.oa_work_id_o IS NOT NULL;

-- ============================================================================
-- Entry-sheet source records (see sources.yml)
-- ============================================================================
-- One row per accepted row of the FLoRA entry sheets (replications /
-- reproductions). Populated by sync_sources.py.
--
-- INSERT-ONLY: a row is written the first time it is seen as accepted and is
-- never updated or deleted by the sync. Upstream sheet edits after that point
-- are ignored — this database is authoritative once a row lands, and changes
-- made in the web app never need to go back to the sheet.
--
-- Identity is the static UUID the sheet carries in its `id` column (written by
-- Apps Script, never regenerated, never reused). Keyed on (source, sheet_row_id)
-- so the two tabs can never collide even if their id spaces overlap.
--
-- Values are stored as the sheet has them — dirty DOIs and implausible years
-- included. Cleaning happens in the downstream transform, so a reviewer can see
-- and fix the real problem rather than having it silently normalised away.
CREATE TABLE IF NOT EXISTS source_records (
    record_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    source              TEXT        NOT NULL,   -- registry key: replications | reproductions
    sheet_row_id        TEXT        NOT NULL,   -- static UUID from the sheet's id column
    display_id          TEXT        UNIQUE,     -- human-facing, e.g. REPL-000847
    type                TEXT        NOT NULL CHECK (type IN ('replication', 'reproduction')),

    -- Shared across both sheets
    ref_o               TEXT,
    doi_o               TEXT,
    url_o               TEXT,
    ref_r               TEXT,
    doi_r               TEXT,
    url_r               TEXT,
    abstract_r          TEXT,

    -- replications only (NULL on reproductions)
    outcome             TEXT,
    outcome_quote       TEXT,
    out_quote_source    TEXT,
    year_r              TEXT,       -- sheet's `year`; text because values are dirty (0, 2366)
    alt_identifier_o    TEXT,
    alt_identifier_r    TEXT,

    -- reproductions only (NULL on replications). The two outcome dimensions are
    -- kept unmerged; the single FLoRA `outcome` label is derived downstream.
    study_o                         TEXT,
    outcome_computational           TEXT,
    outcome_computational_quote     TEXT,
    out_quote_computational_source  TEXT,
    outcome_robustness              TEXT,
    outcome_robustness_quote        TEXT,
    out_quote_robust_source         TEXT,

    -- Merged from validation_status (replications) / validation (reproductions)
    validation_status   TEXT,

    -- Derived from OpenAlex by DOI (cache-backed). Cleared by trigger when the
    -- corresponding DOI is edited, so the backfill re-fetches for the new paper.
    oa_work_id_o        TEXT,
    oa_work_id_r        TEXT,

    -- Complete sheet row, verbatim, keys exactly as the header reads. Promoted
    -- columns above are a projection of this, never a replacement — so anything
    -- not promoted (prep_notes, quote validated, Coder, validator_notes, …) is
    -- still here and can be promoted later by backfill instead of re-download.
    raw                 JSONB,

    -- Duplicate detector: normalised doi_o + doi_r/url_r. NOT an identity key —
    -- only used to flag two different UUIDs describing the same paper.
    content_fingerprint TEXT,

    -- Review state (Stage 8). Stamped even when a save changes nothing, so
    -- "checked and correct" is distinguishable from "not yet checked".
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    version             INTEGER     NOT NULL DEFAULT 1,

    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source, sheet_row_id)
);

CREATE INDEX IF NOT EXISTS idx_source_records_type       ON source_records (type);
CREATE INDEX IF NOT EXISTS idx_source_records_status     ON source_records (validation_status);
CREATE INDEX IF NOT EXISTS idx_source_records_reviewed   ON source_records (reviewed_at NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_source_records_fingerprint
    ON source_records (content_fingerprint) WHERE content_fingerprint IS NOT NULL;

-- Append-only edit history. reviewed_by/reviewed_at on the row carry the LAST
-- review for cheap rendering; this carries the full history.
CREATE TABLE IF NOT EXISTS source_record_edits (
    edit_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id   UUID        NOT NULL REFERENCES source_records(record_id) ON DELETE CASCADE,
    field       TEXT        NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    edited_by   TEXT        NOT NULL,   -- admin handle from _require_admin()
    edited_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_record_edits_record
    ON source_record_edits (record_id, edited_at DESC);

-- One row per sync run per source. Feeds the freshness banner above the grid —
-- without it nobody can tell whether the table is current, and stale-but-plausible
-- data is worse than obviously-broken data.
CREATE TABLE IF NOT EXISTS source_sync_runs (
    run_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL CHECK (status IN ('ok', 'unchanged', 'failed', 'skipped')),
    failure_reason  TEXT,               -- which gate failed, and why

    rows_fetched    INTEGER,
    rows_accepted   INTEGER,
    rows_inserted   INTEGER,
    rows_existing   INTEGER,
    rows_skipped    INTEGER,            -- blank sheet_row_id
    dup_ids         INTEGER,            -- same UUID twice within the sheet
    dup_fingerprints INTEGER,           -- same paper under two UUIDs

    payload_sha256  TEXT,               -- gate 6: identical hash => source skipped
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_sync_runs_recent
    ON source_sync_runs (source, started_at DESC);

-- Per-source display_id counters. Sequential, assigned once, never reused —
-- a raw UUID is unreadable aloud or in Slack, so rows also get REPL-000847.
CREATE TABLE IF NOT EXISTS source_display_counters (
    source      TEXT    PRIMARY KEY,
    -- Stores the LAST value handed out, not the next one: _next_display_id
    -- returns the post-increment value. A reader taking "next" at face value
    -- would reissue the id just used and collide on the display_id index.
    last_value  INTEGER NOT NULL DEFAULT 1
);

-- Editing a DOI in the web app makes the derived OpenAlex work id point at the
-- wrong paper. Same guard the unvalidated table already has (trg_clear_stale_oa_work_id).
CREATE OR REPLACE FUNCTION clear_stale_source_oa_work_id() RETURNS trigger AS $$
BEGIN
    IF NEW.doi_o IS DISTINCT FROM OLD.doi_o THEN
        NEW.oa_work_id_o := NULL;
    END IF;
    IF NEW.doi_r IS DISTINCT FROM OLD.doi_r THEN
        NEW.oa_work_id_r := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_clear_stale_source_oa_work_id ON source_records;
CREATE TRIGGER trg_clear_stale_source_oa_work_id
    BEFORE UPDATE ON source_records
    FOR EACH ROW EXECUTE FUNCTION clear_stale_source_oa_work_id();

-- ============================================================================
-- Source records: duplicate resolution + transform rule tables (phase 4)
-- ============================================================================

-- A paper can arrive under two different sheet UUIDs (re-entered rather than
-- copied). The sync flags these via content_fingerprint but never blocks the
-- insert; a human decides here, and the transform honours the decision.
ALTER TABLE source_records ADD COLUMN IF NOT EXISTS duplicate_status TEXT
    CHECK (duplicate_status IN ('distinct', 'duplicate'));
ALTER TABLE source_records ADD COLUMN IF NOT EXISTS duplicate_of UUID
    REFERENCES source_records(record_id);
ALTER TABLE source_records ADD COLUMN IF NOT EXISTS duplicate_reviewed_by TEXT;
ALTER TABLE source_records ADD COLUMN IF NOT EXISTS duplicate_reviewed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_source_records_dupstatus
    ON source_records (duplicate_status) WHERE content_fingerprint IS NOT NULL;

-- Rows dropped from the transform output. Replaces the exclusions sheet and the
-- hardcoded manual_exclusion_dois list in the R notebook: these are decisions,
-- not data, and changing one should not require editing R.
CREATE TABLE IF NOT EXISTS transform_exclusions (
    exclusion_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    doi_r        TEXT,
    url_r        TEXT,
    reason       TEXT        NOT NULL,
    added_by     TEXT,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (doi_r IS NOT NULL OR url_r IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_transform_exclusions_doi ON transform_exclusions (lower(doi_r));
CREATE INDEX IF NOT EXISTS idx_transform_exclusions_url ON transform_exclusions (lower(url_r));

-- Replication outcome spellings drift between sources. Mapped at transform time
-- rather than stored, so fixing a spelling is a row here and a re-run, never a
-- migration over stored data.
CREATE TABLE IF NOT EXISTS outcome_alias (
    raw_value       TEXT PRIMARY KEY,
    canonical_value TEXT NOT NULL
);

INSERT INTO outcome_alias (raw_value, canonical_value) VALUES
    ('successful',      'successful'),
    ('failed',          'failed'),
    ('mixed',           'mixed'),
    ('uninformative',   'uninformative'),
    ('unclear',         'cannot_be_determined'),
    ('descriptive only','descriptive'),
    ('statistically successful but flawed',                'mixed'),
    ('statistically_successful_but_fundamentally_flawed',  'mixed')
ON CONFLICT (raw_value) DO NOTHING;

-- Reproductions carry a 2-D outcome (computational x robustness). The single
-- FLoRA label is derived from this table, so an unseen combination is a row to
-- add rather than a stored value to migrate.
CREATE TABLE IF NOT EXISTS reproduction_outcome_map (
    computational TEXT NOT NULL,
    robustness    TEXT NOT NULL,
    canonical     TEXT NOT NULL,
    PRIMARY KEY (computational, robustness)
);

INSERT INTO reproduction_outcome_map (computational, robustness, canonical) VALUES
    ('computationally reproducible', 'robust',                'computationally successful, robust'),
    ('computationally reproducible', 'robustness challenges', 'computationally successful, robustness challenges'),
    ('computationally reproducible', 'not checked',           'computationally successful, robustness not checked'),
    ('not checked',                  'robust',                'computation not checked, robust'),
    ('not checked',                  'robustness challenges', 'computation not checked, robustness challenges'),
    ('not checked',                  'not checked',           'cannot_be_determined'),
    ('computational issues',         'robustness challenges', 'computational issues, robustness challenges'),
    -- Two combinations present in the accepted rows that the original
    -- unvalidated CHECK vocabulary never covered.
    ('computational issues',         'robust',                'computational issues, robust'),
    ('computational issues',         'not checked',           'computational issues, robustness not checked'),
    ('failed',                       'not checked',           'failed'),
    ('failed',                       'robust',                'failed'),
    ('failed',                       'robustness challenges', 'failed')
ON CONFLICT (computational, robustness) DO NOTHING;

-- ============================================================================
-- Source records: correctness fixes found in review
-- ============================================================================

-- content_fingerprint must move when a reviewer corrects a DOI. Computing it in
-- Python at insert time froze it to the pre-edit strings, which both hid real
-- duplicates and left false flags a reviewer could act on by marking a good row
-- 'duplicate' (removing it from the transform). The database now owns it, for
-- the same reason trg_clear_stale_source_oa_work_id exists.
-- These mirror _norm_doi / _norm_url / _fingerprint in sync_sources.py.
CREATE OR REPLACE FUNCTION source_norm_doi(v TEXT) RETURNS TEXT AS $$
    SELECT CASE WHEN v IS NULL THEN '' ELSE
        regexp_replace(
            regexp_replace(
                regexp_replace(lower(btrim(v)), '^https?://(dx\.)?doi\.org/', ''),
            '^doi:\s*', ''),
        '\s.*$', '')
    END;
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION source_norm_url(v TEXT) RETURNS TEXT AS $$
    SELECT CASE WHEN v IS NULL THEN '' ELSE
        rtrim(
            regexp_replace(
                regexp_replace(lower(btrim(v)), '^https?://', ''),
            '^www\.', ''),
        '/')
    END;
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION source_content_fingerprint(p_doi_o TEXT, p_doi_r TEXT, p_url_r TEXT)
RETURNS TEXT AS $$
DECLARE l TEXT; r TEXT;
BEGIN
    l := source_norm_doi(p_doi_o);
    r := source_norm_doi(p_doi_r);
    IF r = '' THEN r := source_norm_url(p_url_r); END IF;
    IF l = '' AND r = '' THEN RETURN NULL; END IF;
    RETURN md5(l || '|' || r);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION set_source_content_fingerprint() RETURNS trigger AS $$
BEGIN
    NEW.content_fingerprint := source_content_fingerprint(NEW.doi_o, NEW.doi_r, NEW.url_r);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_source_content_fingerprint ON source_records;
CREATE TRIGGER trg_set_source_content_fingerprint
    BEFORE INSERT OR UPDATE OF doi_o, doi_r, url_r ON source_records
    FOR EACH ROW EXECUTE FUNCTION set_source_content_fingerprint();

UPDATE source_records
   SET content_fingerprint = source_content_fingerprint(doi_o, doi_r, url_r)
 WHERE content_fingerprint IS DISTINCT FROM source_content_fingerprint(doi_o, doi_r, url_r);

-- Nothing previously stopped two rows from each being marked a duplicate of the
-- other. transform_sources.py excludes every 'duplicate' row and does not follow
-- chains, so that combination deleted the paper from the FLoRA dataset outright.
ALTER TABLE source_records DROP CONSTRAINT IF EXISTS source_records_duplicate_coherent;
ALTER TABLE source_records ADD CONSTRAINT source_records_duplicate_coherent CHECK (
       (duplicate_status IS NULL       AND duplicate_of IS NULL)
    OR (duplicate_status = 'distinct'  AND duplicate_of IS NULL)
    OR (duplicate_status = 'duplicate' AND duplicate_of IS NOT NULL)
);

ALTER TABLE source_records DROP CONSTRAINT IF EXISTS source_records_not_self_duplicate;
ALTER TABLE source_records ADD CONSTRAINT source_records_not_self_duplicate
    CHECK (duplicate_of IS NULL OR duplicate_of <> record_id);

-- Migration for databases created before the column was named honestly. Guarded
-- because db_schema.sql is re-executed by init_db() on every app start.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'source_display_counters' AND column_name = 'next_value')
    THEN
        ALTER TABLE source_display_counters RENAME COLUMN next_value TO last_value;
    END IF;
END $$;

-- ON DELETE CASCADE destroyed the append-only history along with its parent row —
-- exactly backwards for an audit trail, whose whole purpose is to outlive the
-- record. display_id is denormalised so history stays readable if a row is gone.
ALTER TABLE source_record_edits DROP CONSTRAINT IF EXISTS source_record_edits_record_id_fkey;
ALTER TABLE source_record_edits ADD COLUMN IF NOT EXISTS display_id TEXT;

UPDATE source_record_edits e
   SET display_id = s.display_id
  FROM source_records s
 WHERE s.record_id = e.record_id AND e.display_id IS NULL;
