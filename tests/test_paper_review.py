"""The rules of the paper review that need no database."""
from types import SimpleNamespace

import pytest

import paper_review
from consensus_engine import marked_wrong_without_replacement as no_original, removal_proposed

WRONG = {"type_check": "correct", "original_check": "incorrect", "outcome_check": "not_applicable"}
PAIR = {"type": "replication", "doi_r": "10.1/x", "outcome": "failed", "abstract_r": "Text."}


@pytest.mark.parametrize("judgement, expected", [
    (WRONG, True),
    ({**WRONG, "outcome_check": "correct"}, True),   # from a page that still asked for it
    ({**WRONG, "corrected_doi_o": "10.1/right"}, False),
    ({**WRONG, "corrected_title_o": "The right paper"}, False),
    ({**WRONG, "corrected_doi_o": "  "}, True),
    ({**WRONG, "additional_checks": {"was_unsure_original": True}}, False),
    ({**WRONG, "additional_checks": {"senior_reject": True}}, False),
    ({**WRONG, "type_check": "incorrect", "corrected_type": "not_validation"}, False),
    ({**WRONG, "original_check": "correct"}, False),
])
def test_what_counts_as_no_original(judgement, expected):
    assert no_original(judgement) is expected


def test_removal_needs_both_validators():
    assert removal_proposed(WRONG, WRONG) is True
    assert removal_proposed(WRONG, {**WRONG, "corrected_doi_o": "10.1/right"}) is False
    assert removal_proposed(WRONG, None) is False


@pytest.mark.parametrize("value, on", [("1", True), ("true", True), ("ON", True), ("0", False), ("", False), ("no", False)])
def test_the_flag(monkeypatch, value, on):
    monkeypatch.setenv("PAPER_REVIEW_ENABLED", value)
    assert paper_review.enabled() is on
    assert paper_review.available(PAIR) is on


@pytest.mark.parametrize("change", [
    {"type": "reproduction"}, {"doi_r": " "}, {"outcome": "cannot_be_determined"}, {"abstract_r": ""},
])
def test_what_keeps_the_one_record_screen(monkeypatch, change):
    monkeypatch.setenv("PAPER_REVIEW_ENABLED", "1")
    assert paper_review.available({**PAIR, **change}) is False


@pytest.mark.parametrize("checks", [None, "x", {}, {"paper_review": "x"}, {"paper_review": {"outcome_group": 3}}])
def test_a_malformed_note_earns_ordinary_points(checks):
    assert paper_review.marker(checks) is None


def test_points_use_the_paper_rules():
    class Cursor:
        def __init__(self, prior): self.prior = prior
        def execute(self, *_): pass
        def fetchall(self): return self.prior

    def req(**fields):
        return SimpleNamespace(**{"validator_notes": None, "type_check": "correct", "corrected_type": None,
                                  "outcome_check": "correct", **fields})

    rec = {"doi_r": "10.1/x", "record_id": "r"}
    note = {"outcome_group": "g"}
    earlier = {"additional_checks": {"paper_review": note}, "validator_notes": None}
    assert paper_review.points(Cursor([]), rec, 1, req(), 10, False, note) == 14
    assert paper_review.points(Cursor([earlier]), rec, 1, req(), 10, False, note) == 2
    assert paper_review.points(Cursor([earlier]), rec, 1, req(), 10, True, {"outcome_group": "h"}) == 5
    assert paper_review.points(Cursor([]), rec, 1, req(outcome_check="not_applicable"), 10, False, note) == 12
