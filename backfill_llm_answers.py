"""
backfill_llm_answers.py — Store the AI sanity check on the records that went to
an admin without one.

evaluate_consensus now asks Gemini for a sanity check in three cases where it did
not before. There the answer is only stored, in unvalidated.llm_validator, for the
admin to see; it never changes where the record goes:

    Can't tell          a validator answered "Can't tell"          ce._is_unsure
    quote flagged       the quote gate flagged a submission        ce._quote_flagged
    corrections differ  the checks agree, the corrections do not   ce._checks_agree and
                                                                   not ce._corrections_agree

Records that took one of these ways before have no answer. This asks Gemini once
for each and stores it, as consensus now would. A record is picked when
- its llm_validator IS NULL,
- both human judgements are submitted (human_1 and human_2, is_validated),
- neither is a senior reject (final in evaluate_consensus, with no LLM call), and
- the judgements fall in one of the three cases, tested with the same predicates
  in the same order as evaluate_consensus.
The dry run also counts the records with both judgements and no answer that are
none of these (a senior reject, say); they are left alone.

Only llm_validator is written: never the status, the tiebreaker flag, the final
values, the quality flags, updated_at or the validated table. Consensus is not
re-run.

WHICH RECORDS
Open ones by default: need_review and consensus_reached, which an admin still
decides with the answer in front of them. --include-closed adds validated and
rejected. Those are decided already, so their answer is history only, and each
still costs a Gemini call. Never unvalidated or validation_inprogress: those wait
for validators and have not been through consensus, which moves every record with
two submitted judgements out of them. The status list has no retired or excluded
value (a retired record leaves the table for retired_records).

HOW IT WRITES
Each record is read again just before its call and skipped if it no longer
qualifies. Gemini is called with no lock and no transaction open, so validators
and admins never wait on it. The answer is then written by one conditional UPDATE,
only while llm_validator is still NULL and the status still in scope, so an answer
stored meanwhile (a submission, a re-evaluation, the nightly retry) is never
overwritten. One commit per record. A call that fails writes nothing and is
counted; a re-run asks again, as the record is still NULL.

UNDO
--apply saves the ids it filled, with the answer written to each, to
backups/llm_backfill_<UTC time>.json (git-ignored), updated as it goes. Setting
those back to NULL undoes the run; to leave alone one answered again since, only
where it still holds the answer from the file:
    UPDATE unvalidated SET llm_validator = NULL
    WHERE record_id = '<id>' AND llm_validator = '<its answer>'::jsonb;

A dry run unless given --apply; the dry run takes no locks and writes nothing.

Usage:
    python backfill_llm_answers.py                      # dry run: counts, and the open records it would fill
    python backfill_llm_answers.py --apply --limit 10   # fill the first 10, to check them
    python backfill_llm_answers.py --apply              # fill every open record listed
    python backfill_llm_answers.py --include-closed     # dry run with validated and rejected too
                                                        # (add --apply to fill those as well)
"""
import argparse
import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import consensus_engine as ce
import llm_validator
from auto_validate_waiting import _HUMAN_COLUMNS
from backfill_outcome_agreement import write_backup
from console_encoding import use_utf8_output

load_dotenv()
use_utf8_output()

OPEN_STATUSES = ("need_review", "consensus_reached")
CLOSED_STATUSES = ("validated", "rejected")

# The ways evaluate_consensus takes two submitted judgements, in its order.
SENIOR_REJECT = "senior_reject"
CANT_TELL = "cant_tell"
QUOTE_FLAGGED = "quote_flagged"
CORRECTIONS_DIFFER = "corrections_differ"
AGREEMENT = "agreement"
CHECKS_DIFFER = "checks_differ"
# Where the answer is only stored for the admin: the records this fills.
STORE_ONLY_CASES = (CANT_TELL, QUOTE_FLAGGED, CORRECTIONS_DIFFER)
OTHER_CASES = (SENIOR_REJECT, AGREEMENT, CHECKS_DIFFER)

LABELS = {
    CANT_TELL: "Can't tell",
    QUOTE_FLAGGED: "quote flagged",
    CORRECTIONS_DIFFER: "corrections differ",
    SENIOR_REJECT: "senior reject (final, no LLM)",
    AGREEMENT: "checks and corrections agree",
    CHECKS_DIFFER: "checks differ (tiebreaker)",
}


def case_of(h1: dict, h2: dict) -> str:
    """Which way evaluate_consensus takes these two submitted judgements: its
    senior-reject guard, then the three branches that only store the LLM's
    answer, then agreement or the tiebreaker. Same predicates, same order."""
    # evaluate_consensus counts slots whose additional_checks has the key (jsonb ?).
    if any("senior_reject" in ce._additional_checks(h) for h in (h1, h2)):
        return SENIOR_REJECT
    if ce._is_unsure(h1) or ce._is_unsure(h2):
        return CANT_TELL
    if ce._quote_flagged(h1) or ce._quote_flagged(h2):
        return QUOTE_FLAGGED
    if ce._checks_agree(h1, h2):
        return AGREEMENT if ce._corrections_agree(h1, h2) else CORRECTIONS_DIFFER
    return CHECKS_DIFFER


def _judgements(cur, record_ids: list[str]) -> dict[str, list[dict]]:
    """The submitted human judgements of these records, human_1 first, read as
    evaluate_consensus reads them."""
    cur.execute(
        f"SELECT record_id::text AS record_id, {_HUMAN_COLUMNS} FROM validation_queue "
        "WHERE record_id = ANY(%s::uuid[]) AND is_validated = TRUE "
        "AND validator_slot IN ('human_1', 'human_2') ORDER BY record_id, validator_slot",
        (list(record_ids),),
    )
    by_record: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        row = dict(row)
        by_record.setdefault(row.pop("record_id"), []).append(row)
    return by_record


def survey(cur) -> list[dict]:
    """Every open or closed record with no answer and both judgements submitted,
    with the way consensus takes it: open first, then the longest untouched."""
    cur.execute(
        "SELECT record_id::text AS record_id, validation_status, updated_at, "
        "final_title_r, title_r FROM unvalidated "
        "WHERE llm_validator IS NULL AND validation_status = ANY(%s)",
        (list(OPEN_STATUSES + CLOSED_STATUSES),),
    )
    records = {r["record_id"]: dict(r) for r in cur.fetchall()}
    found = []
    for record_id, humans in _judgements(cur, list(records)).items():
        if len(humans) == 2 and record_id in records:
            found.append({**records[record_id], "case": case_of(*humans)})
    found.sort(key=lambda r: (r["validation_status"] not in OPEN_STATUSES,
                              r["updated_at"], r["record_id"]))
    return found


def _current(cur, record_id: str, statuses: tuple) -> dict | None:
    """The whole record as it is now, if it still qualifies: no answer yet, in
    scope, and its judgements still one of the three cases."""
    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
    row = cur.fetchone()
    if row is None or row["llm_validator"] is not None or row["validation_status"] not in statuses:
        return None
    humans = _judgements(cur, [record_id]).get(record_id, [])
    if len(humans) != 2 or case_of(*humans) not in STORE_ONLY_CASES:
        return None
    return dict(row)


def _save(path: Path, payload: dict) -> None:
    """Rewrite the backup in place, never leaving it half written."""
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    os.replace(partial, path)


def _title(rec: dict) -> str:
    return (rec.get("final_title_r") or rec.get("title_r") or "")[:60]


def _report(found: list[dict], todo: list[dict], selected: int, include_closed: bool) -> None:
    def count(cases, status_group):
        return sum(1 for r in found if r["case"] in cases and r["validation_status"] in status_group)

    print("  Records with both judgements submitted and no LLM answer")
    print(f"  {'':<34}{'open':>7}{'closed':>8}")
    for case in STORE_ONLY_CASES:
        print(f"    {LABELS[case]:<32}{count((case,), OPEN_STATUSES):>7}"
              f"{count((case,), CLOSED_STATUSES):>8}")
    print(f"    {'one of these cases':<32}{count(STORE_ONLY_CASES, OPEN_STATUSES):>7}"
          f"{count(STORE_ONLY_CASES, CLOSED_STATUSES):>8}")
    print(f"  {'other records, none of these:':<34}{count(OTHER_CASES, OPEN_STATUSES):>7}"
          f"{count(OTHER_CASES, CLOSED_STATUSES):>8}   (left alone)")
    for case in OTHER_CASES:
        print(f"    {LABELS[case]:<32}{count((case,), OPEN_STATUSES):>7}"
              f"{count((case,), CLOSED_STATUSES):>8}")
    scope = "open and closed" if include_closed else "open only (--include-closed adds closed)"
    print(f"\n  Scope: {scope}. open = {', '.join(OPEN_STATUSES)}; "
          f"closed = {', '.join(CLOSED_STATUSES)}.")
    print(f"  Gemini calls --apply would make: {len(todo)}"
          + (f" (--limit; {selected} in scope)" if len(todo) < selected else ""))
    for rec in todo:
        print(f"    {rec['record_id']}  {rec['validation_status']:<17} "
              f"{LABELS[rec['case']]:<18} {_title(rec)}")


def _fill(conn, todo: list[dict], statuses: tuple) -> dict:
    """Ask Gemini for each record and store the answer; see the module docstring."""
    saved = {"filled_record_ids": [], "answers": {}, "statuses_in_scope": list(statuses)}
    backup = write_backup(saved, prefix="llm_backfill")
    print(f"\n  Filled ids are saved, as they are filled, to {backup}")
    filled = errored = skipped = 0
    for n, item in enumerate(todo, 1):
        record_id = item["record_id"]
        head = f"  [{n}/{len(todo)}] {record_id}  {LABELS[item['case']]:<18}"
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            record = _current(cur, record_id, statuses)
        conn.rollback()                      # nothing open while Gemini answers
        if record is None:
            skipped += 1
            print(f"{head} changed meanwhile, left as is")
            continue
        try:
            answer = llm_validator.run_llm_validation(record, context="sanity_check")
        except Exception as exc:             # noqa: BLE001 — counted, and the run goes on
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(answer, dict) or "error" in answer:
            errored += 1
            reason = answer.get("error") if isinstance(answer, dict) else repr(answer)
            print(f"{head} Gemini failed, nothing written: {str(reason)[:120]}")
            continue
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE unvalidated SET llm_validator = %s::jsonb "
                "WHERE record_id = %s AND llm_validator IS NULL AND validation_status = ANY(%s)",
                (json.dumps(answer), record_id, list(statuses)),
            )
            written = cur.rowcount == 1
        if not written:
            conn.rollback()
            skipped += 1
            print(f"{head} changed while Gemini answered, left as is")
            continue
        # Saved before the commit, so the file never misses an id that was filled.
        saved["filled_record_ids"].append(record_id)
        saved["answers"][record_id] = answer
        _save(backup, saved)
        conn.commit()
        filled += 1
        print(f"{head} filled")
    print(f"\n  Filled {filled}; Gemini failed on {errored} (still NULL: a re-run asks again); "
          f"skipped {skipped} that changed meanwhile.")
    print(f"  Filled ids: {backup}")
    return {"filled": filled, "errored": errored, "skipped": skipped, "backup": backup}


def run(apply: bool, include_closed: bool = False, limit: int | None = None) -> dict | None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")
    if apply and not os.environ.get("GEMINI_API_KEY"):
        raise EnvironmentError("GEMINI_API_KEY must be set in environment or .env to ask Gemini")
    statuses = OPEN_STATUSES + (CLOSED_STATUSES if include_closed else ())
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            found = survey(cur)
        conn.rollback()                      # read only; a dry run holds nothing
        in_scope = [r for r in found
                    if r["case"] in STORE_ONLY_CASES and r["validation_status"] in statuses]
        todo = in_scope[:limit] if limit is not None else in_scope
        _report(found, todo, len(in_scope), include_closed)
        if not apply:
            print("\nDry run — nothing written. Re-run with --apply to fill the records listed.")
            return None
        if not todo:
            print("\nNothing to fill.")
            return {"filled": 0, "errored": 0, "skipped": 0, "backup": None}
        return _fill(conn, todo, statuses)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="ask Gemini and store the answers")
    parser.add_argument("--include-closed", action="store_true",
                        help="validated and rejected records too (history only)")
    parser.add_argument("--limit", type=int, metavar="N", help="handle at most N records")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    run(args.apply, include_closed=args.include_closed, limit=args.limit)
