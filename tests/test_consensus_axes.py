"""Consensus resolution for the two reproduction axes.

The axes are independent by definition — a reproduction can fail computationally
and still find the conclusion robust. So each resolves on its own: two validators
can agree about the computation and disagree about robustness, and collapsing that
into one decision would throw away the half they agreed on.
"""
from consensus_engine import _CORRECTION_FIELDS, _resolve_axes, _resolve_final

_RECORD = {
    "study_r": "1", "title_r": "Rep", "url_r": None,
    "doi_o": "10.1/o", "study_o": "2", "title_o": "Orig",
    "outcome": "computationally reproducible, robust", "type": "reproduction",
    "outcome_quote": "", "out_quote_source": None, "abstract_r": "an abstract",
    "outcome_computation": "computationally reproducible",
    "outcome_computational_quote": "the code ran",
    "out_quote_computational_source": "results",
    "outcome_robustness": "robust",
    "outcome_robustness_quote": "held under alternatives",
    "out_quote_robust_source": "discussion",
}


def _human(**overrides):
    base = {
        "corrected_outcome_computation": None, "corrected_computational_quote": None,
        "corrected_computational_source": None, "corrected_outcome_robustness": None,
        "corrected_robustness_quote": None, "corrected_robustness_source": None,
    }
    base.update(overrides)
    return base


def test_unjudged_axes_fall_back_to_the_extractor():
    out = _resolve_axes(_RECORD, _human(), _human())
    assert out["outcome_computation"] == "computationally reproducible"
    assert out["outcome_robustness"] == "robust"
    assert out["outcome_computational_quote"] == "the code ran"
    assert out["out_quote_robust_source"] == "discussion"


def test_each_axis_falls_back_on_its_own():
    """Correcting one axis must not disturb the other. If a single fallback covered
    both, overriding the computation would silently drop the robustness verdict."""
    winner = _human(corrected_outcome_computation="technical failure")
    out = _resolve_axes(_RECORD, winner, _human())
    assert out["outcome_computation"] == "technical failure"
    assert out["outcome_robustness"] == "robust"          # untouched


def test_the_winner_decides_a_contested_axis():
    winner = _human(corrected_outcome_computation="computational issues")
    other  = _human(corrected_outcome_computation="technical failure")
    out = _resolve_axes(_RECORD, winner, other)
    assert out["outcome_computation"] == "computational issues"


def test_longest_quote_wins_per_axis():
    """Same rule as the replication outcome quote: more context is not a
    disagreement. Applied per axis, so a long computational quote cannot displace
    the robustness one."""
    winner = _human(corrected_computational_quote="short")
    other  = _human(corrected_computational_quote="a considerably longer quote")
    out = _resolve_axes(_RECORD, winner, other)
    assert out["outcome_computational_quote"] == "a considerably longer quote"
    assert out["outcome_robustness_quote"] == "held under alternatives"


def test_quote_and_source_are_selected_as_one_evidence_pair():
    out = _resolve_axes(
        _RECORD,
        _human(corrected_computational_quote="short", corrected_computational_source="results"),
        _human(
            corrected_computational_quote="a considerably longer quote",
            corrected_computational_source="discussion",
        ),
    )
    assert out["outcome_computational_quote"] == "a considerably longer quote"
    assert out["out_quote_computational_source"] == "discussion"


def test_a_source_without_its_own_quote_does_not_replace_extractor_evidence():
    out = _resolve_axes(
        _RECORD,
        _human(corrected_computational_source="introduction"),
        _human(corrected_computational_source="discussion"),
    )
    assert out["outcome_computational_quote"] == "the code ran"
    assert out["out_quote_computational_source"] == "results"


def test_axes_are_absent_from_a_replication_record():
    """A replication has no axis values, so every axis field resolves to None
    rather than inventing one."""
    record = {k: v for k, v in _RECORD.items() if "computation" not in k and "robust" not in k}
    record["type"] = "replication"
    out = _resolve_axes(record, _human(), _human())
    assert all(v is None for v in out.values())


def test_resolve_final_carries_the_axes():
    """_resolve_final is what _update_status and the validated insert read."""
    final = _resolve_final(_RECORD, _human(corrected_outcome_robustness="robustness challenges"), _human())
    assert final["outcome_robustness"] == "robustness challenges"
    assert final["outcome_computation"] == "computationally reproducible"
    # The replication fields still resolve alongside them.
    assert final["type"] == "reproduction"
    assert final["doi_o"] == "10.1/o"


def test_reclassifying_to_replication_clears_reproduction_shape():
    final = _resolve_final(
        _RECORD,
        _human(type_check="incorrect", corrected_type="replication", corrected_outcome="successful"),
    )
    assert final["type"] == "replication"
    assert final["outcome"] == "successful"
    for field in (
        "outcome_computation", "outcome_computational_quote",
        "out_quote_computational_source", "outcome_robustness",
        "outcome_robustness_quote", "out_quote_robust_source",
    ):
        assert final[field] is None


def test_reclassifying_to_replication_never_reuses_the_joined_outcome():
    final = _resolve_final(_RECORD, _human(type_check="incorrect", corrected_type="replication"))
    assert final["outcome"] == "cannot_be_determined"


def test_contradictory_type_correction_is_ignored_for_legacy_rows():
    final = _resolve_final(
        _RECORD,
        _human(type_check="correct", corrected_type="not_validation"),
    )
    assert final["type"] == "reproduction"


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------

def test_axis_values_count_toward_corrections_agreeing():
    """Two validators coding an axis differently is a real conflict and must reach
    review, not be silently resolved in the winner's favour."""
    assert "corrected_outcome_computation" in _CORRECTION_FIELDS
    assert "corrected_outcome_robustness" in _CORRECTION_FIELDS


def test_axis_quotes_do_not_count_toward_corrections_agreeing():
    """Mirrors corrected_outcome_quote, which is also excluded: a longer quote is
    extra context, not a disagreement, and sending those to review would stall
    records over wording."""
    for field in ("corrected_computational_quote", "corrected_robustness_quote",
                  "corrected_computational_source", "corrected_robustness_source"):
        assert field not in _CORRECTION_FIELDS
