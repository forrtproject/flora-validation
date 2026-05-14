# FLoRA Validation DB & LLM Validator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `app.py` from the simple `pairs/coders/judgements` schema to the full validation schema, add a Gemini Flash LLM validator, consensus engine, and nightly CSV sync from GitHub — without changing any frontend API signatures.

**Architecture:** Five PostgreSQL tables (`validators`, `unvalidated`, `validation_queue`, `validated`, `record_metadata`) replace the three existing ones. `consensus_engine.py` is called synchronously inside `POST /api/judge` after both human slots complete. `sync_csv.py` fetches `extracted.csv` from GitHub nightly via APScheduler. All existing API endpoints keep identical signatures; only the DB queries inside `app.py` change.

**Tech Stack:** Python 3.11+, FastAPI, psycopg2-binary, google-generativeai>=0.8, apscheduler>=3.10, requests>=2.31, pytest, pytest-mock

**Spec:** `docs/superpowers/specs/2026-05-14-validation-db-llm-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `db_schema.sql` | DDL for fresh deployments |
| Create | `db_migrate.py` | Python migration for existing deployments |
| Create | `llm_validator.py` | Gemini Flash validation module |
| Create | `consensus_engine.py` | Post-judgement consensus logic |
| Create | `sync_csv.py` | Nightly GitHub fetch + import |
| Create | `tests/test_llm_validator.py` | Unit tests for LLM validator |
| Create | `tests/test_consensus_engine.py` | Unit tests for consensus engine |
| Create | `tests/test_sync_csv.py` | Unit tests for sync |
| Create | `tests/__init__.py` | Empty, makes tests a package |
| Create | `.env.example` | Template with all required env vars |
| Modify | `requirements.txt` | Add google-generativeai, apscheduler, requests |
| Modify | `app.py` | New DB init + all endpoint queries + scheduler |
| Modify | `csv_to_db.py` | Switch from Supabase client to psycopg2 |

---

## Task 1: SQL Schema + Requirements

**Files:**
- Create: `db_schema.sql`
- Create: `.env.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Write db_schema.sql**

```sql
-- db_schema.sql
-- Run against a fresh PostgreSQL database to create all tables.

CREATE TABLE IF NOT EXISTS validators (
    id                  SERIAL      PRIMARY KEY,
    email               TEXT        UNIQUE,
    code                TEXT        UNIQUE,
    handle              TEXT        UNIQUE NOT NULL,
    level               INTEGER     NOT NULL DEFAULT 1,
    vote_score          INTEGER     NOT NULL DEFAULT 10,
    total_judgements    INTEGER     NOT NULL DEFAULT 0,
    total_points        INTEGER     NOT NULL DEFAULT 0,
    skipped_count       INTEGER     NOT NULL DEFAULT 0,
    accuracy_score      FLOAT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    onboarded_at        TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS validators_email_key
    ON validators(email) WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS unvalidated (
    record_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pair_id             TEXT        UNIQUE,

    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,
    doi_o               TEXT,
    study_o             TEXT,
    year_o              TEXT,
    url_o               TEXT,
    ref_o               TEXT,
    type                TEXT        CHECK (type IN ('replication', 'reproduction')),
    outcome             TEXT        CHECK (outcome IN (
                                        'success', 'failure', 'mixed',
                                        'uninformative', 'descriptive')),
    outcome_quote       TEXT,
    out_quote_source    TEXT,

    validation_status   TEXT        NOT NULL DEFAULT 'unvalidated'
                                    CHECK (validation_status IN (
                                        'unvalidated', 'validation_inprogress',
                                        'validated', 'need_review')),
    is_tiebreaker       BOOLEAN     NOT NULL DEFAULT FALSE,

    validator_1         JSONB,
    validator_2         JSONB,
    llm_validator       JSONB,

    final_doi_o         TEXT,
    final_study_o       TEXT,
    final_outcome       TEXT,
    final_type          TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS validation_queue (
    queue_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id           UUID        NOT NULL REFERENCES unvalidated(record_id),
    validator_slot      TEXT        NOT NULL
                                    CHECK (validator_slot IN ('human_1', 'human_2', 'llm')),

    is_shown            BOOLEAN     NOT NULL DEFAULT FALSE,
    is_validated        BOOLEAN     NOT NULL DEFAULT FALSE,

    validator_id        INTEGER     REFERENCES validators(id),
    validator_name      TEXT,

    type_check          TEXT        CHECK (type_check     IN ('correct', 'incorrect')),
    original_check      TEXT        CHECK (original_check IN ('correct', 'incorrect')),
    outcome_check       TEXT        CHECK (outcome_check  IN ('correct', 'incorrect')),

    corrected_doi_o     TEXT,
    corrected_study_o   TEXT,
    corrected_outcome   TEXT,
    corrected_type      TEXT,

    additional_checks   JSONB,
    validator_notes     TEXT,
    points              INTEGER     NOT NULL DEFAULT 0,
    shown_at            TIMESTAMPTZ,
    validated_at        TIMESTAMPTZ,

    UNIQUE (record_id, validator_slot)
);

CREATE TABLE IF NOT EXISTS validated (
    validated_record_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id           UUID        NOT NULL REFERENCES unvalidated(record_id),

    doi_r               TEXT        NOT NULL,
    study_r             TEXT,
    year_r              TEXT,
    url_r               TEXT,
    ref_r               TEXT,
    abstract_r          TEXT,

    doi_o               TEXT,
    study_o             TEXT,
    year_o              TEXT,
    url_o               TEXT,
    ref_o               TEXT,

    type                TEXT,
    outcome             TEXT,
    outcome_quote       TEXT,
    out_quote_source    TEXT,

    validated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (doi_r, study_r, doi_o, study_o)
);

CREATE TABLE IF NOT EXISTS record_metadata (
    metadata_id             UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id               UUID    NOT NULL UNIQUE REFERENCES unvalidated(record_id),

    pair_id                 TEXT,
    filter_status           TEXT,
    filter_method           TEXT,
    filter_evidence         TEXT,
    filter_confidence       TEXT,
    original_match_type     TEXT,
    original_match_confidence TEXT,
    link_method             TEXT,
    link_evidence           TEXT,
    link_confidence         TEXT,
    link_llm_model          TEXT,
    outcome_confidence      TEXT,
    authors_r               TEXT,
    authors_o               TEXT,
    journal_r               TEXT,
    openalex_id_r           TEXT,
    source                  TEXT,
    original_rank           INTEGER,
    n_originals             INTEGER,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- [ ] **Step 2: Update requirements.txt**

Replace the entire file:

```
fastapi>=0.110
uvicorn[standard]>=0.27
pydantic[email]>=2.5
psycopg2-binary>=2.9
python-dotenv>=1.0
google-generativeai>=0.8
apscheduler>=3.10
requests>=2.31
pandas>=2.0
```

- [ ] **Step 3: Write .env.example**

```
DATABASE_URL=postgresql://user:password@host:5432/dbname
GEMINI_API_KEY=your_gemini_api_key_here
GITHUB_TOKEN=your_github_fine_grained_pat_here
GITHUB_REPO=forrtproject/flora-extractor
GITHUB_BRANCH=feature/extract
```

- [ ] **Step 4: Install new dependencies**

```
pip install -r requirements.txt
```

Expected: no errors. `google-generativeai`, `apscheduler`, `requests` install successfully.

---

## Task 2: LLM Validator

**Files:**
- Create: `llm_validator.py`
- Create: `tests/__init__.py`
- Create: `tests/test_llm_validator.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/__init__.py` (empty):
```python
```

Create `tests/test_llm_validator.py`:
```python
import json
import pytest
from unittest.mock import patch, MagicMock


SAMPLE_RECORD = {
    "record_id": "abc-123",
    "doi_r": "10.1000/replication",
    "abstract_r": "We attempted to replicate Smith et al. (2020). Our results failed to replicate the original finding.",
    "doi_o": "10.1000/original",
    "study_o": "Original Study Title",
    "year_o": "2020",
    "type": "replication",
    "outcome": "success",
    "outcome_quote": "We successfully replicated the finding.",
}


def test_run_llm_validation_returns_correct_keys():
    """run_llm_validation returns a dict with all required keys."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "correct",
        "original_check": "correct",
        "outcome_check": "incorrect",
        "corrected_outcome": "failure",
        "corrected_doi_o": None,
        "corrected_type": None,
        "notes": "Abstract says failed to replicate but outcome coded as success",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert result["type_check"] in ("correct", "incorrect")
    assert result["original_check"] in ("correct", "incorrect")
    assert result["outcome_check"] in ("correct", "incorrect")
    assert result["context"] == "sanity_check"
    assert "model" in result
    assert "validated_at" in result
    assert "vote_score" in result


def test_run_llm_validation_tiebreaker_context():
    """context field is stored correctly for tiebreaker calls."""
    from llm_validator import run_llm_validation
    mock_response_text = json.dumps({
        "type_check": "correct",
        "original_check": "correct",
        "outcome_check": "correct",
        "corrected_outcome": None,
        "corrected_doi_o": None,
        "corrected_type": None,
        "notes": "",
    })
    with patch("llm_validator._call_gemini", return_value=mock_response_text):
        result = run_llm_validation(SAMPLE_RECORD, context="tiebreaker")

    assert result["context"] == "tiebreaker"


def test_run_llm_validation_handles_api_error():
    """API errors are caught; result has error key and no type_check."""
    from llm_validator import run_llm_validation
    with patch("llm_validator._call_gemini", side_effect=Exception("API timeout")):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert "error" in result
    assert result["context"] == "sanity_check"


def test_run_llm_validation_handles_malformed_json():
    """Malformed JSON from LLM is caught; result has error key."""
    from llm_validator import run_llm_validation
    with patch("llm_validator._call_gemini", return_value="not valid json {{"):
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert "error" in result


def test_run_llm_validation_retries_once_on_failure():
    """_call_gemini is called twice when first call fails, then succeeds."""
    from llm_validator import run_llm_validation
    good_response = json.dumps({
        "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
        "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None, "notes": "",
    })
    with patch("llm_validator._call_gemini", side_effect=[Exception("transient"), good_response]) as mock_call:
        result = run_llm_validation(SAMPLE_RECORD, context="sanity_check")

    assert mock_call.call_count == 2
    assert "error" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_llm_validator.py -v
```

Expected: `ModuleNotFoundError: No module named 'llm_validator'`

- [ ] **Step 3: Implement llm_validator.py**

```python
import json
import os
import re
from datetime import datetime, timezone

import google.generativeai as genai

_LLM_VOTE_SCORE = 15
_MODEL_NAME = "gemini-2.0-flash"

_PROMPT_TEMPLATE = """You are a research quality checker for a database of replication studies.

Given the following replication paper data, check whether the extracted metadata is accurate.
Answer ONLY based on the abstract and provided metadata — do not use external knowledge.
Default to "correct" when uncertain.

--- REPLICATION PAPER ---
Abstract: {abstract_r}

--- EXTRACTED METADATA ---
Type: {type}
Original study DOI: {doi_o}
Original study title: {study_o}
Original study year: {year_o}
Outcome category: {outcome}
Outcome quote: {outcome_quote}

--- YOUR TASK ---
Return a JSON object with exactly these keys:
- "type_check": "correct" or "incorrect" (is the type replication/reproduction accurate?)
- "original_check": "correct" or "incorrect" (does the original study match what the abstract describes?)
- "outcome_check": "correct" or "incorrect" (does the outcome category match the abstract?)
- "corrected_outcome": the correct outcome string if outcome_check is "incorrect", else null
- "corrected_doi_o": corrected DOI string if you can identify a different original, else null
- "corrected_type": "replication" or "reproduction" if type_check is "incorrect", else null
- "notes": one sentence of reasoning (max 200 chars)

Return ONLY the JSON object, no prose, no markdown fences."""


def _call_gemini(prompt: str) -> str:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(_MODEL_NAME)
    response = model.generate_content(prompt)
    return response.text


def _parse_response(text: str) -> dict:
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    # Normalise: ensure all expected keys exist
    for key in ("corrected_outcome", "corrected_doi_o", "corrected_type"):
        if key not in parsed:
            parsed[key] = None
    return parsed


def run_llm_validation(record: dict, context: str) -> dict:
    """
    Call Gemini Flash to validate a record.

    context: "sanity_check" | "tiebreaker"

    Returns a dict suitable for storage in unvalidated.llm_validator JSONB.
    On error, returns {"error": "...", "context": context, ...}.
    """
    prompt = _PROMPT_TEMPLATE.format(
        abstract_r=record.get("abstract_r") or "(no abstract)",
        type=record.get("type") or "",
        doi_o=record.get("doi_o") or "",
        study_o=record.get("study_o") or "",
        year_o=record.get("year_o") or "",
        outcome=record.get("outcome") or "",
        outcome_quote=record.get("outcome_quote") or "",
    )

    last_error = None
    for attempt in range(2):
        try:
            raw = _call_gemini(prompt)
            parsed = _parse_response(raw)
            return {
                "model": _MODEL_NAME,
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "context": context,
                "vote_score": _LLM_VOTE_SCORE,
                "type_check": parsed.get("type_check", "correct"),
                "original_check": parsed.get("original_check", "correct"),
                "outcome_check": parsed.get("outcome_check", "correct"),
                "corrected_outcome": parsed.get("corrected_outcome"),
                "corrected_doi_o": parsed.get("corrected_doi_o"),
                "corrected_type": parsed.get("corrected_type"),
                "notes": str(parsed.get("notes") or "")[:200],
            }
        except Exception as exc:
            last_error = str(exc)

    return {
        "model": _MODEL_NAME,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "context": context,
        "vote_score": _LLM_VOTE_SCORE,
        "error": last_error,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/test_llm_validator.py -v
```

Expected: all 5 tests PASS.

---

## Task 3: Consensus Engine

**Files:**
- Create: `consensus_engine.py`
- Create: `tests/test_consensus_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_consensus_engine.py`:
```python
import json
import pytest
from unittest.mock import MagicMock, patch, call


def _make_cursor(human_rows, unvalidated_row):
    """Build a mock psycopg2 RealDictCursor for consensus engine tests."""
    cur = MagicMock()
    # fetchall() for validation_queue returns human_rows
    # fetchone() for unvalidated returns unvalidated_row
    cur.fetchall.return_value = human_rows
    cur.fetchone.return_value = unvalidated_row
    return cur


BASE_RECORD = {
    "record_id": "rec-001",
    "doi_r": "10.1000/rep", "study_r": "Rep Study", "year_r": "2022",
    "url_r": "", "ref_r": "", "abstract_r": "We replicated X.",
    "doi_o": "10.1000/orig", "study_o": "Orig Study", "year_o": "2018",
    "url_o": "https://doi.org/10.1000/orig", "ref_o": "",
    "type": "replication", "outcome": "success",
    "outcome_quote": "We replicated.", "out_quote_source": "abstract",
    "validation_status": "validation_inprogress",
}

H1_AGREE = {
    "validator_slot": "human_1", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H2_AGREE = {
    "validator_slot": "human_2", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H1_DISAGREE = {
    "validator_slot": "human_1", "type_check": "correct",
    "original_check": "correct", "outcome_check": "correct",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": None, "corrected_type": None,
}
H2_DISAGREE = {
    "validator_slot": "human_2", "type_check": "correct",
    "original_check": "correct", "outcome_check": "incorrect",
    "corrected_doi_o": None, "corrected_study_o": None,
    "corrected_outcome": "failure", "corrected_type": None,
}

LLM_AGREE_ALL = {
    "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
    "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None,
    "context": "sanity_check", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_AGREE_H1 = {
    "type_check": "correct", "original_check": "correct", "outcome_check": "correct",
    "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": None,
    "context": "tiebreaker", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_AGREE_H2 = {
    "type_check": "correct", "original_check": "correct", "outcome_check": "incorrect",
    "corrected_outcome": "failure", "corrected_doi_o": None, "corrected_type": None,
    "context": "tiebreaker", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_3WAY = {
    "type_check": "incorrect", "original_check": "correct", "outcome_check": "correct",
    "corrected_outcome": None, "corrected_doi_o": None, "corrected_type": "reproduction",
    "context": "tiebreaker", "model": "gemini-2.0-flash", "vote_score": 15,
    "validated_at": "2026-05-14T00:00:00+00:00", "notes": "",
}
LLM_ERROR = {"error": "API timeout", "context": "sanity_check", "vote_score": 15,
             "model": "gemini-2.0-flash", "validated_at": "2026-05-14T00:00:00+00:00"}


def test_returns_early_when_only_one_human(mock_execute=None):
    """evaluate_consensus does nothing when only one human slot is complete."""
    from consensus_engine import evaluate_consensus
    cur = MagicMock()
    cur.fetchall.return_value = [H1_AGREE]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()


def test_both_agree_no_corrections_sets_validated():
    """Both humans agree with no corrections → validated status."""
    from consensus_engine import evaluate_consensus
    cur = MagicMock()
    cur.fetchall.return_value = [H1_AGREE, H2_AGREE]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_ALL):
        evaluate_consensus(cur, "rec-001")
    # Check that UPDATE was called with 'validated'
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str
    assert "need_review" not in calls_str


def test_both_agree_llm_errors_still_validates():
    """LLM error during sanity check does not block validation."""
    from consensus_engine import evaluate_consensus
    cur = MagicMock()
    cur.fetchall.return_value = [H1_AGREE, H2_AGREE]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation", return_value=LLM_ERROR):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str


def test_both_agree_different_corrections_sets_need_review():
    """Both humans disagree on corrections → need_review, no LLM call."""
    from consensus_engine import evaluate_consensus
    h1 = {**H1_AGREE, "corrected_doi_o": "10.1000/a"}
    h2 = {**H2_AGREE, "corrected_doi_o": "10.1000/b"}
    cur = MagicMock()
    cur.fetchall.return_value = [h1, h2]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation") as mock_llm:
        evaluate_consensus(cur, "rec-001")
    mock_llm.assert_not_called()
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str


def test_humans_disagree_llm_agrees_h1_sets_validated():
    """Humans disagree; LLM matches H1 → validated with H1 verdict."""
    from consensus_engine import evaluate_consensus
    cur = MagicMock()
    cur.fetchall.return_value = [H1_DISAGREE, H2_DISAGREE]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_H1):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str
    assert "is_tiebreaker" in calls_str or "TRUE" in calls_str


def test_humans_disagree_llm_agrees_h2_sets_validated():
    """Humans disagree; LLM matches H2 → validated with H2 verdict."""
    from consensus_engine import evaluate_consensus
    cur = MagicMock()
    cur.fetchall.return_value = [H1_DISAGREE, H2_DISAGREE]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation", return_value=LLM_AGREE_H2):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "validated" in calls_str


def test_humans_disagree_3way_split_sets_need_review():
    """3-way split → need_review."""
    from consensus_engine import evaluate_consensus
    cur = MagicMock()
    cur.fetchall.return_value = [H1_DISAGREE, H2_DISAGREE]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation", return_value=LLM_3WAY):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str


def test_humans_disagree_llm_error_sets_need_review():
    """LLM error during tiebreaker → need_review."""
    from consensus_engine import evaluate_consensus
    cur = MagicMock()
    cur.fetchall.return_value = [H1_DISAGREE, H2_DISAGREE]
    cur.fetchone.return_value = BASE_RECORD
    with patch("consensus_engine.run_llm_validation", return_value=LLM_ERROR):
        evaluate_consensus(cur, "rec-001")
    calls_str = str(cur.execute.call_args_list)
    assert "need_review" in calls_str
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_consensus_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'consensus_engine'`

- [ ] **Step 3: Implement consensus_engine.py**

```python
import json
from datetime import datetime, timezone
from typing import Optional

from llm_validator import run_llm_validation


_CORRECTION_FIELDS = [
    "corrected_doi_o", "corrected_study_o", "corrected_outcome", "corrected_type"
]
_CHECK_FIELDS = ["type_check", "original_check", "outcome_check"]


def _checks_agree(h1: dict, h2: dict) -> bool:
    return all(h1.get(f) == h2.get(f) for f in _CHECK_FIELDS)


def _corrections_agree(h1: dict, h2: dict) -> bool:
    return all(h1.get(f) == h2.get(f) for f in _CORRECTION_FIELDS)


def _llm_matches(llm: dict, human: dict) -> bool:
    if llm.get("error"):
        return False
    return all(llm.get(f) == human.get(f) for f in _CHECK_FIELDS)


def _resolve_final(record: dict, winner: dict) -> dict:
    return {
        "doi_o":   winner.get("corrected_doi_o")   or record.get("doi_o")   or "",
        "study_o": winner.get("corrected_study_o") or record.get("study_o") or "",
        "outcome": winner.get("corrected_outcome") or record.get("outcome") or "",
        "type":    winner.get("corrected_type")    or record.get("type")    or "",
    }


def _update_status(cur, record_id: str, status: str, is_tiebreaker: bool,
                   final: Optional[dict], llm_summary: Optional[dict]) -> None:
    parts = [
        "validation_status = %s",
        "is_tiebreaker = %s",
        "updated_at = NOW()",
    ]
    params: list = [status, is_tiebreaker]

    if final:
        parts += [
            "final_doi_o = %s", "final_study_o = %s",
            "final_outcome = %s", "final_type = %s",
        ]
        params += [final["doi_o"], final["study_o"], final["outcome"], final["type"]]

    if llm_summary is not None:
        parts.append("llm_validator = %s")
        params.append(json.dumps(llm_summary))

    params.append(record_id)
    cur.execute(
        f"UPDATE unvalidated SET {', '.join(parts)} WHERE record_id = %s",
        params,
    )


def _insert_validated(cur, record: dict, final: dict) -> None:
    doi_o = final["doi_o"]
    url_o = f"https://doi.org/{doi_o}" if doi_o else ""
    cur.execute(
        """
        INSERT INTO validated (
            record_id, doi_r, study_r, year_r, url_r, ref_r, abstract_r,
            doi_o, study_o, year_o, url_o, ref_o,
            type, outcome, outcome_quote, out_quote_source
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (doi_r, study_r, doi_o, study_o) DO NOTHING
        """,
        (
            record["record_id"],
            record.get("doi_r"), record.get("study_r"), record.get("year_r"),
            record.get("url_r"), record.get("ref_r"), record.get("abstract_r"),
            doi_o, final["study_o"], record.get("year_o"), url_o,
            record.get("ref_o"), final["type"], final["outcome"],
            record.get("outcome_quote"), record.get("out_quote_source"),
        ),
    )


def evaluate_consensus(cur, record_id: str) -> None:
    """
    Called inside POST /api/judge after a human submits.
    If both human slots are complete, runs consensus logic and updates the DB.
    """
    cur.execute(
        """
        SELECT validator_slot, type_check, original_check, outcome_check,
               corrected_doi_o, corrected_study_o, corrected_outcome, corrected_type
        FROM validation_queue
        WHERE record_id = %s
          AND validator_slot IN ('human_1', 'human_2')
          AND is_validated = TRUE
        """,
        (record_id,),
    )
    rows = {r["validator_slot"]: dict(r) for r in cur.fetchall()}

    if len(rows) < 2:
        return

    h1 = rows["human_1"]
    h2 = rows["human_2"]

    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
    record = dict(cur.fetchone())

    checks_ok      = _checks_agree(h1, h2)
    corrections_ok = _corrections_agree(h1, h2)

    if checks_ok and corrections_ok:
        # Both humans fully agree — LLM sanity check (humans always win regardless of LLM result)
        llm = run_llm_validation(record, context="sanity_check")
        final = _resolve_final(record, h1)
        _update_status(cur, record_id, "validated", False, final, llm)
        _insert_validated(cur, record, final)

    elif checks_ok and not corrections_ok:
        # Checks agree but corrections differ → need_review, no LLM call needed
        _update_status(cur, record_id, "need_review", False, None, None)

    else:
        # Checks disagree → LLM tiebreaker
        llm = run_llm_validation(record, context="tiebreaker")
        agrees_h1 = _llm_matches(llm, h1)
        agrees_h2 = _llm_matches(llm, h2)

        if llm.get("error") or (agrees_h1 == agrees_h2):
            _update_status(cur, record_id, "need_review", True, None, llm)
        elif agrees_h1:
            final = _resolve_final(record, h1)
            _update_status(cur, record_id, "validated", True, final, llm)
            _insert_validated(cur, record, final)
        else:
            final = _resolve_final(record, h2)
            _update_status(cur, record_id, "validated", True, final, llm)
            _insert_validated(cur, record, final)
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/test_consensus_engine.py -v
```

Expected: all 8 tests PASS.

---

## Task 4: Update csv_to_db.py

**Files:**
- Modify: `csv_to_db.py` (switch from Supabase client to psycopg2; add `pair_id` to `unvalidated`)

- [ ] **Step 1: Replace csv_to_db.py entirely**

```python
"""
csv_to_db.py — Import resolved rows from extracted.csv into the validation database.

Only rows where filter_status is 'replication' or 'reproduction' AND link_method is in
the resolved set are imported. Safe to re-run; existing pair_ids are skipped.

Usage:
    python csv_to_db.py --input data/extracted_latest.csv
    python csv_to_db.py --input data/extracted_latest.csv --dry-run

Required environment variable:
    DATABASE_URL — postgresql://user:password@host:5432/dbname
"""
import argparse
import os
import uuid
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

_RESOLVED_METHODS = {"author_year_match", "llm_abstract", "llm_fulltext"}
_RESOLVED_STATUSES = {"replication", "reproduction"}
_VALIDATOR_SLOTS = ("human_1", "human_2", "llm")


def _s(val) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return str(val).strip()


def _int_or_none(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _derive_url_o(doi_o: str) -> str:
    doi_o = _s(doi_o)
    return f"https://doi.org/{doi_o}" if doi_o else ""


def import_rows(cur, resolved: pd.DataFrame) -> tuple[int, int]:
    """
    Insert resolved rows into unvalidated, record_metadata, validation_queue.
    Returns (inserted, skipped_dup) counts.
    """
    cur.execute("SELECT pair_id FROM record_metadata WHERE pair_id IS NOT NULL")
    existing = {r["pair_id"] for r in cur.fetchall() if r.get("pair_id")}

    inserted = skipped_dup = 0

    for _, row in resolved.iterrows():
        pair_id = _s(row.get("pair_id"))
        if pair_id and pair_id in existing:
            skipped_dup += 1
            continue

        record_id = str(uuid.uuid4())
        doi_o = _s(row.get("doi_o"))

        cur.execute(
            """
            INSERT INTO unvalidated (
                record_id, pair_id,
                doi_r, study_r, year_r, url_r, ref_r, abstract_r,
                doi_o, study_o, year_o, url_o, ref_o,
                type, outcome, outcome_quote, out_quote_source,
                validation_status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, 'unvalidated'
            )
            """,
            (
                record_id, pair_id,
                _s(row.get("doi_r")), _s(row.get("title_r")),
                _s(row.get("year_r")), _s(row.get("url_r")),
                _s(row.get("ref_r")), _s(row.get("abstract_r")),
                doi_o, _s(row.get("title_o")),
                _s(row.get("year_o")), _derive_url_o(doi_o),
                _s(row.get("ref_o")), _s(row.get("type")),
                _s(row.get("outcome")), _s(row.get("outcome_phrase")),
                _s(row.get("out_quote_source")),
            ),
        )

        cur.execute(
            """
            INSERT INTO record_metadata (
                record_id, pair_id,
                filter_status, filter_method, filter_evidence, filter_confidence,
                original_match_type, original_match_confidence,
                link_method, link_evidence, link_confidence, link_llm_model,
                outcome_confidence, authors_r, authors_o, journal_r,
                openalex_id_r, source, original_rank, n_originals
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                record_id, pair_id,
                _s(row.get("filter_status")), _s(row.get("filter_method")),
                _s(row.get("filter_evidence")), _s(row.get("filter_confidence")),
                _s(row.get("original_match_type")),
                _s(row.get("original_match_confidence")),
                _s(row.get("link_method")), _s(row.get("link_evidence")),
                _s(row.get("link_confidence")), _s(row.get("link_llm_model")),
                _s(row.get("outcome_confidence")),
                _s(row.get("authors_r")), _s(row.get("authors_o")),
                _s(row.get("journal_r")), _s(row.get("openalex_id_r")),
                _s(row.get("source")),
                _int_or_none(row.get("original_rank")),
                _int_or_none(row.get("n_originals")),
            ),
        )

        for slot in _VALIDATOR_SLOTS:
            cur.execute(
                """
                INSERT INTO validation_queue (record_id, validator_slot, is_shown, is_validated)
                VALUES (%s, %s, FALSE, FALSE)
                ON CONFLICT (record_id, validator_slot) DO NOTHING
                """,
                (record_id, slot),
            )

        inserted += 1
        if inserted % 25 == 0:
            print(f"  … {inserted} records imported")

    return inserted, skipped_dup


def run_import(csv_path: Path, dry_run: bool = False) -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    print(f"Reading {csv_path} …")
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")

    mask = (
        df["filter_status"].isin(_RESOLVED_STATUSES)
        & df["link_method"].isin(_RESOLVED_METHODS)
    )
    resolved = df[mask].copy()

    print(f"  Total rows:        {len(df)}")
    print(f"  Resolved (import): {len(resolved)}")
    print(f"  Skipping:          {len(df) - len(resolved)}")

    if resolved.empty:
        print("Nothing to import.")
        return

    if dry_run:
        print("[dry-run] Would import:")
        print(resolved[["doi_r", "doi_o", "filter_status", "link_method"]].to_string())
        return

    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        inserted, skipped = import_rows(cur, resolved)
        conn.commit()
        print(f"\nDone. Inserted: {inserted}  |  Skipped (dup): {skipped}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/extracted_latest.csv"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Not found: {args.input}")
    run_import(args.input, dry_run=args.dry_run)
```

- [ ] **Step 2: Verify csv_to_db.py is importable**

```
python -c "import csv_to_db; print('OK')"
```

Expected: `OK`

---

## Task 5: Rewrite app.py — DB Init

**Files:**
- Modify: `app.py` (replace `init_db()`, `migrate_db()`, constants)

- [ ] **Step 1: Replace the top of app.py (imports through `migrate_db` call)**

Replace lines 1–119 with:

```python
import csv
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import psycopg2.errors
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

load_dotenv()
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "extracted.csv"
ONBOARDING_PATH = ROOT / "onboarding.json"
OA_CACHE_PATH = ROOT / "oa_cache.json"

VALID_TYPES = {"replication", "reproduction", "not_validation", "skip"}
CONFIRMING_TYPES = {"replication", "reproduction"}

DATABASE_URL = os.environ["DATABASE_URL"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9._\-]{2,32}$")

app = FastAPI(title="Flora Validator")


@contextmanager
def db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    schema = (ROOT / "db_schema.sql").read_text()
    with db() as cur:
        cur.execute(schema)
        cur.execute("SELECT COUNT(*) AS n FROM unvalidated")
        if cur.fetchone()["n"] == 0 and CSV_PATH.exists():
            print("Seeding DB from extracted.csv …")
            from csv_to_db import import_rows
            import pandas as pd
            df = pd.read_csv(CSV_PATH, dtype=str, encoding="utf-8-sig").fillna("")
            from csv_to_db import _RESOLVED_STATUSES, _RESOLVED_METHODS
            mask = (
                df["filter_status"].isin(_RESOLVED_STATUSES)
                & df["link_method"].isin(_RESOLVED_METHODS)
            )
            inserted, _ = import_rows(cur, df[mask].copy())
            print(f"Seeded {inserted} records.")


init_db()
```

- [ ] **Step 2: Verify app still starts**

```
python -c "import app; print('init OK')"
```

Expected: `init OK` (tables created if they don't exist yet).

---

## Task 6: Update Login + Onboarding Endpoints

**Files:**
- Modify: `app.py` — replace `login`, `onboarding_complete` functions

- [ ] **Step 1: Replace the helper functions and login endpoint**

Find and replace the `load_onboarding`, `oa_url_for`, `with_oa`, `LoginRequest`, `JudgeRequest`, `HANDLE_RE`, `points_for`, `rank_for` section and the `@app.post("/api/login")` handler. Replace with:

```python
def load_onboarding():
    with open(ONBOARDING_PATH) as f:
        return json.load(f)["pairs"]


_OA_CACHE: dict | None = None


def oa_url_for(doi: str | None) -> str | None:
    global _OA_CACHE
    if _OA_CACHE is None:
        try:
            _OA_CACHE = json.loads(OA_CACHE_PATH.read_text())
        except FileNotFoundError:
            _OA_CACHE = {}
    if not doi:
        return None
    return (_OA_CACHE.get(doi.strip()) or {}).get("oa_url")


def with_oa(pair: dict) -> dict:
    pair["oa_url_r"] = oa_url_for(pair.get("doi_r"))
    pair["oa_url_o"] = oa_url_for(pair.get("doi_o"))
    return pair


class LoginRequest(BaseModel):
    handle: str
    code: str | None = None
    email: str | None = None


class JudgeRequest(BaseModel):
    coder_id: int
    pair_id: str
    type_judgement: str
    original_judgement: str | None = None
    outcome_judgement: str | None = None
    comment: str | None = None
    edited_abstract: str | None = None
    edited_outcome_quote: str | None = None
    hard_mode: bool = False
    hard_mode_entry: dict | None = None


def points_for(req: JudgeRequest) -> int:
    if req.type_judgement == "skip":
        return 0
    if req.hard_mode:
        pts = 25
        if req.comment and req.comment.strip():
            pts += 3
        return pts
    if req.type_judgement == "not_validation":
        return 10
    pts = 10
    if req.original_judgement:
        pts += 5
    if req.outcome_judgement:
        pts += 5
    if req.comment and req.comment.strip():
        pts += 3
    if (req.edited_abstract and req.edited_abstract.strip()) or (
        req.edited_outcome_quote and req.edited_outcome_quote.strip()
    ):
        pts += 2
    return pts


def rank_for(cur, points: int) -> int:
    cur.execute(
        "SELECT COUNT(*) + 1 AS rank FROM validators WHERE total_points > %s",
        (points,),
    )
    return cur.fetchone()["rank"]


@app.post("/api/login")
def login(req: LoginRequest):
    handle = req.handle.strip()
    if not HANDLE_RE.match(handle):
        raise HTTPException(400, "Handle must be 2–32 chars: letters, digits, . _ -")

    use_email = bool(req.email and req.email.strip())
    use_code = bool(req.code and req.code.strip())
    if not use_email and not use_code:
        raise HTTPException(400, "Provide either an email or a personal code")

    if use_email:
        email = req.email.strip().lower()
        if not EMAIL_RE.match(email):
            raise HTTPException(400, "Invalid email address")
    else:
        code = req.code.strip()
        if len(code) < 4:
            raise HTTPException(400, "Code too short — fill in all four parts")

    with db() as cur:
        lookup_col = "email" if use_email else "code"
        lookup_val = email if use_email else code
        cur.execute(
            f"SELECT id, code, email, handle, onboarded_at FROM validators WHERE {lookup_col} = %s",
            (lookup_val,),
        )
        existing = cur.fetchone()
        if existing:
            if existing["handle"] != handle:
                method = "email" if use_email else "code"
                raise HTTPException(
                    400,
                    f"This {method} is already linked to handle '{existing['handle']}'. Use that handle.",
                )
            return {
                "coder_id": existing["id"],
                "code": existing["code"],
                "email": existing["email"],
                "handle": existing["handle"],
                "onboarded": bool(existing["onboarded_at"]),
            }
        cur.execute("SELECT 1 FROM validators WHERE handle = %s", (handle,))
        if cur.fetchone():
            raise HTTPException(400, "That handle is already taken.")
        if use_email:
            cur.execute(
                "INSERT INTO validators(email, handle, created_at) VALUES (%s, %s, %s) RETURNING id",
                (email, handle, datetime.now(timezone.utc).isoformat()),
            )
        else:
            cur.execute(
                "INSERT INTO validators(code, handle, created_at) VALUES (%s, %s, %s) RETURNING id",
                (code, handle, datetime.now(timezone.utc).isoformat()),
            )
        new_id = cur.fetchone()["id"]
        return {
            "coder_id": new_id,
            "code": req.code,
            "email": email if use_email else None,
            "handle": handle,
            "onboarded": False,
        }
```

- [ ] **Step 2: Replace onboarding_complete endpoint**

Find `@app.post("/api/onboarding/complete")` and replace its function body:

```python
@app.post("/api/onboarding/complete")
def onboarding_complete(req: OnboardingComplete):
    with db() as cur:
        cur.execute(
            "UPDATE validators SET onboarded_at = %s WHERE id = %s AND onboarded_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), req.coder_id),
        )
        if cur.rowcount == 0:
            cur.execute("SELECT onboarded_at FROM validators WHERE id = %s", (req.coder_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Validator not found")
        return {"onboarded": True}
```

---

## Task 7: Update next-pair Endpoint

**Files:**
- Modify: `app.py` — replace `@app.get("/api/next-pair")`

- [ ] **Step 1: Replace the next-pair handler**

Find `@app.get("/api/next-pair")` and replace its entire function:

```python
@app.get("/api/next-pair")
def next_pair(coder_id: int, mode: str = "normal"):
    if mode not in {"normal", "hard"}:
        raise HTTPException(400, "mode must be normal or hard")

    # hard mode = no abstract; normal mode = has abstract
    abstract_filter = (
        "(abstract_r IS NULL OR abstract_r = '')" if mode == "hard"
        else "abstract_r IS NOT NULL AND abstract_r != ''"
    )

    with db() as cur:
        cur.execute(
            f"""
            SELECT
                u.record_id, u.pair_id,
                u.doi_r, u.study_r AS title_r, u.year_r, u.url_r, u.ref_r, u.abstract_r,
                u.doi_o, u.study_o AS title_o, u.year_o, u.url_o, u.ref_o,
                u.type, u.outcome, u.outcome_quote AS outcome_phrase,
                u.out_quote_source,
                m.authors_r, m.authors_o, m.journal_r, m.link_evidence,
                (
                    SELECT COUNT(*) FROM validation_queue vq
                    WHERE vq.record_id = u.record_id
                      AND vq.validator_slot IN ('human_1', 'human_2')
                      AND vq.is_validated = TRUE
                ) AS judge_count
            FROM unvalidated u
            LEFT JOIN record_metadata m ON m.record_id = u.record_id
            WHERE u.validation_status IN ('unvalidated', 'validation_inprogress')
              AND {abstract_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM validation_queue vq
                  WHERE vq.record_id = u.record_id
                    AND vq.validator_id = %s
                    AND vq.is_validated = TRUE
              )
            ORDER BY judge_count ASC, RANDOM()
            LIMIT 1
            """,
            (coder_id,),
        )
        row = cur.fetchone()

        cur.execute(
            f"""
            SELECT COUNT(*) AS n FROM unvalidated
            WHERE {abstract_filter}
            """,
        )
        total = cur.fetchone()["n"]

        cur.execute(
            f"""
            SELECT COUNT(*) AS n FROM validation_queue vq
            JOIN unvalidated u ON u.record_id = vq.record_id
            WHERE vq.validator_id = %s
              AND vq.is_validated = TRUE
              AND {abstract_filter}
            """,
            (coder_id,),
        )
        done = cur.fetchone()["n"]

        if not row:
            return {"pair": None, "done": done, "total": total}

        pair = dict(row)
        judge_count = pair.pop("judge_count")
        pair = with_oa(pair)
        return {"pair": pair, "judge_count": judge_count, "done": done, "total": total}
```

---

## Task 8: Update judge Endpoint

**Files:**
- Modify: `app.py` — replace `@app.post("/api/judge")`

- [ ] **Step 1: Replace the judge handler**

Find `@app.post("/api/judge")` and replace its entire function:

```python
@app.post("/api/judge")
def judge(req: JudgeRequest):
    if req.type_judgement not in VALID_TYPES:
        raise HTTPException(400, "Invalid type judgement")

    pts = points_for(req)

    with db() as cur:
        # Resolve record
        cur.execute(
            "SELECT record_id, type, outcome, doi_o FROM unvalidated WHERE pair_id = %s",
            (req.pair_id,),
        )
        record_row = cur.fetchone()
        if not record_row:
            raise HTTPException(404, "Pair not found")
        record_id = record_row["record_id"]

        # Handle skip — no queue entry, just increment skip count and return
        if req.type_judgement == "skip":
            cur.execute(
                "UPDATE validators SET skipped_count = skipped_count + 1 WHERE id = %s",
                (req.coder_id,),
            )
            cur.execute(
                "SELECT COALESCE(SUM(total_points), 0) AS total FROM validators WHERE id = %s",
                (req.coder_id,),
            )
            total = cur.fetchone()["total"]
            return {"points_earned": 0, "total_points": total, "rank": rank_for(cur, total)}

        # Check for duplicate judgement
        cur.execute(
            """
            SELECT 1 FROM validation_queue
            WHERE record_id = %s AND validator_id = %s AND is_validated = TRUE
            """,
            (record_id, req.coder_id),
        )
        if cur.fetchone():
            raise HTTPException(400, "Already judged this pair")

        # Assign next available human slot
        cur.execute(
            """
            SELECT queue_id, validator_slot FROM validation_queue
            WHERE record_id = %s
              AND validator_slot IN ('human_1', 'human_2')
              AND is_validated = FALSE
            ORDER BY validator_slot
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (record_id,),
        )
        slot_row = cur.fetchone()
        if not slot_row:
            raise HTTPException(400, "This pair already has two validators")

        queue_id = slot_row["queue_id"]
        validator_slot = slot_row["validator_slot"]

        # Map old judgement fields to new check fields
        extracted_type = record_row["type"] or ""
        extracted_outcome = record_row["outcome"] or ""

        if req.type_judgement == "not_validation":
            type_check = "incorrect"
            corrected_type = None
        elif req.type_judgement == extracted_type:
            type_check = "correct"
            corrected_type = None
        else:
            type_check = "incorrect"
            corrected_type = req.type_judgement

        original_check = None
        if req.original_judgement in ("correct", "wrong", "unsure"):
            original_check = "correct" if req.original_judgement in ("correct", "unsure") else "incorrect"

        outcome_check = None
        if req.outcome_judgement in ("correct", "wrong", "unsure"):
            outcome_check = "correct" if req.outcome_judgement in ("correct", "unsure") else "incorrect"

        corrected_doi_o = None
        corrected_study_o = None
        corrected_outcome = None

        # Hard mode entries become corrections
        if req.hard_mode and req.hard_mode_entry:
            entry = req.hard_mode_entry
            if entry.get("doi_o") and entry.get("doi_o") != record_row["doi_o"]:
                corrected_doi_o = entry["doi_o"]
            if entry.get("title_o"):
                corrected_study_o = entry["title_o"]
            if entry.get("outcome") and entry.get("outcome") != extracted_outcome:
                corrected_outcome = entry["outcome"]

        additional_checks = {}
        if req.original_judgement == "unsure":
            additional_checks["was_unsure_original"] = True
        if req.outcome_judgement == "unsure":
            additional_checks["was_unsure_outcome"] = True
        if req.type_judgement == "not_validation":
            additional_checks["not_validation"] = True

        # Update the queue slot
        cur.execute(
            """
            UPDATE validation_queue SET
                is_shown = TRUE, is_validated = TRUE,
                validator_id = %s, validator_name = %s,
                type_check = %s, original_check = %s, outcome_check = %s,
                corrected_doi_o = %s, corrected_study_o = %s,
                corrected_outcome = %s, corrected_type = %s,
                additional_checks = %s,
                validator_notes = %s, points = %s,
                shown_at = NOW(), validated_at = NOW()
            WHERE queue_id = %s
            """,
            (
                req.coder_id, None,  # validator_name resolved below
                type_check, original_check, outcome_check,
                corrected_doi_o, corrected_study_o,
                corrected_outcome, corrected_type,
                json.dumps(additional_checks) if additional_checks else None,
                req.comment, pts,
                queue_id,
            ),
        )

        # Fetch handle for validator_name
        cur.execute("SELECT handle FROM validators WHERE id = %s", (req.coder_id,))
        handle_row = cur.fetchone()
        validator_name = handle_row["handle"] if handle_row else str(req.coder_id)
        cur.execute(
            "UPDATE validation_queue SET validator_name = %s WHERE queue_id = %s",
            (validator_name, queue_id),
        )

        # Build JSONB summary for unvalidated
        cur.execute("SELECT vote_score FROM validators WHERE id = %s", (req.coder_id,))
        vote_score_row = cur.fetchone()
        vote_score = vote_score_row["vote_score"] if vote_score_row else 10

        summary = {
            "validator_id": req.coder_id,
            "validator_name": validator_name,
            "vote_score": vote_score,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "type_check": type_check,
            "original_check": original_check,
            "outcome_check": outcome_check,
            "corrected_doi_o": corrected_doi_o,
            "corrected_study_o": corrected_study_o,
            "corrected_outcome": corrected_outcome,
            "corrected_type": corrected_type,
            "notes": req.comment or "",
        }

        jsonb_col = "validator_1" if validator_slot == "human_1" else "validator_2"
        cur.execute(
            f"""
            UPDATE unvalidated SET
                {jsonb_col} = %s,
                validation_status = 'validation_inprogress',
                updated_at = NOW()
            WHERE record_id = %s
            """,
            (json.dumps(summary), record_id),
        )

        # Update validator stats
        cur.execute(
            """
            UPDATE validators SET
                total_judgements = total_judgements + 1,
                total_points = total_points + %s
            WHERE id = %s
            """,
            (pts, req.coder_id),
        )

        # Run consensus engine
        from consensus_engine import evaluate_consensus
        evaluate_consensus(cur, record_id)

        cur.execute(
            "SELECT total_points FROM validators WHERE id = %s", (req.coder_id,)
        )
        total = cur.fetchone()["total_points"]
        return {"points_earned": pts, "total_points": total, "rank": rank_for(cur, total)}
```

---

## Task 9: Update Stats + Leaderboard Endpoints

**Files:**
- Modify: `app.py` — replace `stats` and `leaderboard` functions

- [ ] **Step 1: Replace stats endpoint**

Find `@app.get("/api/stats")` and replace its entire function:

```python
@app.get("/api/stats")
def stats(coder_id: int):
    with db() as cur:
        cur.execute(
            """
            SELECT total_points AS points, total_judgements AS done,
                   skipped_count AS skipped
            FROM validators WHERE id = %s
            """,
            (coder_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Validator not found")

        cur.execute("SELECT COUNT(*) AS n FROM unvalidated WHERE abstract_r != ''")
        normal_total = cur.fetchone()["n"]
        cur.execute(
            "SELECT COUNT(*) AS n FROM unvalidated WHERE abstract_r IS NULL OR abstract_r = ''"
        )
        hard_total = cur.fetchone()["n"]

        return {
            "done": row["done"],
            "points": row["points"],
            "skipped": row["skipped"],
            "total": normal_total + hard_total,
            "normal_total": normal_total,
            "hard_total": hard_total,
            "rank": rank_for(cur, row["points"]),
        }
```

- [ ] **Step 2: Replace leaderboard endpoint**

Find `@app.get("/api/leaderboard")` and replace its entire function:

```python
@app.get("/api/leaderboard")
def leaderboard():
    with db() as cur:
        cur.execute(
            """
            SELECT handle AS name, total_points AS points, total_judgements AS pairs
            FROM validators
            ORDER BY total_points DESC, total_judgements DESC, handle ASC
            """
        )
        return [dict(r) for r in cur.fetchall()]
```

---

## Task 10: Nightly CSV Sync

**Files:**
- Create: `sync_csv.py`
- Create: `tests/test_sync_csv.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sync_csv.py`:
```python
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def test_fetch_csv_saves_dated_and_latest(tmp_path):
    """fetch_csv saves both a dated file and extracted_latest.csv."""
    from sync_csv import fetch_csv

    fake_csv_content = b"pair_id,doi_r\nabc,10.1000/x\n"

    with patch("sync_csv.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_csv_content
        mock_get.return_value = mock_response

        dated, latest = fetch_csv(data_dir=tmp_path, date_str="14.05.2026")

    assert dated.exists()
    assert latest.exists()
    assert dated.name == "extracted_14.05.2026.csv"
    assert latest.name == "extracted_latest.csv"
    assert dated.read_bytes() == fake_csv_content
    assert latest.read_bytes() == fake_csv_content


def test_fetch_csv_raises_on_http_error(tmp_path):
    """fetch_csv raises RuntimeError when GitHub returns non-200."""
    from sync_csv import fetch_csv

    with patch("sync_csv.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="404"):
            fetch_csv(data_dir=tmp_path, date_str="14.05.2026")


def test_sync_and_import_calls_run_import(tmp_path):
    """sync_and_import fetches CSV then calls run_import."""
    from sync_csv import sync_and_import

    fake_csv_content = b"pair_id,doi_r,filter_status,link_method\n"

    with patch("sync_csv.requests.get") as mock_get, \
         patch("sync_csv.run_import") as mock_import:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_csv_content
        mock_get.return_value = mock_response

        sync_and_import(data_dir=tmp_path)

    mock_import.assert_called_once()
    # The argument should be the dated CSV path
    call_args = mock_import.call_args
    assert "extracted_" in str(call_args[0][0])
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_sync_csv.py -v
```

Expected: `ModuleNotFoundError: No module named 'sync_csv'`

- [ ] **Step 3: Implement sync_csv.py**

```python
"""
sync_csv.py — Nightly GitHub fetch and DB import for extracted.csv.

Called by APScheduler at 02:00 AM daily. Can also be run manually:
    python sync_csv.py

Required environment variables:
    DATABASE_URL, GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_DATA_DIR = Path(__file__).parent / "data"


def fetch_csv(data_dir: Path, date_str: str | None = None) -> tuple[Path, Path]:
    """
    Download extracted.csv from GitHub and save to data_dir.
    Returns (dated_path, latest_path).
    """
    repo   = os.environ.get("GITHUB_REPO",   "forrtproject/flora-extractor")
    branch = os.environ.get("GITHUB_BRANCH", "feature/extract")
    token  = os.environ.get("GITHUB_TOKEN",  "")

    url = (
        f"https://raw.githubusercontent.com/{repo}/{branch}/data/extracted.csv"
    )
    headers = {"Authorization": f"token {token}"} if token else {}
    response = requests.get(url, headers=headers, timeout=60)

    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub fetch failed: {response.status_code} — {response.text[:200]}"
        )

    data_dir.mkdir(parents=True, exist_ok=True)

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")

    dated_path  = data_dir / f"extracted_{date_str}.csv"
    latest_path = data_dir / "extracted_latest.csv"

    dated_path.write_bytes(response.content)
    latest_path.write_bytes(response.content)

    return dated_path, latest_path


def sync_and_import(data_dir: Path = _DEFAULT_DATA_DIR) -> dict:
    """
    Full nightly sync: fetch CSV, save, import new rows.
    Returns stats dict.
    """
    print(f"[sync] Starting nightly CSV sync — {datetime.now(timezone.utc).isoformat()}")

    dated_path, _ = fetch_csv(data_dir)
    print(f"[sync] Saved to {dated_path}")

    from csv_to_db import run_import
    run_import(dated_path)

    print("[sync] Done.")
    return {"dated_path": str(dated_path)}


if __name__ == "__main__":
    sync_and_import()
```

- [ ] **Step 4: Run tests to verify they pass**

```
python -m pytest tests/test_sync_csv.py -v
```

Expected: all 3 tests PASS.

---

## Task 11: APScheduler Integration in app.py

**Files:**
- Modify: `app.py` — add scheduler startup after `init_db()` call

- [ ] **Step 1: Add scheduler import and startup**

In `app.py`, after the `init_db()` call (around line 60 in the new file), add:

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


def _nightly_sync():
    try:
        from sync_csv import sync_and_import
        sync_and_import()
    except Exception as exc:
        print(f"[scheduler] Nightly sync failed: {exc}")


_scheduler = BackgroundScheduler()
_scheduler.add_job(_nightly_sync, CronTrigger(hour=2, minute=0))
_scheduler.start()
```

- [ ] **Step 2: Verify scheduler starts with app**

```
python -c "import app; print('scheduler OK')"
```

Expected: `scheduler OK` with no errors.

---

## Task 12: Migration Script for Existing Deployments

**Files:**
- Create: `db_migrate.py`

- [ ] **Step 1: Write db_migrate.py**

```python
"""
db_migrate.py — Migrate an existing flora-validation database from the old
pairs/coders/judgements schema to the new validation schema.

Run ONCE on an existing deployment. Safe to inspect with --dry-run first.

Usage:
    python db_migrate.py --dry-run
    python db_migrate.py
"""
import argparse
import json
import os
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def _s(val) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return str(val).strip()


def migrate(dry_run: bool = False) -> None:
    db_url = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1. Check old tables exist
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN ('coders', 'pairs', 'judgements')"
    )
    old_tables = {r["table_name"] for r in cur.fetchall()}
    if not old_tables:
        print("No old tables found — nothing to migrate.")
        conn.close()
        return
    print(f"Found old tables: {old_tables}")

    # 2. Create new tables (idempotent)
    schema_sql = open("db_schema.sql").read()
    if not dry_run:
        cur.execute(schema_sql)
        conn.commit()
        print("New tables created.")

    # 3. Migrate coders → validators
    cur.execute("SELECT id, email, code, handle, created_at, onboarded_at FROM coders")
    coders = cur.fetchall()
    migrated_coders = 0
    for c in coders:
        cur.execute("SELECT 1 FROM validators WHERE handle = %s", (c["handle"],))
        if cur.fetchone():
            continue
        if not dry_run:
            cur.execute(
                """
                INSERT INTO validators (id, email, code, handle, created_at, onboarded_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (c["id"], c["email"], c["code"], c["handle"], c["created_at"], c["onboarded_at"]),
            )
        migrated_coders += 1
    print(f"Coders to migrate: {migrated_coders}")

    # 4. Migrate pairs → unvalidated + record_metadata + validation_queue
    cur.execute("SELECT pair_id, data_json FROM pairs")
    pairs = cur.fetchall()
    migrated_pairs = 0
    for p in pairs:
        data = json.loads(p["data_json"])
        pair_id = p["pair_id"]

        cur.execute("SELECT 1 FROM unvalidated WHERE pair_id = %s", (pair_id,))
        if cur.fetchone():
            continue

        record_id = str(uuid.uuid4())
        doi_o = _s(data.get("doi_o"))
        url_o = f"https://doi.org/{doi_o}" if doi_o else ""

        # Count existing judgements for this pair to set status
        cur.execute(
            "SELECT COUNT(*) AS n FROM judgements WHERE pair_id = %s", (pair_id,)
        )
        jcount = cur.fetchone()["n"]
        status = "validation_inprogress" if jcount > 0 else "unvalidated"

        if not dry_run:
            cur.execute(
                """
                INSERT INTO unvalidated (
                    record_id, pair_id,
                    doi_r, study_r, year_r, url_r, ref_r, abstract_r,
                    doi_o, study_o, year_o, url_o, ref_o,
                    type, outcome, outcome_quote, out_quote_source,
                    validation_status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record_id, pair_id,
                    _s(data.get("doi_r")), _s(data.get("title_r")),
                    _s(data.get("year_r")), _s(data.get("url_r")),
                    _s(data.get("ref_r")), _s(data.get("abstract_r")),
                    doi_o, _s(data.get("title_o")), _s(data.get("year_o")),
                    url_o, _s(data.get("ref_o")),
                    _s(data.get("type")), _s(data.get("outcome")),
                    _s(data.get("outcome_phrase")), _s(data.get("out_quote_source")),
                    status,
                ),
            )
            cur.execute(
                """
                INSERT INTO record_metadata (
                    record_id, pair_id, filter_status, link_method,
                    authors_r, authors_o, journal_r, openalex_id_r, source,
                    original_rank, n_originals
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record_id, pair_id,
                    _s(data.get("filter_status")), _s(data.get("link_method")),
                    _s(data.get("authors_r")), _s(data.get("authors_o")),
                    _s(data.get("journal_r")), _s(data.get("openalex_id_r")),
                    _s(data.get("source")),
                    int(data["original_rank"]) if data.get("original_rank") else None,
                    int(data["n_originals"]) if data.get("n_originals") else None,
                ),
            )
            for slot in ("human_1", "human_2", "llm"):
                cur.execute(
                    """
                    INSERT INTO validation_queue (record_id, validator_slot)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (record_id, slot),
                )

        migrated_pairs += 1
    print(f"Pairs to migrate: {migrated_pairs}")

    # 5. Migrate judgements → validation_queue
    # Assign first judgement per pair → human_1, second → human_2
    cur.execute(
        "SELECT id, coder_id, pair_id, type_judgement, original_judgement, "
        "outcome_judgement, comment, points, created_at "
        "FROM judgements ORDER BY pair_id, created_at"
    )
    judgements = cur.fetchall()
    slot_counters: dict[str, int] = {}
    migrated_j = 0

    for j in judgements:
        if j["type_judgement"] == "skip":
            if not dry_run:
                cur.execute(
                    "UPDATE validators SET skipped_count = skipped_count + 1 WHERE id = %s",
                    (j["coder_id"],),
                )
            continue

        cur.execute("SELECT record_id FROM unvalidated WHERE pair_id = %s", (j["pair_id"],))
        rec = cur.fetchone()
        if not rec:
            continue
        record_id = rec["record_id"]

        key = record_id
        slot_counters[key] = slot_counters.get(key, 0) + 1
        slot_num = slot_counters[key]
        if slot_num > 2:
            continue  # Only 2 human slots
        validator_slot = f"human_{slot_num}"

        type_check = "incorrect" if j["type_judgement"] == "not_validation" else "correct"
        original_check = (
            "correct" if j["original_judgement"] in ("correct", "unsure", None)
            else "incorrect"
        )
        outcome_check = (
            "correct" if j["outcome_judgement"] in ("correct", "unsure", None)
            else "incorrect"
        )

        if not dry_run:
            cur.execute(
                """
                UPDATE validation_queue SET
                    is_shown = TRUE, is_validated = TRUE,
                    validator_id = %s, type_check = %s,
                    original_check = %s, outcome_check = %s,
                    validator_notes = %s, points = %s,
                    shown_at = %s, validated_at = %s
                WHERE record_id = %s AND validator_slot = %s
                """,
                (
                    j["coder_id"], type_check, original_check, outcome_check,
                    j["comment"], j["points"],
                    j["created_at"], j["created_at"],
                    record_id, validator_slot,
                ),
            )
            cur.execute(
                """
                UPDATE validators SET
                    total_judgements = total_judgements + 1,
                    total_points = total_points + %s
                WHERE id = %s
                """,
                (j["points"], j["coder_id"]),
            )
        migrated_j += 1

    print(f"Judgements to migrate: {migrated_j}")

    if not dry_run:
        conn.commit()
        # Drop old tables after confirming migration
        for tbl in ("judgements", "pairs", "coders"):
            if tbl in old_tables:
                cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        conn.commit()
        print("Old tables dropped. Migration complete.")
    else:
        print("[dry-run] No changes made.")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
```

- [ ] **Step 2: Verify migration script is importable**

```
python -c "import db_migrate; print('OK')"
```

Expected: `OK`

---

## Task 13: Full Test Suite

- [ ] **Step 1: Run all tests**

```
python -m pytest tests/ -v
```

Expected: all 16 tests PASS (5 llm_validator + 8 consensus_engine + 3 sync_csv).

- [ ] **Step 2: Verify app imports cleanly**

```
python -c "import app; print('app OK')"
```

Expected: `app OK`

- [ ] **Step 3: Verify csv_to_db dry-run works**

```
python csv_to_db.py --input extracted.csv --dry-run
```

Expected: prints row counts, no DB writes.

---

## Deployment Notes

For a **fresh deployment** (no existing data):
1. Create a PostgreSQL database and set `DATABASE_URL`
2. Run `python app.py` — `init_db()` creates all tables and seeds from `extracted.csv`

For an **existing deployment** (has old `pairs/coders/judgements` tables):
1. Back up the database
2. Set all env vars (`DATABASE_URL`, `GEMINI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`, `GITHUB_BRANCH`)
3. Run `python db_migrate.py --dry-run` to preview
4. Run `python db_migrate.py` to execute
5. Restart the app with `python app.py`

`GITHUB_TOKEN` needs only `Contents: Read` permission on `forrtproject/flora-extractor`.
