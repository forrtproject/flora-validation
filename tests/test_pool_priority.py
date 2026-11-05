"""The Pool Priority preview counts exactly what serving can hand out."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def _body(name: str) -> str:
    return APP.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]


def test_preview_and_serving_share_one_definition_of_servable():
    """The preview once counted records whose two reviewer slots were both taken,
    which serving can never hand out; one shared predicate keeps them equal."""
    servable = APP.split("_SERVABLE_SQL = ", 1)[1].split('"""\n', 2)[1]
    assert "u.validation_status IN ('unvalidated', 'validation_inprogress')" in servable
    assert "u.restricted_access IS NOT TRUE" in servable
    assert "vq.validator_id IS NULL" in servable

    serving = _body("_select_pair_candidate")
    preview = _body("preview_serving_config")
    assert "WHERE {_SERVABLE_SQL}" in serving
    assert 'base = f"FROM unvalidated u WHERE {_SERVABLE_SQL}"' in preview
    # No second, hand-maintained copy that could drift.
    assert "restricted_access IS NOT TRUE" not in serving
    assert "restricted_access IS NOT TRUE" not in preview


def test_the_hard_test_cannot_be_null():
    """NOT (NULL OR FALSE OR FALSE) is NULL, which would drop a record with no outcome
    from both pools. paper_review keeps its own copy of the test."""
    import paper_review
    assert "u.outcome = " not in APP.split("_HARD_COND = ", 1)[1].split("\n\n", 1)[0]
    assert "u.outcome = " not in paper_review._HARD_RECORD
    assert "IS NOT DISTINCT FROM 'cannot_be_determined'" in paper_review._HARD_RECORD
