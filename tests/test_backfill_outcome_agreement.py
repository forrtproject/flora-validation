"""Focused unit tests for the still-runnable backfill_outcome_agreement.py."""
import os
import subprocess
from datetime import datetime, timezone

import pytest

import backfill_outcome_agreement as backfill


def _git(repo, *args, when=None, config_home=None):
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(config_home),
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.org",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.org",
    }
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def git_repo_factory(tmp_path):
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")

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


def _csv(*rows):
    return "pair_id,type,outcome\n" + "".join(f"{p},{t},{o}\n" for p, t, o in rows)


def _at(day):
    return datetime.fromisoformat(f"2026-{day}T12:00:00+00:00")


def test_extractor_history_tracks_consensus_across_commits_branches_and_merges(git_repo_factory):
    extractor, commit = git_repo_factory("extractor")
    own, own_commit = git_repo_factory("own")

    commit("2026-06-01T10:00:00+00:00", {"data/extracted.csv": _csv(("P1", "replication", "failed"))})
    # Side branch changes P1 outcome, then merges with `-s ours` so first-parent
    # history on main NEVER changes P1 from "failed". Full-graph history must still
    # detect the side-branch divergence both before and after the merge.
    commit(
        "2026-06-10T10:00:00+00:00",
        {"data/extracted.csv": _csv(("P1", "replication", "successful"))},
        branch="side",
    )
    commit.git(
        "merge", "--no-ff", "-s", "ours", "-q", "side", "-m", "merge side keeping ours",
        when="2026-06-12T10:00:00+00:00",
    )
    own_commit(
        "2026-06-02T10:00:00+00:00",
        {"data/extracted_latest.csv": _csv(("P2", "replication", "mixed"))},
    )

    history = backfill.ExtractorHistory(extractor, own)
    assert history.shown("P1", _at("06-05")) == ("replication", "failed")
    assert history.shown("P1", _at("06-11")) is None
    assert history.shown("P1", _at("06-15")) is None
    assert history.shown("P2", _at("06-05")) == ("replication", "mixed")


def test_review_candidates_never_converts_record_that_became_a_reproduction():
    """A replication judgement matching a former outcome must not convert or be
    selected for re-evaluation once the record's current type is reproduction."""
    judgement = {
        "validator_slot": "human_1",
        "type_check": "correct",
        "original_check": "correct",
        "outcome_check": "incorrect",
        "corrected_outcome": "failed",
        "corrected_doi_o": None,
        "corrected_title_o": None,
        "corrected_type": None,
        "corrected_title_r": None,
        "corrected_url_r": None,
        "corrected_abstract": None,
        "corrected_outcome_computation": None,
        "corrected_outcome_robustness": None,
        "doi_r_published": None,
        "additional_checks": {},
        "shown_at": datetime(2026, 6, 5, 11, 55, tzinfo=timezone.utc),
        "validated_at": datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
    }
    assert backfill.is_same_outcome_correction(judgement, "reproduction", "failed") is False
    assert backfill.is_same_outcome_correction(judgement, "replication", "failed") is True

    class _FakeHistory:
        def __init__(self, result):
            self._result = result

        def shown(self, pair_id, when, slot=None):
            return self._result

    # History returning a reproduction row never counts as verified replication history.
    assert (
        backfill.saw_outcome(
            judgement, "failed", _FakeHistory(("reproduction", "failed")),
            "P1", judgement["validated_at"],
        )
        == "changed"
    )

    class _FakeCursor:
        def __init__(self, status_row, human_rows):
            self._status = status_row
            self._humans = human_rows
            self._last_sql = ""

        def execute(self, sql, params=None):
            self._last_sql = sql

        def fetchone(self):
            return self._status

        def fetchall(self):
            return self._humans

    h1 = {**judgement, "outcome_check": "correct", "corrected_outcome": None}
    h2 = {**h1, "validator_slot": "human_2"}
    base_status = {
        "validation_status": "need_review",
        "type": "reproduction",
        "outcome": "failed",
        "pair_id": "P1",
        "admin_checked": False,
        "admin_override": False,
        "admin_name": None,
        "admin_notes": None,
        "work_id": None,
        "original_rank": None,
    }
    history = _FakeHistory(("replication", "failed"))

    # Reproduction record is skipped even when both human judgements agree with history.
    assert backfill.review_candidates(_FakeCursor(base_status, [h1, h2]), ["r-1"], history) == []
    # Same record as a replication IS selected.
    repl_status = {**base_status, "type": "replication"}
    assert backfill.review_candidates(_FakeCursor(repl_status, [h1, h2]), ["r-1"], history) == ["r-1"]
