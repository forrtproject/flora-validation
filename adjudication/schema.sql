-- Adjudication of the FLoRA / Metascience Observatory disagreements
-- (forrtproject/fred-data PR #143, external/metascience-observatory/).
--
-- An isolated feature. Everything lives in its own PostgreSQL schema, and nothing
-- here references a table outside it: a validator or admin is stored as an id and
-- a name, not a foreign key. So the main app never depends on these tables, a
-- failure here cannot block it, and `DROP SCHEMA adjudication CASCADE` removes the
-- feature completely. Applied by adjudication/bootstrap.py, never by db_schema.sql.
-- Idempotent: safe to run on every start.
--
-- The three tables mirror the main app:
--   records     ~ unvalidated       one row per disagreement, both answers
--   judgements  ~ validation_queue  one row per validator per record
--   final       ~ validated         the admin-approved answer, then published

CREATE SCHEMA IF NOT EXISTS adjudication;

-- One disagreement between FLoRA's pipeline and the Observatory about one
-- replication. `raw` keeps the CSV row exactly as imported, for the analysis.
CREATE TABLE IF NOT EXISTS adjudication.records (
    record_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- md5(doi_r|our_doi_o|mo_doi_o|kind): a re-import updates instead of duplicating.
    import_key            TEXT        NOT NULL UNIQUE,
    imported_from         TEXT        NOT NULL,     -- e.g. fred-data@55d6f04
    kind                  TEXT        NOT NULL CHECK (kind IN (
                              'we found no original',
                              'different original',
                              'same original, different outcome',
                              'MO names no original DOI'
                          )),
    doi_r                 TEXT        NOT NULL,
    title_r               TEXT,
    abstract_r            TEXT,
    year_r                TEXT,
    -- FLoRA's answer
    flora_doi_o           TEXT,
    flora_title_o         TEXT,
    flora_outcome         TEXT,
    flora_outcome_quote   TEXT,
    flora_quote_source    TEXT,
    flora_link_method     TEXT,
    flora_link_confidence TEXT,
    flora_link_evidence   TEXT,
    -- The Observatory's answer
    mo_doi_o              TEXT,
    mo_title_o            TEXT,
    mo_outcome            TEXT,
    mo_replication_type   TEXT,
    mo_discipline         TEXT,
    mo_source             TEXT,
    mo_confidence         TEXT,
    mo_ai_version         TEXT,
    raw                   JSONB       NOT NULL,
    -- open: needs judgements · awaiting_approval: two judgements in ·
    -- approved: a final row exists · published: that row is in Source Records
    status                TEXT        NOT NULL DEFAULT 'open' CHECK (status IN (
                              'open', 'awaiting_approval', 'approved', 'published'
                          )),
    imported_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS records_status_idx ON adjudication.records (status);
CREATE INDEX IF NOT EXISTS records_doi_r_idx  ON adjudication.records (lower(doi_r));
-- Added after phase 1 shipped, so an upgrade rather than a CREATE TABLE edit.
-- The admin who ran the import (adjudication/importer.py), or 'cli'.
ALTER TABLE adjudication.records ADD COLUMN IF NOT EXISTS imported_by TEXT;
-- Where abstract_r came from: 'openalex' or 'europepmc'.
ALTER TABLE adjudication.records ADD COLUMN IF NOT EXISTS abstract_source TEXT;

-- One validator's work on one record: claimed, then submitted or skipped. Two
-- submitted judgements per record, from two different validators (Trusted or
-- Senior). A skip stays as history, so the record is not served to them again.
CREATE TABLE IF NOT EXISTS adjudication.judgements (
    judgement_id          BIGSERIAL   PRIMARY KEY,
    record_id             UUID        NOT NULL
                          REFERENCES adjudication.records (record_id) ON DELETE CASCADE,
    validator_id          INTEGER     NOT NULL,     -- validators.id, deliberately no FK
    validator_handle      TEXT        NOT NULL,     -- the name at the time
    validator_tier        SMALLINT    NOT NULL,     -- 1 Trusted, 2 Senior
    state                 TEXT        NOT NULL DEFAULT 'claimed' CHECK (state IN (
                              'claimed', 'submitted', 'skipped'
                          )),
    -- Which original is right: FLoRA's, the Observatory's, both (the paper
    -- re-tests both), neither, or the validator cannot tell.
    original_choice       TEXT        CHECK (original_choice IN (
                              'flora', 'observatory', 'both', 'neither', 'cannot_tell'
                          )),
    suggested_doi_o       TEXT,                     -- with 'neither', or when no side has one
    outcome               TEXT,                     -- FLoRA's outcome vocabulary
    note                  TEXT,
    points                INTEGER     NOT NULL DEFAULT 0,
    claimed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at          TIMESTAMPTZ,
    UNIQUE (record_id, validator_id),
    CHECK (state <> 'submitted' OR (original_choice IS NOT NULL AND submitted_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS judgements_validator_idx ON adjudication.judgements (validator_id);

-- The admin-approved answer for a record: what FLoRA should hold. Publishing
-- copies it into Source Records under its own source; withdrawing marks that
-- Source Records row deleted again.
CREATE TABLE IF NOT EXISTS adjudication.final (
    record_id             UUID        PRIMARY KEY
                          REFERENCES adjudication.records (record_id) ON DELETE CASCADE,
    doi_r                 TEXT        NOT NULL,
    title_r               TEXT,
    doi_o                 TEXT,
    title_o               TEXT,
    outcome               TEXT,
    outcome_quote         TEXT,
    quote_source          TEXT,
    -- Whose original/outcome the admin approved, or 'admin' for their own edit.
    basis                 TEXT        NOT NULL CHECK (basis IN (
                              'flora', 'observatory', 'admin'
                          )),
    admin_note            TEXT,
    approved_by_id        INTEGER     NOT NULL,     -- admins.id, deliberately no FK
    approved_by           TEXT        NOT NULL,
    approved_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at          TIMESTAMPTZ,
    published_record_id   TEXT,                     -- the Source Records row it became
    withdrawn_at          TIMESTAMPTZ
);
-- Who published and who withdrew it (adjudication/review.py).
ALTER TABLE adjudication.final ADD COLUMN IF NOT EXISTS published_by TEXT;
ALTER TABLE adjudication.final ADD COLUMN IF NOT EXISTS withdrawn_by TEXT;
