"""Optional API derivative from the supplied filter_flora.R helper.

The primary flora.csv is never changed. The derivative applies identifier,
Boyce, Retraction Watch, and OSF registration filters in that order. Offline
runs explicitly report the two network filters as skipped.
"""
import argparse
import csv
import io
import json
import re
import time
import urllib.request
from pathlib import Path

import pandas as pd

from final_export import csv_text
from validate_flora import DOI_RE, _text
from validate_flora_network import RETRACTION_WATCH_URL

BOYCE_DOI = "10.1098/rsos.231240"
LOG_COLUMNS = ["doi_o", "doi_r", "reason", "detail"]
USER_AGENT = "flora-validation (API dataset filter)"


def fetch_retractions():
    request = urllib.request.Request(RETRACTION_WATCH_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(payload))
    if "OriginalPaperDOI" not in (reader.fieldnames or []):
        raise ValueError("Retraction Watch response has no OriginalPaperDOI column")
    retractions = {}
    for row in reader:
        doi = _text(row.get("OriginalPaperDOI")).lower()
        if doi and doi not in retractions:
            retractions[doi] = (f"Nature: {row.get('RetractionNature', '')}; "
                                f"Reason: {row.get('Reason', '')}")
    return retractions


def registration_type(guid):
    if not re.fullmatch(r"[a-z0-9]+", guid):
        raise ValueError("Invalid OSF GUID")
    request = urllib.request.Request(f"https://api.osf.io/v2/guids/{guid}/",
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    resource_type = payload.get("data", {}).get("type")
    if not resource_type:
        raise ValueError("OSF response has no resource type")
    return resource_type


def filter_dataset(frame, use_network=True, retractions=None, lookup_registration=None):
    if not {"doi_o", "doi_r"} <= set(frame.columns):
        raise ValueError("API filtering requires doi_o and doi_r columns")
    remaining = frame.copy()
    removed = []
    report = {"status": "passed", "input_rows": len(frame), "stages": [], "warnings": []}

    def remove(mask, reason, detail):
        nonlocal remaining
        rows = remaining.loc[mask]
        for row in rows.to_dict("records"):
            removed.append({"doi_o": row.get("doi_o"), "doi_r": row.get("doi_r"),
                            "reason": reason,
                            "detail": detail(row) if callable(detail) else detail})
        remaining = remaining.loc[~mask].copy()
        report["stages"].append({"name": reason, "status": "passed", "removed": len(rows)})

    valid = remaining["doi_r"].map(
        lambda value: bool(DOI_RE.fullmatch(_text(value))) or "handle.net/" in _text(value))
    remove(~valid, "invalid_identifier", "doi_r is not a valid DOI and does not contain handle.net/")
    remove(remaining["doi_r"].map(lambda value: _text(value).lower() == BOYCE_DOI),
           "boyce_exclusion", "Multi-study paper: Eleven years of student replication projects")

    if use_network:
        try:
            retractions = fetch_retractions() if retractions is None else retractions
        except Exception as exc:
            report["status"] = "failed"
            report["warnings"].append(f"Retraction Watch could not be loaded: {type(exc).__name__}: {exc}")
            report["stages"].append({"name": "retracted", "status": "failed"})
            report["stages"].append({"name": "osf_registration", "status": "skipped"})
            report["output_rows"] = None
            report["removed_rows"] = len(removed)
            return None, pd.DataFrame(removed, columns=LOG_COLUMNS), report
        remove(remaining["doi_r"].map(lambda value: _text(value).lower() in retractions),
               "retracted", lambda row: retractions[_text(row["doi_r"]).lower()])
        lookup = lookup_registration or registration_type
        # Cache within the run: a paper can appear against several original DOIs.
        resource_types, failures = {}, {}
        for doi in remaining["doi_r"].map(_text):
            if "osf.io/" not in doi.lower():
                continue
            match = re.search(r"osf\.io/([a-z0-9]+)", doi, re.I)
            if not match:
                failures[doi] = "No OSF GUID could be extracted"
                continue
            guid = match.group(1).lower()
            if guid in resource_types or guid in failures:
                continue
            try:
                resource_types[guid] = lookup(guid)
            except Exception as exc:
                failures[guid] = f"{type(exc).__name__}: {exc}"
            time.sleep(0.5)

        def is_registration(value):
            match = re.search(r"osf\.io/([a-z0-9]+)", _text(value), re.I)
            return bool(match and resource_types.get(match.group(1).lower()) == "registrations")

        remove(remaining["doi_r"].map(is_registration), "osf_registration",
               "OSF GUID resolved to a registration, not a preprint/paper")
        report["osf_checks"] = {"checked": len(resource_types), "failed": len(failures),
                                 "failures": failures}
        if failures:
            report["status"] = "incomplete"
            report["stages"][-1]["status"] = "incomplete"
            report["warnings"].append(f"{len(failures)} OSF lookup(s) failed; affected rows were retained.")
    else:
        report["status"] = "incomplete"
        report["stages"] += [{"name": "retracted", "status": "skipped"},
                               {"name": "osf_registration", "status": "skipped"}]
        report["warnings"].append("Retraction Watch and OSF registration checks were skipped in offline mode.")
    report["output_rows"] = len(remaining)
    report["removed_rows"] = len(removed)
    return remaining, pd.DataFrame(removed, columns=LOG_COLUMNS), report


def run_filter(input_path, output_dir, use_network=True):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    derivative = output_dir / "flora_filtered.csv"
    try:
        frame = pd.read_csv(input_path, dtype=str, keep_default_na=False,
                            na_values=["NA", ""], encoding="utf-8-sig")
    finally:
        # Even an invalid input or provider outage must not leave the previous
        # run's derivative at the current output path. Read first to allow an
        # intentional in-place re-filter of the derivative itself.
        derivative.unlink(missing_ok=True)
    filtered, log, report = filter_dataset(frame, use_network)
    if filtered is not None:
        derivative.write_text(csv_text(filtered), encoding="utf-8", newline="")
    (output_dir / "flora_filter_log.csv").write_text(csv_text(log), encoding="utf-8", newline="")
    (output_dir / "flora_filter_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("output/flora.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    report = run_filter(args.input, args.output_dir, use_network=not args.offline)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
