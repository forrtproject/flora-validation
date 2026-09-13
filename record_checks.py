"""Row-local data-quality checks, run once a record has finished consensus.

These are the half of the FLoRA pipeline's `validate_flora.R` that can be
decided from a single record and whose answer never changes on its own:
required fields, DOI shape, literal 'NA' strings, URL shape, the type and
outcome vocabularies, and the two year checks.

The other half deliberately stays on the pipeline's cron job, because it cannot
be answered here:

  * conflicting references for one DOI, and exact duplicates — both need the
    whole dataset, not one row;
  * retractions, DOI resolution and URL resolution — all time-varying. A paper
    can be retracted, and a link can rot, long after this record was validated,
    so a one-shot check at consensus would be worse than useless: it would read
    as a clean bill of health that silently goes stale.

Flags never change where a record lands. `evaluate_consensus` stores them and
the admin review panel shows them; a human decides whether the record should be
excluded. That is deliberate — several of these checks have real false-positive
rates, and an automated exclusion would be harder to notice than a red flag.

Two adaptations to this app's data model, without which the checks would be
mostly noise:

  * `doi_o` is legitimately empty for originals with no registered DOI (books,
    chapters, pre-DOI papers); the app keeps their identity in `oa_work_id_o`.
    A blank `doi_o` is only flagged when there is no OpenAlex identity either.
  * `year_o`/`year_r` are TEXT and arrive spelled inconsistently ('2020',
    '2020.0', ' 2020'), so years are parsed from the leading four digits rather
    than compared as strings.
"""

from __future__ import annotations

import re
from datetime import date

from extractor_vocab import OUTCOME_AXES, REPLICATION_OUTCOMES

# Mirrors is_doi() in validate_flora.R.
_DOI_RE = re.compile(r"^10\.[0-9]{4,}/\S+$")

# Mirrors the R check: the literal strings, not a missing value.
_NA_STRINGS = {"NA", "N/A"}

_URL_FIELDS = ("url_r", "url_o", "oa_url_r", "oa_url_o")

# Text fields worth scanning for a literal 'NA'. Deliberately not every column:
# free text (abstracts, quotes, references) can legitimately contain "NA" as a
# word or an initialism, and flagging those would bury the real hits.
_NA_SCAN_FIELDS = (
    "doi_r", "doi_o", "title_r", "title_o", "study_r", "study_o",
    "year_r", "year_o", "url_r", "url_o", "type", "outcome",
)

_YEAR_MIN = 1890
_YEAR_RE = re.compile(r"^\s*([0-9]{4})")

VALID_TYPES = frozenset({"replication", "reproduction"})


def _s(value) -> str:
    """Trimmed string; None and NaN-ish values become ''."""
    if value is None:
        return ""
    return str(value).strip()


def _year(value):
    """Leading four digits as an int, or None when there is no year to read."""
    m = _YEAR_RE.match(_s(value))
    return int(m.group(1)) if m else None


def effective_record(record: dict, final: dict | None = None) -> dict:
    """The values that would actually be published.

    Consensus resolves corrections into `final`, and only some branches produce
    one. Checking the raw record when a correction exists would flag a problem a
    validator has already fixed, so `final` wins wherever it has a value.
    """
    merged = dict(record or {})
    for key, value in (final or {}).items():
        if value is not None:
            merged[key] = value
    # Branches that resolve no `final` still have the record's own final_* columns
    # from an earlier pass; prefer those over the raw extractor values.
    for key in ("title_r", "title_o", "doi_o", "outcome", "type", "url_r"):
        carried = merged.get(f"final_{key}")
        if carried is not None and _s(carried):
            merged[key] = carried
    return merged


def _flag(code: str, label: str, detail: str) -> dict:
    return {"code": code, "label": label, "detail": detail}


def check_record(record: dict, final: dict | None = None) -> list[dict]:
    """Every row-local check that fails for this record, as flag dicts.

    An empty list means nothing was found. Order is stable so that a record's
    flags do not churn between runs.
    """
    r = effective_record(record, final)
    flags: list[dict] = []

    doi_o, doi_r = _s(r.get("doi_o")), _s(r.get("doi_r"))
    url_r = _s(r.get("url_r"))
    oa_work_id_o = _s(r.get("oa_work_id_o"))

    # --- Required fields (R check 13), adapted for DOI-less originals ---------
    missing = [name for name in ("title_o", "title_r") if not _s(r.get(name))]
    if not doi_o and not oa_work_id_o:
        # A DOI-less original is fine; one with no identity at all is not.
        missing.append("doi_o (and no oa_work_id_o)")
    if not doi_r and not url_r:
        missing.append("doi_r or url_r")
    if missing:
        flags.append(_flag(
            "missing_required_fields",
            "Missing required fields",
            ", ".join(missing),
        ))

    # --- DOI format (R check 14) ---------------------------------------------
    # A blank doi_o is not malformed — see the module docstring.
    bad_dois = [
        f"{name}='{value}'"
        for name, value in (("doi_o", doi_o), ("doi_r", doi_r))
        if value and not _DOI_RE.match(value)
    ]
    if bad_dois:
        flags.append(_flag(
            "invalid_doi_format",
            "DOI does not match 10.NNNN/…",
            "; ".join(bad_dois),
        ))

    # --- Literal 'NA'/'N/A' strings (R check 1) ------------------------------
    na_hits = [
        f"{name}='{_s(r.get(name))}'"
        for name in _NA_SCAN_FIELDS
        if _s(r.get(name)) in _NA_STRINGS
    ]
    if na_hits:
        flags.append(_flag(
            "na_string",
            "Literal 'NA' stored as a value",
            "; ".join(na_hits),
        ))

    # --- URLs look like URLs (R check 2) -------------------------------------
    bad_urls = [
        f"{name}='{_s(r.get(name))}'"
        for name in _URL_FIELDS
        if _s(r.get(name)) and not _s(r.get(name)).lower().startswith("http")
    ]
    if bad_urls:
        flags.append(_flag(
            "non_http_url",
            "URL does not start with http",
            "; ".join(bad_urls),
        ))

    # --- Type vocabulary (R check 8) -----------------------------------------
    rec_type = _s(r.get("type"))
    if rec_type and rec_type not in VALID_TYPES:
        flags.append(_flag("invalid_type", "Unknown type", f"type='{rec_type}'"))

    # --- Outcome vocabulary (R check 7) --------------------------------------
    # Validate what is actually coded: a replication's flat outcome, and a
    # reproduction's two independent axes. The reproduction's flat outcome is
    # derived from those axes, so checking it as well would double-report.
    if rec_type == "replication":
        outcome = _s(r.get("outcome"))
        if outcome and outcome not in REPLICATION_OUTCOMES:
            flags.append(_flag(
                "invalid_outcome",
                "Outcome outside the replication vocabulary",
                f"outcome='{outcome}'",
            ))
    elif rec_type == "reproduction":
        bad_axes = [
            f"{axis}='{_s(r.get(axis))}'"
            for axis, allowed in OUTCOME_AXES.items()
            if _s(r.get(axis)) and _s(r.get(axis)) not in allowed
        ]
        if bad_axes:
            flags.append(_flag(
                "invalid_outcome",
                "Reproduction axis outside its vocabulary",
                "; ".join(bad_axes),
            ))

    # --- Year sanity (R checks 10 and 11) ------------------------------------
    year_max = date.today().year + 1
    year_o, year_r = _year(r.get("year_o")), _year(r.get("year_r"))
    out_of_range = [
        f"{name}={value}"
        for name, value in (("year_o", year_o), ("year_r", year_r))
        if value is not None and not (_YEAR_MIN <= value <= year_max)
    ]
    if out_of_range:
        flags.append(_flag(
            "year_out_of_range",
            f"Year outside {_YEAR_MIN}–{year_max}",
            "; ".join(out_of_range),
        ))

    if year_o is not None and year_r is not None and year_r < year_o:
        flags.append(_flag(
            "replication_before_original",
            "Replication predates the original",
            f"year_o={year_o}; year_r={year_r}",
        ))

    return flags
