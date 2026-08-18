"""
sync_sources.py — Import accepted rows from the FLoRA entry sheets into source_records.

Reads sources.yml for where the sheets are and how their columns map. For each
enabled source the payload passes six integrity gates before anything touches the
database, then accepted rows are inserted.

INSERT-ONLY. A row is written the first time it is seen as accepted and is never
updated or deleted here. Later edits in the sheet are ignored — the database is
authoritative once a row lands. This is why the gates matter: under insert-only a
bad parse does not delete data, it inserts garbage, and garbage is permanent.

Identity is the static UUID the sheet carries in its `id` column, keyed as
(source, sheet_row_id). A row with a blank id is skipped and reported — never
given a fallback, because inventing one reintroduces the duplicate problem the
sheet UUID exists to solve.

Usage:
    python sync_sources.py
    python sync_sources.py --dry-run
    python sync_sources.py --source replications

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
import psycopg2
import psycopg2.errors
import yaml
from dotenv import load_dotenv
from console_encoding import use_utf8_output
from extractor_vocab import normalize_axis_value
from psycopg2.extras import Json

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

ROOT = Path(__file__).parent
REGISTRY_PATH = ROOT / "sources.yml"

# Raw payloads are kept per run so a bad sync can be reconstructed. Uploaded as a
# workflow artifact rather than committed — several MB a night would bloat git,
# and the hash and row count in source_sync_runs outlive the artifact retention.
SNAPSHOT_DIR = ROOT / "snapshots"

FETCH_TIMEOUT = 30
FETCH_RETRIES = 3

# Every data column on source_records, in insert order. Fixed rather than derived
# from the registry so a typo in sources.yml fails loudly instead of silently
# dropping a column.
DATA_COLUMNS = [
    "ref_o", "doi_o", "url_o", "ref_r", "doi_r", "url_r", "abstract_r",
    "outcome", "outcome_quote", "out_quote_source", "year_r",
    "alt_identifier_o", "alt_identifier_r",
    "study_o",
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
    "validation_status",
]

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


class GateFailure(Exception):
    """A source failed an integrity gate. Its existing rows are left untouched."""


# ── helpers ───────────────────────────────────────────────────────────────────

def _s(val) -> str:
    """Coerce to stripped string; treat NaN/None as empty string."""
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return str(val).strip()


def _or_none(val):
    """Empty string -> None, so 'absent' is NULL rather than '' in the database."""
    s = _s(val)
    return s if s else None


def _norm_doi(value: str) -> str:
    """Normalise a DOI for fingerprinting only. The stored value stays as the sheet
    has it — cleaning belongs in the downstream transform, where a reviewer can
    still see (and fix) the real problem."""
    v = _s(value).lower()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v)
    v = re.sub(r"^doi:\s*", "", v)
    v = re.sub(r"\s.*$", "", v)
    return v


def _norm_url(value: str) -> str:
    v = _s(value).lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    return v.rstrip("/")


def _fingerprint(doi_o: str, doi_r: str, url_r: str) -> "str | None":
    """Duplicate DETECTOR, not an identity key. Flags two different sheet UUIDs
    describing the same paper — a coder re-entering a study rather than copying it."""
    right = _norm_doi(doi_r) or _norm_url(url_r)
    left = _norm_doi(doi_o)
    if not right and not left:
        return None
    return hashlib.md5(f"{left}|{right}".encode("utf-8")).hexdigest()


# ── gates 1-6 ─────────────────────────────────────────────────────────────────

def _fetch(url: str) -> bytes:
    """Gate 1. Retry transient failures; a persistent one fails the source."""
    last = None
    for attempt in range(FETCH_RETRIES):
        try:
            req = Request(url, headers={"User-Agent": "flora-sync/1.0"})
            with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                return resp.read()
        except Exception as e:
            # Deliberately broad: resp.read() raises http.client.IncompleteRead or
            # ConnectionResetError on a truncated response, and neither is a
            # URLError. Letting those escape aborts the whole run with no
            # source_sync_runs row, so the freshness banner still reports healthy.
            last = e
            if attempt < FETCH_RETRIES - 1:
                time.sleep(2 ** attempt)
    raise GateFailure(f"fetch failed after {FETCH_RETRIES} attempts: {last}")


def _assert_is_csv(payload: bytes) -> None:
    """Gate 2. An unshared Google Sheet returns HTTP 200 with an HTML sign-in page.
    The request 'succeeds' and pandas turns the HTML into a one-column frame, so
    without this check everything downstream behaves normally on garbage."""
    head = payload[:2048].lstrip().lower()
    if head.startswith(b"<") or b"<html" in head or b"<!doctype html" in head:
        raise GateFailure("response was HTML, not CSV (sheet unshared or moved?)")
    if not payload.strip():
        raise GateFailure("response was empty")


def _parse(payload: bytes) -> pd.DataFrame:
    """Gate 3."""
    try:
        return pd.read_csv(io.BytesIO(payload), dtype=str, encoding="utf-8-sig")
    except Exception as e:
        raise GateFailure(f"could not parse as CSV: {e}")


def _assert_columns(df: pd.DataFrame, expected: list) -> None:
    """Gate 4. A renamed column upstream fails here rather than silently producing
    a permanently NULL column that nobody notices for months."""
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise GateFailure(f"missing expected column(s): {', '.join(missing)}")


def _assert_row_count(n: int, last_n: "int | None", floor: float) -> None:
    """Gate 5. Sheets grow and shrink by tens of rows; they do not halve overnight."""
    if last_n and n < last_n * floor:
        raise GateFailure(
            f"row count {n} is below {floor:.0%} of last successful run ({last_n})"
        )


# ── row building ──────────────────────────────────────────────────────────────

def _validate_registry(cfg: dict) -> None:
    """A promoted column that does not exist would insert as a permanent NULL —
    and under insert-only, fixing the typo later does not backfill those rows.
    So fail before touching the database rather than after."""
    unknown = [c for c in cfg["promoted"] if c not in DATA_COLUMNS]
    if unknown:
        raise GateFailure(
            f"sources.yml lists promoted column(s) that source_records does not "
            f"have: {', '.join(unknown)}"
        )


def _build_row(raw_row: dict, cfg: dict, source_key: str) -> dict:
    """Map one sheet row onto source_records columns.

    The complete original row goes into `raw` verbatim, keys exactly as the header
    reads, so anything not promoted is still recoverable without a re-download.
    """
    column_map = cfg.get("column_map") or {}
    promoted = set(cfg["promoted"])

    # Apply the registry's renames, then keep only promoted columns.
    mapped = {column_map.get(k, k): v for k, v in raw_row.items()}

    row = {col: None for col in DATA_COLUMNS}
    for col in DATA_COLUMNS:
        if col in promoted:
            row[col] = _or_none(mapped.get(col))

    row["source"] = source_key
    row["sheet_row_id"] = _s(raw_row.get(cfg["id_column"]))
    row["type"] = cfg["type_label"]
    if row["type"] == "reproduction":
        for axis in ("outcome_computation", "outcome_robustness"):
            row[axis] = normalize_axis_value(axis, row.get(axis))
    else:
        # Replications use the single outcome vocabulary and must not retain stale
        # reproduction fields copied into the sheet by accident.
        row["outcome_computation"] = None
        row["outcome_robustness"] = None
    row["raw"] = Json({k: _s(v) for k, v in raw_row.items()})
    # content_fingerprint is computed by a database trigger, not here: a reviewer
    # editing a DOI must move the fingerprint with it, and only the trigger sees
    # those edits. Kept out of the INSERT column list for the same reason.
    return row


def _next_display_id(cur, source_key: str, prefix: str) -> str:
    """Sequential per source, assigned once, never reused. A raw UUID is unreadable
    aloud or in Slack; REPL-000847 is not."""
    cur.execute(
        """
        INSERT INTO source_display_counters (source, last_value) VALUES (%s, 1)
        ON CONFLICT (source) DO UPDATE SET last_value = source_display_counters.last_value + 1
        RETURNING last_value
        """,
        (source_key,),
    )
    return f"{prefix}-{cur.fetchone()[0]:06d}"


def _insert(cur, row: dict, display_id: str) -> bool:
    """Insert-only. ON CONFLICT DO NOTHING is the whole merge: a row already keyed
    by (source, sheet_row_id) is left exactly as it is, edits included."""
    cols = ["source", "sheet_row_id", "display_id", "type", *DATA_COLUMNS, "raw"]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    cur.execute(
        f"""
        INSERT INTO source_records ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (source, sheet_row_id) DO NOTHING
        """,
        {**row, "display_id": display_id},
    )
    return cur.rowcount > 0


# ── run-summary bookkeeping ───────────────────────────────────────────────────

def _last_successful_run(cur, source_key: str) -> "dict | None":
    cur.execute(
        """
        SELECT rows_fetched, payload_sha256 FROM source_sync_runs
        WHERE source = %s AND status IN ('ok', 'unchanged')
        ORDER BY started_at DESC LIMIT 1
        """,
        (source_key,),
    )
    r = cur.fetchone()
    return {"rows_fetched": r[0], "payload_sha256": r[1]} if r else None


def _record_run(cur, source_key: str, status: str, **fields) -> None:
    cur.execute(
        """
        INSERT INTO source_sync_runs (
            source, status, finished_at, failure_reason,
            rows_fetched, rows_accepted, rows_inserted, rows_existing,
            rows_skipped, dup_ids, dup_fingerprints, payload_sha256, notes
        ) VALUES (
            %(source)s, %(status)s, NOW(), %(failure_reason)s,
            %(rows_fetched)s, %(rows_accepted)s, %(rows_inserted)s, %(rows_existing)s,
            %(rows_skipped)s, %(dup_ids)s, %(dup_fingerprints)s, %(payload_sha256)s, %(notes)s
        )
        """,
        {
            "source": source_key, "status": status, "failure_reason": None,
            "rows_fetched": None, "rows_accepted": None, "rows_inserted": None,
            "rows_existing": None, "rows_skipped": None, "dup_ids": None,
            "dup_fingerprints": None, "payload_sha256": None, "notes": None,
            **fields,
        },
    )


# ── per-source sync ───────────────────────────────────────────────────────────

def sync_source(cur, cfg: dict, registry: dict, dry_run: bool) -> dict:
    key = cfg["key"]
    print(f"\n── {cfg['label']} ({key}) " + "─" * (50 - len(cfg["label"]) - len(key)))

    url = registry["url_template"].format(document=registry["document"], gid=cfg["gid"])
    last = _last_successful_run(cur, key)

    try:
        _validate_registry(cfg)                                  # gate 0
        payload = _fetch(url)                                    # gate 1
        _assert_is_csv(payload)                                  # gate 2
        df = _parse(payload)                                     # gate 3
        _assert_columns(df, cfg["expected_columns"])             # gate 4
        _assert_row_count(len(df), last and last["rows_fetched"],
                          registry.get("row_count_floor", 0.5))  # gate 5

        digest = hashlib.sha256(payload).hexdigest()             # gate 6
        if last and last["payload_sha256"] == digest:
            print(f"  unchanged since last run ({len(df)} rows) — skipping")
            if not dry_run:
                _record_run(cur, key, "unchanged", rows_fetched=len(df),
                            payload_sha256=digest)
            return {"status": "unchanged", "inserted": 0}
    except GateFailure as e:
        print(f"  ✗ FAILED — {e}")
        print("    existing rows left untouched")
        if not dry_run:
            _record_run(cur, key, "failed", failure_reason=str(e))
        return {"status": "failed", "inserted": 0, "reason": str(e)}

    if not dry_run:
        # Never in a dry run: these are the only copy of the payload that produced
        # a given night's rows, and reaching for a dry run while debugging a bad
        # sync would otherwise destroy the evidence.
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        (SNAPSHOT_DIR / f"{key}.csv").write_bytes(payload)

    print(f"  fetched {len(df)} rows × {len(df.columns)} columns")

    accepted = df[df[cfg["validation_column"]].isin(registry["accepted_values"])]
    print(f"  accepted: {len(accepted)}")

    # Validate and canonicalise every accepted row before the first insert. A bad
    # value late in the sheet must not commit the valid prefix as a partial sync.
    try:
        built_rows = [
            _build_row(raw_row.to_dict(), cfg, key)
            for _, raw_row in accepted.iterrows()
        ]
    except ValueError as exc:
        reason = f"invalid promoted value: {exc}"
        print(f"  FAILED - {reason}")
        print("    existing rows left untouched")
        if not dry_run:
            _record_run(cur, key, "failed", rows_fetched=len(df),
                        rows_accepted=len(accepted), failure_reason=reason,
                        payload_sha256=hashlib.sha256(payload).hexdigest())
        return {"status": "failed", "inserted": 0, "reason": reason}

    # Copy-pasting a row duplicates its UUID in the sheet. onEdit() won't fix it
    # (the cell isn't empty), and ON CONFLICT DO NOTHING would silently discard the
    # second row — data loss, not duplication. So detect it before the database.
    ids = accepted[cfg["id_column"]].dropna()
    dup_ids = sorted(set(ids[ids.duplicated(keep=False)]))
    dup_id_set = set(dup_ids)
    if dup_ids:
        # Skipped rather than partially stored. ON CONFLICT DO NOTHING would keep
        # whichever row happened to come first and discard the rest — and under
        # insert-only the discarded content can never be recovered. Better to
        # store neither and report it until someone fixes the sheet.
        print(f"  ⚠ {len(dup_ids)} duplicated sheet id(s) — ALL rows sharing them are skipped:")
        for d in dup_ids[:5]:
            print(f"      {d}")

    inserted = existing = skipped = 0
    fingerprints = {}

    for row in built_rows:

        sid = row["sheet_row_id"]
        if not sid:
            skipped += 1
            continue
        if sid in dup_id_set:
            skipped += 1
            continue
        if not UUID_RE.match(sid):
            print(f"  ⚠ sheet id is not a UUID, skipping: {sid!r}")
            skipped += 1
            continue

        fp = _fingerprint(_s(row.get("doi_o")), _s(row.get("doi_r")), _s(row.get("url_r")))
        if fp:
            fingerprints.setdefault(fp, []).append(sid)

        if dry_run:
            inserted += 1
            continue

        # Check first so we don't burn a display_id on the ~99% of rows that already
        # exist after the first run. ON CONFLICT DO NOTHING still guards the insert.
        cur.execute(
            "SELECT 1 FROM source_records WHERE source = %s AND sheet_row_id = %s",
            (key, sid),
        )
        if cur.fetchone():
            existing += 1
            continue

        display_id = _next_display_id(cur, key, cfg["display_prefix"])
        try:
            if _insert(cur, row, display_id):
                inserted += 1
            else:
                existing += 1
        except psycopg2.errors.UniqueViolation as e:
            # ON CONFLICT names only the (source, sheet_row_id) index, so a
            # display_id collision still raises. Happens when the counter falls
            # behind the table — a partial restore, or renaming a source key while
            # keeping its display_prefix.
            raise GateFailure(
                f"display_id collision on {display_id} — source_display_counters "
                f"is behind source_records ({e})"
            )

    dup_fps = {f: s for f, s in fingerprints.items() if len(s) > 1}

    print(f"  inserted: {inserted}   already present: {existing}   skipped (no id): {skipped}")
    if dup_fps:
        print(f"  ⚠ {len(dup_fps)} paper(s) appear under more than one sheet id — flagged for review")

    if not dry_run:
        _record_run(cur, key, "ok",
                    rows_fetched=len(df), rows_accepted=len(accepted),
                    rows_inserted=inserted, rows_existing=existing,
                    rows_skipped=skipped, dup_ids=len(dup_ids),
                    dup_fingerprints=len(dup_fps),
                    payload_sha256=hashlib.sha256(payload).hexdigest())

    return {"status": "ok", "inserted": inserted, "existing": existing,
            "skipped": skipped, "dup_ids": len(dup_ids), "dup_fingerprints": len(dup_fps)}


# ── entry point ───────────────────────────────────────────────────────────────

def run(only: "str | None" = None, dry_run: bool = False) -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))

    sources = [s for s in registry["sources"] if s.get("enabled", True)]
    if only:
        sources = [s for s in sources if s["key"] == only]
        if not sources:
            raise SystemExit(f"No enabled source named {only!r} in sources.yml")

    print("=== FLoRA entry-sheet sync ===")
    if dry_run:
        print("[dry-run] nothing will be written")

    # A dry run still connects: gates 5 and 6 compare against the last successful
    # run, and skipping them would make --dry-run report "+1653 new" against an
    # already-synced database — the opposite of what a real run would do. Nothing
    # is written; every write path is behind `if not dry_run`.
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("ERROR: DATABASE_URL must be set in environment or .env")
    conn = psycopg2.connect(database_url)
    if dry_run:
        conn.set_session(readonly=True)
    cur = conn.cursor()

    results = {}
    try:
        for cfg in sources:
            results[cfg["key"]] = sync_source(cur, cfg, registry, dry_run)
            # Each source commits independently, so one failure can never roll
            # back another source's successful inserts.
            conn.commit()
    finally:
        conn.close()

    print("\n=== Summary ===")
    total = 0
    for key, r in results.items():
        if r["status"] == "failed":
            print(f"  {key:16} FAILED — {r['reason']}")
        elif r["status"] == "unchanged":
            print(f"  {key:16} unchanged")
        else:
            total += r["inserted"]
            extra = []
            if r.get("skipped"):
                extra.append(f"{r['skipped']} skipped (no id)")
            if r.get("dup_ids"):
                extra.append(f"{r['dup_ids']} duplicate ids")
            if r.get("dup_fingerprints"):
                extra.append(f"{r['dup_fingerprints']} duplicate papers")
            suffix = f"   ⚠ {', '.join(extra)}" if extra else ""
            print(f"  {key:16} +{r['inserted']} new, {r['existing']} existing{suffix}")
    print(f"  {'total':16} {total} row(s) inserted")

    if any(r["status"] == "failed" for r in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync FLoRA entry sheets into source_records")
    parser.add_argument("--source", help="Only sync this registry key (e.g. replications)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run the gates and report counts without writing.")
    args = parser.parse_args()

    run(only=args.source, dry_run=args.dry_run)
