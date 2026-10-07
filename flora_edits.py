"""flora_edits.py — changes made to FLoRA records, and the log that keeps them.

Source records are what the entry sheets and the validated set supplied, and they
stay that way: nobody edits them by hand. A change is made to the FLoRA record a
source row feeds. Every save appends one row to `flora_record_changes`, as the
website records' admin_decisions does: who, when, the note, and each field it
changed as [before, after]. The `before` values are the original entries. Each row
also holds every correction in force once it was made, so the record can be put
back to the state of any row in the log (restore()).

A save rewrites the record's row in `flora_data`, the FLoRA table the public API
reads, at once (publish()). The pipeline rebuilds that table from the source
records on every run, so every build re-applies the latest corrections from the
log on top of what it derived:

  * identifiers (doi_o, doi_r, url_o, url_r) replace the derived ones after every
    duplicate step, so a change never drops, merges or hides a record, and the row
    is then joined to the metadata again, so the title, authors and journal follow
    a corrected DOI rather than describing the paper it replaced.
    transform_sources.build() calls apply_identifiers() for this (step 6c).
  * every corrected field replaces the finished value once ids are attached, in
    apply(). The FLoRA tab, its export, validation and the published CSV all read
    the frame after this step.

A correction outlives the build that produced the value it replaces: a sync or an
enrichment refresh can change the derived value underneath, and the correction
still wins. A field reverted, or restored to before its first change, shows the
derived value again.

Records are keyed on their permanent published id: flora_data.id, which is
flora_records.export_id. The log references the registry rather than flora_data
because a row can be corrected before it is first published — a missing title is
exactly what keeps a row out of the export.

Transport-agnostic like the other services: cursors in, plain dicts out.
"""
import json
import re

import pandas as pd
from psycopg2.extras import Json

import author_overlap
import flora_store
import transform_sources
from extractor_vocab import (
    OUTCOME_COMPUTATION_VALUES,
    OUTCOME_ROBUSTNESS_VALUES,
    REPLICATION_OUTCOMES,
    derive_reproduction_outcome,
    normalize_axis_value,
    normalize_outcome,
)


def _side(side):
    return [f"ref_{side}", f"doi_{side}", f"url_{side}", f"alt_identifier_{side}",
            f"title_{side}", f"author_{side}", f"journal_{side}", f"year_{side}",
            f"volume_{side}", f"issue_{side}", f"pages_{side}", f"language_{side}",
            f"oa_url_{side}", f"bibtex_ref_{side}"]


# What an editor can correct, grouped as the editor shows it. Frame column names,
# not the published ones: corrections are applied to the frame, before
# to_output_shape renames ref_* to apa_ref_*. Identity, provenance and values the
# build computes from others (the DOI hashes, OpenAlex work ids, author overlap)
# are not here: they are derived again from what is.
FIELD_GROUPS = {
    "replication": [
        ("Original", _side("o")),
        ("Replication", _side("r") + ["abstract_r"]),
        ("Outcome", ["outcome", "outcome_quote", "outcome_quote_source"]),
    ],
    "reproduction": [
        ("Original", _side("o")),
        ("Reproduction", _side("r") + ["abstract_r"]),
        # A reproduction's outcome, quote and quote source are derived from the two
        # coded axes, so the axes are what an editor corrects.
        ("Computational", ["outcome_computation", "outcome_computational_quote",
                           "out_quote_computational_source"]),
        ("Robustness", ["outcome_robustness", "outcome_robustness_quote",
                        "out_quote_robust_source"]),
    ],
}

# Read by the duplicate steps and the metadata join, so the build applies them after
# the first and repeats the second for their rows (apply_identifiers, step 6c).
IDENTIFIER_FIELDS = ("doi_o", "doi_r", "url_o", "url_r")
AXIS_FIELDS = tuple(field for name, fields in FIELD_GROUPS["reproduction"][2:]
                    for field in fields)
# The build runs these through clean_text (its step 8); a correction gets the same
# treatment, so a pasted title cannot carry markup the build would have removed.
CLEANED_FIELDS = ("title_o", "title_r", "journal_o", "journal_r", "ref_o", "ref_r",
                  "abstract_r", "outcome_quote", "outcome_computational_quote",
                  "outcome_robustness_quote")
URL_FIELDS = ("url_o", "url_r", "oa_url_o", "oa_url_r")
LONG_FIELDS = ("ref_o", "ref_r", "title_o", "title_r", "author_o", "author_r",
               "bibtex_ref_o", "bibtex_ref_r", "abstract_r", "outcome_quote",
               "outcome_computational_quote", "outcome_robustness_quote")
CHOICES = {
    "outcome": sorted(REPLICATION_OUTCOMES),
    "outcome_computation": sorted(OUTCOME_COMPUTATION_VALUES),
    "outcome_robustness": sorted(OUTCOME_ROBUSTNESS_VALUES),
}
_YEAR_RE = re.compile(r"^(1[5-9]\d{2}|2\d{3})$")


class RecordNotFound(LookupError):
    """No FLoRA record with that id in the current dataset."""


class VersionConflict(Exception):
    """The record's change log moved since the caller loaded it."""

    def __init__(self, current_version: int):
        super().__init__("This record was changed by someone else.")
        self.current_version = current_version


def editable_fields(record_type) -> list:
    groups = FIELD_GROUPS.get(record_type, FIELD_GROUPS["replication"])
    return [field for _, fields in groups for field in fields]


def field_spec(record_type) -> list:
    """The editable fields in display order, as the editor renders them."""
    groups = FIELD_GROUPS.get(record_type, FIELD_GROUPS["replication"])
    return [{"field": field, "group": group,
             "label": transform_sources.OUTPUT_RENAMES.get(field, field),
             "long": field in LONG_FIELDS, "choices": CHOICES.get(field)}
            for group, fields in groups for field in fields]


def _text(value):
    """A frame cell or request value as stripped text, None when absent.

    Years can arrive as numbers from the metadata cache; 1972.0 is the year 1972,
    not a different value from the "1972" an editor sees and sends back.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value):
            return None
        if value.is_integer():
            value = int(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _lines(value):
    """Text with every line break written as \\n. A browser sends a textarea's
    breaks as \\n whatever the stored value used, so comparing without this would
    take an untouched field for a changed one."""
    return None if value is None else value.replace("\r\n", "\n").replace("\r", "\n")


def _same(raw, shown) -> bool:
    """Whether a submitted value is the one the editor was shown: equal once line
    breaks are written alike, or equal but for the line breaks a one-line input
    cannot hold."""
    raw, shown = _lines(_text(raw)), _lines(shown)
    if raw == shown:
        return True
    return (raw is not None and shown is not None and "\n" in shown and "\n" not in raw
            and raw == shown.replace("\n", ""))


def clean_value(field: str, value, record_type) -> "str | None":
    """A correction as it will be stored, or ValueError saying why it cannot be.

    Blank means "this field is deliberately empty", which is a correction too.
    """
    if field not in editable_fields(record_type):
        raise ValueError(f"{field} cannot be corrected on a {record_type or 'record'}")
    text = _lines(_text(value))
    if text is None:
        return None
    if field in CLEANED_FIELDS:
        text = transform_sources.clean_text(text)
    if field in ("doi_o", "doi_r"):
        doi = transform_sources.clean_doi(text)
        if not doi or not doi.startswith("10."):
            raise ValueError(f"{field} must be a DOI starting with 10. (leave it blank for none)")
        return doi
    if field == "outcome":
        outcome = normalize_outcome(text)
        if outcome not in REPLICATION_OUTCOMES:
            raise ValueError(f"outcome must be one of: {', '.join(sorted(REPLICATION_OUTCOMES))}")
        return outcome
    if field in ("outcome_computation", "outcome_robustness"):
        return normalize_axis_value(field, text)
    if field in ("year_o", "year_r") and not _YEAR_RE.match(text):
        raise ValueError(f"{field} must be a four-digit year")
    if field in URL_FIELDS and not re.match(r"^https?://\S+$", text, re.I):
        raise ValueError(f"{field} must be a web address starting with http:// or https://")
    if field in ("author_o", "author_r") and text.startswith("["):
        # The structured form the metadata join supplies. A typo in it would make
        # the author list and the overlap computed from it silently empty.
        try:
            authors = json.loads(text)
        except ValueError:
            raise ValueError(f"{field} is not valid JSON; fix it or enter the names as plain text")
        if not isinstance(authors, list):
            raise ValueError(f"{field} must be a JSON list of authors")
    return text


# ── reading the log ───────────────────────────────────────────────────────────

# The corrections in force: those recorded by each record's newest change.
_LATEST = """
    SELECT DISTINCT ON (export_id) export_id, corrections
    FROM flora_record_changes
    ORDER BY export_id, change_id DESC
"""


def _table_exists(cur) -> bool:
    """The nightly job runs from the repository and can reach the database before
    the deployed site's init_db() has created the table."""
    cur.execute("SELECT to_regclass('flora_record_changes') IS NOT NULL AS present")
    return bool(cur.fetchone()["present"])


def load(cur) -> dict:
    """Every correction in force: {export_id: {field: value}}."""
    if not _table_exists(cur):
        return {}
    cur.execute(_LATEST)
    return {row["export_id"]: dict(row["corrections"])
            for row in cur.fetchall() if row["corrections"]}


def load_identifiers(cur) -> dict:
    """Identifier corrections, with the source rows each record may be built from.

    The build has no published ids yet, so a record is found by the source rows it
    claims in the registry: its primary first, then the rows it absorbed or once
    derived from. Those matter on the run where a duplicate ruling changes which row
    survives: the registry is refreshed only after that build, so the primary alone
    would miss the new survivor and its corrected DOI would go out with the old
    paper's metadata.
    """
    if not _table_exists(cur):
        return {}
    cur.execute(f"""
        SELECT c.corrections, f.primary_source_record_id::text AS primary_id,
               f.merged_source_record_ids::text[] AS merged_ids,
               f.historical_source_record_ids::text[] AS historical_ids
        FROM ({_LATEST}) c JOIN flora_records f ON f.export_id = c.export_id
    """)
    records = []
    for row in cur.fetchall():
        fields = {field: value for field, value in (row["corrections"] or {}).items()
                  if field in IDENTIFIER_FIELDS}
        if fields:
            candidates = [row["primary_id"], *(row["merged_ids"] or []),
                          *(row["historical_ids"] or [])]
            records.append((list(dict.fromkeys(candidates)), fields))
    if not records:
        return {}
    cur.execute("SELECT primary_source_record_id::text AS id FROM flora_records")
    return {"records": records, "primaries": {row["id"] for row in cur.fetchall()}}


def identifier_values(cur, fields) -> list:
    """Corrected identifiers on live records, for enrich_works to fetch metadata for.

    Without this a corrected DOI would never be looked up, and the row would lose
    its title, and with it its place in the export, for good.
    """
    if not _table_exists(cur):
        return []
    cur.execute(f"""
        SELECT c.corrections
        FROM ({_LATEST}) c JOIN flora_records f ON f.export_id = c.export_id
        WHERE f.retired_at IS NULL
    """)
    values = {value for row in cur.fetchall()
              for field, value in (row["corrections"] or {}).items()
              if field in fields and value}
    return sorted(values)


# ── applying them to a build ─────────────────────────────────────────────────

def _set(frame, idx, column, value):
    if column not in frame.columns:
        frame[column] = None
    # A column the metadata join filled with numbers (a year) or nothing at all
    # (float NaN) refuses a string in place.
    if isinstance(value, str) and frame[column].dtype.kind in "biufc":
        frame[column] = frame[column].astype(object)
    frame.at[idx, column] = value


def apply_identifiers(df, identifiers: dict) -> dict:
    """Put corrected identifiers on the build's working frame, in place.

    Each record's corrections go to the row of its primary source record, or, when
    that row is not in this build, to the first row it absorbed or derived from that
    is no other record's primary. Returns {source_record_id: {field: derived value}}:
    the values replaced, which the editor shows beside each correction.
    """
    replaced = {}
    if not identifiers or df.empty:
        return replaced
    rows = {rid: idx for idx, rid in zip(df.index, df["record_id"])}
    primaries = identifiers.get("primaries") or set()
    targets, taken = [], set()
    # Primaries first: a record's own row is never given to another record.
    for candidates, fields in identifiers["records"]:
        if candidates and candidates[0] in rows:
            targets.append((candidates[0], fields))
            taken.add(candidates[0])
    for candidates, fields in identifiers["records"]:
        if not candidates or candidates[0] in rows:
            continue
        target = next((sid for sid in candidates[1:]
                       if sid in rows and sid not in primaries and sid not in taken), None)
        if target is not None:
            targets.append((target, fields))
            taken.add(target)
    for source_record_id, fields in targets:
        idx = rows[source_record_id]
        for field, value in fields.items():
            before = _text(df.at[idx, field]) if field in df.columns else None
            if field in ("doi_o", "doi_r") and before \
                    and transform_sources.DUMMY_DOI_RE.match(before):
                before = None                       # the build strips placeholders
            replaced.setdefault(source_record_id, {})[field] = before
            _set(df, idx, field, value)
    return replaced


def apply(cur, frame, corrections: "dict | None" = None):
    """The frame with every correction in force applied. Returns a copy.

    Adds `edited_fields` (the corrected field names, space-separated, for the grid
    and its filter) and attrs["flora_edits"] = {export_id: {field: derived value}}.
    Values computed from a corrected field follow it: a reproduction's outcome,
    quote and quote source from its axes, and the author overlap from the authors.
    """
    if frame.empty or "export_id" not in frame.columns:
        return frame
    if corrections is None:
        corrections = load(cur)
    out = frame.copy()
    out["edited_fields"] = None
    identifier_derived = frame.attrs.get("identifier_edits") or {}
    rows = {export_id: idx for idx, export_id in zip(out.index, out["export_id"])
            if isinstance(export_id, str) and export_id}
    derived = {}
    for export_id, fields in corrections.items():
        idx = rows.get(export_id)
        if idx is None:
            # Retired, or registered after this build: nothing to correct here.
            continue
        record_type = out.at[idx, "type"]
        allowed = set(editable_fields(record_type))
        before = identifier_derived.get(_text(out.at[idx, "source_record_id"])) or {}
        applied = {}
        for field, value in fields.items():
            if field not in allowed:
                continue
            applied[field] = before[field] if field in before else (
                _text(out.at[idx, field]) if field in out.columns else None)
            _set(out, idx, field, value)
        if not applied:
            continue
        if record_type == "reproduction" and applied.keys() & set(AXIS_FIELDS):
            row = out.loc[idx]
            _set(out, idx, "outcome", derive_reproduction_outcome(
                row.get("outcome_computation"), row.get("outcome_robustness")))
            _set(out, idx, "outcome_quote",
                 transform_sources.clean_text(transform_sources.derive_quote(row)))
            _set(out, idx, "outcome_quote_source", transform_sources.derive_quote_source(row))
        if applied.keys() & {"author_o", "author_r"}:
            overlap, pct = author_overlap.count(out.at[idx, "author_o"], out.at[idx, "author_r"])
            _set(out, idx, "author_overlap", overlap)
            _set(out, idx, "author_overlap_pct", pct)
        out.at[idx, "edited_fields"] = " ".join(sorted(applied))
        derived[export_id] = applied
    out.attrs["flora_edits"] = derived
    return out


# ── the editor ────────────────────────────────────────────────────────────────

def version(cur, export_id: str) -> int:
    """The record's newest change id. A save quotes it, so two editors working on
    the same record cannot overwrite each other unseen."""
    cur.execute("SELECT COALESCE(MAX(change_id), 0) AS v FROM flora_record_changes "
                "WHERE export_id = %s", (export_id,))
    return int(cur.fetchone()["v"])


def history(cur, export_id: str) -> list:
    """The record's change log, newest first."""
    cur.execute(
        """
        SELECT change_id, changed_at, admin_handle, action, note, changes, corrections,
               restored_from
        FROM flora_record_changes WHERE export_id = %s
        ORDER BY change_id DESC
        """,
        (export_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def source_edits(cur, export_id: str) -> list:
    """Edits made to this record's source rows before changes moved to FLoRA
    records. They changed the source rows themselves, so they are still in force."""
    cur.execute(
        """
        SELECT s.display_id, e.field, e.old_value, e.new_value, e.edited_by,
               e.edited_at, e.note
        FROM flora_records f
        JOIN source_record_edits e
          ON e.record_id = f.primary_source_record_id
          OR e.record_id = ANY(f.merged_source_record_ids)
        JOIN source_records s ON s.record_id = e.record_id
        WHERE f.export_id = %s
        ORDER BY e.edited_at DESC
        """,
        (export_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def editing_info(cur, record: dict, derived: dict) -> dict:
    """What the editor needs for one record: each editable field with its current
    value — for a corrected one, who changed it last and what the build derives —
    and the change log."""
    export_id = record["export_id"]
    log = history(cur, export_id)
    in_force = (log[0]["corrections"] or {}) if log else {}
    fields = []
    for spec in field_spec(record.get("type")):
        field = spec["field"]
        spec["value"] = _text(record.get(field))
        if field in in_force and field in derived:
            # The newest change that moved this field; any change if none did (a
            # restore can pin a value the derived one has since caught up with).
            last = next((change for change in log if field in (change["changes"] or {})),
                        log[0])
            spec["edit"] = {"derived": derived[field], "edited_by": last["admin_handle"],
                            "edited_at": last["changed_at"], "note": last["note"],
                            "change_id": last["change_id"]}
        fields.append(spec)
    return {"version": log[0]["change_id"] if log else 0, "fields": fields,
            "history": log, "source_edits": source_edits(cur, export_id)}


def _guard(cur, record: dict, expected_version) -> tuple:
    """Lock the record's log, check the caller saw its newest change, and return
    (export_id, corrections in force)."""
    if expected_version is None:
        raise ValueError("version is required")
    export_id = record.get("export_id")
    if not export_id:
        raise ValueError("This row has no FLoRA id yet. Run the pipeline, then correct it.")
    # Serialises saves to one record without touching the registry's rows, which
    # a running pipeline holds while it refreshes them.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('flora_record_changes'), hashtext(%s))",
                (export_id,))
    current_version = version(cur, export_id)
    if int(expected_version) != current_version:
        raise VersionConflict(current_version)
    if not current_version:
        return export_id, {}
    cur.execute("SELECT corrections FROM flora_record_changes WHERE change_id = %s",
                (current_version,))
    return export_id, dict(cur.fetchone()["corrections"] or {})


def _append(cur, export_id, record, derived, in_force, corrections, action, admin,
            note, restored_from=None) -> dict:
    """Append one change: every field whose value moves, as [before, after]."""
    def derived_value(field):
        return derived[field] if field in derived else _text(record.get(field))

    if corrections == in_force:
        return {"change_id": None, "changes": {}, "corrections": in_force}
    changes = {}
    for field in sorted(set(in_force) | set(corrections)):
        before = in_force[field] if field in in_force else derived_value(field)
        after = corrections[field] if field in corrections else derived_value(field)
        # Logged when the value moves, and also when only the correction comes or
        # goes: dropping one that equals the derived value changes nothing shown,
        # but the field follows the source again from then on.
        if before != after or (field in in_force) != (field in corrections) \
                or in_force.get(field) != corrections.get(field):
            changes[field] = [before, after]
    cur.execute(
        """
        INSERT INTO flora_record_changes
            (export_id, admin_id, admin_handle, action, note, changes, corrections,
             restored_from)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING change_id
        """,
        (export_id, admin.get("id"), admin["handle"], action, (note or "").strip() or None,
         Json(changes), Json(corrections), restored_from),
    )
    return {"change_id": cur.fetchone()["change_id"], "changes": changes,
            "corrections": corrections}


def save(cur, record: dict, derived: dict, fields: dict, revert, expected_version,
         admin: dict, note: str = "") -> dict:
    """Record an editor's changes to one FLoRA record as one row of its log.

    `record` is the record as the FLoRA tab currently shows it and `derived` the
    build's own values for the fields already corrected (apply()'s attrs).

    `fields` maps a field to its new value; `revert` names corrections to drop, so
    the derived value shows again. A value equal to what is shown changes nothing.
    A value equal to what the build derives drops the correction rather than storing
    a copy of it, so a later change to the source or the metadata flows through.

    Raises VersionConflict if another change landed since the caller loaded it.
    """
    record_type = record.get("type")
    fields, revert = dict(fields or {}), list(dict.fromkeys(revert or []))
    allowed = set(editable_fields(record_type))
    unknown = sorted((set(fields) | set(revert)) - allowed)
    if unknown:
        raise ValueError(f"Cannot be corrected on a {record_type or 'record'}: {', '.join(unknown)}")
    if set(fields) & set(revert):
        raise ValueError("A field cannot be changed and reverted in the same save")
    export_id, in_force = _guard(cur, record, expected_version)

    corrections = dict(in_force)
    for field in revert:
        corrections.pop(field, None)
    for field, raw in fields.items():
        shown = in_force[field] if field in in_force else _text(record.get(field))
        if _same(raw, shown):
            continue                                # untouched; not even re-validated
        new = clean_value(field, raw, record_type)
        if new == shown:
            continue
        base = derived.get(field, shown) if field in in_force else shown
        if new == base and field in in_force:
            corrections.pop(field)                  # back to the derived value
        else:
            corrections[field] = new
    _refuse_excluded(cur, record, derived, in_force, corrections)
    result = _append(cur, export_id, record, derived, in_force, corrections, "edit",
                     admin, note)
    return _summary(cur, export_id, result, in_force)


def restore(cur, record: dict, derived: dict, change_id: int, expected_version,
            admin: dict, note: str = "") -> dict:
    """Put the record back as it was right after one change in its log, or before
    the first (change_id 0: the derived values). Recorded as a change itself, so a
    restore can be undone by restoring the change before it."""
    export_id, in_force = _guard(cur, record, expected_version)
    if int(change_id):
        cur.execute("SELECT corrections FROM flora_record_changes "
                    "WHERE change_id = %s AND export_id = %s", (int(change_id), export_id))
        row = cur.fetchone()
        if row is None:
            raise RecordNotFound(f"change {change_id} of {export_id}")
        allowed = set(editable_fields(record.get("type")))
        target = {field: value for field, value in (row["corrections"] or {}).items()
                  if field in allowed}
    else:
        target = {}
    _refuse_excluded(cur, record, derived, in_force, target)
    result = _append(cur, export_id, record, derived, in_force, target, "restore", admin,
                     note, restored_from=int(change_id) or None)
    return _summary(cur, export_id, result, in_force)


def _summary(cur, export_id, result, in_force) -> dict:
    """What the change did to the corrections: set or changed, or dropped."""
    corrections = result["corrections"]
    return {"export_id": export_id, "change_id": result["change_id"],
            "changed_fields": sorted(f for f in result["changes"] if f in corrections),
            "reverted_fields": sorted(f for f in result["changes"]
                                      if f in in_force and f not in corrections),
            "version": result["change_id"] or version(cur, export_id)}


def _refuse_excluded(cur, record, derived, in_force, corrections):
    """Refuse a change that puts a record on an excluded identifier.

    The build would leave the record out, so the change would make it disappear;
    better to say so now, with the exclusion's reason. Checked only when the change
    touches doi_o, doi_r or url_r, the identifiers exclusions match on.
    """
    moved = {field for field in ("doi_o", "doi_r", "url_r")
             if in_force.get(field) != corrections.get(field)
             or (field in in_force) != (field in corrections)}
    if not moved:
        return

    def value(field):
        if field in corrections:
            return corrections[field]
        return derived[field] if field in derived else _text(record.get(field))

    cur.execute("SELECT doi_o, doi_r, url_r, reason FROM transform_exclusions")
    match = transform_sources.matching_exclusion(
        [dict(row) for row in cur.fetchall()], value("doi_o"), value("doi_r"), value("url_r"))
    if match is not None:
        raise ValueError("These identifiers are on the exclusion list, so the record would "
                         "leave FLoRA" + (f" ({match['reason']})" if match.get("reason") else "")
                         + ". Remove the exclusion first if the record belongs in FLoRA.")


def duplicate_warnings(frame, record: dict) -> list:
    """Records that share this one's DOIs. The duplicate steps of the build never see
    corrected identifiers — so a correction can never make a record disappear — and
    this says when one makes two records look like the same study."""
    if frame.empty or not record.get("export_id"):
        return []
    keys = transform_sources.dedup_keys(frame)
    mine = frame["export_id"] == record["export_id"]
    if not mine.any():
        return []
    key = keys[mine].iloc[0]
    if key.split("|", 1)[1] == "|":
        return []                                   # no identifiers to compare
    others = frame[(keys == key) & ~mine]
    return [f"{row.get('flora_id') or row.get('source_display_id')} has the same original "
            "and replication identifiers. If it is the same study, rule one of them a "
            "duplicate in Source Records; if they are different studies, nothing to do."
            for row in others.to_dict("records")]


def change_watermark(cur) -> int:
    """The newest change id, which a pipeline run notes as it starts."""
    if not _table_exists(cur):
        return 0
    cur.execute("SELECT COALESCE(MAX(change_id), 0) AS v FROM flora_record_changes")
    return int(cur.fetchone()["v"])


def changed_since(cur, watermark: int) -> set:
    """The records changed after a pipeline run noted the watermark. Each change
    rewrote its own row in flora_data, so the run keeps those rows as they are."""
    if not _table_exists(cur):
        return set()
    cur.execute("SELECT DISTINCT export_id FROM flora_record_changes WHERE change_id > %s",
                (int(watermark or 0),))
    return {row["export_id"] for row in cur.fetchall()}


# ── writing a change through to the FLoRA table ──────────────────────────────

def _published(row: dict) -> dict:
    """One FLoRA tab row as flora_data stores it: the output contract, serialised
    and read back exactly as a pipeline snapshot is, so a value written here
    equals the one the next run would write."""
    _, records, _ = flora_store._snapshot(transform_sources.to_output_shape(pd.DataFrame([row])))
    return records[0]


def publish(cur, before: dict, after: dict) -> bool:
    """Rewrite the record's row in flora_data with what a change altered.

    `before` and `after` are the record as the FLoRA tab showed it either side of
    the change, so everything that follows from it — a title brought in by a
    corrected DOI, an outcome derived from corrected axes — is written too, and
    nothing the change did not touch. Returns False when the record has no live
    row there yet (or the change leaves it untitled, which keeps it out of the
    export); the next pipeline run publishes it with the change applied.

    Moves the table's revision, which the public API's cache keys on and which
    makes a pipeline run that started before the change rebuild rather than
    overwrite it.
    """
    if transform_sources.drop_untitled(pd.DataFrame([after])).empty:
        return False
    old, new = _published(before), _published(after)
    changed = {column: value for column, value in new.items()
               if column not in ("id", "id_md5") and value != old.get(column)}
    if not changed:
        return False
    data = [column for column in flora_store.DATA_COLUMNS if column in changed]
    extras = {column: value for column, value in changed.items()
              if column not in flora_store.DATA_COLUMNS}
    # Column names come from the checked-in schema contract, never from input.
    assignments = "".join(f"{column} = %s, " for column in data)
    cur.execute(
        f"""
        UPDATE flora_data
           SET {assignments}extra_fields = extra_fields || %s::jsonb,
               record_version = record_version + 1, updated_at = NOW()
         WHERE id = %s AND retired_at IS NULL
        """,
        [*(changed[column] for column in data), Json(extras), new["id"]],
    )
    if not cur.rowcount:
        return False
    cur.execute("UPDATE flora_data_metadata SET updated_at = NOW() WHERE singleton")
    return True
