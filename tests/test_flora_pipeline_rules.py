"""Tests for the R-notebook rules ported into the transform.

Covers Step 4 (exclusions), 7c (text cleaning), 9b (COS non-replications), 9d
(meta-paper annotation) and 10 (missing-title report). These are the rules that
decide what reaches the published dataset, so each one's edge is pinned down here
rather than left to a run to discover.
"""
from pathlib import Path

import pandas as pd
import pytest

import sync_exclusions
import transform_sources as ts

ROOT = Path(__file__).resolve().parents[1]


# ── Step 7c: text cleaning ────────────────────────────────────────────────────

def test_markup_is_stripped():
    assert ts.clean_text("The <i>Stroop</i> effect") == "The Stroop effect"


def test_entities_are_decoded():
    assert ts.clean_text("Cognition &amp; Emotion") == "Cognition & Emotion"


def test_double_encoded_markup_is_stripped_too():
    """Publisher XML double-encodes: &amp;lt;i&amp;gt; only becomes a literal tag
    after the first unescape, so one pass leaves it behind."""
    assert "<" not in ts.clean_text("A &amp;lt;i&amp;gt;title&amp;lt;/i&amp;gt;")


def test_jats_math_blocks_go():
    assert ts.clean_text("Effect <mml:math><mml:mi>d</mml:mi></mml:math> size") \
        == "Effect d size"


def test_a_soft_line_break_becomes_a_space():
    """A single newline inside a paragraph is a PDF copy artefact; the sentence
    simply continued."""
    assert ts.clean_text("the results\nwere clear") == "the results were clear"


def test_a_paragraph_break_survives():
    assert ts.clean_text("para one\n\npara two") == "para one\n\npara two"


def test_non_breaking_spaces_become_ordinary_ones():
    assert ts.clean_text("a\xa0b") == "a b"


@pytest.mark.parametrize("value", [None, "", "   ", float("nan")])
def test_nothing_in_nothing_out(value):
    assert ts.clean_text(value) is None


def test_markup_only_input_is_not_an_empty_string():
    """'<i></i>' must not become '', which would read as a real but blank value."""
    assert ts.clean_text("<i></i>") is None


def test_a_capitalised_non_entity_is_lowercased_so_it_decodes():
    """CrossRef mis-emits these; html.unescape knows neither spelling, so without
    the repair they reach the published reference verbatim."""
    assert ts.clean_text("Cognition &Amp; Emotion") == "Cognition & Emotion"
    assert ts.clean_text("the author&Rsquo;s claim") == "the author’s claim"


@pytest.mark.parametrize(("raw", "expected"), [
    ("&Auml;ngstr&ouml;m", "Ängström"),      # Ä, not ä
    ("&Oslash;stergaard", "Østergaard"),          # Ø, not ø
    ("&Eacute;mile", "Émile"),                    # É, not é
    ("&Delta;R&sup2;", "ΔR²"),               # Δ, not δ
    ("Cronbach's &Alpha;", "Cronbach's Α"),       # Α, not α
    ("&Omega; reliability", "Ω reliability"),     # Ω, not ω
])
def test_a_capitalised_entity_that_IS_one_keeps_its_case(raw, expected):
    """375 of the HTML5 named entities differ only by case and mean different
    characters, so lowercasing every entity before unescaping silently rewrites
    them: a German surname loses its umlaut's case and a statistics title turns
    Δ into δ. Only a spelling that is not an entity under any casing is repaired."""
    assert ts.clean_text(raw) == expected


# ── the sheet's own year_r ────────────────────────────────────────────────────

def test_the_transform_reads_the_sheets_year_r():
    """source_records.year_r is hand-entered on the replications sheet and is the
    only year 118 rows have. Leaving it out of the SELECT made the fallback below
    unreachable and published those rows with a blank year."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    select = source[source.index("def load(cur):"):source.index("def load_rules(")]
    assert "year_r" in select


def test_the_sheet_year_is_the_last_resort_not_the_first():
    """'Metadata wins' has to include the work-id lookup of Step 7b: filling the
    sheet value before it would make 7b's fillna a no-op on the rows that have
    both."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    body = source[source.index("def build("):]
    assert body.index('("year", f"year_{side}")') < body.index("fillna(sheet_year)")


@pytest.mark.parametrize(("raw", "expected"), [
    ("2020", "2020"),
    ("2020.0", "2020"),      # 244 stored values read like this
    ("  1997.0 ", "1997"),
    ("", None),
    (None, None),
    ("n/a", None),
    ("20200", None),
    (float("nan"), None),
])
def test_a_sheet_year_is_normalised_or_dropped(raw, expected):
    """The column travelled through a float during the sheet import, so '2020.0'
    would otherwise sit beside OpenAlex's '2020' in one published column."""
    assert ts._sheet_year(raw) == expected


# ── Step 7: the original DOI's landing page ───────────────────────────────────

@pytest.mark.parametrize(("raw", "expected"), [
    ("10.1016/0010-0285(72)90003-5", "https://doi.org/10.1016/0010-0285(72)90003-5"),
    ("  10.1037/abc123  ", "https://doi.org/10.1037/abc123"),
    ("10.123/too-short-a-prefix", None),
    ("https://doi.org/10.1016/j.jesp.2015.10.012", None),   # already a URL, not a bare DOI
    ("", None),
    (None, None),
    (float("nan"), None),
])
def test_a_doi_becomes_a_landing_page_or_nothing(raw, expected):
    assert ts._doi_url(raw) == expected


def test_a_missing_doi_does_not_take_the_build_down():
    """The regression: pandas 3 hands a missing str value to .map() as float NaN,
    and NaN is TRUTHY — so the original `if value and re.match(...)` guard passed
    the float straight to re.match. Every row without an original DOI (about 60 of
    2925) raised TypeError, so the FLoRA tab answered 500 and the preparation
    pipeline could never finish a run.

    Exercised through .map() rather than by calling the helper directly, because
    the NaN only appears once pandas does the mapping. Asserted on missingness
    rather than on None: a str column stores the helper's None back as NaN, which
    is what the fillna downstream consumes anyway."""
    column = pd.Series(["10.1016/abc123", None, ""], dtype="str")
    mapped = column.map(ts._doi_url)
    assert mapped[0] == "https://doi.org/10.1016/abc123"
    assert mapped.isna().tolist() == [False, True, True]


def test_the_landing_page_never_overwrites_a_url_the_source_supplied():
    """fillna, not assignment: url_o carries manually entered links that the DOI
    landing page must not replace."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    assert 'df["url_o"] = df["url_o"].fillna(df["doi_o"].map(_doi_url))' in source


# ── provenance: the registered id reaches the export ──────────────────────────

def test_the_export_attaches_the_registered_flora_id():
    """build() leaves flora_id blank because assigning one is a write. The workflow
    runs flora_registry first so the ids exist — 'before the build so the dataset
    carries them' — but run() has to actually attach them, or the column ships
    empty on every row of the published CSV."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    body = source[source.index("def run("):]
    assert "attach_ids(cur, out)" in body
    assert body.index("build(cur") < body.index("attach_ids(cur, out)")
    assert body.index("attach_ids(cur, out)") < body.index("to_output_shape(out)")


# ── Step 9d: meta-papers ──────────────────────────────────────────────────────

def test_the_meta_paper_list_matches_the_notebook():
    for doi in ("10.1126/science.aac4716",          # RP:P
                "10.1027/1864-9335/a000178",        # ML1
                "10.1177/2515245918810225",         # ML2
                "10.1016/j.jesp.2015.10.012",       # ML3
                "10.1098/rsos.231240"):             # Boyce
        assert doi in ts.META_PAPER_DOIS
    assert len(ts.META_PAPER_DOIS) == 12


def test_meta_paper_dois_are_stored_clean():
    """They are compared against clean_doi output, so an uncleaned entry would
    silently never match."""
    for doi in ts.META_PAPER_DOIS:
        assert ts.clean_doi(doi) == doi


def test_the_candidate_threshold_is_the_notebooks():
    assert ts.META_PAPER_CANDIDATE_URLS == 5


# ── Step 9b + 4: exclusions ───────────────────────────────────────────────────

def test_cos_non_replications_are_pairs():
    """Keyed on (doi_o, doi_r): the same replication DOI can be a non-replication
    against one original and a valid entry against another."""
    assert len(sync_exclusions.COS_NON_REPLICATIONS) == 16
    for doi_o, doi_r in sync_exclusions.COS_NON_REPLICATIONS:
        assert doi_o and doi_r


def test_cos_exclusions_carry_doi_o():
    for doi_o, doi_r, url_r, reason in sync_exclusions.COS_EXCLUSIONS:
        assert doi_o and doi_r and url_r is None and reason


def test_notebook_exclusions_match_the_hardcoded_lists():
    dois = {row[1] for row in sync_exclusions.NOTEBOOK_EXCLUSIONS if row[1]}
    urls = {row[2] for row in sync_exclusions.NOTEBOOK_EXCLUSIONS if row[2]}
    assert dois == {"10.31234/osf.io/jfmsz", "10.1177/0956797619831612",
                    "korbmacher_2022"}
    assert urls == {"https://replications.clearerthinking.org/replication-2022psci33-8"}


def test_notebook_exclusions_have_no_doi_o():
    """They exclude on doi_r/url_r regardless of the original they are paired with."""
    assert all(row[0] is None for row in sync_exclusions.NOTEBOOK_EXCLUSIONS)


def test_every_exclusion_row_names_a_target():
    """transform_exclusions CHECKs that doi_r or url_r is present; a row with
    neither would abort the whole sync."""
    for doi_o, doi_r, url_r, reason in (sync_exclusions.NOTEBOOK_EXCLUSIONS
                                        + sync_exclusions.COS_EXCLUSIONS):
        assert doi_r or url_r


def test_the_three_provenances_are_distinct():
    """They are replaced independently; a shared name would make one wipe another."""
    names = {sync_exclusions.SHEET_SOURCE, sync_exclusions.NOTEBOOK_SOURCE,
             sync_exclusions.COS_SOURCE}
    assert len(names) == 3


# ── Step 10: missing-title report ─────────────────────────────────────────────

def _frame():
    return pd.DataFrame({
        "flora_id": ["A", "B", "C", "D"],
        "source": ["validated"] * 4,
        "title_o": ["Original", None, "Original", "Original"],
        "title_r": ["Replication", "Replication", None, None],
        "doi_o": ["10.1/a", "10.1/b", "10.1/c", "10.1/d"],
        "doi_r": ["10.2/a", "10.2/b", None, "10.2/d"],
        "url_r": [None, None, "https://osf.io/x", None],
        "ref_o": ["Smith 2001"] * 4,
        "ref_r": ["Jones 2020"] * 4,
    })


def test_only_rows_missing_a_title_are_reported():
    report = ts.missing_title_report(_frame())
    assert set(report["flora_id"]) == {"B", "C", "D"}


def test_the_reason_says_which_side():
    report = ts.missing_title_report(_frame()).set_index("flora_id")
    assert report.loc["B", "reason"] == "missing title_o"
    assert report.loc["C", "reason"] == "missing title_r"


def test_a_row_missing_both_says_so():
    frame = _frame()
    frame.loc[frame["flora_id"] == "D", "title_o"] = None
    report = ts.missing_title_report(frame).set_index("flora_id")
    assert report.loc["D", "reason"] == "missing both title_o and title_r"


def test_a_blank_string_counts_as_missing():
    """'' and NULL must not be two different spellings of absent."""
    frame = _frame()
    frame.loc[frame["flora_id"] == "A", "title_r"] = "   "
    assert "A" in set(ts.missing_title_report(frame)["flora_id"])


def test_a_complete_frame_reports_nothing():
    frame = _frame()
    frame["title_o"] = "Original"
    frame["title_r"] = "Replication"
    assert ts.missing_title_report(frame).empty


def test_the_report_carries_enough_to_act_on():
    report = ts.missing_title_report(_frame())
    for column in ("reason", "flora_id", "doi_o", "doi_r", "url_r",
                   "apa_ref_o", "apa_ref_r"):
        assert column in report.columns
