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


def test_update_screen_gate_survives_a_reload():
    # The field the gate reads must reach the browser on the restore path...
    assert "last_seen_update" in _validator_profile_keys("whoami")
    # ...which means _principal() has to select it, or the key is a KeyError.
    principal = ast.get_source_segment(APP_SRC, _function("_principal"))
    assert "last_seen_update" in principal
    # The client-side gate this protects, unchanged.
    assert "(state.coder.last_seen_update ?? 0) < (state.coder.update_version ?? 0)" in JS


def test_startup_replaces_rather_than_merges_the_stored_profile():
    # Documents *why* the test above matters: the server is the sole authority,
    # so localStorage cannot backfill a field the server forgot to send.
    assert "state.coder = me.validator;" in JS
    assert "localStorage.setItem(STORAGE.CODER, JSON.stringify(me.validator));" in JS


# ---------------------------------------------------------------------------
# 2. Idle auto-logout has to end the session, not just the local copy of it.
# ---------------------------------------------------------------------------

def test_idle_logout_revokes_the_server_session_before_reloading():
    body = _code_only(_js_function("_idleRedirect"))
    assert '"/logout", "POST"' in body, (
        "idle logout only cleared localStorage; the HttpOnly cookie stayed live "
        "and the reload signed the same person straight back in via /api/me"
    )
    assert body.index('"/logout"') < body.index("location.reload()")
    assert body.index('"/logout"') < body.index("clearSession()")


def test_idle_logout_cannot_hang_on_a_dead_network():
    body = _code_only(_js_function("_idleRedirect"))
    assert "Promise.race" in body and "_sleep(" in body, (
        "a hung /logout must not leave signed-in content on screen indefinitely"
    )
    assert "location.reload()" in body


def test_idle_logout_no_longer_claims_admins_are_memory_only():
    # Was true under bearer tokens, false under cookies. A stale comment here is
    # what let the bug survive review.
    assert "admin token is in-memory and dies on reload" not in JS


def test_deliberate_logout_still_revokes_first():
    assert re.search(r'try \{ await api\("/logout", "POST"\); \} catch \{\}', JS)


# ---------------------------------------------------------------------------
# 3. A queued judgement belongs to its author, not to the browser.
# ---------------------------------------------------------------------------

def test_queued_judgements_record_their_author():
    body = _js_function("_enqueueSubmit")
    assert "owner_id: state.coder?.coder_id ?? null" in body, (
        "the payload no longer carries coder_id, so without this stamp the "
        "server attributes a flushed item to whoever is signed in at the time"
    )


def test_the_processor_only_sends_the_signed_in_validators_own_work():
    body = _js_function("_processSubmitQueue")
    assert "_isMyQueueItem(it)" in body
    assert "!it.blocked && _isMyQueueItem(it)" in body


def test_recovery_views_never_show_another_validators_pending_work():
    # Reading the raw array in a view would leak a paper title across sign-ins.
    for name in ("_updatePendingIndicator", "_renderPendingPanel", "_pendingRecordIds"):
        body = _js_function(name) if name != "_pendingRecordIds" else ""
        source = _code_only(body or re.search(r"const _pendingRecordIds = .*", JS).group(0))
        assert "_submitQueue" not in source, f"{name}() still reads the whole queue"
        assert "_myQueue()" in source or "_myFailed()" in source or "mine" in source


def test_pending_actions_are_guarded_by_ownership():
    body = _js_function("_pendingAction")
    assert "_isMyQueueItem(_submitQueue[idx])" in body


def test_logout_keeps_the_queue_because_the_work_is_not_the_browsers_to_delete():
    logout = JS[JS.index("const logout = async () => {"):]
    logout = _code_only(logout[:logout.index("\n};")])
    assert "_SUBMIT_KEY" not in logout and "_submitQueue" not in logout


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
