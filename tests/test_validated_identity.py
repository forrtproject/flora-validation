"""The `validated` natural key must survive originals that have no DOI.

Some replications target originals with no registered DOI — books, chapters,
pre-DOI papers. The extractor ships those with doi_o = '' and the identity in
oa_work_id_o. The old key was (doi_r, study_r, doi_o, study_o), when study_*
incorrectly held titles. The current key keeps paper titles and within-paper study
numbers separate, and uses OpenAlex identity when doi_o is blank.

Fix: a generated `original_key` column — doi_o, or oa_work_id_o when there is no
DOI — used in the constraint and in every ON CONFLICT clause.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = (_ROOT / "db_schema.sql").read_text(encoding="utf-8")

_UPSERT_FILES = ["app.py", "consensus_engine.py"]


def _original_key(doi_o, oa_work_id_o):
    """Python mirror of the generated column, for reasoning about the semantics.
    COALESCE(NULLIF(doi_o, ''), oa_work_id_o, '')"""
    return (doi_o or None) or oa_work_id_o or ""


# ---------------------------------------------------------------------------
# The semantics the key has to deliver
# ---------------------------------------------------------------------------

def test_a_doi_bearing_original_is_identified_by_its_doi():
    assert _original_key("10.1/orig", "W999") == "10.1/orig"


def test_a_doi_less_original_is_identified_by_its_work_id():
    """This is the whole point: doi_o is '' so it cannot distinguish anything."""
    assert _original_key("", "W2003152982") == "W2003152982"


def test_two_doi_less_originals_with_different_work_ids_stay_distinct():
    """The bug. Under the old key both were ('', title) and collided when the
    titles matched; now their work ids tell them apart."""
    assert _original_key("", "W111") != _original_key("", "W222")


def test_legacy_rows_without_a_work_id_behave_as_before():
    """Rows imported before the extractor shipped oa_work_id_o have NULL there.
    The fallback is '' — never NULL — because a NULL key column is never equal to
    itself in a unique constraint, so ON CONFLICT would stop matching and
    duplicates would pile up on every re-validation. With '', study_o/title_o
    still provide the legacy fallback identity."""
    assert _original_key("", None) == ""
    assert _original_key(None, None) == ""
