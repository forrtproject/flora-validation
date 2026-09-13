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
import io
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import flora_registry
import transform_sources

# Columns the grid shows. The heavy ones (abstract_r, the quotes) are fetched only
# when a single record is opened, and are always present in the export.
LIST_COLUMNS = [
    "flora_id", "source_display_id",
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

_cache = {"signature": None, "frame": None}
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
               (SELECT COUNT(*) FROM transform_exclusions)          AS n_excluded
        """
    )
    row = cur.fetchone()
    return (row["n_source"], str(row["max_updated"]), row["n_ruled"],
            row["n_flora"], row["n_excluded"])


def dataset(cur) -> pd.DataFrame:
    """The prepared FLoRA dataset with flora_id attached. Read-only."""
    signature = _signature(cur)
    with _lock:
        if _cache["signature"] == signature and _cache["frame"] is not None:
            return _cache["frame"]

    frame = transform_sources.build(cur, verbose=False)
    frame = flora_registry.attach_ids(cur, frame)
    frame = _attach_merged_ids(cur, frame)

    with _lock:
        _cache["signature"] = signature
        _cache["frame"] = frame
    return frame


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
        cols = ["flora_id", "source_display_id", "doi_o", "doi_r", "ref_o", "ref_r",
                # A W-id pasted from OpenAlex finds its row, which is most of the
                # point of carrying the id at all.
                "oa_work_id_o", "oa_work_id_r",
                # So a source id that was collapsed into another row still finds it.
                "merged_display_ids"]
        cols = [c for c in cols if c in out.columns]
        mask = False
        for col in cols:
            mask = mask | out[col].astype(str).str.contains(search, case=False, na=False)
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
        "counts": counts(frame),
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
        "untitled": (int((frame["title_o"].isna() | frame["title_r"].isna()).sum())
                     if {"title_o", "title_r"} <= set(frame.columns) else 0),
        "sources": sorted(frame["source"].dropna().unique().tolist()),
        "outcomes": sorted(frame["outcome"].dropna().unique().tolist()),
    }


def get_record(cur, flora_id: str) -> "dict | None":
    """One full row, every column. Used by the detail panel."""
    frame = dataset(cur)
    match = frame[frame["flora_id"] == flora_id]
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
        WHERE f.flora_id = %s
        ORDER BY s.display_id
        """,
        (flora_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def export_csv(cur, filters: dict) -> str:
    """Every row matching the filter, all columns, as CSV text.

    Column order is the FLoRA output contract — the same order transform_sources
    writes — so this file and the nightly artifact are interchangeable rather than
    subtly different.
    """
    frame = _apply_filters(dataset(cur), filters)
    # The FLoRA output contract (output_cols in the R notebook), with abstract_r,
    # the reproduction axes and our provenance ids kept after it. Also drops
    # merged_display_ids, which is a search helper and not part of the contract.
    ordered = transform_sources.to_output_shape(frame)
    buffer = io.StringIO()
    # Explicit newline: pandas otherwise picks the platform line ending, so the same
    # export would differ between a Windows dev box and the Linux server. A served
    # file should not depend on which machine produced it.
    ordered.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue()


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
