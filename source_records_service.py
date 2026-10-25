"""
source_records_service.py — data layer for the entry-sheet datatable.

Deliberately transport-agnostic: every function takes a cursor and returns plain
dicts, with no FastAPI (or Lambda) types anywhere. app.py wraps these in thin
routes today; the Lambda handlers planned for the next phase call the identical
functions. Domain errors are raised as RecordNotFound and mapped to status codes
by whatever transport is in front.

Source records are read-only here: they stay as the sheets and the validated set
supplied them. Corrections are made to the FLoRA record a row feeds (FLoRA tab,
flora_edits.py). The only decisions taken on this tab are duplicate rulings,
which say whether a row feeds FLoRA at all, not what it says.

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
    "outcome_computation",
    "outcome_robustness",
    "validation_status",
    "year_r",
    "reviewed_by",
    "reviewed_at",
    "version",
    "content_fingerprint",
    "deleted_at",
    "deleted_reason",
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

# The record's content, as the sheet supplied it. Written only by the sync:
# corrections are made to the FLoRA record this row feeds (flora_edits.py).
RECORD_FIELDS = [
    "ref_o", "doi_o", "url_o",
    "ref_r", "doi_r", "url_r",
    "abstract_r",
    "outcome", "outcome_quote", "out_quote_source",
    "year_r", "alt_identifier_o", "alt_identifier_r",
    "study_o",
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
    "validation_status",
]

DETAIL_COLUMNS = [
    "record_id::text AS record_id",
    "display_id", "source", "sheet_row_id", "type",
    *RECORD_FIELDS,
    "oa_work_id_o", "oa_work_id_r",
    "content_fingerprint",
    "reviewed_by", "reviewed_at", "version",
    "first_seen_at", "updated_at",
    "deleted_at", "deleted_reason",
    "raw",
]

# The reason a reviewer gives for a duplicate ruling: as long as a correction's note
# in the FLoRA tab (flora_edits.NOTE_LIMIT).
RULING_NOTE_LIMIT = 500


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


def _build_where(filters: dict):
    """Return (where_sql, params). Shared by the list, count and export paths so
    they can never disagree about what a filter means.

    Rows marked deleted (a record no longer validated) are listed only under the
    `deleted` filter; every other view, and the export, shows live rows."""
    clauses, params = [], []

    clauses.append("deleted_at IS NOT NULL" if filters.get("deleted") == "only"
                   else "deleted_at IS NULL")

    if filters.get("type"):
        clauses.append("type = %s")
        params.append(filters["type"])

    # Registry key, not paper type: `validated` holds both replications and
    # reproductions, so it can only be isolated by where the row came from.
    if filters.get("source"):
        clauses.append("source = %s")
        params.append(filters["source"])

    if filters.get("status"):
        clauses.append("validation_status = %s")
        params.append(filters["status"])

    if filters.get("outcome"):
        # One control covers both vocabularies: replications carry `outcome`,
        # reproductions carry the two dimensions.
        clauses.append(
            "(outcome = %s OR outcome_computation = %s OR outcome_robustness = %s)"
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
                   WHERE content_fingerprint IS NOT NULL AND deleted_at IS NULL
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
            WHERE content_fingerprint = ANY(%s) AND deleted_at IS NULL
            GROUP BY content_fingerprint HAVING COUNT(*) > 1
            """,
            (fingerprints,),
        )
        flagged = {r["content_fingerprint"] for r in cur.fetchall()}
    for r in rows:
        r["is_duplicate"] = r["deleted_at"] is None and r["content_fingerprint"] in flagged

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
            COUNT(*) FILTER (WHERE deleted_at IS NULL)                          AS all_records,
            COUNT(*) FILTER (WHERE deleted_at IS NULL AND type = 'replication')  AS replications,
            COUNT(*) FILTER (WHERE deleted_at IS NULL AND type = 'reproduction') AS reproductions,
            COUNT(*) FILTER (WHERE deleted_at IS NULL AND reviewed_at IS NOT NULL) AS reviewed,
            COUNT(*) FILTER (WHERE deleted_at IS NULL AND reviewed_at IS NULL)  AS unreviewed,
            COUNT(*) FILTER (WHERE deleted_at IS NULL AND source = 'validated') AS validated,
            COUNT(*) FILTER (WHERE deleted_at IS NOT NULL)                      AS deleted
        FROM source_records
        """
    )
    counts = dict(cur.fetchone())

    cur.execute(
        """
        SELECT COALESCE(SUM(n), 0) AS flagged FROM (
            SELECT COUNT(*) AS n FROM source_records
            WHERE content_fingerprint IS NOT NULL AND deleted_at IS NULL
            GROUP BY content_fingerprint HAVING COUNT(*) > 1
        ) t
        """
    )
    # SUM() returns Decimal; cast so the response is plain JSON numbers.
    counts["flagged"] = int(cur.fetchone()["flagged"])
    return counts


def get_record(cur, record_id: str, filters: dict | None = None,
               sort: str = "", direction: str = "asc") -> dict:
    """Full record for the read-only panel: every field, the raw sheet row, the
    history of edits made here before corrections moved to FLoRA records, any
    duplicate siblings, the FLoRA records this row feeds (where it is corrected),
    and the neighbouring ids in the active filter so the panel can walk the queue."""
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
                   outcome_computation, outcome_robustness
            FROM source_records
            WHERE content_fingerprint = %s AND record_id <> %s
            ORDER BY display_id
            """,
            (record["content_fingerprint"], record_id),
        )
        record["duplicates"] = [dict(r) for r in cur.fetchall()]

    record["flora"] = flora_records(cur, record_id)
    record["neighbours"] = neighbours(cur, record_id, filters or {}, sort, direction)
    return record


def flora_records(cur, record_id: str) -> list:
    """The FLoRA records this source row feeds: as the row a record derives from,
    or absorbed into one by deduplication. Where a correction to it is made."""
    cur.execute(
        """
        SELECT flora_id, export_id, retired_at,
               CASE WHEN primary_source_record_id = %(id)s::uuid THEN 'primary' ELSE 'merged' END AS role
        FROM flora_records
        WHERE primary_source_record_id = %(id)s::uuid OR %(id)s::uuid = ANY(merged_source_record_ids)
        ORDER BY (retired_at IS NOT NULL), (primary_source_record_id <> %(id)s::uuid), flora_id
        """,
        {"id": record_id},
    )
    return [dict(r) for r in cur.fetchall()]


def neighbours(cur, record_id: str, filters: dict, sort: str, direction: str) -> dict:
    """Previous/next record id and 1-based position within the active filter."""
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
        WHERE content_fingerprint IS NOT NULL AND deleted_at IS NULL
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
               outcome_computation, outcome_robustness,
               validation_status, sheet_row_id,
               duplicate_status, duplicate_of::text AS duplicate_of,
               duplicate_reviewed_by, duplicate_reviewed_at,
               -- The latest ruling's note, so the panel shows why beside who.
               (SELECT e.note FROM source_record_edits e
                 WHERE e.record_id = source_records.record_id
                   AND e.field = 'duplicate_status'
                 ORDER BY e.edited_at DESC LIMIT 1) AS duplicate_note
        FROM source_records
        WHERE content_fingerprint = ANY(%s) AND deleted_at IS NULL
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
        # Outcomes compared within each type: across types they use different
        # vocabularies, so they always "differ" and say nothing about a duplicate.
        outcomes_by_type = {}
        for m in members:
            outcomes_by_type.setdefault(m.get("type"), set()).add(
                m.get("outcome")
                or f"{m.get('outcome_computation')}/{m.get('outcome_robustness')}")
        type_counts = {}
        for m in members:
            type_counts[m.get("type")] = type_counts.get(m.get("type"), 0) + 1
        # The fingerprint keys on the paper, not the type, so a replication and a
        # reproduction of one paper land here too. Those are usually two valid
        # records; a 'duplicate' ruling between them removes one from FLoRA.
        mixed_types = len(type_counts) > 1
        groups.append({
            "fingerprint": fp,
            "members": members,
            # Members disagreeing about the outcome are the ones worth opening first.
            "outcomes_differ": any(len(v) > 1 for v in outcomes_by_type.values()),
            "cross_sheet": len({m["source"] for m in members}) > 1,
            "mixed_types": mixed_types,
            # Only one row of each type: nothing here is a likely duplicate.
            "_types_only": mixed_types and max(type_counts.values()) == 1,
            "resolved": all(m["duplicate_status"] for m in members),
        })

    # Groups that only pair one replication with one reproduction go last: they
    # are the least likely to be real duplicates, and first place invited the
    # ruling that removes a reproduction.
    groups.sort(key=lambda g: (g["_types_only"], not g["outcomes_differ"],
                               not g["cross_sheet"]))
    for g in groups:
        del g["_types_only"]
    return {"groups": groups, "total": len(groups)}


def resolve_duplicate(cur, record_id: str, status: str, admin_handle: str,
                      duplicate_of: str | None = None, note: str | None = None) -> dict:
    """Rule on one row of a duplicate group.

    'distinct'  — genuinely a different record; keep it in the transform output.
    'duplicate' — the same paper as duplicate_of; the transform drops it.

    `note` is the reviewer's reason. It goes into the edit log beside the ruling,
    as a correction's note does in the FLoRA tab, so the log says why, not only what.
    """
    if status not in ("distinct", "duplicate"):
        raise ValueError("status must be 'distinct' or 'duplicate'")
    if status == "duplicate" and not duplicate_of:
        raise ValueError("duplicate_of is required when marking a row as a duplicate")
    if duplicate_of == record_id:
        raise ValueError("A record cannot be a duplicate of itself")
    note = (note or "").strip() or None
    if note and len(note) > RULING_NOTE_LIMIT:
        raise ValueError(f"The reason is longer than {RULING_NOTE_LIMIT} characters")

    _check_uuid(record_id)
    cur.execute("SELECT display_id, deleted_at FROM source_records WHERE record_id = %s",
                (record_id,))
    row = cur.fetchone()
    if not row:
        raise RecordNotFound(record_id)
    if row["deleted_at"] is not None:
        raise ValueError("This row is deleted: it no longer feeds FLoRA, so there is "
                         "no duplicate to rule on.")

    if status == "duplicate":
        _check_uuid(duplicate_of)
        cur.execute(
            "SELECT display_id, duplicate_status, deleted_at FROM source_records "
            "WHERE record_id = %s",
            (duplicate_of,),
        )
        survivor = cur.fetchone()
        if not survivor:
            raise RecordNotFound(duplicate_of)
        if survivor["deleted_at"] is not None:
            raise ValueError(f"{survivor['display_id']} is deleted and no longer feeds "
                             "FLoRA, so it cannot be the copy that is kept.")
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
    # Display ids, not record ids: the log is read by people.
    ruled_label = row["display_id"] or record_id
    why = f": {note}" if note else ""
    cur.execute(
        """
        INSERT INTO source_record_edits
            (record_id, field, old_value, new_value, edited_by, note)
        VALUES (%s, 'duplicate_status', NULL, %s, %s, %s)
        """,
        (record_id, status, admin_handle,
         f"duplicate of {survivor['display_id'] or duplicate_of}{why}" if status == "duplicate"
         else note),
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
                (duplicate_of, admin_handle, f"kept as the survivor of {ruled_label}{why}"),
            )

    return {"record_id": record_id, "duplicate_status": status,
            "duplicate_of": duplicate_of, "survivor_marked": survivor_marked, "note": note}


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
