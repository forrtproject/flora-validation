"""Tests for the validate_flora port — the structural checks over the dataset.

The checks decide what gets reported to humans as a data problem, so the tests
pin the boundaries: what counts as a bad year, a bad DOI, a duplicate.
"""
import pandas as pd
import pytest

import validate_flora as vf


def _row(**over):
    row = {"doi_o": "10.1177/0956797611432497", "doi_r": "10.1037/xge0000067",
           "url_r": None, "url_o": None, "oa_url_o": None, "oa_url_r": None,
           "title_o": "Original", "title_r": "Replication",
           "type": "replication", "source": "validated", "outcome": "successful",
           "year_o": "2011", "year_r": "2015",
           "apa_ref_o": "Smith (2011)", "apa_ref_r": "Jones (2015)"}
    row.update(over)
    return row


def _df(*rows):
    return pd.DataFrame([_row(**r) for r in (rows or [{}])])


def test_a_clean_row_raises_nothing():
    report = vf.validate(_df())
    assert not report["has_issues"], [r for r in report["results"] if not r["passed"]]


# ── identifiers ───────────────────────────────────────────────────────────────

def test_row_id_matches_the_r_reports_format():
    assert vf.row_id(_row()) == "10.1177/0956797611432497 | 10.1037/xge0000067"


def test_row_id_falls_back_to_url_when_there_is_no_doi_r():
    assert vf.row_id(_row(doi_r=None, url_r="https://osf.io/x")).endswith("https://osf.io/x")


def test_item_id_strips_the_detail():
    assert vf.item_id("10.1/a | 10.2/b: type='bogus'") == "10.1/a | 10.2/b"
    assert vf.item_id("`10.1/a`: retracted") == "10.1/a"


# ── individual checks ─────────────────────────────────────────────────────────

def test_literal_na_strings_are_flagged():
    assert vf._check_na_strings(_df({"title_r": "NA"}))


def test_a_real_title_containing_na_is_not_flagged():
    """Only the whole value counts — 'Nanotechnology' contains 'NA'."""
    assert not vf._check_na_strings(_df({"title_r": "Nanotechnology studies"}))


def test_a_url_column_holding_a_title_is_flagged():
    assert vf._check_urls(_df({"url_o": "When Does Regulation Distort Costs?"}))


def test_a_real_url_passes():
    assert not vf._check_urls(_df({"url_r": "https://osf.io/x"}))


@pytest.mark.parametrize("year", ["1889", "2999"])
def test_implausible_years_are_flagged(year):
    assert vf._check_year_ranges(_df({"year_o": year}))


def test_plausible_years_pass():
    assert not vf._check_year_ranges(_df({"year_o": "1890", "year_r": "2024"}))


def test_a_replication_before_its_original_is_flagged():
    assert vf._check_year_order(_df({"year_o": "2015", "year_r": "2011"}))


def test_missing_years_are_not_an_ordering_problem():
    assert not vf._check_year_order(_df({"year_o": None, "year_r": "2011"}))


@pytest.mark.parametrize("doi", ["not-a-doi", "10.1/x", "doi:10.1234/x"])
def test_bad_doi_formats_are_flagged(doi):
    assert vf._check_doi_format(_df({"doi_o": doi}))


def test_a_good_doi_passes():
    assert not vf._check_doi_format(_df({"doi_o": "10.1234/abc.def"}))


def test_an_unknown_source_is_flagged():
    assert vf._check_sources(_df({"source": "unknown source"}))


def test_our_registry_keys_pass():
    """Both preserved notebook labels and the website registry keys are valid."""
    for source in vf.VALID_SOURCES:
        assert not vf._check_sources(_df({"source": source}))


def test_an_unknown_type_is_flagged():
    assert vf._check_types(_df({"type": "extension"}))


def test_a_blank_outcome_is_flagged():
    assert vf._check_outcomes(_df({"outcome": None}))


@pytest.mark.parametrize("outcome", ["successful", "computationally reproducible, nonsense", "not checked"])
def test_invalid_reproduction_outcome_is_flagged(outcome):
    assert vf._check_outcomes(_df({"type": "reproduction", "outcome": outcome}))


@pytest.mark.parametrize("outcome", ["computationally reproducible, robust",
                                    "computationally successful, robustness not checked",
                                    "computational issues, not checked"])
def test_current_and_supplied_reproduction_outcomes_are_valid(outcome):
    assert not vf._check_outcomes(_df({"type": "reproduction", "outcome": outcome}))


def test_exact_duplicate_pairs_are_flagged():
    frame = _df({}, {})
    assert vf._check_exact_duplicates(frame)


def test_different_pairs_are_not_duplicates():
    assert not vf._check_exact_duplicates(_df({}, {"doi_r": "10.9/other"}))


def test_missing_required_fields_are_flagged():
    assert vf._check_required_fields(_df({"title_r": None}))
    assert vf._check_required_fields(_df({"doi_r": None, "url_r": None}))


def test_a_url_only_row_satisfies_the_identifier_requirement():
    assert not vf._check_required_fields(_df({"doi_r": None, "url_r": "https://osf.io/x"}))


# ── edit distance ─────────────────────────────────────────────────────────────

def test_edit_distance_matches_adist():
    assert vf._edit_distance("kitten", "sitting") == 3
    assert vf._edit_distance("same", "same") == 0
    assert vf._edit_distance("", "abc") == 3


def test_conflicting_references_for_one_doi_are_flagged():
    frame = _df({}, {"apa_ref_o": "A completely different reference string here"})
    assert vf._check_reference_conflicts(frame)


def test_references_differing_only_by_a_doi_suffix_are_not_a_conflict():
    """The comparison strips embedded DOIs and study numbers first."""
    frame = _df({"apa_ref_o": "Smith (2011) study 1 doi:10.1/a"},
                {"apa_ref_o": "Smith (2011) study 2 doi:10.1/a"})
    assert not vf._check_reference_conflicts(frame)


# ── suppression and rendering ─────────────────────────────────────────────────

def test_a_suppressed_item_disappears():
    frame = _df({"type": "extension"})
    label = vf._check_types(frame)[0]
    report = vf.validate(frame, {("Type values valid", vf.item_id(label))})
    types = next(r for r in report["results"] if r["name"] == "Type values valid")
    assert types["passed"] and report["suppressed"] == 1


def test_the_report_renders_checkboxes():
    text = vf.render(vf.validate(_df({"type": "extension"})))
    assert "- [ ] " in text and "FLoRA Data Validation Report" in text


def test_passed_checks_are_ticked():
    assert "- [x] Type values valid" in vf.render(vf.validate(_df()))


def test_long_sections_are_folded():
    frame = pd.DataFrame([_row(type="extension", doi_o=f"10.1/{i}") for i in range(20)])
    assert "<details>" in vf.render(vf.validate(frame))
