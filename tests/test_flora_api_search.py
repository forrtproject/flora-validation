"""Search keeps legacy envelopes while pagination reflects filtered records."""
from copy import deepcopy

import pytest

import flora_api_search
from flora_api_search import search


def paper(doi, title, *, roles=None, year="2020", authors=None, reps=None, repros=None, originals=None):
    return {"doi": doi, "types": roles or ["original"], "title": title, "year": year,
            "authors": authors or [], "record": {
                "replications": reps or [], "reproductions": repros or [],
                "originals": originals or [], "stats": {}}}


def graph():
    return {
        "10.1234/a": paper("10.1234/a", "Social priming and memory", year="2012", reps=[
            {"doi": "10.1234/b", "outcome": "failed", "year": "2015",
             "authors": [{"given": "Maya", "family": "Rivera"}]}]),
        "10.1234/b": paper("10.1234/b", "A repeated memory experiment", roles=["replication"],
                            year="2015", originals=[{"doi": "10.1234/a"}]),
        "10.1234/c": paper("10.1234/c", "Social priming computational study", year="2012", repros=[
            {"doi": "10.1234/d", "outcome": "computationally reproducible"}]),
        "10.1234/d": paper("10.1234/d", "Reanalysis of priming", roles=["reproduction"],
                            year="2024", originals=[{"doi": "10.1234/c"}]),
        "10.1234/e": paper("10.1234/e", "Social behavior and memory", year=None, reps=[
            {"doi": "10.1234/f", "outcome": "successful"},
            {"doi": "10.1234/g", "outcome": "failed"}]),
    }


def test_doi_lookup_accepts_canonical_url_and_case_forms():
    for query in ("10.1234/A", "https://doi.org/10.1234/a", "DOI: 10.1234/a"):
        result = search(graph(), {"q": query})
        assert list(result["results"]) == ["10.1234/a"]
        assert result["results"]["10.1234/a"]["score"] == 0
    assert search(graph(), {"query": "10.1234/absent"})["total"] == 0


def test_free_text_requires_all_words_and_extracts_year():
    result = search(graph(), {"query": "social memory 2012"})
    assert list(result["results"]) == ["10.1234/a"]
    assert result["query"] == "social memory 2012"


def test_title_typo_and_related_authors_are_searchable():
    assert "10.1234/a" in search(graph(), {"query": "primng"})["results"]
    assert list(search(graph(), {"query": "Maya Rivera"})["results"]) == ["10.1234/a"]


def test_advanced_terms_support_and_or_exclusion_and_wildcards():
    result = search(graph(), {"mustHave": ["soc*"], "anyOf": ["mem?ry", "comput*"],
                              "exclude": ["*behavior*"]})
    assert set(result["results"]) == {"10.1234/a", "10.1234/c"}
    result = search(graph(), {"exclude": ["priming", "repeated"]})
    assert list(result["results"]) == ["10.1234/e"]


def test_question_wildcard_requires_exactly_one_character():
    records = {"10.1/a": paper("10.1/a", "cat"), "10.1/b": paper("10.1/b", "coat")}
    assert list(search(records, {"mustHave": ["c?at"]})["results"]) == ["10.1/b"]


def test_unknown_year_is_kept_in_range_but_not_in_exact_year_search():
    result = search(graph(), {"q": "social", "yearFrom": 2010, "yearTo": 2013})
    assert set(result["results"]) == {"10.1234/a", "10.1234/c", "10.1234/e"}
    assert set(search(graph(), {"q": "2012"})["results"]) == {"10.1234/a", "10.1234/c"}


def test_outcomes_filter_before_pagination_and_require_all_selected():
    result = search(graph(), {"query": "social", "outcomes": ["FAILED"], "limit": 1})
    assert result["total"] == 1 and result["hasMore"] is False
    assert list(result["results"]) == ["10.1234/a"]
    assert search(graph(), {"query": "social", "outcomes": ["successful", "failed"]})["total"] == 2


def test_replication_and_reproduction_roles_expand_separately_with_bounded_pages():
    params = {"query": "social", "paperTypes": ["replication"], "limit": 1}
    first = search(graph(), params)
    assert first["total"] == 3 and first["hasMore"] is True
    pages = [search(graph(), dict(params, offset=offset)) for offset in range(3)]
    assert {doi for page in pages for doi in page["results"]} == {"10.1234/a", "10.1234/b", "10.1234/e"}
    assert all(len(page["results"]) == 1 for page in pages)
    assert pages[-1]["hasMore"] is False
    repros = search(graph(), {"query": "social", "paperTypes": ["reproduction"]})
    assert set(repros["results"]) == {"10.1234/c", "10.1234/d"}


def test_linked_expansion_obeys_year_and_exclusions_and_does_not_recurse():
    records = graph()
    records["10.1234/b"]["record"]["originals"].append({"doi": "10.1234/c"})
    result = search(records, {"query": "social memory", "paperTypes": ["original"]})
    assert "10.1234/b" in result["results"] and "10.1234/c" not in result["results"]
    result = search(graph(), {"query": "social", "paperTypes": ["reproduction"], "yearTo": 2020})
    assert list(result["results"]) == ["10.1234/c"]
    result = search(graph(), {"mustHave": ["social"], "exclude": ["reanalysis"],
                              "paperTypes": ["reproduction"]})
    assert list(result["results"]) == ["10.1234/c"]


def test_serialization_can_omit_derived_fields_without_mutating_records():
    records = graph()
    before = deepcopy(records)
    with_derived = search(records, {"query": "10.1234/a"})["results"]["10.1234/a"]
    without = search(records, {"query": "10.1234/a"}, include_derived=False)["results"]["10.1234/a"]
    assert with_derived["outcome_mix"] == {"failed": 1}
    assert "outcome_mix" not in without
    assert records == before


@pytest.mark.parametrize("params", [
    {}, {"query": 17}, {"query": "x", "limit": 0}, {"query": "x", "limit": 1001},
    {"query": "x", "offset": -1}, {"query": "x", "limit": True},
    {"query": "x", "mustHave": [5]}, {"query": "x", "paperTypes": ["unknown"]},
    {"query": "x", "yearFrom": 2024, "yearTo": 2020},
])
def test_bad_parameters_raise_value_error(params):
    with pytest.raises(ValueError):
        search(graph(), params)


def test_string_pagination_and_empty_result_envelope():
    result = search(graph(), {"query": "a", "limit": "3", "offset": "1"})
    assert result == {"query": "a", "total": 0, "offset": 1, "limit": 3,
                      "hasMore": False, "results": {}}


def test_required_terms_stop_scoring_a_record_after_the_first_miss(monkeypatch):
    checked = []

    def score(term, fields):
        checked.append(term)
        assert term == "absent", "A later AND term cannot rescue a rejected record"
        return None

    monkeypatch.setattr(flora_api_search, "_term_score", score)
    result = search(graph(), {"mustHave": ["absent", "expensive", "unnecessary"]})
    assert result["total"] == 0
    assert checked == ["absent"] * len(graph())


def test_direct_doi_lookup_does_not_build_any_text_descriptors(monkeypatch):
    monkeypatch.setattr(flora_api_search, "_fields",
                        lambda record: pytest.fail("Exact DOI lookup must not parse authors or build a text index"))
    result = search(graph(), {"query": "https://doi.org/10.1234/a"})
    assert list(result["results"]) == ["10.1234/a"]


def test_exact_title_match_never_scores_later_author_fields():
    def fields():
        yield "Social priming".casefold(), 0.0
        pytest.fail("An exact title score cannot be improved by later author fields")

    assert flora_api_search._term_score("priming", fields()) == 0.0
