"""
transform_sources.py — turn reviewed source_records into the FLoRA dataset.

Stage 11 of the entry-sheet pipeline. source_records is a *record*: what the
sheet said, plus what a reviewer fixed, stored dirty on purpose. This produces
the *product*: cleaned, deduplicated, narrowed to the columns FLoRA consumes.

The script is a pure function of the database — it writes nothing back. That
matters because the sync is insert-only: a row is written once and never
updated, so cleaning at ingest would freeze today's rules into every existing
row forever. Here, improving a rule means editing it and re-running, and all
rows get the new behaviour immediately.

Six operations:
  1. normalise/derive the outcome (reproductions derive it from their two axes)
  2. clean DOIs                 (prefixes, whitespace, trailing garbage)
  3. strip redundant url_r      (~86% are just doi.org/<doi_r>)
  4. apply exclusions           (transform_exclusions table)
  5. dedup                      (reviewer decisions + identifier collapse)
  6. project to the FLoRA column set

Usage:
    python transform_sources.py
    python transform_sources.py --output output/flora_entry_sheets.csv
    python transform_sources.py --stats-only

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from console_encoding import use_utf8_output
from extractor_vocab import (
    REPLICATION_OUTCOMES,
    derive_reproduction_outcome,
    normalize_axis_value,
)

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

ROOT = Path(__file__).parent
DEFAULT_OUTPUT = ROOT / "output" / "flora_entry_sheets.csv"

# The column set the FLoRA pipeline works with (flora_cols in the R notebook).
FLORA_COLUMNS = [
    "doi_o", "ref_o", "url_o",
    "doi_r", "ref_r", "url_r",
    "abstract_r",
    "outcome", "outcome_quote", "outcome_quote_source",
    "type", "source",
    "alt_identifier_o", "alt_identifier_r",
    # Reproductions only; NULL on replications. The independent coded axes and
    # their evidence are exported alongside the derived flat outcome. New columns
    # go LAST so positional readers of the existing export keep working.
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
]

# Placeholder DOIs used upstream for papers with no real identifier (book
# chapters and the like). Kept through the join, stripped before output.
DUMMY_DOI_RE = re.compile(r"^dummy[_\-]", re.I)


def clean_doi(value):
    """Normalise a scraped DOI to a bare '10.…' form.

    Real garbage this handles, taken from the live data:
        '10.1002/pits.22106digital object identifier (doi)'
        'https://doi.org/10.1016/0010-0285(92)90013-R'
        '10.1016/j.learninstruc.2018.04.010 get rights and content'
    DOIs cannot contain whitespace, so everything from the first space is dropped.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    v = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", v)
    v = re.sub(r"^doi:\s*", "", v)
    v = re.sub(r"\s*digital\s*object\s*identifier.*$", "", v)
    v = re.sub(r"\s*get\s*rights\s*and\s*content.*$", "", v)
    v = re.sub(r"\s.*$", "", v).strip()
    return v or None


def norm_url(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    v = str(value).strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    v = v.rstrip("/")
    return v or None


def strip_redundant_url(row):
    """Drop url_r when it only restates doi_r.

    In FLoRA, url_r should mean 'a link to something that isn't the DOI' — an OSF
    page, a report. 1,403 of the replication rows carry https://doi.org/<doi_r>,
    which is the DOI written twice.
    """
    doi, url = row.get("doi_r"), row.get("url_r")
    if not doi or not url:
        return url
    return None if clean_doi(doi) == clean_doi(url) else url


def load(cur):
    """Reviewer-edited rows. Anything ruled a duplicate is left behind here."""
    cur.execute(
        """
        SELECT record_id::text AS record_id, display_id, source, type,
               doi_o, ref_o, url_o, doi_r, ref_r, url_r, abstract_r,
               outcome, outcome_quote, out_quote_source,
               outcome_computation, outcome_computational_quote, out_quote_computational_source,
               outcome_robustness, outcome_robustness_quote, out_quote_robust_source,
               study_o, alt_identifier_o, alt_identifier_r,
               validation_status, reviewed_by, duplicate_status
        FROM source_records
        WHERE duplicate_status IS DISTINCT FROM 'duplicate'
        ORDER BY display_id
        """
    )
    return pd.DataFrame([dict(r) for r in cur.fetchall()])


def load_rules(cur):
    cur.execute("SELECT raw_value, canonical_value FROM outcome_alias")
    aliases = {r["raw_value"]: r["canonical_value"] for r in cur.fetchall()}

    cur.execute("SELECT doi_r, url_r, reason FROM transform_exclusions")
    exclusions = [dict(r) for r in cur.fetchall()]
    return aliases, exclusions


def _canonical_axis(row, column, problems):
    """Canonical axis value for export, recording rather than hiding bad data."""
    if row.get("type") != "reproduction":
        return None
    try:
        return normalize_axis_value(column, row.get(column))
    except ValueError:
        problems.setdefault("invalid_axis", []).append(
            (row.get("display_id"), column, row.get(column))
        )
        return None


def derive_outcome(row, aliases, problems):
    """The `outcome` string for a row, or None when the row has none.

    Replications carry `outcome` and only need spelling normalised.

    Reproduction axes remain the authoritative coded fields. The flat outcome is
    deterministically derived from their settled 4×3 grid for compatibility with
    the extractor schema; an incomplete or undetermined pair is represented as
    ``cannot_be_determined``.

    Nothing unrecognised is passed through. An outcome spelling absent from
    outcome_alias used to fall through to the export verbatim, which is how a
    vocabulary change reaches published data without anyone noticing — the same
    silent-drift failure that cost five weeks on the extractor side (see
    extractor_vocab.py). Unknown values are collected and reported instead, and
    run() refuses to write once any turned up.
    """
    if row["type"] == "reproduction":
        computation = _canonical_axis(row, "outcome_computation", problems)
        robustness = _canonical_axis(row, "outcome_robustness", problems)
        return derive_reproduction_outcome(
            computation, robustness
        )
    raw = row.get("outcome")
    if not raw:
        return None
    if raw not in aliases:
        problems["unknown_alias"].append((row["display_id"], raw))
        return None
    canonical = aliases[raw]
    # A bad alias row is as damaging as a missing one: it silently republishes a
    # real outcome as the wrong category. Catch a canonical value the rest of the
    # app would reject.
    if canonical not in REPLICATION_OUTCOMES:
        problems["bad_alias"].append((row["display_id"], raw, canonical))
        return None
    return canonical


def _join_parts(*values):
    """Join the non-empty values with ' || ', dropping blanks and repeats.

    `if p` is NOT a sufficient emptiness test here: df.apply(axis=1) hands back
    the column's NA sentinel, and float('nan') is truthy while str(nan) == 'nan'
    — which is how the literal string "nan" ends up in a shipped dataset. Use
    pd.isna, and de-duplicate so an identical quote on both dimensions is not
    emitted twice.
    """
    parts = []
    for v in values:
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            continue
        text = str(v).strip()
        if text and text.lower() != "nan" and text not in parts:
            parts.append(text)
    return " || ".join(parts) if parts else None


def derive_quote(row):
    """Reproductions have two quotes; join them rather than picking one, so no
    coder's text is silently dropped."""
    if row["type"] != "reproduction":
        return row.get("outcome_quote")
    return _join_parts(row.get("outcome_computational_quote"),
                       row.get("outcome_robustness_quote"))


def derive_quote_source(row):
    if row["type"] != "reproduction":
        return row.get("out_quote_source")
    return _join_parts(row.get("out_quote_computational_source"),
                       row.get("out_quote_robust_source"))


def run(output: Path, stats_only: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("ERROR: DATABASE_URL must be set in environment or .env")

    conn = psycopg2.connect(database_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        df = load(cur)
        aliases, exclusions = load_rules(cur)
    finally:
        conn.close()

    print("=== FLoRA entry-sheet transform ===")
    if df.empty:
        print("No rows to transform.")
        return
    print(f"  loaded: {len(df)} rows "
          f"({(df['type'] == 'replication').sum()} replication, "
          f"{(df['type'] == 'reproduction').sum()} reproduction)")

    # 1 ── outcome
    problems = {"unknown_alias": [], "bad_alias": [], "invalid_axis": []}
    for axis in ("outcome_computation", "outcome_robustness"):
        df[axis] = df.apply(lambda r, col=axis: _canonical_axis(r, col, problems), axis=1)
    df["outcome"] = df.apply(lambda r: derive_outcome(r, aliases, problems), axis=1)
    df["outcome_quote"] = df.apply(derive_quote, axis=1)
    df["outcome_quote_source"] = df.apply(derive_quote_source, axis=1)
    n_repro = int((df["type"] == "reproduction").sum())
    print(f"  outcome normalized/derived; {df['outcome'].notna().sum()} row(s) have one")
    if n_repro:
        print(f"  {n_repro} reproduction row(s) carry two authoritative axes")

    # 2 ── DOIs
    before_o, before_r = df["doi_o"].copy(), df["doi_r"].copy()
    df["doi_o"] = df["doi_o"].apply(clean_doi)
    df["doi_r"] = df["doi_r"].apply(clean_doi)
    changed = int(((before_o != df["doi_o"]) & before_o.notna()).sum() +
                  ((before_r != df["doi_r"]) & before_r.notna()).sum())
    print(f"  DOIs cleaned: {changed} value(s) changed")

    # 3 ── exclusions
    # Runs BEFORE url_r is stripped: an operator registering an exclusion copies
    # the URL as it appears in the review UI, and stripping first would leave
    # nothing for a url-keyed exclusion to match.
    excl_dois = {clean_doi(e["doi_r"]) for e in exclusions if e["doi_r"]}
    excl_urls = {norm_url(e["url_r"]) for e in exclusions if e["url_r"]}
    if excl_dois or excl_urls:
        mask = (df["doi_r"].apply(clean_doi).isin(excl_dois - {None}) |
                df["url_r"].apply(norm_url).isin(excl_urls - {None}))
        print(f"  excluded: {int(mask.sum())} row(s) via transform_exclusions")
        df = df[~mask]
    else:
        print("  excluded: 0 (transform_exclusions is empty)")

    # 4 ── redundant url_r
    before_urls = df["url_r"].notna().sum()
    df["url_r"] = df.apply(strip_redundant_url, axis=1)
    print(f"  redundant url_r stripped: {before_urls - df['url_r'].notna().sum()}")

    # 5 ── dedup
    # Rows a reviewer explicitly ruled 'distinct' are exempt: they share
    # identifiers but were judged to be different records, and an automatic
    # collapse would silently overrule that decision.
    before = len(df)

    # coalesce(doi_r, url_r) — NOT a concatenation. Concatenating means adding an
    # unrelated OSF link to one of two otherwise identical rows changes its key,
    # so genuine duplicates escape. This matches _fingerprint() in sync_sources.py,
    # which the duplicate detector already uses.
    right = df["doi_r"].apply(clean_doi).fillna(df["url_r"].apply(norm_url)).fillna("")
    # `type` belongs in the key: it is one of the 14 output columns, so a
    # replication and a reproduction of the same paper are not interchangeable
    # and collapsing them silently deletes the reproduction.
    key = df["type"].fillna("") + "|" + df["doi_o"].fillna("") + "|" + right

    exempt = df["duplicate_status"].eq("distinct")
    collides = key.duplicated() & key.ne("||")
    df = df[~(collides & ~exempt)]
    rescued = int((collides & exempt).sum())
    print(f"  deduplicated: {before - len(df)} row(s) collapsed"
          + (f"; {rescued} kept as reviewer-confirmed distinct" if rescued else ""))

    # 6 ── project
    df["source"] = df["source"].replace({"replications": "entry_sheet_replications",
                                         "reproductions": "entry_sheet_reproductions"})
    out = df.reindex(columns=FLORA_COLUMNS)

    # DUMMY_* placeholders keyed manual references upstream; they are not DOIs.
    for col in ("doi_o", "doi_r"):
        dummy = out[col].astype(str).str.match(DUMMY_DOI_RE, na=False)
        if dummy.any():
            print(f"  stripped {int(dummy.sum())} DUMMY_* placeholder(s) from {col}")
            out.loc[dummy, col] = None

    print(f"\n  final: {len(out)} rows × {len(out.columns)} columns")

    unknown_alias = problems["unknown_alias"]
    bad_alias = problems["bad_alias"]
    invalid_axis = problems["invalid_axis"]
    missing = int(out["outcome"].isna().sum())
    if missing:
        invalid_rows = {display_id for display_id, _, _ in invalid_axis}
        blank = missing - len(unknown_alias) - len(bad_alias) - len(invalid_rows)
        if blank > 0:
            print(f"  ⚠ {blank} row(s) have no outcome — blank or incomplete in the source sheet")

    print("\n  outcome distribution:")
    for value, n in out["outcome"].value_counts(dropna=True).items():
        print(f"      {str(value):55} {n}")

    # An unrecognised spelling is not a gap to fill later — it means a real outcome
    # would be published as blank, or (worse, before this check) verbatim and
    # unrecognised. Report every one, then refuse to write: a partial export that
    # looks complete is what makes this class of bug expensive.
    #
    # Reproduction outcomes bypass aliases because they are derived from axes,
    # which are validated by database constraints on the way in.
    if unknown_alias or bad_alias or invalid_axis:
        print()
        if unknown_alias:
            print(f"  ✗ {len(unknown_alias)} row(s) carry an outcome spelling absent from outcome_alias:")
            for display_id, raw in unknown_alias[:10]:
                print(f"      {display_id}: {raw!r}")
            print("    → add a row to outcome_alias mapping it to a canonical value, and re-run")
        if bad_alias:
            print(f"  ✗ {len(bad_alias)} row(s) alias onto a value the app does not accept:")
            for display_id, raw, canonical in bad_alias[:10]:
                print(f"      {display_id}: {raw!r} → {canonical!r}")
            print("    → fix the outcome_alias row, or add the value to "
                  "extractor_vocab.REPLICATION_OUTCOMES and the CHECK constraint")
        if invalid_axis:
            print(f"  INVALID: {len(invalid_axis)} reproduction axis value(s):")
            for display_id, column, raw in invalid_axis[:10]:
                print(f"      {display_id}: {column}={raw!r}")
            print("    correct the source_record value to a codebook category, and re-run")
        problem_count = len(unknown_alias) + len(bad_alias) + len(invalid_axis)
        raise ValueError(
            f"{problem_count} outcome or axis value(s) are not recognised; "
            f"refusing to produce an export. "
            f"Raised under --stats-only too, so a dry run reports the problem "
            f"rather than passing."
        )

    if stats_only:
        print("\n[stats-only] nothing written")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False, encoding="utf-8")
    print(f"\n  saved: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transform source_records into the FLoRA column set")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output CSV path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--stats-only", action="store_true",
                        help="Report what would be produced without writing.")
    args = parser.parse_args()

    run(args.output, stats_only=args.stats_only)
