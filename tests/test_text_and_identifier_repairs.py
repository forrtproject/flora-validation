"""Repairs the build makes to provider and sheet text, and the checks that report
what slips past them: mis-decoded characters, percent-encoded DOIs, references
without their author, SCORE file names as titles, OpenAlex pages as report links.
The examples are FLoRA 2026.11.2 cells reported in fred-data#179."""
import json

import pandas as pd
import pytest

import bibliographic_helpers as bh
import transform_sources as ts
import validate_flora as vf


# ── mis-decoded UTF-8 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("broken,repaired", [
    # Windows-1252
    ("based on the learnerâ€™s performance", "based on the learner’s performance"),
    ("in a testâ€“retest design", "in a test–retest design"),
    ('[{"given":"Anke","family":"KÃ¶bach"}]', '[{"given":"Anke","family":"Köbach"}]'),
    ("DaniÃ«l Lakens", "Daniël Lakens"),
    # Mac Roman
    ("Replication and extensions of Kruger‚Äôs (1999)", "Replication and extensions of Kruger’s (1999)"),
    ("170‚Äâreplications", "170 replications"),
    # Read left to right: "‚â•" is Mac Roman's ≥, not the middle of a Windows-1252 "â•‚".
    ("for‚ÄÖ‚â•‚ÄÖ5 years", "for ≥ 5 years"),
    # Mis-decoded two and three times over.
    ("14x ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“ 30x", "14x – 30x"),
    ("ANOVA (ÃƒÅ½Ã‚Â± = 5%)", "ANOVA (α = 5%)"),
])
def test_mis_decoded_text_is_decoded_again(broken, repaired):
    assert ts.has_mojibake(broken)
    assert ts.repair_mojibake(broken) == repaired
    # clean_text then makes the thin and six-per-em spaces plain ones.
    assert ts.clean_text(broken, one_line=True) == ts._WS_RE.sub(" ", repaired)


@pytest.mark.parametrize("text", [
    "Köbach", "Łódź", "naïve – test", "“École”", "Ä", "Ångström",
    # French spacing: "à", a no-break space and "«" are also a Windows-1252 reading
    # of U+202B, a format character, which is no repair.
    "à\xa0« citation »",
    "Démonstration", "Straße", "Ïle", None, 1.5,
])
def test_real_text_is_left_alone(text):
    assert ts.repair_mojibake(text) == text
    assert not ts.has_mojibake(text)


def test_mojibake_check_reports_what_the_build_did_not_repair():
    frame = pd.DataFrame([{"flora_id": "X", "title_r": "the learnerâ€™s performance",
                           "author_o": '[{"family":"KÃ¶bach"}]', "abstract_r": "fine"}])
    items = vf._check_mojibake(frame)
    assert [item["column"] for item in items] == ["title_r", "author_o"]
    assert "â€™" in items[0]["value"]


# ── percent-encoded DOIs ──────────────────────────────────────────────────────

ENCODED = "10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y"
DECODED = "10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y"


@pytest.mark.parametrize("value", [ENCODED, "https://doi.org/" + ENCODED, ENCODED.upper()])
def test_a_percent_encoded_doi_is_decoded(value):
    assert ts.clean_doi(value) == DECODED
    assert bh.normalise_key(value) == DECODED


def test_a_plain_doi_is_unchanged():
    assert ts.clean_doi("10.1525/collabra.77859") == "10.1525/collabra.77859"


# ── references without their author ───────────────────────────────────────────

@pytest.mark.parametrize("ref,authors,expected", [
    # A group author dropped by DOI content negotiation.
    ("(Ed.). (2024). First language effects. Studies in Second Language Acquisition, 46(5).",
     [{"name": "The TwiLex Group"}],
     "The TwiLex Group. (2024). First language effects. Studies in Second Language Acquisition, 46(5)."),
    # No "(Ed.).", and authors as the JSON the CSV carries.
    (" (2009). The Impact of Uncertainty Shocks. Econometrica, 77(3), 623–685.",
     json.dumps([{"given": "Nicholas", "family": "Bloom"}]),
     "Bloom, N. (2009). The Impact of Uncertainty Shocks. Econometrica, 77(3), 623–685."),
    ("(n.d.). Title.", "Antràs, Pol; Chor, Davin", "Antràs, P., & Chor, D. (n.d.). Title."),
])
def test_a_missing_author_part_is_supplied(ref, authors, expected):
    assert bh.authorless_reference(ref)
    assert bh.add_reference_authors(ref, authors) == expected


def test_more_than_twenty_authors_are_cut_as_apa_7_does():
    authors = [{"family": f"A{i}", "given": "B"} for i in range(1, 31)]
    ref = bh.add_reference_authors("(2010). Title.", authors)
    assert ref.startswith("A1, B., A2, B.,") and ", A19, B., … A30, B. (2010). Title." in ref


@pytest.mark.parametrize("ref,authors", [
    ("Smith, J. (2009). Title.", [{"family": "Bloom"}]),      # has its author
    ("(Ed.). (2013). Organizing the Global Value Chain.", None),  # none known
    ("(Ed.). (2013). Organizing the Global Value Chain.", "NA"),
    (None, [{"family": "Bloom"}]),
])
def test_a_reference_is_left_as_it_is_without_a_known_author(ref, authors):
    assert bh.add_reference_authors(ref, authors) == ref


def test_authorless_reference_check():
    frame = pd.DataFrame([{"flora_id": "X", "apa_ref_o": "(2010). Genome-wide study.",
                           "apa_ref_r": "Chen, C. (2010). A title."}])
    assert [item["column"] for item in vf._check_authorless_references(frame)] == ["apa_ref_o"]


# ── titles that are file names ────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Trémolière_Cognition_2012_8wZ0 - Bagó_Aczel - New Data Replication - 276",
    "van Gastel_PsychMed_2013_AOQj - Vargo - Secondary Data Replication - 21487",
    "Gelfand_covid_R3eV - Szabelska - Reproduction (with author data & code) - zz51",
    "Ritov_Stage2FinalReport.docx",
    "Final report.pdf",
])
def test_file_names_are_not_titles(title):
    assert vf.title_problem(title) == "looks like a file or project name"


@pytest.mark.parametrize("title", [
    "[Replication report for “Too arrogant for their own good?”]",
    "SCORE new data replication of Wurst et al. (2017)",
    "The effect of x_1 on y",
    "Both better and worse than others depending on difficulty",
])
def test_titles_pass(title):
    assert vf.title_problem(title) is None


# ── OpenAlex pages as report links ────────────────────────────────────────────

def test_openalex_report_links_are_reported():
    frame = pd.DataFrame([
        {"flora_id": "A", "doi_r": None, "url_r": "https://openalex.org/W7120759706"},
        {"flora_id": "B", "doi_r": "10.1177/1745691617693624", "url_r": "https://openalex.org/W7112312965"},
        {"flora_id": "C", "doi_r": None, "url_r": "https://osf.io/bjmyx"},
    ])
    details = [item["detail"] for item in vf._check_openalex_links(frame)]
    assert details == ["url_r is an OpenAlex record, the report's only link",
                       "url_r is an OpenAlex record beside the report's DOI"]


def test_the_metadata_join_supplies_a_cached_reference_its_author():
    """A cached provider reference "(Ed.). (2024). …" gets the group author the
    same cache holds, on the row the join writes."""
    frame = pd.DataFrame([{
        "doi_r": "10.1017/s0272263124000275", "url_r": None, "ref_r": None, "author_r": None,
        "abstract_r": None, "year_r": None, "title_r": None, "journal_r": None,
        "volume_r": None, "issue_r": None, "pages_r": None, "language_r": None,
        "oa_url_r": None, "oa_work_id_r": None, "bibtex_ref_r": None}])
    meta = {"10.1017/s0272263124000275": {
        "title": "First language effects on incidental vocabulary learning through bimodal input",
        "apa_ref": "(Ed.). (2024). First language effects on incidental vocabulary learning "
                   "through bimodal input. Studies in Second Language Acquisition, 46(5), 1413–1438.",
        "authors_json": json.dumps([{"name": "The TwiLex Group"}])}}
    ts.join_metadata(frame, meta, sides=("r",))
    assert frame.loc[0, "ref_r"].startswith("The TwiLex Group. (2024). First language effects")


# ── one separator in identifier lists ─────────────────────────────────────────

SICI = "10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y"


@pytest.mark.parametrize("value,expected", [
    (SICI, SICI),                                    # its semicolon is part of the DOI
    (SICI + "; 10.2/b", SICI + ", 10.2/b"),
    ("10.1/a;10.2/b", "10.1/a, 10.2/b"),
    ("10.1/a ,10.2/b, 10.1/A", "10.1/a, 10.2/b"),
    ("10.1/a | https://osf.io/x", "10.1/a, https://osf.io/x"),
    ("10.1/a\n10.2/b", "10.1/a, 10.2/b"),
    ("10.1/a 10.2/b", "10.1/a, 10.2/b"),
    ("10.1/a, 10.2/b", "10.1/a, 10.2/b"),
    ("  ", None),
])
def test_identifier_lists_are_joined_by_one_separator(value, expected):
    assert ts.normalise_identifier_list(value) == expected


def test_export_and_corrections_use_the_separator():
    import flora_edits
    assert flora_edits.clean_value("alt_identifier_r", "10.1/a; 10.2/b", "replication") == "10.1/a, 10.2/b"
    frame = pd.DataFrame([{"doi_o": "10.1/o", "doi_r": "10.1/r", "title_o": "O", "title_r": "R",
                           "type": "replication", "alt_identifier_r": "10.1/a;10.2/b",
                           "alt_identifier_o": SICI}])
    out = ts.to_output_shape(frame)
    assert out.loc[0, "alt_identifier_r"] == "10.1/a, 10.2/b"
    assert out.loc[0, "alt_identifier_o"] == SICI


def test_separator_check():
    frame = pd.DataFrame([{"flora_id": "A", "alt_identifier_o": SICI, "alt_identifier_r": "10.1/a; 10.2/b"},
                          {"flora_id": "B", "alt_identifier_o": None, "alt_identifier_r": "10.1/a, 10.2/b"}])
    items = vf._check_identifier_separators(frame)
    assert [(item["column"], item["value"]) for item in items] == [("alt_identifier_r", "10.1/a; 10.2/b")]
