"""The transform's duplicate steps, run on source rows without a database.

build() takes its source rows and the metadata from the caller; the few small tables
it still reads (aliases, exclusions, rulings, corrections) come from a stub here.

Step 5 drops a row only when every identifier repeats another's. Rows of one paper
pair that differ only because one lacks a url_r are combined by step 5b. Two url_r
values under one pair are two reports, and two records.
"""
import inspect
import re

import pandas as pd
import pytest

import flora_edits
import preprint_dedup
import transform_sources
from extractor_vocab import REPLICATION_OUTCOMES

ORIGINAL, REPORT = "10.9999/original.1", "10.9999/report.1"

# Every column load() selects, blank.
LOADED = re.search(r"SELECT (.*?) FROM source_records", inspect.getsource(transform_sources.load),
                   re.S).group(1)
BLANK = {re.split(r"\s+AS\s+|::", column.strip())[-1]: None for column in LOADED.split(",")}


class Tables:
    """The small tables build() reads besides its rows and metadata: identity
    aliases for the replication outcomes, the exclusions given, and no rulings or
    corrections (their tables are missing, as before the site first starts)."""

    def __init__(self, exclusions=()):
        self.exclusions = list(exclusions)
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {"present": False}

    def fetchall(self):
        if "FROM outcome_alias" in self.sql:
            return [{"raw_value": v, "canonical_value": v} for v in REPLICATION_OUTCOMES]
        if "FROM transform_exclusions" in self.sql:
            return self.exclusions
        return []


@pytest.fixture(autouse=True)
def no_confirmed_rulings(monkeypatch):
    """The confirmed file's original-side rulings make apply_confirmed() run the pair
    merge over every row at step 2b. Without them, each step tested is the one that
    acts, whatever the file holds today."""
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [])


def source(display_id, **values):
    """One source row as load() returns it: a replication of ORIGINAL by REPORT."""
    return {**BLANK, "record_id": f"id-{display_id}", "display_id": display_id,
            "source": "replications", "type": "replication",
            "doi_o": ORIGINAL, "doi_r": REPORT, **values}


def build(*rows, metadata=None, exclusions=()):
    return transform_sources.build(Tables(exclusions), verbose=False,
                                   rows=pd.DataFrame(list(rows)), metadata=metadata or {})


def by_id(built):
    return built.set_index("source_display_id")


def value(cell):
    return None if cell is None or (isinstance(cell, float) and pd.isna(cell)) else cell


def test_the_stub_loads_what_load_selects():
    assert {"record_id", "display_id", "url_r", "duplicate_status", "out_quote_source",
            "outcome_robustness_quote"} <= set(BLANK)


# ── the key ───────────────────────────────────────────────────────────────────

def test_the_key_is_every_identifier():
    frame = pd.DataFrame([source("REPL-000001", url_r="https://www.OSF.io/abc/")])
    assert transform_sources.dedup_keys(frame).tolist() == [
        f"replication|{ORIGINAL}|{REPORT}|osf.io/abc"]


def test_the_registry_stores_the_key_step_5_collapses_on():
    """flora_records.dedup_key, for information: the key dedup_keys gives."""
    rows = [source("REPL-000001", url_r="https://osf.io/abc"),
            source("REPL-000002", url_r="https://osf.io/def")]
    built = build(*rows)
    expected = transform_sources.dedup_keys(pd.DataFrame(rows))
    assert [built.attrs["dedup_key"][row["record_id"]] for row in rows] == expected.tolist()


def test_two_reports_of_one_paper_pair_are_two_records():
    """Individual reports under one meta-paper (FRED-000166 and FRED-000167): the
    step used to keep the first and log their outcomes as a clash."""
    built = build(source("REPL-000001", url_r="https://rpubs.com/a/pilotB", outcome="failed"),
                  source("REPL-000002", url_r="https://rpubs.com/b/pilotA", outcome="mixed"))
    assert by_id(built)["outcome"].to_dict() == {"REPL-000001": "failed", "REPL-000002": "mixed"}
    assert built.attrs["outcome_conflicts"] == []
    assert all(ids == [] for ids in built.attrs["merged_record_ids"].values())


def test_a_row_without_a_link_is_combined_with_the_one_that_has_it():
    """Step 5b, as in R: the second row's values are combined, not dropped."""
    built = build(source("REPL-000001", outcome="successful", outcome_quote="It replicated.",
                         out_quote_source="abstract", study_o="1"),
                  source("REPL-000002", url_r="https://osf.io/report", outcome="successful",
                         outcome_quote="The effect was found again.", out_quote_source="abstract",
                         study_o="2", abstract_r="The abstract."))
    [row] = built.to_dict("records")
    assert row["source_display_id"] == "REPL-000001"
    assert row["url_r"] == "https://osf.io/report"
    assert row["outcome_quote"] == "It replicated. || The effect was found again."
    assert row["abstract_r"] == "The abstract."
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-REPL-000002"]


def test_a_row_ruled_distinct_does_not_absorb_a_copy_without_its_link():
    """The ruling exempts it from both steps, so the copy stays a record of its own,
    and Source Records keeps their group open until someone rules on the copy."""
    built = build(source("REPL-000001", url_r="https://osf.io/a", duplicate_status="distinct"),
                  source("REPL-000002"))
    assert sorted(built["source_display_id"]) == ["REPL-000001", "REPL-000002"]


def test_rows_with_no_identifiers_never_collide():
    """Of one type and with no doi_o, doi_r or url_r, they still share a key; the
    step used to keep only the first."""
    built = build(source("REPL-000001", doi_o=None, doi_r=None, ref_r="One report"),
                  source("REPL-000002", doi_o=None, doi_r=None, ref_r="Another report"))
    assert sorted(built["source_display_id"]) == ["REPL-000001", "REPL-000002"]
    blank = pd.DataFrame([source("REPL-000001", doi_o=None, doi_r=None)])
    assert not transform_sources.has_identifier(blank).any()


def test_a_tracking_parameter_does_not_make_a_second_report():
    """FRED-000520 and FRED-000521: one thesis, linked once with ?utm_source=…"""
    built = build(source("FRED-000520", doi_r=None, url_r="https://thekeep.eiu.edu/theses/5025"),
                  source("FRED-000521", doi_r=None, url_r="https://thekeep.eiu.edu/theses/5025/"
                                                          "?utm_source=thekeep.eiu.edu%2Ftheses%2F5025"))
    assert built["source_display_id"].tolist() == ["FRED-000520"]
    assert built.attrs["merged_record_ids"]["id-FRED-000520"] == ["id-FRED-000521"]


# ── exact duplicates: the dropped rows add, and never overwrite ───────────────

def test_a_dropped_row_fills_what_the_survivor_left_blank():
    built = build(source("REPL-000001", url_r="https://osf.io/a", ref_r="Survivor (2020).",
                         outcome="successful"),
                  source("REPL-000002", url_r="https://osf.io/a/", ref_r="Dropped (2020).",
                         year_r="2020", abstract_r="Its abstract.", outcome="successful"))
    [row] = built.to_dict("records")
    assert (row["ref_r"], row["year_r"], row["abstract_r"]) == (
        "Survivor (2020).", "2020", "Its abstract.")
    assert row["source_display_id"] == "REPL-000001"
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-REPL-000002"]


def test_a_missing_outcome_comes_with_its_own_quote():
    """FRED-000722 and FRED-000980: the step kept the first row's blank outcome, and
    the outcome only the second had was lost. It must not land beside the first
    row's "no narrative report available" either."""
    built = build(source("FRED-000722", doi_r=None, url_r="https://osf.io/74892/",
                         outcome_quote="no narrative report available", out_quote_source="abstract"),
                  source("FRED-000980", doi_r=None, url_r="https://osf.io/74892/", outcome="mixed",
                         outcome_quote="No interpretation reported, only statistical results - "
                                       "yet they are mixed", out_quote_source="full text"))
    [row] = built.to_dict("records")
    assert row["source_display_id"] == "FRED-000722"
    assert row["outcome"] == "mixed"
    assert row["outcome_quote"].startswith("No interpretation reported")
    assert row["outcome_quote_source"] == "full text"
    assert built.attrs["outcome_conflicts"] == []          # one outcome only: no clash


def test_a_survivors_outcome_takes_a_quote_only_from_the_same_outcome():
    built = build(source("REPL-000001", url_r="https://osf.io/a", outcome="failed"),
                  source("REPL-000002", url_r="https://osf.io/a", outcome="mixed",
                         outcome_quote="Partly replicated.", out_quote_source="abstract"),
                  source("REPL-000003", url_r="https://osf.io/a", outcome="failed",
                         outcome_quote="It did not replicate.", out_quote_source="full text"))
    [row] = built.to_dict("records")
    assert (row["outcome"], row["outcome_quote"], row["outcome_quote_source"]) == (
        "failed", "It did not replicate.", "full text")


def test_a_survivors_own_quote_is_never_replaced():
    built = build(source("REPL-000001", outcome="failed", outcome_quote="Its own quote."),
                  source("REPL-000002", outcome="failed", outcome_quote="Another quote.",
                         out_quote_source="abstract"))
    [row] = built.to_dict("records")
    assert (row["outcome_quote"], value(row["outcome_quote_source"])) == ("Its own quote.", None)


def reproduction(display_id, **values):
    return source(display_id, source="reproductions", type="reproduction", **values)


def test_a_reproductions_axes_come_whole_and_its_outcome_and_quote_follow():
    built = build(reproduction("REPRO-000001", outcome_computation="computationally reproducible",
                               outcome_computational_quote="The code ran.",
                               out_quote_computational_source="abstract"),
                  reproduction("REPRO-000002", outcome_computation="computational issues",
                               outcome_computational_quote="The code failed.",
                               out_quote_computational_source="full text",
                               outcome_robustness="robust",
                               outcome_robustness_quote="Robust to other choices.",
                               out_quote_robust_source="full text"))
    [row] = built.to_dict("records")
    assert (row["outcome_computation"], row["outcome_computational_quote"],
            row["out_quote_computational_source"]) == (
        "computationally reproducible", "The code ran.", "abstract")
    assert (row["outcome_robustness"], row["outcome_robustness_quote"],
            row["out_quote_robust_source"]) == ("robust", "Robust to other choices.", "full text")
    assert row["outcome"] == "computationally reproducible, robust"
    assert row["outcome_quote"] == ("Computational: The code ran. "
                                    "|| Robustness: Robust to other choices.")
    assert row["outcome_quote_source"] == "Computational: abstract || Robustness: full text"


def test_a_reproduction_merged_at_5b_publishes_the_quotes_of_its_axes():
    """The pair merge pastes every row's quote together but takes the axes whole: the
    quote of the computational axis it dropped must not be published beside it."""
    built = build(reproduction("REPRO-000001", url_r="https://osf.io/report",
                               outcome_computation="computationally reproducible",
                               outcome_computational_quote="The code ran.",
                               out_quote_computational_source="abstract"),
                  reproduction("REPRO-000002", outcome_computation="computational issues",
                               outcome_computational_quote="The code failed.",
                               out_quote_computational_source="full text",
                               outcome_robustness="robust", outcome_robustness_quote="Robust.",
                               out_quote_robust_source="full text"))
    [row] = built.to_dict("records")
    assert (row["outcome_computational_quote"], row["outcome_robustness_quote"]) \
        == ("The code ran.", "Robust.")
    assert row["outcome_quote"] == "Computational: The code ran. || Robustness: Robust."
    assert row["outcome_quote_source"] == "Computational: abstract || Robustness: full text"


def test_double_encoded_metadata_is_published_decoded():
    built = build(source("REPL-000001", outcome="successful"), metadata={REPORT: {
        "title": "Replication of Rich &amp;amp; Gureckis (2018)",
        "oa_url": "https://aisel.aisnet.org/viewcontent.cgi?article=1063&amp;amp;context=trr"}})
    [row] = built.to_dict("records")
    assert row["title_r"] == "Replication of Rich & Gureckis (2018)"
    assert row["oa_url_r"] == "https://aisel.aisnet.org/viewcontent.cgi?article=1063&context=trr"


def test_the_website_record_still_wins_over_what_the_others_add():
    website = source("VAL-000001", source="validated", url_r="https://osf.io/a",
                     outcome="successful", outcome_quote="Website quote.",
                     out_quote_source="full text")
    built = build(source("REPL-000001", url_r="https://osf.io/a", outcome="mixed",
                         outcome_quote="Sheet quote.", out_quote_source="abstract"), website)
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["outcome"], row["outcome_quote"], row["source"]) == (
        "REPL-000001", "successful", "Website quote.", "validated")
    [conflict] = built.attrs["outcome_conflicts"]
    assert (conflict["outcomes"], conflict["resolved_by"]) == ("mixed | successful", "website record")


def test_a_clash_among_exact_duplicates_is_still_left_to_a_person():
    """Filling a blank outcome settles nothing: the first outcome stands in and the
    clash is listed, as before (merge rules would make this one "mixed")."""
    built = build(source("REPL-000001", url_r="https://osf.io/a"),
                  source("REPL-000002", url_r="https://osf.io/a", outcome="failed"),
                  source("REPL-000003", url_r="https://osf.io/a", outcome="successful"))
    [row] = built.to_dict("records")
    assert row["outcome"] == "failed"
    assert row[preprint_dedup.CONFLICT_COLUMN] == "failed | successful"
    [conflict] = built.attrs["outcome_conflicts"]
    assert (conflict["resolved_to"], conflict["resolved_by"]) == (
        "failed", preprint_dedup.NEEDS_DECISION)


def test_the_fill_never_touches_identity_provenance_or_review_state():
    survivor = pd.Series(source("REPL-000001", validation_status=None, reviewed_by=None,
                                merged_record_ids=[], outcome_conflict=None))
    dropped = pd.Series(source("REPL-000002", source="validated",
                               validation_status="validated - chosen", reviewed_by="luke",
                               merged_record_ids=["id-REPL-000009"],
                               outcome_conflict="failed | mixed", ref_o="A reference."))
    assert preprint_dedup.fill_blanks([survivor, dropped]) == {"ref_o": "A reference."}


# ── URLs ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, expected", [
    ("https://osf.io/abc/?utm_source=x&view_only=1&UTM_Medium=y", "osf.io/abc/?view_only=1"),
    ("https://thekeep.eiu.edu/theses/5025/?utm_source=thekeep.eiu.edu%2Ftheses%2F5025",
     "thekeep.eiu.edu/theses/5025"),
    ("https://osf.io/abc?utm_campaign=repec&utm_medium=email#files", "osf.io/abc#files"),
    ("https://www.proquest.com/docview/221001969?pq-origsite=gscholar&fromopenview=true",
     "proquest.com/docview/221001969?pq-origsite=gscholar&fromopenview=true"),
    ("https://osf.io/abc/", "osf.io/abc"),
    ("https://osf.io/x/?view_only=", "osf.io/x/?view_only="),
    ("https://osf.io/abc?view_only=1&&utm_source=x", "osf.io/abc?view_only=1&"),
    ("https://osf.io/abc?&utm_source=x&", "osf.io/abc"),
    ("https://osf.io/abc?utm_source", "osf.io/abc"),               # a tag without a value
    ("https://osf.io/abc?utmx=1&utm=2", "osf.io/abc?utmx=1&utm=2"),  # not tags
    ("https://osf.io/abc#utm_source=x", "osf.io/abc#utm_source=x"),  # a fragment, no query
    ("https://osf.io/utm_source/abc", "osf.io/utm_source/abc"),      # a path
    ("https://osf.io/abc%3Futm_source=x", "osf.io/abc%3futm_source=x"),  # an encoded '?'
])
def test_tracking_parameters_are_dropped_and_every_other_kept_in_order(url, expected):
    assert transform_sources.norm_url(url) == expected


@pytest.mark.parametrize("value", [None, float("nan"), "", "   ", "?", "?&", "?utm_source=x",
                                   "#frag?utm_x=1", "https://osf.io/abc??utm_a=1", 12345])
def test_any_value_normalises_without_an_error(value):
    """norm_url runs on every url_r of every build, so one exception stops the build."""
    result = transform_sources.norm_url(value)
    assert result is None or isinstance(result, str)


def test_an_exclusion_matches_a_link_with_or_without_its_tracking_tag():
    exclusion = {"doi_o": None, "doi_r": None, "url_r": "https://osf.io/abc?utm_source=sheet",
                 "reason": "not a replication"}
    assert transform_sources.matching_exclusion([exclusion], None, None, "https://osf.io/abc/")
    built = build(source("REPL-000001", doi_r=None, url_r="https://osf.io/abc"),
                  source("REPL-000002"), exclusions=[exclusion])
    assert built["source_display_id"].tolist() == ["REPL-000002"]


# ── an automatic preprint replacement brings the new DOI's metadata ──────────

PSYCEXTRA, JOURNAL = "10.1037/e519682015-069", "10.1177/0956797612464058"
METADATA = {
    PSYCEXTRA: {"title": "Political Extremism is Supported by an Illusion of Understanding",
                "authors": "Philip Fernbach; Todd Rogers", "journal": "PsycEXTRA Dataset",
                "year": "2012", "oa_work_id": "W4237318195", "bibtex_ref": "@misc{Fernbach_2012}",
                "apa_ref": "Fernbach, P. (2012). PsycEXTRA Dataset."},
    JOURNAL: {"title": "Political Extremism Is Supported by an Illusion of Understanding",
              "authors": "Philip Fernbach; Todd Rogers", "journal": "Psychological Science",
              "year": "2013", "volume": "24", "issue": "6", "pages": "939-946",
              "oa_work_id": "W2161788906", "bibtex_ref": "@article{Fernbach_2013}",
              "apa_ref": "Fernbach, P. (2013). Psychological Science, 24(6), 939-946."},
}


def test_a_replaced_original_describes_the_doi_it_now_carries():
    """REPL-000332 and VAL-000332: the PsycEXTRA copy loses to the journal article,
    and the row moved to it publishes the article's title, journal and year."""
    built = build(source("REPL-000332", doi_o=PSYCEXTRA, doi_r="10.9999/one",
                         ref_o="Fernbach (2012). The sheet's reference."),
                  source("VAL-000332", source="validated", doi_o=JOURNAL, doi_r="10.9999/two"),
                  metadata=METADATA)
    rows = by_id(built)
    assert rows["doi_o"].to_dict() == {"REPL-000332": JOURNAL, "VAL-000332": JOURNAL}
    for column in ("title_o", "journal_o", "year_o", "volume_o", "pages_o", "oa_work_id_o",
                   "bibtex_ref_o", "ref_o", "url_o"):
        assert rows.at["REPL-000332", column] == rows.at["VAL-000332", column], column
    assert rows.at["REPL-000332", "journal_o"] == "Psychological Science"
    assert rows.at["REPL-000332", "url_o"] == f"https://doi.org/{JOURNAL}"
    [decision] = built.attrs["preprint_dedup_log"]
    assert (decision["doi_remove"], decision["applied_action"]) == (PSYCEXTRA, "auto_keep_2")


NBER, ARTICLE = "10.3386/w6614", "10.1016/s0047-2727(99)00036-5"
GOOLSBEE = {
    NBER: {"title": "Coveting Thy Neighbor's Manufacturing", "authors": "Austan Goolsbee",
           "journal": "National Bureau of Economic Research", "year": "1998",
           "bibtex_ref": "@techreport{Goolsbee_1998}"},
    ARTICLE: {"title": "Coveting thy neighbor's manufacturing", "authors": "Austan Goolsbee",
              "journal": "Journal of Public Economics", "year": "2000"},
}


def test_a_replaced_row_merged_with_the_articles_row_describes_the_article():
    """REPL-001032 and REPL-001530: one replication recorded under the NBER working
    paper and under the journal article. Once the DOI is replaced the two rows merge,
    and the merged row must not keep the working paper's metadata."""
    built = build(source("REPL-001032", doi_o=NBER, outcome="mixed"),
                  source("REPL-001530", doi_o=ARTICLE, outcome="mixed"), metadata=GOOLSBEE)
    [row] = built.to_dict("records")
    assert (row["doi_o"], row["journal_o"], row["year_o"]) == (
        ARTICLE, "Journal of Public Economics", "2000")
    assert value(row["bibtex_ref_o"]) is None
    assert row["source_display_id"] == "REPL-001032"


def test_a_row_that_absorbs_a_replaced_original_is_joined_again_too():
    """The article's row comes first this time, so it survives the merge with its DOI
    unchanged. The working paper's row it absorbs still fills what it lacked, the
    working paper's BibTeX here, which the join again clears."""
    built = build(source("REPL-000001", doi_o=ARTICLE, outcome="mixed"),
                  source("REPL-000002", doi_o=NBER, outcome="mixed"), metadata=GOOLSBEE)
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["doi_o"]) == ("REPL-000001", ARTICLE)
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-REPL-000002"]
    assert value(row["bibtex_ref_o"]) is None
    assert (row["journal_o"], row["year_o"]) == ("Journal of Public Economics", "2000")


# ── the correction warnings ───────────────────────────────────────────────────

def _records(*rows):
    return pd.DataFrame([{"export_id": f"FLORA-{n}", "flora_id": f"REPL-{n}",
                          "source_display_id": f"REPL-{n}", **row}
                         for n, row in enumerate(rows, 1)])


PAIR = {"type": "replication", "doi_o": ORIGINAL, "doi_r": REPORT}


def test_a_correction_warning_names_only_a_record_with_every_identifier_the_same():
    """Another report under the same pair is its own record, as the build has it."""
    frame = _records({**PAIR, "url_r": "https://osf.io/a"}, {**PAIR, "url_r": "https://osf.io/b"},
                     {**PAIR, "url_r": "http://www.osf.io/a/"})
    [warning] = flora_edits.duplicate_warnings(frame, frame.iloc[0].to_dict())
    assert warning.startswith("REPL-3 has the same original and replication identifiers.")


def test_a_correction_warning_names_a_record_the_pair_merge_would_combine_it_with():
    """Step 5b combines the rows of one pair when only one of them has a link."""
    frame = _records({**PAIR, "url_r": None}, {**PAIR, "url_r": "https://osf.io/a"})
    for mine, other in ((0, "REPL-2"), (1, "REPL-1")):
        [warning] = flora_edits.duplicate_warnings(frame, frame.iloc[mine].to_dict())
        assert warning.startswith(f"{other} has the same original and replication DOIs, and "
                                  "only one of the two has a replication link.")


@pytest.mark.parametrize("rows", [
    # Two links are two reports, and step 5b keeps a row without one apart from both.
    [{"url_r": None}, {"url_r": "https://osf.io/a"}, {"url_r": "https://osf.io/b"}],
    # Without a doi_r, step 5b combines nothing.
    [{"doi_r": None, "url_r": None}, {"doi_r": None, "url_r": "https://osf.io/a"}],
    # A replication and a reproduction of one paper are two records.
    [{"url_r": None}, {"type": "reproduction", "url_r": "https://osf.io/a"}],
])
def test_no_correction_warning_where_the_duplicate_steps_keep_two_records(rows):
    frame = _records(*({**PAIR, **row} for row in rows))
    assert flora_edits.duplicate_warnings(frame, frame.iloc[0].to_dict()) == []


def test_the_build_warns_about_a_correction_as_the_save_does(monkeypatch, capsys):
    """One helper for both warnings (record_keys). REPL-000002 is corrected onto the
    pair of REPL-000001, without its link: step 5b would combine the two."""
    monkeypatch.setattr(flora_edits, "load_identifiers", lambda cur: {
        "records": [(["id-REPL-000002"], {"doi_r": REPORT})], "primaries": {"id-REPL-000002"}})
    transform_sources.build(Tables(), verbose=True, metadata={}, rows=pd.DataFrame([
        source("REPL-000001", url_r="https://osf.io/a"),
        source("REPL-000002", doi_r="10.9999/another.report")]))
    assert ("1 corrected row(s) now match another record's identifiers: REPL-000002 "
            in capsys.readouterr().out)
