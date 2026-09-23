"""Complete prepared FLoRA records with permanent IDs, ready for future APIs.

The source review table and preparation preview stay separate. This table changes
only when an entire prepared snapshot is stored successfully. No HTTP API or
public access policy is defined here.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re

import pandas as pd
import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values

import final_export
from transform_sources import FLORA_OUTPUT_COLUMNS

DATA_COLUMNS = list(FLORA_OUTPUT_COLUMNS)
CSV_COLUMNS = ["id", "id_md5", *DATA_COLUMNS]
RESERVED = {"export_position", "extra_fields", "record_version", "created_at",
            "updated_at", "retired_at", "_meta"}
# Match the indexed expression in db_schema.sql while keeping the original
# DOI cell unchanged for exact CSV export.
DOI_PREFIX = r"^(https?://)?(dx\.)?doi\.org/|^doi:\s*"
UNCHECKED_REVISION = object()


def dataset_revision(cur):
    """Token for optimistic publication checks, including an uninitialized table."""
    cur.execute("SELECT snapshot_sha256, updated_at FROM flora_data_metadata WHERE singleton")
    row = cur.fetchone()
    return (row["snapshot_sha256"], row["updated_at"]) if row else None


def _value(value):
    if value is None or pd.isna(value):
        return None
    value = str(value)
    return None if value == "NA" else value


def _snapshot(source):
    if isinstance(source, pd.DataFrame):
        frame = source.copy()
        payload = final_export.csv_text(frame).encode("utf-8")
    else:
        payload = Path(source).read_bytes()
        # Check raw headers before pandas can silently rename duplicate columns.
        header = next(csv.reader(io.StringIO(payload.decode("utf-8-sig"))), [])
        if len(header) != len(set(header)):
            raise ValueError("Duplicate CSV column names are not allowed")
        frame = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False,
                            encoding="utf-8-sig")
    columns = list(frame.columns)
    if columns[:len(CSV_COLUMNS)] != CSV_COLUMNS:
        raise ValueError("Expected id, id_md5, then the 35 original FLoRA columns in order")
    if len(columns) != len(set(columns)) or any(not isinstance(c, str) for c in columns):
        raise ValueError("CSV column names must be unique strings")
    if RESERVED.intersection(columns):
        raise ValueError("CSV contains reserved database metadata column names")
    records, seen = [], set()
    for raw in frame.to_dict("records"):
        row = {column: _value(raw.get(column)) for column in columns}
        identifier = row["id"]
        if not identifier or identifier != identifier.strip() or identifier in seen:
            raise ValueError("Each record needs a unique nonblank permanent id without surrounding whitespace")
        if row["id_md5"] != hashlib.md5(identifier.encode("utf-8")).hexdigest():
            raise ValueError(f"Invalid id_md5 for {identifier}")
        seen.add(identifier)
        records.append(row)
    return columns, records, hashlib.sha256(payload).hexdigest()


def materialize(cur, source, *, allow_empty=False, expected_revision=UNCHECKED_REVISION):
    """Upsert an entire snapshot; the caller owns commit/rollback.

    IDs must already exist in the input. Edits update values in place, absent IDs
    retire, and returning IDs reactivate their previous rows. No ID is minted,
    renumbered, deleted, or derived from titles/DOIs here.

    Pipeline callers pass the revision captured before their build. Check it
    under the write lock so a slower run cannot overwrite a newer publication.
    Explicit standalone imports may omit the check to intentionally replace data.
    """
    columns, incoming, digest = _snapshot(source)
    if not incoming and not allow_empty:
        raise ValueError("Refusing to replace the prepared table with an empty snapshot")
    # Use the same allocation lock/order as the identity registry. Records
    # omitted for a missing title still reserve their publication positions.
    cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
    cur.execute("LOCK TABLE flora_data IN SHARE ROW EXCLUSIVE MODE")
    if expected_revision is not UNCHECKED_REVISION and dataset_revision(cur) != expected_revision:
        raise ValueError("The prepared dataset changed during this run; rerun the pipeline to build a current snapshot.")
    cur.execute("SELECT export_id, export_position FROM flora_records WHERE export_id IS NOT NULL")
    registered = {row["export_id"]: row["export_position"] for row in cur.fetchall()}
    cur.execute("SELECT * FROM flora_data ORDER BY export_position")
    existing = {row["id"]: dict(row) for row in cur.fetchall()}
    cur.execute("SELECT columns FROM flora_data_metadata WHERE singleton")
    metadata = cur.fetchone()
    old_columns = list(metadata["columns"]) if metadata else CSV_COLUMNS
    extra_columns = list(dict.fromkeys([*old_columns[len(CSV_COLUMNS):], *columns[len(CSV_COLUMNS):]]))
    output_columns = CSV_COLUMNS + extra_columns
    reference = {identity[0]: identity[1] for identity in final_export.reference_index()[0].values()}
    positions = {position: identifier for identifier, position in registered.items()}
    for identifier, prior in existing.items():
        position = prior["export_position"]
        if identifier in registered and registered[identifier] != position:
            raise ValueError(f"Stored position disagrees with the registry for {identifier}")
        if position in positions and positions[position] != identifier:
            raise ValueError(f"Publication position {position} is claimed by different IDs")
        positions[position] = identifier
    last_position = max([len(reference), *positions])
    stats = {"rows": len(incoming), "inserted": 0, "updated": 0,
             "unchanged": 0, "retired": 0, "reactivated": 0,
             "table": "flora_data", "snapshot_sha256": digest}
    values = []
    for row in incoming:
        identifier = row["id"]
        prior = existing.get(identifier)
        extras = {column: row.get(column) for column in extra_columns}
        if prior:
            position = prior["export_position"]
            changed = (any(row[column] != prior[column] for column in DATA_COLUMNS)
                       or extras != (prior["extra_fields"] or {}))
            if prior["retired_at"] is not None:
                stats["reactivated"] += 1
            elif not changed:
                stats["unchanged"] += 1
                continue
            else:
                stats["updated"] += 1
        else:
            position = registered.get(identifier, reference.get(identifier))
            if position is not None and position in positions and positions[position] != identifier:
                raise ValueError(f"Publication position {position} is already assigned")
            if position is None:
                last_position += 1
                position = last_position
            positions[position] = identifier
            stats["inserted"] += 1
        values.append((identifier, *(row[column] for column in DATA_COLUMNS), position, Json(extras)))
    if values:
        # Column names below are the checked-in schema contract, never input keys.
        names = ["id", *DATA_COLUMNS, "export_position", "extra_fields"]
        updates = ", ".join(f"{name}=EXCLUDED.{name}" for name in [*DATA_COLUMNS, "extra_fields"])
        execute_values(cur, f"""INSERT INTO flora_data ({', '.join(names)}) VALUES %s
            ON CONFLICT (id) DO UPDATE SET {updates}, retired_at=NULL,
                updated_at=NOW(), record_version=flora_data.record_version + 1""", values)
    incoming_ids = [row["id"] for row in incoming]
    cur.execute("""UPDATE flora_data SET retired_at=NOW(), updated_at=NOW(),
                       record_version=record_version + 1
                   WHERE retired_at IS NULL AND NOT (id = ANY(%s::text[]))""", (incoming_ids,))
    stats["retired"] = cur.rowcount
    cur.execute("""INSERT INTO flora_data_metadata (singleton, columns, snapshot_sha256)
                   VALUES (TRUE, %s, %s) ON CONFLICT (singleton) DO UPDATE
                   SET columns=EXCLUDED.columns, snapshot_sha256=EXCLUDED.snapshot_sha256,
                       updated_at=NOW()""", (Json(output_columns), digest))
    return stats


def _record(row):
    if row is None:
        return None
    result = {column: row[column] for column in CSV_COLUMNS}
    result.update(row.get("extra_fields") or {})
    result["_meta"] = {name: row[name] for name in
                       ("export_position", "record_version", "created_at", "updated_at", "retired_at")}
    result["_meta"]["active"] = row["retired_at"] is None
    return result


def get_record(cur, identifier):
    """Resolve active or retired IDs; retirement never turns an old ID into a new work."""
    cur.execute("SELECT * FROM flora_data WHERE id=%s", (identifier,))
    return _record(cur.fetchone())


def get_record_by_hash(cur, id_md5):
    """Lookup by the full MD5 of the ID. The database enforces uniqueness."""
    if not isinstance(id_md5, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", id_md5):
        raise ValueError("Expected a complete 32-character MD5 hash")
    cur.execute("SELECT * FROM flora_data WHERE id_md5=%s", (id_md5.lower(),))
    return _record(cur.fetchone())


def list_records(cur, *, limit=100, offset=0, include_retired=False, title=None, doi=None):
    """Indexed lookup building blocks; HTTP routes/authentication can be designed later."""
    if not isinstance(limit, int) or not 1 <= limit <= 500 or not isinstance(offset, int) or offset < 0:
        raise ValueError("limit must be 1–500 and offset must be nonnegative")
    clauses, params = [], []
    if not include_retired:
        clauses.append("retired_at IS NULL")
    if title:
        clauses.append("to_tsvector('simple', coalesce(title_o, '') || ' ' || coalesce(title_r, '')) "
                       "@@ websearch_to_tsquery('simple', %s)")
        params.append(title)
    if doi:
        normalized = final_export.doi(doi)
        clauses.append("(regexp_replace(lower(btrim(doi_o)), %s, '')=%s "
                       "OR regexp_replace(lower(btrim(doi_r)), %s, '')=%s)")
        params.extend([DOI_PREFIX, normalized, DOI_PREFIX, normalized])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    cur.execute("SELECT COUNT(*) AS total FROM flora_data" + where, params)
    total = cur.fetchone()["total"]
    cur.execute("SELECT * FROM flora_data" + where + " ORDER BY export_position LIMIT %s OFFSET %s",
                [*params, limit, offset])
    return {"records": [_record(row) for row in cur.fetchall()], "total": total,
            "limit": limit, "offset": offset}


def export_csv(cur, *, include_retired=False):
    cur.execute("SELECT columns FROM flora_data_metadata WHERE singleton")
    metadata = cur.fetchone()
    columns = list(metadata["columns"]) if metadata else CSV_COLUMNS
    where = "" if include_retired else " WHERE retired_at IS NULL"
    cur.execute("SELECT * FROM flora_data" + where + " ORDER BY export_position")
    frame = pd.DataFrame([_record(row) for row in cur.fetchall()]).reindex(columns=columns)
    return final_export.csv_text(frame)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Complete prepared CSV with permanent id/id_md5")
    parser.add_argument("--init-schema", action="store_true", help="Apply db_schema.sql before the initial import")
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be configured")
    connection = psycopg2.connect(database_url)
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cur:
                if args.init_schema:
                    cur.execute((final_export.ROOT / "db_schema.sql").read_text(encoding="utf-8"))
                stats = materialize(cur, args.input)
        print(json.dumps(stats, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
