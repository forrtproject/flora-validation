"""Old "Mischaracterised → the same category" judgements, verified by the extractor
history, and the safeguards around converting any of them.

Judgements saved before shown_outcome existed record nothing of what the page
showed. The pair screen showed the record's outcome as the last import left it,
and every import read some version of the extracted file: when every version the
app could have imported, up to the judgement, gives the pair one outcome, the
page showed that one — whichever version it was.

The database checks are opt-in like tests/test_preparation_database.py
(FLORA_TEST_DATABASE_URL).
"""
import json
import os
import subprocess
from datetime import datetime, timezone

import pytest
from psycopg2.extras import RealDictCursor

import backfill_outcome_agreement as backfill
from tests.test_outcome_quote_agreement import _now_agree, _seed, _state, offline_llm  # noqa: F401
from tests.test_preparation_database import local_database  # noqa: F401


# ── reading the history ───────────────────────────────────────────────────────

def _git(repo, *args, when=None, config_home=None):
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(config_home),
           "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.org",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.org"}
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path):
    """A throwaway repository, isolated from the user's and system git settings."""
    config = tmp_path / "gitconfig"
    config.write_text("")

    def make(name):
        path = tmp_path / name
        path.mkdir()
        _git(path, "init", "-q", "-b", "main", config_home=config)

        def commit(when, files, branch=None):
            if branch:
                _git(path, "checkout", "-q", "-B", branch, config_home=config)
            for rel, text in files.items():
                target = path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            _git(path, "add", "-A", config_home=config)
            _git(path, "commit", "-q", "-m", when, when=when, config_home=config)
            if branch:
                _git(path, "checkout", "-q", "main", config_home=config)

        commit.git = lambda *args, when=None: _git(path, *args, when=when, config_home=config)
        return path, commit
    return make


def _csv(*rows, type_column="type"):
    return f"pair_id,{type_column},outcome\n" + "".join(f"{p},{t},{o}\n" for p, t, o in rows)


def _at(day):
    return datetime.fromisoformat(f"2026-{day}T12:00:00+00:00")


def test_the_page_showed_what_every_earlier_version_agrees_on(repo):
    extractor, commit = repo("extractor")
    own, _ = repo("own")
    commit("2026-06-01T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "failed"), ("P2", "replication", "Failed"))})
    commit("2026-06-10T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "failed"), ("P2", "replication", "successful"))})
    commit("2026-07-01T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "successful"), ("P2", "replication", "successful"))})
    history = backfill.ExtractorHistory(extractor, own)

    assert history.shown("P1", _at("06-05")) == ("replication", "failed")
    assert history.shown("P2", _at("06-05")) == ("replication", "failed")    # spelled as stored
    assert history.shown("P1", _at("06-15")) == ("replication", "failed")    # later ones not yet
    assert history.shown("P2", _at("06-15")) is None       # failed, then successful: either
    assert history.shown("P1", _at("07-05")) is None
    assert history.shown("P9", _at("07-05")) is None       # never in the file
    assert history.shown("P1", _at("05-30")) is None       # before any version


def test_every_branch_and_every_snapshot_committed_here_counts(repo):
    """The app's source moved between repositories and branches, and a deployment
    setting can point it elsewhere: any of them might have been imported."""
    extractor, commit = repo("extractor")
    own, commit_own = repo("own")
    commit("2026-06-01T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "failed"), ("P2", "replication", "failed"),
        ("P3", "replication", "failed"))})
    commit("2026-06-03T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "mixed"))}, branch="feature/extract")
    commit_own("2026-06-02T10:00:00+00:00", {
        "data/extracted_02.06.2026.csv": _csv(("P2", "replication", "successful")),
        "data/validated_export.csv": _csv(("P3", "replication", "mixed")),    # not an import
    })
    history = backfill.ExtractorHistory(extractor, own)
    assert history.shown("P1", _at("06-05")) is None
    assert history.shown("P2", _at("06-05")) is None
    assert history.shown("P3", _at("06-05")) == ("replication", "failed")


def test_an_older_type_column_is_read_and_an_unreadable_version_stops(repo):
    extractor, commit = repo("extractor")
    own, _ = repo("own")
    commit("2026-06-01T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "Replication", "failed"), type_column="paper_type")})
    assert backfill.ExtractorHistory(extractor, own).shown("P1", _at("06-05")) == \
        ("replication", "failed")
    commit("2026-06-02T10:00:00+00:00", {"data/extracted.csv": "pair_id,verdict\nP1,failed\n"})
    with pytest.raises(ValueError, match="no pair_id, outcome or type column"):
        backfill.ExtractorHistory(extractor, own)      # it might be the one that disagrees


def test_a_pair_listed_twice_in_one_version_counts_both_rows(repo):
    """The importer kept the first of two rows for one pair; the page showed that,
    not the last."""
    extractor, commit = repo("extractor")
    own, _ = repo("own")
    commit("2026-06-01T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "failed"), ("P2", "replication", "mixed"),
        ("P1", "replication", "successful"))})
    history = backfill.ExtractorHistory(extractor, own)
    assert history.shown("P1", _at("06-05")) is None
    assert history.shown("P2", _at("06-05")) == ("replication", "mixed")


def _slot_csv(*rows):
    return "pair_id,type,outcome,oa_work_id_r,original_rank\n" + "".join(
        f"{p},{t},{o},{w},{r}\n" for p, t, o, w, r in rows)


def test_a_re_keyed_record_counts_the_rows_under_its_earlier_pair_id(repo):
    """A corrected DOI gives a record a new pair_id in the same slot; pages before
    the import that re-keyed it showed the old pair's row."""
    extractor, commit = repo("extractor")
    own, _ = repo("own")
    commit("2026-06-01T10:00:00+00:00", {"data/extracted.csv": _slot_csv(
        ("OLD", "replication", "successful", "W77", "1"))})
    commit("2026-06-05T10:00:00+00:00", {"data/extracted.csv": _slot_csv(
        ("NEW", "replication", "mixed", "W77", "1"), ("OTHER", "replication", "failed", "W77", "2"))})
    history = backfill.ExtractorHistory(extractor, own)
    assert history.shown("NEW", _at("06-10")) == ("replication", "mixed")      # by pair alone
    assert history.shown("NEW", _at("06-10"), (77, 1)) is None                 # by its slot too
    assert history.shown("OTHER", _at("06-10"), (77, 2)) == ("replication", "failed")


def test_versions_reachable_only_through_a_merge_are_read(repo):
    """Default history simplification skips a branch merged with -s ours once its
    name is gone; a merge that resolves to new content lists no changed paths."""
    extractor, commit = repo("extractor")
    own, _ = repo("own")
    commit("2026-06-01T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "failed"), ("P2", "replication", "failed"))})
    commit("2026-06-02T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "successful"), ("P2", "replication", "failed"))}, branch="side")
    commit.git("merge", "-q", "-s", "ours", "side", "-m", "keep ours",
               when="2026-06-03T10:00:00+00:00")
    commit.git("branch", "-q", "-D", "side")
    commit("2026-06-04T10:00:00+00:00", {"data/extracted.csv": _csv(
        ("P1", "replication", "failed"), ("P2", "replication", "failed"), ("P3", "replication", "x"))},
        branch="other")
    commit.git("merge", "-q", "-s", "ours", "--no-commit", "other")
    (extractor / "data" / "extracted.csv").write_text(_csv(
        ("P1", "replication", "failed"), ("P2", "replication", "mixed")), encoding="utf-8")
    commit.git("add", "-A")
    commit.git("commit", "-q", "-m", "resolve", when="2026-06-05T10:00:00+00:00")
    history = backfill.ExtractorHistory(extractor, own)
    assert history.shown("P1", _at("06-10")) is None       # "successful" only on the dropped side
    assert history.shown("P2", _at("06-10")) is None       # "mixed" only in the merge


def test_a_clone_of_another_repository_is_refused(repo):
    elsewhere, commit = repo("elsewhere")
    commit.git("remote", "add", "origin", "https://github.com/forrtproject/flora-validation.git")
    with pytest.raises(SystemExit, match="not forrtproject/flora-extractor"):
        backfill.load_history(elsewhere)


def test_the_history_decides_only_where_nothing_was_recorded():
    class Never:
        def shown(self, pair_id, at, slot=None):
            raise AssertionError("consulted")
    recorded = {"additional_checks": {"shown_outcome": "failed"}}
    assert backfill.saw_outcome(recorded, "failed", Never(), "P1", _at("06-05")) == "verified"


# ── converting against a real database ────────────────────────────────────────

class _History:
    """Stands in for ExtractorHistory: what every earlier version gave each pair."""

    def __init__(self, seen):
        self.seen, self.asked = seen, []

    def shown(self, pair_id, at, slot=None):
        self.asked.append((pair_id, at))
        return self.seen.get(pair_id)


JUDGED = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _with_pair(conn, record_id, pair_id="P1"):
    """Give the seeded record its pair_id and its judgements a submission time."""
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE unvalidated SET pair_id = %s WHERE record_id = %s", (pair_id, record_id))
        cur.execute("UPDATE validation_queue SET shown_at = %s, validated_at = %s "
                    "WHERE record_id = %s", (JUDGED.replace(minute=0, hour=11), JUDGED, record_id))
    return record_id


@pytest.fixture
def backups(tmp_path, monkeypatch):
    folder = tmp_path / "backups"
    monkeypatch.setenv("OUTCOME_BACKFILL_BACKUP_DIR", str(folder))
    return folder


def test_a_judgement_the_history_verifies_is_converted_keeping_its_answer_after_a_backup(
        local_database, capsys, backups):
    record_id = _with_pair(local_database,
                           _seed(local_database, status="consensus_reached", legacy_shown=None))
    history = _History({"P1": ("replication", "failed")})

    backfill.run(apply=False, reevaluate=False, history=history)
    dry = capsys.readouterr().out
    assert "verified by the extractor history:    1" in dry
    assert "unverifiable (nothing recorded):      0" in dry
    assert not backups.exists()                                  # a dry run saves nothing
    assert _state(local_database, record_id)[0][0]["outcome_check"] == "incorrect"
    assert ("P1", JUDGED) in history.asked                       # up to the submission

    backfill.run(apply=True, reevaluate=False, history=history)
    queue, record = _state(local_database, record_id)
    for judgement in (queue[0], record["validator_1"]):
        checks = judgement["additional_checks"]
        assert (judgement["outcome_check"], judgement["corrected_outcome"]) == ("correct", None)
        assert checks["outcome_agreement_history_checked"] is True
        assert "outcome_agreement_unverified" not in checks
        assert checks["outcome_agreement_original"] == {"outcome_check": "incorrect",
                                                        "corrected_outcome": "failed"}
    [backup] = backups.iterdir()
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert [(q["record_id"], q["outcome_check"], q["corrected_outcome"])
            for q in saved["validation_queue"]] == [(record_id, "incorrect", "failed")]
    assert [(c["record_id"], c["column"], c["judgement"]["outcome_check"])
            for c in saved["stored_copies"]] == [(record_id, "validator_1", "incorrect")]


def test_a_history_giving_another_outcome_marks_a_real_correction(local_database, capsys, backups):
    """Every version said "successful" when the validator picked "failed": they
    corrected it, and the extractor agreed later. Never converted."""
    record_id = _with_pair(local_database,
                           _seed(local_database, status="consensus_reached", legacy_shown=None))
    backfill.run(apply=True, reevaluate=False, include_unverified=True,
                 history=_History({"P1": ("replication", "successful")}))
    assert "shown another outcome (left as is):   1" in capsys.readouterr().out
    queue, record = _state(local_database, record_id)
    assert queue[0]["outcome_check"] == record["validator_1"]["outcome_check"] == "incorrect"


def test_an_ambiguous_history_leaves_it_to_a_person(local_database, capsys, backups):
    record_id = _with_pair(local_database,
                           _seed(local_database, status="consensus_reached", legacy_shown=None))
    backfill.run(apply=True, reevaluate=False, history=_History({}))
    out = capsys.readouterr().out
    assert "unverifiable (nothing recorded):      1" in out
    assert record_id in out                                      # listed for a person
    assert _state(local_database, record_id)[0][0]["outcome_check"] == "incorrect"


def test_records_both_judgements_are_verified_for_can_be_settled(local_database, capsys, offline_llm):
    record_id = _with_pair(local_database,
                           _seed(local_database, legacy_shown=None, agree_shown=None))
    backfill.run(apply=False, reevaluate=False)
    assert _now_agree(capsys.readouterr().out) == (0, [])        # nothing says what they saw
    history = _History({"P1": ("replication", "failed")})
    backfill.run(apply=False, reevaluate=False, history=history)
    assert _now_agree(capsys.readouterr().out) == (1, [record_id])
    backfill.run(apply=True, reevaluate=True, history=history)
    assert _state(local_database, record_id)[1]["validation_status"] == "consensus_reached"


def test_a_judgement_converted_unverified_is_never_re_evaluated(local_database, capsys, offline_llm):
    record_id = _with_pair(local_database,
                           _seed(local_database, legacy_shown=None, agree_shown=None))
    backfill.run(apply=True, reevaluate=False, include_unverified=True)
    capsys.readouterr()
    backfill.run(apply=True, reevaluate=True, history=_History({"P1": ("replication", "failed")}))
    assert _now_agree(capsys.readouterr().out) == (0, [])
    assert _state(local_database, record_id)[1]["validation_status"] == "need_review"


def test_no_backup_no_change(local_database, tmp_path, monkeypatch):
    """The backup is written before the first update; if it cannot be, nothing is."""
    record_id = _with_pair(local_database,
                           _seed(local_database, status="consensus_reached", legacy_shown=None))
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("")
    monkeypatch.setenv("OUTCOME_BACKFILL_BACKUP_DIR", str(blocked))
    with pytest.raises(OSError):
        backfill.run(apply=True, reevaluate=False,
                     history=_History({"P1": ("replication", "failed")}))
    queue, record = _state(local_database, record_id)
    assert queue[0]["outcome_check"] == record["validator_1"]["outcome_check"] == "incorrect"


def test_an_assignment_copy_is_not_verified_through_someone_elses_queue_row(
        local_database, capsys, backups):
    """A stored copy shares the history verdict only of the queue row holding the
    same judgement. An assignment that replaced validator_1 is another judgement,
    even by the same validator with the same pick: it has no queue row of its own
    and records no times, so it stays for a person to check."""
    record_id = _with_pair(local_database,
                           _seed(local_database, status="consensus_reached", legacy_shown=None))
    with local_database, local_database.cursor() as cur:
        cur.execute("UPDATE unvalidated SET validator_1 = validator_1 || "
                    "'{\"is_assignment\": true}'::jsonb WHERE record_id = %s", (record_id,))
    backfill.run(apply=True, reevaluate=False, history=_History({"P1": ("replication", "failed")}))
    assert "unverifiable, stored copy only:       1" in capsys.readouterr().out
    queue, record = _state(local_database, record_id)
    assert queue[0]["additional_checks"]["outcome_agreement_history_checked"] is True
    assert record["validator_1"]["outcome_check"] == "incorrect"


def test_the_history_is_asked_about_the_records_slot(local_database, capsys, backups):
    record_id = _with_pair(local_database,
                           _seed(local_database, status="consensus_reached", legacy_shown=None))
    with local_database, local_database.cursor() as cur:
        cur.execute("INSERT INTO record_metadata (record_id, pair_id, work_id, original_rank) "
                    "VALUES (%s, 'P1', 77, 2)", (record_id,))
    slots = []

    class Recording(_History):
        def shown(self, pair_id, at, slot=None):
            slots.append(slot)
            return super().shown(pair_id, at, slot)

    backfill.run(apply=False, reevaluate=False,
                 history=Recording({"P1": ("replication", "failed")}))
    assert (77, 2) in slots


def test_re_evaluating_saves_the_records_first_and_the_advice_keeps_the_history(
        local_database, capsys, offline_llm, backups):
    record_id = _with_pair(local_database,
                           _seed(local_database, legacy_shown=None, agree_shown=None))
    history = _History({"P1": ("replication", "failed")})
    backfill.run(apply=True, reevaluate=False, history=history)
    assert "Re-run with --apply --reevaluate --extractor-history" in capsys.readouterr().out
    backfill.run(apply=True, reevaluate=True, history=history)
    assert "records saved before re-evaluating" in capsys.readouterr().out
    saved = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(backups.iterdir())]
    [before] = [s["records_before_reevaluation"] for s in saved if "records_before_reevaluation" in s]
    assert [(r["record_id"], r["validation_status"]) for r in before["unvalidated"]] == \
        [(record_id, "need_review")]
    assert _state(local_database, record_id)[1]["validation_status"] == "consensus_reached"
