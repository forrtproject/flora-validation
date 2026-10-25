"""A duplicate ruling keeps the reviewer's reason in the edit log, as a correction's
note does, and names records by their display ids. Opt-in PostgreSQL test:
FLORA_TEST_DATABASE_URL."""
import pytest

from tests.test_preparation_database import add_source, local_database  # noqa: F401

ORIGINAL, REPORT = "10.1037/xge0000001", "10.1016/j.replication.2020"


def _edits(cur, record_id):
    cur.execute("SELECT new_value, edited_by, note FROM source_record_edits "
                "WHERE record_id = %s AND field = 'duplicate_status' ORDER BY edited_at",
                (record_id,))
    return [tuple(row.values()) for row in cur.fetchall()]


def _pair(cur):
    kept = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT}, "REPL-000001")
    copy = add_source(cur, {"doi_o": ORIGINAL, "doi_r": REPORT}, "REPL-000002")
    return kept, copy


def test_the_reason_goes_into_both_log_rows_with_display_ids(local_database):
    from psycopg2.extras import RealDictCursor
    import source_records_service as service
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            kept, copy = _pair(cur)
            result = service.resolve_duplicate(cur, copy, "duplicate", "hamid", kept,
                                               "  the protocol of the same replication  ")
            assert result["note"] == "the protocol of the same replication"
            assert _edits(cur, copy) == [
                ("duplicate", "hamid", "duplicate of REPL-000001: the protocol of the same replication")]
            assert _edits(cur, kept) == [
                ("distinct", "hamid", "kept as the survivor of REPL-000002: the protocol of the same "
                                      "replication")]


def test_without_a_reason_the_log_still_names_the_records(local_database):
    from psycopg2.extras import RealDictCursor
    import source_records_service as service
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            kept, copy = _pair(cur)
            service.resolve_duplicate(cur, copy, "duplicate", "hamid", kept, "   ")
            assert _edits(cur, copy) == [("duplicate", "hamid", "duplicate of REPL-000001")]
            assert _edits(cur, kept) == [("distinct", "hamid", "kept as the survivor of REPL-000002")]
    # A later ruling is a later request: its own transaction, so its own edited_at.
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            service.resolve_duplicate(cur, copy, "distinct", "hamid", None, "two different reports")
            assert _edits(cur, copy)[-1] == ("distinct", "hamid", "two different reports")


def test_a_reason_over_the_limit_is_refused_before_anything_is_written(local_database):
    from psycopg2.extras import RealDictCursor
    import source_records_service as service
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            kept, copy = _pair(cur)
            with pytest.raises(ValueError, match="longer than 500"):
                service.resolve_duplicate(cur, copy, "duplicate", "hamid", kept, "x" * 501)
            assert _edits(cur, copy) == []
            cur.execute("SELECT duplicate_status FROM source_records WHERE record_id = %s", (copy,))
            assert cur.fetchone()["duplicate_status"] is None


def test_the_duplicates_panel_shows_the_latest_reason(local_database):
    from psycopg2.extras import RealDictCursor
    import source_records_service as service
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            kept, copy = _pair(cur)
            # The sheet sync sets the fingerprint that groups them; add_source does not.
            cur.execute("UPDATE source_records SET content_fingerprint = 'same-paper' "
                        "WHERE record_id IN (%s, %s)", (kept, copy))
            service.resolve_duplicate(cur, copy, "duplicate", "hamid", kept, "same report twice")
            groups = service.duplicate_groups(cur, unresolved_only=False)["groups"]
            notes = {m["display_id"]: m["duplicate_note"] for g in groups for m in g["members"]}
            assert notes == {"REPL-000001": "kept as the survivor of REPL-000002: same report twice",
                             "REPL-000002": "duplicate of REPL-000001: same report twice"}
