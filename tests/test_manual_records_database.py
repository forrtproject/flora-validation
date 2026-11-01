"""Records added by hand in the FLoRA tab, against a real PostgreSQL (opt-in).

Set FLORA_TEST_DATABASE_URL to an isolated localhost PostgreSQL admin database, as
for tests/test_preparation_database.py; each test runs in a throwaway database with
db_schema.sql applied twice. Without it every test here is skipped.
"""
import uuid

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

import flora_registry
import flora_service
import manual_records
import transform_sources
import validate_flora
from extractor_vocab import derive_reproduction_outcome
from tests.test_preparation_database import local_database  # noqa: F401

ADMIN = {"id": 1, "handle": "sophie"}
OTHER = {"id": 2, "handle": "luke"}
REPLICATION = {"type": "replication", "doi_o": "10.7/orig", "doi_r": "10.7/rep",
               "outcome": "failed", "outcome_quote": "It did not replicate.",
               "out_quote_source": "abstract"}
NOTE = "From the 2026 review of replications in economics."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No metadata lookups, and no confirmed preprint rulings from the repository's
    file: the build here is the rows the tests write."""
    import preprint_dedup
    monkeypatch.setattr("bibliographic_helpers.request", lambda *a, **kw: None)
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [])


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def run(conn, work, *args, **kwargs):
    """One committed transaction, as a request is."""
    with conn, _cursor(conn) as cur:
        return work(cur, *args, **kwargs)


def add_source(conn, display_id, source="replications", **values):
    row = {"source": source, "sheet_row_id": str(uuid.uuid4()), "display_id": display_id,
           "type": "replication", "doi_o": "10.7/orig", "doi_r": "10.7/rep", **values}
    with conn, _cursor(conn) as cur:
        cur.execute(f"INSERT INTO source_records ({', '.join(row)}) "
                    f"VALUES ({', '.join(['%s'] * len(row))}) RETURNING record_id::text AS id",
                    list(row.values()))
        return cur.fetchone()["id"]


def source_row(conn, display_id):
    with conn, _cursor(conn) as cur:
        cur.execute("SELECT *, record_id::text AS record_id FROM source_records WHERE display_id = %s",
                    (display_id,))
        return dict(cur.fetchone())


def built(conn):
    flora_service.invalidate()
    with conn, _cursor(conn) as cur:
        return transform_sources.build(cur, verbose=False, metadata={})


def refresh(conn):
    with conn, _cursor(conn) as cur:
        flora_registry.refresh(cur, verbose=False)


def flora_ids(conn):
    with conn, _cursor(conn) as cur:
        cur.execute("SELECT flora_id, retired_at IS NOT NULL AS retired, "
                    "primary_source_record_id::text AS sid FROM flora_records ORDER BY flora_id")
        return [dict(r) for r in cur.fetchall()]


def add(conn, values=REPLICATION, admin=ADMIN, **kwargs):
    saved = run(conn, manual_records.create, dict(values), NOTE, kwargs.pop("keep_distinct", False),
                admin, **kwargs)
    flora_service.invalidate()
    return saved


def answer(conn, manual_id, **kwargs):
    flora_service.invalidate()
    return run(conn, manual_records.saved, manual_id, **kwargs)


# ── schema ────────────────────────────────────────────────────────────────────

def test_entries_are_never_deleted_and_their_history_never_rewritten(local_database):
    conn = local_database
    saved = add(conn)
    for sql in ("DELETE FROM manual_records", "TRUNCATE manual_records CASCADE",
                "UPDATE manual_record_events SET note = 'x'", "DELETE FROM manual_record_events",
                "TRUNCATE manual_record_events"):
        with pytest.raises(psycopg2.errors.RaiseException):
            run(conn, lambda cur, q=sql: cur.execute(q))
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(conn, lambda cur: cur.execute(
            "UPDATE manual_records SET outcome_computation = 'not checked' WHERE manual_id = %s",
            (saved["manual_id"],)))


# ── a record of its own ───────────────────────────────────────────────────────

def test_a_new_record_is_a_source_row_at_once_and_gets_its_own_id_at_the_next_run(local_database):
    conn = local_database
    values = {**REPLICATION, "doi_o": "https://doi.org/10.7/ORIG", "abstract_r": "An abstract."}
    saved = add(conn, values)
    assert saved["display_id"] == "MAN-000001" and not saved["repeat"]
    row = source_row(conn, "MAN-000001")
    assert (row["source"], row["sheet_row_id"], row["type"]) == ("manual", saved["manual_id"], "replication")
    assert (row["doi_o"], row["doi_r"], row["outcome"]) == ("10.7/orig", "10.7/rep", "failed")
    assert row["content_fingerprint"], "the duplicate detector sees it"
    assert row["validation_status"] is None and row["outcome_computation"] is None
    assert row["raw"]["created_by"] == "sophie" and row["raw"]["note"] == NOTE

    frame = built(conn)
    mine = frame[frame["source_display_id"] == "MAN-000001"].to_dict("records")
    assert len(mine) == 1 and (mine[0]["source"], mine[0]["outcome"]) == ("manual", "failed")
    assert not validate_flora._check_sources(frame), "the nightly check knows the source"

    out = answer(conn, saved["manual_id"])
    assert out["state"]["code"] == "awaiting_id" and out["state"]["editable"]
    assert out["run_pipeline"] is True
    assert out["notice"].startswith("Saved as MAN-000001. A record of its own")

    # Before an ID it can be changed, and is projected again.
    run(conn, manual_records.update, saved["manual_id"], {**values, "year_r": "2021"},
        NOTE, 1, OTHER)
    assert source_row(conn, "MAN-000001")["year_r"] == "2021"
    assert source_row(conn, "MAN-000001")["version"] == 2
    entry = answer(conn, saved["manual_id"])
    assert entry["version"] == 2 and entry["updated_by"] == "luke"
    assert [e["action"] for e in entry["events"]] == ["edit", "create"]
    assert entry["events"][0]["changes"] == {"year_r": [None, "2021"]}
    assert entry["events"][0]["admin_handle"] == "luke"

    refresh(conn)
    assert [r["flora_id"] for r in flora_ids(conn)] == ["MAN-000001"]
    with conn, _cursor(conn) as cur:                      # its public id too (final_export)
        cur.execute("SELECT export_id FROM flora_records")
        assert cur.fetchone()["export_id"] == "MAN-000001"
    entry = answer(conn, saved["manual_id"])
    assert entry["state"]["code"] == "in_flora" and entry["state"]["flora_id"] == "MAN-000001"
    assert not entry["state"]["editable"] and entry["run_pipeline"] is False
    with pytest.raises(manual_records.ManualRecordError) as refused:
        run(conn, manual_records.update, saved["manual_id"], REPLICATION, NOTE, 2, ADMIN)
    assert refused.value.status == 409 and refused.value.detail["code"] == "frozen"


def test_withdraw_retires_the_id_and_restore_brings_the_same_row_and_id_back(local_database):
    conn = local_database
    # Another record: refresh() registers nothing, and retires nothing, from an empty build.
    add_source(conn, "REPL-000001", doi_r="10.7/another", outcome="failed")
    saved = add(conn)
    refresh(conn)
    with pytest.raises(manual_records.ManualRecordError) as refused:
        run(conn, manual_records.withdraw, saved["manual_id"], "", 1, ADMIN)
    assert refused.value.status == 422 and refused.value.detail["field"] == "reason"
    with pytest.raises(manual_records.ManualRecordError) as stale:
        run(conn, manual_records.withdraw, saved["manual_id"], "Wrong paper", 7, ADMIN)
    assert stale.value.detail["current_version"] == 1

    run(conn, manual_records.withdraw, "MAN-000001", "Wrong paper", 1, ADMIN)
    row = source_row(conn, "MAN-000001")
    assert row["deleted_at"] is not None and row["deleted_reason"] == "Withdrawn by sophie: Wrong paper"
    with conn, _cursor(conn) as cur:
        rows = transform_sources.load(cur)
        assert rows.empty or "MAN-000001" not in set(rows["display_id"])
    refresh(conn)
    assert [r["retired"] for r in flora_ids(conn)] == [True, False]
    entry = answer(conn, saved["manual_id"])
    assert entry["state"]["code"] == "withdrawn" and entry["status"] == "withdrawn"

    run(conn, manual_records.restore, "MAN-000001", "It was right after all", 2, ADMIN)
    row = source_row(conn, "MAN-000001")
    assert row["deleted_at"] is None and row["deleted_reason"] is None
    refresh(conn)
    assert flora_ids(conn)[0] == {"flora_id": "MAN-000001", "retired": False, "sid": row["record_id"]}
    entry = answer(conn, saved["manual_id"])
    assert [e["action"] for e in entry["events"]] == ["restore", "withdraw", "create"]
    assert entry["events"][1]["note"] == "Wrong paper"


# ── duplicates: warned about, then absorbed ───────────────────────────────────

def test_same_identifiers_are_absorbed_and_the_existing_record_keeps_its_id_and_values(local_database):
    conn = local_database
    repl = add_source(conn, "REPL-000001", outcome="successful", outcome_quote="It replicated.",
                      out_quote_source="abstract")
    refresh(conn)
    check = run(conn, manual_records.check, {**REPLICATION, "doi_r": "https://doi.org/10.7/REP"})
    assert check["placement"]["kind"] == "absorbed"
    assert check["placement"]["into_display_id"] == "REPL-000001"
    assert check["placement"]["mode"] == "same_identifiers"
    assert check["placement"]["into_flora_id"] == "REPL-000001"
    assert [m["display_id"] for m in check["matches"]["same_identifiers"]] == ["REPL-000001"]
    assert check["matches"]["same_identifiers"][0]["flora_id"] == "REPL-000001"
    assert "REPL-000001 keeps its FLoRA ID and values" in check["message"]

    # Saved without any tick: warned, not refused.
    saved = add(conn, {**REPLICATION, "abstract_r": "Only the entry has it."})
    out = answer(conn, saved["manual_id"])
    assert out["state"]["code"] == "absorbed" and out["state"]["into"] == "REPL-000001"
    assert out["state"]["editable"] and out["run_pipeline"] is False
    assert "No new FLoRA ID will appear" in out["notice"]

    frame = built(conn)
    [row] = frame.to_dict("records")
    assert row["source_display_id"] == "REPL-000001", "although MAN sorts before REPL"
    assert row["outcome"] == "successful" and row["outcome_quote"] == "It replicated."
    assert row["abstract_r"] == "Only the entry has it."
    assert row["outcome_conflict"] is None or row["outcome_conflict"] != row["outcome_conflict"]
    man = source_row(conn, "MAN-000001")["record_id"]
    assert frame.attrs["merged_record_ids"][repl] == [man]
    refresh(conn)
    assert [r["flora_id"] for r in flora_ids(conn)] == ["REPL-000001"]
    state = answer(conn, saved["manual_id"])["state"]
    assert (state["code"], state["flora_id"], state["editable"]) == ("absorbed", "REPL-000001", False), \
        "the registry knows it under REPL-000001: frozen"


def test_same_dois_with_one_link_are_absorbed_without_joining_quotes_or_mixing_outcomes(local_database):
    conn = local_database
    repl = add_source(conn, "REPL-000001", url_r="https://osf.io/report", outcome="successful",
                      outcome_quote="It replicated.", out_quote_source="abstract")
    saved = add(conn, {**REPLICATION, "abstract_r": "From the entry."})
    out = answer(conn, saved["manual_id"])
    assert out["state"]["code"] == "absorbed" and out["state"]["mode"] == "same_dois_one_link"
    assert "only one of the two has a replication link" in out["notice"]

    frame = built(conn)
    [row] = frame.to_dict("records")
    assert row["source_display_id"] == "REPL-000001"
    assert (row["outcome"], row["outcome_quote"]) == ("successful", "It replicated.")
    assert row["url_r"] == "https://osf.io/report" and row["abstract_r"] == "From the entry."
    assert frame.attrs["outcome_conflicts"] == []
    assert frame.attrs["merged_record_ids"][repl] == [source_row(conn, "MAN-000001")["record_id"]]


def test_kept_distinct_it_is_a_record_of_its_own_with_the_ruling_logged(local_database):
    conn = local_database
    add_source(conn, "REPL-000001", outcome="successful")
    check = run(conn, manual_records.check, REPLICATION, keep_distinct=True)
    assert check["placement"]["kind"] == "own"
    assert "ruled a different study from REPL-000001" in check["message"]
    saved = add(conn, keep_distinct=True)
    row = source_row(conn, "MAN-000001")
    assert (row["duplicate_status"], row["duplicate_reviewed_by"]) == ("distinct", "sophie")
    with conn, _cursor(conn) as cur:
        cur.execute("SELECT field, new_value, note FROM source_record_edits WHERE record_id = %s",
                    (row["record_id"],))
        assert [dict(r) for r in cur.fetchall()] == [{
            "field": "duplicate_status", "new_value": "distinct",
            "note": "Kept separate when added by hand: a different study from REPL-000001"}]
    assert sorted(built(conn)["source_display_id"]) == ["MAN-000001", "REPL-000001"]
    refresh(conn)
    assert [r["flora_id"] for r in flora_ids(conn)] == ["MAN-000001", "REPL-000001"]
    out = answer(conn, saved["manual_id"])
    assert out["state"]["code"] == "in_flora"


def test_a_tick_with_nothing_to_keep_apart_is_no_ruling(local_database):
    conn = local_database
    saved = add(conn, keep_distinct=True)
    assert source_row(conn, "MAN-000001")["duplicate_status"] is None
    # Nothing was kept apart, so the entry does not say it was; a later edit still can.
    entry = run(conn, manual_records.detail, saved["manual_id"])
    assert entry["keep_distinct"] is False
    add_source(conn, "REPL-000001", outcome="successful")
    changed = run(conn, manual_records.update, saved["manual_id"], dict(REPLICATION), NOTE,
                  entry["version"], ADMIN, keep_distinct=True)
    assert changed["changed"] is True
    assert source_row(conn, "MAN-000001")["duplicate_status"] == "distinct"
    entry = run(conn, manual_records.detail, saved["manual_id"])
    assert (entry["keep_distinct"], entry["version"]) == (True, 2)


def ruling(conn, display_id, status, of=None):
    import source_records_service
    of_id = source_row(conn, of)["record_id"] if of else None
    run(conn, source_records_service.resolve_duplicate, source_row(conn, display_id)["record_id"],
        status, "reviewer", of_id, "t")


def test_a_later_arrival_only_fills_the_blanks_of_a_hand_record_with_its_id(local_database):
    """Whoever was there first keeps the values: the hand record has its FLoRA ID, so a
    website-validated row arriving later is absorbed into it, as a hand row arriving
    second would be. Ruling the hand row distinct changes nothing about who holds the
    ID; ruling the newcomer distinct gives it a record of its own."""
    conn = local_database
    saved = add(conn)
    refresh(conn)
    add_source(conn, "VAL-000001", source="validated", outcome="successful",
               outcome_quote="It replicated.", abstract_r="From the newcomer.")
    refresh(conn)
    man, val = source_row(conn, "MAN-000001")["record_id"], source_row(conn, "VAL-000001")["record_id"]
    assert flora_ids(conn) == [{"flora_id": "MAN-000001", "retired": False, "sid": man}]
    [row] = built(conn).to_dict("records")
    assert (row["source_display_id"], row["outcome"], row["outcome_quote"]) == \
        ("MAN-000001", "failed", "It did not replicate.")
    assert row["abstract_r"] == "From the newcomer."
    out = answer(conn, saved["manual_id"])
    assert (out["state"]["code"], out["state"]["flora_id"], out["state"]["editable"]) == \
        ("in_flora", "MAN-000001", False)
    assert "rows with its identifiers that arrived later only fill its blanks" in out["state"]["message"]

    ruling(conn, "MAN-000001", "distinct")
    refresh(conn)
    assert flora_ids(conn) == [{"flora_id": "MAN-000001", "retired": False, "sid": man}], \
        "the newcomer never holds the hand record's ID"
    ruling(conn, "VAL-000001", "distinct")
    refresh(conn)
    assert sorted((r["flora_id"], r["sid"]) for r in flora_ids(conn)) == \
        [("MAN-000001", man), ("VAL-000001", val)]


def test_withdrawing_says_what_happens_to_the_record(local_database):
    """A hand record holding a later row: withdrawn, its record stays, built from that
    row, and the confirmation and the state say so; nothing is retired."""
    conn = local_database
    saved = add(conn)
    refresh(conn)
    add_source(conn, "REPL-000001", outcome="successful")
    refresh(conn)
    effect = answer(conn, saved["manual_id"])["state"]["withdraw_effect"]
    assert effect.startswith("MAN-000001 stays in FLoRA, built from the rows merged into it")
    run(conn, manual_records.withdraw, saved["manual_id"], "Entered twice", 1, ADMIN)
    assert "MAN-000001 stays in FLoRA, built from the rows merged into it" in \
        answer(conn, saved["manual_id"])["state"]["message"]
    refresh(conn)
    [record] = flora_ids(conn)
    assert (record["flora_id"], record["retired"], record["sid"]) == \
        ("MAN-000001", False, source_row(conn, "REPL-000001")["record_id"])
    assert "MAN-000001 stays in FLoRA; what this entry filled leaves it" in \
        answer(conn, saved["manual_id"])["state"]["message"]
    # Alone, withdrawing retires the ID.
    alone = add(conn, {**REPLICATION, "doi_r": "10.7/alone"})
    refresh(conn)
    assert answer(conn, alone["manual_id"])["state"]["withdraw_effect"].startswith(
        "MAN-000002 leaves FLoRA at the next pipeline run")


def test_a_distinct_ruling_frees_an_absorbed_entry_until_its_own_id(local_database):
    """Absorbed into REPL-000001 and frozen; ruled a different study in Source Records,
    it is no longer part of that record, so it is not frozen by it (and its editor is
    not named) until the next run gives it its own ID."""
    conn = local_database
    add_source(conn, "REPL-000001", outcome="successful")
    saved = add(conn)
    refresh(conn)
    assert answer(conn, saved["manual_id"])["state"]["frozen_by"] == "REPL-000001"
    ruling(conn, "MAN-000001", "distinct")
    state = answer(conn, saved["manual_id"])["state"]
    assert (state["code"], state["editable"], state["frozen_by"], state["flora_id"]) == \
        ("awaiting_id", True, None, None)
    assert "still part of REPL-000001 until then" in state["message"]
    run(conn, manual_records.update, saved["manual_id"], {**REPLICATION, "year_r": "2020"}, NOTE, 1, ADMIN)
    refresh(conn)
    state = answer(conn, saved["manual_id"])["state"]
    assert (state["code"], state["frozen_by"]) == ("in_flora", "MAN-000001")
    with pytest.raises(manual_records.ManualRecordError) as refused:
        run(conn, manual_records.update, saved["manual_id"], REPLICATION, NOTE, 2, ADMIN)
    assert refused.value.detail["flora_id"] == "MAN-000001"


def test_a_record_without_a_title_says_it_is_not_published(local_database):
    """drop_untitled keeps a record without a title on a side out of flora.csv and the
    API. The save notice says so when it is known (a DOI not found, no reference); the
    entry, from the build, once it is in the tab."""
    conn = local_database
    with conn, _cursor(conn) as cur:
        cur.execute("INSERT INTO work_metadata (doi, not_found) VALUES ('10.7/rep', TRUE)")
    saved = add(conn, {**REPLICATION, "ref_o": "Doe, J. (2019). An original study. Journal, 1, 2-3."})
    notice = answer(conn, saved["manual_id"])["notice"]
    assert "Not published as it stands: it has no title on the replication side" in notice
    assert "Give its reference (APA) here" in notice
    refresh(conn)
    entry = run(conn, manual_records.detail, saved["manual_id"])
    assert entry["state"]["unpublished"] == ["replication"]
    assert "Add the title in the FLoRA editor for record MAN-000001." in entry["state"]["message"]
    titled = add(conn, {**REPLICATION, "doi_r": "10.7/other",
                        "ref_o": "Doe, J. (2019). An original study. Journal, 1, 2-3.",
                        "ref_r": "Roe, R. (2021). A replication of Doe. Journal, 2, 5-6."})
    assert "Not published" not in answer(conn, titled["manual_id"])["notice"]


def test_an_absorbed_entry_is_frozen_once_the_record_it_fills_has_its_id(local_database):
    """Editable while the record it fills has no FLoRA ID; once the registry knows the
    entry under that record's ID it is frozen like an entry with an ID of its own, and
    withdraw and restore still work."""
    conn = local_database
    add_source(conn, "REPL-000001", outcome="successful")
    saved = add(conn)
    state = answer(conn, saved["manual_id"])["state"]
    assert (state["code"], state["editable"], state["flora_id"]) == ("absorbed", True, None)
    run(conn, manual_records.update, saved["manual_id"], {**REPLICATION, "year_r": "2020"}, NOTE, 1, ADMIN)

    refresh(conn)
    man = source_row(conn, "MAN-000001")["record_id"]
    with conn, _cursor(conn) as cur:
        cur.execute("SELECT %s::uuid = ANY(merged_source_record_ids) AS known FROM flora_records", (man,))
        assert cur.fetchone()["known"], "the registry knows the entry under REPL-000001"
    entry = answer(conn, saved["manual_id"])
    assert (entry["state"]["code"], entry["state"]["editable"]) == ("absorbed", False)
    assert entry["state"]["message"].endswith(
        "fix it in the FLoRA editor for record REPL-000001.")
    with pytest.raises(manual_records.ManualRecordError) as refused:
        run(conn, manual_records.update, saved["manual_id"], {**REPLICATION, "year_r": "2021"},
            NOTE, 2, ADMIN)
    assert refused.value.status == 409 and refused.value.detail["code"] == "frozen"
    assert refused.value.detail["flora_id"] == "REPL-000001"
    assert "FLoRA editor for record REPL-000001" in refused.value.detail["message"]

    run(conn, manual_records.withdraw, saved["manual_id"], "Not needed", 2, ADMIN)
    refresh(conn)
    assert [(r["flora_id"], r["retired"]) for r in flora_ids(conn)] == [("REPL-000001", False)], \
        "withdrawing a merged entry retires nothing"
    entry = answer(conn, saved["manual_id"])
    assert (entry["state"]["code"], entry["state"]["editable"]) == ("withdrawn", False)
    run(conn, manual_records.restore, saved["manual_id"], "", 3, ADMIN)
    entry = answer(conn, saved["manual_id"])
    assert (entry["state"]["code"], entry["state"]["editable"]) == ("absorbed", False), \
        "still known to the registry, so still frozen"


def test_two_entries_of_one_paper_the_first_is_the_record(local_database):
    conn = local_database
    first = add(conn)
    second = add(conn, {**REPLICATION, "abstract_r": "Second."})
    assert answer(conn, first["manual_id"])["state"]["code"] == "awaiting_id"
    state = answer(conn, second["manual_id"])["state"]
    assert (state["code"], state["into"]) == ("absorbed", "MAN-000001")


# ── refusals and repeats ──────────────────────────────────────────────────────

def test_an_excluded_pair_is_refused_even_kept_distinct(local_database):
    conn = local_database
    with conn, _cursor(conn) as cur:
        cur.execute("INSERT INTO transform_exclusions (doi_r, reason) VALUES ('10.7/REP', 'not a replication')")
    check = run(conn, manual_records.check, REPLICATION)
    assert check["excluded"] == "not a replication"
    with pytest.raises(manual_records.ManualRecordError) as refused:
        add(conn, keep_distinct=True)
    assert refused.value.status == 409 and refused.value.detail["code"] == "excluded"
    assert refused.value.detail["reason"] == "not a replication"
    with conn, _cursor(conn) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM manual_records")
        assert cur.fetchone()["n"] == 0


def test_a_repeated_submit_returns_the_entry_already_saved(local_database):
    conn = local_database
    token = str(uuid.uuid4())
    first = add(conn, client_token=token)
    again = add(conn, client_token=token)
    assert again == {"manual_id": first["manual_id"], "display_id": "MAN-000001", "repeat": True}
    with conn, _cursor(conn) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM source_records WHERE source = 'manual'")
        assert cur.fetchone()["n"] == 1
    assert answer(conn, first["manual_id"], repeat=True)["notice"].startswith("Already saved as MAN-000001.")


def test_a_reproduction_stores_both_axes_and_publishes_the_derived_outcome(local_database):
    conn = local_database
    values = {"type": "reproduction", "doi_o": "10.7/orig", "doi_r": "10.7/repro",
              "outcome_computation": "computationally successful",       # an archived spelling
              "outcome_computational_quote": "The code ran.",
              "outcome_robustness": "not checked"}
    saved = add(conn, values)
    row = source_row(conn, saved["display_id"])
    assert (row["outcome_computation"], row["outcome_robustness"]) == \
        ("computationally reproducible", "not checked")
    assert row["outcome"] is None and row["out_quote_computational_source"] == "full_text"
    [published] = built(conn).to_dict("records")
    assert published["outcome"] == derive_reproduction_outcome("computationally reproducible",
                                                               "not checked")


def test_a_pair_being_validated_is_pointed_out(local_database):
    conn = local_database
    with conn, _cursor(conn) as cur:
        cur.execute("INSERT INTO unvalidated (doi_r, doi_o, type, outcome, validation_status, title_r) "
                    "VALUES ('10.7/REP', '10.7/orig', 'replication', 'failed', 'need_review', 'T') "
                    "RETURNING record_id::text AS id")
        rid = cur.fetchone()["id"]
    check = run(conn, manual_records.check, REPLICATION)
    assert [(r["record_id"], r["same_original"], r["validated"]) for r in check["in_validation"]] == \
        [(rid, True, False)]


def test_the_list_finds_entries_the_grid_cannot_show(local_database):
    conn = local_database
    add_source(conn, "REPL-000001", outcome="successful")
    absorbed = add(conn)
    own = add(conn, {**REPLICATION, "doi_r": "10.7/other"})
    run(conn, manual_records.withdraw, own["manual_id"], "Duplicate entry", 1, ADMIN)
    flora_service.invalidate()
    listed = run(conn, manual_records.list_records, "all")
    assert listed["counts"] == {"active": 1, "withdrawn": 1}
    states = {e["display_id"]: e["state"]["code"] for e in listed["entries"]}
    assert states == {absorbed["display_id"]: "absorbed", own["display_id"]: "withdrawn"}
    assert [e["display_id"] for e in run(conn, manual_records.list_records, "withdrawn")["entries"]] == \
        [own["display_id"]]


# ── the routes, on the real app ───────────────────────────────────────────────

@pytest.fixture
def admin_api(local_database, monkeypatch):
    import os

    import db_pool
    from fastapi.testclient import TestClient
    from tests.test_dashboard_split import flora
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    flora.app.dependency_overrides[flora.current_admin] = lambda: dict(ADMIN)
    try:
        yield TestClient(flora.app)
    finally:
        flora.app.dependency_overrides.clear()
        db_pool.clear_all()
        flora_service.invalidate()


def test_the_routes_add_change_withdraw_and_restore_with_an_audit_trail(admin_api, local_database):
    client, conn = admin_api, local_database
    options = client.get("/api/admin/flora/manual/options").json()
    assert "failed" in options["outcomes"] and "not_a_replication" not in options["outcomes"]

    bad = client.post("/api/admin/flora/manual", json={"fields": {"type": "replication"}, "note": NOTE})
    assert bad.status_code == 422 and bad.json()["detail"]["field"] == "doi_o"

    body = {"fields": REPLICATION, "note": NOTE, "client_token": str(uuid.uuid4())}
    created = client.post("/api/admin/flora/manual", json=body)
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["display_id"] == "MAN-000001" and entry["run_pipeline"] is True
    again = client.post("/api/admin/flora/manual", json=body)
    assert again.status_code == 200 and again.json()["repeat"] is True

    assert client.get("/api/admin/flora/manual/counts").json() == {"active": 1, "withdrawn": 0}
    assert client.get("/api/admin/flora/manual").json()["entries"][0]["display_id"] == "MAN-000001"
    assert client.get("/api/admin/flora/manual/MAN-000001").json()["manual_id"] == entry["manual_id"]
    assert client.get("/api/admin/flora/manual/nonsense").status_code == 404

    stale = client.patch("/api/admin/flora/manual/MAN-000001",
                         json={"fields": REPLICATION, "note": NOTE, "version": 9})
    assert stale.status_code == 409 and stale.headers["X-Current-Version"] == "1"
    same = client.patch("/api/admin/flora/manual/MAN-000001",
                        json={"fields": REPLICATION, "note": NOTE, "version": 1})
    assert same.status_code == 200 and same.json()["notice"].startswith("Nothing changed in MAN-000001.")
    changed = client.patch("/api/admin/flora/manual/MAN-000001",
                           json={"fields": {**REPLICATION, "year_r": "2020"}, "note": NOTE, "version": 1})
    assert changed.status_code == 200 and changed.json()["version"] == 2

    withdrawn = client.post("/api/admin/flora/manual/MAN-000001/withdraw",
                            json={"version": 2, "reason": "Entered by mistake"})
    assert withdrawn.status_code == 200 and withdrawn.json()["notice"].startswith("Withdrew MAN-000001.")
    restored = client.post("/api/admin/flora/manual/MAN-000001/restore", json={"version": 3})
    assert restored.status_code == 200 and restored.json()["state"]["code"] == "awaiting_id"

    with conn, _cursor(conn) as cur:
        cur.execute("SELECT action, target_kind, target_id, actor_handle FROM security_events "
                    "WHERE action LIKE 'flora.manual%%' ORDER BY event_id")
        assert [tuple(r.values()) for r in cur.fetchall()] == [
            ("flora.manual_added", "manual_record", "MAN-000001", "sophie"),
            ("flora.manual_edited", "manual_record", "MAN-000001", "sophie"),
            ("flora.manual_withdrawn", "manual_record", "MAN-000001", "sophie"),
            ("flora.manual_restored", "manual_record", "MAN-000001", "sophie")]

    sources = client.get("/api/admin/source-records", params={"source": "manual"}).json()
    assert [r["display_id"] for r in sources["records"]] == ["MAN-000001"]
    assert sources["counts"]["manual"] == 1
