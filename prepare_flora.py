"""Finish the website and scheduled FLoRA pipeline with one checked CSV snapshot.

Usage: python prepare_flora.py --output-dir output

The normal run builds from the database and checks every network target, using
the shared 30-day success cache. For an offline validation of an existing export,
use --input path/to/flora.csv --network-checks none. Skipped and limited checks
remain explicit in the report. This command never publishes to GitHub or OSF.
"""
import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

import preprint_dedup
import release_helpers
import validate_flora
import validate_flora_network
from flora_store import UNCHECKED_REVISION
from output_lock import output_directory_lock

REPORT_JSON = "flora_preparation_report.json"
REPORT_MARKDOWN = "flora_preparation_report.md"
TRANSFORM_DIAGNOSTICS = {
    "flora_export_log.csv": "rows omitted because a title is missing",
    "dup_outcome_conflicts.csv": "merged groups with conflicting outcomes",
    "preprint_dedup_candidates.csv":
        "preprint duplicate pairs awaiting a decision (FLoRA tab, Preprint duplicates)",
    "cross_type_duplicate_rulings.csv":
        "rows ruled a duplicate of a record of the other type and left out "
        "(check in Source Records, Duplicates)",
}
# A diagnostic file can log more than needs attention. The candidates log records
# every detected pair, including ones a human already ruled on, so only the
# unresolved ones count.
DIAGNOSTIC_FILTERS = {
    "preprint_dedup_candidates.csv": preprint_dedup.unresolved,
}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_export(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False,
                       na_values=["NA", ""], encoding="utf-8-sig")


def validate_identity(frame):
    """Missing/duplicate identities or wrong digests block the final artifact."""
    if list(frame.columns[:2]) != ["id", "id_md5"]:
        raise ValueError("The first two CSV columns must be id and id_md5")
    if frame.empty:
        raise ValueError("The prepared dataset is empty; refusing a final release")
    ids = frame["id"]
    if ids.isna().any() or ids.str.strip().eq("").any() or ids.duplicated().any():
        raise ValueError("Every exported row needs a unique, nonblank id")
    expected = ids.map(lambda value: hashlib.md5(value.encode("utf-8")).hexdigest())
    if not frame["id_md5"].eq(expected).all():
        raise ValueError("id_md5 must equal the lowercase MD5 of the UTF-8 id")
    return {"status": "passed", "rows": len(frame), "unique_ids": len(ids)}


def render_report(report):
    lines = ["# FLoRA preparation report", "",
             f"Generated: {report['generated_at']}", "",
             f"Status: **{report['status']}**", "",
             f"Mode: {report['mode']}", "",
             f"Rows: {report.get('rows', 0):,}", "",
             "## Stages", ""]
    for stage in report.get("stages", []):
        lines.append(f"- {stage['name']}: {stage['status']}"
                     + (f" — {stage['detail']}" if stage.get("detail") else ""))
    if report.get("warnings"):
        lines += ["", "## Items needing attention", ""]
        lines.extend(f"- {item}" for item in report["warnings"])
    if report.get("errors"):
        lines += ["", "## Execution errors", ""]
        lines.extend(f"- {item}" for item in report["errors"])
    if report.get("storage"):
        storage = report["storage"]
        lines += ["", "## Stored dataset", "",
                  f"- Database status: {storage.get('status', 'unknown')}.",
                  f"- Active rows in flora_data: {storage['rows']:,}."]
        if report.get("recovery_csv"):
            location = ("Use the job's **Download recovery CSV** button."
                        if report.get("recovery_artifact")
                        else f"The imported CSV remains at `{report['recovery_csv']}` for recovery.")
            lines.append("- Database import succeeded, but the final CSV could not be replaced. " + location)
    if report.get("diagnostics"):
        lines += ["", "## Transform diagnostics", ""]
        for filename, diagnostic in report["diagnostics"].items():
            lines.append(f"- {filename}: {diagnostic['rows']} {diagnostic['description']}.")
        lines += ["", "The JSON report includes the affected records and reasons."]
    if report.get("structural"):
        lines += ["", validate_flora.render(report["structural"])]
    if report.get("network"):
        lines += ["", validate_flora_network.render_validation(report["network"])]
    if report.get("release"):
        release = report["release"]
        lines += ["", "## Prepared release", "",
                  f"- File: {release['filename']}",
                  f"- SHA-256: `{release['sha256']}`",
                  "- Publication: local files only; no OSF or GitHub release."]
    lines += ["", "## Dataset history", "",
              "The website records daily dataset counts in its shared history table. "
              "This run also writes a CSV history and a Markdown summary alongside the release."]
    return "\n".join(lines) + "\n"


def write_report(output_dir, report):
    output_dir.mkdir(parents=True, exist_ok=True)
    report["warning_count"] = len(report.get("warnings", []))
    report["failure_count"] = len(report.get("errors", []))
    (output_dir / REPORT_JSON).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / REPORT_MARKDOWN).write_text(render_report(report), encoding="utf-8")


def record_local_history(output_dir, frame):
    path = output_dir / "flora_history.csv"
    date = datetime.now(timezone.utc).date().isoformat()
    history = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as stream:
            history = [row for row in csv.DictReader(stream) if row["date"] != date]
    entry = {"date": date, "total": len(frame),
             "replications": int(frame["type"].eq("replication").sum()),
             "reproductions": int(frame["type"].eq("reproduction").sum())}
    history.append(entry)
    history.sort(key=lambda row: row["date"])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(entry), lineterminator="\n")
        writer.writeheader()
        writer.writerows(history)
    lines = ["# FLoRA dataset history", "",
             "| Date | Total | Replications | Reproductions |",
             "| --- | ---: | ---: | ---: |"]
    for row in reversed(history):
        lines.append(f"| {row['date']} | {row['total']} | {row['replications']} | {row['reproductions']} |")
    (output_dir / "flora_dataset_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def current_dataset_revision():
    from flora_store import dataset_revision
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            return dataset_revision(cur)
    finally:
        connection.close()


def store_dataset(candidate, *, expected_revision=UNCHECKED_REVISION):
    """Commit the complete prepared snapshot to its independent database table."""
    from flora_store import materialize
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required to store the prepared FLoRA table")
    connection = psycopg2.connect(database_url)
    try:
        with connection:
            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                return materialize(cur, candidate, expected_revision=expected_revision)
    finally:
        connection.close()


def publish_release(candidate, staging, output_dir):
    """Replace the release bundle, restoring previous files if a replace fails."""
    files = [(path, output_dir / path.name) for path in staging.iterdir() if path.is_file()]
    # Publish the CSV last so it remains available for database recovery on failure.
    files.append((candidate, output_dir / "flora.csv"))
    backups = staging / "backups"
    backups.mkdir()
    for _, destination in files:
        if destination.exists():
            shutil.copyfile(destination, backups / destination.name)
    replaced = []
    try:
        for source, destination in files:
            source.replace(destination)
            replaced.append(destination)
    except OSError:
        for destination in reversed(replaced):
            backup = backups / destination.name
            if backup.exists():
                backup.replace(destination)
            else:
                destination.unlink(missing_ok=True)
        raise


def prepare(output_dir: Path, input_path=None, network_checks="all", network_limit=0,
            version=None, release_notes="", api_filter=False, store_data=None) -> dict:
    # Own this output directory before even writing the running report. Runs
    # targeting other directories can still prepare concurrently; the database
    # revision check arbitrates their complete-snapshot commits independently.
    with output_directory_lock(output_dir):
        return _prepare(output_dir, input_path, network_checks, network_limit,
                        version, release_notes, api_filter, store_data)


def _prepare(output_dir, input_path, network_checks, network_limit,
             version, release_notes, api_filter, store_data):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate = output_dir / ".flora.candidate.csv"
    # Database pipeline runs persist their prepared product. Reading an arbitrary
    # CSV is offline by default; importing it requires explicit --store-data.
    should_store = input_path is None if store_data is None else store_data
    report = {"schema_version": 1, "generated_at": timestamp(),
              "mode": "existing_csv" if input_path else "database_pipeline",
              "status": "running", "rows": 0, "stages": [], "warnings": [], "errors": []}
    write_report(output_dir, report)
    stage_name = "final CSV"
    release_staging = None
    try:
        # Capture before building, then compare under the materialization lock.
        # This covers website, scheduled, and CLI runs without holding a database
        # transaction open throughout slow external validation.
        expected_revision = current_dataset_revision() if should_store else UNCHECKED_REVISION
        previous_diagnostics = {
            name: (output_dir / name).stat().st_mtime_ns if (output_dir / name).exists() else None
            for name in TRANSFORM_DIAGNOSTICS
        }
        # Always start with an absent candidate so a failed build cannot validate
        # a file left by an earlier run. A previous published flora.csv is retained
        # until a complete new candidate is available.
        candidate.unlink(missing_ok=True)
        if input_path:
            candidate.write_bytes(Path(input_path).read_bytes())
        else:
            import transform_sources
            transform_sources.run(candidate, review_issue=False)
            # The transform emits these files only when it finds an issue. Read
            # only files written in this build, so a historic issue log cannot
            # masquerade as a current finding during a standalone rerun.
            report["diagnostics"] = {}
            for name, description in TRANSFORM_DIAGNOSTICS.items():
                path = output_dir / name
                if path.exists() and path.stat().st_mtime_ns != previous_diagnostics[name]:
                    records = pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")
                    if name in DIAGNOSTIC_FILTERS:
                        records = DIAGNOSTIC_FILTERS[name](records)
                    report["diagnostics"][name] = {
                        "description": description, "rows": len(records), "records": records}
                    if records:
                        report["warnings"].append(f"{len(records)} {description}; see {name}.")
        if not candidate.exists():
            raise ValueError("The transform did not produce a CSV")
        frame = read_export(candidate)
        report["identity"] = validate_identity(frame)
        report["rows"] = len(frame)
        report["columns"] = list(frame.columns)
        report["stages"].append({"name": stage_name, "status": "passed"})
        stage_name = "structural validation"
        suppressions = validate_flora.load_suppressions()
        structural = validate_flora.validate(frame, suppressions)
        report["structural"] = structural
        issues = sum(len(check["items"]) for check in structural["results"])
        report["stages"].append({"name": stage_name,
                                  "status": "needs_attention" if issues else "passed",
                                  "detail": f"{issues} data issue(s)"})
        if issues:
            report["warnings"].append(f"Structural validation found {issues} data issue(s).")
        (output_dir / "flora_validation.md").write_text(
            validate_flora.render(structural), encoding="utf-8")
        write_report(output_dir, report)

        stage_name = "network validation"
        if network_checks == "none":
            network = validate_flora_network.validate(None, frame, checks="none")
        else:
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL is required for the shared network validation cache")
            conn = psycopg2.connect(database_url)
            try:
                conn.autocommit = True
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                validate_flora_network.ensure_cache(cur)
                network = validate_flora_network.validate(
                    cur, frame, network_checks, network_limit, suppressions)
            finally:
                conn.close()
        report["network"] = network
        report["stages"].append({"name": stage_name, "status": network["status"]})
        if network["status"] != "passed":
            report["warnings"].append(f"Network validation: {network['status']}; "
                                       f"{network['issue_count']} finding(s).")
        (output_dir / "flora_network_validation.md").write_text(
            validate_flora_network.render_validation(network), encoding="utf-8")
        network_rows = [{"check": heading, "item": item}
                        for heading, items in network["sections"].items() for item in items]
        pd.DataFrame(network_rows, columns=["check", "item"]).to_csv(
            output_dir / "flora_network_validation.csv", index=False, lineterminator="\n")
        if api_filter:
            stage_name = "optional API dataset filter"
            from filter_flora import run_filter
            filter_report = run_filter(candidate, output_dir, use_network=network_checks != "none")
            report["api_filter"] = filter_report
            report["stages"].append({"name": stage_name, "status": filter_report["status"],
                                      "detail": f"{filter_report['removed_rows']} row(s) removed from derivative"})
            if filter_report["status"] != "passed":
                report["warnings"].append(f"Optional API filter: {filter_report['status']}. "
                                           + " ".join(filter_report["warnings"]))
        stage_name = "release preparation and history"
        release_staging = tempfile.TemporaryDirectory(prefix=".flora-release-", dir=output_dir)
        staged = Path(release_staging.name)
        history = output_dir / "flora_history.csv"
        if history.exists():
            shutil.copyfile(history, staged / history.name)
        report["release"] = release_helpers.prepare_release(
            candidate, staged, len(frame), list(frame.columns), version, release_notes)
        record_local_history(staged, frame)
        report["stages"].append({"name": stage_name, "status": "passed"})
        if should_store:
            stage_name = "store prepared FLoRA table"
            report["storage"] = store_dataset(candidate, expected_revision=expected_revision)
            report["storage"]["status"] = "committed"
            report["stages"].append({"name": stage_name, "status": "passed",
                                      "detail": f"{report['storage']['rows']} active rows in flora_data"})
        stage_name = "publish CSV artifact"
        publish_release(candidate, staged, output_dir)
        report["stages"].append({"name": stage_name, "status": "passed"})
        report["status"] = "needs_attention" if report["warnings"] else "success"
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(f"{stage_name}: {type(exc).__name__}: {exc}")
        report["stages"].append({"name": stage_name, "status": "failed"})
        traceback.print_exc()
    finally:
        if release_staging is not None:
            release_staging.cleanup()
        if report["status"] == "failed" and report.get("storage", {}).get("status") == "committed" and candidate.exists():
            recovery = output_dir / "flora_committed_recovery.csv"
            try:
                # Keep the committed bytes outside the next run's temporary path.
                recovery.write_bytes(candidate.read_bytes())
                report["recovery_csv"] = recovery.name
                candidate.unlink(missing_ok=True)
            except OSError:
                report["recovery_csv"] = candidate.name
        else:
            candidate.unlink(missing_ok=True)
        report["finished_at"] = timestamp()
        write_report(output_dir, report)
    return report


def main():
    from pipeline_logging import start
    start("prepare")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--network-checks", choices=["all", "retractions", "dois", "urls", "none"],
                        default="all")
    parser.add_argument("--network-limit", type=int, default=0)
    parser.add_argument("--version")
    parser.add_argument("--release-notes", default="")
    parser.add_argument("--api-filter", action="store_true",
                        help="also write the separate DynamoDB/API derivative and removal log")
    storage = parser.add_mutually_exclusive_group()
    storage.add_argument("--store-data", dest="store_data", action="store_true",
                         help="also import --input CSV into the permanent flora_data table")
    storage.add_argument("--no-store-data", dest="store_data", action="store_false",
                         help="prepare files only without updating flora_data")
    parser.set_defaults(store_data=None)
    args = parser.parse_args()
    if args.network_limit < 0:
        parser.error("--network-limit must be zero (all) or positive")
    report = prepare(args.output_dir, args.input, args.network_checks,
                     args.network_limit, args.version, args.release_notes, args.api_filter,
                     args.store_data)
    print(f"Preparation {report['status']}: {report['rows']} rows; "
          f"{report['warning_count']} warning(s); {report['failure_count']} failure(s)")
    print(f"Report: {args.output_dir / REPORT_MARKDOWN}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
