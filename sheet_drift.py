"""
sheet_drift.py — which imported rows were edited in their sheet after the import.

sync_sources.py is insert-only: a row is stored the first time it is seen as
accepted, and an edit made in the sheet after that never reaches source_records.
That is deliberate (the database is authoritative once a row lands), but it meant
a coder fixing an outcome or a DOI in the sheet was silently ignored. So every
sync that passes its gates also compares each imported row's current sheet values
with `source_records.raw`, the row exactly as it was imported, and reports what
differs. Nothing is written back: the admin decides, per difference, whether the
FLoRA record takes the sheet's value (Source Records > Sheet edits).

This module is the comparison alone. Pure on purpose: no database and no pandas,
so the rules can be tested cell by cell and sync_sources.py stores what it finds
(_store_drift). sheet_drift_service.py says, when the admin looks, what FLoRA
shows for each difference.

What counts as a difference (v1): only the sheet columns that are promoted to a
source_records column, plus the validation column. Notes columns (Coder,
validator_notes, prep_notes, frq) change all the time and feed nothing, so they
are left out. The id column and pandas' `Unnamed: N` columns are skipped. A
promoted column the row's `raw` does not have (added to the sheet after the row
was imported) is compared with the source_records column instead.

Values are compared normalised (normalise): spacing, line breaks, invisible
characters, a DOI's URL prefix, a URL's scheme and trailing slash, a year's
".0" and the case of an outcome or a status are not edits. The stored values stay
verbatim. RULES_VERSION is part of every check's record, so changing these rules
makes the next sync check an unchanged sheet again.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from extractor_vocab import normalize_axis_value

# Bump whenever normalise() or column_rule() changes what counts as a difference:
# an unchanged sheet is otherwise not compared again (sync_sources._drift_due).
RULES_VERSION = 1

# A missing row (its UUID gone from the sheet) is recorded only while there are few
# of them. Past this many, a filter, a sort gone wrong or a half-pasted tab is more
# likely than real deletions, so they are reported in the check's notes instead.
MISSING_CAP_MIN = 20
MISSING_CAP_SHARE = 0.05

# Kinds of finding, as sheet_drift_findings.kind stores them.
EDITED = "edited"
NOT_ACCEPTED = "not_accepted"
MISSING = "missing"

# Removed rather than read as a space: a zero-width space or a soft hyphen inside a
# word is an invisible character, not a word break. \s below covers NBSP, CR and LF.
_INVISIBLE_RE = re.compile("[­​‌‍⁠﻿]")
_SPACE_RE = re.compile(r"\s+")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/")
_DOI_LABEL_RE = re.compile(r"^doi:\s*")
_YEAR_RE = re.compile(r"^(\d{4})\.0+$")
_UNNAMED_RE = re.compile(r"^Unnamed: \d+")

# Comparison rule by source_records column; anything else is text.
_RULES = {
    "doi_o": "doi", "doi_r": "doi",
    "url_o": "url", "url_r": "url",
    "year_r": "year",
    "outcome": "casefold",
    "validation_status": "casefold",
    "outcome_computation": "axis:outcome_computation",
    "outcome_robustness": "axis:outcome_robustness",
}


@dataclass(frozen=True)
class Finding:
    """One cell of one imported row that differs from the import, or a whole row
    gone from the sheet (kind MISSING, sheet_column '')."""
    record_id: str
    sheet_row_id: str
    kind: str
    sheet_column: str
    record_column: "str | None"
    value_at_import: "str | None"
    value_in_sheet: "str | None"
    sheet_row_number: "int | None"

    def key(self) -> tuple:
        return (self.sheet_row_id, self.sheet_column)


@dataclass
class Comparison:
    """What one source's check found.

    compared   sheet ids of imported rows present exactly once in the sheet: their
               cells were compared, so their open findings not found again resolve
    missing    sheet ids of imported rows no longer in the sheet
    ambiguous  sheet ids of imported rows present more than once in the sheet (any
               status): left exactly as they were, as the sync skips them
    columns    the sheet columns compared, so only findings on those resolve
    """
    findings: list = field(default_factory=list)
    compared: set = field(default_factory=set)
    missing: set = field(default_factory=set)
    ambiguous: set = field(default_factory=set)
    missing_recorded: bool = True
    notes: list = field(default_factory=list)
    rows_compared: int = 0
    columns: set = field(default_factory=set)


def _text(value) -> str:
    """NFC, invisible characters removed, every run of whitespace one space."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _INVISIBLE_RE.sub("", text)
    return _SPACE_RE.sub(" ", text).strip()


def _norm_url(text: str) -> str:
    """sync_sources._norm_url, the import's own URL comparison."""
    text = text.lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.rstrip("/")


def normalise(rule: str, value) -> str:
    """A value as the comparison sees it under `rule`; '' for blank."""
    text = _text(value)
    if not text:
        return ""
    if rule == "doi":
        text = _DOI_LABEL_RE.sub("", _DOI_PREFIX_RE.sub("", text.lower()))
        return text
    if rule == "url":
        return _norm_url(text)
    if rule == "year":
        match = _YEAR_RE.match(text)
        return match.group(1) if match else text
    if rule == "casefold":
        return text.casefold()
    if rule.startswith("axis:"):
        # The import canonicalises the axes the same way (_build_row), so an
        # archived spelling is not an edit. An unknown value still compares.
        try:
            return normalize_axis_value(rule[len("axis:"):], text) or ""
        except ValueError:
            return text.casefold()
    return text


def rule_for(record_column: "str | None") -> str:
    """The comparison rule of a source_records column."""
    return _RULES.get(record_column or "", "text")


def column_rule(cfg: dict, sheet_column: str) -> "tuple[str | None, str | None]":
    """(record_column, rule) for one sheet header, or (None, None) when the column
    is not compared. Works on the header as the sheet reads it; column_map gives
    only the source_records column it is promoted to."""
    if not sheet_column or sheet_column == cfg.get("id_column") or _UNNAMED_RE.match(sheet_column):
        return None, None
    record_column = (cfg.get("column_map") or {}).get(sheet_column, sheet_column)
    promoted = record_column in (cfg.get("promoted") or [])
    if sheet_column == cfg.get("validation_column"):
        return (record_column if promoted else None), "casefold"
    if not promoted:
        return None, None
    return record_column, rule_for(record_column)


def _verbatim(value) -> "str | None":
    """A value as stored in a finding: as the sheet or the import had it, None for
    blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def compare_row(raw: "dict | None", now: dict, record: dict, cfg: dict,
                accepted_values, columns=None) -> list:
    """The cells of one imported row that differ, as (kind, sheet_column,
    record_column, value_at_import, value_in_sheet) tuples.

    `raw` is the row as imported, `now` the row in the sheet today, `record` the
    source_records row (for a promoted column `raw` lacks), `columns` the sheet's
    current header (default: `now`'s keys)."""
    raw = raw or {}
    accepted = {normalise("casefold", value) for value in accepted_values or ()}
    validation_column = cfg.get("validation_column")
    found = []
    for sheet_column in (columns if columns is not None else now.keys()):
        record_column, rule = column_rule(cfg, sheet_column)
        if rule is None:
            continue
        if sheet_column in raw:
            at_import = raw[sheet_column]
        elif record_column:
            at_import = record.get(record_column)
        else:
            continue
        in_sheet = now.get(sheet_column)
        if normalise(rule, at_import) == normalise(rule, in_sheet):
            continue
        kind = EDITED
        if sheet_column == validation_column and normalise(rule, in_sheet) not in accepted:
            kind = NOT_ACCEPTED
        found.append((kind, sheet_column, record_column, _verbatim(at_import), _verbatim(in_sheet)))
    return found


def missing_cap(imported_count: int) -> int:
    """How many missing rows one check records at most."""
    return max(MISSING_CAP_MIN, int(imported_count * MISSING_CAP_SHARE))


def compare_source(sheet_rows: list, imported: list, cfg: dict, accepted_values: list,
                   header_row: int = 0, header=None) -> Comparison:
    """Compare every imported row of one source with the sheet as fetched now.

    `sheet_rows` is every fetched row, accepted or not, as {header: text}, in sheet
    order; `imported` the source's live source_records rows as dicts with
    record_id, sheet_row_id, raw and the data columns. `header_row` is the
    registry's 0-based header row, so a finding can say which row of the tab it is
    on (header_row + 2 + index: one for the header, one for counting from 1).
    `header` is the sheet's columns, when there may be no rows to read them from.
    """
    id_column = cfg["id_column"]
    columns = list(header) if header is not None else list(sheet_rows[0].keys()) if sheet_rows else []
    comparison = Comparison()
    comparison.columns = {column for column in columns if column_rule(cfg, column)[1] is not None}

    # Over every fetched row, not only accepted ones: a copy-pasted row that is
    # still unvalidated duplicates the UUID just the same, and then the sheet has
    # no single answer for that row.
    seen = {}
    for index, row in enumerate(sheet_rows):
        # Stripped only, as the sync keys a row (sync_sources._s).
        sheet_id = str(row.get(id_column) or "").strip()
        if sheet_id:
            seen.setdefault(sheet_id, []).append(index)

    missing_rows = []
    for record in imported:
        sheet_id = record["sheet_row_id"]
        places = seen.get(sheet_id, [])
        if len(places) > 1:
            comparison.ambiguous.add(sheet_id)
            continue
        if not places:
            comparison.missing.add(sheet_id)
            missing_rows.append(record)
            continue
        index = places[0]
        comparison.compared.add(sheet_id)
        for kind, sheet_column, record_column, at_import, in_sheet in compare_row(
                record.get("raw"), sheet_rows[index], record, cfg, accepted_values, columns):
            comparison.findings.append(Finding(
                str(record["record_id"]), sheet_id, kind, sheet_column, record_column,
                at_import, in_sheet, header_row + 2 + index))
    comparison.rows_compared = len(comparison.compared)

    cap = missing_cap(len(imported))
    if len(missing_rows) > cap:
        comparison.missing_recorded = False
        comparison.notes.append(
            f"{len(missing_rows)} imported rows are not in the sheet, more than the "
            f"{cap} one check records; not recorded. Check the tab for a filter, a "
            "sort or a deletion.")
    else:
        for record in missing_rows:
            comparison.findings.append(Finding(
                str(record["record_id"]), record["sheet_row_id"], MISSING, "", None,
                None, None, None))
    if comparison.ambiguous:
        comparison.notes.append(
            f"{len(comparison.ambiguous)} imported row(s) appear more than once in the "
            "sheet and were left as they were.")
    return comparison
