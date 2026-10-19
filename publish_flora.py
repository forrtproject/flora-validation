"""
publish_flora.py — Write the stored FLoRA dataset to output/ for the repository copy.

The website's Run pipeline stores its dataset in flora_data, but the web server
cannot commit to the repository. After a successful run it starts the Publish
FLoRA workflow (.github/workflows/publish-flora.yml), which runs this script and
commits output/ when flora.csv changed: the folder the nightly run commits to.

flora.csv is flora_store.export_csv: the stored snapshot plus any corrections saved
since, byte for byte what Download full CSV serves (the public API serves its
records from the same table). Written beside it: the release manifest and notes
for those bytes, and today's row in flora_history.csv.

The files that describe a build (the preparation report, the validation reports)
are written from the run given with --job, and only when that run built exactly
these bytes. Otherwise, and for the transform's own logs, which only a build
writes, they are removed: left in place they would describe another flora.csv.

Usage:
    python publish_flora.py --output-dir output
    python publish_flora.py --output-dir output --job 3f2c...   # with that run's report

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import flora_store
import release_helpers
import validate_flora
from console_encoding import use_utf8_output
from output_lock import output_directory_lock
from prepare_flora import (REPORT_JSON, REPORT_MARKDOWN, TRANSFORM_DIAGNOSTICS,
                           VALIDATION_FILES, publish_release, read_export,
                           record_local_history, write_network_validation, write_report)

load_dotenv()
use_utf8_output()

# Files describing one build. Kept only beside the flora.csv that build produced.
BUILD_FILES = (REPORT_JSON, REPORT_MARKDOWN, *VALIDATION_FILES, *TRANSFORM_DIAGNOSTICS)


def run_report(cur, job_id):
    """The preparation report of a successful website run, or None."""
    cur.execute("SELECT report_json FROM source_sync_jobs "
                "WHERE job_id = %s AND status = 'success'", (str(uuid.UUID(job_id)),))
    row = cur.fetchone()
    return row["report_json"] if row else None


def _write_build_files(staged, report):
    """The report and the validation reports, from the run's stored report."""
    write_report(staged, report)
    if report.get("structural"):
        (staged / "flora_validation.md").write_text(
            validate_flora.render(report["structural"]), encoding="utf-8")
    if report.get("network"):
        write_network_validation(staged, report["network"])


def publish(cur, output_dir, job_id=None) -> dict:
    """Write the stored dataset and its release files to `output_dir`."""
    if not flora_store.has_dataset(cur):
        raise ValueError("No FLoRA dataset is stored yet: run the pipeline first.")
    output_dir = Path(output_dir)
    # Read under the lock, so a publish waiting behind a build on the same folder
    # writes what is stored once it gets its turn, not what was stored before.
    with output_directory_lock(output_dir):
        # One snapshot for the revision and the rows (a REPEATABLE READ session):
        # the manifest's data version must describe exactly these bytes.
        _, stored_at, watermark = flora_store.dataset_revision(cur)
        payload = flora_store.export_csv(cur).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        report = run_report(cur, job_id) if job_id else None
        if report is None:
            described = "no run given" if not job_id else "not a successful run"
        elif (report.get("release") or {}).get("sha256") != digest:
            # A correction saved, or another run stored, since that run built it.
            report, described = None, "the run built a different flora.csv"
        else:
            described = "included"
        # Outside the staging folder: publish_release moves every file in it.
        candidate = output_dir / ".flora.publish.csv"
        candidate.write_bytes(payload)
        try:
            frame = read_export(candidate)
            with tempfile.TemporaryDirectory(prefix=".flora-release-", dir=output_dir) as staging:
                staged = Path(staging)
                history = output_dir / "flora_history.csv"
                if history.exists():
                    shutil.copyfile(history, staged / history.name)
                manifest = release_helpers.prepare_release(
                    candidate, staged, len(frame), list(frame.columns))
                release_helpers.add_data_version(staged, manifest, stored_at, watermark)
                record_local_history(staged, frame)
                if report:
                    _write_build_files(staged, report)
                written = {path.name for path in staged.iterdir() if path.is_file()}
                publish_release(candidate, staged, output_dir)
        finally:
            candidate.unlink(missing_ok=True)
        removed = []
        for name in BUILD_FILES:
            if name not in written and (output_dir / name).exists():
                (output_dir / name).unlink()
                removed.append(name)
    return {"rows": len(frame), "sha256": manifest["sha256"], "report": described,
            "removed": removed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--job", help="a successful website run whose report to include")
    args = parser.parse_args()
    if args.job:
        try:
            uuid.UUID(args.job)
        except ValueError:
            parser.error("--job must be a pipeline run id")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set in the environment or .env")
    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            result = publish(cur, args.output_dir, args.job)
    except ValueError as exc:
        raise SystemExit(str(exc))
    finally:
        conn.close()
    print(f"Wrote {args.output_dir / 'flora.csv'}: {result['rows']} rows, "
          f"sha256 {result['sha256'][:12]}. Preparation report: {result['report']}.")
    if result["removed"]:
        print("Removed, as they describe another build: " + ", ".join(result["removed"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
