"""Three regressions from moving identity out of localStorage and into a cookie.

Each of these shipped as a working control under the old bearer-token model and
was silently voided by the session-cookie migration, so each test locks the part
of the contract that broke rather than the wording of the fix.
"""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
APP_AST = ast.parse(APP_SRC)


def _function(name):
    for node in ast.walk(APP_AST):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in app.py")


def _returned_dicts(func):
    return [node.value for node in ast.walk(func)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)]


def _keys(dict_node):
    return {k.value for k in dict_node.keys if isinstance(k, ast.Constant)}


def _validator_profile_keys(func_name):
    """The validator profile a handler hands the browser, whatever it is nested in."""
    for returned in _returned_dicts(_function(func_name)):
        keys = _keys(returned)
        if "validator" in keys:                 # /api/me wraps it
            nested = dict(zip([k.value for k in returned.keys], returned.values))
            return _keys(nested["validator"])
        if "coder_id" in keys:                  # login / claim-code return it bare
            return keys
    raise AssertionError(f"{func_name}() returns no validator profile")


def _code_only(src):
    """Drop comments so an order assertion cannot match prose about the code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _js_function(name):
    """Source of one top-level JS function, brace-matched."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", JS, re.M)
    assert match, f"{name}() not found in docs/app.js"
    start = JS.index("{", match.start())
    depth = 0
    for i in range(start, len(JS)):
        if JS[i] == "{":
            depth += 1
        elif JS[i] == "}":
            depth -= 1
            if depth == 0:
                return JS[match.start():i + 1]
    raise AssertionError(f"unbalanced braces in {name}()")


# ---------------------------------------------------------------------------
# 1. GET /api/me must carry the whole profile, because startup() replaces the
#    stored one with it. A missing field is destroyed, not merely absent.
# ---------------------------------------------------------------------------

def test_session_restore_returns_the_same_profile_as_signing_in():
    restored = _validator_profile_keys("whoami")
    for entry_point in ("login", "claim_code_account"):
        assert _validator_profile_keys(entry_point) == restored, (
            f"/api/me and {entry_point}() disagree on the validator profile; "
            "startup() overwrites the stored profile with /api/me's copy, so any "
            "field only the sign-in path returns is lost on the next reload"
        )


# --- the ownership rule itself, actually executed --------------------------

_HELPERS = JS[JS.index("const _queueOwner ="):JS.index("const _myFailed =")]
_HELPERS += "const _myFailed = () => _failedSubmits.filter(_isMyQueueItem);"

_HARNESS = """
%s
const results = [];
for (const c of CASES) {
  state = { coder: c.me === null ? null : { coder_id: c.me } };
  _submitQueue = c.items;
  results.push(_myQueue().map(it => it.tag));
}
console.log(JSON.stringify(results));
""" % _HELPERS.replace("const _queueOwner", "let state, _submitQueue, _failedSubmits;\nconst _queueOwner", 1)

_CASES = [
    # signed in as 7: own items only
    {"me": 7, "items": [{"tag": "mine", "owner_id": 7},
                        {"tag": "theirs", "owner_id": 9}]},
    # a string id from JSON must still match a numeric one
    {"me": 7, "items": [{"tag": "mine", "owner_id": "7"}]},
    # the previously deployed build stamped identity inside the payload
    {"me": 7, "items": [{"tag": "legacy", "payload": {"coder_id": 7}}]},
    # an item with no owner at all belongs to nobody and is never sent
    {"me": 7, "items": [{"tag": "orphan"}]},
    # signed out: nothing is sendable
    {"me": None, "items": [{"tag": "mine", "owner_id": 7}]},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ownership_rule_behaviour():
    script = f"const CASES = {json.dumps(_CASES)};\n{_HARNESS}"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [
        ["mine"],     # another validator's item is filtered out
        ["mine"],     # "7" == 7
        ["legacy"],   # payload.coder_id is honoured as the author
        [],           # unowned is unsendable, not adoptable
        [],           # signed out sends nothing
    ]

# ── a signed-in admin must not be shown a password prompt ─────────────────────
#
# /?admin=1 is the keyboard- and screen-reader-reachable route to the sign-in
# form. startup() opened that form immediately, then restored the session a moment
# later and called enterAdminScreen() — which hid the login screen and showed the
# admin panel but never closed the form. The admin ended up signed in, looking at
# a password prompt sitting on top of their own panel, which is indistinguishable
# from "remember me" having failed. Confirmed in a real browser: with a valid
# remember-me cookie, /?admin=1 left #admin-signin-screen VISIBLE over
# #admin-screen, and elementFromPoint at the centre returned admin-signin-cancel.

def _js_function(name):
    """The source of one top-level function in docs/app.js."""
    start = JS.index(f"function {name}(")
    end = JS.index("\n}\n", start) + 3
    return JS[start:end]


def test_entering_the_admin_screen_closes_the_sign_in_form():
    """Reaching this screen means the server accepted a session, so a sign-in form
    on top of it is wrong whatever opened it. This is the invariant that makes the
    fix hold regardless of which route opened the form."""
    body = _js_function("enterAdminScreen")
    assert "#admin-signin-screen" in body
    assert "hidden" in body


def test_entering_the_admin_screen_clears_the_password_field():
    """A password left in a hidden input is still in the DOM for the next person
    at the machine."""
    assert "admin-signin-password" in _js_function("enterAdminScreen")


def test_the_admin_form_is_not_opened_before_the_session_is_known():
    """Opening it first is what put the prompt on screen for an admin who was
    already signed in; it now waits for /api/me to say there is no session."""
    body = _js_function("startup")
    assert "wantsAdminForm" in body
    # Every call is guarded — there is no longer an unconditional one near the top,
    # which is what opened the form before the session had been checked. (Static
    # mode opens it before /api/me on purpose: there is no server session to wait
    # for, so the guard is the whole contract, not the ordering.)
    assert body.count("openAdminSignIn()") == body.count("if (wantsAdminForm) openAdminSignIn()")
    # The flag is computed before anything can act on it.
    assert body.index("wantsAdminForm =") < body.index("if (wantsAdminForm)")


def test_the_admin_form_still_opens_when_there_is_no_session():
    """The deferral must not become a removal: ?admin=1 is the only route in for
    someone who cannot triple-click, and a signed-out admin still needs it."""
    body = _js_function("startup")
    assert body.count("if (wantsAdminForm) openAdminSignIn();") >= 3   # static, error, no-session
