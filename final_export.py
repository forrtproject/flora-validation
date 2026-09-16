"""The publication contract: reference order, persistent IDs and Excel CSV.

The supplied snapshot defines initial positions, never an instruction to restore
excluded records. Current records are matched to it; genuinely new records are
appended. The registry pins matches so later DOI edits cannot move or rename them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "data" / "flora_reference.csv"
IDENTITY_COLUMNS = ["id", "id_md5"]


def text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    value = str(value).strip()
    return "" if value in {"NA", "N/A", "NULL", "None"} else value


def doi(value) -> str:
    value = text(value).lower()
    value = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*", "", value)
    return value


def url(value) -> str:
    value = text(value).lower()
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", value).rstrip("/")


def key(row) -> tuple:
    original = doi(row.get("doi_o")) or url(row.get("url_o"))
    if not original:
        original = text(row.get("title_o")).casefold()
    replication = doi(row.get("doi_r"))
    report = url(row.get("url_r"))
    if report in {"doi.org/" + replication, "dx.doi.org/" + replication}:
        report = ""
    if not replication and not report:
        report = text(row.get("title_r")).casefold()
    return text(row.get("type")).lower(), original, replication, report


@lru_cache(maxsize=1)
def reference_index() -> tuple[dict, dict, int]:
    with REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    exact, pairs = {}, {}
    for position, row in enumerate(rows, 1):
        identity = (f"FLORA-{position:06d}", position)
        row_key = key(row)
        if row_key in exact:
            raise ValueError("Reference snapshot contains ambiguous normalized record keys")
        exact[row_key] = identity
        # Missing/redundant report URLs can match only an unambiguous DOI pair.
        if row_key[1] and row_key[2]:
            pairs.setdefault(row_key[:3], []).append((row_key[3], identity))
    return exact, pairs, len(rows)


def reference_match(row):
    exact, pairs, _ = reference_index()
    row_key = key(row)
    if row_key in exact:
        return exact[row_key]
    candidates = pairs.get(row_key[:3], [])
    if len(candidates) == 1:
        report, identity = candidates[0]
        if not report or not row_key[3]:
            return identity
    return None


def with_identity(frame: pd.DataFrame) -> pd.DataFrame:
    """Read-only projection. Only the registry may allocate new positions."""
    out = frame.copy()
    ids, positions = [], []
    for row in out.to_dict("records"):
        published_id = text(row.get("export_id")) or text(row.get("id"))
        position = row.get("export_position")
        match = reference_match(row) if not published_id else None
        if match:
            published_id, position = match
        if not published_id:
            published_id = text(row.get("flora_id")) or text(row.get("source_record_id"))
        if not published_id:
            # Pure projections without a registry still have deterministic IDs.
            # Production preparation pins provenance before reaching this path.
            payload = json.dumps(key(row), ensure_ascii=False, separators=(",", ":"))
            published_id = "PAIR-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        ids.append(published_id)
        positions.append(float(position) if text(position) else float("inf"))
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate publication IDs: refresh the registry before exporting")
    out["id"] = ids
    out["id_md5"] = [hashlib.md5(value.encode("utf-8")).hexdigest() for value in ids]
    out["_publication_position"] = positions
    # Registry positions dominate. For unregistered previews ties keep source order.
    out = out.sort_values("_publication_position", kind="stable")
    return out.drop(columns="_publication_position")


def register_order(cur, frame: pd.DataFrame) -> None:
    """Pin reference identities and append newcomers under the registry transaction."""
    cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
    cur.execute("SELECT primary_source_record_id::text AS sid, flora_id, export_id, "
                "export_position FROM flora_records ORDER BY first_seen_at, flora_id")
    records = [dict(row) for row in cur.fetchall()]
    # A full snapshot may have been seeded before its source registry existed.
    # Its IDs and positions are already published and cannot be recycled.
    cur.execute("SELECT id, export_position FROM flora_data")
    published = {row["id"]: int(row["export_position"]) for row in cur.fetchall()}
    published_positions = {position: identifier for identifier, position in published.items()}
    by_source = {text(row.get("source_record_id")): row for row in frame.to_dict("records")}
    taken_ids = {row["export_id"] for row in records if row.get("export_id")}
    taken_positions = {int(row["export_position"]) for row in records if row.get("export_position")}
    reserved_ids = {identity[0] for identity in reference_index()[0].values()}
    next_position = max([reference_index()[2], *taken_positions, *published_positions])
    updates = []
    for record in records:
        if record.get("export_id"):
            continue
        row = by_source.get(record["sid"])
        if row is None:
            continue
        match = reference_match(row)
        if (match and match[0] not in taken_ids and match[1] not in taken_positions
                and published_positions.get(match[1], match[0]) == match[0]
                and published.get(match[0], match[1]) == match[1]):
            export_id, position = match
        else:
            next_position += 1
            export_id, position = record["flora_id"], next_position
            if export_id in taken_ids or export_id in reserved_ids or export_id in published:
                export_id = "NEW-" + record["sid"]
            if export_id in taken_ids or export_id in published:
                raise ValueError(f"Publication ID is already reserved: {export_id}")
        taken_ids.add(export_id)
        taken_positions.add(position)
        updates.append((export_id, position, record["flora_id"]))
    if updates:
        from psycopg2.extras import execute_batch
        execute_batch(cur, "UPDATE flora_records SET export_id = %s, export_position = %s "
                          "WHERE flora_id = %s AND export_id IS NULL", updates)


def csv_text(frame: pd.DataFrame, *, bom: bool = True) -> str:
    """One serializer for website downloads and pipeline artifacts.

    All cells are quoted, missing values use R's NA spelling, embedded newlines
    are preserved, and an Excel-compatible UTF-8 BOM precedes the header.
    """
    buffer = io.StringIO(newline="")
    frame.to_csv(buffer, index=False, lineterminator="\n", na_rep="NA", quoting=csv.QUOTE_ALL)
    return ("\ufeff" if bom else "") + buffer.getvalue()


def replay_reference(output: Path) -> dict:
    """Add ID columns to the supplied snapshot without changing any original cell."""
    frame = pd.read_csv(REFERENCE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    original_columns = list(frame.columns)
    frame = with_identity(frame)
    frame = frame[IDENTITY_COLUMNS + original_columns]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(csv_text(frame), encoding="utf-8", newline="")
    return {"mode": "reference_snapshot", "rows": len(frame), "columns": len(frame.columns),
            "reference_sha256": hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "original_cells_preserved": True, "original_row_order_preserved": True,
            "live_sources_fetched": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reproduce the supplied CSV with id and id_md5 first")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "flora_reference_with_ids.csv")
    args = parser.parse_args()
    report = replay_reference(args.output)
    args.output.with_suffix(".report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
