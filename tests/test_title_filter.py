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
