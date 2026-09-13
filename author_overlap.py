"""author_overlap.py — how much of a replication's author list is the original's.

A port of compute_author_overlap() / fill_author_overlap_from_columns() in
R/augmentation.R.

WHAT IT MEASURES
----------------
How independent the replication team was. A replication written by the original
authors is a different kind of evidence from one written by strangers, and a reader
of the published dataset currently cannot tell the two apart.

    author_overlap      how many family names appear on both sides
    author_overlap_pct  that as a percentage OF THE REPLICATION TEAM

The denominator is the replication's author count, not the union or the original's,
and deliberately: "3 of the 4 replication authors also wrote the original" is the
statement about independence. Against the original's count the same overlap would
say something about the original team instead, which is not the question.

NO NETWORK
----------
R fetches author lists from CrossRef into their own cache. Here author_o and
author_r are already on the frame — OpenAlex supplied them during enrichment — so
this is arithmetic over data we hold. That also means it costs nothing to compute on
every build.

MATCHING IS BY FAMILY NAME, LOWERCASED
--------------------------------------
Our author columns hold display names ("Eyal Peer; Laura Brandimarte"), so the
family name is taken as the last whitespace-separated token. That mis-splits a
particle surname — "Janet G. van Hell" yields "hell" — but identically on both
sides, so a genuine self-replication still matches. It can overcount two unrelated
people who share a surname; the figure is a signal for a reader, not an identity
claim, and the alternative (initials too) would miss "J. Smith" against
"John Smith".
"""
import re

import pandas as pd

from console_encoding import use_utf8_output

use_utf8_output()

# Sheets and OpenAlex both use "; " between names; a few rows use "," or " and ".
_SPLIT = re.compile(r"\s*(?:;|\band\b|&)\s*")
_PUNCT = re.compile(r"[^\w\s'\-]", re.UNICODE)


def families(value) -> set:
    """The set of lowercased family names in an author string."""
    if value is None:
        return set()
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return set()

    out = set()
    for name in _SPLIT.split(text):
        name = _PUNCT.sub(" ", name).strip()
        if not name:
            continue
        tokens = [t for t in name.split() if t]
        if not tokens:
            continue
        # An "Ackerman, Jeff" entry puts the family name first; a display name puts
        # it last. The comma is the only signal, and it is gone by now, so the last
        # token is used throughout — consistently, which is what matters.
        family = tokens[-1].lower()
        # A trailing initial ("Smith J") is not a surname.
        if len(family) < 2 and len(tokens) > 1:
            family = tokens[-2].lower()
        if len(family) >= 2:
            out.add(family)
    return out


def count(author_o, author_r) -> "tuple":
    """(overlap, percentage of the replication team) for one pair.

    Returns (None, None) when either side has no usable author list: zero would
    read as "no shared authors", which is a finding, and we do not have one.
    """
    left, right = families(author_o), families(author_r)
    if not left or not right:
        return None, None
    shared = len(left & right)
    return shared, round(100.0 * shared / len(right), 1)


def augment(frame, verbose: bool = True) -> int:
    """Add author_overlap and author_overlap_pct. Returns how many rows got one."""
    if not {"author_o", "author_r"} <= set(frame.columns):
        frame["author_overlap"] = None
        frame["author_overlap_pct"] = None
        return 0

    pairs = [count(o, r) for o, r in zip(frame["author_o"], frame["author_r"])]
    # Int64, not the default float: a plain list with a None in it makes pandas
    # choose float64, and the published CSV then reads "1.0 shared authors". The
    # nullable integer keeps the count an integer and the unknowns blank.
    frame["author_overlap"] = pd.array([p[0] for p in pairs], dtype="Int64")
    frame["author_overlap_pct"] = pd.array([p[1] for p in pairs], dtype="Float64")

    scored = sum(1 for p in pairs if p[0] is not None)
    if verbose and scored:
        shared = sum(1 for p in pairs if p[0])
        print(f"      author overlap computed for {scored} row(s); "
              f"{shared} share at least one author with the original")
    return scored
