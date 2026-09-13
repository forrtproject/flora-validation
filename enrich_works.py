"""enrich_works.py — bibliographic metadata for every DOI in the FLoRA product.

Fills the enrichment half of the FLoRA output contract: title, authors, journal,
year, volume, issue, pages, language, oa_url and bibtex_ref, for both sides of each
pair. These are the columns the R notebook fetched from CrossRef (Step 6b), OpenAlex
(Step 9) and Unpaywall (Step 9c) on every render.

ONE SOURCE, NOT THREE
---------------------
OpenAlex carries all of it: `biblio` has volume/issue/pages, `language` is a field,
and `open_access.oa_url` is the Unpaywall data OpenAlex already ingests. So one API
replaces three, and it answers 50 DOIs per request instead of one — ~100 requests for
the whole corpus rather than ~15,000.

WHAT IS NOT TAKEN FROM HERE
---------------------------
`apa_ref_o` / `apa_ref_r` keep the entry sheets' own reference strings, which are
already real APA and 100% populated. The R pipeline prefers CrossRef's formatted
citation and falls back to the sheet (`coalesce(ref_o_clean, ref_o)`); with no
CrossRef APA to prefer, the fallback is simply the value. Synthesising a worse APA
string over a good one would be a downgrade, not a fill.

`bibtex_ref` IS synthesised from the structured fields — the same thing the R side
does via `synthesise_missing_refs_from_fields` when CrossRef returns no BibTeX.

CACHING
-------
`work_metadata`, keyed on the cleaned DOI. Safe and cheap to re-run: only DOIs with
no row are fetched. A DOI OpenAlex does not hold is recorded with `not_found`, so a
permanent miss is not re-requested nightly; pass --retry-missing to try those again.

Usage:
    python enrich_works.py
    python enrich_works.py --dry-run
    python enrich_works.py --retry-missing
    python enrich_works.py --limit 200

Required environment variables:
    DATABASE_URL    — PostgreSQL connection string
    OPENALEX_MAILTO — contact address for OpenAlex's polite pool
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging

load_dotenv()
use_utf8_output()

API = "https://api.openalex.org/works"
MAILTO = os.environ.get("OPENALEX_MAILTO", "lukas.wallrich@gmail.com")

# OpenAlex accepts up to 50 values in an OR filter.
BATCH = 50
TIMEOUT = 60
RETRIES = 3

# The polite pool allows 10 requests/second. One every 0.15s is well inside it and
# still finishes the whole corpus in under a minute of request time.
DELAY = 0.15

FIELDS = ("id,doi,title,display_name,publication_year,language,biblio,"
          "authorships,primary_location,open_access,best_oa_location,type")


class EnrichmentError(RuntimeError):
    """OpenAlex could not be reached or answered with an error."""


def _norm_doi(value) -> str:
    """Match transform_sources.clean_doi's output, which is what the join uses."""
    if not value:
        return ""
    v = str(value).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return v.strip()


def _fetch_batch(dois: list) -> list:
    """One OpenAlex request for up to 50 DOIs."""
    filt = "doi:" + "|".join(dois)
    url = (f"{API}?filter={urllib.parse.quote(filt, safe='|:/().-_')}"
           f"&per-page={BATCH}&select={FIELDS}&mailto={MAILTO}")
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "flora-validation/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read()).get("results", [])
        except Exception as exc:                       # noqa: BLE001 - see below
            # Deliberately broad: a truncated response raises IncompleteRead or
            # ConnectionResetError, neither of which is a URLError, and letting one
            # escape would abort a run that is 90 batches in.
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
    raise EnrichmentError(f"OpenAlex request failed after {RETRIES} attempts: {last}")


# ── shaping one work ──────────────────────────────────────────────────────────

def _authors(work: dict) -> "str | None":
    names = [
        (a.get("author") or {}).get("display_name")
        for a in (work.get("authorships") or [])
    ]
    names = [n for n in names if n]
    return "; ".join(names) or None


def _journal(work: dict) -> "str | None":
    source = (work.get("primary_location") or {}).get("source") or {}
    return source.get("display_name") or None


def _pages(biblio: dict) -> "str | None":
    first, last = biblio.get("first_page"), biblio.get("last_page")
    if first and last and first != last:
        return f"{first}-{last}"
    return first or last or None


def _oa_url(work: dict) -> "str | None":
    """Prefer a direct PDF, then the landing page, then OpenAlex's own oa_url.

    open_access.oa_url is frequently just https://doi.org/<doi>, which says the paper
    is open but adds nothing a reader does not already have — so it goes last.
    """
    best = work.get("best_oa_location") or {}
    return (best.get("pdf_url")
            or best.get("landing_page_url")
            or (work.get("open_access") or {}).get("oa_url")
            or None)


def _bibtex(work: dict, doi: str, row: dict) -> "str | None":
    """A BibTeX entry from the structured fields.

    Synthesised rather than fetched: CrossRef serves BibTeX one DOI per request, and
    the R pipeline already treats synthesis-from-fields as an acceptable fallback.
    Returns None without a title, because an entry with no title is not usable.
    """
    if not row.get("title"):
        return None
    key = doi.replace("/", "_").replace(".", "_")
    authors = (row.get("authors") or "").replace("; ", " and ")
    entry_type = "article" if (work.get("type") or "") == "article" else "misc"
    parts = [f"@{entry_type}{{{key},"]
    for field, value in (("title", row.get("title")), ("author", authors),
                         ("journal", row.get("journal")), ("year", row.get("year")),
                         ("volume", row.get("volume")), ("number", row.get("issue")),
                         ("pages", row.get("pages")), ("doi", doi)):
        if value:
            # Braces in a title would unbalance the entry.
            safe = str(value).replace("{", "(").replace("}", ")")
            parts.append(f"  {field} = {{{safe}}},")
    parts.append("}")
    return "\n".join(parts)


def shape(work: dict) -> "tuple | None":
    """OpenAlex work -> a work_metadata row. None if it carries no usable DOI."""
    doi = _norm_doi(work.get("doi"))
    if not doi:
        return None
    biblio = work.get("biblio") or {}
    row = {
        "doi": doi,
        "oa_work_id": (work.get("id") or "").rsplit("/", 1)[-1] or None,
        "title": work.get("title") or work.get("display_name") or None,
        "authors": _authors(work),
        "journal": _journal(work),
        "year": str(work["publication_year"]) if work.get("publication_year") else None,
        "volume": biblio.get("volume") or None,
        "issue": biblio.get("issue") or None,
        "pages": _pages(biblio),
        "language": work.get("language") or None,
        "oa_url": _oa_url(work),
    }
    row["bibtex_ref"] = _bibtex(work, doi, row)
    return row


# ── database ──────────────────────────────────────────────────────────────────

def dois_in_product(cur) -> list:
    """Every distinct cleaned DOI the FLoRA product references, both sides.

    Read from source_records rather than by running the transform: the transform is
    slower, and enrichment is keyed on the DOI alone — a row being deduplicated away
    does not change what its DOI resolves to.
    """
    cur.execute(
        """
        SELECT DISTINCT doi FROM (
            SELECT doi_o AS doi FROM source_records
             WHERE duplicate_status IS DISTINCT FROM 'duplicate'
            UNION ALL
            SELECT doi_r FROM source_records
             WHERE duplicate_status IS DISTINCT FROM 'duplicate'
        ) t
        WHERE doi IS NOT NULL AND btrim(doi) <> ''
        """
    )
    seen, out = set(), []
    for record in cur.fetchall():
        # Cleaned the same way the transform cleans, so the join keys agree.
        from transform_sources import clean_doi
        doi = clean_doi(record["doi"])
        if doi and not doi.lower().startswith("dummy") and doi not in seen:
            seen.add(doi)
            out.append(doi)
    return out


def known_dois(cur, retry_missing: bool = False) -> set:
    sql = "SELECT doi FROM work_metadata"
    if retry_missing:
        sql += " WHERE NOT not_found"
    cur.execute(sql)
    return {r["doi"] for r in cur.fetchall()}


def _store(cur, rows: list) -> None:
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO work_metadata
            (doi, oa_work_id, title, authors, journal, year, volume, issue, pages,
             language, oa_url, bibtex_ref, metadata_source, not_found, fetched_at)
        VALUES (%(doi)s, %(oa_work_id)s, %(title)s, %(authors)s, %(journal)s, %(year)s,
                %(volume)s, %(issue)s, %(pages)s, %(language)s, %(oa_url)s,
                %(bibtex_ref)s, 'openalex', FALSE, NOW())
        ON CONFLICT (doi) DO UPDATE SET
            oa_work_id = EXCLUDED.oa_work_id, title = EXCLUDED.title,
            authors = EXCLUDED.authors, journal = EXCLUDED.journal,
            year = EXCLUDED.year, volume = EXCLUDED.volume, issue = EXCLUDED.issue,
            pages = EXCLUDED.pages, language = EXCLUDED.language,
            oa_url = EXCLUDED.oa_url, bibtex_ref = EXCLUDED.bibtex_ref,
            not_found = FALSE, fetched_at = NOW()
        """,
        rows,
    )


def _store_misses(cur, dois: list) -> None:
    """Recorded so a DOI OpenAlex genuinely lacks is not re-requested every night."""
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO work_metadata (doi, not_found, metadata_source, fetched_at)
        VALUES (%s, TRUE, 'openalex', NOW())
        ON CONFLICT (doi) DO UPDATE SET not_found = TRUE, fetched_at = NOW()
        """,
        [(d,) for d in dois],
    )


def enrich(cur, dry_run: bool = False, retry_missing: bool = False,
           limit: int = 0, verbose: bool = True) -> dict:
    def say(*args):
        if verbose:
            print(*args)

    wanted = dois_in_product(cur)
    have = known_dois(cur, retry_missing)
    todo = [d for d in wanted if d not in have]
    if limit:
        todo = todo[:limit]

    say(f"  DOIs in the product: {len(wanted)}")
    say(f"  already cached:      {len(wanted) - len([d for d in wanted if d not in have])}")
    say(f"  to fetch:            {len(todo)}")

    if dry_run or not todo:
        if dry_run:
            say("\n[dry-run] nothing written")
        return {"wanted": len(wanted), "fetched": 0, "missing": 0, "todo": len(todo)}

    fetched = missing = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        works = _fetch_batch(batch)
        rows = [r for r in (shape(w) for w in works) if r]
        found = {r["doi"] for r in rows}
        absent = [d for d in batch if d not in found]

        if rows:
            _store(cur, rows)
        if absent:
            _store_misses(cur, absent)
        fetched += len(rows)
        missing += len(absent)

        done = min(start + BATCH, len(todo))
        say(f"    {done}/{len(todo)}  (+{len(rows)} found, {len(absent)} not in OpenAlex)")
        time.sleep(DELAY)

    return {"wanted": len(wanted), "fetched": fetched, "missing": missing,
            "todo": len(todo)}


# ── works that have no DOI, only an OpenAlex id ───────────────────────────────
#
# A port of augment_with_openalex_url_refs_r() in R/openalex_cache.R, which the
# notebook calls at Step 7b.
#
# Theses, working papers and conference reports frequently have no DOI, and the
# coding sheets record them as a plain OpenAlex link in url_r
# (https://openalex.org/W2186305685). Enrichment keyed on the DOI alone cannot see
# them, so those rows reach the product with no title, no authors and no year —
# and would be dropped outright by the notebook's title filter.
#
# They are cached in work_metadata under the identifier they were looked up with,
# so `doi` holds a W-id for these rows. The two namespaces cannot collide (a DOI
# always begins "10.") and metadata_source marks which is which.

WORK_ID_SOURCE = "openalex-workid"
WORK_ID_RE = re.compile(r"W\d{5,}")


def extract_work_id(value) -> "str | None":
    """The bare W-id from an OpenAlex URL, or from an already-bare id."""
    if value is None:
        return None
    match = WORK_ID_RE.search(str(value))
    return match.group(0) if match else None


def work_ids_in_product(cur) -> list:
    """W-ids referenced by a url column on a row whose DOI on that side is blank.

    Restricted to rows with no DOI on purpose: where a DOI exists it is the better
    key — it is what the rest of the pipeline joins on — and re-fetching the same
    work by a second identifier would only cost requests.
    """
    cur.execute(
        """
        SELECT DISTINCT url FROM (
            SELECT url_o AS url FROM source_records
             WHERE duplicate_status IS DISTINCT FROM 'duplicate'
               AND (doi_o IS NULL OR btrim(doi_o) = '')
            UNION ALL
            SELECT url_r FROM source_records
             WHERE duplicate_status IS DISTINCT FROM 'duplicate'
               AND (doi_r IS NULL OR btrim(doi_r) = '')
        ) t
        WHERE url ~* 'openalex\\.org/W[0-9]+'
        """
    )
    seen, out = set(), []
    for record in cur.fetchall():
        work_id = extract_work_id(record["url"])
        if work_id and work_id not in seen:
            seen.add(work_id)
            out.append(work_id)
    return out


def _fetch_one_by_id(work_id: str) -> "dict | None":
    """One work by its OpenAlex id.

    One request per id, as the R does: there are a couple of dozen of these, and
    the id filter is a different endpoint shape from the DOI batch filter. A work
    that no longer exists (merged or withdrawn) answers 404, which is a miss rather
    than an error.
    """
    url = f"{API}/{work_id}?select={FIELDS}&mailto={MAILTO}"
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "flora-validation/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            last = exc
        except Exception as exc:                       # noqa: BLE001 - as _fetch_batch
            last = exc
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise EnrichmentError(
        f"OpenAlex request for {work_id} failed after {RETRIES} attempts: {last}")


def shape_by_work_id(work: dict, work_id: str) -> "dict | None":
    """OpenAlex work -> a work_metadata row keyed by the W-id rather than the DOI.

    Unlike shape(), a missing DOI is the expected case: these are the works that
    have none. Without a title there is nothing worth caching, so that is the one
    thing it refuses.
    """
    row = shape(work)
    if row is None:
        biblio = work.get("biblio") or {}
        row = {
            "doi": work_id,
            "oa_work_id": (work.get("id") or "").rsplit("/", 1)[-1] or work_id,
            "title": work.get("title") or work.get("display_name") or None,
            "authors": _authors(work),
            "journal": _journal(work),
            "year": (str(work["publication_year"])
                     if work.get("publication_year") else None),
            "volume": biblio.get("volume") or None,
            "issue": biblio.get("issue") or None,
            "pages": _pages(biblio),
            "language": work.get("language") or None,
            "oa_url": _oa_url(work),
        }
        row["bibtex_ref"] = _bibtex(work, work_id, row)
    else:
        # OpenAlex does hold a DOI for it after all. Cached under the W-id anyway,
        # because that is the key the row will be looked up by: the source sheet
        # has no DOI, so nothing downstream knows this one exists.
        row = dict(row, doi=work_id)
        row["bibtex_ref"] = _bibtex(work, work_id, row)
    if not row.get("title"):
        return None
    return row


def enrich_work_ids(cur, dry_run: bool = False, retry_missing: bool = False,
                    verbose: bool = True) -> dict:
    def say(*args):
        if verbose:
            print(*args)

    wanted = work_ids_in_product(cur)
    have = known_dois(cur, retry_missing)
    todo = [w for w in wanted if w not in have]

    say(f"  OpenAlex-only works (no DOI): {len(wanted)}")
    say(f"  to fetch:                     {len(todo)}")

    if dry_run or not todo:
        if dry_run and todo:
            say("  [dry-run] nothing written")
        return {"wanted": len(wanted), "fetched": 0, "missing": 0}

    rows, absent = [], []
    for work_id in todo:
        work = _fetch_one_by_id(work_id)
        row = shape_by_work_id(work, work_id) if work else None
        if row:
            rows.append(row)
        else:
            absent.append(work_id)
        time.sleep(DELAY)

    if rows:
        _store(cur, rows)
    if absent:
        _store_misses(cur, absent)
    say(f"    +{len(rows)} found, {len(absent)} not in OpenAlex")
    return {"wanted": len(wanted), "fetched": len(rows), "missing": len(absent)}


def load_metadata(cur) -> dict:
    """Every cached row, keyed by DOI, for the transform to join against."""
    cur.execute(
        """
        SELECT doi, oa_work_id, title, authors, journal, year, volume, issue,
               pages, language, oa_url, bibtex_ref
        FROM work_metadata WHERE NOT not_found
        """
    )
    return {r["doi"]: dict(r) for r in cur.fetchall()}


def main() -> int:
    start_logging("enrich-works")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be fetched; write nothing")
    parser.add_argument("--retry-missing", action="store_true",
                        help="also re-request DOIs previously recorded as not found")
    parser.add_argument("--limit", type=int, default=0,
                        help="fetch at most this many DOIs (for a first look)")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    print("=== OpenAlex enrichment ===")
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            stats = enrich(cur, dry_run=args.dry_run,
                           retry_missing=args.retry_missing, limit=args.limit)
            # --limit is a first-look switch for the DOI corpus; the work-id set is
            # a couple of dozen rows, so it always runs in full.
            id_stats = enrich_work_ids(cur, dry_run=args.dry_run,
                                       retry_missing=args.retry_missing)
            stats["fetched"] += id_stats["fetched"]
            stats["missing"] += id_stats["missing"]
            stats["work_ids"] = id_stats["wanted"]
        if not args.dry_run:
            conn.commit()
    except EnrichmentError as exc:
        conn.rollback()
        print(f"  FAILED - {exc}")
        return 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\n  fetched {stats['fetched']} work(s); "
          f"{stats['missing']} DOI(s) not held by OpenAlex")
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
