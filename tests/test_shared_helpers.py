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
