"""
source_records_service.py — data layer for the entry-sheet datatable.

Deliberately transport-agnostic: every function takes a cursor and returns plain
dicts, with no FastAPI (or Lambda) types anywhere. app.py wraps these in thin
routes today; the Lambda handlers planned for the next phase call the identical
functions. Domain errors are raised as RecordNotFound / VersionConflict and
mapped to status codes by whatever transport is in front.

Cursors are expected to be psycopg2 RealDictCursor, as db() in app.py provides.
"""
import re

# Grid columns. Kept lean — the heavy fields (abstract_r, quotes, raw) load only
# when a single record is opened.
LIST_COLUMNS = [
    "record_id::text AS record_id",
    "display_id",
    "source",
    "type",
    "ref_o", "doi_o",
    "ref_r", "doi_r", "url_r",
    "outcome",
    "outcome_computational",
    "outcome_robustness",
    "validation_status",
    "year_r",
    "reviewed_by",
    "reviewed_at",
    "version",
    "content_fingerprint",
]

# Whitelist — never interpolate a caller-supplied sort into SQL.
SORT_COLUMNS = {
    "display_id":        "display_id",
    "type":              "type",
    "ref_o":             "ref_o",
    "ref_r":             "ref_r",
    "doi_o":             "doi_o",
    "doi_r":             "doi_r",
    "outcome":           "outcome",
    "validation_status": "validation_status",
    "year_r":            "year_r",
    "reviewed_at":       "reviewed_at",
}

ACCEPTED_STATUSES = [
    "validated - chosen",
    "validated - changed",
    "validated - unchanged",
]

# Fields a reviewer may edit. Everything else (identity, raw, review state) is
# writable only by the sync or by the service itself.
EDITABLE_FIELDS = [
    "ref_o", "doi_o", "url_o",
    "ref_r", "doi_r", "url_r",
    "abstract_r",
    "outcome", "outcome_quote", "out_quote_source",
    "year_r", "alt_identifier_o", "alt_identifier_r",
    "study_o",
    "outcome_computational", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
    "validation_status",
]

DETAIL_COLUMNS = [
    "record_id::text AS record_id",
    "display_id", "source", "sheet_row_id", "type",
    *EDITABLE_FIELDS,
    "oa_work_id_o", "oa_work_id_r",
    "content_fingerprint",
    "reviewed_by", "reviewed_at", "version",
    "first_seen_at", "updated_at",
    "raw",
]


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _check_uuid(record_id: str) -> str:
    """record_id reaches SQL as a UUID column comparison, so a malformed value
    raises a psycopg2 DataError that escapes the not-found handler and surfaces
    as a 500. Treat anything that isn't a UUID as simply not found."""
    if not record_id or not _UUID_RE.match(str(record_id)):
        raise RecordNotFound(record_id)
    return record_id


def _escape_like(term: str) -> str:
    """Escape ILIKE metacharacters. Without this, a DOI containing '_' matches any
    character in that position — and DOIs contain underscores often enough to
    give visibly wrong search results."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class RecordNotFound(Exception):
    """No source_records row with that id (or the id isn't a UUID at all)."""


class VersionConflict(Exception):
    """Row changed since the caller loaded it. Carries the current row."""

    def __init__(self, current: dict):
        super().__init__("Record was modified by someone else")
        self.current = current


def _build_where(filters: dict):
    """Return (where_sql, params). Shared by the list, count and export paths so
    they can never disagree about what a filter means."""
    clauses, params = [], []

    if filters.get("type"):
        clauses.append("type = %s")
        params.append(filters["type"])

    if filters.get("status"):
        clauses.append("validation_status = %s")
        params.append(filters["status"])

    if filters.get("outcome"):
        # One control covers both vocabularies: replications carry `outcome`,
        # reproductions carry the two dimensions.
        clauses.append(
            "(outcome = %s OR outcome_computational = %s OR outcome_robustness = %s)"
        )
        params.extend([filters["outcome"]] * 3)

    reviewed = filters.get("reviewed")
    if reviewed == "yes":
        clauses.append("reviewed_at IS NOT NULL")
    elif reviewed == "no":
        clauses.append("reviewed_at IS NULL")

    if filters.get("flagged"):
        # Same paper under more than one sheet UUID. Computed across the whole
        # table, so it also catches a study present in both entry sheets.
        clauses.append(
            """content_fingerprint IN (
                   SELECT content_fingerprint FROM source_records
                   WHERE content_fingerprint IS NOT NULL
                   GROUP BY content_fingerprint HAVING COUNT(*) > 1
               )"""
        )

    search = (filters.get("search") or "").strip()
    if search:
        clauses.append(
            "(ref_o ILIKE %s OR ref_r ILIKE %s OR doi_o ILIKE %s "
            " OR doi_r ILIKE %s OR display_id ILIKE %s OR study_o ILIKE %s)"
        )
        params.extend([f"%{_escape_like(search)}%"] * 6)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_records(cur, filters: dict, sort: str = "", direction: str = "asc",
                 page: int = 1, per_page: int = 50) -> dict:
    """One page of the grid, plus the counts the filter chips display."""
    where, params = _build_where(filters)

    sort_col = SORT_COLUMNS.get(sort)
    dir_sql = "ASC" if str(direction).lower() == "asc" else "DESC"
    order_by = (
        f"ORDER BY {sort_col} {dir_sql} NULLS LAST, display_id ASC"
        if sort_col else "ORDER BY display_id ASC"
    )

    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 200))
    offset = (page - 1) * per_page

    cur.execute(f"SELECT COUNT(*) AS n FROM source_records {where}", params)
    total = cur.fetchone()["n"]

    cur.execute(
        f"""
        SELECT {", ".join(LIST_COLUMNS)}
        FROM source_records
        {where}
        {order_by}
        LIMIT %s OFFSET %s
        """,
        params + [per_page, offset],
    )
    rows = [dict(r) for r in cur.fetchall()]

    # Mark the duplicate-paper rows on this page in one query rather than N.
    fingerprints = [r["content_fingerprint"] for r in rows if r["content_fingerprint"]]
    flagged = set()
    if fingerprints:
        cur.execute(
            """
            SELECT content_fingerprint FROM source_records
            WHERE content_fingerprint = ANY(%s)
            GROUP BY content_fingerprint HAVING COUNT(*) > 1
            """,
            (fingerprints,),
        )
        flagged = {r["content_fingerprint"] for r in cur.fetchall()}
    for r in rows:
        r["is_duplicate"] = r["content_fingerprint"] in flagged

    return {
        "records": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "counts": _counts(cur),
    }


def _counts(cur) -> dict:
    cur.execute(
        """
        SELECT
            COUNT(*)                                        AS all_records,
            COUNT(*) FILTER (WHERE type = 'replication')    AS replications,
            COUNT(*) FILTER (WHERE type = 'reproduction')   AS reproductions,
            COUNT(*) FILTER (WHERE reviewed_at IS NOT NULL) AS reviewed,
            COUNT(*) FILTER (WHERE reviewed_at IS NULL)     AS unreviewed
        FROM source_records
        """
    )
    counts = dict(cur.fetchone())

    cur.execute(
        """
        SELECT COALESCE(SUM(n), 0) AS flagged FROM (
            SELECT COUNT(*) AS n FROM source_records
            WHERE content_fingerprint IS NOT NULL
            GROUP BY content_fingerprint HAVING COUNT(*) > 1
        ) t
        """
    )
    # SUM() returns Decimal; cast so the response is plain JSON numbers.
    counts["flagged"] = int(cur.fetchone()["flagged"])
    return counts


def get_record(cur, record_id: str, filters: dict | None = None,
               sort: str = "", direction: str = "asc") -> dict:
    """Full record for the review panel: every field, the raw sheet row, edit
    history, any duplicate siblings, and the neighbouring ids in the active
    filter so 'Save & next' can walk the queue."""
    _check_uuid(record_id)
    cur.execute(
        f"SELECT {', '.join(DETAIL_COLUMNS)} FROM source_records WHERE record_id = %s",
        (record_id,),
    )
    row = cur.fetchone()
    if not row:
        raise RecordNotFound(record_id)
    record = dict(row)

    cur.execute(
        """
        SELECT field, old_value, new_value, edited_by, edited_at, note
        FROM source_record_edits
        WHERE record_id = %s
        ORDER BY edited_at DESC
        """,
        (record_id,),
    )
    record["history"] = [dict(r) for r in cur.fetchall()]

    record["duplicates"] = []
    if record.get("content_fingerprint"):
        cur.execute(
            """
            SELECT record_id::text AS record_id, display_id, source, outcome,
                   outcome_computational, outcome_robustness
            FROM source_records
            WHERE content_fingerprint = %s AND record_id <> %s
            ORDER BY display_id
            """,
            (record["content_fingerprint"], record_id),
        )
        record["duplicates"] = [dict(r) for r in cur.fetchall()]

    record["neighbours"] = neighbours(cur, record_id, filters or {}, sort, direction)
    return record


def neighbours(cur, record_id: str, filters: dict, sort: str, direction: str) -> dict:
    """Previous/next record id and 1-based position within the active filter.

    Must be computed BEFORE a save: stamping reviewed_at can move the row out of
    its own filter (e.g. "not reviewed"), and then the row is absent from the
    ordered set and there is no next id to hand back.
    """
    _check_uuid(record_id)
    where, params = _build_where(filters)
    sort_col = SORT_COLUMNS.get(sort)
    dir_sql = "ASC" if str(direction).lower() == "asc" else "DESC"
    order_by = (
        f"{sort_col} {dir_sql} NULLS LAST, display_id ASC"
        if sort_col else "display_id ASC"
    )

    cur.execute(
        f"""
        WITH ordered AS (
            SELECT record_id::text AS record_id,
                   ROW_NUMBER() OVER (ORDER BY {order_by}) AS pos,
                   COUNT(*)     OVER ()                    AS total
            FROM source_records
            {where}
        )
        SELECT
            (SELECT record_id FROM ordered WHERE pos = o.pos - 1) AS prev_id,
            (SELECT record_id FROM ordered WHERE pos = o.pos + 1) AS next_id,
            o.pos, o.total
        FROM ordered o WHERE o.record_id = %s
        """,
        params + [record_id],
    )
    row = cur.fetchone()
    return dict(row) if row else {"prev_id": None, "next_id": None, "pos": None, "total": 0}


def export_rows(cur, filters: dict):
    """(columns, rows) for CSV export of the current filter. Everything matching,
    not just the current page."""
    where, params = _build_where(filters)
    columns = [c.split(" AS ")[-1] for c in DETAIL_COLUMNS if c != "raw"]
    select = [c for c in DETAIL_COLUMNS if c != "raw"]
    cur.execute(
        f"SELECT {', '.join(select)} FROM source_records {where} ORDER BY display_id",
        params,
    )
    return columns, [dict(r) for r in cur.fetchall()]


# Fields offered as a dropdown rather than free text. Options come from the data
# itself (see field_vocabularies) so a new value appearing upstream shows up
# without a code change.
VOCABULARY_FIELDS = [
    "outcome",
    "out_quote_source",
    "validation_status",
    "outcome_computational",
    "outcome_robustness",
    "out_quote_computational_source",
    "out_quote_robust_source",
]


def field_vocabularies(cur) -> dict:
    """Distinct non-empty values per dropdown field, from the stored rows."""
    vocab = {}
    for field in VOCABULARY_FIELDS:
        cur.execute(
            f"""
            SELECT DISTINCT {field} AS v FROM source_records
            WHERE {field} IS NOT NULL AND {field} <> ''
            ORDER BY v
            """
        )
        vocab[field] = [r["v"] for r in cur.fetchall()]
    return vocab


def update_record(cur, record_id: str, fields: dict, version: int,
                  admin_handle: str, note: str = "") -> dict:
    """Apply a reviewer's edits and stamp the review.

    Only fields in EDITABLE_FIELDS are accepted, and only fields whose value
    actually changed are written or logged. The review stamp is applied even when
    nothing changed — "checked and correct" is a real outcome, and without it a
    reviewer cannot tell that apart from "not looked at yet".

    Raises VersionConflict if the row moved since the caller loaded it.
    """
    # An absent version would silently skip the concurrency check, so require it
    # rather than letting a caller (or a future Lambda handler) opt out.
    if version is None:
        raise ValueError("version is required")

    # Lock the row so a concurrent save can't slip between the version check and
    # the write.
    _check_uuid(record_id)
    cur.execute(
        f"SELECT {', '.join(DETAIL_COLUMNS)} FROM source_records WHERE record_id = %s FOR UPDATE",
        (record_id,),
    )
    current = cur.fetchone()
    if not current:
        raise RecordNotFound(record_id)
    current = dict(current)

    if int(version) != int(current["version"]):
        raise VersionConflict(current)

    unknown = [k for k in fields if k not in EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")

    def _clean(v):
        # Empty string and None both mean "absent"; normalise so a cleared field
        # doesn't register as a change every time it is saved.
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    changed = {
        k: _clean(v) for k, v in fields.items()
        if _clean(v) != (current.get(k) if current.get(k) is not None else None)
    }

    if changed:
        assignments = ", ".join(f"{k} = %({k})s" for k in changed)
        cur.execute(
            f"""
            UPDATE source_records
            SET {assignments},
                reviewed_by = %(_by)s, reviewed_at = NOW(),
                version = version + 1, updated_at = NOW()
            WHERE record_id = %(_id)s
            """,
            {**changed, "_by": admin_handle, "_id": record_id},
        )
        for field, new_value in changed.items():
            cur.execute(
                """
                INSERT INTO source_record_edits
                    (record_id, field, old_value, new_value, edited_by, note)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (record_id, field, current.get(field), new_value, admin_handle, note or None),
            )
    else:
        # No field changed, but the row was still reviewed.
        cur.execute(
            """
            UPDATE source_records
            SET reviewed_by = %s, reviewed_at = NOW(), version = version + 1, updated_at = NOW()
            WHERE record_id = %s
            """,
            (admin_handle, record_id),
        )

    return {"changed_fields": sorted(changed), "reviewed_by": admin_handle}


def duplicate_groups(cur, unresolved_only: bool = True) -> dict:
    """Papers appearing under more than one sheet UUID, grouped for side-by-side
    comparison. Computed across the whole table, so it also catches a study
    present in both entry sheets — which a per-source check cannot see."""
    having = "HAVING COUNT(*) > 1"
    if unresolved_only:
        # A group is done when every row in it has been ruled on.
        having += " AND COUNT(*) FILTER (WHERE duplicate_status IS NULL) > 0"

    cur.execute(
        f"""
        SELECT content_fingerprint FROM source_records
        WHERE content_fingerprint IS NOT NULL
        GROUP BY content_fingerprint {having}
        ORDER BY MIN(display_id)
        """
    )
    fingerprints = [r["content_fingerprint"] for r in cur.fetchall()]
    if not fingerprints:
        return {"groups": [], "total": 0}

    cur.execute(
        """
        SELECT record_id::text AS record_id, content_fingerprint, display_id, source, type,
               doi_o, doi_r, url_r, ref_o, ref_r, outcome,
               outcome_computational, outcome_robustness,
               validation_status, sheet_row_id,
               duplicate_status, duplicate_of::text AS duplicate_of,
               duplicate_reviewed_by, duplicate_reviewed_at
        FROM source_records
        WHERE content_fingerprint = ANY(%s)
        ORDER BY content_fingerprint, display_id
        """,
        (fingerprints,),
    )
    grouped = {}
    for row in cur.fetchall():
        grouped.setdefault(row["content_fingerprint"], []).append(dict(row))

    groups = []
    for fp in fingerprints:
        members = grouped.get(fp, [])
        outcomes = {
            m.get("outcome") or f"{m.get('outcome_computational')}/{m.get('outcome_robustness')}"
            for m in members
        }
        groups.append({
            "fingerprint": fp,
            "members": members,
            # Members disagreeing about the outcome are the ones worth opening first.
            "outcomes_differ": len(outcomes) > 1,
            "cross_sheet": len({m["source"] for m in members}) > 1,
            "resolved": all(m["duplicate_status"] for m in members),
        })

    groups.sort(key=lambda g: (not g["outcomes_differ"], not g["cross_sheet"]))
    return {"groups": groups, "total": len(groups)}


def resolve_duplicate(cur, record_id: str, status: str, admin_handle: str,
                      duplicate_of: str | None = None) -> dict:
    """Rule on one row of a duplicate group.

    'distinct'  — genuinely a different record; keep it in the transform output.
    'duplicate' — the same paper as duplicate_of; the transform drops it.
    """
    if status not in ("distinct", "duplicate"):
        raise ValueError("status must be 'distinct' or 'duplicate'")
    if status == "duplicate" and not duplicate_of:
        raise ValueError("duplicate_of is required when marking a row as a duplicate")
    if duplicate_of == record_id:
        raise ValueError("A record cannot be a duplicate of itself")

    _check_uuid(record_id)
    cur.execute("SELECT 1 FROM source_records WHERE record_id = %s", (record_id,))
    if not cur.fetchone():
        raise RecordNotFound(record_id)

    if status == "duplicate":
        _check_uuid(duplicate_of)
        cur.execute(
            "SELECT display_id, duplicate_status FROM source_records WHERE record_id = %s",
            (duplicate_of,),
        )
        survivor = cur.fetchone()
        if not survivor:
            raise RecordNotFound(duplicate_of)
        # Otherwise both rows end up 'duplicate' (the survivor auto-mark below is
        # guarded by IS NULL and would silently no-op), and the transform drops
        # the paper entirely because it excludes every 'duplicate' row.
        if survivor["duplicate_status"] == "duplicate":
            raise ValueError(
                f"{survivor['display_id']} is itself marked a duplicate — "
                "mark it distinct first, or point at the surviving record"
            )

    cur.execute(
        """
        UPDATE source_records
        SET duplicate_status = %s,
            duplicate_of = %s,
            duplicate_reviewed_by = %s,
            duplicate_reviewed_at = NOW(),
            updated_at = NOW()
        WHERE record_id = %s
        """,
        (status, duplicate_of if status == "duplicate" else None, admin_handle, record_id),
    )
    cur.execute(
        """
        INSERT INTO source_record_edits
            (record_id, field, old_value, new_value, edited_by, note)
        VALUES (%s, 'duplicate_status', NULL, %s, %s, %s)
        """,
        (record_id, status, admin_handle,
         f"duplicate of {duplicate_of}" if duplicate_of else None),
    )

    # Marking X a duplicate OF Y is a decision to keep Y, so record that rather
    # than making the reviewer click twice — otherwise a two-row group can never
    # finish, because the survivor stays unruled forever.
    survivor_marked = False
    if status == "duplicate":
        cur.execute(
            """
            UPDATE source_records
            SET duplicate_status = 'distinct',
                duplicate_reviewed_by = %s, duplicate_reviewed_at = NOW(), updated_at = NOW()
            WHERE record_id = %s AND duplicate_status IS NULL
            """,
            (admin_handle, duplicate_of),
        )
        survivor_marked = cur.rowcount > 0
        if survivor_marked:
            cur.execute(
                """
                INSERT INTO source_record_edits
                    (record_id, field, old_value, new_value, edited_by, note)
                VALUES (%s, 'duplicate_status', NULL, 'distinct', %s, %s)
                """,
                (duplicate_of, admin_handle, f"kept as the survivor of {record_id}"),
            )

    return {"record_id": record_id, "duplicate_status": status,
            "duplicate_of": duplicate_of, "survivor_marked": survivor_marked}


def sync_status(cur) -> dict:
    """Latest run per source, for the freshness banner. Without it nobody can tell
    whether the table is current, and stale-but-plausible data is the worst kind."""
    cur.execute(
        """
        SELECT DISTINCT ON (source)
               source, status, started_at, finished_at, failure_reason,
               rows_fetched, rows_accepted, rows_inserted, rows_existing,
               rows_skipped, dup_ids, dup_fingerprints
        FROM source_sync_runs
        ORDER BY source, started_at DESC
        """
    )
    return {"sources": [dict(r) for r in cur.fetchall()]}
