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
Structured family names preserve surname particles. Plain bylines use the same
normalisation as the final export, so recognised organisations remain literal
names rather than accidentally becoming surnames such as "group". A family name
without a given name is usable. Any collective/literal, incomplete or malformed
entry makes BOTH measures unknown, including mixed person/collective bylines.
We do not infer the membership or size of a collective author. Surname matching
can conflate unrelated people and is only a proxy, not verified person identity.
"""
import json
import re

import pandas as pd

from console_encoding import use_utf8_output

use_utf8_output()


def _family_names(value) -> list:
    """Family names from structured authors or the older display-name format."""
    if value is None:
        return []
    if isinstance(value, list):
        authors = value
    else:
        text = str(value).strip()
        if not text or text.lower() in ("na", "n/a", "nan", "none", "null", "<na>"):
            return []
        try:
            if text.startswith(("[", "{")):
                authors = json.loads(text)
            else:
                # Reuse export normalisation, including its organisation rules;
                # retain the legacy "and" / "&" separators for plain individuals.
                from bibliographic_helpers import normalise_author_json
                authors = json.loads(normalise_author_json(text, preserve_unresolved=True))
        except (ValueError, TypeError):
            return []
    if not isinstance(authors, list) or not authors:
        return []
    names = []
    for author in authors:
        family = author.get("family") if isinstance(author, dict) else None
        if not isinstance(family, str) or not family.strip():
            # Never silently discard an unresolved entry and score a partial list.
            return []
        names.append(re.sub(r"\s+", " ", family.strip().lower()))
    return names


def families(value) -> set:
    """Distinct lowercased surnames, accepting Crossref JSON and display names."""
    return set(_family_names(value))


def count(author_o, author_r) -> "tuple":
    """(overlap, percentage of the replication team) for one pair.

    Returns (None, None) when either list contains any unassessable entry: zero would
    read as "no shared authors", which is a finding, and we do not have one.
    """
    left, right = families(author_o), _family_names(author_r)
    if not left or not right:
        return None, None
    shared = len(left & set(right))
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
