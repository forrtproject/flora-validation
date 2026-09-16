"""Convert a supplied FReD-data cache to the website's portable metadata seed.

Usage: python scripts/import_reference_seed.py CACHE_DIRECTORY [--rscript PATH]
The source cache is read-only. R/jsonlite are needed only for this conversion.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bibliographic_helpers as helpers  # noqa: E402


def convert(cache, rscript="Rscript", output=None):
    cache = Path(cache).resolve()
    output = Path(output or ROOT / "data/reference_metadata_seed.json.gz")
    with tempfile.TemporaryDirectory(prefix="flora_reference_cache_") as temporary:
        converted = Path(temporary) / "reference_cache.json"
        subprocess.run([rscript, str(ROOT / "scripts/convert_reference_cache.R"),
                        str(cache), str(converted)], check=True)
        raw = json.loads(converted.read_text(encoding="utf-8"))
    records = {}
    for item in raw["fields"]:
        key = helpers.normalise_key(item.get("doi"))
        if not key or (item.get("source") or "").endswith("error"):
            continue
        row = {field: helpers.text(item.get(field)) for field in (
            "title", "authors_json", "journal", "year", "volume", "issue", "pages")}
        row.update(helpers.author_fields(helpers._parse_authors(row["authors_json"])))
        row["metadata_source"] = item.get("source") or "crossref"
        records[key] = helpers.merge_present(records.get(key, {}), row)
    for source, target in (("apa", "apa_ref"), ("bibtex", "bibtex_ref")):
        for key, value in raw["citations"].get(source, {}).items():
            key = helpers.normalise_key(key)
            if key and helpers.text(value):
                records.setdefault(key, {})[target] = value
                records[key]["_reference_cached"] = True
    for filename in ("openalex_keywords_language.csv", "openalex_abstracts.csv", "unpaywall_oa.csv"):
        path = cache / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for item in csv.DictReader(handle):
                key = helpers.normalise_key(item.get("doi"))
                if not key:
                    continue
                row = records.setdefault(key, {})
                for field in ("language", "abstract", "oa_url"):
                    if field in item:
                        row[field] = helpers.text(item[field])
                if filename == "unpaywall_oa.csv":
                    row["_unpaywall_cached"] = True
    work_path = cache / "openalex_work_fields.csv"
    if work_path.exists():
        with work_path.open(encoding="utf-8-sig", newline="") as handle:
            for item in csv.DictReader(handle):
                key = helpers.normalise_key(item["work_id"])
                if not key or item.get("source") == "openalex_error":
                    continue
                first, last = helpers.text(item.get("first_page")), helpers.text(item.get("last_page"))
                row = {field: helpers.text(item.get(field)) for field in (
                    "title", "authors_json", "journal", "year", "volume", "issue")}
                row.update(helpers.author_fields(helpers._parse_authors(row["authors_json"])))
                row["pages"] = f"{first}-{last}" if first and last and first != last else first or last
                row["oa_work_id"] = key
                row["metadata_source"] = "openalex-workid"
                records[key] = helpers.synthesise_references(row, key)
    for key, row in records.items():
        if not row.get("title") and row.get("bibtex_ref"):
            row.update(helpers.merge_present(helpers.parse_bibtex(row["bibtex_ref"]), row))
    mapping = {}
    for item in raw["url_to_doi"]:
        url, doi = helpers.normalise_key(item.get("url_r")), helpers.normalise_key(item.get("doi_r"))
        if url and doi:
            mapping.setdefault(url, doi)
    filenames = ("crossref_fields.rds", "crossref_citations.rds", "urlr_doir_map.rds",
                 "openalex_keywords_language.csv", "openalex_abstracts.csv",
                 "openalex_work_fields.csv", "unpaywall_oa.csv")
    manifest = {name: {"sha256": hashlib.sha256((cache / name).read_bytes()).hexdigest(),
                       "bytes": (cache / name).stat().st_size}
                for name in filenames if (cache / name).exists()}
    data = {"schema_version": 1, "source": "User-provided FReD-data/cache",
            "source_files": manifest, "records": records, "url_to_doi": mapping}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8"), mtime=0))
    print(f"Imported {len(records)} reference records and {len(mapping)} URL mappings to {output.name}")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache_directory")
    parser.add_argument("--rscript", default="Rscript")
    parser.add_argument("--output")
    args = parser.parse_args()
    convert(args.cache_directory, args.rscript, args.output)
