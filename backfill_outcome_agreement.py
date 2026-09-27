"""
backfill_outcome_agreement.py — Re-read stored "Mischaracterised → <the same
category>" judgements as what they meant: the outcome is right, the quote is not.

Before "Right outcome, better quote" existed, a validator who agreed with a
replication's outcome but not with its quote had to choose "Mischaracterised" and
re-pick the extracted category. Those judgements say outcome_check='incorrect'
with a corrected_outcome equal to the record's own outcome, which:

- splits consensus against a validator who clicked "Looks right" (both mean the
  same outcome), sending the record to an LLM tiebreaker or to review;
- shows on the admin card as "✗ <validator> suggests: failed" on a failed record;
- counts as an outcome correction in the dashboard.

The API now stores such a submission as agreement (app._same_outcome_as_agreement).
This applies the same reading to judgements saved before it, in both places a
judgement lives:

    validation_queue            outcome_check, corrected_outcome, additional_checks
    unvalidated.validator_1/_2  the JSON copy the admin card reads (and the only
                                copy of an assignment judgement)

ONLY WHERE THE JUDGEMENT SAW TODAY'S OUTCOME
--------------------------------------------
Every import refreshes the extracted outcome of existing records. A genuine
"failed → successful" made before the extractor, too, switched to "successful"
matches the pattern today but was a correction, and converting it would erase it.
Nothing but the judgement itself records what the page showed, so each match is
classified by its additional_checks.shown_outcome:

    verified      shown_outcome is recorded and is today's outcome. Converted.
    changed       shown_outcome is recorded and is not. Never converted.
    unverifiable  nothing recorded: every judgement from before shown_outcome
                  existed. The outcome may have changed since, and nothing says
                  either way. Listed for a person to check (an assignment's
                  too, though only its stored copy exists); converted only with
                  --include-unverified, and then never re-evaluated, here or by
                  the app's nightly tiebreaker retry (app._retry_tiebreakers).

Each converted judgement keeps its quote edit and gains outcome_quote_disputed and
outcome_agreement_backfilled (plus outcome_agreement_unverified when it was one of
the unverifiable) in additional_checks, so the change stays visible. Each write is
conditional on the judgement being as it was read. Points are not recalculated.

RECORDS THE MISMATCH ALONE SENT TO REVIEW
-----------------------------------------
Records in need_review whose two validators agree once converted — in this run or
an earlier one — are listed, provided the record is still a replication and BOTH
judgements recorded today's outcome as the one they were shown (a "Looks right"
made against an outcome an import later changed agrees with nothing), and leaving
out any an admin has already checked,
overridden, or sent back for review. --reevaluate re-runs consensus for exactly
those. That makes the LLM sanity-check call a fresh agreement makes (one per
record, needs GEMINI_API_KEY) and can move a record to consensus_reached, or to
validated when a senior validator took part.

Usage:
    python backfill_outcome_agreement.py                        # dry run: count and list
    python backfill_outcome_agreement.py --apply                # convert the verified ones
    python backfill_outcome_agreement.py --apply --reevaluate   # ...and re-run consensus
    python backfill_outcome_agreement.py --apply --include-unverified
                                          # also convert the listed unverifiable ones
"""
import argparse
import json
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from extractor_vocab import normalize_outcome

load_dotenv()
use_utf8_output()

MARKERS = {"outcome_quote_disputed": True, "outcome_agreement_backfilled": True}
UNVERIFIED_MARKER = {"outcome_agreement_unverified": True}
# The queue slot whose judgement each stored copy holds.
_SLOT = {"validator_1": "human_1", "validator_2": "human_2"}


def _canonical(value) -> str | None:
    """An outcome's stored spelling; None for anything that is not text."""
    if not isinstance(value, str):
        return None
    return normalize_outcome(value.strip().lower()) or None


def _checks(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def is_same_outcome_correction(judgement: dict, record_type, record_outcome) -> bool:
    """The pattern app._same_outcome_as_agreement recognises at submission."""
    if record_type != "replication":
        return False
    if judgement.get("type_check") != "correct" or judgement.get("outcome_check") != "incorrect":
        return False
    requested = _canonical(judgement.get("corrected_outcome"))
    return bool(requested) and requested == _canonical(record_outcome)


def saw_outcome(judgement: dict, outcome) -> str:
    """Did this judgement see `outcome`? "verified", "changed" or "unverifiable"
    (see the module docstring)."""
    shown = _canonical(_checks(judgement.get("additional_checks")).get("shown_outcome"))
    if shown is None:
        return "unverifiable"
    return "verified" if shown == _canonical(outcome) else "changed"


def converted(judgement: dict, unverified: bool = False) -> dict:
    """The judgement as the API would store it today."""
    markers = {**MARKERS, **(UNVERIFIED_MARKER if unverified else {})}
    return {
        **judgement,
        "outcome_check": "correct",
        "corrected_outcome": None,
        "additional_checks": {**_checks(judgement.get("additional_checks")), **markers},
    }


def _queue_rows(cur) -> list:
    cur.execute(
        """
        SELECT vq.queue_id::text AS queue_id, vq.record_id::text AS record_id,
               vq.validator_slot, vq.validator_name, vq.type_check, vq.outcome_check,
               vq.corrected_outcome, vq.additional_checks, vq.validated_at,
               u.type AS record_type, u.outcome AS record_outcome
        FROM validation_queue vq
        JOIN unvalidated u ON u.record_id = vq.record_id
        WHERE vq.is_validated
          AND vq.validator_slot IN ('human_1', 'human_2')
          AND vq.type_check = 'correct' AND vq.outcome_check = 'incorrect'
          AND vq.corrected_outcome IS NOT NULL
          AND u.type = 'replication'
        ORDER BY vq.record_id, vq.validator_slot
        """
    )
    rows = []
    for r in cur.fetchall():
        row = dict(r)
        if is_same_outcome_correction(row, row["record_type"], row["record_outcome"]):
            row["verdict"] = saw_outcome(row, row["record_outcome"])
            rows.append(row)
    return rows


def _json_copies(cur) -> list:
    """(record_id, column, judgement as stored, verdict) for every matching copy."""
    cur.execute(
        """
        SELECT record_id::text AS record_id, type, outcome, validator_1, validator_2
        FROM unvalidated
        WHERE type = 'replication'
          AND (validator_1->>'outcome_check' = 'incorrect'
               OR validator_2->>'outcome_check' = 'incorrect')
        ORDER BY record_id
        """
    )
    copies = []
    for row in cur.fetchall():
        for column in ("validator_1", "validator_2"):
            judgement = _checks(row[column])
            if judgement and is_same_outcome_correction(judgement, row["type"], row["outcome"]):
                copies.append((row["record_id"], column, judgement,
                               saw_outcome(judgement, row["outcome"])))
    return copies


def previously_converted(cur) -> set:
    """Records an earlier --apply converted. The review list must include them, or
    a plain --apply followed by --apply --reevaluate would find nothing to settle:
    the second run has nothing left to convert."""
    cur.execute(
        """
        SELECT DISTINCT record_id::text AS record_id FROM validation_queue
        WHERE additional_checks ? 'outcome_agreement_backfilled'
        """
    )
    return {r["record_id"] for r in cur.fetchall()}


def review_candidates(cur, record_ids) -> list:
    """Replications in need_review whose two human judgements now agree outright
    and both recorded today's outcome as the one they were shown: the ones this
    mismatch alone kept from consensus. Lock the checked record until consensus
    runs, so an import or admin cannot change that baseline in between."""
    from consensus_engine import _checks_agree, _corrections_agree, _is_unsure, _quote_flagged
    candidates = []
    for record_id in sorted(record_ids):
        cur.execute(
            "SELECT validation_status, type, outcome, admin_checked, admin_override, admin_name, "
            "admin_notes FROM unvalidated WHERE record_id = %s FOR UPDATE",
            (record_id,),
        )
        status = cur.fetchone()
        if (not status or status["validation_status"] != "need_review"
                or status["type"] != "replication"):
            continue
        # An admin has already looked — checked it, overrode it, or sent it back
        # for review with their name or a note on it (admin_flag_review sets only
        # those): their review is not this script's to redo.
        if (status["admin_checked"] or status["admin_override"]
                or status["admin_name"] or (status["admin_notes"] or "").strip()):
            continue
        cur.execute(
            """
            SELECT validator_slot, type_check, original_check, outcome_check,
                   corrected_doi_o, corrected_title_o, corrected_outcome, corrected_type,
                   corrected_title_r, corrected_url_r, corrected_abstract,
                   corrected_outcome_computation, corrected_outcome_robustness,
                   doi_r_published, additional_checks
            FROM validation_queue
            WHERE record_id = %s AND is_validated
              AND validator_slot IN ('human_1', 'human_2')
            ORDER BY validator_slot
            """,
            (record_id,),
        )
        humans = [dict(r) for r in cur.fetchall()]
        if len(humans) != 2:
            continue
        h1, h2 = humans
        if any(_checks(h.get("additional_checks")).get("senior_reject") for h in humans):
            continue
        if _is_unsure(h1) or _is_unsure(h2) or _quote_flagged(h1) or _quote_flagged(h2):
            continue
        if any(saw_outcome(h, status["outcome"]) != "verified" for h in humans):
            continue
        if _checks_agree(h1, h2) and _corrections_agree(h1, h2):
            candidates.append(record_id)
    return candidates


def run(apply: bool, reevaluate: bool, include_unverified: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            queue_rows = _queue_rows(cur)
            copies = _json_copies(cur)

            def convertible(verdict):
                return verdict == "verified" or (include_unverified and verdict == "unverifiable")

            to_convert = [r for r in queue_rows if convertible(r["verdict"])]
            copies_to_convert = [c for c in copies if convertible(c[3])]
            by_verdict = {v: [r for r in queue_rows if r["verdict"] == v]
                          for v in ("verified", "changed", "unverifiable")}

            print(f"  validation_queue judgements matching:   {len(queue_rows)}")
            print(f"    verified (recorded today's outcome):  {len(by_verdict['verified'])}")
            print(f"    shown another outcome (left as is):   {len(by_verdict['changed'])}")
            print(f"    unverifiable (nothing recorded):      {len(by_verdict['unverifiable'])}"
                  + ("   → converting" if include_unverified else
                     "   → left as is; check them, then --include-unverified"))
            for row in by_verdict["unverifiable"][:25]:
                when = f"{row['validated_at']:%Y-%m-%d}" if row["validated_at"] else "?"
                print(f"      {row['record_id']}  {row['validator_slot']:<8} "
                      f"{row['validator_name'] or '?':<20} judged {when}  "
                      f"'{row['corrected_outcome']}' on a record now '{row['record_outcome']}'")
            if len(by_verdict["unverifiable"]) > 25:
                print(f"      … and {len(by_verdict['unverifiable']) - 25} more")
            # A stored copy mostly mirrors a queue row listed above. An assignment
            # judgement has no queue row — its copy is the only one — so it is
            # listed here, or --include-unverified would convert it unseen.
            listed = {(r["record_id"], r["validator_slot"]) for r in by_verdict["unverifiable"]}
            copy_only = [c for c in copies if c[3] == "unverifiable"
                         and (c[2].get("is_assignment") or (c[0], _SLOT[c[1]]) not in listed)]
            if copy_only:
                print(f"    unverifiable, stored copy only:       {len(copy_only)}"
                      + ("   → converting" if include_unverified else
                         "   → left as is; check them, then --include-unverified"))
                for record_id, column, judgement, _ in copy_only[:25]:
                    kind = "assignment" if judgement.get("is_assignment") else "no queue row"
                    print(f"      {record_id}  {column:<11} "
                          f"{judgement.get('validator_name') or '?':<20} "
                          f"judged {str(judgement.get('validated_at') or '?')[:10]}  "
                          f"'{judgement.get('corrected_outcome')}' ({kind})")
                if len(copy_only) > 25:
                    print(f"      … and {len(copy_only) - 25} more")

            converted_rows = 0
            record_ids = set()
            for row in to_convert:
                checks = converted(row, row["verdict"] != "verified")["additional_checks"]
                # Only while it is still the judgement that was read.
                cur.execute(
                    """
                    UPDATE validation_queue
                    SET outcome_check = 'correct', corrected_outcome = NULL,
                        additional_checks = %s::jsonb
                    WHERE queue_id = %s AND outcome_check = 'incorrect'
                      AND corrected_outcome IS NOT DISTINCT FROM %s
                      AND additional_checks IS NOT DISTINCT FROM %s::jsonb
                    """,
                    (json.dumps(checks), row["queue_id"], row["corrected_outcome"],
                     json.dumps(row["additional_checks"]) if row["additional_checks"] is not None
                     else None),
                )
                if cur.rowcount:
                    converted_rows += 1
                    record_ids.add(row["record_id"])
            converted_copies = 0
            for record_id, column, judgement, verdict in copies_to_convert:
                # column comes from the fixed pair above, never from input; the
                # copy is replaced only while it is still the one that was read.
                cur.execute(
                    f"UPDATE unvalidated SET {column} = %s::jsonb "
                    f"WHERE record_id = %s AND {column} = %s::jsonb",
                    (json.dumps(converted(judgement, verdict != "verified")), record_id,
                     json.dumps(judgement)),
                )
                if cur.rowcount:
                    converted_copies += 1
                    record_ids.add(record_id)
            skipped = (len(to_convert) - converted_rows) + (len(copies_to_convert) - converted_copies)
            print(f"  validation_queue judgements to convert: {converted_rows}")
            print(f"  stored JSON copies to convert:          {converted_copies}")
            print(f"  records affected:                       {len(record_ids)}")
            if skipped:
                print(f"  changed while this ran (left as is):    {skipped}")

            # Read after the updates, inside the same transaction, so a dry run
            # reports what --apply would leave behind.
            candidates = review_candidates(cur, record_ids | previously_converted(cur))
            print(f"  need_review records that now agree:     {len(candidates)}")
            for record_id in candidates:
                print(f"    {record_id}")

            if not apply:
                conn.rollback()
                print("\nDry run — nothing written. Re-run with --apply to convert.")
                return

            if reevaluate and candidates:
                from consensus_engine import evaluate_consensus
                for record_id in candidates:
                    evaluate_consensus(cur, record_id)
                    cur.execute(
                        "SELECT validation_status FROM unvalidated WHERE record_id = %s",
                        (record_id,),
                    )
                    print(f"  re-evaluated {record_id} → {cur.fetchone()['validation_status']}")
            conn.commit()
            print(f"\nConverted {converted_rows} judgement(s) and {converted_copies} stored copy(ies)."
                  + ("" if reevaluate or not candidates
                     else " Re-run with --reevaluate to settle the records listed above."))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the conversion")
    parser.add_argument("--reevaluate", action="store_true",
                        help="with --apply: re-run consensus for need_review records that now agree")
    parser.add_argument("--include-unverified", action="store_true",
                        help="with --apply: also convert the listed judgements that recorded no "
                             "shown outcome (never re-evaluated)")
    args = parser.parse_args()
    if (args.reevaluate or args.include_unverified) and not args.apply:
        parser.error("--reevaluate and --include-unverified need --apply")
    run(args.apply, args.reevaluate, args.include_unverified)
