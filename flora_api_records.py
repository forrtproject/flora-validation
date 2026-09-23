"""Pure DOI-centric public views of stored FLoRA relationships.

One published FLoRA ID identifies a relationship, not a DOI. Several relationships
can share both DOIs, so their identities stay on the relationship entries and are
never deduplicated by paper pair. No network, database, or cache side effects.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re


BIB_FIELDS = ("title", "authors", "journal", "year", "volume", "issue", "pages",
              "apa_ref", "bibtex_ref", "url")
SIDE_EXTRAS = ("abstract", "language", "oa_url", "oa_work_id")
RELATIONSHIP_EXTRAS = (
    "source", "outcome_computation", "outcome_computational_quote",
    "out_quote_computational_source", "outcome_robustness",
    "outcome_robustness_quote", "out_quote_robust_source", "author_overlap", "author_overlap_pct",
)
DERIVED_KEYS = ("outcome_mix", "replication_year_counts", "first_replication_year",
                "first_replication_outcome")
CITATION_KEYS = ("citation_timeline", "n_citations")
_MISSING = {"", "NA", "N/A", "NULL", "None", "nan"}


def _jsonable(value):
    """Parse exported JSON cells, preserving ordinary text and valid JSON types."""
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        value = html.unescape(value.strip())
        if value in _MISSING:
            return None
        if value.startswith(("[", "{")):
            try:
                return _jsonable(json.loads(value))
            except (ValueError, TypeError):
                pass
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (int, bool)):
        return value
    return str(value)


def _text(value):
    value = _jsonable(value)
    return "" if value is None else str(value).strip()


def normalize_doi(value):
    """Canonical DOI key; blank/missing values never become paper records."""
    value = _text(value).lower()
    return re.sub(r"^(?:(?:https?://)?(?:dx\.)?doi\.org/|doi:\s*)", "", value).strip()


def author_names(value):
    """Accept structured/JSON authors, names, and comma-separated plain text."""
    value = _jsonable(value)
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return [name.strip() for name in str(value).split(",") if name.strip()]
    names = []
    for author in value:
        if isinstance(author, dict):
            name = " ".join(filter(None, (_text(author.get("given")), _text(author.get("family")))))
            name = name or _text(author.get("name")) or _text(author.get("literal"))
        else:
            name = _text(author)
        if name:
            names.append(name)
    return names


def _citation_fields(row, side):
    """Only explicit paper-specific citation data can populate these fields."""
    out = {}
    timeline = _jsonable(row.get(f"citation_timeline_{side}"))
    if timeline is not None:
        out["citation_timeline"] = timeline
    count = _jsonable(row.get(f"n_citations_{side}"))
    if count is not None and not isinstance(count, bool):
        try:
            number = float(count)
            if math.isfinite(number) and number.is_integer() and number >= 0:
                out["n_citations"] = int(number)
        except (ValueError, TypeError, OverflowError):
            pass
    return out


def _entry(row, side):
    doi = normalize_doi(row.get(f"doi_{side}"))
    short_hash = _text(row.get(f"doi_{side}_hash")).lower()
    out = {
        "id": _jsonable(row.get("id")),
        "id_md5": _jsonable(row.get("id_md5")),
        "doi": doi or None,
        "doi_hash": short_hash or (hashlib.md5(doi.encode("utf-8")).hexdigest()[:3] if doi else None),
    }
    for field in BIB_FIELDS:
        column = "author" if field == "authors" else field
        out[field] = _jsonable(row.get(f"{column}_{side}"))
    for field in ("volume", "issue", "pages"):
        out[field] = _text(out[field]) or None
    for field in SIDE_EXTRAS:
        column = f"{field}_{side}"
        if column in row:
            out[field] = _jsonable(row[column])
    if side == "r":
        out["type"] = _text(row.get("type")).lower()
        for field in ("outcome", "outcome_quote", "outcome_quote_source"):
            out[field] = _jsonable(row.get(field))
        for field in RELATIONSHIP_EXTRAS:
            if field in row:
                out[field] = _jsonable(row[field])
    return out


def _new_record(doi):
    return {
        "doi": doi, "types": [], "doi_hash": None,
        **{field: None for field in BIB_FIELDS},
        "record": {"stats": {}, "originals": [], "replications": [], "reproductions": []},
    }


def _missing(value):
    return value is None or value == "" or value == [] or value == {}


def _merge(record, entry, citations):
    for field in ("doi_hash", *BIB_FIELDS):
        if _missing(record.get(field)) and not _missing(entry.get(field)):
            record[field] = copy.deepcopy(entry[field])
    for field, value in citations.items():
        if _missing(record.get(field)):
            record[field] = copy.deepcopy(value)


def build_records(rows):
    """Aggregate flattened prepared rows in input order without dropping IDs."""
    records = {}
    for row in rows:
        original, replication = _entry(row, "o"), _entry(row, "r")
        doi_o, doi_r = original["doi"], replication["doi"]
        rep_type = replication["type"]
        is_reproduction = (rep_type.startswith("reproduction")
                           or rep_type.startswith("computational reproduction"))
        role = "reproduction" if is_reproduction else "replication"
        if doi_o:
            record_o = records.setdefault(doi_o, _new_record(doi_o))
            if "original" not in record_o["types"]:
                record_o["types"].append("original")
            _merge(record_o, original, _citation_fields(row, "o"))
            record_o["record"][role + "s"].append(replication)
        if doi_r:
            record_r = records.setdefault(doi_r, _new_record(doi_r))
            if role not in record_r["types"]:
                record_r["types"].append(role)
            _merge(record_r, replication, _citation_fields(row, "r"))
            # Even an original without a DOI is a published relationship whose
            # permanent row ID must remain reachable from the replication DOI.
            record_r["record"]["originals"].append(original)
    for record in records.values():
        record["types"] = [role for role in ("original", "replication", "reproduction")
                           if role in record["types"]]
        nested = record["record"]
        reps, repros, originals = (nested[key] for key in ("replications", "reproductions", "originals"))
        reps_with_doi = sum(bool(item.get("doi")) for item in reps)
        repros_with_doi = sum(bool(item.get("doi")) for item in repros)
        nested["stats"] = {
            "n_replications_total": len(reps),
            "n_replications_with_doi": reps_with_doi,
            "n_replications_only": len(reps) - reps_with_doi,
            "n_unique_replication_dois": len({item["doi"] for item in reps if item.get("doi")}),
            "n_reproductions_total": len(repros),
            "n_reproductions_with_doi": repros_with_doi,
            "n_reproductions_only": len(repros) - repros_with_doi,
            "n_originals_total": len(originals),
            "n_unique_original_dois": len({item["doi"] for item in originals if item.get("doi")}),
        }
    return records


def _year(value):
    # Match JavaScript parseInt(String(year), 10), including an annotated year.
    match = re.match(r"^[+-]?\d+", _text(value))
    return int(match.group()) if match else None


def serialize_record(rec, include_derived=True):
    """Return an independent safe response with derived fields only at the top."""
    out = _jsonable(copy.deepcopy(rec))
    nested = out.get("record")
    if not isinstance(nested, dict):
        nested = {}
        out["record"] = nested
    citations = {}
    for key in CITATION_KEYS:
        value = out.get(key)
        if value is None:
            value = nested.get(key)
        if value is not None:
            citations[key] = copy.deepcopy(value)
    for key in (*DERIVED_KEYS, *CITATION_KEYS):
        out.pop(key, None)
        nested.pop(key, None)
    if not include_derived:
        return out
    outcomes, years = {}, {}
    first_year, first_outcome = None, None
    for rep in nested.get("replications") or []:
        outcome = rep.get("outcome")
        if isinstance(outcome, str) and outcome:
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        year = _year(rep.get("year"))
        if year is not None:
            years[str(year)] = years.get(str(year), 0) + 1
            if first_year is None or year < first_year:
                first_year, first_outcome = year, outcome
    out.update(outcome_mix=outcomes, replication_year_counts=years,
               first_replication_year=str(first_year) if first_year is not None else None,
               first_replication_outcome=first_outcome, **citations)
    return out
