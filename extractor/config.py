"""Leaf configuration shared across csv_to_db, sync_csv, and extractor_maintenance.

Kept free of internal imports so extractor modules form an acyclic dependency graph.
"""
from __future__ import annotations

import os

GITHUB_REPO = os.environ.get("GITHUB_REPO", "forrtproject/flora-extractor")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

# PostgreSQL advisory locks for extractor maintenance & import/withdraw serialization.
PIPELINE_ADVISORY_LOCK_ID = 7_342_025_091
IMPORT_WITHDRAW_LOCK_ID = 7_342_025_100


def build_github_raw_url(repo: str, branch: str, file_path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"
