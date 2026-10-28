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


def _git(repo, *args, when=None, check=True):
    import os
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}
    subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True,
                   env={**os.environ, **env})


def _old_csv(repo, path, rows, typed=False):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    header = "doi_o,doi_r,rep_type,title_r\n" if typed else "doi_o,doi_r,title_r\n"
    target.write_text(header + "".join(
        f"10.1/{i},10.2/{i},{t + ',' if typed else ''}\"Study {i}, a title\"\n"
        for i, t in enumerate(rows)), encoding="utf-8")


@pytest.fixture
def old_fred_data(tmp_path):
    """The old pipeline's root flora.csv, shaped as fred-data has it: two versions
    with no type column, a release copy of the second, then on 13 Jan 2026 a third
    version (with rep_type) on one side and, on the other, the restructure that
    moved the file to archive/. The merge takes the restructure as its first parent
    and puts the third version in archive/flora.csv."""
    repo = tmp_path / "old-fred-data"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "bot@example.org")
    _git(repo, "config", "user.name", "bot")
    _old_csv(repo, "flora.csv", ["replication"] * 3)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Rename fred_mini to FLoRA", when="2025-11-11T15:12:31Z")
    _old_csv(repo, "flora.csv", ["replication"] * 5)
    _git(repo, "commit", "-q", "-am", "Update Flora.csv", when="2025-11-25T12:44:56Z")
    _old_csv(repo, "release/latest_data/flora.csv", ["replication"] * 5)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Add release script", when="2025-12-17T12:57:39Z")
    _git(repo, "branch", "side")
    _git(repo, "checkout", "-q", "side")
    _old_csv(repo, "flora.csv", ["replication"] * 5 + ["Reproduction "], typed=True)
    _git(repo, "commit", "-q", "-am", "Closes #72", when="2026-01-13T11:42:00Z")
    _git(repo, "checkout", "-q", "main")
    (repo / "archive").mkdir()
    _git(repo, "mv", "flora.csv", "archive/flora.csv")
    _git(repo, "commit", "-q", "-m", "Restructured repository", when="2026-01-13T17:43:01Z")
    # Rename detection carries the third version into archive/flora.csv, as in fred-data.
    _git(repo, "merge", "-q", "--no-edit", "side", when="2026-01-13T17:44:05Z")
    return str(repo)


def test_the_old_pipelines_versions_include_the_one_merged_in_after_the_move(old_fred_data):
    """git's default history follows the merge's first parent, which deleted the root
    file, and would lose its last version; the deletion and the merge are no versions."""
    found = bf.versions(old_fred_data, bf.OLD_FLORA_CSV)
    assert [when.date() for when, _ in found] == [
        dt.date(2025, 11, 11), dt.date(2025, 11, 25), dt.date(2026, 1, 13)]
    assert [bf.count(old_fred_data, sha, bf.OLD_FLORA_CSV) for _, sha in found] == [
        (3, None, None), (5, None, None), (6, 5, 1)]
    archived = subprocess.run(["git", "-C", old_fred_data, "rev-parse", "HEAD:archive/flora.csv",
                               f"{found[-1][1]}:flora.csv"], capture_output=True, text=True,
                              check=True).stdout.split()
    assert archived[0] == archived[1], "archive/ holds the same version, not another one"


def test_without_a_type_column_the_split_is_unknown_not_zero(fred_data, old_fred_data):
    (_, sha), *_ = bf.versions(old_fred_data, bf.OLD_FLORA_CSV)
    assert bf.count(old_fred_data, sha, bf.OLD_FLORA_CSV)[1:] == (None, None)
    assert bf.count(fred_data, bf.versions(fred_data)[0][1])[1:] == (1, 1), "type still read"


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
        # Against a rewritten fred-data the same days come back with other counts:
        # the counts already written stand.
        assert bf.write(cur, [(day, total - 290, reps - 290, repros)
                              for day, total, reps, repros in rows]) == 0
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


def _snapshot(cur):
    cur.execute("SELECT * FROM flora_dataset_history ORDER BY recorded_on")
    return {r["recorded_on"]: dict(r) for r in cur.fetchall()}


def _launch_era(cur):
    """The validator launched on 21 May 2026; flora.csv counts from then, a day seeded
    with source records alone, the pipeline today."""
    cur.execute("INSERT INTO validators (handle, created_at) VALUES ('first', '2026-05-21 08:00+00')")
    cur.execute("""INSERT INTO flora_dataset_history
                       (recorded_on, total_rows, replications, reproductions, source_rows, measured_by)
                   VALUES ('2026-05-21', 2167, 2158, 9, 0, 'flora.csv'),
                          ('2026-09-12', 2600, 2590, 10, 40, 'flora.csv')""")
    cur.execute("INSERT INTO flora_dataset_history (recorded_on, source_rows) VALUES ('2026-08-14', 12)")
    flora_registry.record_history(cur, _pipeline_frame(), verbose=False)


def test_the_old_pipeline_only_fills_days_before_the_launch_without_a_row(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        _launch_era(cur)
        cur.execute("INSERT INTO flora_dataset_history (recorded_on, source_rows) VALUES ('2025-11-25', 0)")
        before = _snapshot(cur)
        rows = [(dt.date(2025, 11, 11), 573, None, None),
                (dt.date(2025, 11, 25), 655, None, None),   # has a row: left alone
                (dt.date(2026, 1, 13), 758, 753, 5),
                (dt.date(2026, 5, 21), 1, 1, 0),            # the launch day: not the old pipeline's
                (dt.date(2026, 8, 14), 2, 2, 0),            # a seeded day after the launch
                (dt.date(2026, 9, 12), 3, 3, 0)]            # a flora.csv count
        assert bf.write(cur, rows, bf.OLD) == 2
        assert bf.write(cur, rows, bf.OLD) == 0, "a re-run writes nothing"
        after = _snapshot(cur)
    assert {day: after[day] for day in before} == before, "no existing row changed"
    added = [(day, r["total_rows"], r["replications"], r["reproductions"], r["measured_by"])
             for day, r in after.items() if day not in before]
    assert added == [(dt.date(2025, 11, 11), 573, None, None, "fred-data"),
                     (dt.date(2026, 1, 13), 758, 753, 5, "fred-data")]


def test_the_old_pipeline_writes_nothing_without_a_launch(local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        assert bf.write(cur, [(dt.date(2025, 11, 11), 573, None, None)], bf.OLD) == 0


def test_a_before_launch_run_end_to_end(local_database, old_fred_data, monkeypatch, capsys):
    """Dry run, --apply, re-run, then the page's payload: the early days, the stretch
    with no counts, and the launch figures, which still count from the launch."""
    import os
    import sys
    from psycopg2.extensions import parse_dsn
    assert parse_dsn(os.environ["DATABASE_URL"])["host"] in {"127.0.0.1", "localhost", "::1"}
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        _launch_era(cur)
        before = _snapshot(cur)

    def run(*flags):
        monkeypatch.setattr(sys, "argv", ["backfill_flora_history.py", "--fred-data",
                                          old_fred_data, "--before-launch", *flags])
        assert bf.main() == 0
        return capsys.readouterr().out

    dry = run()
    assert "flora.csv sizes from 2025-11-11 to 2026-01-13 (the validator launched on 2026-05-21)" in dry
    assert "  2025-11-11      3 records  (no type column)" in dry
    assert "  2026-01-13      6 records  (5 replications, 1 reproductions)" in dry
    assert "[dry run] 3 day(s) would be written" in dry
    assert "3 day(s) written" in run("--apply")
    assert "[dry run] 0 day(s) would be written" in run()
    assert "0 day(s) written" in run("--apply")
    # The root flora.csv was gone after 13 Jan 2026: a later day gets no count.
    assert "Nothing to backfill (from 2026-03-01 to 2026-01-13)" in run("--since", "2026-03-01", "--apply")

    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        after = _snapshot(cur)
        assert {day: after[day] for day in before} == before, "no existing row changed"
        assert sorted(set(after) - set(before)) == [
            dt.date(2025, 11, 11), dt.date(2025, 11, 25), dt.date(2026, 1, 13)]
        out = flora_service.history(cur, window_days=100_000)
    daily = {d["date"]: d for d in out["daily"]}
    assert out["daily"][0]["date"] == "2025-11-11"
    assert (daily["2025-11-11"]["total_rows"], daily["2025-11-11"]["replications"],
            daily["2025-11-11"]["measured_by"]) == (3, None, "fred-data")
    assert (daily["2025-12-01"]["total_rows"], daily["2025-12-01"]["measured"]) == (5, False)
    assert (daily["2026-01-13"]["total_rows"], daily["2026-01-13"]["replications"]) == (6, 5)
    assert (daily["2026-01-14"]["total_rows"], daily["2026-05-20"]["total_rows"]) == (None, None), \
        "the old pipeline's last count is not carried to the launch"
    assert daily["2026-05-21"]["total_rows"] == 2167
    latest = out["latest"]
    assert (latest["launched_on"], latest["launch_total_rows"]) == ("2026-05-21", 2167)
    assert latest["first_counted_on"] == "2025-11-11"
    assert latest["pipeline_from"] == str(max(after))
    assert (latest["no_counts_from"], latest["no_counts_until"]) == ("2026-01-14", "2026-05-20")


def test_an_older_schema_is_widened_for_the_old_pipeline(local_database):
    """A database created before 'fred-data' has the narrower check and a NOT NULL
    split; starting the site (db_schema.sql) widens both."""
    from pathlib import Path
    schema = (Path(__file__).resolve().parents[1] / "db_schema.sql").read_text(encoding="utf-8")
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("ALTER TABLE flora_dataset_history "
                    "DROP CONSTRAINT flora_dataset_history_measured_by_check, "
                    "ADD CONSTRAINT flora_dataset_history_measured_by_check "
                    "CHECK (measured_by IN ('pipeline', 'flora.csv')), "
                    "ALTER COLUMN replications SET NOT NULL, ALTER COLUMN reproductions SET NOT NULL")
        assert not bf.takes_old_counts(cur)
        cur.execute(schema)
        assert bf.takes_old_counts(cur)
        cur.execute("INSERT INTO flora_dataset_history "
                    "(recorded_on, total_rows, replications, reproductions, measured_by) "
                    "VALUES ('2025-11-11', 573, NULL, NULL, 'fred-data')")


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
