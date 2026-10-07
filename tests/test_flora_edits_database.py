"""Changes to FLoRA records, end to end against PostgreSQL.

A change is one row of the record's log, rewrites its row in flora_data at once,
survives the next pipeline run and a change to the source underneath it, can be
restored to any earlier state, and never touches the source record. Opt-in like
tests/test_preparation_database.py (FLORA_TEST_DATABASE_URL).
"""
import csv
import io
import uuid

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

import enrich_works
import flora_edits
import flora_registry
import flora_service
import flora_store
import source_records_service
import transform_sources
from tests.test_preparation_database import add_source, local_database  # noqa: F401

ORIGINAL = "10.9999/original.1"
REPORT = "10.9999/report.1"
FIXED = "10.9999/report.fixed"
TITLES = {ORIGINAL: "The original study", REPORT: "The replication report",
          FIXED: "The corrected replication report"}
LUKE = {"id": None, "handle": "luke"}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))


def _pipeline(connection):
    """What a pipeline run does to flora_data: rebuild from the source records,
    re-apply the change log, and store the complete snapshot."""
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
        frame = transform_sources.build(cur, verbose=False)
        flora_registry.refresh(cur, verbose=False, frame=frame)
        frame = flora_edits.apply(cur, flora_registry.attach_ids(cur, frame))
        shaped = transform_sources.to_output_shape(transform_sources.drop_untitled(frame))
        stats = flora_store.materialize(cur, shaped)
    flora_service.invalidate()
    return stats


@pytest.fixture
def seeded(local_database, offline):
    """One published replication, with metadata for its DOIs and a corrected one."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record_id = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "successful",
                                     "outcome_quote": "It replicated."}, "REPL-000001")
        for doi, title in TITLES.items():
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, title))
    _pipeline(local_database)
    return local_database, record_id


def _source_rows(cur):
    cur.execute("SELECT * FROM source_records ORDER BY display_id")
    return [dict(row) for row in cur.fetchall()]


def _published(connection, export_id="REPL-000001"):
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT updated_at FROM flora_data_metadata WHERE singleton")
        revision = cur.fetchone()["updated_at"]
        record = flora_store.get_record(cur, export_id)
    return record, revision


def _save(connection, fields, version, revert=(), record="REPL-000001"):
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        return flora_service.update_record(cur, record, fields, list(revert), version,
                                           LUKE, "checked against the paper")


def _restore(connection, change_id, version):
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        return flora_service.restore_record(cur, "REPL-000001", change_id, version, LUKE, "")


def _read(connection, record="REPL-000001"):
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        found = flora_service.get_record(cur, record)
        return found, flora_service.editing(cur, found)


def test_a_change_rewrites_the_flora_table_and_leaves_the_source_alone(seeded, tmp_path):
    connection, _ = seeded
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        before = _source_rows(cur)
    stored, revision = _published(connection)
    assert (stored["outcome"], stored["title_o"]) == ("successful", TITLES[ORIGINAL])

    result = _save(connection, {"outcome": "failed", "title_o": "A <i>corrected</i> title"}, 0)
    assert result["changed_fields"] == ["outcome", "title_o"] and result["published"] is True

    # The FLoRA table, and the revision the public API's cache keys on.
    stored, moved = _published(connection)
    assert (stored["outcome"], stored["title_o"]) == ("failed", "A corrected title")
    assert stored["_meta"]["record_version"] == 2 and moved > revision

    # The tab, and the log: who, what (before and after), the note.
    record, editing = _read(connection)
    assert (record["outcome"], record["edited_fields"]) == ("failed", "outcome title_o")
    [change] = editing["history"]
    assert (change["change_id"], change["admin_handle"], change["action"], change["note"]) == \
        (result["change_id"], "luke", "edit", "checked against the paper")
    assert change["changes"] == {"outcome": ["successful", "failed"],
                                 "title_o": [TITLES[ORIGINAL], "A corrected title"]}
    assert change["corrections"] == {"outcome": "failed", "title_o": "A corrected title"}
    corrected = {f["field"]: f["edit"] for f in editing["fields"] if f.get("edit")}
    assert corrected["outcome"]["derived"] == "successful"
    assert editing["version"] == result["version"]

    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        assert _source_rows(cur) == before

    # The next pipeline run rebuilds flora_data from the sources and keeps the change:
    # the row it would write is exactly the one already written.
    stats = _pipeline(connection)
    assert (stats["updated"], stats["unchanged"]) == (0, 1)
    assert _published(connection)[0]["outcome"] == "failed"

    # So does the CSV a standalone transform writes.
    output = tmp_path / "flora.csv"
    transform_sources.run(output)
    [row] = list(csv.DictReader(io.StringIO(output.read_text(encoding="utf-8-sig"))))
    assert (row["outcome"], row["title_o"]) == ("failed", "A corrected title")


def _stale_snapshot(connection):
    """What a pipeline run has in hand when it got going: the revision and its build."""
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        revision = flora_store.dataset_revision(cur)
        frame = flora_edits.apply(cur, flora_registry.attach_ids(
            cur, transform_sources.build(cur, verbose=False)))
    return revision, transform_sources.to_output_shape(transform_sources.drop_untitled(frame))


def test_a_pipeline_run_that_started_before_a_change_keeps_it_and_finishes(seeded):
    """The change rewrote its row while the run built; the run keeps that row rather
    than overwriting it, and does not fail. The next run stores it normally."""
    connection, _ = seeded
    revision, stale = _stale_snapshot(connection)
    _save(connection, {"outcome": "failed"}, 0)
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        stats = flora_store.materialize(cur, stale, expected_revision=revision)
    assert (stats["preserved"], stats["updated"], stats["retired"]) == (1, 0, 0)
    stored, _ = _published(connection)
    assert (stored["outcome"], stored["_meta"]["active"]) == ("failed", True)
    assert _pipeline(connection)["unchanged"] == 1


def test_another_snapshot_stored_meanwhile_still_stops_a_run(seeded):
    connection, _ = seeded
    revision, stale = _stale_snapshot(connection)
    with connection, connection.cursor() as cur:
        cur.execute("UPDATE flora_data_metadata SET snapshot_sha256 = 'another run', "
                    "updated_at = NOW()")
    with pytest.raises(ValueError, match="changed during this run"):
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            flora_store.materialize(cur, stale, expected_revision=revision)


PREPRINT = "10.31234/osf.io/abcde"
PUBLISHED = "10.9999/report.published"


def _two_reports(connection, first_doi, second_doi):
    """Two replications of one original; the second's DOI is the one corrected."""
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        add_source(cur, {"doi_o": ORIGINAL, "doi_r": first_doi, "outcome": "successful"},
                   "REPL-000001")
        add_source(cur, {"doi_o": ORIGINAL, "doi_r": "10.9999/report.other",
                         "outcome": "failed"}, "REPL-000002")
        for doi, title in ((ORIGINAL, "The original study"),
                           (PUBLISHED, "A registered replication of the anchoring effect"),
                           (PREPRINT, "A registered replication of the anchoring effect"),
                           ("10.9999/report.other", "Something else entirely about memory")):
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, title))
    _pipeline(connection)


def _live(connection):
    with connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM flora_data WHERE retired_at IS NULL ORDER BY id")
        published = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT flora_id FROM flora_records WHERE retired_at IS NULL ORDER BY flora_id")
        return published, [row[0] for row in cur.fetchall()]


@pytest.mark.parametrize("first, corrected", [
    (PUBLISHED, PREPRINT),      # the corrected record would have been dropped as the preprint
    (PREPRINT, PUBLISHED),      # the OTHER record would have been dropped as the preprint
])
def test_a_corrected_doi_never_makes_a_record_disappear(local_database, offline, first, corrected):
    """Preprint/publication dedup never sees a corrected identifier, so a correction
    can neither drop the record it is on nor another one; and it can be undone."""
    _two_reports(local_database, first, "10.9999/report.other")
    expected = (["REPL-000001", "REPL-000002"], ["REPL-000001", "REPL-000002"])
    result = _save(local_database, {"doi_r": corrected}, 0, record="REPL-000002")
    assert _read(local_database, "REPL-000002")[0]["doi_r"] == corrected
    assert _read(local_database, "REPL-000001")[0] is not None
    _pipeline(local_database)
    assert _live(local_database) == expected
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        flora_service.restore_record(cur, "REPL-000002", 0, result["version"], LUKE, "")
    assert _read(local_database, "REPL-000002")[0]["doi_r"] == "10.9999/report.other"


def test_a_correction_that_duplicates_another_record_is_kept_and_flagged(local_database, offline):
    """Both stay, the other record's outcome is untouched, and the save names it."""
    _two_reports(local_database, PUBLISHED, "10.9999/report.other")
    result = _save(local_database, {"doi_r": PUBLISHED}, 0, record="REPL-000002")
    [warning] = result["warnings"]
    assert warning.startswith("REPL-000001 has the same original and replication identifiers")
    _pipeline(local_database)
    assert _live(local_database)[0] == ["REPL-000001", "REPL-000002"]
    assert _published(local_database, "REPL-000001")[0]["outcome"] == "successful"
    assert _published(local_database, "REPL-000002")[0]["outcome"] == "failed"


def test_a_corrected_doi_follows_the_record_through_a_survivor_change(local_database, offline):
    """On the run where a duplicate ruling promotes another source row, the registry
    still names the old one; the correction must reach the new survivor anyway, with
    the corrected paper's metadata."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        first = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "successful"},
                           "REPL-000001")
        second = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT, "outcome": "successful"},
                            "REPL-000002")
        for doi, title in TITLES.items():
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, title))
    _pipeline(local_database)
    _save(local_database, {"doi_r": FIXED}, 0)
    with local_database, local_database.cursor() as cur:
        cur.execute("UPDATE source_records SET duplicate_status = 'duplicate', duplicate_of = %s, "
                    "updated_at = NOW() WHERE record_id = %s", (second, first))
    _pipeline(local_database)
    stored, _ = _published(local_database)
    assert (stored["doi_r"], stored["title_r"]) == (FIXED, TITLES[FIXED])
    assert stored["source_display_id"] == "REPL-000002"   # re-pointed, same published id


def test_a_correction_onto_an_excluded_doi_is_refused_and_a_later_exclusion_applies(seeded):
    connection, _ = seeded
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        add_source(cur, {"doi_o": ORIGINAL, "doi_r": "10.9999/third", "outcome": "mixed"},
                   "REPL-000002")
        cur.execute("INSERT INTO work_metadata (doi, title) VALUES ('10.9999/third', 'Third')")
        cur.execute("INSERT INTO transform_exclusions (doi_r, reason) VALUES (%s, 'commentary')",
                    ("10.9999/not.a.replication",))
    _pipeline(connection)
    with pytest.raises(ValueError, match=r"exclusion list.*\(commentary\)"):
        _save(connection, {"doi_r": "10.9999/not.a.replication"}, 0)
    # Excluded after the correction was made: the exclusion is the decision.
    _save(connection, {"doi_r": FIXED}, 0)
    with connection, connection.cursor() as cur:
        cur.execute("INSERT INTO transform_exclusions (doi_r, reason) VALUES (%s, 'withdrawn')",
                    (FIXED,))
    flora_service.invalidate()
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        assert flora_service.get_record(cur, "REPL-000001") is None
    _pipeline(connection)
    assert _live(connection) == (["REPL-000002"], ["REPL-000002"])


def test_the_editor_is_told_the_derived_value_the_build_derives(seeded):
    """url_o is filled from the DOI's landing page late in the join; typing that
    value back drops the correction instead of storing a copy."""
    connection, _ = seeded
    first = _save(connection, {"url_o": "https://example.org/landing"}, 0)
    _, editing = _read(connection)
    derived = {f["field"]: f.get("edit") for f in editing["fields"]}["url_o"]["derived"]
    assert derived == "https://doi.org/" + ORIGINAL
    back = _save(connection, {"url_o": derived}, first["version"])
    assert back["reverted_fields"] == ["url_o"]
    assert _read(connection)[0]["edited_fields"] is None


def test_the_daily_count_includes_rows_titled_by_a_correction(seeded):
    connection, _ = seeded
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        add_source(cur, {"doi_o": "10.9999/untitled.o", "doi_r": "10.9999/untitled.r",
                         "outcome": "mixed"}, "REPL-000002")
    _pipeline(connection)
    _save(connection, {"title_o": "An original", "title_r": "A replication"}, 0,
          record="REPL-000002")
    _pipeline(connection)
    with connection, connection.cursor() as cur:
        cur.execute("SELECT total_rows FROM flora_dataset_history WHERE recorded_on = CURRENT_DATE")
        assert cur.fetchone()[0] == 2


def test_a_rolled_back_save_never_leaves_the_tab_showing_it(seeded):
    """The cache signature names the visible changes exactly, so a frame built inside
    a save that then rolled back is never served for a committed state that happens
    to have as many changes with the same newest id."""
    connection, _ = seeded
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        add_source(cur, {"doi_o": ORIGINAL, "doi_r": FIXED, "outcome": "successful"}, "REPL-000002")
        add_source(cur, {"doi_o": ORIGINAL, "doi_r": "10.9999/third", "outcome": "successful"},
                   "REPL-000003")
        cur.execute("INSERT INTO work_metadata (doi, title) VALUES ('10.9999/third', 'Third')")
    _pipeline(connection)
    dsn = connection.dsn
    in_flight, rolled_back, committed = (psycopg2.connect(dsn) for _ in range(3))

    def change(conn, export_id):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO flora_record_changes (export_id, admin_handle, action, "
                        "changes, corrections) VALUES (%s, 'x', 'edit', '{}', "
                        "'{\"outcome\": \"failed\"}')", (export_id,))

    try:
        change(in_flight, "REPL-000001")
        change(rolled_back, "REPL-000002")
        change(committed, "REPL-000003")
        committed.commit()
        with rolled_back.cursor(cursor_factory=RealDictCursor) as cur:
            flora_service.dataset(cur)              # the save's own rebuild fills the cache
        rolled_back.rollback()
        in_flight.commit()
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
            served = flora_service.dataset(cur).set_index("export_id")["outcome"].to_dict()
    finally:
        for conn in (in_flight, rolled_back, committed):
            conn.close()
    assert served == {"REPL-000001": "failed", "REPL-000002": "successful",
                      "REPL-000003": "failed"}


def test_a_change_survives_a_change_to_the_source(seeded):
    connection, record_id = seeded
    _save(connection, {"outcome": "failed"}, 0)
    with connection, connection.cursor() as cur:
        # What a sync writes when the sheet changes.
        cur.execute("UPDATE source_records SET outcome = 'mixed', updated_at = NOW() "
                    "WHERE record_id = %s", (record_id,))
    record, editing = _read(connection)
    assert record["outcome"] == "failed"
    assert {f["field"]: f.get("edit") for f in editing["fields"]}["outcome"]["derived"] == "mixed"
    _pipeline(connection)
    assert _published(connection)[0]["outcome"] == "failed"


def test_a_corrected_doi_brings_its_own_metadata_and_keeps_the_id(seeded):
    connection, _ = seeded
    result = _save(connection, {"doi_r": "https://doi.org/" + FIXED.upper()}, 0)
    record, editing = _read(connection)
    assert (record["flora_id"], record["doi_r"]) == ("REPL-000001", FIXED)
    assert record["title_r"] == TITLES[FIXED]
    assert {f["field"]: f.get("edit") for f in editing["fields"]}["doi_r"]["derived"] == REPORT
    # Everything that followed from the DOI reached the FLoRA table too.
    stored, _ = _published(connection)
    assert result["published"] and (stored["doi_r"], stored["title_r"]) == (FIXED, TITLES[FIXED])
    assert stored["doi_r_hash"] == transform_sources.doi_hash(FIXED)
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        assert FIXED in enrich_works.dois_in_product(cur)
        stats = flora_registry.refresh(cur, verbose=False)
    assert (stats["assigned"], stats["retired"]) == (0, 0)


def test_any_change_in_the_log_can_be_restored(seeded):
    connection, _ = seeded
    first = _save(connection, {"outcome": "failed"}, 0)
    second = _save(connection, {"outcome": "mixed", "title_o": "Second title"}, first["version"])
    with pytest.raises(flora_service.VersionConflict):
        _save(connection, {"outcome": "uninformative"}, first["version"])

    back = _restore(connection, first["change_id"], second["version"])
    assert (back["changed_fields"], back["reverted_fields"]) == (["outcome"], ["title_o"])
    record, editing = _read(connection)
    assert (record["outcome"], record["title_o"]) == ("failed", TITLES[ORIGINAL])
    stored, _ = _published(connection)
    assert (back["published"], stored["outcome"], stored["title_o"]) == \
        (True, "failed", TITLES[ORIGINAL])
    assert editing["history"][0]["action"] == "restore"
    assert editing["history"][0]["restored_from"] == first["change_id"]

    original = _restore(connection, 0, back["version"])
    assert original["reverted_fields"] == ["outcome"]
    record, editing = _read(connection)
    assert (record["outcome"], record["edited_fields"]) == ("successful", None)
    assert _published(connection)[0]["outcome"] == "successful"
    assert [c["action"] for c in editing["history"]] == ["restore", "restore", "edit", "edit"]

    # Restoring a restore undoes it.
    _restore(connection, back["change_id"], original["version"])
    assert _read(connection)[0]["outcome"] == "failed"


def test_the_log_is_append_only(seeded):
    connection, _ = seeded
    _save(connection, {"outcome": "failed"}, 0)
    for statement in ("UPDATE flora_record_changes SET note = 'rewritten'",
                      "DELETE FROM flora_record_changes",
                      "TRUNCATE flora_record_changes"):
        with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
            with connection, connection.cursor() as cur:
                cur.execute(statement)


def test_a_row_kept_out_of_the_export_can_be_corrected_into_it(seeded):
    """No metadata, so no titles: the row is in the FLoRA tab but not the table."""
    connection, _ = seeded
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        add_source(cur, {"doi_o": "10.9999/untitled.o", "doi_r": "10.9999/untitled.r",
                         "outcome": "mixed"}, "REPL-000002")
    _pipeline(connection)
    assert _published(connection, "REPL-000002")[0] is None

    result = _save(connection, {"title_o": "An original", "title_r": "A replication"}, 0,
                   record="REPL-000002")
    assert result["published"] is False             # no row in flora_data to rewrite yet
    stats = _pipeline(connection)
    assert stats["inserted"] == 1
    stored, _ = _published(connection, "REPL-000002")
    assert (stored["title_o"], stored["title_r"]) == ("An original", "A replication")


def test_a_reproduction_is_corrected_through_its_axes(local_database, offline):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o, doi_r,
                   outcome_computation, outcome_robustness, outcome_robustness_quote)
               VALUES ('reproductions', %s, 'REPR-000001', 'reproduction', %s, %s,
                       'not checked', 'robust', 'Robust to every check.')""",
            (str(uuid.uuid4()), ORIGINAL, REPORT))
        flora_registry.refresh(cur, verbose=False)
    flora_service.invalidate()
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        with pytest.raises(ValueError, match="Cannot be corrected on a reproduction: outcome"):
            flora_service.update_record(cur, "REPR-000001", {"outcome": "failed"}, [], 0, LUKE)
        flora_service.update_record(cur, "REPR-000001", {
            "outcome_computation": "computationally reproducible",
            "outcome_computational_quote": "The code ran."}, [], 0, LUKE)
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record = flora_service.get_record(cur, "REPR-000001")
    assert record["outcome"] == "computationally reproducible, robust"
    assert record["outcome_quote"] == "The code ran. || Robust to every check."


def test_the_source_panel_points_at_the_flora_record_to_correct(seeded):
    connection, record_id = seeded
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        detail = source_records_service.get_record(cur, record_id)
    assert [(f["flora_id"], f["role"], f["retired_at"]) for f in detail["flora"]] == \
        [("REPL-000001", "primary", None)]


def test_earlier_edits_to_the_source_rows_are_shown_with_the_record(seeded):
    connection, record_id = seeded
    with connection, connection.cursor() as cur:
        cur.execute("INSERT INTO source_record_edits (record_id, field, old_value, new_value, "
                    "edited_by, note) VALUES (%s, 'outcome', 'mixed', 'successful', 'ana', 'old')",
                    (record_id,))
    _, editing = _read(connection)
    assert [(e["display_id"], e["field"], e["edited_by"]) for e in editing["source_edits"]] == \
        [("REPL-000001", "outcome", "ana")]
