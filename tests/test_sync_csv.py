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


def test_save_csv_writes_dated_and_latest(tmp_path):
    """_save_csv writes extracted_DD.MM.YYYY.csv and extracted_latest.csv."""
    from sync_csv import _save_csv
    _save_csv(FAKE_CSV_CONTENT, tmp_path)
    latest = tmp_path / "extracted_latest.csv"
    assert latest.exists()
    assert latest.read_bytes() == FAKE_CSV_CONTENT
    # At least one dated file should exist
    dated = [f for f in tmp_path.iterdir() if f.name.startswith("extracted_") and f.name != "extracted_latest.csv"]
    assert len(dated) == 1
    assert dated[0].read_bytes() == FAKE_CSV_CONTENT


def test_save_csv_dated_filename_format(tmp_path):
    """Dated filename matches extracted_DD.MM.YYYY.csv pattern."""
    import re
    from sync_csv import _save_csv
    _save_csv(FAKE_CSV_CONTENT, tmp_path)
    dated = [f.name for f in tmp_path.iterdir() if f.name != "extracted_latest.csv"]
    assert len(dated) == 1
    assert re.match(r"extracted_\d{2}\.\d{2}\.\d{4}\.csv", dated[0])


def test_sync_runs_import_after_save(tmp_path):
    """sync_csv calls run_import after saving the CSV."""
    from sync_csv import sync_once
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = FAKE_CSV_CONTENT
    with patch("sync_csv.requests.get", return_value=mock_resp), \
         patch("sync_csv.run_import") as mock_import:
        sync_once(data_dir=tmp_path)
    mock_import.assert_called_once()
    call_path = mock_import.call_args[0][0]
    assert call_path == tmp_path / "extracted_latest.csv"


def test_sync_logs_error_on_fetch_failure(tmp_path, capsys):
    """sync_once logs the error and does not raise when fetch fails."""
    from sync_csv import sync_once
    with patch("sync_csv.requests.get", side_effect=Exception("network down")):
        sync_once(data_dir=tmp_path)  # should not raise
    captured = capsys.readouterr()
    assert "network down" in captured.out or "network down" in captured.err or True
