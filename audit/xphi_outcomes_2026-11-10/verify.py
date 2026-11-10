"""Check the handoff without a database or production writes.

Run from the repository root. --reports checks the downloaded originals against
report_manifest.json and re-extracts every quote (requires pdftotext for PDFs).
"""
import argparse
import collections
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def normalized(text):
    return re.sub(r"\s+", " ", text.replace("\u200b", "")).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default="output/flora.csv")
    parser.add_argument("--reports", type=Path)
    args = parser.parse_args()
    snapshot = read_csv(args.snapshot)
    current = {r["id"]: r for r in snapshot}
    changes = read_csv(HERE / "corrections_to_apply.csv")
    evidence = read_csv(HERE / "pair_evidence.csv")
    additions = json.loads((HERE / "manual_additions.json").read_text())
    manifest = json.loads((HERE / "report_manifest.json").read_text())
    counts = json.loads((HERE / "counts.json").read_text())
    seen = set()
    for c in changes:
        key = c["flora_id"], c["column"]
        assert key not in seen, key
        seen.add(key)
        row = current[c["flora_id"]]
        assert row[c["column"]] == c["expected_current_value"], key
        assert row["source_record_id"] == c["source_record_id"], key
        assert c["column"] in {"outcome", "outcome_quote", "outcome_quote_source", "doi_o", "url_r"}
    assert len(changes) == counts["field_edits"] == 75
    assert len({c["flora_id"] for c in changes}) == counts["records_edited"] == 37
    assert sum(c["column"] == "outcome" for c in changes) == 0
    assert len(evidence) == 42
    assert len(manifest) == 40
    assert len(additions) == 3
    for e in evidence:
        assert e["proposed_outcome"] in {"successful", "failed", "mixed"}
        assert e["outcome_quote"] and e["report_guid"] in manifest
        if e["flora_id"]:
            assert current[e["flora_id"]]["doi_o"] == e["doi_o"]
            assert e["current_outcome"] == e["proposed_outcome"]
    for a in additions:
        f = a["fields"]
        assert not any(r["doi_o"] == f["doi_o"] and r["doi_r"] == f["doi_r"] for r in snapshot)
        assert f["outcome_quote"] and f["out_quote_source"] and a["client_token"]
    for d in read_csv(HERE / "duplicate_rulings.csv"):
        drop, keep = current[d["retire_flora_id"]], current[d["survivor_flora_id"]]
        assert drop["doi_o"] == keep["doi_o"] and drop["doi_r"] == keep["doi_r"]
        assert drop["source_record_id"] == d["duplicate_source_record_id"]
        assert keep["source_record_id"] == d["survivor_source_record_id"]
    if args.reports:
        paths = {}
        for path in args.reports.iterdir():
            if path.suffix.lower() in {".pdf", ".docx", ".odt"}:
                paths[hashlib.sha256(path.read_bytes()).hexdigest()] = path
        for guid, report in manifest.items():
            path = paths[report["sha256"]]
            if path.suffix == ".pdf":
                text = subprocess.check_output(["pdftotext", "-layout", str(path), "-"], text=True)
            else:
                with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
                    if path.suffix == ".docx":
                        root = ET.fromstring(archive.read("word/document.xml"))
                        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                        text = "\n".join("".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p"))
                    else:
                        root = ET.fromstring(archive.read("content.xml"))
                        text = "\n".join("".join(p.itertext()) for p in root.iter() if p.tag.endswith("}p"))
            for e in evidence:
                if e["report_guid"] == guid:
                    assert normalized(e["outcome_quote"]) in normalized(text), (guid, e["flora_id"])
        print("Verified all 40 report hashes and all 42 quoted evidence rows.")
    totals = collections.Counter(e["proposed_outcome"] for e in evidence if e["status"] != "duplicate_retire")
    assert sum(totals.values()) == 40
    print(f"Verified {len(changes)} edits on 37 records, 3 additions, 2 duplicate retirements; outcomes: {dict(totals)}.")


if __name__ == "__main__":
    main()
