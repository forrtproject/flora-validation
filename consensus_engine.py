"""
consensus_engine.py — Determines validation outcome after both human validators submit.

Decision tree:
  1. checks agree + corrections agree → LLM sanity check → validated (humans always win)
  2. checks agree + corrections differ → need_review (no LLM)
  3. checks differ → LLM tiebreaker → agrees with one human → validated, else → need_review
"""
import json
import random
from datetime import datetime, timezone

from llm_validator import run_llm_validation

_CHECK_FIELDS = ["type_check", "original_check", "outcome_check"]
_CORRECTION_FIELDS = ["corrected_doi_o", "corrected_study_o", "corrected_outcome", "corrected_type"]


def _checks_agree(h1: dict, h2: dict) -> bool:
    return all(h1.get(f) == h2.get(f) for f in _CHECK_FIELDS)


def _corrections_agree(h1: dict, h2: dict) -> bool:
    return all(h1.get(f) == h2.get(f) for f in _CORRECTION_FIELDS)


def _llm_matches(llm: dict, human: dict) -> bool:
    if llm.get("error"):
        return False
    return all(llm.get(f) == human.get(f) for f in _CHECK_FIELDS)


def _resolve_final(record: dict, winner: dict, other: dict | None = None) -> dict:
    """Build final consensus values using winner's corrections, falling back to original record.
    outcome_quote is picked randomly from whichever validators provided a correction."""
    quotes = [h.get("corrected_outcome_quote") for h in [winner, other] if h and h.get("corrected_outcome_quote")]
    abstracts = [h.get("corrected_abstract") for h in [winner, other] if h and h.get("corrected_abstract")]
    return {
        "doi_o": winner.get("corrected_doi_o") or record.get("doi_o"),
        "study_o": winner.get("corrected_study_o") or record.get("study_o"),
        "outcome": winner.get("corrected_outcome") or record.get("outcome"),
        "type": winner.get("corrected_type") or record.get("type"),
        "outcome_quote": random.choice(quotes) if quotes else record.get("outcome_quote"),
        "abstract_r": random.choice(abstracts) if abstracts else None,
    }


def _update_status(cur, record_id: str, status: str, is_tiebreaker: bool,
                   final: dict | None, llm_summary: dict | None) -> None:
    params: list = [status, is_tiebreaker]
    set_clauses = ["validation_status = %s", "is_tiebreaker = %s"]

    if final:
        set_clauses += [
            "final_doi_o = %s", "final_study_o = %s",
            "final_outcome = %s", "final_type = %s",
        ]
        params += [final["doi_o"], final["study_o"], final["outcome"], final["type"]]
        if final.get("abstract_r"):
            set_clauses.append("abstract_r = %s")
            params.append(final["abstract_r"])

    if llm_summary is not None:
        set_clauses.append("llm_validator = %s")
        params.append(json.dumps(llm_summary))

    params.append(record_id)
    cur.execute(
        f"UPDATE unvalidated SET {', '.join(set_clauses)} WHERE record_id = %s",
        params,
    )


def _insert_validated(cur, record: dict, final: dict) -> None:
    cur.execute(
        """
        INSERT INTO validated (record_id, pair_id, doi_r, study_r, year_r,
            doi_o, study_o, year_o, type, outcome, outcome_quote,
            out_quote_source, validated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (record_id) DO NOTHING
        """,
        (
            record.get("record_id"),
            record.get("pair_id"),
            record.get("doi_r"),
            record.get("study_r"),
            record.get("year_r"),
            final["doi_o"],
            final["study_o"],
            record.get("year_o"),
            final["type"],
            final["outcome"],
            final.get("outcome_quote") or record.get("outcome_quote"),
            record.get("out_quote_source"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def evaluate_consensus(cur, record_id: str) -> None:
    """
    Called after each human submission. Reads completed human rows from
    validation_queue, applies the decision tree, and writes the outcome
    back to unvalidated (and validated on success).

    Args:
        cur: open psycopg2 cursor (caller manages transaction/commit)
        record_id: UUID of the record being evaluated
    """
    cur.execute(
        """
        SELECT validator_slot, type_check, original_check, outcome_check,
               corrected_doi_o, corrected_study_o, corrected_outcome, corrected_type
        FROM validation_queue
        WHERE record_id = %s AND is_validated = TRUE
        ORDER BY validator_slot
        """,
        (record_id,),
    )
    rows = cur.fetchall()
    if len(rows) < 2:
        return

    # Support dict rows (DictCursor / tests) and tuple rows (default cursor)
    _human_cols = ["validator_slot", "type_check", "original_check", "outcome_check",
                   "corrected_doi_o", "corrected_study_o", "corrected_outcome", "corrected_type"]
    if rows and isinstance(rows[0], dict):
        humans = [dict(row) for row in rows]
    else:
        humans = [dict(zip(_human_cols, row)) for row in rows]
    h1, h2 = humans[0], humans[1]

    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
    record_row = cur.fetchone()
    if record_row is None:
        return

    if isinstance(record_row, dict):
        record = dict(record_row)
    else:
        record_cols = [d[0] for d in cur.description]
        record = dict(zip(record_cols, record_row))

    checks_ok = _checks_agree(h1, h2)
    corrections_ok = _corrections_agree(h1, h2)

    if checks_ok and corrections_ok:
        # Branch 1: full agreement — LLM sanity check (humans always win regardless)
        llm = run_llm_validation(record, context="sanity_check")
        final = _resolve_final(record, h1, h2)
        _update_status(cur, record_id, "validated", False, final, llm)
        _insert_validated(cur, record, final)

    elif checks_ok and not corrections_ok:
        # Branch 2: checks agree but corrections differ → need_review, no LLM
        _update_status(cur, record_id, "need_review", False, None, None)

    else:
        # Branch 3: checks differ → LLM tiebreaker
        llm = run_llm_validation(record, context="tiebreaker")

        if llm.get("error"):
            _update_status(cur, record_id, "need_review", True, None, llm)
            return

        matches_h1 = _llm_matches(llm, h1)
        matches_h2 = _llm_matches(llm, h2)

        if matches_h1 and not matches_h2:
            final = _resolve_final(record, h1, h2)
            _update_status(cur, record_id, "validated", True, final, llm)
            _insert_validated(cur, record, final)
        elif matches_h2 and not matches_h1:
            final = _resolve_final(record, h2, h1)
            _update_status(cur, record_id, "validated", True, final, llm)
            _insert_validated(cur, record, final)
        else:
            # 3-way split or LLM matches neither/both
            _update_status(cur, record_id, "need_review", True, None, llm)
