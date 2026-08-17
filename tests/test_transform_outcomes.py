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
    "descriptive only": "descriptive",
    FLAWED_OUTCOME: FLAWED_OUTCOME,
    **{alias: FLAWED_OUTCOME for alias in FLAWED_OUTCOME_ALIASES},
}
_REPRO_MAP = {("computationally reproducible", "robust"): "computationally reproducible, robust"}


def _problems():
    return {"repro_unmapped": [], "unknown_alias": [], "bad_alias": []}


def _replication(outcome):
    return {"type": "replication", "outcome": outcome, "display_id": "R-1"}


def _reproduction(computational, robustness):
    return {"type": "reproduction", "display_id": "R-2",
            "outcome_computational": computational, "outcome_robustness": robustness}


# ---------------------------------------------------------------------------
# Replications
# ---------------------------------------------------------------------------

def test_known_spelling_is_normalised():
    p = _problems()
    assert derive_outcome(_replication("descriptive only"), _ALIASES, _REPRO_MAP, p) == "descriptive"
    assert p["unknown_alias"] == []


def test_blank_outcome_stays_blank_and_is_not_an_error():
    """An empty source cell is a known state, not drift."""
    p = _problems()
    assert derive_outcome(_replication(""), _ALIASES, _REPRO_MAP, p) is None
    assert p["unknown_alias"] == []


def test_unknown_spelling_is_collected_not_passed_through():
    """The regression. Previously this returned the raw string, which then went
    straight into the FLoRA export under a spelling nothing else recognises."""
    p = _problems()
    result = derive_outcome(_replication("mostly worked tbh"), _ALIASES, _REPRO_MAP, p)
    assert result is None
    assert p["unknown_alias"] == [("R-1", "mostly worked tbh")]


def test_alias_pointing_at_a_rejected_value_is_collected():
    """A bad alias row is as damaging as a missing one — it republishes a real
    outcome as the wrong category, silently. This is what the flawed category's
    old 'mixed' mapping did."""
    p = _problems()
    aliases = {**_ALIASES, "something": "not_a_real_category"}
    assert derive_outcome(_replication("something"), aliases, _REPRO_MAP, p) is None
    assert p["bad_alias"] == [("R-1", "something", "not_a_real_category")]


# ---------------------------------------------------------------------------
# The flawed category
# ---------------------------------------------------------------------------

def test_all_three_flawed_spellings_normalise_to_one():
    """The extractor writes one spelling, the entry sheets two others; all three
    are live data, so all three land on the same stored category."""
    for spelling in (FLAWED_OUTCOME, *FLAWED_OUTCOME_ALIASES):
        p = _problems()
        assert derive_outcome(_replication(spelling), _ALIASES, _REPRO_MAP, p) == FLAWED_OUTCOME
        assert p["unknown_alias"] == [] and p["bad_alias"] == []


def test_flawed_is_its_own_category_not_mixed_or_successful():
    """It was seeded as an alias of 'mixed'. It is a category in its own right."""
    assert FLAWED_OUTCOME in REPLICATION_OUTCOMES
    p = _problems()
    assert derive_outcome(_replication(FLAWED_OUTCOME), _ALIASES, _REPRO_MAP, p) not in ("mixed", "successful")


def test_flawed_applies_to_reproductions_too():
    from extractor_vocab import REPRODUCTION_OUTCOMES
    assert FLAWED_OUTCOME in REPRODUCTION_OUTCOMES


# ---------------------------------------------------------------------------
# Reproductions keep their softer handling
# ---------------------------------------------------------------------------

def test_mapped_reproduction_pair_resolves():
    p = _problems()
    row = _reproduction("computationally reproducible", "robust")
    assert derive_outcome(row, _ALIASES, _REPRO_MAP, p) == "computationally reproducible, robust"


def test_unmapped_reproduction_pair_is_reported_separately():
    """Kept distinct from unknown_alias on purpose: both axes are known values that
    survive in source_records, so it is a lookup row waiting to be added rather
    than data we failed to understand — a warning, not a refusal."""
    p = _problems()
    row = _reproduction("technical failure", "robust")
    assert derive_outcome(row, _ALIASES, _REPRO_MAP, p) is None
    assert p["repro_unmapped"] == [("R-2", ("technical failure", "robust"))]
    assert p["unknown_alias"] == []


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
