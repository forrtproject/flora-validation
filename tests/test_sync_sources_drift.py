"""The sheet-edit check inside sync_sources.sync_source, with no database.

The check may only run once every gate has passed, may skip an unchanged sheet only
when its last check used the same payload and rules, must never cost a source its
inserts or its result, and writes nothing in a dry run. A scripted cursor answers
the few queries involved and records every statement.
"""
import hashlib
from pathlib import Path

import pytest
import yaml

import sheet_drift
import sync_sources as ss

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
REPL = next(s for s in REGISTRY["sources"] if s["key"] == "replications")
U1 = "11111111-1111-4111-8111-111111111111"


def _payload(outcome="successful", status="validated - chosen"):
    header = ",".join(REPL["expected_columns"])
    cells = {c: "" for c in REPL["expected_columns"]}
    cells.update({"id": U1, "doi_o": "10.1/o", "doi_r": "10.1/r", "outcome": outcome,
                  "validation_status": status})
    return (header + "\n" + ",".join(cells[c] for c in REPL["expected_columns"]) + "\n").encode()


RAW = {c: "" for c in REPL["expected_columns"]}
RAW.update({"id": U1, "doi_o": "10.1/o", "doi_r": "10.1/r", "outcome": "failed",
            "validation_status": "validated - chosen"})
IMPORTED_NAMES = ["record_id", "sheet_row_id", "raw", *ss.DATA_COLUMNS]
IMPORTED = [("rec-1", U1, RAW, *[None] * len(ss.DATA_COLUMNS))]


class Cursor:
    """Answers a query by the first scripted needle it contains."""

    def __init__(self, answers=(), fail_on=None):
        self.statements = []
        self.answers = list(answers)
        self.fail_on = fail_on
        self.description = []
        self.rowcount = 0
        self._rows = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.statements.append(text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("simulated failure")
        for needle, names, rows in self.answers:
            if needle in text:
                self.description = [(name,) for name in names]
                self._rows = list(rows)
                return
        self.description, self._rows = [], []

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def writes(self):
        return [s for s in self.statements if s.split()[0] in ("INSERT", "UPDATE", "DELETE")]


def _answers(last_digest="other", rules=sheet_drift.RULES_VERSION, drift_runs=True):
    return [
        ("to_regclass('sheet_drift_findings')", ["present"], [(True,)]),
        ("FROM sheet_drift_runs WHERE source", ["payload_sha256", "rules_version"],
         [(last_digest, rules)] if drift_runs else []),
        ("WHERE source = %s AND deleted_at IS NULL", IMPORTED_NAMES, IMPORTED),
        ("SELECT finding_id", ["finding_id"], []),
        ("INSERT INTO sheet_drift_runs", ["drift_run_id"], [(7,)]),
        ("COUNT(*) AS n FROM sheet_drift_findings", ["n"], [(1,)]),
        ("SELECT 1 FROM source_records", ["?column?"], [(1,)]),          # every row exists
    ]


@pytest.fixture
def sync(monkeypatch, tmp_path):
    """sync_source with the fetch, the last run and the run log replaced."""
    state = {"payload": _payload(), "last": None, "runs": [], "values": []}
    monkeypatch.setattr(ss, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(ss, "_fetch", lambda url: state["payload"])
    monkeypatch.setattr(ss, "_last_successful_run", lambda cur, key: state["last"])

    def record_run(cur, key, status, **fields):
        state["runs"].append((status, fields))
        return "run-1"
    monkeypatch.setattr(ss, "_record_run", record_run)
    monkeypatch.setattr(ss, "execute_values",
                        lambda cur, sql, rows, **kw: state["values"].append(rows))

    def run(cur, dry_run=False):
        return ss.sync_source(cur, dict(REPL), REGISTRY, dry_run)
    state["run"] = run
    return state


def _spy(monkeypatch):
    calls = []
    monkeypatch.setattr(ss, "_drift_check", lambda *a, **kw: calls.append(a) or {"status": "checked"})
    return calls


# ── never before every gate has passed ────────────────────────────────────────

def test_a_failed_fetch_never_reaches_the_check(sync, monkeypatch):
    calls = _spy(monkeypatch)

    def fail(url):
        raise ss.GateFailure("fetch failed")
    monkeypatch.setattr(ss, "_fetch", fail)
    assert sync["run"](Cursor())["status"] == "failed"
    assert calls == []


def test_an_html_payload_never_reaches_the_check(sync, monkeypatch):
    calls = _spy(monkeypatch)
    sync["payload"] = b"<!DOCTYPE html><html><body>Sign in</body></html>"
    assert sync["run"](Cursor())["status"] == "failed"
    assert calls == []


def test_a_bad_promoted_value_never_reaches_the_check(sync, monkeypatch):
    calls = _spy(monkeypatch)
    cfg = next(s for s in REGISTRY["sources"] if s["key"] == "reproductions")
    header = ",".join(cfg["expected_columns"])
    row = {c: "" for c in cfg["expected_columns"]}
    row.update({"id": U1, "outcome_computational": "not a value", "validation": "validated - chosen"})
    sync["payload"] = (header + "\n" + ",".join(row[c] for c in cfg["expected_columns"])).encode()
    result = ss.sync_source(Cursor(), dict(cfg), REGISTRY, False)
    assert result["status"] == "failed" and calls == []


def test_the_ok_path_checks_after_recording_the_run(sync, monkeypatch):
    calls = _spy(monkeypatch)
    result = sync["run"](Cursor(_answers()))
    assert result["status"] == "ok" and result["drift"] == {"status": "checked"}
    [(cur, cfg, registry, df, digest, run_id, dry_run)] = calls
    assert digest == hashlib.sha256(sync["payload"]).hexdigest()
    assert (run_id, dry_run, len(df)) == ("run-1", False, 1)


# ── an unchanged sheet ────────────────────────────────────────────────────────

def test_unchanged_and_checked_under_the_same_rules_is_skipped(sync):
    digest = hashlib.sha256(sync["payload"]).hexdigest()
    sync["last"] = {"rows_fetched": 1, "payload_sha256": digest}
    cur = Cursor(_answers(last_digest=digest))
    result = sync["run"](cur)
    assert result["status"] == "unchanged"
    assert result["drift"]["status"] == "not_due"
    assert [s for s in cur.writes() if "sheet_drift" in s] == []
    assert sync["runs"] == [("unchanged", {"rows_fetched": 1, "payload_sha256": digest})]


def test_unchanged_but_checked_under_older_rules_runs_again(sync):
    digest = hashlib.sha256(sync["payload"]).hexdigest()
    sync["last"] = {"rows_fetched": 1, "payload_sha256": digest}
    cur = Cursor(_answers(last_digest=digest, rules=sheet_drift.RULES_VERSION - 1))
    result = sync["run"](cur)
    assert result["drift"]["status"] == "checked"
    assert any(s.startswith("INSERT INTO sheet_drift_runs") for s in cur.statements)
    [[finding]] = sync["values"]
    assert finding[3] == "outcome" and finding[6:8] == ("failed", "successful")


def test_unchanged_and_never_checked_runs(sync):
    digest = hashlib.sha256(sync["payload"]).hexdigest()
    sync["last"] = {"rows_fetched": 1, "payload_sha256": digest}
    result = sync["run"](Cursor(_answers(drift_runs=False)))
    assert result["drift"]["status"] == "checked"


def test_no_schema_yet_skips_the_check(sync):
    answers = _answers()
    answers[0] = ("to_regclass('sheet_drift_findings')", ["present"], [(False,)])
    cur = Cursor(answers)
    result = sync["run"](cur)
    assert result["status"] == "ok" and result["drift"] == {"status": "no_schema"}
    assert "RELEASE SAVEPOINT sheet_drift" in cur.statements


# ── safety ────────────────────────────────────────────────────────────────────

def test_a_failing_check_rolls_back_to_its_savepoint_and_the_sync_stands(sync, capsys):
    cur = Cursor(_answers(), fail_on="INSERT INTO sheet_drift_runs")
    result = sync["run"](cur)
    assert result["status"] == "ok"
    assert result["drift"]["status"] == "failed"
    assert [status for status, _ in sync["runs"]] == ["ok"]
    at = cur.statements.index("SAVEPOINT sheet_drift")
    assert cur.statements[at + 1:].count("ROLLBACK TO SAVEPOINT sheet_drift") == 1
    assert cur.statements[-1] == "RELEASE SAVEPOINT sheet_drift"
    assert "check failed, nothing stored" in capsys.readouterr().out


def test_the_savepoint_comes_before_the_existence_check(sync):
    cur = Cursor(_answers(), fail_on="to_regclass('sheet_drift_findings')")
    result = sync["run"](cur)
    assert result["status"] == "ok" and result["drift"]["status"] == "failed"
    at = cur.statements.index("SAVEPOINT sheet_drift")
    assert "to_regclass('sheet_drift_findings')" in cur.statements[at + 1]


def test_a_dry_run_compares_and_writes_nothing(sync, monkeypatch, capsys):
    monkeypatch.setattr(ss, "_record_run", lambda *a, **kw: pytest.fail("no run in a dry run"))
    cur = Cursor(_answers())
    result = sync["run"](cur, dry_run=True)
    assert result["status"] == "ok"
    assert result["drift"] == {"status": "dry_run", "found": 1}
    assert cur.writes() == [] and sync["values"] == []
    out = capsys.readouterr().out
    assert "dry run: not stored" in out and "by column: outcome 1" in out


def test_a_dry_run_of_an_unchanged_sheet_still_previews(sync, monkeypatch):
    monkeypatch.setattr(ss, "_record_run", lambda *a, **kw: pytest.fail("no run in a dry run"))
    digest = hashlib.sha256(sync["payload"]).hexdigest()
    sync["last"] = {"rows_fetched": 1, "payload_sha256": digest}
    cur = Cursor(_answers(last_digest=digest))
    assert sync["run"](cur, dry_run=True)["drift"]["status"] == "dry_run"
    assert cur.writes() == []


# ── the run log ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status, fields", [
    ("failed", {"failure_reason": "fetch failed"}),
    ("unchanged", {"rows_fetched": 3, "payload_sha256": "abc"}),
    ("ok", {"rows_fetched": 3, "rows_inserted": 1}),
])
def test_record_run_returns_its_id_on_every_path(status, fields):
    cur = Cursor([("INSERT INTO source_sync_runs", ["run_id"], [("6b0c2a52-0000-4000-8000-000000000000",)])])
    assert ss._record_run(cur, "replications", status, **fields) == "6b0c2a52-0000-4000-8000-000000000000"
    assert "RETURNING run_id" in cur.statements[0]


def test_the_summary_line_adds_up_the_sources():
    results = {
        "replications": {"status": "ok", "drift": {"status": "checked", "open": 3, "new": 2, "resolved": 1}},
        "fred": {"status": "unchanged", "drift": {"status": "not_due", "open": 4, "new": 0, "resolved": 0}},
        "score": {"status": "ok", "drift": {"status": "failed", "reason": "x"}},
        "gone": {"status": "failed", "reason": "fetch"},
    }
    assert ss._drift_summary(results) == "7 open (+2 new, 1 resolved)   ⚠ check failed for score"
    assert ss._drift_summary({"a": {"status": "ok", "drift": {"status": "no_schema"}}}) == \
        "skipped: schema not applied yet"
    assert "dry run" in ss._drift_summary({"a": {"status": "ok", "drift": {"status": "dry_run", "found": 2}}})
