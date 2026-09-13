"""Tests for apa_references — recovering a title from a hand-typed APA string.

This is the last resort for rows with no DOI, where nothing can be looked up. The
parser's job is to find where the title ends, which is harder than it sounds: a
replication's title routinely quotes the original paper's title, brackets a year,
or ends in "et al." — each of which looks like the end of a sentence.

Getting it wrong is not a blank field but a WRONG title on a published row, so the
tests below lean on the shapes that actually occur in the sheets.
"""
import pandas as pd
import pytest

import apa_references as apa


# ── the ordinary shape ────────────────────────────────────────────────────────

def test_a_plain_reference():
    out = apa.parse("Chandler, J. J. (2016, August 19). Replication of a thing. "
                    "Retrieved from osf.io/abc")
    assert out["title"] == "Replication of a thing"
    assert out["year"] == "2016"


def test_the_year_is_taken_from_the_citation_not_the_title():
    """'Replication of Janiszewski & Uy (2008, PS, Study 4b)' is a real title. The
    citation year is the FIRST parenthesised year, not the one inside the title."""
    out = apa.parse("Chandler, J. J. (2016, August 19). "
                    "Replication of Janiszewski & Uy (2008, PS, Study 4b). "
                    "Retrieved from osf.io/abc")
    assert out["year"] == "2016"
    assert out["title"] == "Replication of Janiszewski & Uy (2008, PS, Study 4b)"


def test_a_reference_with_no_year_is_not_a_citation():
    assert apa.parse("Fernbach, Kan & Lynch, 2015 - Study 2 Replication") is None


def test_n_d_is_accepted_but_yields_no_year():
    out = apa.parse("Smith, J. (n.d.). Some working paper. Retrieved from x.org")
    assert out["title"] == "Some working paper"
    assert out["year"] is None


def test_nonsense_returns_nothing():
    for text in (None, "", "   ", "Wiswede · 2021", "10.1234/abc"):
        assert apa.parse(text) is None


# ── where the title ends ──────────────────────────────────────────────────────

def test_a_full_stop_inside_quotes_does_not_end_the_title():
    """A replication quoting the original's title, which itself ends in a stop."""
    out = apa.parse('Smith, J. (2020). Replication of "Does it work. Or not" '
                    'in adults. Journal of Things, 4, 1-9.')
    assert out["title"] == 'Replication of "Does it work. Or not" in adults'


def test_et_al_does_not_end_the_title():
    out = apa.parse("Smith, J. (2020). A replication of Jones et al. 2011 findings. "
                    "Journal of Things, 4, 1-9.")
    assert out["title"] == "A replication of Jones et al. 2011 findings"


def test_a_closing_quote_after_a_full_stop_ends_the_title_and_is_kept():
    out = apa.parse('Smith, J. (2020). Replication of "Does it work." '
                    'Journal of Things, 4, 1-9.')
    assert out["title"] == 'Replication of "Does it work."'


def test_a_title_with_no_trailing_full_stop_runs_to_the_end():
    out = apa.parse("Crawford, J. (2016, May 2). Syntax and output files")
    assert out["title"] == "Syntax and output files"


def test_the_full_stop_itself_is_not_part_of_the_title():
    out = apa.parse("Smith, J. (2020). A short title. Journal, 1, 2.")
    assert out["title"] == "A short title"


def test_a_decimal_point_does_not_end_the_title():
    """Only a stop FOLLOWED BY A SPACE ends it, so '0.42' stays intact."""
    out = apa.parse("Smith, J. (2020). An effect of 0.42 in adults. Journal, 1, 2.")
    assert out["title"] == "An effect of 0.42 in adults"


# ── the tail is not the title ─────────────────────────────────────────────────

def test_a_link_glued_to_the_title_is_not_part_of_it():
    """No full stop separates the two here, so the scan runs to the end of the
    string and adopts the link. Two live rows published a title ending in a URL."""
    out = apa.parse("Tsang & Feldman, Gray et al (2011) Replication and Extension "
                    "https://osf.io/8hdu3/")
    assert out["title"] == "Replication and Extension"


def test_a_reference_whose_remainder_is_only_a_retrieval_statement_has_no_title():
    """The title sits BEFORE the year in this one, so the remainder is the
    retrieval statement alone. It shipped as the row's title_r; no title at all is
    the honest answer, and the row is then logged by the Step 10 report."""
    assert apa.parse(
        "Pashler, H., Harris, C., & Coburn, N.. Elderly-Related Words Prime Slow "
        "Walking . (2011, September 15). Retrieved 04:36, September 23, 2017 from "
        "http://www.PsychFileDrawer.org/replication.php?attempt=MTU%3D") is None


def test_an_ordinary_retrieved_from_tail_still_parses():
    """The guard is anchored, so it must not disturb the common shape where the
    full stop after the title already ends it."""
    out = apa.parse("Beer, J. S. (2016, August 19). "
                    "Replication of G Tabibnia, AB Satpute (2008, PS 19(4)). "
                    "Retrieved from osf.io/x")
    assert out["title"] == "Replication of G Tabibnia, AB Satpute (2008, PS 19(4))"


def test_the_word_retrieved_inside_a_title_is_left_alone():
    """Only a 'title' that IS the retrieval statement is refused — a title may
    perfectly well contain the word."""
    out = apa.parse("Smith, J. (2020). Information Retrieved from Memory. Journal, 1.")
    assert out["title"] == "Information Retrieved from Memory"


# ── the title-first shape ─────────────────────────────────────────────────────

def test_a_title_first_reference_with_no_author():
    """'Title [Working paper]. (n.d.). https://…' — the part before the year is
    the title, not an author list."""
    out = apa.parse("An untitled working paper [Working paper]. (n.d.). "
                    "https://example.org/x")
    assert out["title"] == "An untitled working paper"
    assert out["authors"] is None


def test_the_bracketed_note_is_stripped_from_a_title_first_reference():
    out = apa.parse("Some report [Preprint]. (2021). https://example.org/x")
    assert out["title"] == "Some report"


# ── authors, in THIS pipeline's format ────────────────────────────────────────

def test_authors_come_back_as_display_names():
    """author_o/author_r already hold OpenAlex's '; '-joined display names. The R
    emits CrossRef JSON here; a second format in one column would break readers."""
    out = apa.parse("Adams, D., Koenig, B., & Davis, C. (2018). A title. Journal.")
    assert out["authors"] == "D. Adams; B. Koenig; C. Davis"


def test_a_single_author():
    out = apa.parse("Chandler, J. J. (2016). A title. Journal.")
    assert out["authors"] == "J. J. Chandler"


def test_an_unpairable_author_string_is_kept_whole():
    """An odd number of comma-separated parts means the family/given pairing does
    not hold — an organisation, or a one-word name. Guessing would invent people."""
    out = apa.parse("The World Health Organization (2019). A report. Geneva.")
    assert out["authors"] == "The World Health Organization"


# ── augment(): what it will and will not touch ────────────────────────────────

def _frame():
    return pd.DataFrame({
        "title_r": [None, "Already known", None],
        "author_r": [None, "Someone", "Existing Author"],
        "year_r": [None, "1999", None],
        "ref_r": ["Smith, J. (2020). Recovered title. Journal, 1, 2.",
                  "Jones, K. (2021). Another title. Journal, 3, 4.",
                  "Brown, L. (2022). Third title. Journal, 5, 6."],
    })


def test_augment_fills_only_rows_with_no_title():
    frame = _frame()
    assert apa.augment(frame, "r", verbose=False) == 2
    assert frame.at[0, "title_r"] == "Recovered title"
    assert frame.at[1, "title_r"] == "Already known"


def test_augment_never_overwrites_an_existing_author():
    """A name from OpenAlex outranks one parsed out of free text."""
    frame = _frame()
    apa.augment(frame, "r", verbose=False)
    assert frame.at[2, "author_r"] == "Existing Author"


def test_augment_fills_a_missing_author_and_year():
    frame = _frame()
    apa.augment(frame, "r", verbose=False)
    assert frame.at[0, "author_r"] == "J. Smith"
    assert frame.at[0, "year_r"] == "2020"


def test_augment_is_a_no_op_without_the_reference_column():
    frame = pd.DataFrame({"title_r": [None]})
    assert apa.augment(frame, "r", verbose=False) == 0


def test_augment_reports_how_many_it_filled():
    frame = _frame()
    assert apa.augment(frame, "r", verbose=False) == 2
    # Second pass finds nothing left to do.
    assert apa.augment(frame, "r", verbose=False) == 0


# ── placement in the pipeline ─────────────────────────────────────────────────

def test_recovery_runs_after_deduplication():
    """Dedup matches on title similarity. Supplying ~300 parsed titles BEFORE it
    would change which rows survive — a data change wearing a metadata fix's
    clothes. This asserts the ordering that keeps it to a metadata fix."""
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "transform_sources.py").read_text(
        encoding="utf-8")
    body = source[source.index("def build("):]
    assert body.index("preprint_dedup.resolve(") < body.index("apa_references.augment(")
