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

Safe to re-run: existing `pair_id` rows have extractor-owned raw fields and
metadata refreshed. If an upstream correction changes the pair id,
`(work_id, original_rank)` re-keys the raw source record without overwriting
validator/final decisions.

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
    normalize_quote_source,
    paper_type as _paper_type,
    paper_type_column as _paper_type_column,
    resolved_mask as _resolved_mask,
    stored_outcome,
)

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

# Validator slots created per record
_VALIDATOR_SLOTS = ("human_1", "human_2", "llm")


class InputIdentityError(ValueError):
    """The CSV cannot be imported without losing or merging source rows."""


class InputSchemaError(ValueError):
    """The input is not the current Stage-3 extracted.csv contract."""


# Current flora-extractor.shared.schema.EXTRACTED_COLS, except paper_type which
# is accepted under its historical filter_status header too. Strict by default:
# silently turning a missing identity/evidence column into an empty string is
# data loss. Archived snapshots can be replayed explicitly with
# --allow-legacy-schema.
_CURRENT_EXTRACTED_COLUMNS = frozenset({
    "pair_id", "doi_r", "title_r", "abstract_r", "year_r", "authors_r",
    "journal_r", "url_r", "openalex_id_r", "source", "ref_r",
    "filter_method", "filter_evidence", "filter_confidence",
    "original_match_type", "original_match_confidence", "classify_llm_model",
    "oa_work_id_r", "oa_work_id_o", "study_r", "doi_o", "title_o", "study_o",
    "year_o", "authors_o", "ref_o", "bibtex_ref_o", "bibtex_ref_r",
    "link_method", "link_evidence", "link_confidence", "link_llm_model",
    "screen_categories", "doi_o_verification", "pdf_source", "parse_method",
    "outcome", "outcome_phrase", "outcome_confidence", "out_quote_source",
    "outcome_reasoning", "outcome_computation", "outcome_computational_quote",
    "out_quote_computational_source", "outcome_robustness",
    "outcome_robustness_quote", "out_quote_robust_source", "outcome_llm_model",
    "type", "original_rank", "n_originals",
})


def _validate_csv_schema(df: pd.DataFrame, allow_legacy: bool = False) -> None:
    if allow_legacy:
        return
    missing = sorted(_CURRENT_EXTRACTED_COLUMNS - set(df.columns))
    if not any(name in df.columns for name in ("paper_type", "filter_status")):
        missing.append("paper_type (or legacy filter_status)")
    if missing:
        raise InputSchemaError(
            "CSV is missing current extracted-schema column(s): "
            + ", ".join(missing)
            + ". Refusing to import blank substitutes. Use --allow-legacy-schema "
              "only for an intentional archived-snapshot replay."
        )


def _derive_url_o(doi_o: str) -> str:
    doi_o = str(doi_o or "").strip()
    return f"https://doi.org/{doi_o}" if doi_o else ""


def _url_o(row) -> str:
    """Build the original link from explicit URL, DOI, or OpenAlex identity."""
    explicit = _s(row.get("url_o"))
    if explicit:
        return explicit
    doi_url = _derive_url_o(row.get("doi_o"))
    if doi_url:
        return doi_url
    work_id = _work_id(row.get("oa_work_id_o") or row.get("openalex_id_o"))
    return f"https://openalex.org/{work_id}" if work_id else ""


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
    single row under validated's paper/study identity key.

    rows: dicts with 'key' (caller-chosen id — pair_id or record_id), the
          doi/study/title fields for each side. Rows with a non-blank doi_o are
          ignored.
    Returns {key: reason} for every row that should be flagged for admin review.
    Warning only — callers still import/update the row, just attach the reason
    somewhere a human will see it (admin_notes, a console summary, etc).
    """
    groups = {}
    for r in rows:
        if _s(r.get("doi_o")):
            continue
        groups.setdefault((
            _s(r.get("doi_r")), _s(r.get("study_r")), _normalize_title(r.get("title_r")),
        ), []).append(r)

    flagged = {}
    for members in groups.values():
        by_title = {}
        for r in members:
            original = (_s(r.get("study_o")), _normalize_title(r.get("title_o")))
            by_title.setdefault(original, []).append(r)
        for (_study_o, norm_title), dupes in by_title.items():
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


def _numeric_work_id(val) -> "int | None":
    """Numeric OpenAlex work identity used by the filter-engine lineage join."""
    work_id = _work_id(val)
    return int(work_id[1:]) if work_id else None


def _build_unvalidated_row(record_id: str, pair_id: str, row: pd.Series) -> dict:
    return {
        "record_id":         record_id,
        "pair_id":           pair_id,
        "doi_r":             _s(row.get("doi_r")),
        # Stage 3 writes within-paper study number(s) separately from titles.
        # Keeping both is essential when one paper contributes several pairs.
        "study_r":           _s(row.get("study_r")),
        "title_r":           _s(row.get("title_r")),
        "year_r":            _s(row.get("year_r")),
        "url_r":             _s(row.get("url_r")),
        "ref_r":             _s(row.get("ref_r")),
        "abstract_r":        _s(row.get("abstract_r")),
        "doi_o":             _s(row.get("doi_o")),
        "study_o":           _s(row.get("study_o")),
        "title_o":           _s(row.get("title_o")),
        "year_o":            _s(row.get("year_o")),
        "url_o":             _url_o(row),
        "ref_o":             _s(row.get("ref_o")),
        # OpenAlex work ids ship with newer extractor data; accept either name.
        "oa_work_id_o":      _work_id(row.get("oa_work_id_o") or row.get("openalex_id_o")),
        "oa_work_id_r":      _work_id(row.get("oa_work_id_r") or row.get("openalex_id_r")),
        "type":              _s(row.get("type")),
        # Reproduction axes are authoritative; derive the flat 4×3 outcome from
        # them so it can never contradict either coded field.
        "outcome":           stored_outcome(
            row.get("outcome"), row.get("type"),
            axes_coded=bool(_s(row.get("outcome_computation")) or _s(row.get("outcome_robustness"))),
            computation=row.get("outcome_computation"),
            robustness=row.get("outcome_robustness"),
        ),
        "outcome_quote":     _s(row.get("outcome_phrase")),
        # Compound values arrive with mixed spacing and the occasional self-join
        # ('abstract | abstract'); normalised on the way in so downstream needs
        # one parser. See extractor_vocab.normalize_quote_source.
        "out_quote_source":  normalize_quote_source(row.get("out_quote_source")),
        # Reproductions are coded on two independent axes, each with its own
        # evidence. Read those fields directly from the CSV; the flat outcome is
        # only their deterministic summary.
        # Replications legitimately have no reproduction-axis verdicts. Store
        # those blanks as SQL NULL: the axis CHECK constraints deliberately
        # reject '' so a missing verdict cannot masquerade as a vocabulary
        # value and roll back the whole import transaction.
        "outcome_computation":            _s(row.get("outcome_computation")) or None,
        "outcome_computational_quote":    _s(row.get("outcome_computational_quote")),
        "out_quote_computational_source": normalize_quote_source(row.get("out_quote_computational_source")),
        "outcome_robustness":             _s(row.get("outcome_robustness")) or None,
        "outcome_robustness_quote":       _s(row.get("outcome_robustness_quote")),
        "out_quote_robust_source":        normalize_quote_source(row.get("out_quote_robust_source")),
        "validation_status": "unvalidated",
    }


def _build_metadata_row(record_id: str, pair_id: str, row: pd.Series,
                        release_id: str = "") -> dict:
    return {
        "record_id":                  record_id,
        "pair_id":                    pair_id,
        "work_id":                    _numeric_work_id(
            row.get("oa_work_id_r") or row.get("openalex_id_r")
        ),
        # release_id is not part of today's EXTRACTED_COLS, but ad-hoc/forward
        # compatible exports may carry it. An explicit import-run value wins;
        # otherwise preserve the row value instead of silently dropping lineage.
        "release_id":                 _s(release_id) or _s(row.get("release_id")) or None,
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
        "screen_categories":          _s(row.get("screen_categories")),
        # Full-text provenance (flora-extractor #124). Blank on rows that never
        # acquired or parsed a document — note that link_method = 'llm_fulltext'
        # with a blank pdf_source is a contradiction, counted in run_import.
        "pdf_source":                 _s(row.get("pdf_source")),
        "parse_method":               _s(row.get("parse_method")),
        "outcome_confidence":         _s(row.get("outcome_confidence")),
        "outcome_reasoning":          _s(row.get("outcome_reasoning")),
        "outcome_llm_model":          _s(row.get("outcome_llm_model")),
        "authors_r":                  _s(row.get("authors_r")),
        "authors_o":                  _s(row.get("authors_o")),
        "journal_r":                  _s(row.get("journal_r")),
        "openalex_id_r":              _s(row.get("openalex_id_r")),
        "source":                     _s(row.get("source")),
        "bibtex_ref_o":               _s(row.get("bibtex_ref_o")),
        "bibtex_ref_r":               _s(row.get("bibtex_ref_r")),
        "original_rank":              _int_or_none(row.get("original_rank")),
        "n_originals":                _int_or_none(row.get("n_originals")),
    }


def _insert_unvalidated(cur, row: dict) -> bool:
    cur.execute(
        """
        INSERT INTO unvalidated (
            record_id, pair_id,
            doi_r, study_r, title_r, year_r, url_r, ref_r, abstract_r,
            doi_o, study_o, title_o, year_o, url_o, ref_o,
            oa_work_id_o, oa_work_id_r,
            type, outcome, outcome_quote, out_quote_source,
            outcome_computation, outcome_computational_quote, out_quote_computational_source,
            outcome_robustness, outcome_robustness_quote, out_quote_robust_source,
            validation_status
        ) VALUES (
            %(record_id)s, %(pair_id)s,
            %(doi_r)s, %(study_r)s, %(title_r)s, %(year_r)s, %(url_r)s, %(ref_r)s, %(abstract_r)s,
            %(doi_o)s, %(study_o)s, %(title_o)s, %(year_o)s, %(url_o)s, %(ref_o)s,
            %(oa_work_id_o)s, %(oa_work_id_r)s,
            %(type)s, %(outcome)s, %(outcome_quote)s, %(out_quote_source)s,
            %(outcome_computation)s, %(outcome_computational_quote)s, %(out_quote_computational_source)s,
            %(outcome_robustness)s, %(outcome_robustness_quote)s, %(out_quote_robust_source)s,
            %(validation_status)s
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
            record_id, pair_id, work_id, release_id,
            filter_status, filter_method, filter_evidence, filter_confidence,
            original_match_type, original_match_confidence, doi_o_verification,
            link_method, link_evidence, link_confidence, link_llm_model, screen_categories,
            pdf_source, parse_method,
            outcome_confidence, outcome_reasoning, outcome_llm_model,
            authors_r, authors_o, journal_r, openalex_id_r, source, bibtex_ref_o, bibtex_ref_r,
            original_rank, n_originals
        ) VALUES (
            %(record_id)s, %(pair_id)s, %(work_id)s, %(release_id)s,
            %(filter_status)s, %(filter_method)s, %(filter_evidence)s, %(filter_confidence)s,
            %(original_match_type)s, %(original_match_confidence)s, %(doi_o_verification)s,
            %(link_method)s, %(link_evidence)s, %(link_confidence)s, %(link_llm_model)s, %(screen_categories)s,
            %(pdf_source)s, %(parse_method)s,
            %(outcome_confidence)s, %(outcome_reasoning)s, %(outcome_llm_model)s,
            %(authors_r)s, %(authors_o)s, %(journal_r)s, %(openalex_id_r)s, %(source)s, %(bibtex_ref_o)s, %(bibtex_ref_r)s,
            %(original_rank)s, %(n_originals)s
        )
        ON CONFLICT (record_id) DO UPDATE SET
            pair_id                  = EXCLUDED.pair_id,
            work_id                  = COALESCE(EXCLUDED.work_id, record_metadata.work_id),
            release_id               = COALESCE(EXCLUDED.release_id, record_metadata.release_id),
            filter_status            = EXCLUDED.filter_status,
            filter_method            = EXCLUDED.filter_method,
            filter_evidence          = EXCLUDED.filter_evidence,
            filter_confidence        = EXCLUDED.filter_confidence,
            original_match_type      = EXCLUDED.original_match_type,
            original_match_confidence= EXCLUDED.original_match_confidence,
            doi_o_verification       = EXCLUDED.doi_o_verification,
            link_method              = EXCLUDED.link_method,
            link_evidence            = EXCLUDED.link_evidence,
            link_confidence          = EXCLUDED.link_confidence,
            link_llm_model           = EXCLUDED.link_llm_model,
            screen_categories        = EXCLUDED.screen_categories,
            pdf_source               = EXCLUDED.pdf_source,
            parse_method             = EXCLUDED.parse_method,
            outcome_confidence       = EXCLUDED.outcome_confidence,
            outcome_reasoning        = EXCLUDED.outcome_reasoning,
            outcome_llm_model        = EXCLUDED.outcome_llm_model,
            authors_r                = EXCLUDED.authors_r,
            authors_o                = EXCLUDED.authors_o,
            journal_r                = EXCLUDED.journal_r,
            openalex_id_r            = EXCLUDED.openalex_id_r,
            source                   = EXCLUDED.source,
            bibtex_ref_o             = EXCLUDED.bibtex_ref_o,
            bibtex_ref_r             = EXCLUDED.bibtex_ref_r,
            original_rank            = EXCLUDED.original_rank,
            n_originals              = EXCLUDED.n_originals
        """,
        row,
    )


def _refresh_study_identifiers(cur, record_id: str, row: pd.Series) -> None:
    """Refresh Stage-3 study identity on an existing pair.

    Older imports stored paper titles in these columns and the schema migration
    correctly moved those titles out, leaving the new study identifiers blank.
    A normal nightly re-import must therefore fill the identifiers even when the
    pair_id itself did not change. They are extractor-owned identity, not a
    validator correction, so the authoritative validated row receives them too.
    """
    if not {"study_r", "study_o"}.issubset(set(row.index)):
        return
    study_r, study_o = _s(row.get("study_r")), _s(row.get("study_o"))
    cur.execute(
        """
        UPDATE unvalidated
           SET study_r = %s, study_o = %s, updated_at = NOW()
         WHERE record_id = %s
           AND (study_r IS DISTINCT FROM %s OR study_o IS DISTINCT FROM %s)
        """,
        (study_r, study_o, record_id, study_r, study_o),
    )
    cur.execute(
        """
        UPDATE validated
           SET study_r = %s, study_o = %s
         WHERE record_id = %s
           AND (study_r IS DISTINCT FROM %s OR study_o IS DISTINCT FROM %s)
        """,
        (study_r, study_o, record_id, study_r, study_o),
    )


def _refresh_extractor_fields(cur, record_id: str, row: pd.Series) -> None:
    """Refresh every extractor-owned raw field for an existing ``pair_id``.

    Final/admin corrections live in separate ``final_*`` columns and are never
    touched. The authoritative validated row receives only identity backfills
    (study numbers and matching-paper OpenAlex ids), not a replacement human
    outcome. DOI triggers deliberately clear stale work ids, so work ids are
    restored in a second statement after all source DOI changes have fired.
    """
    incoming = _build_unvalidated_row(record_id, _s(row.get("pair_id")), row)
    cur.execute(
        """
        UPDATE unvalidated SET
            doi_r = %(doi_r)s, study_r = %(study_r)s, title_r = %(title_r)s,
            year_r = %(year_r)s, url_r = %(url_r)s, ref_r = %(ref_r)s,
            abstract_r = %(abstract_r)s,
            doi_o = %(doi_o)s, study_o = %(study_o)s, title_o = %(title_o)s,
            year_o = %(year_o)s, url_o = %(url_o)s, ref_o = %(ref_o)s,
            type = %(type)s, outcome = %(outcome)s,
            outcome_quote = %(outcome_quote)s, out_quote_source = %(out_quote_source)s,
            outcome_computation = %(outcome_computation)s,
            outcome_computational_quote = %(outcome_computational_quote)s,
            out_quote_computational_source = %(out_quote_computational_source)s,
            outcome_robustness = %(outcome_robustness)s,
            outcome_robustness_quote = %(outcome_robustness_quote)s,
            out_quote_robust_source = %(out_quote_robust_source)s,
            updated_at = NOW()
        WHERE record_id = %(record_id)s
        """,
        incoming,
    )
    cur.execute(
        """
        UPDATE unvalidated
           SET oa_work_id_o = %s, oa_work_id_r = %s
         WHERE record_id = %s
        """,
        (incoming["oa_work_id_o"], incoming["oa_work_id_r"], record_id),
    )
    cur.execute(
        """
        UPDATE validated SET
            study_r = %s,
            study_o = %s,
            oa_work_id_r = CASE
                WHEN doi_r IS NOT DISTINCT FROM %s AND %s IS NOT NULL THEN %s
                ELSE oa_work_id_r END,
            oa_work_id_o = CASE
                WHEN doi_o IS NOT DISTINCT FROM %s AND %s IS NOT NULL THEN %s
                ELSE oa_work_id_o END
        WHERE record_id = %s
        """,
        (
            incoming["study_r"], incoming["study_o"],
            incoming["doi_r"], incoming["oa_work_id_r"], incoming["oa_work_id_r"],
            incoming["doi_o"], incoming["oa_work_id_o"], incoming["oa_work_id_o"],
            record_id,
        ),
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


def _load_existing_pair_ids(cur) -> dict:
    """Map existing source pair ids to records for metadata refreshes."""
    cur.execute("SELECT pair_id, record_id::text FROM unvalidated WHERE pair_id IS NOT NULL")
    return {row[0]: row[1] for row in cur.fetchall()}


def _source_slot_key(row) -> "tuple[int, int] | None":
    """Stable extractor slot used when a DOI correction changes pair_id."""
    work_id = row.get("work_id") if hasattr(row, "get") else None
    if work_id in (None, ""):
        work_id = _numeric_work_id(
            row.get("oa_work_id_r") or row.get("openalex_id_r")
        )
    else:
        try:
            work_id = int(work_id)
        except (TypeError, ValueError):
            work_id = None
    rank = _int_or_none(row.get("original_rank"))
    return (work_id, rank) if work_id is not None and rank is not None else None


def _load_existing_source_slots(cur) -> dict:
    cur.execute(
        """
        SELECT u.record_id::text, u.pair_id, u.validation_status,
               m.work_id, m.original_rank,
               EXISTS (
                   SELECT 1 FROM validation_queue q
                   WHERE q.record_id = u.record_id
                     AND (q.is_shown = TRUE OR q.is_validated = TRUE)
               ) AS has_validator_activity
        FROM unvalidated u
        LEFT JOIN record_metadata m ON m.record_id = u.record_id
        WHERE u.pair_id IS NOT NULL
        """
    )
    grouped = {}
    for record_id, pair_id, status, work_id, rank, active in cur.fetchall():
        key = _source_slot_key({"work_id": work_id, "original_rank": rank})
        if key:
            grouped.setdefault(key, []).append({
                "record_id": record_id,
                "pair_id": pair_id,
                "validation_status": status,
                "has_validator_activity": bool(active),
            })
    return grouped


def _validate_source_slots(resolved: pd.DataFrame) -> None:
    slots = {}
    for _, row in resolved.iterrows():
        key = _source_slot_key(row)
        if key:
            slots.setdefault(key, []).append(_s(row.get("pair_id")))
    collisions = {key: ids for key, ids in slots.items() if len(set(ids)) > 1}
    if collisions:
        sample = ", ".join(
            f"work_id={key[0]}, rank={key[1]} ({len(ids)} rows)"
            for key, ids in list(collisions.items())[:10]
        )
        raise InputIdentityError(
            f"{len(collisions)} replication-work/original-rank slot collision(s): "
            f"{sample}. Refusing to guess which pair_id owns a source slot."
        )


def _refresh_rekeyed_record(cur, existing: dict, incoming: dict) -> None:
    """Apply an authoritative source re-key without overwriting final decisions."""
    cur.execute(
        """
        UPDATE unvalidated SET
            pair_id = %(pair_id)s,
            doi_r = %(doi_r)s, study_r = %(study_r)s, title_r = %(title_r)s,
            year_r = %(year_r)s, url_r = %(url_r)s, ref_r = %(ref_r)s,
            abstract_r = %(abstract_r)s,
            doi_o = %(doi_o)s, study_o = %(study_o)s, title_o = %(title_o)s,
            year_o = %(year_o)s, url_o = %(url_o)s, ref_o = %(ref_o)s,
            type = %(type)s, outcome = %(outcome)s,
            outcome_quote = %(outcome_quote)s, out_quote_source = %(out_quote_source)s,
            outcome_computation = %(outcome_computation)s,
            outcome_computational_quote = %(outcome_computational_quote)s,
            out_quote_computational_source = %(out_quote_computational_source)s,
            outcome_robustness = %(outcome_robustness)s,
            outcome_robustness_quote = %(outcome_robustness_quote)s,
            out_quote_robust_source = %(out_quote_robust_source)s,
            updated_at = NOW()
        WHERE record_id = %(record_id)s
        """,
        {**incoming, "record_id": existing["record_id"]},
    )
    # DOI-change triggers deliberately clear work ids. Restore the ids belonging
    # to the newly imported identities only after those triggers have run.
    cur.execute(
        """
        UPDATE unvalidated
           SET oa_work_id_o = %s, oa_work_id_r = %s
         WHERE record_id = %s
        """,
        (incoming["oa_work_id_o"], incoming["oa_work_id_r"], existing["record_id"]),
    )
    if existing["has_validator_activity"] and existing["validation_status"] != "rejected":
        note = (
            "Source pair_id changed after extraction (usually a corrected DOI). "
            "Raw source values were refreshed; prior validator/final values require review."
        )
        cur.execute(
            """
            UPDATE unvalidated
               SET validation_status = 'need_review',
                   admin_notes = CASE
                       WHEN admin_notes IS NULL OR admin_notes = '' THEN %s
                       WHEN position(%s in admin_notes) > 0 THEN admin_notes
                       ELSE admin_notes || E'\n' || %s END,
                   note_saved_by = COALESCE(note_saved_by, 'import'),
                   note_saved_at = NOW()
             WHERE record_id = %s
            """,
            (note, note, note, existing["record_id"]),
        )


def _validate_pair_ids(resolved) -> None:
    """Reject missing or colliding source identities before touching the DB.

    ``unvalidated.pair_id`` is unique and the insert uses ``ON CONFLICT DO
    NOTHING``. A malformed upstream CSV would therefore look like a successful
    import while dropping later rows. The live extractor now produces unique
    IDs; older snapshots need to be regenerated rather than guessed at here.
    """
    blank_count = int((resolved["pair_id"].map(_s) == "").sum())
    if blank_count:
        raise InputIdentityError(
            f"{blank_count} importable row(s) have a blank pair_id; refusing to "
            "import because the database identity cannot be preserved."
        )

    counts = resolved["pair_id"].value_counts()
    duplicates = counts[counts > 1]
    if not duplicates.empty:
        sample = ", ".join(
            f"{pair_id!r} ({int(count)} rows)"
            for pair_id, count in duplicates.head(10).items()
        )
        more = f"; plus {len(duplicates) - 10} more" if len(duplicates) > 10 else ""
        raise InputIdentityError(
            f"{len(duplicates)} duplicate pair_id group(s) found among importable "
            f"rows ({int((duplicates - 1).sum())} extra row(s)): {sample}{more}. "
            "Refusing to import rather than silently dropping rows; regenerate the "
            "CSV with the extractor's collision-safe pair_id generation."
        )


def _validate_resolved_identity(resolved: pd.DataFrame) -> None:
    """Reject resolved rows whose paper identity cannot be represented safely."""
    missing_work_id = resolved.apply(
        lambda row: _numeric_work_id(
            row.get("oa_work_id_r") or row.get("openalex_id_r")
        ) is None,
        axis=1,
    )
    if missing_work_id.any():
        raise InputIdentityError(
            f"{int(missing_work_id.sum())} resolved row(s) have no numeric "
            "replication work_id in oa_work_id_r/openalex_id_r; engine lineage "
            "would be NULL."
        )

    bad_replication = resolved.apply(
        lambda row: not any(
            _s(row.get(column)) for column in ("doi_r", "oa_work_id_r", "title_r")
        ),
        axis=1,
    )
    if bad_replication.any():
        raise InputIdentityError(
            f"{int(bad_replication.sum())} resolved row(s) have no replication DOI, "
            "OpenAlex id, or title."
        )

    no_doi_without_work = (
        resolved["doi_o_verification"].map(_s).eq("no_doi")
        & resolved["oa_work_id_o"].map(_s).eq("")
    )
    if no_doi_without_work.any():
        raise InputIdentityError(
            f"{int(no_doi_without_work.sum())} resolved no_doi original(s) have no "
            "oa_work_id_o; the validator would have no stable original identity."
        )


def run_import(csv_path: Path, dry_run: bool = False, release_id: str = "",
               allow_legacy_schema: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    print(f"Reading {csv_path} …")
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")

    _validate_csv_schema(df, allow_legacy=allow_legacy_schema)

    # Before anything else: refuse a CSV whose vocabulary we don't recognise.
    # An unknown link_method used to be indistinguishable from "not yet
    # resolved", which is how a rename upstream cost five weeks of imports.
    check_csv_vocabulary(df)

    # Filter to resolved rows only
    paper_type = _paper_type_column(df)
    resolved_mask = _resolved_mask(df)
    resolved = df[resolved_mask].copy()
    _validate_pair_ids(resolved)
    _validate_source_slots(resolved)
    if not allow_legacy_schema:
        _validate_resolved_identity(resolved)
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
                # Existing source rows are not reinserted, but their extractor
                # provenance is refreshed by the metadata upsert below.
                existing_source_slots = _load_existing_source_slots(cur)
                print(f"  Already in DB:      {len(existing_pair_ids)} pair_ids — will skip")

                # Which DOI-less originals can't be told apart by title? Computed
                # over the whole resolved set, so an incoming row is compared
                # against its siblings in this same CSV.
                ambiguous = _flag_ambiguous_doi_o_titles([
                    {"key": _s(r.get("pair_id")), "doi_r": r.get("doi_r"),
                     "study_r": r.get("study_r"), "title_r": r.get("title_r"),
                     "study_o": r.get("study_o"), "title_o": r.get("title_o"),
                     "doi_o": r.get("doi_o")}
                    for _, r in resolved.iterrows()
                ])

                inserted = 0
                rekeyed = 0
                skipped_dup = 0
                flagged_ambiguous = []

                for _, row in resolved.iterrows():
                    pair_id = _s(row.get("pair_id"))
                    if pair_id and pair_id in existing_pair_ids:
                        _refresh_extractor_fields(
                            cur, existing_pair_ids[pair_id], row
                        )
                        _insert_metadata(
                            cur,
                            _build_metadata_row(
                                existing_pair_ids[pair_id], pair_id, row, release_id
                            ),
                        )
                        skipped_dup += 1
                        continue

                    source_slot = _source_slot_key(row)
                    slot_matches = existing_source_slots.get(source_slot, []) if source_slot else []
                    if len(slot_matches) == 1:
                        existing = slot_matches[0]
                        incoming = _build_unvalidated_row(
                            existing["record_id"], pair_id, row
                        )
                        _refresh_rekeyed_record(cur, existing, incoming)
                        _insert_metadata(
                            cur,
                            _build_metadata_row(
                                existing["record_id"], pair_id, row, release_id
                            ),
                        )
                        existing_pair_ids.pop(existing["pair_id"], None)
                        existing_pair_ids[pair_id] = existing["record_id"]
                        rekeyed += 1
                        continue
                    if len(slot_matches) > 1:
                        raise InputIdentityError(
                            f"pair_id {pair_id!r} matches {len(slot_matches)} existing "
                            "records by work_id/original_rank; refusing an ambiguous re-key."
                        )

                    record_id = str(uuid.uuid4())

                    if _insert_unvalidated(cur, _build_unvalidated_row(record_id, pair_id, row)):
                        _insert_metadata(
                            cur, _build_metadata_row(record_id, pair_id, row, release_id)
                        )
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

        print(
            f"\nDone. Inserted: {inserted}  |  Re-keyed: {rekeyed}  |  "
            f"Skipped (already in DB): {skipped_dup}"
        )
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
    parser.add_argument(
        "--release-id", default="",
        help="Filter-engine routing release stamped onto every imported row.",
    )
    parser.add_argument(
        "--allow-legacy-schema", action="store_true",
        help="Allow archived CSV headers that predate the current extractor contract.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    run_import(
        args.input,
        dry_run=args.dry_run,
        release_id=args.release_id,
        allow_legacy_schema=args.allow_legacy_schema,
    )
