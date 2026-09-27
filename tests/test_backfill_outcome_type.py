"""A converted replication judgement cannot approve a later reproduction."""
from unittest.mock import Mock

import pytest

import backfill_outcome_agreement as backfill
import consensus_engine
from tests.test_outcome_quote_agreement import _now_agree, _seed, _state
from tests.test_preparation_database import local_database  # noqa: F401


@pytest.mark.parametrize("record_type, expected_status, expected_candidates", [
    ("replication", "consensus_reached", 1),
    ("reproduction", "need_review", 0),
])
def test_later_reevaluation_requires_the_record_to_remain_a_replication(
        local_database, monkeypatch, capsys, record_type, expected_status, expected_candidates):
    """An import can change type while retaining the shared unclear outcome."""
    outcome = "cannot_be_determined"
    record_id = _seed(local_database, outcome=outcome, legacy_shown=outcome,
                      agree_shown=outcome)
    with local_database, local_database.cursor() as cur:
        cur.execute("UPDATE validation_queue SET corrected_outcome = %s "
                    "WHERE record_id = %s AND validator_slot = 'human_1'", (outcome, record_id))
        cur.execute("UPDATE unvalidated SET validator_1 = jsonb_set(validator_1, "
                    "'{corrected_outcome}', to_jsonb(%s::text)) WHERE record_id = %s",
                    (outcome, record_id))
    llm = Mock(return_value={"error": "offline in tests"})
    monkeypatch.setattr(consensus_engine, "run_llm_validation", llm)

    backfill.run(apply=True, reevaluate=False)
    queue, record = _state(local_database, record_id)
    assert queue[0]["outcome_check"] == "correct"
    assert queue[0]["additional_checks"]["outcome_agreement_backfilled"] is True
    assert record["validation_status"] == "need_review"
    assert _now_agree(capsys.readouterr().out) == (1, [record_id])
    llm.assert_not_called()

    with local_database, local_database.cursor() as cur:
        cur.execute("UPDATE unvalidated SET type = %s WHERE record_id = %s",
                    (record_type, record_id))

    backfill.run(apply=True, reevaluate=True)
    assert _now_agree(capsys.readouterr().out) == (
        expected_candidates, [record_id] if expected_candidates else [])
    assert _state(local_database, record_id)[1]["validation_status"] == expected_status
    assert llm.call_count == expected_candidates
