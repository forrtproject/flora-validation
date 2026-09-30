"""
auto_validate_waiting.py — Apply the auto-validation rules once to the entries that
were already waiting for admin approval when the rules were introduced.

New agreements are auto-validated by consensus_engine as they happen. Entries that
reached consensus before that sit in "Pending approval" until an admin approves
them. This settles those by the same test (consensus_engine.auto_validation_rule):
two agreeing validators, the AI sanity check agreeing, and a Trusted or Senior
validator among them, or two experienced ones.

It asks the AI nothing new: the sanity check each entry already had decides. And it
publishes exactly what an admin approval would (consensus_engine.approval_values):
the values consensus stored on the entry, else the extracted ones they stand in
for — not a fresh resolution against extracted values an import may have changed.

Left for an admin, and listed:
- a tiebreaker: the validators disagreed and the AI picked a side;
- an entry an admin has touched: a name or a note on it;
- anything the rule refuses (see auto_validation_rule), or no rule applies;
- an entry approval would refuse (e.g. a reproduction without both axes);
- a duplicate of an entry already validated: an admin merges those.

A dry run unless given --apply. Before writing, the entries are saved whole to
backups/auto_validation_<UTC time>.json (git-ignored). Each is marked with the
rule, like one consensus auto-validated, so it shows under "Auto-validated" and
can be sent back for review from the admin panel.

Usage:
    python auto_validate_waiting.py            # dry run: list what would pass
    python auto_validate_waiting.py --apply    # validate those
"""
import argparse
import json
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from backfill_outcome_agreement import write_backup
from console_encoding import use_utf8_output
import consensus_engine as ce

load_dotenv()
use_utf8_output()

# The judgement columns evaluate_consensus compares, in its order.
_HUMAN_COLUMNS = (
    "validator_slot, type_check, original_check, outcome_check, corrected_doi_o, "
    "corrected_title_o, corrected_outcome, corrected_type, corrected_title_r, corrected_url_r, "
    "corrected_abstract, corrected_outcome_quote, corrected_outcome_computation, "
    "corrected_computational_quote, corrected_computational_source, "
    "corrected_outcome_robustness, corrected_robustness_quote, corrected_robustness_source, "
    "doi_r_published, additional_checks"
)


def _json(value) -> dict:
    """A JSONB column as a dict, whether the driver decoded it or not."""
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def assess(cur, rec: dict, lock: bool) -> tuple[str | None, str, dict | None]:
    """(rule, why, values to publish) for one waiting entry; rule None means leave
    it for an admin."""
    if rec["is_tiebreaker"]:
        return None, "tiebreaker (the validators disagreed)", None
    if rec["admin_name"] or (rec["admin_notes"] or "").strip():
        return None, "an admin has touched it", None
    cur.execute(
        f"SELECT {_HUMAN_COLUMNS} FROM validation_queue "
        "WHERE record_id = %s AND is_validated = TRUE "
        "AND validator_slot IN ('human_1', 'human_2') ORDER BY validator_slot",
        (rec["record_id"],),
    )
    humans = [dict(r) for r in cur.fetchall()]
    if len(humans) != 2:
        return None, "not two human judgements", None
    llm = _json(rec["llm_validator"])
    if not llm or llm.get("error"):
        return None, "no AI sanity check on record", None
    trusted, experienced = ce.auto_validation_stats(cur, rec["record_id"])
    rule = ce.auto_validation_rule(humans[0], humans[1], llm, trusted, experienced)
    if not rule:
        if not ce._llm_matches(llm, humans[0]):
            return None, "the AI sanity check disagreed", None
        if trusted == 0 and experienced < 2:
            return None, "no Trusted/Senior validator, and not two experienced ones", None
        return None, "not a plain agreement (e.g. the original was disputed)", None
    try:
        final = ce.approval_values(rec)
    except ValueError as exc:
        return None, f"approval would refuse it: {exc}", None
    if ce.identity_taken(cur, rec, final, lock=lock):
        return None, "a duplicate of an entry already validated", None
    return rule, "passes", final


def run(apply: bool) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Locked until the end, so an admin cannot decide one in between. A dry
            # run takes no locks: it must not hold up admins approving meanwhile.
            cur.execute(
                "SELECT * FROM unvalidated WHERE validation_status = 'consensus_reached' "
                "ORDER BY updated_at" + (" FOR UPDATE" if apply else "")
            )
            waiting = [dict(r) for r in cur.fetchall()]
            passing, left = [], {}
            for rec in waiting:
                rule, why, final = assess(cur, rec, lock=apply)
                if rule:
                    passing.append((rec, rule, final))
                else:
                    left.setdefault(why, []).append(rec)

            print(f"  waiting for admin approval:            {len(waiting)}")
            print(f"  would be auto-validated:               {len(passing)}")
            for rec, rule, _ in passing:
                names = " + ".join(filter(None, (_json(rec.get(col)).get("validator_name")
                                                 for col in ("validator_1", "validator_2"))))
                print(f"    {rec['record_id']}  {rule:<11} {names:<28} "
                      f"{(rec.get('final_title_r') or rec.get('title_r') or '')[:60]}")
            print("  left for an admin:")
            for why, recs in sorted(left.items(), key=lambda kv: -len(kv[1])):
                print(f"    {len(recs):>4}  {why}")

            if not apply:
                conn.rollback()
                print("\nDry run — nothing written. Re-run with --apply to validate the entries listed.")
                return
            if not passing:
                conn.rollback()
                print("\nNothing to validate.")
                return

            backup = write_backup({"records_before_auto_validation": [rec for rec, _, _ in passing]},
                                  prefix="auto_validation")
            print(f"  saved as they are now, before writing:  {backup}")
            validated = duplicates = 0
            for rec, rule, final in passing:
                # Assessed in order, inside this transaction: an entry validated just
                # above counts for the duplicate check of the next.
                if ce.identity_taken(cur, rec, final):
                    duplicates += 1
                    continue
                cur.execute(
                    """
                    UPDATE unvalidated SET validation_status = 'validated',
                           auto_validated_rule = %s, auto_validated_at = NOW(),
                           updated_at = NOW()
                    WHERE record_id = %s AND validation_status = 'consensus_reached'
                    """,
                    (rule, rec["record_id"]),
                )
                if cur.rowcount:
                    ce._insert_validated(cur, rec, final)
                    validated += 1
            conn.commit()
            print(f"\nAuto-validated {validated} entr{'y' if validated == 1 else 'ies'}."
                  + (f" Left {duplicates} duplicate(s) of another listed entry for an admin"
                     " to merge." if duplicates else ""))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="validate the entries listed")
    run(parser.parse_args().apply)
