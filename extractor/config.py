"""Leaf configuration shared across csv_to_db, sync_csv, and extractor_maintenance.

Kept free of internal imports so extractor modules form an acyclic dependency graph.
"""
from __future__ import annotations

import os
import sys

DEFAULT_GITHUB_REPO = "forrtproject/flora-extractor"
DEFAULT_GITHUB_BRANCH = "main"

# Legacy module-level aliases (prefer github_repo() / github_branch() so env
# changes and load_dotenv() running after import are always respected).
GITHUB_REPO = os.environ.get("GITHUB_REPO", DEFAULT_GITHUB_REPO)
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", DEFAULT_GITHUB_BRANCH)

# PostgreSQL advisory locks for extractor maintenance & import/withdraw serialization.
PIPELINE_ADVISORY_LOCK_ID = 7_342_025_091
IMPORT_WITHDRAW_LOCK_ID = 7_342_025_100


def github_repo(override: str | None = None) -> str:
    """Resolve the extractor GitHub repo dynamically after load_dotenv/env setup."""
    if override:
        return override
    sync_mod = sys.modules.get("sync_csv")
    if sync_mod is not None:
        sync_val = getattr(sync_mod, "_GITHUB_REPO", None)
        initial = getattr(sync_mod, "_INITIAL_GITHUB_REPO", GITHUB_REPO)
        if sync_val and sync_val != initial:
            return sync_val
    return os.environ.get("GITHUB_REPO") or DEFAULT_GITHUB_REPO


def github_branch(override: str | None = None) -> str:
    """Resolve the extractor GitHub branch dynamically after load_dotenv/env setup."""
    if override:
        return override
    sync_mod = sys.modules.get("sync_csv")
    if sync_mod is not None:
        sync_val = getattr(sync_mod, "_GITHUB_BRANCH", None)
        initial = getattr(sync_mod, "_INITIAL_GITHUB_BRANCH", GITHUB_BRANCH)
        if sync_val and sync_val != initial:
            return sync_val
    return os.environ.get("GITHUB_BRANCH") or DEFAULT_GITHUB_BRANCH


def build_github_raw_url(repo: str, branch: str, file_path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"
