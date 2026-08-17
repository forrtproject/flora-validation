"""Tests for extractor_vocab — the CSV value vocabularies and the drift check.

The bug these guard against: the extractor renamed every link_method value, the
importer's allowlist wasn't updated, and because an unrecognised value was
treated exactly like "not yet resolved", 1859 of 1890 eligible rows were dropped
silently for five weeks. Anything unknown must now raise.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from extractor_vocab import (
    CURRENT_METHODS,
    KNOWN_METHODS,
    OUTCOME_RENAME,
    REPLICATION_OUTCOMES,
    REPRODUCTION_OUTCOMES,
    RESOLVED_METHODS,
    RETIRED_METHODS,
    STORED_OUTCOMES,
    VocabularyDriftError,
    check_csv_vocabulary,
    normalize_outcome,
    paper_type,
    paper_type_column,
    resolved_mask,
)

_SCHEMA = Path(__file__).resolve().parent.parent / "db_schema.sql"


def _frame(**overrides) -> pd.DataFrame:
    """A minimal two-row CSV-shaped frame; override any column."""
    base = {
        "pair_id": ["a", "b"],
        "filter_status": ["replication", "replication"],
        "link_method": ["llm_references", "llm_title_search"],
        "outcome": ["success", "failed"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


# ---------------------------------------------------------------------------
# Vocabulary shape
# ---------------------------------------------------------------------------

def test_current_and_retired_methods_are_disjoint():
    """A value is either current or retired, never both — otherwise 'what does
    the extractor emit today' has no answer."""
    assert CURRENT_METHODS & RETIRED_METHODS == frozenset()


def test_resolved_methods_includes_retired_ones():
    """data/ holds CSV snapshots back to May; find_orphans and cleanup_orphans
    read whichever is passed to --input. Dropping retired names would make every
    record in an archived replay look like an orphan."""
    assert RETIRED_METHODS <= RESOLVED_METHODS
    assert "llm_abstract" in RESOLVED_METHODS


def test_unresolved_methods_are_known_but_not_resolved():
    """'no_original_found' must not raise, and must not import either."""
    assert "no_original_found" in KNOWN_METHODS
    assert "no_original_found" not in RESOLVED_METHODS


def test_outcome_rename_targets_are_all_storable():
    """Every translation must land on a value the CHECK constraint accepts,
    otherwise the rename just moves the failure."""
    assert set(OUTCOME_RENAME.values()) <= STORED_OUTCOMES


def test_outcome_rename_sources_are_not_storable():
    """A retired spelling must not also be a current one, or normalisation is
    ambiguous and stored data ends up mixed."""
    assert set(OUTCOME_RENAME) & STORED_OUTCOMES == frozenset()


def test_flawed_replication_label_is_kept_distinct():
    """statistically_successful_but_flawed counts with the successes but is
    stored as itself, so the caveat survives the import boundary."""
    assert "statistically_successful_but_flawed" in REPLICATION_OUTCOMES
    assert normalize_outcome("statistically_successful_but_flawed") == \
        "statistically_successful_but_flawed"


def test_cannot_be_determined_is_shared_by_both_record_types():
    """The extractor collapses a two-axis unknown to the bare label rather than
    a pair, so reproductions need it too."""
    assert "cannot_be_determined" in REPLICATION_OUTCOMES
    assert "cannot_be_determined" in REPRODUCTION_OUTCOMES


# ---------------------------------------------------------------------------
# The Python vocabulary and the SQL constraint must not drift apart
# ---------------------------------------------------------------------------

def test_stored_outcomes_match_the_check_constraint():
    """db_schema.sql's unvalidated_outcome_check and STORED_OUTCOMES are the same
    vocabulary written twice. A value in one and not the other rolls back the
    entire import transaction, not just its own row."""
    sql = _SCHEMA.read_text(encoding="utf-8")
    body = re.search(
        r"ADD CONSTRAINT unvalidated_outcome_check\s*CHECK \(outcome IN \((.*?)\)\);",
        sql, re.S,
    )
    assert body, "could not locate unvalidated_outcome_check in db_schema.sql"
    in_sql = set(re.findall(r"'([^']+)'", body.group(1)))
    assert in_sql == set(STORED_OUTCOMES)


def test_retired_reproduction_spellings_are_migrated_by_the_schema():
    """Each OUTCOME_RENAME source that is a reproduction label must appear in the
    schema's relabel table, or stored rows keep the old spelling while new
    imports use the new one."""
    sql = _SCHEMA.read_text(encoding="utf-8")
    relabel = re.search(r"INSERT INTO _outcome_relabel .*?;", sql, re.S)
    assert relabel, "could not locate the _outcome_relabel seed in db_schema.sql"
    for old in OUTCOME_RENAME:
        if "," in old:                      # reproduction labels only
            assert old in relabel.group(0), f"{old!r} renamed on import but not migrated"


# ---------------------------------------------------------------------------
# Paper type
# ---------------------------------------------------------------------------

def test_paper_type_prefers_the_newer_column():
    df = _frame(paper_type=["reproduction", "reproduction"])
    assert paper_type_column(df).tolist() == ["reproduction", "reproduction"]


def test_paper_type_falls_back_to_filter_status():
    """CSVs exported before flora-extractor#93 carry the old header."""
    assert paper_type_column(_frame()).tolist() == ["replication", "replication"]


def test_paper_type_row_and_column_agree_on_a_blank_newer_column():
    """Both helpers select by column presence. Selecting by *value* instead would
    make the row-level read fall through to filter_status while the frame-level
    filter used paper_type — disagreeing on exactly these rows."""
    df = _frame(paper_type=["", ""])
    assert paper_type_column(df).tolist() == ["", ""]
    assert paper_type(df.iloc[0]) == ""


def test_paper_type_column_raises_when_absent():
    with pytest.raises(KeyError):
        paper_type_column(pd.DataFrame({"link_method": ["llm_references"]}))


# ---------------------------------------------------------------------------
# The drift check
# ---------------------------------------------------------------------------

def test_current_vocabulary_passes():
    check_csv_vocabulary(_frame())


def test_unknown_link_method_raises():
    df = _frame(link_method=["llm_references", "llm_brand_new_method"])
    with pytest.raises(VocabularyDriftError, match="llm_brand_new_method"):
        check_csv_vocabulary(df)


def test_unknown_outcome_on_an_importable_row_raises():
    df = _frame(outcome=["success", "wildly_novel_outcome"])
    with pytest.raises(VocabularyDriftError, match="wildly_novel_outcome"):
        check_csv_vocabulary(df)


def test_unknown_outcome_on_a_non_importable_row_is_ignored():
    """A false_positive row's outcome never reaches the database, so it isn't
    ours to police — refusing there would block imports over rows we discard."""
    df = _frame(
        filter_status=["replication", "false_positive"],
        outcome=["success", "whatever_the_extractor_felt_like"],
    )
    check_csv_vocabulary(df)


def test_retired_outcome_spelling_passes_and_normalises():
    """An archived CSV must still import, landing on the current spelling."""
    df = _frame(
        filter_status=["reproduction", "reproduction"],
        outcome=["computationally successful, robust", "computation not checked, robust"],
    )
    check_csv_vocabulary(df)
    assert normalize_outcome("computationally successful, robust") == \
        "computationally reproducible, robust"
    assert normalize_outcome("computation not checked, robust") == "not checked, robust"


def test_drift_error_message_is_ascii_printable():
    """The message surfaces through sync_csv.py's traceback on an unattended
    nightly run. A Windows console using cp1252 raises UnicodeEncodeError on
    characters outside it, and an error that cannot print is worse than none."""
    df = _frame(link_method=["llm_references", "llm_brand_new_method"])
    with pytest.raises(VocabularyDriftError) as exc:
        check_csv_vocabulary(df)
    str(exc.value).encode("cp1252")


def test_error_names_the_value_and_its_row_count():
    """The message has to be actionable without opening the CSV."""
    df = pd.DataFrame({
        "pair_id": ["a", "b", "c"],
        "filter_status": ["replication"] * 3,
        "link_method": ["llm_references", "mystery_method", "mystery_method"],
        "outcome": ["success"] * 3,
    })
    with pytest.raises(VocabularyDriftError) as exc:
        check_csv_vocabulary(df)
    assert "'mystery_method' (2 rows)" in str(exc.value)


# ---------------------------------------------------------------------------
# resolved_mask
# ---------------------------------------------------------------------------

def test_resolved_mask_selects_current_and_retired_methods():
    df = _frame(link_method=["llm_references", "llm_abstract"])
    assert resolved_mask(df).tolist() == [True, True]


def test_resolved_mask_excludes_unresolved_methods_and_statuses():
    df = _frame(
        filter_status=["replication", "needs_review"],
        link_method=["no_original_found", "llm_references"],
    )
    assert resolved_mask(df).tolist() == [False, False]
