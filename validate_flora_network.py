"""validate_flora_network.py — the FLoRA checks that need network access.

A Python port of R/validate_flora_network.R, with each check asking the question
that decides whether a value is wrong:

    N1  Retracted papers and expressions of concern, from Crossref's Retraction
        Watch data (both sides)
    N2  DOIs registered at doi.org, asked of its handle API
    N3  Links reachable: HEAD, then GET (url_r, oa_url_o, oa_url_r and url_o)
    N4  Originals without a doi_o that probably have one, looked up in Crossref

Findings are records shaped as validate_flora's are (validate_flora.finding), each
naming the rows that carry the DOI or link, at one of the two levels. An answer
that could not be had (a timeout, a server error, a certificate Python cannot
verify) is not a finding: it is listed apart as "could not check", and asked again
on the next run.

Reachable links and registered DOIs are cached in `link_checks`, N4's answers in
`original_doi_lookups`, each for 25 to 35 days by a hash of the target, so the
entries one full run writes do not all expire on the same night. The R version
caches to an RDS file in one working directory; a table means CI, the web app and
every pod share one cache instead of each rebuilding its own — which matters
here, because a cold run is thousands of requests.

ONLY SUCCESSES ARE CACHED for DOIs and links, as in the R original. A failure is
often transient — a publisher rate-limiting, a host briefly down — and caching it
would turn one bad afternoon into a month of false reports. N4 caches a match and
a confirmed miss alike: both are answers about the paper, and a lookup that failed
is not cached.

Usage:
    python validate_flora_network.py --checks retractions
    python validate_flora_network.py --output report.md
    python validate_flora_network.py --limit 200      # sample, for a first look
    python validate_flora_network.py --minutes 15     # defer what 15 minutes leave

Required environment variables:
    DATABASE_URL    — PostgreSQL connection string
Optional:
    OPENALEX_MAILTO — the contact address enrich_works sends (enrich_works.MAILTO,
                      which has a default), sent to Crossref too
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import pandas as pd
from dotenv import load_dotenv

import enrich_works
import preprint_dedup
import transform_sources
from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
from validate_flora import (ENRICHED_COLUMNS, ENRICHED_NOTE, LEVEL_NOTES, LEVEL_TITLES,
                            POSSIBLE_PROBLEM, PROBLEM, _blank, _code, _text, count_text,
                            doi_problem, finding, is_suppressed, load_dataset,
                            load_suppressions, plain, render_section, suppressed_note,
                            url_problem)

load_dotenv()
use_utf8_output()

# Crossref's distribution of the Retraction Watch database, updated each working
# day (crossref.org/documentation/retrieve-metadata/retraction-watch). The Labs API
# R downloads from no longer runs and serves out-of-date data, so it is no fallback.
RETRACTION_WATCH_URL = ("https://gitlab.com/crossref/retraction-watch-data/-/raw/main/"
                        "retraction_watch.csv")
RETRACTION_WATCH_COLUMNS = ("OriginalPaperDOI", "RetractionNature", "RetractionDate")
# The papers with a DOI the download names: 64,343 in October 2026, from 73,356
# notices, and the database only grows. Far fewer means a truncated or substituted
# download, or a DOI column gone blank, any of which would pass every paper. DOIs
# are counted, not rows, so that last case is caught too.
RETRACTION_WATCH_MIN_DOIS = 50_000
HANDLE_API = "https://doi.org/api/handles/"
CROSSREF_WORKS = "https://api.crossref.org/works"
CACHE_DAYS = (25, 35)
REQUEST_TIMEOUT = 15
# ~5 requests/second, matching the R version's RATE_LIMIT_DELAY.
DELAY = 0.2
USER_AGENT = "flora-validation (network validation)"
# A GET only has to show that the server answers; the body is not needed.
GET_BYTES = 1024

OK, FAILED, UNCHECKED = "ok", "failed", "could not check"

# (code, level, heading, selector): the network checks, as validate_flora.CHECKS.
CHECKS = [
    ("retracted", PROBLEM, "Retracted papers", "retractions"),
    ("doi_not_registered", PROBLEM, "DOIs not registered at doi.org", "dois"),
    ("dead_link", PROBLEM, "Dead links", "urls"),
    ("expression_of_concern", POSSIBLE_PROBLEM, "Expressions of concern", "retractions"),
    ("original_doi_found", POSSIBLE_PROBLEM, "Original probably has a DOI", "originals"),
]
SELECTORS = ("retractions", "dois", "urls", "originals")
# What each check's targets are called: one, several.
TARGET_NAMES = {"dois": ("DOI", "DOIs"), "urls": ("link", "links"),
                "originals": ("original without a DOI", "originals without a DOI")}


def _targets(count: int, kind: str) -> str:
    return f"{count:,} {TARGET_NAMES[kind][0 if count == 1 else 1]}"


# ── requests ──────────────────────────────────────────────────────────────────

# getaddrinfo's "no such name": socket.EAI_NONAME is 11001 on Windows, -2 on Linux,
# where EAI_NODATA (-5) says the name exists without an address. A temporary
# failure (EAI_AGAIN) is not a dead link.
_NAME_NOT_FOUND = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}


def classify_status(code: int):
    """(outcome, reason) for an HTTP status. 401 and 403 say the resource exists
    behind a login; 405 says it exists but not for that method."""
    if code < 400 or code in (401, 403, 405):
        return OK, f"HTTP {code}"
    if code in (400, 404, 410):
        return FAILED, f"HTTP {code}"
    return UNCHECKED, f"HTTP {code}"             # 429, 5xx and the rest


def classify_error(exc):
    """(outcome, reason) for a request that raised. Only a name that does not exist
    and a refused connection say the link is dead."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, str):
        return UNCHECKED, reason[:160]
    if isinstance(reason, socket.gaierror):
        if reason.errno in _NAME_NOT_FOUND:
            return FAILED, "DNS: name not found"
        return UNCHECKED, f"DNS: {reason.strerror or reason}"
    if isinstance(reason, ConnectionRefusedError):
        return FAILED, "connection refused"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return UNCHECKED, f"TLS: {getattr(reason, 'verify_message', None) or reason}"
    if isinstance(reason, ssl.SSLError):
        return UNCHECKED, f"TLS: {getattr(reason, 'reason', None) or reason}"
    if isinstance(reason, TimeoutError):
        return UNCHECKED, "timed out"
    return UNCHECKED, f"{type(reason).__name__}: {reason}"[:160]


_URL_SAFE = "/:?#[]@!$&'()*+,;=%~"


def request_url(url: str) -> str:
    """`url` as a request line can carry it: non-ASCII and other unsafe characters
    percent-encoded as UTF-8, escapes already in it kept, an international host
    name IDNA-encoded, and no fragment (a fragment is never sent)."""
    parts = urllib.parse.urlsplit(url.strip())
    netloc = parts.netloc
    if not netloc.isascii():
        try:
            netloc = netloc.encode("idna").decode("ascii")
        except UnicodeError:
            pass
    return urllib.parse.urlunsplit((parts.scheme, netloc,
                                    urllib.parse.quote(parts.path, safe=_URL_SAFE),
                                    urllib.parse.quote(parts.query, safe=_URL_SAFE), ""))


def _request(url: str, method: str):
    """(outcome, reason) for one request. Any error is an answer, never an exception:
    one unreachable host must not end a run that has thousands left to check."""
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            if method == "GET":
                response.read(GET_BYTES)
            return classify_status(response.status)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            # urllib follows redirects; one it hands back is a loop or has no target.
            loop = "infinite loop" in str(exc.msg)
            return UNCHECKED, "redirect loop" if loop else f"HTTP {exc.code} without a target"
        return classify_status(exc.code)
    except Exception as exc:                                   # noqa: BLE001
        return classify_error(exc)


_DOI_LINK_RE = re.compile(r"^https?://(dx\.)?doi\.org/(10\.\S+)$", re.I)


def check_link(url: str):
    """(outcome, reason) for a link.

    A doi.org link is answered by the DOI's registration: the request would
    otherwise end at the publisher, whose login walls and certificate chains fail
    for links that work. Anything else gets HEAD, then GET when HEAD gives anything
    but reachable, because many servers answer HEAD wrongly (EconPapers answers it
    404 and serves the page). The GET's answer stands.
    """
    doi_link = _DOI_LINK_RE.match(url.strip())
    if doi_link:
        outcome, reason = doi_registration(urllib.parse.unquote(doi_link.group(2)))
        return outcome, "the DOI is not registered at doi.org" if outcome == FAILED else reason
    try:
        target = request_url(url)
    except ValueError as exc:
        # A host urlsplit cannot parse. The structural check reports such a link
        # (url_problem), so this only keeps one bad value from ending the run.
        return UNCHECKED, f"not a link Python can request: {exc}"[:160]
    outcome, reason = _request(target, "HEAD")
    if outcome == OK:
        return outcome, reason
    time.sleep(DELAY)
    return _request(target, "GET")


def doi_registration(doi: str):
    """(outcome, reason): whether doi.org has this DOI, from its handle API.

    responseCode 1 is registered and 100 is not; anything else could not be
    checked. doi.org answers this itself, so a publisher's broken page cannot make a
    registered DOI look unregistered. A stored escape (%3c in an old Wiley DOI) is
    the DOI's own character, so it is decoded before the DOI is encoded again.
    """
    handle = urllib.parse.unquote(doi.strip())
    request = urllib.request.Request(HANDLE_API + urllib.parse.quote(handle, safe="/"),
                                     headers={"User-Agent": USER_AGENT,
                                              "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            return UNCHECKED, f"HTTP {exc.code}"
        status, body = exc.code, exc.read()           # an unknown DOI is a 404 with an answer
    except Exception as exc:                                   # noqa: BLE001
        return UNCHECKED, classify_error(exc)[1]
    try:
        code = json.loads(body).get("responseCode")
    except (ValueError, AttributeError):
        return UNCHECKED, f"HTTP {status} without a handle answer"
    if code == 1:
        return OK, "registered"
    if code == 100:
        return FAILED, "not registered at doi.org"
    return UNCHECKED, f"handle API responseCode {code}"


# ── cache ─────────────────────────────────────────────────────────────────────

def ensure_cache(cur) -> None:
    """Both cache tables, created if missing. Additive: link_checks keeps the shape
    production already has."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS link_checks (
            target     TEXT PRIMARY KEY,
            kind       TEXT NOT NULL CHECK (kind IN ('doi', 'url')),
            ok         BOOLEAN NOT NULL,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS original_doi_lookups (
            query      TEXT PRIMARY KEY,
            doi        TEXT,
            title      TEXT,
            author     TEXT,
            year       TEXT,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def cache_days(target: str) -> int:
    """CACHE_DAYS[0] to CACHE_DAYS[1], fixed for each target by a hash of it."""
    low, high = CACHE_DAYS
    return low + hashlib.sha256(target.encode("utf-8")).digest()[0] % (high - low + 1)


def _fresh(target, checked_at, now) -> bool:
    return checked_at > now - timedelta(days=cache_days(target))


def fresh_targets(cur, kind: str) -> set:
    cur.execute("SELECT target, checked_at FROM link_checks WHERE kind = %s AND ok", (kind,))
    now = datetime.now(timezone.utc)
    return {r["target"] for r in cur.fetchall() if _fresh(r["target"], r["checked_at"], now)}


def remember(cur, kind: str, target: str) -> None:
    cur.execute(
        """
        INSERT INTO link_checks (target, kind, ok, checked_at)
        VALUES (%s, %s, TRUE, NOW())
        ON CONFLICT (target) DO UPDATE SET kind = EXCLUDED.kind, ok = TRUE, checked_at = NOW()
        """,
        (target, kind),
    )


def fresh_lookups(cur) -> dict:
    """{query: match or None} for N4's answers still fresh."""
    cur.execute("SELECT query, doi, title, author, year, checked_at FROM original_doi_lookups")
    now = datetime.now(timezone.utc)
    return {r["query"]: ({"doi": r["doi"], "title": r["title"], "author": r["author"],
                          "year": r["year"]} if r["doi"] else None)
            for r in cur.fetchall() if _fresh(r["query"], r["checked_at"], now)}


def remember_lookup(cur, query: str, match) -> None:
    match = match or {}
    cur.execute(
        """
        INSERT INTO original_doi_lookups (query, doi, title, author, year, checked_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (query) DO UPDATE SET doi = EXCLUDED.doi, title = EXCLUDED.title,
            author = EXCLUDED.author, year = EXCLUDED.year, checked_at = NOW()
        """,
        (query, match.get("doi"), match.get("title"), match.get("author"), match.get("year")),
    )


# ── occurrences ───────────────────────────────────────────────────────────────

def _occurrences(df, columns, accept) -> dict:
    """{target: {column: [rows]}} for the cells `accept(value, row, column)` keeps,
    in first-seen order: a finding names every row that carries its target."""
    out = {}
    for _, row in df.iterrows():
        for column in columns:
            value = _text(row.get(column))
            if value and accept(value, row, column):
                out.setdefault(value, {}).setdefault(column, []).append(row)
    return out


def _findings(check, occurrences, detail) -> list:
    """One finding per column a target appears in."""
    return [finding(check, rows, column, target, detail(column))
            for target, columns in occurrences.items() for column, rows in columns.items()]


def _unchecked(target, columns, reason) -> dict:
    rows = [row for column_rows in columns.values() for row in column_rows]
    return {"target": target, "columns": list(columns), "reason": reason,
            "flora_ids": sorted({_text(row.get("flora_id")) for row in rows} - {""})}


# ── N1: retractions ───────────────────────────────────────────────────────────

class RetractionDataError(RuntimeError):
    """The Retraction Watch download is missing or not the file it should be."""


def _notice_date(value):
    text = _text(value).split(" ")[0]
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def parse_retraction_watch(payload: str) -> dict:
    """{original DOI: [(nature, date)]} from the Retraction Watch CSV.

    Raises RetractionDataError when a column the check needs is missing (a
    renamed column, or an HTML page served with 200) or the file names
    implausibly few papers: either would otherwise pass every paper in silence.
    """
    reader = csv.DictReader(io.StringIO(payload))
    missing = [c for c in RETRACTION_WATCH_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise RetractionDataError(f"the download has no {', '.join(missing)} column")
    notices = {}
    for row in reader:
        doi = preprint_dedup.normalize_doi(row.get("OriginalPaperDOI"))
        if doi.startswith("10."):
            notices.setdefault(doi, []).append(
                (_text(row.get("RetractionNature")).lower(), _notice_date(row.get("RetractionDate"))))
    if len(notices) < RETRACTION_WATCH_MIN_DOIS:
        raise RetractionDataError(f"the download names {len(notices):,} "
                                  f"DOI{'' if len(notices) == 1 else 's'}, fewer than "
                                  f"{RETRACTION_WATCH_MIN_DOIS:,}")
    return notices


def notice_status(notices) -> "str | None":
    """'retracted', 'concern' or None for one paper's notices.

    Retracted: a Retraction with no Reinstatement on or after it (a Reinstatement
    with no date is taken to follow it). Concern: an Expression of concern on a
    paper that is not retracted. Corrections say nothing about the findings.
    """
    retractions = [date for nature, date in notices if nature == "retraction"]
    if retractions:
        latest = None if None in retractions else max(retractions)
        reinstated = any(nature == "reinstatement"
                         and (date is None or latest is None or date >= latest)
                         for nature, date in notices)
        if not reinstated:
            return "retracted"
    if any(nature == "expression of concern" for nature, _ in notices):
        return "concern"
    return None


def retraction_notices() -> dict:
    request = urllib.request.Request(RETRACTION_WATCH_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read().decode("utf-8-sig", errors="replace")
    return parse_retraction_watch(payload)


def check_retractions(df):
    """(findings, None), or ([], the reason) when the check could not run."""
    try:
        notices = retraction_notices()
    except Exception as exc:                                   # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:200]

    def accept(value, row, column):
        return notice_status(notices.get(preprint_dedup.normalize_doi(value), [])) is not None

    issues = []
    for doi, columns in _occurrences(df, ("doi_o", "doi_r"), accept).items():
        status = notice_status(notices[preprint_dedup.normalize_doi(doi)])
        check, word = (("retracted", "is retracted") if status == "retracted"
                       else ("expression_of_concern", "has an expression of concern"))
        issues += _findings(check, {doi: columns},
                            lambda column: f"{column} {word} (Retraction Watch)")
    return issues, None


# ── N2/N3: registration and reachability ──────────────────────────────────────

def _doi_cell(value, row, column) -> bool:
    # A malformed DOI is the structural report's (validate_flora, Invalid DOI format).
    return doi_problem(value) is None


def _doi_fallback(row) -> bool:
    """Whether url_o only restates doi_o as a doi.org link (the transform fills it in
    where the source gave none), which the DOI check already covers."""
    url = _text(row.get("url_o"))
    return bool(_DOI_LINK_RE.match(url)) and \
        transform_sources.clean_doi(url) == transform_sources.clean_doi(row.get("doi_o"))


def _link_cell(value, row, column) -> bool:
    # Not a link, or not one that can be requested as written (spaces, two links,
    # cut off): the structural report's (validate_flora, Invalid URLs).
    if url_problem(value) is not None:
        return False
    return not (column == "url_o" and _doi_fallback(row))


def _dead_link_detail(reason):
    def detail(column):
        note = f"; {ENRICHED_NOTE}" if column in ENRICHED_COLUMNS else ""
        return f"{column} is dead ({reason}{note})"
    return detail


def _out_of_time(deadline) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def check_resolution(cur, df, kind: str, limit: int = 0, verbose: bool = True,
                     statistics: dict = None, deadline: float = None):
    """N2 (kind 'doi') or N3 ('url'): (findings, could-not-check items).

    `deadline` (time.monotonic()) stops it early: the targets not asked by then are
    deferred, as --limit defers them, and the next run starts on them.
    """
    if limit < 0:
        raise ValueError("network limit must be zero (all) or positive")
    if kind == "doi":
        occurrences = _occurrences(df, ("doi_o", "doi_r"), _doi_cell)
    else:
        occurrences = _occurrences(df, ("url_r", "oa_url_o", "oa_url_r", "url_o"), _link_cell)
    targets = list(occurrences)

    cached = fresh_targets(cur, kind)
    outstanding = [t for t in targets if t not in cached]
    # Counted before the limit is applied: with --limit, the rest are deferred, not
    # cached, and reporting them as cached would say the run was complete.
    n_cached = len(targets) - len(outstanding)
    todo = outstanding[:limit] if limit else outstanding

    if verbose:
        deferred = len(outstanding) - len(todo)
        note = f", {deferred} deferred by --limit" if deferred else ""
        print(f"  {kind}s: {len(targets)} distinct, {n_cached} cached, "
              f"{len(todo)} to check{note}", file=sys.stderr)

    issues, unchecked, asked = [], [], 0
    for n, target in enumerate(todo, 1):
        if _out_of_time(deadline):
            break
        asked = n
        outcome, reason = doi_registration(target) if kind == "doi" else check_link(target)
        if outcome == OK:
            remember(cur, kind, target)
        elif outcome == FAILED and kind == "doi":
            issues += _findings("doi_not_registered", {target: occurrences[target]},
                                lambda column: f"{column} is not registered at doi.org")
        elif outcome == FAILED:
            issues += _findings("dead_link", {target: occurrences[target]},
                                _dead_link_detail(reason))
        else:
            unchecked.append(_unchecked(target, occurrences[target], reason))
        if verbose and n % 100 == 0:
            print(f"    {n}/{len(todo)}", file=sys.stderr)
        time.sleep(DELAY)
    if statistics is not None:
        statistics.update(targets=len(targets), cached=n_cached, checked=asked,
                          deferred=len(outstanding) - asked, unchecked=len(unchecked))
    return issues, unchecked


# ── N4: originals that probably have a DOI ────────────────────────────────────

# A suggestion is only made when the work found is surely this paper.
MATCH_SIMILARITY = 0.9
MATCH_YEARS = 1
# Works that are not the paper itself, though they share its title: data and
# supplements, peer reviews, preprints (posted-content).
NOT_THE_PAPER = {"dataset", "database", "component", "peer-review", "posted-content",
                 "grant", "standard"}
# Crossref's test prefix, which the ACM Digital Library gives papers that have no DOI.
TEST_PREFIX = "10.5555/"
_NAME_PARTICLES = {"jr", "sr", "ii", "iii", "van", "von", "de", "der", "den", "la", "le",
                   "da", "di", "del"}
# JATS markup in a title (<i>In Utero</i>) is no part of what to search for.
_TAG_RE = re.compile(r"<[^>]+>")


def _name_tokens(name) -> set:
    text = unicodedata.normalize("NFKD", _text(name)).encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z]+", text)) - _NAME_PARTICLES


def lookup_query(row) -> "tuple | None":
    """(title, first author's surname, year) for a row without a doi_o, or None."""
    if not _blank(row.get("doi_o")) or _blank(row.get("title_o")):
        return None
    title = " ".join(_TAG_RE.sub("", _text(row.get("title_o"))).split())
    surname = preprint_dedup.extract_first_author(row.get("author_o")) or ""
    year = _text(row.get("year_o"))
    year = str(int(float(year))) if re.fullmatch(r"\d{4}(\.0+)?", year) else ""
    return title, surname, year


def query_key(query) -> str:
    title, surname, year = query
    return f"{preprint_dedup.normalize_title(title)}|{surname}|{year}"


def best_match(query, candidates) -> "dict | None":
    """The candidate that is surely this paper, or None.

    Surely: titles at least MATCH_SIMILARITY alike (preprint_dedup.title_similarity),
    years at most MATCH_YEARS apart when both are known, and, when the row names a
    first author, a candidate whose first author shares that surname. A candidate
    without authors does not pass then: on the real rows those were reviews of the
    book. Data, peer reviews, preprints and repository copies never pass.
    """
    title, surname, year = query
    wanted = _name_tokens(surname)
    best, best_similarity = None, 0.0
    for candidate in candidates:
        doi = _text(candidate.get("doi")).lower()
        if (not doi or doi.startswith(TEST_PREFIX) or candidate.get("type") in NOT_THE_PAPER
                or preprint_dedup.is_preprint_doi(doi) or preprint_dedup.is_repository_doi(doi)):
            continue
        similarity = preprint_dedup.title_similarity(title, candidate.get("title"))
        if similarity < MATCH_SIMILARITY:
            continue
        if year and candidate.get("year") and abs(int(year) - int(candidate["year"])) > MATCH_YEARS:
            continue
        if wanted and not wanted & _name_tokens(candidate.get("author")):
            continue
        if similarity > best_similarity:
            best, best_similarity = {**candidate, "doi": doi}, similarity
    return best


def _crossref_candidates(message) -> list:
    out = []
    for item in (message or {}).get("items", []):
        parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
        authors = item.get("author") or []
        first = authors[0] if authors else {}
        out.append({"doi": item.get("DOI"), "title": (item.get("title") or [""])[0],
                    "type": item.get("type"), "year": str(parts[0]) if parts[0] else "",
                    "author": first.get("family") or first.get("name") or ""})
    return out


def lookup_original_doi(query):
    """(outcome, match or None, reason): Crossref's best works for the title, first
    author and year. Crossref rather than OpenAlex, from a trial on the real rows:
    it found more of the DOIs, and says which works are reviews or data."""
    text = " ".join(part for part in query if part)
    url = (f"{CROSSREF_WORKS}?query.bibliographic={urllib.parse.quote(text)}&rows=5"
           f"&select=DOI,title,issued,author,type"
           f"&mailto={urllib.parse.quote(enrich_works.MAILTO)}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT * 2) as response:
            message = json.loads(response.read()).get("message")
    except urllib.error.HTTPError as exc:
        return UNCHECKED, None, f"HTTP {exc.code}"
    except Exception as exc:                                   # noqa: BLE001
        return UNCHECKED, None, classify_error(exc)[1]
    try:
        match = best_match(query, _crossref_candidates(message))
    except (AttributeError, TypeError, IndexError, ValueError):
        # An answer in another shape than Crossref's list of works: no answer, and
        # not a confirmed miss to cache for a month.
        return UNCHECKED, None, "Crossref's answer could not be read"
    return OK, match, "answered"


def _suggestion_detail(match):
    title = " ".join(_TAG_RE.sub("", _text(match.get("title"))).split())
    title = title if len(title) <= 80 else title[:79] + "…"
    about = ", ".join(part for part in (_text(match.get("year")), _text(match.get("author"))) if part)
    return f"doi_o is blank; Crossref lists this paper with a DOI: '{title}'" + \
        (f" ({about})" if about else "")


def check_original_dois(cur, df, limit: int = 0, verbose: bool = True,
                        statistics: dict = None, deadline: float = None):
    """N4: (findings, could-not-check items) for originals without a doi_o. A
    `deadline` defers the rest, as in check_resolution."""
    if limit < 0:
        raise ValueError("network limit must be zero (all) or positive")
    queries = {}
    for _, row in df.iterrows():
        query = lookup_query(row)
        if query:
            queries.setdefault(query_key(query), (query, []))[1].append(row)

    answers = fresh_lookups(cur)
    outstanding = [key for key in queries if key not in answers]
    todo = outstanding[:limit] if limit else outstanding
    if verbose:
        print(f"  originals without a DOI: {len(queries)} distinct, "
              f"{len(queries) - len(outstanding)} cached, {len(todo)} to look up", file=sys.stderr)

    unchecked, asked = [], 0
    for n, key in enumerate(todo, 1):
        if _out_of_time(deadline):
            break
        asked = n
        query, rows = queries[key]
        outcome, match, reason = lookup_original_doi(query)
        if outcome == OK:
            remember_lookup(cur, key, match)
            answers[key] = match
        else:
            unchecked.append(_unchecked(query[0], {"title_o": rows}, reason))
        time.sleep(DELAY)

    issues = [finding("original_doi_found", rows, "doi_o", answers[key]["doi"],
                      _suggestion_detail(answers[key]))
              for key, (query, rows) in queries.items() if answers.get(key)]
    if statistics is not None:
        statistics.update(targets=len(queries), cached=len(queries) - len(outstanding),
                          checked=asked, deferred=len(outstanding) - asked,
                          unchecked=len(unchecked))
    return issues, unchecked


# ── the run ───────────────────────────────────────────────────────────────────

def _mostly_unchecked(stats) -> bool:
    """More than half of at least ten targets went unanswered: the service, or this
    machine's network, was likely down, and a clean result would mean nothing."""
    return stats["checked"] >= 10 and stats["unchecked"] * 2 > stats["checked"]


def validate(cur, df, checks="all", limit=0, suppressions=None, minutes=0) -> dict:
    """Check one exported frame and preserve coverage as well as findings.

    A limited run, a Retraction Watch download that failed its checks, and a check
    where most targets went unanswered are incomplete, even when nothing was
    found. They must never look like a pass. A target that could not be checked
    is listed, never counted as a finding.

    `minutes` (0: none) bounds the time the checks may take, the download
    included. What is left when it runs out is deferred, and the run is
    incomplete. Successes are cached as they come, so the next run starts where
    this one stopped: a cold cache is worked through over several runs instead of
    one run outliving its stage's timeout (source_sync_runner.STAGE_TIMEOUT_SECONDS).
    """
    if checks not in {"all", "none", *SELECTORS}:
        raise ValueError(f"Unknown network checks: {checks}")
    if limit < 0:
        raise ValueError("network limit must be zero (all) or positive")
    if minutes < 0:
        raise ValueError("network minutes must be zero (no budget) or positive")
    deadline = time.monotonic() + minutes * 60 if minutes else None
    suppressions = suppressions or set()
    ran = [selector for selector in SELECTORS if checks in ("all", selector)]
    found, unchecked, notices, coverage = [], {}, [], {}
    incomplete = checks != "all"

    if "retractions" in ran:
        items, problem = check_retractions(df)
        if problem:
            ran.remove("retractions")
            notices.append(f"Retraction check did not run: {problem}")
            coverage["retractions"] = {"status": "did not run", "reason": problem}
            incomplete = True
        else:
            coverage["retractions"] = {"status": "checked"}
            found += items
    for selector, run in (
            ("dois", lambda s: check_resolution(cur, df, "doi", limit, statistics=s, deadline=deadline)),
            ("urls", lambda s: check_resolution(cur, df, "url", limit, statistics=s, deadline=deadline)),
            ("originals", lambda s: check_original_dois(cur, df, limit, statistics=s, deadline=deadline))):
        if selector not in ran:
            continue
        stats = {}
        items, unchecked[selector] = run(stats)
        found += items
        coverage[selector] = stats
        if _mostly_unchecked(stats):
            notices.append(f"Most {TARGET_NAMES[selector][1]} could not be checked "
                           f"({stats['unchecked']:,} of {stats['checked']:,})")
        incomplete = incomplete or bool(stats["deferred"]) or _mostly_unchecked(stats)
    deferred = sum(stats.get("deferred", 0) for stats in coverage.values())
    if _out_of_time(deadline) and deferred:
        notices.append(f"The {minutes:g}-minute network budget ran out: {deferred:,} "
                       f"target{'' if deferred == 1 else 's'} deferred to the next run")

    results, suppressed = [], 0
    for code, level, heading, selector in CHECKS:
        if selector not in ran:
            continue
        kept = []
        for item in (i for i in found if i["check"] == code):
            if is_suppressed(item, (code, heading), suppressions):
                suppressed += 1
            else:
                kept.append({**item, "level": level})
        results.append({"code": code, "level": level, "heading": heading,
                        "items": kept, "passed": not kept})
    problems = sum(len(r["items"]) for r in results if r["level"] == PROBLEM)
    possible = sum(len(r["items"]) for r in results if r["level"] == POSSIBLE_PROBLEM)
    return {"status": "skipped" if checks == "none" else
            "incomplete" if incomplete else "needs_attention" if problems or possible else "passed",
            "checks": checks, "results": results, "unchecked": unchecked, "notices": notices,
            "coverage": coverage, "suppressed": suppressed, "problems": problems,
            "possible_problems": possible, "issue_count": problems + possible, "rows": len(df)}


def _upgrade(report: dict) -> dict:
    """A report stored before findings were records (sections of strings, no
    levels), in the current shape, so publish_flora can still write it out."""
    if "results" in report:
        return report
    results = [{"heading": heading, "level": PROBLEM, "items": items, "passed": not items}
               for heading, items in report.get("sections", {}).items()]
    return {**report, "results": results, "unchecked": {}, "notices": [],
            "problems": report.get("issue_count", 0), "possible_problems": 0}


# Status values as the website's run cards word them. The JSON keeps the values.
STATUS_LABELS = {"needs_attention": "needs review"}
COVERAGE_LABELS = {"dois": "DOIs", "urls": "Links", "originals": "Originals without a DOI"}
CSV_COLUMNS = ["check", "level", "flora_ids", "column", "value", "detail", "key"]


def finding_rows(report: dict) -> list:
    """The findings as flora_network_validation.csv's rows."""
    rows = []
    for result in _upgrade(report)["results"]:
        for item in result["items"]:
            if isinstance(item, str):
                rows.append({"check": result["heading"], "level": result["level"], "detail": item})
            else:
                rows.append({**{c: item.get(c) for c in CSV_COLUMNS},
                             "flora_ids": " ".join(item["flora_ids"])})
    return rows


def render_validation(report: dict) -> str:
    report = _upgrade(report)
    text = render(report)
    status = STATUS_LABELS.get(report["status"], report["status"])
    text += f"\n\n**Network validation status: {status}**\n"
    for kind, coverage in report["coverage"].items():
        if "targets" in coverage:
            unchecked = coverage.get("unchecked", 0)
            note = f" ({unchecked:,} could not be checked)" if unchecked else ""
            text += (f"\n- {COVERAGE_LABELS.get(kind, kind)}: {coverage['targets']:,} distinct; "
                     f"{coverage['cached']:,} cached, {coverage['checked']:,} checked{note}, "
                     f"{coverage['deferred']:,} deferred.")
    if report["checks"] == "none":
        text += "\n\nNo network checks ran (`--network-checks none`)."
    elif report["checks"] != "all":
        text += f"\n\nOnly these checks ran: {report['checks']}."
    return text + "\n"


# ── report ────────────────────────────────────────────────────────────────────

ITEM_FORMAT = ("Each item names the FLoRA records carrying the DOI or link by FLoRA ID, "
               "says what is wrong and, after the dash, gives the DOI or link.")


def _unchecked_line(item) -> str:
    ids = ", ".join(item["flora_ids"][:5])
    more = len(item["flora_ids"]) - 5
    ids += f" and {more:,} more" if more > 0 else ""
    where = f"{ids}, {'/'.join(item['columns'])}" if ids else "/".join(item["columns"])
    # The reason can be a server's own words (an error message), the target any link.
    return f"{where}: {plain(item['reason'])} — {_code(item['target'])}"


def render(report: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = ["## FLoRA network validation", "",
           f"_Generated: {stamp} UTC | Dataset: {report['rows']:,} rows_", ""]
    # Status lines, never findings: a check that could not run found nothing. A
    # notice can carry an exception's message.
    for notice in report["notices"]:
        out += [f"**{plain(notice)}.**", ""]
    if report["results"]:
        out += [ITEM_FORMAT, ""]

    for level, title in LEVEL_TITLES.items():
        mine = [r for r in report["results"] if r.get("level", PROBLEM) == level]
        if not mine:
            continue
        out += [f"### {title} ({sum(len(r['items']) for r in mine):,})", ""]
        if level in LEVEL_NOTES and any(r["items"] for r in mine):
            out += [f"_{LEVEL_NOTES[level]}_", ""]
        for result in mine:
            if result["items"]:
                out += render_section(result["heading"], result["items"])
            else:
                # A paragraph, not a list item: after a list and a blank line, "- "
                # would continue that list and read as one of its findings.
                out += [f"**{result['heading']}:** none", ""]

    unchecked = {kind: items for kind, items in report["unchecked"].items() if items}
    if unchecked:
        total = sum(len(items) for items in unchecked.values())
        counts = ", ".join(_targets(len(items), kind) for kind, items in unchecked.items())
        out += [f"<details><summary><b>Could not check</b> ({counts})</summary>", "",
                f"_Not findings: no answer could be had for these {total:,}, and they are "
                "checked again on the next run._", ""]
        for kind, items in unchecked.items():
            out += [f"**{COVERAGE_LABELS[kind]}** ({len(items):,}):", ""]
            out += [f"- {_unchecked_line(item)}" for item in items] + [""]
        out += ["</details>", ""]

    if report["suppressed"]:
        out += ["", suppressed_note(report["suppressed"])]
    return "\n".join(out)


def main() -> int:
    start_logging("validate-network")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checks", default="all", choices=["all", *SELECTORS],
                        help="which checks to run (default: all)")
    parser.add_argument("--limit", type=int, default=0,
                        help="check at most this many new targets per kind")
    parser.add_argument("--minutes", type=float, default=0,
                        help="stop checking after this many minutes and defer the rest "
                             "to the next run (default: no limit)")
    parser.add_argument("--output", type=Path, help="write the report here")
    parser.add_argument("--input", type=Path,
                        help="validate this exact CSV; database is used only for the caches")
    parser.add_argument("--fail-on-issues", action="store_true",
                        help="exit 1 on a Problem or an incomplete run; Possible problems never fail")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        ensure_cache(cur)
        df = (pd.read_csv(args.input, dtype=str, keep_default_na=False,
                          na_values=["NA", ""], encoding="utf-8-sig")
              if args.input else load_dataset(cur))
        suppressions = load_suppressions()
        report = validate(cur, df, args.checks, args.limit, suppressions, args.minutes)
        text = render_validation(report)
    finally:
        conn.close()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"report written to {args.output}")
    else:
        print(text)

    print(f"\n{count_text(report['problems'], report['possible_problems'])}; "
          f"status {STATUS_LABELS.get(report['status'], report['status'])}", file=sys.stderr)
    failing = report["problems"] or report["status"] == "incomplete"
    return 1 if (args.fail_on_issues and failing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
