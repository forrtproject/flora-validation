"""Apply a Sheet edits CSV that a coding agent filled in (docs/SHEET_EDITS_FOR_AGENTS.md).

Dry run by default. Use --apply --admin-handle HANDLE to commit every decided row in
one transaction: one flora_service.update_record per FLoRA record (the FLoRA editor's
save, with its log, notes and publication) and sheet_drift_service.keep() for each
keep_flora finding. The whole batch is checked first, against the live data: a row
whose value_in_sheet, flora_value or flora_version changed since the CSV was
downloaded is refused, and nothing is written. Requires DATABASE_URL; no sheet and no
source record is changed.
"""
import argparse
import csv
import json
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

import flora_edits
import flora_service
import security_events
import sheet_drift_service

DECISIONS = ("use_sheet", "keep_flora", "set_value", "skip")
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _text(value):
    return flora_edits._text(value)


def _note(value) -> str:
    """A note as typed: the export defuses a note that starts like a formula with a
    leading apostrophe, and an agent may have copied that."""
    text = (value or "").strip()
    return text[1:] if text.startswith("'") and text[1:].startswith(_FORMULA) else text


def _finding_record(cur, finding_id: int) -> "str | None":
    cur.execute("SELECT record_id::text AS record_id FROM sheet_drift_findings WHERE finding_id = %s",
                (finding_id,))
    row = cur.fetchone()
    return row["record_id"] if row else None


def _live(cur, finding_id: int, views: dict) -> dict:
    """The finding as the Sheet edits view shows it now, or ValueError."""
    record_id = _finding_record(cur, finding_id)
    if record_id is None:
        raise ValueError(f"finding {finding_id} does not exist")
    if record_id not in views:
        views[record_id] = sheet_drift_service.record_view(cur, record_id)
    view = views[record_id] or {}
    field = next((f for f in view.get("fields", []) if f["finding_id"] == finding_id), None)
    if field is None:
        raise ValueError(f"finding {finding_id} is resolved: the sheet has the imported value again")
    return field


def plan(cur, rows) -> dict:
    """Check every decided row against the live data before anything is written.
    Returns {"edits": {flora_id: {fields, notes, version}}, "keeps": {finding_id:
    {note, sheet_value}}}; raises ValueError naming the line of the first bad row."""
    if rows:
        missing = set(sheet_drift_service.CSV_COLUMNS) - set(rows[0])
        if missing:
            raise ValueError("Not a Sheet edits CSV: missing " + ", ".join(sorted(missing)))
    views, edits, keeps, changed = {}, {}, {}, set()
    for line, row in enumerate(rows, start=2):           # line 1 is the header
        decision = (row.get("decision") or "").strip().lower() or "skip"
        if decision not in DECISIONS:
            raise ValueError(f"line {line}: decision must be one of {', '.join(DECISIONS)}")
        if decision == "skip":
            continue
        note = _note(row.get("decision_note"))
        if len(note) < 3 or len(note) > flora_edits.NOTE_LIMIT:
            raise ValueError(f"line {line}: decision_note must be 3 to {flora_edits.NOTE_LIMIT} characters")
        try:
            finding_id = int(row["finding_id"])
        except (TypeError, ValueError):
            raise ValueError(f"line {line}: finding_id is not a number")
        field = _live(cur, finding_id, views)
        if _text(field["value_in_sheet"]) != _text(row.get("value_in_sheet")):
            raise ValueError(f"line {line}: the sheet changed since the download; download it again")
        flora_id = (row.get("flora_id") or "").strip()
        target = next((t for t in field["targets"] if t["flora_id"] == flora_id), None)
        # A keep is judged against FLoRA's value as much as a change is: one made on a
        # value someone has corrected since would keep what nobody looked at.
        if target is not None and (
                _text(target["flora_value"]) != _text(row.get("flora_value"))
                or str(target["version"]) != str(row.get("flora_version") or "").strip()):
            raise ValueError(f"line {line}: {flora_id} changed since the download; download it again")
        if decision == "keep_flora":
            if flora_id and target is None:
                raise ValueError(f"line {line}: finding {finding_id} no longer applies to {flora_id}; "
                                 "download it again")
            if field["state"] not in sheet_drift_service.NEEDS_REVIEW:
                raise ValueError(f"line {line}: nothing to decide ({field['state']})")
            if finding_id in changed:
                raise ValueError(f"line {line}: finding {finding_id} is both kept and changed")
            keeps[finding_id] = {"note": note, "sheet_value": field["value_in_sheet"]}
            continue

        if target is None or not field.get("flora_field"):
            raise ValueError(f"line {line}: finding {finding_id} has no FLoRA field on {flora_id or 'any record'}")
        if finding_id in keeps:
            raise ValueError(f"line {line}: finding {finding_id} is both kept and changed")
        if decision == "use_sheet":
            if target["sheet_value_error"]:
                raise ValueError(f"line {line}: {target['sheet_value_error']}")
            value = target["sheet_value_for_flora"]
        else:
            value = _text(row.get("new_flora_value"))
        record = flora_service.get_record(cur, flora_id)
        name = field["flora_field"]
        try:
            value = flora_edits.clean_value(name, value, (record or {}).get("type"))
        except ValueError as exc:
            raise ValueError(f"line {line}: {exc}")
        edit = edits.setdefault(flora_id, {"fields": {}, "notes": {}, "version": target["version"]})
        if name in edit["fields"] and edit["fields"][name] != value:
            raise ValueError(f"line {line}: two values for {name} on {flora_id}")
        edit["fields"][name] = value
        edit["notes"][name] = note
        changed.add(finding_id)
    return {"edits": edits, "keeps": keeps}


def _audit_keep(cur, admin: dict, kept: dict) -> None:
    """The admin audit log entry the panel's "Keep FLoRA value" writes (app.py,
    _drift_audit), so a keep made here is found where every other one is."""
    column = kept.get("sheet_column")
    security_events.record(
        cur, security_events.SOURCE_DRIFT_KEPT, actor_kind="admin", actor_id=admin.get("id"),
        actor_handle=admin.get("handle"), target_kind="sheet_edit", target_id=kept.get("finding_id"),
        target_label=f"{kept.get('source')} {kept.get('sheet_row_id')} {column or '(row)'}",
        user_agent="apply_sheet_edits.py",
        detail={"note": kept.get("kept_note"), "source": kept.get("source"),
                "sheet_row_id": kept.get("sheet_row_id"), "sheet_column": column,
                "sheet_value": (kept.get("kept_sheet_value") or "")[:500] or None})


def apply_plan(cur, planned: dict, admin: dict) -> list:
    """Keeps first, while their states are the ones checked; then one editor save per
    record. Each save is verified on the record as the FLoRA tab shows it."""
    results = []
    for finding_id, keep in planned["keeps"].items():
        kept = sheet_drift_service.keep(cur, finding_id, admin, keep["note"], keep["sheet_value"])
        _audit_keep(cur, admin, kept)
        results.append({"kept": kept})
    for flora_id, edit in planned["edits"].items():
        result = flora_service.update_record(cur, flora_id, edit["fields"], [], edit["version"],
                                             admin, edit["notes"])
        record = flora_service.get_record(cur, flora_id) or {}
        if any(_text(record.get(name)) != _text(value) for name, value in edit["fields"].items()):
            raise ValueError(f"{flora_id} does not show the corrected values; rolling back everything")
        results.append({"flora_id": flora_id, "change_id": result.get("change_id"),
                        "changed_fields": result.get("changed_fields")})
    return results


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
    parser.add_argument("--admin-handle", help="whose name the changes are logged under (required with --apply)")
    args = parser.parse_args()
    if args.apply and not args.admin_handle:
        parser.error("--apply needs --admin-handle")
    with args.csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            planned = plan(cur, rows)
            result = (apply_plan(cur, planned, {"id": None, "handle": args.admin_handle})
                      if args.apply else planned)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        print(json.dumps({"applied": args.apply, "corrections": len(planned["edits"]),
                          "kept": len(planned["keeps"]), "results": result},
                         indent=2, default=str, ensure_ascii=False))
    finally:
        conn.close()   # any exception rolls back the whole batch


if __name__ == "__main__":
    main()
