"""Append reviewed SCORE paper DOIs through the FLoRA correction log.

Default: validate the evidence batch locally. --apply uses DATABASE_URL and an
existing administrator ID. All records are checked before writes; the entire
batch commits together. Existing alternative identifiers are retained.
"""
import argparse
import csv
import re
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BATCH = ROOT / 'data/corrections/score_paper_identifiers.csv'
PROVENANCE = ROOT / 'data/corrections/score_identifier_provenance.json'
PAPERS = {
    'replication': ('replicability', '10.1038/s41586-025-10078-y'),
    'reproduction': ('reproducibility', '10.1038/s41586-026-10203-5'),
}


def append_identifier(existing, doi):

    value = str(existing or '').strip()
    if value.lower() in {'', 'na', 'n/a', 'nan', 'none', 'null'}:
        return doi
    identifiers = {'doi:' + m.group().lower().rstrip('.,')
                   for m in re.finditer(r'10\.\d{4,9}/[^\s,;|]+', value, re.I)}
    if 'doi:' + doi in identifiers:
        return value
    other_papers = {paper_doi for _, paper_doi in PAPERS.values()} - {doi}
    if any('doi:' + other in identifiers for other in other_papers):
        raise ValueError('Conflicting SCORE paper DOI already present; review the record')
    return value + '; ' + doi


def load_batch(path=BATCH):
    batch = json.loads(PROVENANCE.read_text(encoding='utf-8'))['papers']
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        table = list(csv.DictReader(f))
    rows = []
    for r in table:
        paper = r['paper']
        evidence = {k: r[k] for k in ('matched_by', 'report_project', 'paper_id', 'rr_id', 'original_doi')}
        evidence['report_ids'] = json.loads(r['report_ids'])
        note = (f"Link SCORE {paper} paper {r['add_alt_identifier_r']}; verified attempt {r['rr_id']} "
                f"(paper {r['paper_id']}) in published {paper} dataset {batch['publications'][paper]['data_url']}. "
                f"Retain the individual report identifiers and URL. flora-validation SCORE paper-link batch, {batch['recorded_on']}.")
        rows.append({'id': r['id'], 'type': r['type'], 'baseline': {k: r[k] for k in ('doi_o','doi_r','url_r')},
                     'paper': paper, 'add_alt_identifier_r': r['add_alt_identifier_r'], 'evidence': evidence, 'note': note})
    seen = set()
    for row in rows:
        if row['id'] in seen:
            raise ValueError('Duplicate record in correction batch')
        seen.add(row['id'])
        paper, doi = PAPERS[row['type']]
        if (row['paper'], row['add_alt_identifier_r']) != (paper, doi):
            raise ValueError('SCORE paper does not match record type')
        if batch['publications'][paper]['doi'] != doi:
            raise ValueError('Publication evidence does not match correction')
        if not all(row['evidence'].get(k) for k in
                   ('report_project', 'rr_id', 'paper_id', 'report_ids', 'matched_by')):
            raise ValueError('Every correction requires report and published-outcome evidence')
        if not row['note'] or len(row['note']) > 500:
            raise ValueError('Every correction requires a note of at most 500 characters')
    return rows


def _baseline(value):
    text = str(value or '').strip()
    return '' if text.lower() in {'na', 'n/a', 'nan', 'none', 'null'} else text


def apply_batch(cur, rows, admin, progress=None):
    import flora_edits
    import flora_service

    # Build once, with existing corrections applied. An alternative-identifier
    # addition changes no metadata-derived field: save/publish are the ordinary
    # correction-log operations, without a full rebuild for every row.
    frame = flora_service.dataset(cur)
    selected = frame[frame['export_id'].isin([r['id'] for r in rows])]
    if selected['export_id'].duplicated().any():
        raise ValueError('Ambiguous published identity in current dataset')
    records = {r['export_id']: {k: flora_service._cell(v) for k, v in r.items()}
               for r in selected.to_dict(orient='records')}
    ids = [r['id'] for r in rows]
    cur.execute('SELECT id FROM flora_data WHERE id = ANY(%s) AND retired_at IS NULL', (ids,))
    live = {r['id'] for r in cur.fetchall()}
    cur.execute('SELECT export_id, MAX(change_id) AS version FROM flora_record_changes '
                'WHERE export_id = ANY(%s) GROUP BY export_id', (ids,))
    versions = {r['export_id']: int(r['version']) for r in cur.fetchall()}
    pending = []
    for row in rows:
        record = records.get(row['id'])
        if record is None or row['id'] not in live:
            raise ValueError(f"Record is not currently published: {row['id']}")
        if record['type'] != row['type']:
            raise ValueError(f"Record type changed since review: {row['id']}")
        before = record.get('alt_identifier_r')
        after = append_identifier(before, row['add_alt_identifier_r'])
        if _baseline(after) == _baseline(before):
            continue
        for field, expected in row['baseline'].items():
            if _baseline(record.get(field)) != _baseline(expected):
                raise ValueError(f"{field} changed since review: {row['id']}")
        pending.append((row, record, after, versions.get(row['id'], 0)))
    receipt = []
    for row, record, after, version in pending:
        derived = (frame.attrs.get('flora_edits') or {}).get(row['id']) or {}
        result = flora_edits.save(cur, record, derived, {'alt_identifier_r': after}, [],
                                  version, admin, {'alt_identifier_r': row['note']})
        if not result['change_id']:
            raise ValueError(f"Correction was not recorded: {row['id']}")
        if not flora_edits.publish(cur, record, {**record, 'alt_identifier_r': after}):
            raise ValueError(f"Correction was not published to stored data: {row['id']}")
        receipt.append({'id': row['id'], 'change_id': result['change_id'],
                        'before': record.get('alt_identifier_r'), 'after': after})
        if progress:
            progress(len(receipt), len(pending))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--admin-id', type=int)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    rows = load_batch()
    if not args.apply:
        print(f'Validated {len(rows)} paper-identifier additions; no database opened.')
        return
    if args.admin_id is None:
        parser.error('--apply requires --admin-id')
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    from db_pool import cursor
    def progress(done, total):
        if done % 25 == 0 or done == total:
            print(f'Prepared {done}/{total} logged corrections in transaction.', flush=True)
    with cursor(os.environ['DATABASE_URL']) as cur:
        cur.execute('SELECT id, handle FROM admins WHERE id = %s', (args.admin_id,))
        admin = cur.fetchone()
        if not admin:
            raise ValueError('Administrator does not exist')
        receipt = apply_batch(cur, rows, admin, progress)
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(f'Committed {len(receipt)} corrections; only alt_identifier_r changed.')


if __name__ == '__main__':
    main()
