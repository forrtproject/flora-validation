"""A replication may target several originals, and Gate II serves only one of them.

The extractor codes one row per (replication, original) pair. A paper that
replicates a dominant original alongside secondary ones is legitimately coded
against one of them or against all of them — but not against a paper it never
replicated. From a single row those three cases look identical, so a validator
cannot answer "is this the right original?" without seeing the coded set.

`_coded_originals` attaches that set to every served pair, and Gate II renders it
with the row under judgement marked.
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch


def _import_app():
    """Import app.py with the database and scheduler stubbed out.

    Importing app.py runs init_db() at module scope (app.py:486), which executes
    the whole of db_schema.sql against DATABASE_URL — the production database
    whenever .env is present. This feature adds index DDL to that schema, so an
    unguarded import would apply a DROP INDEX and a CREATE INDEX to production on
    every run of the test suite. Mirrors tests/test_submission_failure_stamps.py.
    """
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


app = _import_app()

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "docs" / "style.css").read_text(encoding="utf-8")
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")


def _js_block():
    """The body of _codedOriginalsBlock, for asserting on what it renders."""
    marker = "function _codedOriginalsBlock("
    return JS.split(marker, 1)[1].split(chr(10) + "function ", 1)[0]


class FakeCursor:
    """Records the SQL it was given and replays canned rows, like RealDictCursor."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


def _row(rid, doi_o, title_o, total=None, **kw):
    row = {
        "record_id": rid, "doi_o": doi_o, "study_o": None, "study_r": None,
        "title_o": title_o, "year_o": None, "url_o": None,
        "oa_work_id_o": None, "authors_o": None, "original_rank": None,
        "coded_total": total,
    }
    row.update(kw)
    return row


def _rows(*rows):
    """Stamp the window-function total the query returns on every row."""
    for r in rows:
        if r["coded_total"] is None:
            r["coded_total"] = len(rows)
    return list(rows)


def _originals(cur, doi_r, record_id):
    """Just the list, for assertions that do not care about the total."""
    return app._coded_originals(cur, doi_r, record_id)[0]


# ---------------------------------------------------------------------------
# The set itself
# ---------------------------------------------------------------------------

def test_every_original_coded_for_the_replication_comes_back():
    """The motivating case: one paper, three originals, one of them under judgement."""
    cur = FakeCursor(_rows(
        _row("r1", "10.1/a", "Dominant original"),
        _row("r2", "10.1/b", "Secondary original"),
        _row("r3", "10.1/c", "Third original"),
    ))
    out = _originals(cur, "10.1080/10826084.2016.1267222", "r2")
    assert [o["title_o"] for o in out] == [
        "Dominant original", "Secondary original", "Third original"]
    assert cur.params[0] == "10.1080/10826084.2016.1267222"


def test_the_row_under_judgement_is_the_only_one_marked_current():
    cur = FakeCursor(_rows(_row("r1", "10.1/a", "A"), _row("r2", "10.1/b", "B")))
    out = _originals(cur, "10.1/rep", "r2")
    assert [o["is_current"] for o in out] == [False, True]


def test_current_row_is_matched_across_uuid_and_string_forms():
    """record_id arrives as a UUID from psycopg2 and as a str from enriched dicts."""
    import uuid
    rid = uuid.uuid4()
    cur = FakeCursor(_rows(_row(rid, "10.1/a", "A")))
    out = _originals(cur, "10.1/rep", str(rid))
    assert out[0]["is_current"] is True
    assert out[0]["record_id"] == str(rid)


def test_a_replication_without_a_doi_is_never_grouped():
    """'' is not an identity — grouping on it would pull every DOI-less
    replication into one set and invent originals that belong to other papers."""
    for blank in (None, "", "   "):
        cur = FakeCursor(_rows(_row("r1", "10.1/a", "A")))
        assert app._coded_originals(cur, blank, "r1") == ([], 0)
        assert cur.sql is None, "must not query at all for a blank doi_r"


def test_the_query_is_bounded():
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    assert "LIMIT" in cur.sql
    assert app._CODED_ORIGINALS_LIMIT in cur.params


def test_doi_less_originals_keep_their_openalex_identity():
    cur = FakeCursor(_rows(_row("r1", "", "A book", oa_work_id_o="W123")))
    out = _originals(cur, "10.1/rep", "r1")
    assert out[0]["oa_work_id_o"] == "W123"


# ---------------------------------------------------------------------------
# Anti-anchoring: the set is extractor output, not peer verdicts
# ---------------------------------------------------------------------------

def test_no_judgement_field_appears_anywhere_in_the_lookup():
    """Showing a sibling's verdict would anchor the second validator, which the
    two-human consensus design exists to prevent. These may not even be joined."""
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    lowered = cur.sql.lower()
    for leak in ("final_outcome", "validator_1", "validator_2",
                 "original_check", "validation_queue"):
        assert leak not in lowered, f"{leak} must not reach the validator here"


def test_status_may_gate_the_lookup_but_is_never_selected():
    """validation_status is the one status column the query is allowed to mention,
    and only in the WHERE clause — filtering out dead rows is not anchoring."""
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    selected, rest = cur.sql.lower().split("from", 1)
    assert "validation_status" not in selected
    assert "validation_status" in rest.split("order by", 1)[0]


# ---------------------------------------------------------------------------
# Wiring: every served pair carries the set
# ---------------------------------------------------------------------------

def test_enrich_pair_attaches_the_set_when_given_a_cursor():
    cur = FakeCursor(_rows(_row("r1", "10.1/a", "A"), _row("r2", "10.1/b", "B")))
    pair = app._enrich_pair({"record_id": "r1", "doi_r": "10.1/rep"}, cur)
    assert [o["title_o"] for o in pair["coded_originals"]] == ["A", "B"]


def test_enrich_pair_without_a_cursor_keeps_the_old_payload_shape():
    pair = app._enrich_pair({"record_id": "r1", "doi_r": "10.1/rep"})
    assert "coded_originals" not in pair


def test_every_validator_facing_pair_path_passes_a_cursor():
    """A call site that forgets the cursor silently drops the set for that view.

    Counting one call shape is not enough — an earlier version of this test did
    exactly that and sailed past /api/onboarding, which uses a different shape.
    Assert over every call instead, and name the two that must NOT pass one.
    """
    calls = [ln.strip() for ln in APP.splitlines() if "_enrich_pair(" in ln
             and not ln.lstrip().startswith("def ")]
    with_cur = [c for c in calls if "cur)" in c]
    without  = [c for c in calls if "cur)" not in c]
    assert len(with_cur) == 3, with_cur
    assert len(without) == 2, without


def test_onboarding_pairs_never_get_a_coded_set():
    """Onboarding pairs are fixtures from onboarding.json with no rows in
    unvalidated. A lookup would match live records that share their doi_r and
    show a real coded set against a calibration exercise."""
    body = APP.split("def onboarding_pairs(", 1)[1].split(chr(10) + "@app.", 1)[0]
    assert "_enrich_pair(p)" in body and "cur" not in body


def test_the_admin_detail_view_does_not_pay_for_a_set_it_never_renders():
    """preloadAdminDetails() fetches 15 entries at a time and renderAdminDetail()
    shows no coded set, so a cursor here is 15 wasted lookups per preload."""
    body = APP.split("def admin_entry_detail(", 1)[1].split("_enrich_pair", 1)[1]
    assert body.startswith("(dict(row))")
    assert "coded_originals" not in JS.split("function renderAdminDetail(", 1)[1][:4000]


# ---------------------------------------------------------------------------
# Gate II rendering
# ---------------------------------------------------------------------------

def test_gate_two_renders_the_coded_set():
    gate = JS.split('<h3><span class="gate-num">ii.</span>Original check</h3>', 1)[1]
    gate = gate.split('data-original="correct"', 1)[0]
    assert "${_codedOriginalsBlock(p)}" in gate


def test_a_single_coded_original_renders_nothing():
    """One original disambiguates nothing; the block would be pure noise."""
    body = JS.split("function _codedOriginalsBlock(", 1)[1].split("\nfunction ", 1)[0]
    assert "set.length < 2" in body and 'return ""' in body


def test_the_block_marks_the_row_under_judgement_and_explains_the_rule():
    body = JS.split("function _codedOriginalsBlock(", 1)[1].split("\nfunction ", 1)[0]
    assert "is_current" in body and "cs-current" in body
    assert "one of its originals or all of them" in body, \
        "the subset rule is the whole point of showing the set"


def test_the_set_is_styled():
    for cls in (".coded-set", ".cs-item", ".cs-current", ".cs-badge", ".cs-hint"):
        assert cls in CSS


def test_the_sibling_lookup_is_indexed():
    """Serving prefetches a batch of pairs, and each looks up its siblings by
    doi_r — unindexed that is one sequential scan of unvalidated per card."""
    assert ("CREATE INDEX IF NOT EXISTS idx_unvalidated_doi_r_lower "
            "ON unvalidated (lower(doi_r));") in SCHEMA


def test_the_index_expression_matches_the_predicate():
    """A functional index is only used when its expression matches the WHERE
    clause; lower(doi_r) in one and doi_r in the other would silently seq-scan."""
    assert "lower(u.doi_r)" in app._CODED_ORIGINALS_WHERE
    assert "lower(doi_r)" in SCHEMA.split("idx_unvalidated_doi_r_lower", 1)[1].split(";", 1)[0]


def test_the_superseded_plain_index_is_dropped():
    assert "DROP INDEX IF EXISTS idx_unvalidated_doi_r;" in SCHEMA


def test_a_coded_set_is_not_split_by_doi_casing():
    """DOI names are case-insensitive by spec and doi_r is only whitespace-stripped
    on import, so an exact match could hide originals from the validator — the
    exact failure this feature exists to prevent."""
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1080/ABC", "r1")
    where = cur.sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "lower(u.doi_r) = lower(%s)" in where


def test_case_insensitive_matching_is_scoped_to_this_lookup():
    """The pair identity key stays exact; widening it is a separate decision."""
    assert "UNIQUE (doi_r, study_r, title_r, doi_o, study_o, title_o)" in SCHEMA


def test_the_index_statements_are_idempotent():
    """db_schema.sql is re-executed by init_db() on every app start, so a bare
    CREATE or DROP would take the server down on the second boot."""
    for line in SCHEMA.splitlines():
        if "idx_unvalidated_doi_r" not in line:
            continue
        if line.strip().startswith("CREATE"):
            assert "IF NOT EXISTS" in line
        elif line.strip().startswith("DROP"):
            assert "IF EXISTS" in line


def test_ordering_is_total_so_the_displayed_position_is_stable():
    """"You are judging #2" must mean the same row on every render."""
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    order = cur.sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
    assert "u.record_id" in order, "needs a unique final tiebreaker"


# ---------------------------------------------------------------------------
# Regressions: two bugs in the first cut of this feature
# ---------------------------------------------------------------------------

def test_rejected_rows_are_not_part_of_the_coded_set():
    """A duplicate resolved by an admin is deliberately retained in unvalidated as
    'rejected' and carries the SAME original as its survivor. Without this filter
    the set listed one original twice and reported an inflated count; dead
    not-a-validation and wrong-original pairs leaked in the same way."""
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    assert "u.validation_status <> 'rejected'" in cur.sql


def test_filtering_rejects_does_not_serve_the_status_to_the_validator():
    """The status may gate the WHERE clause but must never reach the payload."""
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    selected = cur.sql.split("FROM", 1)[0]
    assert "validation_status" not in selected


def test_the_row_under_judgement_survives_truncation():
    """It used to sort by rank alone, so a replication with more coded originals
    than the cap could drop the row being judged out of the window — leaving Gate
    II with nothing highlighted while telling the validator to judge the
    highlighted original."""
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    order = cur.sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
    pin = order.split(",")[0]
    assert "u.record_id::text = %s" in pin and "DESC" in pin,         "the judged row must be pinned into the window before any other ordering"


def test_the_pin_compares_as_text_so_a_uuid_or_str_record_id_both_work():
    cur = FakeCursor([])
    app._coded_originals(cur, "10.1/rep", "r1")
    assert "u.record_id::text" in cur.sql, "uuid = text has no operator in Postgres"


def test_natural_order_is_restored_after_pinning():
    """Pinning is a windowing device, not the display order: #1 must still be the
    first original by rank, not whichever row happens to be under judgement."""
    cur = FakeCursor(_rows(
        _row("r2", "10.1/b", "Second", original_rank=2),   # pinned first by SQL
        _row("r1", "10.1/a", "First", original_rank=1),
    ))
    out = _originals(cur, "10.1/rep", "r2")
    assert [o["title_o"] for o in out] == ["First", "Second"]
    assert out[1]["is_current"] is True


def test_the_true_size_of_the_set_is_reported_even_when_truncated():
    """COUNT(*) OVER () runs before LIMIT, so a capped list still reports the real
    total rather than the size of its own window."""
    window = [_row(f"r{i}", f"10.1/{i}", f"Original {i}", total=137) for i in range(3)]
    out, total = app._coded_originals(FakeCursor(window), "10.1/rep", "r0")
    assert len(out) == 3 and total == 137


def test_the_total_is_carried_onto_the_pair():
    cur = FakeCursor(_rows(_row("r1", "10.1/a", "A"), _row("r2", "10.1/b", "B")))
    pair = app._enrich_pair({"record_id": "r1", "doi_r": "10.1/rep"}, cur)
    assert pair["coded_originals_total"] == 2


def test_an_empty_set_reports_a_zero_total():
    out, total = app._coded_originals(FakeCursor([]), "10.1/rep", "r1")
    assert out == [] and total == 0


def test_the_header_reports_the_true_total_not_the_window():
    body = _js_block()
    assert "p.coded_originals_total" in body
    assert "total > set.length" in body, "a truncated list must say so"


def test_the_cap_is_a_runaway_guard_not_a_working_ceiling():
    assert app._CODED_ORIGINALS_LIMIT >= 100


def test_this_suite_never_reaches_a_real_database():
    """init_db() runs at import and executes db_schema.sql, so a bare `import app`
    applies this feature's index DDL to whatever DATABASE_URL points at."""
    src = Path(__file__).read_text(encoding="utf-8")
    head = src.split("app = _import_app()", 1)[0]
    assert 'patch("psycopg2.connect"' in head
    bare = chr(10) + "import app" + chr(10)
    assert bare not in src, "app must only be imported inside the guard"


def test_no_test_module_imports_app_unguarded():
    """Suite-wide guard. init_db() runs at import and executes db_schema.sql, so a
    bare `import app` anywhere in tests/ applies this schema — index DDL included —
    to whatever DATABASE_URL points at, which is production when .env is present."""
    offenders = []
    for f in sorted(Path(__file__).parent.glob("test_*.py")):
        src = f.read_text(encoding="utf-8")
        imports_app = any(
            ln.startswith(("import app", "from app"))
            for ln in src.splitlines()
        )
        if imports_app and 'patch("psycopg2.connect"' not in src:
            offenders.append(f.name)
    assert not offenders, f"unguarded app import in: {offenders}"
