"""Telling a validator that the review team wrote to them.

The message itself lives in validator_messages. These tests cover what goes with
it: the email (sent only once the message is committed, never with its text, and
never able to fail the request), what the admin is told about that email, and the
small query the open app polls for news.
"""
import contextlib
import inspect
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from email_templates import APP_URL, new_message_email

ADMIN = {"id": 1, "handle": "Hamid", "trusted": True}
SUBJECT = "Subject-not-for-the-mail"
BODY = "Body-not-for-the-mail"
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def app_module():
    """Import app.py with the database and scheduler stubbed out."""
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


class FakeCursor:
    """Answers the statements the messaging endpoints issue, keeping what they write.

    Unknown SQL fails the test: an endpoint that asks for something else must not
    be answered with whatever the last statement left behind.
    """

    def __init__(self, email="sam@example.org", handle="sam", flagged=False,
                 queue_validator=7, others=()):
        self.validators = {7: {"id": 7, "handle": handle, "email": email}}
        self.validators.update({v["id"]: v for v in others})
        self.slot = {"flagged": flagged, "validator_id": queue_validator, "record_id": "r1"}
        self.messages = []
        self._one, self._all = None, []

    def execute(self, sql, params=()):
        self._one, self._all = None, []
        if "SET flagged = NOT flagged" in sql:
            self.slot["flagged"] = not self.slot["flagged"]
            self._one = dict(self.slot)
        elif "UPDATE validation_queue SET flag_reason" in sql:
            pass
        elif "SELECT title_r, study_r, doi_r FROM unvalidated" in sql:
            self._one = {"title_r": SUBJECT, "study_r": None, "doi_r": None}
        elif "SELECT handle, email FROM validators WHERE id = %s" in sql:
            self._one = self.validators.get(params[0])
        elif sql.strip() == "SELECT id FROM validators":
            self._all = list(self.validators.values())
        elif "FROM validator_messages m" in sql and "JOIN validators v ON v.id = m.validator_id" in sql:
            if params == (40,):
                self._one = {"validator_id": 7, "subject": SUBJECT, "queue_id": None,
                             "handle": self.validators[7]["handle"],
                             "email": self.validators[7]["email"]}
        elif "SELECT validator_id, subject, direction, queue_id FROM validator_messages" in sql:
            self._one = {"validator_id": 7, "subject": SUBJECT, "direction": "outbound", "queue_id": None}
        elif "INSERT INTO validator_messages" in sql:
            self.messages.append(params)
            self._one = {"id": 41, "sent_at": NOW}
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def executemany(self, sql, rows):
        assert "INSERT INTO validator_messages" in sql
        self.messages.extend(rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class Mailbox:
    """The Resend client, plus a record of what happened in which order."""

    def __init__(self, error=None):
        self.error, self.sent, self.events = error, [], []
        self.client = MagicMock()
        self.client.Emails.send.side_effect = self._send

    def _send(self, mail):
        self.events.append("mail")
        if self.error:
            raise self.error
        self.sent.append(mail)

    @contextlib.contextmanager
    def db(self, cursor, commit_error=None):
        self.events.append("begin")
        yield cursor
        if commit_error:
            raise commit_error
        self.events.append("commit")


@contextlib.contextmanager
def running(app_module, cursor, mailbox, key="re_test_key", commit_error=None):
    with patch.object(app_module, "db", lambda: mailbox.db(cursor, commit_error)), \
         patch.object(app_module, "RESEND_API_KEY", key), \
         patch.object(app_module, "resend", mailbox.client):
        yield


def send_message(app_module):
    req = app_module.AdminMessageRequest(validator_id=7, subject=SUBJECT, body=BODY)
    return app_module.admin_send_message(req, admin=ADMIN)


def reply(app_module):
    return app_module.admin_reply_to_thread(40, app_module.AdminReplyRequest(body=BODY), admin=ADMIN)


def flag(app_module, reason=BODY):
    return app_module.admin_flag_queue("q1", app_module.FlagQueueRequest(reason=reason), admin=ADMIN)


# Every path that writes one message to one validator, and what it returned before.
SENDING = pytest.mark.parametrize(("call", "keys"), [
    (send_message, {"ok": True}),
    (reply, {"ok": True, "id": 41, "sent_at": NOW.isoformat()}),
    (flag, {"flagged": True}),
], ids=["message", "reply", "flag"])


# ---------------------------------------------------------------------------
# The three paths that email
# ---------------------------------------------------------------------------

@SENDING
def test_the_validator_is_emailed_at_the_address_on_file(app_module, call, keys):
    cursor, mailbox = FakeCursor(), Mailbox()
    with running(app_module, cursor, mailbox):
        result = call(app_module)

    assert result == {**keys, "email_notice": "sent"}
    assert len(cursor.messages) == 1
    [mail] = mailbox.sent
    assert mail["to"] == ["sam@example.org"]
    assert mail["from"] == app_module.EMAIL_FROM
    assert APP_URL in mail["html"] and APP_URL in mail["text"]


@SENDING
def test_the_mail_carries_neither_subject_nor_body(app_module, call, keys):
    mailbox = Mailbox()
    cursor = FakeCursor()
    with running(app_module, cursor, mailbox):
        call(app_module)

    stored = str(cursor.messages)
    assert BODY in stored and SUBJECT in stored, "the message itself has both"
    mail = str(mailbox.sent)
    assert BODY not in mail and SUBJECT not in mail and "flagged" not in mail


@SENDING
def test_the_mail_goes_out_after_the_message_is_committed(app_module, call, keys):
    """Sending inside the transaction would hold a pooled connection (and the flag's
    row lock) for the length of an HTTP call, about a message that may roll back."""
    mailbox = Mailbox()
    with running(app_module, FakeCursor(), mailbox):
        call(app_module)

    assert mailbox.events == ["begin", "commit", "mail"]


@SENDING
def test_no_mail_for_a_message_that_was_not_saved(app_module, call, keys):
    mailbox = Mailbox()
    with running(app_module, FakeCursor(), mailbox, commit_error=RuntimeError("commit failed")):
        with pytest.raises(RuntimeError, match="commit failed"):
            call(app_module)

    assert mailbox.events == ["begin"]


@SENDING
@pytest.mark.parametrize("email", [None, ""])
def test_no_address_on_file(app_module, call, keys, email):
    cursor, mailbox = FakeCursor(email=email), Mailbox()
    with running(app_module, cursor, mailbox):
        result = call(app_module)

    assert result == {**keys, "email_notice": "no_email"}
    assert len(cursor.messages) == 1
    assert mailbox.client.Emails.send.call_count == 0


@SENDING
def test_email_is_not_set_up(app_module, call, keys):
    cursor, mailbox = FakeCursor(), Mailbox()
    with running(app_module, cursor, mailbox, key=""):
        result = call(app_module)

    assert result == {**keys, "email_notice": "off"}
    assert len(cursor.messages) == 1
    assert mailbox.client.Emails.send.call_count == 0


@SENDING
def test_a_mail_failure_leaves_the_message_sent(app_module, call, keys, caplog):
    cursor, mailbox = FakeCursor(), Mailbox(error=RuntimeError("Resend is down"))
    with running(app_module, cursor, mailbox):
        result = call(app_module)

    assert result == {**keys, "email_notice": "failed"}
    assert len(cursor.messages) == 1
    assert mailbox.events == ["begin", "commit", "mail"]
    assert "Resend is down" in caplog.text, "the failure is logged with its cause"


# ---------------------------------------------------------------------------
# The paths that do not
# ---------------------------------------------------------------------------

def test_a_broadcast_emails_nobody(app_module):
    others = [{"id": 8, "handle": "jo", "email": "jo@example.org"}]
    cursor, mailbox = FakeCursor(others=others), Mailbox()
    req = app_module.AdminMessageRequest(subject=SUBJECT, body=BODY, broadcast=True)
    with running(app_module, cursor, mailbox):
        result = app_module.admin_send_message(req, admin=ADMIN)

    assert result == {"ok": True, "broadcast": True, "sent": 2, "email_notice": None}
    assert len(cursor.messages) == 2
    assert mailbox.client.Emails.send.call_count == 0


@pytest.mark.parametrize(("already_flagged", "reason", "judge", "now_flagged"), [
    (True, BODY, 7, False),    # unflagging, even with a reason in the request
    (False, "  ", 7, True),    # no reason, so no message
    (False, BODY, None, True),  # nobody holds the slot
], ids=["unflag", "no reason", "no validator"])
def test_a_flag_without_a_message_emails_nobody(app_module, already_flagged, reason, judge, now_flagged):
    cursor, mailbox = FakeCursor(flagged=already_flagged, queue_validator=judge), Mailbox()
    with running(app_module, cursor, mailbox):
        result = flag(app_module, reason)

    assert result == {"flagged": now_flagged, "email_notice": None}
    assert cursor.messages == []
    assert mailbox.client.Emails.send.call_count == 0


def test_a_flag_without_a_request_body_emails_nobody(app_module):
    cursor, mailbox = FakeCursor(), Mailbox()
    with running(app_module, cursor, mailbox):
        result = app_module.admin_flag_queue("q1", None, admin=ADMIN)

    assert result == {"flagged": True, "email_notice": None}
    assert mailbox.client.Emails.send.call_count == 0


def test_a_validators_own_reply_emails_nobody(app_module):
    cursor, mailbox = FakeCursor(), Mailbox()
    with running(app_module, cursor, mailbox):
        result = app_module.reply_to_message(
            40, app_module.ReplyRequest(body=BODY), validator={"coder_id": 7})

    assert result == {"ok": True, "id": 41}
    assert len(cursor.messages) == 1
    assert mailbox.client.Emails.send.call_count == 0


def test_an_unknown_validator_or_thread_is_refused_and_emails_nobody(app_module):
    from fastapi import HTTPException

    cursor, mailbox = FakeCursor(), Mailbox()
    req = app_module.AdminMessageRequest(validator_id=99, subject=SUBJECT, body=BODY)
    with running(app_module, cursor, mailbox):
        with pytest.raises(HTTPException) as unknown:
            app_module.admin_send_message(req, admin=ADMIN)
        with pytest.raises(HTTPException) as gone:
            app_module.admin_reply_to_thread(99, app_module.AdminReplyRequest(body=BODY), admin=ADMIN)

    assert (unknown.value.status_code, gone.value.status_code) == (404, 404)
    assert cursor.messages == []
    assert mailbox.client.Emails.send.call_count == 0


# ---------------------------------------------------------------------------
# The template
# ---------------------------------------------------------------------------

def test_the_handle_is_escaped_in_the_html(app_module):
    mailbox = Mailbox()
    with running(app_module, FakeCursor(handle='<img src=x onerror="alert(1)">'), mailbox):
        send_message(app_module)

    [mail] = mailbox.sent
    assert "<img src=x" not in mail["html"]
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in mail["html"]


def test_the_template_is_complete():
    mail = new_message_email("sam")
    assert set(mail) == {"subject", "html", "text"}
    assert "FLoRA review team" in mail["subject"]
    assert "sam" in mail["html"] and "sam" in mail["text"]
    assert f'href="{APP_URL}"' in mail["html"]
    assert "reply in the app" in mail["html"] and "reply in the app" in mail["text"]


# ---------------------------------------------------------------------------
# What the open app polls
# ---------------------------------------------------------------------------

class SqliteCursor:
    """Runs the endpoint's own SQL, so the scoping is tested and not just read."""

    def __init__(self, rows):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        self.cur = connection.cursor()
        self.cur.execute("CREATE TABLE validator_messages "
                         "(id INTEGER, validator_id INTEGER, direction TEXT, is_read BOOLEAN)")
        self.cur.executemany("INSERT INTO validator_messages VALUES (?, ?, ?, ?)", rows)
        self.statements = 0

    def execute(self, sql, params=()):
        self.statements += 1
        self.cur.execute(sql.replace("%s", "?"), params)

    def fetchone(self):
        return self.cur.fetchone()


INBOXES = [
    # id, validator, direction, read
    (1, 7, "outbound", True),
    (2, 7, "outbound", False),
    (3, 7, "inbound", False),    # sam's own reply: never counts, whatever its flag
    (4, 8, "outbound", False),   # jo's
    (5, 8, "outbound", False),
    (6, 8, "inbound", False),
]


@pytest.mark.parametrize(("coder_id", "expected"), [
    (7, {"unread": 1, "latest_id": 2}),
    (8, {"unread": 2, "latest_id": 5}),
    (9, {"unread": 0, "latest_id": None}),   # no messages at all
])
def test_unread_counts_only_the_signed_in_validators_messages(app_module, coder_id, expected):
    cursor = SqliteCursor(INBOXES)
    with running(app_module, cursor, Mailbox()):
        assert app_module.get_unread_messages(validator={"coder_id": coder_id}) == expected
    assert cursor.statements == 1, "one tiny query per poll"


def test_unread_takes_the_validator_from_the_session_only(app_module):
    parameters = inspect.signature(app_module.get_unread_messages).parameters
    assert list(parameters) == ["validator"], "nothing in the request names an account"
    assert parameters["validator"].default.dependency is app_module.current_validator
    [route] = [r for r in app_module.app.routes if getattr(r, "path", "") == "/api/messages/unread"]
    assert route.methods == {"GET"}


# ---------------------------------------------------------------------------
# Admins see the address
# ---------------------------------------------------------------------------

class Answers:
    """Hands out prepared results in order, keeping the SQL."""

    def __init__(self, *answers):
        self.answers, self.sql = list(answers), []

    def execute(self, sql, params=()):
        self.sql.append(" ".join(sql.split()))
        self.current = self.answers.pop(0)

    def fetchone(self):
        return self.current

    def fetchall(self):
        return self.current


@pytest.mark.parametrize("email", ["sam@example.org", None])
def test_the_conversation_list_returns_the_validators_email(app_module, email):
    row = {"thread_id": 40, "validator_id": 7, "validator_handle": "sam", "validator_email": email,
           "subject": SUBJECT, "admin_name": "Hamid", "queue_id": None, "last_activity": NOW,
           "unread_count": 0, "last_body": BODY, "last_direction": "outbound"}
    cursor = Answers([row])
    with running(app_module, cursor, Mailbox()):
        [conversation] = app_module.list_admin_conversations(admin=ADMIN)["conversations"]

    assert conversation["validator_email"] == email
    assert conversation["validator_handle"] == "sam"
    assert "v.email AS validator_email" in cursor.sql[0]


@pytest.mark.parametrize(("meta", "email"), [
    ({"handle": "sam", "validator_email": "sam@example.org", "subject": SUBJECT, "admin_name": "Hamid"},
     "sam@example.org"),
    ({"handle": "sam", "validator_email": None, "subject": SUBJECT, "admin_name": "Hamid"}, None),
    (None, None),   # the thread is gone
])
def test_a_thread_returns_the_validators_email(app_module, meta, email):
    cursor = Answers([], meta)
    with running(app_module, cursor, Mailbox()):
        thread = app_module.get_admin_thread(40, admin=ADMIN)

    assert thread["validator_email"] == email
    assert set(thread) == {"messages", "handle", "validator_email", "subject", "admin_name"}
    assert "v.email AS validator_email" in cursor.sql[1]
