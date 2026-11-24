"""apply_sheet_edits: a filled-in Sheet edits CSV, checked against the live data.

No database: the live view of each finding, the FLoRA record and the two writes are
replaced. The whole batch is refused on the first stale or incomplete row, and an
applied batch is one editor save per record, keeps first.
"""
import importlib

import pytest

import flora_service
import security_events
import sheet_drift_service as svc

# By name: a line starting "import app" reads as an import of app.py to the suite's
# guard (test_coded_originals), and this module does not touch app.py.
ase = importlib.import_module("apply_sheet_edits")

LIVE = {
    5: {"finding_id": 5, "state": "pending", "value_in_sheet": "successful", "flora_field": "outcome",
        "targets": [{"flora_id": "REPL-1", "flora_value": "failed", "version": 3,
                     "sheet_value_for_flora": "successful", "sheet_value_error": None}]},
    6: {"finding_id": 6, "state": "conflict", "value_in_sheet": "https://doi.org/10.1/NEW",
        "flora_field": "doi_r",
        "targets": [{"flora_id": "REPL-1", "flora_value": "10.1/old", "version": 3,
                     "sheet_value_for_flora": "10.1/new", "sheet_value_error": None}]},
    7: {"finding_id": 7, "state": "matches", "value_in_sheet": "x", "flora_field": "outcome_quote",
        "targets": [{"flora_id": "REPL-1", "flora_value": "x", "version": 3,
                     "sheet_value_for_flora": "x", "sheet_value_error": None}]},
}


@pytest.fixture
def live(monkeypatch):
    calls = {"keep": [], "update": [], "audit": []}
    monkeypatch.setattr(security_events, "record",
                        lambda cur, action, **fields: calls["audit"].append((action, fields)))
    monkeypatch.setattr(ase, "_finding_record", lambda cur, fid: f"rec-{fid}" if fid in LIVE else None)
    monkeypatch.setattr(svc, "record_view",
                        lambda cur, rid: {"fields": [LIVE[int(rid.split("-")[1])]]})
    state = {"record": {"type": "replication", "outcome": "successful", "doi_r": "10.1/new"}}
    monkeypatch.setattr(flora_service, "get_record", lambda cur, fid: dict(state["record"]))
    monkeypatch.setattr(svc, "keep", lambda cur, fid, admin, note, value:
                        calls["keep"].append((fid, admin["handle"], note, value)) or {"finding_id": fid})
    monkeypatch.setattr(flora_service, "update_record",
                        lambda cur, fid, fields, revert, version, admin, notes:
                        calls["update"].append((fid, fields, version, notes)) or {"change_id": 9,
                                                                                  "changed_fields": sorted(fields)})
    return calls, state


def row(finding_id, decision="", note="", **cells):
    live = LIVE.get(finding_id, LIVE[5])
    target = live["targets"][0]
    out = {column: "" for column in svc.CSV_COLUMNS}
    out.update({"finding_id": str(finding_id), "flora_id": target["flora_id"],
                "flora_version": str(target["version"]), "value_in_sheet": live["value_in_sheet"],
                "flora_value": target["flora_value"], "decision": decision, "decision_note": note})
    out.update(cells)
    return out


def test_decisions_become_one_save_per_record_and_keeps(live):
    planned = ase.plan(None, [row(5, "use_sheet", "paper p. 4 says successful"),
                              row(6, "set_value", "resolver: 10.1/new", new_flora_value="https://doi.org/10.1/NEW"),
                              row(7, ""), row(7, "skip")])
    assert planned == {"keeps": {}, "edits": {"REPL-1": {
        "fields": {"outcome": "successful", "doi_r": "10.1/new"}, "version": 3,
        "notes": {"outcome": "paper p. 4 says successful", "doi_r": "resolver: 10.1/new"}}}}


def test_keep_is_per_finding_and_applied_first(live):
    calls, _ = live
    planned = ase.plan(None, [row(6, "keep_flora", "'=the old DOI is the published one"),
                              row(5, "use_sheet", "checked")])
    assert planned["keeps"] == {6: {"note": "=the old DOI is the published one",
                                    "sheet_value": "https://doi.org/10.1/NEW"}}
    ase.apply_plan(None, planned, {"id": None, "handle": "luke"})
    assert calls["keep"] == [(6, "luke", "=the old DOI is the published one", "https://doi.org/10.1/NEW")]
    # Audited as the panel's Keep is.
    [(action, fields)] = calls["audit"]
    assert action == security_events.SOURCE_DRIFT_KEPT
    assert (fields["actor_handle"], fields["target_kind"], fields["target_id"]) == ("luke", "sheet_edit", 6)
    assert calls["update"] == [("REPL-1", {"outcome": "successful"}, 3, {"outcome": "checked"})]


@pytest.mark.parametrize("rows, words", [
    ([row(5, "use_sheet", "ok note", value_in_sheet="mixed")], "the sheet changed"),
    ([row(5, "use_sheet", "ok note", flora_value="mixed")], "changed since the download"),
    ([row(5, "use_sheet", "ok note", flora_version="2")], "changed since the download"),
    ([row(5, "use_sheet", "")], "decision_note"),
    ([row(5, "approve", "ok note")], "decision must be one of"),
    ([row(7, "keep_flora", "ok note")], "nothing to decide"),
    ([row(9, "use_sheet", "ok note")], "does not exist"),
    ([row(5, "use_sheet", "ok note", flora_id="REPL-2")], "no FLoRA field"),
    ([row(5, "keep_flora", "ok note"), row(5, "use_sheet", "ok note")], "both kept and changed"),
    ([row(5, "use_sheet", "ok note"), row(5, "set_value", "ok note", new_flora_value="mixed")], "two values"),
    # A keep is as stale as a change once FLoRA's value or version has moved.
    ([row(6, "keep_flora", "ok note", flora_value="10.1/other")], "changed since the download"),
    ([row(6, "keep_flora", "ok note", flora_version="2")], "changed since the download"),
    ([row(6, "keep_flora", "ok note", flora_id="REPL-9")], "no longer applies"),
    ([row(6, "set_value", "ok note", new_flora_value="not a doi")], "DOI"),
    ([{"finding_id": "5", "decision": "use_sheet"}], "Not a Sheet edits CSV"),
])
def test_a_bad_or_stale_row_refuses_the_whole_batch(live, rows, words):
    with pytest.raises(ValueError, match=words):
        ase.plan(None, rows)


def test_a_sheet_value_flora_would_refuse_is_refused(live, monkeypatch):
    refused = {**LIVE[5], "targets": [{**LIVE[5]["targets"][0], "sheet_value_for_flora": None,
                                       "sheet_value_error": "not an outcome FLoRA knows"}]}
    monkeypatch.setitem(LIVE, 5, refused)
    with pytest.raises(ValueError, match="not an outcome FLoRA knows"):
        ase.plan(None, [row(5, "use_sheet", "ok note")])


def test_an_apply_that_does_not_show_rolls_back(live):
    _, state = live
    state["record"] = {"type": "replication", "outcome": "failed"}
    planned = ase.plan(None, [row(5, "use_sheet", "ok note")])
    with pytest.raises(ValueError, match="does not show the corrected values"):
        ase.apply_plan(None, planned, {"id": None, "handle": "luke"})
