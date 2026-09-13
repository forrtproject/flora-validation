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


# ── month-end ─────────────────────────────────────────────────────────────────

def test_a_month_reports_the_value_it_finished_at():
    """A monthly mean would smooth away the step changes that are the entire shape
    of a cumulative series."""
    rows = [_row(1, 10), _row(15, 50), _row(31, 90)]
    out = _history(rows)
    assert out["monthly"] == [{"month": "2026-08", "date": "2026-08-31",
                               "source_rows": 90, "total_rows": None, "measured": False}]


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
               "replications", "reproductions", "measured_on"}
    out = _history([_row(1, 10, total_rows=8, reps=6, repros=2)])
    for point in out["daily"] + out["monthly"] + [out["latest"]]:
        assert set(point) <= allowed, f"unexpected field(s): {set(point) - allowed}"


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
    cur = FakeCursor([{"n": 12}])
    flora_registry.record_history(cur, pd.DataFrame({"type": ["replication"]}), verbose=False)
    sql = cur.executed[-1][0]
    assert "ON CONFLICT (recorded_on) DO UPDATE" in sql


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
    assert "never true on that day" in html


def test_the_charts_share_one_y_axis():
    """Two series of the same measure on two scales is the single most misleading
    thing a line chart can do — it makes 3,346 and 2,926 rows look identical."""
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    scales = re.findall(r"scales:\s*\{", html)
    assert len(scales) == 1
    assert "y1" not in html


def test_both_series_are_labelled_in_a_legend():
    """Identity is never carried by colour alone."""
    html = (ROOT / "docs" / "dataset.html").read_text(encoding="utf-8")
    assert "legend:" in html and "display: false" not in html.split("legend:")[1][:200]


def test_the_footer_date_is_rendered_in_utc():
    """Every date on this page is a UTC calendar day printed verbatim; generated_at
    is an instant. Letting the browser localise just that one put the footer a day
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
