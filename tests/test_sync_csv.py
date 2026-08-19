import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


FAKE_CSV_CONTENT = b"pair_id,doi_r\nabc,10.1/x\n"


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


def test_save_csv_writes_dated_archive_and_staged_candidate(tmp_path):
    """_save_csv does not promote a candidate before it has imported."""
    from sync_csv import _save_csv
    candidate = _save_csv(FAKE_CSV_CONTENT, tmp_path)
    latest = tmp_path / "extracted_latest.csv"
    assert not latest.exists()
    assert candidate.exists()
    assert candidate.read_bytes() == FAKE_CSV_CONTENT
    # At least one dated file should exist
    dated = [f for f in tmp_path.iterdir() if f.name.startswith("extracted_") and f.name != "extracted_latest.csv"]
    assert len(dated) == 1
    assert dated[0].read_bytes() == FAKE_CSV_CONTENT


def test_save_csv_dated_filename_format(tmp_path):
    """Dated filename matches extracted_DD.MM.YYYY.csv pattern."""
    import re
    from sync_csv import _save_csv
    _save_csv(FAKE_CSV_CONTENT, tmp_path)
    dated = [f.name for f in tmp_path.iterdir() if f.name.startswith("extracted_")]
    assert len(dated) == 1
    assert re.match(r"extracted_\d{2}\.\d{2}\.\d{4}\.csv", dated[0])


def test_sync_imports_candidate_then_promotes_latest(tmp_path):
    """A successful importer atomically promotes the staged candidate."""
    from sync_csv import sync_once
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = FAKE_CSV_CONTENT
    with patch("sync_csv.requests.get", return_value=mock_resp), \
         patch("sync_csv.run_import") as mock_import:
        sync_once(data_dir=tmp_path)
    mock_import.assert_called_once()
    call_path = mock_import.call_args[0][0]
    assert call_path.parent == tmp_path
    assert call_path.name.startswith(".extracted_candidate_")
    assert (tmp_path / "extracted_latest.csv").read_bytes() == FAKE_CSV_CONTENT
    assert not call_path.exists()


def test_failed_import_preserves_previous_latest(tmp_path):
    """Schema drift may be archived, but it must never become latest."""
    from sync_csv import sync_once
    previous = b"known-good\n"
    latest = tmp_path / "extracted_latest.csv"
    latest.write_bytes(previous)
    mock_resp = MagicMock(status_code=200, content=FAKE_CSV_CONTENT)

    with patch("sync_csv.requests.get", return_value=mock_resp), \
         patch("sync_csv.run_import", side_effect=ValueError("schema drift")):
        sync_once(data_dir=tmp_path)

    assert latest.read_bytes() == previous
    assert not list(tmp_path.glob(".extracted_candidate_*.csv"))
    archives = list(tmp_path.glob("extracted_*.csv"))
    assert any(path.name != "extracted_latest.csv" and path.read_bytes() == FAKE_CSV_CONTENT
               for path in archives)


def test_sync_logs_error_on_fetch_failure(tmp_path, capsys):
    """sync_once logs the error and does not raise when fetch fails."""
    from sync_csv import sync_once
    with patch("sync_csv.requests.get", side_effect=Exception("network down")):
        sync_once(data_dir=tmp_path)  # should not raise
    captured = capsys.readouterr()
    assert "network down" in captured.out or "network down" in captured.err
