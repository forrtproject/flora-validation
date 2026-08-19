"""
sync_csv.py — Nightly sync of extracted.csv from the flora-extractor GitHub repo.

Downloads the latest extracted.csv, archives a dated copy, imports a staged
candidate, then atomically promotes it to extracted_latest.csv on success.

Scheduled via APScheduler (see app.py startup). Can also be run standalone:
    python sync_csv.py
"""
import os
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from console_encoding import use_utf8_output

from csv_to_db import run_import

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

_DEFAULT_DATA_DIR = Path(__file__).parent / "data"

_GITHUB_REPO = os.environ.get("GITHUB_REPO", "forrtproject/flora-extractor")
_GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
_CSV_FILE_PATH = "data/extracted.csv"
_ROUTING_RELEASE_ID = os.environ.get("ROUTING_RELEASE_ID", "")


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
    """Archive the download and return a staged candidate path.

    The caller promotes this path only after run_import succeeds, so malformed
    input cannot replace the last known-good extracted_latest.csv.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    dated_name = f"extracted_{now.strftime('%d.%m.%Y')}.csv"
    dated_path = data_dir / dated_name
    dated_path.write_bytes(content)

    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=".extracted_candidate_", suffix=".csv",
        dir=data_dir, delete=False,
    ) as staged:
        staged.write(content)
        candidate_path = Path(staged.name)

    return candidate_path


def _promote_csv(candidate_path: Path, data_dir: Path) -> Path:
    """Atomically replace extracted_latest.csv with an imported candidate."""
    latest_path = data_dir / "extracted_latest.csv"
    os.replace(candidate_path, latest_path)
    return latest_path


def sync_once(data_dir: Path = _DEFAULT_DATA_DIR) -> None:
    """Download, archive, import, and promote one extractor snapshot."""
    url = _build_url(_GITHUB_REPO, _GITHUB_BRANCH, _CSV_FILE_PATH)
    candidate_path = None
    try:
        print(f"[sync_csv] Fetching {url} …")
        content = _fetch_csv(url)
        candidate_path = _save_csv(content, data_dir)
        print(f"[sync_csv] Staged {len(content)} bytes → {candidate_path}")
        run_import(candidate_path, release_id=_ROUTING_RELEASE_ID)
        latest_path = _promote_csv(candidate_path, data_dir)
        candidate_path = None  # os.replace moved it to latest_path
        print(f"[sync_csv] Import complete; promoted → {latest_path}")
    except Exception:
        print(f"[sync_csv] ERROR during sync:")
        traceback.print_exc()
    finally:
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sync_once()
