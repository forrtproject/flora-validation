"""Row-local data-quality checks run when a record finishes consensus.

Issue #139 asked which of the FLoRA pipeline's R checks belong in the final
validation step and which belong on a cron job. The dividing line implemented
here: a check runs per-record if it can be decided from one row AND its answer
never changes on its own. Everything cross-row (duplicates, conflicting
references) or time-varying (retractions, DOI/URL resolution) stays on cron.

The flags are advisory by explicit decision — they are recorded and shown in red
on the admin review panel, and never change where a record lands.
"""
from datetime import date
from pathlib import Path

import record_checks
from record_checks import check_record

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
CONSENSUS = (ROOT / "consensus_engine.py").read_text(encoding="utf-8")
CHECKS = (ROOT / "record_checks.py").read_text(encoding="utf-8")
BACKFILL = (ROOT / "backfill_quality_flags.py").read_text(encoding="utf-8")
SCHEMA = (ROOT / "db_schema.sql").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "docs" / "style.css").read_text(encoding="utf-8")
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def _clean(**over):
    rec = {
        "title_o": "An original", "title_r": "A replication",
        "doi_o": "10.1234/orig", "doi_r": "10.5678/repl",
        "type": "replication", "outcome": "successful",
        "year_o": "2009", "year_r": "2015",
    }
    rec.update(over)
    return rec


def _codes(rec, final=None):
    return [f["code"] for f in check_record(rec, final)]


# ---------------------------------------------------------------------------
# A well-formed record is silent
# ---------------------------------------------------------------------------

def test_a_clean_record_raises_nothing():
    assert check_record(_clean()) == []


def test_every_flag_carries_a_label_and_a_detail():
    """The admin panel renders these; a bare code would be unreadable."""
    flags = check_record(_clean(doi_o="nope", title_r=""))
    assert flags
    for f in flags:
        assert f["code"] and f["label"] and f["detail"]


# ---------------------------------------------------------------------------
# This app's data model, where it departs from the R script
# ---------------------------------------------------------------------------

def test_a_doi_less_original_with_openalex_identity_is_not_flagged():
    """Books, chapters and pre-DOI papers legitimately have doi_o = ''. The R
    script requires doi_o, which here would flag a large, correct population."""
    assert _codes(_clean(doi_o="", oa_work_id_o="W2003152982")) == []


def test_an_original_with_no_identity_at_all_is_flagged():
    assert "missing_required_fields" in _codes(_clean(doi_o="", oa_work_id_o=""))


def test_a_blank_doi_is_not_reported_as_malformed():
    """Blank is a legitimate state; only a non-empty bad DOI is a format error."""
    assert "invalid_doi_format" not in _codes(_clean(doi_o="", oa_work_id_o="W1"))


def test_years_parse_from_inconsistent_spellings():
    """year_o/year_r are TEXT and arrive as '2020', '2020.0', ' 2020'."""
    assert _codes(_clean(year_o="2009.0", year_r=" 2015")) == []
    assert record_checks._year("2020.0") == 2020
    assert record_checks._year("  1999") == 1999
    assert record_checks._year("") is None
    assert record_checks._year(None) is None


def test_a_year_that_is_not_a_year_is_ignored_rather_than_flagged():
    """Unparseable is not the same as out of range; the R check only compares
    values it could read."""
    assert _codes(_clean(year_r="in press")) == []


# ---------------------------------------------------------------------------
# The checks themselves
# ---------------------------------------------------------------------------

def test_missing_required_fields():
    assert "missing_required_fields" in _codes(_clean(title_o=""))
    assert "missing_required_fields" in _codes(_clean(title_r=None))


def test_a_replication_needs_a_doi_or_a_url():
    assert "missing_required_fields" in _codes(_clean(doi_r="", url_r=""))
    assert _codes(_clean(doi_r="", url_r="https://example.org/paper")) == []


def test_invalid_doi_format():
    for bad in ("nope", "10.1/x", "https://doi.org/10.1234/x", "10.1234/"):
        assert "invalid_doi_format" in _codes(_clean(doi_r=bad)), bad


def test_literal_na_strings():
    assert "na_string" in _codes(_clean(title_o="NA"))
    assert "na_string" in _codes(_clean(study_r="N/A"))


def test_free_text_is_not_scanned_for_na():
    """'NA' appears legitimately in abstracts and references as a word or an
    initialism; scanning them would bury the real hits."""
    assert _codes(_clean(abstract_r="NA", ref_o="NA")) == []


def test_non_http_urls():
    assert "non_http_url" in _codes(_clean(url_r="ftp://example.org"))
    assert "non_http_url" in _codes(_clean(url_r="example.org"))
    assert _codes(_clean(url_r="https://example.org")) == []


def test_unknown_type():
    assert "invalid_type" in _codes(_clean(type="meta-analysis"))


def test_replication_outcome_vocabulary():
    assert "invalid_outcome" in _codes(_clean(outcome="kind of worked"))
    assert _codes(_clean(outcome="mixed")) == []


def test_reproduction_axes_are_checked_not_the_derived_outcome():
    """Reproductions are coded on two independent axes; the flat outcome is
    derived from them, so checking it too would double-report one problem."""
    repro = _clean(type="reproduction", outcome="anything derived",
                   outcome_computation="computationally reproducible",
                   outcome_robustness="robust")
    assert _codes(repro) == []
    assert "invalid_outcome" in _codes(dict(repro, outcome_robustness="very robust"))


def test_year_out_of_range():
    nxt = date.today().year + 2
    assert "year_out_of_range" in _codes(_clean(year_o="1500"))
    assert "year_out_of_range" in _codes(_clean(year_r=str(nxt)))


def test_replication_before_original():
    assert "replication_before_original" in _codes(_clean(year_o="2015", year_r="2009"))


def test_flag_order_is_stable():
    """Flags are stored and re-stored; churn would make diffs unreadable."""
    messy = _clean(doi_o="bad", title_r="NA", url_r="ftp://x", year_o="1500")
    assert _codes(messy) == _codes(messy)


# ---------------------------------------------------------------------------
# Corrections win: check what would actually be published
# ---------------------------------------------------------------------------

def test_a_correction_resolved_by_consensus_clears_the_flag():
    broken = _clean(doi_o="not-a-doi")
    assert "invalid_doi_format" in _codes(broken)
    assert _codes(broken, {"doi_o": "10.1234/fixed"}) == []


def test_a_correction_can_also_introduce_a_flag():
    assert "invalid_doi_format" in _codes(_clean(), {"doi_o": "rubbish"})


def test_final_columns_already_on_the_record_are_preferred():
    """Branches that resolve no `final` still carry final_* from an earlier pass."""
    rec = _clean(doi_o="not-a-doi", final_doi_o="10.1234/fixed")
    assert _codes(rec) == []


def test_a_none_valued_final_does_not_erase_a_good_record_value():
    assert _codes(_clean(), {"doi_o": None, "title_o": None}) == []


# ---------------------------------------------------------------------------
# The split that issue #139 asked for
# ---------------------------------------------------------------------------

def test_time_varying_and_cross_row_checks_are_not_done_here():
    """Retraction status and link rot change after validation, so a one-shot
    check would read as a clean bill of health that silently goes stale.
    Duplicates and conflicting references need the whole dataset."""
    # Skip the module docstring: it names these precisely to explain why they are
    # NOT here, so a whole-file search matches the explanation, not an import.
    code = CHECKS.split('"""', 2)[2]
    for absent in ("retract", "requests", "httpx", "urlopen", "HEAD("):
        assert absent not in code, f"{absent} belongs on the cron job"


def test_the_split_is_documented_where_the_next_person_will_look():
    doc = CHECKS.split('"""', 2)[1]
    for term in ("cron", "retract", "duplicate", "time-varying"):
        assert term in doc.lower()


# ---------------------------------------------------------------------------
# Advisory, not gating — the decision that was made explicitly
# ---------------------------------------------------------------------------

def test_consensus_records_flags_without_gating_on_them():
    """The flags must never decide where a record lands. If _update_status ever
    branches on them, a flagged record would silently stop being published."""
    body = CONSENSUS.split("def _update_status(", 1)[1].split("\ndef ", 1)[0]
    assert "quality_flags = %s::jsonb" in body
    assert "check_record(record, final)" in body
    for gating in ("if check_record", "if flags", "need_review"):
        assert gating not in body


def test_every_terminal_branch_of_consensus_records_flags():
    """_update_status is the single point every branch reaches, so passing the
    record there covers all of them — including the senior auto-validate path
    that writes straight to `validated` with no admin in the loop."""
    evaluated = CONSENSUS.split("def evaluate_consensus(", 1)[1]
    calls = [ln for ln in evaluated.splitlines() if "_update_status(cur, record_id" in ln]
    assert len(calls) >= 12
    assert all(ln.rstrip().endswith(", record)") for ln in calls), calls


def test_the_backfill_never_changes_validation_status():
    """'Flag but don't reopen': settled records stay settled."""
    # The SET clause of the one UPDATE, not the file: the script also *prints*
    # that it left validation_status alone, which a file-wide search would match.
    update = BACKFILL.split("UPDATE unvalidated", 1)[1].split('"""', 1)[0]
    assert "SET quality_flags" in update
    assert "validation_status" not in update
    assert BACKFILL.count("UPDATE unvalidated") == 1, "only one write path"


def test_the_backfill_clears_flags_that_no_longer_apply():
    """Re-running must not leave a warning about something already fixed."""
    assert "cleared" in BACKFILL


def test_the_backfill_skips_records_still_in_flight():
    """Unfinished records still hold raw extractor values that consensus may yet
    correct, so flagging them would report problems that are not real yet."""
    assert "validation_inprogress" in BACKFILL   # named as deliberately excluded
    assert "FINISHED_STATUSES" in BACKFILL


# ---------------------------------------------------------------------------
# Storage and admin surfacing
# ---------------------------------------------------------------------------

def test_flags_are_stored_on_the_record():
    assert "quality_flags      JSONB NOT NULL DEFAULT '[]'::jsonb" in SCHEMA
    assert "quality_checked_at TIMESTAMPTZ" in SCHEMA


def test_the_schema_statements_are_idempotent():
    """db_schema.sql is re-executed by init_db() on every app start."""
    for line in SCHEMA.splitlines():
        stripped = line.strip()
        if ("quality_flags" in line or "quality_checked_at" in line) and \
                stripped.startswith("ALTER"):
            assert "IF NOT EXISTS" in line
        if "idx_unvalidated_quality_flagged" in line and stripped.startswith("CREATE"):
            assert "IF NOT EXISTS" in line


def test_the_admin_panel_warns_in_red_that_the_record_may_need_excluding():
    """Nothing else tells an admin: the flags do not change validation_status."""
    body = JS.split("const qualityBanner", 1)[1][:1400]
    assert "may need to be excluded" in body
    assert "admin-quality-banner" in body
    assert ".admin-quality-banner strong { color: var(--red); }" in CSS
    assert "border: 1px solid var(--red)" in CSS.split(".admin-quality-banner", 1)[1][:400]


def test_the_banner_is_rendered_first_in_the_panel():
    panel = JS.split('$("#admin-detail-body").innerHTML = `', 1)[1][:300]
    assert panel.index("${qualityBanner}") < panel.index("${abstractBanner}")


def test_flagged_records_are_visible_without_opening_them():
    assert "aq-row-flag" in JS and ".aq-row-flag" in CSS
    assert 'data-filter="quality_flagged"' in HTML
    assert '"quality_flagged":' in APP


def test_the_flag_detail_is_escaped():
    """Flag details embed stored field values straight from the record."""
    body = JS.split("const qualityBanner", 1)[1][:1400]
    assert "escapeHtml(f.label" in body and "escapeHtml(f.detail)" in body


def test_jsonb_flags_survive_the_string_fallback():
    assert '"llm_validator", "quality_flags"' in APP


def test_the_backfill_can_print_its_own_output_on_a_windows_console():
    """Flag labels and this script's help carry non-ASCII glyphs; a cp1252 console
    cannot encode them and print() would abort a run that had already committed."""
    assert "use_utf8_output()" in BACKFILL
