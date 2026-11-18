"""Rejecting a record vs recording the other FLoRA type.

The admin panel's single "✗ Reject — Not a Replication" read as if reproductions
were to be rejected too, though both types belong in FLoRA and the resolve form
could always save a record as a reproduction. Then "Not a replication?" and
"neither a replication nor a reproduction" read as if a paper that calls itself
a replication could not be left out. Now the panel offers "↻ Is a reproduction"
(which opens its fields, also from a rejected record's notice) apart from
"✗ Reject — does not meet FLoRA inclusion criteria", which confirms first; and every
screen names a `not_validation` the same way.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def _admin_detail():
    start = APP_JS.index("function renderAdminDetail(data) {")
    return APP_JS[start:APP_JS.index("\n}\n", start)]


def test_the_panel_offers_the_other_type_apart_from_rejecting():
    panel = _admin_detail()
    assert 'class="admin-reject-heading">Other decisions</p>' in panel
    assert 'id="admin-other-type-btn"' in panel
    assert '↻ Is a ${finalType === "reproduction" ? "replication" : "reproduction"}' in panel
    assert "✗ Reject — does not meet FLoRA inclusion criteria" in panel
    assert "left out of FLoRA, whatever the paper calls itself" in panel


def test_the_other_type_opens_its_fields_through_the_type_selector():
    panel = _admin_detail()
    open_as = panel[panel.index("const openAsType = (type) => {"):]
    open_as = open_as[:open_as.index("\n  };")]
    assert '$("#ar-normal-form")?.classList.remove("hidden")' in open_as     # the edit panel
    assert 'sel.dispatchEvent(new Event("change"))' in open_as               # outcome + axes follow
    assert '$("#ar-outcome-computation")' in open_as                         # where a reproduction needs input
    assert 'addEventListener("click", (e) => openAsType(e.currentTarget.dataset.type))' in panel


def test_a_rejected_record_can_be_opened_as_a_reproduction():
    panel = _admin_detail()
    assert 'id="notval-is-repro-btn"' in panel
    assert '$("#notval-is-repro-btn")?.addEventListener("click", () => openAsType("reproduction"))' in panel


def test_switching_the_type_keeps_the_choice_offering_the_other_one():
    panel = _admin_detail()
    handler = panel[panel.index('$("#ar-type-sel")?.addEventListener("change"'):]
    handler = handler[:handler.index("\n  });")]
    assert 'other.dataset.type = ev.target.value === "reproduction" ? "replication" : "reproduction"' in handler
    assert "other.textContent = `↻ Is a ${other.dataset.type}`" in handler
    assert "heading" not in handler          # the heading no longer names a type


def test_rejecting_asks_first():
    panel = _admin_detail()
    handler = panel[panel.index('$("#admin-reject-btn")?.addEventListener("click"'):]
    assert handler.index("await showConfirm(") < handler.index("adminApi(")
    assert "if (!sure) return;" in handler


def test_the_confirmation_names_the_other_type_button_as_it_reads():
    """On a reproduction the button offers "Is a replication"; the confirmation
    pointed to an "It's a reproduction" that was not there."""
    panel = _admin_detail()
    handler = panel[panel.index('$("#admin-reject-btn")?.addEventListener("click"'):]
    handler = handler[:handler.index("adminApi(")]
    assert 'const other = $("#admin-other-type-btn")?.dataset.type || "reproduction";' in handler
    assert "`If it is a ${other} that meets them, use “Is a ${other}” instead.`" in handler
    assert "If it is a reproduction, use" not in handler


def test_no_screen_still_calls_it_not_a_replication():
    for old in ("Reject — Not a Replication", "Mark as Not a Replication",
                "marked as not a replication", "rejected</strong> as not a replication",
                "mark as not a replication", "<small>not a replication</small>",
                "not studying replication", "it's not a validation",
                "neither a replication nor a reproduction", "Not either type",
                "neither type — not in FLoRA", "Not a replication/reproduction",
                "not a replication or reproduction", "↻ It's a", "Not a ${"):
        assert old not in APP_JS, old
    assert 'const NOT_IN_FLORA_LABEL = "does not meet FLoRA inclusion criteria";' in APP_JS
    assert APP_JS.count('v.corrected_type === "not_validation" ? NOT_IN_FLORA_LABEL') == 2


def test_judging_names_it_by_the_inclusion_criteria_too():
    adjudication = (ROOT / "docs" / "adjudication.js").read_text(encoding="utf-8")
    for old in ('"Not a replication"', '"not a replication"', "not in FLoRA's scope"):
        assert old not in adjudication, old
    assert 'not_a_replication: "Does not meet FLoRA inclusion criteria"' in adjudication
