"""csv_to_db.py --retire: remove only what flora-extractor named, only if untouched."""
import pandas as pd
import pytest

import csv_to_db


def _entry(pair_id, reason="superseded", **extra):
    return {"pair_id": pair_id, "reason": reason, "detail": "", "superseded_by": "",
            "doi_r": "10.1/r", "doi_o": "10.1/old", "doi_o_now": "",
            "retired_at": "2026-09-23T00:00:00+00:00", "release": "rel", **extra}


def _record(record_id, status="unvalidated", **flags):
    return {"record_id": record_id, "validation_status": status,
            "has_activity": False, "in_validated": False, "assigned": False, **flags}


def test_the_plan_retires_only_untouched_records_and_flags_the_rest():
    manifest = {p: _entry(p) for p in
                ("shipped", "gone", "clean", "seen", "final", "assigned", "done",
                 "excluded")}
    manifest["ghost"] = _entry("ghost", reason="unexplained")
    state = {
        "shipped": _record("r0"),
        "clean": _record("r1"),
        "seen": _record("r2", has_activity=True),
        "final": _record("r3", in_validated=True),
        "assigned": _record("r4", assigned=True),
        "done": _record("r5", status="validated", has_activity=True),
        "excluded": _record("r6", status="rejected", has_activity=True),
        "ghost": _record("r7"),
    }
    plan = {s["pair_id"]: s["action"] for s in
            csv_to_db.plan_retirements(manifest, {"shipped"}, state)}
    assert plan == {"shipped": "still_shipped", "gone": "absent", "clean": "retire",
                    "seen": "flag", "final": "flag", "assigned": "flag", "done": "flag",
                    "excluded": "already_excluded", "ghost": "held_unexplained"}
    forced = csv_to_db.plan_retirements(manifest, {"shipped"}, state,
                                        include_unexplained=True)
    assert next(s for s in forced if s["pair_id"] == "ghost")["action"] == "retire"


def test_a_manifest_with_an_unknown_reason_is_refused(tmp_path):
    path = tmp_path / "retired_pairs.csv"
    pd.DataFrame([_entry("a", reason="because")]).to_csv(path, index=False)
    with pytest.raises(csv_to_db.RetireManifestError, match="because"):
        csv_to_db.load_retire_manifest(path)


class _Cursor:
    """Answers the retire path's reads from *state*; records every statement."""

    def __init__(self, state, log):
        self.state, self.log, self.rowcount, self._rows = state, log, 0, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.log.append(text)
        if "FROM unvalidated u WHERE u.pair_id = ANY" in text:
            wanted = set(params[0])
            self._rows = [(r["record_id"], p, r["validation_status"], r["has_activity"],
                           r["in_validated"], r["assigned"])
                          for p, r in self.state.items() if p in wanted]
        elif text.startswith("SELECT"):
            self._rows = [(True,)]
        elif text.startswith("DELETE FROM unvalidated"):
            self.rowcount = len(params[0])
        else:
            self.rowcount = 1

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]


class _Conn:
    def __init__(self, state):
        self.state, self.log, self.readonly, self.commits = state, [], False, 0

    def set_session(self, readonly=False, **_):
        self.readonly = readonly

    def cursor(self):
        return _Cursor(self.state, self.log)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.commits += 1
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def _files(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://fake")
    manifest = tmp_path / "retired_pairs.csv"
    pd.DataFrame([_entry("clean"), _entry("seen")]).to_csv(manifest, index=False)
    extracted = tmp_path / "extracted.csv"
    extracted.write_text("pair_id,paper_type,link_method,outcome\n", encoding="utf-8")
    return manifest, extracted


def test_the_dry_run_is_a_read_only_session_that_only_selects(_files, monkeypatch):
    conn = _Conn({"clean": _record("r1"), "seen": _record("r2", has_activity=True)})
    monkeypatch.setattr(csv_to_db.psycopg2, "connect", lambda url: conn)
    plan = csv_to_db.run_retire(*_files)
    assert {s["pair_id"]: s["action"] for s in plan} == {"clean": "retire",
                                                         "seen": "flag"}
    assert conn.readonly and conn.commits == 0
    assert conn.log and all(sql.startswith("SELECT") for sql in conn.log)


def test_apply_refuses_a_plan_other_than_the_one_reviewed(_files, monkeypatch):
    conn = _Conn({"clean": _record("r1")})
    monkeypatch.setattr(csv_to_db.psycopg2, "connect", lambda url: conn)
    with pytest.raises(RuntimeError, match="expect-retire"):
        csv_to_db.run_retire(*_files, apply=True)
    with pytest.raises(RuntimeError, match="not the 5"):
        csv_to_db.run_retire(*_files, apply=True, expect_retire=5)
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in conn.log)


def test_apply_archives_before_it_deletes_and_only_notes_a_touched_record(
        _files, monkeypatch):
    conn = _Conn({"clean": _record("r1"), "seen": _record("r2", has_activity=True)})
    monkeypatch.setattr(csv_to_db.psycopg2, "connect", lambda url: conn)
    csv_to_db.run_retire(*_files, apply=True, expect_retire=1)
    writes = [sql for sql in conn.log if sql.startswith(("INSERT", "UPDATE", "DELETE",
                                                          "LOCK"))]
    assert writes[0].startswith("LOCK TABLE unvalidated")
    archive = next(i for i, sql in enumerate(writes)
                   if sql.startswith("INSERT INTO retired_records"))
    delete = next(i for i, sql in enumerate(writes)
                  if sql.startswith("DELETE FROM unvalidated"))
    assert archive < delete
    notes = [sql for sql in writes if sql.startswith("UPDATE unvalidated SET admin_notes")]
    assert len(notes) == 1
    assert not any(sql.startswith("UPDATE unvalidated SET validation_status")
                   for sql in writes)
