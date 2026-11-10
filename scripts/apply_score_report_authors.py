"""Apply the reviewed SCORE batch through the ordinary FLoRA correction log.

Default: validate the batch without opening a database. --apply requires the
maintainer's configured DATABASE_URL and an existing administrator id. All rows
are checked before any are saved, and the transaction rolls back on failure.
"""
import argparse
import json
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BATCH = ROOT / 'data/corrections/score_report_authors.json'
FIELDS = {'ref_r', 'title_r', 'author_r', 'year_r', 'journal_r', 'bibtex_ref_r'}


def load_batch(path=BATCH):
    rows = json.loads(Path(path).read_text())
    ids = set()
    for row in rows:
        if row['id'] in ids or set(row['fields']) != FIELDS:
            raise ValueError('Duplicate id or unexpected correction fields')
        ids.add(row['id'])
        if not row['before_ref_r'] or not row['note']:
            raise ValueError('Every correction needs a baseline and evidence note')
        if not json.loads(row['fields']['author_r']):
            raise ValueError('Every correction needs named authors')
    return rows


def apply_batch(cur, rows, admin):
    import flora_service
    pending = []
    for row in rows:
        record = flora_service.get_record(cur, row['id'])
        if record is None:
            raise ValueError(f"Record unavailable: {row['id']}")
        if all(str(record.get(k) or '') == str(v) for k, v in row['fields'].items()):
            continue
        if record.get('ref_r') != row['before_ref_r']:
            raise ValueError(f"Reference changed since audit: {row['id']}")
        info = flora_service.editing(cur, record)
        if info is None:
            raise ValueError(f"Record has no published identity: {row['id']}")
        pending.append((row, info['version']))
    for row, version in pending:
        flora_service.update_record(cur, row['id'], row['fields'], [], version,
                                    admin, {k: row['note'] for k in row['fields']})
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--admin-id', type=int)
    args = parser.parse_args()
    rows = load_batch()
    if not args.apply:
        print(f'Validated {len(rows)} corrections; no database opened.')
        return
    if args.admin_id is None:
        parser.error('--apply requires --admin-id')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    from db_pool import cursor
    with cursor(os.environ['DATABASE_URL']) as cur:
        cur.execute('SELECT * FROM admins WHERE id = %s', (args.admin_id,))
        admin = cur.fetchone()
        if not admin:
            raise ValueError('Administrator does not exist')
        count = apply_batch(cur, rows, admin)
    print(f'Applied {count} corrections through flora_edits; outcomes untouched.')


if __name__ == '__main__':
    main()
