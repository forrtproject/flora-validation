"""Judging the disagreements: what a Trusted or Senior validator sees and answers.

Each record needs two submitted judgements from two different validators, as
each record in the main app does. A validator works on one record at a time:
opening the next one claims it, which holds one of its two places for
CLAIM_MINUTES; after that another validator may take the place, though the
claim stays good for as long as the place is still free. Claims, submissions
and skips are serialised by one advisory lock, so two validators can never take
the same place.

A submission earns points as a normal judgement does (the validator's
vote_score, plus 2 for a decided original, 2 for a decided outcome and 1 for a
note), added to their normal total in the same transaction. The second
submission moves the record to 'awaiting_approval'. Validators never see each
other's answers.
"""

from __future__ import annotations

import re

from extractor_vocab import CURRENT_REPRODUCTION_OUTCOMES, FLAWED_OUTCOME, DESCRIPTIVE_OUTCOME

JUDGES_PER_RECORD = 2
CLAIM_MINUTES = 60
MIN_TIER = 1                     # 1 Trusted, 2 Senior
NOTE_LIMIT = 2000
# Serialises claims and submissions; distinct from the setup and import locks.
JUDGING_LOCK_ID = 7_342_025_096
LOCK_TIMEOUT = "5s"

CANNOT_TELL = "cannot_tell"
ORIGINAL_CHOICES = ("flora", "observatory", "both", "neither", CANNOT_TELL)
# FLoRA's replication outcomes (extractor_vocab.REPLICATION_OUTCOMES), in the
# order the judging screen offers them.
REPLICATION_CHOICES = (
    "successful", "failed", "mixed", FLAWED_OUTCOME, "uninformative",
    DESCRIPTIVE_OUTCOME, "cannot_be_determined", "not_a_replication",
)
# Served first: records already half judged, then the kinds in this order. The
# analysis spec samples every 'same original, different outcome' row.
KIND_ORDER = (
    "same original, different outcome",
    "different original",
    "we found no original",
    "MO names no original DOI",
)

_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


class Refused(Exception):
    """A request the rules refuse; *status* is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _doi(value) -> str:
    return _DOI_PREFIX.sub("", str(value or "").strip()).strip().lower()


def can_judge(validator: dict) -> bool:
    return int(validator.get("validator_tier") or 0) >= MIN_TIER


# ---------------------------------------------------------------------------
# What each record asks
# ---------------------------------------------------------------------------

def original_choices(record: dict) -> tuple[str, ...]:
    """The answers to "which original is right?" that make sense for this record."""
    flora, mo = _doi(record.get("flora_doi_o")), _doi(record.get("mo_doi_o"))
    if flora and mo and flora == mo:
        # Both name the same original: is it the right one? 'both' = yes.
        return ("both", "neither", CANNOT_TELL)
    choices = []
    if flora:
        choices.append("flora")
    if mo:
        choices.append("observatory")
    if flora and mo:
        choices.append("both")
    return (*choices, "neither", CANNOT_TELL)


def doi_required(record: dict) -> bool:
    """With no original on either side, 'neither' only means something with a DOI."""
    return not _doi(record.get("flora_doi_o")) and not _doi(record.get("mo_doi_o"))


def outcome_choices(record: dict) -> tuple[str, ...]:
    """FLoRA's replication outcomes, FLoRA's own answer when it is a reproduction
    outcome (two axes, e.g. "computational issues, robust"), and "can't tell"."""
    extra = ()
    flora = str(record.get("flora_outcome") or "").strip().lower()
    if flora in CURRENT_REPRODUCTION_OUTCOMES:
        extra = (flora,)
    return (*REPLICATION_CHOICES, *extra, CANNOT_TELL)


def check_answer(record: dict, answer: dict) -> dict:
    """The answer cleaned, or Refused(422) naming what is wrong with it."""
    choice = str(answer.get("original_choice") or "").strip()
    if choice not in original_choices(record):
        raise Refused(422, "Choose which original is right")
    suggested = _doi(answer.get("suggested_doi_o"))
    if suggested and choice != "neither":
        raise Refused(422, "A suggested DOI goes with \"neither\"")
    if suggested and not _DOI.match(suggested):
        raise Refused(422, "That does not look like a DOI (10.xxxx/…)")
    if choice == "neither" and doi_required(record) and not suggested:
        raise Refused(422, "Give the DOI of the original you found")
    if suggested and suggested == _doi(record.get("flora_doi_o")):
        raise Refused(422, "That is FLoRA's original: choose FLoRA's answer instead")
    if suggested and suggested == _doi(record.get("mo_doi_o")):
        raise Refused(422, "That is the Observatory's original: choose its answer instead")
    outcome = str(answer.get("outcome") or "").strip().lower()
    if outcome not in outcome_choices(record):
        raise Refused(422, "Choose the outcome")
    note = str(answer.get("note") or "").strip()
    if len(note) > NOTE_LIMIT:
        raise Refused(422, f"The note is longer than {NOTE_LIMIT} characters")
    return {"original_choice": choice, "suggested_doi_o": suggested or None,
            "outcome": outcome, "note": note or None}


def points_for(vote_score: int, answer: dict) -> int:
    """As a normal judgement: the validator's base, +2 for a decided original,
    +2 for a decided outcome, +1 for a note."""
    points = int(vote_score or 0)
    if answer["original_choice"] != CANNOT_TELL:
        points += 2
    if answer["outcome"] != CANNOT_TELL:
        points += 2
    if answer["note"]:
        points += 1
    return points


def public_view(record: dict) -> dict:
    """What a validator sees: both answers, labelled by source. Never another
    validator's judgement, never the raw CSV row."""
    return {
        "record_id": str(record["record_id"]),
        "kind": record["kind"],
        "doi_r": record["doi_r"],
        "title_r": record.get("title_r"),
        "abstract_r": record.get("abstract_r"),
        "abstract_source": record.get("abstract_source"),
        "year_r": record.get("year_r"),
        "flora": {
            "doi_o": record.get("flora_doi_o"),
            "title_o": record.get("flora_title_o"),
            "outcome": record.get("flora_outcome"),
            "outcome_quote": record.get("flora_outcome_quote"),
            "quote_source": record.get("flora_quote_source"),
            "link_method": record.get("flora_link_method"),
            "link_confidence": record.get("flora_link_confidence"),
            "link_evidence": record.get("flora_link_evidence"),
        },
        "observatory": {
            "doi_o": record.get("mo_doi_o"),
            "title_o": record.get("mo_title_o"),
            "outcome": record.get("mo_outcome"),
            "replication_type": record.get("mo_replication_type"),
            "discipline": record.get("mo_discipline"),
            "confidence": record.get("mo_confidence"),
        },
        "original_choices": list(original_choices(record)),
        "outcome_choices": list(outcome_choices(record)),
        "doi_required": doi_required(record),
    }


# ---------------------------------------------------------------------------
# Claims, submissions and skips
# ---------------------------------------------------------------------------

# The places other validators hold on record r: their submissions and their
# live claims. %(vid)s is the asking validator, %(ttl)s CLAIM_MINUTES.
_TAKEN_BY_OTHERS = """
    (SELECT COUNT(*) FROM adjudication.judgements o
      WHERE o.record_id = r.record_id AND o.validator_id <> %(vid)s
        AND (o.state = 'submitted'
             OR (o.state = 'claimed'
                 AND o.claimed_at > NOW() - make_interval(mins => %(ttl)s))))
"""
# Records still open to this validator: open, never submitted or skipped by
# them, and with a place free. Their own claim counts as theirs.
_OPEN_TO_ME = f"""
    r.status = 'open'
    AND NOT EXISTS (SELECT 1 FROM adjudication.judgements m
                     WHERE m.record_id = r.record_id AND m.validator_id = %(vid)s
                       AND m.state <> 'claimed')
    AND {_TAKEN_BY_OTHERS} < {JUDGES_PER_RECORD}
"""


def _params(validator: dict) -> dict:
    return {"vid": int(validator["coder_id"]), "ttl": CLAIM_MINUTES}


def _lock(cur) -> None:
    cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (JUDGING_LOCK_ID,))


def progress(cur, validator: dict) -> dict:
    """How many records are left for this validator, and how many they judged."""
    cur.execute(
        f"""
        SELECT (SELECT COUNT(*) FROM adjudication.records r WHERE {_OPEN_TO_ME}) AS remaining,
               (SELECT COUNT(*) FROM adjudication.judgements
                 WHERE validator_id = %(vid)s AND state = 'submitted') AS judged
        """,
        _params(validator),
    )
    row = cur.fetchone()
    return {"left": int(row["remaining"]), "judged": int(row["judged"])}


def _vote_score(cur, validator: dict) -> int:
    cur.execute("SELECT vote_score FROM validators WHERE id = %s", (int(validator["coder_id"]),))
    row = cur.fetchone()
    if not row:
        raise Refused(404, "Validator not found")
    return int(row["vote_score"] or 0)


def next_record(cur, validator: dict) -> dict:
    """The validator's current record, or a newly claimed one; None when done."""
    params = _params(validator)
    _lock(cur)
    # Their claim, while its place is still free (a lapsed claim whose place
    # someone else took is let go). Claims on records no longer open go too.
    cur.execute(
        f"""
        DELETE FROM adjudication.judgements j
         USING adjudication.records r
         WHERE j.record_id = r.record_id AND j.validator_id = %(vid)s
           AND j.state = 'claimed' AND NOT ({_OPEN_TO_ME})
        """,
        params,
    )
    cur.execute(
        """
        SELECT r.* FROM adjudication.judgements j
          JOIN adjudication.records r ON r.record_id = j.record_id
         WHERE j.validator_id = %(vid)s AND j.state = 'claimed'
         ORDER BY j.claimed_at LIMIT 1
        """,
        params,
    )
    record = cur.fetchone()
    if record is None:
        cur.execute(
            f"""
            SELECT r.* FROM adjudication.records r
             WHERE {_OPEN_TO_ME}
             ORDER BY (SELECT COUNT(*) FROM adjudication.judgements s
                        WHERE s.record_id = r.record_id AND s.state = 'submitted') DESC,
                      array_position(%(kinds)s::text[], r.kind),
                      r.imported_at, r.record_id
             LIMIT 1
            """,
            {**params, "kinds": list(KIND_ORDER)},
        )
        record = cur.fetchone()
        if record is not None:
            cur.execute(
                """
                INSERT INTO adjudication.judgements
                    (record_id, validator_id, validator_handle, validator_tier)
                VALUES (%s, %s, %s, %s)
                """,
                (record["record_id"], params["vid"], validator["handle"],
                 int(validator["validator_tier"])),
            )
    else:
        # Still working on it: the claim is renewed.
        cur.execute(
            "UPDATE adjudication.judgements SET claimed_at = NOW() "
            "WHERE record_id = %s AND validator_id = %s",
            (record["record_id"], params["vid"]),
        )
    return {
        "record": public_view(record) if record is not None else None,
        "points_base": _vote_score(cur, validator),
        **progress(cur, validator),
    }


def _my_claim(cur, validator: dict, record_id: str) -> dict:
    """The record, when this validator holds a claim on it that still counts."""
    params = {**_params(validator), "rid": record_id}
    cur.execute(
        "SELECT state FROM adjudication.judgements WHERE record_id = %(rid)s AND validator_id = %(vid)s",
        params,
    )
    mine = cur.fetchone()
    if mine is None:
        raise Refused(409, "Open this record from the Disagreements screen first")
    if mine["state"] == "submitted":
        raise Refused(409, "You have already judged this record")
    if mine["state"] == "skipped":
        raise Refused(409, "You skipped this record")
    cur.execute(f"SELECT r.*, ({_OPEN_TO_ME}) AS still_mine "
                "FROM adjudication.records r WHERE r.record_id = %(rid)s", params)
    record = cur.fetchone()
    if record is None:
        raise Refused(404, "No such record")
    if not record["still_mine"]:
        # next_record() lets the claim go.
        raise Refused(409, "Two other validators judged this record meanwhile")
    return record


def submit(cur, validator: dict, record_id: str, answer: dict) -> dict:
    _lock(cur)
    record = _my_claim(cur, validator, record_id)
    clean = check_answer(record, answer)
    points = points_for(_vote_score(cur, validator), clean)
    vid = int(validator["coder_id"])
    cur.execute(
        """
        UPDATE adjudication.judgements
           SET state = 'submitted', original_choice = %(original_choice)s,
               suggested_doi_o = %(suggested_doi_o)s, outcome = %(outcome)s,
               note = %(note)s, points = %(points)s, submitted_at = NOW(),
               validator_handle = %(handle)s, validator_tier = %(tier)s
         WHERE record_id = %(rid)s AND validator_id = %(vid)s AND state = 'claimed'
        """,
        {**clean, "points": points, "handle": validator["handle"],
         "tier": int(validator["validator_tier"]), "rid": record_id, "vid": vid},
    )
    # The validator's normal total, as every judgement adds to it.
    cur.execute(
        "UPDATE validators SET total_points = total_points + %s, "
        "total_judgements = total_judgements + 1 WHERE id = %s RETURNING total_points",
        (points, vid),
    )
    total = cur.fetchone()["total_points"]
    cur.execute(
        """
        UPDATE adjudication.records SET status = 'awaiting_approval', updated_at = NOW()
         WHERE record_id = %s AND status = 'open'
           AND (SELECT COUNT(*) FROM adjudication.judgements
                 WHERE record_id = %s AND state = 'submitted') >= %s
        RETURNING status
        """,
        (record_id, record_id, JUDGES_PER_RECORD),
    )
    complete = cur.fetchone() is not None
    return {"points": points, "total_points": int(total), "record_complete": complete,
            **progress(cur, validator)}


def skip(cur, validator: dict, record_id: str) -> dict:
    """Pass on a record: it is not served to this validator again."""
    _lock(cur)
    _my_claim(cur, validator, record_id)
    cur.execute(
        "UPDATE adjudication.judgements SET state = 'skipped' "
        "WHERE record_id = %s AND validator_id = %s AND state = 'claimed'",
        (record_id, int(validator["coder_id"])),
    )
    return progress(cur, validator)
