"""One screen for a replication paper with several coded originals.

The extractor codes one record per (replication, original) pair, and each record
keeps its own two human judgements and its own consensus. This module only changes
how a validator gets to them: the paper's open records are reserved together and
answered from one table, then submitted as ordinary judgements, one per record.

Sibling verdicts are never served, so one validator cannot anchor another. A sibling
this validator cannot judge is listed for context with no reason given.

The first version covers replications with a DOI in the normal pool. Reproductions
(two axes per record), the hard pool, assignments and onboarding keep the
one-record screen. PAPER_REVIEW_ENABLED turns the table on.
"""
import os

from consensus_engine import NO_OUTCOME, effective_corrected_type

_PAPER_LIMIT = 50

_HARD_RECORD = "(u.outcome = 'cannot_be_determined' OR u.abstract_r IS NULL OR u.abstract_r = '')"

# The same set _coded_originals lists: an extractor record that is not rejected.
_SAME_PAPER = ("lower(u.doi_r) = lower(%s) AND u.validation_status <> 'rejected'"
               " AND u.origin = 'extractor'")

_CONTEXT_FIELDS = ("record_id", "title_o", "authors_o", "year_o", "study_o", "study_r",
                   "doi_o", "url_o", "oa_work_id_o")
_JUDGEABLE_FIELDS = ("pair_id", "title_r", "doi_r", "type", "outcome", "outcome_quote",
                     "out_quote_source", "abstract_r", "link_evidence")


def enabled() -> bool:
    return os.environ.get("PAPER_REVIEW_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def available(pair: dict) -> bool:
    """Whether this record's paper can open in the table."""
    return (
        enabled()
        and (pair.get("type") or "").lower() == "replication"
        and bool((pair.get("doi_r") or "").strip())
        and pair.get("outcome") != "cannot_be_determined"
        and bool((pair.get("abstract_r") or "").strip())
    )


def _judgeable(row: dict, base_type: str) -> bool:
    return (
        row["validation_status"] in ("unvalidated", "validation_inprogress")
        and row["restricted_access"] is not True
        and not row["is_hard"]
        and (row["type"] or "").lower() == base_type
    )


def _sort_key(row: dict):
    rank, year, title = row["original_rank"], row["year_o"], row["title_o"]
    return (rank is None, rank or 0, year is None, year or "", title is None, title or "",
            str(row["record_id"]))


def _own_slot(cur, coder_id: int, record_id):
    cur.execute(
        "SELECT queue_id, is_validated, started_at FROM validation_queue "
        "WHERE validator_id = %s AND record_id = %s LIMIT 1",
        (coder_id, record_id),
    )
    return cur.fetchone()


def claim(cur, coder_id: int, record_id: str, claim_slot, oa_url_for) -> dict | None:
    """Reserve this validator's open records for the paper and return the set.

    Safe to repeat: a slot the validator already holds is kept, and a buffered one
    is promoted to the active lock. Returns None unless the validator holds an open
    slot on `record_id`, or when that record cannot open in the table.

    `claim_slot(cur, coder_id, record_id, started)` takes a free human slot and
    returns its queue_id, or None if another validator got there first.
    """
    cur.execute(
        """
        SELECT u.record_id, u.doi_r, u.type, u.outcome, u.abstract_r
        FROM unvalidated u
        JOIN validation_queue vq ON vq.record_id = u.record_id
        WHERE u.record_id = %s AND vq.validator_id = %s AND vq.is_validated = FALSE
          AND vq.validator_slot IN ('human_1', 'human_2')
        """,
        (record_id, coder_id),
    )
    base = cur.fetchone()
    if not base or not available(base):
        return None

    # Locks the open records by record_id before any queue row changes, so two claims
    # on one paper, or a claim and a reaper pass, always take them in the same order.
    cur.execute(
        f"""
        SELECT u.record_id FROM unvalidated u
        WHERE {_SAME_PAPER}
        ORDER BY u.record_id
        LIMIT %s
        FOR UPDATE OF u
        """,
        (base["doi_r"], _PAPER_LIMIT),
    )
    cur.fetchall()
    cur.execute(
        f"""
        SELECT u.record_id, u.pair_id, u.doi_r, u.title_r, u.study_r, u.type, u.outcome,
               u.outcome_quote, u.out_quote_source, u.abstract_r, u.validation_status,
               u.restricted_access, {_HARD_RECORD} AS is_hard,
               u.doi_o, u.study_o, u.title_o, u.year_o, u.url_o, u.oa_work_id_o,
               rm.authors_o, rm.original_rank, rm.link_evidence
        FROM unvalidated u
        LEFT JOIN record_metadata rm ON rm.record_id = u.record_id
        WHERE {_SAME_PAPER}
        ORDER BY u.record_id
        LIMIT %s
        """,
        (base["doi_r"], _PAPER_LIMIT),
    )
    rows = [dict(r) for r in cur.fetchall()]
    record_ids = [r["record_id"] for r in rows]
    cur.execute(
        """
        SELECT record_id, queue_id, is_validated, started_at
        FROM validation_queue
        WHERE validator_id = %s AND record_id = ANY(%s::uuid[])
        """,
        (coder_id, [str(r) for r in record_ids]),
    )
    own = {str(r["record_id"]): r for r in cur.fetchall()}

    base_type = (base["type"] or "").lower()
    originals = []
    for row in sorted(rows, key=_sort_key):
        rid = str(row["record_id"])
        queue_id = None
        slot = own.get(rid)
        # A slot already held counts only if the table can answer it: a buffered
        # reproduction or hard-pool sibling stays on its own screen.
        if _judgeable(row, base_type):
            if not slot:
                queue_id = claim_slot(cur, coder_id, row["record_id"], started=True)
                if not queue_id:
                    # This browser's buffer top-up may have got there first.
                    slot = _own_slot(cur, coder_id, row["record_id"])
        else:
            slot = None
        if slot and not slot["is_validated"]:
            queue_id = slot["queue_id"]
            if slot["started_at"] is None:
                cur.execute(
                    "UPDATE validation_queue SET started_at = NOW(), shown_at = NOW() "
                    "WHERE queue_id = %s", (queue_id,))

        entry = {field: row.get(field) for field in _CONTEXT_FIELDS}
        entry["record_id"] = rid
        entry["oa_url_o"] = oa_url_for(row.get("doi_o"))
        entry["judgeable"] = queue_id is not None
        if queue_id is not None:
            entry["queue_id"] = str(queue_id)
            entry.update({field: row.get(field) for field in _JUDGEABLE_FIELDS})
            entry["outcome_phrase"] = row.get("outcome_quote") or ""
        originals.append(entry)
    return {"doi_r": base["doi_r"], "originals": originals}


def marker(additional_checks) -> dict | None:
    """The table's note on a judgement: which outcome check it answered and whether
    that check covered several originals. A malformed note counts as no note."""
    if not isinstance(additional_checks, dict):
        return None
    note = additional_checks.get("paper_review")
    if not isinstance(note, dict) or not isinstance(note.get("outcome_group"), str):
        return None
    return note


def points(cur, rec: dict, coder_id: int, req, vote_score: int, improved_evidence: bool,
           note: dict) -> int:
    """Points for one record answered from the table.

    The base score covers the paper, so it and any note bonus are paid once. Each
    original answered pays 2 and each outcome check answered pays 2, whatever the
    answer. Originals sharing a check share those 2 points.

    The caller holds the validators row lock, so the paper's records are counted one
    at a time however quickly the browser sends them.
    """
    cur.execute(
        """
        SELECT vq.additional_checks, vq.validator_notes
        FROM validation_queue vq
        JOIN unvalidated u ON u.record_id = vq.record_id
        WHERE vq.validator_id = %s AND vq.is_validated = TRUE
          AND vq.validator_slot IN ('human_1', 'human_2')
          AND lower(u.doi_r) = lower(%s) AND u.record_id <> %s
        """,
        (coder_id, rec["doi_r"], rec["record_id"]),
    )
    prior = [dict(r) for r in cur.fetchall()]
    total = 0 if prior else vote_score
    if (req.validator_notes or "").strip() and not any(
            (p.get("validator_notes") or "").strip() for p in prior):
        total += 1
    if effective_corrected_type({"type_check": req.type_check,
                                 "corrected_type": req.corrected_type}) == "not_validation":
        return total
    total += 2
    group = note["outcome_group"]
    check_paid = any((marker(p.get("additional_checks")) or {}).get("outcome_group") == group
                     for p in prior)
    if req.outcome_check != NO_OUTCOME and not check_paid:
        total += 2
        if improved_evidence:
            total += 1
    return total


def admin_paper_set(cur, record: dict) -> list[dict]:
    """Every coded original of this entry's paper, for the admin entry detail."""
    doi = (record.get("doi_r") or "").strip()
    if not doi:
        return []
    cur.execute(
        f"""
        SELECT u.record_id::text AS record_id, u.title_o, u.final_title_o, u.year_o, u.doi_o,
               u.final_doi_o, u.outcome, u.final_outcome, u.validation_status,
               u.validator_1, u.validator_2, rm.authors_o, rm.original_rank
        FROM unvalidated u
        LEFT JOIN record_metadata rm ON rm.record_id = u.record_id
        WHERE lower(u.doi_r) = lower(%s) AND u.origin = 'extractor'
        ORDER BY rm.original_rank NULLS LAST, u.year_o NULLS LAST, u.title_o, u.record_id
        LIMIT %s
        """,
        (doi, _PAPER_LIMIT),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if len(rows) < 2:
        return []
    out = []
    for row in rows:
        judgements = [v for v in (row.pop("validator_1"), row.pop("validator_2")) if isinstance(v, dict)]
        row["original_checks"] = [v.get("original_check") for v in judgements]
        row["outcome_checks"] = [v.get("outcome_check") for v in judgements]
        row["shared_check"] = any(
            (marker(v.get("additional_checks")) or {}).get("outcome_shared") is True
            for v in judgements)
        row["is_current"] = row["record_id"] == str(record.get("record_id"))
        out.append(row)
    return out
