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
"""
import re
from pathlib import Path

import pandas as pd
import pytest

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
    assert "_explicit_src = normalize_quote_source(req.out_quote_source)" in app_py


def test_admin_select_keeps_an_unlisted_stored_value():
    """The regression: a granular value matched no <option>, so the select showed
    'Auto-detect' and saving replaced a known source with a guess."""
    app_js = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"function _adminQuoteSourceOptions\(selected\) \{(.*?)\n\}", app_js, re.S)
    assert fn, "could not locate _adminQuoteSourceOptions"
    assert "!known.includes(selected)" in fn.group(1), \
        "an unlisted stored value is not preserved as an option"


def test_admin_select_still_offers_auto_detect_and_full_text():
    app_js = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"function _adminQuoteSourceOptions\(selected\) \{(.*?)\n\}", app_js, re.S)
    assert "Auto-detect" in fn.group(1)
    assert "full_text" in fn.group(1)


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
