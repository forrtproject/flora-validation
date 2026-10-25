"""Tests for the validate_flora port — the structural checks over the dataset.

The checks decide what gets reported to humans as a data problem, so the tests
pin the boundaries: what counts as a bad year, a bad DOI, a duplicate, and which
level a finding is reported at.
"""
import time

import pandas as pd
import pytest

import transform_sources
import validate_flora as vf


def _row(**over):
    row = {"flora_id": "REPL-000001",
           "doi_o": "10.1177/0956797611432497", "doi_r": "10.1037/xge0000067",
           "url_r": None, "url_o": None, "oa_url_o": None, "oa_url_r": None,
           "alt_identifier_o": None, "oa_work_id_o": None,
           "title_o": "Original", "title_r": "Replication",
           "type": "replication", "source": "validated", "outcome": "successful",
           "year_o": "2011", "year_r": "2015",
           "apa_ref_o": "Smith (2011)", "apa_ref_r": "Jones (2015)"}
    row.update(over)
    return row


def _df(*rows):
    return pd.DataFrame([_row(**r) for r in (rows or [{}])])


def _numbered(*rows):
    """Rows with their own FLoRA IDs, REPL-000001 on."""
    return _df(*[{"flora_id": f"REPL-{n:06d}", **r} for n, r in enumerate(rows, 1)])


def _details(items):
    return [item["detail"] for item in items]


def test_a_clean_row_raises_nothing():
    report = vf.validate(_df())
    assert not report["has_issues"], [r for r in report["results"] if not r["passed"]]
    assert report["problems"] == report["possible_problems"] == 0


# ── identifiers and findings ──────────────────────────────────────────────────

def test_row_id_matches_the_r_reports_format():
    assert vf.row_id(_row()) == "10.1177/0956797611432497 | 10.1037/xge0000067"


def test_row_id_falls_back_to_url_when_there_is_no_doi_r():
    assert vf.row_id(_row(doi_r=None, url_r="https://osf.io/x")).endswith("https://osf.io/x")


def test_a_finding_is_a_small_record_naming_its_rows():
    item = vf._check_types(_df({"type": "extension"}))[0]
    assert item == {"check": "invalid_type", "flora_ids": ["REPL-000001"], "column": "type",
                    "value": "extension", "detail": "type is neither replication nor reproduction",
                    "label": "10.1177/0956797611432497 | 10.1037/xge0000067",
                    "key": "invalid_type|REPL-000001|type|extension"}


def test_the_key_is_check_sorted_ids_column_and_normalised_value():
    key = vf.finding_key("reversed_pair", ["REPL-000009", "REPL-000002"], "doi_o", "  10.1/A  |  10.2/B ")
    assert key == "reversed_pair|REPL-000002,REPL-000009|doi_o|10.1/a | 10.2/b"


def test_the_key_is_stable_while_the_flagged_value_stays():
    """Step 3 keeps a finding open until the value it flagged changes: anything else
    in the row may change without making it a new finding."""
    before = vf._check_types(_df({"type": "extension"}))[0]["key"]
    after = vf._check_types(_df({"type": "extension", "title_o": "Retitled",
                                 "doi_r": "10.9/other"}))[0]["key"]
    changed = vf._check_types(_df({"type": "extensions"}))[0]["key"]
    assert before == after != changed


def test_a_finding_about_many_rows_sorts_their_ids_and_drops_a_shared_label():
    rows = [pd.Series(_row(flora_id="REPL-000002")), pd.Series(_row(flora_id="REPL-000001",
                                                                     doi_r="10.9/x"))]
    item = vf.finding("reversed_pair", rows, "doi_o", "x", "detail")
    assert item["flora_ids"] == ["REPL-000001", "REPL-000002"] and item["label"] == ""


def test_each_item_leads_with_the_flora_id_then_the_pair():
    item = vf._check_types(_df({"type": "extension"}))[0]
    assert vf.item_line(item) == ("REPL-000001 (`10.1177/0956797611432497 | 10.1037/xge0000067`): "
                                  "type is neither replication nor reproduction — `extension`")


@pytest.mark.parametrize("value,shown", [
    ("plain", "`plain`"),
    ("a `tick`", "`` a `tick` ``"),
    # LaTeX quotes, as real references hold them: a double fence would close early.
    ("A Replication of ``Using self-esteem''", "``` A Replication of ``Using self-esteem'' ```"),
    ("`", "`` ` ``"),
    ("x\n  y", "`x y`"),
])
def test_a_value_is_one_code_span_whatever_backticks_it_holds(value, shown):
    assert vf._code(value) == shown


def test_the_label_is_code_so_the_rows_own_values_cannot_become_markdown():
    item = vf._check_types(_df({"type": "extension", "doi_r": None,
                                "url_r": "https://example.org/*a*_`b`"}))[0]
    assert vf.item_line(item).startswith(
        "REPL-000001 (`` 10.1177/0956797611432497 | https://example.org/*a*_`b` ``): ")


def test_text_from_data_in_a_detail_is_escaped():
    """A detail can carry a title Crossref gives (N4); what it says must not render."""
    item = vf.finding("original_doi_found", [pd.Series(_row())], "doi_o", "10.1/x",
                      "Crossref lists 'A *bold* <i>claim</i> [x] `y`'")
    assert vf.item_line(item).split("): ", 1)[1] == (
        r"Crossref lists 'A \*bold\* \<i\>claim\</i\> \[x\] \`y\`' — `10.1/x`")
    assert item["detail"] == "Crossref lists 'A *bold* <i>claim</i> [x] `y`'", "the record keeps the text"


def test_a_long_id_list_is_counted_and_values_cannot_become_markdown():
    rows = [pd.Series(_row(flora_id=f"REPL-{n:06d}")) for n in range(1, 9)]
    item = vf.finding("reference_conflict", rows, "apa_ref_r", "*bold* `tick`\n<b>", "d", label="")
    line = vf.item_line(item)
    assert line.startswith("REPL-000001, REPL-000002, REPL-000003, REPL-000004, REPL-000005 "
                           "and 3 more: d")
    assert line.endswith("— `` *bold* `tick` <b> ``")


def test_count_text_names_each_level():
    assert vf.count_text(3, 1) == "3 problems and 1 possible problem"
    assert vf.count_text(1, 0) == "1 problem"
    assert vf.count_text(0, 2) == "2 possible problems"
    assert vf.count_text(0, 0) == "no problems"


# ── levels ────────────────────────────────────────────────────────────────────

def test_every_check_has_a_code_and_one_of_the_two_levels():
    codes = [check[0] for check in vf.CHECKS]
    assert len(codes) == len(set(codes))
    assert {check[1] for check in vf.CHECKS} == {vf.PROBLEM, vf.POSSIBLE_PROBLEM}


@pytest.mark.parametrize("code,level", [
    ("invalid_url", vf.PROBLEM), ("invalid_doi", vf.PROBLEM), ("duplicate_record", vf.PROBLEM),
    ("retracted_title", vf.PROBLEM), ("missing_required", vf.PROBLEM),
    ("year_order", vf.POSSIBLE_PROBLEM), ("cross_type_pair", vf.POSSIBLE_PROBLEM),
    ("reversed_pair", vf.POSSIBLE_PROBLEM), ("file_name_title", vf.POSSIBLE_PROBLEM)])
def test_the_level_of_each_check(code, level):
    assert next(check[1] for check in vf.CHECKS if check[0] == code) == level


def test_problems_and_possible_problems_are_counted_apart():
    report = vf.validate(_df({"type": "extension", "year_o": "2015", "year_r": "2011"}))
    assert (report["problems"], report["possible_problems"]) == (1, 1)
    assert report["has_problems"] and report["has_issues"]
    assert all(item["level"] == result["level"]
               for result in report["results"] for item in result["items"])


def test_possible_problems_alone_are_not_problems():
    report = vf.validate(_df({"year_o": "2015", "year_r": "2011"}))
    assert report["has_issues"] and not report["has_problems"]


def test_the_report_lists_problems_before_possible_problems():
    text = vf.render(vf.validate(_df({"type": "extension", "year_o": "2015", "year_r": "2011"})))
    assert text.index("### Problems (1)") < text.index("### Possible problems (1)")
    assert text.index("Invalid type values") < text.index("Replication year before original year")
    assert vf.LEVEL_NOTES[vf.POSSIBLE_PROBLEM] in text


def test_the_header_explains_the_item_format():
    text = vf.render(vf.validate(_df({"type": "extension"})))
    assert vf.ITEM_FORMAT in text and "FLoRA ID" in vf.ITEM_FORMAT


# ── NA strings ────────────────────────────────────────────────────────────────

def test_literal_na_strings_are_flagged():
    assert vf._check_na_strings(_df({"title_r": "NA"}))
    assert vf._check_na_strings(_df({"title_r": "N/A"}))


def test_a_real_title_containing_na_is_not_flagged():
    """Only the whole value counts — 'Nanotechnology' contains 'NA'."""
    assert not vf._check_na_strings(_df({"title_r": "Nanotechnology studies"}))


# ── links ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("column,value,detail", [
    ("url_o", "10.1111/j.2047-2927.2012.00013.x", "url_o holds a DOI, not a link"),
    ("url_r", "doi:10.1037/xlm0001650", "url_r holds a DOI, not a link"),
    ("url_o", "Original", "url_o holds the title, not a link"),
    ("url_o", "Senior Role", "url_o holds text, not a link"),
    ("url_o", "www.proquest.com/docview/1470800730", "url_o has no http(s)://"),
    ("url_o", "thekeep.eiu.edu/theses/4783", "url_o has no http(s)://"),
    ("url_r", "https://osf.io/v6zk8 or http://rpubs.com/a/final", "url_r holds two links"),
    ("url_r", "https://osf.io/a; https://osf.io/b", "url_r holds two links"),
    ("url_r", "https://journals.sagepub.com/doi/full/10.1177/2515…", "url_r is cut off"),
    ("url_r", "https://example.org/report...", "url_r is cut off"),
    ("url_o", "https://doi.org/10.1111/ecoj.12233Digital Object Identifier (DOI)",
     "url_o contains spaces"),
    ("oa_url_r", "http://libres.uncg.edu/ir/asu/f/Spring 2018 Thesis.pdf",
     f"oa_url_r contains spaces ({vf.ENRICHED_NOTE})"),
    # urlsplit raises on these; the network check would otherwise request them.
    ("url_r", "https://osf.io]", "url_r has no valid host name"),
    ("url_r", "https://[x]/a", "url_r has no valid host name"),
    ("url_r", "https://", "url_r has no valid host name"),
])
def test_each_kind_of_bad_link_is_named(column, value, detail):
    assert _details(vf._check_urls(_df({column: value}))) == [detail]


def test_a_missing_title_does_not_count_as_a_title():
    assert vf.url_problem("Senior Role", float("nan")) == "holds text, not a link"


def test_a_title_in_a_url_column_is_flagged_even_without_a_match():
    """R's check skipped url_o, which is how 121 titles went unseen there."""
    assert vf._check_urls(_df({"url_o": "When Does Regulation Distort Costs?"}))


@pytest.mark.parametrize("value", [
    "https://osf.io/x",
    # One link each: an archived page, and a link handed on as a parameter.
    "https://web.archive.org/web/20191117063540/http://www.psychfiledrawer.org/replication.php?attempt=MTQ5",
    "http://elibrary.aisnet.org/Default.aspx?url=https://aisel.aisnet.org/cgi/viewcontent.cgi?article=1006",
])
def test_a_real_url_passes(value):
    assert not vf._check_urls(_df({"url_r": value}))


# ── outcomes ──────────────────────────────────────────────────────────────────

def test_a_blank_outcome_is_flagged():
    assert _details(vf._check_outcomes(_df({"outcome": None}))) == ["outcome is blank"]


@pytest.mark.parametrize("record_type", ["replication", "reproduction"])
def test_a_published_not_a_replication_is_a_problem(record_type):
    items = vf._check_outcomes(_df({"type": record_type, "outcome": "not_a_replication"}))
    assert _details(items) == ["outcome says the record is not a replication"]


def test_cannot_be_determined_stays_a_valid_replication_outcome():
    assert not vf._check_outcomes(_df({"outcome": "cannot_be_determined"}))


@pytest.mark.parametrize("outcome", ["successful", "computationally reproducible, nonsense", "not checked"])
def test_invalid_reproduction_outcome_is_flagged(outcome):
    assert vf._check_outcomes(_df({"type": "reproduction", "outcome": outcome}))


@pytest.mark.parametrize("outcome", ["computationally reproducible, robust",
                                    "computationally successful, robustness not checked",
                                    "computational issues, not checked"])
def test_current_and_supplied_reproduction_outcomes_are_valid(outcome):
    assert not vf._check_outcomes(_df({"type": "reproduction", "outcome": outcome}))


# ── years ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("year", ["1599", "2999"])
def test_implausible_years_are_flagged(year):
    assert vf._check_year_ranges(_df({"year_o": year}))


def test_plausible_years_pass():
    """Ebbinghaus's 1880 manuscript is a real original in FLoRA."""
    assert not vf._check_year_ranges(_df({"year_o": "1600", "year_r": "2024"}))
    assert not vf._check_year_ranges(_df({"year_o": "1880"}))


def test_a_replication_before_its_original_is_flagged():
    items = vf._check_year_order(_df({"year_o": "2015", "year_r": "2011"}))
    assert _details(items) == ["year_r is before year_o (2015)"] and items[0]["value"] == "2011"


@pytest.mark.parametrize("over", [
    {"doi_o": "10.31234/osf.io/x6ehu"},                     # the original is a preprint
    {"doi_o": "10.3386/w5398"},                             # an NBER working paper
    {"url_o": "https://doi.org/10.31234/osf.io/x6ehu"},     # VAL-000221
    {"url_o": "https://osf.io/x6ehu"},
    {"url_o": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1"},
    {"alt_identifier_o": "10.1037/a0012833, 10.31234/osf.io/abcde"},
])
def test_a_replication_of_an_earlier_version_is_not_flagged(over):
    assert not vf._check_year_order(_df({"year_o": "2026", "year_r": "2024", **over}))


def test_missing_years_are_not_an_ordering_problem():
    assert not vf._check_year_order(_df({"year_o": None, "year_r": "2011"}))


@pytest.mark.parametrize("over", [{"url_o": "[Re] A title in brackets"}, {"url_o": "https://osf.io]"},
                                  {"alt_identifier_o": "Study [2], 10.1037/a0012833"}])
def test_a_value_urlsplit_refuses_does_not_end_the_check(over):
    """urlsplit raises on a stray bracket: one such cell must not end the report."""
    assert vf._check_year_order(_df({"year_o": "2015", "year_r": "2011", **over}))


@pytest.mark.parametrize("year", ["inf", "Infinity", "-inf", "1e400", "nan"])
def test_a_year_that_is_no_number_is_skipped(year):
    assert vf._year(year) is None
    assert not vf._check_year_ranges(_df({"year_o": year}))


# ── duplicates ────────────────────────────────────────────────────────────────

def test_rows_the_build_would_make_one_record_are_a_problem():
    items = vf._check_duplicate_records(_numbered({}, {}))
    assert len(items) == 1 and items[0]["flora_ids"] == ["REPL-000001", "REPL-000002"]


def test_a_report_link_on_one_of_two_rows_is_still_one_record():
    """Step 5b combines rows of one pair that have at most one url_r between them."""
    assert vf._check_duplicate_records(_numbered({}, {"url_r": "https://osf.io/a"}))


def test_separate_reports_of_one_pair_are_not_duplicates():
    """The pipeline's rule since step 1: two report links are two records."""
    assert not vf._check_duplicate_records(_numbered({"url_r": "https://osf.io/a"},
                                                     {"url_r": "https://osf.io/b"}))


def test_the_duplicate_rule_is_the_builds_own():
    frame = _numbered({}, {"type": "reproduction"}, {"doi_r": "10.9/other"})
    assert not vf._check_duplicate_records(frame)
    assert transform_sources.record_keys(frame).nunique() == 3


def test_rows_without_identifiers_are_not_duplicates_of_each_other():
    assert not vf._check_duplicate_records(_numbered({"doi_o": None, "doi_r": None},
                                                     {"doi_o": None, "doi_r": None}))


def test_one_pair_under_both_types_is_a_possible_problem():
    items = vf._check_cross_type_pairs(_numbered({}, {"type": "reproduction"}))
    assert len(items) == 1 and items[0]["flora_ids"] == ["REPL-000001", "REPL-000002"]
    assert items[0]["value"] == "replication, reproduction"


def test_without_a_doi_r_the_pair_is_matched_on_url_r():
    frame = _numbered({"doi_r": None, "url_r": "https://shs.hal.science/hal-05474766/"},
                      {"doi_r": None, "url_r": "https://shs.hal.science/hal-05474766",
                       "type": "reproduction"})
    assert vf._check_cross_type_pairs(frame)


def test_a_replication_and_a_reproduction_of_one_original_with_different_reports_pass():
    frame = _numbered({"doi_r": None, "url_r": "https://osf.io/udbnf"},
                      {"doi_r": None, "url_r": "https://osf.io/ye5mq", "type": "reproduction"})
    assert not vf._check_cross_type_pairs(frame)


def test_a_pair_entered_both_ways_round_is_a_possible_problem():
    frame = _numbered({"doi_o": "10.1002/bin.1836", "doi_r": "10.1002/jaba.659"},
                      {"doi_o": "10.1002/jaba.659", "doi_r": "10.1002/bin.1836"})
    items = vf._check_reversed_pairs(frame)
    assert len(items) == 1 and items[0]["flora_ids"] == ["REPL-000001", "REPL-000002"]
    assert items[0]["value"] == "10.1002/bin.1836 | 10.1002/jaba.659"


def test_a_pair_entered_once_is_not_reversed():
    assert not vf._check_reversed_pairs(_numbered({}, {"doi_r": "10.9/other"}))


# ── required fields ───────────────────────────────────────────────────────────

def test_missing_required_fields_are_flagged():
    assert _details(vf._check_required_fields(_df({"title_r": None}))) == ["title_r is missing"]
    assert _details(vf._check_required_fields(_df({"doi_r": None, "url_r": None}))) == \
        ["doi_r and url_r are both missing"]


def test_a_url_only_row_satisfies_the_identifier_requirement():
    assert not vf._check_required_fields(_df({"doi_r": None, "url_r": "https://osf.io/x"}))


@pytest.mark.parametrize("identity", [
    {"url_o": "https://www.jstor.org/stable/1911360"},
    {"alt_identifier_o": "10.2139/ssrn.2187902"},
    {"oa_work_id_o": "W2168190474"},
    # A link without its scheme (REPL-001618) or a DOI still names the paper:
    # Invalid URLs reports the form, once.
    {"url_o": "www.proquest.com/docview/1470800730"},
    {"url_o": "10.1111/j.2047-2927.2012.00013.x"},
])
def test_an_original_without_a_doi_but_with_another_identifier_passes(identity):
    """Books, theses and pre-DOI papers have no DOI to give; N4 suggests one where it can."""
    assert not vf._check_required_fields(_df({"doi_o": None, **identity}))


@pytest.mark.parametrize("url_o,detail", [
    (None, "the original has no identifier: no doi_o, url_o, alt_identifier_o or oa_work_id_o"),
    # REPRO-000342: also an Invalid URLs finding, and this one says so.
    ("Original", "the original has no identifier: no doi_o, alt_identifier_o or oa_work_id_o, "
                 "and url_o holds the title, not a link"),
    ("Senior Role", "the original has no identifier: no doi_o, alt_identifier_o or oa_work_id_o, "
                    "and url_o holds text, not a link"),
])
def test_an_original_with_no_identifier_at_all_is_a_problem(url_o, detail):
    items = vf._check_required_fields(_df({"doi_o": None, "url_o": url_o}))
    assert len(items) == 1 and items[0]["column"] == "doi_o"
    assert items[0]["detail"] == detail


# ── DOI format ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doi,detail", [
    ("0.1162/0033553053327524", "doi_o starts with 0. (missing the 1?)"),
    ("https://www.jstor.org/stable/41387554", "doi_o is a link, not a DOI"),
    ("thekeep.eiu.edu/theses/4783", "doi_o is a link, not a DOI"),
    ("art00005", "doi_o is not a DOI"),
    ("djaerv_2018", "doi_o is not a DOI"),
    ("escholarship.orgucitem39d347bk", "doi_o is not a DOI"),
    ("doi:10.1234/x", "doi_o is not a DOI"),
    ("10.1/x", "doi_o is not a DOI"),
    ("10.1108/s0193-230620150000018002?utm_campaign=repec&wt.mc_id=repec",
     "doi_o carries query or tracking text (?…)"),
])
def test_bad_doi_formats_are_flagged_with_what_is_wrong(doi, detail):
    assert _details(vf._check_doi_format(_df({"doi_o": doi}))) == [detail]


@pytest.mark.parametrize("doi", [
    "10.1234/abc.def",
    # Old Wiley SICI DOIs, and the escaped spelling of one, are valid.
    "10.1002/(sici)1097-0266(199704)18:4<303::aid-smj869>3.0.co;2-g",
    "10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y",
    "10.1644/1545-1542(2005)086[0745:bbabss]2.0.co;2",
    "10.1002/(sici)1520-6297(199601/02)12:1<1::aid-agr1>3.0.co;2-#",
    "10.1093/acprof:oso/9780190205782.001.0001",
])
def test_a_good_doi_passes(doi):
    assert not vf._check_doi_format(_df({"doi_o": doi}))


@pytest.mark.parametrize("column", vf.ALT_COLUMNS)
@pytest.mark.parametrize("part,detail", [
    # REPL-001062 holds REPL-001285's DOI, shared with its tracking text.
    ("10.1108/s0193-230620150000018002?utm_campaign=repec&wt.mc_id=repec",
     "lists a DOI that carries query or tracking text (?…)"),
    ("0.1162/0033553053327524", "lists a DOI that starts with 0. (missing the 1?)"),
    ("10.12/x", "lists a DOI that is malformed"),
])
def test_a_bad_doi_in_an_alt_identifier_list_is_flagged(column, part, detail):
    cell = f"10.31234/osf.io/abcde, {part}, https://osf.io/preprints/psyarxiv/nza9g_v1"
    items = vf._check_doi_format(_df({column: cell}))
    assert [(i["column"], i["value"], i["detail"]) for i in items] == [(column, part, f"{column} {detail}")]


@pytest.mark.parametrize("cell", [
    "10.31234/osf.io/abcde",
    "10.1037/a0012833, 10.31234/osf.io/abcde",
    # The lists also hold links and other identifiers, which are not DOIs to check.
    "https://doi.org/10.1037/e653412011-001",
    "https://ejwagenmakers.com/2012/Appendix_PoPS_WagenmakersEtAl.pdf, 10.1037/a0012833",
    "a meta paper, ",
])
def test_good_alt_identifier_lists_pass(cell):
    assert not vf._check_doi_format(_df({"alt_identifier_r": cell, "alt_identifier_o": cell}))


# ── conflicting references ────────────────────────────────────────────────────

def test_edit_distance_matches_adist():
    assert vf._edit_distance("kitten", "sitting") == 3
    assert vf._edit_distance("same", "same") == 0
    assert vf._edit_distance("", "abc") == 3


def test_conflicting_references_for_one_doi_are_flagged():
    frame = _numbered({}, {"apa_ref_o": "A completely different reference string here"})
    items = vf._check_reference_conflicts(frame)
    assert len(items) == 1 and items[0]["flora_ids"] == ["REPL-000001", "REPL-000002"]
    assert items[0]["value"] == "10.1177/0956797611432497"


def test_references_differing_only_by_a_doi_suffix_are_not_a_conflict():
    """The comparison strips embedded DOIs and study numbers first."""
    frame = _df({"apa_ref_o": "Smith (2011) study 1 doi:10.1/a"},
                {"apa_ref_o": "Smith (2011) study 2 doi:10.1/a"})
    assert not vf._check_reference_conflicts(frame)


def test_the_meta_paper_annotation_is_not_a_conflict():
    """The transform appends its note and the row's own report link to every
    reference of a meta-paper: 95 "different" references of the Boyce paper."""
    ref = "Boyce, V. (2023). Eleven years of student replication projects."
    frame = _numbered(*[{"apa_ref_r": f"{ref} {transform_sources.META_PAPER_NOTE} "
                                      f"https://osf.io/report-{n}-with-a-long-path"}
                        for n in range(95)])
    started = time.perf_counter()
    assert not vf._check_reference_conflicts(frame)
    assert time.perf_counter() - started < 5


def test_one_wrong_reference_among_agreeing_ones_is_reported():
    """The largest distance: R's smallest would hide it behind the two that agree."""
    frame = _numbered({}, {}, {"apa_ref_o": "Doe (1999). Something else entirely, in another journal."})
    assert vf._check_reference_conflicts(frame)


# ── sources and types ─────────────────────────────────────────────────────────

def test_an_unknown_source_is_flagged():
    assert vf._check_sources(_df({"source": "unknown source"}))


def test_every_label_the_build_publishes_passes():
    for source in vf.VALID_SOURCES:
        assert not vf._check_sources(_df({"source": source}))


def test_the_allowed_sources_come_from_the_transforms_mapping():
    """So a registry key that a broken mapping lets through is reported."""
    from adjudication import review
    import sync_validated
    assert vf.VALID_SOURCES == (set(transform_sources.OUTPUT_SOURCES.values())
                                | {sync_validated.SOURCE_KEY, review.SOURCE_KEY})
    for key in transform_sources.OUTPUT_SOURCES:
        assert vf._check_sources(_df({"source": key})), key


def test_an_unknown_type_is_flagged():
    assert vf._check_types(_df({"type": "extension"}))


# ── titles ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "RETRACTED: Betatrophin: A Hormone that Controls Pancreatic β Cell Proliferation",
    "Retracted: Growing up in a Recession",
    "RETRACTED BY THE AUTHORS: Dividend Taxes and the Allocation of Capital",
    "Retracted article: Something",
    "WITHDRAWN: A preprint",
    "Retraction: Ego depletion revisited",
    "Retraction Note to: A finding",
    "Super Size Me (Retracted)",
    "A finding [RETRACTED ARTICLE]",
    "Effects of ego depletion on self-control, RETRACTED",
])
def test_a_title_marking_a_retraction_is_a_problem(title):
    items = vf._check_retracted_titles(_df({"title_o": title}))
    assert _details(items) == ["title_o marks the paper retracted or withdrawn"]


@pytest.mark.parametrize("title", [
    "Retracted papers keep being cited",
    "Retractions in psychology: a decade of notices",
    "Why researchers withdraw from replication projects",
    "RETRACTED SCIENCE AND THE PUBLIC",                    # a title in capitals
    "Retraction and replication",
])
def test_a_title_about_retractions_is_not_flagged(title):
    assert not vf._check_retracted_titles(_df({"title_o": title}))


@pytest.mark.parametrize("title,detail", [
    ("Wetzel_EurJournPersonality_2016_Ovkm_Somo_78gg", "title_r looks like a file or project name"),
    ("Analysis", "title_r is a generic name, not a title"),
    ("Final Report", "title_r is a generic name, not a title"),
])
def test_a_title_r_that_is_a_file_or_component_name_is_a_possible_problem(title, detail):
    assert _details(vf._check_file_name_titles(_df({"title_r": title}))) == [detail]


@pytest.mark.parametrize("title", ["Embodying Power", "Power Posing", "Analysis of variance revisited",
                                   "A test of p_rep"])
def test_short_or_unusual_real_titles_pass(title):
    assert not vf._check_file_name_titles(_df({"title_r": title}))


# ── suppression and rendering ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["invalid_type", "Type values valid", "Invalid type values"])
@pytest.mark.parametrize("item_id", ["invalid_type|REPL-000001|type|extension", "REPL-000001"])
def test_a_suppressed_item_disappears(name, item_id):
    report = vf.validate(_df({"type": "extension"}), {(name, item_id)})
    types = next(r for r in report["results"] if r["code"] == "invalid_type")
    assert types["passed"] and report["suppressed"] == 1 and report["problems"] == 0


def test_a_suppression_for_another_row_keeps_the_item():
    report = vf.validate(_df({"type": "extension"}), {("invalid_type", "REPL-000002")})
    assert report["problems"] == 1 and report["suppressed"] == 0


def test_the_report_lists_each_issue_without_a_checkbox():
    """Nothing reads a tick back here (fred-data's report is a GitHub issue; this one
    is a file), so the report offers none and does not ask for one."""
    frame = _df({"type": "extension"})
    item = vf._check_types(frame)[0]
    text = vf.render(vf.validate(frame))
    assert "FLoRA structural validation" in text
    assert f"\n- {vf.item_line(item)}\n" in text
    assert "[ ]" not in text and "[x]" not in text and "Tick" not in text


def test_passed_checks_are_listed():
    text = vf.render(vf.validate(_df()))
    assert "**Checks passed:**" in text and "\n- Type values valid\n" in text
    assert "[x]" not in text


def test_long_sections_are_folded():
    frame = pd.DataFrame([_row(type="extension", doi_o=f"10.1/{i}") for i in range(20)])
    assert "<details>" in vf.render(vf.validate(frame))


def test_a_report_stored_before_findings_were_records_still_renders():
    """publish_flora writes a stored run's report out; a run from before this change
    has string items and no levels."""
    stored = {"rows": 2, "suppressed": 0, "has_issues": True, "results": [
        {"name": "Type values valid", "heading": "Invalid type values", "description": "d",
         "items": ["10.1/a | 10.2/b: type='bogus'"], "passed": False},
        {"name": "Source values valid", "heading": "Invalid source values", "description": "d",
         "items": [], "passed": True}]}
    text = vf.render(stored)
    assert "### Problems (1)" in text and "\n- 10.1/a | 10.2/b: type='bogus'\n" in text
