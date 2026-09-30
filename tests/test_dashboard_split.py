"""The admin dashboard's confusion matrices, split by type, and the approval
screen's validator track record.

One outcome matrix mixed replication categories with joined reproduction labels
("computational issues, not checked"), compared reproductions by that joined
label — so a validator's axis correction looked like agreement — and read
"Can't tell" as agreement too. Now replications get one outcome matrix and
reproductions one per axis, only over records where both sides are that type,
with empty rows and columns left out.

The approval card showed a validator's total submissions ("55 validated"); it now
shows the validators table's own counts, "16 approved · 1 🚩".

The endpoint checks marked with local_database need a real server and are opt-in
like tests/test_preparation_database.py (FLORA_TEST_DATABASE_URL).
"""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from psycopg2.extras import RealDictCursor

import db_pool
from tests.test_preparation_database import local_database  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _import_app():
    """Import app.py with the database and scheduler stubbed out: init_db() runs at
    import and would otherwise apply db_schema.sql to whatever DATABASE_URL names."""
    os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
    os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")
    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 1}
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor
    with patch("psycopg2.connect", return_value=connection), patch(
        "apscheduler.schedulers.background.BackgroundScheduler.start"
    ):
        import app
    return app


flora = _import_app()

NO_AXES = {"outcome_computation": None, "outcome_robustness": None}


def _v(**fields):
    return {"type_check": "correct", "original_check": "correct", "outcome_check": "correct",
            **fields}


def _a(record_type="replication", outcome="failed", v1=None, v2=None, status="need_review", **axes):
    return {"validation_status": status, "type": record_type, "outcome": outcome,
            **{**NO_AXES, **axes}, "validator_1": v1 or _v(), "validator_2": v2 or _v()}


# ── _confusion ────────────────────────────────────────────────────────────────

def test_a_label_only_ever_beside_a_blank_is_not_an_empty_row_and_column():
    m = flora._confusion([("failed", "failed"), ("mixed", None), (None, "uninformative")])
    assert m == {"labels": ["failed"], "grid": [[1]]}


def test_labels_follow_the_codebook_order_then_the_alphabet():
    m = flora._confusion([("mixed", "successful"), ("zeta", "failed")],
                         ("successful", "failed", "mixed"))
    assert m["labels"] == ["successful", "failed", "mixed", "zeta"]


# ── validator vs validator ────────────────────────────────────────────────────

def test_replications_get_their_own_outcome_matrix():
    d = flora._disagreements([_a(), _a(v2=_v(outcome_check="incorrect", corrected_outcome="mixed"))], [])
    rep = d["validator"]["replication_outcome"]
    assert rep["matrix"] == {"labels": ["failed", "mixed"], "grid": [[1, 1], [0, 0]]}
    assert (rep["records"], rep["unvalidated"]) == (2, 1)


def test_cant_tell_is_its_own_answer_not_agreement():
    unsure = _v(outcome_check="incorrect", additional_checks={"was_unsure_outcome": True})
    rep = flora._disagreements([_a(v2=unsure)], [])["validator"]["replication_outcome"]
    assert rep["matrix"]["labels"] == ["failed", "Can't tell"]
    assert rep["unvalidated"] == 1


def test_reproductions_are_compared_axis_by_axis():
    """The old matrix compared the joined label, so this robustness correction
    counted as agreement."""
    axes = {"outcome_computation": "computationally reproducible", "outcome_robustness": "robust"}
    row = _a("reproduction", "computationally reproducible, robust",
             v1=_v(corrected_outcome_computation="computationally reproducible",
                   corrected_outcome_robustness="robust"),
             v2=_v(outcome_check="incorrect",
                   corrected_outcome_computation="computationally reproducible",
                   corrected_outcome_robustness="robustness challenges"), **axes)
    v = flora._disagreements([row], [])["validator"]
    assert v["reproduction_computation"]["unvalidated"] == 0
    assert v["reproduction_robustness"]["unvalidated"] == 1
    assert v["reproduction_robustness"]["matrix"]["labels"] == ["robust", "robustness challenges"]
    assert v["replication_outcome"]["records"] == 0      # never mixed into the replication one


def test_an_older_summary_without_axes_falls_back_on_what_it_confirmed():
    axes = {"outcome_computation": "technical failure", "outcome_robustness": "not checked"}
    v = flora._disagreements([_a("reproduction", "x", **axes)], [])["validator"]
    assert v["reproduction_computation"]["matrix"] == {"labels": ["technical failure"], "grid": [[1]]}


def test_records_whose_type_the_validators_disagree_on_are_compared_under_type_only():
    split = _a(v2=_v(type_check="incorrect", corrected_type="reproduction", outcome_check="incorrect"))
    v = flora._disagreements([split], [])["validator"]
    assert v["type_split"] == 1
    assert v["type"]["unvalidated"] == 1
    assert v["replication_outcome"]["records"] == 0
    assert v["reproduction_computation"]["records"] == 0


def test_an_assignment_is_compared_on_what_it_showed():
    """An assignment shows earlier decisions over the extracted values. Its "Looks
    right" on an earlier "successful" agrees with a validator who corrected the
    extracted "failed" to "successful" — not a failed-vs-successful split."""
    assignment = _v(is_assignment=True,
                    shown_record={"type": "replication", "outcome": "successful"})
    corrected = _v(outcome_check="incorrect", corrected_outcome="successful")
    rep = flora._disagreements([_a(v1=assignment, v2=corrected)], [])["validator"]["replication_outcome"]
    assert rep["matrix"] == {"labels": ["successful"], "grid": [[1]]}
    assert rep["unvalidated"] == 0


def test_an_assignment_that_confirmed_an_earlier_type_change_agrees_with_it():
    assignment = _v(is_assignment=True, outcome_check="incorrect",
                    shown_record={"type": "reproduction"})
    moved = _v(type_check="incorrect", corrected_type="reproduction", outcome_check="incorrect")
    v = flora._disagreements([_a(v1=assignment, v2=moved)], [])["validator"]
    assert (v["type"]["unvalidated"], v["type_split"]) == (0, 0)


def test_an_older_assignment_without_a_snapshot_falls_back_on_the_record():
    v = flora._disagreements([_a(v1=_v(is_assignment=True))], [])["validator"]
    assert v["replication_outcome"]["matrix"] == {"labels": ["failed"], "grid": [[1]]}


def test_cant_tell_on_the_original_is_its_own_answer_too():
    unsure = _v(original_check="incorrect", additional_checks={"was_unsure_original": True})
    v = flora._disagreements([_a(v2=unsure)], [])["validator"]
    assert v["original"]["matrix"]["labels"] == ["correct", "Can't tell"]


# ── pipeline vs final ─────────────────────────────────────────────────────────

def _b(record_type="replication", outcome="failed", final_type=None, final_outcome=None,
       doi_o="10.1/o", final_doi_o=None, **axes):
    return {"type": record_type, "outcome": outcome, **{**NO_AXES, **axes},
            "final_type": final_type, "final_outcome": final_outcome,
            "final_outcome_computation": axes.get("final_outcome_computation"),
            "final_outcome_robustness": axes.get("final_outcome_robustness"),
            "doi_o": doi_o, "final_doi_o": final_doi_o}


def test_the_pipeline_view_splits_the_same_way():
    rows = [
        _b(final_outcome="mixed"),
        _b("reproduction", "x", outcome_computation="technical failure", outcome_robustness="robust",
           final_type="reproduction", final_outcome_computation="computational issues"),
        _b(final_type="reproduction", final_doi_o="10.1/fixed"),       # type changed
    ]
    p = flora._disagreements([], rows)["pipeline"]
    assert p["replication_outcome"]["count"] == 1
    assert p["reproduction_computation"]["matrix"]["labels"] == ["computational issues", "technical failure"]
    assert p["reproduction_computation"]["count"] == 1
    assert p["reproduction_robustness"]["count"] == 0
    assert (p["type_changed"], p["type"]["count"], p["original"]["count"]) == (1, 1, 1)


# ── the dashboard's rendering ─────────────────────────────────────────────────

APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def _js_function(signature):
    start = APP_JS.index(signature)
    return APP_JS[start:APP_JS.index("\n}\n", start)]


def test_the_dashboard_shows_replications_and_reproductions_separately():
    render = _js_function("function _renderDisagree(view)")
    for piece in ('group("Replications")', 'group("Reproductions")',
                  'dim("Outcome", v.replication_outcome)', 'dim("Computation", v.reproduction_computation)',
                  'dim("Robustness", v.reproduction_robustness)', "v.type_split",
                  'dim("Outcome", p.replication_outcome)', "p.type_changed", "compared"):
        assert piece in render, piece
    assert 'x.matrix' in render and "dd.validator[k]" not in render


def test_neither_type_reads_as_such_in_a_matrix():
    assert 's === "not_validation" ? "Neither type"' in _js_function("function _renderMatrix(m, rowLabel, colLabel)")


# ── the approval screen's track record ────────────────────────────────────────

def test_the_card_shows_approved_and_flagged_not_the_total():
    assert _js_function("function _trackRecordText(st)").count("st.approved") == 1
    assert "validated · " not in _js_function("function renderAdminDetail(data)")
    title = _js_function("function _trackRecordTitle(st)")
    assert "st.judged" in title                           # the total, on hover
    assert "chip.textContent = _trackRecordText(stats);" in APP_JS   # kept after a flag toggle


def test_the_card_and_the_validators_table_count_approved_the_same_way():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert source.count("{_APPROVED_COUNT_SQL} AS approved") == 2
    assert "AND au.validation_status = 'validated') AS approved_count" not in source


@pytest.fixture
def admin_api(local_database, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(flora, "DATABASE_URL", os.environ["DATABASE_URL"])
    flora.app.dependency_overrides[flora.current_admin] = lambda: {"handle": "admin", "id": 1}
    try:
        yield TestClient(flora.app)
    finally:
        flora.app.dependency_overrides.clear()
        db_pool.clear_all()


def test_the_approval_card_counts_what_the_validators_table_counts(admin_api, local_database):
    """16 approved · 1 🚩 for a validator with 55 submissions, in miniature: four
    judgements, one on a record an admin approved, one flagged, and one on a record
    auto-validated without an admin — not an approval."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("INSERT INTO validators (handle, total_judgements) VALUES ('m.sparhuber', 3) "
                    "RETURNING id")
        vid = cur.fetchone()["id"]
        summary = json.dumps({"validator_id": vid, "validator_name": "m.sparhuber",
                              "type_check": "correct", "original_check": "correct",
                              "outcome_check": "correct"})
        ids = []
        for status, flagged, by_admin in (("validated", False, True), ("need_review", True, False),
                                          ("consensus_reached", False, False),
                                          ("validated", False, False)):
            cur.execute("INSERT INTO unvalidated (doi_r, type, outcome, validation_status, validator_1, "
                        "admin_checked) VALUES ('10.9/r', 'replication', 'failed', %s, %s, %s) "
                        "RETURNING record_id::text AS id",
                        (status, summary, by_admin))
            record_id = cur.fetchone()["id"]
            ids.append(record_id)
            cur.execute("INSERT INTO validation_queue (record_id, validator_slot, is_shown, is_validated, "
                        "validator_id, validator_name, flagged) VALUES (%s, 'human_1', TRUE, TRUE, %s, "
                        "'m.sparhuber', %s)", (record_id, vid, flagged))
    card = admin_api.get(f"/api/admin/entries/{ids[1]}")
    assert card.status_code == 200, card.text
    stats = card.json()["validator_stats"][str(vid)]
    assert (stats["approved"], stats["flags"], stats["judged"]) == (1, 1, 3)
    table = admin_api.get("/api/admin/stats")
    assert table.status_code == 200, table.text
    row = next(v for v in table.json()["validators"] if v["id"] == vid)
    assert (row["approved_count"], row["flagged_count"]) == (stats["approved"], stats["flags"])


def test_outcome_corrections_leave_out_only_a_pure_cant_tell(admin_api, local_database):
    """"Can't tell" corrects nothing. But on a reproduction was_unsure_outcome means
    either axis was unsure, and the other axis may still have been corrected."""
    judgements = [
        ("replication", {"was_unsure_outcome": True}),                        # Can't tell
        ("replication", None),                                                 # a correction
        ("reproduction", {"was_unsure_outcome": True,                          # both unsure
                          "reproduction_axis_checks": {"computation": "unsure",
                                                       "robustness": "unsure"}}),
        ("reproduction", {"was_unsure_outcome": True,                          # one corrected
                          "reproduction_axis_checks": {"computation": "wrong",
                                                       "robustness": "unsure"}}),
        ("reproduction", {"was_unsure_outcome": True,
                          "reproduction_axis_checks": {"computation": "correct",
                                                       "robustness": "wrong"}}),
    ]
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        for record_type, checks in judgements:
            cur.execute("INSERT INTO unvalidated (doi_r, type, outcome, validation_status) "
                        "VALUES ('10.9/c', %s, 'failed', 'need_review') "
                        "RETURNING record_id::text AS id", (record_type,))
            cur.execute("INSERT INTO validation_queue (record_id, validator_slot, is_shown, "
                        "is_validated, type_check, original_check, outcome_check, additional_checks) "
                        "VALUES (%s, 'human_1', TRUE, TRUE, 'correct', 'correct', 'incorrect', %s)",
                        (cur.fetchone()["id"], json.dumps(checks) if checks else None))
    dashboard = admin_api.get("/api/admin/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["corrections"]["outcome_corrections"] == 3


# ── outcome quote corrections ─────────────────────────────────────────────────

def _record(cur, record_type="replication", status="need_review", **quotes):
    columns = ["doi_r", "type", "outcome", "validation_status", *quotes]
    cur.execute(f"INSERT INTO unvalidated ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING record_id::text AS id",
                ("10.9/q", record_type, "failed", status, *quotes.values()))
    return cur.fetchone()["id"]


def test_a_reworded_quote_counts_as_a_quote_correction_whatever_button(admin_api, local_database):
    """"Right outcome, better quote", "Looks right" with a reworded quote and an old
    "Mischaracterised → the same outcome" all corrected the quote. Punctuation, case
    and spacing alone did not; nor did leaving it."""
    extracted = "The effect replicated, but only partially."
    judgements = [
        ("replication", {"outcome_quote": extracted},
         {"outcome_check": "correct", "corrected_outcome_quote": "The effect did not replicate.",
          "additional_checks": {"outcome_quote_disputed": True}}),                  # counts
        ("replication", {"outcome_quote": extracted},
         {"outcome_check": "correct",
          "corrected_outcome_quote": extracted + " Both samples were small."}),      # counts
        ("replication", {"outcome_quote": extracted},
         {"outcome_check": "correct", "corrected_outcome_quote": "the effect replicated but "
                                                                 "only partially"}),  # wording same
        ("replication", {"outcome_quote": extracted}, {"outcome_check": "correct"}),  # no edit
        ("replication", {"outcome_quote": extracted},
         {"outcome_check": "correct", "corrected_outcome_quote": "Mixed evidence overall.",
          "additional_checks": {"outcome_agreement_backfilled": True,
                                "outcome_quote_disputed": True}}),                  # counts
        ("reproduction", {"outcome_computational_quote": "The code ran."},
         {"outcome_check": "incorrect",
          "corrected_computational_quote": "The code ran after two fixes."}),         # counts
    ]
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        for record_type, quotes, judgement in judgements:
            record_id = _record(cur, record_type, **quotes)
            checks = judgement.pop("additional_checks", None)
            columns = ["record_id", "validator_slot", "is_shown", "is_validated", "type_check",
                       "original_check", *judgement, "additional_checks"]
            cur.execute(f"INSERT INTO validation_queue ({', '.join(columns)}) "
                        f"VALUES ({', '.join(['%s'] * len(columns))})",
                        (record_id, "human_1", True, True, "correct", "correct",
                         *judgement.values(), json.dumps(checks) if checks else None))
    dashboard = admin_api.get("/api/admin/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    corrections = dashboard.json()["corrections"]
    assert corrections["outcome_quote_corrections"] == 4
    assert corrections["outcome_corrections"] == 1        # only the reproduction's axis


def test_the_pipeline_counts_final_quotes_reworded_from_the_extracted(admin_api, local_database):
    extracted = "The effect replicated."
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        _record(cur, status="validated", outcome_quote=extracted,
                final_outcome_quote="The effect did not replicate.")                   # counts
        _record(cur, status="validated", outcome_quote=extracted,
                final_outcome_quote="the effect  replicated")                           # wording same
        _record(cur, status="validated", outcome_quote=extracted)                       # kept
        _record(cur, "reproduction", status="validated",
                outcome_robustness_quote="Robust.", final_robustness_quote="Robust to all checks.")
        _record(cur, status="need_review", outcome_quote=extracted,
                final_outcome_quote="Not validated yet.")                               # not final
    dashboard = admin_api.get("/api/admin/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["disagreements"]["pipeline"]["outcome_quote"] == {"count": 2}


def test_the_dashboard_shows_the_corrections_and_the_quote_line():
    render = _js_function("function renderAdminDashboard(d) {")
    assert '["Outcome quote", c.outcome_quote_corrections,' in render
    for label in ('["Type", c.type_corrections,', '["Original", c.original_corrections,',
                  '["Outcome", c.outcome_corrections,', '["Title", c.title_corrections,'):
        assert label in render
    disagree = _js_function("function _renderDisagree(view) {")
    assert "final quote reworded" in disagree and "p.outcome_quote.count" in disagree


def _judgement(cur, record_id, checks=None, **columns):
    columns = {"type_check": "correct", "original_check": "correct", "outcome_check": "correct",
               **columns}
    names = ["record_id", "validator_slot", "is_shown", "is_validated", *columns, "additional_checks"]
    cur.execute(f"INSERT INTO validation_queue ({', '.join(names)}) "
                f"VALUES ({', '.join(['%s'] * len(names))})",
                (record_id, "human_1", True, True, *columns.values(),
                 json.dumps(checks) if checks else None))


def test_neither_type_counts_under_type_alone(admin_api, local_database):
    """"Neither type" is sent with every check incorrect; it flagged no original and
    changed no outcome."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        _judgement(cur, _record(cur), type_check="incorrect", corrected_type="not_validation",
                   original_check="incorrect", outcome_check="incorrect")
    corrections = admin_api.get("/api/admin/dashboard").json()["corrections"]
    assert (corrections["type_corrections"], corrections["original_corrections"],
            corrections["outcome_corrections"]) == (1, 0, 0)


def test_the_quote_count_follows_the_rule_that_earns_the_point(admin_api, local_database):
    """Only a quote of the type judged, not "Can't tell", and — once judgements
    record it — what was decided against the quote shown, not the record's now."""
    extracted = "The effect replicated."
    reworded = "The effect did not replicate at all."
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        _judgement(cur, _record(cur, outcome_quote=extracted), corrected_outcome_quote=reworded,
                   type_check="incorrect", corrected_type="not_validation",
                   original_check="incorrect", outcome_check="incorrect")      # left from before
        _judgement(cur, _record(cur, outcome_quote=extracted), corrected_outcome_quote=reworded,
                   type_check="incorrect", corrected_type="reproduction",
                   outcome_check="incorrect")                                  # the other type's
        _judgement(cur, _record(cur, outcome_quote=extracted), corrected_outcome_quote=reworded,
                   outcome_check="incorrect", checks={"was_unsure_outcome": True})   # Can't tell
        for _ in range(2):
            _judgement(cur, _record(cur, outcome_quote=reworded), corrected_outcome_quote=reworded,
                       checks={"outcome_quote_reworded": True})     # the import caught up since
        _judgement(cur, _record(cur, outcome_quote=extracted), corrected_outcome_quote=reworded,
                   checks={"outcome_quote_reworded": False})        # the import moved away since
    corrections = admin_api.get("/api/admin/dashboard").json()["corrections"]
    assert corrections["outcome_quote_corrections"] == 2


def test_the_pipeline_quote_line_leaves_type_changes_to_type(admin_api, local_database):
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        _record(cur, status="validated", final_type="reproduction",        # type changed, and
                outcome_quote="The effect replicated.",                     # its replication quote
                final_outcome_quote="The code ran and the effect held.",    # rewritten with it
                final_computational_quote="The code ran.")
        _record(cur, status="validated", outcome_quote="The effect replicated.",
                final_outcome_quote="The effect did not replicate.")
    pipeline = admin_api.get("/api/admin/dashboard").json()["disagreements"]["pipeline"]
    assert (pipeline["type_changed"], pipeline["outcome_quote"]["count"]) == (1, 1)
