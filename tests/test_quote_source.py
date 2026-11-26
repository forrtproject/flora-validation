"""Quote-source normalisation and round-tripping.

The extractor names the section a quote came from, and pipe-joins several when it
spans them. Two quirks, both agreed as ours to absorb:

  - 'abstract | abstract' on 2 rows — a self-join carrying no more information
    than 'abstract'; the upstream join does not deduplicate.
  - two separator conventions in one file: out_quote_source uses ' | ' (34 rows),
    screen_categories uses '|' (1238).

Separately, the admin form only offered 'abstract' and 'full_text', so a stored
granular value matched no option and was replaced by an auto-detected guess on
save.

The admin form now shows what a save would detect as a suggestion, so the copy of
that check in docs/app.js is held to quote_source_for here, under node. How the form
behaves is tests/test_quote_source_ui.cjs.

The form also sends a stored source back on every save, so only a changed value is the
admin's choice (admin_quote_source). The backfill cannot read a missing name as "nobody
chose this", so it never replaces a named source (recomputed_source).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from consensus_engine import admin_quote_source, quote_source_for
from extractor_vocab import QUOTE_SOURCE_SEPARATOR, normalize_quote_source
from csv_to_db import _build_unvalidated_row

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# normalize_quote_source
# ---------------------------------------------------------------------------

def test_a_single_source_is_untouched():
    assert normalize_quote_source("abstract") == "abstract"


def test_blank_stays_blank():
    for value in ("", None, "   "):
        assert normalize_quote_source(value) == ""


def test_the_self_join_collapses():
    """'abstract | abstract' says nothing 'abstract' does not."""
    assert normalize_quote_source("abstract | abstract") == "abstract"


def test_spacing_is_removed_to_match_the_other_pipe_column():
    """screen_categories uses '|' with no spaces and is 36x more common. One
    convention means consumers need one parser."""
    assert normalize_quote_source("abstract | discussion") == "abstract|discussion"
    assert normalize_quote_source("abstract|discussion") == "abstract|discussion"
    assert normalize_quote_source("  abstract  |  discussion  ") == "abstract|discussion"


def test_order_is_preserved_not_sorted():
    """'title | abstract' may encode where the quote starts; reordering it would
    invent meaning."""
    assert normalize_quote_source("title | abstract") == "title|abstract"
    assert normalize_quote_source("abstract | title") == "abstract|title"


def test_case_is_folded_so_duplicates_actually_collapse():
    assert normalize_quote_source("Abstract | abstract") == "abstract"


def test_partial_duplicates_collapse_but_distinct_parts_survive():
    assert normalize_quote_source("abstract | discussion | abstract") == "abstract|discussion"


def test_empty_segments_are_dropped():
    assert normalize_quote_source("abstract ||  | discussion") == "abstract|discussion"


def test_separator_constant_is_the_bare_pipe():
    assert QUOTE_SOURCE_SEPARATOR == "|"


# ---------------------------------------------------------------------------
# Applied at import, to all three source columns
# ---------------------------------------------------------------------------

def test_import_normalises_the_replication_quote_source():
    row = pd.Series({"type": "replication", "out_quote_source": "abstract | abstract"})
    assert _build_unvalidated_row("r", "p", row)["out_quote_source"] == "abstract"


def test_import_normalises_both_axis_quote_sources():
    """No compound values on the axis columns today, but they are the same kind of
    field written by the same pipeline — normalising only one would leave a trap."""
    row = pd.Series({
        "type": "reproduction",
        "out_quote_computational_source": "results | conclusions",
        "out_quote_robust_source": "Discussion | discussion",
    })
    built = _build_unvalidated_row("r", "p", row)
    assert built["out_quote_computational_source"] == "results|conclusions"
    assert built["out_quote_robust_source"] == "discussion"


def test_every_real_csv_value_survives_normalisation():
    """Each observed value must normalise to something non-empty, and normalising
    twice must be a no-op (or stored data drifts on re-import)."""
    observed = ["abstract", "abstract | abstract", "abstract | discussion",
                "abstract | introduction", "discussion", "introduction",
                "results | conclusions", "title", "title | abstract"]
    for value in observed:
        once = normalize_quote_source(value)
        assert once, f"{value!r} normalised to nothing"
        assert normalize_quote_source(once) == once, f"{value!r} is not idempotent"


# ---------------------------------------------------------------------------
# Only a changed source is the admin's choice
# ---------------------------------------------------------------------------

def _resolved(submitted, stored=None, final=None, stored_by=None):
    """A resolve whose quote is in the abstract, so detecting would say 'abstract'."""
    rec = {"out_quote_source": stored, "final_out_quote_source": final,
           "out_quote_source_by": stored_by}
    return admin_quote_source(submitted, rec, "it failed", "We found it failed again.", "sophie")


def test_a_changed_source_is_used_and_stamped():
    assert _resolved("full_text", stored="abstract") == ("full_text", "sophie")
    assert _resolved("Results", stored="abstract") == ("results", "sophie")


def test_a_change_takes_over_an_earlier_admins_stamp():
    assert _resolved("results", stored="abstract", stored_by="luke") == ("results", "sophie")


def test_a_source_with_none_stored_is_a_choice():
    """Only a picked suggestion gets here: an untouched one is sent as null."""
    assert _resolved("abstract") == ("abstract", "sophie")


def test_the_stored_source_sent_back_stays_unstamped():
    assert _resolved("abstract", stored="abstract") == ("abstract", None)


def test_the_stored_source_sent_back_keeps_an_earlier_admins_stamp():
    assert _resolved("abstract", stored="abstract", stored_by="luke") == ("abstract", "luke")


@pytest.mark.parametrize("submitted, stored", [
    ("Abstract", "abstract"),
    ("abstract | abstract", "abstract"),
    ("abstract | discussion", "abstract|discussion"),
    ("abstract|discussion", "Abstract | Discussion"),
])
def test_a_different_spelling_of_the_stored_source_is_no_change(submitted, stored):
    assert _resolved(submitted, stored=stored, stored_by="luke") == \
        (normalize_quote_source(submitted), "luke")
    assert _resolved(submitted, stored=stored) == (normalize_quote_source(submitted), None)


def test_a_granular_source_is_kept_rather_than_detected():
    """The earlier bug: sending null for an untouched value made the server detect
    'abstract' over 'discussion'. The quote here is in the abstract."""
    assert _resolved("discussion", stored="discussion") == ("discussion", None)
    assert _resolved("abstract|discussion", stored="abstract|discussion") == ("abstract|discussion", None)


def test_the_final_source_is_the_one_compared():
    assert _resolved("results", stored="abstract", final="results", stored_by="luke") == ("results", "luke")
    assert _resolved("abstract", stored="abstract", final="results") == ("abstract", "sophie")


@pytest.mark.parametrize("nothing", [None, "", "   "])
def test_nothing_submitted_detects_as_it_always_did(nothing):
    assert _resolved(nothing) == ("abstract", None)
    # Detection wins over what is stored, and whoever set that loses the stamp.
    assert _resolved(nothing, stored="discussion", final="results", stored_by="luke") == ("abstract", None)


@pytest.mark.parametrize("nothing", [None, "", "   "])
def test_nothing_submitted_and_no_quote_falls_back_to_the_stored_source(nothing):
    def with_no_quote(rec):
        return admin_quote_source(nothing, rec, None, "An abstract.", "sophie")
    stored = {"out_quote_source": "discussion", "out_quote_source_by": "luke"}
    assert with_no_quote(stored) == ("discussion", None)
    assert with_no_quote({**stored, "final_out_quote_source": "results"}) == ("results", None)
    assert with_no_quote({}) == (None, None)


# ---------------------------------------------------------------------------
# The admin form's suggestion is what a save detects (JS/Python parity under node)
# ---------------------------------------------------------------------------

_DETECTION_CASES = {
    "contained": ("it failed", "We found it failed again."),
    "not contained": ("it failed", "We found it held."),
    "the longer side is the quote": ("We found it failed again.", "it failed"),
    "inside a word": ("art", "The start of it."),
    "case and punctuation": ("It failed; (d = 0.5)!", "it failed d=0.5"),
    "whitespace and nbsp": ("two\u00a0words \n\t apart", "Two words\r\napart."),
    "dashes and curly quotes": ("\u201cpre\u2013registered\u201d", '"preregistered"'),
    "accent on one side": ("na\u00efve", "the naive view"),
    "decomposed accent": ("cafe\u0301 study", "cafe study"),
    "fi ligature stripped": ("\ufb01nding", "the nding"),
    "fullwidth letters stripped": ("\uff21\uff22\uff23", "abc"),
    "kelvin sign lowercases to k": ("5 \u212a", "5k"),
    "dotted capital I": ("\u0130stanbul", "in istanbul"),
    "sharp s": ("Stra\u1e9eE", "strae"),
    "non-latin and astral": ("it \U0001f600 \u03a3\u03b1 failed", "it failed"),
    "no quote": (None, "An abstract."),
    "blank quote": ("  \n ", "An abstract."),
    "punctuation only quote": ("...\u2014!?", "An abstract."),
    "no abstract": ("a quote", None),
    "neither": (None, None),
}


@pytest.fixture(scope="module")
def suggested():
    """What docs/app.js suggests for each case, from the function itself under node."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    app_js = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function suggestQuoteSource(")
    script = app_js[start:app_js.index("\n}\n", start) + 3] + """
const cases = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map(([q, a]) => suggestQuoteSource(q, a))));
"""
    out = subprocess.run(["node", "-e", script], input=json.dumps(list(_DETECTION_CASES.values())),
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return dict(zip(_DETECTION_CASES, json.loads(out.stdout)))


@pytest.mark.parametrize("name", _DETECTION_CASES)
def test_the_suggestion_is_what_a_save_detects(suggested, name):
    assert suggested[name] == quote_source_for(*_DETECTION_CASES[name])


def test_the_detection_cases_reach_every_answer():
    """A sample that never says one of the three would prove nothing about it."""
    answers = {quote_source_for(*case) for case in _DETECTION_CASES.values()}
    assert answers == {"abstract", "full_text", None}


def test_export_validated_helpers_remain_importable():
    """The guard must gate the procedural run, not the reusable helpers."""
    import export_validated
    assert callable(export_validated.clean_doi)
    assert callable(export_validated.openalex_reference)
    assert export_validated.clean_doi("https://doi.org/10.1/x") == "10.1/x"


def test_export_metadata_lateral_query_reads_record_metadata():
    """The selected lineage columns are not in scope unless the lateral query
    names record_metadata; PostgreSQL otherwise fails before exporting a row."""
    from export_validated import QUERY

    lateral = re.search(r"LEFT JOIN LATERAL \((.*?)\) m ON true", QUERY, re.S)
    assert lateral, "could not locate the export metadata lateral query"
    assert re.search(r"\bFROM\s+record_metadata\b", lateral.group(1)), \
        "metadata export query has no source table"
