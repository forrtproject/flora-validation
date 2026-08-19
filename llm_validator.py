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

from extractor_vocab import (
    AXIS_VALUE_ALIASES,
    OUTCOME_COMPUTATION_VALUES,
    OUTCOME_RENAME,
    OUTCOME_ROBUSTNESS_VALUES,
    REPLICATION_OUTCOMES,
    REPRODUCTION_OUTCOMES,
)

_LLM_VOTE_SCORE = 15
_MODEL_NAME = "gemini-3.1-flash-lite"

# Outcome vocabulary. The valid labels come from extractor_vocab, which is also
# what db_schema.sql's `unvalidated_outcome_check` mirrors — showing the model a
# label the constraint rejects would fail the import, and describing one it has
# dropped would waste prompt space. Only the wording lives here.
#
# Reproductions carry a two-axis label, "<computational>, <robustness>". The axes
# are independent: a reproduction can fail computationally and still find the
# conclusion robust, and vice versa.
_OUTCOME_DESCRIPTIONS = {
    # replications
    "successful": "the replication found the same effect/result as the original study",
    "failed": "the replication did NOT find the original effect (result contradicts or is inconsistent with the original)",
    "mixed": "some but not all of the original findings replicated",
    "uninformative": "the study design could not meaningfully test the original finding",
    "descriptive only": "the paper is descriptive/exploratory, not a hypothesis-testing replication",
    # Shared by both record types — see the note above the reproduction block.
    "statistically successful but flawed": "the result held statistically, but the authors flag a methodological problem that undermines it",
    "cannot_be_determined": "the abstract does not give enough information to judge the outcome",
    "not_a_replication": "the paper does not actually test the named original",
    # reproductions. Two labels above — cannot_be_determined and
    # statistically_successful_but_flawed — apply here too and are not
    # (computational, robustness) pairs; extractor_vocab decides membership, so
    # they reach the model for both record types without being repeated here.
    "computationally reproducible, robust": "the original computation reproduced, and it held up under robustness checks",
    "computationally reproducible, robustness challenges": "the original computation reproduced, but robustness checks raised issues",
    "computationally reproducible, not checked": "the original computation reproduced; robustness was not assessed",
    "computational issues, robust": "the original computation could not be reproduced, but the conclusion still held under robustness checks",
    "computational issues, robustness challenges": "the original computation could not be reproduced, and robustness checks raised issues",
    "computational issues, not checked": "the original computation could not be reproduced; robustness was not assessed",
    "technical failure, robust": "the materials could not be run, but the conclusion held under robustness checks",
    "technical failure, robustness challenges": "the materials could not be run, and robustness checks raised issues",
    "technical failure, not checked": "the materials could not be run; robustness was not assessed",
    "not checked, robust": "computational reproduction wasn't assessed; robustness checks were fine",
    "not checked, robustness challenges": "computational reproduction wasn't assessed; robustness checks raised issues",
    "not checked, not checked": "neither computational reproduction nor robustness was assessed",
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
    """{label: description} for the categories that apply to *record_type*.

    Membership comes from extractor_vocab; the order follows
    _OUTCOME_DESCRIPTIONS. A label added upstream but not yet described still
    reaches the model with an empty description rather than vanishing from the
    prompt — silently narrowing the vocabulary is how the model ends up
    'correcting' a perfectly valid outcome.
    """
    values = REPRODUCTION_OUTCOMES if record_type == "reproduction" else REPLICATION_OUTCOMES
    vocab = {k: v for k, v in _OUTCOME_DESCRIPTIONS.items() if k in values}
    vocab.update({v: "" for v in sorted(values) if v not in _OUTCOME_DESCRIPTIONS})
    return vocab


def _vocab_block(record_type: str) -> str:
    return "\n".join(
        f'- "{label}" — {desc}' if desc else f'- "{label}"'
        for label, desc in _outcome_vocab(record_type).items()
    )


# Reproductions are judged on two INDEPENDENT axes, each with its own evidence.
# Asking the model for one compound verdict invites it to trade one axis off
# against the other — the whole point of the codebook change is that a
# reproduction can fail computationally and still find the conclusion robust.
_AXES = (
    ("outcome_computation", "computational", OUTCOME_COMPUTATION_VALUES,
     "whether the original computation could be reproduced from the authors' data and code"),
    ("outcome_robustness", "robustness", OUTCOME_ROBUSTNESS_VALUES,
     "whether the original conclusion held up under alternative specifications or robustness checks"),
)


def _is_repro(record_type: str) -> bool:
    return record_type == "reproduction"


def _extracted_outcome_block(record: dict, record_type: str) -> str:
    """The extracted-outcome lines shown to the model, per record type."""
    if not _is_repro(record_type):
        return (f"Outcome category: {record.get('outcome') or ''}\n"
                f"Outcome quote: {record.get('outcome_quote') or ''}")
    lines = []
    for field, label, _values, _desc in _AXES:
        quote_field = ("outcome_computational_quote" if label == "computational"
                       else "outcome_robustness_quote")
        lines.append(f"{label.capitalize()} verdict: {record.get(field) or '(not coded)'}")
        lines.append(f"{label.capitalize()} quote: {record.get(quote_field) or '(none extracted)'}")
    return "\n".join(lines)


def _vocab_section(record_type: str) -> str:
    if not _is_repro(record_type):
        return (f'--- VALID OUTCOME CATEGORIES (for type "{record_type}") ---\n'
                f"{_vocab_block(record_type)}\n"
                "If outcome_check is \"incorrect\", corrected_outcome MUST be exactly one of the\n"
                "category strings above — never invent a new label.")
    blocks = ["--- VALID VALUES FOR EACH REPRODUCTION AXIS ---",
              "The two axes are INDEPENDENT. Judge each on its own evidence; a paper can fail",
              "one and pass the other. Do not let one axis influence the other."]
    for field, label, values, desc in _AXES:
        blocks.append(f"\n{field} — {desc}:")
        blocks += [f'- "{v}"' for v in sorted(values)]
    blocks.append('\nIf outcome_check is "incorrect", set the axis you believe is wrong to exactly'
                  "\none of its strings above and leave the other null. Never invent a value.")
    return "\n".join(blocks)


def _task_outcome_keys(record_type: str) -> str:
    if not _is_repro(record_type):
        return ('- "corrected_outcome": if outcome_check is "incorrect", the correct category '
                "from the list above, else null")
    return ('- "corrected_outcome_computation": the correct computational value if that axis is '
            "wrong, else null\n"
            '- "corrected_outcome_robustness": the correct robustness value if that axis is '
            "wrong, else null")


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
Original paper title: {title_o}
Original within-paper study number(s): {study_o}
Original study year: {year_o}
{extracted_outcome}

{vocab_section}

--- YOUR TASK ---
Return a JSON object with exactly these keys:
- "type_check": "correct", "incorrect", or "uncertain" — is the type replication/reproduction accurate?
- "original_check": "correct", "incorrect", or "uncertain" — does the original study match what the abstract describes?
- "outcome_check": "correct", "incorrect", or "uncertain" — does the outcome match the abstract?
{task_outcome_keys}
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
    props = {
        "type_check":        {"type": "STRING", "enum": check_enum},
        "original_check":    {"type": "STRING", "enum": check_enum},
        "outcome_check":     {"type": "STRING", "enum": check_enum},
        "corrected_doi_o":   {"type": "STRING", "nullable": True},
        "corrected_type":    {"type": "STRING", "enum": ["replication", "reproduction"], "nullable": True},
        "notes":             {"type": "STRING"},
    }
    # Reproductions correct an axis, not a compound label. Enumerating each axis
    # separately is what stops the model returning a value from the wrong one.
    if _is_repro(record_type):
        outcome_keys = ["corrected_outcome_computation", "corrected_outcome_robustness"]
        for (field, _label, values, _desc), key in zip(_AXES, outcome_keys):
            props[key] = {"type": "STRING", "enum": sorted(values), "nullable": True}
    else:
        outcome_keys = ["corrected_outcome"]
        props["corrected_outcome"] = {
            "type": "STRING", "enum": list(_outcome_vocab(record_type).keys()), "nullable": True}
    return {
        "type": "OBJECT",
        "properties": props,
        "required": ["type_check", "original_check", "outcome_check",
                     *outcome_keys, "corrected_doi_o", "corrected_type", "notes"],
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
    mapping retired spellings (OUTCOME_RENAME) and common near-misses
    (_OUTCOME_SYNONYMS) onto the closest valid category. Returns
    (outcome_or_None, note_suffix_or_None); an unrecognized suggestion is dropped
    (never passed through as an off-schema string) but flagged in the note suffix
    so it isn't silently lost."""
    if not raw:
        return None, None
    raw = str(raw).strip()
    vocab = _outcome_vocab(record_type)
    if raw in vocab:
        return raw, None
    # A retired label is a real judgement in outdated words — the model saw these
    # spellings in the prompt until the reproduction relabelling, and they read as
    # natural English either way. Translate rather than discard.
    renamed = OUTCOME_RENAME.get(raw)
    if renamed and renamed in vocab:
        return renamed, None
    mapped = _OUTCOME_SYNONYMS.get(raw.lower())
    if mapped and mapped in vocab:
        return mapped, None
    return None, f'LLM suggested "{raw}" — not a valid category, dropped.'


def _notes_with_flags(raw_notes: str, flags: list) -> str:
    """The model's reasoning plus any dropped-suggestion flags, inside 200 chars.

    Room is reserved for the flags BEFORE truncating the model's own notes:
    truncating first and appending after silently loses the flag whenever the
    reasoning was already verbose — exactly the "off-schema suggestion vanishes
    with no trace" failure the flags exist to prevent."""
    raw_notes = (raw_notes or "").strip()
    suffix = " ".join(f for f in flags if f)
    if not suffix:
        return raw_notes[:200]
    budget = max(200 - len(suffix) - 1, 0)
    return (raw_notes[:budget].rstrip() + " " + suffix).strip()[:200]


def _parse_reproduction(parsed: dict, outcome_check: str) -> dict:
    """Two independent axes, each validated against its own vocabulary."""
    keys = ("corrected_outcome_computation", "corrected_outcome_robustness")
    values, flags = {}, []
    for (field, _label, allowed, _desc), key in zip(_AXES, keys):
        coerced, flag = _coerce_axis(parsed.get(key), allowed, AXIS_VALUE_ALIASES.get(field, {}))
        values[key] = coerced
        if flag:
            flags.append(flag)
    # A confident "incorrect" the model could not back with a valid value on either
    # axis is not trustworthy — surface it as uncertain rather than a rejection with
    # no usable correction, matching the replication path.
    suggested_anything = any(parsed.get(k) for k in keys)
    if outcome_check == "incorrect" and suggested_anything and not any(values.values()):
        outcome_check = "uncertain"

    corrected_type = parsed.get("corrected_type")
    if corrected_type not in ("replication", "reproduction"):
        corrected_type = None

    return {
        "type_check": _coerce_check(parsed.get("type_check")),
        "original_check": _coerce_check(parsed.get("original_check")),
        "outcome_check": outcome_check,
        # No compound label: the axes ARE the judgement.
        "corrected_outcome": None,
        **values,
        "corrected_doi_o": parsed.get("corrected_doi_o") or None,
        "corrected_type": corrected_type,
        "notes": _notes_with_flags(str(parsed.get("notes") or ""), flags),
    }


def _coerce_axis(raw, allowed: frozenset, aliases: dict) -> tuple[str | None, str | None]:
    """Validate one reproduction axis against its own vocabulary. Separate from
    _coerce_outcome because the axes have separate vocabularies — accepting a
    robustness value for the computational axis would look valid and be nonsense.

    `aliases` are that axis's retired spellings. The model saw them in the prompt
    until the relabelling and they read the same in English, so a judgement in the
    old words is translated rather than thrown away."""
    if not raw:
        return None, None
    raw = str(raw).strip()
    if raw in allowed:
        return raw, None
    renamed = aliases.get(raw)
    if renamed and renamed in allowed:
        return renamed, None
    return None, f'LLM suggested "{raw}" — not a valid axis value, dropped.'


def _parse_response(text: str, record_type: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)

    outcome_check = _coerce_check(parsed.get("outcome_check"))
    if _is_repro(record_type):
        return _parse_reproduction(parsed, outcome_check)
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
                title_o, study_o, year_o, outcome, outcome_quote)
        context: "sanity_check" | "tiebreaker"

    Returns:
        dict suitable for unvalidated.llm_validator JSONB.
        On error, returns {"error": "...", "context": context, ...}.
    """
    # Normalized for vocabulary/schema selection; falls back to the replication
    # vocabulary if `type` is missing or unrecognized (the model can still
    # override this via corrected_type if the abstract says otherwise).
    record_type = record.get("type") if record.get("type") == "reproduction" else "replication"

    # Some originals genuinely have no DOI (books, chapters, pre-DOI-era papers) —
    # a blank line here would read as a data-quality gap rather than an honest "no
    # DOI exists", and could bias the model toward flagging original_check.
    doi_o = record.get("doi_o") or "(none — original has no registered DOI, e.g. a book or pre-DOI-era paper)"

    prompt = _PROMPT_TEMPLATE.format(
        abstract_r=record.get("abstract_r") or "(no abstract)",
        type=record.get("type") or "",
        doi_o=doi_o,
        title_o=record.get("title_o") or "",
        study_o=record.get("study_o") or "(not specified)",
        year_o=record.get("year_o") or "",
        # Replications show one outcome + quote; reproductions show both axes with
        # their own evidence, and are asked to judge each independently.
        extracted_outcome=_extracted_outcome_block(record, record_type),
        vocab_section=_vocab_section(record_type),
        task_outcome_keys=_task_outcome_keys(record_type),
    )

    # Retry once on a transient failure (network blip, API timeout) before
    # giving up — a single retry absorbs most momentary glitches.
    last_error = None
    for _ in range(2):
        try:
            raw = _call_gemini(prompt, record_type)
            parsed = _parse_response(raw, record_type)
            # Spread the parsed result rather than re-listing its keys: the
            # reproduction path returns two axis fields the replication path does
            # not, and an explicit list silently drops whichever it forgets.
            # _parse_response has already validated every value.
            return {
                "model": _MODEL_NAME,
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "context": context,
                "vote_score": _LLM_VOTE_SCORE,
                **parsed,
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
