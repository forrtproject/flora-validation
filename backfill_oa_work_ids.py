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
    url = (f"https://api.openalex.org/works?filter={quote(filt, safe='|:/().-_')}"
           f"&per-page={BATCH}&select=id,doi&mailto={MAILTO}")
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


def run(dry_run: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set")
    conn = psycopg2.connect(database_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    filled = missing = 0

    for side, doi_expr, col in [
        ("original",    "COALESCE(final_doi_o, doi_o)", "oa_work_id_o"),
        ("replication", "COALESCE(final_doi_r, doi_r)", "oa_work_id_r"),
    ]:
        cur.execute(
            f"""SELECT record_id, {doi_expr} AS doi FROM unvalidated
                WHERE {col} IS NULL AND {doi_expr} IS NOT NULL AND {doi_expr} <> ''"""
        )
        rows = cur.fetchall()
        by_doi: dict[str, list] = {}
        for r in rows:
            by_doi.setdefault(_norm_doi(r["doi"]), []).append(r["record_id"])
        dois = [d for d in by_doi if d]
        print(f"[{side}] {len(rows)} rows missing {col} across {len(dois)} distinct DOIs")

        found: dict[str, str] = {}
        for i in range(0, len(dois), BATCH):
            try:
                found.update(_fetch_batch(dois[i:i + BATCH]))
            except (HTTPError, URLError, TimeoutError, ValueError) as e:
                print(f"  batch {i // BATCH} error: {e}")
            time.sleep(0.15)   # be polite to the API
            print(f"  … {min(i + BATCH, len(dois))}/{len(dois)} DOIs queried")

        updates = 0
        for dn, wid in found.items():
            for rid in by_doi.get(dn, []):
                if not dry_run:
                    cur.execute(
                        f"UPDATE unvalidated SET {col} = %s WHERE record_id = %s AND {col} IS NULL",
                        (wid, rid),
                    )
                updates += 1
        filled += updates
        missing += len(rows) - updates
        print(f"[{side}] matched {updates} / {len(rows)}  (not found on OpenAlex: {len(rows) - updates})")

    if dry_run:
        conn.rollback()
        print("DRY RUN — nothing written.")
    else:
        conn.commit()
        print("Committed.")
    conn.close()
    print(f"TOTAL filled: {filled}  |  still missing: {missing}")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
