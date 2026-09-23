"""flora_service.py — data layer for the FLoRA tab.

The tab shows the *product*: `transform_sources.build()` output, with the stable
`flora_id` from `flora_records` attached. Nothing is materialised — the dataset is
derived from source_records on demand, so it can never drift from the records the
Source Records tab shows.

Transport-agnostic like source_records_service: cursors in, plain dicts out.

WHY A CACHE
-----------
A build is ~0.5s in-process — fine once, wasteful on every keystroke of a search
box and every page change. The frame is cached and reused until the underlying
tables actually change, detected by a cheap signature query (row counts plus the
latest updated_at) rather than a timer, so an edit in the Source Records tab shows
up here immediately instead of after an arbitrary delay.

The cache is per process. Several pods each keep their own; they agree because
they derive from the same rows.
"""
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import flora_registry
import preprint_dedup
import transform_sources
import final_export

# Columns the grid shows. The heavy ones (abstract_r, the quotes) are fetched only
# when a single record is opened, and are always present in the export.
LIST_COLUMNS = [
    "flora_id", "export_id", "source_display_id",
    "type", "source",
    # oa_work_id_* rides with the DOI it was resolved from, on both sides. It is the
    # identifier for papers OpenAlex knows but a DOI lookup cannot reach directly,
    # and it is the only id on a row whose DOI is a placeholder.
    "doi_o", "oa_work_id_o", "ref_o",
    "doi_r", "oa_work_id_r", "url_r", "ref_r",
    "outcome",
]

SORT_COLUMNS = {
    "flora_id": "flora_id",
    "source_display_id": "source_display_id",
    "type": "type",
    "source": "source",
    "doi_o": "doi_o",
    "doi_r": "doi_r",
    "outcome": "outcome",
}

_cache = {"signature": None, "frame": None, "dedup_log": None}
_lock = threading.Lock()


def _signature(cur) -> tuple:
    """Cheap 'has anything changed' probe.

    Counts plus the newest updated_at. A reviewer's edit bumps updated_at, a sync
    bumps the count, and a registry refresh bumps the flora count — so every way
    the dataset can change moves this.
    """
    cur.execute(
        """
        SELECT (SELECT COUNT(*) FROM source_records)                AS n_source,
               (SELECT MAX(updated_at) FROM source_records)         AS max_updated,
               (SELECT COUNT(*) FROM source_records
                 WHERE duplicate_status IS NOT NULL)                AS n_ruled,
               (SELECT COUNT(*) FROM flora_records)                 AS n_flora,
               (SELECT COUNT(*) FROM transform_exclusions)          AS n_excluded,
               (SELECT MAX(last_seen_at) FROM flora_records)        AS registry_updated,
               (SELECT COUNT(*) FROM work_metadata)                 AS n_metadata,
               (SELECT MAX(fetched_at) FROM work_metadata)          AS metadata_updated,
               (SELECT MAX(reference_checked_at) FROM work_metadata) AS references_updated,
               (SELECT md5(string_agg(row_to_json(e)::text, '' ORDER BY row_to_json(e)::text))
                  FROM transform_exclusions e) AS exclusions_signature,
               (SELECT md5(string_agg(row_to_json(a)::text, '' ORDER BY row_to_json(a)::text))
                  FROM outcome_alias a) AS aliases_signature,
               -- DOI order too: keep_1 names doi_1, so a re-ruling that stores the
               -- pair the other way round changes the outcome with the same action.
               (SELECT md5(string_agg(concat_ws('|', pair_key, side, doi_1, doi_2, action),
                                      '' ORDER BY pair_key))
                  FROM preprint_dedup_decisions) AS dedup_decisions_signature
        """
    )
    row = cur.fetchone()
    return (row["n_source"], str(row["max_updated"]), row["n_ruled"],
            row["n_flora"], row["n_excluded"],
            *(str(row.get(field)) for field in ("registry_updated", "n_metadata",
              "metadata_updated", "references_updated", "exclusions_signature", "aliases_signature",
              "dedup_decisions_signature")))


def _current(cur) -> "tuple[pd.DataFrame, list]":
    """The cached build: the frame, and the preprint dedup log that came with it."""
    signature = _signature(cur)
    with _lock:
        if _cache["signature"] == signature and _cache["frame"] is not None:
            return _cache["frame"], _cache["dedup_log"]

    frame = transform_sources.build(cur, verbose=False)
    # Taken before the joins below: attrs are metadata, and the review queue must
    # not depend on whether a later frame operation carries them along.
    dedup_log = list(frame.attrs.get("preprint_dedup_log") or [])
    frame = flora_registry.attach_ids(cur, frame)
    frame = _attach_merged_ids(cur, frame)
    # Pinned on the cached frame too, so a caller holding the frame reads the log of
    # that same build — not whatever a concurrent build left in the cache since.
    frame.attrs["preprint_dedup_log"] = dedup_log

    with _lock:
        _cache["signature"] = signature
        _cache["frame"] = frame
        _cache["dedup_log"] = dedup_log
    return frame, dedup_log


def dataset(cur) -> pd.DataFrame:
    """The prepared FLoRA dataset with flora_id attached. Read-only."""
    return _current(cur)[0]


def _attach_merged_ids(cur, frame):
    """Add the display_ids the dedup collapsed into each row, as one searchable string.

    Without this, the 314 absorbed source records are a dead end: someone reads
    REPL-000481 in the Source Records tab, searches for it here, finds nothing, and
    concludes the record was dropped — when in fact its content is in the product
    under the id of the row that absorbed it.

    Not a grid column: it is for finding a row, not for reading.
    """
    if frame.empty:
        return frame
    cur.execute(
        """
        SELECT f.primary_source_record_id::text AS sid,
               string_agg(s.display_id, ' ' ORDER BY s.display_id) AS merged
        FROM flora_records f
        JOIN source_records s ON s.record_id = ANY(f.merged_source_record_ids)
        GROUP BY f.primary_source_record_id
        """
    )
    mapping = {r["sid"]: r["merged"] for r in cur.fetchall()}
    frame = frame.copy()
    frame["merged_display_ids"] = frame["source_record_id"].map(mapping)
    return frame


def invalidate() -> None:
    """Drop the cached frame. For tests and for a caller that knows it just wrote."""
    with _lock:
        _cache["signature"] = None
        _cache["frame"] = None
        _cache["dedup_log"] = None


def _cell(value):
    """One frame cell as something the JSON encoder will accept.

    Both endpoints that hand a frame row to FastAPI have to come through here,
    because a frame carries two kinds of value the response layer refuses:

    * MISSING TEXT. pandas 3 stores a `str` column's missing values as NaN, and
      `DataFrame.where(cond, None)` cannot put a None into one — the float comes
      straight back out. Starlette's JSONResponse serialises with
      `allow_nan=False`, so a single missing DOI turned the whole page into a 500
      rather than into a null.
    * NULLABLE NUMERICS. Int64/Float64 columns (author_overlap and its percentage)
      hand back np.int64/np.float64, which jsonable_encoder cannot encode at all.

    Checked in that order: a numpy scalar is unwrapped first so that np.float64
    NaN is then recognised as missing.
    """
    if isinstance(value, np.generic):
        value = value.item()
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        # pd.isna on a list or an array answers element-wise, which is not a
        # question about this cell. Nothing in the grid is a container today; this
        # only stops a future column from raising here.
        return value
    return value


def _apply_filters(frame: pd.DataFrame, filters: dict) -> pd.DataFrame:
    out = frame

    if filters.get("type"):
        out = out[out["type"] == filters["type"]]

    if filters.get("source"):
        out = out[out["source"] == filters["source"]]

    if filters.get("outcome"):
        out = out[out["outcome"] == filters["outcome"]]

    # Rows the registry has not reached yet. Worth being able to find: it means a
    # sync landed rows and no refresh has run since.
    if filters.get("unregistered"):
        out = out[out["flora_id"].isna()]

    search = (filters.get("search") or "").strip()
    if search:
        cols = ["flora_id", "export_id", "source_display_id", "doi_o", "doi_r", "ref_o", "ref_r",
                # A W-id pasted from OpenAlex finds its row, which is most of the
                # point of carrying the id at all.
                "oa_work_id_o", "oa_work_id_r",
                # So a source id that was collapsed into another row still finds it.
                "merged_display_ids"]
        cols = [c for c in cols if c in out.columns]
        mask = False
        for col in cols:
            mask = mask | out[col].astype(str).str.contains(search, case=False, na=False, regex=False)
        out = out[mask]

    return out


def list_records(cur, filters: dict, sort: str = "", direction: str = "asc",
                 page: int = 1, per_page: int = 50) -> dict:
    frame = dataset(cur)
    filtered = _apply_filters(frame, filters)

    sort_col = SORT_COLUMNS.get(sort)
    if sort_col:
        filtered = filtered.sort_values(
            sort_col, ascending=(str(direction).lower() == "asc"),
            na_position="last", kind="stable",
        )

    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 200))
    start = (page - 1) * per_page

    window = filtered.iloc[start:start + per_page]
    records = [
        {key: _cell(value) for key, value in row.items()}
        for row in window.reindex(columns=LIST_COLUMNS).to_dict("records")
    ]

    return {
        "records": records,
        "total": int(len(filtered)),
        "page": page,
        "per_page": per_page,
        "counts": {**counts(frame),
                   # Drives the badge on the tab's review button, so nobody has to
                   # open the queue to learn whether it is empty.
                   "preprint_pending": len(preprint_dedup.unresolved(
                       frame.attrs.get("preprint_dedup_log") or []))},
    }


def counts(frame: pd.DataFrame) -> dict:
    return {
        "all_records": int(len(frame)),
        "replications": int((frame["type"] == "replication").sum()),
        "reproductions": int((frame["type"] == "reproduction").sum()),
        "unregistered": int(frame["flora_id"].isna().sum()),
        # Rows the published export drops for want of a title (the notebook's
        # Step 10). Counted here rather than filtered out: this grid is where
        # someone fixes them, and a row hidden here is a row nobody fixes.
        "untitled": len(frame) - len(transform_sources.drop_untitled(frame)),
        "sources": sorted(frame["source"].dropna().unique().tolist()),
        "outcomes": sorted(frame["outcome"].dropna().unique().tolist()),
    }


def get_record(cur, flora_id: str) -> "dict | None":
    """One full row, every column. Used by the detail panel."""
    frame = dataset(cur)
    matched = frame["flora_id"] == flora_id
    if "export_id" in frame:
        matched = matched | (frame["export_id"] == flora_id)
    match = frame[matched]
    if match.empty:
        return None
    row = match.iloc[0]
    return {key: _cell(value) for key, value in row.items()}


def merged_sources(cur, flora_id: str) -> list:
    """Source records collapsed into this row, for the detail panel."""
    cur.execute(
        """
        SELECT s.display_id, s.source, s.doi_o, s.doi_r, s.outcome
        FROM flora_records f
        JOIN source_records s ON s.record_id = ANY(f.merged_source_record_ids)
        WHERE f.flora_id = %s OR f.export_id = %s
        ORDER BY s.display_id
        """,
        (flora_id, flora_id),
    )
    return [dict(r) for r in cur.fetchall()]


def export_csv(cur, filters: dict) -> str:
    """Every row matching the filter, all columns, as CSV text.

    Column order is the FLoRA output contract — the same order transform_sources
    writes — so this file and the nightly artifact are interchangeable rather than
    subtly different.
    """
    frame = transform_sources.drop_untitled(_apply_filters(dataset(cur), filters))
    if not frame.empty and (
        "export_id" not in frame or "export_position" not in frame
        or frame["export_id"].map(final_export.text).eq("").any()
        or frame["export_position"].isna().any()
    ):
        raise ValueError("Run the pipeline before exporting: some records do not yet have permanent IDs.")
    # The FLoRA output contract (output_cols in the R notebook), with abstract_r,
    # the reproduction axes and our provenance ids kept after it. Also drops
    # merged_display_ids, which is a search helper and not part of the contract.
    ordered = transform_sources.to_output_shape(frame)
    # Explicit newline: pandas otherwise picks the platform line ending, so the same
    # export would differ between a Windows dev box and the Linux server. A served
    # file should not depend on which machine produced it.
    return final_export.csv_text(ordered)


def history(cur, window_days: int = 120) -> dict:
    """The dataset-size series for the public page.

    Reads counts only — never builds the frame. This is served unauthenticated, so
    it must stay cheap and must expose nothing but aggregate totals: no identifiers,
    no references, no reviewer information.

    ONE ROW PER CALENDAR DAY, NOT PER RUN
    -------------------------------------
    flora_dataset_history has a row only for days something happened. Plotting those
    rows directly puts Aug 10, Aug 14 and Aug 21 at equal spacing on the x-axis,
    which makes a quiet fortnight look like the same elapsed time as a busy day and
    misstates every slope on the chart. So the series is filled out to a real daily
    calendar:

    `source_rows`  is computed for every day as the number of source records whose
                   first_seen_at falls on or before it. source_records is insert-only
                   and first_seen_at never moves, so this is exact for days no run
                   touched — it is a count, not an interpolation.

    `total_rows`   is carried forward from the last run. The published dataset only
                   changes when the pipeline regenerates it, so on a day with no run
                   it genuinely still held the previous run's count. It stays NULL
                   before the first recorded run rather than being extended backwards:
                   today's exclusion and deduplication rules did not exist then, and
                   applying them to an older set of rows would produce a number that
                   was never true on that day.
    """
    cur.execute(
        """
        WITH bounds AS (
            SELECT LEAST(
                       (SELECT MIN(first_seen_at::date) FROM source_records),
                       (SELECT MIN(recorded_on) FROM flora_dataset_history),
                       CURRENT_DATE
                   ) AS first_day,
                   GREATEST(
                       (SELECT MAX(recorded_on) FROM flora_dataset_history),
                       CURRENT_DATE
                   ) AS last_day
        ),
        calendar AS (
            SELECT generate_series(first_day, last_day, INTERVAL '1 day')::date AS day
            FROM bounds
        ),
        arrivals AS (
            SELECT first_seen_at::date AS day, COUNT(*) AS n
            FROM source_records
            GROUP BY 1
        )
        SELECT c.day,
               COALESCE(SUM(a.n) OVER (ORDER BY c.day), 0)::int AS source_rows,
               h.total_rows,
               h.replications,
               h.reproductions,
               -- Derived from total_rows, NOT from the row existing.
               -- flora_dataset_history also holds SEEDED days: backfill_source_history
               -- writes recorded_on + source_rows, and no total, for every past day
               -- source records arrived on. Those are days the pipeline did not run,
               -- so the carry-forward below fills their total in — and keying this on
               -- the row's existence would then publish that carried figure as a
               -- reading, which is the one thing this page must not do.
               h.total_rows IS NOT NULL AS was_run
        FROM calendar c
        LEFT JOIN arrivals a ON a.day = c.day
        LEFT JOIN flora_dataset_history h ON h.recorded_on = c.day
        ORDER BY c.day
        """
    )
    rows = [dict(r) for r in cur.fetchall()]

    carried = None
    for r in rows:
        if r["total_rows"] is not None:
            carried = r
        elif carried is not None:
            r["total_rows"] = carried["total_rows"]
            r["replications"] = carried["replications"]
            r["reproductions"] = carried["reproductions"]

    series = [{
        "date": str(r["day"]),
        "total_rows": r["total_rows"],
        "source_rows": r["source_rows"],
        # The chart draws every day; the table marks which of them is a reading
        # rather than a day the previous reading still stood for.
        "measured": r["was_run"],
    } for r in rows]

    # Month-end: where each month finished, which is the value that was true at the
    # end of it. A monthly mean would smooth away the step changes that are the whole
    # shape of this series. The current month reports today.
    by_month = {}
    for point in series:
        by_month[point["date"][:7]] = point
    monthly = [{"month": month, **point} for month, point in sorted(by_month.items())]

    last = rows[-1] if rows else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest": {
            "date": str(last["day"]),
            "total_rows": last["total_rows"],
            "replications": last["replications"] or 0,
            "reproductions": last["reproductions"] or 0,
            "source_rows": last["source_rows"],
            "measured_on": str(carried["day"]) if carried else None,
        } if last else None,
        # Capped: the charts need 30 days and the table is a reader's aid, not an
        # export. The whole history would grow without bound in a public payload.
        "daily": series[-window_days:],
        "monthly": monthly,
    }


def stats(cur) -> dict:
    """Headline numbers for the panel above the grid."""
    frame = dataset(cur)
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE retired_at IS NULL) AS live,
               COUNT(*) FILTER (WHERE retired_at IS NOT NULL) AS retired,
               MAX(last_seen_at) AS last_refresh
        FROM flora_records
        """
    )
    registry = dict(cur.fetchone())
    return {
        "rows": int(len(frame)),
        "replications": int((frame["type"] == "replication").sum()),
        "reproductions": int((frame["type"] == "reproduction").sum()),
        "unregistered": int(frame["flora_id"].isna().sum()),
        "registry_live": registry["live"],
        "registry_retired": registry["retired"],
        "last_refresh": registry["last_refresh"],
    }


# ---------------------------------------------------------------------------
# Preprint duplicates: the review queue in the FLoRA tab.
#
# The build detects pairs that may be one paper under two DOIs, typically a
# preprint and its publication. The pairs it could not settle (held with both rows
# kept, or dropped by the default guess) wait here for an admin. A ruling goes into
# preprint_dedup_decisions, which the transform reads on every build: this tab
# reflects it at once, the published CSV at the next pipeline run.
# ---------------------------------------------------------------------------

class PairNotFound(LookupError):
    """The pair is not detected by the current build, so there is nothing to rule on."""


_REVIEW_FIELDS = ("side", "doi_o_group", "title_sim", "resolution", "applied_action",
                  "doi_remove", "doi_keep")
_PAPER_FIELDS = ("doi", "title", "first_author", "year", "is_preprint",
                 "source_display_id", "type", "outcome", "url")


def _lower(value) -> str:
    return "" if _cell(value) is None else str(value).strip().lower()


def _review_item(decision: dict) -> dict:
    """One candidate as a review card needs it: the verdict on the pair, and each
    side as a paper someone can recognise."""
    item = {field: _cell(decision.get(field)) for field in _REVIEW_FIELDS}
    item["pair_key"] = preprint_dedup.pair_key(decision.get("doi_1"), decision.get("doi_2"))
    item["papers"] = []
    for n in (1, 2):
        paper = {field: _cell(decision.get(f"{field}_{n}")) for field in _PAPER_FIELDS}
        paper["is_repository"] = preprint_dedup.is_repository_doi(paper["doi"])
        paper["position"] = n
        item["papers"].append(paper)
    return item


def preprint_review(cur) -> dict:
    """Pairs awaiting a ruling, and the rulings already made, newest first."""
    _, dedup_log = _current(cur)
    pending = [_review_item(d) for d in preprint_dedup.unresolved(dedup_log)]
    cur.execute(
        """
        SELECT pair_key, side, doi_1, doi_2, action, doi_o_group, title_1, title_2,
               note, decided_by, decided_at
        FROM preprint_dedup_decisions
        ORDER BY decided_at DESC
        """
    )
    decided = [{key: _cell(value) for key, value in dict(row).items()}
               for row in cur.fetchall()]
    return {"pending": pending, "decided": decided, "total_pending": len(pending)}


def decide_preprint_pair(cur, doi_1: str, doi_2: str, action: str,
                         admin_handle: str, note: str = "") -> dict:
    """Record an admin's ruling on a pair the current build detected.

    Only a detected pair can be ruled on: it is what the admin was shown. The
    ruling is stored in the candidate's own DOI order, so keep_1 / keep_2 name the
    same paper here as in the candidates log, whichever order the client sent.
    """
    if action not in preprint_dedup.VALID_ACTIONS:
        raise ValueError("action must be keep_1, keep_2 or keep_both")
    key = preprint_dedup.pair_key(doi_1, doi_2)
    _, dedup_log = _current(cur)
    candidate = next((d for d in dedup_log
                      if preprint_dedup.pair_key(d.get("doi_1"), d.get("doi_2")) == key), None)
    if candidate is None:
        raise PairNotFound(key)
    if not _lower(candidate.get("doi_1")) or not _lower(candidate.get("doi_2")):
        raise ValueError("This pair has a missing DOI and cannot be ruled on here")
    if action != "keep_both" and _lower(doi_1) != _lower(candidate["doi_1"]):
        action = "keep_2" if action == "keep_1" else "keep_1"

    cur.execute(
        """
        INSERT INTO preprint_dedup_decisions
            (pair_key, side, doi_1, doi_2, action, doi_o_group, title_1, title_2,
             note, decided_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (pair_key) DO UPDATE
           SET side = EXCLUDED.side, doi_1 = EXCLUDED.doi_1, doi_2 = EXCLUDED.doi_2,
               action = EXCLUDED.action, doi_o_group = EXCLUDED.doi_o_group,
               title_1 = EXCLUDED.title_1, title_2 = EXCLUDED.title_2,
               note = EXCLUDED.note, decided_by = EXCLUDED.decided_by,
               decided_at = NOW()
        """,
        (key, _cell(candidate.get("side")), candidate["doi_1"], candidate["doi_2"],
         action, _cell(candidate.get("doi_o_group")), _cell(candidate.get("title_1")),
         _cell(candidate.get("title_2")), (note or "").strip() or None, admin_handle),
    )
    return {"pair_key": key, "action": action, "decided_by": admin_handle,
            "doi_1": candidate["doi_1"], "doi_2": candidate["doi_2"]}


def undo_preprint_decision(cur, pair_key: str) -> dict:
    """Withdraw a ruling and return what it was, for the audit trail. The pair
    returns to the default rules on the next build, or to the confirmed file's
    ruling if the file has one for it."""
    cur.execute(
        "DELETE FROM preprint_dedup_decisions WHERE pair_key = %s "
        "RETURNING action, doi_1, doi_2, note, decided_by",
        (pair_key,))
    withdrawn = cur.fetchone()
    if withdrawn is None:
        raise PairNotFound(pair_key)
    return dict(withdrawn)
