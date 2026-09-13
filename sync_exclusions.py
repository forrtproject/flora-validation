"""sync_exclusions.py — keep transform_exclusions in step with the exclusions sheet.

Step 4 of the R notebook: papers that should not reach the FLoRA dataset — a link
that is not a report, a duplicate reached by another route, a study that replicates
correlations rather than claims. The notebook reads the sheet on every render.

Until this existed, `transform_exclusions` was EMPTY and the transform was excluding
nothing at all, so the published dataset carried rows the exclusions list had removed
from the R output for months.

THREE KINDS OF ROW, DISTINGUISHED BY added_by
---------------------------------------------
`sheet`     Mirrors the exclusions Google Sheet. Replaced wholesale on every run, so
            deleting a row from the sheet genuinely un-excludes the paper — the sheet
            is the authority for these, exactly as it is for the R pipeline.
`notebook`  The four exclusions hard-coded in the R notebook rather than in the
            sheet. Seeded here so they live somewhere visible and editable instead of
            inside a code block, and re-seeded every run so the set stays complete.
anything    Added by an admin through the app. NEVER touched by this sync: a decision
else        made here is not the sheet's to overrule.

Usage:
    python sync_exclusions.py
    python sync_exclusions.py --dry-run

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import yaml
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
from sync_sources import GateFailure, _assert_is_csv, _fetch, _parse

load_dotenv()
use_utf8_output()

ROOT = Path(__file__).parent
REGISTRY_PATH = ROOT / "sources.yml"

SHEET_SOURCE = "sheet"
NOTEBOOK_SOURCE = "notebook"
COS_SOURCE = "notebook-cos"

EXPECTED_COLUMNS = ["doi_r", "url_r", "reason"]

# Hard-coded in the R notebook's `manual_exclusion_dois` / `manual_exclusion_urls`
# rather than in the sheet. Kept verbatim, reasons included, so the list is auditable
# against the notebook it came from.
NOTEBOOK_EXCLUSIONS = [
    (None, "10.31234/osf.io/jfmsz", None, "Withdrawn paper"),
    (None, "10.1177/0956797619831612", None,
     "Replicates correlations, not claims (Soto 2019)"),
    (None, "korbmacher_2022", None, "Re-added with a proper identifier"),
    (None, None, "https://replications.clearerthinking.org/replication-2022psci33-8",
     "Re-added with a proper identifier"),
]

# Step 9b: COS entries a one-off LLM validation (issue #124) judged NOT to be genuine
# self-identified replications. Frozen in the notebook from the 2026-06-17 render
# rather than re-run, because the classification costs paid LLM calls and its answer
# does not change. Keyed on the PAIR — the same replication can be a valid entry
# against a different original.
COS_NON_REPLICATIONS = [
    ("10.1016/j.cognition.2014.09.006", "10.1007/s00213-019-05314-z"),
    ("10.1016/j.jml.2010.11.002",       "10.3758/s13421-019-00899-4"),
    ("10.1016/j.neuron.2006.03.036",    "10.1093/geronb/gbt044"),
    ("10.1037/0022-3514.71.2.230",      "10.1037/0022-3514.83.2.406"),
    ("10.1037/0022-3514.71.2.230",      "10.1037/0022-3514.90.6.893"),
    ("10.1037/0022-3514.94.1.116",      "10.1177/0146167213510746"),
    ("10.1037/0096-3445.136.2.241",     "10.1186/2050-7283-1-22"),
    ("10.1037/a0019337",                "10.3758/s13421-019-00958-w"),
    ("10.1080/13506280544000110",       "10.1016/j.actpsy.2020.103138"),
    ("10.1080/135062899395000",         "10.1080/17470218.2012.699077"),
    ("10.1111/j.1467-7687.2009.00859.x", "10.1037/a0024923"),
    ("10.1111/j.1467-9280.2007.02004.x", "10.1371/journal.pone.0096339"),
    ("10.1177/0146167216684132",        "10.1525/collabra.185"),
    ("10.1177/0956797611435919",        "10.1177/0956797613486983"),
    ("10.1177/0956797617721270",        "10.3758/s13414-019-01723-6"),
    ("10.1523/jneurosci.3427-13.2014",  "10.1093/cercor/bhx353"),
]

COS_EXCLUSIONS = [
    (doi_o, doi_r, None, "COS entry judged not a self-identified replication (issue #124)")
    for doi_o, doi_r in COS_NON_REPLICATIONS
]


def _s(value) -> "str | None":
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value).strip()
    return text or None


def load_registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def sheet_url(registry: dict) -> str:
    config = registry.get("exclusions") or {}
    if not config.get("gid"):
        raise GateFailure("sources.yml has no exclusions.gid")
    document = config.get("document") or registry["document"]
    return registry["url_template"].format(document=document, gid=config["gid"])


def fetch_rows(url: str) -> list:
    payload = _fetch(url)
    _assert_is_csv(payload)
    frame = _parse(payload)

    missing = [c for c in EXPECTED_COLUMNS if c not in frame.columns]
    if missing:
        raise GateFailure(f"exclusions sheet is missing column(s): {', '.join(missing)}")

    rows = []
    for _, raw in frame.iterrows():
        doi_r, url_r = _s(raw.get("doi_r")), _s(raw.get("url_r"))
        # The sheet carries trailing blank rows; a row with neither identifier
        # excludes nothing and would violate the table's CHECK.
        if not doi_r and not url_r:
            continue
        rows.append((None, doi_r, url_r, _s(raw.get("reason")) or "no reason given"))
    return rows


def _replace(cur, added_by: str, rows: list) -> dict:
    """Delete-then-insert for one provenance, inside the caller's transaction.

    Replaced wholesale rather than merged: removing a row from the sheet has to
    un-exclude the paper, and a merge cannot express a deletion.
    """
    cur.execute("SELECT COUNT(*) AS n FROM transform_exclusions WHERE added_by = %s",
                (added_by,))
    before = cur.fetchone()["n"]

    cur.execute("DELETE FROM transform_exclusions WHERE added_by = %s", (added_by,))
    if rows:
        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO transform_exclusions (doi_o, doi_r, url_r, reason, added_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(doi_o, doi_r, url_r, reason, added_by)
             for doi_o, doi_r, url_r, reason in rows],
        )
    return {"before": before, "after": len(rows)}


def sync(cur, dry_run: bool = False, verbose: bool = True) -> dict:
    def say(*args):
        if verbose:
            print(*args)

    registry = load_registry()
    rows = fetch_rows(sheet_url(registry))
    say(f"  sheet rows: {len(rows)}")

    cur.execute(
        # NULL added_by counts as app-added: it predates this script, so it is not
        # ours to replace.
        "SELECT COUNT(*) AS n FROM transform_exclusions "
        "WHERE added_by IS NULL OR NOT (added_by = ANY(%s))",
        ([SHEET_SOURCE, NOTEBOOK_SOURCE, COS_SOURCE],),
    )
    manual = cur.fetchone()["n"]
    say(f"  added in the app (left alone): {manual}")

    if dry_run:
        say("\n[dry-run] nothing written")
        return {"sheet": len(rows), "notebook": len(NOTEBOOK_EXCLUSIONS),
                "cos": len(COS_EXCLUSIONS), "app": manual, "written": False}

    sheet_stats = _replace(cur, SHEET_SOURCE, rows)
    _replace(cur, NOTEBOOK_SOURCE, NOTEBOOK_EXCLUSIONS)
    _replace(cur, COS_SOURCE, COS_EXCLUSIONS)

    say(f"  sheet exclusions: {sheet_stats['before']} -> {sheet_stats['after']}")
    say(f"  notebook exclusions: {len(NOTEBOOK_EXCLUSIONS)}")
    say(f"  COS non-replications: {len(COS_EXCLUSIONS)}")
    return {"sheet": len(rows), "notebook": len(NOTEBOOK_EXCLUSIONS),
            "cos": len(COS_EXCLUSIONS), "app": manual, "written": True}


def main() -> int:
    start_logging("sync-exclusions")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change; write nothing")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    print("=== exclusions sync ===")
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            stats = sync(cur, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
    except GateFailure as exc:
        conn.rollback()
        print(f"  FAILED - {exc}")
        print("    existing exclusions left untouched")
        return 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    total = stats["sheet"] + stats["notebook"] + stats["cos"] + stats["app"]
    print(f"\n  {total} exclusion(s) active ({stats['sheet']} sheet, "
          f"{stats['notebook']} notebook, {stats['cos']} COS pairs, {stats['app']} app)")
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
