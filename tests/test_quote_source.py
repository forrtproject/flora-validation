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

from backfill_quote_source import recomputed_source
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
# The admin form round-trips a granular value
# ---------------------------------------------------------------------------

def test_admin_backend_accepts_any_named_source():
    """It used to accept only 'abstract'/'full_text'; anything else fell through to
    auto-detection, silently discarding the admin's choice."""
    app_py = (_ROOT / "app.py").read_text(encoding="utf-8")
    assert 'req.out_quote_source in ("abstract", "full_text")' not in app_py
    assert "admin_quote_source(\n            req.out_quote_source, rec," in app_py.replace("\r\n", "\n")


def test_admin_select_keeps_an_unlisted_stored_value():
    """The regression: a granular value matched no <option>, so the select fell back
    to its blank option and saving replaced a known source with a guess."""
    app_js = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"function _adminQuoteSourceOptions\(selected[^)]*\) \{(.*?)\n\}", app_js, re.S)
    assert fn, "could not locate _adminQuoteSourceOptions"
    assert "!known.includes(selected)" in fn.group(1), \
        "an unlisted stored value is not preserved as an option"


def test_admin_select_offers_real_sources_and_no_auto_detect():
    """Detection is the server's, shown as a suggestion, not a choice to make."""
    app_js = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"function _adminQuoteSourceOptions\(selected[^)]*\) \{(.*?)\n\}", app_js, re.S)
    assert "full_text" in fn.group(1)
    assert "auto-detect" not in app_js.lower()


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
# The backfill may replace only what a computation wrote
# ---------------------------------------------------------------------------

_IN_ABSTRACT = {"outcome_quote": "it failed", "abstract_r": "We found it failed again."}
_NOT_IN_ABSTRACT = {"outcome_quote": "it failed", "abstract_r": "We found it held."}


@pytest.mark.parametrize("stored", [
    "discussion", "results", "title", "Discussion", "abstract|discussion", "abstract | discussion",
    "title | abstract", "abstract|full_text", "fulltext",
])
def test_recompute_all_never_replaces_a_named_or_compound_source(stored):
    """Unstamped, as a record resolved without touching its source now is."""
    row = {**_IN_ABSTRACT, "out_quote_source": stored, "out_quote_source_by": None}
    assert recomputed_source(row, recompute_all=True) is None


@pytest.mark.parametrize("stored", ["full_text", "Full_Text", "full_text | full_text", " FULL_TEXT "])
def test_recompute_all_refreshes_a_computed_source_in_any_spelling(stored):
    assert recomputed_source({**_IN_ABSTRACT, "out_quote_source": stored}, recompute_all=True) == "abstract"


@pytest.mark.parametrize("stored", ["abstract", "Abstract", "abstract | abstract"])
def test_recompute_all_refreshes_a_stale_abstract(stored):
    assert recomputed_source({**_NOT_IN_ABSTRACT, "out_quote_source": stored}, recompute_all=True) == "full_text"


def test_recompute_all_writes_nothing_when_the_answer_is_the_same_or_there_is_no_quote():
    assert recomputed_source({**_IN_ABSTRACT, "out_quote_source": "abstract"}, recompute_all=True) is None
    row = {"outcome_quote": None, "abstract_r": "An abstract.", "out_quote_source": "full_text"}
    assert recomputed_source(row, recompute_all=True) is None


@pytest.mark.parametrize("recompute_all", [False, True])
@pytest.mark.parametrize("stored", [None, "abstract", "full_text", "discussion"])
def test_an_admin_locked_row_is_never_touched(stored, recompute_all):
    row = {**_NOT_IN_ABSTRACT, "out_quote_source": stored, "out_quote_source_by": "sophie"}
    assert recomputed_source(row, recompute_all=recompute_all) is None


@pytest.mark.parametrize("recompute_all", [False, True])
@pytest.mark.parametrize("blank", [None, ""])
def test_a_blank_source_is_filled_either_way(blank, recompute_all):
    assert recomputed_source({**_IN_ABSTRACT, "out_quote_source": blank}, recompute_all=recompute_all) == "abstract"


@pytest.mark.parametrize("stored", ["abstract", "full_text", "discussion"])
def test_the_default_run_leaves_any_stored_source(stored):
    assert recomputed_source({**_NOT_IN_ABSTRACT, "out_quote_source": stored}) is None


def test_the_backfill_decides_through_the_tested_function():
    src = (_ROOT / "backfill_quote_source.py").read_text(encoding="utf-8")
    assert "new_src = recomputed_source(r, recompute_all)" in src
    assert src.count("quote_source_for(") == 1, "a second detection would skip the rule"


# ---------------------------------------------------------------------------
# The admin form's suggestion is what a save detects
# ---------------------------------------------------------------------------

# Wherever the normalising could go differently in the two languages, plus the blanks.
_DETECTION_CASES = {
    "contained": ("it failed", "We found it failed again."),
    "not contained": ("it failed", "We found it held."),
    "the longer side is the quote": ("We found it failed again.", "it failed"),
    "inside a word": ("art", "The start of it."),
    "across words": ("dit", "ad it"),
    "case": ("WE REPLICATED", "we Replicated it"),
    "punctuation": ("It failed; (d = 0.5)!", "it failed d=0.5"),
    "whitespace": ("two \n\t words  apart", "Two words\r\napart."),
    "digits are kept": ("d = 0.50", "d = 0.5"),
    "no-break and thin spaces": ("two words apart", "two words apart"),
    "en and em dashes": ("pre–registered — twice", "preregistered twice"),
    "minus sign": ("r = −0.5", "r = -0.5"),
    "curly quotes": ("“it’s held”", "\"it's held\""),
    "ellipsis character": ("it failed…", "it failed..."),
    "zero-width space": ("it​ failed", "it failed"),
    "an accent on both": ("naïve", "the naïve view"),
    "an accent on one drops out": ("naïve", "the naive view"),
    "an accent on the other": ("naive", "the naïve view"),
    "decomposed accent": ("café study", "cafe study"),
    "greek is stripped": ("Σα effect", "the effect"),
    "fi ligature is stripped": ("ﬁnding", "the nding"),
    "fullwidth letters are stripped": ("ＡＢＣ", "abc"),
    "kelvin sign lowercases to k": ("5 K", "5k"),
    "dotted capital I": ("İstanbul", "in istanbul"),
    "sharp s": ("StraẞE", "strae"),
    "astral characters": ("it \U0001f600 failed", "it failed"),
    "no quote": (None, "An abstract."),
    "empty quote": ("", "An abstract."),
    "blank quote": ("  \n ", "An abstract."),
    "punctuation for a quote": ("...—!?", "An abstract."),
    "non-ascii for a quote": ("αβγ", "An abstract."),
    "no abstract": ("a quote", None),
    "empty abstract": ("a quote", ""),
    "punctuation for an abstract": ("a quote", "...---"),
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


def test_the_suggestion_does_not_use_the_validators_fuzzy_check():
    """quoteAppearsInAbstract forgives a misquote; quote_source_for does not."""
    app_js = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"function suggestQuoteSource\(.*?\n\}", app_js, re.S)
    assert fn and "quoteAppearsInAbstract(" not in fn.group(0)


def test_an_untouched_suggestion_is_sent_as_null():
    """With nothing stored, the server stamps whatever it is sent as the admin's choice,
    and a stamped source is locked against the backfill: a suggestion nobody touched must
    not get there."""
    app_js = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    assert 'out_quote_source:        _quoteSourceToSend($("#ar-quote-source"))' in app_js


# ---------------------------------------------------------------------------
# export_validated must not run on import
# ---------------------------------------------------------------------------

def test_export_validated_is_inert_on_import():
    """Importing it used to connect to the database and rewrite both CSVs — easy to
    trigger by accident from a test or a REPL."""
    src = (_ROOT / "export_validated.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in src
    guard_at = src.index('if __name__ == "__main__":')
    assert src.index('print("Connecting to database') > guard_at, \
        "the database connection is outside the guard"


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
