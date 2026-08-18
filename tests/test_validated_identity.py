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


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_generated_column_uses_the_coalesce_fallback_chain():
    assert re.search(
        r"ADD COLUMN IF NOT EXISTS original_key TEXT\s*"
        r"GENERATED ALWAYS AS \(COALESCE\(NULLIF\(doi_o, ''\), oa_work_id_o, ''\)\) STORED",
        _SCHEMA), "original_key is missing or its fallback chain changed"


def test_the_new_constraint_replaces_the_old_one():
    assert "DROP CONSTRAINT IF EXISTS validated_doi_r_study_r_doi_o_study_o_key" in _SCHEMA
    assert re.search(
        r"ADD CONSTRAINT validated_pair_identity_key\s*"
        r"UNIQUE \(doi_r, study_r, title_r, original_key, study_o, title_o\)", _SCHEMA)


def test_the_constraint_swap_fails_closed():
    """A duplicate under the new key must abort the migration. Swallowing that
    error would commit the earlier DROP statements and leave every ON CONFLICT
    write without a matching unique constraint."""
    block = re.search(r"DO \$\$\s*BEGIN\s*IF NOT EXISTS .*?validated_pair_identity_key.*?END \$\$;",
                      _SCHEMA, re.S)
    assert block, "the constraint swap is not inside a DO block"
    assert "DROP CONSTRAINT IF EXISTS validated_doi_r_study_r_doi_o_study_o_key" in block.group(0)
    assert "ADD CONSTRAINT validated_pair_identity_key" in block.group(0)
    assert "EXCEPTION WHEN unique_violation" not in block.group(0)
    assert "RAISE WARNING" not in block.group(0)


def test_old_constraints_are_not_dropped_before_the_atomic_swap():
    """Every destructive identity change belongs to the same DO statement as the
    replacement ADD, so an unhandled error rolls all of it back together."""
    block_start = _SCHEMA.index("-- Swap the identity atomically")
    first_drop = _SCHEMA.index(
        "DROP CONSTRAINT IF EXISTS validated_doi_r_study_r_doi_o_study_o_key"
    )
    assert first_drop > block_start


def test_the_constraint_add_is_idempotent():
    """The file re-runs on every start; adding an existing constraint would error."""
    block = re.search(r"DO \$\$\s*BEGIN\s*IF NOT EXISTS .*?validated_pair_identity_key.*?END \$\$;",
                      _SCHEMA, re.S)
    assert "IF NOT EXISTS (SELECT 1 FROM pg_constraint" in block.group(0)


def test_generated_column_is_declared_after_oa_work_id_o_exists():
    """oa_work_id_o is added to validated by ALTER, not in CREATE TABLE. A generated
    column referencing it must come later in the file or the statement fails."""
    add_col = _SCHEMA.index("ALTER TABLE validated   ADD COLUMN IF NOT EXISTS oa_work_id_o")
    generated = _SCHEMA.index("ADD COLUMN IF NOT EXISTS original_key")
    assert add_col < generated


# ---------------------------------------------------------------------------
# Every write path
# ---------------------------------------------------------------------------

def test_no_upsert_still_keys_on_doi_o():
    for name in _UPSERT_FILES:
        text = (_ROOT / name).read_text(encoding="utf-8")
        assert "ON CONFLICT (doi_r, study_r, doi_o, study_o)" not in text, \
            f"{name} still keys on doi_o, which is '' for every DOI-less original"


def test_all_three_upserts_key_on_full_pair_identity():
    total = sum((_ROOT / name).read_text(encoding="utf-8")
                .count("ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o)")
                for name in _UPSERT_FILES)
    assert total == 3, f"expected 3 rekeyed upserts, found {total}"


def test_no_upsert_assigns_the_generated_column():
    """original_key is GENERATED ALWAYS — assigning it is an error, not a no-op."""
    for name in _UPSERT_FILES:
        text = (_ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s+original_key\s*=", text, re.M), \
            f"{name} assigns original_key"


def test_every_upsert_path_deletes_the_prior_row_first():
    """original_key is derived from mutable columns, so a correction — an admin
    adding a DOI to a DOI-less original, say — changes a record's identity. Without
    the delete, the row under the old identity is orphaned rather than updated."""
    for name in _UPSERT_FILES:
        text = (_ROOT / name).read_text(encoding="utf-8")
        upserts = text.count("ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o)")
        deletes = text.count("DELETE FROM validated WHERE record_id")
        assert deletes >= upserts, \
            f"{name}: {upserts} upsert(s) but only {deletes} delete-first guard(s)"
