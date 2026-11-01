"""Records added by hand in the FLoRA tab, without a database.

The rule they are built on: a row added by hand never takes over a record another
source holds. load() puts these rows last, so the dedup keeps the other row; the
pair merge makes the group's row from the other rows alone and lets the row added by
hand fill only blanks. The entry-time prediction (manual_records.placements) runs the
build's own duplicate steps, and is checked against the build here.
"""
import ast
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

import manual_records
import preprint_dedup
import security_events
import transform_sources
import validate_flora
from extractor_vocab import REPLICATION_OUTCOMES
from tests.test_duplicate_steps import BLANK, Tables

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL, REPORT = "10.9999/original.1", "10.9999/report.1"


@pytest.fixture(autouse=True)
def no_confirmed_rulings(monkeypatch):
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [])


def source(display_id, **values):
    """One source row as load() returns it."""
    return {**BLANK, "record_id": f"id-{display_id}", "display_id": display_id,
            "source": "replications", "type": "replication",
            "doi_o": ORIGINAL, "doi_r": REPORT, **values}


def by_hand(display_id, **values):
    return source(display_id, source=manual_records.SOURCE_KEY, **values)


def build(*rows, exclusions=()):
    return transform_sources.build(Tables(exclusions), verbose=False,
                                   rows=pd.DataFrame(list(rows)), metadata={})


def value(cell):
    return None if cell is None or (isinstance(cell, float) and pd.isna(cell)) else cell


# ── one constant ──────────────────────────────────────────────────────────────

def test_the_source_key_is_one_constant_known_to_the_nightly_check():
    assert manual_records.SOURCE_KEY == preprint_dedup.MANUAL_SOURCE == "manual"
    assert "manual" in validate_flora.VALID_SOURCES
    assert not validate_flora._check_sources(pd.DataFrame({"source": ["manual"]}))
    assert {security_events.FLORA_MANUAL_ADDED, security_events.FLORA_MANUAL_EDITED,
            security_events.FLORA_MANUAL_WITHDRAWN,
            security_events.FLORA_MANUAL_RESTORED} <= security_events.ACTIONS


def test_no_sheet_takes_a_key_or_prefix_of_the_sources_that_are_not_sheets():
    registry = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
    keys = {s["key"] for s in registry["sources"]}
    prefixes = {s["display_prefix"] for s in registry["sources"]}
    assert not keys & {"validated", "adjudicated", manual_records.SOURCE_KEY}
    assert not prefixes & {"VAL", "ADJ", manual_records.DISPLAY_PREFIX}


# ── load(): rows added by hand come last ──────────────────────────────────────

def test_load_puts_rows_added_by_hand_last():
    class Cursor:
        def execute(self, sql, params=None):
            self.sql, self.params = sql, params

        def fetchall(self):
            return []

    cur = Cursor()
    transform_sources.load(cur)
    assert "ORDER BY (source = %s), display_id" in cur.sql
    assert cur.params == (preprint_dedup.MANUAL_SOURCE,)


# ── the dedup (step 5): absorbed, filling blanks, no clash ────────────────────

def test_same_identifiers_the_existing_record_keeps_its_id_and_values():
    built = build(source("REPL-000001", outcome="successful", outcome_quote="It replicated.",
                         out_quote_source="abstract"),
                  by_hand("MAN-000001", outcome="failed", outcome_quote="It did not.",
                          out_quote_source="full_text", abstract_r="Only the entry has it."))
    [row] = built.to_dict("records")
    assert row["source_display_id"] == "REPL-000001"
    assert (row["outcome"], row["outcome_quote"]) == ("successful", "It replicated.")
    assert row["abstract_r"] == "Only the entry has it."
    assert row["source"] == "entry_sheet_replications"
    assert value(row["outcome_conflict"]) is None and built.attrs["outcome_conflicts"] == []
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-MAN-000001"]


def test_a_row_added_by_hand_fills_a_missing_outcome_with_its_quote():
    built = build(source("REPL-000001"),
                  by_hand("MAN-000001", outcome="failed", outcome_quote="It did not.",
                          out_quote_source="full_text"))
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["outcome"], row["outcome_quote"]) == \
        ("REPL-000001", "failed", "It did not.")


def test_two_rows_added_by_hand_clash_as_any_two_rows_do():
    built = build(by_hand("MAN-000001", outcome="successful"), by_hand("MAN-000002", outcome="failed"))
    [row] = built.to_dict("records")
    assert row["source_display_id"] == "MAN-000001"
    assert len(built.attrs["outcome_conflicts"]) == 1


# ── the pair merge (5b): no joined quote, no "mixed", no clash ────────────────

def test_same_dois_one_link_the_record_is_made_from_the_other_rows_alone():
    built = build(source("REPL-000001", url_r="https://osf.io/report", outcome="successful",
                         outcome_quote="It replicated.", out_quote_source="abstract", study_o="1"),
                  by_hand("MAN-000001", outcome="failed", outcome_quote="It did not.",
                          out_quote_source="full_text", study_o="2", abstract_r="From the entry."))
    [row] = built.to_dict("records")
    assert row["source_display_id"] == "REPL-000001"
    assert (row["outcome"], row["outcome_quote"]) == ("successful", "It replicated.")
    assert row["abstract_r"] == "From the entry."
    assert value(row["outcome_conflict"]) is None and built.attrs["outcome_conflicts"] == []
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-MAN-000001"]


def test_the_entry_brings_the_link_the_record_lacks():
    built = build(source("REPL-000001", outcome="successful"),
                  by_hand("MAN-000001", url_r="https://osf.io/report", outcome="mixed"))
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["url_r"], row["outcome"]) == \
        ("REPL-000001", "https://osf.io/report", "successful")


def _merge_frame():
    """Rows as the pair merge receives them: groups A (two sheet rows that clash), B
    (a sheet row, a website row and a row added by hand), C (rows added by hand only),
    D (one row) and E (two links, kept apart)."""
    rows = [
        ("a1", "replications", "10.1/a", "10.2/a", None, "successful", "qa1", "1"),
        ("a2", "COS", "10.1/a", "10.2/a", None, "uninformative", "qa2", "2"),
        ("b1", "replications", "10.1/b", "10.2/b", "https://osf.io/b", "failed", "qb1", None),
        ("b2", "validated", "10.1/b", "10.2/b", None, "failed", "qb2", "3"),
        ("bm", "manual", "10.1/b", "10.2/b", None, "successful", "qbm", "9"),
        ("c1", "manual", "10.1/c", "10.2/c", None, "successful", "qc1", None),
        ("c2", "manual", "10.1/c", "10.2/c", None, "failed", "qc2", None),
        ("d1", "SCORE", "10.1/d", "10.2/d", None, "mixed", "qd1", None),
        ("e1", "replications", "10.1/e", "10.2/e", "https://osf.io/e1", "failed", "qe1", None),
        ("e2", "manual", "10.1/e", "10.2/e", "https://osf.io/e2", "successful", "qe2", None),
    ]
    return pd.DataFrame([
        {"record_id": rid, "display_id": rid.upper(), "source": src, "type": "replication",
         "doi_o": doi_o, "doi_r": doi_r, "url_r": url, "outcome": outcome,
         "outcome_quote": quote, "outcome_quote_source": "abstract", "out_quote_source": "abstract",
         "study_o": study, "abstract_r": "abstract " + rid if rid in ("bm", "c2") else None,
         "duplicate_status": None, "outcome_conflict": None, "merged_record_ids": []}
        for rid, src, doi_o, doi_r, url, outcome, quote, study in rows])


def test_groups_without_a_row_added_by_hand_merge_exactly_as_before():
    frame = _merge_frame()
    out, conflicts = preprint_dedup.merge_doi_pair_dups(frame, verbose=False)
    plain = frame[~frame["doi_o"].isin(["10.1/b", "10.1/c", "10.1/e"])]
    expected, expected_conflicts = preprint_dedup.merge_doi_pair_dups(plain, verbose=False)
    untouched = ~out["doi_o"].isin(["10.1/b", "10.1/c", "10.1/e"])

    def records(frame):
        return [{k: value(v) for k, v in row.items()} for row in frame.to_dict("records")]

    assert records(out[untouched]) == records(expected.reindex(columns=out.columns))
    a = out[out["doi_o"] == "10.1/a"].iloc[0]
    # As the merge always did: quotes and study numbers joined, COS credited, the
    # clash the rules cannot settle logged and carried on the record.
    assert (a["record_id"], a["source"], a["outcome_quote"], a["study_o"]) == \
        ("a1", "COS", "qa1 || qa2", "1; 2")
    assert a["outcome"] == "successful" and a["outcome_conflict"] == "successful | uninformative"
    assert a["merged_record_ids"] == ["a2"]
    assert conflicts[conflicts["doi_o"] == "10.1/a"].to_dict("records") == \
        expected_conflicts.to_dict("records")


def test_in_a_mixed_group_the_row_added_by_hand_only_fills_blanks():
    out, conflicts = preprint_dedup.merge_doi_pair_dups(_merge_frame(), verbose=False)
    b = out[out["doi_o"] == "10.1/b"].iloc[0]
    # b1 and b2 merge as they would alone (the website row's judgement wins) ...
    alone, _ = preprint_dedup.merge_doi_pair_dups(
        _merge_frame().query("record_id in ['b1', 'b2']"), verbose=False)
    for column in ("record_id", "source", "outcome", "outcome_quote", "study_o", "url_r"):
        assert b[column] == alone.iloc[0][column], column
    # ... and bm fills only what they left blank, adding no clash and no "mixed".
    assert b["abstract_r"] == "abstract bm"
    assert b["outcome"] == "failed" and "qbm" not in b["outcome_quote"]
    assert b["merged_record_ids"] == ["b2", "bm"]
    assert "10.1/b" not in set(conflicts["doi_o"])


def test_a_group_of_rows_added_by_hand_alone_merges_like_any_other():
    out, conflicts = preprint_dedup.merge_doi_pair_dups(_merge_frame(), verbose=False)
    c = out[out["doi_o"] == "10.1/c"].iloc[0]
    assert (c["record_id"], c["outcome"], c["outcome_quote"]) == ("c1", "mixed", "qc1 || qc2")
    assert c["merged_record_ids"] == ["c2"]
    assert conflicts[conflicts["doi_o"] == "10.1/c"]["resolved_by"].tolist() == ["merge rules"]
    # Two links are two reports, a row added by hand or not.
    assert sorted(out[out["doi_o"] == "10.1/e"]["record_id"]) == ["e1", "e2"]


def test_a_reproduction_added_by_hand_fills_a_missing_axis_only():
    axes = dict(type="reproduction", outcome=None)
    built = build(source("REPRO-000001", outcome_computation="computational issues",
                         outcome_computational_quote="It broke.", **axes),
                  by_hand("MAN-000001", url_r="https://osf.io/r",
                          outcome_computation="computationally reproducible",
                          outcome_robustness="robust", outcome_robustness_quote="Robust.", **axes))
    [row] = built.to_dict("records")
    assert row["source_display_id"] == "REPRO-000001"
    assert (row["outcome_computation"], row["outcome_robustness"]) == ("computational issues", "robust")
    assert row["outcome"] == "computational issues, robust"


def test_a_link_of_its_own_on_the_entry_does_not_unmerge_the_other_rows():
    """Two sheet rows that merge on their own (one link, one without) still merge when
    an entry with another link joins the group: the entry is a record of its own."""
    rows = [source("REPL-000001", url_r="https://osf.io/1", outcome="failed",
                   outcome_quote="No.", out_quote_source="abstract"),
            source("REPL-000002", outcome="failed", outcome_quote="No.", out_quote_source="abstract")]
    alone = build(*rows)
    assert len(alone) == 1
    with_entry = build(*rows, by_hand("MAN-000001", url_r="https://osf.io/2", outcome="failed",
                                      outcome_quote="No.", out_quote_source="abstract"))
    assert sorted(with_entry["source_display_id"]) == ["MAN-000001", "REPL-000001"]
    kept = with_entry[with_entry["source_display_id"] == "REPL-000001"].iloc[0]
    assert kept["url_r"] == "https://osf.io/1" and kept["outcome"] == "failed"
    assert with_entry.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-REPL-000002"]
    predicted = manual_records.placements(
        pd.DataFrame([*rows, by_hand("MAN-000001", url_r="https://osf.io/2")]), ["id-MAN-000001"])
    assert predicted["id-MAN-000001"]["kind"] == "own"


def test_the_entry_does_not_make_the_website_records_evidence_win():
    """The website record's quote replaces another's only when the judgements of the
    group disagree; an entry's judgement is not one of them."""
    sheet = source("REPL-000001", source="replications", outcome="failed", outcome_quote="Sheet quote.",
                   out_quote_source="abstract")
    website = source("REPL-000002", source="validated", outcome="failed", outcome_quote="Website quote.",
                     out_quote_source="abstract")
    entry = by_hand("MAN-000001", outcome="mixed", outcome_quote="Entry quote.", out_quote_source="abstract")
    assert build(sheet, website)["outcome_quote"].tolist() == build(sheet, website, entry)["outcome_quote"].tolist()


def test_an_entry_dropped_in_the_dedup_fills_the_record_after_the_pair_merge():
    """The entry shares its key with one sheet row; that row and another (with a link)
    merge into one record. The record is what the two make, and the entry fills only
    what that leaves blank: it must not hand the first row a judgement that then
    competes with the second's."""
    first = source("REPL-000001")
    second = source("REPL-000002", url_r="https://osf.io/1", outcome="failed",
                    outcome_quote="No.", out_quote_source="abstract")
    entry = by_hand("MAN-000001", outcome="successful", outcome_quote="Yes.",
                    out_quote_source="abstract", abstract_r="From the entry.")
    alone = build(first, second)
    built = build(first, second, entry)
    [row], [before] = built.to_dict("records"), alone.to_dict("records")
    assert (row["source_display_id"], row["outcome"], row["outcome_quote"]) ==         (before["source_display_id"], "failed", "No.")
    assert row["abstract_r"] == "From the entry."
    assert built.attrs["outcome_conflicts"] == [] and value(row["outcome_conflict"]) is None
    assert set(built.attrs["merged_record_ids"]["id-REPL-000001"]) == {"id-REPL-000002", "id-MAN-000001"}


# ── the prediction agrees with the build ──────────────────────────────────────

SCENARIOS = {
    "nothing shared": ([source("REPL-000001", doi_r="10.9999/other")], {}, "own"),
    "same identifiers": ([source("REPL-000001")], {}, "absorbed"),
    "same DOIs, one link": ([source("REPL-000001", url_r="https://osf.io/x")], {}, "absorbed"),
    "two links": ([source("REPL-000001", url_r="https://osf.io/x")],
                  {"url_r": "https://osf.io/y"}, "own"),
    "a redundant link": ([source("REPL-000001")],
                         {"url_r": "https://doi.org/" + REPORT}, "absorbed"),
    "kept distinct": ([source("REPL-000001")], {"duplicate_status": "distinct"}, "own"),
    "other type": ([source("REPL-000001")], {"type": "reproduction"}, "own"),
    "no original DOI": ([source("REPL-000001", doi_o=None, ref_o="A ref")],
                        {"doi_o": None, "ref_o": "A ref"}, "absorbed"),
    "after another entry": ([by_hand("MAN-000001")], {}, "absorbed"),
}


def _survivor_in_build(built, rid):
    for survivor, merged in built.attrs["merged_record_ids"].items():
        if survivor == rid:
            return ("own", None)
        if rid in merged:
            return ("absorbed", survivor)
    return ("missing", None)


@pytest.mark.parametrize("name", SCENARIOS)
@pytest.mark.parametrize("merge_at_2b", [False, True], ids=["plain", "with 2b merge"])
def test_the_prediction_is_what_the_build_does(name, merge_at_2b, monkeypatch):
    existing, overrides, expected = SCENARIOS[name]
    if merge_at_2b:
        # An original-side ruling on unrelated DOIs: in production the confirmed file
        # has some, so step 2b runs the pair merge over every row.
        monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [
            {"side": "original", "doi_1": "10.1/unrelated", "doi_2": "10.1/elsewhere",
             "action": "keep_1"}])
    entry = by_hand("MAN-000009", **overrides)
    rows = pd.DataFrame(existing + [entry])
    predicted = manual_records.placements(rows, ["id-MAN-000009"])["id-MAN-000009"]
    kind, survivor = _survivor_in_build(build(*existing, entry), "id-MAN-000009")
    assert predicted["kind"] == kind == expected
    assert predicted.get("into") == survivor


def test_a_copy_added_by_hand_of_a_distinct_record_is_absorbed_whatever_2b_moves(monkeypatch):
    """When step 2b's pair merge merges anything, anywhere, it puts the rows it left
    alone, a row ruled distinct among them, behind the others. A row added by hand
    still never comes first beside another source's row, so a copy of a distinct
    record is absorbed into it either way. The prediction looks at only a few rows,
    and still follows the build."""
    rows = [source("REPL-000001", duplicate_status="distinct"), by_hand("MAN-000009"),
            # Elsewhere in the sources: two rows of another paper, which 2b merges.
            source("REPL-000005", doi_r="10.9999/elsewhere"),
            source("REPL-000006", doi_r="10.9999/elsewhere")]

    def both():
        predicted = manual_records.placements(pd.DataFrame(rows), ["id-MAN-000009"])["id-MAN-000009"]
        return (predicted["kind"], predicted.get("into")), _survivor_in_build(build(*rows), "id-MAN-000009")

    assert both() == (("absorbed", "id-REPL-000001"), ("absorbed", "id-REPL-000001"))
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [
        {"side": "original", "doi_1": "10.1/unrelated", "doi_2": "10.1/elsewhere", "action": "keep_1"}])
    assert both() == (("absorbed", "id-REPL-000001"), ("absorbed", "id-REPL-000001"))
    rows = rows[:2]                     # nothing for 2b to merge: nothing moves
    assert both() == (("absorbed", "id-REPL-000001"), ("absorbed", "id-REPL-000001"))


def test_the_prediction_reports_an_exclusion_and_a_preprint_drop(monkeypatch):
    rows = pd.DataFrame([source("REPL-000001", doi_r="10.9999/published"), by_hand("MAN-000009")])
    excluded = manual_records.placements(rows, ["id-MAN-000009"],
                                         exclusions=[{"doi_o": None, "doi_r": REPORT, "url_r": None,
                                                      "reason": "not a replication"}])
    assert excluded["id-MAN-000009"] == {"kind": "excluded", "reason": "not a replication"}
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [
        {"side": "replication", "doi_1": "10.9999/published", "doi_2": REPORT, "action": "keep_1"}])
    # Dropped by a ruling beside another source's row, it is absorbed into that row.
    place = manual_records.placements(rows, ["id-MAN-000009"])["id-MAN-000009"]
    assert (place["kind"], place["into"], place["mode"]) == ("absorbed", "id-REPL-000001", "preprint_ruling")
    # Beside a row added by hand, it is dropped as any row is.
    both = pd.DataFrame([by_hand("MAN-000001", doi_r="10.9999/published"), by_hand("MAN-000009")])
    assert manual_records.placements(both, ["id-MAN-000009"])["id-MAN-000009"]["kind"] == "left_out"
    assert manual_records.placements(rows, ["id-elsewhere"])["id-elsewhere"]["kind"] == "not_loaded"


# ── preprint and published versions (2b rulings, 6b): never against another source ──

PREPRINT_R, PUBLISHED_R = "10.31234/osf.io/abcde", "10.1177/09567976231234567"
PSYCEXTRA, JOURNAL = "10.1037/e519682015-069", "10.1177/0956797612464058"
REPORTS = {
    PREPRINT_R: {"title": "A registered replication of the anchoring effect",
                 "authors": "Ana Lopez; Ben Kim", "year": "2022"},
    PUBLISHED_R: {"title": "A Registered Replication of the Anchoring Effect",
                  "authors": "Ana Lopez; Ben Kim", "year": "2023", "journal": "Psychological Science"},
    PSYCEXTRA: {"title": "Political Extremism is Supported by an Illusion of Understanding",
                "authors": "Philip Fernbach; Todd Rogers", "journal": "PsycEXTRA Dataset", "year": "2012"},
    JOURNAL: {"title": "Political Extremism Is Supported by an Illusion of Understanding",
              "authors": "Philip Fernbach; Todd Rogers", "journal": "Psychological Science", "year": "2013"},
}


def build_with(*rows, metadata=REPORTS):
    return transform_sources.build(Tables(), verbose=False, rows=pd.DataFrame(list(rows)),
                                   metadata=metadata)


def test_without_a_row_added_by_hand_the_preprint_rule_acts_as_before():
    """The automatic rule drops a preprint for its published version: the baseline."""
    built = build_with(source("REPL-000001", doi_r=PREPRINT_R),
                       source("VAL-000001", source="validated", doi_r=PUBLISHED_R))
    assert built["source_display_id"].tolist() == ["VAL-000001"]


def test_a_published_version_added_by_hand_never_removes_the_preprint_record():
    built = build_with(source("REPL-000001", doi_r=PREPRINT_R, outcome="failed", outcome_quote="It failed."),
                       by_hand("MAN-000001", doi_r=PUBLISHED_R, outcome="successful",
                               outcome_quote="It worked.", abstract_r="From the entry."))
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["doi_r"]) == ("REPL-000001", PREPRINT_R)
    assert (row["outcome"], row["outcome_quote"]) == ("failed", "It failed.")
    assert row["abstract_r"] == "From the entry."
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-MAN-000001"]
    [decision] = built.attrs["preprint_dedup_log"]
    assert decision["doi_remove"] == PUBLISHED_R and "added by hand" in decision["resolution"]
    assert decision["applied_action"] in preprint_dedup.UNRESOLVED_ACTIONS, "still for a person to confirm"


def test_a_preprint_added_by_hand_is_absorbed_into_the_published_record():
    built = build_with(source("REPL-000001", doi_r=PUBLISHED_R, outcome="successful"),
                       by_hand("MAN-000001", doi_r=PREPRINT_R, abstract_r="From the entry."))
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["doi_r"], row["abstract_r"]) == \
        ("REPL-000001", PUBLISHED_R, "From the entry.")
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-MAN-000001"]


def test_a_ruling_for_the_doi_only_an_entry_holds_is_turned_round(monkeypatch):
    """A confirmed ruling (2b, and again at 6b) that keeps the DOI only rows added by
    hand hold: another source's record stays, with its DOI."""
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [
        {"side": "replication", "doi_1": PUBLISHED_R, "doi_2": PREPRINT_R, "action": "keep_1"}])
    built = build_with(source("REPL-000001", doi_r=PREPRINT_R, outcome="failed"),
                       by_hand("MAN-000001", doi_r=PUBLISHED_R, outcome="successful"))
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["doi_r"], row["outcome"]) == ("REPL-000001", PREPRINT_R, "failed")
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-MAN-000001"]
    assert value(row["alt_identifier_r"]) is None, "no claim about the other DOI"
    place = manual_records.placements(pd.DataFrame([source("REPL-000001", doi_r=PREPRINT_R),
                                                    by_hand("MAN-000001", doi_r=PUBLISHED_R)]),
                                      ["id-MAN-000001"])["id-MAN-000001"]
    assert (place["kind"], place["into"]) == ("absorbed", "id-REPL-000001")


def test_an_original_added_by_hand_never_moves_another_sources_records():
    """The journal version of an original, added by hand, against records under its
    PsycEXTRA copy: they keep their original's DOI, and the entries move to it."""
    built = build_with(source("REPL-000001", doi_o=PSYCEXTRA, doi_r="10.9999/one"),
                       source("REPL-000002", doi_o=PSYCEXTRA, doi_r="10.9999/two"),
                       by_hand("MAN-000001", doi_o=JOURNAL, doi_r="10.9999/one", abstract_r="Entry."),
                       by_hand("MAN-000002", doi_o=JOURNAL, doi_r="10.9999/three"))
    rows = built.set_index("source_display_id")
    assert rows["doi_o"].to_dict() == {"REPL-000001": PSYCEXTRA, "REPL-000002": PSYCEXTRA,
                                       "MAN-000002": PSYCEXTRA}
    assert rows.at["REPL-000001", "abstract_r"] == "Entry."
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-MAN-000001"]
    decision = next(d for d in built.attrs["preprint_dedup_log"] if d["side"].startswith("original"))
    assert decision["doi_remove"] == JOURNAL and "added by hand" in decision["resolution"]


def test_a_confirmed_original_ruling_moves_the_same_records_with_or_without_an_entry(monkeypatch):
    """A confirmed original-side ruling rewrites doi_o whether or not a row holds the
    DOI it keeps, so an entry does not bring it about: the record moves as it would
    alone, keeps its id, and the entry only fills its blanks."""
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [
        {"side": "original", "doi_1": JOURNAL, "doi_2": PSYCEXTRA, "action": "keep_1"}])
    existing = source("REPL-000001", doi_o=PSYCEXTRA, doi_r="10.9999/one")
    [alone] = build_with(existing).to_dict("records")
    built = build_with(existing, by_hand("MAN-000001", doi_o=JOURNAL, doi_r="10.9999/one",
                                         abstract_r="Entry."))
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["doi_o"], row["alt_identifier_o"]) == \
        ("REPL-000001", alone["doi_o"], alone["alt_identifier_o"]) == ("REPL-000001", JOURNAL, PSYCEXTRA)
    assert row["abstract_r"] == "Entry."
    assert built.attrs["merged_record_ids"]["id-REPL-000001"] == ["id-MAN-000001"]


def test_entries_alone_follow_the_preprint_rule_as_any_rows_do():
    built = build_with(by_hand("MAN-000001", doi_o=PSYCEXTRA, doi_r="10.9999/one"),
                       by_hand("MAN-000002", doi_o=JOURNAL, doi_r="10.9999/two"))
    assert set(built["doi_o"]) == {JOURNAL}


def test_a_pair_held_for_review_is_left_alone():
    """Neither DOI a preprint: held for a person, both records stay as they are."""
    other = "10.1016/j.jesp.2023.104500"
    built = build_with(source("REPL-000001", doi_r=PUBLISHED_R), by_hand("MAN-000001", doi_r=other),
                       metadata={**REPORTS, other: {**REPORTS[PUBLISHED_R], "journal": "JESP"}})
    assert sorted(built["source_display_id"]) == ["MAN-000001", "REPL-000001"]
    [decision] = built.attrs["preprint_dedup_log"]
    assert decision["applied_action"] == preprint_dedup.NEEDS_REVIEW


def test_an_entry_never_replaces_a_quote_the_record_has():
    """The record has a quote but no outcome: the entry's outcome would come with its
    own quote, so neither is taken; the record keeps what it has."""
    built = build(source("REPL-000001", outcome_quote="Their quote."),
                  by_hand("MAN-000001", outcome="successful", outcome_quote="Mine.",
                          abstract_r="From the entry."))
    [row] = built.to_dict("records")
    assert (value(row["outcome"]), row["outcome_quote"], row["abstract_r"]) == \
        (None, "Their quote.", "From the entry.")
    unit = preprint_dedup.fill_blanks_by_hand([
        {"type": "replication", "outcome": "failed", "outcome_quote": None},
        {"type": "replication", "outcome": "failed", "outcome_quote": "It failed."}])
    assert unit == {"outcome_quote": "It failed."}, "evidence for the same judgement is a blank filled"


def test_an_entry_fills_only_after_the_other_rows_are_merged():
    """6b moves VAL-000001 to the journal DOI and merges it with REPL-000001: the
    record's abstract is VAL-000001's, as without the entry, which fills only after."""
    existing = [source("REPL-000001", doi_o=JOURNAL, doi_r="10.9999/one", url_r="https://osf.io/a"),
                source("VAL-000001", source="validated", doi_o=PSYCEXTRA, doi_r="10.9999/one",
                       abstract_r="Theirs.")]
    [alone] = build_with(*existing).to_dict("records")
    built = build_with(*existing, by_hand("MAN-000001", doi_o=JOURNAL, doi_r="10.9999/one",
                                          url_r="https://osf.io/a", abstract_r="Mine."))
    [row] = built.to_dict("records")
    assert row["abstract_r"] == alone["abstract_r"] == "Theirs."
    assert sorted(built.attrs["merged_record_ids"]["id-REPL-000001"]) == ["id-MAN-000001", "id-VAL-000001"]


# ── a hand record that is already a FLoRA record keeps it ──────────────────────

class Registered(Tables):
    """Tables, with the given record_ids as the source rows of live FLoRA records."""

    def __init__(self, live, exclusions=()):
        super().__init__(exclusions)
        self.live = list(live)

    def fetchone(self):
        return {"present": "flora_records" in self.sql}

    def fetchall(self):
        if "FROM flora_records" in self.sql:
            return [{"sid": sid} for sid in self.live]
        return super().fetchall()


def build_registered(live, *rows, metadata=None):
    return transform_sources.build(Registered(live), verbose=False, rows=pd.DataFrame(list(rows)),
                                   metadata=metadata or {})


HAND = by_hand("MAN-000001", outcome="failed", outcome_quote="Mine.", out_quote_source="abstract")


def test_a_later_row_only_fills_the_blanks_of_a_hand_record_with_its_id():
    """The mirror of the rule: the hand record was there first, with its FLoRA ID, so
    it stays the record, with its values; a website-validated row arriving later fills
    only its blanks: no website precedence, no clash."""
    later = source("VAL-000001", source="validated", outcome="successful",
                   outcome_quote="Theirs.", abstract_r="From the newcomer.")
    [alone] = build_registered({"id-MAN-000001"}, HAND).to_dict("records")
    built = build_registered({"id-MAN-000001"}, later, HAND)
    [row] = built.to_dict("records")
    assert row["source_display_id"] == "MAN-000001" and row["source"] == "manual"
    assert (row["outcome"], row["outcome_quote"]) == (alone["outcome"], alone["outcome_quote"]) == \
        ("failed", "Mine.")
    assert row["abstract_r"] == "From the newcomer."
    assert value(row["outcome_conflict"]) is None and built.attrs["outcome_conflicts"] == []
    assert built.attrs["merged_record_ids"]["id-MAN-000001"] == ["id-VAL-000001"]


def test_a_later_row_with_the_link_only_fills_blanks_in_the_pair_merge_too():
    later = source("REPL-000002", url_r="https://osf.io/report", outcome="successful",
                   outcome_quote="Theirs.")
    built = build_registered({"id-MAN-000001"}, later, HAND)
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["outcome"], row["outcome_quote"]) == \
        ("MAN-000001", "failed", "Mine.")
    assert row["url_r"] == "https://osf.io/report", "the link it lacked"
    assert built.attrs["merged_record_ids"]["id-MAN-000001"] == ["id-REPL-000002"]


def test_a_later_published_version_never_removes_a_hand_record_with_its_id():
    hand = by_hand("MAN-000001", doi_r=PREPRINT_R, outcome="failed", outcome_quote="Mine.")
    built = build_registered({"id-MAN-000001"}, source("REPL-000002", doi_r=PUBLISHED_R,
                                                       outcome="successful"), hand,
                             metadata=REPORTS)
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["doi_r"], row["outcome"]) == ("MAN-000001", PREPRINT_R, "failed")
    assert built.attrs["merged_record_ids"]["id-MAN-000001"] == ["id-REPL-000002"]


def test_a_hand_record_never_takes_over_another_sources_record_with_its_id():
    """Both are FLoRA records already (one was once kept apart): the other source's
    record stays, and the hand row only fills it."""
    other = source("REPL-000002", outcome="successful")
    built = build_registered({"id-MAN-000001", "id-REPL-000002"}, other, HAND)
    [row] = built.to_dict("records")
    assert (row["source_display_id"], row["outcome"]) == ("REPL-000002", "successful")


def test_the_prediction_keeps_a_hand_record_with_its_id_too():
    rows = pd.DataFrame([source("VAL-000001", source="validated"), HAND])
    place = manual_records.placements(rows, ["id-MAN-000001"], registered={"id-MAN-000001"})
    assert place["id-MAN-000001"] == {"kind": "own", "absorbs": ["id-VAL-000001"]}
    place = manual_records.placements(rows, ["id-MAN-000001"])
    assert (place["id-MAN-000001"]["kind"], place["id-MAN-000001"]["into"]) == ("absorbed", "id-VAL-000001")


def test_an_identifier_correction_keeps_what_an_entry_filled(monkeypatch):
    """6c joins a corrected record to the metadata again from its source's fallbacks;
    what an entry filled is the record's own by then, and stays."""
    import flora_edits
    monkeypatch.setattr(flora_edits, "load_identifiers", lambda cur: {
        "records": [(["id-REPL-000001"], {"doi_r": "10.9999/corrected"})],
        "primaries": {"id-REPL-000001"}})
    built = build(source("REPL-000001"),
                  by_hand("MAN-000001", abstract_r="From the entry.", year_r="2020",
                          ref_r="Roe (2020). The replication."))
    [row] = built.to_dict("records")
    assert row["doi_r"] == "10.9999/corrected"
    assert (row["abstract_r"], row["year_r"], row["ref_r"]) == \
        ("From the entry.", "2020", "Roe (2020). The replication.")


def test_rows_filling_one_record_fill_it_in_load_order_whatever_step_folded_them():
    frame = pd.DataFrame([{"record_id": "r", "display_id": "REPL-000001", "source": "replications",
                           "type": "replication", "doi_r": REPORT, "url_r": None, "outcome": None,
                           "outcome_quote": None, "merged_record_ids": ["m1", "m2"]}])
    fills = [("r", [{"record_id": "m2", "display_id": "MAN-000002", "source": "manual",
                     "outcome": "successful", "outcome_quote": "Later."}], False),
             ("r", [{"record_id": "m1", "display_id": "MAN-000001", "source": "manual",
                     "outcome": "failed", "outcome_quote": "First."}], False)]
    out = preprint_dedup.apply_hand_fills(frame, fills)
    assert (out.at[0, "outcome"], out.at[0, "outcome_quote"]) == ("failed", "First.")


def test_no_preprint_rule_retires_a_hand_record_with_its_id():
    """Two hand records with their FLoRA IDs, one the preprint of the other: neither
    is dropped; a hand record keeps its ID unless another source's record takes it."""
    preprint = by_hand("MAN-000001", doi_r=PREPRINT_R, outcome="failed")
    published = by_hand("MAN-000002", doi_r=PUBLISHED_R, outcome="failed")
    built = build_registered({"id-MAN-000001", "id-MAN-000002"}, preprint, published, metadata=REPORTS)
    assert sorted(built["source_display_id"]) == ["MAN-000001", "MAN-000002"]
    unregistered = build_with(preprint, published)
    assert unregistered["source_display_id"].tolist() == ["MAN-000002"], "without IDs, as any rows"


def test_the_absorption_notice_names_the_record_and_says_no_id_will_appear():
    place = {"kind": "absorbed", "into": "rid", "into_display_id": "REPL-000397",
             "mode": "same_identifiers"}
    text = manual_records._placement_text(place, {"rid": ("REPL-000397", False)})
    assert text.startswith("REPL-000397 has the same identifiers")
    assert "REPL-000397 keeps its FLoRA ID and values" in text and "No new FLoRA ID" in text
    unregistered = manual_records._placement_text({**place, "mode": "same_dois_one_link"}, {})
    assert "only one of the two has a replication link" in unregistered
    assert "REPL-000397 has none yet" in unregistered


# ── validation ────────────────────────────────────────────────────────────────

def test_the_duplicate_lists_are_json_when_links_and_no_links_mix():
    """strip_redundant_url over rows with and without a link gives NaN for the missing
    one; the lists go out as JSON, which has no NaN (a 500 before)."""
    rows = pd.DataFrame([
        {"record_id": "a", "display_id": "REPL-000001", "source": "replications", "type": "replication",
         "doi_o": "10.7/orig", "doi_r": "10.7/rep", "url_r": None, "duplicate_status": None},
        {"record_id": "b", "display_id": "MAN-000001", "source": "manual", "type": "replication",
         "doi_o": "10.7/orig", "doi_r": "10.7/rep", "url_r": "https://osf.io/abcd", "duplicate_status": None},
    ])
    found = manual_records._matches(rows, "b")
    assert [m["display_id"] for m in found["same_dois_one_link"]] == ["REPL-000001"]
    assert found["same_dois_one_link"][0]["url_r"] is None
    json.dumps(found, allow_nan=False)


class AliasCursor:
    """Answers the outcome_alias query: identity aliases for the replication
    outcomes, and one alias that maps elsewhere."""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return [*({"raw_value": o, "canonical_value": o} for o in REPLICATION_OUTCOMES),
                {"raw_value": "replicated", "canonical_value": "successful"}]


GOOD = {"type": "replication", "doi_o": "10.1/o", "doi_r": "10.1/r", "outcome": "failed",
        "outcome_quote": "It failed."}


def refused(values, field=None):
    with pytest.raises(manual_records.ManualRecordError) as caught:
        manual_records.clean(AliasCursor(), values)
    assert caught.value.status == 422
    if field:
        assert caught.value.detail["field"] == field, caught.value.detail
    return caught.value.detail


def test_a_complete_replication_is_cleaned():
    v = manual_records.clean(AliasCursor(), {**GOOD, "doi_o": "https://doi.org/10.1/O",
                                              "abstract_r": "It failed, said the abstract."})
    assert (v["doi_o"], v["doi_r"], v["outcome"]) == ("10.1/o", "10.1/r", "failed")
    assert v["out_quote_source"] == "abstract", "worked out from the abstract"
    assert v["outcome_computation"] is None and set(v) == set(manual_records.FIELDS)


@pytest.mark.parametrize(("change", "field"), [
    ({"type": "extension"}, "type"),
    ({"doi_o": None}, "doi_o"),                       # neither a DOI nor a reference
    ({"doi_r": None}, "doi_r"),                       # neither a DOI nor a link
    ({"doi_r": None, "url_r": "https://osf.io/x"}, "ref_r"),
    ({"doi_o": "not-a-doi"}, "doi_o"),
    ({"url_r": "osf.io/x"}, "url_r"),
    ({"year_r": "20"}, "year_r"),
    ({"outcome": None}, "outcome"),
    ({"outcome": "not_a_replication"}, "outcome"),
    ({"outcome": "kind of"}, "outcome"),
    ({"outcome_quote": " "}, "outcome_quote"),
    ({"outcome_computation": "computational issues"}, "outcome_computation"),
    ({"study_o": "x" * 51}, "study_o"),
])
def test_what_a_replication_needs(change, field):
    refused({**GOOD, **change}, field)


def test_a_reference_stands_in_for_a_missing_original_doi():
    v = manual_records.clean(AliasCursor(), {**GOOD, "doi_o": None, "ref_o": "Doe (2001). A study."})
    assert v["doi_o"] is None and v["ref_o"] == "Doe (2001). A study."


def test_an_outcome_alias_is_accepted_as_the_build_reads_it():
    assert manual_records.clean(AliasCursor(), {**GOOD, "outcome": "success"})["outcome"] == "successful"


REPRO = {"type": "reproduction", "doi_o": "10.1/o", "doi_r": "10.1/r",
         "outcome_computation": "computational issues", "outcome_computational_quote": "It broke.",
         "outcome_robustness": "not checked"}


def test_a_reproduction_needs_both_verdicts_and_a_quote_unless_not_checked():
    v = manual_records.clean(AliasCursor(), {**REPRO, "out_quote_computational_source": "Results | results"})
    assert v["out_quote_computational_source"] == "results"
    assert v["outcome_robustness_quote"] is None and v["out_quote_robust_source"] is None
    refused({**REPRO, "outcome_robustness": None}, "outcome_robustness")
    refused({**REPRO, "outcome_computational_quote": None}, "outcome_computational_quote")
    refused({**REPRO, "outcome": "failed"}, "outcome")
    refused({**REPRO, "outcome_computation": "fine"}, "outcome_computation")


def test_the_note_is_required_and_bounded():
    with pytest.raises(manual_records.ManualRecordError) as caught:
        manual_records.clean_note("  ")
    assert caught.value.detail["field"] == "note"
    with pytest.raises(manual_records.ManualRecordError):
        manual_records.clean_note("x" * (manual_records.NOTE_LIMIT + 1))


def test_the_source_row_carries_one_outcome_shape_and_who_added_it():
    rec = {**dict.fromkeys(manual_records.FIELDS), **REPRO, "outcome": "stale",
           "manual_id": "m-1", "display_id": "MAN-000001", "status": "active",
           "note": "From a review", "created_by": "sophie", "keep_distinct": False}
    row = manual_records.source_row(rec)
    assert (row["source"], row["sheet_row_id"]) == ("manual", "m-1")
    assert row["outcome"] is None and row["outcome_computation"] == "computational issues"
    assert "validation_status" not in row
    raw = json.loads(row["raw"])
    assert raw["created_by"] == "sophie" and raw["note"] == "From a review" and raw["updated_by"] == ""


# ── routes ────────────────────────────────────────────────────────────────────

ROUTES = ("_manual_call", "ManualRecordFields", "ManualRecordCheck", "ManualRecordCreate",
          "ManualRecordUpdate", "ManualRecordAction", "admin_flora_manual_options",
          "admin_flora_manual_counts", "admin_flora_manual_check", "admin_flora_manual_list",
          "admin_flora_manual_create", "admin_flora_manual_record", "admin_flora_manual_update",
          "admin_flora_manual_withdraw", "admin_flora_manual_restore")
ADMIN = {"id": 7, "handle": "test-admin"}


@pytest.fixture
def api():
    app = FastAPI()
    service = SimpleNamespace(
        ManualRecordError=manual_records.ManualRecordError,
        options=Mock(return_value={"outcomes": ["failed"]}),
        counts=Mock(return_value={"active": 2, "withdrawn": 1}),
        check=Mock(return_value={"ready": True}),
        list_records=Mock(return_value={"entries": []}),
        create=Mock(return_value={"manual_id": "m-1", "display_id": "MAN-000001", "repeat": False}),
        detail=Mock(return_value={"display_id": "MAN-000001"}),
        update=Mock(return_value={"manual_id": "m-1", "display_id": "MAN-000001", "changed": True}),
        withdraw=Mock(return_value={"manual_id": "m-1", "display_id": "MAN-000001"}),
        restore=Mock(return_value={"manual_id": "m-1", "display_id": "MAN-000001"}),
        saved=Mock(side_effect=lambda cur, mid, head=None, repeat=False: {
            "manual_id": mid, "head": head, "repeat": repeat}))

    def current_admin():
        raise HTTPException(401, "Admin sign-in required")

    @contextmanager
    def db():
        yield "isolated-cursor"

    source = ROOT / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in ROUTES]
    assert {node.name for node in nodes} == set(ROUTES)
    audit = Mock()
    namespace = {"app": app, "Depends": Depends, "HTTPException": HTTPException,
                 "BaseModel": BaseModel, "Field": Field, "Request": Request, "Response": Response,
                 "current_admin": current_admin, "db": db, "manual_records": service,
                 "security_events": security_events, "_audit": audit}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    app.dependency_overrides[current_admin] = lambda: dict(ADMIN)
    return SimpleNamespace(app=app, client=TestClient(app), service=service, audit=audit)


BODY = {"fields": {"type": "replication", "doi_r": "10.1/r"}, "note": "Why"}


def test_every_route_needs_an_admin(api):
    api.app.dependency_overrides.clear()
    calls = [("get", "/api/admin/flora/manual/options", None),
             ("get", "/api/admin/flora/manual/counts", None),
             ("post", "/api/admin/flora/manual/check", BODY),
             ("get", "/api/admin/flora/manual", None),
             ("post", "/api/admin/flora/manual", BODY),
             ("get", "/api/admin/flora/manual/MAN-000001", None),
             ("patch", "/api/admin/flora/manual/MAN-000001", {**BODY, "version": 1}),
             ("post", "/api/admin/flora/manual/MAN-000001/withdraw", {"version": 1, "reason": "x"}),
             ("post", "/api/admin/flora/manual/MAN-000001/restore", {"version": 1})]
    for method, url, body in calls:
        response = getattr(api.client, method)(url, **({"json": body} if body else {}))
        assert response.status_code == 401, url
    api.service.create.assert_not_called()


def test_a_create_passes_the_admin_and_token_and_is_audited(api):
    response = api.client.post("/api/admin/flora/manual",
                               json={**BODY, "keep_distinct": True, "client_token": "tok"})
    assert response.status_code == 201
    args, kwargs = api.service.create.call_args
    assert args[0] == "isolated-cursor" and args[1]["doi_r"] == "10.1/r" and args[1]["type"] == "replication"
    assert args[2:] == ("Why", True, ADMIN) and kwargs == {"client_token": "tok"}
    assert api.audit.call_args.args[1] == security_events.FLORA_MANUAL_ADDED
    assert api.audit.call_args.kwargs["target_id"] == "MAN-000001"
    assert response.json() == {"manual_id": "m-1", "head": None, "repeat": False}


def test_a_repeated_create_answers_200_and_is_not_audited_again(api):
    api.service.create.return_value = {"manual_id": "m-1", "display_id": "MAN-000001", "repeat": True}
    response = api.client.post("/api/admin/flora/manual", json=BODY)
    assert response.status_code == 200 and response.json()["repeat"] is True
    api.audit.assert_not_called()


def test_a_change_withdraw_and_restore_name_the_version_and_are_audited(api):
    assert api.client.patch("/api/admin/flora/manual/MAN-000001",
                            json={**BODY, "version": 4}).status_code == 200
    assert api.service.update.call_args.args[1:] == (
        "MAN-000001", api.service.update.call_args.args[2], "Why", 4, ADMIN)
    assert api.client.post("/api/admin/flora/manual/MAN-000001/withdraw",
                           json={"version": 5, "reason": "Wrong"}).json()["head"] == "Withdrew MAN-000001."
    api.service.withdraw.assert_called_once_with("isolated-cursor", "MAN-000001", "Wrong", 5, ADMIN)
    assert api.client.post("/api/admin/flora/manual/MAN-000001/restore",
                           json={"version": 6}).json()["head"] == "Restored MAN-000001."
    assert [c.args[1] for c in api.audit.call_args_list] == [
        security_events.FLORA_MANUAL_EDITED, security_events.FLORA_MANUAL_WITHDRAWN,
        security_events.FLORA_MANUAL_RESTORED]


def test_an_unchanged_save_says_so_and_is_not_audited(api):
    api.service.update.return_value = {"manual_id": "m-1", "display_id": "MAN-000001", "changed": False}
    response = api.client.patch("/api/admin/flora/manual/MAN-000001", json={**BODY, "version": 1})
    assert response.json()["head"] == "Nothing changed in MAN-000001."
    api.audit.assert_not_called()


def test_a_refusal_keeps_its_detail_and_a_stale_version_its_header(api):
    api.service.update.side_effect = manual_records.ManualRecordError(
        409, {"code": "version_conflict", "message": "Changed", "current_version": 8})
    response = api.client.patch("/api/admin/flora/manual/MAN-000001", json={**BODY, "version": 1})
    assert response.status_code == 409 and response.headers["X-Current-Version"] == "8"
    assert response.json()["detail"]["code"] == "version_conflict"
    api.service.create.side_effect = manual_records.ManualRecordError(
        422, {"code": "invalid", "message": "Bad DOI", "field": "doi_o"})
    response = api.client.post("/api/admin/flora/manual", json=BODY)
    assert response.status_code == 422 and response.json()["detail"]["field"] == "doi_o"
    api.audit.assert_not_called()


def test_a_check_and_the_list_pass_their_arguments(api):
    api.client.post("/api/admin/flora/manual/check",
                    json={**BODY, "keep_distinct": True, "manual_id": "MAN-000001"})
    assert api.service.check.call_args.kwargs == {"exclude_ref": "MAN-000001", "keep_distinct": True}
    api.client.get("/api/admin/flora/manual", params={"status": "withdrawn"})
    api.service.list_records.assert_called_once_with("isolated-cursor", "withdrawn")
    assert api.client.get("/api/admin/flora/manual/counts").json() == {"active": 2, "withdrawn": 1}


def test_the_fixed_paths_are_declared_before_the_ones_that_would_take_them():
    """GET /flora/{flora_id} would take "manual" for a record id, and
    /flora/manual/{ref} "options", "counts" or "check"."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    order = []
    for node in tree.body:
        for decorator in getattr(node, "decorator_list", []):
            if (isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", "") in
                    ("get", "post", "patch", "delete") and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)):
                order.append((decorator.func.attr, decorator.args[0].value))
    position = {route: i for i, route in enumerate(order)}
    record = position[("get", "/api/admin/flora/{flora_id}")]
    manual = [route for route in order if route[1].startswith("/api/admin/flora/manual")]
    assert len(manual) == 9 and all(position[route] < record for route in manual)
    entry = position[("get", "/api/admin/flora/manual/{ref}")]
    for fixed in ("options", "counts"):
        assert position[("get", f"/api/admin/flora/manual/{fixed}")] < entry
