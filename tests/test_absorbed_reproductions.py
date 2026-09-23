"""A replication and a reproduction of one paper are two FLoRA records.

Earlier builds merged them on (doi_o, doi_r) alone, so the reproduction was published
as a replication with an invalid joined outcome ("absorbed"). These tests pin the
fix and what it does to identities already published.
"""
import uuid

import pytest

import flora_service
import preprint_dedup
import source_records_service
from tests.test_preparation_database import add_source, local_database  # noqa: F401

ORIGINAL, REPORT = "10.1086/511995", "10.5018/economics-ejournal.ja.2017-13"


class RecordingCursor:
    def __init__(self, results=None):
        self.batches = list(results or [])
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.batches.pop(0) if self.batches else []


def member(display_id, kind, source):
    return {"record_id": display_id, "content_fingerprint": "fp", "display_id": display_id,
            "source": source, "type": kind, "outcome": None, "outcome_computation": None,
            "outcome_robustness": None, "duplicate_status": None}


def test_mixed_type_groups_are_flagged_and_listed_last():
    """They are the least likely to be real duplicates; first place invited the
    ruling that removes a reproduction."""
    cur = RecordingCursor([
        [{"content_fingerprint": "mixed"}, {"content_fingerprint": "same"}],
        [dict(member("REPL-1", "replication", "replications"), content_fingerprint="mixed"),
         dict(member("REPRO-1", "reproduction", "reproductions"), content_fingerprint="mixed"),
         dict(member("REPL-2", "replication", "replications"), content_fingerprint="same"),
         dict(member("REPL-3", "replication", "replications"), content_fingerprint="same")],
    ])
    groups = source_records_service.duplicate_groups(cur)["groups"]
    assert [g["fingerprint"] for g in groups] == ["same", "mixed"]
    assert groups[1]["mixed_types"] is True and groups[1]["outcomes_differ"] is False
    assert groups[0]["mixed_types"] is False


def test_two_disagreeing_replications_in_a_mixed_group_are_still_flagged():
    """Outcomes are compared within each type, so a real duplicate among the
    replications is not hidden just because a reproduction shares the paper."""
    rows = [dict(member("REPL-1", "replication", "replications"), outcome="successful"),
            dict(member("REPL-2", "replication", "replications"), outcome="failed"),
            member("REPRO-1", "reproduction", "reproductions"),
            dict(member("REPL-3", "replication", "replications"), content_fingerprint="pair"),
            dict(member("REPRO-3", "reproduction", "reproductions"), content_fingerprint="pair")]
    cur = RecordingCursor([[{"content_fingerprint": "pair"}, {"content_fingerprint": "fp"}],
                           rows])
    groups = source_records_service.duplicate_groups(cur)["groups"]
    by_fp = {g["fingerprint"]: g for g in groups}
    assert by_fp["fp"]["outcomes_differ"] is True and by_fp["fp"]["mixed_types"] is True
    # One replication + one reproduction only: listed after the real duplicate.
    assert [g["fingerprint"] for g in groups] == ["fp", "pair"]
    assert "_types_only" not in by_fp["fp"]


# ── on PostgreSQL (opt-in: FLORA_TEST_DATABASE_URL) ───────────────────────────

def _seed(cur):
    """One paper coded twice: a replication (new data) and a reproduction."""
    replication = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "failed"},
                             "REPL-000001")
    cur.execute(
        """INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o, doi_r,
               outcome_computation, outcome_robustness)
           VALUES ('reproductions', %s, 'REPRO-000001', 'reproduction', %s, %s,
                   'computational issues', 'robustness challenges')
           RETURNING record_id::text AS record_id""",
        (str(uuid.uuid4()), ORIGINAL, REPORT))
    reproduction = cur.fetchone()["record_id"]
    for doi, title in ((ORIGINAL, "Original study"), (REPORT, "Replication and reanalysis")):
        cur.execute("INSERT INTO work_metadata (doi, title, authors, year) VALUES (%s, %s, %s, %s)",
                    (doi, title, "Ana Fox", "2017"))
    return replication, reproduction


def _published(connection):
    """Active flora_data rows as the public API reads them, extra columns included."""
    from psycopg2.extras import RealDictCursor
    import flora_store
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        rows = flora_store.list_records(cur)["records"]
    connection.rollback()
    return sorted(rows, key=lambda r: r["type"])


def _prepare(tmp_path, name):
    import prepare_flora
    report = prepare_flora.prepare(tmp_path / name, network_checks="none")
    assert report["status"] in {"success", "needs_attention"}, report["errors"]
    return report


def test_an_absorbed_reproduction_is_published_on_its_own_at_the_next_run(
        local_database, tmp_path, monkeypatch):
    from psycopg2.extras import RealDictCursor
    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            _seed(cur)

    # The state production is in today: a build that merged on (doi_o, doi_r).
    typed_merge = preprint_dedup.merge_doi_pair_dups

    def type_blind(frame, *args, **kwargs):
        out, conflicts = typed_merge(frame.rename(columns={"type": "_type"}), *args, **kwargs)
        return out.rename(columns={"_type": "type"}), conflicts

    monkeypatch.setattr(preprint_dedup, "merge_doi_pair_dups", type_blind)
    _prepare(tmp_path, "before")
    before = _published(local_database)
    assert [(r["type"], r["outcome"]) for r in before] == [
        ("replication", "failed || computational issues, robustness challenges")]

    monkeypatch.setattr(preprint_dedup, "merge_doi_pair_dups", typed_merge)
    _prepare(tmp_path, "after")
    after = _published(local_database)
    replication, reproduction = after
    # The replication keeps the ID it was published under, with its own outcome back.
    assert replication["id"] == before[0]["id"]
    assert (replication["type"], replication["outcome"]) == ("replication", "failed")
    assert replication["outcome_computation"] is None
    # The reproduction is published as itself, under a new ID.
    assert reproduction["type"] == "reproduction"
    assert reproduction["outcome"] == "computational issues, robustness challenges"
    assert reproduction["id"] != before[0]["id"]


def test_a_cross_type_duplicate_ruling_is_reported_on_every_run(local_database, tmp_path,
                                                                 monkeypatch):
    """What may have hidden FLORA-001411/001415's reproductions: a Source Records
    ruling that the reproduction duplicates the replication."""
    from psycopg2.extras import RealDictCursor
    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            replication, reproduction = _seed(cur)
            source_records_service.resolve_duplicate(cur, reproduction, "duplicate", "hamid",
                                                     replication)

    report = _prepare(tmp_path, "release")
    assert [r["type"] for r in _published(local_database)] == ["replication"]
    diagnostic = report["diagnostics"]["cross_type_duplicate_rulings.csv"]
    assert diagnostic["rows"] == 1
    assert diagnostic["records"][0]["display_id"] == "REPRO-000001"
    assert diagnostic["records"][0]["duplicate_of"] == "REPL-000001"
    assert any("ruled a duplicate of a record of the other type" in w
               for w in report["warnings"])

    # Ruling it distinct brings the reproduction back and clears the warning.
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            source_records_service.resolve_duplicate(cur, reproduction, "distinct", "hamid")
    report = _prepare(tmp_path, "release")
    assert [r["type"] for r in _published(local_database)] == ["replication", "reproduction"]
    assert "cross_type_duplicate_rulings.csv" not in report["diagnostics"]
    flora_service.invalidate()
