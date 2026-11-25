"""Admin review of judged disagreements, and publishing the answers to FLoRA.

An admin sees each record with both validators' answers by name, and approves
the final answer: an original, a type and an outcome (a reproduction's on its
two axes, with a quote for each), edited where needed. That answer is a row in
adjudication.final; approval works the same whether the two judges agreed or
not.

Publishing copies an approved answer into Source Records under its own source,
`adjudicated`, with an `ADJ-` display id; the FLoRA build then takes it like any
other source row. It is the one write this feature makes outside its own schema
(besides validators' points), and it follows sync_validated.py's rules: a row a
reviewer has edited in Source Records is never overwritten, and nothing is
deleted. Withdrawing marks the row deleted, which takes it out of the build and
keeps its display id reserved; publishing again brings it back.

The build refuses to write any export once a replication outcome is missing from
outcome_alias, so an outcome is checked against that table before it is
published, not after.
"""

from __future__ import annotations

import json
import re

from extractor_vocab import (
    CURRENT_REPRODUCTION_OUTCOMES,
    REPLICATION_OUTCOMES,
    split_joined_outcome,
)

from .judging import (
    CANNOT_TELL,
    COMPUTATION_CHOICES,
    NOT_A_REPLICATION,
    REPLICATION_CHOICES,
    ROBUSTNESS_CHOICES,
    Refused,
    _DOI,
    _doi,
    _observatory_outcome,
    check_outcome,
    public_view,
)

SOURCE_KEY = "adjudicated"
DISPLAY_PREFIX = "ADJ"
STATUSES = ("open", "awaiting_approval", "approved", "published")
TEXT_LIMITS = {"title_o": 1000, "outcome_quote": 4000, "quote_source": 100,
               "computation_quote": 4000, "computation_quote_source": 100,
               "robustness_quote": 4000, "robustness_quote_source": 100, "admin_note": 2000}
WITHDRAWN_REASON = "Withdrawn from the Observatory adjudication by {admin}."
UNDETERMINED = "cannot_be_determined"
# An approved answer's quotes: one for a replication, one per axis for a
# reproduction. The other kind's are stored empty.
QUOTE_FIELDS = ("outcome_quote", "quote_source")
AXIS_QUOTE_FIELDS = ("computation_quote", "computation_quote_source",
                     "robustness_quote", "robustness_quote_source")


def _text(value, field: str) -> str | None:
    text = str(value or "").strip()
    if len(text) > TEXT_LIMITS[field]:
        raise Refused(422, f"{field} is longer than {TEXT_LIMITS[field]} characters")
    return text or None


def record_type(outcome: str | None) -> str:
    """The type a flat outcome implies, for a row saved before types were asked."""
    if outcome in CURRENT_REPRODUCTION_OUTCOMES:
        return "reproduction"
    return "not_validation" if outcome == NOT_A_REPLICATION else "replication"


def answer_type(row: dict) -> str:
    """A judgement's or an approved answer's type: its own, or its outcome's."""
    return row.get("type") or record_type(row.get("outcome"))


def answer_axes(row: dict) -> tuple[str | None, str | None]:
    """A reproduction answer's (computation, robustness); (None, None) for any
    other. A row saved before the axes were stored has them in its flat outcome.
    The one place a joined outcome is split, for every reader of these rows."""
    if answer_type(row) != "reproduction":
        return None, None
    if row.get("outcome_computation") or row.get("outcome_robustness"):
        return row.get("outcome_computation"), row.get("outcome_robustness")
    return split_joined_outcome(row.get("outcome"))


def typed(row: dict) -> dict:
    """The row with its type and axes filled in where it was saved without them."""
    computation, robustness = answer_axes(row)
    return {**row, "type": answer_type(row), "outcome_computation": computation,
            "outcome_robustness": robustness}


def _verdict(row: dict) -> tuple:
    """What a judge said the paper is and found, as one comparable value."""
    kind = answer_type(row)
    return (kind, answer_axes(row) if kind == "reproduction" else (row.get("outcome"),))


def agreement(judgements: list[dict]) -> str | None:
    """'agree' when both submitted answers match on original, type and outcome
    (both axes, for a reproduction) and neither says "can't tell", 'disagree'
    when they do not, None before there are two."""
    done = [j for j in judgements if j.get("original_choice")]
    if len(done) < 2:
        return None
    a, b = done[0], done[1]
    kind, found = _verdict(a)
    same = (a["original_choice"] == b["original_choice"]
            and (a.get("suggested_doi_o") or "") == (b.get("suggested_doi_o") or "")
            and (kind, found) == _verdict(b)
            and a["original_choice"] != CANNOT_TELL and CANNOT_TELL not in found)
    return "agree" if same else "disagree"


def basis_for(record: dict, doi_o: str | None, outcome: str) -> str:
    """Whose answer the admin approved: FLoRA's, the Observatory's, or their own.
    A reproduction's outcome is the flat value derived from its axes, so it is
    FLoRA's when it is FLoRA's flat outcome."""
    doi, flora, mo = _doi(doi_o), _doi(record.get("flora_doi_o")), _doi(record.get("mo_doi_o"))
    if doi and flora == mo == doi:
        # The same original on both sides: the outcome decides.
        if outcome == str(record.get("flora_outcome") or "").lower():
            return "flora"
        return "observatory" if _observatory_outcome(record) == outcome else "admin"
    if doi and doi == flora:
        return "flora"
    if doi and doi == mo:
        return "observatory"
    return "admin"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

_LIST_SQL = """
    SELECT r.record_id::text AS record_id, r.kind, r.doi_r, r.title_r, r.status, r.updated_at,
           f.doi_o AS final_doi_o, f.type AS final_type, f.outcome AS final_outcome,
           f.outcome_computation AS final_outcome_computation,
           f.outcome_robustness AS final_outcome_robustness, f.basis,
           f.published_record_id, f.withdrawn_at,
           COALESCE(json_agg(json_build_object(
               'handle', j.validator_handle, 'tier', j.validator_tier,
               'original_choice', j.original_choice, 'suggested_doi_o', j.suggested_doi_o,
               'type', j.type, 'outcome', j.outcome,
               'outcome_computation', j.outcome_computation,
               'outcome_robustness', j.outcome_robustness,
               'original_prefilled', j.original_prefilled,
               'outcome_prefilled', j.outcome_prefilled) ORDER BY j.submitted_at)
               FILTER (WHERE j.state = 'submitted'), '[]') AS judgements
      FROM adjudication.records r
      LEFT JOIN adjudication.final f ON f.record_id = r.record_id
      LEFT JOIN adjudication.judgements j ON j.record_id = r.record_id
     WHERE (%(status)s::text IS NULL OR r.status = %(status)s)
     GROUP BY r.record_id, f.record_id
     ORDER BY array_position(%(order)s::text[], r.status), r.updated_at DESC, r.record_id
"""


def list_records(cur, status: str | None) -> dict:
    if status is not None and status not in STATUSES:
        raise Refused(422, f"Unknown status {status!r}")
    cur.execute(_LIST_SQL, {"status": status,
                            "order": ["awaiting_approval", "approved", "published", "open"]})
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        judgements = list(item["judgements"] or [])
        hide = item["status"] == "open"
        item["judgements"] = [_unanswered(j) if hide else typed(j) for j in judgements]
        item["agreement"] = agreement(item["judgements"])
        if item["final_outcome"] is not None:
            final = typed({"type": item["final_type"], "outcome": item["final_outcome"],
                           "outcome_computation": item["final_outcome_computation"],
                           "outcome_robustness": item["final_outcome_robustness"]})
            item.update({f"final_{k}": final[k] for k in
                         ("type", "outcome_computation", "outcome_robustness")})
        item["updated_at"] = item["updated_at"].isoformat() if item["updated_at"] else None
        item["withdrawn_at"] = item["withdrawn_at"].isoformat() if item["withdrawn_at"] else None
        rows.append(item)
    return {"records": rows}


# The answers a judge gave; hidden while a record still waits for its second
# judgement, so the two stay independent even for an admin who also judges.
ANSWER_FIELDS = ("original_choice", "suggested_doi_o", "type", "outcome", "outcome_computation",
                 "outcome_robustness", "original_prefilled", "outcome_prefilled", "note", "points")


def _unanswered(judgement: dict) -> dict:
    return {k: v for k, v in judgement.items() if k not in ANSWER_FIELDS}


def _iso(row: dict | None, *fields: str) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for field in fields:
        if out.get(field) is not None:
            out[field] = out[field].isoformat()
    return out


def norm_doi(value) -> str:
    """db_schema.sql's source_norm_doi(), in Python."""
    from shared.doi import normalize_doi_key

    return normalize_doi_key(value, drop_whitespace=True)


def _in_flora(cur, record_id: str, doi_r: str) -> list[dict]:
    """Source Records rows for the same replication, other than this record's
    own: what FLoRA already holds. Read under a savepoint, so a problem here
    costs only this list."""
    cur.execute("SAVEPOINT adjudication_in_flora")
    try:
        cur.execute(
            """
            SELECT display_id, source, type, doi_o, outcome,
                   duplicate_status = 'duplicate' AS ruled_duplicate,
                   deleted_at IS NOT NULL AS deleted
              FROM source_records
             WHERE source_norm_doi(doi_r) = source_norm_doi(%s)
               AND NOT (source = %s AND sheet_row_id = %s)
             ORDER BY deleted_at IS NOT NULL, display_id
             LIMIT 20
            """,
            (doi_r, SOURCE_KEY, str(record_id)),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT adjudication_in_flora")
        return rows
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT adjudication_in_flora")
        return []


def _record(cur, record_id: str, *, lock: bool = False) -> dict:
    cur.execute("SELECT * FROM adjudication.records WHERE record_id = %s"
                + (" FOR UPDATE" if lock else ""), (record_id,))
    record = cur.fetchone()
    if record is None:
        raise Refused(404, "No such record")
    return dict(record)


def _final(cur, record_id: str) -> dict | None:
    cur.execute("SELECT * FROM adjudication.final WHERE record_id = %s", (record_id,))
    row = cur.fetchone()
    return typed(dict(row)) if row else None


def pair_conflicts(final: dict | None, in_flora: list[dict]) -> list[dict]:
    """Live rows holding the same replication-original pair as the approved
    answer. Of the same type, the FLoRA build keeps one row per pair, and a row
    from the website (source 'validated') imposes its outcome on whichever row it
    keeps, so until one of the two is ruled a duplicate in Source Records the
    answer may not be what FLoRA shows. Of the other type (`other_type`), the
    build keeps both (it collapses rows by type|doi_o|doi_r), so FLoRA would hold
    the pair twice, once as each type."""
    if not final or not final.get("doi_o"):
        return []
    kind = answer_type(final)
    if kind not in ("replication", "reproduction"):
        return []
    return [{**r, "other_type": r["type"] != kind} for r in in_flora
            if not r["deleted"] and not r.get("ruled_duplicate")
            and norm_doi(r["doi_o"]) == norm_doi(final["doi_o"])]


def detail(cur, record_id: str) -> dict:
    """Everything an admin needs to decide: both answers, both judges by name,
    the final answer if there is one, and what FLoRA already holds."""
    record = _record(cur, record_id)
    cur.execute(
        """
        SELECT validator_handle AS handle, validator_tier AS tier, state, original_choice,
               suggested_doi_o, type, outcome, outcome_computation, outcome_robustness,
               original_prefilled, outcome_prefilled, note, points, claimed_at, submitted_at
          FROM adjudication.judgements WHERE record_id = %s
         ORDER BY submitted_at NULLS LAST, claimed_at
        """,
        (record_id,),
    )
    judgements = [_iso(dict(j), "claimed_at", "submitted_at") for j in cur.fetchall()]
    hide = record["status"] == "open"
    submitted = [_unanswered(j) if hide else typed(j)
                 for j in judgements if j["state"] == "submitted"]
    final = _final(cur, record_id)
    in_flora = _in_flora(cur, record_id, record["doi_r"])
    return {
        "record": {**public_view(record), "status": record["status"],
                   "imported_from": record["imported_from"]},
        "judgements": submitted,
        "skipped": sum(1 for j in judgements if j["state"] == "skipped"),
        "agreement": agreement(submitted),
        "final": _iso(final, "approved_at", "published_at", "withdrawn_at"),
        "publish_problem": publish_problem(cur, final) if final else None,
        "in_flora": in_flora,
        "pair_conflicts": pair_conflicts(final, in_flora),
        # What the approval form offers. "Not a replication" is its own type there.
        "replication_outcomes": [o for o in REPLICATION_CHOICES if o != NOT_A_REPLICATION],
        "computation_outcomes": list(COMPUTATION_CHOICES),
        "robustness_outcomes": list(ROBUSTNESS_CHOICES),
    }


# ---------------------------------------------------------------------------
# Approving
# ---------------------------------------------------------------------------

def check_decision(decision: dict) -> dict:
    """The decision cleaned: a replication's outcome with its quote, or a
    reproduction's two axes (from FLoRA's vocabulary, never "can't tell") with a
    quote each, or 'not_validation'. Refused(422) otherwise."""
    doi_o = _doi(decision.get("doi_o"))
    if doi_o and not _DOI.match(doi_o):
        raise Refused(422, "The original's DOI does not look like a DOI (10.xxxx/…)")
    kind, outcome, computation, robustness = check_outcome(decision, decision=True)
    kept = AXIS_QUOTE_FIELDS if kind == "reproduction" else QUOTE_FIELDS
    return {
        "doi_o": doi_o or None,
        "title_o": _text(decision.get("title_o"), "title_o"),
        "type": kind,
        "outcome": outcome,
        "outcome_computation": computation,
        "outcome_robustness": robustness,
        **{f: _text(decision.get(f), f) if f in kept else None
           for f in (*QUOTE_FIELDS, *AXIS_QUOTE_FIELDS)},
        "admin_note": _text(decision.get("admin_note"), "admin_note"),
    }


def approve(cur, record_id: str, admin: dict, decision: dict) -> dict:
    record = _record(cur, record_id, lock=True)
    if record["status"] == "open":
        raise Refused(409, "This record is still waiting for its two judgements")
    if record["status"] == "published":
        raise Refused(409, "Withdraw it from FLoRA before changing the answer")
    if admin.get("validator_id") is not None:
        cur.execute("SELECT 1 FROM adjudication.judgements WHERE record_id = %s "
                    "AND validator_id = %s AND state = 'submitted'",
                    (record_id, int(admin["validator_id"])))
        if cur.fetchone():
            raise Refused(409, "You judged this record yourself; another admin approves it")
    clean = check_decision(decision)
    cur.execute(
        """
        INSERT INTO adjudication.final
            (record_id, doi_r, title_r, doi_o, title_o, type, outcome, outcome_computation,
             outcome_robustness, outcome_quote, quote_source, computation_quote,
             computation_quote_source, robustness_quote, robustness_quote_source,
             basis, admin_note, approved_by_id, approved_by, approved_at)
        VALUES (%(record_id)s, %(doi_r)s, %(title_r)s, %(doi_o)s, %(title_o)s, %(type)s,
                %(outcome)s, %(outcome_computation)s, %(outcome_robustness)s,
                %(outcome_quote)s, %(quote_source)s, %(computation_quote)s,
                %(computation_quote_source)s, %(robustness_quote)s, %(robustness_quote_source)s,
                %(basis)s, %(admin_note)s, %(admin_id)s, %(admin)s, NOW())
        ON CONFLICT (record_id) DO UPDATE SET
            doi_o = EXCLUDED.doi_o, title_o = EXCLUDED.title_o, type = EXCLUDED.type,
            outcome = EXCLUDED.outcome, outcome_computation = EXCLUDED.outcome_computation,
            outcome_robustness = EXCLUDED.outcome_robustness,
            outcome_quote = EXCLUDED.outcome_quote, quote_source = EXCLUDED.quote_source,
            computation_quote = EXCLUDED.computation_quote,
            computation_quote_source = EXCLUDED.computation_quote_source,
            robustness_quote = EXCLUDED.robustness_quote,
            robustness_quote_source = EXCLUDED.robustness_quote_source,
            basis = EXCLUDED.basis, admin_note = EXCLUDED.admin_note,
            approved_by_id = EXCLUDED.approved_by_id, approved_by = EXCLUDED.approved_by,
            approved_at = NOW()
        """,
        {**clean, "record_id": record_id, "doi_r": record["doi_r"], "title_r": record["title_r"],
         "basis": basis_for(record, clean["doi_o"], clean["outcome"]),
         "admin_id": int(admin["id"]), "admin": admin["handle"]},
    )
    _set_status(cur, record_id, "approved")
    return detail(cur, record_id)


def undo_approval(cur, record_id: str, admin: dict) -> dict:
    """Back to 'awaiting approval', for an answer that has never been in FLoRA.
    One that has keeps its history (published and withdrawn, by whom); it can
    still be changed and published again."""
    record = _record(cur, record_id, lock=True)
    if record["status"] != "approved":
        raise Refused(409, "Only an approved answer that is not in FLoRA can be undone")
    final = _final(cur, record_id)
    if final and final.get("published_record_id"):
        raise Refused(409, f"This answer has been in FLoRA as {final['published_record_id']}; "
                           "change it instead of undoing it")
    cur.execute("DELETE FROM adjudication.final WHERE record_id = %s", (record_id,))
    _set_status(cur, record_id, "awaiting_approval")
    return detail(cur, record_id)


def _set_status(cur, record_id: str, status: str) -> None:
    cur.execute("UPDATE adjudication.records SET status = %s, updated_at = NOW() "
                "WHERE record_id = %s", (status, record_id))


# ---------------------------------------------------------------------------
# Publishing to FLoRA through Source Records
# ---------------------------------------------------------------------------

def publish_problem(cur, final: dict) -> str | None:
    """Why this answer cannot go into FLoRA, or None when it can."""
    if not final.get("doi_o"):
        return "FLoRA needs the original's DOI; this answer has none."
    kind = answer_type(final)
    if kind == "not_validation":
        return "The answer is that it does not meet FLoRA inclusion criteria, so there is nothing to add to FLoRA."
    if kind == "reproduction":
        # The build derives the flat outcome from the axes; one undetermined axis
        # makes it cannot_be_determined, which FLoRA holds for a reproduction.
        computation, robustness = answer_axes(final)
        if computation not in COMPUTATION_CHOICES or robustness not in ROBUSTNESS_CHOICES:
            return "The answer lacks one of a reproduction's two outcomes (computation and robustness)."
        if computation == robustness == UNDETERMINED:
            return ("Neither the computation nor the robustness of this reproduction could be "
                    "determined, so there is no outcome to add to FLoRA.")
        return None
    outcome = final["outcome"]
    cur.execute("SELECT canonical_value FROM outcome_alias WHERE raw_value = %s", (outcome,))
    row = cur.fetchone()
    if row is None or row["canonical_value"] not in REPLICATION_OUTCOMES:
        return (f"FLoRA's build does not accept the outcome \"{outcome}\" "
                "(it is not in outcome_alias), so publishing it would stop the export.")
    return None


def _judges(cur, record_id: str) -> str:
    cur.execute("SELECT string_agg(validator_handle, '; ' ORDER BY submitted_at) AS judges "
                "FROM adjudication.judgements WHERE record_id = %s AND state = 'submitted'",
                (record_id,))
    return cur.fetchone()["judges"] or ""


def flora_fields(final: dict) -> dict:
    """An approved answer's type, outcome and quotes under FLoRA's column names
    (transform_sources.FLORA_COLUMNS): a replication's outcome with its quote,
    or a reproduction's two axes with a quote each. `outcome` is the flat value
    (derived from the axes, for a reproduction). Shared by the Source Records row
    (source_row) and final.csv (export.final_rows)."""
    kind = answer_type(final)
    computation, robustness = answer_axes(final)
    reproduction = kind == "reproduction"

    def own(field: str):
        return final.get(field) if reproduction == (field in AXIS_QUOTE_FIELDS) else None

    return {
        "type": kind,
        "outcome": final.get("outcome"),
        "outcome_quote": own("outcome_quote"),
        "outcome_quote_source": own("quote_source"),
        "outcome_computation": computation,
        "outcome_computational_quote": own("computation_quote"),
        "out_quote_computational_source": own("computation_quote_source"),
        "outcome_robustness": robustness,
        "outcome_robustness_quote": own("robustness_quote"),
        "out_quote_robust_source": own("robustness_quote_source"),
    }


def source_row(record: dict, final: dict, judges: str) -> dict:
    """The Source Records row for an approved answer."""
    fields = flora_fields(final)
    kind = fields["type"]
    raw = {
        "adjudication_record_id": str(record["record_id"]),
        "kind": record["kind"],
        "imported_from": record["imported_from"],
        "title_r": record.get("title_r"), "title_o": final.get("title_o"),
        "type": kind, "outcome": final["outcome"],
        "outcome_computation": fields["outcome_computation"],
        "outcome_robustness": fields["outcome_robustness"],
        **{field: final.get(field) for field in (*QUOTE_FIELDS, *AXIS_QUOTE_FIELDS)},
        "basis": final["basis"], "judges": judges,
        "approved_by": final["approved_by"], "approved_at": final["approved_at"],
        "admin_note": final.get("admin_note"),
        "flora_doi_o": record.get("flora_doi_o"), "flora_outcome": record.get("flora_outcome"),
        "mo_doi_o": record.get("mo_doi_o"), "mo_outcome": record.get("mo_outcome"),
    }
    return {
        "source": SOURCE_KEY,
        "sheet_row_id": str(record["record_id"]),
        "type": kind,
        "doi_o": final["doi_o"],
        "doi_r": record["doi_r"],
        "abstract_r": record.get("abstract_r"),
        "year_r": record.get("year_r"),
        # Source Records holds a reproduction on its axes only; the build derives
        # its flat outcome (transform_sources.derive_outcome).
        "outcome": fields["outcome"] if kind == "replication" else None,
        "outcome_quote": fields["outcome_quote"],
        "out_quote_source": fields["outcome_quote_source"],
        **{column: fields[column] for column in _AXIS_COLUMNS},
        # Stringified like every other source's raw row.
        "raw": json.dumps({k: ("" if v is None else str(v)) for k, v in raw.items()}),
    }


def _next_display_id(cur) -> str:
    from shared.display_id import next_display_id
    return next_display_id(cur, SOURCE_KEY, DISPLAY_PREFIX)


_AXIS_COLUMNS = ("outcome_computation", "outcome_computational_quote",
                 "out_quote_computational_source", "outcome_robustness",
                 "outcome_robustness_quote", "out_quote_robust_source")
_SOURCE_COLUMNS = ("type", "doi_o", "doi_r", "abstract_r", "year_r", "outcome", "outcome_quote",
                   "out_quote_source", *_AXIS_COLUMNS, "raw")


def publish(cur, record_id: str, admin: dict) -> dict:
    record = _record(cur, record_id, lock=True)
    if record["status"] != "approved":
        raise Refused(409, "Only an approved answer can be published")
    final = _final(cur, record_id)
    problem = publish_problem(cur, final)
    if problem:
        raise Refused(409, problem)
    row = source_row(record, final, _judges(cur, record_id))
    cur.execute(
        "SELECT display_id, reviewed_at IS NOT NULL AS reviewed FROM source_records "
        "WHERE source = %s AND sheet_row_id = %s FOR UPDATE",
        (SOURCE_KEY, row["sheet_row_id"]),
    )
    existing = cur.fetchone()
    if existing and existing["reviewed"]:
        raise Refused(409, f"{existing['display_id']} has been reviewed in Source Records; "
                           "change it there rather than overwriting the reviewer's edit")
    if existing:
        display_id = existing["display_id"]
        cur.execute(
            f"""
            UPDATE source_records
               SET {", ".join(f"{c} = %({c})s" for c in _SOURCE_COLUMNS)},
                   deleted_at = NULL, deleted_reason = NULL,
                   version = version + 1, updated_at = NOW()
             WHERE source = %(source)s AND sheet_row_id = %(sheet_row_id)s
            """,
            row,
        )
    else:
        display_id = _next_display_id(cur)
        columns = ("source", "sheet_row_id", "display_id", *_SOURCE_COLUMNS)
        cur.execute(
            f"INSERT INTO source_records ({', '.join(columns)}) "
            f"VALUES ({', '.join(f'%({c})s' for c in columns)})",
            {**row, "display_id": display_id},
        )
    cur.execute(
        "UPDATE adjudication.final SET published_at = NOW(), published_record_id = %s, "
        "published_by = %s, withdrawn_at = NULL, withdrawn_by = NULL WHERE record_id = %s",
        (display_id, admin["handle"], record_id),
    )
    _set_status(cur, record_id, "published")
    return detail(cur, record_id)


def withdraw(cur, record_id: str, admin: dict) -> dict:
    """Take a published answer out of FLoRA: its Source Records row is marked
    deleted (never removed), and the record goes back to 'approved'."""
    record = _record(cur, record_id, lock=True)
    if record["status"] != "published":
        raise Refused(409, "Only a published answer can be withdrawn")
    cur.execute("SELECT display_id, reviewed_at IS NOT NULL AS reviewed FROM source_records "
                "WHERE source = %s AND sheet_row_id = %s FOR UPDATE", (SOURCE_KEY, str(record_id)))
    row = cur.fetchone()
    if row and row["reviewed"]:
        raise Refused(409, f"{row['display_id']} has been reviewed in Source Records; "
                           "change or remove it there")
    cur.execute(
        """
        UPDATE source_records
           SET deleted_at = NOW(), deleted_reason = %s, version = version + 1, updated_at = NOW()
         WHERE source = %s AND sheet_row_id = %s AND deleted_at IS NULL
        """,
        (WITHDRAWN_REASON.format(admin=admin["handle"]), SOURCE_KEY, str(record_id)),
    )
    cur.execute("UPDATE adjudication.final SET withdrawn_at = NOW(), withdrawn_by = %s "
                "WHERE record_id = %s", (admin["handle"], record_id))
    _set_status(cur, record_id, "approved")
    return detail(cur, record_id)

