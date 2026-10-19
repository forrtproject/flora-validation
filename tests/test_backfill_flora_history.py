"""backfill_flora_history.py: the FLoRA dataset's size before this pipeline measured it.

The plan (which published flora.csv version stands for which day) is pure and
tested here; so is the CSV count, against a throwaway git repository. The writes
run against PostgreSQL in the opt-in database test at the end
(FLORA_TEST_DATABASE_URL).
"""
import datetime as dt
import subprocess

import pytest
from psycopg2.extras import RealDictCursor

import backfill_flora_history as bf
import flora_registry
import flora_service
from tests.test_preparation_database import local_database  # noqa: F401

UTC = dt.timezone.utc


def _at(day, hour=3):
    return dt.datetime(2026, 5, day, hour, tzinfo=UTC)


def test_the_launch_day_gets_the_version_in_force_at_its_end():
    found = [(_at(18), "a"), (_at(20), "b"), (_at(23), "c")]
    assert bf.plan(found, dt.date(2026, 5, 21), dt.date(2026, 5, 31)) == [
        (dt.date(2026, 5, 21), "b"), (dt.date(2026, 5, 23), "c")]


def test_a_day_with_several_versions_takes_its_last():
    found = [(_at(22, 3), "early"), (_at(22, 15), "late")]
    assert bf.plan(found, dt.date(2026, 5, 21), dt.date(2026, 5, 31))[-1] == (dt.date(2026, 5, 22), "late")


def test_nothing_after_the_last_day_and_no_invented_days():
    found = [(_at(21), "a"), (_at(25), "b"), (dt.datetime(2026, 6, 2, tzinfo=UTC), "c")]
    assert bf.plan(found, dt.date(2026, 5, 21), dt.date(2026, 5, 31)) == [
        (dt.date(2026, 5, 21), "a"), (dt.date(2026, 5, 25), "b")]


def test_no_launch_row_without_a_version_by_then():
    assert bf.plan([(_at(25), "b")], dt.date(2026, 5, 21), dt.date(2026, 5, 31)) == [
        (dt.date(2026, 5, 25), "b")]


@pytest.fixture
def fred_data(tmp_path):
    """A repository with two flora.csv versions, committed on different days."""
    repo = tmp_path / "fred-data"
    (repo / "output").mkdir(parents=True)

    def git(*args, when=None):
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}
        import os
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, **env})

    git("init", "-q")
    git("config", "user.email", "bot@example.org")
    git("config", "user.name", "bot")
    rows = [("replication", "A"), ("reproduction", "B"), ("Replication ", "C")]
    for n, when in ((2, "2026-05-20T04:00:00+00:00"), (3, "2026-05-23T04:00:00+00:00")):
        body = "﻿id,type,title_r\n" + "".join(f"{i},{t},\"{x}, with a comma\"\n"
                                                   for i, (t, x) in enumerate(rows[:n]))
        (repo / "output" / "flora.csv").write_text(body, encoding="utf-8")
        git("add", "output/flora.csv")
        git("commit", "-q", "-m", "Update flora.csv [automated]", when=when)
    return str(repo)


def test_commits_in_the_same_second_keep_their_order(tmp_path):
    """The sha must not decide which of two same-second versions was the day's last."""
    import os
    repo = tmp_path / "same-second"
    (repo / "output").mkdir(parents=True)
    when = "2026-05-22T04:00:00+00:00"
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    git("init", "-q")
    git("config", "user.email", "bot@example.org")
    git("config", "user.name", "bot")
    for n in (1, 2, 3):
        body = "id,type\n" + "".join(f"{i},replication\n" for i in range(n))
        (repo / "output" / "flora.csv").write_text(body, encoding="utf-8")
        git("add", "output/flora.csv")
        git("commit", "-q", "-m", f"{n} rows")
    found = bf.versions(str(repo))
    assert [bf.count(str(repo), sha)[0] for _, sha in found] == [1, 2, 3]
    (_, last), = bf.plan(found, dt.date(2026, 5, 21), dt.date(2026, 5, 31))
    assert bf.count(str(repo), last)[0] == 3


def test_versions_are_read_from_git_oldest_first(fred_data):
    found = bf.versions(fred_data)
    assert [when.date() for when, _ in found] == [dt.date(2026, 5, 20), dt.date(2026, 5, 23)]


def test_a_version_is_counted_by_record_and_type(fred_data):
    (_, first), (_, second) = bf.versions(fred_data)
    assert bf.count(fred_data, first) == (2, 1, 1)
    assert bf.count(fred_data, second) == (3, 2, 1)      # type is read case- and space-blind


class _Cur:
    def __init__(self, column_present=True):
        self.column_present, self.executed = column_present, []

    def execute(self, sql, params=None):
        self.executed.append(" ".join(sql.split()))

    def fetchone(self):
        last = self.executed[-1]
        if "pg_attribute" in last:
            return {"1": 1} if self.column_present else None
        return {"day": dt.date(2026, 9, 13)}


def test_only_pipeline_figures_count_as_the_pipeline_start():
    cur = _Cur()
    assert bf.first_pipeline_day(cur) == dt.date(2026, 9, 13)
    assert "COALESCE(measured_by, 'pipeline') = 'pipeline'" in cur.executed[-1]


def test_before_the_column_is_deployed_every_figure_is_the_pipelines():
    cur = _Cur(column_present=False)
    bf.first_pipeline_day(cur)
    assert "measured_by" not in cur.executed[-1]


# ── against PostgreSQL ────────────────────────────────────────────────────────

def test_the_backfill_fills_the_gap_and_never_overwrites_the_pipeline(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("INSERT INTO validators (handle, created_at) VALUES ('first', '2026-05-21 08:00+00')")
        flora_registry.record_history(cur, _pipeline_frame(), verbose=False)   # today, pipeline
        cur.execute("SELECT recorded_on FROM flora_dataset_history")
        today = cur.fetchone()["recorded_on"]
        rows = [(dt.date(2026, 5, 21), 2167, 2158, 9), (dt.date(2026, 5, 23), 2172, 2163, 9),
                (today, 1, 1, 0),                       # a pipeline day: must stay as it is
                (today + dt.timedelta(days=1), 2, 2, 0)]  # after it, though the pipeline has no row
        assert bf.write(cur, rows) == 2
        assert bf.write(cur, rows[:2]) == 2, "a re-run replaces its own rows"
        cur.execute("SELECT recorded_on, total_rows, measured_by FROM flora_dataset_history "
                    "ORDER BY recorded_on")
        stored = [tuple(r.values()) for r in cur.fetchall()]
    assert stored == [(dt.date(2026, 5, 21), 2167, "flora.csv"),
                      (dt.date(2026, 5, 23), 2172, "flora.csv"),
                      (today, 3, "pipeline")]

    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        out = flora_service.history(cur, window_days=100_000)    # all of it, in any year
    first, carried = out["daily"][0], out["daily"][1]
    assert (first["date"], first["total_rows"], first["measured_by"]) == ("2026-05-21", 2167, "flora.csv")
    assert (carried["total_rows"], carried["measured"], carried["measured_by"]) == (2167, False, "flora.csv")
    assert first["source_rows"] is None, "no source record had arrived yet"
    assert out["latest"]["launched_on"] == "2026-05-21"
    assert out["latest"]["launch_total_rows"] == 2167
    assert out["latest"]["first_counted_on"] == "2026-05-21"
    assert out["latest"]["pipeline_from"] == str(today)
    assert out["latest"]["total_rows"] == 3


def test_the_pipeline_records_before_the_site_adds_the_column(local_database):
    """The nightly job can run this version against a schema without measured_by."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("ALTER TABLE flora_dataset_history DROP COLUMN measured_by")
        flora_registry.record_history(cur, _pipeline_frame(), verbose=False)
        flora_registry.record_history(cur, _pipeline_frame(), verbose=False)   # the upsert too
        cur.execute("SELECT total_rows FROM flora_dataset_history")
        assert [r["total_rows"] for r in cur.fetchall()] == [3]
        assert bf.first_pipeline_day(cur) is not None


def test_days_are_utc_whatever_the_session_time_zone(local_database):
    """20:00 UTC on 21 May is already 22 May at UTC+14; the page says UTC days."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET TIME ZONE 'Pacific/Kiritimati'")
        cur.execute("INSERT INTO validators (handle, created_at) VALUES ('first', '2026-05-21 20:00+00')")
        assert bf.launch_day(cur) == dt.date(2026, 5, 21)
        assert flora_service.history(cur)["latest"]["launched_on"] == "2026-05-21"
        flora_registry.record_history(cur, _pipeline_frame(), verbose=False)
        cur.execute("SELECT recorded_on, (NOW() AT TIME ZONE 'UTC')::date AS utc_today "
                    "FROM flora_dataset_history")
        row = cur.fetchone()
        assert row["recorded_on"] == row["utc_today"]


def _pipeline_frame():
    import pandas as pd
    return pd.DataFrame({"type": ["replication", "replication", "reproduction"]})
