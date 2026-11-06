"""The Final Preview in a Pending Approval panel against what approving publishes.

_apPublished and _apCannotApprove in docs/app.js copy consensus_engine.approval_values,
so that a panel can say what Approve would publish and whether it would be refused.
The server compares nothing with what the panel showed, so a drift between the two
would publish something other than the preview, unnoticed. These run the JavaScript
itself under node on sample records and compare the answers. app.py is only read as
text: consensus_engine and what it imports open no connection.
"""
from itertools import product
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from consensus_engine import approval_values
from extractor_vocab import (
    OUTCOME_COMPUTATION_VALUES,
    OUTCOME_ROBUSTNESS_VALUES,
    REPLICATION_OUTCOMES,
)

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")

# Every column the two read, as SELECT * returns them: unset is None, never missing.
NO_VALUES = dict.fromkeys((
    "type", "final_type", "outcome", "final_outcome",
    "outcome_computation", "final_outcome_computation",
    "outcome_computational_quote", "final_computational_quote",
    "out_quote_computational_source", "final_computational_source",
    "outcome_robustness", "final_outcome_robustness",
    "outcome_robustness_quote", "final_robustness_quote",
    "out_quote_robust_source", "final_robustness_source",
    "title_r", "final_title_r", "url_r", "final_url_r", "abstract_r", "final_abstract_r",
    "doi_r", "final_doi_r", "doi_o", "final_doi_o", "title_o", "final_title_o",
    "outcome_quote", "final_outcome_quote", "out_quote_source", "final_out_quote_source",
    "doi_r_published", "alt_identifier_r",
))
EXTRACTED = {
    **NO_VALUES,
    "type": "replication", "outcome": "failed", "title_r": "Extracted title",
    "url_r": "https://example.test/r", "abstract_r": "Extracted abstract.", "doi_r": "10.5555/r",
    "doi_o": "10.5555/o", "title_o": "Extracted original", "outcome_quote": "It failed.",
    "out_quote_source": "abstract",
}
CORRECTED = {
    "final_type": "replication", "final_outcome": "successful", "final_title_r": "Corrected title",
    "final_url_r": "https://example.test/fixed", "final_abstract_r": "Corrected abstract.",
    "final_doi_o": "10.5555/o2", "final_title_o": "Corrected original",
    "final_outcome_quote": "It held.", "final_out_quote_source": "discussion",
    "doi_r_published": "10.5555/published", "alt_identifier_r": "OSF:abc12",
}
AXES = {
    "outcome_computation": "computational issues", "outcome_robustness": "robust",
    "outcome_computational_quote": "Extracted computation.", "out_quote_computational_source": "abstract",
    "outcome_robustness_quote": "Extracted robustness.", "out_quote_robust_source": "abstract",
}
CORRECTED_AXES = {
    "final_outcome_computation": "computationally reproducible",
    "final_outcome_robustness": "robustness challenges",
    "final_computational_quote": "Corrected computation.", "final_computational_source": "discussion",
    "final_robustness_quote": "Corrected robustness.", "final_robustness_source": "discussion",
}
REPRODUCTION = {**EXTRACTED, "type": "reproduction", "outcome": "computational issues, robust", **AXES}

CASES = {
    "extracted values only": EXTRACTED,
    "every field corrected": {**EXTRACTED, **CORRECTED},
    "empty corrections stand in for nothing": {
        **EXTRACTED, "final_title_r": "", "final_outcome": "", "final_title_o": "", "final_url_r": "",
    },
    "a blank original DOI stays blank": {**EXTRACTED, "final_doi_o": ""},
    "a corrected replication DOI is not what approve publishes": {**EXTRACTED, "final_doi_r": "10.5555/other"},
    "a corrected type replaces the extracted one": {**REPRODUCTION, "final_type": "replication", "final_outcome": "mixed"},
    "reproduction, extracted axes": REPRODUCTION,
    "reproduction, corrected axes": {**REPRODUCTION, **CORRECTED_AXES},
    "reproduction, the outcome follows the axes, not a stale stored one": {
        **REPRODUCTION, **CORRECTED_AXES, "final_outcome": "computational issues, robust",
    },
    "reproduction, one corrected axis": {**REPRODUCTION, "final_outcome_robustness": "not checked"},
    "reproduction, an axis left undetermined": {**REPRODUCTION, "outcome_computation": "cannot_be_determined"},
    "reproduction, computation missing": {**REPRODUCTION, "outcome_computation": None},
    "reproduction, robustness missing": {**REPRODUCTION, "outcome_robustness": None},
    "reproduction, both missing": {**REPRODUCTION, "outcome_computation": None, "outcome_robustness": None},
    "not a validation at all": {**EXTRACTED, "final_type": "not_validation"},
    "no type": {**EXTRACTED, "type": None},
    "no outcome": {**EXTRACTED, "outcome": None},
    "a reproduction's outcome left on a replication": {**EXTRACTED, "outcome": "computational issues, robust"},
    "an outcome outside the vocabulary": {**EXTRACTED, "outcome": "inconclusive"},
    **{f"replication outcome {outcome}": {**EXTRACTED, "outcome": outcome}
       for outcome in sorted(REPLICATION_OUTCOMES)},
    **{f"axes {computation} / {robustness}": {
           **REPRODUCTION, "outcome_computation": computation, "outcome_robustness": robustness}
       for computation, robustness in product(sorted(OUTCOME_COMPUTATION_VALUES),
                                              sorted(OUTCOME_ROBUSTNESS_VALUES))},
}


def _declaration(name):
    """One top-level function or constant of docs/app.js, whole."""
    match = re.search(rf"^(function|const) {name}\b", APP_JS, re.M)
    assert match, f"{name} is not in docs/app.js"
    end = "\n}\n" if match[1] == "function" else ";\n"
    return APP_JS[match.start():APP_JS.index(end, match.start()) + len(end)]


HARNESS = """
const records = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const answers = records.map((rec) => {
  const published = _apPublished(rec);
  return { published, refused: _apCannotApprove(published) };
});
process.stdout.write(JSON.stringify(answers));
"""


@pytest.fixture(scope="module")
def panel():
    """What the panel's own code says about each sample record."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    script = "\n".join(_declaration(name) for name in (
        "_FLAWED_OUTCOME", "_AP_OUTCOMES", "_apReproOutcome", "_apPublished", "_apCannotApprove",
    )) + HARNESS
    out = subprocess.run(["node", "-e", script], input=json.dumps(list(CASES.values())),
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return dict(zip(CASES, json.loads(out.stdout)))


@pytest.mark.parametrize("name", CASES)
def test_the_panel_says_what_approving_publishes(panel, name):
    answer = panel[name]
    try:
        expected = approval_values(CASES[name])
    except ValueError:
        assert answer["refused"], "the server refuses this, and the panel would offer Approve"
        return
    assert answer["refused"] is None, "the server accepts this, and the panel would hold Approve back"
    missing = expected.keys() - answer["published"].keys()
    assert not missing, "approval_values publishes fields the panel does not know about"
    assert {key: answer["published"][key] for key in expected} == expected


def test_the_cases_reach_both_answers():
    """A sample that never refuses, or never publishes, would prove nothing."""
    outcomes = []
    for record in CASES.values():
        try:
            outcomes.append(approval_values(record)["type"])
        except ValueError:
            outcomes.append(None)
    assert {None, "replication", "reproduction"} <= set(outcomes)


@pytest.mark.parametrize("name", CASES)
def test_the_two_fields_approval_values_leaves_to_the_insert(panel, name):
    """The preview also shows doi_r and alt_identifier_r, which admin_approve takes from
    the record directly: the raw doi_r, never final_doi_r."""
    assert panel[name]["published"]["doi_r"] == CASES[name]["doi_r"]
    assert panel[name]["published"]["alt_identifier_r"] == CASES[name]["alt_identifier_r"]


def test_admin_approve_still_publishes_those_two_as_the_panel_says():
    start = APP_PY.index("def admin_approve(")
    insert = APP_PY[APP_PY.index("INSERT INTO validated", start):APP_PY.index("if not cur.fetchone():", start)]
    assert 'rec["doi_r"], rec["study_r"]' in insert
    assert 'rec.get("doi_r_published"), rec.get("alt_identifier_r"),' in insert
    assert "final_doi_r" not in insert


def test_admin_approve_builds_the_rest_as_approval_values_does():
    """admin_approve writes these with its own expressions, not from approval_values, so
    a change to only one of the two would not show in the comparison above."""
    start = APP_PY.index("def admin_approve(")
    insert = APP_PY[APP_PY.index("INSERT INTO validated", start):APP_PY.index("if not cur.fetchone():", start)]
    for expression in (
        'rec.get("final_title_r") or rec["title_r"]',
        'rec.get("final_url_r") or rec["url_r"]',
        'rec.get("final_abstract_r") or rec["abstract_r"]',
        'rec["final_doi_o"] if rec.get("final_doi_o") is not None else rec["doi_o"]',
        'rec.get("final_title_o") or rec["title_o"]',
        'rec.get("final_outcome_quote") or rec["outcome_quote"]',
        'rec.get("final_out_quote_source") or rec.get("out_quote_source")',
    ):
        assert expression in insert
