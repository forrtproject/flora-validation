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
Only the judgement itself records what the page showed, in
additional_checks.shown_outcome, and only since that field existed; for older
ones the extractor history can stand in where it is unambiguous. Each match is:

    verified      shown_outcome is recorded and is today's outcome. Converted.
    history       nothing recorded, but with --extractor-history every row that
                  could have been the record — under its pair_id, duplicates
                  included, or in its extractor slot under an earlier pair_id — in
                  every version of the extracted file the app could have imported
                  (flora-extractor, any branch; this repository's snapshots),
                  committed up to the judgement's submission, gives today's outcome
                  as a replication: whichever was imported, the page showed it.
                  Converted, marked outcome_agreement_history_checked.
    changed       shown_outcome is recorded and is not (or every version gives
                  another outcome). Never converted.
    unverifiable  nothing recorded and no unambiguous history. The outcome may
                  have changed since, and nothing says either way. Listed for a
                  person to check (an assignment's too, though only its stored
                  copy exists); converted only with --include-unverified, and
                  then never re-evaluated, here or by the app's nightly
                  tiebreaker retry (app._retry_tiebreakers).

Each converted judgement keeps its quote edit and gains outcome_quote_disputed and
outcome_agreement_backfilled (plus outcome_agreement_unverified or
outcome_agreement_history_checked, by how it was verified) in additional_checks, so
the change stays visible, and
outcome_agreement_original: the outcome_check and corrected_outcome it replaced.
Before writing, --apply saves every judgement it is about to change, as stored, to
backups/outcome_agreement_<UTC time>.json (git-ignored), and --reevaluate saves the
records it is about to settle, whole, to another such file. Each write is
conditional on the judgement being as it was read. Points are not recalculated.

RECORDS THE MISMATCH ALONE SENT TO REVIEW
-----------------------------------------
Records in need_review whose two validators agree once converted — in this run or
an earlier one — are listed, provided the record is still a replication and BOTH
judgements saw today's outcome, as recorded or by the extractor history (a "Looks
right" made against an outcome an import later changed agrees with nothing), none
was converted unverified, and leaving out any an admin has already checked,
overridden, or sent back for review. --reevaluate re-runs consensus for exactly
those. That makes the LLM sanity-check call a fresh agreement makes (one per
record, needs GEMINI_API_KEY) and can move a record to consensus_reached, or to
validated when an auto-validation rule applies (consensus_engine.auto_validation_rule).

Usage:
    python backfill_outcome_agreement.py                        # dry run: count and list
    python backfill_outcome_agreement.py --apply                # convert the verified ones
    python backfill_outcome_agreement.py --apply --reevaluate   # ...and re-run consensus
    python backfill_outcome_agreement.py --apply --include-unverified
                                          # also convert the listed unverifiable ones
    python backfill_outcome_agreement.py --extractor-history [CLONE]
                                          # verify the unrecorded ones by the history
                                          # first (combine with --apply)
"""
import argparse
import fnmatch
import io
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from extractor_vocab import normalize_outcome

load_dotenv()
use_utf8_output()

MARKERS = {"outcome_quote_disputed": True, "outcome_agreement_backfilled": True}
UNVERIFIED_MARKER = {"outcome_agreement_unverified": True}
HISTORY_MARKER = {"outcome_agreement_history_checked": True}
HERE = Path(__file__).resolve().parent
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


def _git(repo: Path, *args) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          check=True).stdout


class ExtractorHistory:
    """What a pair screen could have shown, read from every version of the extracted
    file the app could have imported: flora-extractor's data/extracted.csv on any
    branch, and every data/extracted*.csv ever committed to this repository. The
    app's source moved between them, and a deployment setting can override it, so
    no one version is assumed to be the one imported.

    Every row that could have been the record counts: each row under its pair_id
    (a version can list a pair twice, and the importer kept the first), and each
    row in its extractor slot (work_id, original_rank), under any pair_id — an
    import re-keys a record whose DOI was corrected, and the versions under its
    old pair_id are what earlier pages showed."""

    SOURCES = (("extractor", "data/extracted.csv"), ("own", "data/extracted*.csv"))

    def __init__(self, extractor_repo, own_repo: Path = HERE):
        self.versions = []      # (committed_at, {pair_id: {values}}, {slot: {values}})
        parsed = {}
        repos = {"extractor": Path(extractor_repo), "own": Path(own_repo)}
        for source, pattern in self.SOURCES:
            repo = repos[source]
            for committed_at, label, oid in self._file_versions(repo, pattern):
                if oid not in parsed:
                    parsed[oid] = self._rows(_git(repo, "cat-file", "blob", oid), label)
                self.versions.append((committed_at, *parsed[oid]))
        if not self.versions:
            raise ValueError("no version of the extracted file found")

    @staticmethod
    def _file_versions(repo: Path, pattern: str):
        """(committed_at, label, blob id) for every commit on any branch that holds a
        version of a file matching `pattern`. --full-history keeps what default
        history simplification drops — commits reachable only through a merge that
        kept the other side — and merges are read from their tree, since a merge
        can create a version and lists no changed paths."""
        log = _git(repo, "log", "--all", "--full-history", "--format=%H %cI", "--", pattern)
        for line in log.decode().splitlines():
            commit, committed = line.split()
            for entry in _git(repo, "ls-tree", "-r", commit, "--", "data").decode().splitlines():
                meta, path = entry.split("\t", 1)
                if fnmatch.fnmatchcase(path, pattern):
                    yield (datetime.fromisoformat(committed), f"{repo.name}@{commit[:8]}:{path}",
                           meta.split()[2])

    @staticmethod
    def _rows(blob: bytes, label: str) -> tuple:
        """({pair_id: {(type, outcome)}}, {slot: {(type, outcome)}}) of one version."""
        import pandas as pd
        from csv_to_db import _source_slot_key
        frame = pd.read_csv(io.BytesIO(blob), dtype=str, keep_default_na=False,
                            encoding="utf-8-sig")
        type_column = next((c for c in ("type", "paper_type") if c in frame.columns), None)
        if "pair_id" not in frame.columns or "outcome" not in frame.columns or not type_column:
            # A version that cannot be read might be the one that disagrees.
            raise ValueError(f"{label}: no pair_id, outcome or type column")
        by_pair, by_slot = {}, {}
        for row in frame.to_dict("records"):
            value = ((row[type_column] or "").strip().lower(), _canonical(row["outcome"]))
            if row["pair_id"]:
                by_pair.setdefault(row["pair_id"], set()).add(value)
            slot = _source_slot_key(row)
            if slot:
                by_slot.setdefault(slot, set()).add(value)
        return by_pair, by_slot

    def shown(self, pair_id, at, slot=None):
        """(type, outcome) that every row that could have been this record gives,
        in every version committed by `at` — so whichever was imported, the page
        showed it — or None when they disagree or none holds it."""
        values = set()
        for committed, by_pair, by_slot in self.versions:
            if committed <= at:
                values |= by_pair.get(pair_id, set())
                if slot:
                    values |= by_slot.get(slot, set())
        return values.pop() if len(values) == 1 else None


# Fixed, not GITHUB_REPO: that names the app's current source (for a while this
# repository), and this repository's own files are read separately anyway.
EXTRACTOR_REPO = "forrtproject/flora-extractor"


def load_history(clone: Path) -> ExtractorHistory:
    """Bring both sources up to date — a branch missing here is a version unread —
    and read them. `clone` is made (without file contents) if missing, and must be
    a clone of flora-extractor if not."""
    if clone.exists():
        origin = _git(clone, "remote", "get-url", "origin").decode().strip()
        if not origin.rstrip("/").removesuffix(".git").endswith(EXTRACTOR_REPO):
            raise SystemExit(f"{clone} is a clone of {origin}, not {EXTRACTOR_REPO}")
        _git(clone, "fetch", "--all", "--quiet")
    else:
        subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{EXTRACTOR_REPO}.git", str(clone)], check=True)
    _git(HERE, "fetch", "--all", "--quiet")
    print(f"Reading every version of the extracted file (clone: {clone}) …")
    history = ExtractorHistory(clone)
    print(f"  versions read: {len(history.versions)}\n")
    return history


def _slot(row: dict):
    """The record's extractor slot, keyed as the importer keys it."""
    from csv_to_db import _source_slot_key
    return _source_slot_key({"work_id": row.get("work_id"),
                             "original_rank": row.get("original_rank")})


def is_same_outcome_correction(judgement: dict, record_type, record_outcome) -> bool:
    """The pattern app._same_outcome_as_agreement recognises at submission."""
    if record_type != "replication":
        return False
    if judgement.get("type_check") != "correct" or judgement.get("outcome_check") != "incorrect":
        return False
    requested = _canonical(judgement.get("corrected_outcome"))
    return bool(requested) and requested == _canonical(record_outcome)


def saw_outcome(judgement: dict, outcome, history=None, pair_id=None, judged_at=None,
                slot=None) -> str:
    """Did this judgement see `outcome`? "verified", "history", "changed" or
    "unverifiable" (see the module docstring). What the judgement recorded decides;
    with no record, the extractor history can, when it is unambiguous up to the
    judgement's submission."""
    shown = _canonical(_checks(judgement.get("additional_checks")).get("shown_outcome"))
    if shown is not None:
        return "verified" if shown == _canonical(outcome) else "changed"
    seen = (history.shown(pair_id, judged_at, slot)
            if history and (pair_id or slot) and judged_at else None)
    if seen is None:
        return "unverifiable"
    return "history" if seen == ("replication", _canonical(outcome)) else "changed"


def converted(judgement: dict, unverified: bool = False, history: bool = False) -> dict:
    """The judgement as the API would store it today, keeping the answer it replaces
    (outcome_agreement_original) so each conversion can be undone exactly."""
    markers = {**MARKERS, **(UNVERIFIED_MARKER if unverified else {}),
               **(HISTORY_MARKER if history else {})}
    original = {"outcome_check": judgement.get("outcome_check"),
                "corrected_outcome": judgement.get("corrected_outcome")}
    return {
        **judgement,
        "outcome_check": "correct",
        "corrected_outcome": None,
        "additional_checks": {**_checks(judgement.get("additional_checks")), **markers,
                              "outcome_agreement_original": original},
    }


def _judged_at(row):
    """When the judgement was submitted: the latest a page could have been
    reloaded, so every version up to then counts (shown_at would leave some out)."""
    return row.get("validated_at") or row.get("shown_at")


def _queue_rows(cur, history=None) -> list:
    cur.execute(
        """
        SELECT vq.queue_id::text AS queue_id, vq.record_id::text AS record_id,
               vq.validator_slot, vq.validator_id, vq.validator_name, vq.type_check,
               vq.outcome_check, vq.corrected_outcome, vq.additional_checks,
               vq.shown_at, vq.validated_at,
               u.type AS record_type, u.outcome AS record_outcome, u.pair_id,
               m.work_id, m.original_rank
        FROM validation_queue vq
        JOIN unvalidated u ON u.record_id = vq.record_id
        LEFT JOIN record_metadata m ON m.record_id = vq.record_id
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
            row["verdict"] = saw_outcome(row, row["record_outcome"], history,
                                         row["pair_id"], _judged_at(row), _slot(row))
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


def review_candidates(cur, record_ids, history=None) -> list:
    """Replications in need_review whose two human judgements now agree outright
    and both saw today's outcome — recorded, or by the extractor history — as the
    one they were shown: the ones this mismatch alone kept from consensus. Lock the
    checked record until consensus runs, so an import or admin cannot change that
    baseline in between."""
    from consensus_engine import _checks_agree, _corrections_agree, _is_unsure, _quote_flagged
    candidates = []
    for record_id in sorted(record_ids):
        cur.execute(
            "SELECT u.validation_status, u.type, u.outcome, u.pair_id, u.admin_checked, "
            "u.admin_override, u.admin_name, u.admin_notes, m.work_id, m.original_rank "
            "FROM unvalidated u LEFT JOIN record_metadata m ON m.record_id = u.record_id "
            "WHERE u.record_id = %s FOR UPDATE OF u",
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
                   doi_r_published, additional_checks, shown_at, validated_at
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
        # A judgement converted unverified is never re-evaluated, whatever the
        # history says now: that conversion was taken on a person's word.
        if any(_checks(h.get("additional_checks")).get("outcome_agreement_unverified")
               for h in humans):
            continue
        if any(saw_outcome(h, status["outcome"], history, status["pair_id"], _judged_at(h),
                           _slot(status))
               not in ("verified", "history") for h in humans):
            continue
        if _checks_agree(h1, h2) and _corrections_agree(h1, h2):
            candidates.append(record_id)
    return candidates


def conversion_backup(queue_rows, copies) -> dict:
    """Every judgement a conversion is about to change, exactly as stored now."""
    return {
        "validation_queue": [
            {key: row[key] for key in ("queue_id", "record_id", "validator_slot", "outcome_check",
                                       "corrected_outcome", "additional_checks")}
            for row in queue_rows],
        "stored_copies": [{"record_id": record_id, "column": column, "judgement": judgement}
                          for record_id, column, judgement, _ in copies],
    }


def reevaluation_backup(cur, record_ids) -> dict:
    """The records consensus is about to settle, whole: it rewrites their status and
    final values and replaces their row in validated."""
    saved = {}
    for table in ("unvalidated", "validated"):
        cur.execute(f"SELECT * FROM {table} WHERE record_id::text = ANY(%s)",   # fixed names
                    (list(record_ids),))
        saved[table] = [dict(r) for r in cur.fetchall()]
    return {"records_before_reevaluation": saved}


def write_backup(payload: dict, prefix: str = "outcome_agreement") -> Path:
    """Save what this run is about to change to a new file — raising, before
    anything is written to the database, if it cannot. Also used by
    auto_validate_waiting.py."""
    folder = Path(os.environ.get("OUTCOME_BACKFILL_BACKUP_DIR")
                  or Path(__file__).resolve().parent / "backups")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for n in range(1000):
        path = folder / f"{prefix}_{stamp}{f'_{n}' if n else ''}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:     # never overwrite one
                json.dump(payload, handle, indent=1, default=str)
            return path
        except FileExistsError:
            continue
    raise FileExistsError(f"no free backup name in {folder}")


def run(apply: bool, reevaluate: bool, include_unverified: bool = False,
        history: ExtractorHistory | None = None) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise EnvironmentError("DATABASE_URL must be set in environment or .env")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            queue_rows = _queue_rows(cur, history)
            copies = _json_copies(cur)
            # A stored copy records no times of its own; it shares the history
            # verdict — either way — of the queue row holding the same judgement,
            # so the two are never split. An assignment's copy has no such row.
            by_history = {(r["record_id"], r["validator_slot"]): r for r in queue_rows
                          if r["verdict"] in ("history", "changed")}
            copies = [
                (record_id, column, judgement, row["verdict"])
                if verdict == "unverifiable" and not judgement.get("is_assignment")
                and (row := by_history.get((record_id, _SLOT[column])))
                and str(judgement.get("validator_id")) == str(row["validator_id"])
                and judgement.get("corrected_outcome") == row["corrected_outcome"]
                else (record_id, column, judgement, verdict)
                for record_id, column, judgement, verdict in copies
            ]

            def convertible(verdict):
                return (verdict in ("verified", "history")
                        or (include_unverified and verdict == "unverifiable"))

            to_convert = [r for r in queue_rows if convertible(r["verdict"])]
            copies_to_convert = [c for c in copies if convertible(c[3])]
            by_verdict = {v: [r for r in queue_rows if r["verdict"] == v]
                          for v in ("verified", "history", "changed", "unverifiable")}

            print(f"  validation_queue judgements matching:   {len(queue_rows)}")
            print(f"    verified (recorded today's outcome):  {len(by_verdict['verified'])}")
            if history is not None:
                print(f"    verified by the extractor history:    {len(by_verdict['history'])}")
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

            if apply and (to_convert or copies_to_convert):
                backup = write_backup(conversion_backup(to_convert, copies_to_convert))
                print(f"  saved as they are now, before writing:  {backup}")

            converted_rows = 0
            record_ids = set()
            for row in to_convert:
                checks = converted(row, row["verdict"] == "unverifiable",
                                   row["verdict"] == "history")["additional_checks"]
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
                    (json.dumps(converted(judgement, verdict == "unverifiable",
                                          verdict == "history")), record_id,
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
            candidates = review_candidates(cur, record_ids | previously_converted(cur), history)
            print(f"  need_review records that now agree:     {len(candidates)}")
            for record_id in candidates:
                print(f"    {record_id}")

            if not apply:
                conn.rollback()
                print("\nDry run — nothing written. Re-run with --apply to convert.")
                return

            if reevaluate and candidates:
                from consensus_engine import evaluate_consensus
                backup = write_backup(reevaluation_backup(cur, candidates))
                print(f"  records saved before re-evaluating:     {backup}")
                for record_id in candidates:
                    evaluate_consensus(cur, record_id)
                    cur.execute(
                        "SELECT validation_status FROM unvalidated WHERE record_id = %s",
                        (record_id,),
                    )
                    print(f"  re-evaluated {record_id} → {cur.fetchone()['validation_status']}")
            conn.commit()
            # The same evidence settles them: without the history, the judgements
            # it verified are unverifiable again and the re-run finds nothing.
            again = "--apply --reevaluate" + (" --extractor-history" if history is not None else "")
            print(f"\nConverted {converted_rows} judgement(s) and {converted_copies} stored copy(ies)."
                  + ("" if reevaluate or not candidates
                     else f" Re-run with {again} to settle the records listed above."))
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
    parser.add_argument("--extractor-history", nargs="?", metavar="CLONE",
                        const=str(Path(tempfile.gettempdir()) / "flora-extractor-history"),
                        help="verify judgements that recorded no shown outcome against every "
                             "version of the extracted file; CLONE is a clone of flora-extractor, "
                             "made (without file contents) if missing")
    args = parser.parse_args()
    if (args.reevaluate or args.include_unverified) and not args.apply:
        parser.error("--reevaluate and --include-unverified need --apply")
    history = None
    if args.extractor_history:
        history = load_history(Path(args.extractor_history))
    run(args.apply, args.reevaluate, args.include_unverified, history)
