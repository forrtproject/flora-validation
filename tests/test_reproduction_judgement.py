"""The reproduction judgement contract: browser -> API -> validation_queue.

Reproductions are judged on two independent axes, each with its own evidence. The
fields have to survive the whole chain — the front end sends them, the Pydantic
model accepts them, and both write paths persist them. A field dropped anywhere in
between is silent: the judgement submits, the validator is thanked, and the axis
they coded is gone.

The JS is asserted by reading docs/app.js. Crude, but it is the only cross-check
available without a browser harness, and the failure it guards against — a field
renamed on one side of the wire — is exactly the kind a Python-only suite misses.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_APP_JS = (_ROOT / "docs" / "app.js").read_text(encoding="utf-8")
_APP_PY = (_ROOT / "app.py").read_text(encoding="utf-8")

# The six corrected_* fields a reproduction judgement carries.
_AXIS_FIELDS = [
    "corrected_outcome_computation",
    "corrected_computational_quote",
    "corrected_computational_source",
    "corrected_outcome_robustness",
    "corrected_robustness_quote",
    "corrected_robustness_source",
]


@pytest.mark.parametrize("field", _AXIS_FIELDS)
def test_front_end_sends_the_field(field):
    payload = re.search(r"const payload = \{(.*?)\n  \};", _APP_JS, re.S)
    assert payload, "could not locate the judgement payload in docs/app.js"
    assert field in payload.group(1), f"{field} is never sent"


@pytest.mark.parametrize("field", _AXIS_FIELDS)
def test_api_model_accepts_the_field(field):
    model = re.search(r"class JudgeRequest\(BaseModel\):(.*?)\n\nclass ", _APP_PY, re.S)
    assert model, "could not locate JudgeRequest"
    assert re.search(rf"^\s+{field}: str \| None = None", model.group(1), re.M), \
        f"{field} would be dropped by the model"


@pytest.mark.parametrize("field", _AXIS_FIELDS)
def test_queue_update_persists_the_field(field):
    update = re.search(r"UPDATE validation_queue SET\s+is_validated = TRUE(.*?)WHERE queue_id", _APP_PY, re.S)
    assert update, "could not locate the judgement UPDATE"
    assert f"{field} = %s" in update.group(1), f"{field} is accepted but never stored"


@pytest.mark.parametrize("field", _AXIS_FIELDS)
def test_both_summaries_carry_the_field(field):
    """Two resolution paths write a JSONB summary — the queued flow and the
    assignment flow. A field in one and not the other loses the judgement for
    whichever route the record happened to take."""
    if field == "corrected_outcome_computation":
        assert '"corrected_outcome_computation": final_computation' in _APP_PY
        assert '"corrected_outcome_computation": target_computation' in _APP_PY
    elif field == "corrected_outcome_robustness":
        assert '"corrected_outcome_robustness": final_robustness' in _APP_PY
        assert '"corrected_outcome_robustness": target_robustness' in _APP_PY
    else:
        assert _APP_PY.count(f'"{field}": req.{field}') >= 2, \
            f"{field} is missing from one of the two validator summaries"


@pytest.mark.parametrize("field", _AXIS_FIELDS)
def test_schema_has_the_queue_column(field):
    sql = (_ROOT / "db_schema.sql").read_text(encoding="utf-8")
    assert re.search(rf"ALTER TABLE validation_queue ADD COLUMN IF NOT EXISTS\s+{field}\s+TEXT", sql), \
        f"validation_queue has no {field} column for the UPDATE to write"


# ---------------------------------------------------------------------------
# The joined string is gone from the validator flow
# ---------------------------------------------------------------------------

def test_the_axis_join_helper_is_deleted():
    """_reproLabel built "<computation>, <robustness>". It could not represent an
    undetermined axis — the extractor's equivalent collapsed to the bare label and
    discarded the other axis on 6 of 25 rows."""
    assert "_reproLabel" not in _APP_JS


def test_axis_selection_does_not_write_a_combined_outcome():
    """The axes ARE the judgement; corrected_outcome is cleared when one is picked
    so a stale joined value cannot ride along."""
    branch = re.search(r"else if \(btn\.dataset\.reproComp \|\| btn\.dataset\.reproRobust\) \{(.*?)\n    return;",
                       _APP_JS, re.S)
    assert branch, "could not locate the axis branch in onChoice"
    assert "state.judgement.corrected_outcome = null" in branch.group(1)


def test_record_is_served_with_both_axes_and_their_evidence():
    """The validator has to see what the extractor coded before confirming it."""
    select = re.search(r"_PAIR_SELECT = \"\"\"(.*?)\"\"\"", _APP_PY, re.S)
    assert select, "could not locate _PAIR_SELECT"
    for column in ["outcome_computation", "outcome_computational_quote",
                   "out_quote_computational_source", "outcome_robustness",
                   "outcome_robustness_quote", "out_quote_robust_source"]:
        assert column in select.group(1), f"{column} is never served to the validator"


def test_submit_requires_both_axes():
    """One axis is not a complete reproduction judgement."""
    assert re.search(
        r"\? \(j\.repro_computation && j\.repro_robustness\)", _APP_JS)


def test_each_axis_repeats_the_review_then_correction_pattern():
    assert 'data-repro-action="correct"' in _APP_JS
    assert 'data-repro-action="wrong"' in _APP_JS
    assert 'data-repro-action="unsure"' in _APP_JS
    assert 'id="repro-correction-${key}"' in _APP_JS
    assert 'values.filter(([value]) => value !== "cannot_be_determined")' in _APP_JS


def test_replication_correction_separates_main_and_niche_outcomes():
    helper = re.search(
        r"function _replicationCorrectionChoices\(\) \{(.*?)\n\}", _APP_JS, re.S
    )
    assert helper
    body = helper.group(1)
    primary = body.index('outcome-choice-primary')
    niche = body.index('outcome-choice-niche')
    assert primary < niche
    for value in (
        "successful", "failed", "mixed", "statistically successful but flawed",
        "uninformative", "descriptive only", "cannot_be_determined",
    ):
        assert value in body
    assert 'data-tooltip=' in _APP_JS


def test_quotes_are_not_required_to_submit():
    """The extractor supplies neither quote on some reproductions (2 of 25). A
    judgement must not be blocked on evidence that may not exist."""
    ready = re.search(r"const outcomeReady = j\.type === \"reproduction\"(.*?);", _APP_JS, re.S)
    assert ready
    for field in ("edited_computational_quote", "edited_robustness_quote"):
        assert field not in ready.group(1), f"submission blocked on {field}"
