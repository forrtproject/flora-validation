"""
csv_to_db.py — Import resolved rows from extracted.csv into the validation database.

Only rows whose paper type is 'replication' or 'reproduction' AND whose link_method is
one of the resolved methods are imported. These are the rows ready for validation.

The paper type arrives as `paper_type` (flora-extractor issue #93) or, on a CSV
exported before that rename, as `filter_status`. This script is the conversion
point: it reads either name and writes the database column, which keeps the name
`record_metadata.filter_status`.

Every value vocabulary the CSV uses — link methods, paper types, outcome labels —
lives in extractor_vocab.py, which also refuses a CSV carrying a value it has
never seen. Add new upstream values there, not here.

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
import re
import uuid
from pathlib import Path

import psycopg2
import pandas as pd
from dotenv import load_dotenv
from console_encoding import use_utf8_output

from extractor_vocab import (
    check_csv_vocabulary,
    normalize_outcome,
    paper_type as _paper_type,
    paper_type_column as _paper_type_column,
    resolved_mask as _resolved_mask,
)

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

# Validator slots created per record
_VALIDATOR_SLOTS = ("human_1", "human_2", "llm")


def _derive_url_o(doi_o: str) -> str:
    doi_o = str(doi_o or "").strip()
    return f"https://doi.org/{doi_o}" if doi_o else ""


def _url_o(row) -> str:
    """The original's URL: prefer the CSV's url_o (for DOI-less originals —
    books, chapters, pre-DOI papers — the extractor points it at OpenAlex),
    falling back to a doi.org link derived from doi_o."""
    return _s(row.get("url_o")) or _derive_url_o(row.get("doi_o"))


def _s(val) -> str:
    """Coerce to stripped string; treat NaN/None as empty string."""
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return str(val).strip()


def _normalize_title(t) -> str:
    """Casefold + collapse whitespace, for loose duplicate-title comparison
    (catches near-dupes like trailing periods or double spaces, not just
    byte-identical strings)."""
    return re.sub(r'\s+', ' ', _s(t)).casefold()


def _flag_ambiguous_doi_o_titles(rows: list) -> dict:
    """Among DOI-less originals (doi_o == ''), find ones whose title can't tell
    them apart from another original of the SAME replication. doi_o is blank
    for every DOI-less original, so the title is the only thing left to keep
    two distinct originals of one replication from silently merging into a
    single row under validated's (doi_r, study_r, doi_o, study_o) natural key.

    rows: dicts with 'key' (caller-chosen id — pair_id or record_id), 'doi_r',
          'study_r', 'study_o', 'doi_o'. Rows with a non-blank doi_o are ignored.
    Returns {key: reason} for every row that should be flagged for admin review.
    Warning only — callers still import/update the row, just attach the reason
    somewhere a human will see it (admin_notes, a console summary, etc).
    """
    groups = {}
    for r in rows:
        if _s(r.get("doi_o")):
            continue
        groups.setdefault((_s(r.get("doi_r")), _s(r.get("study_r"))), []).append(r)

    flagged = {}
    for members in groups.values():
        by_title = {}
        for r in members:
            by_title.setdefault(_normalize_title(r.get("study_o")), []).append(r)
        for norm_title, dupes in by_title.items():
            if not norm_title:
                for r in dupes:
                    flagged[r["key"]] = (
                        "this original has no registered DOI and a blank title — "
                        "cannot be distinguished from other originals of this replication"
                    )
            elif len(dupes) > 1:
                for r in dupes:
                    flagged[r["key"]] = (
                        "this original has no registered DOI and shares its title with "
                        "another original of this replication — cannot be told apart"
                    )
    return flagged


def _int_or_none(val) -> "int | None":
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _work_id(val) -> "str | None":
    """Bare 'W…' OpenAlex work id (strip any 'https://openalex.org/' prefix).
    Returns None (not '') when absent, so the NULL-keyed schema seed and OpenAlex
    backfill will still fill it later — an empty string would look 'already filled'."""
    m = re.search(r"W\d+", _s(val))
    return m.group(0) if m else None


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
        "url_o":             _url_o(row),
        "ref_o":             _s(row.get("ref_o")),
        # OpenAlex work ids ship with newer extractor data; accept either name.
        "oa_work_id_o":      _work_id(row.get("oa_work_id_o") or row.get("openalex_id_o")),
        "oa_work_id_r":      _work_id(row.get("oa_work_id_r") or row.get("openalex_id_r")),
        "type":              _s(row.get("type")),
        "outcome":           normalize_outcome(row.get("outcome")),
        "outcome_quote":     _s(row.get("outcome_phrase")),
        "out_quote_source":  _s(row.get("out_quote_source")),
        "validation_status": "unvalidated",
    }


def _build_metadata_row(record_id: str, pair_id: str, row: pd.Series) -> dict:
    return {
        "record_id":                  record_id,
        "pair_id":                    pair_id,
        # The DB column keeps the old name; the CSV may use either (issue #93).
        "filter_status":              _paper_type(row),
        "filter_method":              _s(row.get("filter_method")),
        "filter_evidence":            _s(row.get("filter_evidence")),
        "filter_confidence":          _s(row.get("filter_confidence")),
        "original_match_type":        _s(row.get("original_match_type")),
        "original_match_confidence":  _s(row.get("original_match_confidence")),
        # 'no_doi' marks originals with no registered DOI by design (books,
        # chapters, pre-DOI papers) — distinguishes them from lookup failures.
        "doi_o_verification":         _s(row.get("doi_o_verification")),
        "link_method":                _s(row.get("link_method")),
        "link_evidence":              _s(row.get("link_evidence")),
        "link_confidence":            _s(row.get("link_confidence")),
        "link_llm_model":             _s(row.get("link_llm_model")),
        # Full-text provenance (flora-extractor #124). Blank on rows that never
        # acquired or parsed a document — note that link_method = 'llm_fulltext'
        # with a blank pdf_source is a contradiction, counted in run_import.
        "pdf_source":                 _s(row.get("pdf_source")),
        "parse_method":               _s(row.get("parse_method")),
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
            oa_work_id_o, oa_work_id_r,
            type, outcome, outcome_quote, out_quote_source, validation_status
        ) VALUES (
            %(record_id)s, %(pair_id)s,
            %(doi_r)s, %(study_r)s, %(year_r)s, %(url_r)s, %(ref_r)s, %(abstract_r)s,
            %(doi_o)s, %(study_o)s, %(year_o)s, %(url_o)s, %(ref_o)s,
            %(oa_work_id_o)s, %(oa_work_id_r)s,
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
            original_match_type, original_match_confidence, doi_o_verification,
            link_method, link_evidence, link_confidence, link_llm_model,
            pdf_source, parse_method,
            outcome_confidence,
            authors_r, authors_o, journal_r, openalex_id_r, source,
            original_rank, n_originals
        ) VALUES (
            %(record_id)s, %(pair_id)s,
            %(filter_status)s, %(filter_method)s, %(filter_evidence)s, %(filter_confidence)s,
            %(original_match_type)s, %(original_match_confidence)s, %(doi_o_verification)s,
            %(link_method)s, %(link_evidence)s, %(link_confidence)s, %(link_llm_model)s,
            %(pdf_source)s, %(parse_method)s,
            %(outcome_confidence)s,
            %(authors_r)s, %(authors_o)s, %(journal_r)s, %(openalex_id_r)s, %(source)s,
            %(original_rank)s, %(n_originals)s
        )
        """,
        row,
    )


_AMBIGUOUS_NOTE_PREFIX = "⚠ Auto-flagged:"


def _note_ambiguous_original(cur, record_id: str, reason: str) -> None:
    """Append the flag to admin_notes so it surfaces on the admin review screen
    (the import runs unattended — a console line alone is easy to miss). Appends
    rather than overwrites, and won't duplicate itself on a re-run."""
    note = f"{_AMBIGUOUS_NOTE_PREFIX} {reason} — please verify before resolving."
    cur.execute(
        """
        UPDATE unvalidated
        SET admin_notes = CASE
                WHEN admin_notes IS NULL OR admin_notes = '' THEN %s
                WHEN position(%s in admin_notes) > 0 THEN admin_notes
                ELSE admin_notes || E'\n' || %s
            END,
            note_saved_by = COALESCE(note_saved_by, 'import'),
            note_saved_at = NOW()
        WHERE record_id = %s
        """,
        (note, note, note, record_id),
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

    # Before anything else: refuse a CSV whose vocabulary we don't recognise.
    # An unknown link_method used to be indistinguishable from "not yet
    # resolved", which is how a rename upstream cost five weeks of imports.
    check_csv_vocabulary(df)

    # Filter to resolved rows only
    paper_type = _paper_type_column(df)
    resolved_mask = _resolved_mask(df)
    resolved = df[resolved_mask].copy()
    skipped_fp = (paper_type == "false_positive").sum()
    skipped_no_orig = (df["link_method"] == "no_original_found").sum()
    skipped_pending = (~resolved_mask & ~(paper_type == "false_positive")).sum()

    print(f"  Total rows:         {len(df)}")
    print(f"  Resolved (import):  {len(resolved)}")
    print(f"  false_positive:     {skipped_fp}  (skipped — not replications)")
    print(f"  no_original_found:  {skipped_no_orig}  (skipped — no identifiable original)")
    print(f"  target_pending / api_error / other: {skipped_pending - skipped_no_orig}  (skipped — not yet resolved)")

    # link_method = 'llm_fulltext' means the LLM read a full document, so a blank
    # pdf_source contradicts it (flora-extractor #124). Reported, not fatal — the
    # affected rows import and should be treated as unverified.
    if "pdf_source" in resolved.columns:
        contradictory = (
            (resolved["link_method"] == "llm_fulltext")
            & (resolved["pdf_source"].map(_s) == "")
        ).sum()
        if contradictory:
            print(f"  WARNING: {contradictory} row(s) claim llm_fulltext but name no "
                  f"pdf_source - treat as unverified")

    if resolved.empty:
        print("Nothing to import.")
        return

    if dry_run:
        print("[dry-run] Would import the following rows:")
        print(resolved[["doi_r", "doi_o", paper_type.name, "link_method"]].to_string())
        return

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                existing_pair_ids = _load_existing_pair_ids(cur)
                print(f"  Already in DB:      {len(existing_pair_ids)} pair_ids — will skip")

                # Which DOI-less originals can't be told apart by title? Computed
                # over the whole resolved set, so an incoming row is compared
                # against its siblings in this same CSV.
                ambiguous = _flag_ambiguous_doi_o_titles([
                    {"key": _s(r.get("pair_id")), "doi_r": r.get("doi_r"),
                     "study_r": r.get("title_r"), "study_o": r.get("title_o"),
                     "doi_o": r.get("doi_o")}
                    for _, r in resolved.iterrows()
                ])

                inserted = 0
                skipped_dup = 0
                flagged_ambiguous = []

                for _, row in resolved.iterrows():
                    pair_id = _s(row.get("pair_id"))
                    if pair_id and pair_id in existing_pair_ids:
                        skipped_dup += 1
                        continue

                    record_id = str(uuid.uuid4())

                    if _insert_unvalidated(cur, _build_unvalidated_row(record_id, pair_id, row)):
                        _insert_metadata(cur, _build_metadata_row(record_id, pair_id, row))
                        _insert_queue_slots(cur, record_id)
                        # Warning only — the row is imported either way.
                        if pair_id in ambiguous:
                            _note_ambiguous_original(cur, record_id, ambiguous[pair_id])
                            flagged_ambiguous.append(pair_id)
                        inserted += 1
                        if inserted % 10 == 0:
                            print(f"  … imported {inserted} records")
                    else:
                        skipped_dup += 1

        print(f"\nDone. Inserted: {inserted}  |  Skipped (already in DB): {skipped_dup}")
        if flagged_ambiguous:
            print(f"\n  ⚠  {len(flagged_ambiguous)} imported record(s) have a DOI-less original "
                  f"with a blank or duplicate title.")
            print("     Flagged in admin_notes for review. pair_ids:")
            for pid in flagged_ambiguous[:20]:
                print(f"       {pid}")
            if len(flagged_ambiguous) > 20:
                print(f"       … and {len(flagged_ambiguous) - 20} more")
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
