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


def test_fetch_retire_manifest_honors_env_and_sync_csv_repo_overrides(tmp_path, monkeypatch):
    seen_urls = []

    class _Resp:
        status_code = 404
        content = b""

    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: (seen_urls.append(url), _Resp())[1])

    # 1) Env override after import
    monkeypatch.setenv("GITHUB_REPO", "custom-org/custom-extractor")
    monkeypatch.setenv("GITHUB_BRANCH", "staging")
    assert csv_to_db.fetch_retire_manifest(tmp_path / "m1.csv") is None
    assert seen_urls[-1] == (
        "https://raw.githubusercontent.com/custom-org/custom-extractor/staging/data/retired_pairs.csv"
    )

    # 2) Explicit monkeypatch on sync_csv._GITHUB_REPO / _GITHUB_BRANCH (baseline behavior)
    monkeypatch.setattr(sync_csv, "_GITHUB_REPO", "fork-org/fork-extractor")
    monkeypatch.setattr(sync_csv, "_GITHUB_BRANCH", "exp-branch")
    assert csv_to_db.fetch_retire_manifest(tmp_path / "m2.csv") is None
    assert seen_urls[-1] == (
        "https://raw.githubusercontent.com/fork-org/fork-extractor/exp-branch/data/retired_pairs.csv"
    )


def test_conftest_blocks_non_local_hostaddr_and_remote_dsn():
    import pytest
    from tests.conftest import _assert_safe_dsn

    # Loopback and stub are allowed
    _assert_safe_dsn("postgresql://stub/stub")
    _assert_safe_dsn("postgresql://flora_test@127.0.0.1:5432/postgres")
    _assert_safe_dsn("postgresql://flora_test@localhost:5432/postgres?hostaddr=127.0.0.1")

    # Non-loopback host or hostaddr must be rejected
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("postgresql://user@db.example.org:5432/prod")
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("postgresql://user@localhost:5432/prod?hostaddr=203.0.113.10")
    with pytest.raises(RuntimeError, match="Blocked non-local PostgreSQL"):
        _assert_safe_dsn("host=localhost hostaddr=203.0.113.10 dbname=prod")
