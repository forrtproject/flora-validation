"""CSV exports of the adjudication: the final answers, and everything for the analysis.

final.csv       one row per approved answer, in the column order of the FLoRA
                build's input (transform_sources.FLORA_COLUMNS) and then the
                adjudication's own columns, so it can be merged into the FLoRA
                CSV as it is.
judgements.csv  one row per record: the kind, both answers, both judges by name
                with their answers, whether they agreed, and the final answer;
                what fred-data's analysis spec asks for.
"""

from __future__ import annotations

import csv
import io

from .review import SOURCE_KEY, agreement, record_type

# transform_sources.FLORA_COLUMNS; a test keeps the two in step.
FLORA_COLUMNS = (
    "doi_o", "ref_o", "url_o",
    "doi_r", "ref_r", "url_r",
    "abstract_r",
    "outcome", "outcome_quote", "outcome_quote_source",
    "type", "source",
    "alt_identifier_o", "alt_identifier_r",
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
)
FINAL_EXTRA = (
    "title_o", "title_r", "year_r", "kind", "basis", "judges", "admin_note",
    "approved_by", "approved_at", "published_record_id", "published_at", "withdrawn_at",
    "adjudication_record_id",
)
JUDGE_FIELDS = ("handle", "tier", "original_choice", "suggested_doi_o", "outcome", "note", "submitted_at")
JUDGEMENT_COLUMNS = (
    "adjudication_record_id", "kind", "status", "doi_r", "title_r", "year_r",
    "flora_doi_o", "flora_title_o", "flora_outcome", "flora_outcome_quote",
    "flora_link_method", "flora_link_confidence",
    "mo_doi_o", "mo_title_o", "mo_outcome", "mo_replication_type", "mo_discipline",
    "mo_confidence",
    *(f"judge_{n}_{field}" for n in (1, 2) for field in JUDGE_FIELDS),
    "agreement",
    "final_doi_o", "final_title_o", "final_outcome", "final_basis", "approved_by",
    "approved_at", "published_record_id",
)
EXPORTS = ("final", "judgements")
# Free text people typed. A spreadsheet runs a cell that starts with one of
# _FORMULA as a formula, so these get a leading apostrophe; the data columns
# (DOIs, titles, outcomes) are left exactly as they are, for merging.
NOTE_COLUMNS = {"admin_note", "judge_1_note", "judge_2_note"}
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(column: str, value):
    if value is None:
        return ""
    if column in NOTE_COLUMNS and str(value).startswith(_FORMULA):
        return "'" + str(value)
    return value


def _csv(columns, rows) -> str:
    out = io.StringIO()
    out.write("﻿")                       # so Excel reads UTF-8
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _cell(k, row.get(k)) for k in columns})
    return out.getvalue()


def _judges(cur) -> dict:
    cur.execute(
        """
        SELECT record_id::text AS record_id, validator_handle AS handle, validator_tier AS tier,
               original_choice, suggested_doi_o, outcome, note, submitted_at
          FROM adjudication.judgements WHERE state = 'submitted'
         ORDER BY record_id, submitted_at
        """
    )
    by_record: dict[str, list] = {}
    for row in cur.fetchall():
        by_record.setdefault(row["record_id"], []).append(dict(row))
    return by_record


def final_rows(cur) -> list[dict]:
    judges = _judges(cur)
    cur.execute(
        """
        SELECT f.*, r.record_id::text AS adjudication_record_id, r.kind, r.abstract_r, r.year_r
          FROM adjudication.final f JOIN adjudication.records r ON r.record_id = f.record_id
         ORDER BY f.approved_at, f.record_id
        """
    )
    rows = []
    for f in cur.fetchall():
        kind = record_type(f["outcome"])
        computation = robustness = None
        if kind == "reproduction":
            computation, _, robustness = f["outcome"].partition(", ")
        rows.append({
            **{k: f[k] for k in ("doi_o", "doi_r", "abstract_r", "outcome", "title_o", "title_r",
                                 "year_r", "kind", "basis", "admin_note", "approved_by",
                                 "published_record_id", "adjudication_record_id")},
            "outcome_quote": f["outcome_quote"] if kind == "replication" else None,
            "outcome_quote_source": f["quote_source"] if kind == "replication" else None,
            "type": kind,
            "source": SOURCE_KEY,
            "outcome_computation": computation,
            "outcome_robustness": robustness,
            "judges": "; ".join(j["handle"] for j in judges.get(f["adjudication_record_id"], [])),
            "approved_at": f["approved_at"].isoformat() if f["approved_at"] else None,
            "published_at": f["published_at"].isoformat() if f["published_at"] else None,
            "withdrawn_at": f["withdrawn_at"].isoformat() if f["withdrawn_at"] else None,
        })
    return rows


def judgement_rows(cur) -> list[dict]:
    judges = _judges(cur)
    cur.execute(
        """
        SELECT r.*, r.record_id::text AS adjudication_record_id,
               f.doi_o AS final_doi_o, f.title_o AS final_title_o, f.outcome AS final_outcome,
               f.basis AS final_basis, f.approved_by, f.approved_at, f.published_record_id
          FROM adjudication.records r LEFT JOIN adjudication.final f ON f.record_id = r.record_id
         ORDER BY r.kind, r.doi_r, r.record_id
        """
    )
    rows = []
    for r in cur.fetchall():
        row = dict(r)
        mine = judges.get(row["adjudication_record_id"], [])
        if row["status"] == "open":
            # The first answer stays hidden until the second is in.
            mine = [{**j, "original_choice": None, "suggested_doi_o": None,
                     "outcome": None, "note": None} for j in mine]
        for n, judge in enumerate(mine[:2], start=1):
            for field in JUDGE_FIELDS:
                value = judge[field]
                row[f"judge_{n}_{field}"] = value.isoformat() if hasattr(value, "isoformat") else value
        row["agreement"] = agreement(mine)
        row["approved_at"] = row["approved_at"].isoformat() if row["approved_at"] else None
        rows.append(row)
    return rows


def export_csv(cur, name: str) -> str:
    if name == "final":
        return _csv((*FLORA_COLUMNS, *FINAL_EXTRA), final_rows(cur))
    if name == "judgements":
        return _csv(JUDGEMENT_COLUMNS, judgement_rows(cur))
    raise KeyError(name)
