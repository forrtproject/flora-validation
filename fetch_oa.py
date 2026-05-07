"""
Fetch Open Access status from Unpaywall for every DOI in extracted.csv +
onboarding.json and cache the result in oa_cache.json.

Run on demand; the result is checked into the repo so the app doesn't hit the
network at request time. Re-run when extracted.csv or onboarding.json change.

Usage: .venv/bin/python fetch_oa.py
"""

import csv
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

EMAIL = "lukas.wallrich@gmail.com"
ROOT = Path(__file__).parent
CSV_PATH = ROOT / "extracted.csv"
ONBOARDING_PATH = ROOT / "onboarding.json"
CACHE_PATH = ROOT / "oa_cache.json"


def collect_dois() -> set[str]:
    dois = set()
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            doi = (row.get("doi_r") or "").strip()
            if doi:
                dois.add(doi)
    onboarding = json.loads(ONBOARDING_PATH.read_text())
    for p in onboarding["pairs"]:
        for key in ("doi_r", "doi_o"):
            doi = (p.get(key) or "").strip()
            if doi and not doi.startswith("10.0000/"):
                dois.add(doi)
    return dois


def lookup(doi: str) -> dict:
    url = f"https://api.unpaywall.org/v2/{quote(doi, safe='/.()_-')}?email={EMAIL}"
    req = Request(url, headers={"User-Agent": "flora-validator/0.2"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def best_oa_url(payload: dict) -> str | None:
    loc = payload.get("best_oa_location") or {}
    return loc.get("url_for_pdf") or loc.get("url")


def main():
    cache: dict[str, dict] = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())

    dois = sorted(collect_dois())
    print(f"resolving {len(dois)} DOIs (already cached: {sum(1 for d in dois if d in cache)})")

    for i, doi in enumerate(dois, 1):
        if doi in cache and cache[doi].get("source") == "unpaywall":
            continue
        try:
            data = lookup(doi)
            cache[doi] = {
                "is_oa": bool(data.get("is_oa")),
                "oa_url": best_oa_url(data),
                "license": (data.get("best_oa_location") or {}).get("license"),
                "source": "unpaywall",
            }
            status = "OA" if cache[doi]["is_oa"] else "gated"
            print(f"  [{i:3d}/{len(dois)}] {doi}  {status}")
        except HTTPError as e:
            cache[doi] = {"is_oa": False, "oa_url": None, "error": f"http {e.code}", "source": "unpaywall"}
            print(f"  [{i:3d}/{len(dois)}] {doi}  http {e.code}")
        except (URLError, TimeoutError) as e:
            cache[doi] = {"is_oa": False, "oa_url": None, "error": str(e), "source": "unpaywall"}
            print(f"  [{i:3d}/{len(dois)}] {doi}  error: {e}")
        if i % 20 == 0:
            CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
        time.sleep(0.05)

    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
    oa = sum(1 for v in cache.values() if v.get("is_oa"))
    print(f"\ndone. {oa}/{len(cache)} OA. cache: {CACHE_PATH}")


if __name__ == "__main__":
    sys.exit(main())
