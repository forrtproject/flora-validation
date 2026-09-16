"""Tests for the export's title filter — Step 10 of the R notebook.

The notebook drops rows with no title on one side, unconditionally. This project
kept them for a long time and was right to: our title coverage was worse than R's,
so 331 rows were blank for reasons of OUR pipeline rather than the data, and
dropping them would have published a smaller dataset than R does while looking
like parity.

That reason is now gone — OpenAlex work-id lookups and reference-text parsing
closed it to 24 genuinely untitled rows — so the filter matches the notebook again.

The asymmetry these tests pin down: the EXPORT drops them, the FLoRA TAB does not.
The tab is where someone fixes such a row, and a row hidden there is a row nobody
fixes.
"""
import inspect
from pathlib import Path

import pandas as pd
import pytest

import flora_service
import transform_sources

ROOT = Path(__file__).resolve().parents[1]


def _frame():
    return pd.DataFrame({
        "flora_id": ["F-1", "F-2", "F-3", "F-4"],
        "type": ["replication"] * 4,
        "source": ["validated"] * 4,
        "outcome": ["successful"] * 4,
        "title_o": ["Original", "Original", None, None],
        "title_r": ["Replication", None, "Replication", None],
    })


# ── the default ───────────────────────────────────────────────────────────────

def test_the_export_drops_untitled_rows_by_default():
    """Matching the notebook, which does this with no flag at all."""
    assert inspect.signature(transform_sources.run) \
        .parameters["require_titles"].default is True


def test_the_flag_now_turns_the_filter_OFF():
    """--require-titles turned it on while the default was off. With the default
    flipped, a flag of that name would read as a no-op, so it is --keep-untitled."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    assert '"--keep-untitled"' in source
    assert '"--require-titles"' not in source
    assert "require_titles=not args.keep_untitled" in source


def test_the_dropped_rows_are_logged_before_they_are_dropped():
    """A row that vanishes from the export with no record of why is the failure
    mode here; flora_export_log.csv is what makes it reviewable."""
    source = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    body = source[source.index("def run("):]
    assert body.index("flora_export_log.csv") < body.index("if require_titles:")


# ── which rows go ─────────────────────────────────────────────────────────────

def test_a_missing_title_on_either_side_is_enough():
    report = transform_sources.missing_title_report(_frame())
    assert len(report) == 3            # F-2, F-3, F-4 — only F-1 has both


def test_the_reason_says_which_side_was_missing():
    reasons = set(transform_sources.missing_title_report(_frame())["reason"])
    assert reasons == {"missing title_r", "missing title_o",
                       "missing both title_o and title_r"}


def test_a_fully_titled_frame_reports_nothing():
    frame = _frame().assign(title_o="Original", title_r="Replication")
    assert transform_sources.missing_title_report(frame).empty


# ── one predicate, two callers ────────────────────────────────────────────────

def test_drop_untitled_keeps_only_rows_titled_on_both_sides():
    kept = transform_sources.drop_untitled(_frame())
    assert kept["flora_id"].tolist() == ["F-1"]


def test_drop_untitled_is_a_no_op_without_the_title_columns():
    frame = _frame().drop(columns=["title_o", "title_r"])
    assert len(transform_sources.drop_untitled(frame)) == 4


def test_the_validator_checks_the_same_rows_that_ship():
    """R validates output/flora.csv, which has already been through Step 10. A
    report listing rows that were never published puts items in the issue that
    nobody can resolve, which is how a report gets ignored."""
    source = (ROOT / "validate_flora.py").read_text(encoding="utf-8")
    body = source[source.index("def load_dataset("):]
    assert "drop_untitled(" in body


def test_the_export_and_the_validator_share_the_predicate():
    """Two copies would drift, and the published set and the validated set would
    quietly stop being the same thing."""
    export = (ROOT / "transform_sources.py").read_text(encoding="utf-8")
    run_body = export[export.index("def run("):]
    assert "drop_untitled(out)" in run_body


# ── the tab keeps them, and says so ───────────────────────────────────────────

def test_the_tab_still_counts_every_row():
    """Filtering the grid too would hide exactly the rows that need attention."""
    assert flora_service.counts(_frame())["all_records"] == 4


def test_the_tab_reports_how_many_will_not_reach_the_export():
    assert flora_service.counts(_frame())["untitled"] == 3


def test_the_count_is_zero_when_every_row_has_titles():
    frame = _frame().assign(title_o="Original", title_r="Replication")
    assert flora_service.counts(frame)["untitled"] == 0


def test_the_count_survives_a_frame_without_title_columns():
    """counts() runs on bare frames in tests and on an empty frame in production."""
    frame = _frame().drop(columns=["title_o", "title_r"])
    assert flora_service.counts(frame)["untitled"] == 0


def test_the_grid_surfaces_the_count_to_a_reviewer():
    """A number nobody sees is not a warning. Without this the export silently
    holds fewer rows than the tab shows."""
    app_js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    assert "counts.untitled" in app_js
    assert "left out of the final export" in app_js
    assert "pipeline report records excluded rows" in app_js
