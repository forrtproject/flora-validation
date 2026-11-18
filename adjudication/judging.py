"""Judging the disagreements: what a Trusted or Senior validator sees and answers.

Each record needs two submitted judgements from two different validators, as
each record in the main app does. A validator works on one record at a time:
opening the next one claims it, which holds one of its two places for
CLAIM_MINUTES; after that another validator may take the place, though the
claim stays good for as long as the place is still free. Claims, submissions
and skips are serialised by one advisory lock, so two validators can never take
the same place.

A judgement says which original is right, whether the paper is a replication
or a reproduction (asked only when FLoRA's type is unknown or the Observatory
disputes it), and what it found: a replication outcome, or a reproduction's two
axes as FLoRA's codebook codes them. What FLoRA and the Observatory already
agree on is offered pre-selected (defaults()), and the submission records which
of those answers the judge kept.

A submission earns points as a normal judgement does (the validator's
vote_score, plus 2 for a decided original, 2 for a decided outcome, or 1 for
each decided axis of a reproduction, and 1 for a note), added to their normal
total in the same transaction. The second submission moves the record to
'awaiting_approval'. Validators never see each other's answers.
"""

from __future__ import annotations

import re

from extractor_vocab import (
    CURRENT_REPRODUCTION_OUTCOMES,
    DESCRIPTIVE_OUTCOME,
    FLAWED_OUTCOME,
    REPLICATION_OUTCOMES,
    derive_reproduction_outcome,
    split_joined_outcome,
)

JUDGES_PER_RECORD = 2
CLAIM_MINUTES = 60
MIN_TIER = 1                     # 1 Trusted, 2 Senior
NOTE_LIMIT = 2000
# Serialises claims and submissions; distinct from the setup and import locks.
JUDGING_LOCK_ID = 7_342_025_096
LOCK_TIMEOUT = "5s"

CANNOT_TELL = "cannot_tell"
NOT_A_REPLICATION = "not_a_replication"
ORIGINAL_CHOICES = ("flora", "observatory", "both", "neither", CANNOT_TELL)
# What kind of paper it is. 'not_validation' is outside FLoRA's scope, the same
# answer as the outcome "not_a_replication", which is stored with it.
TYPES = ("replication", "reproduction", "not_validation")
# FLoRA's replication outcomes (extractor_vocab.REPLICATION_OUTCOMES), in the
# order the judging screen offers them.
REPLICATION_CHOICES = (
    "successful", "failed", "mixed", FLAWED_OUTCOME, "uninformative",
    DESCRIPTIVE_OUTCOME, "cannot_be_determined", NOT_A_REPLICATION,
)
# A reproduction's two independent axes (extractor_vocab.OUTCOME_COMPUTATION_VALUES
# and OUTCOME_ROBUSTNESS_VALUES), in the order the screens offer them.
COMPUTATION_CHOICES = (
    "computationally reproducible", "computational issues", "technical failure",
    "not checked", "cannot_be_determined",
)
ROBUSTNESS_CHOICES = ("robust", "robustness challenges", "not checked", "cannot_be_determined")
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


def _word(value) -> str:
    return str(value or "").strip().lower()


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


def outcome_type(outcome) -> str | None:
    """The type a flat FLoRA outcome implies, or None when it implies none:
    "pending", a blank, or cannot_be_determined, which a replication and a
    reproduction (with an axis undetermined) share."""
    value = _word(outcome)
    if value in CURRENT_REPRODUCTION_OUTCOMES or split_joined_outcome(value) != (None, None):
        return "reproduction"
    if value == NOT_A_REPLICATION:
        return "not_validation"
    if value in REPLICATION_OUTCOMES and value != "cannot_be_determined":
        return "replication"
    return None


def flora_type(record: dict) -> str | None:
    """FLoRA's type for the record. PR #143's file has no type column, so its
    outcome is the only evidence; None where that says nothing."""
    return outcome_type(record.get("flora_outcome"))


def observatory_type(record: dict) -> str:
    """The Observatory's database is of replications; its replication_type
    ('direct', 'close extension', 'conceptual', …) never names a reproduction in
    PR #143's file, but would be believed if it did."""
    return "reproduction" if "reproduc" in _word(record.get("mo_replication_type")) else "replication"


def type_disputed(record: dict) -> bool:
    """FLoRA holds the paper as one type and the Observatory as another."""
    flora = flora_type(record)
    return flora is not None and flora != observatory_type(record)


def ask_type(record: dict) -> bool:
    """Whether the judge is asked the type: when FLoRA's is unknown or disputed."""
    return flora_type(record) is None or type_disputed(record)


def outcome_choices(record: dict) -> tuple[str, ...]:
    """FLoRA's replication outcomes and "can't tell". Where the judge is asked
    the type, "not a replication" is the type question's "neither" instead."""
    choices = REPLICATION_CHOICES
    if ask_type(record):
        choices = tuple(c for c in choices if c != NOT_A_REPLICATION)
    return (*choices, CANNOT_TELL)


def _observatory_outcome(record: dict) -> str | None:
    """The Observatory's outcome in FLoRA's words, where there is one. A reversal
    (a significant effect the other way) is a failed replication; "inconclusive"
    has no single counterpart, so approving one of FLoRA's words for it counts as
    the admin's own answer."""
    return {"success": "successful", "failure": "failed", "reversal": "failed"}.get(
        _word(record.get("mo_outcome")))


def defaults(record: dict) -> dict:
    """The answers FLoRA and the Observatory already agree on, which the judging
    screen pre-selects (the judge may still change them): the original when both
    name the same one, the type when it is not asked, and the outcome when
    FLoRA's replication outcome is the Observatory's in FLoRA's words. Nothing
    when FLoRA's outcome is pending (it found no original to code); only the
    original when the two disagree about the type."""
    out = {"original": None, "type": None, "outcome": None}
    flora_outcome = _word(record.get("flora_outcome"))
    if flora_outcome in ("", "pending"):
        return out
    if original_choices(record)[0] == "both":
        out["original"] = "both"
    if type_disputed(record):
        return out
    kind = flora_type(record)          # not disputed, so the Observatory's too
    out["type"] = kind
    if kind == "replication" and _observatory_outcome(record) == flora_outcome:
        out["outcome"] = flora_outcome
    return out


def check_outcome(answer: dict, *, decision: bool = False) -> tuple[str, str, str | None, str | None]:
    """(type, outcome, computation, robustness), cleaned, or Refused(422).

    A reproduction is answered on its two axes, any pair of FLoRA's values, and
    its outcome is the flat value derived from them; "not a replication" is the
    type 'not_validation'. An answer without a type (from a page loaded before
    the type was asked) is typed by its outcome. A judge may also say they
    cannot tell; an admin's *decision* may not."""
    kind = _word(answer.get("type"))
    outcome = _word(answer.get("outcome"))
    computation = _word(answer.get("outcome_computation"))
    robustness = _word(answer.get("outcome_robustness"))
    if not kind:
        kind = "reproduction" if outcome_type(outcome) == "reproduction" else "replication"
        if kind == "reproduction":
            computation, robustness = split_joined_outcome(outcome)
    if kind not in TYPES:
        raise Refused(422, "Choose whether it is a replication or a reproduction")
    unsure = () if decision else (CANNOT_TELL,)
    vocabulary = " from FLoRA's vocabulary" if decision else ""
    if kind == "replication":
        if outcome not in (*REPLICATION_CHOICES, *unsure):
            raise Refused(422, f"Choose the outcome{vocabulary}")
        if outcome != NOT_A_REPLICATION:
            return kind, outcome, None, None
        kind = "not_validation"
    if kind == "not_validation":
        return kind, NOT_A_REPLICATION, None, None
    if computation not in (*COMPUTATION_CHOICES, *unsure):
        raise Refused(422, f"Choose the computation outcome{vocabulary}")
    if robustness not in (*ROBUSTNESS_CHOICES, *unsure):
        raise Refused(422, f"Choose the robustness outcome{vocabulary}")
    return kind, derive_reproduction_outcome(computation, robustness), computation, robustness


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
    kind, outcome, computation, robustness = check_outcome(answer)
    note = str(answer.get("note") or "").strip()
    if len(note) > NOTE_LIMIT:
        raise Refused(422, f"The note is longer than {NOTE_LIMIT} characters")
    return {"original_choice": choice, "suggested_doi_o": suggested or None, "type": kind,
            "outcome": outcome, "outcome_computation": computation,
            "outcome_robustness": robustness, "note": note or None}


def prefilled(record: dict, clean: dict) -> dict:
    """Which of the pre-selected answers (defaults()) the judge submitted."""
    offered = defaults(record)
    return {
        "original_prefilled": offered["original"] is not None
                              and clean["original_choice"] == offered["original"],
        "outcome_prefilled": offered["outcome"] is not None and clean["type"] == "replication"
                             and clean["outcome"] == offered["outcome"],
    }


def points_for(vote_score: int, answer: dict) -> int:
    """As a normal judgement: the validator's base, +2 for a decided original,
    +2 for a decided outcome (+1 for each decided axis of a reproduction), +1
    for a note. A pre-selected answer counts as any other."""
    points = int(vote_score or 0)
    if answer["original_choice"] != CANNOT_TELL:
        points += 2
    if answer.get("type") == "reproduction":
        points += sum(axis != CANNOT_TELL
                      for axis in (answer["outcome_computation"], answer["outcome_robustness"]))
    elif answer["outcome"] != CANNOT_TELL:
        points += 2
    if answer["note"]:
        points += 1
    return points


def public_view(record: dict) -> dict:
    """What a validator sees: both answers, labelled by source. Never another
    validator's judgement, never the raw CSV row."""
    computation, robustness = split_joined_outcome(record.get("flora_outcome"))
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
            # A reproduction outcome's two axes; None for any other outcome.
            "outcome_computation": computation,
            "outcome_robustness": robustness,
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
        "flora_type": flora_type(record),
        "observatory_type": observatory_type(record),
        "type_disputed": type_disputed(record),
        "ask_type": ask_type(record),
        "original_choices": list(original_choices(record)),
        "outcome_choices": list(outcome_choices(record)),
        "computation_choices": [*COMPUTATION_CHOICES, CANNOT_TELL],
        "robustness_choices": [*ROBUSTNESS_CHOICES, CANNOT_TELL],
        "doi_required": doi_required(record),
        "defaults": defaults(record),
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
               suggested_doi_o = %(suggested_doi_o)s, type = %(type)s, outcome = %(outcome)s,
               outcome_computation = %(outcome_computation)s,
               outcome_robustness = %(outcome_robustness)s,
               original_prefilled = %(original_prefilled)s,
               outcome_prefilled = %(outcome_prefilled)s,
               note = %(note)s, points = %(points)s, submitted_at = NOW(),
               validator_handle = %(handle)s, validator_tier = %(tier)s
         WHERE record_id = %(rid)s AND validator_id = %(vid)s AND state = 'claimed'
        """,
        {**clean, **prefilled(record, clean), "points": points, "handle": validator["handle"],
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
