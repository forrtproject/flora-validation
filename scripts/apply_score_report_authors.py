"""Apply reviewed SCORE bylines, or explicitly mark unestablished authors unknown.

Default: validate files offline. --check-live preflights in a read-only database
transaction. --apply uses the ordinary versioned correction service and commits
all changes together; it never changes identifiers, report URLs or outcomes.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BATCH = ROOT / 'data/corrections/score_report_authors.json'
AUDIT = ROOT / 'data/corrections/score_report_audit.csv'
FIELDS = {'ref_r', 'title_r', 'author_r', 'year_r', 'journal_r', 'bibtex_ref_r'}


def load_batch(path=BATCH):
    """Validate exact field scope and require explicit evidence for unknown authors."""
    import flora_edits
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    with AUDIT.open(encoding='utf-8-sig', newline='') as f:
        audit = {r['row_id']: r for r in csv.DictReader(f)}
    ids = set()
    for row in rows:
        if row['id'] in ids or set(row['fields']) != FIELDS:
            raise ValueError('Duplicate id or unexpected correction fields')
        ids.add(row['id'])
        if (not row['before_ref_r'] or not row['note']
                or not all(k in row for k in ('before_url_r', 'before_doi_o', 'type'))):
            raise ValueError('Every correction needs reference/identity baselines and evidence note')
        if row['type'] not in {'replication', 'reproduction'}:
            raise ValueError('Invalid record type')
        for field, value in row['fields'].items():
            flora_edits.clean_value(field, value, row['type'])
        flora_edits._checked_notes({field: row['note'] for field in FIELDS})
        if row.get('author_status') == 'unknown':
            evidence = audit.get(row['id'], {})
            if (evidence.get('status') != 'unresolved' or not evidence.get('reason')
                    or evidence.get('disposition') != 'explicit_unknown_author'
                    or row['fields']['author_r'] is not None):
                raise ValueError('Unknown authors require a held audit record and a null author field')
            if (any(row[k] != evidence[v] for k, v in
                    [('before_ref_r', 'before'), ('before_url_r', 'url_r'), ('before_doi_o', 'doi_o')])
                    or row['fields']['ref_r'] != evidence['after']):
                raise ValueError('Unknown-author correction differs from retained audit evidence')
            if re.search(r'\bauthor\s*=', row['fields']['bibtex_ref_r'], re.I):
                raise ValueError('Unknown authors must not credit contributors in BibTeX')
        elif row.get('author_status') == 'named':
            authors = json.loads(row['fields']['author_r'] or '[]')
            if not any(isinstance(a, dict) and isinstance(a.get('family'), str)
                       and a['family'].strip() for a in authors):
                raise ValueError('Named corrections need at least one author family name')
        else:
            raise ValueError('Every correction must state named or unknown authorship')
    return rows


def _same(actual, expected):
    """Compare nullable cells without confusing None with the text 'None'."""
    return ('' if actual is None else str(actual)) == ('' if expected is None else str(expected))


def check_identity(record, row):
    """Require the report URL, original DOI and attempt type reviewed in the audit."""
    if record is None:
        raise ValueError(f"Record unavailable: {row['id']}")
    for field, baseline in [('url_r', 'before_url_r'), ('doi_o', 'before_doi_o'), ('type', 'type')]:
        if not _same(record.get(field), row[baseline]):
            raise ValueError(f"Report identity changed since audit: {row['id']} ({field})")


def preflight(cur, rows):
    """Check the entire batch before saving any row; retain strict reference guards."""
    import flora_service
    import flora_edits
    pending = []
    for row in rows:
        version = flora_edits.version(cur, row['id'])
        record = flora_service.get_record(cur, row['id'])
        check_identity(record, row)
        if all(_same(record.get(k), v) for k, v in row['fields'].items()):
            continue
        if record.get('ref_r') != row['before_ref_r']:
            raise ValueError(f"Reference changed since audit: {row['id']}")
        if not record.get('export_id'):
            raise ValueError(f"Record has no published identity: {row['id']}")
        pending.append((row, version))
    return pending


def apply_batch(cur, rows, admin):
    """Save only bibliographic fields through the existing correction service."""
    import flora_service
    pending = preflight(cur, rows)
    for row, version in pending:
        flora_service.update_record(cur, row['id'], row['fields'], [], version,
                                    admin, {k: row['note'] for k in row['fields']})
    for row, _ in pending:
        check_identity(flora_service.get_record(cur, row['id']), row)
    return len(pending)


def main():
    """Validate offline, check live read-only, or explicitly apply an atomic batch."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check-live', action='store_true')
    parser.add_argument('--admin-id', type=int)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    rows = load_batch()
    if not args.apply and not args.check_live:
        print(f'Validated {len(rows)} corrections; no database opened.')
        return
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    if args.check_live:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        conn.set_session(readonly=True)
        try:
            with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                pending = preflight(cur, rows)
            print(f'Ready: {len(pending)}; already applied: {len(rows) - len(pending)}; no writes.')
        finally:
            conn.close()
        return
    if args.admin_id is None:
        parser.error('--apply requires --admin-id')
    from db_pool import cursor
    with cursor(os.environ['DATABASE_URL']) as cur:
        cur.execute('SELECT id, handle FROM admins WHERE id = %s', (args.admin_id,))
        admin = cur.fetchone()
        if not admin:
            raise ValueError('Administrator does not exist')
        count = apply_batch(cur, rows, admin)
    print(f'Applied {count} corrections through flora_edits; identifiers and outcomes untouched.')


if __name__ == '__main__':
    main()
