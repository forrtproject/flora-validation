import hashlib
import json
import pytest
from unittest.mock import patch, MagicMock


FAKE_CSV_CONTENT = (
    b"pair_id,paper_type,link_method,doi_r\n"
    b"abc,replication,llm_references,10.1/x\n"
)


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "ten percent",
        "NaN",
        "nan",
        "Infinity",
        "+inf",
        "-Infinity",
        float("nan"),
        float("inf"),
        float("-inf"),
        "-0.01",
        "100.01",
        -1,
        101,
        True,
    ],
)
def test_parse_max_removal_percent_rejects_unsafe_values(configured):
    from sync_csv import (
        RemovalPercentConfigurationError,
        parse_max_removal_percent,
    )

    with pytest.raises(
        RemovalPercentConfigurationError,
        match="EXTRACTOR_MAX_REMOVAL_PERCENT must be a finite number",
    ):
        parse_max_removal_percent(configured)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("0", 0.0),
        ("0.25", 0.25),
        (10, 10.0),
        (" 42.5 ", 42.5),
        (100.0, 100.0),
    ],
)
def test_parse_max_removal_percent_accepts_finite_values_in_range(
    configured,
    expected,
):
    from sync_csv import parse_max_removal_percent

    assert parse_max_removal_percent(configured) == expected


def test_invalid_removal_configuration_fails_closed_before_download(
    tmp_path,
    monkeypatch,
    capsys,
):
    from sync_csv import sync_once

    monkeypatch.setenv("EXTRACTOR_MAX_REMOVAL_PERCENT", "NaN")
    report_path = tmp_path / "report.json"
    with patch("sync_csv._fetch_csv") as mock_fetch, \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(data_dir=tmp_path, report_path=report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is False
    mock_fetch.assert_not_called()
    mock_import.assert_not_called()
    assert report["success"] is False
    assert report["status"] == "error"
    assert report["error_code"] == "invalid_removal_percent_configuration"
    assert report["max_removal_percent"] is None
    assert "finite number from 0 through 100" in report["message"]
    assert "CONFIGURATION ERROR" in capsys.readouterr().out


def test_sync_report_and_comparison_use_same_runtime_validated_limit(
    tmp_path,
    monkeypatch,
):
    from sync_csv import sync_once

    monkeypatch.setenv("EXTRACTOR_MAX_REMOVAL_PERCENT", "12.5")
    previous = _snapshot(*(f"p{i}" for i in range(10)))
    (tmp_path / "extracted_latest.csv").write_bytes(previous)
    report_path = tmp_path / "report.json"
    response = MagicMock(
        status_code=200,
        content=_snapshot(*(f"p{i}" for i in range(9))),
    )

    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(data_dir=tmp_path, report_path=report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is True
    assert mock_import.call_count == 1
    assert report["max_removal_percent"] == 12.5
    assert report["removed_percent"] == 10.0


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
    """A DB/file split is persisted as a failed Part 1 and cannot unlock cleanup."""
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


def test_more_than_ten_percent_removed_is_blocked_but_exactly_ten_is_allowed(tmp_path):
    from sync_csv import SnapshotSafetyError, _compare_snapshots

    previous = tmp_path / "extracted_latest.csv"
    previous.write_bytes(_snapshot(*(f"p{i}" for i in range(10))))
    candidate = tmp_path / "candidate.csv"
    candidate.write_bytes(_snapshot(*(f"p{i}" for i in range(9))))

    allowed = _compare_snapshots(candidate, previous)
    assert allowed.removed_percent == 10

    candidate.write_bytes(_snapshot(*(f"p{i}" for i in range(8))))
    with pytest.raises(SnapshotSafetyError) as raised:
        _compare_snapshots(candidate, previous)
    assert raised.value.code == "excessive_resolved_removal"
    assert raised.value.details["removed_count"] == 2
    assert raised.value.details["removed_percent"] == 20


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


def test_excessive_removal_preserves_latest_and_reports_why(tmp_path):
    from sync_csv import sync_once

    previous = _snapshot(*(f"p{i}" for i in range(10)))
    latest = tmp_path / "extracted_latest.csv"
    latest.write_bytes(previous)
    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot("p0"))

    with patch("sync_csv.requests.get", return_value=response), \
         patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(data_dir=tmp_path, report_path=report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is False
    assert mock_import.call_count == 0
    assert latest.read_bytes() == previous
    assert report["status"] == "blocked"
    assert report["error_code"] == "excessive_resolved_removal"
    assert report["removed_count"] == 9


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


def test_truncated_archive_write_fails_part1_instead_of_unlocking_cleanup(tmp_path):
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


def test_missing_baseline_blocks_instead_of_posing_as_a_first_deployment(tmp_path):
    """An empty volume must not silently switch the removal guard off."""
    from sync_csv import sync_once

    report_path = tmp_path / "report.json"
    response = MagicMock(status_code=200, content=_snapshot("p0"))
    with patch("sync_csv.requests.get", return_value=response),          patch("sync_csv.run_import") as mock_import:
        succeeded = sync_once(
            data_dir=tmp_path,
            report_path=report_path,
            maintenance_run_id="run-no-baseline",
            require_baseline=True,
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert succeeded is False
    assert mock_import.call_count == 0
    assert report["status"] == "blocked"
    assert report["error_code"] == "missing_local_baseline"
    assert not (tmp_path / "extracted_latest.csv").exists()


def test_stale_local_csv_cannot_stand_in_for_the_recorded_baseline(tmp_path):
    """A replacement pod's older bundled CSV is not a baseline."""
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
    assert succeeded is False
    assert mock_import.call_count == 0
    assert report["error_code"] == "baseline_snapshot_unavailable"


def test_recorded_archive_is_preferred_over_the_mutable_promoted_csv(tmp_path):
    """The removal guard compares against the last verified sync's own bytes."""
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
    assert succeeded is False
    assert mock_import.call_count == 0
    assert report["baseline_file"] == archive.name
    assert report["error_code"] == "excessive_resolved_removal"
    assert report["removed_count"] == 2
