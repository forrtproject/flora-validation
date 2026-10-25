"""validate_flora.py — structural validation of the FLoRA dataset.

A Python port of R/validate_flora.R, with its rules changed so the report lists
real problems. Each finding is a small record: the check's code, the FLoRA IDs of
its rows, the column, the value and a plain detail, with a stable key (see
finding()). The Markdown report is rendered from those records, one list item per
finding, so a resolved finding disappears on the next run.

TWO LEVELS
----------
Every check is a Problem, a value that is wrong as published, or a Possible
problem, a heuristic whose findings a person decides on. The report lists the
Problems first. --fail-on-issues fails on Problems only.

WHAT CHANGED IN THE PORT
------------------------
`source` must be one of the labels the build publishes, taken from the transform's
own mapping, so a registry key leaking through a broken mapping is reported.

A missing doi_o is not a problem by itself: books, theses and pre-DOI papers have
none. Only an original with no identifier at all is reported; the network checks
look up a DOI for the others (validate_flora_network, N4).

Duplicates are the rows the build itself would make one record. Separate reports
of one paper pair are two records by the pipeline's rule and are not reported.

No checkboxes. In fred-data the report is a GitHub issue whose ticked boxes are read
back as suppressions; here it is a file that nothing reads back, so a box would ask
for a tick that does nothing.

Suppressions are read from `output/flora_validation_suppressions.csv` when it
exists: check_name and item_id columns, as the R version's file has. Nothing in
this project writes it; the database will hold these decisions instead.

Usage:
    python validate_flora.py                     # report to stdout
    python validate_flora.py --output report.md
    python validate_flora.py --fail-on-issues    # exit 1 if a Problem is found

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import csv
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import preprint_dedup
import transform_sources
from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
from extractor_vocab import REPLICATION_OUTCOMES
from record_checks import YEAR_MIN

load_dotenv()
use_utf8_output()

ROOT = Path(__file__).parent
SUPPRESSIONS_FILE = ROOT / "output" / "flora_validation_suppressions.csv"

PROBLEM, POSSIBLE_PROBLEM = "problem", "possible_problem"
LEVEL_TITLES = {PROBLEM: "Problems", POSSIBLE_PROBLEM: "Possible problems"}
LEVEL_NOTES = {POSSIBLE_PROBLEM: "Heuristics: a person decides whether each one is a problem."}

# What the build publishes as `source`: the transform's output labels for the
# registry's sources, and the two sources published under their own key, the
# website's validated records (sync_validated.SOURCE_KEY) and the adjudication's
# answers (adjudication.review.SOURCE_KEY). Taken from OUTPUT_SOURCES rather than
# listed again, so a registry key that a broken mapping lets through is reported.
VALID_SOURCES = frozenset(transform_sources.OUTPUT_SOURCES.values()) | {"validated", "adjudicated"}
VALID_TYPES = {"replication", "reproduction"}
URL_COLUMNS = ["url_r", "oa_url_o", "oa_url_r", "url_o"]
# Filled from the enrichment cache (enrich_works), not from a source record, so a
# fix belongs there rather than in a sheet.
ENRICHED_COLUMNS = {"oa_url_o", "oa_url_r"}
ENRICHED_NOTE = "an open-access link from enrichment, not from the source record"

# Above this many issues the section is folded into a <details> block, so one noisy
# check cannot bury every other one.
DETAILS_THRESHOLD = 10
# A finding about many rows names this many and counts the rest.
SHOWN_IDS = 5
SHOWN_VALUE = 120

DOI_RE = re.compile(r"^10\.[0-9]{4,}/\S+$")
NA_STRINGS = {"NA", "N/A", "na", "n/a", "NULL", "None"}


def _blank(value) -> bool:
    return (value is None or (isinstance(value, float) and pd.isna(value))
            or not str(value).strip())


def _text(value) -> str:
    return "" if _blank(value) else str(value).strip()


def row_id(row) -> str:
    """`doi_o | doi_r-or-url_r`, the pair as the R report names a row. Shown after
    the FLoRA ID, so a reader recognises the papers without looking the ID up."""
    right = _text(row.get("doi_r")) or _text(row.get("url_r"))
    return f"{_text(row.get('doi_o'))} | {right}"


# ── findings ──────────────────────────────────────────────────────────────────

def finding_key(check, flora_ids, column, value) -> str:
    """(check, sorted FLoRA IDs, column, normalised value) as one string.

    The same finding gets the same key on every run, whatever else in the row
    changes, and a new key once the value it flagged changes. Only the value can
    hold a "|", and it comes last, so the key splits back unambiguously.
    """
    normalised = " ".join(_text(value).split()).casefold()
    return "|".join([check, ",".join(sorted(flora_ids)), column or "", normalised])


def finding(check, rows, column, value, detail, label=None) -> dict:
    """One finding about one or more rows.

    `detail` is plain text saying what is wrong; the value it is about is kept
    apart, for the key and for whoever shows it. `label` is the rows' `doi_o |
    doi_r`, by default the one they all share.
    """
    rows = list(rows)
    flora_ids = sorted({_text(row.get("flora_id")) for row in rows} - {""})
    if label is None:
        labels = {row_id(row) for row in rows}
        label = labels.pop() if len(labels) == 1 else ""
    value = None if _blank(value) else str(value).strip()
    return {"check": check, "flora_ids": flora_ids, "column": column, "value": value,
            "detail": detail, "label": label,
            "key": finding_key(check, flora_ids, column, value)}


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
_META_PAPER_NOTE = transform_sources.META_PAPER_NOTE.lower()


def _clean_ref(value: str) -> str:
    text = _text(value).lower()
    # The transform appends "<META_PAPER_NOTE> <url_r>" to the reference of every
    # row of a meta-paper (transform_sources.build, step 9), so those rows differ by
    # that suffix alone. Everything from the note on is the transform's.
    text = text.partition(_META_PAPER_NOTE)[0]
    text = _REF_DOI_RE.sub("", text)
    text = _REF_STUDY_RE.sub("", text)
    return " ".join(text.split())


# ── suppressions ──────────────────────────────────────────────────────────────

def load_suppressions(path: Path = SUPPRESSIONS_FILE) -> set:
    """(check_name, item_id) pairs recorded as false positives, if the file exists.

    check_name is a check's code, name or heading; item_id is a finding's stable
    key, or its FLoRA IDs as the report lists them ("REPL-000001, REPL-000002").
    """
    if not path.exists():
        return set()
    out = set()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("check_name") and row.get("item_id"):
                out.add((row["check_name"].strip(), row["item_id"].strip()))
    return out


def is_suppressed(item: dict, check_names, suppressions) -> bool:
    ids = (item["key"], ", ".join(item["flora_ids"]))
    return any((name, item_id) in suppressions for name in check_names for item_id in ids)


# ── the checks ────────────────────────────────────────────────────────────────

def _check_na_strings(df):
    # The "NA" half fires only on a frame straight from the build (this script
    # without --input). flora.csv writes a missing value as NA
    # (final_export.csv_text), and every reader of it, prepare_flora's included,
    # reads NA back as missing: in the CSV, a cell holding the text "NA" cannot be
    # told from an empty one. The other spellings reach the check either way.
    issues = []
    for _, row in df.iterrows():
        for column, value in row.items():
            if isinstance(value, str) and value.strip() in NA_STRINGS:
                issues.append(finding("na_string", [row], column, value,
                                      f"{column} holds a placeholder, not a missing value"))
    return issues


_SCHEME_RE = re.compile(r"^https?://", re.I)
_BARE_DOI_RE = re.compile(r"^(doi:\s*)?10\.\d{4,}/\S+$", re.I)
# A host name ending in a top-level domain, then perhaps a path: "osf.io/abc".
_BARE_LINK_RE = re.compile(r"^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/\S*)?$", re.I)
# A second link after a separator. Not any second "http": an archived page
# (web.archive.org/web/…/http://…) and a redirect (?url=https://…) hold one link.
_SECOND_LINK_RE = re.compile(r"[\s;,|]\s*((and|or)\s+)?https?://", re.I)


def _host(link) -> "str | None":
    """The host name of a link, or None when it has none Python can parse: urlsplit
    raises on a stray bracket ("https://osf.io]", "[Re] A title")."""
    try:
        return urllib.parse.urlsplit(link).hostname
    except ValueError:
        return None


def url_problem(value, title=None) -> "str | None":
    """What is wrong with a url cell as a link, or None when it is one usable link."""
    text = _text(value)
    if not text:
        return None
    if not _SCHEME_RE.match(text):
        if _BARE_DOI_RE.match(text):
            return "holds a DOI, not a link"
        if _BARE_LINK_RE.match(text):
            return "has no http(s)://"
        if not _blank(title) and preprint_dedup.title_similarity(text, title) >= 0.9:
            return "holds the title, not a link"
        return "holds text, not a link"
    if _SECOND_LINK_RE.search(text):
        return "holds two links"
    if text.endswith(("…", "...")):
        return "is cut off"
    if len(text.split()) > 1:
        return "contains spaces"
    if not _host(text):
        return "has no valid host name"
    return None


def _check_urls(df):
    issues = []
    for _, row in df.iterrows():
        for column in URL_COLUMNS:
            value = _text(row.get(column))
            problem = url_problem(value, row.get(f"title_{column[-1]}"))
            if problem:
                note = f" ({ENRICHED_NOTE})" if column in ENRICHED_COLUMNS else ""
                issues.append(finding("invalid_url", [row], column, value,
                                      f"{column} {problem}{note}"))
    return issues


def _check_outcomes(df):
    from extractor_vocab import (normalize_axis_value, OUTCOME_COMPUTATION_VALUES,
                                 OUTCOME_ROBUSTNESS_VALUES)
    issues = []
    for _, row in df.iterrows():
        outcome = _text(row.get("outcome"))
        if not outcome:
            issues.append(finding("invalid_outcome", [row], "outcome", None, "outcome is blank"))
        elif outcome == "not_a_replication":
            # A record coded as no replication at all has no place in the dataset.
            issues.append(finding("invalid_outcome", [row], "outcome", outcome,
                                  "outcome says the record is not a replication"))
        elif row.get("type") == "replication" and outcome not in REPLICATION_OUTCOMES:
            issues.append(finding("invalid_outcome", [row], "outcome", outcome,
                                  "outcome is not a replication outcome"))
        elif row.get("type") == "reproduction":
            parts = [part.strip() for part in outcome.split(",")]
            try:
                valid = (len(parts) == 2
                         and normalize_axis_value("outcome_computation", parts[0]) in OUTCOME_COMPUTATION_VALUES
                         and normalize_axis_value("outcome_robustness", parts[1]) in OUTCOME_ROBUSTNESS_VALUES)
            except ValueError:
                valid = False
            if not valid:
                issues.append(finding("invalid_outcome", [row], "outcome", outcome,
                                      "outcome is not a reproduction outcome"))
    return issues


TEXT_COLUMNS = ("title_o", "title_r", "apa_ref_o", "apa_ref_r", "journal_o", "journal_r")


def _check_structured_text(df):
    """A text cell holding JSON: an API client parsing it gets an object, not text."""
    from bibliographic_helpers import is_structured_text
    issues = []
    for _, row in df.iterrows():
        for column in TEXT_COLUMNS:
            value = _text(row.get(column))
            if is_structured_text(value):
                issues.append(finding("json_in_text", [row], column, value,
                                      f"{column} holds JSON, not text"))
    return issues


def _check_types(df):
    return [finding("invalid_type", [r], "type", r.get("type"),
                    "type is neither replication nor reproduction")
            for _, r in df.iterrows() if r.get("type") not in VALID_TYPES]


def _check_sources(df):
    return [finding("invalid_source", [r], "source", r.get("source"),
                    "source is not a label the build publishes")
            for _, r in df.iterrows() if r.get("source") not in VALID_SOURCES]


def _year(value):
    try:
        return int(float(str(value)))
    except (TypeError, ValueError, OverflowError):        # "inf" and "Infinity" parse
        return None


def _check_year_ranges(df):
    current = datetime.now(timezone.utc).year
    issues = []
    for _, row in df.iterrows():
        for column in ("year_o", "year_r"):
            year = _year(row.get(column))
            # The floor is record_checks', so consensus and this report agree.
            if year is not None and (year < YEAR_MIN or year > current + 1):
                issues.append(finding("implausible_year", [row], column, row.get(column),
                                      f"{column} is outside {YEAR_MIN} to {current + 1}"))
    return issues


# Where an earlier version of a paper lives: a replication can rightly come before
# the journal article when it replicated the preprint or working paper.
PREPRINT_HOSTS = ("osf.io", "arxiv.org", "psyarxiv.com", "biorxiv.org", "medrxiv.org",
                  "ssrn.com", "researchsquare.com", "preprints.org", "nber.org")


def has_earlier_version(row) -> bool:
    """Whether the original has a preprint or other earlier version: a preprint or
    repository doi_o, or a url_o or alt_identifier_o that points to one."""
    doi = row.get("doi_o")
    if preprint_dedup.is_preprint_doi(doi) or preprint_dedup.is_repository_doi(doi):
        return True
    others = [_text(row.get("url_o"))] + _text(row.get("alt_identifier_o")).split(",")
    for value in (part.strip() for part in others if part.strip()):
        if preprint_dedup.is_preprint_doi(value) or preprint_dedup.is_repository_doi(value):
            return True
        # A url_o can hold a title, which has no host; a bare link has no scheme.
        host = _host(value if _SCHEME_RE.match(value) else "//" + value)
        if host and any(host == name or host.endswith("." + name) for name in PREPRINT_HOSTS):
            return True
    return False


def _check_year_order(df):
    issues = []
    for _, row in df.iterrows():
        year_o, year_r = _year(row.get("year_o")), _year(row.get("year_r"))
        if year_o and year_r and year_r < year_o and not has_earlier_version(row):
            issues.append(finding("year_order", [row], "year_r", row.get("year_r"),
                                  f"year_r is before year_o ({year_o})"))
    return issues


def _rows(df, positions):
    return [df.iloc[position] for position in positions]


def _check_duplicate_records(df):
    """Rows the build itself would have made one record (transform_sources.record_keys):
    normally none, so this guards the duplicate steps, and catches a correction that
    gives a record another record's identifiers.

    Separate reports of one paper pair (the same doi_o and doi_r, different url_r)
    are two records by that rule, as in R, and are not reported. Re-entries of one
    study under different identifiers show in Source Records → Duplicates.
    """
    if not {"type", "doi_o", "doi_r", "url_r"} <= set(df.columns):
        return []
    groups = {}
    for position, key in enumerate(transform_sources.record_keys(df).to_numpy()):
        if isinstance(key, str):                    # missing for a row with no identifier
            groups.setdefault(key, []).append(position)
    return [finding("duplicate_record", _rows(df, positions), "", key,
                    f"{len(positions)} rows the build would make one record")
            for key, positions in groups.items() if len(positions) > 1]


def _doi_pairs(df, url_r=False):
    """{(doi_o, doi_r): row positions}, cleaned as the duplicate steps compare them.
    With `url_r`, a row without a doi_r is paired on its url_r instead."""
    pairs = {}
    # Not Series.map: it hands back clean_doi's None as NaN, which is truthy.
    clean = transform_sources.clean_doi
    links = df["url_r"] if url_r and "url_r" in df.columns else [None] * len(df)
    for position, (doi_o, doi_r, link) in enumerate(zip(df["doi_o"], df["doi_r"], links)):
        doi_o, doi_r = clean(doi_o), clean(doi_r) or transform_sources.norm_url(link)
        if doi_o and doi_r:
            pairs.setdefault((doi_o, doi_r), []).append(position)
    return pairs


def _check_cross_type_pairs(df):
    """The same doi_o and doi_r (or url_r, where there is no doi_r) as a replication
    and as a reproduction: possibly one coding entered in both sheets, possibly two
    studies of one paper pair."""
    issues = []
    for positions in _doi_pairs(df, url_r=True).values():
        rows = _rows(df, positions)
        types = sorted({_text(row.get("type")) for row in rows})
        if set(types) >= VALID_TYPES:
            issues.append(finding("cross_type_pair", rows, "type", ", ".join(types),
                                  "the same paper pair is published as a replication and "
                                  "as a reproduction"))
    return issues


def _check_reversed_pairs(df):
    """One row's (doi_o, doi_r) is another row's (doi_r, doi_o)."""
    pairs = _doi_pairs(df)
    issues = []
    for (doi_o, doi_r), positions in pairs.items():
        if doi_o < doi_r and (doi_r, doi_o) in pairs:
            rows = _rows(df, positions + pairs[(doi_r, doi_o)])
            issues.append(finding("reversed_pair", rows, "doi_o", f"{doi_o} | {doi_r}",
                                  "the paper pair is entered both ways round: one row's "
                                  "original is the other's replication"))
    return issues


# What url_o holds when it names no paper. A link without its http(s):// or a DOI
# still names one: Invalid URLs reports its form, and one finding is enough.
_NAMES_NO_PAPER = ("holds the title, not a link", "holds text, not a link")


def _url_o_names_no_paper(row) -> "str | None":
    """url_problem's words for a url_o that holds a title or other text, else None."""
    problem = url_problem(row.get("url_o"), row.get("title_o"))
    return problem if problem in _NAMES_NO_PAPER else None


def _original_identified(row) -> bool:
    return bool(_text(row.get("doi_o")) or _text(row.get("alt_identifier_o"))
                or _text(row.get("oa_work_id_o"))
                or (_text(row.get("url_o")) and not _url_o_names_no_paper(row)))


def _check_required_fields(df):
    # The titles and the replication's identifier hardly ever fire: untitled rows
    # are left out of the export, and a row with neither doi_r nor url_r has
    # nothing the build can key it on. Kept, because they guard those steps.
    issues = []
    for _, row in df.iterrows():
        for column in ("title_o", "title_r"):
            if _blank(row.get(column)):
                issues.append(finding("missing_required", [row], column, None, f"{column} is missing"))
        if _blank(row.get("doi_r")) and _blank(row.get("url_r")):
            issues.append(finding("missing_required", [row], "doi_r", None,
                                  "doi_r and url_r are both missing"))
        if not _original_identified(row):
            # A url_o holding a title is also an Invalid URLs finding. Both are
            # true, and adding a doi_o settles this one only, so both are kept;
            # naming the url_o here shows that its link or DOI would settle both.
            unusable = _url_o_names_no_paper(row)
            missing = (f"no doi_o, alt_identifier_o or oa_work_id_o, and url_o {unusable}"
                       if unusable else "no doi_o, url_o, alt_identifier_o or oa_work_id_o")
            issues.append(finding("missing_required", [row], "doi_o", None,
                                  f"the original has no identifier: {missing}"))
    return issues


# A query string after the DOI: "?utm_campaign=…" is how a link was shared, not
# part of the DOI. Old Wiley SICI DOIs hold < > ; # [ ] and :, but never "?…=".
_DOI_QUERY_RE = re.compile(r"\?[\w.-]+=")


def doi_problem(value) -> "str | None":
    """What is wrong with a doi cell as a bare DOI, or None when it is one."""
    text = _text(value)
    if not text:
        return None
    if DOI_RE.match(text):
        return "carries query or tracking text (?…)" if _DOI_QUERY_RE.search(text) else None
    if text.startswith("0."):
        return "starts with 0. (missing the 1?)"
    if _SCHEME_RE.match(text) or (_BARE_LINK_RE.match(text) and "/" in text):
        return "is a link, not a DOI"
    return "is not a DOI"


ALT_COLUMNS = ("alt_identifier_o", "alt_identifier_r")
# A part of an alt_identifier list that is meant as a DOI. The lists also hold
# links (a preprint, a meta-paper), which are left alone.
_DOI_SHAPED_RE = re.compile(r"^1?0\.\d")


def _check_doi_format(df):
    issues = []
    for _, row in df.iterrows():
        for column in ("doi_o", "doi_r"):
            value = _text(row.get(column))
            problem = doi_problem(value)
            if problem:
                issues.append(finding("invalid_doi", [row], column, value, f"{column} {problem}"))
        # Comma-separated, as preprint_dedup.append_alt_identifier writes them: an
        # admin's alias, or the DOI of a duplicate the build merged into this row.
        for column in ALT_COLUMNS:
            for part in (p.strip() for p in _text(row.get(column)).split(",")):
                problem = doi_problem(part) if _DOI_SHAPED_RE.match(part) else None
                if problem:
                    problem = "is malformed" if problem == "is not a DOI" else problem
                    issues.append(finding("invalid_doi", [row], column, part,
                                          f"{column} lists a DOI that {problem}"))
    return issues


def _references_conflict(refs, threshold=0.2) -> bool:
    """Whether any two of these references are more than `threshold` apart.

    The largest distance, where R takes the smallest: one row with a wrong
    reference is enough to report, and R's smallest hides it as soon as two other
    rows agree. Stops at the first such pair, and needs no edit distance where the
    lengths alone already differ by more than the threshold.
    """
    refs = sorted(refs)
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            if (abs(len(a) - len(b)) / max(len(a), len(b), 1) > threshold
                    or _normalised_distance(a, b) > threshold):
                return True
    return False


def _check_reference_conflicts(df):
    """The same DOI appearing with substantially different reference text."""
    issues = []
    for doi_column, ref_column in (("doi_o", "apa_ref_o"), ("doi_r", "apa_ref_r")):
        if ref_column not in df.columns:
            continue
        groups = {}
        for position, (doi, ref) in enumerate(zip(df[doi_column], df[ref_column])):
            cleaned = _clean_ref(ref)
            if _text(doi) and cleaned:
                groups.setdefault(_text(doi), []).append((position, cleaned))
        for doi, entries in groups.items():
            # A set first: the rows of one meta-paper share a handful of references.
            refs = {cleaned for _, cleaned in entries}
            if len(refs) > 1 and _references_conflict(refs):
                issues.append(finding("reference_conflict",
                                      _rows(df, [position for position, _ in entries]),
                                      ref_column, doi,
                                      f"{len(refs)} different references in {ref_column} "
                                      f"for this {doi_column}", label=""))
    return issues


_RETRACTED_TITLE_RES = (
    # "RETRACTED: …", "Retracted article: …", "RETRACTED BY THE AUTHORS: …", "[Withdrawn] …"
    re.compile(r"^\W*(retracted|withdrawn)(\s+(article|paper|by\s+the\s+authors?))?\s*[:\]]", re.I),
    # A publisher's notice: "Retraction: …", "Retraction note to: …", "Notice of retraction: …"
    re.compile(r"^\W*(notice\s+of\s+retraction|retraction(\s+(note|notice))?)\s*(:|to\b)", re.I),
    # "… (Retracted)", "… [RETRACTED ARTICLE]"
    re.compile(r"[\[(]\s*(retracted|withdrawn)(\s+article)?\s*[\])]", re.I),
)
_RETRACTED_WORD_RE = re.compile(r"\b(RETRACTED|WITHDRAWN)\b")


def says_retracted(title) -> bool:
    """Whether a title marks its paper retracted or withdrawn. A title about
    retractions ("Retracted papers keep being cited") says no such thing: it takes
    the word as a label, at the start or in brackets, or in capitals in a title
    that is not all capitals."""
    text = _text(title)
    if any(pattern.search(text) for pattern in _RETRACTED_TITLE_RES):
        return True
    letters = [c for c in text if c.isalpha()]
    shouting = letters and sum(c.isupper() for c in letters) / len(letters) > 0.5
    return bool(_RETRACTED_WORD_RE.search(text)) and not shouting


def _check_retracted_titles(df):
    return [finding("retracted_title", [row], column, row.get(column),
                    f"{column} marks the paper retracted or withdrawn")
            for _, row in df.iterrows() for column in ("title_o", "title_r")
            if says_retracted(row.get(column))]


# Names an OSF component or a report goes by, which a title lookup can return in
# place of the paper's title.
GENERIC_TITLES = {"analysis", "analyses", "final report", "report", "results", "data",
                  "code", "manuscript", "draft", "untitled", "preregistration"}


def title_problem(title) -> "str | None":
    text = _text(title)
    if not text:
        return None
    if "_" in text and " " not in text:
        return "looks like a file or project name"
    if preprint_dedup.normalize_title(text) in GENERIC_TITLES:
        return "is a generic name, not a title"
    return None


def _check_file_name_titles(df):
    issues = []
    for _, row in df.iterrows():
        problem = title_problem(row.get("title_r"))
        if problem:
            issues.append(finding("file_name_title", [row], "title_r", row.get("title_r"),
                                  f"title_r {problem}"))
    return issues


# (code, level, name, heading, check, description). The code is the stable name a
# finding's key and the database use; the name is what "Checks passed" lists.
CHECKS = [
    ("invalid_url", PROBLEM, "All links are valid URLs", "Invalid URLs", _check_urls,
     "A url column holds something other than one complete http(s) link."),
    ("invalid_outcome", PROBLEM, "Outcome values valid", "Invalid outcome values",
     _check_outcomes,
     "An outcome outside the allowed set for the row's type, not_a_replication, or missing."),
    ("json_in_text", PROBLEM, "Text fields hold text", "JSON in a text field",
     _check_structured_text,
     "A title, journal or APA reference holds a JSON record instead of text."),
    ("invalid_type", PROBLEM, "Type values valid", "Invalid type values", _check_types,
     "type must be 'replication' or 'reproduction'."),
    ("invalid_source", PROBLEM, "Source values valid", "Invalid source values", _check_sources,
     "source must be one of the labels the build publishes."),
    ("implausible_year", PROBLEM, "Year ranges reasonable", "Implausible years",
     _check_year_ranges, f"year_o or year_r is outside {YEAR_MIN} to next year."),
    ("duplicate_record", PROBLEM, "No duplicate records", "Duplicate records",
     _check_duplicate_records,
     "Rows the build would have made one record: the same type, doi_o, doi_r and url_r, "
     "or the same DOIs with one url_r between them."),
    ("missing_required", PROBLEM, "Required fields present", "Missing required fields",
     _check_required_fields,
     "title_o, title_r, doi_r or url_r, and an identifier for the original: doi_o, a "
     "url_o that is a link or DOI (not a title), alt_identifier_o or oa_work_id_o."),
    ("invalid_doi", PROBLEM, "DOI format valid", "Invalid DOI format", _check_doi_format,
     "doi_o or doi_r is not a bare DOI (10.NNNN/…), or a DOI listed in alt_identifier_o "
     "or alt_identifier_r is malformed."),
    ("reference_conflict", PROBLEM, "DOI mapped to conflicting references",
     "DOI with conflicting references", _check_reference_conflicts,
     "The same DOI appears with references more than 20% apart."),
    ("retracted_title", PROBLEM, "No title marks a retraction", "Title says retracted",
     _check_retracted_titles, "title_o or title_r marks the paper retracted or withdrawn."),
    ("na_string", PROBLEM, "No 'NA'/'N/A' strings", "Literal 'NA'/'N/A' strings found",
     _check_na_strings,
     "A text column holds the literal string 'NA' instead of a missing value."),
    ("year_order", POSSIBLE_PROBLEM, "year_r >= year_o", "Replication year before original year",
     _check_year_order,
     "Could be swapped entries or a data-entry error. Not reported when the original "
     "has a preprint or other earlier version."),
    ("cross_type_pair", POSSIBLE_PROBLEM, "No paper pair under both types",
     "Same paper pair as replication and reproduction", _check_cross_type_pairs,
     "The same doi_o and doi_r (or url_r, without a doi_r) as a replication and as a "
     "reproduction: possibly one coding entered in both sheets."),
    ("reversed_pair", POSSIBLE_PROBLEM, "No pair entered both ways round",
     "Pair entered both ways round", _check_reversed_pairs,
     "One row's doi_o and doi_r are another row's doi_r and doi_o."),
    ("file_name_title", POSSIBLE_PROBLEM, "Replication titles look like titles",
     "Title looks like a file or project name", _check_file_name_titles,
     "title_r is a file or project name, or a generic name such as 'Analysis'."),
]


def validate(df, suppressions=None) -> dict:
    suppressions = suppressions or set()
    results, suppressed = [], 0

    for code, level, name, heading, fn, description in CHECKS:
        kept = []
        for item in fn(df):
            if is_suppressed(item, (code, name, heading), suppressions):
                suppressed += 1
            else:
                kept.append({**item, "level": level})
        results.append({"code": code, "level": level, "name": name, "heading": heading,
                        "description": description, "items": kept, "passed": not kept})

    problems = sum(len(r["items"]) for r in results if r["level"] == PROBLEM)
    possible = sum(len(r["items"]) for r in results if r["level"] == POSSIBLE_PROBLEM)
    return {"results": results, "suppressed": suppressed,
            "problems": problems, "possible_problems": possible,
            "has_problems": bool(problems), "has_issues": bool(problems or possible),
            "rows": len(df)}


def count_text(problems: int, possible: int) -> str:
    """"3 problems and 1 possible problem", leaving out a level with none."""
    parts = [f"{n:,} {word}{'' if n == 1 else 's'}"
             for n, word in ((problems, "problem"), (possible, "possible problem")) if n]
    return " and ".join(parts) or "no problems"


# ── report ────────────────────────────────────────────────────────────────────

ITEM_FORMAT = ("Each item names the FLoRA record by its FLoRA ID and `doi_o | doi_r` "
               "(or `url_r`), says what is wrong and, after the dash, gives the value.")


def _code(value) -> str:
    """A value as inline code, on one line and shortened, so no title or link in it
    can turn into Markdown. The fence is longer than any run of backticks in the
    value, and padded with a space, which Markdown removes, when it holds one: a
    backtick in the value can then neither close the span nor merge with its fence."""
    text = " ".join(str(value).split())
    if len(text) > SHOWN_VALUE:
        text = text[:SHOWN_VALUE - 1] + "…"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence, pad = "`" * (longest + 1), " " if longest else ""
    return f"{fence}{pad}{text}{pad}{fence}"


# What could start a code span, emphasis, a link or an HTML tag in running text.
_MARKDOWN_RE = re.compile(r"([\\`*\[\]<>])")


def plain(text) -> str:
    """Text that comes from data or from a server (a title Crossref gives, an error
    message), on one line and with Markdown's special characters escaped, so it
    reads as written. The checks' own wording has none of them."""
    return _MARKDOWN_RE.sub(r"\\\1", " ".join(str(text).split()))


def item_line(item) -> str:
    """One finding as a list item's text."""
    if isinstance(item, str):              # a report stored before findings were records
        return item
    ids = item.get("flora_ids") or []
    who = ", ".join(ids[:SHOWN_IDS])
    if len(ids) > SHOWN_IDS:
        who += f" and {len(ids) - SHOWN_IDS:,} more"
    if item.get("label"):
        # The label is the rows' own DOIs and links: code, as the value is.
        label = _code(item["label"])
        who = f"{who} ({label})" if who else label
    text = f"{who}: {plain(item['detail'])}" if who else plain(item["detail"])
    if item.get("value") is not None:
        text += f" — {_code(item['value'])}"
    return text


def render_section(heading, items, description=None) -> list:
    """A check's findings: a bold heading and a list, folded in <details> when long."""
    n = len(items)
    intro = [f"_{description}_", ""] if description else []
    lines = [f"- {item_line(item)}" for item in items]
    if n <= DETAILS_THRESHOLD:
        return [f"**{heading}** ({n} finding{'s' if n != 1 else ''}):", ""] + intro + lines + [""]
    return ([f"<details><summary><b>{heading}</b> ({n:,} findings)</summary>", ""]
            + intro + lines + ["", "</details>", ""])


def render(report: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = [
        "## FLoRA structural validation",
        "",
        f"_Generated: {stamp} UTC | Dataset: {report['rows']:,} rows_",
        "",
        ITEM_FORMAT,
        "",
    ]

    # A report stored before the levels existed has none: each of its checks was
    # reported as a problem.
    failed = [r for r in report["results"] if not r["passed"]]
    passed = [r for r in report["results"] if r["passed"]]
    for level, title in LEVEL_TITLES.items():
        mine = [r for r in failed if r.get("level", PROBLEM) == level]
        if not mine:
            continue
        out += [f"### {title} ({sum(len(r['items']) for r in mine):,})", ""]
        if level in LEVEL_NOTES:
            out += [f"_{LEVEL_NOTES[level]}_", ""]
        for result in mine:
            out += render_section(result["heading"], result["items"], result["description"])

    if passed:
        out += ["---", "", "**Checks passed:**", ""]
        out += [f"- {r['name']}" for r in passed]
        out.append("")

    if report["suppressed"]:
        out.append(suppressed_note(report["suppressed"]))
    return "\n".join(out)


def suppressed_note(count: int) -> str:
    return (f"_{count} item{'s' if count != 1 else ''} not shown: listed as false positives "
            f"in `{SUPPRESSIONS_FILE.name}`._")


def load_dataset(cur):
    """The product as it is published, with the output contract's column names.

    Untitled rows are dropped here for the same reason the export drops them: the
    R notebook validates output/flora.csv, which has already been through Step 10.
    Reporting a row that was never published would put an item in the report that
    nobody can resolve — it is already recorded in output/flora_export_log.csv and
    counted on the FLoRA tab.
    """
    import flora_edits
    import flora_registry
    frame = transform_sources.build(cur, verbose=False)
    frame = flora_registry.attach_ids(cur, frame)
    frame = flora_edits.apply(cur, frame)
    return transform_sources.drop_untitled(transform_sources.to_output_shape(frame))


def main() -> int:
    start_logging("validate")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="write the report here instead of stdout")
    parser.add_argument("--input", type=Path,
                        help="validate this exact CSV instead of rebuilding from the database")
    parser.add_argument("--fail-on-issues", action="store_true",
                        help="exit 1 when a Problem is found; Possible problems never fail (for CI)")
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
    print(f"\n{failed}/{len(report['results'])} check(s) found "
          f"{count_text(report['problems'], report['possible_problems'])}", file=sys.stderr)
    return 1 if (args.fail_on_issues and report["has_problems"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
