"""Pure search over prepared DOI aggregates with the legacy response envelope.

Ranking uses deterministic Python word similarity (minimum 0.85), not Fuse.js's
scoring algorithm. All filters and one-hop relationship expansion are applied
before pagination, so total and hasMore describe the results actually available.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from fnmatch import fnmatchcase
import re

from flora_api_records import author_names, normalize_doi, serialize_record

YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
WORDS = re.compile(r"\w+(?:['’-]\w+)*", re.UNICODE)
DOI = re.compile(r"^10\.\d+/\S+$", re.IGNORECASE)
PAPER_TYPES = {"original", "replication", "reproduction"}


def _strings(value, name, *, comma_separated=False):
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",") if comma_separated else [value]
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        raise ValueError(f"{name} must be a string or an array of strings")
    return list(dict.fromkeys(v.strip() for v in value if v.strip()))


def _integer(value, name, *, default=None, minimum=0, maximum=None):
    if value is None:
        return default
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside the supported range")
    return value


def _year(value):
    if value is None or isinstance(value, bool):
        return None
    found = re.match(r"^[+-]?\d+", str(value).strip())
    return int(found.group()) if found else None


def _fields(record):
    fields = [(str(record.get("title") or "").casefold(), 0.0)]
    fields.extend((name.casefold(), 0.03) for name in author_names(record.get("authors")))
    nested = record.get("record") or {}
    for role in ("replications", "reproductions", "originals"):
        for linked in nested.get(role) or []:
            fields.extend((name.casefold(), 0.08) for name in author_names(linked.get("authors")))
    return fields


def _wildcard_match(term, text):
    # fnmatch handles repeated stars without building a backtracking-heavy
    # user regex. Single words match word boundaries; phrases match field text.
    if " " in term:
        return fnmatchcase(text, f"*{term}*")
    return any(fnmatchcase(word, term) for word in WORDS.findall(text))


def _excluded(term, fields):
    term = term.casefold()
    if "*" in term or "?" in term:
        return any(_wildcard_match(term, text) for text, _ in fields)
    pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")
    return any(pattern.search(text) for text, _ in fields)


def _term_score(term, fields):
    term = term.casefold()
    best = None
    for text, penalty in fields:
        if not text or (best is not None and penalty >= best):
            continue
        if "*" in term or "?" in term:
            score = penalty if _wildcard_match(term, text) else None
        elif term in text:
            score = penalty
        else:
            words = WORDS.findall(text)
            width = len(term.split())
            candidates = (" ".join(words[i:i + width]) for i in range(len(words) - width + 1))
            ratios = (SequenceMatcher(None, term, word, autojunk=False).ratio() for word in candidates)
            ratio = max(ratios, default=0.0)
            score = 1.0 - ratio + penalty if ratio >= 0.85 else None
        if score is not None and (best is None or score < best):
            best = score
            if best == 0.0:
                return best
    return best


def _matches_type(record, requested):
    if not requested:
        return True
    roles = {str(role).casefold() for role in record.get("types") or []}
    nested = record.get("record") or {}
    if nested.get("replications") or nested.get("reproductions"):
        roles.add("original")
    for role in requested:
        if role in roles:
            return True
        if role == "original" and nested.get("originals"):
            return True
        if role == "replication" and nested.get("replications"):
            return True
        if role == "reproduction" and nested.get("reproductions"):
            return True
    return False


def _matches_outcome(record, requested, *, expanded=False):
    if not requested:
        return True
    outcomes = [str(rep["outcome"]).casefold()
                for rep in (record.get("record") or {}).get("replications") or []
                if rep.get("outcome")]
    # Legacy outcome controls describe an original's replication outcomes. Linked
    # papers without their own sub-replications retain their expansion behavior.
    return all(value in requested for value in outcomes) if outcomes else expanded


def search(records: dict, params: dict, include_derived=True) -> dict:
    """Return a bounded page of DOI records; no database or network access.

    `paperTypes` expands at most one relationship hop. Expanded records still
    obey year, exclusion, role, and outcome filters before joining the final page.
    Wildcards use `*` for any number of characters and `?` for exactly one.
    """
    if not isinstance(params, dict):
        raise ValueError("Search parameters must be an object")
    query = params.get("query") or params.get("q") or ""
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    query = query.strip()
    must = _strings(params.get("mustHave"), "mustHave")
    any_of = _strings(params.get("anyOf"), "anyOf")
    exclude = _strings(params.get("exclude"), "exclude")
    types = {value.casefold() for value in _strings(params.get("paperTypes"), "paperTypes", comma_separated=True)}
    if types - PAPER_TYPES:
        raise ValueError("paperTypes supports original, replication, and reproduction")
    outcomes = {value.casefold() for value in _strings(params.get("outcomes"), "outcomes", comma_separated=True)}
    limit = _integer(params.get("limit"), "limit", default=1000, minimum=1, maximum=1000)
    offset = _integer(params.get("offset"), "offset", default=0)
    year_from = _integer(params.get("yearFrom"), "yearFrom", minimum=1)
    year_to = _integer(params.get("yearTo"), "yearTo", minimum=1)
    if year_from is not None and year_to is not None and year_from > year_to:
        raise ValueError("yearFrom must not exceed yearTo")
    if not query and not (must or any_of or exclude):
        raise ValueError('Provide a search term via "query", "mustHave", "anyOf", or "exclude"')

    advanced = bool(must or any_of or exclude)
    exact_year = None
    query_doi = normalize_doi(query) if query and not advanced else ""
    doi_query = bool(query_doi and (query_doi in records or DOI.fullmatch(query_doi)))
    if not advanced and not doi_query:
        found = YEAR.search(query)
        exact_year = int(found.group()) if found else None
        remainder = query[:found.start()] + query[found.end():] if found else query
        must = [word for word in remainder.split() if len(word) >= 2]

    descriptors = {}

    def fields_for(doi):
        # DOI lookups and year/type-only checks need no text index. Build author
        # and title descriptors only for records that reach text matching.
        if doi not in descriptors:
            descriptors[doi] = _fields(records[doi])
        return descriptors[doi]

    def passes_filters(doi, record, *, expanded=False):
        year = _year(record.get("year"))
        if exact_year is not None and year != exact_year:
            return False
        if year is not None and ((year_from is not None and year < year_from)
                                 or (year_to is not None and year > year_to)):
            return False
        return (_matches_type(record, types)
                and _matches_outcome(record, outcomes, expanded=expanded)
                and not any(_excluded(term, fields_for(doi)) for term in exclude))

    matches = {}
    if doi_query or must or any_of or exclude or exact_year is not None:
        candidates = [query_doi] if doi_query and query_doi in records else ([] if doi_query else records)
        for doi in candidates:
            record = records[doi]
            if not passes_filters(doi, record):
                continue
            scores = []
            for term in must:
                score = _term_score(term, fields_for(doi))
                if score is None:
                    break
                scores.append(score)
            if len(scores) != len(must):
                continue
            if any_of:
                optional = [_term_score(term, fields_for(doi)) for term in any_of]
                optional = [score for score in optional if score is not None]
                if not optional:
                    continue
                scores.append(min(optional))
            matches[doi] = sum(scores) / len(scores) if scores else 0.0

    if types:
        # Iterate only original matches: expansion never recursively traverses
        # the graph, and response size remains bounded by pagination below.
        for doi in list(matches):
            nested = records[doi].get("record") or {}
            for role in ("replications", "reproductions", "originals"):
                for linked in nested.get(role) or []:
                    target = normalize_doi(linked.get("doi"))
                    if target in matches or target not in records:
                        continue
                    if passes_filters(target, records[target], expanded=True):
                        matches[target] = 1.0

    ordered = sorted(matches, key=lambda doi: (matches[doi], doi))
    total = len(ordered)
    results = {}
    for doi in ordered[offset:offset + limit]:
        results[doi] = {**serialize_record(records[doi], include_derived=include_derived),
                        "score": round(matches[doi], 6)}
    return {"query": query, "total": total, "offset": offset, "limit": limit,
            "hasMore": offset + limit < total, "results": results}
