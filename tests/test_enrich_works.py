"""Tests for enrich_works — bibliographic metadata for the FLoRA output contract.

One API replaces the R pipeline's three (CrossRef, OpenAlex, Unpaywall): OpenAlex
carries biblio, language and the Unpaywall OA data it already ingests, and answers
50 DOIs per request. The shaping rules below are where fidelity to the output
contract actually lives.

No network. Every test shapes a canned OpenAlex work.
"""
import pytest

import enrich_works as ew


def _work(**over):
    work = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.1177/09567976221082637",
        "title": "Choice Boosts Curiosity",
        "publication_year": 2022,
        "language": "en",
        "type": "article",
        "biblio": {"volume": "34", "issue": "1", "first_page": "99", "last_page": "110"},
        "authorships": [
            {"author": {"display_name": "Patricia Romero Verdugo"}},
            {"author": {"display_name": "Roshan Cools"}},
        ],
        "primary_location": {"source": {"display_name": "Psychological Science"}},
        "open_access": {"oa_url": "https://doi.org/10.1177/09567976221082637"},
        "best_oa_location": {"pdf_url": "https://example.org/paper.pdf",
                             "landing_page_url": "https://example.org/paper"},
    }
    work.update(over)
    return work


# ── DOI normalisation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "https://doi.org/10.1/ABC", "http://dx.doi.org/10.1/abc",
    "doi:10.1/abc", " 10.1/ABC ", "10.1/abc",
])
def test_dois_normalise_to_the_form_the_transform_produces(raw):
    """The join key is transform_sources.clean_doi's output. Any disagreement here
    means the metadata is fetched and then never matched to a row."""
    assert ew._norm_doi(raw) == "10.1/abc"


def test_empty_doi_normalises_to_empty():
    for value in (None, "", "   "):
        assert ew._norm_doi(value) == ""


# ── what the transform can actually join against ──────────────────────────────

class _Cur:
    def __init__(self, rows):
        self.rows, self.sql = rows, None

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())

    def fetchall(self):
        return self.rows


def test_load_metadata_hands_over_the_openalex_work_id():
    """The id was fetched and stored from the first run, but load_metadata did not
    select it — so the transform had nothing to join and the product carried only
    oa_url. Shipping the column without this is shipping an empty column."""
    cur = _Cur([{"doi": "10.1/x", "oa_work_id": "W123", "title": "T"}])
    assert ew.load_metadata(cur, include_seed=False)["10.1/x"]["oa_work_id"] == "W123"
    assert "oa_work_id" in cur.sql


def test_load_metadata_keys_on_doi():
    cur = _Cur([{"doi": "10.1/x", "oa_work_id": "W1"}, {"doi": "10.2/y", "oa_work_id": "W2"}])
    assert set(ew.load_metadata(cur, include_seed=False)) == {"10.1/x", "10.2/y"}


def test_load_metadata_skips_dois_openalex_could_not_find():
    """A not_found row is a cached negative, not metadata; joining it would overwrite
    a good sheet value with blanks."""
    cur = _Cur([])
    ew.load_metadata(cur, include_seed=False)
    assert "WHERE NOT not_found" in cur.sql


# ── shaping ───────────────────────────────────────────────────────────────────

def test_shape_maps_every_contract_field():
    row = ew.shape(_work())
    assert row["doi"] == "10.1177/09567976221082637"
    assert row["oa_work_id"] == "W2741809807"
    assert row["title"] == "Choice Boosts Curiosity"
    assert row["journal"] == "Psychological Science"
    assert row["year"] == "2022"
    assert row["volume"] == "34"
    assert row["issue"] == "1"
    assert row["pages"] == "99-110"
    assert row["language"] == "en"


def test_year_is_text():
    """The column it feeds is TEXT on both sides — the sheets carry values like 0
    and 2366, so the type was chosen to hold dirty data rather than reject it."""
    assert isinstance(ew.shape(_work())["year"], str)


def test_authors_are_joined_not_nested():
    assert ew.shape(_work())["authors"] == "Patricia Romero Verdugo; Roshan Cools"


def test_a_work_with_no_authors_gives_none_not_an_empty_string():
    assert ew.shape(_work(authorships=[]))["authors"] is None


def test_missing_author_names_are_skipped():
    work = _work(authorships=[{"author": {}}, {"author": {"display_name": "Real Name"}}])
    assert ew.shape(work)["authors"] == "Real Name"


def test_journal_survives_a_missing_location():
    for missing in ({}, {"source": None}, None):
        assert ew.shape(_work(primary_location=missing))["journal"] is None


@pytest.mark.parametrize("biblio,expected", [
    ({"first_page": "99", "last_page": "110"}, "99-110"),
    ({"first_page": "e70057", "last_page": "e70057"}, "e70057"),  # not "e70057-e70057"
    ({"first_page": "99"}, "99"),
    ({"last_page": "110"}, "110"),
    ({}, None),
])
def test_page_ranges(biblio, expected):
    assert ew.shape(_work(biblio=biblio))["pages"] == expected


def test_a_work_without_a_doi_is_skipped():
    """OpenAlex returns DOI-less works; there is nothing to key them on."""
    assert ew.shape(_work(doi=None)) is None


# ── OA url preference ─────────────────────────────────────────────────────────

def test_oa_url_prefers_a_direct_pdf():
    assert ew.shape(_work())["oa_url"] == "https://example.org/paper.pdf"


def test_oa_url_falls_back_to_the_landing_page():
    work = _work(best_oa_location={"landing_page_url": "https://example.org/paper"})
    assert ew.shape(work)["oa_url"] == "https://example.org/paper"


def test_oa_url_uses_open_access_last():
    """open_access.oa_url is frequently just doi.org/<doi>, which tells a reader
    nothing they did not already have — so it is the last resort, not the first."""
    work = _work(best_oa_location={})
    assert ew.shape(work)["oa_url"] == "https://doi.org/10.1177/09567976221082637"


def test_no_oa_anywhere_is_none():
    assert ew.shape(_work(best_oa_location={}, open_access={}))["oa_url"] is None


# ── BibTeX synthesis ──────────────────────────────────────────────────────────

def test_bibtex_uses_and_between_authors():
    """BibTeX separates authors with ' and ', not the '; ' used by the author column."""
    bibtex = ew.shape(_work())["bibtex_ref"]
    assert "Patricia Romero Verdugo and Roshan Cools" in bibtex


def test_bibtex_carries_the_structured_fields():
    bibtex = ew.shape(_work())["bibtex_ref"]
    for fragment in ("@article{", "title = {Choice Boosts Curiosity}",
                     "journal = {Psychological Science}", "year = {2022}",
                     "volume = {34}", "number = {1}", "pages = {99-110}"):
        assert fragment in bibtex


def test_bibtex_braces_in_a_title_cannot_unbalance_the_entry():
    bibtex = ew.shape(_work(title="A {weird} title"))["bibtex_ref"]
    assert bibtex.count("{") == bibtex.count("}")


def test_no_title_means_no_bibtex():
    """An entry with no title is not a usable reference."""
    assert ew.shape(_work(title=None, display_name=None))["bibtex_ref"] is None


def test_non_articles_are_misc():
    assert ew.shape(_work(type="posted-content"))["bibtex_ref"].startswith("@misc{")


# ── batching ──────────────────────────────────────────────────────────────────

def test_batch_size_respects_the_api_limit():
    """OpenAlex accepts at most 50 values in an OR filter."""
    assert ew.BATCH <= 50


def test_delay_stays_inside_the_polite_pool():
    """10 requests/second is the documented allowance."""
    assert ew.DELAY >= 0.1
