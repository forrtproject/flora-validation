"""
consensus_engine.py — Determines validation outcome after both human validators submit.

Decision tree:
  1. checks agree + corrections agree → LLM sanity check → consensus_reached for an
     admin to approve, or validated straight away when an auto-validation rule
     applies (auto_validation_rule)
  2. checks agree + corrections differ → LLM sanity check, stored for the admin
     only → need_review
  3. checks differ → LLM tiebreaker → agrees with one human → consensus_reached,
     else → need_review

A validator answering "Can't tell", or the quote gate flagging a submission, sends
the record to need_review before this tree; the LLM sanity check still runs there,
for the admin only. Once both humans have submitted, every record gets an LLM call
except a senior reject, which is final.
"""
import json
import re
from datetime import datetime, timezone

import extra_originals
from llm_validator import run_llm_validation
from record_checks import check_record
from extractor_vocab import (
    REPLICATION_OUTCOMES,
    derive_reproduction_outcome,
    normalize_axis_value,
    normalize_outcome,
    normalize_quote_source,
    split_joined_outcome,
)

_CHECK_FIELDS = ["type_check", "original_check", "outcome_check"]
# outcome_check for an original marked wrong with no replacement: no paper, so no
# outcome to judge. Never equals an LLM answer, so it cannot win a tiebreak.
NO_OUTCOME = "not_applicable"
# A record in one of these has been decided, and only an admin moves it on. The same
# definition of "not open" as the assignment list (app.my_assignments).
DECIDED_STATUSES = ("validated", "rejected")
_CORRECTION_FIELDS = [
    "corrected_doi_o", "corrected_title_o", "corrected_outcome", "corrected_type",
    "corrected_title_r", "corrected_url_r",
    # Reproduction axes. The coded values are compared; their quotes are not, for
    # the same reason corrected_outcome_quote isn't — a longer quote is extra
    # context, not a disagreement, so the longest wins in _resolve_final instead
    # of sending the record to review.
    "corrected_outcome_computation", "corrected_outcome_robustness",
]

# (coded field, quote field, source field, final_* prefix) per reproduction axis.
# Drives resolution, the UPDATE and the validated insert, so adding an axis is one
# entry rather than three parallel edits that can fall out of step.
_REPRO_AXES = (
    ("corrected_outcome_computation", "corrected_computational_quote", "corrected_computational_source",
     "outcome_computation", "outcome_computational_quote", "out_quote_computational_source"),
    ("corrected_outcome_robustness", "corrected_robustness_quote", "corrected_robustness_source",
     "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source"),
)


# ── Auto-validation ───────────────────────────────────────────────────────────
# Two agreeing validators skip admin review when the AI sanity check agrees too and
#   "trusted":     one of them is Trusted or Senior, or
#   "experienced": both have had more than 19 entries approved by an admin and
#                  fewer than 3 judgements flagged, ever.
# A look-back over every past agreement found the admin review changed no type,
# outcome or original study on any entry these rules would have let through.
AUTO_RULE_TRUSTED = "trusted"
AUTO_RULE_EXPERIENCED = "experienced"
EXPERIENCED_MIN_APPROVED = 20
EXPERIENCED_MAX_FLAGS = 2

# Entries a validator (alias `v`) judged that an admin approved. Auto-validated
# entries do not count, or the rules would feed themselves. Also the "approved"
# figure on the validators table and the approval card (app._APPROVED_COUNT_SQL).
APPROVED_BY_ADMIN_SQL = """(SELECT COUNT(DISTINCT aq.record_id)
                 FROM validation_queue aq
                 JOIN unvalidated au ON au.record_id = aq.record_id
                 WHERE aq.validator_id   = v.id
                   AND aq.is_validated   = TRUE
                   AND aq.validator_slot IN ('human_1', 'human_2')
                   AND au.validation_status = 'validated'
                   AND au.admin_checked  = TRUE)"""

# Per record: how many of its two human validators are Trusted or Senior, and how
# many are experienced. One row, so the rule reads it with a single fetchone.
_AUTO_VALIDATION_STATS_SQL = f"""
    SELECT COUNT(*) FILTER (WHERE tier >= 1) AS trusted_count,
           COUNT(*) FILTER (WHERE approved >= {EXPERIENCED_MIN_APPROVED}
                              AND flags <= {EXPERIENCED_MAX_FLAGS}) AS experienced_count
    FROM (
        SELECT v.validator_tier AS tier,
               {APPROVED_BY_ADMIN_SQL} AS approved,
               (SELECT COUNT(*) FROM validation_queue fq
                WHERE fq.validator_id = v.id AND fq.flagged) AS flags
        FROM validation_queue vq
        JOIN validators v ON v.id = vq.validator_id
        WHERE vq.record_id = %s
          AND vq.is_validated = TRUE
          AND vq.validator_slot IN ('human_1', 'human_2')
    ) team
"""


def auto_validation_stats(cur, record_id: str) -> tuple[int, int]:
    """(trusted_count, experienced_count) among the record's human validators."""
    cur.execute(_AUTO_VALIDATION_STATS_SQL, (record_id,))
    row = cur.fetchone()
    if row is None:
        return 0, 0
    if isinstance(row, dict):
        return int(row["trusted_count"] or 0), int(row["experienced_count"] or 0)
    return int(row[0] or 0), int(row[1] or 0)


def _original_disputed(h: dict) -> bool:
    """"Wrong original": consensus fills in only what the validators supplied and
    keeps the rest of the extracted original — its title with a corrected DOI, or
    its DOI and OpenAlex work with a corrected title (none at all, with neither).
    An admin puts the right original together."""
    return h.get("original_check") == "incorrect"


def marked_wrong_without_replacement(h: dict) -> bool:
    """Wrong paper, and no suggestion for the right one. Judged on what the validator
    said rather than on outcome_check, so a judgement from a page that still asked
    for the outcome counts the same."""
    if h.get("original_check") != "incorrect" or _effective_corrected_type(h) == "not_validation":
        return False
    checks = _additional_checks(h)
    if checks.get("was_unsure_original") or checks.get("senior_reject"):
        return False
    return not ((h.get("corrected_doi_o") or "").strip() or (h.get("corrected_title_o") or "").strip())


def removal_proposed(h1: dict | None, h2: dict | None) -> bool:
    """Both validators say this original is not what the paper replicated and neither
    names another: there is nothing to publish. An admin confirms the removal."""
    return bool(h1 and h2 and marked_wrong_without_replacement(h1)
                and marked_wrong_without_replacement(h2))


def auto_validation_rule(h1: dict, h2: dict, llm: dict | None,
                         trusted_count: int, experienced_count: int) -> str | None:
    """The rule that lets this pair of judgements skip admin review, or None.

    Only a plain agreement qualifies: never "Can't tell", a quote the gate flagged,
    a senior reject, "neither type" (the LLM and an admin confirm those), a wrong
    original, a tiebreaker (the judgements disagree), or an AI sanity check that
    failed or disagrees. Self-contained, so a caller outside evaluate_consensus
    applies exactly the same test. Evaluated before the published row is written,
    so callers also refuse one that would take over another entry's
    (identity_taken)."""
    for h in (h1, h2):
        if _is_unsure(h) or _quote_flagged(h) or _additional_checks(h).get("senior_reject"):
            return None
    if not (_checks_agree(h1, h2) and _corrections_agree(h1, h2)):
        return None
    if _effective_corrected_type(h1) == "not_validation" or _original_disputed(h1):
        return None
    if not llm or not _llm_matches(llm, h1):
        return None
    if trusted_count >= 1:
        return AUTO_RULE_TRUSTED
    if experienced_count >= 2:
        return AUTO_RULE_EXPERIENCED
    return None


def validated_identity_conflict(
    cur, record_id: str, *, doi_r, study_r, title_r,
    doi_o, oa_work_id_o, study_o, title_o, lock: bool = True,
):
    """Return the other validated row with this proposed identity — locked, unless
    the caller is only looking (a dry run must not hold up admins)."""
    candidate_original_key = (doi_o if doi_o not in (None, "") else oa_work_id_o) or ""
    cur.execute(
        f"""
        SELECT v.record_id::text AS record_id,
               v.doi_r, v.study_r, v.title_r,
               v.doi_o, v.original_key, v.study_o, v.title_o,
               v.type, v.outcome
        FROM validated v
        WHERE v.record_id <> %s
          AND v.doi_r IS NOT DISTINCT FROM %s
          AND v.study_r IS NOT DISTINCT FROM %s
          AND v.title_r IS NOT DISTINCT FROM %s
          AND v.original_key IS NOT DISTINCT FROM %s
          AND v.study_o IS NOT DISTINCT FROM %s
          AND v.title_o IS NOT DISTINCT FROM %s
        LIMIT 1
        {"FOR UPDATE OF v" if lock else ""}
        """,
        (record_id, doi_r, study_r, title_r, candidate_original_key, study_o, title_o),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def identity_taken(cur, record: dict, final: dict, lock: bool = True) -> bool:
    """Would publishing `final` (as _insert_validated would) collide with another
    entry's row in validated? An admin merges those (admin_resolve's duplicate
    dialog); nothing publishes one unattended. This check can lose a race to a
    concurrent publish; _insert_validated cannot, and refuses too."""
    prior_doi_o = (record["final_doi_o"] if record.get("final_doi_o") is not None
                   else record.get("doi_o"))
    return validated_identity_conflict(
        cur, str(record.get("record_id")),
        doi_r=record.get("doi_r"), study_r=record.get("study_r"),
        title_r=final.get("title_r") or record.get("title_r"),
        doi_o=final["doi_o"],
        oa_work_id_o=record.get("oa_work_id_o") if final["doi_o"] == prior_doi_o else None,
        study_o=record.get("study_o"), title_o=final["title_o"], lock=lock,
    ) is not None


def approval_values(rec: dict) -> dict:
    """What approving an entry that reached consensus publishes, in the shape
    _insert_validated takes: each stored final_* value, else the extracted value it
    stands in for. Raises ValueError for an entry an admin must resolve first.
    One definition for admin approval (app.admin_approve) and
    auto_validate_waiting.py, so the two publish the same thing."""
    approved_type = rec.get("final_type") or rec.get("type")
    axes = dict.fromkeys(("outcome_computation", "outcome_computational_quote",
                          "out_quote_computational_source", "outcome_robustness",
                          "outcome_robustness_quote", "out_quote_robust_source"))
    if approved_type == "reproduction":
        computation = normalize_axis_value(
            "outcome_computation",
            rec.get("final_outcome_computation") or rec.get("outcome_computation"))
        robustness = normalize_axis_value(
            "outcome_robustness",
            rec.get("final_outcome_robustness") or rec.get("outcome_robustness"))
        if not computation or not robustness:
            raise ValueError("Resolve both reproduction axes before approval")
        outcome = derive_reproduction_outcome(computation, robustness)
        axes = {
            "outcome_computation": computation,
            "outcome_computational_quote":
                rec.get("final_computational_quote") or rec.get("outcome_computational_quote"),
            "out_quote_computational_source":
                rec.get("final_computational_source") or rec.get("out_quote_computational_source"),
            "outcome_robustness": robustness,
            "outcome_robustness_quote":
                rec.get("final_robustness_quote") or rec.get("outcome_robustness_quote"),
            "out_quote_robust_source":
                rec.get("final_robustness_source") or rec.get("out_quote_robust_source"),
        }
    else:
        outcome = normalize_outcome(rec.get("final_outcome") or rec.get("outcome"))
        if approved_type != "replication" or outcome not in REPLICATION_OUTCOMES \
                or outcome == "not_a_replication":
            raise ValueError("Resolve a valid replication outcome before approval")
    final_doi_o = rec.get("final_doi_o")
    return {
        "type": approved_type,
        "outcome": outcome,
        **axes,
        "title_r": rec.get("final_title_r") or rec.get("title_r"),
        "url_r": rec.get("final_url_r") or rec.get("url_r"),
        "abstract_r": rec.get("final_abstract_r") or rec.get("abstract_r"),
        # A deliberately blank original DOI (books, chapters) stays blank.
        "doi_o": final_doi_o if final_doi_o is not None else rec.get("doi_o"),
        "title_o": rec.get("final_title_o") or rec.get("title_o"),
        "outcome_quote": rec.get("final_outcome_quote") or rec.get("outcome_quote"),
        "out_quote_source": rec.get("final_out_quote_source") or rec.get("out_quote_source"),
        "doi_r_published": rec.get("doi_r_published"),
    }


def _normalize(text: str | None) -> str:
    """Lowercase, strip punctuation and whitespace — used for fuzzy abstract comparison."""
    if not text:
        return ""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _norm_doi(doi: str | None) -> str:
    """DOIs are case-insensitive and often pasted as resolver links — compare the
    bare, lowercased form so '10.1/X' and 'https://doi.org/10.1/x' agree."""
    return re.sub(r'(?i)^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*', '', (doi or '').strip()).lower()


def _additional_checks(h: dict) -> dict:
    """The validator's additional_checks as a dict. Tolerates the column arriving
    as a dict (jsonb adapter) or a raw JSON string."""
    ac = h.get("additional_checks")
    if isinstance(ac, str):
        try:
            ac = json.loads(ac)
        except (ValueError, TypeError):
            return {}
    return ac if isinstance(ac, dict) else {}


def _is_unsure(h: dict) -> bool:
    """Did this validator answer 'Can't tell' on the original or outcome? The check
    columns store 'incorrect' for unsure, so the real signal lives in additional_checks."""
    ac = _additional_checks(h)
    axis_checks = ac.get("reproduction_axis_checks")
    axis_unsure = isinstance(axis_checks, dict) and "unsure" in axis_checks.values()
    return bool(
        ac.get("was_unsure_original")
        or ac.get("was_unsure_outcome")
        or axis_unsure
    )


def _stored_llm(record: dict) -> dict | None:
    """The record's stored llm_validator as a dict, or None when it holds none.
    Tolerates the column arriving as a dict (jsonb adapter) or a raw JSON string."""
    stored = record.get("llm_validator")
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except (ValueError, TypeError):
            return None
    return stored if isinstance(stored, dict) else None


def _store_only_answer(record: dict, llm: dict) -> dict | None:
    """What a branch that only stores the LLM's answer writes to llm_validator.

    A call that errored does not replace a clean answer the record already holds
    (from an earlier evaluation, or the nightly retry): None leaves the column as
    it is. Only for the branches where the answer is shown to the admin and
    decides nothing. Where it decides the outcome, the stored value must be the
    answer the decision used, errors included: the nightly tiebreaker retry finds
    its records by that error."""
    if "error" in llm:
        earlier = _stored_llm(record)
        if earlier is not None and "error" not in earlier:
            return None
    return llm


def _quote_flagged(h: dict) -> bool:
    """Did the frontend's quote gate flag this submission? Stamped when the outcome
    quote (validator's edit, else extracted) wasn't found in the abstract even with
    fuzzy matching — either a legitimate full-text quote or a mis-copied one, and
    only a human can tell which."""
    return bool(_additional_checks(h).get("quote_not_in_abstract"))


def _checks_agree(h1: dict, h2: dict) -> bool:
    return all(h1.get(f) == h2.get(f) for f in _CHECK_FIELDS)


def effective_corrected_type(human: dict) -> str | None:
    return _effective_corrected_type(human)


def _effective_corrected_type(human: dict) -> str | None:
    """A type correction exists only when the validator rejected the stored type.

    Older clients could send a stale ``corrected_type`` while also saying the type
    was correct. Ignore that contradictory field here as a defence-in-depth measure
    for rows already stored before the API boundary became strict.
    """
    if human.get("type_check") != "incorrect":
        return None
    return human.get("corrected_type")


def _corrections_agree(h1: dict, h2: dict) -> bool:
    for field in _CORRECTION_FIELDS:
        left = _effective_corrected_type(h1) if field == "corrected_type" else h1.get(field)
        right = _effective_corrected_type(h2) if field == "corrected_type" else h2.get(field)
        if left != right:
            return False
    # Published DOIs compare in bare, case-folded form (resolver-link paste ≠ conflict)
    if _norm_doi(h1.get("doi_r_published")) != _norm_doi(h2.get("doi_r_published")):
        return False
    # Normalize text fields before comparing so minor formatting differences don't cause conflicts
    return _normalize(h1.get("corrected_abstract")) == _normalize(h2.get("corrected_abstract"))


def _llm_matches(llm: dict, human: dict) -> bool:
    if llm.get("error"):
        return False
    # Equality check, no special-casing needed: llm_validator can return "uncertain"
    # for any of the three checks, which never equals a human's "correct"/"incorrect"
    # — so an unconfident LLM automatically fails to match, the same as a genuine
    # disagreement, and the record falls through to need_review instead of the LLM
    # accidentally winning a tiebreak or forcing a reject on a guess.
    return all(llm.get(f) == human.get(f) for f in _CHECK_FIELDS)


def quote_source_for(quote: str | None, abstract: str | None) -> str | None:
    """Where does this outcome quote come from?
      'abstract'  — the (normalised) quote is contained in the (normalised) abstract
      'full_text' — it isn't (so it must have come from the paper body)
      None        — there is no quote to place.
    Normalising (lowercase, drop punctuation/whitespace) makes the match robust to
    the light edits validators make to quotes."""
    nq = _normalize(quote)
    if not nq:
        return None
    return "abstract" if nq in _normalize(abstract) else "full_text"


def admin_quote_source(submitted, rec: dict, quote: str | None, abstract: str | None,
                       admin_handle: str | None) -> tuple[str | None, str | None]:
    """What an admin's resolve stores as the outcome quote's source, and under whose
    name: (source, out_quote_source_by). A name says a person chose the source: the
    backfill leaves that row alone and the Final Preview says "set by".

      - A source other than the stored one is a real choice: it is used, in the
        admin's name.
      - The stored one sent back is not a choice: the form returns what it was given
        whether or not anyone looked at it. It stays, with the name it already has,
        an earlier admin's or none. Detecting here would be no fix: it would replace
        a granular value such as 'discussion' with a guess.
      - Nothing sent: detected from the final quote against the final abstract,
        falling back to the stored value, in nobody's name.

    Any named source counts, not just abstract/full_text, and values are compared
    normalised: 'abstract | discussion' is 'abstract|discussion'. `rec` is the
    unvalidated row as read before the resolve.
    """
    chosen = normalize_quote_source(submitted)
    stored = rec.get("final_out_quote_source") or rec.get("out_quote_source")
    if not chosen:
        return quote_source_for(quote, abstract) or stored, None
    if chosen == normalize_quote_source(stored):
        return chosen, rec.get("out_quote_source_by")
    return chosen, admin_handle


def _resolve_quote_source(record: dict, suggested_quotes: list, abstract: str | None = None) -> str | None:
    """Pick the outcome_quote source per the agreed rule:
      - validators suggested a new quote → check the longest against the abstract;
      - they agreed with the extracted quote → keep the existing source, no re-check;
      - no quote at all → leave it unset.
    `abstract` defaults to the record's extracted abstract; callers pass the FINAL
    (possibly validator-corrected) abstract so the check matches what gets published."""
    if abstract is None:
        abstract = record.get("abstract_r")
    if suggested_quotes:
        return quote_source_for(max(suggested_quotes, key=len), abstract)
    if not _normalize(record.get("outcome_quote")):
        return None
    return record.get("out_quote_source")


def _resolve_axes(record: dict, winner: dict, other: dict | None) -> dict:
    """Resolve each reproduction axis independently.

    The axes are independent by definition — a reproduction can fail
    computationally and still find the conclusion robust — so neither may be
    derived from the other, and one axis being corrected says nothing about the
    other. Each falls back to the extractor's value on its own.

    Quotes follow the same longest-wins rule as the replication outcome quote:
    extra context is not a disagreement. Each selected quote keeps the source from
    that same submission because these quotes legitimately come from named full-text
    sections (results, discussion, etc.).
    """
    resolved: dict = {}
    for coded, quote_f, source_f, out_coded, out_quote, out_source in _REPRO_AXES:
        judged = [h for h in (winner, other) if h]
        evidence = [
            (h.get(quote_f), h.get(source_f))
            for h in judged if h.get(quote_f)
        ]
        try:
            resolved[out_coded] = normalize_axis_value(
                out_coded, winner.get(coded) or record.get(out_coded)
            )
        except ValueError:
            # Historical malformed summaries must not make consensus write a value
            # that the database rejects. The final resolver below turns a missing
            # axis into an explicit cannot_be_determined judgement.
            resolved[out_coded] = None
        if evidence:
            chosen_quote, chosen_source = max(evidence, key=lambda pair: len(pair[0]))
            resolved[out_quote] = chosen_quote
            # Quote and source are one evidence tuple. Combining the longest quote
            # from one validator with another validator's source fabricates a claim
            # neither person submitted.
            resolved[out_source] = chosen_source
        else:
            resolved[out_quote] = record.get(out_quote)
            resolved[out_source] = record.get(out_source)
    return resolved


def _resolve_final(record: dict, winner: dict, other: dict | None = None) -> dict:
    """Build final consensus values using winner's corrections, falling back to original record.
    outcome_quote: when validators edited it, the longest edit wins (most context)."""
    quotes = [h.get("corrected_outcome_quote") for h in [winner, other] if h and h.get("corrected_outcome_quote")]
    abstracts = [h.get("corrected_abstract") for h in [winner, other] if h and h.get("corrected_abstract")]
    # Keep the longest edited abstract (most complete) — deterministic and
    # reproducible, mirroring how the outcome quote is chosen below.
    final_abstract = max(abstracts, key=len) if abstracts else None
    final_type = _effective_corrected_type(winner) or record.get("type")
    resolved_axes = _resolve_axes(record, winner, other)
    if final_type == "reproduction":
        legacy_computation, legacy_robustness = split_joined_outcome(
            winner.get("corrected_outcome")
            or (other or {}).get("corrected_outcome")
            or record.get("outcome")
        )
        resolved_axes["outcome_computation"] = (
            resolved_axes.get("outcome_computation")
            or legacy_computation
            or "cannot_be_determined"
        )
        resolved_axes["outcome_robustness"] = (
            resolved_axes.get("outcome_robustness")
            or legacy_robustness
            or "cannot_be_determined"
        )
        final_outcome = derive_reproduction_outcome(
            resolved_axes.get("outcome_computation"),
            resolved_axes.get("outcome_robustness"),
        )
    else:
        # Replications never carry the reproduction grid. If this is a type change,
        # only a genuine replication correction is eligible; the record's old joined
        # reproduction label is deliberately not used as a fallback.
        candidate = winner.get("corrected_outcome") or (other or {}).get("corrected_outcome")
        if not candidate and record.get("type") == "replication":
            candidate = record.get("outcome")
        final_outcome = normalize_outcome(candidate)
        if final_outcome not in REPLICATION_OUTCOMES or final_outcome == "not_a_replication":
            final_outcome = "cannot_be_determined"
        resolved_axes = {field: None for axis in _REPRO_AXES for field in axis[3:]}
    return {
        **resolved_axes,
        # Study numbers are extractor identity, not title text and not a typo-
        # correction target. Carry them unchanged while resolving titles in their
        # own fields.
        "study_r": record.get("study_r"),
        "title_r": winner.get("corrected_title_r") or record.get("title_r"),
        "url_r":   winner.get("corrected_url_r")    or record.get("url_r"),
        "doi_o":   winner.get("corrected_doi_o")   or record.get("doi_o"),
        "study_o": record.get("study_o"),
        "title_o": winner.get("corrected_title_o") or record.get("title_o"),
        "outcome": final_outcome,
        "type":    final_type,
        "outcome_quote": max(quotes, key=len) if quotes else record.get("outcome_quote"),
        # check the source against the same abstract we publish (corrected if present)
        "out_quote_source": _resolve_quote_source(record, quotes, final_abstract or record.get("abstract_r")),
        "abstract_r": final_abstract,
        "doi_r_published": winner.get("doi_r_published") or record.get("doi_r_published"),
    }


def _update_status(cur, record_id: str, status: str, is_tiebreaker: bool,
                   final: dict | None, llm_summary: dict | None,
                   record: dict | None = None, auto_rule: str | None = None) -> None:
    """Write the consensus outcome, and the row-local quality flags with it.

    Every terminal branch of evaluate_consensus() routes through here, so this is
    the one place that sees a record once all three judges are done. The flags are
    advisory: they are recorded and shown on the admin review panel, and never
    change `status`. See record_checks for what is checked here and what stays on
    the pipeline's cron job.
    """
    params: list = [status, is_tiebreaker]
    set_clauses = ["validation_status = %s", "is_tiebreaker = %s"]

    if record is not None:
        # Checked against the values that would actually be published, so a
        # correction a validator already made is not reported as a problem.
        set_clauses += ["quality_flags = %s::jsonb", "quality_checked_at = NOW()"]
        params.append(json.dumps(check_record(record, final)))

    if final:
        set_clauses += [
            "final_title_r = %s", "final_url_r = %s",
            "final_doi_o = %s", "final_title_o = %s",
            "final_outcome = %s", "final_type = %s",
            "final_outcome_quote = %s",
        ]
        params += [final.get("title_r"), final.get("url_r"), final["doi_o"], final["title_o"],
                   final["outcome"], final["type"], final.get("outcome_quote")]
        set_clauses.append("final_out_quote_source = %s")
        params.append(final.get("out_quote_source"))
        # Reproduction axes. Written unconditionally, like the replication fields
        # above: on a replication every value resolves to NULL, which is correct.
        for _, _, _, out_coded, out_quote, out_source in _REPRO_AXES:
            final_col = {
                "outcome_computation": "final_outcome_computation",
                "outcome_computational_quote": "final_computational_quote",
                "out_quote_computational_source": "final_computational_source",
                "outcome_robustness": "final_outcome_robustness",
                "outcome_robustness_quote": "final_robustness_quote",
                "out_quote_robust_source": "final_robustness_source",
            }
            for src in (out_coded, out_quote, out_source):
                set_clauses.append(f"{final_col[src]} = %s")
                params.append(final.get(src))
        if final.get("abstract_r"):
            set_clauses.append("abstract_r = %s")
            params.append(final["abstract_r"])
        if final.get("doi_r_published"):
            set_clauses.append("doi_r_published = %s")
            params.append(final["doi_r_published"])

    if llm_summary is not None:
        set_clauses.append("llm_validator = %s")
        params.append(json.dumps(llm_summary))

    if auto_rule:
        set_clauses += ["auto_validated_rule = %s", "auto_validated_at = NOW()"]
        params.append(auto_rule)

    params.append(record_id)
    cur.execute(
        f"UPDATE unvalidated SET {', '.join(set_clauses)} WHERE record_id = %s",
        params,
    )

    # A rejected record must never remain in the authoritative export table.
    if status == "rejected":
        cur.execute("DELETE FROM validated WHERE record_id = %s", (record_id,))
        # Nor may the originals an admin added to it. Reachable: the nightly import
        # can send a validated record back to review (csv_to_db re-key), and the
        # tiebreaker retry can then reject it.
        if extra_originals.may_have_children(record):
            extra_originals.reject_children(cur, record_id)


def _insert_validated(cur, record: dict, final: dict) -> bool:
    """Publish this record. Returns False, with validated as it was, if another entry
    holds the identity: that entry's row must not be overwritten, and the caller sends
    this one to an admin to merge. The answer comes from the insert itself, because a
    check made before it can lose the race to a concurrent publish."""
    # Undoes the DELETE below when the insert is refused: a record re-keyed back to
    # review can still have a row that stays published until an admin decides.
    cur.execute("SAVEPOINT publish_validated")
    # Remove any prior row for this record first: the natural key
    # (doi/title + within-paper study numbers) is mutable, so a correction to those
    # fields would otherwise leave a stale duplicate under the old key.
    cur.execute("DELETE FROM validated WHERE record_id = %s", (record.get("record_id"),))
    # A work id belongs to a specific DOI: if consensus resolves the original to a
    # different DOI than the one the work id was fetched for, drop it so we never
    # publish a work id pointing at the old paper. (doi_r isn't corrected in consensus.)
    # doi_o has a legitimate blank state (see admin_resolve/db_schema.sql) — a
    # deliberate '' clear must not collapse to the raw doi_o here either, even
    # though today's call graph can't actually reach this branch with one (only
    # admin actions can produce a '' final_doi_o, and they never re-enter this
    # function) — matches the same fix applied everywhere else doi_o is read.
    _prior_doi_o = record["final_doi_o"] if record.get("final_doi_o") is not None else record.get("doi_o")
    wid_o = record.get("oa_work_id_o") if final["doi_o"] == _prior_doi_o else None
    wid_r = record.get("oa_work_id_r")
    cur.execute(
        """
        INSERT INTO validated (
            record_id, doi_r, study_r, title_r, year_r, url_r, ref_r, abstract_r,
            doi_o, study_o, title_o, year_o, url_o, ref_o,
            oa_work_id_o, oa_work_id_r,
            type, outcome, outcome_quote, out_quote_source,
            outcome_computation, outcome_computational_quote, out_quote_computational_source,
            outcome_robustness, outcome_robustness_quote, out_quote_robust_source,
            doi_r_published, alt_identifier_r, validated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        -- original_key is doi_o, or oa_work_id_o when the original has no registered
        -- DOI (books, chapters, pre-DOI papers). doi_o is '' for ALL of those, so
        -- keying on it merged distinct originals that also shared a title. See
        -- db_schema.sql.
        ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o) DO NOTHING
        """,
        (
            record.get("record_id"),
            record.get("doi_r"),
            record.get("study_r"),
            final.get("title_r") or record.get("title_r"),
            record.get("year_r"),
            final.get("url_r") or record.get("url_r"),
            record.get("ref_r"),
            final.get("abstract_r") or record.get("abstract_r"),
            final["doi_o"],
            record.get("study_o"),
            final["title_o"],
            record.get("year_o"),
            record.get("url_o"),
            record.get("ref_o"),
            wid_o,
            wid_r,
            final["type"],
            final["outcome"],
            final.get("outcome_quote") or record.get("outcome_quote"),
            final.get("out_quote_source") or record.get("out_quote_source"),
            # Already resolved against the record in _resolve_axes, so no second
            # fallback here — that would resurrect an extractor value the winning
            # validator deliberately left blank.
            final.get("outcome_computation"),
            final.get("outcome_computational_quote"),
            final.get("out_quote_computational_source"),
            final.get("outcome_robustness"),
            final.get("outcome_robustness_quote"),
            final.get("out_quote_robust_source"),
            final.get("doi_r_published") or record.get("doi_r_published"),
            record.get("alt_identifier_r"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    if cur.rowcount == 0:
        cur.execute("ROLLBACK TO SAVEPOINT publish_validated")
        return False
    cur.execute("RELEASE SAVEPOINT publish_validated")
    # Originals an admin added to this record follow what it now publishes. Not
    # strict: no admin is here to decide, so one that would collide is skipped.
    if extra_originals.may_have_children(record):
        extra_originals.sync_children(cur, record.get("record_id"), strict=False)
    return True


def evaluate_consensus(cur, record_id: str, llm_answer: dict | None = None) -> None:
    """
    Called after each human submission. Reads completed human rows from
    validation_queue, applies the decision tree, and writes the outcome
    back to unvalidated (and validated on success).

    Args:
        cur: open psycopg2 cursor (caller manages transaction/commit)
        record_id: UUID of the record being evaluated
        llm_answer: an answer the caller already has, in place of asking Gemini here,
            so that it need not hold the record's row lock across the call
    """
    cur.execute(
        """
        SELECT validator_slot, type_check, original_check, outcome_check,
               corrected_doi_o, corrected_title_o, corrected_outcome, corrected_type,
               corrected_title_r, corrected_url_r, corrected_abstract, corrected_outcome_quote,
               corrected_outcome_computation, corrected_computational_quote,
               corrected_computational_source, corrected_outcome_robustness,
               corrected_robustness_quote, corrected_robustness_source,
               doi_r_published, additional_checks
        FROM validation_queue
        WHERE record_id = %s AND is_validated = TRUE
          AND validator_slot IN ('human_1', 'human_2')
        ORDER BY validator_slot
        """,
        (record_id,),
    )
    rows = cur.fetchall()
    if len(rows) < 2:
        return

    # Support dict rows (DictCursor / tests) and tuple rows (default cursor)
    # MUST stay in the SELECT's column order — tuple rows are zipped against it.
    _human_cols = ["validator_slot", "type_check", "original_check", "outcome_check",
                   "corrected_doi_o", "corrected_title_o", "corrected_outcome", "corrected_type",
                   "corrected_title_r", "corrected_url_r", "corrected_abstract", "corrected_outcome_quote",
                   "corrected_outcome_computation", "corrected_computational_quote",
                   "corrected_computational_source", "corrected_outcome_robustness",
                   "corrected_robustness_quote", "corrected_robustness_source",
                   "doi_r_published", "additional_checks"]
    if rows and isinstance(rows[0], dict):
        humans = [dict(row) for row in rows]
    else:
        humans = [dict(zip(_human_cols, row)) for row in rows]
    h1, h2 = humans[0], humans[1]

    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (record_id,))
    record_row = cur.fetchone()
    if record_row is None:
        return

    if isinstance(record_row, dict):
        record = dict(record_row)
    else:
        record_cols = [d[0] for d in cur.description]
        record = dict(zip(record_cols, record_row))

    # Decided already: re-evaluating would overwrite that decision with the validators'
    # values and leave the published row behind. Status, not admin_checked: a validated
    # record the import re-keyed to need_review keeps admin_checked and is open again.
    if record.get("validation_status") in DECIDED_STATUSES:
        return

    def ask_llm(context: str) -> dict:
        return llm_answer if llm_answer is not None else run_llm_validation(record, context=context)

    # A reject made through the senior-reject path is authoritative: once a senior
    # has used it, neither the second validator nor the LLM can overturn it. This
    # makes the conclusion 'rejected' regardless of the order in which the two
    # humans submitted. Detected via the 'senior_reject' marker the senior-reject
    # endpoint stamps on the slot (NOT a plain not_validation in the normal flow).
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM validation_queue vq
        WHERE vq.record_id = %s
          AND vq.is_validated = TRUE
          AND vq.validator_slot IN ('human_1', 'human_2')
          AND vq.additional_checks ? 'senior_reject'
        """,
        (record_id,),
    )
    sr_row = cur.fetchone()
    if (sr_row["n"] if isinstance(sr_row, dict) else sr_row[0]) >= 1:
        _update_status(cur, record_id, "rejected", False, None, None, record)
        return

    # If either validator answered "Can't tell" (unsure) on the original or outcome,
    # the record can't be auto-resolved — a validator explicitly couldn't judge it.
    # Route it to human review instead of treating unsure as a hard 'incorrect'.
    # The LLM still runs, but only so the admin sees its answer: it never decides.
    # An errored call keeps a clean answer stored earlier (_store_only_answer).
    if _is_unsure(h1) or _is_unsure(h2):
        llm = _store_only_answer(record, ask_llm("sanity_check"))
        _update_status(cur, record_id, "need_review", False, None, llm, record)
        return

    # If the quote gate flagged either submission (outcome quote not found in the
    # abstract, even fuzzily), an admin must look before anything gets published:
    # it's either a legitimate full-text quote — the admin sets the quote source —
    # or a mis-copied quote that needs fixing. This intentionally also stops the
    # senior auto-validate shortcut. The LLM's answer is stored for the admin only.
    if _quote_flagged(h1) or _quote_flagged(h2):
        llm = _store_only_answer(record, ask_llm("sanity_check"))
        _update_status(cur, record_id, "need_review", False, None, llm, record)
        return

    # Two validators, no original: nothing here may reach FLoRA. The LLM's answer is
    # stored for the admin only, and final values stay empty so approval cannot
    # publish the extracted original and outcome.
    if removal_proposed(h1, h2):
        llm = _store_only_answer(record, ask_llm("sanity_check"))
        _update_status(cur, record_id, "need_review", False, None, llm, record)
        return

    # Who the two validators are, for the auto-validation rules.
    trusted_count, experienced_count = auto_validation_stats(cur, record_id)

    checks_ok = _checks_agree(h1, h2)
    corrections_ok = _corrections_agree(h1, h2)

    if checks_ok and corrections_ok:
        # Both validators agree this is not a replication → LLM confirms or sends to admin
        if (_effective_corrected_type(h1) == "not_validation" and
                _effective_corrected_type(h2) == "not_validation"):
            llm = ask_llm("sanity_check")
            if not llm.get("error") and _llm_matches(llm, h1):
                _update_status(cur, record_id, "rejected", False, None, llm, record)
            else:
                # LLM thinks it IS a replication — admin should review
                _update_status(cur, record_id, "need_review", False, None, llm, record)
            return

        llm = ask_llm("sanity_check")
        final = _resolve_final(record, h1, h2)
        rule = auto_validation_rule(h1, h2, llm, trusted_count, experienced_count)
        # A duplicate of a validated entry goes to an admin to merge, whether this
        # check finds it or the insert does, having lost a race to a concurrent publish.
        if rule and (identity_taken(cur, record, final) or not _insert_validated(cur, record, final)):
            rule = None
        if rule:
            # An auto-validation rule applies — validated, no admin review needed.
            _update_status(cur, record_id, "validated", False, final, llm, record, auto_rule=rule)
        else:
            # Normal agreement — admin must approve
            _update_status(cur, record_id, "consensus_reached", False, final, llm, record)

    elif checks_ok and not corrections_ok:
        # Branch 2: checks agree but corrections differ → need_review. The LLM
        # sanity check runs only so the admin sees its answer; it never decides.
        llm = _store_only_answer(record, ask_llm("sanity_check"))
        _update_status(cur, record_id, "need_review", False, None, llm, record)

    else:
        # Branch 3: checks differ → LLM tiebreaker
        llm = ask_llm("tiebreaker")

        if llm.get("error"):
            _update_status(cur, record_id, "need_review", True, None, llm, record)
            return

        matches_h1 = _llm_matches(llm, h1)
        matches_h2 = _llm_matches(llm, h2)

        if matches_h1 and not matches_h2:
            if _effective_corrected_type(h1) == "not_validation":
                # LLM + h1 agree it's not a replication, but h2 disagrees → admin decides
                _update_status(cur, record_id, "need_review", True, None, llm, record)
            else:
                final = _resolve_final(record, h1, h2)
                _update_status(cur, record_id, "consensus_reached", True, final, llm, record)
        elif matches_h2 and not matches_h1:
            if _effective_corrected_type(h2) == "not_validation":
                # LLM + h2 agree it's not a replication, but h1 disagrees → admin decides
                _update_status(cur, record_id, "need_review", True, None, llm, record)
            else:
                final = _resolve_final(record, h2, h1)
                _update_status(cur, record_id, "consensus_reached", True, final, llm, record)
        else:
            # 3-way split or LLM matches neither/both
            _update_status(cur, record_id, "need_review", True, None, llm, record)
