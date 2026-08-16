"""
llm_validator.py — Gemini Flash validator for the FLoRA validation pipeline.

Checks whether extracted metadata (type, original study, outcome) is consistent
with the replication paper's abstract. Called by consensus_engine.py either as a
sanity check (both humans agreed) or as a tiebreaker (humans disagreed).

Each check is "correct", "incorrect", or "uncertain" — the model is instructed to
say "uncertain" rather than guess. consensus_engine never special-cases this: an
"uncertain" value simply can't equal a human's "correct"/"incorrect", so it fails
_llm_matches like any other disagreement and the record falls through to admin
review — an unconfident LLM can no longer force a reject or accidentally win a
tiebreak by defaulting to "correct".

corrected_outcome is constrained to the exact category vocabulary from
db_schema.sql's unvalidated_outcome_check (mirrored below) through three
independent guards, so the model can no longer invent an off-schema label
(FLoRA issue #4, e.g. "inconsistent" instead of "failed"):
  1. the prompt lists the real categories with one-line definitions,
  2. Gemini's response_schema constrains the field to that enum structurally,
  3. _coerce_outcome re-validates server-side regardless of what came back.
"""
import json
import os
import re
from datetime import datetime, timezone

from google import genai
from google.genai import types

_LLM_VOTE_SCORE = 15
_MODEL_NAME = "gemini-3.1-flash-lite"

# Outcome vocabulary — must mirror the `unvalidated_outcome_check` CHECK constraint
# in db_schema.sql exactly. Split by record type: the two are mutually-exclusive
# subsets of the same `outcome` column (replications use one set, reproductions
# the other), so the model is only ever shown the categories that apply.
_REPLICATION_OUTCOMES = {
    "successful": "the replication found the same effect/result as the original study",
    "failed": "the replication did NOT find the original effect (result contradicts or is inconsistent with the original)",
    "mixed": "some but not all of the original findings replicated",
    "uninformative": "the study design could not meaningfully test the original finding",
    "descriptive": "the paper is descriptive/exploratory, not a hypothesis-testing replication",
    "cannot_be_determined": "the abstract does not give enough information to judge the outcome",
}
_REPRODUCTION_OUTCOMES = {
    "computationally successful, robust": "the original computation reproduced, and it held up under robustness checks",
    "computationally successful, robustness challenges": "the original computation reproduced, but robustness checks raised issues",
    "computation not checked, robust": "computational reproduction wasn't assessed; robustness checks were fine",
    "computation not checked, robustness challenges": "computational reproduction wasn't assessed; robustness checks raised issues",
    "computational issues, robustness challenges": "the original computation could not be reproduced, and robustness checks raised issues",
}

# Common near-miss labels the model tends to invent, mapped onto the closest real
# category. Applied only as a fallback when the raw suggestion isn't already a
# valid label (see _coerce_outcome) — anything not covered here is dropped, never
# guessed further.
_OUTCOME_SYNONYMS = {
    "success": "successful", "replicated": "successful", "confirmed": "successful",
    "failure": "failed", "not replicated": "failed", "did not replicate": "failed",
    "inconsistent": "failed", "contradicted": "failed", "null result": "failed",
    "partial": "mixed", "partially successful": "mixed", "partial success": "mixed",
    "undetermined": "cannot_be_determined", "not determined": "cannot_be_determined",
    "indeterminate": "cannot_be_determined", "unclear": "cannot_be_determined",
}

_CHECK_VALUES = {"correct", "incorrect", "uncertain"}


def _outcome_vocab(record_type: str) -> dict:
    return _REPRODUCTION_OUTCOMES if record_type == "reproduction" else _REPLICATION_OUTCOMES


def _vocab_block(record_type: str) -> str:
    return "\n".join(f'- "{label}" — {desc}' for label, desc in _outcome_vocab(record_type).items())


_PROMPT_TEMPLATE = """You are a research quality checker for a database of replication studies.

Given the following replication paper data, check whether the extracted metadata is accurate.
Answer ONLY based on the abstract and provided metadata — do not use external knowledge.
If the abstract does not give you enough information to confidently judge a field, answer
"uncertain" for that field rather than guessing "correct" or "incorrect".

--- REPLICATION PAPER ---
Abstract: {abstract_r}

--- EXTRACTED METADATA ---
Type: {type}
Original study DOI: {doi_o}
Original study title: {study_o}
Original study year: {year_o}
Outcome category: {outcome}
Outcome quote: {outcome_quote}

--- VALID OUTCOME CATEGORIES (for type "{record_type}") ---
{outcome_vocab}
If outcome_check is "incorrect", corrected_outcome MUST be exactly one of the category
strings above — never invent a new label.

--- YOUR TASK ---
Return a JSON object with exactly these keys:
- "type_check": "correct", "incorrect", or "uncertain" — is the type replication/reproduction accurate?
- "original_check": "correct", "incorrect", or "uncertain" — does the original study match what the abstract describes?
- "outcome_check": "correct", "incorrect", or "uncertain" — does the outcome category match the abstract?
- "corrected_outcome": if outcome_check is "incorrect", the correct category from the list above, else null
- "corrected_doi_o": corrected DOI string if you can identify a different original, else null
- "corrected_type": "replication" or "reproduction" if type_check is "incorrect", else null
- "notes": one sentence of reasoning (max 200 chars)

Return ONLY the JSON object, no prose, no markdown fences."""


def _response_schema(record_type: str) -> dict:
    """Gemini structured-output schema: a hard, model-level guarantee that the
    three checks are one of the three allowed values and corrected_outcome (when
    not null) is one of the real category strings — on top of the prompt
    instructions and the _coerce_* server-side validation below."""
    check_enum = sorted(_CHECK_VALUES)
    return {
        "type": "OBJECT",
        "properties": {
            "type_check":        {"type": "STRING", "enum": check_enum},
            "original_check":    {"type": "STRING", "enum": check_enum},
            "outcome_check":     {"type": "STRING", "enum": check_enum},
            "corrected_outcome": {"type": "STRING", "enum": list(_outcome_vocab(record_type).keys()), "nullable": True},
            "corrected_doi_o":   {"type": "STRING", "nullable": True},
            "corrected_type":    {"type": "STRING", "enum": ["replication", "reproduction"], "nullable": True},
            "notes":             {"type": "STRING"},
        },
        "required": ["type_check", "original_check", "outcome_check",
                     "corrected_outcome", "corrected_doi_o", "corrected_type", "notes"],
    }


def _call_gemini(prompt: str, record_type: str) -> str:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_response_schema(record_type),
    )
    response = client.models.generate_content(model=_MODEL_NAME, contents=prompt, config=config)
    return response.text


def _coerce_check(value) -> str:
    """Normalize a check field to the allowed set. An unrecognized or missing
    value becomes "uncertain" — never "correct": silently guessing away a real
    uncertainty is exactly the failure mode this replaces."""
    v = str(value).strip().lower() if value is not None else ""
    return v if v in _CHECK_VALUES else "uncertain"


def _coerce_outcome(raw, record_type: str) -> tuple[str | None, str | None]:
    """Validate a suggested outcome against the record type's real vocabulary,
    mapping common near-misses (see _OUTCOME_SYNONYMS) onto the closest valid
    category. Returns (outcome_or_None, note_suffix_or_None); an unrecognized
    suggestion is dropped (never passed through as an off-schema string) but
    flagged in the note suffix so it isn't silently lost."""
    if not raw:
        return None, None
    raw = str(raw).strip()
    vocab = _outcome_vocab(record_type)
    if raw in vocab:
        return raw, None
    mapped = _OUTCOME_SYNONYMS.get(raw.lower())
    if mapped and mapped in vocab:
        return mapped, None
    return None, f'LLM suggested "{raw}" — not a valid category, dropped.'


def _parse_response(text: str, record_type: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)

    outcome_check = _coerce_check(parsed.get("outcome_check"))
    corrected_outcome, outcome_note = _coerce_outcome(parsed.get("corrected_outcome"), record_type)
    # An unrecognized suggestion means the check itself isn't trustworthy either —
    # surface it as uncertain rather than a confident "incorrect" with no valid fix.
    if outcome_check == "incorrect" and parsed.get("corrected_outcome") and corrected_outcome is None:
        outcome_check = "uncertain"

    # Reserve room for outcome_note BEFORE truncating the model's own reasoning:
    # slicing notes to 200 first and only then appending would silently drop the
    # flag whenever the model's own notes were already verbose — exactly the
    # "off-schema suggestion vanishes with no trace" failure this exists to avoid.
    raw_notes = str(parsed.get("notes") or "").strip()
    if outcome_note:
        budget = max(200 - len(outcome_note) - 1, 0)
        notes = (raw_notes[:budget].rstrip() + " " + outcome_note).strip()[:200]
    else:
        notes = raw_notes[:200]

    corrected_type = parsed.get("corrected_type")
    if corrected_type not in ("replication", "reproduction"):
        corrected_type = None

    return {
        "type_check": _coerce_check(parsed.get("type_check")),
        "original_check": _coerce_check(parsed.get("original_check")),
        "outcome_check": outcome_check,
        "corrected_outcome": corrected_outcome,
        "corrected_doi_o": parsed.get("corrected_doi_o") or None,
        "corrected_type": corrected_type,
        "notes": notes,
    }


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
    # Normalized for vocabulary/schema selection; falls back to the replication
    # vocabulary if `type` is missing or unrecognized (the model can still
    # override this via corrected_type if the abstract says otherwise).
    record_type = record.get("type") if record.get("type") == "reproduction" else "replication"

    prompt = _PROMPT_TEMPLATE.format(
        abstract_r=record.get("abstract_r") or "(no abstract)",
        type=record.get("type") or "",
        doi_o=record.get("doi_o") or "",
        study_o=record.get("study_o") or "",
        year_o=record.get("year_o") or "",
        outcome=record.get("outcome") or "",
        outcome_quote=record.get("outcome_quote") or "",
        record_type=record_type,
        outcome_vocab=_vocab_block(record_type),
    )

    # Retry once on a transient failure (network blip, API timeout) before
    # giving up — a single retry absorbs most momentary glitches.
    last_error = None
    for _ in range(2):
        try:
            raw = _call_gemini(prompt, record_type)
            parsed = _parse_response(raw, record_type)
            return {
                "model": _MODEL_NAME,
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "context": context,
                "vote_score": _LLM_VOTE_SCORE,
                "type_check": parsed["type_check"],
                "original_check": parsed["original_check"],
                "outcome_check": parsed["outcome_check"],
                "corrected_outcome": parsed["corrected_outcome"],
                "corrected_doi_o": parsed["corrected_doi_o"],
                "corrected_type": parsed["corrected_type"],
                "notes": parsed["notes"],
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
