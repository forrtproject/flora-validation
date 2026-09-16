"""Public aggregation preserves published relationships and the legacy API shape."""
import copy
import hashlib
import json

import pytest

from flora_api_records import author_names, build_records, normalize_doi, serialize_record


def row(identifier="FLORA-000001", **overrides):
    return dict(id=identifier, id_md5=hashlib.md5(identifier.encode()).hexdigest(),
                doi_o="10.1234/original", doi_r="10.1234/replication",
                title_o="Original paper", title_r="Replication paper",
                type="replication", outcome="successful", year_r="2022", **overrides)


def changed(identifier="FLORA-000001", **overrides):
    result = row(identifier)
    result.update(overrides)
    return result


def test_same_doi_pair_keeps_each_permanent_relationship_and_unique_counts():
    records = build_records([row(), changed("FLORA-000002", outcome="failed")])
    original = records["10.1234/original"]
    replication = records["10.1234/replication"]
    assert "id" not in original and "id_md5" not in original
    assert [item["id"] for item in original["record"]["replications"]] == ["FLORA-000001", "FLORA-000002"]
    assert [item["id"] for item in replication["record"]["originals"]] == ["FLORA-000001", "FLORA-000002"]
    assert all(item["id_md5"] == hashlib.md5(item["id"].encode()).hexdigest()
               for item in original["record"]["replications"])
    assert original["record"]["stats"]["n_replications_total"] == 2
    assert original["record"]["stats"]["n_unique_replication_dois"] == 1
    assert replication["record"]["stats"]["n_originals_total"] == 2
    assert replication["record"]["stats"]["n_unique_original_dois"] == 1


def test_multiple_paper_roles_have_stable_order_and_first_nonempty_metadata():
    records = build_records([
        changed(title_o=None, doi_o=" HTTPS://DOI.ORG/10.1234/ORIGINAL "),
        changed("FLORA-000002", doi_o="10.1234/other", doi_r="doi: 10.1234/ORIGINAL",
                title_r="First useful title", type="reproduction"),
        changed("FLORA-000003", doi_o="10.1234/other", doi_r="10.1234/original", title_r="Later title"),
    ])
    original = records["10.1234/original"]
    assert original["types"] == ["original", "replication", "reproduction"]
    assert original["title"] == "First useful title"
    assert original["doi_hash"] == hashlib.md5(b"10.1234/original").hexdigest()[:3]


def test_reproduction_and_missing_doi_relationships_stay_identifiable():
    records = build_records([
        changed(doi_r=None, type="reproduction"),
        changed("FLORA-000002", doi_r="NA"),
        changed("FLORA-000003", doi_o=None),
        changed("FLORA-000004", doi_o=None, doi_r=None),
        changed("FLORA-000005", type="computational reproduction"),
    ])
    original = records["10.1234/original"]["record"]
    assert [item["id"] for item in original["reproductions"]] == ["FLORA-000001", "FLORA-000005"]
    assert original["reproductions"][0]["doi"] is None
    assert original["replications"][0]["id"] == "FLORA-000002"
    assert original["stats"]["n_reproductions_only"] == 1
    assert original["stats"]["n_replications_only"] == 1
    anonymous_original = records["10.1234/replication"]["record"]["originals"][0]
    assert anonymous_original["id"] == "FLORA-000003" and anonymous_original["doi"] is None
    assert set(records) == {"10.1234/original", "10.1234/replication"}


def test_public_fields_parse_json_and_exclude_internal_and_unknown_extras():
    records = build_records([changed(
        author_o='[{"given":"Ada","family":"Lovelace"}]',
        bibtex_ref_o='{"title":"Original &amp; paper"}',
        volume_o=2, issue_o=3, pages_o="1-3", doi_o_hash="AbC",
        abstract_r="Evidence", outcome_computation="reproducible",
        source_record_id="private-uuid", reviewer_email="private@example.org",
        extra_fields={"secret": "no"}, _meta={"created_at": "internal"},
    )])
    original = records["10.1234/original"]
    assert original["authors"] == [{"given": "Ada", "family": "Lovelace"}]
    assert original["bibtex_ref"] == {"title": "Original & paper"}
    assert original["volume"] == "2" and original["issue"] == "3"
    assert original["doi_hash"] == "abc"
    rep = original["record"]["replications"][0]
    assert rep["abstract"] == "Evidence" and rep["outcome_computation"] == "reproducible"
    payload = json.dumps(records)
    for excluded in ("private-uuid", "private@example.org", "secret", "created_at"):
        assert excluded not in payload


def test_derived_fields_match_legacy_year_parsing_and_first_outcome():
    record = build_records([
        changed(year_r="2022", outcome="failed"),
        changed("FLORA-000002", year_r="2018 (online)", outcome="successful"),
        changed("FLORA-000003", year_r="2018", outcome="mixed"),
        changed("FLORA-000004", year_r="unknown", outcome=None),
        changed("FLORA-000005", year_r="2000", type="reproduction", outcome="excluded from replication mix"),
    ])["10.1234/original"]
    record["record"].update(outcome_mix={"stale": 7}, first_replication_year="1900")
    record["replication_year_counts"] = {"stale": 99}
    out = serialize_record(record)
    assert out["outcome_mix"] == {"failed": 1, "successful": 1, "mixed": 1}
    assert out["replication_year_counts"] == {"2022": 1, "2018": 2}
    assert out["first_replication_year"] == "2018"
    assert out["first_replication_outcome"] == "successful"
    assert "outcome_mix" not in out["record"] and "first_replication_year" not in out["record"]


def test_citations_only_come_from_explicit_side_data_and_suppress_for_identified_callers():
    record = build_records([changed(citation_timeline_o='{"2020":4}', n_citations_o="4",
                                   citation_timeline_r='{"2021":0}', n_citations_r="0")])
    original = record["10.1234/original"]
    assert serialize_record(original)["citation_timeline"] == {"2020": 4}
    assert serialize_record(original)["n_citations"] == 4
    assert serialize_record(record["10.1234/replication"])["n_citations"] == 0
    original["record"]["outcome_mix"] = {"stale": 1}
    original["outcome_mix"] = {"also stale": 1}
    original["record"]["citation_timeline"] = {"1900": 100}
    suppressed = serialize_record(original, include_derived=False)
    for name in ("outcome_mix", "replication_year_counts", "first_replication_year",
                 "first_replication_outcome", "citation_timeline", "n_citations"):
        assert name not in suppressed and name not in suppressed["record"]
    absent = serialize_record(build_records([row()])["10.1234/original"])
    assert "citation_timeline" not in absent and "n_citations" not in absent


def test_serialization_and_aggregation_do_not_mutate_inputs_or_share_response_state():
    rows = [changed(author_o=[{"given": "Ada", "family": "Lovelace"}])]
    before = copy.deepcopy(rows)
    record = build_records(rows)["10.1234/original"]
    assert rows == before
    saved = copy.deepcopy(record)
    serialized = serialize_record(record)
    serialized["authors"][0]["given"] = "Changed"
    serialized["record"]["replications"][0]["title"] = "Changed"
    assert record == saved and rows == before


@pytest.mark.parametrize("value, expected", [
    (None, []), ("NA", []),
    ('[{"given":"Ada","family":"Lovelace"},{"name":"Research Group"}]', ["Ada Lovelace", "Research Group"]),
    (["Ada Lovelace", {"literal": "Study Consortium"}, None], ["Ada Lovelace", "Study Consortium"]),
    ("A. One, B. Two", ["A. One", "B. Two"]),
    ({"given": "Ada", "family": "Lovelace"}, ["Ada Lovelace"]),
])
def test_author_names(value, expected):
    assert author_names(value) == expected


@pytest.mark.parametrize("value, expected", [
    (" HTTP://DX.DOI.ORG/10.1234/MIXED ", "10.1234/mixed"),
    ("doi: 10.1234/TEST", "10.1234/test"), (float("nan"), ""), (None, ""),
])
def test_normalize_doi(value, expected):
    assert normalize_doi(value) == expected


def test_empty_records_serialize_without_fabricated_citations():
    assert build_records([]) == {}
    out = serialize_record({"doi": "10.1234/lonely", "record": {"replications": []}})
    assert out["outcome_mix"] == out["replication_year_counts"] == {}
    assert out["first_replication_year"] is None and out["first_replication_outcome"] is None
    assert "citation_timeline" not in out


def test_json_payload_has_no_nonfinite_numbers():
    record = build_records([changed(year_o=float("nan"), n_citations_o=float("inf"))])["10.1234/original"]
    json.dumps(serialize_record(record), allow_nan=False)
    assert record["year"] is None and "n_citations" not in record
