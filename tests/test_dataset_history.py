"""Tests for the public dataset-growth page: /api/flora/history and docs/dataset.html.

Two properties carry the weight here.

HONESTY. The page makes a claim about the past, to readers who cannot check it. A
number that was never true on the day it is plotted against is the failure mode, so
these tests pin down exactly which values may be filled in (`source_rows`, an exact
cumulative count over an insert-only table; `total_rows` carried forward from a run)
and which may not (`total_rows` before the first recorded run).

PUBLICNESS. The route is reachable without a session, so it must expose counts and
nothing else. A test asserts the payload shape rather than trusting review.
"""
import datetime as dt
import re
from pathlib import Path

import pandas as pd
import pytest

import flora_registry
import flora_service

ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    def fetchall(self):
        out, self.results = self.results, []
        return out


def _day(n):
    return dt.date(2026, 8, 1) + dt.timedelta(days=n - 1)


def _row(n, source_rows, total_rows=None, reps=0, repros=0):
    """One row as the calendar query returns it.

    `was_run` is not a free parameter, because the query does not treat it as one:
    it is `total_rows IS NOT NULL`. flora_dataset_history also holds SEEDED days —
    backfill_source_history writes recorded_on and source_rows for every past day
    source records arrived on, and no total — and those are days the pipeline did
    not run. A row existing there is therefore not evidence that anything was
    measured on it.
    """
    return {
        "day": _day(n),
        "source_rows": source_rows,
        "total_rows": total_rows,
        "replications": reps if total_rows is not None else None,
        "reproductions": repros if total_rows is not None else None,
        "was_run": total_rows is not None,
    }


def _history(rows, **kwargs):
    return flora_service.history(FakeCursor(rows), **kwargs)


# ── the calendar: one point per day, not one per run ──────────────────────────

def test_every_calendar_day_is_a_point():
    """The whole reason for the query's generate_series. Plotting only the days a
    run happened puts Aug 1 and Aug 14 side by side at equal spacing, which makes a
    quiet fortnight look like one day and misstates every slope on the chart."""
    out = _history([_row(1, 10), _row(2, 10), _row(3, 12)])
    assert [d["date"] for d in out["daily"]] == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_a_quiet_day_keeps_the_running_source_count():
    """Not a fill: source_records is insert-only and first_seen_at never moves, so
    the cumulative count on a day nothing arrived is exactly the previous count."""
    out = _history([_row(1, 10), _row(2, 10), _row(3, 10)])
    assert [d["source_rows"] for d in out["daily"]] == [10, 10, 10]


# ── carry-forward: forwards only, never backwards ─────────────────────────────

def test_the_dataset_size_is_carried_to_days_with_no_run():
    """The published dataset only changes when the pipeline regenerates it, so on a
    day with no run it genuinely still held the last run's count."""
    out = _history([_row(1, 10, total_rows=8), _row(2, 10), _row(3, 10)])
    assert [d["total_rows"] for d in out["daily"]] == [8, 8, 8]


def test_the_dataset_size_is_never_extended_backwards():
    """The one number that must stay empty. Today's exclusion and dedup rules did not
    exist last month; applying them to an older set of rows produces a figure that was
    never true on that day, and a reader of a public chart cannot tell the difference."""
    out = _history([_row(1, 10), _row(2, 10), _row(3, 12, total_rows=9)])
    assert [d["total_rows"] for d in out["daily"]] == [None, None, 9]


def test_carried_days_are_marked():
    """So the chart's tooltip and the table can say which figures are readings and
    which are the previous reading still standing."""
    out = _history([_row(1, 10, total_rows=8), _row(2, 10)])
    assert [d["measured"] for d in out["daily"]] == [True, False]


def test_a_seeded_day_after_a_run_is_marked_carried_not_measured():
    """The failure this page exists to avoid.

    backfill_source_history seeds a row for every past day source records arrived
    on, with source_rows and no total. If a day like that falls AFTER a recorded run
    — records land but the registry step does not finish, and a later run seeds the
    gap — the carry-forward below fills its total in. Keying `measured` on the
    history row existing then published that carried figure as a reading: no
    "carried" chip in the table, no "(carried forward)" in the tooltip, and a reader
    of a public chart cannot tell the difference.
    """
    out = _history([
        _row(1, 10, total_rows=8),   # a real run
        _row(2, 25),                 # seeded: rows arrived, nothing was measured
    ])
    assert [d["total_rows"] for d in out["daily"]] == [8, 8]
    assert [d["measured"] for d in out["daily"]] == [True, False]


def test_measured_is_read_from_the_recorded_total_not_from_the_row():
    """Pins the SQL, because the Python loop above only echoes what it selects."""
    cur = FakeCursor([_row(1, 10, total_rows=8)])
    flora_service.history(cur)
    sql = cur.executed[0][0]
    assert "h.total_rows IS NOT NULL AS was_run" in sql
    assert "h.recorded_on IS NOT NULL AS was_run" not in sql


def test_a_later_run_replaces_the_carried_value():
    out = _history([
        _row(1, 10, total_rows=8),
        _row(2, 10),
        _row(3, 20, total_rows=17),
        _row(4, 20),
    ])
    assert [d["total_rows"] for d in out["daily"]] == [8, 8, 17, 17]


def test_the_breakdown_is_carried_with_the_total():
    """Otherwise a carried day reports 2,900 rows of which 0 are replications."""
    rows = [_row(1, 10, total_rows=8, reps=6, repros=2), _row(2, 10)]
    assert _history(rows)["latest"]["replications"] == 6


def test_every_day_carries_its_own_breakdown():
    """The page charts replications and reproductions separately, so each day needs
    its split — carried with the total on a day without a run."""
    rows = [_row(1, 10), _row(2, 10, total_rows=8, reps=6, repros=2), _row(3, 12)]
    daily = _history(rows)["daily"]
    assert [(d["replications"], d["reproductions"]) for d in daily] == [
        (None, None), (6, 2), (6, 2)]


def test_a_seeded_day_reports_no_breakdown():
    """The columns default to 0, so a seeded day comes back as 0 rather than NULL.
    Publishing that would say the collection held no replications on a day nothing
    was measured."""
    seeded = {**_row(1, 10), "replications": 0, "reproductions": 0}
    point = _history([seeded])["daily"][0]
    assert point["replications"] is None and point["reproductions"] is None


# ── month-end ─────────────────────────────────────────────────────────────────

def test_a_month_reports_the_value_it_finished_at():
    """A monthly mean would smooth away the step changes that are the entire shape
    of a cumulative series."""
    rows = [_row(1, 10), _row(15, 50), _row(31, 90)]
    out = _history(rows)
    assert out["monthly"] == [{"month": "2026-08", "date": "2026-08-31",
                               "source_rows": 90, "total_rows": None,
                               "replications": None, "reproductions": None,
                               "measured": False, "measured_by": None}]


def test_each_month_gets_exactly_one_point():
    rows = [_row(1, 10), _row(31, 90), _row(32, 95), _row(40, 120)]
    assert [m["month"] for m in _history(rows)["monthly"]] == ["2026-08", "2026-09"]


# ── the payload ───────────────────────────────────────────────────────────────

def test_latest_is_today_not_the_last_run():
    """The tiles say what the collection holds now. Reporting the last run's date
    would show a stale total as though it were current."""
    out = _history([_row(1, 10, total_rows=8), _row(2, 25)])
    assert out["latest"]["date"] == "2026-08-02"
    assert out["latest"]["source_rows"] == 25


def test_latest_says_when_the_dataset_figure_was_actually_measured():
    out = _history([_row(1, 10, total_rows=8), _row(2, 25)])
    assert out["latest"]["measured_on"] == "2026-08-01"


def test_the_daily_window_is_capped():
    """A public payload that grows by a row a day forever is a slow leak; the charts
    need 30 days and the table is a reader's aid, not an export."""
    rows = [_row(n, n) for n in range(1, 200)]
    assert len(_history(rows, window_days=120)["daily"]) == 120


def test_the_monthly_series_is_not_capped_by_the_window():
    """'All-time' has to mean all time even once the daily window has rolled past."""
    rows = [_row(n, n) for n in range(1, 200)]
    assert len(_history(rows, window_days=30)["monthly"]) >= 6


def test_an_empty_history_does_not_crash():
    out = _history([])
    assert out["latest"] is None and out["daily"] == []


def test_the_payload_carries_counts_and_nothing_else():
    """This is served without a session. A field added here later that names a
    record, a reference or a reviewer would publish it to everyone."""
    allowed = {"date", "month", "total_rows", "source_rows", "measured",
               "replications", "reproductions", "measured_on", "measured_by", "launched_on",
               "launch_total_rows", "first_counted_on", "pipeline_from",
               "no_counts_from", "no_counts_until"}
    out = _history([_row(1, 10, total_rows=8, reps=6, repros=2)])
    for point in out["daily"] + out["monthly"] + [out["latest"]]:
        assert set(point) <= allowed, f"unexpected field(s): {set(point) - allowed}"


def test_each_figure_says_where_it_came_from_and_carries_that_forward():
    """flora.csv counts before the pipeline, pipeline counts after: the page names
    the handover, so a carried day must keep its figure's origin."""
    rows = [{**_row(1, None, total_rows=900), "measured_by": "flora.csv"},
            {**_row(2, None), "measured_by": None},
            {**_row(3, 10, total_rows=950), "measured_by": "pipeline"}]
    daily = _history(rows)["daily"]
    assert [d["measured_by"] for d in daily] == ["flora.csv", "flora.csv", "pipeline"]
    assert [d["measured"] for d in daily] == [True, False, True]


def test_no_origin_is_claimed_for_a_day_without_a_figure():
    assert _history([_row(1, 10)])["daily"][0]["measured_by"] is None


def test_latest_names_the_validators_launch_day():
    rows = [{**_row(1, 10, total_rows=8), "launched_on": dt.date(2026, 5, 21)}]
    assert _history(rows)["latest"]["launched_on"] == "2026-05-21"


def _csv_then_pipeline():
    """flora.csv counts on days 1 and 3, a carried day between, the pipeline from day 4."""
    launch = {"launched_on": _day(2)}
    return [{**_row(1, None, total_rows=900), "measured_by": "flora.csv", **launch},
            {**_row(2, None), "measured_by": None, **launch},
            {**_row(3, 10, total_rows=930), "measured_by": "flora.csv", **launch},
            {**_row(4, 12, total_rows=950), "measured_by": "pipeline", **launch},
            {**_row(5, 14, total_rows=960), "measured_by": "pipeline", **launch}]


def test_latest_gives_the_count_in_force_at_the_launch():
    """The launch day had no count of its own: the one carried to it stood then."""
    assert _history(_csv_then_pipeline())["latest"]["launch_total_rows"] == 900


def test_latest_names_the_first_count_and_the_handover():
    latest = _history(_csv_then_pipeline())["latest"]
    assert latest["first_counted_on"] == str(_day(1))
    assert latest["pipeline_from"] == str(_day(4))


def test_the_launch_and_the_handover_survive_the_daily_window():
    """Both leave `daily` in time; the page still needs them."""
    out = _history(_csv_then_pipeline(), window_days=1)
    assert [d["date"] for d in out["daily"]] == [str(_day(5))]
    assert (out["latest"]["launch_total_rows"], out["latest"]["pipeline_from"],
            out["latest"]["first_counted_on"]) == (900, str(_day(4)), str(_day(1)))


def _fred_data_then_launch():
    """The old fred-data pipeline on days 1 and 3 (no type on day 1), no counts on
    days 4-5, flora.csv from the launch on day 6, the pipeline from day 8."""
    launch = {"launched_on": _day(6)}
    old = {"measured_by": "fred-data", **launch}
    return [{**_row(1, None, total_rows=573), "replications": None, "reproductions": None, **old},
            {**_row(2, None), "measured_by": None, **launch},
            {**_row(3, None, total_rows=758, reps=753, repros=5), **old},
            {**_row(4, None), "measured_by": None, **launch},
            {**_row(5, None), "measured_by": None, **launch},
            {**_row(6, None, total_rows=2167, reps=2158, repros=9), "measured_by": "flora.csv", **launch},
            {**_row(7, None), "measured_by": None, **launch},
            {**_row(8, 10, total_rows=2200, reps=2190, repros=10), "measured_by": "pipeline", **launch}]


def test_the_old_pipelines_last_count_is_not_carried_to_the_launch():
    """Its flora.csv left the repository root that day and nothing was counted until
    the next count (here the launch's): a carried figure there would be one that
    never held."""
    daily = _history(_fred_data_then_launch())["daily"]
    assert [d["total_rows"] for d in daily] == [573, 573, 758, None, None, 2167, 2167, 2200]
    assert [d["measured_by"] for d in daily] == [
        "fred-data", "fred-data", "fred-data", None, None, "flora.csv", "flora.csv", "pipeline"]
    assert [d["measured"] for d in daily] == [True, False, True, False, False, True, False, True]


def test_a_count_with_no_type_has_no_split():
    """The old pipeline's first versions record no type: unknown, not 0."""
    daily = _history(_fred_data_then_launch())["daily"]
    assert [(d["replications"], d["reproductions"]) for d in daily[:4]] == [
        (None, None), (None, None), (753, 5), (None, None)]


def test_latest_names_the_stretch_with_no_counts_and_keeps_the_launch_figures():
    latest = _history(_fred_data_then_launch(), window_days=1)["latest"]
    assert (latest["no_counts_from"], latest["no_counts_until"]) == (str(_day(4)), str(_day(5)))
    assert latest["launched_on"] == str(_day(6))
    assert latest["launch_total_rows"] == 2167, "growth is still measured from the launch"
    assert latest["first_counted_on"] == str(_day(1))
    assert latest["pipeline_from"] == str(_day(8))


def _gap_filled():
    """Every day from 11 Nov 2025 to 14 Sep 2026 as production holds them once the
    gap is filled: the old pipeline's root flora.csv to 13 Jan 2026, nothing 14-19
    Jan, output/flora.csv from 20 Jan (the pull request's versions, then the README
    log from 16 Mar, which lacks 25-28 Apr), the launch on 21 May, the pipeline from
    13 Sep."""
    counts = {"2025-11-11": (573, None, None, "fred-data"),
              "2026-01-13": (758, 753, 5, "fred-data"),
              "2026-01-20": (740, 740, 0, "flora.csv"),
              "2026-03-10": (1128, 1120, 8, "flora.csv"),
              "2026-03-16": (1126, 1118, 8, "flora.csv"),
              "2026-04-24": (2097, 2088, 9, "flora.csv"),
              "2026-04-29": (2167, 2158, 9, "flora.csv"),
              "2026-05-20": (2168, 2159, 9, "flora.csv"),
              "2026-05-21": (2167, 2158, 9, "flora.csv"),
              "2026-09-12": (2600, 2590, 10, "flora.csv"),
              "2026-09-13": (2610, 2600, 10, "pipeline")}
    rows, day = [], dt.date(2025, 11, 11)
    while day <= dt.date(2026, 9, 14):
        total, reps, repros, by = counts.get(str(day), (None, None, None, None))
        rows.append({"day": day, "source_rows": None, "total_rows": total, "replications": reps,
                     "reproductions": repros, "measured_by": by, "launched_on": dt.date(2026, 5, 21),
                     "was_run": total is not None})
        day += dt.timedelta(days=1)
    return rows


def test_the_stretch_with_no_counts_is_the_six_days_without_a_file():
    """The root flora.csv left on 13 Jan 2026 and output/flora.csv first appeared on
    20 Jan: 14-19 Jan had nothing published."""
    latest = _history(_gap_filled())["latest"]
    assert (latest["no_counts_from"], latest["no_counts_until"]) == ("2026-01-14", "2026-01-19")


def test_nothing_is_carried_across_the_six_days():
    daily = {d["date"]: d for d in _history(_gap_filled())["daily"]}
    gap = [daily[f"2026-01-{n}"] for n in range(14, 20)]
    assert [(d["total_rows"], d["replications"], d["measured_by"]) for d in gap] == [(None, None, None)] * 6
    assert (daily["2026-01-13"]["total_rows"], daily["2026-01-13"]["measured_by"]) == (758, "fred-data")
    assert (daily["2026-01-20"]["total_rows"], daily["2026-01-20"]["measured"],
            daily["2026-01-20"]["measured_by"]) == (740, True, "flora.csv")
    assert (daily["2026-02-01"]["total_rows"], daily["2026-02-01"]["measured"]) == (740, False), \
        "a flora.csv count carries like any other"


def test_a_day_the_readme_log_lacks_carries_the_day_before():
    daily = {d["date"]: d for d in _history(_gap_filled())["daily"]}
    assert [(daily[f"2026-04-{n}"]["total_rows"], daily[f"2026-04-{n}"]["replications"],
             daily[f"2026-04-{n}"]["reproductions"], daily[f"2026-04-{n}"]["measured"],
             daily[f"2026-04-{n}"]["measured_by"]) for n in range(25, 29)] == [
        (2097, 2088, 9, False, "flora.csv")] * 4
    assert daily["2026-04-29"]["measured"] is True


def test_filling_the_gap_leaves_the_launch_figures_and_the_handover():
    latest = _history(_gap_filled(), window_days=1)["latest"]
    assert (latest["launched_on"], latest["launch_total_rows"]) == ("2026-05-21", 2167)
    assert latest["pipeline_from"] == "2026-09-13", "the first pipeline count, not the first after any flora.csv"


def test_the_band_runs_from_the_first_count_to_the_launch():
    """The page shades the fred-data pipeline's era from `first_counted_on` to
    `launched_on`: every count before the launch is that pipeline's, its root
    flora.csv's or output/flora.csv's. Both survive the daily window."""
    out = _history(_gap_filled(), window_days=1)
    assert (out["latest"]["first_counted_on"], out["latest"]["launched_on"]) == ("2025-11-11", "2026-05-21")
    daily = _history(_gap_filled())["daily"]
    assert {d["measured_by"] for d in daily if d["date"] < "2026-05-21" and d["total_rows"] is not None} == {
        "fred-data", "flora.csv"}


def test_no_stretch_without_old_pipeline_counts_or_a_day_between():
    assert _history(_csv_then_pipeline())["latest"]["no_counts_from"] is None
    back_to_back = [r for r in _fred_data_then_launch() if r["day"] not in (_day(4), _day(5))]
    back_to_back[3:] = [{**r, "day": r["day"] - dt.timedelta(days=2)} for r in back_to_back[3:]]
    latest = _history(back_to_back)["latest"]
    assert (latest["no_counts_from"], latest["no_counts_until"]) == (None, None)


def test_no_handover_without_flora_csv_counts():
    rows = [{**_row(1, 10, total_rows=8), "measured_by": "pipeline"},
            {**_row(2, 10, total_rows=9), "measured_by": "pipeline"}]
    latest = _history(rows)["latest"]
    assert latest["pipeline_from"] is None
    assert latest["launch_total_rows"] is None, "no launch day known"


def test_no_launch_figure_before_the_first_count():
    rows = [{**_row(1, 10), "launched_on": _day(1)},
            {**_row(2, 10, total_rows=9), "launched_on": _day(1)}]
    latest = _history(rows)["latest"]
    assert latest["launch_total_rows"] is None
    assert latest["first_counted_on"] == str(_day(2))


def test_the_query_leaves_source_records_blank_before_the_first_arrived():
    """Nothing was being ingested yet: zero would be a reading, and it is not one."""
    cur = FakeCursor([])
    flora_service.history(cur)
    sql = cur.executed[0][0]
    assert "CASE WHEN f.day IS NULL OR c.day < f.day THEN NULL" in sql
    assert "COALESCE(h.measured_by, 'pipeline')" in sql


def test_the_daily_window_reaches_back_past_the_launch():
    assert flora_service.history.__defaults__ == (400,)


def test_history_never_builds_the_frame(monkeypatch):
    """A public URL that triggers a two-second dataset build is a free way to load
    the server. It reads recorded counts only."""
    monkeypatch.setattr(flora_service, "dataset",
                        lambda *a, **k: pytest.fail("history() built the frame"))
    _history([_row(1, 10, total_rows=8)])


def test_history_runs_one_query():
    cur = FakeCursor([_row(1, 10, total_rows=8)])
    flora_service.history(cur)
    assert len(cur.executed) == 1


# ── recording, at the end of every refresh ────────────────────────────────────

def test_todays_row_is_upserted_rather_than_duplicated():
    """The pipeline can run several times a day; each run must correct the day's row,
    not add a second point at the same x."""
    cur = FakeCursor([{"n": 12}, {"present": 1}])
    flora_registry.record_history(cur, pd.DataFrame({"type": ["replication"]}), verbose=False)
    sql = cur.executed[-1][0]
    assert "ON CONFLICT (recorded_on) DO UPDATE" in sql
    assert "measured_by = 'pipeline'" in sql, "a pipeline run marks its own figure"


def test_recording_works_before_the_site_has_added_measured_by():
    """The nightly job runs from the repository and can reach the database before
    the deployed site's init_db() adds the column; failing here would roll back the
    whole registry refresh and skip that night's FLoRA build."""
    cur = FakeCursor([{"n": 12}, None])
    flora_registry.record_history(cur, pd.DataFrame({"type": ["replication"]}), verbose=False)
    assert "pg_attribute" in cur.executed[-2][0]
    sql = cur.executed[-1][0]
    assert "INSERT INTO flora_dataset_history" in sql and "measured_by" not in sql


def test_the_recorded_split_matches_the_frame():
    cur = FakeCursor([{"n": 12}])
    frame = pd.DataFrame({"type": ["replication", "replication", "reproduction"]})
    stats = flora_registry.record_history(cur, frame, verbose=False)
    assert (stats["total_rows"], stats["replications"], stats["reproductions"]) == (3, 2, 1)


def test_an_empty_frame_records_zeroes_rather_than_failing():
    cur = FakeCursor([{"n": 0}])
    stats = flora_registry.record_history(cur, pd.DataFrame(), verbose=False)
    assert stats["total_rows"] == 0 and stats["replications"] == 0


def test_the_seeded_past_never_overwrites_a_recorded_day():
    """A day the pipeline actually measured outranks a day reconstructed from
    first_seen_at."""
    cur = FakeCursor()
    flora_registry.backfill_source_history(cur, verbose=False)
    assert "ON CONFLICT (recorded_on) DO NOTHING" in cur.executed[-1][0]


def test_the_seeded_past_sets_no_dataset_size():
    """It cannot be known for those days, and a plotted guess is indistinguishable
    from a measurement."""
    cur = FakeCursor()
    flora_registry.backfill_source_history(cur, verbose=False)
    sql = cur.executed[-1][0]
    assert "INSERT INTO flora_dataset_history (recorded_on, source_rows)" in sql
    assert "total_rows" not in sql


def test_refresh_records_history():
    """The user's ask was 'updated after every run'. If refresh() stops calling these,
    the public chart quietly freezes at whatever day it last saw."""
    source = Path(flora_registry.__file__).read_text(encoding="utf-8")
    body = source[source.index("def refresh("):]
    assert "record_history(" in body
    assert "backfill_source_history(" in body


# ── the page itself ───────────────────────────────────────────────────────────

def test_the_page_exists_where_static_files_are_served_from():
    """docs/ is mounted at / with html=True, which is what makes /dataset.html
    reachable with no session — the whole point of the request."""
    assert (ROOT / "docs" / "dataset.html").exists()


def test_the_route_takes_no_session():
    """Adding a session dependency here would lock out exactly the readers it was
    built for."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = source[source.index('@app.get("/api/flora/history")'):]
    signature = block[:block.index(")", block.index("def public_flora_history"))]
    assert "Depends" not in signature and "request" not in signature


def test_the_sign_in_screen_links_to_it():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    login = html[html.index('id="login-screen"'):html.index('class="login-right"')]
    assert 'href="./dataset.html"' in login


def test_the_page_asks_for_the_public_route():
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    assert '"/api/flora/history"' in html


def test_the_page_states_which_figures_are_not_measured():
    """The chart's honesty depends on the reader being told; a legend alone cannot
    say that a line stops because the number is unknowable rather than zero."""
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    assert "carried forward" in html
    earlier = html[html.index('id="def-earlier"'):]
    earlier = earlier[:earlier.index("</div>")]
    assert "never true on that day" in html.replace(earlier, ""), \
        "said on every page, not only once flora.csv counts exist"


def test_the_charts_share_one_y_axis():
    """Two series of the same measure on two scales is the single most misleading
    thing a line chart can do — it makes 3,346 and 2,926 rows look identical."""
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    scales = re.findall(r"scales:\s*\{", html)
    assert len(scales) == 1
    assert "y1" not in html


def test_both_series_are_labelled_in_a_legend():
    """Identity is never carried by colour alone. The legend is HTML above the plot
    rather than drawn on the canvas, so it must name both series itself."""
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    legend = html[html.index('<ul class="legend"'):]
    legend = legend[:legend.index("</ul>")]
    assert "FLoRA dataset" in legend and "Source records" in legend


def test_the_updated_date_is_rendered_in_utc():
    """Every date on this page is a UTC calendar day printed verbatim; generated_at
    is an instant. Letting the browser localise just that one put the stamp a day
    AHEAD of the newest row in the table for every reader east of UTC — one payload
    showing two different "today"s."""
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    call = html[html.index("toLocaleDateString"):]
    call = call[:call.index(");")]
    assert 'timeZone: "UTC"' in call


def test_the_page_does_not_reach_for_the_app_stylesheet():
    """It renders for signed-out readers with no app shell around it; a dependency on
    style.css would make it inherit rules written for the authenticated layout."""
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    assert not re.search(r"<link[^>]+style\.css", html)
