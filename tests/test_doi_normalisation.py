"""Contract and parity tests for shared/doi.py across all FLoRA modules."""
import pytest

import adjudication.importer as adj_importer
import adjudication.review as adj_review
import backfill_oa_work_ids
import bibliographic_helpers
import consensus_engine
import enrich_works
import export_validated
import extra_originals
import flora_api_records
import preprint_dedup
import sync_sources
import transform_sources
from shared.doi import (
    canonical_doi,
    clean_scraped_doi,
    decode_doi_escapes,
    normalize_bare_doi,
    normalize_doi_key,
)
from tests.test_flora_store import local_database  # noqa: F401  (re-export opt-in PG fixture)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://doi.org/10.1037/AbC", "10.1037/AbC"),
        ("http://dx.doi.org/10.1037/AbC", "10.1037/AbC"),
        ("DOI: 10.1037/AbC", "10.1037/AbC"),
        ("  https://doi.org/ 10.1037/AbC  ", "10.1037/AbC"),
        ("", None),
        ("   ", None),
        (None, None),
        (float("nan"), None),
    ],
)
def test_normalize_bare_doi_preserves_case_and_handles_missing(raw, expected):
    assert normalize_bare_doi(raw) == expected
    if not isinstance(raw, float):
        assert extra_originals.normalize_doi(raw) == (expected or "")


def test_export_validated_clean_doi_enforces_prefix_and_truncates_whitespace():
    assert export_validated.clean_doi("https://doi.org/10.1037/AbC extra text") == "10.1037/AbC"
    assert export_validated.clean_doi("https://example.org/not-a-doi") is None
    assert export_validated.clean_doi(None) is None


@pytest.mark.parametrize(
    "raw",
    [
        "https://doi.org/10.1037/AbC",
        "http://dx.doi.org/10.1037/AbC",
        "https://dx.doi.org/10.1037/AbC",
        "http://doi.org/10.1037/AbC",
        "doi: 10.1037/AbC",
        "DOI:10.1037/AbC",
    ],
)
def test_lowercased_doi_keys_agree_across_modules(raw):
    # All matching-key callers must handle http://dx.doi.org/ and 'doi: ' consistently.
    assert normalize_doi_key(raw) == "10.1037/abc"
    assert consensus_engine._norm_doi(raw) == "10.1037/abc"
    assert backfill_oa_work_ids._norm_doi(raw) == "10.1037/abc"
    assert adj_importer.normalise_doi(raw) == "10.1037/abc"
    assert sync_sources._norm_doi(raw) == "10.1037/abc"
    assert adj_review.norm_doi(raw) == "10.1037/abc"
    assert flora_api_records.published_doi(raw) == "10.1037/abc"


def test_clean_scraped_doi_and_bibliographic_normalise_key_agree():
    samples = [
        ("10.1002/pits.22106digital object identifier (doi)", "10.1002/pits.22106"),
        ("10.1016/j.learninstruc.2018.04.010 get rights and content", "10.1016/j.learninstruc.2018.04.010"),
        (
            "https://doi.org/10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y",
            "10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y",
        ),
        ("https://doi.org/10.1/a%252fb", "10.1/a%252fb"),
    ]
    for raw, expected in samples:
        assert clean_scraped_doi(raw) == expected
        assert transform_sources.clean_doi(raw) == expected
        assert bibliographic_helpers.normalise_key(raw) == expected
        assert enrich_works._norm_doi(raw) == expected
    assert clean_scraped_doi(None) is None
    assert clean_scraped_doi(float("nan")) is None
    assert decode_doi_escapes("10.1/a%252fb") == "10.1/a%252fb"


def test_canonical_doi_for_api_and_preprint_dedup():
    sici = "10.1002/(sici)1099-0771(199806)11:2%3C107::aid-bdm292%3E3.0.co;2-y"
    decoded = "10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y"
    assert canonical_doi(sici) == decoded
    assert flora_api_records.normalize_doi(sici) == decoded
    assert preprint_dedup.normalize_doi(sici) == decoded
    assert preprint_dedup.normalize_doi("doi: 10.1037//0022-3514.46.4.778") == "10.1037/0022-3514.46.4.778"
    # %25 idempotency prevents double-decoding across passes
    assert flora_api_records.normalize_doi("10.1/a%252fb") == "10.1/a%252fb"
    assert preprint_dedup.normalize_doi("10.1/a%252fb") == "10.1/a%252fb"
    # published_doi keeps percent escapes untouched for client lookup stability
    assert flora_api_records.published_doi(sici) == sici.lower()


def test_normalize_doi_key_matches_postgres_source_norm_doi(local_database):
    """Python normalize_doi_key(..., drop_whitespace=True) must agree with SQL source_norm_doi()."""
    cases = [
        None,
        "",
        "   ",
        "10.1037/ABC",
        "https://doi.org/10.1037/AbC",
        "http://dx.doi.org/10.1037/AbC",
        "DOI: 10.1037/AbC",
        "https://doi.org/10.1016/j.x.2020.01.001 get rights and content",
        "doi:10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y trailing",
    ]
    with local_database.cursor() as cur:
        for raw in cases:
            cur.execute("SELECT source_norm_doi(%s)", (raw,))
            sql_val = cur.fetchone()[0]
            assert normalize_doi_key(raw, drop_whitespace=True) == sql_val
            assert sync_sources._norm_doi(raw) == sql_val
            assert adj_review.norm_doi(raw) == sql_val
