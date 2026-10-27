"""The nightly import rewrites an existing pair only where a value changed.

Opt-in PostgreSQL checks: set FLORA_TEST_DATABASE_URL (see
tests/test_preparation_database.py). Even a no-op UPDATE leaves a dead tuple, and
the import re-sends ~10,000 existing pairs every night, so an unchanged re-import
must leave every row's xmin where it was. Anything that did change must still be
written exactly as before, including around the DOI trigger that clears work ids.
"""
import pandas as pd
from psycopg2.extras import RealDictCursor

import csv_to_db
from tests.test_preparation_database import local_database  # noqa: F401


# Exactly the columns the existing-pair path assigns, per table.
UNVALIDATED_REFRESHED = (
    "doi_r", "study_r", "title_r", "year_r", "url_r", "ref_r", "abstract_r",
    "doi_o", "study_o", "title_o", "year_o", "url_o", "ref_o",
    "oa_work_id_o", "oa_work_id_r",
    "type", "outcome", "outcome_quote", "out_quote_source",
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
)
METADATA_REFRESHED = (
    "pair_id", "work_id",
    "filter_status", "filter_method", "filter_evidence", "filter_confidence",
    "original_match_type", "original_match_confidence", "doi_o_verification",
    "link_method", "link_evidence", "link_confidence", "link_llm_model", "screen_categories",
    "pdf_source", "parse_method",
    "outcome_confidence", "outcome_reasoning", "outcome_llm_model",
    "authors_r", "authors_o", "journal_r", "openalex_id_r", "source",
    "bibtex_ref_o", "bibtex_ref_r", "original_rank", "n_originals",
)
_WORK_IDS = {"a": ("W100", "W200"), "b": ("W300", "W400")}


def _row(key="a", **overrides):
    """One resolved current-contract row; every value is distinct and non-blank."""
    work_r, work_o = _WORK_IDS[key]
    row = {column: "" for column in csv_to_db._CURRENT_EXTRACTED_COLUMNS}
    row.update({
        "pair_id": f"pair-{key}", "paper_type": "reproduction", "type": "reproduction",
        "doi_r": f"10.1/rep-{key}", "study_r": "1", "title_r": f"Replication {key}",
        "year_r": "2020", "url_r": f"https://example.org/rep-{key}", "ref_r": f"Doe 2020 {key}",
        "abstract_r": f"Abstract {key}", "authors_r": "Doe, J.", "journal_r": "Journal R",
        "openalex_id_r": f"https://openalex.org/{work_r}", "oa_work_id_r": work_r,
        "doi_o": f"10.1/orig-{key}", "study_o": "2", "title_o": f"Original {key}",
        "year_o": "1999", "url_o": f"https://example.org/orig-{key}", "ref_o": f"Roe 1999 {key}",
        "authors_o": "Roe, R.", "oa_work_id_o": work_o,
        "source": "openalex", "filter_method": "llm", "filter_evidence": "filter evidence",
        "filter_confidence": "high", "original_match_type": "single_original",
        "original_match_confidence": "medium", "classify_llm_model": "classifier",
        "bibtex_ref_o": "@article{o}", "bibtex_ref_r": "@article{r}",
        "link_method": "llm_references", "link_evidence": "link evidence",
        "link_confidence": "low", "link_llm_model": "linker",
        "screen_categories": "clearly_declared", "doi_o_verification": "verified",
        "pdf_source": "unpaywall", "parse_method": "grobid",
        "outcome_phrase": "the result held", "outcome_confidence": "high",
        "out_quote_source": "abstract", "outcome_reasoning": "reasoning",
        "outcome_computation": "computational issues",
        "outcome_computational_quote": "code needed repairs",
        "out_quote_computational_source": "results",
        "outcome_robustness": "robust", "outcome_robustness_quote": "conclusion held",
        "out_quote_robust_source": "discussion", "outcome_llm_model": "outcome-model",
        "original_rank": "1", "n_originals": "1",
    })
    row.update(overrides)
    return row


def _import(tmp_path, *rows):
    path = tmp_path / "extracted.csv"
    pd.DataFrame(list(rows)).to_csv(path, index=False)
    csv_to_db.run_import(path)


def _state(connection, pair_id):
    """The record's unvalidated, record_metadata and validated rows, with xmin."""
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT xmin::text AS xmin, * FROM unvalidated WHERE pair_id = %s", (pair_id,))
        unvalidated = cur.fetchone()
        record_id = unvalidated["record_id"]
        cur.execute("SELECT xmin::text AS xmin, * FROM record_metadata WHERE record_id = %s",
                    (record_id,))
        metadata = cur.fetchone()
        cur.execute("SELECT xmin::text AS xmin, * FROM validated WHERE record_id = %s",
                    (record_id,))
        validated = cur.fetchone()
    return unvalidated, metadata, validated


def _validate(connection, pair_id, **overrides):
    """Give the record a validated row copied from its source, then overrides."""
    columns = ("doi_r", "study_r", "title_r", "doi_o", "study_o", "title_o",
               "oa_work_id_r", "oa_work_id_o")
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT record_id, {', '.join(columns)} FROM unvalidated WHERE pair_id = %s",
                    (pair_id,))
        values = {**cur.fetchone(), **overrides}
        cur.execute(
            f"INSERT INTO validated (record_id, {', '.join(columns)}) "
            f"VALUES (%(record_id)s, {', '.join(f'%({c})s' for c in columns)})",
            values,
        )


def _tamper(connection, table, column, value, record_id):
    with connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {table} SET {column} = %s WHERE record_id = %s", (value, record_id))


def _without(row, *columns):
    return {k: v for k, v in row.items() if k not in ("xmin", *columns)}


def test_reimporting_an_unchanged_csv_touches_no_row(local_database, tmp_path):
    connection = local_database
    _import(tmp_path, _row("a"), _row("b"))
    _validate(connection, "pair-a")
    # A validator corrected b's DOIs, so its stale work ids are kept (the CASE
    # branches fall through to the stored value) and must not count as a change.
    _validate(connection, "pair-b", doi_r="10.1/corrected-r", oa_work_id_r="W999",
              doi_o="10.1/corrected-o", oa_work_id_o="W998")
    before = {key: _state(connection, f"pair-{key}") for key in "ab"}

    _import(tmp_path, _row("a"), _row("b"))

    for key in "ab":
        after = _state(connection, f"pair-{key}")
        for old, new in zip(before[key], after):
            assert new["xmin"] == old["xmin"]
            assert new == old
    assert after[2]["oa_work_id_r"] == "W999"


def test_a_changed_field_is_written_and_stamps_updated_at(local_database, tmp_path):
    connection = local_database
    _import(tmp_path, _row("a"))
    _validate(connection, "pair-a")
    unvalidated, metadata, validated = _state(connection, "pair-a")

    _import(tmp_path, _row("a", title_r="Corrected title", filter_evidence="new evidence"))

    new_unvalidated, new_metadata, new_validated = _state(connection, "pair-a")
    assert new_unvalidated["title_r"] == "Corrected title"
    assert new_unvalidated["xmin"] != unvalidated["xmin"]
    assert new_unvalidated["updated_at"] > unvalidated["updated_at"]
    assert _without(new_unvalidated, "title_r", "updated_at") == _without(unvalidated, "title_r", "updated_at")
    assert new_metadata["filter_evidence"] == "new evidence"
    assert new_metadata["xmin"] != metadata["xmin"]
    assert _without(new_metadata, "filter_evidence") == _without(metadata, "filter_evidence")
    # The validated row takes identity backfills only, and none changed.
    assert new_validated == validated

    # A metadata-only change leaves the source row (and its updated_at) alone.
    _import(tmp_path, _row("a", title_r="Corrected title", filter_evidence="new evidence",
                           link_evidence="newer link evidence"))
    last_unvalidated, last_metadata, _ = _state(connection, "pair-a")
    assert last_unvalidated == new_unvalidated
    assert last_metadata["link_evidence"] == "newer link evidence"
    assert last_metadata["xmin"] != new_metadata["xmin"]


def test_every_assigned_column_is_compared(local_database, tmp_path):
    """Drift any one assigned column and the import must write it back.

    NULL is the drift wherever the column allows it: a comparison written with
    = or <> instead of IS DISTINCT FROM would miss it and skip the write.
    """
    connection = local_database
    _import(tmp_path, _row("a"))
    _validate(connection, "pair-a")
    unvalidated, metadata, validated = _state(connection, "pair-a")
    record_id = unvalidated["record_id"]

    drifts = [("unvalidated", column, None) for column in UNVALIDATED_REFRESHED]
    drifts[0] = ("unvalidated", "doi_r", "10.1/drifted")  # doi_r is NOT NULL
    drifts += [("record_metadata", column, None) for column in METADATA_REFRESHED]
    drifts += [("validated", "study_r", None), ("validated", "study_o", None),
               ("validated", "oa_work_id_r", "W999"), ("validated", "oa_work_id_o", None)]
    expected = {"unvalidated": unvalidated, "record_metadata": metadata, "validated": validated}
    for table, column, value in drifts:
        _tamper(connection, table, column, value, record_id)
        drifted = dict(zip(expected, _state(connection, "pair-a")))
        assert drifted[table][column] != expected[table][column], (table, column)

        _import(tmp_path, _row("a"))

        restored = dict(zip(expected, _state(connection, "pair-a")))
        assert restored[table]["xmin"] != drifted[table]["xmin"], (table, column)
        assert _without(restored[table], "updated_at") == _without(expected[table], "updated_at"), \
            (table, column)


def test_release_id_is_compared_with_the_value_set_would_keep(local_database, tmp_path):
    """release_id = COALESCE(EXCLUDED.release_id, stored): with no incoming
    release, the stored one stays, so that row has nothing to write."""
    connection = local_database
    _import(tmp_path, _row("a"))
    record_id = _state(connection, "pair-a")[0]["record_id"]
    _tamper(connection, "record_metadata", "release_id", "kept-release", record_id)
    _, metadata, _ = _state(connection, "pair-a")

    _import(tmp_path, _row("a"))

    assert _state(connection, "pair-a")[1] == metadata


def test_a_doi_change_still_ends_with_the_incoming_work_ids(local_database, tmp_path):
    connection = local_database
    _import(tmp_path, _row("a"))
    _validate(connection, "pair-a")
    unvalidated, _, validated = _state(connection, "pair-a")

    # Same work ids: the trigger clears them as the DOIs change, and the
    # restore must still put them back.
    _import(tmp_path, _row("a", doi_r="10.1/rep-a-v2", doi_o="10.1/orig-a-v2"))

    new_unvalidated, _, new_validated = _state(connection, "pair-a")
    assert (new_unvalidated["doi_r"], new_unvalidated["doi_o"]) == ("10.1/rep-a-v2", "10.1/orig-a-v2")
    assert (new_unvalidated["oa_work_id_r"], new_unvalidated["oa_work_id_o"]) == ("W100", "W200")
    assert new_unvalidated["updated_at"] > unvalidated["updated_at"]
    # validated still carries the old DOIs, so its work ids are not backfilled.
    assert new_validated == validated

    # A DOI change that brings a new original work id ends with that id.
    _import(tmp_path, _row("a", doi_r="10.1/rep-a-v2", doi_o="10.1/orig-a-v3", oa_work_id_o="W201"))
    assert _state(connection, "pair-a")[0]["oa_work_id_o"] == "W201"


def test_same_doi_with_a_new_work_id_is_written(local_database, tmp_path):
    connection = local_database
    _import(tmp_path, _row("a"))
    _validate(connection, "pair-a")
    unvalidated, metadata, validated = _state(connection, "pair-a")

    _import(tmp_path, _row("a", oa_work_id_r="W101", oa_work_id_o="W201"))

    new_unvalidated, new_metadata, new_validated = _state(connection, "pair-a")
    assert (new_unvalidated["oa_work_id_r"], new_unvalidated["oa_work_id_o"]) == ("W101", "W201")
    assert new_unvalidated["updated_at"] > unvalidated["updated_at"]
    assert new_metadata["work_id"] == 101 and metadata["work_id"] == 100
    # Its DOIs still match, so the validated row takes the new ids too.
    assert (new_validated["oa_work_id_r"], new_validated["oa_work_id_o"]) == ("W101", "W201")
    assert new_validated["xmin"] != validated["xmin"]


def test_a_rekey_still_rewrites_the_metadata_row(local_database, tmp_path):
    connection = local_database
    _import(tmp_path, _row("a"))
    record_id = _state(connection, "pair-a")[0]["record_id"]

    # Same work and original_rank under a new pair_id: the re-key path.
    _import(tmp_path, _row("a", pair_id="pair-a2", doi_o="10.1/orig-a-v2"))

    unvalidated, metadata, _ = _state(connection, "pair-a2")
    assert unvalidated["record_id"] == record_id
    assert metadata["pair_id"] == "pair-a2"

    _import(tmp_path, _row("a", pair_id="pair-a2", doi_o="10.1/orig-a-v2"))
    assert _state(connection, "pair-a2")[:2] == (unvalidated, metadata)
