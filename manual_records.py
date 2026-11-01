"""Records added by hand in the FLoRA tab.

An admin adds a replication or reproduction that no source holds. It is stored in
manual_records, with its history in manual_record_events, and every save projects it
into source_records in the same transaction: source 'manual', display id MAN-000001,
the way adjudication/review.py publishes ADJ- rows. From there it is a source row
like any other. The Source Records grid and its duplicate queue show it at once, so
does the FLoRA tab (a live build), and the next pipeline run gives it its FLoRA ID
(flora_registry.refresh) and publishes it.

Duplicates are warned about, never refused. transform_sources.load() puts rows added
by hand last, so one with the identifiers of a record another source holds is
absorbed into that record: the record keeps its ID and values, and the entry only
fills what the record lacks (preprint_dedup.merge_doi_pair_dups and fill_blanks). An
admin who knows it is a different study says so (keep_distinct): its source row is
ruled 'distinct' and it becomes a record of its own. The other way round, a hand
record that is already a FLoRA record keeps its ID and values against rows arriving
later, which only fill its blanks (preprint_dedup._yields). Which of these happens is
predicted from the build's own input rows (placements) and said in every answer, so
nobody waits for a MAN- ID that will not come.

Withdrawn, never deleted. Once the registry knows an entry under a FLoRA ID, its own
or the one of the record it is absorbed into, the entry is frozen and the record is
corrected in its FLoRA editor (flora_edits.py), so a record has one place to correct
it. Identifiers on the exclusion list are refused.

No FastAPI types here: app.py maps ManualRecordError to an HTTP response.
"""
from __future__ import annotations

import json
import re

import pandas as pd

import flora_edits
import preprint_dedup
import source_records_service
import transform_sources
from extractor_vocab import (
    OUTCOME_COMPUTATION_VALUES,
    OUTCOME_ROBUSTNESS_VALUES,
    REPLICATION_OUTCOMES,
    derive_reproduction_outcome,
    normalize_axis_value,
    normalize_outcome,
    normalize_quote_source,
)

SOURCE_KEY = preprint_dedup.MANUAL_SOURCE
DISPLAY_PREFIX = "MAN"

ORIGINAL_FIELDS = ("doi_o", "url_o", "ref_o", "study_o", "alt_identifier_o")
REPLICATION_SIDE = ("doi_r", "url_r", "ref_r", "year_r", "abstract_r", "alt_identifier_r")
OUTCOME_FIELDS = {
    "replication": ("outcome", "outcome_quote", "out_quote_source"),
    "reproduction": ("outcome_computation", "outcome_computational_quote",
                     "out_quote_computational_source", "outcome_robustness",
                     "outcome_robustness_quote", "out_quote_robust_source"),
}
# The two reproduction axes: verdict, quote, quote source, and the name the form uses.
AXES = (("outcome_computation", "outcome_computational_quote",
         "out_quote_computational_source", "Computational"),
        ("outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
         "Robustness"))
# Every stored field but the note, in the form's order. The columns
# transform_sources.load() reads: study_r is left out, as the build has no use for it.
FIELDS = ("type", *ORIGINAL_FIELDS, *REPLICATION_SIDE,
          *OUTCOME_FIELDS["replication"], *OUTCOME_FIELDS["reproduction"])
LIMITS = {"doi_o": 300, "doi_r": 300, "url_o": 1000, "url_r": 1000, "ref_o": 2000,
          "ref_r": 2000, "study_o": 50, "alt_identifier_o": 300, "alt_identifier_r": 300,
          "year_r": 10, "abstract_r": 20000, "outcome": 100, "outcome_quote": 5000,
          "out_quote_source": 100, "outcome_computation": 100,
          "outcome_computational_quote": 5000, "out_quote_computational_source": 100,
          "outcome_robustness": 100, "outcome_robustness_quote": 5000,
          "out_quote_robust_source": 100}
NOTE_LIMIT = 2000
REASON_LIMIT = 1000
LABELS = {"doi_o": "Original DOI", "url_o": "Original link", "ref_o": "Original reference",
          "study_o": "Original study", "alt_identifier_o": "Original alternative identifier",
          "doi_r": "Replication DOI", "url_r": "Replication link", "ref_r": "Replication reference",
          "year_r": "Year", "abstract_r": "Abstract", "alt_identifier_r": "Replication alternative identifier",
          "outcome": "Outcome", "outcome_quote": "Outcome quote", "out_quote_source": "Quote source",
          "outcome_computation": "Computational verdict",
          "outcome_computational_quote": "Computational quote",
          "out_quote_computational_source": "Computational quote source",
          "outcome_robustness": "Robustness verdict", "outcome_robustness_quote": "Robustness quote",
          "out_quote_robust_source": "Robustness quote source", "note": "Why it is added"}
# Cleaned by flora_edits.clean_value, as a correction to the same field is: DOIs,
# links, the year, and clean_text on references, the abstract and quotes.
_EDITOR_CLEANED = ("doi_o", "doi_r", "url_o", "url_r", "ref_o", "ref_r", "abstract_r",
                   "alt_identifier_o", "alt_identifier_r", "year_r", "outcome_quote",
                   "outcome_computation", "outcome_computational_quote",
                   "outcome_robustness", "outcome_robustness_quote")
_QUOTE_SOURCES = ("out_quote_source", "out_quote_computational_source", "out_quote_robust_source")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_DISPLAY_RE = re.compile(r"^MAN-\d{6,}$")
NEW_ID = "new"                                    # the record_id a draft is placed under


class ManualRecordError(Exception):
    """A refused request: an HTTP status and a detail (str or structured dict)."""

    def __init__(self, status: int, detail):
        super().__init__(detail if isinstance(detail, str) else detail.get("message"))
        self.status = status
        self.detail = detail


def _refuse(status: int, code: str, message: str, field: str | None = None, **extra):
    detail = {"code": code, "message": message, **extra}
    if field:
        detail["field"] = field
    raise ManualRecordError(status, detail)


def _text(value) -> str | None:
    """Stripped text with \\n line breaks, None for anything blank or not text."""
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    # A NUL cannot be stored in PostgreSQL text.
    out = str(value).replace(chr(0), "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return out or None


def _same(a, b) -> bool:
    return (_text(a) or "") == (_text(b) or "")


# ── Vocabulary and validation ─────────────────────────────────────────────────

def _aliases(cur) -> dict:
    cur.execute("SELECT raw_value, canonical_value FROM outcome_alias")
    return {r["raw_value"]: r["canonical_value"] for r in cur.fetchall()}


def options(cur) -> dict:
    """What the form offers: the replication outcomes the build accepts as they are
    (outcome_alias maps them onto themselves), the reproduction verdicts, the limits."""
    outcomes = sorted(raw for raw, canonical in _aliases(cur).items()
                      if raw == canonical and canonical in REPLICATION_OUTCOMES
                      and canonical != "not_a_replication")
    return {"outcomes": outcomes,
            "outcome_computation": sorted(OUTCOME_COMPUTATION_VALUES),
            "outcome_robustness": sorted(OUTCOME_ROBUSTNESS_VALUES),
            "limits": {**LIMITS, "note": NOTE_LIMIT, "reason": REASON_LIMIT}}


def clean_note(note) -> str:
    text = _text(note)
    if not text:
        _refuse(422, "note_required", "Say where this record comes from and why it is added. "
                "The note is internal: it is never published.", "note")
    if len(text) > NOTE_LIMIT:
        _refuse(422, "too_long", f"The note is longer than {NOTE_LIMIT} characters.", "note")
    return text


def clean(cur, values: dict) -> dict:
    """The fields as stored, or a 422 naming the field that is wrong.

    Required: the type; a DOI or a reference for the original; a DOI or a link for
    the replication, and a reference without a DOI (FLoRA describes a paper without
    a DOI by its reference); a replication's outcome, quote and quote source; both
    of a reproduction's verdicts, each with a quote and its source unless it is
    "not checked". A quote source left blank is worked out from the abstract, as
    consensus does.
    """
    from consensus_engine import quote_source_for      # local: it imports the validators' stack
    values = values or {}
    kind = _text(values.get("type"))
    if kind not in OUTCOME_FIELDS:
        _refuse(422, "invalid_type", "Choose replication or reproduction.", "type")
    other = "reproduction" if kind == "replication" else "replication"
    for field in OUTCOME_FIELDS[other]:
        if _text(values.get(field)):
            _refuse(422, "not_for_type", f"A {kind} has no {LABELS[field].lower()}.", field)

    out = dict.fromkeys(FIELDS)
    out["type"] = kind
    for field in (*ORIGINAL_FIELDS, *REPLICATION_SIDE, *OUTCOME_FIELDS[kind]):
        text = _text(values.get(field))
        if text and len(text) > LIMITS[field]:
            _refuse(422, "too_long", f"{LABELS[field]} is longer than {LIMITS[field]} characters.", field)
        if text and field in _EDITOR_CLEANED:
            try:
                text = flora_edits.clean_value(field, text, kind)
            except ValueError as exc:
                _refuse(422, "invalid", f"{LABELS[field]}: {exc}", field)
        out[field] = text

    if not out["doi_o"] and not out["ref_o"]:
        _refuse(422, "original_required", "The original needs a DOI, or its reference when it "
                "has none: FLoRA describes an original without a DOI by its reference.", "doi_o")
    if not out["doi_r"] and not out["url_r"]:
        _refuse(422, "replication_required", "The replication needs a DOI or a link: without "
                "one it can be traced to nothing and compared with nothing.", "doi_r")
    if not out["doi_r"] and not out["ref_r"]:
        _refuse(422, "reference_required", "The replication has no DOI, so it needs its "
                "reference.", "ref_r")

    def source_of(quote, given):
        chosen = normalize_quote_source(given)
        return chosen or (quote_source_for(quote, out["abstract_r"]) if quote else None)

    if kind == "replication":
        raw = normalize_outcome(out["outcome"])
        canonical = _aliases(cur).get(raw) if raw else None
        if not raw or canonical not in REPLICATION_OUTCOMES or canonical == "not_a_replication":
            _refuse(422, "outcome_required", "Choose an outcome FLoRA accepts.", "outcome")
        out["outcome"] = raw
        if not out["outcome_quote"]:
            _refuse(422, "quote_required", "Quote the passage the outcome rests on.", "outcome_quote")
        out["out_quote_source"] = source_of(out["outcome_quote"], out["out_quote_source"])
    else:
        for verdict, quote, source, label in AXES:
            if not out[verdict]:
                _refuse(422, "outcome_required", f"Choose the {label.lower()} verdict.", verdict)
            if out[verdict] == "not checked":
                continue
            if not out[quote]:
                _refuse(422, "quote_required", f"Quote the passage the {label.lower()} verdict "
                        "rests on (not needed when it is not checked).", quote)
        for verdict, quote, source, _ in AXES:
            out[source] = source_of(out[quote], out[source])
    return out


def _draft_identifiers(values: dict) -> dict | None:
    """The identifiers of a form still being filled in, cleaned the way the build
    cleans them, or None while there is nothing to compare."""
    values = values or {}
    kind = _text(values.get("type"))
    kind = kind if kind in OUTCOME_FIELDS else "replication"
    out = {"type": kind,
           "doi_o": transform_sources.clean_doi(_text(values.get("doi_o"))),
           "doi_r": transform_sources.clean_doi(_text(values.get("doi_r"))),
           "url_r": _text(values.get("url_r"))}
    return out if (out["doi_r"] or out["url_r"]) else None


def _refuse_excluded(cur, v: dict) -> None:
    _, exclusions = transform_sources.load_rules(cur)
    match = transform_sources.matching_exclusion(exclusions, v.get("doi_o"), v.get("doi_r"),
                                                 v.get("url_r"))
    if match is not None:
        reason = match.get("reason") or ""
        _refuse(409, "excluded", "These identifiers are on the exclusion list, so FLoRA would "
                "leave the record out" + (f" ({reason})" if reason else "") + ". Remove the "
                "exclusion first if it belongs in FLoRA.", "doi_r", reason=reason or None)


# ── Where the build puts a row ────────────────────────────────────────────────

def _confirmed(rulings, side: str) -> list:
    """The confirmed preprint rulings step 2b applies, on one side."""
    return [row for row in preprint_dedup.confirmed_decisions(preprint_dedup.CONFIRMED_PATH, rulings)
            if row["action"] in preprint_dedup.APPLY_ACTIONS
            and (row.get("side") or "").startswith(side)]


def _pair_merge_reorders(df: pd.DataFrame, doi_r: pd.Series, originals: list) -> bool:
    """Whether step 2b's pair merge, run over every row, merges some group. It runs
    when there are original-side rulings, and when it merges anything it puts the
    rows it leaves alone (ruled distinct, or without a DOI on a side) after the
    others (merge_doi_pair_dups), which decides which of two rows of one key the
    dedup keeps. A placement worked out on a few rows has to know."""
    if not originals:
        return False
    mapping = {}
    for row in originals:
        remove, keep = preprint_dedup.derive_remove_keep(row["action"], row["doi_1"], row["doi_2"])
        if not preprint_dedup._blank(remove) and not preprint_dedup._blank(keep):
            mapping[str(remove).strip().lower()] = str(keep).strip().lower()
    doi_o = df["doi_o"].apply(transform_sources.clean_doi).map(lambda d: mapping.get(d, d))
    paired = doi_o.notna() & doi_r.notna() & ~df["duplicate_status"].eq("distinct")
    if paired.sum() < 2:
        return False
    groups = pd.DataFrame({"type": df["type"].fillna(""), "doi_o": doi_o, "doi_r": doi_r,
                           "url_r": df["url_r"]})[paired].groupby(["type", "doi_o", "doi_r"])
    return bool(((groups.size() >= 2) & (groups["url_r"].nunique() <= 1)).any())


def placements(rows: pd.DataFrame, record_ids, exclusions=(), rulings=(), registered=()) -> dict:
    """Where the next build puts each of `record_ids`, rows of `rows` (load() order).

    Runs the build's duplicate steps on the rows that can share a record with them —
    those with the same replication DOI (or, without one, the same link), and the
    other DOI of a confirmed replication-side preprint ruling — in the build's order:
    DOIs cleaned (2), confirmed preprint rulings (2b), exclusions (3), redundant links
    (4), the dedup (5) and the pair merge (5b), with their own functions where the
    build has them. Not the preprint detection after enrichment (6b), which needs
    the papers' metadata.

    `registered` are the record_ids that are a live FLoRA record's source row
    (transform_sources.load_live_primaries): a hand record among them keeps its record
    against rows arriving later, as in the build.

    Returns {record_id: placement}. A placement's `kind` is "own" (a record of its
    own, with `absorbs`, the record_ids folded into it), "absorbed" (into `into`, a
    record_id, by `mode`: "same_identifiers", "same_dois_one_link" or
    "preprint_ruling"), "excluded" (with the exclusion's `reason`), "left_out"
    (dropped by a preprint ruling) or "not_loaded" (not among the rows: withdrawn,
    or ruled a duplicate).
    """
    wanted = [str(r) for r in record_ids]
    out = {rid: {"kind": "not_loaded"} for rid in wanted}
    if rows is None or rows.empty or not wanted:
        return out
    df = rows.reset_index(drop=True)
    present = df["record_id"].astype(str).isin(wanted)
    if not present.any():
        return out

    doi_r = df["doi_r"].apply(transform_sources.clean_doi)
    url_r = df["url_r"].apply(transform_sources.norm_url)
    dois = set(doi_r[present].dropna())
    for row in _confirmed(rulings, "replication"):
        pair = {transform_sources.clean_doi(row["doi_1"]), transform_sources.clean_doi(row["doi_2"])}
        if pair & dois:
            dois |= pair - {None}
    links = set(url_r[present & doi_r.isna()].dropna())
    sub = df[present | doi_r.isin(dois) | (doi_r.isna() & url_r.isin(links))].copy()

    # 2 ── DOIs
    sub[preprint_dedup.CONFLICT_COLUMN] = None
    if sub["source"].eq(SOURCE_KEY).any():
        sub[preprint_dedup.REGISTERED_COLUMN] = sub["record_id"].astype(str).isin(set(map(str, registered)))
    for column in ("doi_o", "doi_r"):
        sub[column] = sub[column].apply(transform_sources.clean_doi)
    # Each row's identifiers as step 5 will compare them, to say how two rows meet.
    before = sub.assign(url_r=sub.apply(transform_sources.strip_redundant_url, axis=1))
    keys = dict(zip(before["record_id"].astype(str), transform_sources.dedup_keys(before)))
    display = dict(zip(df["record_id"].astype(str), df["display_id"]))

    # 2b ── confirmed preprint rulings, with the pair merge they bring
    reorders = _pair_merge_reorders(df, doi_r, _confirmed(rulings, "original"))
    sub = preprint_dedup.apply_confirmed(sub, verbose=False, rulings=rulings)
    if reorders and not sub.empty:
        # As the merge over every row leaves them, whether or not one of these merged.
        paired = (sub["doi_o"].notna() & sub["doi_r"].notna()
                  & ~sub["duplicate_status"].eq("distinct"))
        sub = pd.concat([sub[paired], sub[~paired]])
    # 3 ── exclusions
    if exclusions and not sub.empty:
        reasons = [transform_sources.matching_exclusion(exclusions, r["doi_o"], r["doi_r"], r["url_r"])
                   for r in sub.to_dict("records")]
        for rid, match in zip(sub["record_id"].astype(str), reasons):
            if match is not None and rid in out:
                out[rid] = {"kind": "excluded", "reason": match.get("reason")}
        sub = sub[[match is None for match in reasons]]
    # 4 ── redundant url_r
    if not sub.empty:
        sub["url_r"] = sub.apply(transform_sources.strip_redundant_url, axis=1)
    # 5 ── dedup: each key's first row survives; a row ruled distinct is never dropped
    if "merged_record_ids" not in sub.columns:
        sub["merged_record_ids"] = [[] for _ in range(len(sub))]
    if not sub.empty:
        key = transform_sources.dedup_keys(sub)
        # Rows that only fill blanks go after the others, as in the build.
        yields = preprint_dedup.yielding(sub, key)
        if yields.any():
            order = list(sub.index[~yields.to_numpy()]) + list(sub.index[yields.to_numpy()])
            sub, key = sub.loc[order], key.loc[order]
        dropped = (key.duplicated() & transform_sources.has_identifier(sub)
                   & ~sub["duplicate_status"].eq("distinct"))
        first = {}
        for idx, k in zip(sub.index, key):
            first.setdefault(k, idx)
        merged = {idx: list(v) if isinstance(v, list) else []
                  for idx, v in zip(sub.index, sub["merged_record_ids"])}
        for idx in sub.index[dropped.to_numpy()]:
            merged[first[key[idx]]] += [sub.at[idx, "record_id"], *merged[idx]]
        sub = sub[~dropped.to_numpy()].copy()
        sub["merged_record_ids"] = [merged[idx] for idx in sub.index]
        # 5b ── the pair merge
        sub, _ = preprint_dedup.merge_doi_pair_dups(sub, verbose=False)

    for row in sub.to_dict("records"):
        rid = str(row["record_id"])
        absorbed = [str(m) for m in (row.get("merged_record_ids") or [])]
        if rid in out:
            out[rid] = {"kind": "own", "absorbs": absorbed}
        for member in absorbed:
            if member in out and out[member]["kind"] == "not_loaded":
                mine, theirs = keys.get(member, ""), keys.get(rid, "")
                mode = ("same_identifiers" if mine == theirs
                        else "same_dois_one_link" if mine.rsplit("|", 1)[0] == theirs.rsplit("|", 1)[0]
                        else "preprint_ruling")
                out[member] = {"kind": "absorbed", "into": rid,
                               "into_display_id": display.get(rid), "mode": mode}
    loaded = set(df.loc[present, "record_id"].astype(str))
    for rid in wanted:
        if out[rid]["kind"] == "not_loaded" and rid in loaded:
            out[rid] = {"kind": "left_out"}
    return out


def _flora_ids(cur, claims: dict) -> dict:
    """{record_id: (flora_id, retired)} for the FLoRA record each source row is, or
    would be re-pointed to: the record it is primary of, else one claiming it or a
    row folded into it (flora_registry.refresh re-matches the same way).
    `claims` is {record_id: [record_ids folded into it]}."""
    ids = sorted({i for rid, merged in claims.items() for i in (rid, *merged)
                  if _UUID_RE.match(str(i))})
    if not ids:
        return {}
    cur.execute(
        """
        SELECT flora_id, retired_at IS NOT NULL AS retired,
               primary_source_record_id::text AS sid,
               merged_source_record_ids::text[] AS merged
        FROM flora_records
        WHERE primary_source_record_id = ANY(%s::uuid[])
           OR merged_source_record_ids && %s::uuid[]
        """,
        (ids, ids),
    )
    records = [dict(r) for r in cur.fetchall()]
    out = {}
    for rid, merged in claims.items():
        primary = [r for r in records if r["sid"] == str(rid)]
        claiming = [r for r in records
                    if {r["sid"], *(r["merged"] or [])} & {str(rid), *map(str, merged)}]
        found = sorted(primary or claiming, key=lambda r: (r["retired"], r["flora_id"]))
        if found:
            out[str(rid)] = (found[0]["flora_id"], found[0]["retired"])
    return out


def _issued(cur, recs: list) -> dict:
    """{manual_id: (flora_id, retired, role, merged)} for entries the registry knows
    under an issued FLoRA ID: the record whose source row the entry is ("primary"), the
    record it is absorbed into ("merged"), one minted from its display id ("minted";
    flora_registry pins it, with -R2 on a clash), or one it was part of before
    ("historical"). Live records first, then in that order. `merged` is how many other
    source rows that record holds."""
    ids = [str(r["manual_id"]) for r in recs if r.get("display_id")]
    if not ids:
        return {}
    cur.execute(
        """
        SELECT manual_id, flora_id, retired, role, merged FROM (
            SELECT m.manual_id::text AS manual_id, f.flora_id,
                   f.retired_at IS NOT NULL AS retired,
                   cardinality(f.merged_source_record_ids) AS merged,
                   CASE WHEN f.primary_source_record_id = m.source_record_id THEN 'primary'
                        WHEN m.source_record_id = ANY(f.merged_source_record_ids) THEN 'merged'
                        WHEN f.flora_id = m.display_id
                          OR f.flora_id LIKE m.display_id || '-R%%' THEN 'minted'
                        ELSE 'historical' END AS role
            FROM manual_records m
            JOIN flora_records f
              ON f.flora_id = m.display_id OR f.flora_id LIKE m.display_id || '-R%%'
              OR f.primary_source_record_id = m.source_record_id
              OR m.source_record_id = ANY(f.merged_source_record_ids)
              OR m.source_record_id = ANY(f.historical_source_record_ids)
            WHERE m.manual_id = ANY(%s::uuid[])
        ) known
        ORDER BY retired, array_position(ARRAY['primary', 'merged', 'minted', 'historical'], role),
                 flora_id
        """,
        (ids,),
    )
    out = {}
    for row in cur.fetchall():
        out.setdefault(row["manual_id"], (row["flora_id"], row["retired"], row["role"],
                                          int(row["merged"] or 0)))
    return out


def _frozen_text(flora_id: str) -> str:
    return (f"The entry is frozen because {flora_id} has its FLoRA ID: fix it in the FLoRA "
            f"editor for record {flora_id}.")


def _title_gaps(cur, recs: list, places: dict, from_build: bool) -> dict:
    """{manual_id: [the sides with no title]} for entries that are a record of their
    own. flora.csv and the public API leave out a record without a title on both
    sides (transform_sources.drop_untitled); the FLoRA tab still lists it.

    `from_build`: read the titles off the FLoRA tab's build (flora_service.dataset).
    Otherwise only what is known without a build: a side with neither a reference to
    read a title from nor a DOI whose metadata has one, where the DOI has no metadata
    (or was not found). A DOI not yet looked up may still bring one: not claimed."""
    own = [r for r in recs if places.get(r.get("source_record_id"), {}).get("kind") == "own"
           and r.get("display_id")]
    if not own:
        return {}
    sides = (("o", "original"), ("r", "replication"))
    out = {}
    if from_build:
        import flora_service                            # local: flora_service never imports us
        frame = flora_service.dataset(cur)
        mine = {r["display_id"]: r["manual_id"] for r in own}
        if frame.empty or "source_display_id" not in frame.columns:
            return {}
        for row in frame[frame["source_display_id"].isin(list(mine))].to_dict("records"):
            gaps = [label for side, label in sides if not _text(row.get(f"title_{side}"))]
            if gaps:
                out[mine[row["source_display_id"]]] = gaps
        return out
    dois = sorted({r[f"doi_{side}"] for r in own for side, _ in sides if r.get(f"doi_{side}")})
    titled, unknown = set(), set()
    if dois:
        cur.execute("SELECT doi, not_found, COALESCE(title, '') <> '' AS titled "
                    "FROM work_metadata WHERE doi = ANY(%s)", (dois,))
        for row in cur.fetchall():
            (titled if row["titled"] and not row["not_found"] else unknown).add(row["doi"])
    for r in own:
        gaps = [label for side, label in sides
                if not r.get(f"ref_{side}") and r.get(f"doi_{side}") not in titled
                and (not r.get(f"doi_{side}") or r.get(f"doi_{side}") in unknown)]
        if gaps:
            out[r["manual_id"]] = gaps
    return out


def _untitled_text(gaps: list, frozen_by: str | None) -> str:
    where = " and ".join(gaps)
    fix = (f"Add the title in the FLoRA editor for record {frozen_by}." if frozen_by
           else "Give its reference (APA) here, so the title can be read from it, or add the "
                "title in its FLoRA editor once it has an ID.")
    return (f" Not published as it stands: it has no title on the {where} side, so flora.csv "
            f"and the public API leave it out (the FLoRA tab still lists it). {fix}")


def _source_rows(cur):
    import flora_service                                # local: flora_service never imports us
    return flora_service.source_rows(cur)


def _rules(cur):
    _, exclusions = transform_sources.load_rules(cur)
    return exclusions, transform_sources.load_dedup_decisions(cur)


def _placement_text(place: dict, flora: dict, kept_from: list | None = None) -> str:
    """What a placement means, in one or two sentences."""
    kind = place["kind"]
    if kind == "absorbed":
        into = place.get("into_display_id") or "another record"
        flora_id = (flora.get(place["into"]) or (None,))[0]
        named = f"{into} (FLoRA ID {flora_id})" if flora_id and flora_id != into else into
        how = {"same_identifiers": "has the same identifiers",
               "same_dois_one_link": "has the same DOIs, and only one of the two has a replication link",
               "preprint_ruling": "is the same paper by a preprint ruling"}[place["mode"]]
        if flora_id:
            effect = (f"{flora_id} keeps its FLoRA ID and values, and this entry only fills what "
                      "it lacks. No new FLoRA ID will appear.")
        else:
            effect = (f"{into} keeps its values, and this entry only fills what it lacks. No FLoRA "
                      f"ID will appear for this entry: {into} has none yet and gets one at the "
                      "next pipeline run.")
        return f"{named} {how}, so the build merges this entry into that record: {effect}"
    if kind == "excluded":
        reason = place.get("reason")
        return ("Its identifiers are on the exclusion list" + (f" ({reason})" if reason else "")
                + ", so FLoRA leaves it out.")
    if kind == "left_out":
        return "A preprint ruling on its replication DOI leaves it out of FLoRA."
    if kind == "own":
        text = "A record of its own"
        if kept_from:
            text += ", ruled a different study from " + ", ".join(kept_from)
        return text + "."
    return "It is not in FLoRA."


# ── Duplicate check ───────────────────────────────────────────────────────────

def _candidate(rows: pd.DataFrame, v: dict, rid: str, display_id: str, distinct: bool):
    """`rows` with the candidate in place: replacing its own row when it has one
    (an entry being edited keeps its place), else last, where a new row added by
    hand sorts."""
    columns = list(rows.columns) if not rows.empty else ["record_id", "display_id", "source",
                                                          "type", "doi_o", "doi_r", "url_r",
                                                          "duplicate_status"]
    row = dict.fromkeys(columns)
    row.update({"record_id": rid, "display_id": display_id, "source": SOURCE_KEY,
                "type": v["type"], "doi_o": v.get("doi_o"), "doi_r": v.get("doi_r"),
                "url_r": v.get("url_r"), "duplicate_status": "distinct" if distinct else None})
    if not rows.empty and rows["record_id"].astype(str).eq(rid).any():
        out = rows.copy()
        mine = out.index[out["record_id"].astype(str) == rid][0]
        for column, value in row.items():
            if column in ("type", "doi_o", "doi_r", "url_r") or (column == "duplicate_status" and distinct):
                out.at[mine, column] = value
        return out
    return pd.concat([rows, pd.DataFrame([row])], ignore_index=True)


def _matches(rows: pd.DataFrame, rid: str) -> dict:
    """The live source rows a row shares identifiers with, for the warning: the same
    identifiers, the same DOIs where only one has a replication link (both made one
    record unless a ruling keeps them apart), the same replication with another
    original, and the same paper pair as the other type."""
    empty = {"same_identifiers": [], "same_dois_one_link": [], "same_replication": [],
             "same_pair_other_type": []}
    if rows.empty or not rows["record_id"].astype(str).eq(rid).any():
        return empty
    df = rows.reset_index(drop=True)
    # Every list shares the replication: its DOI, or the link of a row without one.
    doi_r = df["doi_r"].apply(transform_sources.clean_doi)
    mine = df["record_id"].astype(str) == rid
    if doi_r[mine].notna().iloc[0]:
        near = doi_r.eq(doi_r[mine].iloc[0])
    else:
        url_r = df["url_r"].apply(transform_sources.norm_url)
        near = doi_r.isna() & url_r.eq(url_r[mine].iloc[0])
    df = df[near | mine].copy().reset_index(drop=True)
    for column in ("doi_o", "doi_r"):
        df[column] = df[column].apply(transform_sources.clean_doi)
    df["url_r"] = df.apply(transform_sources.strip_redundant_url, axis=1)
    mine = df["record_id"].astype(str) == rid
    me = df[mine].iloc[0]
    dedup, record = transform_sources.dedup_keys(df), transform_sources.record_keys(df)
    others = ~mine
    same_key = others & dedup.eq(dedup[mine].iloc[0]) & transform_sources.has_identifier(df)
    one_link = others & ~same_key & record.notna() & record.eq(record[mine].iloc[0])
    same_rep = (others & df["doi_r"].notna() & df["doi_r"].eq(me["doi_r"])
                & df["type"].eq(me["type"]) & df["doi_o"].ne(me["doi_o"]))
    other_type = (others & df["doi_r"].notna() & df["doi_r"].eq(me["doi_r"])
                  & df["doi_o"].eq(me["doi_o"]) & df["type"].ne(me["type"]))

    def listed(mask):
        # A blank cell can be NaN here (the apply above gives NaN for a missing link
        # beside rows with one), and NaN is not JSON.
        keys = ("display_id", "source", "type", "doi_o", "doi_r", "url_r", "duplicate_status")
        return [{"record_id": str(r["record_id"]),
                 **{k: None if preprint_dedup._blank(r.get(k)) else r.get(k) for k in keys}}
                for r in df[mask].to_dict("records")]

    return {"same_identifiers": listed(same_key), "same_dois_one_link": listed(one_link),
            "same_replication": listed(same_rep), "same_pair_other_type": listed(other_type)}


def _hidden(cur, v: dict, exclude_record_id=None) -> list:
    """Source rows with the same paper that the build leaves out: deleted, or ruled
    a duplicate (Source Records' fingerprint, which is indexed)."""
    cur.execute(
        """
        SELECT display_id, source, type, deleted_at IS NOT NULL AS deleted, duplicate_status
        FROM source_records
        WHERE content_fingerprint = source_content_fingerprint(%s, %s, %s)
          AND (deleted_at IS NOT NULL OR duplicate_status = 'duplicate')
          AND record_id IS DISTINCT FROM %s::uuid
        ORDER BY display_id
        LIMIT 20
        """,
        (v.get("doi_o"), v.get("doi_r"), v.get("url_r"), exclude_record_id),
    )
    return [dict(r) for r in cur.fetchall()]


def _in_validation(cur, v: dict) -> list:
    """Records on this website with the same replication DOI that are being validated,
    or are validated but not yet in Source Records. A pair being validated reaches
    FLoRA as a VAL- row; another original for a validated entry belongs under the
    entry's Additional originals, where it follows the entry's corrections."""
    if not v.get("doi_r"):
        return []
    cur.execute(
        """
        SELECT u.record_id::text AS record_id, u.validation_status, u.doi_o,
               v.validated_record_id IS NOT NULL AS validated,
               EXISTS (SELECT 1 FROM source_records s
                        WHERE s.source = 'validated'
                          AND s.sheet_row_id = v.validated_record_id::text) AS synced
        FROM unvalidated u
        LEFT JOIN validated v ON v.record_id = u.record_id
        WHERE lower(u.doi_r) = lower(%s) AND u.validation_status <> 'rejected'
        ORDER BY u.record_id
        LIMIT 20
        """,
        (v["doi_r"],),
    )
    out = []
    for row in cur.fetchall():
        same = (transform_sources.clean_doi(row["doi_o"]) or "") == (v.get("doi_o") or "")
        if same and row["synced"]:
            continue                               # already a VAL- row: listed as a match
        out.append({**dict(row), "same_original": same})
    return out


def check(cur, values: dict, exclude_ref=None, keep_distinct: bool = False) -> dict:
    """What saving these values would do. Reads only: run as the admin types.

    Warnings, never refusals, except for an exclusion, which a save refuses. The
    placement is worked out from the build's own input rows (placements), with the
    draft placed last, as a new row added by hand sorts.
    """
    v = _draft_identifiers(values)
    if v is None:
        return {"ready": False}
    rec = _resolve(cur, exclude_ref) if exclude_ref else None
    rid = str(rec["source_record_id"]) if rec and rec.get("source_record_id") else NEW_ID
    display_id = (rec or {}).get("display_id") or "MAN-(new)"
    rows = _candidate(_source_rows(cur), v, rid, display_id, keep_distinct)
    exclusions, rulings = _rules(cur)
    place = placements(rows, [rid], exclusions, rulings,
                       transform_sources.load_live_primaries(cur))[rid]
    matches = _matches(rows, rid)
    claims = {m["record_id"]: [] for group in matches.values() for m in group}
    if place["kind"] == "absorbed":
        claims[place["into"]] = []
    flora = _flora_ids(cur, claims)
    for group in matches.values():
        for m in group:
            m["flora_id"] = (flora.get(m["record_id"]) or (None,))[0]
    kept_from = ([m["display_id"] for m in matches["same_identifiers"] + matches["same_dois_one_link"]]
                 if keep_distinct else None)
    return {"ready": True, "placement": {**place, "into_flora_id": (flora.get(place.get("into")) or (None,))[0]},
            "message": _placement_text(place, flora, kept_from),
            "matches": matches,
            "hidden": _hidden(cur, v, rec.get("source_record_id") if rec else None),
            "in_validation": _in_validation(cur, v),
            "excluded": place.get("reason") if place["kind"] == "excluded" else None}


# ── Reading entries ───────────────────────────────────────────────────────────

_ENTRY = """
    SELECT m.*, s.deleted_at AS source_deleted_at, s.duplicate_status,
           d.display_id AS duplicate_of
    FROM manual_records m
    LEFT JOIN source_records s ON s.record_id = m.source_record_id
    LEFT JOIN source_records d ON d.record_id = s.duplicate_of
"""


def _resolve(cur, ref, lock: bool = False) -> dict:
    """An entry by its manual_id or its MAN- display id, or a 404."""
    ref = str(ref or "").strip()
    if _UUID_RE.match(ref):
        column = "manual_id"
    elif _DISPLAY_RE.match(ref):
        column = "display_id"
    else:
        _refuse(404, "not_found", "No such record added by hand.")
    cur.execute(f"SELECT manual_id FROM manual_records WHERE {column} = %s"
                + (" FOR UPDATE" if lock else ""), (ref,))
    row = cur.fetchone()
    if row is None:
        _refuse(404, "not_found", "No such record added by hand.")
    cur.execute(_ENTRY + " WHERE m.manual_id = %s", (row["manual_id"],))
    return dict(cur.fetchone())


def _states(cur, recs: list, rows=None, titles: str | None = "build") -> dict:
    """{manual_id: state} for entries. A state's `code` is one of withdrawn,
    ruled_duplicate, excluded, in_flora, awaiting_id, absorbed, left_out; `editable`
    says whether the entry can still be changed, `message` what it means, and
    `withdraw_effect` what withdrawing it would do.

    Frozen (`frozen_by`, the record to correct instead) while the registry knows the
    entry under the record it is, or is absorbed into, now. A ruling made in Source
    Records since then that takes it out of that record frees it until the next run.
    `titles`: "build" checks the titles on the FLoRA tab's build, "known" only what is
    known without one, None not at all (_title_gaps)."""
    if not recs:
        return {}
    live = [r for r in recs if r["status"] == "active" and r.get("source_record_id")]
    places = {}
    if live:
        rows = _source_rows(cur) if rows is None else rows
        exclusions, rulings = _rules(cur)
        places = placements(rows, [r["source_record_id"] for r in live], exclusions, rulings,
                            transform_sources.load_live_primaries(cur))
    issued = _issued(cur, recs)
    claims = {}
    for place in places.values():
        if place["kind"] == "absorbed":
            claims.setdefault(place["into"], [])
    flora = _flora_ids(cur, claims)
    gaps_by_entry = (_title_gaps(cur, live, places, from_build=titles == "build")
                     if titles and live else {})

    states = {}
    for rec in recs:
        mid = rec["manual_id"]
        flora_id, retired, role, merged = issued.get(mid, (None, False, None, 0))
        place = places.get(rec.get("source_record_id"), {"kind": "not_loaded"})
        into_flora = (flora.get(place["into"]) or (None,))[0] if place["kind"] == "absorbed" else None
        if role in ("primary", "minted"):
            frozen_by = flora_id
        elif role in ("merged", "historical"):
            frozen_by = flora_id if place["kind"] == "absorbed" and into_flora == flora_id else None
        else:
            frozen_by = None
        state = {"code": None, "editable": False, "flora_id": frozen_by, "retired": retired,
                 "frozen_by": frozen_by, "into": None, "into_flora_id": None, "mode": None}
        frozen = f" {_frozen_text(frozen_by)}" if frozen_by else ""
        gaps = gaps_by_entry.get(mid)
        if rec["status"] == "withdrawn":
            state["flora_id"] = flora_id if role in ("primary", "minted") else None
            if role == "primary" and retired:
                after = (f" {flora_id} is retired; a restore brings it back at the next pipeline "
                         "run, unless another source has taken its identifiers meanwhile.")
            elif role == "primary" and merged:
                after = (f" {flora_id} stays in FLoRA, built from the rows merged into it; this "
                         "entry's own values leave it at the next pipeline run.")
            elif role == "primary":
                after = f" {flora_id} is retired at the next pipeline run; a restore brings it back."
            elif flora_id:
                after = f" {flora_id} stays in FLoRA; what this entry filled leaves it at the next pipeline run."
            else:
                after = ""
            state["code"] = "withdrawn"
            state["message"] = (f"Withdrawn by {rec.get('withdrawn_by') or 'an admin'}: "
                                f"{rec.get('withdrawn_reason') or ''}".rstrip(": ")
                                + ". Its source row is marked deleted, so it is out of FLoRA." + after)
        elif rec.get("duplicate_status") == "duplicate":
            state["code"] = "ruled_duplicate"
            state["message"] = (f"Ruled a duplicate of {rec.get('duplicate_of') or 'another row'} "
                                "in Source Records, so FLoRA leaves it out. The ruling is changed "
                                "there.")
        elif place["kind"] == "excluded":
            state.update(code="excluded", editable=not frozen_by,
                         message=_placement_text(place, flora) + frozen)
        elif frozen_by and (role in ("merged", "historical") or place["kind"] == "absorbed"):
            # Absorbed into a record the registry knows it under: frozen like its own.
            into = place.get("into_display_id") if place["kind"] == "absorbed" else None
            state.update(code="absorbed", into=into or flora_id, into_flora_id=flora_id,
                         mode=place.get("mode"),
                         message=f"Merged into {flora_id}" + (" (retired)" if retired else "")
                         + (f", built from {into}" if into and into != flora_id else "")
                         + ": that record keeps its FLoRA ID and values, and this entry only "
                         "fills its blanks." + frozen)
        elif frozen_by:
            state["code"] = "in_flora"
            absorbs = place.get("absorbs") if place["kind"] == "own" else []
            state["message"] = (f"In FLoRA as {flora_id}" + (" (retired)" if retired else "")
                                + (", with the values it had: rows with its identifiers that "
                                   "arrived later only fill its blanks" if absorbs else "")
                                + "." + frozen + (_untitled_text(gaps, frozen_by) if gaps else ""))
        elif place["kind"] == "own":
            kept = rec.get("keep_distinct") and rec.get("duplicate_status") == "distinct"
            leaving = (f" It is still part of {flora_id} until then." if flora_id else "")
            state.update(code="awaiting_id", editable=True,
                         message="A record of its own" + (", ruled a different study" if kept else "")
                         + ": it is in the FLoRA tab now and gets its FLoRA ID at the next "
                         "pipeline run (nightly, 03:00 UTC)." + leaving
                         + (_untitled_text(gaps, None) if gaps else ""))
        elif place["kind"] == "absorbed":
            state.update(code="absorbed", editable=True, into=place.get("into_display_id"),
                         into_flora_id=into_flora, mode=place["mode"],
                         message=_placement_text(place, flora))
        elif place["kind"] == "left_out":
            state.update(code="left_out", editable=True, message=_placement_text(place, flora))
        else:
            state.update(code="unknown", message="Not in the current build.")
        if gaps and state["code"] in ("in_flora", "awaiting_id"):
            state["unpublished"] = gaps
        if rec["status"] == "active":
            state["withdraw_effect"] = _withdraw_effect(state, place, role, merged)
        states[mid] = state
    return states


def _withdraw_effect(state: dict, place: dict, role, merged: int) -> str:
    """What withdrawing an active entry would do, for the confirmation."""
    record = state.get("frozen_by")
    if state["code"] == "in_flora":
        if merged or place.get("absorbs"):
            return (f"{record} stays in FLoRA, built from the rows merged into it: this entry's "
                    "own values leave it at the next pipeline run, and the ID stays.")
        return (f"{record} leaves FLoRA at the next pipeline run: its ID is retired and never "
                "reused, and its corrections stop applying until the entry is restored.")
    if state["code"] == "absorbed":
        return (f"Nothing is retired: {record or state.get('into') or 'that record'} stays, and "
                "what this entry fills leaves it at the next build.")
    return "It leaves the FLoRA tab at once. No FLoRA ID has been issued under it."


def _entry(rec: dict) -> dict:
    keep = (*FIELDS, "note", "keep_distinct", "status", "version", "manual_id", "display_id",
            "source_record_id", "created_by", "created_at", "updated_by", "updated_at",
            "withdrawn_by", "withdrawn_at", "withdrawn_reason", "duplicate_status", "duplicate_of")
    out = {k: rec.get(k) for k in keep}
    out["source_deleted"] = rec.get("source_deleted_at") is not None
    if rec.get("type") == "reproduction":
        out["outcome"] = derive_reproduction_outcome(rec.get("outcome_computation"),
                                                     rec.get("outcome_robustness"))
    return out


def detail(cur, ref, rows=None, titles: str | None = "build") -> dict:
    """One entry: its fields, history, state and the rows it shares identifiers with."""
    rec = _resolve(cur, ref)
    cur.execute(
        """
        SELECT event_id, occurred_at, admin_handle, action, changes, note
        FROM manual_record_events WHERE manual_id = %s ORDER BY event_id DESC
        """,
        (rec["manual_id"],),
    )
    events = [dict(r) for r in cur.fetchall()]
    state = _states(cur, [rec], rows, titles)[rec["manual_id"]]
    out = {**_entry(rec), "events": events, "state": state}
    if rec["status"] == "active" and rec.get("source_record_id"):
        rows = _source_rows(cur) if rows is None else rows
        out["matches"] = _matches(rows, rec["source_record_id"])
    return out


def list_records(cur, status: str = "active") -> dict:
    """Every entry with the given status (active, withdrawn or all), newest first.
    The place to find one: an entry absorbed into another record has no row of its
    own in the FLoRA tab."""
    where = {"active": "WHERE m.status = 'active'", "withdrawn": "WHERE m.status = 'withdrawn'",
             "all": ""}.get(status)
    if where is None:
        _refuse(400, "invalid_status", "status is active, withdrawn or all.")
    cur.execute(_ENTRY + where + " ORDER BY m.created_at DESC, m.display_id DESC")
    recs = [dict(r) for r in cur.fetchall()]
    states = _states(cur, recs)
    return {"entries": [{**_entry(r), "state": states[r["manual_id"]]} for r in recs],
            "counts": counts(cur)}


def counts(cur) -> dict:
    cur.execute("SELECT COUNT(*) FILTER (WHERE status = 'active') AS active, "
                "COUNT(*) FILTER (WHERE status = 'withdrawn') AS withdrawn FROM manual_records")
    row = cur.fetchone()
    return {"active": int(row["active"] or 0), "withdrawn": int(row["withdrawn"] or 0)}


def saved(cur, manual_id: str, head: str | None = None, repeat: bool = False) -> dict:
    """The answer to a save, withdraw or restore: the entry, and a notice saying
    plainly what the next build makes of it — a record of its own (so a new FLoRA ID
    at the next pipeline run, which the form offers to start now) or a record
    another source holds, named. `head` opens the notice ("Saved as MAN-000001.")."""
    out = detail(cur, manual_id, titles="known")
    state = out["state"]
    head = head or (f"Already saved as {out['display_id']}." if repeat
                    else f"Saved as {out['display_id']}.")
    out["notice"] = f"{head} {state['message']}"
    out["run_pipeline"] = state["code"] == "awaiting_id"
    out["repeat"] = repeat
    return out


# ── Writing ───────────────────────────────────────────────────────────────────

_SOURCE_COLUMNS = ("type", "ref_o", "doi_o", "url_o", "study_o", "alt_identifier_o",
                   "ref_r", "doi_r", "url_r", "year_r", "abstract_r", "alt_identifier_r",
                   "outcome", "outcome_quote", "out_quote_source",
                   "outcome_computation", "outcome_computational_quote",
                   "out_quote_computational_source", "outcome_robustness",
                   "outcome_robustness_quote", "out_quote_robust_source")
_RAW_FIELDS = ("manual_id", "display_id", "status", "note", "keep_distinct", "created_by",
               "created_at", "updated_by", "updated_at", "withdrawn_by", "withdrawn_at",
               "withdrawn_reason")


def source_row(rec: dict) -> dict:
    """The Source Records row an entry is projected to. Only one outcome shape, as
    sync_validated writes them; raw keeps who added it and why, stringified like
    every other source's raw row (admin-only: the build and the API never read it)."""
    row = {column: rec.get(column) for column in _SOURCE_COLUMNS}
    if rec["type"] == "reproduction":
        row.update(outcome=None, outcome_quote=None, out_quote_source=None)
    else:
        for verdict, quote, source, _ in AXES:
            row.update({verdict: None, quote: None, source: None})
    row["source"] = SOURCE_KEY
    row["sheet_row_id"] = str(rec["manual_id"])
    row["raw"] = json.dumps({k: ("" if rec.get(k) is None else str(rec.get(k)))
                             for k in _RAW_FIELDS})
    return row


def _next_display_id(cur) -> str:
    cur.execute(
        """
        INSERT INTO source_display_counters (source, last_value) VALUES (%s, 1)
        ON CONFLICT (source) DO UPDATE SET last_value = source_display_counters.last_value + 1
        RETURNING last_value
        """,
        (SOURCE_KEY,),
    )
    return f"{DISPLAY_PREFIX}-{cur.fetchone()['last_value']:06d}"


def _project(cur, manual_id: str) -> dict:
    """Write the entry through to source_records: the first time a new row with the
    next MAN- id, after that its row, never its id. A withdrawn entry's row is
    marked deleted, an active one's not. Returns the entry as it now stands."""
    rec = _resolve(cur, manual_id)
    if rec.get("source_record_id") is None:
        display_id = _next_display_id(cur)
        cur.execute("UPDATE manual_records SET display_id = %s WHERE manual_id = %s",
                    (display_id, manual_id))
        row = source_row({**rec, "display_id": display_id})
        columns = ("source", "sheet_row_id", "display_id", *_SOURCE_COLUMNS, "raw")
        cur.execute(
            f"INSERT INTO source_records ({', '.join(columns)}) "
            f"VALUES ({', '.join(f'%({c})s' for c in columns)}) RETURNING record_id::text AS rid",
            {**row, "display_id": display_id},
        )
        cur.execute("UPDATE manual_records SET source_record_id = %s WHERE manual_id = %s",
                    (cur.fetchone()["rid"], manual_id))
        return _resolve(cur, manual_id)
    row = source_row(rec)
    withdrawn = rec["status"] == "withdrawn"
    cur.execute(
        f"""
        UPDATE source_records
           SET {", ".join(f"{c} = %({c})s" for c in (*_SOURCE_COLUMNS, "raw"))},
               deleted_at = CASE WHEN %(withdrawn)s THEN COALESCE(deleted_at, NOW()) END,
               deleted_reason = CASE WHEN %(withdrawn)s THEN %(deleted_reason)s END,
               version = version + 1, updated_at = NOW()
         WHERE record_id = %(record_id)s
        """,
        {**row, "withdrawn": withdrawn, "record_id": rec["source_record_id"],
         "deleted_reason": (f"Withdrawn by {rec.get('withdrawn_by')}: {rec.get('withdrawn_reason')}"
                            if withdrawn else None)},
    )
    return rec


def _log(cur, rec: dict, admin: dict, action: str, changes: dict, note=None) -> None:
    cur.execute(
        """
        INSERT INTO manual_record_events
            (manual_id, display_id, admin_id, admin_handle, action, changes, note)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        """,
        (rec["manual_id"], rec.get("display_id"), admin.get("id"), admin["handle"], action,
         json.dumps(changes, default=str), note),
    )


def _rule_distinct(cur, rec: dict, admin: dict) -> list:
    """Rule the entry's source row 'distinct' when it shares identifiers with live
    rows: the admin said it is a different study. Returns the rows named. Only that
    row is ruled, so Source Records keeps the group open until someone rules on the
    others, as for any other source."""
    rows = transform_sources.load(cur)          # inside this write: never cached
    matches = _matches(rows, rec["source_record_id"])
    named = [m["display_id"] for m in matches["same_identifiers"] + matches["same_dois_one_link"]]
    if named and rec.get("duplicate_status") is None:
        source_records_service.resolve_duplicate(
            cur, rec["source_record_id"], "distinct", admin["handle"],
            note=("Kept separate when added by hand: a different study from "
                  + ", ".join(named))[:source_records_service.RULING_NOTE_LIMIT])
    return named


def create(cur, values: dict, note, keep_distinct: bool, admin: dict,
           client_token: str | None = None) -> dict:
    """Add an entry and project it. Returns {manual_id, display_id, repeat}.

    A repeated submit (the same client_token) returns the entry already saved. No
    duplicate is refused: one is absorbed into the record it matches, unless the
    admin keeps it apart. An exclusion is refused.
    """
    token = _text(client_token)
    if token:
        cur.execute("SELECT manual_id::text AS manual_id, display_id FROM manual_records "
                    "WHERE client_token = %s", (token,))
        row = cur.fetchone()
        if row:
            return {"manual_id": row["manual_id"], "display_id": row["display_id"], "repeat": True}
    v = clean(cur, values)
    text = clean_note(note)
    _refuse_excluded(cur, v)
    columns = (*FIELDS, "note", "keep_distinct", "client_token", "created_by_id", "created_by",
               "updated_by")
    cur.execute(
        f"INSERT INTO manual_records ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))}) "
        "ON CONFLICT (client_token) DO NOTHING RETURNING manual_id::text AS manual_id",
        [*(v[f] for f in FIELDS), text, False, token, admin.get("id"),
         admin["handle"], admin["handle"]],
    )
    row = cur.fetchone()
    if row is None:                     # the same submit, committed meanwhile
        cur.execute("SELECT manual_id::text AS manual_id, display_id FROM manual_records "
                    "WHERE client_token = %s", (token,))
        row = cur.fetchone()
        return {"manual_id": row["manual_id"], "display_id": row["display_id"], "repeat": True}
    rec = _project(cur, row["manual_id"])
    kept_from = _rule_distinct(cur, rec, admin) if keep_distinct else []
    # The flag records a ruling made, not the tick: with nothing to keep apart there is
    # none, and a later edit can still make one (update()).
    if kept_from:
        cur.execute("UPDATE manual_records SET keep_distinct = TRUE WHERE manual_id = %s",
                    (rec["manual_id"],))
        _project(cur, rec["manual_id"])
    _log(cur, rec, admin, "create", {f: [None, v[f]] for f in FIELDS if v[f] is not None},
         note=("Kept as its own record: a different study from " + ", ".join(kept_from))
         if kept_from else None)
    return {"manual_id": rec["manual_id"], "display_id": rec["display_id"], "repeat": False,
            "kept_from": kept_from}


def _check_version(rec: dict, version) -> None:
    if version is None or int(version) != int(rec["version"]):
        _refuse(409, "version_conflict", "This entry was changed by someone else. Reload it.",
                current_version=int(rec["version"]))


def _frozen(cur, rec: dict) -> None:
    """Refuse a change once the registry knows the entry under a FLoRA ID, its own or
    the one of the record it is absorbed into: that record has one place to correct
    it, its FLoRA editor."""
    record = _states(cur, [rec], titles=None)[rec["manual_id"]].get("frozen_by")
    if record:
        _refuse(409, "frozen", _frozen_text(record) + " A record has one place to correct "
                "it, so the entry stays as it is; withdrawing it is still possible.",
                flora_id=record)


def update(cur, ref, values: dict, note, version, admin: dict, keep_distinct: bool = False) -> dict:
    """Change an entry the registry does not know under a FLoRA ID yet (its own, or
    that of a record it is absorbed into), and project it again. A ruling made in
    Source Records stays with its row. Returns {manual_id, display_id, changed}."""
    rec = _resolve(cur, ref, lock=True)
    _check_version(rec, version)
    if rec["status"] != "active":
        _refuse(409, "withdrawn", "This entry is withdrawn. Restore it before changing it.")
    _frozen(cur, rec)
    if rec.get("duplicate_status") == "duplicate":
        _refuse(409, "ruled_duplicate", f"Its source row {rec['display_id']} is ruled a duplicate "
                f"of {rec.get('duplicate_of') or 'another row'} in Source Records, so a change "
                "would not reach FLoRA. Change the ruling there first.")
    v = clean(cur, values)
    text = clean_note(note)
    _refuse_excluded(cur, v)
    changes = {f: [rec.get(f), v[f]] for f in FIELDS if not _same(rec.get(f), v[f])}
    if not _same(rec.get("note"), text):
        changes["note"] = [rec.get("note"), text]
    # Ruled only while its row is unruled; a ruling made in Source Records stands.
    wants_kept = bool(keep_distinct) and rec.get("duplicate_status") is None
    if not changes and not wants_kept:
        return {"manual_id": rec["manual_id"], "display_id": rec["display_id"], "changed": False}
    if changes:
        cur.execute(
            f"""
            UPDATE manual_records
               SET {", ".join(f"{f} = %s" for f in FIELDS)}, note = %s,
                   updated_by = %s, updated_at = NOW(), version = version + 1
             WHERE manual_id = %s
            """,
            [*(v[f] for f in FIELDS), text, admin["handle"], rec["manual_id"]],
        )
        _project(cur, rec["manual_id"])
        rec = _resolve(cur, rec["manual_id"])
    kept_from = _rule_distinct(cur, rec, admin) if wants_kept else []
    if kept_from:
        changes["keep_distinct"] = [bool(rec.get("keep_distinct")), True]
        cur.execute(
            "UPDATE manual_records SET keep_distinct = TRUE, updated_by = %s, updated_at = NOW(), "
            "version = version + %s WHERE manual_id = %s",
            (admin["handle"], 0 if len(changes) > 1 else 1, rec["manual_id"]))
        _project(cur, rec["manual_id"])
        rec = _resolve(cur, rec["manual_id"])
    if not changes:
        return {"manual_id": rec["manual_id"], "display_id": rec["display_id"], "changed": False}
    moved = {"doi_o", "doi_r", "url_r", "type"} & set(changes)
    log_note = None
    if kept_from:
        log_note = "Kept as its own record: a different study from " + ", ".join(kept_from)
    elif moved and rec.get("duplicate_status") == "distinct":
        log_note = ("Identifiers changed; the 'distinct' ruling on its source row stays with "
                    "it (Source Records).")
    _log(cur, rec, admin, "edit", changes, note=log_note)
    return {"manual_id": rec["manual_id"], "display_id": rec["display_id"], "changed": True}


def _reason(reason, required: bool) -> str | None:
    text = _text(reason)
    if required and not text:
        _refuse(422, "reason_required", "Say why it is withdrawn.", "reason")
    if text and len(text) > REASON_LIMIT:
        _refuse(422, "too_long", f"The reason is longer than {REASON_LIMIT} characters.", "reason")
    return text


def withdraw(cur, ref, reason, version, admin: dict) -> dict:
    """Take an entry out of FLoRA: its source row is marked deleted, never removed.
    A FLoRA ID issued under it is retired at the next pipeline run; a restore brings
    the same row back, and with it that ID."""
    rec = _resolve(cur, ref, lock=True)
    _check_version(rec, version)
    if rec["status"] == "withdrawn":
        _refuse(409, "withdrawn", "This entry is already withdrawn.")
    text = _reason(reason, required=True)
    cur.execute(
        """
        UPDATE manual_records
           SET status = 'withdrawn', withdrawn_by = %s, withdrawn_at = NOW(),
               withdrawn_reason = %s, updated_by = %s, updated_at = NOW(),
               version = version + 1
         WHERE manual_id = %s
        """,
        (admin["handle"], text, admin["handle"], rec["manual_id"]),
    )
    rec = _project(cur, rec["manual_id"])
    rec = _resolve(cur, rec["manual_id"])
    _log(cur, rec, admin, "withdraw", {"status": ["active", "withdrawn"]}, note=text)
    return {"manual_id": rec["manual_id"], "display_id": rec["display_id"]}


def restore(cur, ref, note, version, admin: dict) -> dict:
    """Bring a withdrawn entry back, with the same source row and display id."""
    rec = _resolve(cur, ref, lock=True)
    _check_version(rec, version)
    if rec["status"] != "withdrawn":
        _refuse(409, "not_withdrawn", "This entry is not withdrawn.")
    text = _reason(note, required=False)
    _refuse_excluded(cur, rec)
    cur.execute(
        """
        UPDATE manual_records
           SET status = 'active', withdrawn_by = NULL, withdrawn_at = NULL,
               withdrawn_reason = NULL, updated_by = %s, updated_at = NOW(),
               version = version + 1
         WHERE manual_id = %s
        """,
        (admin["handle"], rec["manual_id"]),
    )
    rec = _project(cur, rec["manual_id"])
    rec = _resolve(cur, rec["manual_id"])
    _log(cur, rec, admin, "restore", {"status": ["withdrawn", "active"]}, note=text)
    return {"manual_id": rec["manual_id"], "display_id": rec["display_id"]}
