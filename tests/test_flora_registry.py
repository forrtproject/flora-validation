"""Tests for flora_registry and flora_service — stable ids for the FLoRA product.

The rule that matters is that an id follows PROVENANCE, not content: a record keeps
its id when a reviewer corrects a DOI, and keeps it when the dedup survivor changes.
Getting that wrong silently re-issues ids for records that never changed, which is
exactly what a citable identifier must not do.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

import flora_registry
import flora_service
import transform_sources

ROOT = Path(__file__).resolve().parents[1]


def _mint(cur, source_ids, taken=None):
    return flora_registry._mint_ids(cur, source_ids, set(taken or ()))


class FakeCursor:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    def fetchall(self):
        out, self.results = self.results, []
        return out


# ── id minting: the source record's own display_id, pinned ────────────────────

def test_id_is_the_source_records_display_id():
    """One namespace, not two: the id a FLoRA row carries is the id already visible
    in the Source Records tab."""
    cur = FakeCursor([{"record_id": "src-a", "display_id": "REPL-000397"}])
    assert _mint(cur, ["src-a"]) == {"src-a": "REPL-000397"}


def test_ids_are_fetched_in_one_query():
    """One query per row would make a 3,000-row first run 3,000 round trips."""
    cur = FakeCursor([{"record_id": f"src-{i}", "display_id": f"REPL-{i:06d}"}
                      for i in range(3)])
    _mint(cur, ["src-0", "src-1", "src-2"])
    assert len(cur.executed) == 1


def test_a_taken_id_gets_a_suffix_rather_than_colliding():
    """Reachable when a record pinned REPL-000001, re-pointed elsewhere, and
    REPL-000001 later returned as its own row. Stealing the id would break whatever
    cited it; colliding would abort the whole refresh."""
    cur = FakeCursor([{"record_id": "src-a", "display_id": "REPL-000001"}])
    assert _mint(cur, ["src-a"], taken={"REPL-000001"}) == {"src-a": "REPL-000001-R2"}


def test_suffixes_keep_climbing():
    cur = FakeCursor([{"record_id": "src-a", "display_id": "REPL-000001"}])
    minted = _mint(cur, ["src-a"], taken={"REPL-000001", "REPL-000001-R2"})
    assert minted == {"src-a": "REPL-000001-R3"}


def test_two_new_rows_cannot_mint_the_same_id_in_one_pass():
    """`taken` is mutated as it goes, so the second row in the same batch sees the
    first row's claim."""
    cur = FakeCursor([{"record_id": "src-a", "display_id": "REPL-000001"},
                      {"record_id": "src-b", "display_id": "REPL-000001"}])
    minted = _mint(cur, ["src-a", "src-b"])
    assert len(set(minted.values())) == 2


def test_a_source_row_with_no_display_id_still_gets_something():
    """A missing display_id must not produce a NULL flora_id — the column is NOT
    NULL, and the whole refresh would abort on one bad row."""
    cur = FakeCursor([])
    minted = _mint(cur, ["abcdef1234-rest"])
    assert minted["abcdef1234-rest"].startswith("UNKNOWN-")


def test_minting_nothing_touches_the_database():
    cur = FakeCursor([])
    assert _mint(cur, []) == {}
    assert cur.executed == []


def test_no_separate_flora_series_remains():
    """The FLORA- counter is gone; an id that looks like one would mean the old
    minting path came back."""
    assert not hasattr(flora_registry, "_next_ids")
    assert not hasattr(flora_registry, "DISPLAY_PREFIX")


# ── the uuid[] parsing trap ───────────────────────────────────────────────────

def test_merged_ids_are_cast_to_text_array():
    """psycopg2 has no uuid[] parser registered, so a bare uuid[] arrives as the raw
    '{a,b}' STRING. Iterating that yields single characters, which silently builds an
    index of punctuation — and every survivor change then mints a fresh id."""
    cur = FakeCursor([])
    flora_registry._load_existing(cur)
    sql = cur.executed[0][0]
    assert "merged_source_record_ids::text[]" in sql


def test_by_any_indexes_survivor_and_absorbed_alike():
    cur = FakeCursor([{
        "flora_record_id": "fr-1", "flora_id": "FLORA-000001",
        "primary_source_record_id": "src-a",
        "merged_source_record_ids": ["src-b", "src-c"],
        "retired_at": None,
    }])
    by_primary, by_any = flora_registry._load_existing(cur)
    assert set(by_primary) == {"src-a"}
    assert set(by_any) == {"src-a", "src-b", "src-c"}


def test_by_any_tolerates_an_empty_merged_array():
    cur = FakeCursor([{
        "flora_record_id": "fr-1", "flora_id": "FLORA-000001",
        "primary_source_record_id": "src-a",
        "merged_source_record_ids": None, "retired_at": None,
    }])
    _, by_any = flora_registry._load_existing(cur)
    assert set(by_any) == {"src-a"}


# ── attach_ids never writes ───────────────────────────────────────────────────

def test_attach_ids_issues_no_id():
    """The read path must not assign an id as a side effect of opening a page."""
    cur = FakeCursor([{"sid": "src-a", "flora_id": "FLORA-000001"}])
    frame = pd.DataFrame({"source_record_id": ["src-a", "src-b"], "flora_id": [None, None]})
    out = flora_registry.attach_ids(cur, frame)
    # pandas .map() leaves NaN for an unmatched key, not None. Every boundary that
    # exposes this frame normalises it (list_records, get_record, counts), so NaN
    # is the correct internal representation of "no id yet".
    assert out["flora_id"].iloc[0] == "FLORA-000001"
    assert pd.isna(out["flora_id"].iloc[1])
    assert all("INSERT" not in sql and "UPDATE" not in sql for sql, _ in cur.executed)


def test_attach_ids_on_an_empty_frame():
    assert flora_registry.attach_ids(FakeCursor([]), pd.DataFrame()).empty


# ── the transform carries provenance ──────────────────────────────────────────

def test_provenance_columns_are_appended_not_inserted():
    """The FLoRA column comment is explicit that positional readers of the export
    must keep working, so flora_id cannot arrive first."""
    assert transform_sources.PROVENANCE_COLUMNS == [
        "flora_id", "source_display_id", "source_record_id"
    ]
    assert transform_sources.FLORA_COLUMNS[0] == "doi_o"


def test_provenance_columns_do_not_collide_with_the_flora_set():
    assert not set(transform_sources.PROVENANCE_COLUMNS) & set(transform_sources.FLORA_COLUMNS)


# ── service filtering ─────────────────────────────────────────────────────────

def _frame():
    return pd.DataFrame({
        "flora_id":          ["FLORA-000001", "FLORA-000002", None],
        "source_display_id": ["REPL-000001", "REPRO-000002", "FRED-000003"],
        "type":              ["replication", "reproduction", "replication"],
        "source":            ["entry_sheet_replications", "entry_sheet_reproductions", "validated"],
        "doi_o":             ["10.1/a", "10.1/b", None],
        "doi_r":             ["10.2/a", None, "10.2/c"],
        "url_r":             [None, "https://osf.io/x", None],
        "ref_o":             ["Smith 2001", "Jones 2002", "Brown 2003"],
        "ref_r":             ["Rep A", "Rep B", "Rep C"],
        "outcome":           ["successful", "computationally reproducible, robust", "failed"],
        "oa_work_id_o":      ["W1984281061", None, None],
        "oa_work_id_r":      ["W1986096778", None, None],
        "merged_display_ids": ["REPL-000481 FRED-000900", None, None],
    })


@pytest.mark.parametrize("filters,expected", [
    ({}, 3),
    ({"type": "replication"}, 2),
    ({"type": "reproduction"}, 1),
    ({"source": "validated"}, 1),
    ({"outcome": "failed"}, 1),
    ({"unregistered": True}, 1),
])
def test_filters(filters, expected):
    assert len(flora_service._apply_filters(_frame(), filters)) == expected


def test_search_spans_ids_dois_and_references():
    for term, expected in [("FLORA-000001", 1), ("REPRO", 1), ("10.1/a", 1),
                           ("smith", 1), ("Rep", 3)]:
        assert len(flora_service._apply_filters(_frame(), {"search": term})) == expected, term


def test_search_finds_a_row_by_an_id_it_absorbed():
    """Without this the 314 collapsed source records are a dead end: someone reads
    REPL-000481 in Source Records, searches here, finds nothing, and concludes the
    record was dropped — when its content is in the product under another id."""
    out = flora_service._apply_filters(_frame(), {"search": "REPL-000481"})
    assert out["flora_id"].tolist() == ["FLORA-000001"]


def test_search_survives_a_frame_without_the_merged_column():
    """dataset() always adds it, but _apply_filters is called on bare frames in
    tests and on an empty frame in production."""
    frame = _frame().drop(columns=["merged_display_ids"])
    assert len(flora_service._apply_filters(frame, {"search": "smith"})) == 1


def test_search_is_case_insensitive_and_ignores_missing_values():
    """A None doi must not raise, and must not match every search."""
    assert len(flora_service._apply_filters(_frame(), {"search": "SMITH"})) == 1
    assert len(flora_service._apply_filters(_frame(), {"search": "zzz"})) == 0


def test_unregistered_filter_finds_rows_with_no_id():
    out = flora_service._apply_filters(_frame(), {"unregistered": True})
    assert out["source_display_id"].tolist() == ["FRED-000003"]


def test_counts_report_the_unregistered_backlog():
    counts = flora_service.counts(_frame())
    assert counts["all_records"] == 3
    assert counts["replications"] == 2
    assert counts["reproductions"] == 1
    assert counts["unregistered"] == 1
    assert "validated" in counts["sources"]


def test_grid_columns_are_a_subset_of_what_the_transform_produces():
    produced = set(transform_sources.FLORA_COLUMNS
                   + transform_sources.ENRICHMENT_COLUMNS
                   + transform_sources.PROVENANCE_COLUMNS
                   + ["export_id"])  # attached from the publication registry
    assert not set(flora_service.LIST_COLUMNS) - produced


def test_the_grid_is_sent_the_openalex_work_ids():
    """The transform and the CSV export had them before the tab did: LIST_COLUMNS is
    what reaches the browser, so a column missing here is invisible in the FLoRA tab
    no matter what the export contains."""
    assert "oa_work_id_o" in flora_service.LIST_COLUMNS
    assert "oa_work_id_r" in flora_service.LIST_COLUMNS


def test_a_pasted_openalex_id_finds_its_row():
    """Most of the point of carrying the id: someone reading a paper in OpenAlex can
    check whether we already hold it."""
    out = flora_service._apply_filters(_frame(), {"search": "W1984281061"})
    assert out["flora_id"].tolist() == ["FLORA-000001"]


def test_the_replication_side_work_id_is_searchable_too():
    out = flora_service._apply_filters(_frame(), {"search": "W1986096778"})
    assert len(out) == 1


def test_search_survives_a_frame_without_the_work_id_columns():
    """_apply_filters runs on bare frames in tests and on an empty frame in
    production; a missing column must not raise."""
    frame = _frame().drop(columns=["oa_work_id_o", "oa_work_id_r"])
    assert len(flora_service._apply_filters(frame, {"search": "smith"})) == 1


def test_sort_columns_are_all_listable():
    assert not set(flora_service.SORT_COLUMNS) - set(flora_service.LIST_COLUMNS)


# ── the payload actually has to serialise ─────────────────────────────────────
#
# json.dumps(..., allow_nan=False) is exactly what Starlette's JSONResponse does,
# so these two tests reproduce the response layer rather than approximating it.
# Both endpoints returned 500 before the fix below them: a NaN or a numpy scalar
# reaching the encoder is not a degraded value, it is a dead page.

def _dumps(payload):
    return json.dumps(payload, allow_nan=False)


def test_the_grid_payload_carries_no_nan(monkeypatch):
    """pandas 3 keeps missing text as NaN inside a `str` dtype column, and
    DataFrame.where(cond, None) cannot write None into one — the float survived
    into the response and JSONResponse rejected the whole page. 43 of the 59 live
    pages were a 500."""
    monkeypatch.setattr(flora_service, "dataset", lambda cur: _frame())
    out = flora_service.list_records(None, {}, page=1, per_page=50)
    _dumps(out["records"])
    missing = [r for r in out["records"] if r["oa_work_id_r"] is None]
    assert len(missing) == 2, "a missing work id must arrive as null, not as NaN"


def test_the_detail_payload_carries_no_numpy_scalars(monkeypatch):
    """The nullable numeric dtypes hand back np.int64/np.float64, which FastAPI's
    encoder cannot serialise — so the detail panel failed for every record, not
    just for rows with something missing."""
    frame = _frame()
    frame["author_overlap"] = pd.array([2, None, 0], dtype="Int64")
    frame["author_overlap_pct"] = pd.array([0.5, None, 0.0], dtype="Float64")
    monkeypatch.setattr(flora_service, "dataset", lambda cur: frame)

    record = flora_service.get_record(None, "FLORA-000001")
    _dumps(record)
    assert record["author_overlap"] == 2
    assert record["author_overlap_pct"] == 0.5
    # pd.NA in a nullable column is still a missing value.
    assert flora_service.get_record(None, "FLORA-000002")["author_overlap"] is None


# ── the tab's own controls ────────────────────────────────────────────────────

def test_returning_to_the_tab_resets_the_controls_with_the_state():
    """switchAdminTab() calls resetFloraView() on every entry to the FLoRA tab, so
    the chip and the two selects have to come back with the state variables. Left
    where the previous visit put them, they advertise a filter that is not being
    applied: the grid returns every row while "Replications" is still lit."""
    source = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    body = source[source.index("function resetFloraView()"):]
    body = body[:body.index(chr(10) + "}" + chr(10))]
    assert "#flora-filters" in body, "the active filter chip is never cleared"
    assert "#flora-source-filter" in body and "#flora-outcome-filter" in body


# ── cache ─────────────────────────────────────────────────────────────────────

def test_invalidate_clears_the_cached_frame():
    flora_service._cache["signature"] = ("x",)
    flora_service._cache["frame"] = _frame()
    flora_service.invalidate()
    assert flora_service._cache["signature"] is None
    assert flora_service._cache["frame"] is None


def test_signature_covers_every_way_the_dataset_can_change():
    """Counts alone would miss a reviewer's edit; updated_at alone would miss an
    exclusion being added."""
    cur = FakeCursor([{"n_source": 1, "max_updated": "t", "n_ruled": 0,
                       "n_flora": 1, "n_excluded": 0}])
    flora_service._signature(cur)
    sql = cur.executed[0][0]
    for table in ("source_records", "flora_records", "transform_exclusions"):
        assert table in sql
    assert "MAX(updated_at)" in sql


# ── the FLoRA output contract ─────────────────────────────────────────────────

FLORA_OUTPUT_CONTRACT = [
    "doi_o", "alt_identifier_o", "doi_o_hash", "title_o", "author_o", "journal_o",
    "year_o", "volume_o", "issue_o", "pages_o", "apa_ref_o", "bibtex_ref_o",
    "url_o", "language_o",
    "doi_r", "alt_identifier_r", "doi_r_hash", "title_r", "author_r", "journal_r",
    "year_r", "volume_r", "issue_r", "pages_r", "apa_ref_r", "bibtex_ref_r",
    "url_r", "language_r",
    "oa_url_o", "oa_url_r",
    "outcome", "outcome_quote", "outcome_quote_source", "type", "source",
]


def test_output_contract_matches_the_r_notebooks_output_cols():
    """Spelled out here rather than imported, so a change to the module is a change
    to a test too — this list is an agreement with a pipeline we do not control."""
    assert transform_sources.FLORA_OUTPUT_COLUMNS == FLORA_OUTPUT_CONTRACT


def _built_frame():
    return pd.DataFrame({
        "doi_o": ["10.1177/09567976221082637", None],
        "doi_r": ["10.1098/rsos.231240", None],
        "ref_o": ["Smith (2001)", "Brown (2003)"],
        "ref_r": ["Jones (2020)", None],
        "url_o": [None, None], "url_r": [None, "https://osf.io/x"],
        "abstract_r": ["abstract", None],
        "outcome": ["successful", "failed"],
        "outcome_quote": ["q", None], "outcome_quote_source": ["abstract", None],
        "type": ["replication", "replication"], "source": ["validated", "validated"],
        "alt_identifier_o": [None, None], "alt_identifier_r": [None, None],
        # build() creates these in the enrichment loop for every row, filled or not,
        # so a frame reaching to_output_shape always has them.
        "oa_work_id_o": ["W2741809807", None], "oa_work_id_r": ["W4386304018", None],
        "outcome_computation": [None, None], "outcome_computational_quote": [None, None],
        "out_quote_computational_source": [None, None],
        "outcome_robustness": [None, None], "outcome_robustness_quote": [None, None],
        "out_quote_robust_source": [None, None],
        "flora_id": ["REPL-000001", "REPL-000002"],
        "source_display_id": ["REPL-000001", "REPL-000002"],
        "source_record_id": ["src-a", "src-b"],
    })


def test_output_shape_starts_with_the_contract_exactly():
    out = transform_sources.to_output_shape(_built_frame())
    assert list(out.columns)[:2] == ["id", "id_md5"]
    assert list(out.columns)[2:37] == FLORA_OUTPUT_CONTRACT


def test_columns_we_cannot_fill_are_present_but_empty():
    """Emitted rather than omitted: the R pipeline left_joins onto these, and an
    absent column breaks the join while a blank one simply gets filled."""
    out = transform_sources.to_output_shape(_built_frame())
    for col in ("title_o", "author_o", "language_r", "oa_url_o", "bibtex_ref_r"):
        assert col in out.columns
        assert out[col].isna().all()


def test_sheet_references_become_the_apa_columns():
    out = transform_sources.to_output_shape(_built_frame())
    assert out["apa_ref_o"].tolist() == ["Smith (2001)", "Brown (2003)"]
    assert out["apa_ref_r"].iloc[0] == "Jones (2020)"


def test_extras_are_kept_after_the_contract_not_dropped():
    """abstract_r feeds downstream classification and the reproduction axes are what
    the flat outcome is derived FROM. Dropping either to match a column list exactly
    would lose data the database is the only copy of."""
    out = transform_sources.to_output_shape(_built_frame())
    tail = list(out.columns)[37:]
    assert "abstract_r" in tail
    assert "outcome_computation" in tail
    assert "flora_id" in tail


def test_the_openalex_work_id_trails_the_contract_rather_than_entering_it():
    """OpenAlex's own id for the paper ('W2168190474'), which is what lets a row be
    looked up without a DOI round-trip. It goes AFTER the 35: the contract is an
    agreement with a pipeline we do not control, and positional readers of the
    existing export must keep working."""
    out = transform_sources.to_output_shape(_built_frame())
    columns = list(out.columns)
    assert columns[:2] == ["id", "id_md5"]
    assert columns[2:37] == FLORA_OUTPUT_CONTRACT
    assert "oa_work_id_o" in columns[37:]
    assert "oa_work_id_r" in columns[37:]
    assert out["oa_work_id_o"].iloc[0] == "W2741809807"
    assert pd.isna(out["oa_work_id_o"].iloc[1])


def test_the_work_id_survives_the_projection_inside_build():
    """The failure this guards against has happened once already: build() reindexed
    to the FLoRA columns alone and silently dropped every enriched field before
    to_output_shape could see it."""
    assert "oa_work_id_o" in transform_sources.ENRICHMENT_COLUMNS
    assert "oa_work_id_r" in transform_sources.ENRICHMENT_COLUMNS


def test_the_work_id_is_not_the_open_access_url():
    """Different things, and the reason the id was missing for so long. A paper has a
    work id whether or not anyone has posted a free copy of it, so oa_url_* cannot
    stand in for it."""
    columns = list(transform_sources.to_output_shape(_built_frame()).columns)
    for column in ("oa_url_o", "oa_url_r", "oa_work_id_o", "oa_work_id_r"):
        assert columns.count(column) == 1


def test_the_work_id_is_dropped_for_an_exact_contract_match():
    """keep_extras=False means 'exactly the 35 the R notebook writes' — a new column
    must not leak into that file."""
    out = transform_sources.to_output_shape(_built_frame(), keep_extras=False)
    assert "oa_work_id_o" not in out.columns


def test_extras_can_be_dropped_for_an_exact_contract_match():
    out = transform_sources.to_output_shape(_built_frame(), keep_extras=False)
    assert list(out.columns) == ["id", "id_md5"] + FLORA_OUTPUT_CONTRACT


def test_doi_hash_is_three_hex_characters():
    """substr(openssl::md5(doi), 1, 3) in R — 4,096 buckets, deliberately lossy."""
    h = transform_sources.doi_hash("10.1177/09567976221082637")
    assert h == "a61" and len(h) == 3


def test_doi_hash_of_nothing_is_nothing():
    """A hash of the empty string would be a real-looking value on a row that has
    no DOI, and would bucket every such row together."""
    for value in (None, "", "   ", float("nan")):
        assert transform_sources.doi_hash(value) is None


def test_hashes_are_derived_for_rows_that_have_a_doi():
    out = transform_sources.to_output_shape(_built_frame())
    assert out["doi_o_hash"].iloc[0] == transform_sources.doi_hash("10.1177/09567976221082637")
    assert pd.isna(out["doi_o_hash"].iloc[1])
