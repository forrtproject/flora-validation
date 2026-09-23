"""
transform_sources.py — turn reviewed source_records into the FLoRA dataset.

Stage 11 of the entry-sheet pipeline. source_records is a *record*: what the
sheet said, plus what a reviewer fixed, stored dirty on purpose. This produces
the *product*: cleaned, deduplicated, narrowed to the columns FLoRA consumes.

The build() function derives the product without changing source data. Improving
a cleaning rule and re-running applies the new behaviour to every row. Normal
CLI exports also register permanent publication IDs before writing the CSV;
--stats-only keeps the whole operation read-only.

Seven operations:
  1. normalise/derive the outcome (reproductions derive it from their two axes)
  2. clean DOIs                 (prefixes, whitespace, trailing garbage)
  3. strip redundant url_r      (~86% are just doi.org/<doi_r>)
  4. apply exclusions           (transform_exclusions table)
  5. dedup                      (reviewer decisions + identifier collapse)
  6. join cached bibliographic metadata (enrich_works.py)
  7. project to the FLoRA output contract

Usage:
    python transform_sources.py
    python transform_sources.py --output output/flora_entry_sheets.csv
    python transform_sources.py --stats-only

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import hashlib
import html
import os
import re
import sys
from html.entities import html5
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
import apa_references
import author_overlap
import cos_quote_rewrite
import preprint_dedup
import final_export
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

# The FLoRA *output* contract — `output_cols` in the R notebook, the shape of
# flora.csv. FLORA_COLUMNS above is the notebook's *input* selection (`flora_cols`);
# this is what it emits after enrichment.
#
# 15 of these we produce ourselves. The other 20 are enrichment the R pipeline fetches
# from CrossRef (title/author/journal/year/volume/issue/pages/bibtex), OpenAlex
# (language) and Unpaywall (oa_url). They are emitted as EMPTY columns rather than
# omitted, so the file has the right shape and the pipeline's left_join/coalesce steps
# fill them — an absent column would make those joins fail, a blank one just gets
# filled.
FLORA_OUTPUT_COLUMNS = [
    "doi_o", "alt_identifier_o", "doi_o_hash", "title_o", "author_o", "journal_o",
    "year_o", "volume_o", "issue_o", "pages_o", "apa_ref_o", "bibtex_ref_o",
    "url_o", "language_o",
    "doi_r", "alt_identifier_r", "doi_r_hash", "title_r", "author_r", "journal_r",
    "year_r", "volume_r", "issue_r", "pages_r", "apa_ref_r", "bibtex_ref_r",
    "url_r", "language_r",
    "oa_url_o", "oa_url_r",
    "outcome", "outcome_quote", "outcome_quote_source", "type", "source",
]

# Filled from work_metadata by the enrichment join in build(). Named here so the
# projection inside build() keeps them — reindexing to FLORA_COLUMNS alone silently
# dropped every one of them before to_output_shape could ever see them.
ENRICHMENT_COLUMNS = [
    f"{field}_{side}"
    for side in ("o", "r")
    for field in ("title", "author", "journal", "year", "volume", "issue",
                  "pages", "language", "oa_url", "oa_work_id", "bibtex_ref")
]

# Computed by build() rather than read or fetched. Named here for the same reason
# ENRICHMENT_COLUMNS is: the projection at the end of build() reindexes to an
# explicit list, so a column missing from it is silently dropped before
# to_output_shape can ever see it.
DERIVED_COLUMNS = ["author_overlap", "author_overlap_pct"]

# Our columns under the output contract's names.
OUTPUT_RENAMES = {"ref_o": "apa_ref_o", "ref_r": "apa_ref_r"}

# The admin grid retains source registry keys; the published CSV uses the names
# from the supplied preparation notebook and reference snapshot.
OUTPUT_SOURCES = {
    "entry_sheet_replications": "replications",
    "entry_sheet_reproductions": "reproductions",
    "fred_replication_success": "COS",
    "score_2025": "SCORE",
}

# Kept AFTER the 35 rather than dropped. The output contract has no home for them,
# but abstract_r feeds downstream classification and the reproduction axes are the
# authoritative coded fields the flat `outcome` is derived FROM — discarding either
# to match a column list exactly would lose data the database is the only copy of.
# Readers select by name, so trailing columns cost nothing.
#
# oa_work_id_* is OpenAlex's own identifier for the paper ('W2168190474'), which is
# what lets a row be looked up in OpenAlex without a DOI round-trip. enrich_works.py
# has always stored it; until now nothing joined it in, so it sat in the cache while
# the product carried only oa_url_*, the open-access LINK. Those are different things:
# a paper has a work id whether or not anyone has posted a free copy of it.
OUTPUT_EXTRAS = [
    "oa_work_id_o", "oa_work_id_r",
    # How many family names the two author lists share, and that as a percentage of
    # the replication team. A replication by the original authors is a different
    # kind of evidence from one by strangers, and nothing else in the output says so.
    "author_overlap", "author_overlap_pct",
    "abstract_r",
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
]


# Step 9d of the R notebook. Replication papers that aggregate many studies: a
# reference to one of these alone does not tell a reader which of the aggregated
# studies a row is about, so the row's own url_r is appended to the reference.
#
# Kept as a list here, as the notebook keeps it: it is a curated judgement, not
# something a sheet supplies. The candidate warning below is what keeps it current.
META_PAPER_DOIS = {
    "10.1038/s41562-024-02062-9",   # Holzmeister et al. 2024
    "10.3389/fcomm.2022.1048896",   # 10 cases of sensory research
    "10.1073/pnas.2103313118",      # scarcity effects
    "10.1007/s13164-018-0400-9",    # X-PHI
    "10.1038/s41562-018-0399-z",    # Camerer 2018
    "10.1098/rsos.231240",          # Boyce
    "10.1027/1864-9335/a000178",    # ML1
    "10.1177/2515245918810225",     # ML2
    "10.1016/j.jesp.2015.10.012",   # ML3
    "10.1126/science.aac4716",      # RP:P
    "10.1177/08902070221094216",    # SVO development
    "10.1126/science.aaf0918",      # Camerer 2016
}

META_PAPER_NOTE = "Individual report available at"

# A replication DOI carrying this many distinct url_r values is probably a
# meta-paper nobody has classified yet.
META_PAPER_CANDIDATE_URLS = 5

# JATS/HTML markup that reaches us inside titles and abstracts: <i>, <sup>, <scp>,
# and whole <mml:math> blocks from publisher XML.
_TAG_RE = re.compile(r"<[^>]+>")
# A single newline inside a paragraph is a PDF copy artefact — the sentence simply
# continued. Two or more are a real paragraph break and are preserved.
_SOFT_BREAK_RE = re.compile(r"(?<!\n)\n(?!\n)")
_WS_RE = re.compile(r"[ \t]+")


def _s(value) -> str:
    """Stripped string, with NaN/None as empty. Used by the reporting paths."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


_ENTITY_RE = re.compile(r"&[A-Za-z]{2,8};")


def _lower_unknown_entity(match):
    """`&Amp;` -> `&amp;`, but `&Auml;` left exactly as it is.

    Lowercasing every entity is NOT safe: 375 of the HTML5 named entities differ
    only by case and mean different characters. `&Auml;` is Ä and `&auml;` is ä, so
    a blanket lowercase turns Ängström into ängström and, in a statistics title,
    &Delta; (Δ) into δ and &Omega; (Ω) into ω. The capitalised forms CrossRef
    actually mis-emits — `&Amp;`, `&Rsquo;` — are not entities under any casing,
    which is what separates them from the ones that must be left alone.
    """
    name = match.group(0)[1:]                 # "Amp;" / "Auml;"
    if name in html5 or name.lower() not in html5:
        return match.group(0)
    return match.group(0).lower()


def clean_text(value):
    """Strip markup, decode entities, and repair PDF-copied line breaks.

    Step 7c of the R notebook. Runs before anything compares strings, so a title
    differing only by an `<i>` is not mistaken for a different paper.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value)
    if not text.strip():
        return None
    text = _TAG_RE.sub(" ", text)
    # Unrecognised capitalised entities are lowercased before unescaping: CrossRef
    # returns forms ("&Amp;", "&Rsquo;") that html.unescape does not recognise, and
    # which then reach the published reference verbatim. Only an entity that is not
    # itself a real one is touched — see _lower_unknown_entity.
    text = _ENTITY_RE.sub(_lower_unknown_entity, text)
    text = html.unescape(text)
    # A second pass: publisher XML double-encodes, so &amp;lt;i&amp;gt; only becomes
    # a literal tag after the first unescape.
    if "<" in text and ">" in text:
        text = _TAG_RE.sub(" ", text)
    text = text.replace("\xa0", " ")
    text = _SOFT_BREAK_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    text = "\n\n".join(part.strip() for part in text.split("\n\n"))
    return text.strip() or None


def drop_untitled(frame):
    """Rows with a title on both sides — the notebook's Step 10 filter.

    Shared by the export and by validate_flora's load_dataset so that what ships
    and what gets validated are the same set of rows. Two copies of this predicate
    would drift, and the report would then flag rows nobody can act on because they
    were never published.
    """
    if not {"title_o", "title_r"} <= set(frame.columns):
        return frame
    return frame[frame["title_o"].map(lambda value: bool(_s(value)))
                 & frame["title_r"].map(lambda value: bool(_s(value)))]


def missing_title_report(frame):
    """Rows the R notebook's Step 10 drops for want of a title, with the reason.

    Returned rather than written here so the caller decides where it goes — and so
    the report exists even when the filter itself is off.
    """
    def _blank(value):
        return value is None or (isinstance(value, float) and pd.isna(value)) \
            or not str(value).strip()

    rows = []
    for _, row in frame.iterrows():
        no_o, no_r = _blank(row.get("title_o")), _blank(row.get("title_r"))
        if not (no_o or no_r):
            continue
        if no_o and no_r:
            reason = "missing both title_o and title_r"
        elif no_o:
            reason = "missing title_o"
        else:
            reason = "missing title_r"
        rows.append({
            "reason": reason,
            "flora_id": row.get("flora_id"),
            "source": row.get("source"),
            "doi_o": row.get("doi_o"),
            "apa_ref_o": row.get("ref_o"),
            "doi_r": row.get("doi_r"),
            "url_r": row.get("url_r"),
            "apa_ref_r": row.get("ref_r"),
        })
    return pd.DataFrame(rows)


def doi_hash(value):
    """First 3 hex chars of the DOI's md5 — `substr(openssl::md5(doi), 1, 3)` in R.

    A privacy-preserving bucket for API lookup, not an identifier: three characters
    is 4,096 buckets, so it is deliberately lossy.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:3]


def to_output_shape(frame, keep_extras: bool = True):
    """Project a built frame onto the FLoRA output contract.

    Columns we cannot fill are created empty rather than omitted — see
    FLORA_OUTPUT_COLUMNS.
    """
    out = frame.rename(columns=OUTPUT_RENAMES).copy()
    out["doi_o_hash"] = out["doi_o"].apply(doi_hash)
    out["doi_r_hash"] = out["doi_r"].apply(doi_hash)
    if "source" in out:
        out["source"] = out["source"].replace(OUTPUT_SOURCES)

    out = final_export.with_identity(out)
    columns = final_export.IDENTITY_COLUMNS + list(FLORA_OUTPUT_COLUMNS)
    if keep_extras:
        columns += [c for c in OUTPUT_EXTRAS if c in out.columns]
        columns += [c for c in PROVENANCE_COLUMNS if c in out.columns]
    return out.reindex(columns=columns)


# Traceability columns appended to the FLoRA set. Appended, never inserted: the
# comment above is explicit that positional readers of the existing export must keep
# working, and flora_id arriving first would break every one of them.
PROVENANCE_COLUMNS = ["flora_id", "source_display_id", "source_record_id"]

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


_SHEET_YEAR_RE = re.compile(r"^(1[5-9]\d{2}|2\d{3})(?:\.0+)?$")


def _sheet_year(value):
    """A hand-entered year_r from the sheet, or None if it is not one.

    244 of the stored values read "2020.0": the column travelled through a float
    somewhere in the sheet import, and publishing that spelling beside OpenAlex's
    "2020" would put two formats in one column. Anything that is not a plausible
    four-digit year is dropped rather than guessed at — this is a fallback for rows
    the DOI lookup could not answer, not a second source of truth.
    """
    text = _s(value)
    match = _SHEET_YEAR_RE.match(text)
    return match.group(1) if match else None


_DOI_URL_RE = re.compile(r"^10\.\d{4,}/")


def _doi_url(value):
    """The DOI's landing page, which R's formatted metadata carries, or None.

    Normalises through _s() rather than truth-testing the cell. pandas 3 hands a
    missing str value to .map() as a float NaN, and NaN is TRUTHY, so a bare
    `if value` guard let it through to re.match and took the whole build down
    with a TypeError on every row that has no original DOI.
    """
    doi = _s(value)
    return "https://doi.org/" + doi if _DOI_URL_RE.match(doi) else None


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
               year_r,
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

    cur.execute("SELECT doi_o, doi_r, url_r, reason FROM transform_exclusions")
    exclusions = [dict(r) for r in cur.fetchall()]
    return aliases, exclusions


def load_dedup_decisions(cur):
    """Admin rulings on preprint duplicate pairs, made in the FLoRA tab.

    Guarded: the nightly job runs from the repository and can reach the database
    before the deployed site's init_db() has created the table.
    """
    cur.execute("SELECT to_regclass('preprint_dedup_decisions') IS NOT NULL AS present")
    if not cur.fetchone()["present"]:
        return []
    cur.execute("SELECT side, doi_1, doi_2, action, decided_at FROM preprint_dedup_decisions")
    return [dict(r) for r in cur.fetchall()]


def load_cross_type_duplicates(cur):
    """Rows ruled a duplicate of a row of the OTHER type, which load() leaves out.

    The Source Records duplicate detector keys on the paper, not the type, so a
    replication and a reproduction of one paper arrive there as a duplicate group.
    Usually both are valid records; a 'duplicate' ruling between them removes one
    from FLoRA. Listed so every run surfaces them for a second look.
    """
    cur.execute(
        """
        SELECT d.display_id, d.type, d.doi_o, d.doi_r, d.url_r,
               s.display_id AS duplicate_of, s.type AS duplicate_of_type,
               d.duplicate_reviewed_by
        FROM source_records d
        JOIN source_records s ON s.record_id = d.duplicate_of
        WHERE d.duplicate_status = 'duplicate' AND d.type IS DISTINCT FROM s.type
        ORDER BY d.display_id
        """
    )
    return [dict(r) for r in cur.fetchall()]


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
    # RealDictCursor returns SQL NULL as None, but once the records enter a
    # DataFrame pandas promotes missing values in this column to float NaN.
    # NaN is truthy, so `if not raw` misclassified an ordinary blank as an
    # unknown literal outcome and stopped the nightly export.
    if raw is None or (not isinstance(raw, str) and pd.isna(raw)):
        return None
    raw = str(raw).strip()
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


def build(cur, verbose: bool = True,
          review_issue: bool = False) -> pd.DataFrame:
    """The prepared FLoRA dataset, as a DataFrame. Reads only; writes nothing.

    Carries three provenance columns beyond the FLoRA set so a produced row can be
    traced back to what it came from:

        source_record_id   the surviving source_records row (dedup keeps the first
                           row; a website-validated duplicate decides its values)
        source_display_id  that row's human id, e.g. REPL-000397
        merged_record_ids  rows the dedup collapsed into it, if any

    flora_id is NOT added here: assigning one is a write, and this stays a pure
    function of the database. flora_registry.refresh() attaches it.
    """
    def say(*args):
        if verbose:
            print(*args)

    df = load(cur)
    aliases, exclusions = load_rules(cur)
    dedup_decisions = load_dedup_decisions(cur)
    cross_type = load_cross_type_duplicates(cur)

    say("=== FLoRA entry-sheet transform ===")
    if cross_type:
        say(f"  ⚠ {len(cross_type)} row(s) ruled a duplicate of a record of the other "
            "type are left out: " + ", ".join(r["display_id"] for r in cross_type[:10]))
    if df.empty:
        say("No rows to transform.")
        return pd.DataFrame(columns=FLORA_COLUMNS + PROVENANCE_COLUMNS)
    say(f"  loaded: {len(df)} rows "
          f"({(df['type'] == 'replication').sum()} replication, "
          f"{(df['type'] == 'reproduction').sum()} reproduction)")

    # 1 ── outcome
    problems = {"unknown_alias": [], "bad_alias": [], "invalid_axis": []}
    for axis in ("outcome_computation", "outcome_robustness"):
        df[axis] = df.apply(lambda r, col=axis: _canonical_axis(r, col, problems), axis=1)
    outcome_before = list(df["outcome"]) if "outcome" in df.columns else []
    df["outcome"] = df.apply(lambda r: derive_outcome(r, aliases, problems), axis=1)
    df["outcome_quote"] = df.apply(derive_quote, axis=1)
    df["outcome_quote_source"] = df.apply(derive_quote_source, axis=1)

    # 1b ── COS coder shorthand -> a sentence that reads on its own
    # The notebook does this as the COS sheet is read; here the sheets are already
    # in source_records, so it runs on the derived quote instead — the same text,
    # one step later. rewrite_one returns anything it does not recognise unchanged,
    # so a real quote is never touched.
    before_quotes = df["outcome_quote"].copy()
    df["outcome_quote"] = cos_quote_rewrite.rewrite(df["outcome_quote"])
    rewritten = int((before_quotes.fillna("") != df["outcome_quote"].fillna("")).sum())
    if rewritten:
        say(f"  rewrote {rewritten} shorthand outcome quote(s) into readable form")
    n_repro = int((df["type"] == "reproduction").sum())
    say(f"  outcome normalized/derived; {df['outcome'].notna().sum()} row(s) have one")
    # Which spellings were recoded, and how often. A value appearing here for the
    # first time is usually a vocabulary change upstream.
    recodes = {}
    for raw, canonical in zip(outcome_before, df["outcome"]):
        raw_text, canonical_text = _s(raw), _s(canonical)
        if raw_text and canonical_text and raw_text != canonical_text:
            recodes[(raw_text, canonical_text)] = recodes.get((raw_text, canonical_text), 0) + 1
    if recodes:
        say(f"  recoded {sum(recodes.values())} outcome value(s):")
        for (raw_text, canonical_text), n in sorted(recodes.items(), key=lambda kv: -kv[1]):
            say(f"      {raw_text!r} -> {canonical_text!r} ({n})")
    if n_repro:
        say(f"  {n_repro} reproduction row(s) carry two authoritative axes")

    # 2 ── DOIs
    before_o, before_r = df["doi_o"].copy(), df["doi_r"].copy()
    df["doi_o"] = df["doi_o"].apply(clean_doi)
    df["doi_r"] = df["doi_r"].apply(clean_doi)
    changed = int(((before_o != df["doi_o"]) & before_o.notna()).sum() +
                  ((before_r != df["doi_r"]) & before_r.notna()).sum())
    say(f"  DOIs cleaned: {changed} value(s) changed")

    # Step 5: a DOI that does not start with "10." after cleaning is not a DOI.
    # Reported, never dropped — DUMMY_ placeholders are legitimate here and are
    # stripped at output, and a real mistake is for a reviewer to correct.
    for column in ("doi_o", "doi_r"):
        bad = [v for v in df[column] if _s(v) and not _s(v).lower().startswith("10.")]
        if bad:
            shown = bad if len(bad) < 10 else bad[:5]
            label = "" if len(bad) < 10 else " (first 5)"
            say(f"  ⚠ {len(bad)} {column} value(s) are not DOIs{label}: "
                + ", ".join(repr(v) for v in shown))

    # 2b ── confirmed preprint/publication resolutions (notebook Step 6a)
    # Before enrichment on purpose: this rewrites doi_o to the canonical form, and
    # fetching metadata first would spend requests on DOIs about to be discarded.
    def _normalise_outcome(value):
        text = "" if value is None or (isinstance(value, float) and pd.isna(value)) \
            else str(value).strip()
        return aliases.get(text, text) or None

    # Every (doi_o, doi_r) merge that resolved an outcome clash, from whichever
    # step performed it. Written by run() to output/dup_outcome_conflicts.csv;
    # build() stays read-only.
    conflicts = []

    df = preprint_dedup.apply_confirmed(
        df, normalise_outcome=_normalise_outcome, verbose=verbose,
        conflicts_out=conflicts, rulings=dedup_decisions)

    # 3 ── exclusions
    # Runs BEFORE url_r is stripped: an operator registering an exclusion copies
    # the URL as it appears in the review UI, and stripping first would leave
    # nothing for a url-keyed exclusion to match.
    # An exclusion with doi_o set matches the PAIR; without it, doi_r/url_r alone.
    # The distinction matters: a replication DOI can be a non-replication against one
    # original and a perfectly good entry against another, so excluding it outright
    # would take valid rows with it.
    pairs = {(clean_doi(e["doi_o"]), clean_doi(e["doi_r"]))
             for e in exclusions if e.get("doi_o") and e["doi_r"]}
    excl_dois = {clean_doi(e["doi_r"])
                 for e in exclusions if e["doi_r"] and not e.get("doi_o")}
    excl_urls = {norm_url(e["url_r"])
                 for e in exclusions if e["url_r"] and not e.get("doi_o")}

    if pairs or excl_dois or excl_urls:
        clean_r = df["doi_r"].apply(clean_doi)
        mask = (clean_r.isin(excl_dois - {None}) |
                df["url_r"].apply(norm_url).isin(excl_urls - {None}))
        if pairs:
            pair_key = list(zip(df["doi_o"].apply(clean_doi), clean_r))
            mask = mask | pd.Series([k in pairs for k in pair_key], index=df.index)
        n_pair = int(pd.Series([k in pairs for k in
                                zip(df["doi_o"].apply(clean_doi), clean_r)]).sum()) if pairs else 0
        say(f"  excluded: {int(mask.sum())} row(s) via transform_exclusions"
            + (f" ({n_pair} by (doi_o, doi_r) pair)" if n_pair else ""))
        df = df[~mask]
    else:
        say("  excluded: 0 (transform_exclusions is empty)")

    # 4 ── redundant url_r
    before_urls = df["url_r"].notna().sum()
    df["url_r"] = df.apply(strip_redundant_url, axis=1)
    say(f"  redundant url_r stripped: {before_urls - df['url_r'].notna().sum()}")

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
    # The survivor of each key is its first row, as it always was: which row a key
    # becomes is its published identity, so nothing here moves it. A record validated
    # on this website decides the VALUES instead (below), whatever its position.
    collides = key.duplicated() & key.ne("||")
    survivors = df.index[~key.duplicated()]

    # Which rows each survivor absorbs. Captured BEFORE the drop: afterwards the
    # collapsed rows are simply gone and their record_ids with them, and those ids
    # are what makes a produced row traceable back to its sources.
    df = df.assign(_key=key)
    dropped = df[collides & ~exempt]
    # A dropped row hands over its own id AND whatever an earlier merge (step 2b)
    # already folded into it; the survivor then adds these to its own list rather
    # than replacing it, so no absorbed id is lost between the two steps.
    prior = (df["merged_record_ids"] if "merged_record_ids" in df.columns
             else pd.Series([[] for _ in range(len(df))], index=df.index))
    merged_by_key = {}
    for idx in dropped.index:
        ids = merged_by_key.setdefault(df.at[idx, "_key"], [])
        ids.append(df.at[idx, "record_id"])
        ids.extend(prior[idx] if isinstance(prior[idx], list) else [])

    # A website-validated row that is dropped still decides the survivor's values:
    # its judgement wins, as in the merge below (preprint_dedup.website_overrides).
    # A row ruled 'distinct' is its own record and is never dropped, so it never
    # speaks for another. And a drop settles a disagreement as silently as a merge
    # does, so it is logged the same way.
    survivor_of = {df.at[idx, "_key"]: idx for idx in survivors}
    for k, group in dropped.groupby("_key"):
        kept = survivor_of[k]
        rows = [df.loc[kept], *(group.loc[i] for i in group.index)]
        overrides = preprint_dedup.website_overrides(rows, _normalise_outcome)
        for column, value in overrides.items():
            if column in df.columns:
                df.at[kept, column] = value
        outcomes = {o for o in (_normalise_outcome(r.get("outcome")) for r in rows) if o}
        if len(outcomes) > 1:
            conflicts.append({
                "doi_o": df.at[kept, "doi_o"], "doi_r": df.at[kept, "doi_r"],
                "type": df.at[kept, "type"],
                "outcomes": " | ".join(sorted(outcomes)),
                "resolved_to": _normalise_outcome(df.at[kept, "outcome"]),
                "resolved_by": "website record" if "outcome" in overrides else "first record",
                "url_r": df.at[kept, "url_r"],
                "sources": " | ".join(dict.fromkeys(str(r.get("source")) for r in rows)),
            })

    keep = ~(collides & ~exempt)
    survivor_set = set(survivors)
    df = df[keep].copy()
    df["merged_record_ids"] = [
        (list(prior[idx]) if isinstance(prior[idx], list) else [])
        + (merged_by_key.get(k, []) if idx in survivor_set else [])
        for idx, k in zip(df.index, df["_key"])
    ]
    df["dedup_key"] = df["_key"]
    df = df.drop(columns=["_key"])
    rescued = int((collides & exempt).sum())
    say(f"  deduplicated: {before - len(df)} row(s) collapsed"
          + (f"; {rescued} kept as reviewer-confirmed distinct" if rescued else ""))

    # 5b ── merge rows that share (doi_o, doi_r) with compatible url_r values.
    # The step above DROPS a colliding row; this one COMBINES them, because a row
    # about to be discarded can hold the only outcome_quote there is. Two distinct
    # url_r values mean two different reports and are left alone.
    df, outcome_conflicts = preprint_dedup.merge_doi_pair_dups(
        df, _normalise_outcome, verbose=verbose)
    if not outcome_conflicts.empty:
        conflicts.extend(outcome_conflicts.to_dict("records"))

    # 6 ── project
    df["source"] = df["source"].replace({"replications": "entry_sheet_replications",
                                         "reproductions": "entry_sheet_reproductions"})
    # 7 ── bibliographic enrichment
    # Fills the half of the output contract the R notebook fetched from CrossRef,
    # OpenAlex and Unpaywall. Cached per DOI in work_metadata by enrich_works.py, so
    # this is a dictionary join, not network traffic.
    from enrich_works import load_metadata          # local: avoids an import cycle
    from bibliographic_helpers import normalise_key
    meta = load_metadata(cur)
    for side, doi_col in (("o", "doi_o"), ("r", "doi_r")):
        keys = df[doi_col].map(lambda d: meta.get(normalise_key(d)) if d else None)
        for field, column in (("title", f"title_{side}"), ("authors", f"author_{side}"),
                              ("journal", f"journal_{side}"), ("volume", f"volume_{side}"),
                              ("issue", f"issue_{side}"), ("pages", f"pages_{side}"),
                              ("language", f"language_{side}"), ("oa_url", f"oa_url_{side}"),
                              ("oa_work_id", f"oa_work_id_{side}"),
                              ("bibtex_ref", f"bibtex_ref_{side}")):
            df[column] = keys.map(lambda m, f=field: (m or {}).get(f))

        # Prefer provider-formatted citations and structured Crossref authors;
        # retain source-sheet references when no provider can resolve the work.
        df[f"author_{side}"] = keys.map(lambda m: (m or {}).get("authors_json")).fillna(df[f"author_{side}"])
        df[f"ref_{side}"] = keys.map(lambda m: (m or {}).get("apa_ref")).fillna(df[f"ref_{side}"])
        if side == "r":
            df["abstract_r"] = df["abstract_r"].fillna(keys.map(lambda m: (m or {}).get("abstract")))

        # year is the one field we may already hold: the replications sheet carries
        # one. Metadata wins (it is the published year, the sheet's is hand-entered),
        # but the sheet's is kept where the DOI resolved to nothing — the R pipeline
        # drops the sheet value entirely and ends up with a blank.
        # Held back until after 7b rather than applied here: "metadata wins" means
        # the work-id lookup below wins too, and filling the sheet value first would
        # make its fillna a no-op on the 17 rows that have both.
        sheet_year = (df[f"year_{side}"].map(_sheet_year)
                      if f"year_{side}" in df.columns else None)
        df[f"year_{side}"] = keys.map(lambda m: (m or {}).get("year"))

        # 7b ── works with no DOI, only an OpenAlex link (notebook Step 7b)
        # Theses and working papers are recorded as https://openalex.org/W… in the
        # url column. Keyed on the W-id, cached by enrich_works alongside the DOI
        # rows. Only fills what the DOI join left empty, so a real DOI always wins.
        url_column = f"url_{side}"
        if url_column in df.columns:
            from enrich_works import extract_work_id
            id_keys = df[url_column].map(
                lambda u: (meta.get(normalise_key(u)) or meta.get(extract_work_id(u))) if u else None)
            for field, column in (("title", f"title_{side}"),
                                  ("authors", f"author_{side}"),
                                  ("journal", f"journal_{side}"),
                                  ("volume", f"volume_{side}"),
                                  ("issue", f"issue_{side}"),
                                  ("pages", f"pages_{side}"),
                                  ("language", f"language_{side}"),
                                  ("oa_url", f"oa_url_{side}"),
                                  ("oa_work_id", f"oa_work_id_{side}"),
                                  ("bibtex_ref", f"bibtex_ref_{side}"),
                                  ("year", f"year_{side}")):
                from_id = id_keys.map(lambda m, f=field: (m or {}).get(f))
                df[column] = df[column].fillna(from_id)
            # URL-only manual entries and OSF citations apply on both sides.
            no_doi_citation = keys.map(lambda m: not bool((m or {}).get("apa_ref")))
            from_citation = id_keys.map(lambda m: (m or {}).get("apa_ref"))
            df.loc[no_doi_citation, f"ref_{side}"] = from_citation[no_doi_citation].combine_first(df.loc[no_doi_citation, f"ref_{side}"])
            from_authors = id_keys.map(lambda m: (m or {}).get("authors_json"))
            no_doi_authors = keys.map(lambda m: not bool((m or {}).get("authors_json") or (m or {}).get("authors")))
            df.loc[no_doi_authors, f"author_{side}"] = from_authors[no_doi_authors].fillna(df.loc[no_doi_authors, f"author_{side}"])

        # …and only now the sheet's own year, for the rows neither lookup answered.
        if sheet_year is not None:
            df[f"year_{side}"] = df[f"year_{side}"].fillna(sheet_year)

        if side == "o":
            # R's formatted metadata includes the original DOI landing page.
            df["url_o"] = df["url_o"].fillna(df["doi_o"].map(_doi_url))

    enriched = int(df["title_o"].notna().sum())
    say(f"  enriched: {enriched}/{len(df)} row(s) have original-side metadata")

    # 6b ── preprint/publication duplicates (notebook Step 7d)
    # After enrichment because detection needs titles and authors, which only
    # arrive with the metadata join.
    # No file here: build() stays read-only. run() writes the log beside the
    # output, and the FLoRA tab reads it from the frame.
    df, dedup_log = preprint_dedup.resolve(
        df, candidates_out=None, normalise_outcome=_normalise_outcome,
        verbose=verbose, conflicts_out=conflicts, open_review_issue=review_issue,
        rulings=dedup_decisions)

    # 8 ── text cleaning (Step 7c of the notebook)
    # After enrichment, because titles and journals arrive from OpenAlex carrying
    # JATS markup, and before anything compares strings.
    for column in ("title_o", "title_r", "journal_o", "journal_r",
                   "ref_o", "ref_r", "abstract_r", "outcome_quote"):
        if column not in df.columns:
            continue
        before = df[column].copy()
        df[column] = df[column].apply(clean_text)
        # Reported per column, as the R side does: a sudden jump in one column is
        # how a new markup dialect upstream announces itself.
        changed = int((before.fillna("") != df[column].fillna("")).sum())
        if changed:
            say(f"      cleaned {column}: {changed} cell(s) changed")

    # 8b ── last-resort title recovery from the reference text
    # Placed HERE, after the dedup in 6b, deliberately: dedup matches on title
    # similarity, so supplying these titles earlier would change which rows survive
    # rather than only what the survivors report. See apa_references.
    # Also after the cleaning above, so a title is parsed out of cleaned text.
    for _side in ("o", "r"):
        apa_references.augment(df, _side, verbose=verbose)

    # 8c ── author overlap (augmentation.R)
    # How independent the replication team was. Arithmetic over author_o/author_r,
    # which enrichment already filled, so it costs nothing and needs no network.
    # After 8b so a name parsed out of a reference string counts too.
    author_overlap.augment(df, verbose=verbose)

    # 9 ── meta-paper annotation (Step 9d)
    # A reference to an aggregate paper does not say which of its studies a row is
    # about, so the row's own report URL is appended to it.
    clean_r = df["doi_r"].apply(clean_doi)
    has_url = df["url_r"].notna() & df["url_r"].astype(str).str.strip().ne("")
    annotate = clean_r.isin(META_PAPER_DOIS) & has_url

    if annotate.any():
        def _annotate(row):
            ref = row["ref_r"]
            # Idempotent: a re-run must not append the note a second time.
            if not ref or META_PAPER_NOTE in str(ref):
                return ref
            return f"{str(ref).rstrip()} {META_PAPER_NOTE} {row['url_r']}"
        df.loc[annotate, "ref_r"] = df[annotate].apply(_annotate, axis=1)
    say(f"  meta-paper rows annotated with their report URL: {int(annotate.sum())}")

    # Candidates nobody has classified yet. Reported, never applied automatically:
    # deciding a paper is an aggregate is a judgement, and guessing it would rewrite
    # references on rows where the URL means something else.
    linked = df[df["url_r"].notna() & df["doi_r"].notna()].copy()
    if not linked.empty:
        linked["_doi"] = linked["doi_r"].apply(clean_doi)
        url_counts = linked.groupby("_doi")["url_r"].nunique()
        candidates = [(d, n) for d, n in url_counts.items()
                      if n >= META_PAPER_CANDIDATE_URLS and d not in META_PAPER_DOIS]
        if candidates:
            say(f"  ⚠ {len(candidates)} DOI(s) carry >= {META_PAPER_CANDIDATE_URLS} "
                f"distinct url_r but are not listed as meta-papers:")
            for doi, n in sorted(candidates, key=lambda c: -c[1])[:10]:
                say(f"      {doi} ({n} distinct urls)")
            say("    → add to META_PAPER_DOIS if they are aggregate reports")

    df["source_record_id"] = df["record_id"]
    df["source_display_id"] = df["display_id"]
    df["flora_id"] = None          # filled in by flora_registry.refresh()
    out = df.reindex(columns=FLORA_COLUMNS + ENRICHMENT_COLUMNS
                             + DERIVED_COLUMNS + PROVENANCE_COLUMNS)
    # Beside the frame rather than in a column: a list per row would have to be
    # serialised into the CSV, and only the registry needs it.
    out.attrs["merged_record_ids"] = dict(zip(df["record_id"], df["merged_record_ids"]))
    out.attrs["dedup_key"] = dict(zip(df["record_id"], df["dedup_key"]))
    out.attrs["outcome_conflicts"] = conflicts
    # NaN never equals itself, and pandas compares attrs when it combines frames.
    out.attrs["preprint_dedup_log"] = [
        {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()}
        for row in dedup_log.to_dict("records")
    ]
    out.attrs["cross_type_duplicates"] = cross_type

    # DUMMY_* placeholders keyed manual references upstream; they are not DOIs.
    for col in ("doi_o", "doi_r"):
        dummy = out[col].astype(str).str.match(DUMMY_DOI_RE, na=False)
        if dummy.any():
            say(f"  stripped {int(dummy.sum())} DUMMY_* placeholder(s) from {col}")
            out.loc[dummy, col] = None

    say(f"\n  final: {len(out)} rows × {len(out.columns)} columns")

    unknown_alias = problems["unknown_alias"]
    bad_alias = problems["bad_alias"]
    invalid_axis = problems["invalid_axis"]
    missing = int(out["outcome"].isna().sum())
    if missing:
        invalid_rows = {display_id for display_id, _, _ in invalid_axis}
        blank = missing - len(unknown_alias) - len(bad_alias) - len(invalid_rows)
        if blank > 0:
            say(f"  ⚠ {blank} row(s) have no outcome — blank or incomplete in the source sheet")

    say("\n  outcome distribution:")
    for value, n in out["outcome"].value_counts(dropna=True).items():
        say(f"      {str(value):55} {n}")

    # An unrecognised spelling is not a gap to fill later — it means a real outcome
    # would be published as blank, or (worse, before this check) verbatim and
    # unrecognised. Report every one, then refuse to write: a partial export that
    # looks complete is what makes this class of bug expensive.
    #
    # Reproduction outcomes bypass aliases because they are derived from axes,
    # which are validated by database constraints on the way in.
    if unknown_alias or bad_alias or invalid_axis:
        say()
        if unknown_alias:
            say(f"  ✗ {len(unknown_alias)} row(s) carry an outcome spelling absent from outcome_alias:")
            for display_id, raw in unknown_alias[:10]:
                say(f"      {display_id}: {raw!r}")
            say("    → add a row to outcome_alias mapping it to a canonical value, and re-run")
        if bad_alias:
            say(f"  ✗ {len(bad_alias)} row(s) alias onto a value the app does not accept:")
            for display_id, raw, canonical in bad_alias[:10]:
                say(f"      {display_id}: {raw!r} → {canonical!r}")
            say("    → fix the outcome_alias row, or add the value to "
                  "extractor_vocab.REPLICATION_OUTCOMES and the CHECK constraint")
        if invalid_axis:
            say(f"  INVALID: {len(invalid_axis)} reproduction axis value(s):")
            for display_id, column, raw in invalid_axis[:10]:
                say(f"      {display_id}: {column}={raw!r}")
            say("    correct the source_record value to a codebook category, and re-run")
        problem_count = len(unknown_alias) + len(bad_alias) + len(invalid_axis)
        raise ValueError(
            f"{problem_count} outcome or axis value(s) are not recognised; "
            f"refusing to produce an export. "
            f"Raised under --stats-only too, so a dry run reports the problem "
            f"rather than passing."
        )

    return out


def _has_text(series):
    return series.apply(lambda v: bool(_s(v)))


def print_summary(shaped, output: Path) -> None:
    """The notebook's closing statistics. Printed rather than returned: this is
    the thing an operator reads to decide whether a run looks sane."""
    print("\n### Summary statistics")
    print(f"  total paper pairs: {len(shaped)}")

    if "type" in shaped.columns:
        print("\n  rows by type:")
        for value, n in shaped["type"].value_counts(dropna=False).items():
            print(f"      {str(value):<14} {n}")

    print()
    pairs = [
        ("rows with an outcome code", "outcome"),
        ("original papers with an APA reference", "apa_ref_o"),
        ("replication papers with an APA reference", "apa_ref_r"),
        ("original papers with BibTeX", "bibtex_ref_o"),
        ("replication papers with BibTeX", "bibtex_ref_r"),
        ("original papers with a title", "title_o"),
        ("replication papers with a title", "title_r"),
        ("original papers with language metadata", "language_o"),
        ("replication papers with language metadata", "language_r"),
        ("original papers with an OA url", "oa_url_o"),
        ("replication papers with an OA url", "oa_url_r"),
        ("rows with an original DOI", "doi_o"),
        ("rows with a replication DOI", "doi_r"),
    ]
    for label, column in pairs:
        if column in shaped.columns:
            n = int(_has_text(shaped[column]).sum())
            pct = (100 * n / len(shaped)) if len(shaped) else 0
            print(f"  {label:<44} {n:>5}  ({pct:>3.0f}%)")


def run(output: Path, stats_only: bool = False,
        require_titles: bool = True, review_issue: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("ERROR: DATABASE_URL must be set in environment or .env")

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Standalone preparation must pin IDs too. Otherwise a new row
                # can ship under a provisional UUID and change identity when a
                # later scheduled registry stage assigns its permanent ID.
                # Lock before building; register this exact frame rather than
                # running the transform again against potentially changed data.
                if not stats_only:
                    cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
                out = build(cur, review_issue=review_issue)
                # Imported here because flora_registry imports this module.
                from flora_registry import attach_ids, refresh
                if not stats_only:
                    refresh(cur, verbose=False, frame=out)
                out = attach_ids(cur, out)
                if not stats_only and not out.empty:
                    if (out["export_id"].map(final_export.text).eq("").any()
                            or out["export_position"].isna().any()):
                        raise ValueError("Cannot export a record without a pinned publication identity")
    finally:
        conn.close()

    if out.empty:
        return
    if stats_only:
        print("\n[stats-only] nothing written")
        return

    output.parent.mkdir(parents=True, exist_ok=True)

    # Step 10 of the notebook: rows with no title on one side are logged, and
    # dropped from the export. The notebook does this unconditionally.
    #
    # It was OFF here while our title coverage was worse than R's — we had only the
    # OpenAlex DOI lookup against R's CrossRef plus three fallbacks, so 331 rows
    # lacked a title for reasons of OUR coverage rather than the data's, and
    # dropping them would have quietly published a smaller dataset than R does.
    #
    # The fallbacks now exist: OpenAlex work-id lookups for DOI-less rows (Step 7b)
    # and title recovery from the reference text (Step 8b). That leaves 24 rows,
    # which are genuinely untitled — Google Docs links, DOIs OpenAlex does not hold
    # — so the filter is ON, matching the notebook. --keep-untitled turns it off.
    #
    # This applies to the EXPORT only. The FLoRA tab still shows these rows: it is
    # the surface someone fixes them on, and a row hidden there is a row nobody
    # fixes. flora_service.counts() reports how many will not reach the export.
    # Outcome clashes resolved during a merge — the same paper, the same url_r,
    # disagreeing outcomes. Source-data mistakes worth someone's attention, so
    # they get their own file rather than scrolling past in the log.
    conflicts = out.attrs.get("outcome_conflicts") or []
    if conflicts:
        conflict_path = output.parent / "dup_outcome_conflicts.csv"
        pd.DataFrame(conflicts).to_csv(conflict_path, index=False,
                                       encoding="utf-8", lineterminator="\n")
        print(f"\n  ⚠ {len(conflicts)} merged group(s) had conflicting outcomes "
              f"-> {conflict_path}")
        for conflict in conflicts[:5]:
            print(f"      {conflict.get('doi_o')} + {conflict.get('doi_r')}  "
                  f"[{conflict.get('outcomes')} -> {conflict.get('resolved_to')!r}]")

    # Every detected preprint pair and what happened to it. Beside the output so
    # the nightly artifact and the preparation report carry it.
    dedup_log = out.attrs.get("preprint_dedup_log") or []
    if dedup_log:
        dedup_path = output.parent / "preprint_dedup_candidates.csv"
        preprint_dedup.write_candidates(dedup_log, dedup_path)
        pending = len(preprint_dedup.unresolved(dedup_log))
        print(f"\n  {len(dedup_log)} preprint duplicate pair(s), {pending} awaiting "
              f"a decision in the FLoRA tab -> {dedup_path}")

    cross_type = out.attrs.get("cross_type_duplicates") or []
    if cross_type:
        cross_path = output.parent / "cross_type_duplicate_rulings.csv"
        pd.DataFrame(cross_type).to_csv(cross_path, index=False, encoding="utf-8",
                                        lineterminator="\n")
        print(f"\n  ⚠ {len(cross_type)} row(s) ruled a duplicate of a record of the other "
              f"type -> {cross_path}")

    report = missing_title_report(out)
    if not report.empty:
        log_path = output.parent / "flora_export_log.csv"
        report.to_csv(log_path, index=False, encoding="utf-8", lineterminator="\n")
        print(f"\n  {len(report)} row(s) missing a title -> {log_path}")
        for reason, n in report["reason"].value_counts().items():
            print(f"      {reason}: {n}")
        if require_titles:
            out = drop_untitled(out)
            print(f"  dropped from the export; {len(out)} row(s) remain "
                  f"(--keep-untitled to keep them)")
        else:
            print("  --keep-untitled: kept in the export")

    shaped = to_output_shape(out)
    # Explicit newline so the file does not differ between a Windows dev box and
    # the Linux runner that produces the nightly artifact.
    output.write_text(final_export.csv_text(shaped), encoding="utf-8", newline="")
    empty = [c for c in FLORA_OUTPUT_COLUMNS if shaped[c].isna().all()]
    print(f"\n  saved: {output}")
    print(f"  columns: {len(shaped.columns)} "
          f"({len(FLORA_OUTPUT_COLUMNS) - len(empty)}/{len(FLORA_OUTPUT_COLUMNS)} "
          f"of the output contract carry data)")
    if empty:
        # A contract column with nothing in it at all means an enrichment source
        # stopped answering — worth saying rather than shipping quietly.
        print(f"  ⚠ no data at all in: {', '.join(empty)}")
        print("    → run enrich_works.py, or check whether OpenAlex is reachable")

    print_summary(shaped, output)


if __name__ == "__main__":
    start_logging("transform")
    parser = argparse.ArgumentParser(description="Transform source_records into the FLoRA column set")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output CSV path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--review-issue", action="store_true",
                        help="file or comment on a GitHub issue when pairs are "
                             "being auto-resolved and cache/confirmed_preprint_"
                             "duplicates.csv has gone stale. Needs the gh CLI. "
                             "OFF by default so opening the FLoRA tab cannot file "
                             "issues.")
    parser.add_argument("--keep-untitled", action="store_true",
                        help="keep rows with no title on one side. They are dropped "
                             "by default, as the R notebook's Step 10 does.")
    parser.add_argument("--stats-only", action="store_true",
                        help="Report what would be produced without writing.")
    args = parser.parse_args()

    run(args.output, stats_only=args.stats_only,
        require_titles=not args.keep_untitled, review_issue=args.review_issue)
