"""The nightly published-versions step, end to end against PostgreSQL.

The step reads the prepared dataset the build hands over, so its column types are
the real ones. Opt-in like tests/test_preparation_database.py
(FLORA_TEST_DATABASE_URL); every lookup is synthetic.
"""
import json
import sys

import pytest
from psycopg2.extras import RealDictCursor

import flora_service
import preprint_dedup
import resolve_publication_versions as runner
from tests.test_flora_edits_database import _pipeline, _published, _source_rows, offline  # noqa: F401
from tests.test_preparation_database import add_source, local_database  # noqa: F401
from tests.test_published_versions import ARTICLE, PREPRINT, FakeClient

ORIGINAL, UNRELATED = "10.9999/original.1", "10.9999/original.2"
EARLY = "10.31234/osf.io/zz9zz"     # invented: the metadata seed knows PREPRINT, a real paper
# UNRELATED has no authors, so its record has no author overlap: a blank in both
# nullable number columns, which is what stopped this step every night.
METADATA = {ORIGINAL: ("The original study", "Ana Fox"),
            UNRELATED: ("Another original study", None),
            EARLY: ("Published paper", "Sam Smith"),
            ARTICLE: ("Published paper", "Sam Smith"),
            "10.9999/report.2": ("Another report", "Ben Lee")}


class Registry(FakeClient):
    """The synthetic registry of test_published_versions, for this preprint."""
    def work(self, doi):
        return super().work(PREPRINT if doi == EARLY else doi)


@pytest.fixture
def database(local_database, offline, monkeypatch):
    """Metadata for every DOI, and no rulings from the repository's confirmed file."""
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [])
    with local_database, local_database.cursor() as cur:
        for doi, (title, authors) in METADATA.items():
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, %s, '2020')", (doi, title, authors))
    return local_database


def _add(connection, *reports):
    """One source row per (original, report), taken through a pipeline run."""
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        for number, (original, report) in enumerate(reports, 1):
            add_source(cur, {"doi_o": original, "doi_r": report, "outcome": "failed",
                             "outcome_quote": "It did not replicate."}, f"REPL-{number:06d}")
    _pipeline(connection)


def _run(monkeypatch, tmp_path, errors=None, apply=True):
    """The command the nightly job runs: its exit code and its status file."""
    client = Registry(article_doi=ARTICLE)
    client.errors = errors or {}
    monkeypatch.setattr(runner.discovery, "Client", lambda *a: client)
    output = tmp_path / "candidates.csv"
    monkeypatch.setattr(sys, "argv", ["resolve_publication_versions.py", "--output", str(output)]
                        + (["--apply"] if apply else []))
    code = runner.main()
    flora_service.invalidate()
    return code, json.loads(output.with_suffix(".status.json").read_text())


def _changes(connection):
    """Everything the step may write: logged corrections, and stored rulings."""
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM flora_record_changes")
        changes = cur.fetchone()["n"]
        cur.execute("SELECT action, side, decided_by FROM preprint_dedup_decisions")
        return changes, [dict(row) for row in cur.fetchall()]


def test_a_lone_preprint_is_corrected_once_and_keeps_its_doi_as_alternative(database, monkeypatch, tmp_path):
    """The record moves to the journal DOI through the log; its source row is untouched."""
    _add(database, (ORIGINAL, EARLY), (UNRELATED, "10.9999/report.2"))
    with database, database.cursor(cursor_factory=RealDictCursor) as cur:
        before = _source_rows(cur)
        built = flora_service.dataset(cur)
        assert built["author_overlap_pct"].dtype == "Float64" and built["author_overlap_pct"].isna().any()

    assert _run(monkeypatch, tmp_path, apply=False) == (0, {
        "applied": False, "errors": {}, "candidates": 1, "corrections": 1, "dedup_decisions": 0, "review": 0})
    assert _changes(database) == (0, [])            # a preview writes nothing

    code, status = _run(monkeypatch, tmp_path)
    assert code == 0 and status["applied"] and len(status["corrections"]) == 1
    record, _ = _published(database)
    assert record["doi_r"] == ARTICLE and record["alt_identifier_r"] == EARLY
    logged = _changes(database)
    assert logged[0] > 0 and logged[1] == []

    # The next night changes nothing more, and the pipeline keeps the correction.
    _pipeline(database)
    code, status = _run(monkeypatch, tmp_path)
    assert code == 0 and status["corrections"] == []
    assert _changes(database) == logged
    assert _published(database)[0]["doi_r"] == ARTICLE
    with database, database.cursor(cursor_factory=RealDictCursor) as cur:
        assert _source_rows(cur) == before


def test_two_entered_versions_become_one_record_at_the_next_build(database, monkeypatch, tmp_path):
    """Both versions entered for one original: a stored ruling, merged by the pipeline."""
    # The build drops a preprint titled like its article by itself; this is the pair
    # it leaves in place.
    find = preprint_dedup.find_duplicates
    monkeypatch.setattr(preprint_dedup, "find_duplicates",
                        lambda frame, threshold, verbose: find(frame, 2, verbose))
    _add(database, (ORIGINAL, ARTICLE), (ORIGINAL, EARLY), (UNRELATED, "10.9999/report.2"))
    code, status = _run(monkeypatch, tmp_path)
    assert code == 0 and status["corrections"] == [] and status["new_dedup_decisions"] == 1
    assert _changes(database) == (0, [{"action": "keep_2", "side": "replication (publication link)",
                                       "decided_by": runner.ACTOR}])
    _pipeline(database)
    with database, database.cursor(cursor_factory=RealDictCursor) as cur:
        built = flora_service.dataset(cur)
    records = built[built["doi_o"].eq(ORIGINAL)]
    assert records["doi_r"].tolist() == [ARTICLE] and records["alt_identifier_r"].tolist() == [EARLY]


def test_a_failed_lookup_leaves_the_database_alone(database, monkeypatch, tmp_path):
    """One unanswered lookup withholds the whole night's automatic changes."""
    _add(database, (ORIGINAL, EARLY), (UNRELATED, "10.9999/report.2"))
    code, status = _run(monkeypatch, tmp_path, errors={"https://api.example/lookup": "timeout"})
    assert code == 1 and status["applied"] is False
    assert _changes(database) == (0, [])
    assert _published(database)[0]["doi_r"] == EARLY
