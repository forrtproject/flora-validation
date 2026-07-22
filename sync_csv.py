"""
sync_csv.py — Nightly sync of extracted.csv from the flora-extractor GitHub repo.

Downloads the latest extracted.csv, archives a dated copy, overwrites
extracted_latest.csv, then calls csv_to_db.run_import to upsert new rows.

Scheduled via APScheduler (see app.py startup). Can also be run standalone:
    python sync_csv.py
"""
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from csv_to_db import run_import

load_dotenv()

_DEFAULT_DATA_DIR = Path(__file__).parent / "data"

_GITHUB_REPO = os.environ.get("GITHUB_REPO", "forrtproject/flora-extractor")
_GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
_CSV_FILE_PATH = "data/extracted.csv"


def _build_url(repo: str, branch: str, file_path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"


def _fetch_csv(url: str) -> bytes:
    """Download CSV from URL. Raises RuntimeError on non-200 status."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Authorization": f"token {token}"} if token else {}
    response = requests.get(url, headers=headers, timeout=60)
    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub returned {response.status_code} for {url}: {response.text[:200]}"
        )
    return response.content


def _save_csv(content: bytes, data_dir: Path) -> Path:
    """Write bytes to a dated archive AND to extracted_latest.csv."""
    data_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    dated_name = f"extracted_{now.strftime('%d.%m.%Y')}.csv"
    dated_path = data_dir / dated_name
    dated_path.write_bytes(content)

    latest_path = data_dir / "extracted_latest.csv"
    latest_path.write_bytes(content)

    return latest_path


def sync_once(data_dir: Path = _DEFAULT_DATA_DIR) -> None:
    """Download the latest CSV, save it, and import new rows into the DB."""
    url = _build_url(_GITHUB_REPO, _GITHUB_BRANCH, _CSV_FILE_PATH)
    try:
        print(f"[sync_csv] Fetching {url} …")
        content = _fetch_csv(url)
        latest_path = _save_csv(content, data_dir)
        print(f"[sync_csv] Saved {len(content)} bytes → {latest_path}")
        run_import(latest_path)
        print("[sync_csv] Import complete.")
    except Exception:
        print(f"[sync_csv] ERROR during sync:")
        traceback.print_exc()


if __name__ == "__main__":
    sync_once()
