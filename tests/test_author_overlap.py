"""Tests for author_overlap — a port of R/augmentation.R's compute_author_overlap.

What it measures is how independent the replication team was. A replication written
by the original authors is a different kind of evidence from one by strangers, and
nothing else in the published output says which you are looking at.

Two things these tests hold down:

  - the DENOMINATOR is the replication team, not the union or the original. "3 of
    the 4 replication authors also wrote the original" is the statement about
    independence; against the original's count the same overlap says something about
    the original team instead.
  - an unknown author list yields None, never 0. Zero means "no shared authors",
    which is a finding; we do not have one.
"""
from pathlib import Path

import pandas as pd
import pytest

import author_overlap as ao
import transform_sources

ROOT = Path(__file__).resolve().parents[1]


# ── pulling family names out of a display-name list ───────────────────────────

def test_families_from_the_usual_separator():
    assert ao.families("Eyal Peer; Laura Brandimarte") == {"peer", "brandimarte"}


def test_matching_ignores_case():
    assert ao.families("EYAL PEER") == ao.families("eyal peer")


def test_initials_are_not_mistaken_for_a_surname():
    """'Smith, J.' loses its comma to punctuation stripping, leaving 'Smith J' —
    whose last token is an initial, not a family name."""
    assert ao.families("Smith, J.") == {"smith"}


def test_a_middle_initial_does_not_become_the_family_name():
    assert ao.families("C. Shawn Green") == {"green"}


def test_alternative_separators_are_accepted():
    """A few sheet rows use 'and' or '&' rather than a semicolon."""
    assert ao.families("Alice Roberts and Bob Stone") == {"roberts", "stone"}
    assert ao.families("Alice Roberts & Bob Stone") == {"roberts", "stone"}


def test_nothing_in_nothing_out():
    for value in (None, "", "   ", "nan", "None"):
        assert ao.families(value) == set()


def test_accented_names_survive():
    assert ao.families("Grégoire Borst; Olivier Houdé") == {"borst", "houdé"}


# ── the pair calculation ──────────────────────────────────────────────────────

def test_a_shared_author_is_counted():
    assert ao.count("Eyal Peer; Laura Brandimarte", "Eyal Peer; Sonam Samat") == (1, 50.0)


def test_no_shared_authors_is_zero_not_missing():
    """This is a real finding — an independent replication — and must be
    distinguishable from 'we could not tell'."""
    assert ao.count("Jeff Ackerman", "Alice Roberts") == (0, 0.0)


def test_the_percentage_is_of_the_replication_team():
    """One shared name out of a one-person replication team is 100%, even though
    the original had four authors. The question is the replication's independence."""
    overlap, pct = ao.count("A One; B Two; C Three; D Four", "A One")
    assert (overlap, pct) == (1, 100.0)


def test_a_replication_by_the_same_team_is_total():
    assert ao.count("Vasilisa Akselevich; Sharon Gilaie-Dotan",
                    "Vasilisa Akselevich; Sharon Gilaie-Dotan") == (2, 100.0)


def test_author_order_does_not_matter():
    """Sets, not sequences: a reordered author list is the same team."""
    assert ao.count("Emmanuel Ahr; Olivier Houdé; Grégoire Borst",
                    "Grégoire Borst; Emmanuel Ahr; Olivier Houdé") == (3, 100.0)


def test_an_unknown_author_list_is_not_scored():
    """Returning 0 here would publish 'no shared authors' for a row where nobody
    knows — inventing an independence finding out of missing metadata."""
    assert ao.count(None, "Alice Roberts") == (None, None)
    assert ao.count("Alice Roberts", "") == (None, None)


def test_the_percentage_is_rounded_to_one_place():
    _, pct = ao.count("A One; B Two; C Three", "A One; X Nine; Y Eight")
    assert pct == 33.3


# ── augment() over a frame ────────────────────────────────────────────────────

def _frame():
    return pd.DataFrame({
        "author_o": ["Eyal Peer; Laura Brandimarte", "Jeff Ackerman", None],
        "author_r": ["Eyal Peer; Sonam Samat", "Alice Roberts", "Someone Else"],
    })


def test_augment_adds_both_columns():
    frame = _frame()
    ao.augment(frame, verbose=False)
    assert frame["author_overlap"].tolist()[:2] == [1, 0]
    assert frame["author_overlap_pct"].tolist()[:2] == [50.0, 0.0]
    assert pd.isna(frame["author_overlap"].iloc[2])


def test_the_count_stays_an_integer():
    """A float column publishes "1.0 shared authors" in the CSV. The nullable
    integer dtype keeps the count whole and leaves the unknowns blank."""
    frame = _frame()
    ao.augment(frame, verbose=False)
    assert str(frame["author_overlap"].dtype) == "Int64"
    assert str(frame["author_overlap"].iloc[0]) == "1"


def test_augment_reports_only_scorable_rows():
    assert ao.augment(_frame(), verbose=False) == 2


def test_augment_on_a_frame_with_no_author_columns():
    """build() calls this unconditionally; a frame without the columns must get
    empty ones rather than an exception."""
    frame = pd.DataFrame({"doi_o": ["10.1/a"]})
    assert ao.augment(frame, verbose=False) == 0
    assert "author_overlap" in frame.columns


# ── it reaches the output ─────────────────────────────────────────────────────

def test_the_columns_survive_the_projection():
    """build() reindexes to an explicit column list, so a derived column missing
    from it is dropped before to_output_shape can see it — which is exactly what
    happened to the enrichment columns once before."""
    assert "author_overlap" in transform_sources.DERIVED_COLUMNS
    assert "author_overlap_pct" in transform_sources.DERIVED_COLUMNS
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    assert "+ DERIVED_COLUMNS" in source


def test_the_columns_trail_the_output_contract():
    """The 35 are an agreement with a pipeline we do not control."""
    assert "author_overlap" in transform_sources.OUTPUT_EXTRAS
    extras_start = len(transform_sources.FLORA_OUTPUT_COLUMNS)
    assert extras_start == 35


def test_overlap_is_computed_after_titles_are_recovered():
    """8b parses authors out of reference strings for rows no lookup could reach.
    Computing overlap before that would score those rows as unknown."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    body = source[source.index("def build("):]
    assert body.index("apa_references.augment(") < body.index("author_overlap.augment(")
