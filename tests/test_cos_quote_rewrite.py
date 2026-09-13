"""Tests for cos_quote_rewrite — a port of R/cos_quote_rewrite.R.

`outcome_quote` is meant to be the sentence the coder's verdict rests on. For the
big multi-study projects the coders wrote shorthand instead ("no marked in Replicate
column"), which means nothing to someone reading one exported row.

The property that matters most is the NEGATIVE one: anything unrecognised comes back
untouched. A rewriter that paraphrases a real quote is worse than one that leaves
shorthand alone, because the quote is supposed to be verbatim evidence.
"""
import pandas as pd
import pytest

import cos_quote_rewrite as cq


# ── RP:P ──────────────────────────────────────────────────────────────────────

def test_rpp_yes_becomes_a_sentence():
    out = cq.rewrite_one("yes marked in Replicate column")
    assert out.startswith("Coded as successfully replicated: Replicate = 1")


def test_rpp_no_becomes_a_sentence():
    out = cq.rewrite_one("no marked in Replicate column")
    assert out.startswith("Coded as not replicated: Replicate = 0")


def test_rpp_accepts_the_parenthesised_variant():
    """The sheet carries both 'Replicate column' and 'Replicate (R) column'."""
    assert cq.rewrite_one("no marked in Replicate (R) column") is not None
    assert "Replicate = 0" in cq.rewrite_one("no marked in Replicate (R) column")


def test_matching_ignores_case():
    assert "Replicate = 1" in cq.rewrite_one("YES MARKED IN REPLICATE COLUMN")


# ── the other projects ────────────────────────────────────────────────────────

def test_xphi_replication_success():
    out = cq.rewrite_one("YES in ReplicationSUCCESS column")
    assert "ReplicationSUCCESS = 1" in out
    assert "pre-registered" in out


def test_xphi_alternate_phrasing():
    assert "ReplicationSUCCESS = 0" in cq.rewrite_one("ReplicationSUCCESS = NO")


def test_camerer_experimental_economics():
    out = cq.rewrite_one("Yes stated in Replicated column")
    assert out.startswith("Replicated = Yes")
    assert "p < 0.05" in out


def test_camerer_social_sciences_names_the_sample():
    """s1 and s1+s2 are different tests, and the row must say which it was."""
    assert "first-stage replication sample" in \
        cq.rewrite_one("Yes stated in the Rep. s1 column")
    assert "pooled" in cq.rewrite_one("No stated in the Rep. s1+s2 column")


def test_camerer_social_sciences_negation():
    assert "fails to reach" in cq.rewrite_one("No stated in the Rep. s1 column")


def test_many_labs_5_both_protocols():
    out = cq.rewrite_one(cq.ML5_BOTH_INCLUDE)
    assert "under either" in out


def test_many_labs_5_revised_only():
    out = cq.rewrite_one(cq.ML5_REVISED_ONLY)
    assert "only under the Revised Protocol" in out


def test_website_result_type_badge():
    out = cq.rewrite_one("Result Type Successful Replication")
    assert out == ('Outcome classified as "Successful Replication" '
                   "(the project's own label).")


# ── Soto ratings ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("subjective replication success rating 0", "0 (failed)"),
    ("subjective replication success rating 1", "1 (successful)"),
    ("subjective replication success: .5", "0.5 (mixed)"),
    ("sub_rep .75 in dataset", "0.75 (mixed)"),
])
def test_soto_rating_values(text, expected):
    assert f"rating = {expected}" in cq.rewrite_one(text)


def test_soto_carries_the_scale_explanation():
    """The number alone says nothing; the rule is what makes the row readable."""
    assert cq.SOTO_RULE in cq.rewrite_one("sub_rep 0 in dataset")


def test_a_leading_decimal_point_is_not_read_as_a_whole_number():
    """'.5' must become 0.5, not 5 — which would fall outside the 0-1 scale and
    read as 'mixed' for the wrong reason."""
    assert "rating = 0.5 " in cq.rewrite_one("sub_rep .5 in dataset")


def test_soto_mixed_without_a_number():
    out = cq.rewrite_one("mixed as not both columns 1 or 0")
    assert out.startswith("Classified as mixed:")
    assert cq.SOTO_RULE in out


def test_trailing_zeroes_are_dropped():
    assert "rating = 0.5 " in cq.rewrite_one("subjective replication success: 0.50")


# ── the negative property ─────────────────────────────────────────────────────

def test_a_real_quote_is_returned_untouched():
    """The whole point of the column is that it is verbatim."""
    quote = ("The replication was successful, with an effect size of d = 0.42 "
             "in the same direction as the original.")
    assert cq.rewrite_one(quote) == quote


def test_unrecognised_shorthand_is_left_as_itself():
    """A new shorthand must appear in the data as itself so someone notices it,
    not be absorbed into whichever rule is closest."""
    assert cq.rewrite_one("Table 3 ML5:RP:P Protocol CI includes 0") == \
        "Table 3 ML5:RP:P Protocol CI includes 0"


def test_none_and_blank_survive():
    assert cq.rewrite_one(None) is None
    assert cq.rewrite_one("") == ""
    assert cq.rewrite_one("   ") == "   "


def test_partial_matches_do_not_trigger():
    """'marked in Replicate column' appears inside a longer sentence: that
    sentence is a real quote and must not be replaced by the shorthand rule."""
    quote = "The authors say yes marked in Replicate column was an error."
    assert cq.rewrite_one(quote) == quote


# ── the vectorised wrapper ────────────────────────────────────────────────────

def test_rewrite_maps_over_a_series():
    series = pd.Series(["no marked in Replicate column", "a real quote", None])
    out = cq.rewrite(series)
    assert out.iloc[0].startswith("Coded as not replicated")
    assert out.iloc[1] == "a real quote"
    # pandas stores the None as NaN on construction; what matters is that a missing
    # quote stays missing rather than becoming the string "nan".
    assert pd.isna(out.iloc[2])


def test_count_rewritten_counts_only_rewrites():
    series = pd.Series([
        cq.rewrite_one("no marked in Replicate column"),
        "The effect did not replicate.",
        None,
    ])
    assert cq.count_rewritten(series) == 1
