"""Tests for the OpenAlex work-id path in enrich_works.

A port of augment_with_openalex_url_refs_r() in R/openalex_cache.R, which the
notebook calls at Step 7b and which this project had never had.

THE CASE IT COVERS
------------------
Theses, working papers and conference reports often have no DOI. The coding sheets
record them as a plain OpenAlex link in url_r (https://openalex.org/W2186305685).
Enrichment keyed on the DOI cannot see them, so those rows arrived with no title,
no authors and no year — and a title is what the notebook's Step 10 filter keeps a
row for.

These works are cached in work_metadata under the identifier they were looked up
with, so `doi` holds a W-id for them. The namespaces cannot collide: a DOI always
begins "10.".

No network. Every test shapes a canned OpenAlex work.
"""
from pathlib import Path

import pytest

import enrich_works as ew

ROOT = Path(__file__).resolve().parents[1]


def _work(**over):
    work = {
        "id": "https://openalex.org/W2186305685",
        "doi": None,
        "title": "A thesis nobody gave a DOI",
        "publication_year": 2015,
        "language": "en",
        "type": "dissertation",
        "biblio": {"volume": None, "issue": None,
                   "first_page": None, "last_page": None},
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
        "primary_location": {"source": {"display_name": "Some University"}},
        "open_access": {"oa_url": None},
        "best_oa_location": {},
    }
    work.update(over)
    return work


# ── pulling the id out of what the sheet wrote ────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("https://openalex.org/W2186305685", "W2186305685"),
    ("http://openalex.org/W96171599", "W96171599"),
    ("W2735016722", "W2735016722"),
    ("see https://openalex.org/W123456 for details", "W123456"),
])
def test_work_ids_are_recognised(value, expected):
    assert ew.extract_work_id(value) == expected


@pytest.mark.parametrize("value", [
    None, "", "https://osf.io/abcde", "10.1234/abc", "W123",      # too short
])
def test_non_work_ids_are_rejected(value):
    assert ew.extract_work_id(value) is None


# ── shaping a DOI-less work ───────────────────────────────────────────────────

def test_a_work_with_no_doi_is_cached_under_its_work_id():
    """shape() returns None for these — that is the hole this fills."""
    assert ew.shape(_work()) is None
    row = ew.shape_by_work_id(_work(), "W2186305685")
    assert row["doi"] == "W2186305685"
    assert row["title"] == "A thesis nobody gave a DOI"


def test_the_shaped_row_carries_the_fields_the_product_needs():
    row = ew.shape_by_work_id(_work(), "W2186305685")
    assert row["authors"] == "Ada Lovelace"
    assert row["journal"] == "Some University"
    assert row["year"] == "2015"
    assert row["language"] == "en"
    assert row["oa_work_id"] == "W2186305685"


def test_a_work_without_a_title_is_not_cached():
    """A row with no title is exactly what this exists to fix; caching a blank
    would record the miss as a success and stop it being retried."""
    assert ew.shape_by_work_id(_work(title=None, display_name=None),
                               "W2186305685") is None


def test_a_work_that_does_have_a_doi_is_still_keyed_by_the_work_id():
    """The sheet recorded no DOI, so nothing downstream can look this row up by
    one. The key has to be what the row actually carries."""
    row = ew.shape_by_work_id(
        _work(doi="https://doi.org/10.1234/abc"), "W2186305685")
    assert row["doi"] == "W2186305685"


def test_the_bibtex_key_falls_back_to_the_work_id():
    """_bibtex() builds its key from the DOI, which these works do not have."""
    row = ew.shape_by_work_id(_work(), "W2186305685")
    assert row["bibtex_ref"].startswith("@misc{W2186305685,")


# ── which rows are looked up ──────────────────────────────────────────────────

class _Cur:
    def __init__(self, rows=None):
        self.rows, self.sql = rows or [], None

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())

    def fetchall(self):
        return self.rows


def test_only_rows_missing_a_doi_are_looked_up():
    """Where a DOI exists it is the better key — it is what the rest of the
    pipeline joins on — and a second lookup would only cost requests."""
    cur = _Cur()
    ew.work_ids_in_product(cur)
    assert "doi_o IS NULL OR btrim(doi_o) = ''" in cur.sql
    assert "doi_r IS NULL OR btrim(doi_r) = ''" in cur.sql


def test_rows_already_ruled_duplicate_are_skipped():
    cur = _Cur()
    ew.work_ids_in_product(cur)
    assert "duplicate_status IS DISTINCT FROM 'duplicate'" in cur.sql


def test_both_sides_are_searched():
    cur = _Cur()
    ew.work_ids_in_product(cur)
    assert "url_o" in cur.sql and "url_r" in cur.sql


def test_ids_come_back_deduplicated_and_bare():
    cur = _Cur([{"url": "https://openalex.org/W2186305685"},
                {"url": "https://openalex.org/W2186305685"},
                {"url": "https://openalex.org/W96171599"},
                {"url": "https://osf.io/nope"}])
    assert ew.work_ids_in_product(cur) == ["W2186305685", "W96171599"]


# ── the transform joins them in ───────────────────────────────────────────────

def test_the_transform_fills_only_what_the_doi_join_left_empty():
    """A real DOI always outranks a work id: the fill uses fillna, never assignment,
    so a DOI-resolved title is never replaced by a work-id one."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    block = source[source.index("url_column = f\"url_{side}\""):]
    block = block[:block.index("enriched =")]
    assert "df[column].fillna(from_id)" in block
    assert "extract_work_id" in block


def test_enrichment_runs_the_work_id_pass():
    """Without this call the cache never gains a work-id row, and the transform's
    join finds nothing to fill with."""
    source = (ROOT / "enrich_works.py").read_text(encoding="utf-8")
    assert "enrich_work_ids(cur" in source[source.index("def main("):]
