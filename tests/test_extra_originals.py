"""Additional originals: a replication with more than one original study.

Validators suggest the other originals with their judgement; an admin adds each
one as a record of its own, linked to the extractor record (extra_originals.py).
These tests need no database: the helpers are pure or run against a recording
cursor, and the wiring is checked in the source. The database behaviour is in
tests/test_extra_originals_database.py (opt-in, FLORA_TEST_DATABASE_URL).
"""
import inspect
import json
import re
from pathlib import Path

import psycopg2
import pytest

import csv_to_db
import extra_originals as xo

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
FIND_ORPHANS = (ROOT / "find_orphans.py").read_text(encoding="utf-8")
XO_SCHEMA = SCHEMA.split("-- ── Additional originals", 1)[1]


class RecordingCursor:
    """Records every statement; raises `fail_on` (a psycopg2 error) for the first
    statement containing `fail_text`."""

    def __init__(self, fail_text=None):
        self.statements = []
        self.fail_text = fail_text

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if self.fail_text and self.fail_text in sql:
            self.fail_text = None
            raise psycopg2.errors.CheckViolation("boom")

    def sql(self):
        return [s for s, _ in self.statements]


# ── normalising a suggestion ──────────────────────────────────────────────────

def test_a_doi_is_stored_bare_whatever_form_it_was_pasted_in():
    for raw in ("https://doi.org/10.1000/ABC", "doi:10.1000/ABC", " 10.1000/ABC "):
        s = xo.normalize_suggestion({"doi_or_url": raw})
        assert s["doi_o"] == "10.1000/ABC" and not s["url_o"]


def test_a_link_that_is_not_a_doi_is_kept_as_a_link():
    s = xo.normalize_suggestion({"doi_or_url": "https://osf.io/abcd/", "title": "T"})
    assert s["url_o"] == "https://osf.io/abcd/" and s["doi_o"] == ""


def test_text_that_is_neither_doi_nor_link_is_kept_as_the_title_not_refused():
    """The judgement this rides on is sent from a queue that gives up on any 4xx,
    so a typo here must never be a reason to refuse it."""
    s = xo.normalize_suggestion({"doi_or_url": "Smith 2019", "title": "Priming"})
    assert s["title_o"] == "Priming — Smith 2019" and not s["doi_o"] and not s["url_o"]
    assert xo.normalize_suggestion({"doi_or_url": "Smith 2019"})["title_o"] == "Smith 2019"


def test_a_script_link_never_becomes_a_link():
    s = xo.normalize_suggestion({"doi_or_url": "javascript:alert(1)"})
    assert s["url_o"] == "" and s["title_o"] == "javascript:alert(1)"


def test_a_suggestion_naming_no_paper_is_dropped():
    assert xo.normalize_suggestion({"study": "2", "note": "see table 3"}) is None
    assert xo.normalize_suggestion({}) is None
    assert xo.normalize_suggestion("10.1/x") is None
    assert xo.normalize_suggestion(None) is None


def test_garbage_in_every_field_is_tolerated():
    s = xo.normalize_suggestion({"doi_or_url": ["x"], "title": {"a": 1}, "study": 7,
                                 "note": True})
    assert s is None
    s = xo.normalize_suggestion({"doi_or_url": 12345, "title": None, "study": 2.5})
    assert s["title_o"] == "12345" and s["study_o"] == "2.5"


def test_over_long_text_is_cut_to_the_limits():
    s = xo.normalize_suggestion({"title": "t" * 5000, "study": "9" * 99, "note": "n" * 5000})
    assert len(s["title_o"]) == xo.SUGGESTION_LIMITS["title"]
    assert len(s["study_o"]) == xo.SUGGESTION_LIMITS["study"]
    assert len(s["note"]) == xo.SUGGESTION_LIMITS["note"]


def test_two_suggestions_of_one_paper_share_a_key_whatever_the_case():
    a = xo.normalize_suggestion({"doi_or_url": "https://doi.org/10.1/ABC"})
    b = xo.normalize_suggestion({"doi_or_url": "10.1/abc"})
    assert xo.suggestion_key(a) == xo.suggestion_key(b)
    t1 = xo.normalize_suggestion({"title": "The Effect, of X!"})
    t2 = xo.normalize_suggestion({"title": "the effect of x"})
    assert xo.suggestion_key(t1) == xo.suggestion_key(t2)


# ── storing a judgement's suggestions ─────────────────────────────────────────

def test_suggestions_are_stored_one_row_each_under_a_savepoint():
    cur = RecordingCursor()
    n = xo.store_suggestions(cur, "rec-1", "q-1", 7, "ana",
                             [{"doi_or_url": "10.1/a"}, {"title": "Second"}], "replication")
    assert n == 2
    sql = cur.sql()
    assert sql[0] == "SAVEPOINT extra_original_suggestions"
    assert sql[-1] == "RELEASE SAVEPOINT extra_original_suggestions"
    inserts = [p for s, p in cur.statements if "INSERT INTO extra_original_suggestions" in s]
    assert [p[4] for p in inserts] == [0, 1]                     # position
    assert inserts[0][:4] == ("rec-1", "q-1", 7, "ana")
    assert inserts[0][5] == "10.1/a" and inserts[1][7] == "Second"


def test_a_database_error_drops_the_suggestions_but_not_the_judgement():
    """The judgement is already written in the same transaction; rolling back to
    the savepoint keeps it."""
    cur = RecordingCursor(fail_text="INSERT INTO extra_original_suggestions")
    assert xo.store_suggestions(cur, "rec-1", None, 7, "ana",
                                [{"title": "A"}], "replication") == 0
    assert "ROLLBACK TO SAVEPOINT extra_original_suggestions" in cur.sql()
    assert "RELEASE SAVEPOINT extra_original_suggestions" not in cur.sql()


def test_titles_in_other_scripts_are_not_all_the_same_paper():
    """norm_title kept a-z0-9 only, so every title without Latin letters became ''
    and two different Cyrillic or Chinese suggestions were stored (and listed) as one."""
    a = xo.normalize_suggestion({"title": "Эффект прайминга"})
    b = xo.normalize_suggestion({"title": "Влияние сна на память"})
    assert xo.suggestion_key(a) != xo.suggestion_key(b)
    assert xo.suggestion_key(a) == xo.suggestion_key(xo.normalize_suggestion({"title": "эффект, прайминга!"}))
    cur = RecordingCursor()
    assert xo.store_suggestions(cur, "r", None, 1, "v", [{"title": "启动效应"}, {"title": "睡眠与记忆"}],
                                "replication") == 2
    assert xo.same_original("", "启动效应", "", "睡眠与记忆") is False


def test_the_entries_badge_counts_suggested_papers_as_the_section_does():
    entries = APP.split("def admin_entries(", 1)[1].split("\n@app.", 1)[0]
    assert "COUNT(DISTINCT lower(COALESCE(NULLIF(xs.doi_o, ''), NULLIF(xs.url_o, ''), xs.title_o)))" in entries


def test_nothing_is_stored_for_neither_type_or_a_payload_that_is_not_a_list():
    for items, judged in (([{"title": "A"}], "not_validation"), ({"title": "A"}, "replication"),
                          ("10.1/a", "replication"), (None, "replication"), ([], "reproduction")):
        cur = RecordingCursor()
        assert xo.store_suggestions(cur, "r", None, 1, "v", items, judged) == 0
        assert cur.statements == []


def test_duplicates_within_a_judgement_are_stored_once_and_extras_ignored():
    cur = RecordingCursor()
    items = [{"doi_or_url": "10.1/A"}, {"doi_or_url": "https://doi.org/10.1/a"}]
    items += [{"title": f"Paper {i}"} for i in range(20)]
    n = xo.store_suggestions(cur, "r", None, 1, "v", items, "replication")
    # The first ten items are read; one of them repeats a DOI.
    assert n == xo.MAX_SUGGESTIONS - 1


def test_the_log_writes_explicit_changes_with_a_handle():
    cur = RecordingCursor()
    xo.log_decision(cur, "r", {"id": 3, "handle": "sophie"}, "extra_original_added",
                    {"extra_original": [None, "c"]}, None, "validated")
    sql, params = cur.statements[0]
    assert "INSERT INTO admin_decisions" in sql
    assert params[:4] == ("r", 3, "sophie", "extra_original_added")
    xo.log_decision(cur, "r", None, "rejected_with_parent", {})
    assert cur.statements[1][1][2] == "system"


# ── schema ────────────────────────────────────────────────────────────────────

def test_the_schema_additions_are_idempotent_and_safe_for_extractor_inserts():
    """db_schema.sql runs on every start, and flora-extractor inserts rows through
    PostgREST naming only its own columns, so every new column needs a default."""
    assert "ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'extractor'" in XO_SCHEMA
    assert "ADD COLUMN IF NOT EXISTS parent_record_id UUID REFERENCES unvalidated(record_id)" in XO_SCHEMA
    assert "ADD COLUMN IF NOT EXISTS inherits_outcome BOOLEAN NOT NULL DEFAULT FALSE" in XO_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS extra_original_suggestions" in XO_SCHEMA
    for line in XO_SCHEMA.splitlines():
        stripped = line.strip()
        if stripped.startswith(("CREATE INDEX", "CREATE TABLE")):
            assert "IF NOT EXISTS" in stripped, stripped
    # The one constraint is added only inside its pg_constraint guard.
    assert XO_SCHEMA.count("ADD CONSTRAINT") == 1
    guard = XO_SCHEMA.split("conname = 'unvalidated_origin_shape'", 1)[1]
    assert guard.index("ADD CONSTRAINT unvalidated_origin_shape") < guard.index("END IF")


def test_an_added_original_can_never_carry_a_pair_id():
    """pair_id is the extractor's identity; a record with one would be re-keyed,
    refreshed or retired by the nightly sync."""
    shape = XO_SCHEMA.split("ADD CONSTRAINT unvalidated_origin_shape CHECK", 1)[1].split("END IF", 1)[0]
    assert "origin = 'admin_extra_original' AND parent_record_id IS NOT NULL" in shape
    assert "pair_id IS NULL" in shape
    assert "parent_record_id <> record_id" in shape
    assert "origin = 'extractor' AND parent_record_id IS NULL" in shape


def test_retiring_a_record_removes_its_suggestions_first():
    body = inspect.getsource(csv_to_db.delete_source_records)
    suggestions = body.index("DELETE FROM extra_original_suggestions")
    assert suggestions < body.index("DELETE FROM validation_queue")
    assert suggestions < body.index("DELETE FROM unvalidated")


def test_the_orphan_report_ignores_added_originals():
    assert "FROM unvalidated WHERE origin = 'extractor'" in FIND_ORPHANS


def test_the_validators_coded_set_lists_extractor_records_only():
    where = re.search(r"_CODED_ORIGINALS_WHERE = \((.*?)\)\n", APP, re.S).group(1)
    assert "u.origin = 'extractor'" in where
    assert "lower(u.doi_r) = lower(%s)" in where


def test_the_extractor_sync_keys_on_pair_id():
    """The import, re-key and retire stages never see a record without a pair_id."""
    assert "WHERE pair_id IS NOT NULL" in inspect.getsource(csv_to_db._load_existing_pair_ids)
    assert "WHERE u.pair_id IS NOT NULL" in inspect.getsource(csv_to_db._load_existing_source_slots)
    assert "u.pair_id = ANY(%s)" in inspect.getsource(csv_to_db._retire_state)


@pytest.mark.parametrize("value,expected", [
    ("https://dx.doi.org/10.5/x", "10.5/x"), ("DOI: 10.5/x", "10.5/x"), ("", ""), (None, ""),
])
def test_normalize_doi(value, expected):
    assert xo.normalize_doi(value) == expected


# ── phase 2: the judgement carries the suggestions ────────────────────────────

APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def _flora():
    from tests.test_dashboard_split import flora   # app.py, imported with the DB stubbed
    return flora


def _judge_body(fn):
    return APP.split(f"def {fn}(", 1)[1].split("\n@app.", 1)[0]


def test_an_older_page_without_suggestions_still_validates():
    req = _flora().JudgeRequest(record_id="r", type_check="correct",
                                original_check="correct", outcome_check="correct")
    assert req.extra_originals is None


@pytest.mark.parametrize("garbage", [
    "10.1/a", 7, {"title": "x"}, [1, None, "x"], [{"title": "t" * 50_000}] * 60,
    [{"doi_or_url": {"nested": True}, "title": ["list"], "study": None}],
])
def test_no_suggestion_payload_can_make_a_judgement_fail_validation(garbage):
    """A 422 here would be terminal in the browser's background queue and the
    whole judgement would be lost (docs/app.js _isTerminalErr)."""
    req = _flora().JudgeRequest(record_id="r", type_check="correct", original_check="correct",
                                outcome_check="correct", extra_originals=garbage)
    assert req.extra_originals == garbage


def test_judge_stores_suggestions_after_its_one_shot_guard_and_before_consensus():
    body = _judge_body("judge")
    store = body.index("extra_originals.store_suggestions(")
    assert body.index('raise HTTPException(409, "This record was already submitted")') < store
    assert store < body.index("evaluate_consensus(cur, record_id)")
    assert '"extra_original_count": extra_original_count' in body


def test_an_assignment_stores_suggestions_after_its_one_shot_guard():
    body = _judge_body("assignment_judge")
    store = body.index("extra_originals.store_suggestions(")
    assert body.index('raise HTTPException(409, "This assignment was already submitted")') < store
    assert "req.extra_originals, final_type)" in body[store:store + 200]


def test_the_page_sends_complete_suggestions_only_and_never_for_neither():
    assert "extra_originals:         isNotValidation ? null : _extraOriginalsPayload(j)," in APP_JS
    payload = APP_JS.split("function _extraOriginalsPayload(j) {", 1)[1].split("\n}\n", 1)[0]
    assert ".filter(_xoComplete)" in payload and ".slice(0, XO_MAX)" in payload
    assert "if (!j.has_extra_originals" in payload


def test_the_panel_lives_between_the_gates_and_not_in_onboarding():
    """An answered gate collapses its body, so the panel cannot sit inside Gate II."""
    gate2_end = APP_JS.index('<div id="oc-saved-confirm"')
    block = APP_JS.index('${onboarding ? "" : _extraOriginalsBlock(p)}')
    assert gate2_end < block < APP_JS.index('<div class="gate hidden" id="gate-3">')


def test_the_browser_enforces_the_limits_the_server_only_cuts_to():
    limits = re.search(r"const XO_LIMITS = \{(.*?)\};", APP_JS).group(1)
    for name, limit in xo.SUGGESTION_LIMITS.items():
        assert f"{name}: {limit}" in limits
    assert f"const XO_MAX = {xo.MAX_SUGGESTIONS};" in APP_JS
    assert 'maxlength="${XO_LIMITS[name]}"' in APP_JS


def test_a_draft_restores_its_suggestions():
    restore = APP_JS.split("function _restoreDraftInputs(card, draft) {", 1)[1].split("\n}\n", 1)[0]
    assert "draft.has_extra_originals" in restore
    assert "_renderExtraOriginalsList(card)" in restore


# ── phase 3: what the admin sees ──────────────────────────────────────────────

class ScriptedCursor:
    """Answers each statement with the rows of the first script entry whose
    marker the SQL contains."""

    def __init__(self, script):
        self.script = script
        self.statements = []
        self.rows = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.rows = next((rows for marker, rows in self.script if marker in sql), [])

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


PARENT = {"record_id": "p", "origin": "extractor", "validation_status": "validated",
          "admin_checked": True, "doi_r": "10.1/REP", "type": "replication",
          "final_type": None, "final_doi_o": None, "doi_o": "10.1/own", "title_o": "Own"}
PUBLISHED = {"record_id": "p", "type": "replication", "doi_r": "10.1/REP", "doi_o": "10.1/own",
             "title_o": "Own", "original_key": "10.1/own", "outcome": "failed"}


def _suggestion(sid, doi="", title="", status="open", who="ana", vid=1, **extra):
    return {"suggestion_id": sid, "doi_o": doi, "url_o": "", "title_o": title, "study_o": "",
            "note": None, "status": status, "validator_id": vid, "validator_name": who,
            "resolved_record_id": None, **extra}


def test_suggestions_of_one_paper_are_one_row_with_everyone_who_made_them():
    groups = xo._group_suggestions([
        _suggestion(1, doi="10.5/AB", who="ana", study_o="1", note="Study 2"),
        _suggestion(2, doi="10.5/ab", who="ben", vid=2, study_o="2", note="Study 2"),
        _suggestion(3, title="A Book", who="ben", vid=2, status="dismissed"),
    ])
    assert len(groups) == 2
    doi = next(g for g in groups if g["doi_o"])
    assert [s["validator_name"] for s in doi["suggested_by"]] == ["ana", "ben"]
    assert doi["study_o"] == "1, 2" and doi["notes"] == ["Study 2"]
    assert doi["status"] == "open" and doi["open_suggestion_ids"] == [1, 2]
    book = next(g for g in groups if not g["doi_o"])
    assert book["status"] == "dismissed" and book["open_suggestion_ids"] == []


@pytest.mark.parametrize("a,b,same", [
    (("10.1/A", ""), ("https://doi.org/10.1/a", ""), True),
    (("10.1/A", "T"), ("10.1/B", "T"), False),
    (("", "The Book!"), ("", "the book"), True),
    (("", "The Book"), ("10.1/x", "The Book"), False),
    (("", ""), ("", ""), False),
])
def test_same_original(a, b, same):
    assert xo.same_original(*a, *b) is same


def test_originals_are_added_only_to_an_admin_reviewed_published_record():
    assert xo.parent_block(PARENT, PUBLISHED) is None
    assert xo.parent_block({**PARENT, "origin": "admin_extra_original"}, PUBLISHED)[0] == "extra_original_on_child"
    assert xo.parent_block({**PARENT, "validation_status": "need_review"}, PUBLISHED)[0] == "parent_not_validated"
    assert xo.parent_block(PARENT, None)[0] == "parent_not_validated"
    assert xo.parent_block({**PARENT, "admin_checked": False}, PUBLISHED)[0] == "parent_not_admin_checked"
    # Not "only after validation": the reasons point to drafting with Mark as Resolved.
    for rec, pub in (({**PARENT, "validation_status": "need_review"}, None),
                     ({**PARENT, "admin_checked": False}, PUBLISHED)):
        assert "saved when you click Mark as Resolved" in xo.parent_block(rec, pub)[1]


# ── drafting originals to be saved with Mark as Resolved ──────────────────────

JUDGED = [{"validator_slot": "human_1"}, {"validator_slot": "human_2"}]
LLM_OK = {"model": "m", "context": "sanity_check", "type_check": "correct",
          "original_check": "correct", "outcome_check": "correct"}
PENDING = {**PARENT, "validation_status": "need_review", "admin_checked": False,
           "llm_validator": LLM_OK, "parent_record_id": None, "study_r": "1",
           "abstract_r": "Raw abstract.", "final_abstract_r": None}
SURVIVOR_ID = "5a5a5a5a-0000-4000-8000-000000000000"


def _gate_route(slots=JUDGED, merged=None):
    def route(sql, params):
        if "FROM validation_queue" in sql:
            return list(slots)
        if "FROM validated_record_merges" in sql:
            return [{"survivor_record_id": merged}] if merged else []
        if "FROM unvalidated u LEFT JOIN validated v" in sql:
            return [{"record_id": params[0], "validation_status": "validated", "parent_record_id": None,
                     "title_o": "The survivor", "doi_o": "10.1/own"}]
        return []
    return route


def test_drafting_needs_both_validators_and_an_llm_answer():
    cur = RoutingCursor(_gate_route())
    assert xo.draft_block(cur, PENDING) is None
    sql, params = next((s, p) for s, p in cur.statements if "FROM validation_queue" in s)
    assert "is_validated = TRUE AND validator_slot IN ('human_1', 'human_2')" in sql
    assert params == ("p",)
    # The answer may arrive as a JSON string.
    assert xo.draft_block(RoutingCursor(_gate_route()), {**PENDING, "llm_validator": json.dumps(LLM_OK)}) is None

    code, reason, extra = xo.draft_block(RoutingCursor(_gate_route(slots=JUDGED[:1])),
                                         {**PENDING, "llm_validator": None})
    assert code == "judgements_missing"
    assert extra["missing"] == ["validator 2's judgement", "the LLM check"]
    assert reason.endswith("Still missing: validator 2's judgement and the LLM check.")
    # An LLM call that failed judged nothing.
    _, reason, extra = xo.draft_block(RoutingCursor(_gate_route()),
                                      {**PENDING, "llm_validator": json.dumps({"error": "timeout"})})
    assert extra["missing"] == ["the LLM check (its last call failed)"]
    # Nor did an empty answer.
    for empty in ({}, "{}"):
        _, _, extra = xo.draft_block(RoutingCursor(_gate_route()), {**PENDING, "llm_validator": empty})
        assert extra["missing"] == ["the LLM check"]


def test_drafting_is_refused_on_a_child_a_merged_entry_a_reject_or_another_admin_s_decision():
    cur = RoutingCursor(_gate_route())
    child = {**PENDING, "origin": "admin_extra_original", "parent_record_id": "p0"}
    assert xo.draft_block(cur, child)[0] == "extra_original_on_child"
    code, reason, extra = xo.draft_block(cur, PENDING, merged_into_id=SURVIVOR_ID)
    assert code == "entry_already_merged"
    assert reason == ("This entry was merged into entry 5a5a5a5a (“The survivor”, 10.1/own, validated). "
                      "Add originals on that entry instead.")
    assert extra["survivor"]["record_id"] == SURVIVOR_ID and extra["survivor"]["relation"] == "survivor"
    mine = {"allowed": False, "reason": "You validated this entry, so another admin needs to decide it."}
    assert xo.draft_block(cur, PENDING, decision=mine)[:2] == ("not_your_decision", mine["reason"])
    assert xo.draft_block(cur, PENDING, decision={"allowed": True}) is None
    assert xo.draft_block(cur, PENDING, final_type="not_validation")[0] == "extra_originals_with_reject"


def test_the_resolve_refuses_drafts_it_may_not_take_and_says_what_to_do():
    err = _refusal(xo.check_resolve_drafts, RoutingCursor(_gate_route()),
                   {**PENDING, "llm_validator": None}, "replication", 2)
    assert (err.status, err.detail["code"], err.detail["missing"]) == (409, "judgements_missing", ["the LLM check"])
    assert err.detail["message"] == (
        "Nothing was saved. Other originals can be added to this entry once the LLM and both "
        "validators have judged it. Still missing: the LLM check. Remove the 2 other originals "
        "you drafted and resolve the entry; once it is resolved, originals are added to it straight away.")
    err = _refusal(xo.check_resolve_drafts, RoutingCursor(_gate_route()), PENDING, "not_validation", 1)
    assert err.detail["code"] == "extra_originals_with_reject"
    assert "Remove the 1 other original you drafted to reject it" in err.detail["message"]
    err = _refusal(xo.check_resolve_drafts, RoutingCursor(_gate_route()), PENDING, None, 1,
                   merged_into_id=SURVIVOR_ID)
    assert err.detail["code"] == "entry_already_merged" and err.detail["survivor"]["short_id"] == "5a5a5a5a"
    xo.check_resolve_drafts(RoutingCursor(_gate_route()), PENDING, "reproduction", 3)


@pytest.mark.parametrize("rec,slots,decision,mode,code", [
    (PARENT, JUDGED, None, "add", None),                                    # validated, admin-checked
    (PENDING, JUDGED, None, "draft", None),                                 # all three judgements
    ({**PENDING, "validation_status": "validated"}, JUDGED, None, "draft", None),   # auto-validated
    ({**PENDING, "llm_validator": None}, JUDGED, None, "blocked", "judgements_missing"),
    (PENDING, JUDGED[1:], None, "blocked", "judgements_missing"),
    (PENDING, JUDGED, {"allowed": False, "reason": "Not yours."}, "blocked", "not_your_decision"),
])
def test_the_view_says_whether_originals_are_added_now_drafted_or_blocked(rec, slots, decision, mode, code):
    published = PUBLISHED if rec["validation_status"] == "validated" else None

    def route(sql, params):
        if "SELECT * FROM unvalidated WHERE record_id" in sql:
            return [rec]
        if "SELECT * FROM validated WHERE record_id" in sql:
            return [published] if published else []
        return _gate_route(slots)(sql, params)
    parent = xo.read_view(RoutingCursor(route), "p", decision=decision)["parent"]
    assert (parent["mode"], parent["mode_code"], parent["can_add"]) == (mode, code, mode == "add")
    assert bool(parent["mode_reason"]) is (mode == "blocked")
    if mode != "add":
        assert parent["current"] == {"study_r": "1", "abstract_r": "Raw abstract.", "type": "replication"}


def test_a_reviewed_entry_published_as_another_type_is_not_offered_drafting():
    rec = {**PENDING, "validation_status": "validated", "admin_checked": True}
    published = {**PUBLISHED, "type": "not_validation"}

    def route(sql, params):
        if "SELECT * FROM unvalidated WHERE record_id" in sql:
            return [rec]
        if "SELECT * FROM validated WHERE record_id" in sql:
            return [published]
        return _gate_route()(sql, params)
    parent = xo.read_view(RoutingCursor(route), "p")["parent"]
    assert (parent["mode"], parent["block_code"], parent["mode_code"]) == ("blocked", "parent_type", None)
    assert parent["block_reason"] == "Only a replication or a reproduction can have originals added."


def test_the_view_flags_own_and_already_coded_originals():
    cur = ScriptedCursor([
        ("FROM unvalidated WHERE record_id", [PARENT]),
        ("FROM validated WHERE record_id", [PUBLISHED]),
        ("u.parent_record_id = %s", [{"record_id": "sib", "origin": "extractor",
                                      "validation_status": "need_review", "parent_record_id": None,
                                      "doi_o": "10.1/SIB", "title_o": "Sibling", "study_o": ""}]),
        ("FROM extra_original_suggestions", [
            _suggestion(1, doi="10.1/own"), _suggestion(2, doi="10.1/sib"),
            _suggestion(3, doi="10.1/new")]),
        ("WHERE parent_record_id = %s AND validation_status", []),
    ])
    view = xo.read_view(cur, "p")
    assert view["parent"]["can_add"] is True
    flags = {g["doi_o"]: (g["is_own_original"], g["coded_match"]) for g in view["suggestions"]}
    assert flags["10.1/own"] == (True, None)
    assert flags["10.1/sib"] == (False, {"record_id": "sib", "origin": "extractor",
                                         "validation_status": "need_review"})
    assert flags["10.1/new"] == (False, None)


def test_a_replication_without_a_doi_only_groups_its_own_added_originals():
    """'' is not an identity: grouping on it would pull in every DOI-less replication."""
    cur = ScriptedCursor([])
    xo._family(cur, {"record_id": "p", "doi_r": ""})
    sql, params = cur.statements[0]
    assert "(%s <> '' AND lower(u.doi_r) = lower(%s)) OR u.parent_record_id = %s" in sql
    assert params[1] == "" and params[3] == "p"


def test_the_view_on_an_added_original_points_at_its_entry():
    child = {**PARENT, "record_id": "c", "origin": "admin_extra_original", "parent_record_id": "p"}
    cur = ScriptedCursor([("FROM unvalidated WHERE record_id", [child]),
                          ("FROM validated WHERE record_id", [PUBLISHED])])
    view = xo.read_view(cur, "c")
    assert view["parent_record_id"] == "p" and view["parent"]["can_add"] is False
    assert view["suggestions"] == [] and view["children"] == []


def test_dismissing_dismisses_every_open_suggestion_of_that_paper():
    cur = ScriptedCursor([
        ("SELECT 1 FROM unvalidated", [{"?column?": 1}]),
        ("FROM extra_original_suggestions WHERE record_id", [
            _suggestion(1, doi="10.5/AB"), _suggestion(2, doi="10.5/ab", who="ben"),
            _suggestion(3, title="Other")]),
    ])
    assert xo.dismiss_suggestion(cur, "p", 2, {"id": 1, "handle": "sophie"}) == 2
    update = next(p for s, p in cur.statements if "SET status = 'dismissed'" in s)
    assert update == ("sophie", [1, 2])
    assert any("INSERT INTO admin_decisions" in s for s, _ in cur.statements)
    with pytest.raises(xo.ExtraOriginalError) as missing:
        xo.dismiss_suggestion(cur, "p", 99, {"handle": "sophie"})
    assert missing.value.status == 404


def test_the_entries_list_has_a_suggested_originals_filter_and_names_the_original():
    entries = APP.split("def admin_entries(", 1)[1].split("\n@app.", 1)[0]
    assert '"extra_originals":  f"WHERE {_OPEN_EXTRA_SUGGESTIONS_SQL}"' in entries
    assert '"extra_originals": c_extra' in entries
    for column in ("u.origin", "AS effective_title_o", "AS has_extra_children",
                   "AS open_extra_suggestions"):
        assert column in entries
    index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert 'data-filter="extra_originals"' in index and 'id="fc-extra-originals"' in index


def test_the_entry_detail_counts_but_does_not_load_the_section():
    detail = APP.split("def admin_entry_detail(", 1)[1].split("\n@app.", 1)[0]
    assert "extra_originals.detail_summary(cur, record_id)" in detail
    assert "read_view" not in detail


def test_admin_helpers_live_outside_render_admin_detail():
    """Static tests slice renderAdminDetail up to its first closing brace at
    column 0 (tests/test_admin_reject_choice.py); the helpers must not move it."""
    body = APP_JS.split("function renderAdminDetail(data) {", 1)[1].split("\n}\n", 1)[0]
    assert "_adminExtraOriginalsShell(rec, data)" in body
    assert "_wireAdminExtraOriginals(rec, data);" in body
    assert "function _renderAdminExtraOriginals" not in body
    assert "_xoSafeHref" in APP_JS.split("function renderAdminDetail(data) {", 1)[0]


# ── phase 4: adding originals, and keeping them in step ───────────────────────

ADMIN = {"id": 1, "handle": "sophie", "validator_id": 42}
PUB = {"record_id": "p", "doi_r": "10.1/rep", "study_r": "1", "title_r": "Rep", "year_r": "2020",
       "url_r": "https://r", "ref_r": "R", "abstract_r": "We found no effect at all.",
       "doi_r_published": None, "alt_identifier_r": None, "oa_work_id_r": "W9",
       "doi_o": "10.1/own", "study_o": "", "title_o": "Own", "original_key": "10.1/own",
       "type": "replication", "outcome": "failed", "outcome_quote": "No effect.",
       "out_quote_source": "abstract", "outcome_computation": None,
       "outcome_computational_quote": None, "out_quote_computational_source": None,
       "outcome_robustness": None, "outcome_robustness_quote": None, "out_quote_robust_source": None}


class RoutingCursor:
    """Answers each statement through `route(sql, params)`; records everything."""

    def __init__(self, route):
        self.route = route
        self.statements = []
        self.rows = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.rows = self.route(sql, params) or []
        self.rowcount = len(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def sql(self):
        return [s for s, _ in self.statements]


def _refusal(fn, *args, **kwargs):
    with pytest.raises(xo.ExtraOriginalError) as err:
        fn(*args, **kwargs)
    return err.value


def _item(**fields):
    return {"title_o": "Another original", "doi_o": "10.5/new", **fields}


@pytest.mark.parametrize("fields,code,field", [
    ({"title_o": " "}, "title_required", "title_o"),
    ({"doi_o": "Smith 2019"}, "invalid_doi", "doi_o"),
    ({"doi_o": "", "ref_o": ""}, "reference_required", "ref_o"),
    ({"url_o": "javascript:alert(1)"}, "invalid_url", "url_o"),
    ({"year_o": "19"}, "invalid_year", "year_o"),
    ({"oa_work_id_o": "X12"}, "invalid_work_id", "oa_work_id_o"),
    ({"own_outcome": True, "outcome": "not_a_replication"}, "outcome_required", "outcome"),
    ({"own_outcome": True, "outcome": ""}, "outcome_required", "outcome"),
])
def test_a_wrong_entry_is_refused_with_its_index_and_field(fields, code, field):
    err = _refusal(xo._child_values, 2, _item(**fields), PUB, ADMIN)
    assert (err.status, err.detail["code"], err.detail["index"], err.detail["field"]) == (422, code, 2, field)
    assert err.detail["message"].startswith("Original #3")
    assert err.detail["message"].startswith(err.detail["label"])


def test_a_refusal_says_which_original_what_is_wrong_and_what_to_do():
    err = _refusal(xo._child_values, 0, _item(year_o="19"), PUB, ADMIN)
    assert err.detail["message"] == (
        "Original #1 (“Another original”, 10.5/new) has “19” as its year, but a year has four "
        "digits. Enter it like 2018, or clear it.")
    err = _refusal(xo._child_values, 1, {"title_o": "A book", "doi_o": ""}, PUB, ADMIN)
    assert err.detail["message"] == (
        "Original #2 (“A book”) has no DOI, so it needs a reference: FLoRA describes an original "
        "without a DOI by its reference alone. Enter the full reference, or add the DOI.")


def test_an_inheriting_original_takes_the_entry_s_outcome_and_blank_keys_are_empty():
    v = xo._child_values(0, {"title_o": "A book", "ref_o": "Smith (1990). A book."}, PUB, ADMIN)
    assert v["inherits_outcome"] is True
    assert (v["type"], v["outcome"], v["outcome_quote"]) == ("replication", "failed", "No effect.")
    # '' not NULL: a NULL key column never matches in the unique key.
    assert (v["doi_o"], v["study_o"], v["study_r"]) == ("", "", "1")
    assert v["oa_work_id_r"] == "W9" and v["abstract_r"] == PUB["abstract_r"]
    assert xo._child_values(0, _item(study_r="2"), PUB, ADMIN)["study_r"] == "2"


def test_an_own_replication_outcome_detects_or_records_its_quote_source():
    v = xo._child_values(0, _item(own_outcome=True, outcome="successful",
                                  outcome_quote="no effect at all"), PUB, ADMIN)
    assert (v["inherits_outcome"], v["outcome"], v["out_quote_source"]) == (False, "successful", "abstract")
    assert v["out_quote_source_by"] is None
    v = xo._child_values(0, _item(own_outcome=True, outcome="mixed", outcome_quote="q",
                                  out_quote_source="Results"), PUB, ADMIN)
    assert (v["out_quote_source"], v["out_quote_source_by"]) == ("results", "sophie")


def test_an_own_reproduction_outcome_needs_both_axes():
    repro = {**PUB, "type": "reproduction"}
    err = _refusal(xo._child_values, 0, _item(own_outcome=True, outcome_computation="computational issues"),
                   repro, ADMIN)
    assert (err.detail["code"], err.detail["field"]) == ("outcome_required", "outcome_robustness")
    assert "no robustness verdict" in err.detail["message"]
    err = _refusal(xo._child_values, 0, _item(own_outcome=True, outcome_computation="bogus",
                                              outcome_robustness="robust"), repro, ADMIN)
    assert (err.detail["code"], err.detail["field"]) == ("invalid_outcome", "outcome_computation")
    v = xo._child_values(0, _item(own_outcome=True, outcome_computation="computational issues",
                                  outcome_robustness="robust", outcome_robustness_quote="Robust."),
                         repro, ADMIN)
    assert v["outcome"] == "computational issues, robust"
    assert (v["outcome_robustness_quote"], v["outcome_quote"]) == ("Robust.", None)


def _create_route(parent=None, published=PUB, suggestions=(), published_hit=None, sibling=None,
                  inserted=True, family=()):
    parent = parent or {"record_id": "p", "origin": "extractor", "validation_status": "validated",
                        "admin_checked": True, "doi_r": "10.1/rep"}

    def route(sql, params):
        if "SELECT * FROM unvalidated WHERE record_id" in sql:
            return [parent]
        if "FROM unvalidated u" in sql and "u.parent_record_id = %s" in sql:
            return list(family)
        if "SELECT * FROM validated WHERE record_id" in sql:
            return [published] if published else []
        if "SELECT suggestion_id, validator_id" in sql:
            return [s for s in suggestions if s["suggestion_id"] in params[1]]
        if "FROM validated v" in sql:
            return [published_hit] if published_hit else []
        if "NOT IN ('rejected', 'validated')" in sql:
            return [sibling] if sibling else []
        if "INSERT INTO validated" in sql:
            return [{"record_id": params[0]}] if inserted else []
        return []
    return route


def test_originals_are_added_only_to_a_published_admin_reviewed_entry():
    cur = RoutingCursor(_create_route(parent={"record_id": "p", "origin": "extractor",
                                              "validation_status": "validated", "admin_checked": False}))
    err = _refusal(xo.create_children, cur, "p", [_item()], ADMIN)
    assert (err.status, err.detail["code"]) == (409, "parent_not_admin_checked")
    assert not any("INSERT" in s for s in cur.sql())


def test_an_admin_may_add_their_own_suggestion_and_the_log_names_them():
    """ADMIN is validator 42, who made suggestion 5: adding it is allowed, and
    logged under the admin's handle like any other addition."""
    mine = [{"suggestion_id": 5, "validator_id": 42, "status": "open"}]
    cur = RoutingCursor(_create_route(suggestions=mine))
    created = xo.create_children(cur, "p", [_item(from_suggestion_ids=[5])], ADMIN)
    assert len(created) == 1
    accept = next(p for s, p in cur.statements if "SET status = 'accepted'" in s)
    assert accept == (created[0]["record_id"], "sophie", "p", [5])
    logs = [p for s, p in cur.statements if "INSERT INTO admin_decisions" in s]
    assert [(p[2], p[3]) for p in logs] == [("sophie", "extra_original_added")] * 2
    assert '"own_suggestion"' not in inspect.getsource(xo.create_children)


def test_an_unknown_suggestion_names_the_original_that_carries_it():
    err = _refusal(xo.create_children, RoutingCursor(_create_route()), "p",
                   [_item(), _item(doi_o="10.5/other", from_suggestion_ids=[77])], ADMIN)
    assert (err.status, err.detail["code"], err.detail["index"]) == (422, "unknown_suggestion", 1)
    assert err.detail["message"].startswith("Original #2 (“Another original”, 10.5/other)")


@pytest.mark.parametrize("items,route,code,status", [
    ([_item(doi_o="https://doi.org/10.1/OWN")], {}, "extra_original_is_own", 409),
    ([_item(), _item(doi_o="10.5/NEW")], {}, "duplicate_in_batch", 422),
    ([_item()], {"published_hit": {"record_id": "x", "origin": "extractor", "parent_record_id": None}},
     "extra_original_published", 409),
    ([_item()], {"sibling": {"record_id": "s", "validation_status": "need_review"}},
     "extra_original_coded", 409),
    ([_item()], {"inserted": False}, "extra_original_published", 409),
])
def test_every_collision_is_refused_with_a_reason(items, route, code, status):
    cur = RoutingCursor(_create_route(**route))
    err = _refusal(xo.create_children, cur, "p", items, ADMIN)
    assert (err.status, err.detail["code"]) == (status, code)
    assert err.detail["message"]


ADDED_ID = "c0ffee00-0000-4000-8000-000000000000"


@pytest.mark.parametrize("route,code,relation,message", [
    ({"published_hit": {"record_id": ADDED_ID, "origin": "admin_extra_original", "parent_record_id": "p",
                        "title_o": "Added before", "doi_o": "10.5/new", "validation_status": "validated"}},
     "extra_original_published", "added_original",
     "Original #1 (“Another original”, 10.5/new) duplicates an original already added to this entry, "
     "entry c0ffee00 (“Added before”, 10.5/new, validated): the same DOI and the same study numbers. "
     "Remove it from the list. If it is another study of that paper, enter the study numbers."),
    ({"published_hit": {"record_id": ADDED_ID, "origin": "extractor", "parent_record_id": None,
                        "title_o": "Coded", "doi_o": "10.5/NEW", "validation_status": "validated"}},
     "extra_original_published", "extractor_record",
     "Original #1 (“Another original”, 10.5/new) duplicates an extractor-coded original already published "
     "for this replication, entry c0ffee00 (“Coded”, 10.5/NEW, validated): the same DOI and the same "
     "study numbers. Remove it from the list. If it is another study of that paper, enter the study numbers."),
    ({"sibling": {"record_id": ADDED_ID, "validation_status": "need_review", "title_o": "Sibling",
                  "doi_o": "10.5/new"}},
     "extra_original_coded", "extractor_record",
     "Original #1 (“Another original”, 10.5/new) is already coded for this replication by the extractor, "
     "entry c0ffee00 (“Sibling”, 10.5/new, needs review), which is still being validated. That entry's "
     "own validation will publish it: remove it from the list."),
])
def test_a_duplicate_names_the_record_it_duplicates(route, code, relation, message):
    err = _refusal(xo.create_children, RoutingCursor(_create_route(**route)), "p", [_item()], ADMIN)
    detail = err.detail
    assert (detail["code"], detail["index"], detail["field"], detail["message"]) == (code, 0, "doi_o", message)
    assert detail["conflict"]["relation"] == relation and detail["conflict"]["short_id"] == "c0ffee00"
    assert detail["existing_record_id"] == ADDED_ID


def test_the_entry_s_own_original_and_a_repeat_in_the_list_are_named():
    err = _refusal(xo.create_children, RoutingCursor(_create_route()), "p",
                   [_item(doi_o="10.1/OWN", title_o="Own again")], ADMIN)
    assert err.detail["conflict"]["relation"] == "own_original" and err.detail["conflict"]["record_id"] == "p"
    assert err.detail["message"].startswith(
        "Original #1 (“Own again”, 10.1/OWN) is this entry's own original, entry p (“Own”, 10.1/own, validated)")
    err = _refusal(xo.create_children, RoutingCursor(_create_route()), "p",
                   [_item(), _item(doi_o="10.5/x"), _item(title_o="Again")], ADMIN)
    assert (err.detail["index"], err.detail["other_index"]) == (2, 0)
    assert "repeats original #1 in this list" in err.detail["message"]
    # Without a DOI the title is the identity, and the field to fix.
    book = {"doi_o": "", "ref_o": "Smith (1990). A book.", "title_o": "A book"}
    err = _refusal(xo.create_children, RoutingCursor(_create_route()), "p",
                   [_item(**book), _item(**book)], ADMIN)
    assert (err.detail["code"], err.detail["field"]) == ("duplicate_in_batch", "title_o")


def _survivor_route(admin_checked):
    survivor = {"record_id": SURVIVOR_ID, "origin": "extractor", "validation_status": "validated",
                "admin_checked": admin_checked, "doi_r": "10.1/rep"}
    base = _create_route(parent=survivor)

    def route(sql, params):
        if "FROM unvalidated u LEFT JOIN validated v" in sql:
            return [{"record_id": SURVIVOR_ID, "validation_status": "validated", "parent_record_id": None,
                     "title_o": "The survivor", "doi_o": "10.1/own"}]
        return base(sql, params)
    return route


def test_drafts_of_a_merged_duplicate_are_added_to_the_survivor():
    cur = RoutingCursor(_survivor_route(admin_checked=True))
    created = xo.create_on_survivor(cur, "dup", SURVIVOR_ID, [_item()], ADMIN)
    insert = next((s, p) for s, p in cur.statements if s.startswith("INSERT INTO unvalidated"))
    row = dict(zip(insert[0].split("(", 1)[1].split(")", 1)[0].split(", "), insert[1]))
    assert row["parent_record_id"] == SURVIVOR_ID and row["record_id"] == created[0]["record_id"]


def test_a_survivor_that_cannot_take_originals_refuses_the_merge_naming_it():
    cur = RoutingCursor(_survivor_route(admin_checked=False))
    err = _refusal(xo.create_on_survivor, cur, "dup", SURVIVOR_ID, [_item(), _item(doi_o="10.5/b")], ADMIN)
    detail = err.detail
    assert (err.status, detail["code"], detail["reason_code"]) == (409, "survivor_not_eligible",
                                                                   "parent_not_admin_checked")
    assert detail["survivor"]["short_id"] == "5a5a5a5a" and detail["duplicate_record_id"] == "dup"
    assert detail["message"].startswith(
        "Nothing was saved. This entry duplicates entry 5a5a5a5a (“The survivor”, 10.1/own, validated), "
        "so merging it moves the 2 other originals you drafted to that entry, but that entry cannot "
        "take originals: no admin has reviewed it yet (a rule validated it).")
    assert not any(s.startswith("INSERT") for s in cur.sql())


def test_an_own_outcome_drafted_for_the_other_type_is_refused_on_merge():
    cur = RoutingCursor(_survivor_route(admin_checked=True))
    items = [_item(), _item(doi_o="10.5/b", own_outcome=True, outcome_computation="computational issues",
                            outcome_robustness="robust")]
    err = _refusal(xo.create_on_survivor, cur, "dup", SURVIVOR_ID, items, ADMIN, drafted_type="reproduction")
    detail = err.detail
    assert (err.status, detail["code"], detail["index"]) == (409, "survivor_type_differs", 1)
    # No field to mark: the form shows the duplicate's type, not the survivor's.
    assert "field" not in detail and detail["survivor"]["short_id"] == "5a5a5a5a"
    assert detail["message"].startswith("Original #2 (")
    assert ("has its own reproduction outcome, but this entry duplicates entry 5a5a5a5a (“The survivor”, "
            "10.1/own, validated), a replication, and merging moves the drafted originals there. "
            "Nothing was saved.") in detail["message"]
    assert not any(s.startswith("INSERT") for s in cur.sql())
    # One that follows the survivor's outcome goes there whatever the type.
    assert xo.create_on_survivor(RoutingCursor(_survivor_route(admin_checked=True)), "dup", SURVIVOR_ID,
                                 [_item()], ADMIN, drafted_type="reproduction")


BOOK = {"doi_o": "", "title_o": "A book", "ref_o": "Smith (1990). A book."}


@pytest.mark.parametrize("published,family,items,index,existing", [
    # The entry's own original has no DOI either.
    ({**PUB, "doi_o": "", "original_key": ""}, (), [_item(**BOOK)], 0, "p"),
    # An original already added to the entry has none.
    (PUB, ({"record_id": "c1", "doi_o": ""},), [_item(**BOOK)], 0, "c1"),
    # Two in one request: the second would merge with the first.
    (PUB, (), [_item(**BOOK), _item(**{**BOOK, "title_o": "Another book"})], 1, None),
])
def test_a_second_original_without_a_doi_is_refused_as_flora_would_merge_them(
        published, family, items, index, existing):
    """transform_sources.dedup_keys collapses rows on type | doi_o | doi_r | url_r: two
    DOI-less originals of one replication would publish as one record."""
    cur = RoutingCursor(_create_route(published=published, family=family))
    err = _refusal(xo.create_children, cur, "p", items, ADMIN)
    assert (err.status, err.detail["code"], err.detail["index"]) == (409, "extra_original_no_doi", index)
    assert err.detail.get("existing_record_id") == existing
    assert not any("INSERT" in s for s in cur.sql())


def test_an_original_without_a_doi_is_allowed_when_every_other_has_one():
    cur = RoutingCursor(_create_route(family=({"record_id": "c1", "doi_o": "10.5/x"},)))
    assert xo.create_children(cur, "p", [_item(**BOOK)], ADMIN)
    cur = RoutingCursor(_create_route(published={**PUB, "doi_o": "", "original_key": ""}))
    assert xo.create_children(cur, "p", [_item()], ADMIN)


def test_another_study_of_the_entry_s_own_original_is_allowed():
    cur = RoutingCursor(_create_route())
    assert xo.create_children(cur, "p", [_item(doi_o="10.1/own", study_o="2")], ADMIN)


def test_published_duplicates_are_found_whatever_the_case():
    cur = RoutingCursor(lambda sql, params: [])
    xo._published_match(cur, {"doi_r": "10.1/REP", "study_r": "", "title_r": "", "doi_o": "10.5/X",
                              "oa_work_id_o": None, "study_o": "", "title_o": "T"})
    sql, params = cur.statements[0]
    assert "lower(v.doi_r) = lower(%s)" in sql and "lower(v.original_key) = lower(%s)" in sql
    assert "COALESCE(v.study_o, '') = %s" in sql
    cur = RoutingCursor(lambda sql, params: [])
    xo._published_match(cur, {"doi_r": "", "study_r": "", "title_r": "Rep", "doi_o": "",
                              "oa_work_id_o": None, "study_o": "", "title_o": "A book"})
    sql, params = cur.statements[0]
    assert "lower(COALESCE(v.title_r, '')) = lower(%s)" in sql
    assert "lower(COALESCE(v.title_o, '')) = lower(%s)" in sql
    assert params[:4] == ("Rep", "", "A book", "")


def test_a_replication_without_a_doi_has_no_coded_siblings():
    cur = RoutingCursor(lambda sql, params: [{"record_id": "s"}])
    assert xo._pending_sibling(cur, {"record_id": "p"}, {"doi_r": "", "doi_o": "10.5/x",
                                                         "title_o": "", "study_o": ""}) is None
    assert cur.statements == []


def test_adding_writes_both_rows_accepts_the_suggestions_and_logs_both_records():
    suggestions = [{"suggestion_id": 5, "validator_id": 7, "status": "open"}]
    cur = RoutingCursor(_create_route(suggestions=suggestions))
    created = xo.create_children(cur, "p", [_item(from_suggestion_ids=[5])], ADMIN)
    child = created[0]["record_id"]
    sql = cur.sql()
    assert sql[0].startswith("SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE")
    insert_u = next((s, p) for s, p in cur.statements if s.startswith("INSERT INTO unvalidated"))
    columns = insert_u[0].split("(", 1)[1].split(")", 1)[0].split(", ")
    row = dict(zip(columns, insert_u[1]))
    assert (row["origin"], row["parent_record_id"], row["validation_status"]) == \
        ("admin_extra_original", "p", "validated")
    assert (row["admin_checked"], row["admin_name"], row["inherits_outcome"]) == (True, "sophie", True)
    assert "pair_id" not in row
    assert row["final_doi_o"] == "10.5/new" and row["final_outcome"] == "failed"
    insert_v = next(s for s in sql if "INSERT INTO validated" in s)
    assert "admin_approved" in insert_v
    assert "ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o) DO NOTHING" in insert_v
    accept = next(p for s, p in cur.statements if "SET status = 'accepted'" in s)
    assert accept == (child, "sophie", "p", [5])
    logs = [p for s, p in cur.statements if "INSERT INTO admin_decisions" in s]
    assert [(p[0], p[3]) for p in logs] == [(child, "extra_original_added"), ("p", "extra_original_added")]


def _sync_route(parent_pub, children, child_pubs, hit=None):
    def route(sql, params):
        if "SELECT * FROM validated WHERE record_id" in sql:
            return [parent_pub] if params[0] == "p" else [child_pubs[params[0]]]
        if "WHERE parent_record_id = %s AND validation_status = 'validated'" in sql:
            return children
        if "FROM validated v" in sql:
            return [hit] if hit else []
        return []
    return route


CHILD_PUB = {**PUB, "record_id": "c", "doi_o": "10.5/c", "title_o": "C", "original_key": "10.5/c",
             "study_o": "", "oa_work_id_o": None}


def _set_columns(update_sql):
    assignments = update_sql.split("SET ", 1)[1].split(" WHERE")[0].split(", ")
    return {a.split(" =")[0] for a in assignments}


def test_an_inheriting_original_follows_its_entry_s_outcome_and_replication():
    parent = {**PUB, "outcome": "successful", "outcome_quote": "It replicated.", "title_r": "Rep v2"}
    cur = RoutingCursor(_sync_route(parent, [{"record_id": "c", "inherits_outcome": True}],
                                    {"c": CHILD_PUB}))
    result = xo.sync_children(cur, "p", admin=ADMIN)
    assert result == {"updated": ["c"], "skipped": []}
    update_v = next(s for s in cur.sql() if s.startswith("UPDATE validated SET"))
    assert _set_columns(update_v) == {"title_r", "outcome", "outcome_quote"}
    update_u = next(s for s in cur.sql() if s.startswith("UPDATE unvalidated SET"))
    for col in ("title_r = %s", "final_title_r = %s", "final_outcome = %s", "final_outcome_quote = %s"):
        assert col in update_u
    log = next(p for s, p in cur.statements if "INSERT INTO admin_decisions" in s)
    assert log[3] == "inherited_update" and '"outcome": ["failed", "successful"]' in log[6]


def test_an_own_outcome_follows_only_the_replication_side():
    parent = {**PUB, "outcome": "successful", "url_r": "https://new"}
    cur = RoutingCursor(_sync_route(parent, [{"record_id": "c", "inherits_outcome": False}],
                                    {"c": CHILD_PUB}))
    xo.sync_children(cur, "p", admin=ADMIN)
    update_v = next(s for s in cur.sql() if s.startswith("UPDATE validated SET"))
    assert _set_columns(update_v) == {"url_r"}


def test_an_own_outcome_of_the_wrong_type_inherits_again():
    parent = {**PUB, "type": "reproduction", "outcome": "computational issues, robust",
              "outcome_computation": "computational issues", "outcome_robustness": "robust"}
    cur = RoutingCursor(_sync_route(parent, [{"record_id": "c", "inherits_outcome": False}],
                                    {"c": CHILD_PUB}))
    xo.sync_children(cur, "p", admin=ADMIN)
    update_u = next(s for s in cur.sql() if s.startswith("UPDATE unvalidated SET"))
    assert "inherits_outcome = TRUE" in update_u and "final_outcome_computation = %s" in update_u
    log = next(p for s, p in cur.statements if "INSERT INTO admin_decisions" in s)
    assert "the entry's type changed" in log[6]


def test_a_change_that_would_collide_a_child_is_refused_or_skipped():
    parent = {**PUB, "doi_r": "10.1/moved"}
    children = [{"record_id": "c", "inherits_outcome": True}]
    hit = {"record_id": "x", "origin": "extractor", "parent_record_id": None}
    cur = RoutingCursor(_sync_route(parent, children, {"c": CHILD_PUB}, hit=hit))
    err = _refusal(xo.sync_children, cur, "p", admin=ADMIN)
    assert (err.status, err.detail["code"]) == (409, "children_identity_conflict")
    assert err.detail["children"][0]["conflicts_with"] == "x"
    assert not any(s.startswith("UPDATE") for s in cur.sql())
    cur = RoutingCursor(_sync_route(parent, children, {"c": CHILD_PUB}, hit=hit))
    assert xo.sync_children(cur, "p", strict=False)["skipped"][0]["record_id"] == "c"
    assert not any(s.startswith("UPDATE") for s in cur.sql())


def test_nothing_is_written_when_nothing_changed_and_work_ids_go_after_the_doi_trigger():
    cur = RoutingCursor(_sync_route(PUB, [{"record_id": "c", "inherits_outcome": True}],
                                    {"c": CHILD_PUB}))
    assert xo.sync_children(cur, "p")["updated"] == []
    assert not any(s.startswith("UPDATE") for s in cur.sql())
    cur = RoutingCursor(_sync_route({**PUB, "oa_work_id_r": "W10"},
                                    [{"record_id": "c", "inherits_outcome": True}], {"c": CHILD_PUB}))
    xo.sync_children(cur, "p")
    updates = [s for s in cur.sql() if s.startswith("UPDATE unvalidated")]
    assert updates[-1] == "UPDATE unvalidated SET oa_work_id_r = %s WHERE record_id = %s"


def test_rejecting_an_entry_takes_its_added_originals_out_of_flora():
    def route(sql, params):
        if "WHERE parent_record_id = %s AND validation_status <> 'rejected'" in sql:
            return [{"record_id": "c", "validation_status": "validated"}]
        return []
    cur = RoutingCursor(route)
    assert xo.reject_children(cur, "p") == 1
    sql = cur.sql()
    assert "DELETE FROM validated WHERE record_id = %s" in sql
    assert any("SET validation_status = 'rejected'" in s for s in sql)
    log = next(p for s, p in cur.statements if "INSERT INTO admin_decisions" in s)
    assert (log[3], log[4], log[5]) == ("rejected_with_parent", "validated", "rejected")


def test_a_merged_duplicate_s_originals_move_to_the_survivor():
    cur = RoutingCursor(lambda sql, params: [{"record_id": "c"}] if "RETURNING record_id" in sql else [])
    assert xo.repoint_children(cur, "dup", "surv", ADMIN) == 1
    move = next(p for s, p in cur.statements if "SET parent_record_id = %s" in s)
    assert move == ("surv", "dup")
    assert any("UPDATE extra_original_suggestions SET record_id = %s" in s for s in cur.sql())


def test_removing_an_added_original_reopens_its_suggestions():
    rows = {"origin": [{"parent_record_id": "p", "origin": "admin_extra_original"}],
            "child": [{"record_id": "c", "parent_record_id": "p", "validation_status": "validated"}]}

    def route(sql, params):
        if "SELECT parent_record_id::text" in sql:
            return rows["origin"]
        if "SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE" in sql:
            return rows["child"]
        return [{"one": 1}] if "SELECT 1" in sql else []
    cur = RoutingCursor(route)
    xo.remove_child(cur, "c", ADMIN)
    sql = cur.sql()
    # The parent is locked before the child, as everywhere else.
    assert sql.index("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE") < \
        sql.index("SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE")
    assert "DELETE FROM validated WHERE record_id = %s" in sql
    assert any("SET status = 'open', resolved_record_id = NULL" in s for s in sql)
    logs = [(p[0], p[3]) for s, p in cur.statements if "INSERT INTO admin_decisions" in s]
    assert logs == [("c", "extra_original_removed"), ("p", "extra_original_removed")]

    rows["origin"] = [{"parent_record_id": None, "origin": "extractor"}]
    assert _refusal(xo.remove_child, RoutingCursor(route), "c", ADMIN).detail["code"] == "not_an_added_original"
    rows["origin"] = [{"parent_record_id": "p", "origin": "admin_extra_original"}]
    rows["child"] = [{"record_id": "c", "parent_record_id": "p", "validation_status": "rejected"}]
    assert _refusal(xo.remove_child, RoutingCursor(route), "c", ADMIN).detail["code"] == "already_removed"


def test_a_conflict_with_an_added_original_is_never_a_merge():
    cur = RoutingCursor(lambda sql, params: [{"origin": "admin_extra_original", "parent_record_id": "p"}])
    detail = xo.child_conflict_detail(cur, {"record_id": "c"})
    assert detail["code"] == "conflicts_with_added_original" and detail["parent_record_id"] == "p"
    cur = RoutingCursor(lambda sql, params: [{"origin": "extractor", "parent_record_id": None}])
    assert xo.child_conflict_detail(cur, {"record_id": "x"}) is None
    assert xo.child_conflict_detail(cur, None) is None


def test_resolving_an_added_original_edits_only_its_original_and_outcome():
    cur = RoutingCursor(lambda sql, params: [PUB])
    child = {"record_id": "c", "parent_record_id": "p"}
    err = _refusal(xo.check_child_resolve, cur, child, {"corrected_title_r": "X"}, "replication")
    assert (err.status, err.detail["code"]) == (400, "child_replication_fixed")
    err = _refusal(xo.check_child_resolve, cur, child, {}, "reproduction")
    assert err.detail["code"] == "child_type_fixed"
    xo.check_child_resolve(cur, child, {"corrected_doi_r": None}, "replication")
    xo.check_child_resolve(cur, child, {}, "not_validation")


def test_a_nul_or_lone_surrogate_never_fails_the_judgement():
    """psycopg2 raises ValueError (not psycopg2.Error) for text PostgreSQL cannot
    hold; that must not become a 500 that the submit queue retries forever."""
    s = xo.normalize_suggestion({"title": "A\x00B", "note": "n\x00"})
    assert s["title_o"] == "AB" and s["note"] == "n"

    class Refusing(RecordingCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "INSERT INTO extra_original_suggestions" in sql:
                raise ValueError("cannot encode")

    cur = Refusing()
    assert xo.store_suggestions(cur, "r", None, 1, "v", [{"title": "A"}], "replication") == 0
    assert "ROLLBACK TO SAVEPOINT extra_original_suggestions" in cur.sql()


def test_a_removed_added_original_cannot_be_resolved_back_into_flora():
    cur = RoutingCursor(lambda sql, params: [PUB])
    child = {"record_id": "c", "parent_record_id": "p", "validation_status": "rejected"}
    err = _refusal(xo.check_child_resolve, cur, child, {}, "replication")
    assert (err.status, err.detail["code"]) == (409, "child_removed")
    xo.check_child_resolve(cur, child, {}, "not_validation")

def test_resolving_an_added_original_s_outcome_gives_it_its_own():
    cur = RoutingCursor(lambda sql, params: [{**CHILD_PUB, "outcome": "mixed"}]
                        if "FROM validated" in sql else [{"record_id": "c"}])
    xo.after_child_resolved(cur, "c", CHILD_PUB, ADMIN)
    assert any("SET inherits_outcome = FALSE" in s for s in cur.sql())
    assert any("INSERT INTO admin_decisions" in s for s in cur.sql())
    cur = RoutingCursor(lambda sql, params: [CHILD_PUB] if "FROM validated" in sql else [])
    xo.after_child_resolved(cur, "c", CHILD_PUB, ADMIN)
    assert not any("inherits_outcome" in s for s in cur.sql())


def test_only_an_admin_reviewed_extractor_record_can_have_children():
    assert xo.may_have_children({"admin_checked": True, "origin": "extractor"})
    assert xo.may_have_children({"admin_checked": True})          # rows read before the column
    assert not xo.may_have_children({"admin_checked": False, "origin": "extractor"})
    assert not xo.may_have_children({"admin_checked": True, "origin": "admin_extra_original"})
    assert not xo.may_have_children(None)


# ── phase 4: wiring ───────────────────────────────────────────────────────────

CONSENSUS = (ROOT / "consensus_engine.py").read_text(encoding="utf-8")


def _fn(source, name):
    return source.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0].split("\n@app.", 1)[0]


def test_every_place_that_unpublishes_a_record_handles_its_added_originals():
    """Each DELETE FROM validated by record_id either takes the record's added
    originals along, or is exempt for a stated reason. A new site fails here
    until someone decides which it is."""
    pattern = r"DELETE FROM validated WHERE record_id = %s"
    owners = [re.findall(r"\ndef (\w+)\(", APP[:m.start()])[-1] for m in re.finditer(pattern, APP)]
    # Exempt: flag-review deletes only for an auto-validated record (validated AND
    # NOT admin_checked), and only admin-checked records are given children.
    exempt = {"admin_flag_review"}
    handled = {
        # admin_resolve deletes three times: reject (cascade, or re-open for a
        # child), merge (children move to the survivor), re-insert (children follow).
        "admin_resolve": ("extra_originals.reject_children(cur, record_id, admin)",
                          "extra_originals.child_left(cur, rec, admin)",
                          "extra_originals.repoint_children(cur, record_id, conflict_id, admin)",
                          "_extra_originals_call(extra_originals.sync_children, cur, record_id, admin=admin)"),
        "admin_approve": ("_extra_originals_call(extra_originals.sync_children, cur, record_id, admin=admin)",),
        # Refused while an added original is live, so it never takes one along.
        "admin_remove_original": ('extra_originals.detail_summary(cur, record_id)["children"]',),
        "assignment_judge": ("extra_originals.reject_children(cur, req.record_id)",),
        "senior_reject": ("extra_originals.reject_children(cur, record_id)",),
    }
    assert sorted(set(owners)) == sorted(exempt | set(handled))
    assert owners.count("admin_resolve") == 3
    for owner, calls in handled.items():
        for call in calls:
            assert call in _fn(APP, owner), (owner, call)
    assert [re.findall(r"\ndef (\w+)\(", CONSENSUS[:m.start()])[-1]
            for m in re.finditer(pattern, CONSENSUS)] == ["_update_status", "_insert_validated"]
    assert "extra_originals.reject_children(cur, record_id)" in _fn(CONSENSUS, "_update_status")
    assert 'extra_originals.sync_children(cur, record.get("record_id"), strict=False)' in \
        _fn(CONSENSUS, "_insert_validated")


def test_consensus_only_looks_for_children_of_an_admin_reviewed_record():
    assert "if extra_originals.may_have_children(record):" in _fn(CONSENSUS, "_update_status")
    assert "if extra_originals.may_have_children(record):" in _fn(CONSENSUS, "_insert_validated")


def test_approve_and_resolve_refuse_a_merge_with_an_added_original():
    approve, resolve = _fn(APP, "admin_approve"), _fn(APP, "admin_resolve")
    assert "extra_originals.child_conflict_detail(cur, conflict)" in approve
    assert resolve.count("extra_originals.child_conflict_detail(cur, conflict)") == 2
    merge = resolve.split("conflict_id = conflict[\"record_id\"]", 1)[1]
    assert merge.index("child_conflict_detail") < merge.index("INSERT INTO validated_record_merges")


def test_resolve_treats_an_added_original_as_its_own_kind_of_record():
    resolve = _fn(APP, "admin_resolve")
    assert resolve.index("extra_originals.check_child_resolve") < resolve.index("UPDATE unvalidated SET")
    assert "and not is_added_original):" in resolve
    assert "extra_originals.after_child_resolved(cur, record_id, published_before, admin)" in resolve


def test_resolve_saves_drafted_originals_with_the_entry_in_one_transaction():
    model = APP.split("class AdminResolveRequest(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    assert "extra_originals: list[ExtraOriginalCreate] = Field(default_factory=list, max_length=10)" in model
    assert APP.index("class ExtraOriginalCreate(BaseModel):") < APP.index("class AdminResolveRequest(BaseModel):")
    resolve = _fn(APP, "admin_resolve")
    assert resolve.count("with db() as cur:") == 1
    # Refused before anything is written; a merged entry even on a repeated request.
    check = "extra_originals.check_resolve_drafts, cur, rec, final_type"
    assert resolve.index(check) < resolve.index("UPDATE unvalidated SET")
    assert resolve.index("merged_into_id=survivor_id") < resolve.index('"already_merged": True')
    # Created from the published values: after the entry's validated row and the sync
    # of its existing added originals.
    create = "extra_originals.create_children, cur, record_id"
    assert resolve.index("INSERT INTO validated (") < resolve.index(create)
    assert resolve.index("_extra_originals_call(extra_originals.sync_children, cur, record_id") < resolve.index(create)
    # A merge sends them to the survivor, after its existing originals moved there.
    merge = "extra_originals.create_on_survivor, cur, record_id"
    assert resolve.index("extra_originals.repoint_children(cur, record_id, conflict_id, admin)") < resolve.index(merge)
    # The section's mode depends on who is signed in (the self-approval rule).
    assert "decision=_self_approval(cur, record_id, admin)" in _fn(APP, "admin_extra_originals")


def test_added_originals_stay_out_of_extractor_and_validator_metrics():
    dashboard, stats = _fn(APP, "admin_dashboard"), _fn(APP, "admin_stats")
    pipeline = dashboard.split("AS auto_validated", 1)[1].split("pipeline = dict", 1)[0]
    assert "WHERE origin = 'extractor'" in pipeline
    assert 'pipeline["admin_extra_originals"]' in dashboard
    view_b = dashboard.split("View B", 1)[1].split("b_rows = cur.fetchall()", 1)[0]
    assert "AND origin = 'extractor'" in view_b
    assert stats.count("AND origin = 'extractor'") == 2


# ── phase 5: the admin's add form ─────────────────────────────────────────────

def test_the_add_form_helpers_live_outside_render_admin_detail():
    before = APP_JS.split("function renderAdminDetail(data) {", 1)[0]
    for helper in ("function _renderAdminXoAddSection(", "function _xoAdminEntryForm(",
                   "function _submitAdminExtraOriginals(", "function _useXoSuggestion("):
        assert helper in before, helper


def test_the_add_form_has_no_abstract_field_and_its_limits_match_the_server():
    form = APP_JS.split("function _xoAdminEntryForm(", 1)[1].split("\n}\n", 1)[0]
    assert 'data-xoa-field="abstract_r"' not in form and "read-only" in form
    model = APP.split("class ExtraOriginalCreate(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    limits = dict(re.findall(r"(\w+): (\d+)", re.search(r"_XO_ADMIN_LIMITS = \{(.*?)\};", APP_JS, re.S).group(1)))
    for name, limit in limits.items():
        if name == "year_o":
            continue                    # the server allows a little slack and checks the format
        assert re.search(rf"{name}: str \| None = Field\(default=None, max_length={limit}\)", model) or \
            re.search(rf"{name}: str = Field\(default=\"\", max_length={limit}\)", model), name


def test_approve_and_resolve_point_to_waiting_suggestions():
    # Resolve leaves out the suggestions its drafted originals just answered.
    assert 'showToast(_xoPendingNote(recordId, addedNote(resp, "Entry resolved."), used));' in APP_JS
    assert 'showToast(_xoPendingNote(id, "Entry approved."));' in APP_JS


def _js_fn(head):
    return APP_JS.split(head, 1)[1].split("\n}\n", 1)[0]


def test_resolve_sends_the_drafted_originals_and_shows_a_refusal_in_place():
    submit = _js_fn("async function submitAdminResolve(recordId) {")
    assert "const drafted = _xoResolveDrafts(recordId);" in submit
    assert "body.extra_originals = drafted.map(_xoAdminPayload);" in submit
    assert submit.count("_xoShowError(recordId,") == 2              # the resolve, and the merge retry
    assert "confirmValidatedDuplicateMerge(e.detail, drafted.length)" in submit
    drafts = _js_fn("function _xoResolveDrafts(recordId) {")
    assert 'draft.mode !== "draft"' in drafts and "!draft.on" in drafts
    show = _js_fn("function _xoShowError(recordId, detail) {")
    for part in ("draft.open = detail.index", "field: detail.field", "section.open = true", "scrollIntoView"):
        assert part in show, part


def test_rejecting_an_entry_with_drafted_originals_says_they_are_discarded():
    panel = APP_JS[APP_JS.index("function renderAdminDetail(data) {"):]
    panel = panel[:panel.index("\n}\n")]
    reject = panel[panel.index('$("#admin-reject-btn")?.addEventListener("click"'):]
    confirm = reject[:reject.index("if (!sure) return;")]
    assert "_xoResolveDrafts(rec.record_id).length" in confirm and "will be discarded" in confirm
    assert "delete _adminXoDrafts[rec.record_id];" in reject[:reject.index("advanceToNextAdminEntry")]


def test_the_section_follows_the_type_in_the_resolve_form():
    shell = _js_fn("function _adminExtraOriginalsShell(rec, data) {")
    assert shell.count('return "";') == 1                            # only an added original has none
    wire = _js_fn("function _wireAdminExtraOriginals(rec, data) {")
    assert '$("#ar-type-sel")?.addEventListener("change", follow);' in wire
    assert '$(".not-val-decision")?.addEventListener("click", follow);' in wire
    # Own-outcome fields follow the form's type in draft mode, the published one otherwise.
    entry_type = _js_fn("function _xoEntryType(parent) {")
    assert '_xoMode(parent) === "draft"' in entry_type and "_xoFormType(" in entry_type
    # Before the first resolve there is no published row: drafts start from the entry.
    assert "parent.current?.study_r" in _js_fn("function _blankAdminXo(parent) {")
    assert "parent.current?.abstract_r" in _js_fn("function _xoAbstract(parent) {")
