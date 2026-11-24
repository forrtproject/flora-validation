"""
sheet_drift_service.py — Source Records > Sheet edits: what each difference means now.

sync_sources.py keeps, per imported sheet row, the cells the sheet has changed
since the import (sheet_drift.py, table sheet_drift_findings). This module says
what each of them means for FLoRA when someone looks, from three values: the value
at import (A), the sheet now (S) and the FLoRA record now (F). F comes from
flora_service.dataset(), the frame the FLoRA editor shows with its corrections
applied, so a correction saved a moment ago already counts.

Nothing here writes to source_records or to a sheet. The one write is the "Keep
FLoRA value" decision on a finding (keep, undo_keep). Taking the sheet's value
into FLoRA is an ordinary correction saved in the FLoRA editor (flora_edits.py),
so it is logged, noted and published like any other.

States, checked in this order for each FLoRA record the row feeds:

  not_in_flora   the row feeds no live record: ruled a duplicate, excluded,
                 retired or not built yet
  not_validated  the sheet no longer marks the row validated         needs review
  row_removed    the row is gone from the sheet                       needs review
  no_field       the column is not a FLoRA field on this record's type
  matches        FLoRA already has the sheet's value
  kept           someone kept FLoRA's value, and the sheet still says what
                 they saw then
  metadata       FLoRA uses publisher metadata here and nobody changed it
  pending        FLoRA still has the imported value                   needs review
  conflict       FLoRA differs from both: a correction, an old source edit, the
                 row it was merged into, or the build                 needs review

A missing FLoRA record is checked first rather than after the row-level states:
a row ruled a duplicate and then deleted from the sheet leaves nothing to decide.
The UI never says "conflict": the FLoRA tab uses the word for duplicate outcome
clashes.

Transport-agnostic like the other services: RealDictCursor in, plain dicts out.
"""
import csv
import io
import logging
import re
from pathlib import Path

import yaml

import flora_edits
import flora_service
import sheet_drift
import transform_sources
from cos_quote_rewrite import mark_coding_note, rewrite_one
from extractor_vocab import REPLICATION_OUTCOMES, normalize_axis_value, normalize_outcome

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent / "sources.yml"

# The "Keep FLoRA value" note: required, as long as a check finding's comment.
KEEP_NOTE_MIN = 3
KEEP_NOTE_MAX = 1000

NOT_IN_FLORA = "not_in_flora"
NOT_VALIDATED = "not_validated"
ROW_REMOVED = "row_removed"
NO_FIELD = "no_field"
MATCHES = "matches"
KEPT = "kept"
METADATA = "metadata"
PENDING = "pending"
CONFLICT = "conflict"
UNKNOWN = "unknown"          # FLoRA could not be built, so nothing can be compared
RESOLVED = "resolved"        # the sheet went back to the imported value

# What needs someone. UNKNOWN too: a failed build must not make the list look done.
NEEDS_REVIEW = frozenset({NOT_VALIDATED, ROW_REMOVED, PENDING, CONFLICT, UNKNOWN})
ROW_STATES = frozenset({NOT_VALIDATED, ROW_REMOVED})

# Worst first: a record shows its worst field.
_SEVERITY = [ROW_REMOVED, NOT_VALIDATED, CONFLICT, PENDING, UNKNOWN, KEPT, METADATA,
             MATCHES, NO_FIELD, NOT_IN_FLORA, RESOLVED]

STATE_LABELS = {
    NOT_IN_FLORA: "Not in FLoRA",
    NOT_VALIDATED: "No longer marked validated in the sheet",
    ROW_REMOVED: "Row deleted from the sheet",
    NO_FIELD: "Not a FLoRA field for this type",
    MATCHES: "FLoRA already has the sheet's value",
    KEPT: "Kept FLoRA value",
    METADATA: "FLoRA uses publisher data",
    PENDING: "Sheet changed, FLoRA not yet",
    CONFLICT: "Changed in both places",
    UNKNOWN: "FLoRA could not be checked",
    RESOLVED: "Resolved: the sheet has the imported value again",
}

STATE_EXPLAINED = {
    NOT_IN_FLORA: "This row feeds no FLoRA record, so there is nothing to change.",
    NOT_VALIDATED: "The sheet's status is no longer an accepted one. FLoRA still has the "
                   "record; remove it through Exclusions or a duplicate ruling, or keep it.",
    ROW_REMOVED: "The row's sheet ID is no longer in the sheet. FLoRA still has the record.",
    NO_FIELD: "FLoRA has no field for this column on a record of this type.",
    MATCHES: "FLoRA already shows what the sheet says now. Nothing to do.",
    KEPT: "Someone decided FLoRA's value is right. It is asked again if the sheet changes.",
    METADATA: "FLoRA takes this field from the publisher's metadata, not from the sheet.",
    PENDING: "The sheet was corrected after import and FLoRA still has the imported value.",
    CONFLICT: "FLoRA's value differs from both the import and the sheet now.",
    UNKNOWN: "FLoRA could not be built just now, so its value is not known.",
    RESOLVED: "The sheet went back to the value it was imported with.",
}

# The filters of the view. needs_review, nothing_to_do, resolved and all are the
# select's; the others narrow by one field state; open is any unresolved field.
FILTERS = ("needs_review", "nothing_to_do", "resolved", "all", "open",
           "pending", "conflict", "row", "kept", "matches", "info")

# source_records column -> (FLoRA frame field, rule). Frame names, as the editor's
# data-field, not the published ones: ref_o is published as apa_ref_o.
FLORA_FIELDS = {
    "ref_o": ("ref_o", "reference"),
    "ref_r": ("ref_r", "reference"),
    "doi_o": ("doi_o", "doi"),
    "doi_r": ("doi_r", "doi"),
    "url_o": ("url_o", "url_o"),
    "url_r": ("url_r", "url_r"),
    "alt_identifier_o": ("alt_identifier_o", "identifiers"),
    "alt_identifier_r": ("alt_identifier_r", "identifiers"),
    "abstract_r": ("abstract_r", "fill"),
    "year_r": ("year_r", "year"),
    "outcome": ("outcome", "outcome"),
    "outcome_quote": ("outcome_quote", "quote"),
    "out_quote_source": ("outcome_quote_source", "quote_source"),
    "outcome_computation": ("outcome_computation", "axis"),
    "outcome_robustness": ("outcome_robustness", "axis"),
    "outcome_computational_quote": ("outcome_computational_quote", "text"),
    "out_quote_computational_source": ("out_quote_computational_source", "text"),
    "outcome_robustness_quote": ("outcome_robustness_quote", "text"),
    "out_quote_robust_source": ("out_quote_robust_source", "text"),
}
# Publisher metadata wins over the sheet for these when the DOI resolves.
METADATA_RULES = frozenset({"reference", "year"})

CSV_COLUMNS = [
    "finding_id", "display_id", "flora_id", "flora_role", "flora_version", "source",
    "sheet_label", "sheet_row_id", "sheet_row_number", "kind", "sheet_column",
    "flora_field", "flora_column", "value_at_import", "value_in_sheet", "flora_value",
    "flora_value_source", "state", "state_explained", "suggested_action",
    "sheet_value_for_flora", "first_seen_at", "last_seen_at", "kept_by", "kept_at",
    "kept_note", "decision", "new_flora_value", "decision_note",
]
# Free text people typed. A spreadsheet runs a cell starting with one of _FORMULA
# as a formula, so these get a leading apostrophe; data columns stay exactly as
# stored, for the apply script to compare (adjudication/export.py does the same).
CSV_NOTE_COLUMNS = frozenset({"state_explained", "kept_note", "decision_note"})
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


class FindingNotFound(LookupError):
    """No sheet edit with that id."""


class StaleFinding(Exception):
    """The sheet changed after the caller loaded the finding."""

    def __init__(self, value_in_sheet):
        super().__init__("The sheet changed since this page loaded.")
        self.value_in_sheet = value_in_sheet


# ── the registry ──────────────────────────────────────────────────────────────

_SOURCES = None


def _sources() -> list:
    """Every sheet source of sources.yml, in its order: {key, label, enabled}."""
    global _SOURCES
    if _SOURCES is None:
        registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
        _SOURCES = [{"key": s["key"], "label": s.get("label") or s["key"],
                     "enabled": s.get("enabled", True)} for s in registry.get("sources") or []]
    return _SOURCES


def source_label(key: str) -> str:
    return next((s["label"] for s in _sources() if s["key"] == key), key)


def _source_order(key: str) -> int:
    keys = [s["key"] for s in _sources()]
    return keys.index(key) if key in keys else len(keys)


# ── comparing values the way the build does ───────────────────────────────────

def _plain(value) -> str:
    """Text with spacing, line breaks and invisible characters evened out; '' for
    blank. 2020.0 from the metadata cache is the year 2020 (flora_edits._text)."""
    return sheet_drift._text(flora_edits._text(value))


def _cleaned(value, one_line=True) -> str:
    """As the build's text cleaning leaves it (markup, entities), then evened out."""
    text = _plain(value)
    if not text:
        return ""
    return sheet_drift._text(transform_sources.clean_text(text, one_line=one_line) or "")


def _folded_aliases(aliases: dict) -> dict:
    return {str(raw).strip().casefold(): canonical for raw, canonical in (aliases or {}).items()}


def _outcome(value, aliases: dict) -> "tuple[str | None, str | None]":
    """(canonical outcome, None), (None, None) for blank, or (None, why) for a
    spelling FLoRA does not know. The build reads outcome_alias exactly; a case
    difference is accepted here, as the import's comparison ignores it."""
    text = _plain(value)
    if not text:
        return None, None
    canonical = (aliases or {}).get(text) or _folded_aliases(aliases).get(text.casefold()) \
        or normalize_outcome(text)
    if canonical in REPLICATION_OUTCOMES:
        return canonical, None
    return None, (f"The sheet's outcome “{text}” is not one FLoRA knows. Choose the "
                  "outcome in the editor, or add the spelling to outcome_alias.")


def _context(field: str, flora_row: dict, record_row: dict, aliases: dict) -> dict:
    """What the url rules compare against: the DOIs of the record and of the row."""
    def dois(side):
        values = {transform_sources.clean_doi(_plain(flora_row.get(f"doi_{side}")) or None),
                  transform_sources.clean_doi(_plain(record_row.get(f"doi_{side}")) or None)}
        return {doi for doi in values if doi}
    return {"field": field, "aliases": aliases,
            "landing_pages": {transform_sources.norm_url("https://doi.org/" + doi) for doi in dois("o")},
            "dois_r": dois("r")}


def _view(rule: str, value, ctx: dict) -> str:
    """A value as `rule` compares it: '' for blank, and for what the build itself
    treats as blank (an original's DOI landing page, a url_r restating doi_r)."""
    if rule == "reference":
        # The build appends "Individual report available at <url_r>" to a
        # meta-paper's reference (transform_sources, step 9).
        return _cleaned(value).split(" " + transform_sources.META_PAPER_NOTE)[0].strip()
    if rule == "doi":
        doi = transform_sources.clean_doi(_plain(value) or None) or ""
        return "" if transform_sources.DUMMY_DOI_RE.match(doi) else doi
    if rule in ("url_o", "url_r"):
        url = transform_sources.norm_url(_plain(value) or None) or ""
        if rule == "url_o" and url in ctx["landing_pages"]:
            return ""
        if rule == "url_r" and url and transform_sources.clean_doi(_plain(value)) in ctx["dois_r"]:
            return ""
        return url
    if rule == "identifiers":
        # Order and separators are not an edit: "a; b" is "b, a".
        return " ".join(sorted({part for part in re.split(r"[\s,;|]+", _plain(value).lower()) if part}))
    if rule == "year":
        text = _plain(value)
        return transform_sources._sheet_year(text) or text
    if rule == "outcome":
        canonical, _ = _outcome(value, ctx["aliases"])
        return canonical or ("?" + _plain(value).casefold() if _plain(value) else "")
    if rule == "axis":
        text = _plain(value)
        try:
            return normalize_axis_value(ctx["field"], text) or ""
        except ValueError:
            return "?" + text.casefold()
    # fill, quote, quote_source, text
    return _cleaned(value, one_line=False)


def _same(rule: str, flora_value, value, ctx: dict) -> bool:
    """Whether FLoRA's value is `value` as the build would have published it."""
    flora = _view(rule, flora_value, ctx)
    if flora == _view(rule, value, ctx):
        return True
    text = _plain(value)
    # The build rewrites coders' COS shorthand into a sentence, and marks the
    # rewritten quote's source a coding note (cos_quote_rewrite).
    if rule == "quote" and text and flora:
        return flora == _view(rule, rewrite_one(text), ctx)
    if rule == "quote_source" and flora:
        return flora == _view(rule, mark_coding_note(text or None), ctx)
    return False


def sheet_value_for_flora(field: str, rule: str, value, record_type: str,
                          ctx: dict) -> "tuple[str | None, str | None]":
    """(the sheet's value as FLoRA stores it, None), or (None, why it cannot be).
    The checks of flora_edits.clean_value, so a value offered here saves."""
    raw = flora_edits._text(value)
    if raw is None:
        return None, None                     # a cleared cell: the field becomes blank
    if rule == "outcome":
        raw, error = _outcome(raw, ctx["aliases"])
        if error:
            return None, error
    elif rule == "year":
        raw = transform_sources._sheet_year(raw) or raw
    elif rule == "url_r" and not _view(rule, raw, ctx):
        return None, None                     # restates doi_r: the build drops it
    try:
        return flora_edits.clean_value(field, raw, record_type), None
    except ValueError as exc:
        return None, str(exc)


def _kept_current(finding: dict) -> bool:
    """A keep stands while the sheet still says what it said when it was made."""
    if not finding.get("kept_at"):
        return False
    rule = sheet_drift.rule_for(finding.get("record_column"))
    return (sheet_drift.normalise(rule, finding.get("kept_sheet_value"))
            == sheet_drift.normalise(rule, finding.get("value_in_sheet")))


def _source_edited(finding: dict, record_row: dict) -> bool:
    """Whether the source_records column was changed by hand (the old Source Records
    editing): it no longer says what the row's raw import said. From the finding's
    raw_has / raw_value / record_value when _load() read them (only those cells, not
    every row's raw), else from the source row's own raw."""
    column, header = finding.get("record_column"), finding.get("sheet_column")
    if not column:
        return False
    if "raw_has" in finding:
        if not finding["raw_has"]:
            return False
        imported, current = finding.get("raw_value"), finding.get("record_value")
    else:
        raw = record_row.get("raw") or {}
        if header not in raw:
            return False
        imported, current = raw[header], record_row.get(column)
    rule = sheet_drift.rule_for(column)
    return sheet_drift.normalise(rule, imported) != sheet_drift.normalise(rule, current)


def _when(value) -> str:
    """3 Nov 2026."""
    return f"{value.day} {value:%b %Y}" if hasattr(value, "day") else str(value or "")


def classify(finding: dict, record_row: dict, target: dict, aliases: dict) -> dict:
    """What one finding means for one live FLoRA record the row feeds.

    `target` is {flora_id, export_id, role ('primary' or 'merged'), version, row
    (the frame row, None when the build failed), corrected ({field: derived value},
    frame.attrs["flora_edits"]), corrections ({field: who, when, note}),
    primary_display_id}. Pure.
    """
    out = {"flora_id": target.get("flora_id"), "export_id": target.get("export_id"),
           "role": target.get("role"), "version": target.get("version"),
           "flora_value": None, "flora_source": None, "flora_source_text": None,
           "state": None, "suggested_action": "none",
           "sheet_value_for_flora": None, "sheet_value_error": None}
    kept = _kept_current(finding)
    kind = finding.get("kind")
    if kind == sheet_drift.NOT_ACCEPTED:
        out.update(state=KEPT if kept else NOT_VALIDATED,
                   suggested_action="none" if kept else "decide_status")
        return out
    if kind == sheet_drift.MISSING:
        action = "review_duplicates" if finding.get("reentered_as") else "decide_missing"
        out.update(state=KEPT if kept else ROW_REMOVED, suggested_action="none" if kept else action)
        return out
    row = target.get("row")
    if row is None:
        out["state"] = UNKNOWN
        return out
    mapping = FLORA_FIELDS.get(finding.get("record_column"))
    record_type = row.get("type")
    if not mapping or mapping[0] not in flora_edits.editable_fields(record_type):
        out["state"] = NO_FIELD
        return out
    field, rule = mapping
    ctx = _context(field, row, record_row, aliases)
    flora_value = flora_edits._text(row.get(field))
    at_import, in_sheet = finding.get("value_at_import"), finding.get("value_in_sheet")
    corrected = field in (target.get("corrected") or {})
    source_edit = _source_edited(finding, record_row)
    as_imported = _same(rule, flora_value, at_import, ctx)
    out["flora_value"] = flora_value
    out["sheet_value_for_flora"], out["sheet_value_error"] = sheet_value_for_flora(
        field, rule, in_sheet, record_type, ctx)

    # Where FLoRA's value comes from, whatever the state: shown beside it.
    if corrected:
        change = (target.get("corrections") or {}).get(field) or {}
        note = (change.get("note") or "").strip()
        source, text = "correction", (
            f"corrected by {change.get('by') or 'an admin'}"
            + (f", {_when(change.get('at'))}" if change.get("at") else "")
            + (f": “{note}”" if note else ""))
    elif source_edit:
        source, text = "source_edit", "edited by hand in Source Records before corrections moved to FLoRA"
    elif as_imported and target.get("role") != "merged":
        source, text = "import", "as imported"
    elif rule in METADATA_RULES and not as_imported:
        source, text = "metadata", "from the publisher's metadata"
    elif target.get("role") == "merged":
        primary = target.get("primary_display_id") or "another row"
        source, text = "other_row", f"taken from {primary}, the row this one was merged into"
    else:
        source, text = "build", "changed by FLoRA's build (cleaning, deduplication or metadata)"
    out.update(flora_source=source, flora_source_text=text)

    if _same(rule, flora_value, in_sheet, ctx):
        state = MATCHES
    elif kept:
        state = KEPT
    elif rule in METADATA_RULES and not corrected and not source_edit and not as_imported:
        state = METADATA
    elif (not corrected and not source_edit and target.get("role") == "primary"
          and (as_imported or (rule == "fill" and not _view(rule, at_import, ctx)))):
        state = PENDING
    else:
        state = CONFLICT
    out["state"] = state
    if state in (PENDING, CONFLICT):
        out["suggested_action"] = "use_sheet_value" if state == PENDING else "decide"
    return out


def _worst(states) -> str:
    states = [s for s in states if s]
    return min(states, key=_SEVERITY.index) if states else NOT_IN_FLORA


def _kept_info(finding: dict) -> "dict | None":
    if not finding.get("kept_at"):
        return None
    return {"kept_at": finding["kept_at"], "kept_by": finding.get("kept_by"),
            "note": finding.get("kept_note"), "sheet_value": finding.get("kept_sheet_value"),
            "current": _kept_current(finding)}


def _field(finding: dict, record_row: dict, targets: list, aliases: dict) -> dict:
    """A finding as the view shows it: the cell, and what it means per FLoRA record."""
    mapping = FLORA_FIELDS.get(finding.get("record_column"))
    flora_field = mapping[0] if mapping else None
    out = {
        "finding_id": finding["finding_id"], "kind": finding["kind"],
        "sheet_column": finding["sheet_column"], "record_column": finding.get("record_column"),
        "flora_field": flora_field,
        "flora_column": transform_sources.OUTPUT_RENAMES.get(flora_field, flora_field) if flora_field else None,
        "value_at_import": finding.get("value_at_import"),
        "value_in_sheet": finding.get("value_in_sheet"),
        "sheet_row_number": finding.get("sheet_row_number"),
        "first_seen_at": finding.get("first_seen_at"), "last_seen_at": finding.get("last_seen_at"),
        "resolved_at": finding.get("resolved_at"), "kept": _kept_info(finding),
        "targets": [],
    }
    if finding.get("resolved_at"):
        out.update(state=RESOLVED, counted=False)
        return out
    if not targets:
        out.update(state=NOT_IN_FLORA, counted=False)
        return out
    # A failed build leaves every target without a row: classify() says unknown.
    out["targets"] = [classify(finding, record_row, target, aliases) for target in targets]
    out["state"] = _worst(t["state"] for t in out["targets"])
    out["counted"] = out["state"] in NEEDS_REVIEW
    return out


# ── reading ───────────────────────────────────────────────────────────────────

_FINDING_COLUMNS = """
    f.finding_id, f.record_id::text AS record_id, f.source, f.sheet_row_id, f.sheet_column,
    f.record_column, f.kind, f.value_at_import, f.value_in_sheet, f.sheet_row_number,
    f.first_seen_at, f.last_seen_at, f.resolved_at,
    f.kept_at, f.kept_by, f.kept_note, f.kept_sheet_value
"""
# What the view reads of a source row: its identity, and the identifiers the url
# rules and the exclusions compare. Not raw, abstracts or quotes: _load() takes the
# one cell of each that a finding needs, so the count on every visit of the tab does
# not download every edited row whole.
_RECORD_FIELDS = ["ref_o", "ref_r", "doi_o", "doi_r", "url_r"]


def tables_exist(cur) -> bool:
    """False until init_db() has created the tables: the view then shows nothing
    compared yet instead of failing."""
    cur.execute("SELECT to_regclass('sheet_drift_findings') IS NOT NULL "
                "AND to_regclass('sheet_drift_runs') IS NOT NULL AS present")
    return bool(cur.fetchone()["present"])


def last_checks(cur) -> list:
    """Per enabled sheet source: its latest check, and its latest sync, so the view
    can say how fresh each sheet's findings are (a failed sync leaves them as of
    the last check that ran)."""
    checks, syncs = {}, {}
    if tables_exist(cur):
        cur.execute("""SELECT DISTINCT ON (source) source, drift_run_id, checked_at, rows_compared,
                              rules_version, counts, notes
                         FROM sheet_drift_runs ORDER BY source, drift_run_id DESC""")
        checks = {row["source"]: dict(row) for row in cur.fetchall()}
    cur.execute("""SELECT DISTINCT ON (source) source, status, started_at, failure_reason
                     FROM source_sync_runs ORDER BY source, started_at DESC""")
    syncs = {row["source"]: dict(row) for row in cur.fetchall()}
    out = []
    for source in _sources():
        if not source["enabled"]:
            continue
        check, sync = checks.get(source["key"]) or {}, syncs.get(source["key"]) or {}
        out.append({"source": source["key"], "label": source["label"],
                    "checked_at": check.get("checked_at"),
                    "rows_compared": check.get("rows_compared"),
                    "counts": check.get("counts") or {}, "notes": check.get("notes") or [],
                    "last_sync_status": sync.get("status"),
                    "last_sync_at": sync.get("started_at"),
                    "last_sync_failure": sync.get("failure_reason")})
    return out


def _load(cur, record_ids=None) -> "tuple[list, dict]":
    """(findings, source rows by record_id), optionally for some records only."""
    where, params = "", []
    if record_ids is not None:
        where, params = "WHERE f.record_id = ANY(%s::uuid[])", [list(record_ids)]
    # raw_has / raw_value / record_value: the finding's cell as imported and as stored,
    # for _source_edited().
    cur.execute(
        f"""
        SELECT {_FINDING_COLUMNS},
               COALESCE(s.raw ? f.sheet_column, FALSE) AS raw_has,
               s.raw ->> f.sheet_column AS raw_value,
               to_jsonb(s) ->> f.record_column AS record_value
        FROM sheet_drift_findings f JOIN source_records s ON s.record_id = f.record_id
        {where}
        ORDER BY f.finding_id
        """,
        params)
    findings = [dict(row) for row in cur.fetchall()]
    ids = sorted({f["record_id"] for f in findings})
    if not ids:
        return findings, {}
    cur.execute(
        f"""
        SELECT record_id::text AS record_id, display_id, source, sheet_row_id, type,
               content_fingerprint, duplicate_status, duplicate_of::text AS duplicate_of,
               deleted_at, deleted_reason, {", ".join(_RECORD_FIELDS)}
        FROM source_records WHERE record_id = ANY(%s::uuid[])
        """,
        (ids,),
    )
    return findings, {row["record_id"]: dict(row) for row in cur.fetchall()}


def _links(cur, record_ids: list) -> dict:
    """{record_id: [the FLoRA records it feeds]}: as their primary row or merged into
    them, retired ones included (source_records_service.flora_records)."""
    if not record_ids:
        return {}
    cur.execute(
        """
        SELECT flora_id, export_id, retired_at, primary_source_record_id::text AS primary_id,
               ARRAY(SELECT m::text FROM unnest(merged_source_record_ids) m) AS merged_ids
        FROM flora_records
        WHERE primary_source_record_id = ANY(%s::uuid[]) OR merged_source_record_ids && %s::uuid[]
        ORDER BY flora_id
        """,
        (record_ids, record_ids),
    )
    wanted, links = set(record_ids), {}
    for row in cur.fetchall():
        for record_id, role in ([(row["primary_id"], "primary")]
                                + [(m, "merged") for m in row["merged_ids"] or []]):
            if record_id in wanted:
                links.setdefault(record_id, []).append(
                    {"flora_id": row["flora_id"], "export_id": row["export_id"],
                     "retired": row["retired_at"] is not None, "role": role})
    return links


def _frame(cur) -> "tuple[object, str | None]":
    """The FLoRA frame, or (None, why) when it cannot be built. Behind a savepoint:
    a failed build must not abort the transaction the view still reads in."""
    cur.execute("SAVEPOINT sheet_drift_frame")
    try:
        frame = flora_service.dataset(cur)
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT sheet_drift_frame")
        logger.exception("Sheet edits: FLoRA could not be built")
        return None, (f"FLoRA could not be built just now ({exc}). The sheet edits are listed "
                      "without FLoRA's values; try again after the next pipeline run.")
    cur.execute("RELEASE SAVEPOINT sheet_drift_frame")
    return frame, None


def _frame_rows(frame, flora_ids) -> "tuple[dict, dict]":
    """({flora_id: frame row}, {export_id: corrected fields}) for those records."""
    if frame is None or frame.empty or "flora_id" not in frame:
        return {}, {}
    match = frame[frame["flora_id"].isin(sorted(flora_ids))]
    rows = {}
    for _, values in match.iterrows():
        row = {key: flora_service._cell(value) for key, value in values.items()}
        rows.setdefault(row["flora_id"], row)
    return rows, dict(frame.attrs.get("flora_edits") or {})


def _changes(cur, export_ids) -> "tuple[dict, dict]":
    """({export_id: version}, {export_id: {field: the change that last moved it}})."""
    export_ids = sorted({e for e in export_ids if e})
    if not export_ids or not flora_edits._table_exists(cur):
        return {}, {}
    cur.execute(
        """
        SELECT export_id, change_id, changed_at, admin_handle, note, notes, changes
        FROM flora_record_changes WHERE export_id = ANY(%s)
        ORDER BY change_id DESC
        """,
        (export_ids,),
    )
    versions, moved = {}, {}
    for row in cur.fetchall():
        versions.setdefault(row["export_id"], row["change_id"])
        for field in row["changes"] or {}:
            moved.setdefault(row["export_id"], {}).setdefault(field, {
                "by": row["admin_handle"], "at": row["changed_at"], "change_id": row["change_id"],
                "note": (row["notes"] or {}).get(field) or row["note"]})
    return versions, moved


def _display_ids(cur, record_ids) -> dict:
    record_ids = sorted({r for r in record_ids if r})
    if not record_ids:
        return {}
    cur.execute("SELECT record_id::text AS record_id, display_id FROM source_records "
                "WHERE record_id = ANY(%s::uuid[])", (record_ids,))
    return {row["record_id"]: row["display_id"] for row in cur.fetchall()}


def _reentered(cur, rows: list) -> dict:
    """{record_id: live rows with the same paper fingerprint}, for rows gone from the
    sheet: re-entering a study under a new UUID looks exactly like a deletion."""
    prints = {r["content_fingerprint"] for r in rows if r.get("content_fingerprint")}
    if not prints:
        return {}
    cur.execute(
        """
        SELECT record_id::text AS record_id, display_id, content_fingerprint FROM source_records
        WHERE content_fingerprint = ANY(%s) AND deleted_at IS NULL ORDER BY display_id
        """,
        (sorted(prints),),
    )
    by_print = {}
    for row in cur.fetchall():
        by_print.setdefault(row["content_fingerprint"], []).append(dict(row))
    return {r["record_id"]: [{"record_id": m["record_id"], "display_id": m["display_id"]}
                             for m in by_print.get(r.get("content_fingerprint"), [])
                             if m["record_id"] != r["record_id"]]
            for r in rows}


def _not_in_flora(record_row: dict, links: list, exclusions: list, displays: dict) -> dict:
    """Why a row feeds no live FLoRA record."""
    if record_row.get("deleted_at"):
        return {"reason": "deleted", "text": "Deleted from Source Records: " +
                (record_row.get("deleted_reason") or "it is no longer validated.")}
    if record_row.get("duplicate_status") == "duplicate":
        survivor = displays.get(record_row.get("duplicate_of")) or "another row"
        return {"reason": "duplicate", "suggested_action": "check_survivor",
                "survivor_record_id": record_row.get("duplicate_of"),
                "survivor_display_id": survivor,
                "text": f"Ruled a duplicate of {survivor}. Check whether {survivor} needs the "
                        "same change."}
    exclusion = transform_sources.matching_exclusion(
        exclusions, record_row.get("doi_o"), record_row.get("doi_r"), record_row.get("url_r"))
    if exclusion is not None:
        why = (exclusion.get("reason") or "").strip()
        return {"reason": "excluded",
                "text": "Excluded by the Exclusions sheet" + (f": {why}" if why else ".")}
    if links:
        return {"reason": "retired", "text": "Retired from FLoRA: a later run no longer built it."}
    return {"reason": "not_built", "text": "Not in FLoRA yet: the next pipeline run builds it."}


def _view_records(cur, record_ids=None) -> "tuple[list, str | None]":
    """Every record with findings (or those named), each with its fields classified,
    and the build error if FLoRA could not be built."""
    findings, rows = _load(cur, record_ids)
    if not findings:
        return [], None
    ids = sorted(rows)
    links = _links(cur, ids)
    live_ids = {link["flora_id"] for found in links.values() for link in found if not link["retired"]}
    frame, flora_error = _frame(cur) if live_ids else (None, None)
    frame_rows, corrected = _frame_rows(frame, live_ids)
    versions, moved = _changes(cur, [link["export_id"] for found in links.values() for link in found])
    aliases, exclusions = transform_sources.load_rules(cur)
    displays = _display_ids(cur, [r.get("duplicate_of") for r in rows.values()])
    gone = [rows[f["record_id"]] for f in findings
            if f["kind"] == sheet_drift.MISSING and f["record_id"] in rows]
    reentered = _reentered(cur, gone)

    by_record = {}
    for finding in findings:
        by_record.setdefault(finding["record_id"], []).append(finding)
    records = []
    for record_id, record_findings in by_record.items():
        record_row = rows.get(record_id)
        if record_row is None:
            continue
        live = [link for link in links.get(record_id, []) if not link["retired"]]
        targets = []
        for link in live:
            frame_row = frame_rows.get(link["flora_id"])
            export_id = link["export_id"]
            targets.append({
                **link, "version": versions.get(export_id, 0),
                "row": frame_row,
                "corrected": corrected.get(export_id) or {},
                "corrections": moved.get(export_id) or {},
                "primary_display_id": (frame_row or {}).get("source_display_id"),
            })
        if not flora_error and frame is not None:
            # Registered, but not in this build (a ruling or an exclusion since the
            # last run): it has no values to compare, so it is not a target.
            targets = [t for t in targets if t["row"] is not None]
        again = reentered.get(record_id) or []
        fields = []
        for finding in record_findings:
            if finding["kind"] == sheet_drift.MISSING:
                finding = dict(finding, reentered_as=again)
            fields.append(_field(finding, record_row, targets, aliases))
        numbers = sorted((f for f in record_findings if f.get("sheet_row_number")),
                         key=lambda f: f["last_seen_at"])
        record = {
            "record_id": record_id, "display_id": record_row.get("display_id"),
            "source": record_row.get("source"), "source_label": source_label(record_row.get("source")),
            "type": record_row.get("type"), "sheet_row_id": record_row.get("sheet_row_id"),
            "sheet_row_number": numbers[-1]["sheet_row_number"] if numbers else None,
            "ref_o": record_row.get("ref_o"), "ref_r": record_row.get("ref_r"),
            "flora": [{"flora_id": t["flora_id"], "export_id": t["export_id"], "role": t["role"],
                       "version": t["version"]} for t in targets],
            "not_in_flora": None if targets else _not_in_flora(
                record_row, links.get(record_id, []), exclusions, displays),
            "reentered_as": again,
            "fields": fields,
        }
        records.append(record)
    return records, flora_error


def _open_fields(record: dict) -> list:
    return [f for f in record["fields"] if not f.get("resolved_at")]


def _matches(record: dict, state: str) -> bool:
    """Whether a record belongs under one filter, by its unresolved fields."""
    open_states = {f["state"] for f in _open_fields(record)}
    if state == "all":
        return bool(record["fields"])
    if state == "resolved":
        return any(f.get("resolved_at") for f in record["fields"])
    if state == "open":
        return bool(open_states)
    if state == "needs_review":
        return bool(open_states & NEEDS_REVIEW)
    if state == "nothing_to_do":
        return bool(open_states) and not open_states & NEEDS_REVIEW
    if state == "row":
        return bool(open_states & ROW_STATES)
    if state == "info":
        return bool(open_states & {METADATA, NO_FIELD, NOT_IN_FLORA})
    return state in open_states


def _shown(record: dict, state: str) -> dict:
    """The record with the fields its filter shows: resolved ones under Resolved,
    every one under All, the unresolved ones otherwise. Its state is its worst."""
    if state == "resolved":
        fields = [f for f in record["fields"] if f.get("resolved_at")]
    elif state == "all":
        fields = list(record["fields"])
    else:
        fields = _open_fields(record)
    return {**record, "fields": fields, "state": _worst(f["state"] for f in fields)}


def _counts(records: list) -> dict:
    """Records per filter, and the unresolved fields per state."""
    counts = {name: sum(1 for r in records if _matches(r, name)) for name in FILTERS}
    fields = {}
    for record in records:
        for f in _open_fields(record):
            fields[f["state"]] = fields.get(f["state"], 0) + 1
    counts["fields"] = fields
    counts["needs_review_fields"] = sum(n for state, n in fields.items() if state in NEEDS_REVIEW)
    return counts


def _search_hit(record: dict, term: str) -> bool:
    term = term.casefold()
    ids = [record.get("display_id"), record.get("sheet_row_id"),
           *[f["flora_id"] for f in record["flora"]], *[f["export_id"] for f in record["flora"]]]
    return any(term in str(value).casefold() for value in ids if value)


def _filtered(cur, filters: dict) -> "tuple[list, dict, str | None]":
    state = filters.get("state") or "needs_review"
    if state not in FILTERS:
        raise ValueError(f"Unknown state filter: {state}")
    record_ids = [filters["record_id"]] if filters.get("record_id") else None
    records, flora_error = _view_records(cur, record_ids)
    counts = _counts(records)
    source, search = filters.get("source") or "", (filters.get("search") or "").strip()
    chosen = [_shown(r, state) for r in records if _matches(r, state)
              and (not source or r["source"] == source)
              and (not search or _search_hit(r, search))]
    chosen.sort(key=lambda r: (_source_order(r["source"]), r["sheet_row_number"] or 10**9,
                               r["display_id"] or ""))
    return chosen, counts, flora_error


def _empty(page=1, per_page=25, cur=None) -> dict:
    return {"records": [], "total": 0, "fields_total": 0, "page": page, "per_page": per_page,
            "counts": {**{name: 0 for name in FILTERS}, "fields": {}, "needs_review_fields": 0},
            "last_checks": last_checks(cur) if cur is not None else [], "flora_error": None,
            "set_up": False}


def list_findings(cur, filters: dict) -> dict:
    """One page of records with sheet edits under a filter: state (FILTERS), source,
    search (an ID), page, per_page. With the counts of every filter, when each sheet
    was last compared, and flora_error when FLoRA could not be built."""
    page = max(1, int(filters.get("page") or 1))
    per_page = max(1, min(int(filters.get("per_page") or 25), 100))
    if not tables_exist(cur):
        return _empty(page, per_page, cur)
    chosen, counts, flora_error = _filtered(cur, filters)
    start = (page - 1) * per_page
    return {"records": chosen[start:start + per_page], "total": len(chosen),
            "fields_total": sum(len(r["fields"]) for r in chosen),
            "page": page, "per_page": per_page, "counts": counts,
            "last_checks": last_checks(cur), "flora_error": flora_error, "set_up": True}


def summary(cur) -> dict:
    """The counts the Sheet edits chip and select show, and the last checks."""
    if not tables_exist(cur):
        empty = _empty(cur=cur)
        return {"counts": empty["counts"], "last_checks": empty["last_checks"],
                "flora_error": None, "set_up": False}
    records, flora_error = _view_records(cur)
    return {"counts": _counts(records), "last_checks": last_checks(cur),
            "flora_error": flora_error, "set_up": True}


def record_view(cur, record_id: str) -> "dict | None":
    """One record's unresolved sheet edits, as the view shows them."""
    records, _, flora_error = _filtered(cur, {"state": "open", "record_id": record_id})
    if not records:
        return None
    return {**records[0], "flora_error": flora_error}


def for_flora_record(cur, record: dict) -> dict:
    """The FLoRA editor's box: the unresolved sheet edits of the source rows this
    record is built from, classified against this record alone."""
    empty = {"fields": [], "rows": [], "last_checks": []}
    flora_id = record.get("flora_id")
    if not flora_id or not tables_exist(cur):
        return empty
    cur.execute(
        """
        SELECT primary_source_record_id::text AS primary_id,
               ARRAY(SELECT m::text FROM unnest(merged_source_record_ids) m) AS merged_ids
        FROM flora_records WHERE flora_id = %s
        """,
        (flora_id,),
    )
    link = cur.fetchone()
    if not link:
        return empty
    ids = [link["primary_id"], *(link["merged_ids"] or [])]
    cur.execute("SELECT 1 FROM sheet_drift_findings WHERE record_id = ANY(%s::uuid[]) "
                "AND resolved_at IS NULL LIMIT 1", (ids,))
    if not cur.fetchone():
        return empty
    records, _ = _view_records(cur, ids)
    fields, rows = [], []
    for r in records:
        mine = [t for t in r["flora"] if t["flora_id"] == flora_id]
        if not mine:
            continue
        shown = []
        for f in _open_fields(r):
            targets = [t for t in f["targets"] if t["flora_id"] == flora_id]
            if not targets:
                continue
            state = targets[0]["state"]
            shown.append({**f, "targets": targets, "state": state, "counted": state in NEEDS_REVIEW,
                          "record_id": r["record_id"], "display_id": r["display_id"],
                          "source_label": r["source_label"]})
        if shown:
            fields.extend(shown)
            rows.append({"record_id": r["record_id"], "display_id": r["display_id"],
                         "source": r["source"], "source_label": r["source_label"],
                         "sheet_row_id": r["sheet_row_id"], "sheet_row_number": r["sheet_row_number"],
                         "role": mine[0]["role"]})
    sources = {row["source"] for row in rows}
    return {"fields": fields, "rows": rows,
            "last_checks": [c for c in last_checks(cur) if c["source"] in sources]}


def editor_box(cur, record: dict) -> "dict | None":
    """for_flora_record() for the FLoRA editor, which must open whether or not this
    can be read: behind a savepoint, so a failed read leaves its transaction usable,
    and None, logged, instead of an error."""
    try:
        cur.execute("SAVEPOINT sheet_drift_box")
    except Exception:
        logger.exception("Sheet edits: no savepoint for FLoRA record %s", record.get("flora_id"))
        return None
    try:
        box = for_flora_record(cur, record)
    except Exception:
        logger.exception("Sheet edits could not be read for FLoRA record %s", record.get("flora_id"))
        try:
            cur.execute("ROLLBACK TO SAVEPOINT sheet_drift_box")
        except Exception:
            logger.exception("Sheet edits: could not roll back to the savepoint")
        return None
    cur.execute("RELEASE SAVEPOINT sheet_drift_box")
    return box


# ── "Keep FLoRA value" ────────────────────────────────────────────────────────

def _locked(cur, finding_id: int) -> dict:
    """The finding, locked until the transaction ends: two people deciding at once
    are answered in turn."""
    cur.execute(
        f"SELECT {_FINDING_COLUMNS} FROM sheet_drift_findings f WHERE f.finding_id = %s FOR UPDATE",
        (int(finding_id),))
    row = cur.fetchone()
    if row is None:
        raise FindingNotFound(finding_id)
    return dict(row)


def _same_sheet_value(a, b) -> bool:
    return (flora_edits._text(a) or None) == (flora_edits._text(b) or None)


def keep(cur, finding_id: int, admin: dict, note: str, sheet_value) -> dict:
    """Decide FLoRA's value is right for one sheet edit, with the note saying why.

    `sheet_value` is the sheet's value the caller saw: a sheet that changed since
    raises StaleFinding. Refused for a resolved finding and for one with nothing to
    decide (FLoRA already matches, a field FLoRA does not have). The decision holds
    while the sheet keeps that value; a later edit of the cell asks again."""
    text = (note or "").strip()
    if len(text) < KEEP_NOTE_MIN:
        raise ValueError(f"Say why FLoRA's value is right: at least {KEEP_NOTE_MIN} characters.")
    if len(text) > KEEP_NOTE_MAX:
        raise ValueError(f"The note is longer than {KEEP_NOTE_MAX:,} characters.")
    finding = _locked(cur, finding_id)
    if finding["resolved_at"] is not None:
        raise ValueError("This sheet edit is resolved: the sheet has the imported value again.")
    if not _same_sheet_value(sheet_value, finding["value_in_sheet"]):
        raise StaleFinding(finding["value_in_sheet"])
    if _kept_current(finding):
        raise ValueError(f"{finding['kept_by']} already kept FLoRA's value for this sheet edit.")
    view = record_view(cur, finding["record_id"])
    field = next((f for f in (view or {}).get("fields", []) if f["finding_id"] == finding["finding_id"]), None)
    if field is not None and field["state"] not in NEEDS_REVIEW:
        raise ValueError("Nothing to decide here: " + STATE_LABELS.get(field["state"], field["state"]).lower() + ".")
    cur.execute(
        """
        UPDATE sheet_drift_findings
           SET kept_at = NOW(), kept_by = %s, kept_note = %s, kept_sheet_value = value_in_sheet
         WHERE finding_id = %s
        RETURNING finding_id, record_id::text AS record_id, source, sheet_row_id, sheet_column,
                  kept_at, kept_by, kept_note, kept_sheet_value
        """,
        (admin["handle"], text, finding["finding_id"]),
    )
    return dict(cur.fetchone())


def undo_keep(cur, finding_id: int, admin: dict) -> dict:
    """Withdraw a "Keep FLoRA value": the sheet edit needs a decision again. Returns
    the decision withdrawn, as the row keeps none once it is cleared."""
    finding = _locked(cur, finding_id)
    if finding["kept_at"] is None:
        raise ValueError("FLoRA's value is not kept for this sheet edit.")
    cur.execute("UPDATE sheet_drift_findings SET kept_at = NULL, kept_by = NULL, kept_note = NULL, "
                "kept_sheet_value = NULL WHERE finding_id = %s", (finding["finding_id"],))
    return {"finding_id": finding["finding_id"], "record_id": finding["record_id"],
            "source": finding["source"], "sheet_row_id": finding["sheet_row_id"],
            "sheet_column": finding["sheet_column"], "withdrawn_by": admin["handle"],
            "withdrawn": {"kept_at": finding["kept_at"], "kept_by": finding["kept_by"],
                          "kept_note": finding["kept_note"],
                          "kept_sheet_value": finding["kept_sheet_value"]}}


# ── the CSV for coding agents ─────────────────────────────────────────────────

def _csv_cell(column: str, value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if column in CSV_NOTE_COLUMNS and str(value).startswith(_FORMULA):
        return "'" + str(value)
    return value


def csv_rows(records: list) -> list:
    """One row per finding and live FLoRA record (one with blank FLoRA columns when
    the row feeds none). The decision columns are left blank for the agent."""
    out = []
    for record in records:
        for f in record["fields"]:
            kept = f.get("kept") or {}
            base = {
                "finding_id": f["finding_id"], "display_id": record.get("display_id"),
                "source": record.get("source"), "sheet_label": record.get("source_label"),
                "sheet_row_id": record.get("sheet_row_id"),
                "sheet_row_number": f.get("sheet_row_number") or record.get("sheet_row_number"),
                "kind": f["kind"], "sheet_column": f["sheet_column"],
                "flora_field": f.get("flora_field"), "flora_column": f.get("flora_column"),
                "value_at_import": f.get("value_at_import"), "value_in_sheet": f.get("value_in_sheet"),
                "first_seen_at": f.get("first_seen_at"), "last_seen_at": f.get("last_seen_at"),
                "kept_by": kept.get("kept_by"), "kept_at": kept.get("kept_at"),
                "kept_note": kept.get("note"),
            }
            targets = f.get("targets") or [None]
            for t in targets:
                state = (t or {}).get("state") or f["state"]
                explained = STATE_EXPLAINED.get(state, "")
                if t and t.get("flora_source_text") and state in (CONFLICT, METADATA, KEPT, MATCHES):
                    explained += f" FLoRA's value is {t['flora_source_text']}."
                if t and t.get("sheet_value_error"):
                    explained += " " + t["sheet_value_error"]
                if not t and record.get("not_in_flora"):
                    explained += " " + record["not_in_flora"]["text"]
                if kept and not kept.get("current"):
                    explained += f" Kept earlier when the sheet said “{kept.get('sheet_value') or ''}”."
                out.append({
                    **base,
                    "flora_id": (t or {}).get("flora_id"), "flora_role": (t or {}).get("role"),
                    "flora_version": (t or {}).get("version"),
                    "flora_value": (t or {}).get("flora_value"),
                    "flora_value_source": (t or {}).get("flora_source"),
                    "state": state, "state_explained": explained.strip(),
                    "suggested_action": (t or {}).get("suggested_action")
                                        or (record.get("not_in_flora") or {}).get("suggested_action")
                                        or "none",
                    "sheet_value_for_flora": (t or {}).get("sheet_value_for_flora"),
                    "decision": None, "new_flora_value": None, "decision_note": None,
                })
    return out


def export_csv(cur, filters: dict) -> str:
    """Every finding matching the filter, every page, as CSV text with a BOM and \\n
    line endings: the file docs/SHEET_EDITS_FOR_AGENTS.md describes."""
    records = _filtered(cur, filters)[0] if tables_exist(cur) else []
    out = io.StringIO()
    out.write("﻿")                       # so Excel reads UTF-8
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in csv_rows(records):
        writer.writerow({column: _csv_cell(column, row.get(column)) for column in CSV_COLUMNS})
    return out.getvalue()
