"""Unit tests for shared leaf helpers and cycle-free extractor configuration."""
from unittest.mock import MagicMock

import consensus_engine
import csv_to_db
import extractor_maintenance
import extractor_vocab
import sync_csv
from extractor.config import (
    GITHUB_BRANCH,
    GITHUB_REPO,
    IMPORT_WITHDRAW_LOCK_ID,
    PIPELINE_ADVISORY_LOCK_ID,
    build_github_raw_url,
)
from shared.display_id import next_display_id


def test_next_display_id_supports_dict_and_tuple_cursors():
    dict_cur = MagicMock()
    dict_cur.fetchone.return_value = {"last_value": 42}
    assert next_display_id(dict_cur, "manual", "MAN") == "MAN-000042"

    tuple_cur = MagicMock()
    tuple_cur.fetchone.return_value = (7,)
    assert next_display_id(tuple_cur, "replications", "REPL") == "REPL-000007"


def test_quote_source_for_is_shared_between_vocab_and_consensus():
    assert consensus_engine.quote_source_for is extractor_vocab.quote_source_for
    assert extractor_vocab.quote_source_for("it failed", "We found it failed.") == "abstract"
    assert extractor_vocab.quote_source_for("it failed", "We found it held.") == "full_text"
    assert extractor_vocab.quote_source_for("", "Anything") is None


def test_extractor_config_reexports_preserve_lock_ids_and_url_builder():
    assert csv_to_db.IMPORT_WITHDRAW_LOCK_ID == IMPORT_WITHDRAW_LOCK_ID
    assert csv_to_db.PIPELINE_ADVISORY_LOCK_ID == PIPELINE_ADVISORY_LOCK_ID
    assert extractor_maintenance.PIPELINE_ADVISORY_LOCK_ID == PIPELINE_ADVISORY_LOCK_ID
    assert sync_csv._GITHUB_REPO == GITHUB_REPO
    assert sync_csv._GITHUB_BRANCH == GITHUB_BRANCH
    assert sync_csv._build_url is build_github_raw_url
    assert build_github_raw_url("org/repo", "main", "data/extracted.csv") == (
        "https://raw.githubusercontent.com/org/repo/main/data/extracted.csv"
    )


def test_sync_csv_and_retire_manifest_resolve_same_post_import_repo_and_branch(tmp_path, monkeypatch):
    import json
    import requests

    seen_urls = []
    commit_sha = "a" * 40

    class _Resp:
        def __init__(self, status_code, content=b"", text=""):
            self.status_code = status_code
            self.content = content
            self.text = text

    def fake_get(url, **kw):
        seen_urls.append(url)
        if "/commits/" in url:
            return _Resp(200, text=commit_sha)
        if url.endswith("extracted.csv"):
            csv_body = (
                b"pair_id,paper_type,link_method,doi_r\n"
                b"P1,replication,llm_references,10.1/b\n"
            )
            return _Resp(200, content=csv_body)
        return _Resp(404)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(sync_csv, "run_import", lambda *a, **kw: None)

    # 1) Post-import env override: BOTH sync_csv and fetch_retire_manifest must use the updated repo/branch.
    monkeypatch.setenv("GITHUB_REPO", "custom-org/custom-extractor")
    monkeypatch.setenv("GITHUB_BRANCH", "staging")
    report_path = tmp_path / "sync_report.json"
    ok = sync_csv.sync_once(data_dir=tmp_path, report_path=report_path)
    assert ok is True
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["source_repo"] == "custom-org/custom-extractor"
    assert report["source_commit"] == commit_sha
    assert seen_urls[0] == "https://api.github.com/repos/custom-org/custom-extractor/commits/staging"
    assert seen_urls[1] == f"https://raw.githubusercontent.com/custom-org/custom-extractor/{commit_sha}/data/extracted.csv"

    assert csv_to_db.fetch_retire_manifest(
        tmp_path / "m1.csv", commit=report["source_commit"], repo=report["source_repo"]
    ) is None
    assert seen_urls[-1] == (
        f"https://raw.githubusercontent.com/custom-org/custom-extractor/{commit_sha}/data/retired_pairs.csv"
    )

    # 2) Explicit monkeypatch on sync_csv._GITHUB_REPO / _GITHUB_BRANCH still works across both modules.
    monkeypatch.setattr(sync_csv, "_GITHUB_REPO", "fork-org/fork-extractor")
    monkeypatch.setattr(sync_csv, "_GITHUB_BRANCH", "exp-branch")
    assert sync_csv._resolve_source_commit() == commit_sha
    assert seen_urls[-1] == "https://api.github.com/repos/fork-org/fork-extractor/commits/exp-branch"
    assert csv_to_db.fetch_retire_manifest(tmp_path / "m2.csv") is None
    assert seen_urls[-1] == (
        "https://raw.githubusercontent.com/fork-org/fork-extractor/exp-branch/data/retired_pairs.csv"
    )


def test_conftest_blocks_non_local_hostaddr_multi_host_and_keyword_dsn():
    import pytest
    from tests.conftest import _assert_safe_dsn

    # Loopback and stub are allowed
    _assert_safe_dsn("postgresql://stub/stub")
    _assert_safe_dsn("postgresql://flora_test@127.0.0.1:5432/postgres")
    _assert_safe_dsn("postgresql://flora_test@localhost:5432/postgres?hostaddr=127.0.0.1")
    _assert_safe_dsn("postgresql://127.0.0.1:5432,localhost:5433/postgres")

    # Non-loopback host, hostaddr, loopback-first multi-host URI, and keyword DSN with URI in value
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("postgresql://user@db.example.org:5432/prod")
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("postgresql://user@localhost:5432/prod?hostaddr=203.0.113.10")
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("host=localhost hostaddr=203.0.113.10 dbname=prod")
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("postgresql://127.0.0.1:5432,db.example.org:5432/prod")
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("host=db.example.org application_name=postgresql://127.0.0.1/db dbname=prod")
