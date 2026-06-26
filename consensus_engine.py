"""
consensus_engine.py — Determines validation outcome after both human validators submit.

Decision tree:
  1. checks agree + corrections agree → LLM sanity check → validated (humans always win)
  2. checks agree + corrections differ → need_review (no LLM)
  3. checks differ → LLM tiebreaker → agrees with one human → validated, else → need_review
"""
import json
import re
import random
from datetime import datetime, timezone

from llm_validator import run_llm_validation

_CHECK_FIELDS = ["type_check", "original_check", "outcome_check"]
_CORRECTION_FIELDS = ["corrected_doi_o", "corrected_study_o", "corrected_outcome", "corrected_type", "corrected_study_r", "corrected_url_r"]


def _normalize(text: str | None) -> str:
    """Lowercase, strip punctuation and whitespace — used for fuzzy abstract comparison."""
    if not text:
        return ""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _checks_agree(h1: dict, h2: dict) -> bool:
    return all(h1.get(f) == h2.get(f) for f in _CHECK_FIELDS)


def _corrections_agree(h1: dict, h2: dict) -> bool:
    if not all(h1.get(f) == h2.get(f) for f in _CORRECTION_FIELDS):
        return False
    # Normalize text fields before comparing so minor formatting differences don't cause conflicts
    return _normalize(h1.get("corrected_abstract")) == _normalize(h2.get("corrected_abstract"))


def _llm_matches(llm: dict, human: dict) -> bool:
    if llm.get("error"):
        return False
    return all(llm.get(f) == human.get(f) for f in _CHECK_FIELDS)


def quote_source_for(quote: str | None, abstract: str | None) -> str | None:
    """Where does this outcome quote come from?
      'abstract'  — the (normalised) quote is contained in the (normalised) abstract
      'full_text' — it isn't (so it must have come from the paper body)
      None        — there is no quote to place.
    Normalising (lowercase, drop punctuation/whitespace) makes the match robust to
    the light edits validators make to quotes."""
    nq = _normalize(quote)
    if not nq:
        return None
    return "abstract" if nq in _normalize(abstract) else "full_text"


def _resolve_quote_source(record: dict, suggested_quotes: list, abstract: str | None = None) -> str | None:
    """Pick the outcome_quote source per the agreed rule:
      - validators suggested a new quote → check the longest against the abstract;
      - they agreed with the extracted quote → keep the existing source, no re-check;
      - no quote at all → leave it unset.
    `abstract` defaults to the record's extracted abstract; callers pass the FINAL
    (possibly validator-corrected) abstract so the check matches what gets published."""
    if abstract is None:
        abstract = record.get("abstract_r")
    if suggested_quotes:
        return quote_source_for(max(suggested_quotes, key=len), abstract)
    if not _normalize(record.get("outcome_quote")):
        return None
    return record.get("out_quote_source")


def _resolve_final(record: dict, winner: dict, other: dict | None = None) -> dict:
    """Build final consensus values using winner's corrections, falling back to original record.
    outcome_quote: when validators edited it, the longest edit wins (most context)."""
    quotes = [h.get("corrected_outcome_quote") for h in [winner, other] if h and h.get("corrected_outcome_quote")]
    abstracts = [h.get("corrected_abstract") for h in [winner, other] if h and h.get("corrected_abstract")]
    final_abstract = random.choice(abstracts) if abstracts else None
    return {
        "study_r": winner.get("corrected_study_r") or record.get("study_r"),
        "url_r":   winner.get("corrected_url_r")    or record.get("url_r"),
        "doi_o":   winner.get("corrected_doi_o")   or record.get("doi_o"),
        "study_o": winner.get("corrected_study_o") or record.get("study_o"),
        "outcome": winner.get("corrected_outcome") or record.get("outcome"),
        "type":    winner.get("corrected_type")    or record.get("type"),
        "outcome_quote": max(quotes, key=len) if quotes else record.get("outcome_quote"),
        # check the source against the same abstract we publish (corrected if present)
        "out_quote_source": _resolve_quote_source(record, quotes, final_abstract or record.get("abstract_r")),
        "abstract_r": final_abstract,
    }


def _update_status(cur, record_id: str, status: str, is_tiebreaker: bool,
                   final: dict | None, llm_summary: dict | None) -> None:
    params: list = [status, is_tiebreaker]
    set_clauses = ["validation_status = %s", "is_tiebreaker = %s"]

    if final:
        set_clauses += [
            "final_study_r = %s", "final_url_r = %s",
            "final_doi_o = %s", "final_study_o = %s",
            "final_outcome = %s", "final_type = %s",
            "final_outcome_quote = %s",
        ]
        params += [final.get("study_r"), final.get("url_r"), final["doi_o"], final["study_o"],
                   final["outcome"], final["type"], final.get("outcome_quote")]
        set_clauses.append("final_out_quote_source = %s")
        params.append(final.get("out_quote_source"))
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
        INSERT INTO validated (
            record_id, doi_r, study_r, year_r, url_r, ref_r, abstract_r,
            doi_o, study_o, year_o, url_o, ref_o,
            type, outcome, outcome_quote, out_quote_source, validated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (doi_r, study_r, doi_o, study_o) DO NOTHING
        """,
        (
            record.get("record_id"),
            record.get("doi_r"),
            final.get("study_r") or record.get("study_r"),
            record.get("year_r"),
            final.get("url_r") or record.get("url_r"),
            record.get("ref_r"),
            final.get("abstract_r") or record.get("abstract_r"),
            final["doi_o"],
            final["study_o"],
            record.get("year_o"),
            record.get("url_o"),
            record.get("ref_o"),
            final["type"],
            final["outcome"],
            final.get("outcome_quote") or record.get("outcome_quote"),
            final.get("out_quote_source") or record.get("out_quote_source"),
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
               corrected_doi_o, corrected_study_o, corrected_outcome, corrected_type,
               corrected_study_r, corrected_url_r, corrected_abstract, corrected_outcome_quote
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
                   "corrected_doi_o", "corrected_study_o", "corrected_outcome", "corrected_type",
                   "corrected_study_r", "corrected_url_r", "corrected_abstract", "corrected_outcome_quote"]
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

    # Check if both human validators are senior (bypasses admin review on agreement)
    cur.execute(
        """
        SELECT COUNT(*) AS senior_count
        FROM validation_queue vq
        JOIN validators v ON v.id = vq.validator_id
        WHERE vq.record_id = %s
          AND vq.is_validated = TRUE
          AND vq.validator_slot IN ('human_1', 'human_2')
          AND v.validator_tier >= 2
        """,
        (record_id,),
    )
    senior_row = cur.fetchone()
    has_senior = (senior_row["senior_count"] if isinstance(senior_row, dict) else senior_row[0]) >= 1

    checks_ok = _checks_agree(h1, h2)
    corrections_ok = _corrections_agree(h1, h2)

    if checks_ok and corrections_ok:
        # Both validators agree this is not a replication → LLM confirms or sends to admin
        if (h1.get("corrected_type") == "not_validation" and
                h2.get("corrected_type") == "not_validation"):
            llm = run_llm_validation(record, context="sanity_check")
            if not llm.get("error") and _llm_matches(llm, h1):
                _update_status(cur, record_id, "rejected", False, None, llm)
            else:
                # LLM thinks it IS a replication — admin should review
                _update_status(cur, record_id, "need_review", False, None, llm)
            return

        llm = run_llm_validation(record, context="sanity_check")
        final = _resolve_final(record, h1, h2)
        if has_senior:
            # At least one senior agreed — auto-validate, no admin review needed
            _update_status(cur, record_id, "validated", False, final, llm)
            _insert_validated(cur, record, final)
        else:
            # Normal agreement — admin must approve
            _update_status(cur, record_id, "consensus_reached", False, final, llm)

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
            if h1.get("corrected_type") == "not_validation":
                # LLM + h1 agree it's not a replication, but h2 disagrees → admin decides
                _update_status(cur, record_id, "need_review", True, None, llm)
            else:
                final = _resolve_final(record, h1, h2)
                _update_status(cur, record_id, "consensus_reached", True, final, llm)
        elif matches_h2 and not matches_h1:
            if h2.get("corrected_type") == "not_validation":
                # LLM + h2 agree it's not a replication, but h1 disagrees → admin decides
                _update_status(cur, record_id, "need_review", True, None, llm)
            else:
                final = _resolve_final(record, h2, h1)
                _update_status(cur, record_id, "consensus_reached", True, final, llm)
        else:
            # 3-way split or LLM matches neither/both
            _update_status(cur, record_id, "need_review", True, None, llm)
