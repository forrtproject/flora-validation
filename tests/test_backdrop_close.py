"""Overlays close on a backdrop click only when the click started on the backdrop.

Selecting the text of a field by dragging — the admin panel's replication or
original title, say — and releasing the mouse past the panel's edge delivers the
click to the backdrop. Every overlay closed on that, and closing the admin entry
panel reloads the list behind it, so it looked as if the whole site had reloaded
mid-edit.
"""
import re
from pathlib import Path

APP_JS = (Path(__file__).resolve().parent.parent / "docs" / "app.js").read_text(encoding="utf-8")


def test_the_shared_helper_requires_the_press_on_the_backdrop_too():
    start = APP_JS.index("function onBackdropClick(overlay, close) {")
    helper = APP_JS[start:APP_JS.index("\n}\n", start)]
    assert 'addEventListener("pointerdown", (e) => { pressedOnBackdrop = e.target === overlay; })' in helper
    assert "if (e.target === overlay && pressedOnBackdrop) close(e);" in helper


def test_every_overlay_uses_it():
    for overlay in ("assignments-modal", "inbox-modal", "history-modal", "hist-detail-modal",
                    "pending-submissions-modal", "faq-modal", "validator-flags-modal",
                    "admin-detail-modal", "forgot-modal", "src-detail-modal"):
        assert f'onBackdropClick($("#{overlay}"),' in APP_JS, overlay


def test_no_overlay_closes_on_the_release_alone():
    """The old pattern: close whenever the click's target is the backdrop."""
    leftovers = re.findall(r"if \((?:e|ev|event)\.target === (?:e|ev|event)\.currentTarget\)", APP_JS)
    leftovers += re.findall(r'if \((?:e|event)\.target === \$\("#[\w-]+-modal"\)\)', APP_JS)
    assert not leftovers, leftovers


def test_the_skip_dialog_keeps_a_comment_being_selected():
    start = APP_JS.index("function showSkipReasonDialog()")
    dialog = APP_JS[start:APP_JS.index("\n}\n", start)]
    assert "modal.onpointerdown = (event) => { pressedOnBackdrop = event.target === modal; };" in dialog
    assert "if (event.target === modal && pressedOnBackdrop) finish(null);" in dialog
