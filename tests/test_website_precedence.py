"""A record validated on this website wins over an entry-sheet copy of itself.

Before, the exact-duplicate drop kept the first row by display_id, and VAL-… always
sorted last, so the website's validated outcome was the one discarded (FLORA-000918,
FLORA-000950, FLORA-002599); the merge joined it into 'mixed' or 'A || B'
(FLORA-001697). Opt-in PostgreSQL test: FLORA_TEST_DATABASE_URL.
"""
import uuid

import pytest

import preprint_dedup
from tests.test_preparation_database import add_source, local_database  # noqa: F401

ORIGINAL, REPORT = "10.1037/xge0000001", "10.1016/j.replication.2020"


def _published(connection):
    from psycopg2.extras import RealDictCursor
    import flora_store
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        rows = flora_store.list_records(cur)["records"]
    connection.rollback()
    return rows


def test_the_website_outcome_replaces_the_sheet_one_under_the_same_id(local_database, tmp_path,
                                                                       monkeypatch):
    from psycopg2.extras import RealDictCursor
    import prepare_flora
    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "mixed"},
                       "REPL-000001")
            cur.execute(
                """INSERT INTO source_records (source, sheet_row_id, display_id, type,
                       doi_o, doi_r, outcome)
                   VALUES ('validated', %s, 'VAL-000001', 'replication', %s, %s, 'successful')""",
                (str(uuid.uuid4()), ORIGINAL, REPORT))
            for doi in (ORIGINAL, REPORT):
                cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                            "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, "Title " + doi))

    def run(name):
        report = prepare_flora.prepare(tmp_path / name, network_checks="none")
        assert report["status"] in {"success", "needs_attention"}, report["errors"]
        return _published(local_database)

    # What production publishes today: the sheet row survives, the website's is dropped.
    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "no-such-source")
    before = run("before")
    assert [r["outcome"] for r in before] == ["mixed"]
    # ...and the registry as production has it: the step-2b merge used to lose the
    # absorbed ids, so the record does not know the website row belongs to it.
    with local_database:
        with local_database.cursor() as cur:
            cur.execute("UPDATE flora_records SET merged_source_record_ids = '{}', "
                        "historical_source_record_ids = '{}'")

    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "validated")
    after = run("after")
    assert [r["outcome"] for r in after] == ["successful"]
    assert after[0]["source"] == "validated"
    # The website's values, under the row's unchanged published identity; nothing
    # is retired.
    assert after[0]["id"] == before[0]["id"]
    from psycopg2.extras import RealDictCursor as Dict
    with local_database.cursor(cursor_factory=Dict) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM flora_data WHERE retired_at IS NOT NULL")
        assert cur.fetchone()["n"] == 0
    local_database.rollback()


def test_a_merge_before_the_dedup_step_still_records_what_it_absorbed():
    """apply_confirmed() merges at step 2b, before the transform's dedup has created
    merged_record_ids. The absorbed id used to be dropped there."""
    import pandas as pd
    frame = pd.DataFrame({"doi_o": ["10.1/o"] * 2, "doi_r": ["10.1/r"] * 2,
                          "url_r": [None, None], "type": ["replication"] * 2,
                          "source": ["replications", "validated"],
                          "record_id": ["sheet", "website"], "outcome": ["mixed", "successful"]})
    out, _ = preprint_dedup.merge_doi_pair_dups(frame, verbose=False)
    assert out.iloc[0]["record_id"] == "sheet"
    assert out.iloc[0]["merged_record_ids"] == ["website"]
    assert out.iloc[0]["outcome"] == "successful"


# ── the website rule never overrides a reviewer's ruling (review findings) ────

def _website_row(cur, display_id, *, url_r=None, outcome="successful", doi_r=REPORT):
    cur.execute(
        """INSERT INTO source_records (source, sheet_row_id, display_id, type,
               doi_o, doi_r, url_r, outcome)
           VALUES ('validated', %s, %s, 'replication', %s, %s, %s, %s)
           RETURNING record_id::text AS record_id""",
        (str(uuid.uuid4()), display_id, ORIGINAL, doi_r, url_r, outcome))
    return cur.fetchone()["record_id"]


def _metadata(cur):
    for doi in (ORIGINAL, REPORT):
        cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                    "VALUES (%s, %s, 'Ana Fox', '2020') ON CONFLICT DO NOTHING",
                    (doi, "Title " + doi))


def _register(connection):
    """Refresh the registry and return (published ids, refresh stats)."""
    from psycopg2.extras import RealDictCursor
    import flora_registry
    import transform_sources
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            stats = flora_registry.refresh(cur, verbose=False)
            frame = flora_registry.attach_ids(cur, transform_sources.build(cur, verbose=False))
    return sorted(frame["export_id"]), stats


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))


@pytest.mark.parametrize("website_url", ["https://osf.io/val", None])
def test_a_website_row_ruled_distinct_does_not_swallow_the_other(local_database, offline,
                                                                 monkeypatch, website_url):
    from psycopg2.extras import RealDictCursor
    import source_records_service
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "url_r": "https://osf.io/sheet",
                             "outcome": "mixed"}, "REPL-000001")
            website = _website_row(cur, "VAL-000001", url_r=website_url)
            _metadata(cur)
            source_records_service.resolve_duplicate(cur, website, "distinct", "hamid")
    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "no-such-source")
    before, _ = _register(local_database)
    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "validated")
    after, stats = _register(local_database)
    assert after == before == ["REPL-000001", "VAL-000001"]
    assert stats["retired"] == 0


def test_a_group_a_reviewer_settled_is_not_republished_twice(local_database, offline,
                                                             monkeypatch):
    """REPL-2 ruled a duplicate of REPL-1 (which marks REPL-1 distinct); the
    unruled website copy must not come back as a second record of the paper."""
    from psycopg2.extras import RealDictCursor
    import source_records_service
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            first = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT,
                                     "url_r": "https://osf.io/a", "outcome": "mixed"},
                               "REPL-000001")
            second = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "mixed"},
                                "REPL-000002")
            _website_row(cur, "VAL-000001", url_r="https://osf.io/b")
            _metadata(cur)
            source_records_service.resolve_duplicate(cur, second, "duplicate", "hamid", first)
    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "no-such-source")
    before, _ = _register(local_database)
    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "validated")
    after, _ = _register(local_database)
    assert after == before == ["REPL-000001"]


def test_a_website_row_with_an_old_retired_id_does_not_take_the_published_one(
        local_database, offline, monkeypatch):
    """VAL-1 was once published alone (a typo in its DOI), then corrected and
    absorbed by REPL-1. When it becomes the survivor it must take over REPL-1's live
    record, not revive its own retired one."""
    from psycopg2.extras import RealDictCursor
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "mixed"}, "REPL-000001")
            website = _website_row(cur, "VAL-000001", doi_r="10.1016/j.typo.2020")
            _metadata(cur)
    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "no-such-source")
    assert _register(local_database)[0] == ["REPL-000001", "VAL-000001"]
    with local_database:
        with local_database.cursor() as cur:
            cur.execute("UPDATE source_records SET doi_r = %s WHERE record_id = %s",
                        (REPORT, website))
    corrected, _ = _register(local_database)
    assert corrected == ["REPL-000001"]
    monkeypatch.setattr(preprint_dedup, "WEBSITE_SOURCE", "validated")
    after, stats = _register(local_database)
    assert after == ["REPL-000001"] and stats["retired"] == 0


def test_an_outcome_the_website_row_overrode_in_the_dedup_is_logged(local_database, offline):
    """Different url_r values keep the rows out of the merge, so the dedup drop
    settles it; that used to discard the losing outcome without a trace."""
    from psycopg2.extras import RealDictCursor
    import transform_sources
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "url_r": "https://osf.io/sheet",
                             "outcome": "mixed"}, "REPL-000001")
            _website_row(cur, "VAL-000001", url_r="https://osf.io/val")
            _metadata(cur)
    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        frame = transform_sources.build(cur, verbose=False)
    local_database.rollback()
    assert frame["outcome"].tolist() == ["successful"]
    conflict = frame.attrs["outcome_conflicts"][0]
    assert conflict["resolved_by"] == "website record"
    assert conflict["outcomes"] == "mixed | successful"
