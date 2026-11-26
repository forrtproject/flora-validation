"""Apply a reviewed batch of field corrections through the FLoRA correction log.

The batch is a CSV with one row per record and field: id, field, action, before,
value, note, evidence_url. `set` replaces the field, and refuses the record unless
it still holds `before` (blank for an empty field); a blank `value` empties it.
`append` adds `value` to an alt_identifier list unless it is already there.

Default: validate the file without a database. --check-live plans the batch against
the live data in a transaction it rolls back. --apply saves each record's changes
as one editor save (flora_service.update_record: the versioned log, the metadata
rebuild and live publication), checks the published values, and commits the whole
batch at once, or nothing. Reapplying an applied batch changes nothing.

    python3 scripts/apply_field_corrections.py data/corrections/fred_data_179_fields.csv
    python3 scripts/apply_field_corrections.py BATCH --check-live
    python3 scripts/apply_field_corrections.py BATCH --apply --admin-handle HANDLE
"""
import argparse
import csv
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

ACTIONS = {"set", "append"}
APPEND_FIELDS = {"alt_identifier_o", "alt_identifier_r"}
REQUIRED = ["id", "field", "action", "before", "value", "note", "evidence_url"]


def load_batch(path):
    """Rows of the batch, checked for shape, field names, values and notes."""
    import flora_edits
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in REQUIRED if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError("Missing CSV columns: " + ", ".join(missing))
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    editable = set(flora_edits.editable_fields("replication")) | set(
        flora_edits.editable_fields("reproduction"))
    seen = set()
    for row in rows:
        # A field is set once per record; appends add one identifier each.
        key = (row["id"], row["field"], row["value"] if row["action"] == "append" else "")
        if not row["id"] or key in seen or (row["action"] == "set" and any(
                k[:2] == key[:2] for k in seen)) or (row["id"], row["field"], "") in seen:
            raise ValueError(f"Missing id, or a field both set and appended or set twice: {key[:2]}")
        seen.add(key)
        if row["action"] not in ACTIONS:
            raise ValueError(f"Unknown action {row['action']!r} for {key}")
        if row["field"] not in editable:
            raise ValueError(f"Not an editable field: {key}")
        if row["action"] == "append" and (row["field"] not in APPEND_FIELDS or not row["value"]):
            raise ValueError(f"append adds a value to an alt_identifier list: {key}")
        if not row["note"] or not row["evidence_url"]:
            raise ValueError(f"Every correction needs a note and its evidence: {key}")
        flora_edits._checked_notes({row["field"]: row["note"]})
    return rows


def _same(field, actual, expected) -> bool:
    import transform_sources
    def text(value):
        return "" if value is None or (isinstance(value, float) and value != value) else str(value).strip()
    actual, expected = text(actual), text(expected)
    if field.startswith("doi_"):
        return (transform_sources.clean_doi(actual) or "") == (transform_sources.clean_doi(expected) or "")
    return actual == expected


def plan(cur, rows):
    """One editor save per record: its fields, notes and the log version it read.
    Refuses the whole batch if any record moved since the batch was written."""
    import flora_edits
    import flora_service
    from preprint_dedup import append_alt_identifier
    by_record = {}
    for row in rows:
        by_record.setdefault(row["id"], []).append(row)
    plans = []
    for record_id, changes in by_record.items():
        # The version first, then the record, then the version again: a save between
        # the reads would otherwise pair a newer record with an older version, and
        # the batch could overwrite that save. flora_edits.save guards it once more.
        found = flora_service.get_record(cur, record_id)     # by FLoRA or published id
        if found is None or not found.get("export_id"):
            raise flora_edits.RecordNotFound(record_id)
        export_id = found["export_id"]
        version = flora_edits.version(cur, export_id)
        record = flora_service.get_record(cur, export_id)
        if record is None or flora_edits.version(cur, export_id) != version:
            raise flora_edits.VersionConflict(flora_edits.version(cur, export_id))
        fields, notes = {}, {}
        for change in changes:
            field = change["field"]
            current = fields.get(field, record.get(field))   # earlier appends to it included
            if change["action"] == "append":
                value = append_alt_identifier(current, change["value"])
            else:
                value = change["value"] or None
                if not _same(field, current, change["before"]) and not _same(field, current, value):
                    raise ValueError(f"{record_id} {field} is {current!r}, not {change['before']!r}: "
                                     "review the record and the batch again")
            cleaned = flora_edits.clean_value(field, value, record["type"])
            if not _same(field, current, cleaned):
                fields[field] = cleaned
                notes[field] = " ".join(filter(None, [notes.get(field), change["note"]]))
                if len(notes[field]) > flora_edits.NOTE_LIMIT:
                    raise ValueError(f"{record_id} {field}: the combined notes exceed "
                                     f"{flora_edits.NOTE_LIMIT} characters; shorten them")
        if fields:
            plans.append({"record_id": record["export_id"], "fields": fields,
                          "version": version, "notes": notes})
    return plans


def verify(cur, plans):
    """Every saved value as published, or an error that rolls the batch back."""
    import flora_store
    for item in plans:
        live = flora_store.get_record(cur, item["record_id"]) or {}
        for field, value in item["fields"].items():
            if not _same(field, live.get(field), value):
                raise ValueError(f"{item['record_id']} {field} was not published as saved")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--check-live", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--admin-handle")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    rows = load_batch(args.batch)
    if not (args.check_live or args.apply):
        print(f"Validated {len(rows)} corrections to {len({r['id'] for r in rows})} records; "
              "no database opened.")
        return
    if args.apply and not args.admin_handle:
        parser.error("--apply requires --admin-handle")

    from dotenv import load_dotenv
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from correct_publication_identifiers import apply_plans, prepare_metadata
    load_dotenv(args.env_file)
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            plans = plan(cur, rows)
            if args.apply:
                prepare_metadata(cur, plans)
                apply_plans(cur, plans, {"handle": args.admin_handle})
                verify(cur, plans)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()            # an exception before commit rolls back the whole batch
    print(json.dumps({"applied": args.apply, "records": len(plans), "plans": plans},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
