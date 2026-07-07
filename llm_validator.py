"""
llm_validator.py — Gemini Flash validator for the FLoRA validation pipeline.

Checks whether extracted metadata (type, original study, outcome) is consistent
with the replication paper's abstract. Called by consensus_engine.py either as a
sanity check (both humans agreed) or as a tiebreaker (humans disagreed).

The LLM defaults to "correct" when uncertain — it is conservative by design.
"""
import json
import os
import re
from datetime import datetime, timezone

from google import genai

_LLM_VOTE_SCORE = 15
_MODEL_NAME = "gemini-3.1-flash-lite"

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
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(model=_MODEL_NAME, contents=prompt)
    return response.text


def _parse_response(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    for key in ("corrected_outcome", "corrected_doi_o", "corrected_type"):
        if key not in parsed:
            parsed[key] = None
    return parsed


def run_llm_validation(record: dict, context: str) -> dict:
    """
    Call Gemini Flash to validate a record.

    Args:
        record: dict from unvalidated table (needs abstract_r, type, doi_o,
                study_o, year_o, outcome, outcome_quote)
        context: "sanity_check" | "tiebreaker"

    Returns:
        dict suitable for unvalidated.llm_validator JSONB.
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

    # Retry once on a transient failure (network blip, API timeout) before
    # giving up — a single retry absorbs most momentary glitches.
    last_error = None
    for _ in range(2):
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
