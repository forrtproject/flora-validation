"""Fill verified missing SCORE report DOIs through the ordinary correction log.

Dry run validates the evidence file without a database. Application saves all
corrections in one transaction, rebuilds once through flora_service, and refuses
any change to report authorship, citations, outcomes or alternative identifiers.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BATCH = ROOT / 'data/corrections/score_report_dois.csv'


def load_batch(path=BATCH):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        table = list(csv.DictReader(f))
    rows = []
    for r in table:
        evidence = json.loads(r['evidence_extra'] or '{}')
        evidence.update({k: r[k] for k in ('node','identifier_api','evidence_date','method')})
        if r['http_status']:
            evidence['http_status'] = int(r['http_status'])
        evidence.update(id=r['id'], report_url=r['url_r'], status='verified', dois=[r['doi_r']])
        rows.append({'id': r['id'], 'doi_r': r['doi_r'],
                     'baseline': {k: r[k] for k in ('type','doi_o','url_r','alt_identifier_r')}, 'evidence': evidence})
    seen = set()
    for row in rows:
        if row['id'] in seen:
            raise ValueError('Duplicate correction')
        seen.add(row['id'])
        if row['doi_r'] not in row['evidence']['dois']:
            raise ValueError('DOI was not returned by the identifier endpoint')
        if not row['doi_r'].lower().startswith('10.17605/osf.io/'):
            raise ValueError('Expected a registered OSF report DOI')
        if row['doi_r'].split('/')[-1].lower() != row['evidence']['node'].lower():
            raise ValueError('DOI does not identify the verified report component')
    return rows


def _records(frame):
    from flora_service import _cell
    if frame['export_id'].dropna().duplicated().any():
        raise ValueError('Ambiguous published identity')
    return {r['export_id']: {k: _cell(v) for k, v in r.items()}
            for r in frame.to_dict(orient='records') if r.get('export_id')}


def apply_batch(cur, rows, admin):
    import flora_edits
    import flora_service
    from scripts.apply_score_paper_identifiers import _baseline

    frame = flora_service.dataset(cur)
    before = _records(frame)
    ids = [r['id'] for r in rows]
    cur.execute('SELECT id FROM flora_data WHERE id = ANY(%s) AND retired_at IS NULL', (ids,))
    live = {r['id'] for r in cur.fetchall()}
    cur.execute('SELECT export_id, MAX(change_id) AS version FROM flora_record_changes '
                'WHERE export_id = ANY(%s) GROUP BY export_id', (ids,))
    versions = {r['export_id']: int(r['version']) for r in cur.fetchall()}
    pending = []
    for row in rows:
        record = before.get(row['id'])
        if record is None or row['id'] not in live:
            raise ValueError(f"Record is not published: {row['id']}")
        if _baseline(record.get('doi_r')).lower() == row['doi_r'].lower():
            continue
        if _baseline(record.get('doi_r')):
            raise ValueError(f"Refusing to replace an existing DOI: {row['id']}")
        for field, expected in row['baseline'].items():
            if _baseline(record.get(field)) != _baseline(expected):
                raise ValueError(f"{field} changed since audit: {row['id']}")
        pending.append(row)
    changes = []
    for row in pending:
        record = before[row['id']]
        derived = (frame.attrs.get('flora_edits') or {}).get(row['id']) or {}
        note = ('Fill missing individual-report DOI; registered identifier verified at '
                + row['evidence']['identifier_api']
                + '. Keep the SCORE paper in alt_identifier_r and retain the report URL/byline.')
        saved = flora_edits.save(cur, record, derived, {'doi_r': row['doi_r']}, [],
                                 versions.get(row['id'], 0), admin, {'doi_r': note})
        if not saved['change_id']:
            raise ValueError(f"DOI correction was not logged: {row['id']}")
        changes.append({'id': row['id'], 'change_id': saved['change_id'],
                        'before': record.get('doi_r'), 'after': row['doi_r']})
    if not changes:
        return []
    # Unlike an alternative identifier, a primary DOI participates in the normal
    # metadata join. Rebuild with the saved corrections before publishing them.
    after = _records(flora_service.dataset(cur))
    allowed = {'doi_r', 'doi_r_hash', 'oa_work_id_r', 'edited_fields'}
    for change in changes:
        identifier = change['id']
        if identifier not in after:
            raise ValueError(f'Corrected record disappeared: {identifier}')
        moved = {k for k in before[identifier].keys() | after[identifier].keys()
                 if before[identifier].get(k) != after[identifier].get(k)}
        if moved - allowed:
            raise ValueError(f'Metadata changed for {identifier}: {sorted(moved - allowed)}')
        if after[identifier]['doi_r'].lower() != change['after'].lower():
            raise ValueError(f'Corrected DOI missing after rebuild: {identifier}')
        change['after'] = after[identifier]['doi_r']
    for change in changes:
        identifier = change['id']
        if not flora_edits.publish(cur, before[identifier], after[identifier]):
            raise ValueError(f'DOI correction was not published: {identifier}')
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--admin-id', type=int)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    rows = load_batch()
    if not args.apply:
        print(f'Validated {len(rows)} registered report DOIs; no database opened.')
        return
    if args.admin_id is None:
        parser.error('--apply requires --admin-id')
    from dotenv import load_dotenv
    from db_pool import cursor
    load_dotenv(args.env_file)
    with cursor(os.environ['DATABASE_URL']) as cur:
        cur.execute('SELECT id, handle FROM admins WHERE id = %s', (args.admin_id,))
        admin = cur.fetchone()
        if not admin:
            raise ValueError('Administrator does not exist')
        receipt = apply_batch(cur, rows, admin)
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(f'Committed {len(receipt)} missing report DOIs; SCORE alternatives preserved.')


if __name__ == '__main__':
    main()
