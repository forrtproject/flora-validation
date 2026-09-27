"""Rejecting "not a replication" vs recording the other FLoRA type.

The admin panel's single "✗ Reject — Not a Replication" read as if reproductions
were to be rejected too, though both types belong in FLoRA and the resolve form
could always save a record as a reproduction. Now the panel asks "Not a
replication?" and offers the other type (which opens its fields, also from a
rejected record's notice) apart from "✗ Reject — not in FLoRA", which confirms
first; and every screen names a `not_validation` the same way.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def _admin_detail():
    start = APP_JS.index("function renderAdminDetail(data) {")
    return APP_JS[start:APP_JS.index("\n}\n", start)]


def test_the_panel_offers_the_other_type_apart_from_rejecting():
    panel = _admin_detail()
    assert 'class="admin-reject-heading">Not a ${finalType === "reproduction" ? "reproduction" : "replication"}?' in panel
    assert 'id="admin-other-type-btn"' in panel
    assert "✗ Reject — not in FLoRA" in panel
    assert "neither a replication nor a reproduction" in panel


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
    assert "other.dataset.type = isRepro ? \"replication\" : \"reproduction\"" in handler
    assert 'heading.textContent = `Not a ${isRepro ? "reproduction" : "replication"}?`' in handler


def test_rejecting_asks_first():
    panel = _admin_detail()
    handler = panel[panel.index('$("#admin-reject-btn")?.addEventListener("click"'):]
    assert handler.index("await showConfirm(") < handler.index("adminApi(")
    assert "if (!sure) return;" in handler


def test_the_confirmation_names_the_other_type_button_as_it_reads():
    """On a reproduction the button offers "It's a replication"; the confirmation
    pointed to an "It's a reproduction" that was not there."""
    panel = _admin_detail()
    handler = panel[panel.index('$("#admin-reject-btn")?.addEventListener("click"'):]
    handler = handler[:handler.index("adminApi(")]
    assert 'const other = $("#admin-other-type-btn")?.dataset.type || "reproduction";' in handler
    assert "`If it is a ${other}, use “It's a ${other}” instead.`" in handler
    assert "If it is a reproduction, use" not in handler


def test_no_screen_still_calls_it_not_a_replication():
    for old in ("Reject — Not a Replication", "Mark as Not a Replication",
                "marked as not a replication", "rejected</strong> as not a replication",
                "mark as not a replication", "<small>not a replication</small>",
                "not studying replication", "it's not a validation"):
        assert old not in APP_JS, old
    assert 'const NOT_IN_FLORA_LABEL = "neither type — not in FLoRA";' in APP_JS
    assert APP_JS.count('v.corrected_type === "not_validation" ? NOT_IN_FLORA_LABEL') == 2
