"""
backfill_author_orcids.py — Bring back the ORCIDs the cached author lists lost.

Every author list in the metadata cache (work_metadata.authors_json) was written by
bibliographic_helpers.author_fields, which until now kept only given, family and
sequence: Crossref's and DataCite's ORCIDs were dropped, and so flora.csv carried
none, where fred-data's carries them for 1,292 records. author_fields keeps them
now, for every new lookup. This refreshes the lists already cached for every DOI
row (manual, OSF and Crossref-not-found entries are left alone), asking in the
order a new lookup does and taking the first answer with authors:

    https://api.crossref.org/works/{doi}
    https://api.datacite.org/dois/{doi}
    https://doi.org/{doi} as CSL JSON

Only authors_json and authors change; nothing else in the row, and a row no
provider answers with authors keeps its list. A dry run unless given --apply.

Usage:
    python backfill_author_orcids.py                 # dry run: what would change
    python backfill_author_orcids.py --limit 50      # a sample
    python backfill_author_orcids.py --apply

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import json
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import bibliographic_helpers as references
from console_encoding import use_utf8_output

load_dotenv()
use_utf8_output()

MAILTO = os.environ.get("OPENALEX_MAILTO", "lukas.wallrich@gmail.com")
LEFT_ALONE = ("manual", "osf", "crossref_not_found")


def cached_rows(cur, limit=0):
    cur.execute(
        """
        SELECT doi, metadata_source, authors, authors_json FROM work_metadata
        WHERE NOT not_found AND authors_json IS NOT NULL
          AND COALESCE(metadata_source, '') <> ALL(%s)
        ORDER BY doi
        """ + (" LIMIT %s" % int(limit) if limit else ""),
        (list(LEFT_ALONE),),
    )
    return [dict(row) for row in cur.fetchall() if references.is_doi(row["doi"])]


def fetch_authors(row):
    """(authors, authors_json) from the first provider with authors; None if none has.

    The order of bibliographic_helpers.fetch_doi_reference: a row cached from
    "crossref+datacite" took its authors from DataCite, because Crossref had none."""
    encoded = urllib.parse.quote(row["doi"], safe="")
    data = references.request(f"https://api.crossref.org/works/{encoded}?mailto={MAILTO}")
    message = (data or {}).get("message")
    fields = references.author_fields(message.get("author")) if isinstance(message, dict) else {}
    if not fields.get("authors_json"):
        data = references.request(f"https://api.datacite.org/dois/{encoded}")
        attributes = ((data or {}).get("data") or {}).get("attributes")
        fields = references.datacite_fields(attributes) if attributes else {}
    if not fields.get("authors_json"):
        csl = references.request(f"https://doi.org/{encoded}",
                                 "application/vnd.citationstyles.csl+json")
        fields = references.author_fields(csl.get("author")) if isinstance(csl, dict) else {}
    if not fields.get("authors_json"):
        return None
    return fields["authors"], fields["authors_json"]


def _names(authors_json):
    return [(a.get("given"), a.get("family"), a.get("name"))
            for a in json.loads(authors_json or "[]")]


def plan(rows, workers=4):
    """Each row with its refreshed list, and what kind of change that is."""
    def one(row):
        try:
            fresh = fetch_authors(row)
        except references.ReferenceLookupError as exc:
            return row, None, f"lookup failed: {exc}"
        if fresh is None:
            return row, None, "no authors from any provider"
        authors, authors_json = fresh
        if authors_json == row["authors_json"]:
            return row, None, "unchanged"
        kind = ("adds ORCIDs" if "ORCID" in authors_json and _names(authors_json) == _names(row["authors_json"])
                else "names changed" if _names(authors_json) != _names(row["authors_json"])
                else "format only")
        return row, (authors, authors_json), kind
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, rows))


def write(cur, results):
    updates = [(authors, authors_json, row["doi"])
               for row, fresh, _ in results if fresh for authors, authors_json in [fresh]]
    psycopg2.extras.execute_batch(
        cur, "UPDATE work_metadata SET authors = %s, authors_json = %s WHERE doi = %s", updates)
    return len(updates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write; otherwise a dry run")
    parser.add_argument("--limit", type=int, default=0, help="only the first N cached rows")
    parser.add_argument("--workers", type=int, default=4, help="parallel provider requests")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set in the environment or .env")
    conn = psycopg2.connect(database_url)
    try:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            rows = cached_rows(cur, args.limit)
    finally:
        # Closed during the fetch: thousands of requests take longer than an idle
        # connection survives Supabase's pooler. The write opens its own.
        conn.close()

    print(f"Refreshing {len(rows)} cached author list(s) from Crossref, DataCite and doi.org…")
    results = plan(rows, args.workers)
    tally = {}
    for _, _, kind in results:
        key = kind.split(":")[0]
        tally[key] = tally.get(key, 0) + 1
    for kind, n in sorted(tally.items(), key=lambda item: -item[1]):
        print(f"  {n:>5}  {kind}")
    with_orcid = sum(1 for _, fresh, _ in results if fresh and "ORCID" in fresh[1])
    print(f"  {with_orcid} refreshed list(s) carry at least one ORCID.")
    if not args.apply:
        print("\n[dry run] nothing written. Re-run with --apply.")
        return 0

    conn = psycopg2.connect(database_url)
    try:
        with conn, conn.cursor() as cur:               # one transaction: all or nothing
            written = write(cur, results)
    finally:
        conn.close()
    print(f"\n{written} cached author list(s) updated. The next pipeline run publishes them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
