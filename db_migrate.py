"""
db_migrate.py — Migrate an existing FLoRA validation database from the old schema
(pairs / coders / judgements) to the new schema (validators / unvalidated /
validation_queue / validated / record_metadata).

SAFE TO RE-RUN: each step is guarded by IF NOT EXISTS / IF EXISTS checks.
Run this BEFORE starting app.py against the migrated database.

Usage:
    python db_migrate.py

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from console_encoding import use_utf8_output
from extractor_vocab import (
    normalize_axis_value,
    normalize_quote_source,
    split_joined_outcome,
    stored_outcome,
)

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

DATABASE_URL = os.environ["DATABASE_URL"]
SCHEMA_PATH = Path(__file__).parent / "db_schema.sql"


def _conn():
    return psycopg2.connect(DATABASE_URL)


def step_create_new_tables(cur) -> None:
    """Apply the new schema DDL (all statements are IF NOT EXISTS)."""
    print("  [1] Creating new tables from db_schema.sql …")
    cur.execute(SCHEMA_PATH.read_text())


def step_migrate_coders(cur) -> None:
    """Copy coders → validators, skipping rows already present."""
    print("  [2] Migrating coders → validators …")
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'coders'
        """
    )
    if not cur.fetchone():
        print("       coders table not found — skipping")
        return

    cur.execute(
        """
        INSERT INTO validators (email, code, handle, created_at, onboarded_at)
        SELECT email, code, handle,
               created_at::TIMESTAMPTZ,
               onboarded_at::TIMESTAMPTZ
        FROM coders
        ON CONFLICT (handle) DO NOTHING
        """
    )
    print(f"       Migrated {cur.rowcount} coder(s)")


def step_migrate_pairs(cur) -> None:
    """
    Copy pairs → unvalidated + record_metadata + validation_queue.
    Skips pair_ids already present in unvalidated.
    """
    print("  [3] Migrating pairs → unvalidated / record_metadata / validation_queue …")
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'pairs'
        """
    )
    if not cur.fetchone():
        print("       pairs table not found — skipping")
        return

    cur.execute("SELECT pair_id FROM unvalidated WHERE pair_id IS NOT NULL")
    existing = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT pair_id, data_json FROM pairs")
    rows = cur.fetchall()
    inserted = 0

    for row in rows:
        pair_id = row[0]
        if pair_id in existing:
            continue
        try:
            data = json.loads(row[1])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"pair_id {pair_id!r} has malformed data_json: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"pair_id {pair_id!r} has malformed data_json: expected a JSON object"
            )

        def _s(k):
            v = data.get(k, "")
            return "" if v is None else str(v).strip()

        def _int_or_none(k):
            try:
                return int(data[k])
            except (KeyError, TypeError, ValueError):
                return None

        record_id = str(uuid.uuid4())
        doi_o = _s("doi_o")
        url_o = f"https://doi.org/{doi_o}" if doi_o else ""
        record_type = _s("type")
        raw_outcome = _s("outcome")
        try:
            outcome_computation = normalize_axis_value(
                "outcome_computation",
                data.get("outcome_computation") or data.get("outcome_computational"),
            )
            outcome_robustness = normalize_axis_value(
                "outcome_robustness", data.get("outcome_robustness")
            )
        except ValueError as exc:
            raise ValueError(f"pair_id {pair_id}: {exc}") from exc
        if record_type == "reproduction" and (not outcome_computation or not outcome_robustness):
            legacy_computation, legacy_robustness = split_joined_outcome(raw_outcome)
            outcome_computation = outcome_computation or legacy_computation
            outcome_robustness = outcome_robustness or legacy_robustness
        outcome = stored_outcome(
            raw_outcome,
            record_type,
            axes_coded=bool(outcome_computation or outcome_robustness),
            computation=outcome_computation,
            robustness=outcome_robustness,
        )

        cur.execute(
            """
            INSERT INTO unvalidated (
                record_id, pair_id,
                doi_r, study_r, title_r, year_r, url_r, ref_r, abstract_r,
                doi_o, study_o, title_o, year_o, url_o, ref_o,
                type, outcome, outcome_quote, out_quote_source,
                outcome_computation, outcome_computational_quote,
                out_quote_computational_source, outcome_robustness,
                outcome_robustness_quote, out_quote_robust_source,
                validation_status
            ) VALUES (
                %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                'unvalidated'
            )
            """,
            (
                record_id, pair_id,
                _s("doi_r"), _s("study_r"), _s("title_r"), _s("year_r"), _s("url_r"), _s("ref_r"), _s("abstract_r"),
                doi_o, _s("study_o"), _s("title_o"), _s("year_o"), url_o, _s("ref_o"),
                record_type, outcome, _s("outcome_phrase"),
                normalize_quote_source(data.get("out_quote_source")),
                outcome_computation, _s("outcome_computational_quote"),
                normalize_quote_source(data.get("out_quote_computational_source")),
                outcome_robustness, _s("outcome_robustness_quote"),
                normalize_quote_source(data.get("out_quote_robust_source")),
            ),
        )

        cur.execute(
            """
            INSERT INTO record_metadata (
                record_id, pair_id,
                filter_status, filter_method, filter_evidence, filter_confidence,
                original_match_type, original_match_confidence,
                link_method, link_evidence, link_confidence, link_llm_model,
                outcome_confidence, authors_r, authors_o, journal_r,
                openalex_id_r, source, original_rank, n_originals
            ) VALUES (
                %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            """,
            (
                record_id, pair_id,
                _s("filter_status"), _s("filter_method"), _s("filter_evidence"), _s("filter_confidence"),
                _s("original_match_type"), _s("original_match_confidence"),
                _s("link_method"), _s("link_evidence"), _s("link_confidence"), _s("link_llm_model"),
                _s("outcome_confidence"), _s("authors_r"), _s("authors_o"), _s("journal_r"),
                _s("openalex_id_r"), _s("source"), _int_or_none("original_rank"), _int_or_none("n_originals"),
            ),
        )

        for slot in ("human_1", "human_2", "llm"):
            cur.execute(
                """
                INSERT INTO validation_queue (record_id, validator_slot, is_shown, is_validated)
                VALUES (%s, %s, FALSE, FALSE)
                """,
                (record_id, slot),
            )

        existing.add(pair_id)
        inserted += 1

    print(f"       Migrated {inserted} pair(s)")


def step_migrate_judgements(cur) -> None:
    """
    Map old judgements into validation_queue and update validators totals.
    Old fields → new fields:
      type_judgement in ('replication','reproduction') → type_check='correct'
      type_judgement = 'not_validation' → type_check='incorrect'
      original_judgement in ('yes','unsure') → original_check='correct'
      original_judgement = 'no' → original_check='incorrect'
      outcome_judgement in ('correct','unsure') → outcome_check='correct'
      outcome_judgement = 'incorrect' → outcome_check='incorrect'
    """
    print("  [4] Migrating judgements → validation_queue …")
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'judgements'
        """
    )
    if not cur.fetchone():
        print("       judgements table not found — skipping")
        return

    cur.execute(
        """
        SELECT j.id, j.coder_id, j.pair_id, j.type_judgement,
               j.original_judgement, j.outcome_judgement,
               j.comment, j.points, j.created_at
        FROM judgements j
        ORDER BY j.created_at NULLS LAST, j.id
        """
    )
    judgement_rows = cur.fetchall()

    # Build lookup: old coder_id → new validator id (by handle)
    cur.execute(
        """
        SELECT c.id, v.id, v.handle, v.validator_tier
        FROM coders c
        JOIN validators v ON v.handle = c.handle
        """
    )
    coder_to_validator = {
        old_id: {"id": new_id, "handle": handle, "validator_tier": tier}
        for old_id, new_id, handle, tier in cur.fetchall()
    }

    # Build pair_id → record_id map
    cur.execute("SELECT pair_id, record_id FROM unvalidated WHERE pair_id IS NOT NULL")
    pair_to_record = {r[0]: r[1] for r in cur.fetchall()}

    migrated = 0
    affected_records = set()
    for row in judgement_rows:
        _, old_coder_id, pair_id, type_j, orig_j, out_j, comment, points, created_at = row

        validator = coder_to_validator.get(old_coder_id)
        if not validator:
            continue
        validator_id = validator["id"]
        record_id = pair_to_record.get(pair_id)
        if not record_id:
            continue

        # Map old judgement values to new check fields
        if type_j in ("replication", "reproduction"):
            type_check = "correct"
            corrected_type = None
        elif type_j == "not_validation":
            type_check = "incorrect"
            corrected_type = type_j
        else:
            continue  # skip ('skip', etc.)

        orig_j = (orig_j or "").lower()
        original_check = "incorrect" if orig_j == "no" else "correct"

        out_j = (out_j or "").lower()
        outcome_check = "incorrect" if out_j == "incorrect" else "correct"

        # Find the slot assigned to this coder for this record
        cur.execute(
            """
            SELECT queue_id, validator_slot FROM validation_queue
            WHERE record_id = %s AND validator_id = %s
              AND validator_slot IN ('human_1', 'human_2')
            LIMIT 1
            """,
            (record_id, validator_id),
        )
        slot_row = cur.fetchone()

        # Repair a database already touched by the old migrator. That version put
        # the legacy numeric coder_id directly into validator_id and left
        # validator_name NULL. The NULL marker distinguishes those rows from slots
        # claimed by the current application, so this rerun can safely remap them.
        if not slot_row and old_coder_id != validator_id:
            cur.execute(
                """
                SELECT queue_id, validator_slot FROM validation_queue
                WHERE record_id = %s AND validator_id = %s
                  AND validator_name IS NULL
                  AND is_validated = TRUE
                  AND validator_slot IN ('human_1', 'human_2')
                LIMIT 1
                """,
                (record_id, old_coder_id),
            )
            slot_row = cur.fetchone()

        if slot_row:
            queue_id = slot_row[0]
            validator_slot = slot_row[1]
        else:
            # Assign to first free human slot
            cur.execute(
                """
                SELECT queue_id, validator_slot FROM validation_queue
                WHERE record_id = %s
                  AND validator_slot IN ('human_1', 'human_2')
                  AND validator_id IS NULL
                ORDER BY validator_slot
                LIMIT 1
                """,
                (record_id,),
            )
            free = cur.fetchone()
            if not free:
                continue
            queue_id = free[0]
            validator_slot = free[1]
            cur.execute(
                "UPDATE validation_queue SET validator_id = %s, validator_name = %s "
                "WHERE queue_id = %s",
                (validator_id, validator["handle"], queue_id),
            )

        if isinstance(created_at, datetime):
            created = created_at
        else:
            try:
                created = datetime.fromisoformat(created_at) if created_at else datetime.now(timezone.utc)
            except (ValueError, TypeError):
                created = datetime.now(timezone.utc)

        cur.execute(
            """
            UPDATE validation_queue SET
                validator_id = %s,
                validator_name = %s,
                is_validated = TRUE,
                type_check = %s,
                original_check = %s,
                outcome_check = %s,
                corrected_type = %s,
                validator_notes = %s,
                points = %s,
                validated_at = %s
            WHERE queue_id = %s
            """,
            (
                validator_id, validator["handle"],
                type_check, original_check, outcome_check,
                corrected_type, comment, points or 0,
                created, queue_id,
            ),
        )
        summary = {
            "validator_id": validator_id,
            "validator_name": validator["handle"],
            "validator_tier": validator["validator_tier"],
            "type_check": type_check,
            "original_check": original_check,
            "outcome_check": outcome_check,
            "corrected_type": corrected_type,
            "validator_notes": comment or "",
            "points": points or 0,
            "validated_at": created.isoformat(),
            "migrated_from_legacy": True,
        }
        summary_column = "validator_1" if validator_slot == "human_1" else "validator_2"
        cur.execute(
            f"UPDATE unvalidated SET {summary_column} = %s WHERE record_id = %s",
            (json.dumps(summary), record_id),
        )
        affected_records.add(record_id)
        migrated += 1

    # Legacy rows do not carry the complete modern correction/evidence shape. A
    # pair with two migrated judgements therefore goes to admin review; leaving it
    # `unvalidated` would strand it because both human slots are already occupied.
    for record_id in affected_records:
        cur.execute(
            """
            SELECT COUNT(*) FROM validation_queue
            WHERE record_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND is_validated = TRUE
            """,
            (record_id,),
        )
        completed = cur.fetchone()[0]
        status = "need_review" if completed >= 2 else "validation_inprogress"
        cur.execute(
            """
            UPDATE unvalidated SET validation_status = %s, updated_at = NOW()
            WHERE record_id = %s
              AND validation_status NOT IN ('validated', 'rejected')
            """,
            (status, record_id),
        )

    # Update validator totals from migrated judgements
    cur.execute(
        """
        UPDATE validators v SET
            total_points = sub.pts,
            total_judgements = sub.cnt
        FROM (
            SELECT validator_id, SUM(points) AS pts, COUNT(*) AS cnt
            FROM validation_queue
            WHERE is_validated = TRUE AND validator_slot IN ('human_1', 'human_2')
            GROUP BY validator_id
        ) sub
        WHERE v.id = sub.validator_id
        """
    )

    print(f"       Migrated {migrated} judgement(s)")


def run_migration() -> None:
    print("Starting FLoRA DB migration (old → new schema) …")
    conn = _conn()
    try:
        with conn:
            cur = conn.cursor()
            step_create_new_tables(cur)
            step_migrate_coders(cur)
            step_migrate_pairs(cur)
            step_migrate_judgements(cur)
        print("\nMigration complete.")
        print("You can now start app.py against the migrated database.")
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
