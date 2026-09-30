import hashlib
import json
import pytest
from unittest.mock import patch, MagicMock


FAKE_CSV_CONTENT = (
    b"pair_id,paper_type,link_method,doi_r\n"
    b"abc,replication,llm_references,10.1/x\n"
)


def test_the_removal_limit_is_gone(tmp_path, monkeypatch):
    """A drop in the extractor's CSV never stops a run: nothing is deleted by
    absence, and the retire stage removes only the pairs the extractor lists."""
    import sync_csv
    from sync_csv import sync_once

    assert not hasattr(sync_csv, "parse_max_removal_percent")
    # The old setting, even an invalid value, changes nothing.
    monkeypatch.setenv("EXTRACTOR_MAX_REMOVAL_PERCENT", "NaN")
    (tmp_path / "extracted_latest.csv").write_bytes(_snapshot(*(f"p{i}" for i in range(10))))
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot("p0"))

    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(data_dir=tmp_path, report_path=report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert "max_removal_percent" not in report
    assert report["removed_count"] == 9
    assert report["removed_percent"] == 90.0
    assert (tmp_path / "extracted_latest.csv").read_bytes() == _snapshot("p0")


def test_fetch_csv_returns_bytes_on_200():
    """_fetch_csv returns raw bytes when GitHub responds 200."""
    from sync_csv import _fetch_csv
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = FAKE_CSV_CONTENT
    with patch("sync_csv.requests.get", return_value=mock_resp):
        result = _fetch_csv("https://example.com/file.csv")
    assert result == FAKE_CSV_CONTENT


def test_fetch_csv_raises_on_non_200():
    """_fetch_csv raises RuntimeError when GitHub returns non-200."""
    from sync_csv import _fetch_csv
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"
    with patch("sync_csv.requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="404"):
            _fetch_csv("https://example.com/file.csv")


def test_save_csv_writes_unique_archive_and_staged_candidate(tmp_path):
    """_save_csv does not promote a candidate before it has imported."""
    from sync_csv import _save_csv
    candidate, archive = _save_csv(
        FAKE_CSV_CONTENT,
        tmp_path,
        maintenance_run_id="7b42ecc5-1111-2222-3333-444444444444",
    )
    latest = tmp_path / "extracted_latest.csv"
    assert not latest.exists()
    assert candidate.exists()
    assert candidate.read_bytes() == FAKE_CSV_CONTENT
    assert archive.exists()
    assert archive.read_bytes() == FAKE_CSV_CONTENT


def test_archive_filename_contains_utc_time_and_run_id(tmp_path):
    """Archive identity is visible without consulting a separate hash."""
    import re
    from sync_csv import _save_csv
    _, archive = _save_csv(
        FAKE_CSV_CONTENT,
        tmp_path,
        maintenance_run_id="7b42ecc5-1111-2222-3333-444444444444",
    )
    assert re.fullmatch(
        r"extracted_\d{8}T\d{6}Z_7b42ecc5\.csv",
        archive.name,
    )


def test_same_time_and_run_id_never_overwrite_an_archive(tmp_path):
    """Exclusive creation adds a suffix even under an exact naming collision."""
    from datetime import datetime, timezone
    from sync_csv import _save_csv

    fixed_now = datetime(2026, 9, 1, 14, 5, 32, tzinfo=timezone.utc)
    with patch("sync_csv.datetime") as clock:
        clock.now.return_value = fixed_now
        _, first = _save_csv(b"candidate A", tmp_path, "7b42ecc5")
        _, second = _save_csv(b"candidate B", tmp_path, "7b42ecc5")

    assert first.name == "extracted_20260901T140532Z_7b42ecc5.csv"
    assert second.name == "extracted_20260901T140532Z_7b42ecc5_2.csv"
    assert first.read_bytes() == b"candidate A"
    assert second.read_bytes() == b"candidate B"


def test_sync_imports_candidate_then_promotes_latest(tmp_path):
    """A successful importer atomically promotes the staged candidate."""
    from sync_csv import sync_once
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = FAKE_CSV_CONTENT
    with patch("sync_csv.requests.get", return_value=mock_resp), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(data_dir=tmp_path)
    assert succeeded is True
    mock_import.assert_called_once()
    call_path = mock_import.call_args[0][0]
    assert call_path.parent == tmp_path
    assert call_path.name.startswith(".extracted_candidate_")
    assert (tmp_path / "extracted_latest.csv").read_bytes() == FAKE_CSV_CONTENT
    assert not call_path.exists()


def test_success_report_marks_every_part1_step_for_the_same_run(tmp_path):
    """Downstream stages receive an explicit, run-scoped completion proof."""
    from sync_csv import sync_once

    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=FAKE_CSV_CONTENT)
    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import"):
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-123",
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert report["maintenance_run_id"] == "run-123"
    assert report["archive_file"].startswith("extracted_")
    assert "run123" in report["archive_file"]
    for field in (
        "download_completed",
        "validation_completed",
        "import_completed",
        "promotion_completed",
        "promotion_verified",
        "part1_completed",
    ):
        assert report[field] is True


def test_promotion_failure_records_committed_import_but_not_part1_completion(tmp_path):
    """A DB/file split is persisted as a failed Part 1 and cannot unlock later stages."""
    from sync_csv import sync_once

    previous = _snapshot("abc")
    latest = tmp_path / "extracted_latest.csv"
    latest.write_bytes(previous)
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=FAKE_CSV_CONTENT)

    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import, \
         patch("sync_csv._promote_csv", side_effect=OSError("disk is read-only")):
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-split",
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is False
    assert mock_import.call_count == 1
    assert report["import_completed"] is True
    assert report["promotion_completed"] is False
    assert report["promotion_verified"] is False
    assert report["part1_completed"] is False
    assert latest.read_bytes() == previous


def test_failed_import_preserves_previous_latest(tmp_path):
    """Schema drift may be archived, but it must never become latest."""
    from sync_csv import sync_once
    previous = FAKE_CSV_CONTENT
    latest = tmp_path / "extracted_latest.csv"
    latest.write_bytes(previous)
    mock_resp = MagicMock(status_code=200, content=FAKE_CSV_CONTENT)

    with patch("sync_csv.requests.get", return_value=mock_resp), \
         patch("sync_csv.run_import", side_effect=ValueError("schema drift")):
        succeeded = sync_once(data_dir=tmp_path)

    assert succeeded is False
    assert latest.read_bytes() == previous
    assert not list(tmp_path.glob(".extracted_candidate_*.csv"))
    archives = list(tmp_path.glob("extracted_*.csv"))
    assert any(path.name != "extracted_latest.csv" and path.read_bytes() == FAKE_CSV_CONTENT
               for path in archives)


def test_sync_logs_error_on_fetch_failure(tmp_path, capsys):
    """sync_once logs and reports failure without raising in background use."""
    from sync_csv import sync_once
    with patch("sync_csv.requests.get", side_effect=Exception("network down")):
        succeeded = sync_once(data_dir=tmp_path)
    assert succeeded is False
    captured = capsys.readouterr()
    assert "network down" in captured.out or "network down" in captured.err


def _snapshot(*pair_ids: str) -> bytes:
    rows = "".join(
        f"{pair_id},replication,llm_references,10.1/{pair_id}\n"
        for pair_id in pair_ids
    )
    return (
        "pair_id,paper_type,link_method,doi_r\n" + rows
    ).encode("utf-8")


def test_zero_resolved_snapshot_is_blocked_with_structured_counts(tmp_path):
    from sync_csv import SnapshotSafetyError, _compare_snapshots

    previous = tmp_path / "extracted_latest.csv"
    previous.write_bytes(_snapshot("a", "b"))
    candidate = tmp_path / "candidate.csv"
    candidate.write_bytes(
        b"pair_id,paper_type,link_method,doi_r\n"
        b"ignored,false_positive,prescreen_discard,10.1/x\n"
    )

    with pytest.raises(SnapshotSafetyError) as raised:
        _compare_snapshots(candidate, previous)

    assert raised.value.code == "empty_resolved_snapshot"
    assert raised.value.details["previous_resolved_count"] == 2
    assert raised.value.details["candidate_resolved_count"] == 0
    assert raised.value.details["removed_count"] == 2


def test_dropped_pairs_are_counted_whatever_their_share(tmp_path):
    from sync_csv import _compare_snapshots

    previous = tmp_path / "extracted_latest.csv"
    previous.write_bytes(_snapshot(*(f"p{i}" for i in range(10))))
    candidate = tmp_path / "candidate.csv"
    candidate.write_bytes(_snapshot("p0", "p1", "new"))

    comparison = _compare_snapshots(candidate, previous)
    assert comparison.removed_percent == 80
    assert comparison.removed_pair_ids == [f"p{i}" for i in range(2, 10)]
    assert comparison.added_pair_ids == ["new"]


def test_new_resolved_pair_ids_are_promoted_with_a_nonblocking_warning(tmp_path):
    from sync_csv import sync_once

    (tmp_path / "extracted_latest.csv").write_bytes(_snapshot("existing"))
    report_path = tmp_path / "report.json"
    response = MagicMock(
        status_code=200,
        content=_snapshot("existing", "new-pair"),
    )

    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(data_dir=tmp_path, report_path=report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["status"] == "warning"
    assert report["warning_codes"] == ["new_resolved_pair_ids"]
    assert report["added_count"] == 1
    assert report["added_pair_ids"] == ["new-pair"]


def test_an_empty_csv_is_still_refused(tmp_path):
    """Zero importable pairs is a broken extractor file, not a large drop."""
    from sync_csv import sync_once

    previous = _snapshot(*(f"p{i}" for i in range(10)))
    latest = tmp_path / "extracted_latest.csv"
    latest.write_bytes(previous)
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=b"pair_id,paper_type,link_method,doi_r\n")

    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(data_dir=tmp_path, report_path=report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is False
    assert mock_import.call_count == 0
    assert latest.read_bytes() == previous
    assert report["error_code"] == "empty_resolved_snapshot"


def test_archive_digest_is_read_back_from_disk_and_reported(tmp_path):
    """Parts 2 and 3 are bound to this digest, so it must describe the file."""
    from sync_csv import sync_once

    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=FAKE_CSV_CONTENT)
    with patch("sync_csv.requests.get", return_value=response),          patch("sync_csv.run_import"):
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-archive",
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    archive = tmp_path / report["archive_file"]
    assert succeeded is True
    assert report["archive_verified"] is True
    assert report["archive_sha256"] == hashlib.sha256(FAKE_CSV_CONTENT).hexdigest()
    assert report["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert report["archive_bytes"] == len(FAKE_CSV_CONTENT)


def test_truncated_archive_write_fails_part1_instead_of_unlocking_later_stages(tmp_path):
    """A silently short write must not be reported as a verified snapshot."""
    from sync_csv import sync_once

    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=FAKE_CSV_CONTENT)
    with patch("sync_csv.requests.get", return_value=response),          patch("sync_csv.sha256_file", return_value="0" * 64),          patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-truncated",
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is False
    assert mock_import.call_count == 0
    assert report["archive_verified"] is False
    assert report["part1_completed"] is False


def test_an_empty_volume_imports_as_a_first_import(tmp_path):
    """With nothing to compare against, the import goes ahead without counts."""
    from sync_csv import sync_once

    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot("p0"))
    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-no-baseline",
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["previous_resolved_count"] is None
    assert "baseline_unavailable" not in report["warning_codes"]


def test_stale_local_csv_cannot_stand_in_for_the_recorded_baseline(tmp_path):
    """A replacement pod's older bundled CSV is not what the counts compare with;
    without the recorded bytes the run imports and says it could not compare."""
    from sync_csv import sync_once

    (tmp_path / "extracted_latest.csv").write_bytes(_snapshot("stale"))
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot("p0"))
    with patch("sync_csv.requests.get", return_value=response),          patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-stale-baseline",
            baseline_file="extracted_20260901T000000Z_abcdef01.csv",
            baseline_sha256=hashlib.sha256(_snapshot(*(f"p{i}" for i in range(10)))).hexdigest(),
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["baseline_file"] is None
    assert report["previous_resolved_count"] is None
    assert "baseline_unavailable" in report["warning_codes"]


def test_recorded_archive_is_preferred_over_the_mutable_promoted_csv(tmp_path):
    """The counts compare against the last verified sync's own bytes."""
    from sync_csv import sync_once

    previous = _snapshot(*(f"p{i}" for i in range(10)))
    archive = tmp_path / "extracted_20260901T000000Z_abcdef01.csv"
    archive.write_bytes(previous)
    # Whatever a replacement pod happens to hold here is irrelevant.
    (tmp_path / "extracted_latest.csv").write_bytes(_snapshot("unrelated"))
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot(*(f"p{i}" for i in range(8))))

    with patch("sync_csv.requests.get", return_value=response),          patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-archive-baseline",
            baseline_file=archive.name,
            baseline_sha256=hashlib.sha256(previous).hexdigest(),
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["baseline_file"] == archive.name
    assert report["removed_count"] == 2


def test_a_lost_baseline_is_recovered_from_a_byte_identical_archive(tmp_path):
    """The blocked runs of 2026-09-13..21 re-downloaded the baseline's own bytes."""
    from sync_csv import sync_once

    previous = _snapshot(*(f"p{i}" for i in range(10)))
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=previous)
    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-identical",
            baseline_file="extracted_20260912T142657Z_88d4c0c8.csv",
            baseline_sha256=hashlib.sha256(previous).hexdigest(),
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["baseline_file"] == report["archive_file"]
    assert report["removed_count"] == 0


def _github(history: dict, candidate: bytes, commits_seen: list):
    """requests.get stand-in: the branch file, the commit list, blobs by commit."""
    def get(url, headers=None, params=None, timeout=None):
        if url.startswith("https://api.github.com/"):
            commits_seen.append(params)
            return MagicMock(status_code=200,
                             json=MagicMock(return_value=[{"sha": s} for s in history]))
        for sha, content in history.items():
            if f"/{sha}/" in url:
                return MagicMock(status_code=200, content=content)
        return MagicMock(status_code=200, content=candidate)
    return get


def test_a_lost_baseline_is_recovered_from_the_extractor_history_by_digest(tmp_path):
    from sync_csv import sync_once

    previous = _snapshot(*(f"p{i}" for i in range(10)))
    candidate = _snapshot(*(f"p{i}" for i in range(10)), "p10")
    commits_seen: list = []
    history = {"newer": _snapshot("unrelated"), "d7f55d9": previous}
    report_path = tmp_path / "report.json"
    with patch("sync_csv.requests.get", side_effect=_github(history, candidate, commits_seen)), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-history",
            baseline_file="extracted_20260912T142657Z_88d4c0c8.csv",
            baseline_sha256=hashlib.sha256(previous).hexdigest(),
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    history_queries = [params for params in commits_seen if params]
    assert history_queries[0]["until"] == "2026-09-12T14:26:57Z"
    restored = tmp_path / report["baseline_file"]
    assert hashlib.sha256(restored.read_bytes()).hexdigest() == report["baseline_sha256"]
    assert report["added_count"] == 1 and report["removed_count"] == 0


def test_history_without_the_recorded_bytes_gives_no_counts(tmp_path):
    """Recovery never substitutes a near miss: only the recorded digest will do.
    Without it the import still goes ahead, just without added/dropped counts."""
    from sync_csv import sync_once

    previous = _snapshot(*(f"p{i}" for i in range(10)))
    candidate = _snapshot(*(f"p{i}" for i in range(9)))
    history = {"a": _snapshot("p0"), "b": candidate}
    report_path = tmp_path / "report.json"
    with patch("sync_csv.requests.get", side_effect=_github(history, candidate, [])), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-history-miss",
            baseline_file="extracted_20260912T142657Z_88d4c0c8.csv",
            baseline_sha256=hashlib.sha256(previous).hexdigest(),
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["baseline_file"] is None
    assert "baseline_unavailable" in report["warning_codes"]


def test_the_csv_is_read_at_a_resolved_commit_which_the_report_records(tmp_path):
    """The retire stage reads the manifest at this commit, never the moving branch."""
    from sync_csv import sync_once

    sha = "d7f55d98b7994109a12701c935b21fcc9dd14968"
    urls = []

    def get(url, headers=None, params=None, timeout=None):
        urls.append(url)
        if url.startswith("https://api.github.com/"):
            return MagicMock(status_code=200, text=sha + "\n")
        return MagicMock(status_code=200, content=FAKE_CSV_CONTENT)

    report_path = tmp_path / "report.json"
    with patch("sync_csv.requests.get", side_effect=get), \
         patch("sync_csv.run_import"):
        assert sync_once(data_dir=tmp_path, report_path=report_path,
                         maintenance_run_id="run-pinned") is True
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["source_commit"] == sha
    assert urls[-1].endswith(f"/{sha}/data/extracted.csv")


@pytest.mark.parametrize("baseline_bytes", [
    b"pair_id,paper_type,link_method,doi_r\nabc,replication,a_method_nobody_knows,10.1/x\n",
    b"doi_r\n10.1/x\n",
    b"",
])
def test_an_unreadable_baseline_costs_the_counts_not_the_import(tmp_path, baseline_bytes):
    """Only the candidate is checked strictly: a last import that no longer reads
    (a vocabulary change, a damaged file) must not stop every run from then on."""
    from sync_csv import sync_once

    archive = tmp_path / "extracted_20260930T191256Z_cd9302ef.csv"
    archive.write_bytes(baseline_bytes)
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot("p0", "p1"))
    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-bad-baseline",
            baseline_file=archive.name,
            baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest(),
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["part1_completed"] is True
    assert report["previous_resolved_count"] is None
    assert report["warning_codes"].count("baseline_unavailable") == 1


def test_a_recovery_that_cannot_write_its_file_does_not_stop_the_import(tmp_path):
    from sync_csv import sync_once

    previous = _snapshot(*(f"p{i}" for i in range(10)))
    sha256 = hashlib.sha256(previous).hexdigest()
    # The recovered copy cannot be written: a directory sits where its file goes.
    (tmp_path / f"extracted_baseline_{sha256[:16]}.tmp").mkdir()
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot("p0"))
    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv._baseline_from_history", return_value=(previous, "d7f55d9")), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-full-disk",
            baseline_file="extracted_20260912T142657Z_88d4c0c8.csv",
            baseline_sha256=sha256,
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert "baseline_unavailable" in report["warning_codes"]


def test_one_unreachable_commit_does_not_end_the_history_search(tmp_path):
    from sync_csv import _baseline_from_history

    previous = _snapshot(*(f"p{i}" for i in range(10)))

    def get(url, headers=None, params=None, timeout=None):
        if url.startswith("https://api.github.com/"):
            return MagicMock(status_code=200,
                             json=MagicMock(return_value=[{"sha": "gone"}, {"sha": "d7f55d9"}]))
        if "/gone/" in url:
            return MagicMock(status_code=404, text="not found")
        return MagicMock(status_code=200, content=previous)

    with patch("sync_csv.requests.get", side_effect=get):
        found = _baseline_from_history("extracted_20260912T142657Z_88d4c0c8.csv",
                                       hashlib.sha256(previous).hexdigest())
    assert found == (previous, "d7f55d9")
