"""Stage-3 study numbers must never be overwritten by paper titles.

The extractor emits both: study_r/study_o identify which within-paper studies
form the pair, while title_r/title_o identify the papers. Several pairs can share
the same papers and differ only by study number, so collapsing these fields loses
records at import and again at validated-table upsert time.
"""
from pathlib import Path

import pandas as pd

from consensus_engine import _resolve_final
from csv_to_db import (
    _build_unvalidated_row,
    _insert_unvalidated,
    _refresh_study_identifiers,
)
from update_originals import _UNVAL_FIELDS


ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")


def _csv_row():
    return pd.Series({
        "doi_r": "10.1/rep",
        "study_r": "1, 2",
        "title_r": "Replication paper",
        "doi_o": "10.1/orig",
        "study_o": "3",
        "title_o": "Original paper",
        "type": "replication",
        "outcome": "successful",
    })


def test_import_row_keeps_study_numbers_and_titles_separate():
    built = _build_unvalidated_row("record", "pair", _csv_row())
    assert built["study_r"] == "1, 2"
    assert built["title_r"] == "Replication paper"
    assert built["study_o"] == "3"
    assert built["title_o"] == "Original paper"


def test_insert_sends_all_four_fields_to_the_database():
    class Cursor:
        rowcount = 1

        def execute(self, sql, params):
            self.sql, self.params = sql, params

    cur = Cursor()
    row = _build_unvalidated_row("record", "pair", _csv_row())
    assert _insert_unvalidated(cur, row)
    for field in ("study_r", "title_r", "study_o", "title_o"):
        assert field in cur.sql
        assert cur.params[field] == row[field]


def test_existing_row_refresh_uses_study_columns_not_titles():
    row = _csv_row()
    assert _UNVAL_FIELDS["study_r"](row) == "1, 2"
    assert _UNVAL_FIELDS["study_o"](row) == "3"
    assert _UNVAL_FIELDS["title_o"](row) == "Original paper"


def test_existing_validated_rows_receive_refreshed_study_identity():
    source = (ROOT / "update_originals.py").read_text(encoding="utf-8")
    assert 'for col in ("study_r", "study_o")' in source
    assert 'UPDATE validated SET {identity_sets} WHERE record_id = %s' in source


def test_nightly_reimport_refreshes_study_identity_for_existing_pairs():
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

    cur = Cursor()
    _refresh_study_identifiers(cur, "record", _csv_row())
    assert len(cur.calls) == 2
    assert "UPDATE unvalidated" in cur.calls[0][0]
    assert "UPDATE validated" in cur.calls[1][0]
    assert cur.calls[0][1][:3] == ("1, 2", "3", "record")
    assert cur.calls[1][1][:3] == ("1, 2", "3", "record")


def test_consensus_corrects_titles_without_touching_study_identity():
    record = {
        "study_r": "1, 2", "title_r": "Replication paper",
        "study_o": "3", "title_o": "Original paper",
        "doi_o": "10.1/orig", "url_r": "", "outcome": "successful",
        "type": "replication", "outcome_quote": "", "abstract_r": "",
    }
    final = _resolve_final(record, {
        "corrected_title_r": "Corrected replication title",
        "corrected_title_o": "Corrected original title",
    })
    assert final["study_r"] == "1, 2"
    assert final["study_o"] == "3"
    assert final["title_r"] == "Corrected replication title"
    assert final["title_o"] == "Corrected original title"


def test_schema_migrates_legacy_title_data_before_rebuilding_identity():
    migration = SCHEMA.index("UPDATE unvalidated\n   SET title_r")
    identity = SCHEMA.index("ADD CONSTRAINT validated_pair_identity_key")
    assert migration < identity
    assert "SET title_r = COALESCE(study_r, ''), study_r = ''" in SCHEMA
    assert "SET title_o = COALESCE(study_o, ''), study_o = ''" in SCHEMA


def test_validated_identity_contains_titles_and_study_numbers():
    key = "UNIQUE (doi_r, study_r, title_r, original_key, study_o, title_o)"
    assert key in SCHEMA
    total = sum((ROOT / name).read_text(encoding="utf-8").count(
        "ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o)"
    ) for name in ("app.py", "consensus_engine.py"))
    assert total == 3


def test_validator_displays_titles_and_study_numbers_from_distinct_fields():
    js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    assert 'escapeHtml(p.title_r || "(untitled)")' in js
    assert 'p.study_r ? " · Study " + escapeHtml(p.study_r)' in js
    assert 'escapeHtml(p.title_o || "(no title)")' in js
    assert 'p.study_o ? " · Study " + escapeHtml(p.study_o)' in js
