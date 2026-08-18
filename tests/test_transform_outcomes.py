"""Tests for transform_sources.derive_outcome — the entry-sheets outcome mapping.

The bug: an outcome spelling absent from outcome_alias fell through to the
published export verbatim (`return aliases.get(raw, raw)`). That is the same
silent-drift failure that cost five weeks on the extractor side — a vocabulary
change reaches published data and nothing reports it. Unknown values are now
collected and the transform refuses to produce an export.
"""
import pytest

from extractor_vocab import FLAWED_OUTCOME, FLAWED_OUTCOME_ALIASES, REPLICATION_OUTCOMES
from transform_sources import derive_outcome

# What db_schema.sql seeds into outcome_alias.
_ALIASES = {
    "successful": "successful",
    "failed": "failed",
    "mixed": "mixed",
    "uninformative": "uninformative",
    "unclear": "cannot_be_determined",
    "descriptive": "descriptive only",
    "descriptive only": "descriptive only",
    FLAWED_OUTCOME: FLAWED_OUTCOME,
    **{alias: FLAWED_OUTCOME for alias in FLAWED_OUTCOME_ALIASES},
}
def _problems():
    return {"unknown_alias": [], "bad_alias": []}


def _replication(outcome):
    return {"type": "replication", "outcome": outcome, "display_id": "R-1"}


def _reproduction(computational, robustness):
    return {"type": "reproduction", "display_id": "R-2",
            "outcome_computation": computational, "outcome_robustness": robustness}


# ---------------------------------------------------------------------------
# Replications
# ---------------------------------------------------------------------------

def test_known_spelling_is_normalised():
    p = _problems()
    assert derive_outcome(_replication("descriptive only"), _ALIASES, p) == "descriptive only"
    assert p["unknown_alias"] == []


def test_blank_outcome_stays_blank_and_is_not_an_error():
    """An empty source cell is a known state, not drift."""
    p = _problems()
    assert derive_outcome(_replication(""), _ALIASES, p) is None
    assert p["unknown_alias"] == []


def test_unknown_spelling_is_collected_not_passed_through():
    """The regression. Previously this returned the raw string, which then went
    straight into the FLoRA export under a spelling nothing else recognises."""
    p = _problems()
    result = derive_outcome(_replication("mostly worked tbh"), _ALIASES, p)
    assert result is None
    assert p["unknown_alias"] == [("R-1", "mostly worked tbh")]


def test_alias_pointing_at_a_rejected_value_is_collected():
    """A bad alias row is as damaging as a missing one — it republishes a real
    outcome as the wrong category, silently. This is what the flawed category's
    old 'mixed' mapping did."""
    p = _problems()
    aliases = {**_ALIASES, "something": "not_a_real_category"}
    assert derive_outcome(_replication("something"), aliases, p) is None
    assert p["bad_alias"] == [("R-1", "something", "not_a_real_category")]


# ---------------------------------------------------------------------------
# The flawed category
# ---------------------------------------------------------------------------

def test_all_three_flawed_spellings_normalise_to_one():
    """The extractor writes one spelling, the entry sheets two others; all three
    are live data, so all three land on the same stored category."""
    for spelling in (FLAWED_OUTCOME, *FLAWED_OUTCOME_ALIASES):
        p = _problems()
        assert derive_outcome(_replication(spelling), _ALIASES, p) == FLAWED_OUTCOME
        assert p["unknown_alias"] == [] and p["bad_alias"] == []


def test_flawed_is_its_own_category_not_mixed_or_successful():
    """It was seeded as an alias of 'mixed'. It is a category in its own right."""
    assert FLAWED_OUTCOME in REPLICATION_OUTCOMES
    p = _problems()
    assert derive_outcome(_replication(FLAWED_OUTCOME), _ALIASES, p) not in ("mixed", "successful")


def test_flawed_is_not_a_reproduction_axis_outcome():
    from extractor_vocab import REPRODUCTION_OUTCOMES
    assert FLAWED_OUTCOME not in REPRODUCTION_OUTCOMES


# ---------------------------------------------------------------------------
# Reproductions no longer derive a label at all
# ---------------------------------------------------------------------------

def test_a_reproduction_has_a_derived_outcome():
    """The two axes are exported as themselves. Deriving one label from them could
    not represent two independent verdicts, and lost one whenever the other was
    undetermined."""
    p = _problems()
    row = _reproduction("computationally reproducible", "robust")
    assert derive_outcome(row, _ALIASES, p) == "computationally reproducible, robust"


def test_a_blank_reproduction_outcome_is_not_reported_as_a_problem():
    """It is blank by design, so it must not show up as drift or a missing mapping —
    that noise is what would hide a real one."""
    p = _problems()
    derive_outcome(_reproduction("technical failure", "robust"), _ALIASES, p)
    assert p == {"unknown_alias": [], "bad_alias": []}


def test_technical_failure_needs_no_lookup_row():
    """Under the old derivation this pair had no reproduction_outcome_map entry and
    was reported as unmapped. With the axes exported directly there is nothing to
    map, so a new axis value cannot stall the export."""
    p = _problems()
    assert derive_outcome(
        _reproduction("technical failure", "cannot_be_determined"), _ALIASES, p
    ) == "cannot_be_determined"
    assert not any(p.values())


def test_the_lookup_table_is_dropped():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "db_schema.sql").read_text(encoding="utf-8")
    assert "DROP TABLE IF EXISTS reproduction_outcome_map;" in sql
    assert "CREATE TABLE IF NOT EXISTS reproduction_outcome_map" not in sql


def test_the_axes_are_in_the_exported_column_set():
    from transform_sources import FLORA_COLUMNS
    for column in ["outcome_computation", "outcome_computational_quote",
                   "out_quote_computational_source", "outcome_robustness",
                   "outcome_robustness_quote", "out_quote_robust_source"]:
        assert column in FLORA_COLUMNS, f"{column} is not exported"


def test_the_new_columns_go_last():
    """Positional readers of the existing export must keep working."""
    from transform_sources import FLORA_COLUMNS
    assert FLORA_COLUMNS[-6:] == [
        "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
        "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source"]


# ---------------------------------------------------------------------------
# The schema seed and the Python vocabulary must agree
# ---------------------------------------------------------------------------

def test_schema_seeds_every_flawed_spelling_onto_the_category():
    """db_schema.sql's outcome_alias and extractor_vocab are the same mapping
    written twice; a disagreement silently republishes the wrong category."""
    import re
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "db_schema.sql").read_text(encoding="utf-8")
    seed = re.search(r"INSERT INTO outcome_alias .*?ON CONFLICT", sql, re.S)
    assert seed, "could not locate the outcome_alias seed"
    for spelling in (FLAWED_OUTCOME, *FLAWED_OUTCOME_ALIASES):
        assert re.search(rf"\('{re.escape(spelling)}',\s*'{re.escape(FLAWED_OUTCOME)}'\)", seed.group(0)), \
            f"{spelling!r} is not seeded onto {FLAWED_OUTCOME!r}"
