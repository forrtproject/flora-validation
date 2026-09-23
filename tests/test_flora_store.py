"""Prepared-table contract, including the complete supplied CSV on PostgreSQL."""
import csv
import hashlib
import io

import pandas as pd
from psycopg2.extras import RealDictCursor
import pytest

import final_export
import flora_store
from tests.test_preparation_database import local_database, add_source


def snapshot(*identifiers, **extras):
    rows = []
    for identifier in identifiers:
        row = {column: None for column in flora_store.CSV_COLUMNS}
        row.update(id=identifier, id_md5=hashlib.md5(identifier.encode()).hexdigest(),
                   title_o="Memory and attention", title_r="An independent replication",
                   doi_o="10.1234/original", doi_r="10.1234/replication", **extras)
        rows.append(row)
    return pd.DataFrame(rows, columns=[*flora_store.CSV_COLUMNS, *extras.keys()])


def test_reference_roundtrip_is_exact_and_idempotent(local_database, tmp_path):
    source = tmp_path / "reference.csv"
    final_export.replay_reference(source)
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            first = flora_store.materialize(cur, source)
            assert first["rows"] == first["inserted"] == 2914
            assert first["snapshot_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
            assert flora_store.export_csv(cur).encode("utf-8") == source.read_bytes()
            before = flora_store.get_record(cur, "FLORA-000001")
            second = flora_store.materialize(cur, source)
            assert second["unchanged"] == 2914
            assert second["inserted"] == second["updated"] == second["retired"] == 0
            assert flora_store.get_record(cur, before["id"]) == before
            assert flora_store.get_record_by_hash(cur, before["id_md5"].upper()) == before
            page = flora_store.list_records(cur, limit=2, offset=2912)
            assert page["total"] == 2914
            assert [row["id"] for row in page["records"]] == ["FLORA-002913", "FLORA-002914"]


def test_corrections_retirement_reappearance_and_append_keep_identity(local_database):
    initial = snapshot("FLORA-000001", "NEW-first")
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            flora_store.materialize(cur, initial)
            before = flora_store.get_record(cur, "FLORA-000001")
            first_new = flora_store.get_record(cur, "NEW-first")
            corrected = snapshot("NEW-second", "FLORA-000001", "NEW-first")
            corrected.loc[1, "title_o"] = "Corrected scientific title"
            corrected.loc[1, "doi_o"] = "10.1234/corrected"
            stats = flora_store.materialize(cur, corrected)
            assert stats["inserted"] == stats["updated"] == stats["unchanged"] == 1
            after = flora_store.get_record_by_hash(cur, before["id_md5"])
            assert after["id"] == before["id"]
            assert after["title_o"] == "Corrected scientific title"
            assert after["_meta"]["export_position"] == before["_meta"]["export_position"]
            assert after["_meta"]["created_at"] == before["_meta"]["created_at"]
            assert after["_meta"]["record_version"] == 2
            assert [row["id"] for row in flora_store.list_records(cur)["records"]] == [
                "FLORA-000001", "NEW-first", "NEW-second"]
            assert first_new["_meta"]["export_position"] == 2915
            assert flora_store.get_record(cur, "NEW-second")["_meta"]["export_position"] == 2916

            stats = flora_store.materialize(cur, corrected[corrected.id != "FLORA-000001"])
            assert stats["retired"] == 1
            retired = flora_store.get_record_by_hash(cur, before["id_md5"])
            assert retired["id"] == before["id"] and not retired["_meta"]["active"]
            assert retired["_meta"]["record_version"] == 3
            assert flora_store.list_records(cur)["total"] == 2
            assert flora_store.list_records(cur, include_retired=True)["total"] == 3
            stats = flora_store.materialize(cur, corrected)
            assert stats["reactivated"] == 1
            returned = flora_store.get_record(cur, before["id"])
            assert returned["id_md5"] == before["id_md5"]
            assert returned["_meta"]["export_position"] == 1
            assert returned["_meta"]["record_version"] == 4
            assert returned["_meta"]["active"]


def test_search_extras_and_transaction_rollback(local_database):
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            frame = snapshot("NEW-a", reviewer_note="multiline\n\"quoted\"; café")
            flora_store.materialize(cur, frame)
            assert flora_store.list_records(cur, title="memory attention")["total"] == 1
            assert flora_store.list_records(cur, title="independent replication")["total"] == 1
            assert flora_store.list_records(cur, title="unrelated")["total"] == 0
            for doi in ["https://doi.org/10.1234/ORIGINAL", "doi:10.1234/replication"]:
                assert flora_store.list_records(cur, doi=doi)["records"][0]["id"] == "NEW-a"
            frame.loc[0, "doi_o"] = " https://dx.doi.org/10.1234/ORIGINAL "
            frame.loc[0, "doi_r"] = "doi: 10.1234/Replication"
            flora_store.materialize(cur, frame)
            assert flora_store.list_records(cur, doi="10.1234/original")["total"] == 1
            assert flora_store.list_records(cur, doi="10.1234/replication")["total"] == 1
            assert flora_store.get_record(cur, "NEW-a")["doi_o"] == frame.loc[0, "doi_o"]
            assert flora_store.list_records(cur, doi="10.1234/absent")["total"] == 0
            assert flora_store.get_record(cur, "absent") is None
            assert flora_store.get_record_by_hash(cur, "0" * 32) is None
            frame["appended"] = "tail"
            flora_store.materialize(cur, frame)
            exported = list(csv.reader(io.StringIO(flora_store.export_csv(cur).lstrip("\ufeff"))))
            assert exported[0] == [*flora_store.CSV_COLUMNS, "reviewer_note", "appended"]
            assert exported[1][-2:] == ["multiline\n\"quoted\"; café", "tail"]
            before = flora_store.export_csv(cur)
    with pytest.raises(RuntimeError, match="cancel transaction"):
        with local_database:
            with local_database.cursor(cursor_factory=RealDictCursor) as cur:
                flora_store.materialize(cur, snapshot("NEW-b"))
                raise RuntimeError("cancel transaction")
    with local_database.cursor(cursor_factory=RealDictCursor) as cur:
        assert flora_store.export_csv(cur) == before
        assert flora_store.get_record(cur, "NEW-b") is None


def test_empty_snapshot_requires_deliberate_opt_in(local_database):
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            flora_store.materialize(cur, snapshot("NEW-a"))
            empty = pd.DataFrame(columns=flora_store.CSV_COLUMNS)
            with pytest.raises(ValueError, match="empty snapshot"):
                flora_store.materialize(cur, empty)
            assert flora_store.list_records(cur)["total"] == 1
            assert flora_store.materialize(cur, empty, allow_empty=True)["retired"] == 1
            assert flora_store.list_records(cur)["total"] == 0
            assert flora_store.get_record(cur, "NEW-a") is not None


def test_table_uses_registry_positions_when_previously_untitled_row_appears(local_database):
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            for index, identifier in enumerate(["NEW-a", "NEW-b"], start=2915):
                source_id = add_source(cur, {}, identifier)
                cur.execute("""INSERT INTO flora_records
                    (flora_id, primary_source_record_id, export_id, export_position)
                    VALUES (%s, %s, %s, %s)""", (identifier, source_id, identifier, index))
            # A is registered but lacks a title, so B is initially the only export.
            flora_store.materialize(cur, snapshot("NEW-b"))
            assert flora_store.get_record(cur, "NEW-b")["_meta"]["export_position"] == 2916
            flora_store.materialize(cur, snapshot("NEW-a", "NEW-b"))
            assert [row["id"] for row in flora_store.list_records(cur)["records"]] == ["NEW-a", "NEW-b"]
            assert flora_store.get_record(cur, "NEW-a")["_meta"]["export_position"] == 2915
            flora_store.materialize(cur, snapshot("NEW-a", "NEW-b", "NEW-c"))
            assert flora_store.get_record(cur, "NEW-c")["_meta"]["export_position"] == 2917


def test_registry_reserves_already_imported_ids_and_positions(local_database):
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            flora_store.materialize(cur, snapshot("IMPORTED"))
            source_id = add_source(cur, {}, "IMPORTED")
            cur.execute("INSERT INTO flora_records (flora_id, primary_source_record_id) VALUES (%s, %s)",
                        ("IMPORTED", source_id))
            final_export.register_order(cur, pd.DataFrame([{"source_record_id": source_id}]))
            cur.execute("SELECT export_id, export_position FROM flora_records")
            registered = cur.fetchone()
            assert registered["export_id"] == "NEW-" + source_id
            assert registered["export_position"] == 2916
            assert flora_store.get_record(cur, "IMPORTED")["_meta"]["export_position"] == 2915


@pytest.mark.parametrize("case", ["hash", "duplicate_id", "blank_id", "space_id", "order", "reserved", "duplicate_header"])
def test_malformed_snapshot_rejected_before_database_access(case):
    frame = snapshot("NEW-a", "NEW-b")
    if case == "hash":
        frame.loc[0, "id_md5"] = "0" * 32
    elif case == "duplicate_id":
        frame.loc[1, ["id", "id_md5"]] = frame.loc[0, ["id", "id_md5"]]
    elif case == "blank_id":
        frame.loc[0, "id"] = ""
    elif case == "space_id":
        frame.loc[0, "id"] = " NEW-a "
    elif case == "order":
        frame = frame[frame.columns[::-1]]
    elif case == "reserved":
        frame["export_position"] = 1
    elif case == "duplicate_header":
        frame.columns = [*frame.columns[:-1], "title_o"]
    with pytest.raises(ValueError):
        flora_store.materialize(None, frame)


@pytest.mark.parametrize("bad_hash", [None, "", "short", "g" * 32, "a" * 33])
def test_hash_lookup_requires_complete_md5(bad_hash):
    with pytest.raises(ValueError, match="32-character"):
        flora_store.get_record_by_hash(None, bad_hash)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"limit": "5"}])
def test_pagination_rejects_invalid_bounds(params):
    with pytest.raises(ValueError):
        flora_store.list_records(None, **params)
