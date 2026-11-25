import csv

import pytest

from scripts.apply_duplicate_rulings import load_batch, plan


def _write(tmp_path, rows):
    path = tmp_path / "rulings.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["absorbed_id", "survivor_id", "note"])
        writer.writeheader()
        writer.writerows(rows)
    return path


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self.rows


def _record(export_id, primary, **extra):
    return {"export_id": export_id, "primary_id": primary, "merged": 0, "retired_at": None,
            "duplicate_status": None, "duplicate_of": None, **extra}


def test_the_committed_rulings_are_valid():
    rows = load_batch("data/corrections/fred_data_179_duplicates.csv")
    assert ("FLORA-002632", "FLORA-001168") in {(r["absorbed_id"], r["survivor_id"]) for r in rows}


def test_rulings_resolve_to_source_records(tmp_path):
    rows = load_batch(_write(tmp_path, [{"absorbed_id": "A", "survivor_id": "B", "note": "Same report."}]))
    [item] = plan(_Cur([_record("A", "uuid-a"), _record("B", "uuid-b")]), rows)
    assert (item["record_id"], item["duplicate_of"]) == ("uuid-a", "uuid-b")


def test_an_applied_ruling_is_skipped_and_a_conflicting_one_refused(tmp_path):
    rows = load_batch(_write(tmp_path, [{"absorbed_id": "A", "survivor_id": "B", "note": "Same report."}]))
    done = _record("A", "uuid-a", duplicate_status="duplicate", duplicate_of="uuid-b")
    assert plan(_Cur([done, _record("B", "uuid-b")]), rows) == []
    other = _record("A", "uuid-a", duplicate_status="duplicate", duplicate_of="uuid-c")
    with pytest.raises(ValueError, match="another record"):
        plan(_Cur([other, _record("B", "uuid-b")]), rows)


def test_a_record_combining_several_sources_is_refused(tmp_path):
    rows = load_batch(_write(tmp_path, [{"absorbed_id": "A", "survivor_id": "B", "note": "Same report."}]))
    with pytest.raises(ValueError, match="several source rows"):
        plan(_Cur([_record("A", "uuid-a", merged=1), _record("B", "uuid-b")]), rows)


@pytest.mark.parametrize("rows", [
    [{"absorbed_id": "A", "survivor_id": "A", "note": "Same report."}],
    [{"absorbed_id": "A", "survivor_id": "B", "note": ""}],
    [{"absorbed_id": "A", "survivor_id": "B", "note": "Same."}, {"absorbed_id": "B", "survivor_id": "C", "note": "Same."}],
])
def test_malformed_rulings_are_refused(tmp_path, rows):
    with pytest.raises(ValueError):
        load_batch(_write(tmp_path, rows))
