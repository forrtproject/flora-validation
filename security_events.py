"""Durable record of privileged actions: who did what, when, from where.

The application log already narrates some of this, but a log is the wrong place
to keep it: on Kubernetes it dies with the pod unless shipped elsewhere, it
cannot be queried ("who removed that admin last month?"), and most privileged
actions were never written to it at all.

Events are recorded on the caller's own cursor so they commit with the action
they describe. An action that rolls back takes its event with it, which is the
behaviour you want — the alternative records things that never happened. The
exception is a failed sign-in, which has no surrounding transaction to join.

The actor is always the server's view of the caller, never a claim from the
request body.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Kept far longer than login_attempts, which exists to throttle rather than to
# explain. An audit trail is only useful once the question is asked late.
RETENTION_DAYS = 365

# Some of what lands here is attacker-controlled — the handle on a failed
# sign-in, for one — and an audit table nobody reads until it matters is a
# tempting place to dump megabytes. Bound every field here rather than at each
# call site, so a future caller cannot forget.
MAX_HANDLE = 128
MAX_LABEL = 256
MAX_ID = 128
MAX_IP = 64
MAX_USER_AGENT = 300
MAX_DETAIL_CHARS = 4000

# Actions worth being able to search for afterwards. Listed explicitly so a typo
# becomes an error rather than a silently unfindable event.
ADMIN_SIGNED_IN = "admin.signed_in"
ADMIN_SIGN_IN_FAILED = "admin.sign_in_failed"
ADMIN_SIGNED_OUT = "admin.signed_out"
ADMIN_INVITED = "admin.invited"
ADMIN_INVITE_RESENT = "admin.invite_resent"
ADMIN_PASSWORD_SET = "admin.password_set"
ADMIN_DELETED = "admin.deleted"
ADMIN_TRUST_CHANGED = "admin.trust_changed"
ADMIN_BOOTSTRAPPED = "admin.bootstrapped"
ADMIN_PASSWORDS_MIGRATED = "admin.passwords_migrated"
VALIDATOR_TIER_CHANGED = "validator.tier_changed"
VALIDATOR_CODE_CLAIMED = "validator.code_claimed"
VALIDATOR_ASSIGNED = "validator.assigned"
MAINTENANCE_STARTED = "maintenance.started"
SOURCE_SYNC_DISPATCHED = "source_sync.dispatched"

ACTIONS = frozenset({
    ADMIN_SIGNED_IN, ADMIN_SIGN_IN_FAILED, ADMIN_SIGNED_OUT, ADMIN_INVITED,
    ADMIN_INVITE_RESENT, ADMIN_PASSWORD_SET, ADMIN_DELETED,
    ADMIN_TRUST_CHANGED, ADMIN_BOOTSTRAPPED, ADMIN_PASSWORDS_MIGRATED,
    VALIDATOR_TIER_CHANGED, VALIDATOR_CODE_CLAIMED, VALIDATOR_ASSIGNED,
    MAINTENANCE_STARTED,
    SOURCE_SYNC_DISPATCHED,
})


def _clip(value: str | None, limit: int) -> str | None:
    """Trim a field to its column budget, keeping None as None."""
    if value is None:
        return None
    text = str(value)
    return text[:limit] if len(text) > limit else text


def _detail_json(detail: dict | None) -> str:
    """Serialise the detail blob, refusing to store an unbounded one."""
    encoded = json.dumps(detail or {})
    if len(encoded) > MAX_DETAIL_CHARS:
        logger.warning("Security event detail was too large; storing a summary")
        return json.dumps({"truncated": True, "bytes": len(encoded)})
    return encoded


def record(cur, action: str, *, actor_kind: str | None = None,
           actor_id: int | None = None, actor_handle: str | None = None,
           target_kind: str | None = None, target_id: str | None = None,
           target_label: str | None = None, client_ip: str | None = None,
           user_agent: str | None = None, detail: dict | None = None) -> None:
    """Append one event on the caller's cursor, inside their transaction.

    Never raises: an audit write must not be the thing that fails a legitimate
    admin action. A failure is logged loudly instead, because a silently empty
    audit trail is worse than a noisy one.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown security action: {action}")
    try:
        cur.execute(
            """
            INSERT INTO security_events
                (action, actor_kind, actor_id, actor_handle, target_kind,
                 target_id, target_label, client_ip, user_agent, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                action, actor_kind, actor_id, _clip(actor_handle, MAX_HANDLE),
                _clip(target_kind, MAX_HANDLE),
                _clip(str(target_id) if target_id is not None else None, MAX_ID),
                _clip(target_label, MAX_LABEL), _clip(client_ip, MAX_IP),
                _clip(user_agent, MAX_USER_AGENT), _detail_json(detail),
            ),
        )
    except Exception:
        logger.exception("Could not record security event %r", action)


def prune(cur) -> int:
    cur.execute(
        "DELETE FROM security_events "
        "WHERE occurred_at < NOW() - (%s * INTERVAL '1 day')",
        (RETENTION_DAYS,),
    )
    return cur.rowcount
