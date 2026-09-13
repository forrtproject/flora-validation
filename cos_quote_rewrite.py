"""cos_quote_rewrite.py — make shorthand COS outcome quotes read on their own.

A port of R/cos_quote_rewrite.R, called at the same point in the pipeline: as the
COS rows are ingested, before anything else looks at outcome_quote.

WHAT IT IS FOR
--------------
`outcome_quote` is supposed to be the sentence in the replication report that the
coder's verdict rests on. For several large multi-study projects the coders wrote
their own shorthand instead — "no marked in Replicate column", "sub_rep .5 in
dataset" — which is meaningless to anyone reading a single exported row, and which
is the majority of what those projects contribute.

Each rewrite states the project's coding logic instead. Deliberately absent:
  - the location (which column, table or dataset) — that is outcome_quote_source
  - the project's name — ref_r already identifies it

ANYTHING UNRECOGNISED IS RETURNED UNCHANGED. A real quote must never be rewritten,
and a new shorthand should show up in the data as itself rather than be absorbed
into the nearest rule.
"""
import re

from console_encoding import use_utf8_output

use_utf8_output()

# Applied identically across every rating value, so it is written once.
SOTO_RULE = (
    "The replication report rated each sub-outcome on a 0-1 scale; "
    "the row is recorded as failed only when all sub-outcomes equal 0, "
    "successful only when all equal 1, and mixed for any value strictly between."
)

_RPP = re.compile(r"^(yes|no) marked in Replicate( \(R\))? column$", re.I)
_XPHI = re.compile(
    r"^(YES|NO) in ReplicationSUCCESS column$"
    r"|^ReplicationSUCCESS (?:marked as |= )(YES|NO)$", re.I)
_CAMERER_EE = re.compile(r"^(Yes|No) stated in Replicated column$", re.I)
_CAMERER_SS = re.compile(
    r"^(Yes|No) stated in the Rep\. (s1\+s2|s1|s2) column$", re.I)
_SOTO_MIXED = re.compile(r"^mixed as not both columns 1 or 0$", re.I)
_BADGE = re.compile(
    r"^Result Type\s+(Successful Replication|Failure to Replicate)$", re.I)

# The Soto ratings arrive in four shapes, including two where the value is repeated
# either side of a stray "[In figure 2, ...]" note. Kept as one alternation, as the
# R does, so the four stay visibly a set.
_SOTO = re.compile(
    r"^subjective replication success(?: (?:rating|score))?\s*[:=]?\s*(\.?\d+(?:\.\d+)?)$"
    r"|^sub_rep (\.?\d+(?:\.\d+)?) in dataset$"
    r"|^(\.?\d+(?:\.\d+)?) \[In figure 2,.*?\]\.?\s*"
    r"subjective replication success rating \.?\d+(?:\.\d+)?\s*$"
    r"|^subjective replication success rating (\.?\d+(?:\.\d+)?)\.?\s*"
    r"\.?\d+(?:\.\d+)? \[In figure 2,.*?\]\.?\s*$",
    re.I)

# Many Labs 5 always reports both protocols, so these are whole-string matches
# rather than patterns.
ML5_BOTH_INCLUDE = "Table 3 ML5 Revised CI includes 0, ML5:RP:P Protocol CI includes 0"
ML5_REVISED_ONLY = ("Table 3 ML5 Revised CI does not include 0, "
                    "ML5:RP:P Protocol CI includes 0")

_WS = re.compile(r"\s+")


def _squish(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _number(value: float) -> str:
    """R's format(drop0trailing = TRUE): 0 -> '0', 0.50 -> '0.5'."""
    text = f"{value:g}"
    return text


def _soto(value: float) -> str:
    if value == 0:
        label = " (failed)"
    elif value == 1:
        label = " (successful)"
    else:
        label = " (mixed)"
    return (f"Subjective replication success rating = {_number(value)}{label}. "
            f"{SOTO_RULE}")


def rewrite_one(quote):
    """One quote in, one quote out. Unrecognised text is returned unchanged."""
    if quote is None:
        return None
    text = str(quote)
    if not text.strip():
        return quote
    squished = _squish(text)

    # 1 ── RP:P (Reproducibility Project: Psychology), the "Replicate" column
    m = _RPP.match(squished)
    if m:
        yes = m.group(1).lower() == "yes"
        return ("Coded as {}: Replicate = {} (the project's binary success indicator "
                "combining significance and direction).").format(
                    "successfully replicated" if yes else "not replicated",
                    "1" if yes else "0")

    # 2 ── X-Phi Replication Project, the ReplicationSUCCESS column
    m = _XPHI.match(squished)
    if m:
        yes = "yes" in squished.lower()
        return ("Coded as {}: ReplicationSUCCESS = {} (the project's pre-registered "
                "binary indicator of whether the replication met its success "
                "criterion).").format(
                    "successfully replicated" if yes else "not replicated",
                    "1" if yes else "0")

    # 3 ── Camerer et al. 2016 (Experimental Economics), the "Replicated" column
    m = _CAMERER_EE.match(squished)
    if m:
        yes = m.group(1).lower() == "yes"
        return ("Replicated = {}: the project's binary indicator (p < 0.05 in the "
                "same direction as the original) is {}met.").format(
                    "Yes" if yes else "No", "" if yes else "not ")

    # 4 ── Camerer et al. 2018 (Social Sciences), the Rep. s1 / s1+s2 columns
    m = _CAMERER_SS.match(squished)
    if m:
        yes = m.group(1).lower() == "yes"
        column = m.group(2)
        sample = {
            "s1": "first-stage replication sample",
            "s1+s2": "pooled first-stage and replication-sample test",
            "s2": "replication sample",
        }[column.lower()]
        return ("Rep. {} = {}: the {} {} significance in the same direction as the "
                "original.").format(column, "Yes" if yes else "No", sample,
                                    "reaches" if yes else "fails to reach")

    # 5 ── Many Labs 5: both protocols are always reported together
    if ML5_BOTH_INCLUDE in squished:
        return ("95% CI for the meta-analytic replication includes 0 under both the "
                "RP:P and Revised Protocols (effect not significant in the same "
                "direction as the original under either).")
    if ML5_REVISED_ONLY in squished:
        return ("95% CI for the meta-analytic replication includes 0 under the RP:P "
                "Protocol but not under the Revised Protocol (effect significant in "
                "the same direction as the original only under the Revised Protocol).")

    # 6 ── Soto repository: the subjective replication success rating
    m = _SOTO.match(squished)
    if m:
        raw = next(g for g in m.groups() if g is not None)
        if raw.startswith("."):
            raw = "0" + raw
        return _soto(float(raw))

    # 7 ── Soto repository: mixed because the sub-outcome ratings split
    if _SOTO_MIXED.match(squished):
        return ("Classified as mixed: sub-outcome ratings were neither all 0 nor "
                "all 1. " + SOTO_RULE)

    # 8 ── A website's own "Result Type" badge
    m = _BADGE.match(squished)
    if m:
        return f'Outcome classified as "{m.group(1)}" (the project\'s own label).'

    return quote


def rewrite(series):
    """Vectorised over a pandas Series, returning a new Series."""
    return series.map(rewrite_one)


# The openings every rewrite above produces. Used to count what was rewritten
# without re-running the matchers, exactly as the notebook's own tally does.
REWRITTEN_PREFIXES = (
    "Coded as ",
    "Replicated = ",
    "Rep. ",
    "95% CI for the meta-analytic ",
    "Subjective replication success rating ",
    "Classified as mixed:",
    "Outcome classified as ",
)


def count_rewritten(series) -> int:
    return int(series.fillna("").astype(str).str.startswith(REWRITTEN_PREFIXES).sum())
