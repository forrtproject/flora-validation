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
    normalize_axis_value,
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
    assert "statistically successful but flawed" in REPLICATION_OUTCOMES
    assert normalize_outcome("statistically_successful_but_flawed") == \
        "statistically successful but flawed"


def test_live_extractor_outcome_aliases_are_supported():
    from extractor_vocab import normalize_outcome

    assert normalize_outcome("descriptive") == "descriptive only"
    assert normalize_outcome("descriptive only") == "descriptive only"
    assert normalize_outcome("api_error") == "api_error"


def test_live_reproduction_joined_values_are_recognised_and_stored():
    from extractor_vocab import CURRENT_REPRODUCTION_OUTCOMES, stored_outcome

    for value in (
        "computational issues, not checked",
        "not checked, not checked",
        "technical failure, not checked",
    ):
        assert value in CURRENT_REPRODUCTION_OUTCOMES
        assert stored_outcome(value, "reproduction") == value


def test_api_error_is_known_but_stored_as_null():
    from extractor_vocab import stored_outcome

    assert stored_outcome("api_error", "replication") is None


def test_cannot_be_determined_is_shared_by_both_record_types():
    """The extractor collapses a two-axis unknown to the bare label rather than
    a pair, so reproductions need it too."""
    assert "cannot_be_determined" in REPLICATION_OUTCOMES
    assert "cannot_be_determined" in REPRODUCTION_OUTCOMES


def test_axis_values_are_strictly_normalised_at_write_boundaries():
    assert normalize_axis_value("outcome_computation", "") is None
    assert normalize_axis_value(
        "outcome_computation", "computationally successful"
    ) == "computationally reproducible"
    with pytest.raises(ValueError, match="invalid outcome_computation"):
        normalize_axis_value("outcome_computation", "mostly worked")
    with pytest.raises(ValueError, match="invalid outcome_robustness"):
        normalize_axis_value("outcome_robustness", "sort of robust")


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


def test_unknown_paper_type_raises():
    df = _frame(filter_status=["replication", "brand_new_type"])
    with pytest.raises(VocabularyDriftError, match="brand_new_type"):
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


def test_retired_joined_outcomes_pass_the_drift_check():
    """The extractor still ships them, so an archived CSV must import. Recognising
    something we deliberately discard is different from not recognising it — the
    check must not confuse the two."""
    df = _frame(
        filter_status=["reproduction", "reproduction"],
        outcome=["computationally successful, robust", "computation not checked, robust"],
    )
    check_csv_vocabulary(df)


def test_legacy_joined_outcomes_are_recognised_and_normalised():
    from extractor_vocab import LEGACY_JOINED_OUTCOMES, KNOWN_CSV_OUTCOMES, stored_outcome
    assert LEGACY_JOINED_OUTCOMES <= KNOWN_CSV_OUTCOMES
    assert stored_outcome("computationally successful, robust", "reproduction") == \
        "computationally reproducible, robust"


def test_a_reproduction_keeps_the_derived_joined_outcome():
    """The axes carry the judgement. None, not '' — the CHECK rejects the empty
    string, and NULL is the honest 'this row's verdict is not that shape'."""
    from extractor_vocab import stored_outcome
    assert stored_outcome("computational issues, robust", "reproduction") == \
        "computational issues, robust"
    assert stored_outcome("computationally successful, robust", "reproduction") == \
        "computationally reproducible, robust"


def test_a_reproduction_keeps_shared_terminal_values():
    """Neither flat value is a point on the grid, so the axes cannot hold them —
    but they only mean anything when the axes are empty."""
    from extractor_vocab import stored_outcome
    assert stored_outcome("cannot_be_determined", "reproduction") == "cannot_be_determined"
    assert stored_outcome("not_a_replication", "reproduction") == "not_a_replication"


def test_coded_axes_win_over_a_flat_value():
    """The extractor writes 'cannot_be_determined' next to coded axes on 6 of 25
    rows, and on one of those the robustness axis says 'robustness challenges'.
    Keeping both would restate the joined string's own bug in a second column."""
    from extractor_vocab import stored_outcome
    assert stored_outcome(
        "cannot_be_determined", "reproduction", axes_coded=True,
        computation="computational issues", robustness="robust",
    ) == "computational issues, robust"
    assert stored_outcome(
        "computational issues, robust", "reproduction", axes_coded=True,
        computation="cannot_be_determined", robustness="robust",
    ) == "cannot_be_determined"


def test_axes_coded_does_not_affect_a_replication():
    """Replications have no axes; the flag must not reach into their outcome."""
    from extractor_vocab import stored_outcome
    assert stored_outcome("success", "replication", axes_coded=True) == "successful"


def test_a_replication_outcome_is_unaffected():
    from extractor_vocab import stored_outcome
    assert stored_outcome("success", "replication") == "successful"
    assert stored_outcome("", "replication") is None


def test_every_legacy_joined_value_recovers_into_both_axes():
    """The schema migration splits stored joined values rather than deleting them.
    A value that cannot split would be silently dropped instead of migrated."""
    from extractor_vocab import (OUTCOME_COMPUTATION_VALUES, OUTCOME_ROBUSTNESS_VALUES,
                                 LEGACY_JOINED_OUTCOMES, split_joined_outcome)
    for joined in LEGACY_JOINED_OUTCOMES:
        computation, robustness = split_joined_outcome(joined)
        assert computation in OUTCOME_COMPUTATION_VALUES, f"{joined!r} -> {computation!r}"
        assert robustness in OUTCOME_ROBUSTNESS_VALUES, f"{joined!r} -> {robustness!r}"


def test_flat_values_do_not_split():
    """'cannot_be_determined' is not a pair; splitting it would invent an axis."""
    from extractor_vocab import split_joined_outcome
    assert split_joined_outcome("cannot_be_determined") == (None, None)
    assert split_joined_outcome("successful") == (None, None)
    assert split_joined_outcome("") == (None, None)


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
