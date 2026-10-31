"""Additional originals: more than one original study for one replication.

The extractor codes one record per (replication, original) pair, and a validator
judges one of them at a time. When a paper replicates (or reproduces) an original
the extractor missed, the validator can suggest it with their judgement
("This study has more than one original"); an admin then adds it as a record of
its own — origin 'admin_extra_original', linked to the extractor record by
parent_record_id — or adds one by hand. See db_schema.sql, "Additional originals".

Why a record of its own: every path that re-publishes a record deletes its rows
in `validated` by record_id first, so a row sharing the parent's would vanish at
the parent's next approve or resolve. pair_id stays NULL, so the extractor sync,
which keys on pair_id, never sees one; it has no queue slots, so it is never
served and consensus never runs on it.

No FastAPI types here: app.py maps ExtraOriginalError to an HTTP response, and
consensus_engine.py can call the parent hooks without importing app.
"""
from __future__ import annotations

import json
import re

import psycopg2

ORIGIN_EXTRACTOR = "extractor"
ORIGIN_CHILD = "admin_extra_original"

# A judgement carries at most this many suggestions; further ones are ignored.
MAX_SUGGESTIONS = 10
# Longest stored text per suggestion field. Longer input is cut, not refused.
SUGGESTION_LIMITS = {"doi_or_url": 500, "title": 1000, "study": 50, "note": 1000}

_DOI_PREFIX = re.compile(r"(?i)^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*")
_HTTP_URL = re.compile(r"(?i)^https?://\S+$")


class ExtraOriginalError(Exception):
    """A refused request: an HTTP status and a detail (str or structured dict)."""

    def __init__(self, status: int, detail):
        super().__init__(detail if isinstance(detail, str) else detail.get("message"))
        self.status = status
        self.detail = detail


# ── Small normalisers ─────────────────────────────────────────────────────────

def text(value, limit: int | None = None) -> str:
    """Stripped text, '' for anything blank or not text. Cut to `limit`."""
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    # A NUL cannot be stored in PostgreSQL text; strip it so a pasted one can
    # never turn a judgement into a server error.
    out = str(value).replace(chr(0), "").strip()
    return out[:limit] if limit else out


def normalize_doi(value) -> str:
    """A bare DOI: no resolver prefix or 'doi:'. '' when blank. Case is kept;
    comparisons lower-case both sides, as DOI names are case-insensitive."""
    return _DOI_PREFIX.sub("", text(value)).strip()


def norm_title(value) -> str:
    """Letters and digits only, lower-cased: the comparison form of a title. Any
    script counts as letters: a title in Cyrillic or Chinese must not reduce to ''
    and look the same as every other one."""
    return re.sub(r"[\W_]+", "", text(value).lower())


def is_http_url(value) -> bool:
    return bool(_HTTP_URL.match(text(value)))


# ── Validator suggestions ─────────────────────────────────────────────────────

def normalize_suggestion(item) -> dict | None:
    """One suggestion as stored, or None when it names no paper.

    Lenient on purpose. Judgements are sent from a background queue that gives up
    on any 4xx (docs/app.js _isTerminalErr), so refusing a typo in this optional
    side field would throw the whole judgement away. A value that is neither a DOI
    nor an http(s) link is kept as text in the title; over-long text is cut.
    """
    if not isinstance(item, dict):
        return None
    raw = text(item.get("doi_or_url"), SUGGESTION_LIMITS["doi_or_url"])
    title = text(item.get("title"), SUGGESTION_LIMITS["title"])
    study = text(item.get("study"), SUGGESTION_LIMITS["study"])
    note = text(item.get("note"), SUGGESTION_LIMITS["note"])
    doi = url = ""
    if raw:
        bare = normalize_doi(raw)
        if bare.lower().startswith("10."):
            doi = bare
        elif is_http_url(raw):
            url = raw
        else:
            title = (f"{title} — {raw}" if title else raw)[:SUGGESTION_LIMITS["title"]]
    if not (doi or url or title):
        return None
    return {"doi_o": doi, "url_o": url, "title_o": title, "study_o": study, "note": note}


def suggestion_key(s: dict) -> str:
    """What makes two suggestions the same paper: the DOI, else the link, else
    the title — each compared case-insensitively."""
    if s.get("doi_o"):
        return "doi:" + s["doi_o"].lower()
    if s.get("url_o"):
        return "url:" + s["url_o"].lower().rstrip("/")
    return "title:" + norm_title(s.get("title_o"))


def store_suggestions(cur, record_id, queue_id, validator_id, validator_name,
                      items, judged_type) -> int:
    """Store a judgement's suggestions; return how many were kept.

    Never raises for the suggestions' sake: the inserts run under a savepoint, and
    a database error rolls back to it, dropping the suggestions but keeping the
    judgement the caller has already written in the same transaction. Nothing is
    stored for a "neither a replication nor a reproduction" judgement — it says
    the record has no original at all.
    """
    if judged_type == "not_validation" or not isinstance(items, list):
        return 0
    kept, seen = [], set()
    for item in items[:MAX_SUGGESTIONS]:
        s = normalize_suggestion(item)
        if s is None:
            continue
        key = suggestion_key(s)
        if key in seen:
            continue
        seen.add(key)
        kept.append(s)
    if not kept:
        return 0
    cur.execute("SAVEPOINT extra_original_suggestions")
    try:
        for position, s in enumerate(kept):
            cur.execute(
                """
                INSERT INTO extra_original_suggestions (
                    record_id, queue_id, validator_id, validator_name, position,
                    doi_o, url_o, title_o, study_o, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (str(record_id), str(queue_id) if queue_id else None, validator_id,
                 validator_name, position, s["doi_o"] or None, s["url_o"] or None,
                 s["title_o"] or None, s["study_o"] or None, s["note"] or None),
            )
    except (psycopg2.Error, ValueError) as exc:
        # ValueError: text psycopg2 refuses to encode (a lone surrogate from JSON).
        cur.execute("ROLLBACK TO SAVEPOINT extra_original_suggestions")
        print(f"[extra_originals] suggestions for {record_id} not stored: {exc}")
        return 0
    cur.execute("RELEASE SAVEPOINT extra_original_suggestions")
    return len(kept)


# ── The admin decision log ────────────────────────────────────────────────────

def log_decision(cur, record_id, admin: dict | None, action: str, changes: dict,
                 status_before: str | None = None, status_after: str | None = None) -> None:
    """One admin_decisions row with explicit, compact changes.

    app._log_decision diffs a record's published fields before and after a
    decision; for a record created from nothing that diff is every field, and for
    the parent it is nothing, so these actions say what happened directly.
    `admin` is None when a pipeline step (consensus) made the change.
    """
    admin = admin or {}
    cur.execute(
        """
        INSERT INTO admin_decisions (record_id, admin_id, admin_handle, action,
                                     status_before, status_after, changes)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (str(record_id), admin.get("id"), admin.get("handle") or "system", action,
         status_before, status_after, json.dumps(changes, default=str)),
    )


# ── What a record's family looks like (admin view) ────────────────────────────

# The replication side of a child: always its parent's published values.
REPLICATION_SIDE = ("doi_r", "title_r", "url_r", "abstract_r", "doi_r_published",
                    "alt_identifier_r", "oa_work_id_r")
# What an inheriting child takes from its parent besides the replication side.
OUTCOME_SIDE = ("type", "outcome", "outcome_quote", "out_quote_source",
                "outcome_computation", "outcome_computational_quote",
                "out_quote_computational_source", "outcome_robustness",
                "outcome_robustness_quote", "out_quote_robust_source")
# The parent's published row, as the admin form needs it.
_PUBLISHED_COLUMNS = ("doi_r", "study_r", "title_r", "year_r", "url_r", "ref_r",
                      "abstract_r", "doi_o", "study_o", "title_o", "original_key",
                      *OUTCOME_SIDE)


def published_row(cur, record_id) -> dict | None:
    cur.execute("SELECT * FROM validated WHERE record_id = %s", (str(record_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def parent_block(rec: dict, published: dict | None) -> tuple[str, str] | None:
    """Why originals cannot be added to this record now, or None when they can.

    An inherited outcome needs a published one, and an admin must have reviewed it:
    an auto-validated record can still be sent back for review, which would leave
    its added originals publishing an outcome nobody has confirmed.
    """
    if rec.get("origin") == ORIGIN_CHILD:
        return ("extra_original_on_child",
                "This is an added original. Add originals on the entry it belongs to.")
    if rec.get("validation_status") != "validated" or published is None:
        return ("parent_not_validated",
                "Approve or resolve this entry first. Its suggestions stay here until then.")
    if not rec.get("admin_checked"):
        return ("parent_not_admin_checked",
                "No admin has reviewed this entry yet (a rule validated it). "
                "Confirm it with Mark as Resolved first.")
    if published.get("type") not in ("replication", "reproduction"):
        return ("parent_type", "Only a replication or a reproduction can have originals added.")
    return None


def _family(cur, rec: dict) -> list[dict]:
    """Every other live record of this replication: extractor siblings (same
    replication DOI, compared case-insensitively) and added originals. A
    replication without a DOI has no identity to group on, so only its own added
    originals count; grouping on '' would pull in every DOI-less replication."""
    doi_r = text(rec.get("doi_r"))
    cur.execute(
        """
        SELECT u.record_id::text AS record_id, u.origin, u.validation_status,
               u.parent_record_id::text AS parent_record_id,
               COALESCE(u.final_doi_o, u.doi_o, '') AS doi_o,
               COALESCE(NULLIF(u.final_title_o, ''), u.title_o, '') AS title_o,
               COALESCE(u.study_o, '') AS study_o
        FROM unvalidated u
        WHERE u.validation_status <> 'rejected'
          AND u.record_id <> %s
          AND ((%s <> '' AND lower(u.doi_r) = lower(%s)) OR u.parent_record_id = %s)
        """,
        (str(rec["record_id"]), doi_r, doi_r, str(rec["record_id"])),
    )
    return [dict(r) for r in cur.fetchall()]


def same_original(a_doi, a_title, b_doi, b_title) -> bool:
    """One paper: equal DOIs (any case), or no DOI on either side and equal titles."""
    a_doi, b_doi = normalize_doi(a_doi).lower(), normalize_doi(b_doi).lower()
    if a_doi or b_doi:
        return a_doi == b_doi
    return bool(norm_title(a_title)) and norm_title(a_title) == norm_title(b_title)


def _row_key(row: dict) -> str:
    return suggestion_key({k: row.get(k) or "" for k in ("doi_o", "url_o", "title_o")})


def _group_suggestions(rows: list[dict]) -> list[dict]:
    """One entry per paper, with everyone who suggested it."""
    groups: dict[str, dict] = {}
    for r in rows:
        key = _row_key(r)
        g = groups.setdefault(key, {"key": key, "doi_o": "", "url_o": "", "title_o": "",
                                    "studies": [], "notes": [], "suggested_by": [],
                                    "statuses": set(), "resolved_record_id": None})
        for field in ("doi_o", "url_o", "title_o"):
            g[field] = g[field] or (r.get(field) or "")
        if r.get("study_o") and r["study_o"] not in g["studies"]:
            g["studies"].append(r["study_o"])
        if r.get("note") and r["note"] not in g["notes"]:
            g["notes"].append(r["note"])
        g["suggested_by"].append({"suggestion_id": r["suggestion_id"],
                                  "validator_id": r.get("validator_id"),
                                  "validator_name": r.get("validator_name"),
                                  "status": r["status"]})
        g["statuses"].add(r["status"])
        if r["status"] == "accepted" and not g["resolved_record_id"] and r.get("resolved_record_id"):
            g["resolved_record_id"] = str(r["resolved_record_id"])
    out = []
    for g in groups.values():
        statuses = g.pop("statuses")
        g["status"] = ("open" if "open" in statuses
                       else "accepted" if "accepted" in statuses else "dismissed")
        g["study_o"] = ", ".join(g.pop("studies"))
        g["open_suggestion_ids"] = [s["suggestion_id"] for s in g["suggested_by"]
                                    if s["status"] == "open"]
        out.append(g)
    return out


def read_view(cur, record_id) -> dict:
    """Everything the admin's "Additional originals" section shows for a record."""
    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s", (str(record_id),))
    row = cur.fetchone()
    if not row:
        raise ExtraOriginalError(404, "Record not found")
    rec = dict(row)
    published = published_row(cur, record_id)
    block = parent_block(rec, published)
    parent = {
        "record_id": str(rec["record_id"]), "origin": rec.get("origin"),
        "validation_status": rec.get("validation_status"),
        "admin_checked": bool(rec.get("admin_checked")),
        "type": (published or {}).get("type") or rec.get("final_type") or rec.get("type"),
        "can_add": block is None,
        "block_code": block[0] if block else None,
        "block_reason": block[1] if block else None,
        "published": ({k: published.get(k) for k in _PUBLISHED_COLUMNS} if published else None),
    }
    if rec.get("origin") == ORIGIN_CHILD:
        return {"parent": parent, "parent_record_id": str(rec["parent_record_id"]),
                "suggestions": [], "children": []}

    if published:
        own_doi, own_title = published.get("doi_o"), published.get("title_o")
    else:
        own_doi = rec["final_doi_o"] if rec.get("final_doi_o") is not None else rec.get("doi_o")
        own_title = rec.get("final_title_o") or rec.get("title_o")
    family = _family(cur, rec)

    cur.execute(
        "SELECT * FROM extra_original_suggestions WHERE record_id = %s "
        "ORDER BY created_at, position, suggestion_id",
        (str(record_id),),
    )
    suggestions = _group_suggestions([dict(r) for r in cur.fetchall()])
    for g in suggestions:
        g["is_own_original"] = same_original(g["doi_o"], g["title_o"], own_doi, own_title)
        match = next((f for f in family
                      if same_original(g["doi_o"], g["title_o"], f["doi_o"], f["title_o"])), None)
        g["coded_match"] = ({k: match[k] for k in ("record_id", "origin", "validation_status")}
                            if match else None)

    cur.execute(
        """
        SELECT record_id::text AS record_id, COALESCE(final_doi_o, doi_o) AS doi_o,
               COALESCE(NULLIF(final_title_o, ''), title_o) AS title_o, study_o, study_r,
               year_o, ref_o, url_o, inherits_outcome,
               COALESCE(final_type, type) AS type, COALESCE(final_outcome, outcome) AS outcome,
               validation_status, admin_name, created_at
        FROM unvalidated
        WHERE parent_record_id = %s AND validation_status <> 'rejected'
        ORDER BY created_at, record_id
        """,
        (str(record_id),),
    )
    children = []
    for r in cur.fetchall():
        child = dict(r)
        child["created_at"] = child["created_at"].isoformat() if child.get("created_at") else None
        children.append(child)
    return {"parent": parent, "parent_record_id": None,
            "suggestions": suggestions, "children": children}


def detail_summary(cur, record_id) -> dict:
    """The counts the entry detail shows before the section is opened. Suggestions
    are counted by paper, as the section lists them (two validators naming one
    DOI are one suggestion); this approximates suggestion_key in SQL."""
    cur.execute(
        """
        SELECT (SELECT COUNT(DISTINCT lower(COALESCE(NULLIF(doi_o, ''), NULLIF(url_o, ''), title_o)))
                FROM extra_original_suggestions
                WHERE record_id = %s AND status = 'open') AS open_suggestions,
               (SELECT COUNT(*) FROM unvalidated
                WHERE parent_record_id = %s AND validation_status <> 'rejected') AS children
        """,
        (str(record_id), str(record_id)),
    )
    row = cur.fetchone() or {}
    return {"open_suggestions": int(row.get("open_suggestions") or 0),
            "children": int(row.get("children") or 0)}


def dismiss_suggestion(cur, record_id, suggestion_id: int, admin: dict) -> int:
    """Dismiss a suggestion and every open one naming the same paper on this
    record (the admin sees them as one row). Returns how many were dismissed."""
    cur.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE", (str(record_id),))
    if not cur.fetchone():
        raise ExtraOriginalError(404, "Record not found")
    cur.execute(
        "SELECT * FROM extra_original_suggestions WHERE record_id = %s AND status = 'open'",
        (str(record_id),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    target = next((r for r in rows if r["suggestion_id"] == suggestion_id), None)
    if target is None:
        raise ExtraOriginalError(404, "No open suggestion with that id on this record")
    key = _row_key(target)
    ids = [r["suggestion_id"] for r in rows if _row_key(r) == key]
    cur.execute(
        """
        UPDATE extra_original_suggestions
           SET status = 'dismissed', resolved_by = %s, resolved_at = NOW()
         WHERE suggestion_id = ANY(%s) AND status = 'open'
        """,
        (admin.get("handle"), ids),
    )
    label = target.get("doi_o") or target.get("url_o") or target.get("title_o")
    log_decision(cur, record_id, admin, "suggestion_dismissed", {"suggestion": [label, None]})
    return len(ids)


# ── Adding originals ──────────────────────────────────────────────────────────

_YEAR = re.compile(r"^\d{4}$")
_WORK_ID = re.compile(r"^W\d+$")


def _refuse(status: int, code: str, message: str, index: int | None = None, **extra):
    detail = {"code": code, "message": message, **extra}
    if index is not None:
        detail["index"] = index
    raise ExtraOriginalError(status, detail)


def _original_key(v: dict) -> str:
    """The original's identity as validated.original_key computes it."""
    return text(v.get("doi_o")) or text(v.get("oa_work_id_o"))


def _child_values(index: int, item: dict, published: dict, admin: dict) -> dict:
    """One added original as stored, or a 422 naming the entry that is wrong.

    Every identity column is '' rather than NULL, as the import stores them: a
    NULL never equals anything in the unique key, so a duplicate would slip in.
    """
    from consensus_engine import quote_source_for
    from extractor_vocab import (REPLICATION_OUTCOMES, derive_reproduction_outcome,
                                 normalize_axis_value, normalize_outcome,
                                 normalize_quote_source)
    n = index + 1
    doi_o = normalize_doi(item.get("doi_o"))
    title_o = text(item.get("title_o"))
    ref_o = text(item.get("ref_o"))
    url_o = text(item.get("url_o"))
    year_o = text(item.get("year_o"))
    work_id = text(item.get("oa_work_id_o"))
    if not title_o:
        _refuse(422, "title_required", f"Original #{n} needs a title.", index)
    if doi_o and not doi_o.lower().startswith("10."):
        _refuse(422, "invalid_doi", f"Original #{n}: a DOI starts with 10.", index)
    if not doi_o and not ref_o:
        _refuse(422, "reference_required",
                f"Original #{n} has no DOI, so it needs a reference: FLoRA describes an "
                "original without a DOI by its reference alone.", index)
    if url_o and not is_http_url(url_o):
        _refuse(422, "invalid_url", f"Original #{n}: the link must start with http:// or https://.", index)
    if year_o and not _YEAR.match(year_o):
        _refuse(422, "invalid_year", f"Original #{n}: the year must be four digits.", index)
    if work_id and not _WORK_ID.match(work_id):
        _refuse(422, "invalid_work_id", f"Original #{n}: an OpenAlex id looks like W123456.", index)

    study_r = (text(item.get("study_r")) if item.get("study_r") is not None
               else text(published.get("study_r")))
    v = {
        "doi_r": text(published.get("doi_r")), "study_r": study_r,
        "title_r": text(published.get("title_r")),
        "year_r": published.get("year_r"), "url_r": published.get("url_r"),
        "ref_r": published.get("ref_r"), "abstract_r": published.get("abstract_r"),
        "doi_r_published": published.get("doi_r_published"),
        "alt_identifier_r": published.get("alt_identifier_r"),
        "oa_work_id_r": published.get("oa_work_id_r"),
        "doi_o": doi_o, "study_o": text(item.get("study_o")), "title_o": title_o,
        "year_o": year_o or None, "url_o": url_o or None, "ref_o": ref_o or None,
        "oa_work_id_o": work_id or None, "out_quote_source_by": None,
    }
    ptype = published.get("type")
    if not item.get("own_outcome"):
        v.update({col: published.get(col) for col in OUTCOME_SIDE})
        v["inherits_outcome"] = True
        return v

    v.update(dict.fromkeys(OUTCOME_SIDE[1:]))
    v["type"], v["inherits_outcome"] = ptype, False
    explicit = []

    def source(quote, requested):
        chosen = normalize_quote_source(requested)
        if chosen:
            explicit.append(chosen)
            return chosen
        return quote_source_for(quote, v["abstract_r"]) if quote else None

    if ptype == "reproduction":
        try:
            computation = normalize_axis_value("outcome_computation", item.get("outcome_computation"))
            robustness = normalize_axis_value("outcome_robustness", item.get("outcome_robustness"))
        except ValueError as exc:
            _refuse(422, "invalid_outcome", f"Original #{n}: {exc}", index)
        if not computation or not robustness:
            _refuse(422, "outcome_required",
                    f"Original #{n}: choose both reproduction verdicts, or let it inherit the entry's.", index)
        comp_quote = text(item.get("outcome_computational_quote")) or None
        rob_quote = text(item.get("outcome_robustness_quote")) or None
        v.update({
            "outcome": derive_reproduction_outcome(computation, robustness),
            "outcome_computation": computation, "outcome_computational_quote": comp_quote,
            "out_quote_computational_source": source(comp_quote, item.get("out_quote_computational_source")),
            "outcome_robustness": robustness, "outcome_robustness_quote": rob_quote,
            "out_quote_robust_source": source(rob_quote, item.get("out_quote_robust_source")),
        })
    else:
        outcome = normalize_outcome(item.get("outcome"))
        if outcome not in REPLICATION_OUTCOMES or outcome == "not_a_replication":
            _refuse(422, "outcome_required",
                    f"Original #{n}: choose an outcome, or let it inherit the entry's.", index)
        quote = text(item.get("outcome_quote")) or None
        v.update({"outcome": outcome, "outcome_quote": quote,
                  "out_quote_source": source(quote, item.get("out_quote_source"))})
    if explicit:
        v["out_quote_source_by"] = admin.get("handle")
    return v


def _published_match(cur, v: dict, exclude_record_id=None) -> dict | None:
    """A published row for the same replication study and the same original
    study, compared case-insensitively. The unique key compares exactly, so
    '10.1/ABC' and '10.1/abc' would both get in. Without a DOI on either side
    the title stands in, as the key's own fallback does."""
    rep = ("lower(v.doi_r) = lower(%s)" if v["doi_r"]
           else "COALESCE(v.doi_r, '') = '' AND lower(COALESCE(v.title_r, '')) = lower(%s)")
    key = _original_key(v)
    orig = ("lower(v.original_key) = lower(%s)" if key
            else "COALESCE(v.original_key, '') = '' AND lower(COALESCE(v.title_o, '')) = lower(%s)")
    cur.execute(
        f"""
        SELECT v.record_id::text AS record_id, u.origin, u.parent_record_id::text AS parent_record_id
        FROM validated v
        LEFT JOIN unvalidated u ON u.record_id = v.record_id
        WHERE {rep} AND COALESCE(v.study_r, '') = %s
          AND {orig} AND COALESCE(v.study_o, '') = %s
          AND v.record_id IS DISTINCT FROM %s
        LIMIT 1
        """,
        (v["doi_r"] or v["title_r"], v["study_r"], key or v["title_o"], v["study_o"],
         str(exclude_record_id) if exclude_record_id else None),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _pending_sibling(cur, parent: dict, v: dict) -> dict | None:
    """An extractor record of this replication, still in validation, coding the
    same original. Its own validation will publish it; adding it here as well
    would make the two collide. A replication without a DOI has no siblings."""
    if not v["doi_r"]:
        return None
    if v["doi_o"]:
        orig, value = "lower(COALESCE(u.final_doi_o, u.doi_o, '')) = lower(%s)", v["doi_o"]
    else:
        orig = ("COALESCE(u.final_doi_o, u.doi_o, '') = '' AND "
                "lower(COALESCE(NULLIF(u.final_title_o, ''), u.title_o, '')) = lower(%s)")
        value = v["title_o"]
    cur.execute(
        f"""
        SELECT u.record_id::text AS record_id, u.validation_status
        FROM unvalidated u
        WHERE lower(u.doi_r) = lower(%s)
          AND u.origin = 'extractor'
          AND u.validation_status NOT IN ('rejected', 'validated')
          AND u.record_id <> %s
          AND {orig}
          AND (COALESCE(u.study_o, '') = %s OR COALESCE(u.study_o, '') = '' OR %s = '')
        LIMIT 1
        """,
        (v["doi_r"], str(parent["record_id"]), value, v["study_o"], v["study_o"]),
    )
    row = cur.fetchone()
    return dict(row) if row else None


_UNVALIDATED_PAIRS = (
    # (unvalidated raw column, its final_* column or None)
    ("doi_r", "final_doi_r"), ("title_r", "final_title_r"), ("url_r", "final_url_r"),
    ("abstract_r", "final_abstract_r"), ("doi_r_published", None), ("alt_identifier_r", None),
    ("type", "final_type"), ("outcome", "final_outcome"),
    ("outcome_quote", "final_outcome_quote"), ("out_quote_source", "final_out_quote_source"),
    ("outcome_computation", "final_outcome_computation"),
    ("outcome_computational_quote", "final_computational_quote"),
    ("out_quote_computational_source", "final_computational_source"),
    ("outcome_robustness", "final_outcome_robustness"),
    ("outcome_robustness_quote", "final_robustness_quote"),
    ("out_quote_robust_source", "final_robustness_source"),
)


def _insert_child(cur, child_id: str, parent_id: str, v: dict, admin: dict) -> bool:
    """Both rows of a new added original. False when its identity was taken
    between the checks and the insert (another admin, same moment)."""
    columns = {
        "record_id": child_id, "origin": ORIGIN_CHILD, "parent_record_id": parent_id,
        "inherits_outcome": v["inherits_outcome"],
        "study_r": v["study_r"], "year_r": v["year_r"], "ref_r": v["ref_r"],
        "doi_o": v["doi_o"], "study_o": v["study_o"], "title_o": v["title_o"],
        "year_o": v["year_o"], "url_o": v["url_o"], "ref_o": v["ref_o"],
        "oa_work_id_o": v["oa_work_id_o"], "oa_work_id_r": v["oa_work_id_r"],
        "final_doi_o": v["doi_o"], "final_title_o": v["title_o"],
        "validation_status": "validated", "admin_checked": True,
        "admin_name": admin.get("handle"), "out_quote_source_by": v["out_quote_source_by"],
    }
    for raw, final in _UNVALIDATED_PAIRS:
        columns[raw] = v[raw]
        if final:
            columns[final] = v[raw]
    cur.execute(
        f"INSERT INTO unvalidated ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})",
        list(columns.values()),
    )
    published = ("doi_r", "study_r", "title_r", "year_r", "url_r", "ref_r", "abstract_r",
                 "doi_o", "study_o", "title_o", "year_o", "url_o", "ref_o",
                 "oa_work_id_o", "oa_work_id_r", *OUTCOME_SIDE, "out_quote_source_by",
                 "doi_r_published", "alt_identifier_r")
    cur.execute(
        f"""
        INSERT INTO validated (record_id, {', '.join(published)}, admin_approved)
        VALUES (%s, {', '.join(['%s'] * len(published))}, TRUE)
        ON CONFLICT (doi_r, study_r, title_r, original_key, study_o, title_o) DO NOTHING
        RETURNING record_id
        """,
        [child_id, *(v[c] for c in published)],
    )
    return cur.fetchone() is not None


def create_children(cur, parent_id, items: list[dict], admin: dict) -> list[dict]:
    """Add originals to a published, admin-reviewed record: one new record each,
    with its row in validated. All of them or none; the first problem is refused
    with the index of the entry it concerns."""
    import uuid

    parent_id = str(parent_id)
    # The parent is locked first, before anything that touches its added originals.
    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE", (parent_id,))
    row = cur.fetchone()
    if not row:
        raise ExtraOriginalError(404, "Record not found")
    parent = dict(row)
    published = published_row(cur, parent_id)
    block = parent_block(parent, published)
    if block:
        _refuse(409, block[0], block[1])

    # An admin may not turn their own suggestion (as a validator) into a record:
    # that would be deciding their own judgement. Adding by hand is open to anyone.
    wanted = [int(s) for item in items for s in (item.get("from_suggestion_ids") or [])]
    if wanted:
        cur.execute(
            "SELECT suggestion_id, validator_id, status FROM extra_original_suggestions "
            "WHERE record_id = %s AND suggestion_id = ANY(%s)",
            (parent_id, wanted),
        )
        found = {r["suggestion_id"]: dict(r) for r in cur.fetchall()}
        if set(wanted) - set(found):
            _refuse(422, "unknown_suggestion", "A suggestion named here is not on this entry.")
        own = admin.get("validator_id")
        if own and any(s["validator_id"] == own for s in found.values()):
            _refuse(403, "own_suggestion",
                    "You suggested this original as a validator, so another admin needs to add it.")

    values = [_child_values(i, item, published, admin) for i, item in enumerate(items)]
    own_key = (text(published.get("original_key")).lower()
               or "title:" + text(published.get("title_o")).lower(),
               text(published.get("study_o")))
    # FLoRA's build makes one record of rows sharing type | doi_o | doi_r | url_r
    # (transform_sources.dedup_keys), and an added original carries its entry's type
    # and replication. Two originals without a DOI would publish as one, the later
    # row's values replacing the other's. A duplicate ruling can separate them only
    # after the nightly sync has published the merged record, so a DOI-less original
    # is refused while this replication already has one.
    doi_less = [("this entry's own original", parent_id)] if not text(published.get("doi_o")) else []
    doi_less += [(f"the original of entry {r['record_id']}", r["record_id"])
                 for r in _family(cur, parent) if not text(r["doi_o"])]
    seen = set()
    for i, v in enumerate(values):
        n = i + 1
        ident = (_original_key(v).lower() or "title:" + v["title_o"].lower(), v["study_o"])
        if ident == own_key and v["study_r"] == text(published.get("study_r")):
            _refuse(409, "extra_original_is_own",
                    f"Original #{n} is this entry's own original (same study).", i)
        if (ident, v["study_r"]) in seen:
            _refuse(422, "duplicate_in_batch", f"Original #{n} repeats an earlier one.", i)
        seen.add((ident, v["study_r"]))
        hit = _published_match(cur, v)
        if hit:
            whose = ("an original already added to this entry"
                     if hit.get("parent_record_id") == parent_id
                     else "an original already published for this replication")
            _refuse(409, "extra_original_published", f"Original #{n} is {whose}.", i,
                    existing_record_id=hit["record_id"])
        sibling = _pending_sibling(cur, parent, v)
        if sibling:
            _refuse(409, "extra_original_coded",
                    f"Original #{n} is already coded for this replication by the extractor "
                    f"(entry {sibling['record_id']}, {sibling['validation_status']}). "
                    "Validate that entry instead.", i, existing_record_id=sibling["record_id"])
        if not v["doi_o"]:
            if doi_less:
                whose, existing = doi_less[0]
                _refuse(409, "extra_original_no_doi",
                        f"Original #{n} has no DOI, and neither does {whose}. FLoRA would "
                        "publish the two as one record, so an original without a DOI "
                        "cannot be added to this replication.", i,
                        **({"existing_record_id": existing} if existing else {}))
            doi_less.append((f"original #{n}", None))

    created = []
    for i, (item, v) in enumerate(zip(items, values)):
        child_id = str(uuid.uuid4())
        if not _insert_child(cur, child_id, parent_id, v, admin):
            _refuse(409, "extra_original_published",
                    f"Original #{i + 1} was published by someone else a moment ago.", i)
        ids = [int(s) for s in (item.get("from_suggestion_ids") or [])]
        if ids:
            cur.execute(
                """
                UPDATE extra_original_suggestions
                   SET status = 'accepted', resolved_record_id = %s,
                       resolved_by = %s, resolved_at = NOW()
                 WHERE record_id = %s AND suggestion_id = ANY(%s) AND status = 'open'
                """,
                (child_id, admin.get("handle"), parent_id, ids),
            )
        label = v["doi_o"] or v["title_o"]
        log_decision(cur, child_id, admin, "extra_original_added",
                     {"parent_record_id": [None, parent_id], "original": [None, label],
                      "outcome": [None, "inherited" if v["inherits_outcome"] else v["outcome"]]},
                     None, "validated")
        log_decision(cur, parent_id, admin, "extra_original_added",
                     {"extra_original": [None, child_id], "original": [None, label]})
        created.append({"record_id": child_id, "doi_o": v["doi_o"], "title_o": v["title_o"]})
    return created


# ── Keeping added originals in step with their entry ──────────────────────────

def may_have_children(record: dict | None) -> bool:
    """Whether a record can have added originals at all: only an extractor record
    an admin has reviewed is given any (parent_block), and nothing clears
    admin_checked. Lets consensus skip the lookups for every other record."""
    return bool(record and record.get("admin_checked")
                and record.get("origin", ORIGIN_EXTRACTOR) == ORIGIN_EXTRACTOR)


def _same(a, b) -> bool:
    return (a if a is not None else "") == (b if b is not None else "")


def sync_children(cur, parent_id, admin: dict | None = None, strict: bool = True) -> dict:
    """Bring a record's added originals in line with its published row.

    Called after the parent's row in validated is written. The replication side
    always follows; type always follows (an outcome of the wrong shape cannot
    stand, so an own-outcome child whose type no longer matches inherits again);
    the outcome follows where the child inherits it. Only columns that differ are
    written. When a child's identity would move onto a row that is already
    published, `strict` refuses the whole change (the admin decides); otherwise
    that child is skipped and the rest are updated.
    """
    parent_id = str(parent_id)
    published = published_row(cur, parent_id)
    if not published:
        return {"updated": [], "skipped": []}
    cur.execute(
        "SELECT * FROM unvalidated WHERE parent_record_id = %s AND validation_status = 'validated' "
        "ORDER BY record_id FOR UPDATE",
        (parent_id,),
    )
    children = [dict(r) for r in cur.fetchall()]
    plan, skipped = [], []
    for child in children:
        current = published_row(cur, child["record_id"])
        if current is None:
            continue
        desired = {col: published.get(col) for col in REPLICATION_SIDE}
        desired["doi_r"], desired["title_r"] = text(published.get("doi_r")), text(published.get("title_r"))
        type_moved = not _same(current.get("type"), published.get("type"))
        convert = type_moved and not child.get("inherits_outcome")
        if child.get("inherits_outcome") or type_moved:
            desired.update({col: published.get(col) for col in OUTCOME_SIDE})
        changes = {col: [current.get(col), val] for col, val in desired.items()
                   if not _same(current.get(col), val)}
        if not changes:
            continue
        if "doi_r" in changes or "title_r" in changes:
            moved = {**current, "doi_r": desired["doi_r"], "title_r": desired["title_r"]}
            moved = {k: text(moved.get(k)) for k in ("doi_r", "title_r", "study_r", "doi_o",
                                                      "study_o", "title_o")} | {
                "oa_work_id_o": current.get("oa_work_id_o")}
            hit = _published_match(cur, moved, exclude_record_id=child["record_id"])
            if hit:
                skipped.append({"record_id": str(child["record_id"]),
                                "title_o": current.get("title_o"),
                                "conflicts_with": hit["record_id"]})
                continue
        plan.append((child, changes, convert))
    if skipped and strict:
        _refuse(409, "children_identity_conflict",
                "Saving this would give an added original of this entry the same identity "
                "as a record that is already published. Remove or correct that added original "
                "first; nothing was saved.", children=skipped)

    updated = []
    for child, changes, convert in plan:
        child_id = str(child["record_id"])
        cur.execute(
            f"UPDATE validated SET {', '.join(f'{c} = %s' for c in changes)} WHERE record_id = %s",
            [new for _, new in changes.values()] + [child_id],
        )
        sets, params = [], []
        for raw, final in _UNVALIDATED_PAIRS:
            if raw in changes:
                for col in (raw, final) if final else (raw,):
                    sets.append(f"{col} = %s")
                    params.append(changes[raw][1])
        if convert:
            sets.append("inherits_outcome = TRUE")
        cur.execute(
            f"UPDATE unvalidated SET {', '.join(sets + ['updated_at = NOW()'])} WHERE record_id = %s",
            params + [child_id],
        )
        if "oa_work_id_r" in changes:
            # After the DOI trigger, which clears a work id whose DOI just changed.
            cur.execute("UPDATE unvalidated SET oa_work_id_r = %s WHERE record_id = %s",
                        (changes["oa_work_id_r"][1], child_id))
        log_changes = dict(changes)
        if convert:
            log_changes["reason"] = ["own outcome", "inherited: the entry's type changed"]
        log_decision(cur, child_id, admin, "inherited_update", log_changes)
        updated.append(child_id)
    for s in skipped:
        print(f"[extra_originals] {s['record_id']} not updated from {parent_id}: "
              f"its identity would collide with {s['conflicts_with']}")
    return {"updated": updated, "skipped": skipped}


def reject_children(cur, parent_id, admin: dict | None = None) -> int:
    """The entry is not a replication or reproduction after all, so neither are
    its added originals: they leave FLoRA with it."""
    cur.execute(
        "SELECT record_id::text AS record_id, validation_status FROM unvalidated "
        "WHERE parent_record_id = %s AND validation_status <> 'rejected' "
        "ORDER BY record_id FOR UPDATE",
        (str(parent_id),),
    )
    children = [dict(r) for r in cur.fetchall()]
    for child in children:
        cur.execute("DELETE FROM validated WHERE record_id = %s", (child["record_id"],))
        cur.execute(
            "UPDATE unvalidated SET validation_status = 'rejected', updated_at = NOW() "
            "WHERE record_id = %s",
            (child["record_id"],),
        )
        log_decision(cur, child["record_id"], admin, "rejected_with_parent",
                     {"parent_record_id": [str(parent_id), str(parent_id)]},
                     child["validation_status"], "rejected")
    return len(children)


def repoint_children(cur, duplicate_id, survivor_id, admin: dict) -> int:
    """A merged duplicate's added originals belong to the survivor, which has the
    same replication and original; so do its open suggestions."""
    cur.execute(
        "UPDATE unvalidated SET parent_record_id = %s, updated_at = NOW() "
        "WHERE parent_record_id = %s RETURNING record_id::text AS record_id",
        (str(survivor_id), str(duplicate_id)),
    )
    moved = [r["record_id"] for r in cur.fetchall()]
    cur.execute(
        "UPDATE extra_original_suggestions SET record_id = %s "
        "WHERE record_id = %s AND status = 'open'",
        (str(survivor_id), str(duplicate_id)),
    )
    if moved:
        log_decision(cur, survivor_id, admin, "extra_originals_moved",
                     {"from": [str(duplicate_id), str(survivor_id)], "records": [None, moved]})
        sync_children(cur, survivor_id, admin=admin, strict=False)
    return len(moved)


def _reopen_suggestions(cur, child_id) -> None:
    cur.execute(
        """
        UPDATE extra_original_suggestions
           SET status = 'open', resolved_record_id = NULL, resolved_by = NULL, resolved_at = NULL
         WHERE resolved_record_id = %s AND status = 'accepted'
        """,
        (str(child_id),),
    )


def child_left(cur, child: dict, admin: dict) -> None:
    """An added original was taken out of FLoRA (removed, or rejected through
    Resolve): its suggestions are open again and its entry's log says so."""
    _reopen_suggestions(cur, child["record_id"])
    log_decision(cur, child["parent_record_id"], admin, "extra_original_removed",
                 {"extra_original": [str(child["record_id"]), None]})


def remove_child(cur, child_id, admin: dict) -> None:
    cur.execute("SELECT parent_record_id::text AS parent_record_id, origin FROM unvalidated "
                "WHERE record_id = %s", (str(child_id),))
    row = cur.fetchone()
    if not row:
        raise ExtraOriginalError(404, "Record not found")
    if row["origin"] != ORIGIN_CHILD:
        _refuse(409, "not_an_added_original", "Only an added original can be removed this way.")
    # Parent first, then the child: the same order as every other path.
    cur.execute("SELECT 1 FROM unvalidated WHERE record_id = %s FOR UPDATE", (row["parent_record_id"],))
    cur.execute("SELECT * FROM unvalidated WHERE record_id = %s FOR UPDATE", (str(child_id),))
    child = dict(cur.fetchone())
    if child["validation_status"] == "rejected":
        _refuse(409, "already_removed", "This added original was already removed.")
    cur.execute("DELETE FROM validated WHERE record_id = %s", (str(child_id),))
    cur.execute(
        "UPDATE unvalidated SET validation_status = 'rejected', admin_name = %s, "
        "updated_at = NOW() WHERE record_id = %s",
        (admin.get("handle"), str(child_id)),
    )
    log_decision(cur, child_id, admin, "extra_original_removed", {},
                 child["validation_status"], "rejected")
    child_left(cur, child, admin)


# ── Guards for approve and resolve ────────────────────────────────────────────

def child_conflict_detail(cur, conflict: dict | None) -> dict | None:
    """When approving or resolving would collide with an added original, say so
    instead of offering the duplicate merge: merging into an added original would
    make an extractor record the child of a child."""
    if not conflict:
        return None
    cur.execute("SELECT origin, parent_record_id::text AS parent_record_id FROM unvalidated "
                "WHERE record_id = %s", (str(conflict["record_id"]),))
    row = cur.fetchone()
    if not row or row["origin"] != ORIGIN_CHILD:
        return None
    return {
        "code": "conflicts_with_added_original",
        "message": ("This matches an original an admin added to entry "
                    f"{row['parent_record_id']}. Remove that added original first, "
                    "or correct this entry's original."),
        "added_record_id": str(conflict["record_id"]),
        "parent_record_id": row["parent_record_id"],
    }


# What resolving an added original may not change: it takes these from its entry.
_CHILD_FIXED_CORRECTIONS = ("corrected_title_r", "corrected_doi_r", "corrected_url_r",
                            "corrected_abstract_r", "doi_r_published", "alt_identifier_r")


def check_child_resolve(cur, child: dict, corrections: dict, final_type: str) -> None:
    """Resolve on an added original edits only its original and outcome."""
    if child.get("validation_status") == "rejected" and final_type != "not_validation":
        # Removed (or excluded with its entry): resolving would publish it again,
        # with stale replication details, perhaps under an excluded entry.
        _refuse(409, "child_removed",
                "This added original was removed. Add it again on its entry instead.")
    changed = [k for k in _CHILD_FIXED_CORRECTIONS if corrections.get(k) is not None]
    if changed:
        _refuse(400, "child_replication_fixed",
                "An added original takes the replication's details from its entry. "
                "Change them on that entry instead.", fields=changed)
    if final_type == "not_validation":
        return
    parent = published_row(cur, child["parent_record_id"]) or {}
    if parent.get("type") and final_type != parent["type"]:
        _refuse(400, "child_type_fixed",
                "An added original takes its type from its entry. Change the type there instead.")


def after_child_resolved(cur, child_id, before: dict | None, admin: dict) -> None:
    """An admin resolved an added original by hand. If they changed its outcome,
    it keeps that outcome from now on rather than following its entry's."""
    after = published_row(cur, child_id) or {}
    if not before or all(_same(before.get(c), after.get(c)) for c in OUTCOME_SIDE[1:]):
        return
    cur.execute("UPDATE unvalidated SET inherits_outcome = FALSE "
                "WHERE record_id = %s AND inherits_outcome", (str(child_id),))
    if cur.rowcount:
        log_decision(cur, child_id, admin, "own_outcome_set", {"inherits_outcome": [True, False]})
