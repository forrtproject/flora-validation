"""
csv_to_db.py — Import resolved rows from extracted.csv into the validation database.

Only rows where filter_status is 'replication' or 'reproduction' AND link_method is
one of the resolved methods are imported. These are the rows ready for validation.

For each imported row this script creates:
  - 1 row in 'unvalidated'      (the record, validation_status = 'unvalidated')
  - 1 row in 'record_metadata'  (supplementary extraction data)
  - 3 rows in 'validation_queue' (one slot each for human_1, human_2, llm)

Safe to re-run: rows already in the database are detected by pair_id and skipped.

Usage:
    python csv_to_db.py --input data/extracted.csv

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import os
import uuid
from pathlib import Path

import psycopg2
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Resolved link methods — rows with these methods are ready for validation
_RESOLVED_METHODS = {
    "author_year_match", "llm_abstract", "llm_fulltext",
    "single_candidate_after_requery", "title_pattern_match",
    "citation_context_match", "same_author_year_title_overlap",
}
_RESOLVED_STATUSES = {"replication", "reproduction"}

# Validator slots created per record
_VALIDATOR_SLOTS = ("human_1", "human_2", "llm")

# The upstream extractor still emits 'success'/'failure'. This app (and the FLoRA
# export) use 'successful'/'failed', so translate at the import boundary. Exact-match
# only, so reproduction labels like "computationally successful, robust" pass through.
_OUTCOME_RENAME = {"success": "successful", "failure": "failed"}


def _derive_url_o(doi_o: str) -> str:
    doi_o = str(doi_o or "").strip()
    return f"https://doi.org/{doi_o}" if doi_o else ""


def _s(val) -> str:
    """Coerce to stripped string; treat NaN/None as empty string."""
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return str(val).strip()


def _int_or_none(val) -> "int | None":
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _build_unvalidated_row(record_id: str, pair_id: str, row: pd.Series) -> dict:
    return {
        "record_id":         record_id,
        "pair_id":           pair_id,
        "doi_r":             _s(row.get("doi_r")),
        "study_r":           _s(row.get("title_r")),
        "year_r":            _s(row.get("year_r")),
        "url_r":             _s(row.get("url_r")),
        "ref_r":             _s(row.get("ref_r")),
        "abstract_r":        _s(row.get("abstract_r")),
        "doi_o":             _s(row.get("doi_o")),
        "study_o":           _s(row.get("title_o")),
        "year_o":            _s(row.get("year_o")),
        "url_o":             _derive_url_o(row.get("doi_o")),
        "ref_o":             _s(row.get("ref_o")),
        "type":              _s(row.get("type")),
        "outcome":           _OUTCOME_RENAME.get(_s(row.get("outcome")), _s(row.get("outcome"))),
        "outcome_quote":     _s(row.get("outcome_phrase")),
        "out_quote_source":  _s(row.get("out_quote_source")),
        "validation_status": "unvalidated",
    }


def _build_metadata_row(record_id: str, pair_id: str, row: pd.Series) -> dict:
    return {
        "record_id":                  record_id,
        "pair_id":                    pair_id,
        "filter_status":              _s(row.get("filter_status")),
        "filter_method":              _s(row.get("filter_method")),
        "filter_evidence":            _s(row.get("filter_evidence")),
        "filter_confidence":          _s(row.get("filter_confidence")),
        "original_match_type":        _s(row.get("original_match_type")),
        "original_match_confidence":  _s(row.get("original_match_confidence")),
        "link_method":                _s(row.get("link_method")),
        "link_evidence":              _s(row.get("link_evidence")),
        "link_confidence":            _s(row.get("link_confidence")),
        "link_llm_model":             _s(row.get("link_llm_model")),
        "outcome_confidence":         _s(row.get("outcome_confidence")),
        "authors_r":                  _s(row.get("authors_r")),
        "authors_o":                  _s(row.get("authors_o")),
        "journal_r":                  _s(row.get("journal_r")),
        "openalex_id_r":              _s(row.get("openalex_id_r")),
        "source":                     _s(row.get("source")),
        "original_rank":              _int_or_none(row.get("original_rank")),
        "n_originals":                _int_or_none(row.get("n_originals")),
    }


def _insert_unvalidated(cur, row: dict) -> bool:
    cur.execute(
        """
        INSERT INTO unvalidated (
            record_id, pair_id,
            doi_r, study_r, year_r, url_r, ref_r, abstract_r,
            doi_o, study_o, year_o, url_o, ref_o,
            type, outcome, outcome_quote, out_quote_source, validation_status
        ) VALUES (
            %(record_id)s, %(pair_id)s,
            %(doi_r)s, %(study_r)s, %(year_r)s, %(url_r)s, %(ref_r)s, %(abstract_r)s,
            %(doi_o)s, %(study_o)s, %(year_o)s, %(url_o)s, %(ref_o)s,
            %(type)s, %(outcome)s, %(outcome_quote)s, %(out_quote_source)s, %(validation_status)s
        )
        ON CONFLICT (pair_id) DO NOTHING
        """,
        row,
    )
    return cur.rowcount > 0


def _insert_metadata(cur, row: dict) -> None:
    cur.execute(
        """
        INSERT INTO record_metadata (
            record_id, pair_id,
            filter_status, filter_method, filter_evidence, filter_confidence,
            original_match_type, original_match_confidence,
            link_method, link_evidence, link_confidence, link_llm_model,
            outcome_confidence,
            authors_r, authors_o, journal_r, openalex_id_r, source,
            original_rank, n_originals
        ) VALUES (
            %(record_id)s, %(pair_id)s,
            %(filter_status)s, %(filter_method)s, %(filter_evidence)s, %(filter_confidence)s,
            %(original_match_type)s, %(original_match_confidence)s,
            %(link_method)s, %(link_evidence)s, %(link_confidence)s, %(link_llm_model)s,
            %(outcome_confidence)s,
            %(authors_r)s, %(authors_o)s, %(journal_r)s, %(openalex_id_r)s, %(source)s,
            %(original_rank)s, %(n_originals)s
        )
        """,
        row,
    )


def _insert_queue_slots(cur, record_id: str) -> None:
    for slot in _VALIDATOR_SLOTS:
        cur.execute(
            """
            INSERT INTO validation_queue (record_id, validator_slot, is_shown, is_validated)
            VALUES (%s, %s, FALSE, FALSE)
            """,
            (record_id, slot),
        )


def _load_existing_pair_ids(cur) -> set:
    """Fetch pair_ids already in unvalidated so we can skip duplicates."""
    cur.execute("SELECT pair_id FROM unvalidated WHERE pair_id IS NOT NULL")
    return {row[0] for row in cur.fetchall()}


def run_import(csv_path: Path, dry_run: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    print(f"Reading {csv_path} …")
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")

    # Filter to resolved rows only
    resolved_mask = (
        df["filter_status"].isin(_RESOLVED_STATUSES) &
        df["link_method"].isin(_RESOLVED_METHODS)
    )
    resolved = df[resolved_mask].copy()
    skipped_fp = (df["filter_status"] == "false_positive").sum()
    skipped_no_orig = (df["link_method"] == "no_original_found").sum()
    skipped_pending = (~resolved_mask & ~(df["filter_status"] == "false_positive")).sum()

    print(f"  Total rows:         {len(df)}")
    print(f"  Resolved (import):  {len(resolved)}")
    print(f"  false_positive:     {skipped_fp}  (skipped — not replications)")
    print(f"  no_original_found:  {skipped_no_orig}  (skipped — no identifiable original)")
    print(f"  target_pending / api_error / other: {skipped_pending - skipped_no_orig}  (skipped — not yet resolved)")

    if resolved.empty:
        print("Nothing to import.")
        return

    if dry_run:
        print("[dry-run] Would import the following rows:")
        print(resolved[["doi_r", "doi_o", "filter_status", "link_method"]].to_string())
        return

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                existing_pair_ids = _load_existing_pair_ids(cur)
                print(f"  Already in DB:      {len(existing_pair_ids)} pair_ids — will skip")

                inserted = 0
                skipped_dup = 0

                for _, row in resolved.iterrows():
                    pair_id = _s(row.get("pair_id"))
                    if pair_id and pair_id in existing_pair_ids:
                        skipped_dup += 1
                        continue

                    record_id = str(uuid.uuid4())

                    if _insert_unvalidated(cur, _build_unvalidated_row(record_id, pair_id, row)):
                        _insert_metadata(cur, _build_metadata_row(record_id, pair_id, row))
                        _insert_queue_slots(cur, record_id)
                        inserted += 1
                        if inserted % 10 == 0:
                            print(f"  … imported {inserted} records")
                    else:
                        skipped_dup += 1

        print(f"\nDone. Inserted: {inserted}  |  Skipped (already in DB): {skipped_dup}")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import extracted.csv into validation DB")
    parser.add_argument(
        "--input", type=Path, default=Path("data/extracted.csv"),
        help="Path to extracted.csv (default: data/extracted.csv)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be imported without touching the database.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    run_import(args.input, dry_run=args.dry_run)
