"""Identity is what the server issued, not what the caller claimed."""
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sessions

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Token and cookie primitives
# ---------------------------------------------------------------------------

def test_tokens_are_random_and_stored_only_as_digests():
    first, second = sessions.new_token(), sessions.new_token()
    assert first != second
    assert len(first) >= 40
    digest = sessions.token_digest(first)
    assert digest == sessions.token_digest(first)
    assert first not in digest


def test_cookie_keeps_the_token_away_from_page_scripts():
    kwargs = sessions.cookie_kwargs(3600)
    assert kwargs["httponly"] is True, "a readable cookie can be stolen by injection"
    assert kwargs["samesite"] == "lax", "blocks the cross-site form post"
    assert kwargs["secure"] is True
    assert kwargs["path"] == "/"


def test_secure_can_only_be_disabled_deliberately(monkeypatch):
    monkeypatch.delenv("SESSION_COOKIE_INSECURE", raising=False)
    assert sessions.cookie_is_secure() is True
    monkeypatch.setenv("SESSION_COOKIE_INSECURE", "1")
    assert sessions.cookie_is_secure() is False
    monkeypatch.setenv("SESSION_COOKIE_INSECURE", "no")
    assert sessions.cookie_is_secure() is True


def test_an_unremembered_session_is_short_lived():
    """Leaving the box unticked is the shared-machine choice, so it must matter."""
    for kind in (sessions.KIND_ADMIN, sessions.KIND_VALIDATOR):
        short = sessions.ttl_seconds(kind)
        remembered = sessions.ttl_seconds(kind, remember=True)
        assert short == 12 * 3600, kind
        assert remembered == 30 * 24 * 3600, kind
        assert short < remembered
    with pytest.raises(ValueError):
        sessions.ttl_seconds("something_else")


def test_remembering_lengthens_the_session_and_stores_no_credential():
    """The honest version of "remember me": a cookie the page cannot read."""
    begin = APP.split("def _begin_session(", 1)[1].split("\ndef ", 1)[0]
    assert "sessions.ttl_seconds(kind, remember)" in begin
    assert "sessions.create(" in begin
    # Nothing but the handle is ever written to the browser.
    remember_fn = JS.split("function rememberLogin(", 1)[1].split("\n}", 1)[0]
    assert "password" not in remember_fn


def test_lookup_rejects_an_empty_token_without_touching_the_database():
    cursor = MagicMock()
    assert sessions.lookup(cursor, "") is None
    assert cursor.execute.call_count == 0


def test_lookup_requires_the_row_to_be_live():
    """Expiry and revocation are evaluated by the database, not in Python."""
    cursor = MagicMock()
    sessions.lookup(cursor, "some-token")
    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "revoked_at IS NULL" in sql
    assert "expires_at > NOW()" in sql
    # One statement claims and touches the row, so there is no gap between
    # checking a session and acting on it.
    assert sql.startswith("UPDATE sessions SET last_used_at = NOW()")


# ---------------------------------------------------------------------------
# No endpoint trusts a client-supplied identity any more
# ---------------------------------------------------------------------------

def test_no_endpoint_accepts_coder_id_from_the_caller():
    endpoints = re.findall(
        r'@app\.(?:get|post|delete|put)\("([^"]+)"\)\n(?:@[^\n]+\n)*def \w+\((.*?)\):\n',
        APP, re.S,
    )
    assert endpoints, "no endpoints parsed"
    offenders = [path for path, sig in endpoints if "coder_id" in sig]
    assert offenders == [], f"these still take coder_id from the caller: {offenders}"


def test_no_request_model_carries_coder_id():
    models = re.findall(r"class (\w+)\(BaseModel\):\n((?:    .*\n|\n)*)", APP)
    offenders = [name for name, body in models if "coder_id" in body]
    assert offenders == [], f"models still carrying coder_id: {offenders}"


def test_the_admin_bearer_header_is_gone():
    assert "x_admin_token" not in APP
    assert "X-Admin-Token" not in JS
    assert "_adminToken" not in JS


def test_state_changing_endpoints_are_not_gets():
    """A GET that mutates can be triggered by any page, prefetcher, or cache."""
    assert '@app.post("/api/next-pairs")' in APP
    assert '@app.get("/api/next-pairs")' not in APP


def test_password_changes_and_deletions_revoke_sessions():
    redeem = APP.split("def redeem_auth_link(", 1)[1].split("@app.", 1)[0]
    assert "sessions.revoke_all_for(" in redeem
    delete = APP.split("def delete_admin(", 1)[1].split("@app.", 1)[0]
    assert "sessions.revoke_all_for(" in delete


def test_sign_in_opens_a_session_without_an_email_round_trip():
    """Product decision: handle + email signs in at once, email is a notice.

    Pinned deliberately. This is weaker than an emailed link, and the point of
    the test is that the weakening stays visible rather than becoming folklore.
    """
    body = APP.split("def login(", 1)[1].split("@app.", 1)[0]
    code = body.split('"""', 2)[-1]
    assert "_begin_session(response, sessions.KIND_VALIDATOR" in code
    assert "_notify_sign_in(" in code
    # Nothing issues validator sign-in links any more.
    assert "issue_for_validator" not in APP
    assert "/api/login/redeem" not in APP


def test_both_handle_and_email_must_match_the_same_account():
    """The pairing is the only check left, so it must not be loose."""
    code = APP.split("def login(", 1)[1].split("@app.", 1)[0].split('"""', 2)[-1]
    assert 'existing["handle"] != handle' in code
    assert "This email is already registered" in code
    assert "That handle is already taken." in code


def test_the_sign_in_notice_can_never_block_a_sign_in():
    notify = APP.split("def _notify_sign_in(", 1)[1].split("@app.", 1)[0]
    assert "except Exception:" in notify
    assert "raise" not in notify, "a mail failure must not fail the sign-in"
    # It is sent after the session exists, so it cannot gate anything.
    login = APP.split("def login(", 1)[1].split("def _notify_sign_in", 1)[0]
    assert login.index("_begin_session(") < login.index("_notify_sign_in(")


def test_the_residual_impersonation_risk_stays_documented():
    """If this ever stops being recorded, the risk has quietly become invisible."""
    login = APP.split("def login(", 1)[1].split("@app.", 1)[0]
    assert "DELIBERATE PRODUCT DECISION" in login
    assert "NOT asked to prove they hold the mailbox" in login

    # Match the explicit status line, not prose that happens to use the words:
    # an earlier version of this test passed on a sentence describing the OLD
    # problem, which is exactly the kind of false pass it exists to prevent.
    project = (ROOT / "docs" / "PROJECT.md").read_text(encoding="utf-8")
    assert "**NOT closed: validator authentication.**" in project
    assert "deliberate product decision" in project.lower()

    readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "Validator sign-in is deliberately weak" in readme


# ---------------------------------------------------------------------------
# Cross-site protection
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app_module():
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


def _request(method="POST", **headers):
    request = MagicMock()
    request.method = method
    request.headers = {k.replace("_", "-"): v for k, v in headers.items()}
    return request


@pytest.mark.parametrize("fetch_site", ["same-origin", "same-site", "none"])
def test_same_site_writes_are_allowed(app_module, fetch_site):
    assert app_module._is_cross_site(_request(sec_fetch_site=fetch_site)) is False


def test_cross_site_writes_are_blocked(app_module):
    assert app_module._is_cross_site(_request(sec_fetch_site="cross-site")) is True


def test_a_foreign_origin_is_blocked(app_module, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://validation.forrt.org")
    assert app_module._is_cross_site(_request(origin="https://evil.example")) is True
    assert app_module._is_cross_site(
        _request(origin="https://validation.forrt.org")
    ) is False


def test_reads_are_never_blocked(app_module):
    for method in ("GET", "HEAD", "OPTIONS"):
        assert app_module._is_cross_site(
            _request(method, sec_fetch_site="cross-site")
        ) is False


def test_a_headerless_client_is_not_the_csrf_threat(app_module):
    """curl and scripts carry no ambient cookie, so they are not what this stops."""
    assert app_module._is_cross_site(_request()) is False


def test_the_guard_is_middleware_so_no_route_can_opt_out():
    assert '@app.middleware("http")' in APP
    middleware = APP.split('@app.middleware("http")', 1)[1].split("\n\n\n", 1)[0]
    assert "_is_cross_site(request)" in middleware
    assert "status_code=403" in middleware
    assert "no-store" in middleware


def test_the_personal_code_is_no_longer_a_way_to_sign_in():
    """It survives only as a one-time proof of ownership, not as a credential."""
    model = APP.split("class LoginRequest(BaseModel):", 1)[1].split("class ", 1)[0]
    assert "code:" not in model

    login = APP.split("def login(", 1)[1].split("@app.", 1)[0]
    assert "req.code" not in login

    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    # The sign-in form itself offers no code entry any more.
    # Code entry exists only inside the one-time claim screen, never above it
    # in the sign-in form itself.
    before_claim = html.split('id="claim-code-screen"', 1)[0]
    assert "code-part" not in before_claim
    assert "login-mode-toggle" not in html
    for leftover in ("loginMode", "getCode()", '$("#cp1")'):
        assert leftover not in JS, f"{leftover} still in app.js"


def test_claiming_an_account_trades_the_code_away_for_good():
    """Keeping both would leave the weaker credential as a permanent second door."""
    claim = APP.split("def claim_code_account(", 1)[1].split("@app.", 1)[0]
    assert "SET email = %s, code = NULL" in claim
    # Only an account with no email can be claimed, or knowing someone's code
    # would let an attacker repoint their account at a new mailbox.
    assert 'account["email"]' in claim
    assert "hmac.compare_digest" in claim, "the code compare must be constant-time"
    # One answer for every failure, so this cannot be used to probe handles.
    assert "That username and personal code do not match an account." in claim
    assert claim.count("raise wrong") >= 2
    assert "_throttle_login(handle, request)" in claim
    # Everything above the write is a check-then-act on one row, so the row must
    # be locked and the write itself conditional. Without both, two people
    # presenting the same code concurrently each got a session and the last
    # writer decided which mailbox owned the account.
    assert "FOR UPDATE" in claim
    assert "AND email IS NULL" in claim
    assert "if cur.rowcount == 0:" in claim


def test_a_pre_email_account_is_pointed_at_the_way_out():
    """A dead end with no instructions is how someone ends up emailing support."""
    login = APP.split("def login(", 1)[1].split("@app.", 1)[0]
    assert "created with a personal code" in login
    assert login.index("created with a personal code") < login.index("already taken")

    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert 'id="claim-code-btn"' in html
    assert "Signed up with a personal code?" in html
    assert 'id="claim-code-screen"' in html


def test_the_docs_do_not_describe_the_replaced_auth_model():
    """Stale docs are how a fixed problem gets re-reported, or a live one missed."""
    docs = Path(__file__).resolve().parent.parent / "docs"
    stale = {
        "X-Admin-Token header": "the bearer header is gone",
        "GET /api/next-pairs": "next-pairs is a POST",
        "flora-admin-2025": "the fallback password is gone",
        "return deterministic token": "tokens are opaque sessions now",
    }
    problems = []
    for path in sorted(docs.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for phrase, why in stale.items():
            if phrase in text:
                problems.append(f"{path.name}: {phrase!r} ({why})")
    assert problems == [], "stale documentation:\n  " + "\n  ".join(problems)


def test_the_docs_still_disclose_the_sign_in_weakness():
    docs = Path(__file__).resolve().parent.parent / "docs"
    readme = (docs / "README.md").read_text(encoding="utf-8")
    setup = (docs / "SETUP.md").read_text(encoding="utf-8")
    arch = (docs / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "does not prove mailbox ownership" in readme
    assert "prove mailbox ownership" in setup
    assert "does **not** do: prove that a validator holds the" in arch


def test_no_credential_is_ever_written_to_the_browser():
    """The reason "remember my password" is a session, not localStorage.

    Any script on the page can read localStorage, and encrypting it needs a key
    that same script can read, so there is no safe version of storing it.
    """
    remember = JS.split("function rememberLogin(", 1)[1].split("\n}", 1)[0]
    assert "password" not in remember
    # Only ever the handle and the email address are persisted.
    assert "JSON.stringify({ handle: handle || \"\", email: email || \"\" })" in remember

    admin_submit = JS.split("async function submitAdminSignIn(", 1)[1].split("\n}", 1)[0]
    assert 'rememberLogin(handle, "")' in admin_submit, "admin handle only"
    # Match the call, not the word: the comment above it mentions localStorage.
    assert "localStorage.setItem" not in admin_submit


def test_a_prefilled_validator_form_can_be_cleared_in_one_click():
    """Handle plus email is the whole credential, so it must be droppable."""
    assert 'id="not-you-btn"' in HTML
    assert "Not you? Clear this device" in HTML
    forget = JS.split("function forgetLogin(", 1)[1].split("\n}", 1)[0]
    assert "removeItem(LAST_LOGIN_KEY)" in forget
    prefill = JS.split("function prefillLogin(", 1)[1].split("\n}\n", 1)[0]
    assert "not-you-row" in prefill


def test_signing_out_drops_the_remembered_email():
    """Choosing to leave is the moment to stop holding half the credential."""
    body = JS.split("const logout = async () => {", 1)[1].split("\n};", 1)[0]
    assert 'rememberLogin(saved.handle, "")' in body
    assert body.index('api("/logout"') < body.index("rememberLogin")


def test_both_forms_offer_the_choice():
    assert 'id="remember-me"' in HTML
    assert 'id="admin-remember-me"' in HTML
    assert HTML.count("Stay signed in on this device") == 2
    assert "remember" in JS.split("async function doLogin()", 1)[1][:2000]
    assert "adminLogin(handle, password, remember" in JS \
        or "$(\"#admin-remember-me\")?.checked" in JS


# ---------------------------------------------------------------------------
# Races found by driving concurrent requests
# ---------------------------------------------------------------------------

def test_a_colliding_first_time_signin_is_a_conflict_not_a_crash():
    """Two first-time sign-ins racing used to hand the loser a 500."""
    code = APP.split("def login(", 1)[1].split("def _notify_sign_in", 1)[0]
    insert = code.split("INSERT INTO validators", 1)[1]
    assert "psycopg2.errors.UniqueViolation" in insert
    assert "409" in insert
    assert "Please try again" in insert


def test_the_login_button_cannot_be_double_submitted():
    """The double-click was what produced the collision in the first place."""
    body = JS.split("async function doLogin()", 1)[1].split("\n}\n", 1)[0]
    assert "if (_loginInFlight) return;" in body
    assert "loginBtn.disabled = true" in body
    # And it must always be released, or one failed sign-in bricks the form.
    assert "finally {" in body
    assert "_loginInFlight = false" in body
    assert "loginBtn.disabled = false" in body


def test_the_stay_signed_in_choice_survives_a_visit():
    """Unticking it on a shared machine is a safety decision; it must stick."""
    assert "REMEMBER_KEY" in JS
    assert 'saveRememberChoice("validator"' in JS
    assert 'saveRememberChoice("admin"' in JS
    assert 'restoreRememberChoice("validator", "#remember-me")' in JS
    assert 'restoreRememberChoice("admin", "#admin-remember-me")' in JS
    # "Not you? Clear this device" means this is not my machine: fall back to
    # the safe option rather than leaving a 30-day session armed.
    forget = JS.split("function forgetLogin(", 1)[1].split("\n}", 1)[0]
    assert "removeItem(REMEMBER_KEY)" in forget
    assert "box.checked = false" in forget


def test_account_probes_count_against_the_throttle():
    """Answers about someone else's account must not be askable without limit.

    Two branches tell the caller something about an account they do not own:
    that a handle is taken, and that an account still holds a personal code.
    The second is the more sensitive — it names exactly the accounts worth
    guessing against /api/login/claim-code.
    """
    login = APP.split("def login(", 1)[1].split("def _notify_sign_in", 1)[0]
    branch = login.split("if is_new:", 1)[1]
    taken_block = branch.split('if taken and not taken["email"]:', 1)[1]
    pre_email, rest = taken_block.split("if taken:", 1)
    assert "_record_login_attempt(email, request, False)" in pre_email, \
        "the personal-code disclosure is unthrottled"
    assert "_record_login_attempt(email, request, False)" in rest, \
        "the handle-taken disclosure is unthrottled"
    # Every branch that reveals anything about another account records first.
    assert pre_email.index("_record_login_attempt") < pre_email.index("raise")
