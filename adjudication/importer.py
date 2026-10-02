"""Import fred-data PR #143's disagreements.csv into adjudication.records.

The file is read at a fixed commit, so the rows cannot change under the people
judging them. Each row keeps both answers as given (FLoRA's and the
Observatory's) and its raw CSV row; OpenAlex adds what the CSV lacks: the
replication's abstract and year, and the title of each original. OpenAlex holds
no abstract for most of these papers, so Europe PMC fills the gaps. Both lookups
are best effort: a row they cannot enrich is still imported. Results are kept in
memory for an hour, so an import right after its preview does not repeat them.

Dry run by default, in a read-only transaction. An apply inserts new rows and
refreshes rows nobody has judged yet; a row with a submitted judgement is never
changed underneath its judges. Nothing is ever deleted.

    python -m adjudication.importer            # dry run against DATABASE_URL
    python -m adjudication.importer --apply
    python -m adjudication.importer --csv path/to/disagreements.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import time
import urllib.parse
from collections import Counter
from contextlib import contextmanager
from typing import Callable, Iterable

import requests

from .judging import JUDGING_LOCK_ID

SOURCE_REPO = "forrtproject/fred-data"
SOURCE_COMMIT = "55d6f044038b57ac5f6057e4011214c192c7d7ac"
SOURCE_PATH = "external/metascience-observatory/disagreements.csv"
SOURCE_URL = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_COMMIT}/{SOURCE_PATH}"
SOURCE_LABEL = f"{SOURCE_REPO}@{SOURCE_COMMIT[:7]}:{SOURCE_PATH}"

# Serialises imports; distinct from bootstrap.ADVISORY_LOCK_ID and the app's own.
IMPORT_LOCK_ID = 7_342_025_095

KINDS = (
    "we found no original",
    "different original",
    "same original, different outcome",
    "MO names no original DOI",
)
REQUIRED_COLUMNS = (
    "doi_r", "title_r", "kind", "our_doi_o", "our_title_o", "our_outcome",
    "mo_doi_o", "mo_outcome", "our_link_method", "our_link_confidence",
    "our_outcome_quote", "out_quote_source", "mo_replication_type",
    "mo_discipline", "mo_source", "mo_confidence", "mo_ai_version",
    "our_link_evidence",
)

# The columns an import writes; status and the timestamps are the table's own.
RECORD_FIELDS = (
    "kind", "doi_r", "title_r", "abstract_r", "abstract_source", "year_r",
    "flora_doi_o", "flora_title_o", "flora_outcome", "flora_outcome_quote",
    "flora_quote_source", "flora_link_method", "flora_link_confidence",
    "flora_link_evidence",
    "mo_doi_o", "mo_title_o", "mo_outcome", "mo_replication_type",
    "mo_discipline", "mo_source", "mo_confidence", "mo_ai_version",
    "raw",
)

OPENALEX_API = "https://api.openalex.org/works"
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "lukas.wallrich@gmail.com")
OPENALEX_BATCH = 50          # the most values an OpenAlex OR filter accepts
OPENALEX_RETRIES = 3
OPENALEX_DELAY = 0.15        # well inside the polite pool's 10 requests/second
EUROPEPMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CACHE_SECONDS = 3600         # a preview's lookups serve the import that follows it


class ImportDataError(ValueError):
    """The CSV cannot be imported as it is."""


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

def normalise_doi(value) -> str:
    text = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip()


# A DOI inside a cell that holds more: "10.1017/x (case conflict 1)" or
# "10.1371/a ; 10.1371/a.s002". Parentheses inside a DOI are kept.
_FIRST_DOI = re.compile(r"10\.\d+/[^\s;,]+")


def clean_doi(value) -> str:
    """The DOI a cell names: its first DOI, without notes after it. A value with
    no DOI in it (a Semantic Scholar link, say) is kept as it is. Identity
    (import_key) still uses the whole cell, so rows that differ only in such a
    note stay separate records."""
    text = normalise_doi(value)
    found = _FIRST_DOI.search(text)
    return found.group(0) if found else text


def _is_doi(value: str) -> bool:
    return value.startswith("10.")


def import_key(row: dict) -> str:
    """Stable identity of one row, so a re-import updates instead of duplicating."""
    parts = (normalise_doi(row["doi_r"]), normalise_doi(row["our_doi_o"]),
             normalise_doi(row["mo_doi_o"]), row["kind"].strip())
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def fetch_source(url: str = SOURCE_URL) -> bytes:
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        raise ImportDataError(f"GitHub returned HTTP {response.status_code} for {url}")
    return response.content


def read_rows(content: bytes) -> list[dict]:
    """The CSV's rows, checked. Refuses rather than guesses."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportDataError(f"the file is not UTF-8: {exc}") from exc
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ImportDataError(f"the file lacks column(s): {', '.join(missing)}")
    rows, seen = [], {}
    for number, raw in enumerate(reader, start=2):        # line 1 is the header
        row = {k: (v or "").strip() for k, v in raw.items() if k is not None}
        if row["kind"] not in KINDS:
            raise ImportDataError(f"line {number}: unknown kind {row['kind']!r}")
        if not row["doi_r"]:
            raise ImportDataError(f"line {number}: no replication DOI (doi_r)")
        key = import_key(row)
        if key in seen:
            raise ImportDataError(f"line {number} repeats line {seen[key]}")
        seen[key] = number
        rows.append(row)
    if not rows:
        raise ImportDataError("the file has no rows")
    return rows


# ---------------------------------------------------------------------------
# OpenAlex: abstracts, years and titles the CSV lacks
# ---------------------------------------------------------------------------

def reconstruct_abstract(index) -> str | None:
    """OpenAlex stores abstracts as {word: [positions]} (as enrich_works.py does)."""
    if not isinstance(index, dict) or not index:
        return None
    words = {position: word for word, positions in index.items()
             for position in (positions or []) if isinstance(position, int)}
    return " ".join(str(words[p]) for p in sorted(words)) or None


def _openalex_batch(dois: list[str]) -> list[dict]:
    flt = urllib.parse.quote("doi:" + "|".join(dois), safe="|:/().-_")
    url = (f"{OPENALEX_API}?filter={flt}&per-page={OPENALEX_BATCH}"
           f"&select=doi,title,publication_year,abstract_inverted_index&mailto={OPENALEX_MAILTO}")
    last = None
    for attempt in range(OPENALEX_RETRIES):
        try:
            response = requests.get(url, timeout=60,
                                    headers={"User-Agent": "flora-validation/adjudication"})
            response.raise_for_status()
            return response.json().get("results", [])
        except Exception as exc:          # a truncated read is not a RequestException
            last = exc
            if attempt < OPENALEX_RETRIES - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"OpenAlex failed after {OPENALEX_RETRIES} attempts: {last}")


def lookup_works(dois: Iterable[str], fetch_batch: Callable = _openalex_batch) -> tuple[dict, int]:
    """{doi: {title, year, abstract}} for the DOIs OpenAlex knows, and how many
    batches failed. A failed batch costs its enrichment, never the import."""
    wanted = sorted({clean_doi(d) for d in dois if _is_doi(clean_doi(d))})
    found, failed = {}, 0
    for start in range(0, len(wanted), OPENALEX_BATCH):
        batch = wanted[start:start + OPENALEX_BATCH]
        try:
            works = _cached("openalex", "|".join(batch), lambda: fetch_batch(batch))
        except Exception as exc:
            print(f"[adjudication] OpenAlex lookup failed for one batch: {exc}")
            failed += 1
            continue
        for work in works:
            doi = normalise_doi(work.get("doi"))
            if doi:
                found[doi] = {
                    "title": (work.get("title") or "").strip() or None,
                    "year": str(work["publication_year"]) if work.get("publication_year") else None,
                    "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
                }
        if start + OPENALEX_BATCH < len(wanted):
            time.sleep(OPENALEX_DELAY)
    return found, failed


_CACHE: dict[tuple[str, str], tuple[float, object]] = {}


def _cached(kind: str, key: str, compute: Callable):
    """compute() once per hour per key; a failure is not cached."""
    hit = _CACHE.get((kind, key))
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = compute()
    _CACHE[(kind, key)] = (time.monotonic(), value)
    return value


# Only the markup Europe PMC uses. A bare "<[^>]+>" would also eat text, because
# Europe PMC writes comparisons literally: "p < .05 and n > 300" became "p 300".
_TAGS = re.compile(
    r"</?(?:h[1-6]|p|i|b|em|strong|sup|sub|br|u|sc|span|div|italic|bold|ul|ol|li)"
    r"(?:\s[^<>]*)?/?>",
    re.IGNORECASE,
)


def _europepmc_abstract(doi: str) -> str | None:
    response = requests.get(
        EUROPEPMC_API,
        params={"query": f'DOI:"{doi}"', "resultType": "core", "format": "json", "pageSize": 1},
        headers={"User-Agent": "flora-validation/adjudication"}, timeout=30,
    )
    response.raise_for_status()
    results = (response.json().get("resultList") or {}).get("result") or []
    text = (results[0].get("abstractText") if results else None) or ""
    # Europe PMC marks up headings and italics; keep the words.
    text = html.unescape(_TAGS.sub(" ", text))
    return " ".join(text.split()) or None


def lookup_abstracts(dois: Iterable[str], fetch: Callable = _europepmc_abstract) -> tuple[dict, int]:
    """{doi: abstract} from Europe PMC, and how many lookups failed (best effort)."""
    found, failed = {}, 0
    for doi in sorted({clean_doi(d) for d in dois if _is_doi(clean_doi(d))}):
        try:
            abstract = _cached("europepmc", doi, lambda: fetch(doi))
        except Exception as exc:
            print(f"[adjudication] Europe PMC lookup failed for {doi}: {exc}")
            failed += 1
            continue
        if abstract:
            found[doi] = abstract
    return found, failed


# ---------------------------------------------------------------------------
# Rows → records
# ---------------------------------------------------------------------------

def _or_none(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else value
    return value or None


def build_records(rows: list[dict], works: dict, abstracts: dict | None = None) -> list[dict]:
    """One record per row, both answers as given, the gaps filled from OpenAlex
    and, for abstracts OpenAlex lacks, Europe PMC."""
    abstracts = abstracts or {}
    records = []
    for row in rows:
        doi_r = clean_doi(row["doi_r"])
        flora_doi = clean_doi(row["our_doi_o"]) or None
        mo_doi = clean_doi(row["mo_doi_o"]) or None
        rep = works.get(doi_r, {})
        abstract, abstract_source = rep.get("abstract"), "openalex"
        if not abstract:
            abstract, abstract_source = abstracts.get(doi_r), "europepmc"
        records.append({
            "import_key": import_key(row),
            "kind": row["kind"],
            "doi_r": doi_r,
            "title_r": _or_none(row["title_r"]) or rep.get("title"),
            "abstract_r": abstract,
            "abstract_source": abstract_source if abstract else None,
            "year_r": rep.get("year"),
            "flora_doi_o": flora_doi,
            "flora_title_o": _or_none(row["our_title_o"]) or works.get(flora_doi or "", {}).get("title"),
            "flora_outcome": _or_none(row["our_outcome"]),
            "flora_outcome_quote": _or_none(row["our_outcome_quote"]),
            "flora_quote_source": _or_none(row["out_quote_source"]),
            "flora_link_method": _or_none(row["our_link_method"]),
            "flora_link_confidence": _or_none(row["our_link_confidence"]),
            "flora_link_evidence": _or_none(row["our_link_evidence"]),
            "mo_doi_o": mo_doi,
            "mo_title_o": works.get(mo_doi or "", {}).get("title"),
            "mo_outcome": _or_none(row["mo_outcome"]),
            "mo_replication_type": _or_none(row["mo_replication_type"]),
            "mo_discipline": _or_none(row["mo_discipline"]),
            "mo_source": _or_none(row["mo_source"]),
            "mo_confidence": _or_none(row["mo_confidence"]),
            "mo_ai_version": _or_none(row["mo_ai_version"]),
            "raw": row,
        })
    return records


def enrichment_summary(records: list[dict], failed_batches: int, failed_abstracts: int = 0) -> dict:
    with_mo = [r for r in records if r["mo_doi_o"]]
    with_flora = [r for r in records if r["flora_doi_o"]]
    replications = {r["doi_r"]: r for r in records}
    return {
        "replications": len(replications),
        "abstracts": sum(1 for r in replications.values() if r["abstract_r"]),
        "abstracts_from_europepmc": sum(1 for r in replications.values()
                                        if r["abstract_source"] == "europepmc"),
        "years": sum(1 for r in replications.values() if r["year_r"]),
        "observatory_originals": len(with_mo),
        "observatory_titles": sum(1 for r in with_mo if r["mo_title_o"]),
        "flora_originals": len(with_flora),
        "flora_titles": sum(1 for r in with_flora if r["flora_title_o"]),
        "failed_batches": failed_batches,
        "failed_abstracts": failed_abstracts,
    }


# ---------------------------------------------------------------------------
# Plan and apply
# ---------------------------------------------------------------------------

def _comparable(record: dict) -> dict:
    return {f: record[f] for f in RECORD_FIELDS}


# Values the lookups add, each with the fields that travel with it. The lookups
# are best effort, so finding nothing this time means "not found now", never
# "gone": a re-import during an outage must not erase what an earlier one found.
_ENRICHED = (("abstract_r", "abstract_source"), ("year_r",),
             ("flora_title_o",), ("mo_title_o",), ("title_r",))


def _keep_earlier_enrichment(record: dict, current: dict) -> None:
    for group in _ENRICHED:
        if record[group[0]] is None and current[group[0]] is not None:
            for field in group:
                record[field] = current[field]


def plan(cur, records: list[dict]) -> dict:
    """What an apply would do: new, updated, unchanged, or kept (already judged).

    Fills each record's lookup gaps from the stored row first (see _ENRICHED),
    so the comparison and the write never treat a failed lookup as a change."""
    cur.execute(
        f"""
        SELECT r.import_key, {', '.join('r.' + f for f in RECORD_FIELDS)},
               EXISTS (SELECT 1 FROM adjudication.judgements j
                       WHERE j.record_id = r.record_id AND j.state = 'submitted') AS judged
        FROM adjudication.records r
        WHERE r.import_key = ANY(%s)
        """,
        ([r["import_key"] for r in records],),
    )
    existing = {row["import_key"]: row for row in cur.fetchall()}
    actions = {}
    for record in records:
        current = existing.get(record["import_key"])
        if current is not None:
            _keep_earlier_enrichment(record, current)
        if current is None:
            actions[record["import_key"]] = "new"
        elif _comparable(record) == {f: current[f] for f in RECORD_FIELDS}:
            actions[record["import_key"]] = "unchanged"
        elif current["judged"]:
            actions[record["import_key"]] = "kept"
        else:
            actions[record["import_key"]] = "updated"
    return actions


def _write(cur, records: list[dict], actions: dict, imported_by: str) -> None:
    columns = ("import_key", "imported_from", "imported_by") + RECORD_FIELDS
    for record in records:
        action = actions[record["import_key"]]
        values = {**record, "imported_from": SOURCE_LABEL, "imported_by": imported_by,
                  "raw": json.dumps(record["raw"], ensure_ascii=False)}
        if action == "new":
            cur.execute(
                f"INSERT INTO adjudication.records ({', '.join(columns)}) "
                f"VALUES ({', '.join('%s::jsonb' if c == 'raw' else '%s' for c in columns)})",
                tuple(values[c] for c in columns),
            )
        elif action == "updated":
            assignments = ", ".join(f"{f} = %s::jsonb" if f == "raw" else f"{f} = %s"
                                    for f in RECORD_FIELDS)
            cur.execute(
                f"UPDATE adjudication.records SET {assignments}, imported_from = %s, "
                f"imported_by = %s, updated_at = NOW() WHERE import_key = %s "
                f"AND NOT EXISTS (SELECT 1 FROM adjudication.judgements j "
                f"WHERE j.record_id = adjudication.records.record_id AND j.state = 'submitted')",
                tuple(values[f] for f in RECORD_FIELDS)
                + (SOURCE_LABEL, imported_by, record["import_key"]),
            )


def run_import(cursor: Callable, *, apply: bool, imported_by: str,
               content: bytes | None = None, lookup: Callable = lookup_works,
               abstracts: Callable = lookup_abstracts) -> dict:
    """Read, enrich, plan and (with *apply*) write. *cursor* is a context manager
    factory yielding a dict cursor in one transaction (app.py's db())."""
    content = fetch_source() if content is None else content
    rows = read_rows(content)
    dois = ([r["doi_r"] for r in rows] + [r["mo_doi_o"] for r in rows]
            + [r["our_doi_o"] for r in rows if not r["our_title_o"]])
    works, failed = lookup(dois)
    lacking = [r["doi_r"] for r in rows
               if not (works.get(clean_doi(r["doi_r"])) or {}).get("abstract")]
    from_europepmc, failed_abstracts = abstracts(lacking)
    records = build_records(rows, works, from_europepmc)

    with cursor() as cur:
        if not apply:
            # Postgres itself refuses any write in a dry run.
            cur.execute("SET TRANSACTION READ ONLY")
        else:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (IMPORT_LOCK_ID,))
            # And judging's lock, so no judgement is submitted between the plan
            # below and the writes: a judged row must never change.
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (JUDGING_LOCK_ID,))
        actions = plan(cur, records)
        if apply:
            _write(cur, records, actions, imported_by)

    return {
        "source": SOURCE_LABEL,
        "source_url": SOURCE_URL,
        "sha256": hashlib.sha256(content).hexdigest(),
        "rows": len(rows),
        "by_kind": dict(Counter(r["kind"] for r in rows).most_common()),
        "actions": {a: sum(1 for v in actions.values() if v == a)
                    for a in ("new", "updated", "unchanged", "kept")},
        "enrichment": enrichment_summary(records, failed, failed_abstracts),
        "applied": apply,
    }


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _cli_cursor(database_url: str):
    import psycopg2
    import psycopg2.extras

    @contextmanager
    def cursor():
        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return cursor


def main(argv=None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    parser.add_argument("--csv", default=None, help=f"a local file instead of {SOURCE_LABEL}")
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")
    content = open(args.csv, "rb").read() if args.csv else None
    summary = run_import(_cli_cursor(database_url), apply=args.apply, imported_by="cli",
                         content=content)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not args.apply:
        print("\nDry run: nothing written. Add --apply to import.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
