"""
Generate the static-mode data files for GitHub Pages.

Reads extracted.csv + onboarding.json + oa_cache.json and writes:
  - docs/pairs.json        (161 normal-mode pairs with OA fields)
  - docs/hard_pairs.json   (8 no-abstract pairs for hard mode)
  - docs/onboarding.json   (5 onboarding pairs with OA fields)

Run after editing extracted.csv, onboarding.json, or refreshing oa_cache.json.

Usage: .venv/bin/python build_static.py
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "extracted.csv"
ONBOARDING_PATH = ROOT / "onboarding.json"
OA_PATH = ROOT / "oa_cache.json"
DOCS = ROOT / "docs"


def main():
    oa = json.loads(OA_PATH.read_text())

    def oa_url(doi: str | None) -> str | None:
        if not doi:
            return None
        return (oa.get(doi.strip()) or {}).get("oa_url")

    # The extractor still emits 'success'/'failure'; this app uses 'successful'/'failed'.
    # Mirrors _OUTCOME_RENAME in csv_to_db.py so the static/demo pairs match the live app.
    # Exact match only, so reproduction labels ("computationally successful, …") pass through.
    outcome_rename = {"success": "successful", "failure": "failed"}

    def decorate(p: dict) -> dict:
        p["oa_url_r"] = oa_url(p.get("doi_r"))
        p["oa_url_o"] = oa_url(p.get("doi_o"))
        if p.get("outcome") in outcome_rename:
            p["outcome"] = outcome_rename[p["outcome"]]
        return p

    normal, hard, seen = [], [], set()
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if row["pair_id"] in seen:
                continue
            seen.add(row["pair_id"])
            decorated = decorate(dict(row))
            if (row.get("abstract_r") or "").strip():
                normal.append(decorated)
            else:
                hard.append(decorated)

    onboarding = json.loads(ONBOARDING_PATH.read_text())
    onboarding["pairs"] = [decorate(p) for p in onboarding["pairs"]]

    (DOCS / "pairs.json").write_text(json.dumps(normal, indent=2))
    (DOCS / "hard_pairs.json").write_text(json.dumps(hard, indent=2))
    (DOCS / "onboarding.json").write_text(json.dumps(onboarding, indent=2))

    print(f"  normal pairs: {len(normal)} -> docs/pairs.json")
    print(f"  hard pairs:   {len(hard)} -> docs/hard_pairs.json")
    print(f"  onboarding:   {len(onboarding['pairs'])} -> docs/onboarding.json")


if __name__ == "__main__":
    main()
