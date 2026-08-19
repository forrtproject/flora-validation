"""
backfill_oa_work_ids.py — Fill missing OpenAlex work IDs (oa_work_id_o / oa_work_id_r)
from OpenAlex, looked up by DOI in batches of 50. Stores the bare 'W…' id.

Safe to re-run: only rows whose work ID is currently NULL are touched, and the DB is
the cache (nothing is re-fetched once filled). Uses the effective DOI
(COALESCE(final_doi_*, doi_*)) so corrections are respected. Intended to run after the
nightly sync and after DOI corrections (which NULL the stale id via the DB trigger).

Usage:
    python backfill_oa_work_ids.py            # apply
    python backfill_oa_work_ids.py --dry-run  # fetch + report, write nothing
"""
import json
import os
import sys
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

MAILTO = os.environ.get("OPENALEX_MAILTO", "lukas.wallrich@gmail.com")
BATCH = 50   # OpenAlex allows up to 50 values in an OR filter


def _norm_doi(doi: str) -> str:
    """Lowercase, strip any URL/prefix → bare '10.xxxx/…' for matching."""
    d = (doi or "").strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if d.startswith(p):
            d = d[len(p):]
    return d


def _fetch_batch(dois: list[str]) -> dict[str, str]:
    """Look up a batch of ≤50 DOIs → {normalized_doi: 'W…'}."""
    filt = "doi:" + "|".join(dois)
    # per-page > BATCH: a DOI can match more than one OpenAlex work (duplicates/versions),
    # so 50 DOIs may return >50 rows — a per-page of 50 would truncate and silently drop
    # another DOI's only match. 200 is OpenAlex's max and can never overflow a 50-DOI batch.
    url = (f"https://api.openalex.org/works?filter={quote(filt, safe='|:/().-_')}"
           f"&per-page=200&select=id,doi&mailto={MAILTO}")
    req = Request(url, headers={"User-Agent": f"flora-backfill (mailto:{MAILTO})"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = {}
    for w in data.get("results", []):
        wid = (w.get("id") or "").rsplit("/", 1)[-1]   # 'https://openalex.org/W…' → 'W…'
        dn = _norm_doi(w.get("doi"))
        if wid.startswith("W") and dn:
            out[dn] = wid
    return out


SIDES = [
    ("original",    "final_doi_o", "doi_o", "oa_work_id_o"),
    ("replication", "final_doi_r", "doi_r", "oa_work_id_r"),
]


def _connect(database_url: str):
    """Connect with TCP keepalives so a cloud pooler doesn't silently drop the socket."""
    return psycopg2.connect(
        database_url, connect_timeout=30,
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
    )


def _read_missing(database_url: str) -> list[dict]:
    """Short DB session: collect the rows still missing each work id, grouped by DOI.
    Returns one plan entry per side. The connection is opened and closed here so it is
    never held open across the slow OpenAlex fetch (a cloud DB drops idle connections)."""
    conn = _connect(database_url)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        plan = []
        for side, final_doi_col, doi_col, col in SIDES:
            doi_expr = f"COALESCE({final_doi_col}, {doi_col})"
            cur.execute(
                f"""SELECT record_id, {doi_expr} AS doi FROM unvalidated
                    WHERE {col} IS NULL AND {doi_expr} IS NOT NULL AND {doi_expr} <> ''"""
            )
            by_doi: dict[str, list] = {}
            n = 0
            for r in cur.fetchall():
                by_doi.setdefault(_norm_doi(r["doi"]), []).append(r["record_id"])
                n += 1
            plan.append({"side": side, "col": col, "by_doi": by_doi, "n_rows": n})
        return plan
    finally:
        conn.close()


def _sync_validated_ids(cur) -> int:
    """Copy known IDs into DOI-matching validated rows; return rows changed."""
    synced = 0
    for side, final_doi_col, doi_col, col in SIDES:
        cur.execute(
            f"""
            UPDATE validated v
            SET {col} = u.{col}
            FROM unvalidated u
            WHERE u.record_id = v.record_id
              AND NULLIF(v.{col}, '') IS NULL
              AND NULLIF(u.{col}, '') IS NOT NULL
              AND v.{doi_col} IS NOT DISTINCT FROM
                  COALESCE(u.{final_doi_col}, u.{doi_col})
            """
        )
        synced += cur.rowcount
        print(f"[{side}] copied {cur.rowcount} work ID(s) into validated")
    return synced


def run(dry_run: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set")

    # Phase 1 — read what's missing (quick DB session, then disconnect).
    plan = _read_missing(database_url)

    # Phase 2 — fetch from OpenAlex. No DB connection is held during this slow step.
    for entry in plan:
        dois = [d for d in entry["by_doi"] if d]
        print(f"[{entry['side']}] {entry['n_rows']} rows missing {entry['col']} across {len(dois)} distinct DOIs")
        found: dict[str, str] = {}
        for i in range(0, len(dois), BATCH):
            try:
                found.update(_fetch_batch(dois[i:i + BATCH]))
            except (HTTPError, URLError, TimeoutError, ValueError) as e:
                print(f"  batch {i // BATCH} error: {e}")
            time.sleep(0.15)   # be polite to the API
            print(f"  … {min(i + BATCH, len(dois))}/{len(dois)} DOIs queried")
        entry["found"] = found

    if dry_run:
        for entry in plan:
            hits = sum(len(entry["by_doi"].get(dn, [])) for dn in entry["found"])
            print(f"[{entry['side']}] would fill {hits} / {entry['n_rows']}")
        print("DRY RUN — nothing written.")
        return

    # Phase 3 — write results (fresh DB session, so the connection is never stale). Each
    # side is written as ONE bulk UPDATE via a VALUES join instead of ~1500 round-trips,
    # which both is far faster and avoids the pooler dropping a long-running transaction.
    conn = _connect(database_url)
    filled = missing = export_synced = 0
    try:
        cur = conn.cursor()
        for entry in plan:
            col = entry["col"]
            pairs = [
                (str(rid), wid)
                for dn, wid in entry["found"].items()
                for rid in entry["by_doi"].get(dn, [])
            ]
            if pairs:
                psycopg2.extras.execute_values(
                    cur,
                    f"UPDATE unvalidated u SET {col} = v.wid "
                    f"FROM (VALUES %s) AS v(record_id, wid) "
                    f"WHERE u.record_id = v.record_id::uuid AND u.{col} IS NULL",
                    pairs,
                    page_size=len(pairs),   # one statement so cur.rowcount is the true total
                )
            updates = cur.rowcount if pairs else 0
            filled += updates
            missing += entry["n_rows"] - updates
            print(f"[{entry['side']}] matched {updates} / {entry['n_rows']}  "
                  f"(not found on OpenAlex: {entry['n_rows'] - updates})")

        # Keep the authoritative export table current in this same transaction.
        # This loop is independent of `pairs`, so it also repairs validated rows
        # left blank by backfill runs from before this synchronization existed.
        export_synced = _sync_validated_ids(cur)
        conn.commit()
        print("Committed.")
    finally:
        conn.close()
    print(f"TOTAL filled: {filled}  |  exported: {export_synced}  |  still missing: {missing}")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
