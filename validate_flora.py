"""validate_flora.py — structural validation of the FLoRA dataset.

A Python port of R/validate_flora.R. Runs the same checks against the dataset this
project now builds itself, and emits the same Markdown report: one checkbox per
issue, so a resolved issue simply disappears on the next run and a false positive
can be ticked to suppress it.

WHAT CHANGED IN THE PORT
------------------------
`source` values. Both the supplied notebook labels and the website's registry keys
are accepted, and preserved reference rows
must not become validation failures merely because their labels predate the site.

Suppressions live in the same CSV the R version uses
(`output/flora_validation_suppressions.csv`), in the same shape, so a suppression
ticked under either implementation is honoured by both.

Usage:
    python validate_flora.py                     # report to stdout
    python validate_flora.py --output report.md
    python validate_flora.py --fail-on-issues    # exit 1 if anything is flagged

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
from extractor_vocab import REPLICATION_OUTCOMES

load_dotenv()
use_utf8_output()

ROOT = Path(__file__).parent
SUPPRESSIONS_FILE = ROOT / "output" / "flora_validation_suppressions.csv"

VALID_SOURCES = {
    "entry_sheet_replications", "entry_sheet_reproductions",
    "fred_replication_success", "score_2025", "validated",
    "COS", "SCORE", "replications", "reproductions",
    "openalex", "openalex_snapshot", "i4r",
}
VALID_TYPES = {"replication", "reproduction"}
URL_COLUMNS = ["url_r", "oa_url_o", "oa_url_r", "url_o"]

# Above this many issues the section is folded into a <details> block, so one noisy
# check cannot bury every other one.
DETAILS_THRESHOLD = 10

DOI_RE = re.compile(r"^10\.[0-9]{4,}/\S+$")
NA_STRINGS = {"NA", "N/A", "na", "n/a", "NULL", "None"}


def _blank(value) -> bool:
    return (value is None or (isinstance(value, float) and pd.isna(value))
            or not str(value).strip())


def _text(value) -> str:
    return "" if _blank(value) else str(value).strip()


def row_id(row) -> str:
    """`doi_o | doi_r-or-url_r`, the identifier the R report uses.

    Kept even though our rows now carry a real flora_id, because a suppression
    ticked against the R report has to keep matching. flora_id is shown beside it.
    """
    right = _text(row.get("doi_r")) or _text(row.get("url_r"))
    return f"{_text(row.get('doi_o'))} | {right}"


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein, as R's adist computes it."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _normalised_distance(a: str, b: str) -> float:
    return _edit_distance(a, b) / max(len(a), len(b), 1)


_REF_DOI_RE = re.compile(r"\bdoi:\s*10[./]\S+|https?://(dx\.)?doi\.org/10[./]\S+", re.I)
_REF_STUDY_RE = re.compile(r"study\s*\d+[a-z]?\b", re.I)


def _clean_ref(value: str) -> str:
    text = _text(value).lower()
    text = _REF_DOI_RE.sub("", text)
    text = _REF_STUDY_RE.sub("", text)
    return " ".join(text.split())


# ── suppressions ──────────────────────────────────────────────────────────────

def load_suppressions(path: Path = SUPPRESSIONS_FILE) -> set:
    """(check_name, item_id) pairs previously ticked as false positives."""
    if not path.exists():
        return set()
    out = set()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("check_name") and row.get("item_id"):
                out.add((row["check_name"].strip(), row["item_id"].strip()))
    return out


def item_id(label: str) -> str:
    """The stable part of an item label — everything before the first ': '."""
    text = label.strip()
    if text.startswith("`"):
        end = text.find("`", 1)
        return text[1:end] if end > 0 else text
    marker = text.find(": ")
    return text[:marker] if marker > 0 else text


# ── the checks ────────────────────────────────────────────────────────────────

def _check_na_strings(df):
    issues = []
    for _, row in df.iterrows():
        for column, value in row.items():
            if isinstance(value, str) and value.strip() in NA_STRINGS:
                issues.append(f"{row_id(row)}: {column}='{value.strip()}'")
    return issues


def _check_urls(df):
    issues = []
    for _, row in df.iterrows():
        for column in URL_COLUMNS:
            value = _text(row.get(column))
            if value and not value.lower().startswith("http"):
                issues.append(f"{row_id(row)}: {column}='{value}'")
    return issues


def _check_outcomes(df):
    from extractor_vocab import (normalize_axis_value, OUTCOME_COMPUTATION_VALUES,
                                 OUTCOME_ROBUSTNESS_VALUES)
    issues = []
    for _, row in df.iterrows():
        outcome = _text(row.get("outcome"))
        if not outcome:
            issues.append(f"{row_id(row)}: outcome is blank")
        elif row.get("type") == "replication" and outcome not in REPLICATION_OUTCOMES:
            issues.append(f"{row_id(row)}: type=replication; outcome='{outcome}'")
        elif row.get("type") == "reproduction":
            parts = [part.strip() for part in outcome.split(",")]
            try:
                valid = (len(parts) == 2
                         and normalize_axis_value("outcome_computation", parts[0]) in OUTCOME_COMPUTATION_VALUES
                         and normalize_axis_value("outcome_robustness", parts[1]) in OUTCOME_ROBUSTNESS_VALUES)
            except ValueError:
                valid = False
            if not valid:
                issues.append(f"{row_id(row)}: type=reproduction; outcome='{outcome}'")
    return issues


def _check_types(df):
    return [f"{row_id(r)}: type='{_text(r.get('type'))}'"
            for _, r in df.iterrows() if r.get("type") not in VALID_TYPES]


def _check_sources(df):
    return [f"{row_id(r)}: source='{_text(r.get('source'))}'"
            for _, r in df.iterrows() if r.get("source") not in VALID_SOURCES]


def _year(value):
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _check_year_ranges(df):
    current = datetime.now(timezone.utc).year
    issues = []
    for _, row in df.iterrows():
        for column in ("year_o", "year_r"):
            year = _year(row.get(column))
            if year is not None and (year < 1890 or year > current + 1):
                issues.append(f"{row_id(row)}: {column}={year}")
    return issues


def _check_year_order(df):
    issues = []
    for _, row in df.iterrows():
        year_o, year_r = _year(row.get("year_o")), _year(row.get("year_r"))
        if year_o and year_r and year_r < year_o:
            issues.append(f"{row_id(row)}: year_o={year_o}; year_r={year_r}")
    return issues


def _check_exact_duplicates(df):
    pairs = df[["doi_o", "doi_r"]].fillna("")
    duplicated = pairs.duplicated(keep=False) & (pairs["doi_o"] != "")
    return sorted({row_id(r) for _, r in df[duplicated].iterrows()})


def _check_required_fields(df):
    issues = []
    for _, row in df.iterrows():
        missing = [c for c in ("title_o", "title_r", "doi_o") if _blank(row.get(c))]
        if _blank(row.get("doi_r")) and _blank(row.get("url_r")):
            missing.append("doi_r or url_r")
        if missing:
            issues.append(f"{row_id(row)}: missing {', '.join(missing)}")
    return issues


def _check_doi_format(df):
    issues = []
    for _, row in df.iterrows():
        for column in ("doi_o", "doi_r"):
            value = _text(row.get(column))
            if value and not DOI_RE.match(value):
                issues.append(f"{row_id(row)}: {column}='{value}'")
    return issues


def _check_reference_conflicts(df):
    """The same DOI appearing with substantially different reference text."""
    issues = []
    for doi_column, ref_column in (("doi_o", "apa_ref_o"), ("doi_r", "apa_ref_r")):
        if ref_column not in df.columns:
            continue
        frame = df[[doi_column, ref_column]].dropna()
        for doi, group in frame.groupby(doi_column):
            refs = {_clean_ref(r) for r in group[ref_column] if _clean_ref(r)}
            if len(refs) < 2:
                continue
            refs = sorted(refs)
            worst = max(_normalised_distance(a, b)
                        for i, a in enumerate(refs) for b in refs[i + 1:])
            if worst > 0.2:
                issues.append(f"{doi}: {len(refs)} different reference strings "
                              f"({doi_column}/{ref_column})")
    return issues


CHECKS = [
    ("No 'NA'/'N/A' strings", "Literal 'NA'/'N/A' strings found", _check_na_strings,
     "A text column holds the literal string 'NA' instead of a missing value."),
    ("All links are valid URLs", "Invalid URLs (not starting with http)", _check_urls,
     "A url column holds something that is not a link."),
    ("Outcome values valid", "Invalid outcome values", _check_outcomes,
     "An outcome outside the allowed set for the row's type, or missing."),
    ("Type values valid", "Invalid type values", _check_types,
     "type must be 'replication' or 'reproduction'."),
    ("Source values valid", "Invalid source values", _check_sources,
     "source must identify a supported dataset input."),
    ("Year ranges reasonable", "Implausible years", _check_year_ranges,
     "year_o or year_r is outside 1890 to next year."),
    ("year_r >= year_o", "Replication year before original year", _check_year_order,
     "Could indicate swapped entries or a data-entry error."),
    ("No exact duplicates", "Exact duplicates (by doi_o + doi_r)",
     _check_exact_duplicates,
     "Rows sharing the same doi_o and doi_r after deduplication."),
    ("Required fields present", "Missing required fields", _check_required_fields,
     "title_o, title_r and doi_o must be present, plus doi_r or url_r."),
    ("DOI format valid", "Invalid DOI format", _check_doi_format,
     "DOI does not match 10.NNNN/..."),
    ("DOI mapped to conflicting references", "DOI with conflicting references",
     _check_reference_conflicts,
     "The same DOI appears with reference strings more than 20% apart."),
]


def validate(df, suppressions=None) -> dict:
    suppressions = suppressions or set()
    results, suppressed = [], 0

    for name, heading, fn, description in CHECKS:
        items = fn(df)
        kept = []
        for label in items:
            if (name, item_id(label)) in suppressions or (heading, item_id(label)) in suppressions:
                suppressed += 1
            else:
                kept.append(label)
        results.append({"name": name, "heading": heading, "description": description,
                        "items": kept, "passed": not kept})

    return {"results": results, "suppressed": suppressed,
            "has_issues": any(not r["passed"] for r in results), "rows": len(df)}


def render(report: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = [
        "## FLoRA Data Validation Report",
        "",
        f"_Generated: {stamp} UTC | Dataset: {report['rows']} rows_",
        "",
        "Each row is identified as `doi_o | doi_r (or url_r)`, followed by "
        "check-specific detail after `:`.",
        "Tick an item to mark it a false positive; it is suppressed on the next run.",
        "",
    ]

    failed = [r for r in report["results"] if not r["passed"]]
    passed = [r for r in report["results"] if r["passed"]]

    for result in failed:
        items = result["items"]
        n = len(items)
        lines = [f"- [ ] {label}" for label in items]
        if n <= DETAILS_THRESHOLD:
            out += [f"**{result['heading']}** ({n} issue{'s' if n != 1 else ''}):", "",
                    f"_{result['description']}_", ""] + lines + [""]
        else:
            out += [f"<details><summary><b>{result['heading']}</b> ({n} issues)</summary>",
                    "", f"_{result['description']}_", ""] + lines + ["", "</details>", ""]

    if passed:
        out += ["---", "", "**Checks passed:**"]
        out += [f"- [x] {r['name']}" for r in passed]
        out.append("")

    if report["suppressed"]:
        out.append(f"_({report['suppressed']} suppressed false positive"
                   f"{'s' if report['suppressed'] != 1 else ''} not shown)_")
    return "\n".join(out)


def load_dataset(cur):
    """The product as it is published, with the output contract's column names.

    Untitled rows are dropped here for the same reason the export drops them: the
    R notebook validates output/flora.csv, which has already been through Step 10.
    Reporting a row that was never published would put an item in the issue that
    nobody can resolve — it is already recorded in output/flora_export_log.csv and
    counted on the FLoRA tab.
    """
    import flora_registry
    import transform_sources
    frame = transform_sources.build(cur, verbose=False)
    frame = flora_registry.attach_ids(cur, frame)
    return transform_sources.drop_untitled(transform_sources.to_output_shape(frame))


def main() -> int:
    start_logging("validate")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="write the report here instead of stdout")
    parser.add_argument("--input", type=Path,
                        help="validate this exact CSV instead of rebuilding from the database")
    parser.add_argument("--fail-on-issues", action="store_true",
                        help="exit 1 when anything is flagged (for CI)")
    args = parser.parse_args()

    if args.input:
        df = pd.read_csv(args.input, dtype=str, keep_default_na=False,
                         na_values=["NA", ""], encoding="utf-8-sig")
    else:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            print("DATABASE_URL is not set", file=sys.stderr)
            return 2
        conn = psycopg2.connect(database_url)
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            df = load_dataset(cur)
        finally:
            conn.close()

    report = validate(df, load_suppressions())
    text = render(report)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"report written to {args.output}")
    else:
        print(text)

    failed = sum(1 for r in report["results"] if not r["passed"])
    total = sum(len(r["items"]) for r in report["results"])
    print(f"\n{failed}/{len(report['results'])} check(s) flagged {total} issue(s)",
          file=sys.stderr)
    return 1 if (args.fail_on_issues and report["has_issues"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
