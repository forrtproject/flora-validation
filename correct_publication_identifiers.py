"""Apply an explicitly approved publication-corrections CSV through FLoRA's log.

Dry run by default. Use --apply --admin-handle HANDLE to commit all approved
rows in one transaction through flora_service.update_record / flora_edits.save.
Requires DATABASE_URL; no source records or dedup decisions are rewritten.
"""
import argparse
import csv
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

import flora_edits
import flora_service
from preprint_dedup import append_alt_identifier, normalize_doi


REQUIRED = {'record_id', 'side', 'old_identifier', 'new_doi', 'confidence', 'approved'}


def plan(cur, rows):
    """Validate the full batch and preserve current aliases before any writes."""
    plans, seen = [], set()
    for row in rows:
        if str(row.get('approved', '')).strip().lower() != 'yes':
            continue
        if not REQUIRED <= row.keys():
            raise ValueError('Missing CSV fields: ' + ', '.join(sorted(REQUIRED - row.keys())))
        if row['confidence'] != 'high':
            raise ValueError('Only high-confidence corrections can be bulk applied; weaker matches require review')
        side = row['side']
        if side not in ('o', 'r'):
            raise ValueError('side must be o or r')
        record = flora_service.get_record(cur, row['record_id'])
        if record is None:
            raise flora_edits.RecordNotFound(row['record_id'])
        # Pair the record snapshot with its log version. A concurrent change
        # during planning is refused; save() guards again before writing.
        version = flora_edits.version(cur, record['export_id'])
        record = flora_service.get_record(cur, row['record_id'])
        if record is None:
            raise flora_edits.RecordNotFound(row['record_id'])
        current_version = flora_edits.version(cur, record['export_id'])
        if current_version != version:
            raise flora_edits.VersionConflict(current_version)
        key = (record['export_id'], side)
        if key in seen:
            raise ValueError('Multiple corrections for the same record and side')
        seen.add(key)
        doi_field, alt_field = 'doi_' + side, 'alt_identifier_' + side
        new = flora_edits.clean_value(doi_field, row['new_doi'], record['type'])
        old = str(row['old_identifier']).strip()
        if not old or not new or not re.fullmatch(r'10\.\d{4,9}/\S+', new) or normalize_doi(old) == new:
            raise ValueError('An old identifier and a distinct new DOI are required')
        current = record.get(doi_field) or record.get('url_' + side) or ''
        if normalize_doi(current) not in (normalize_doi(old), new):
            raise ValueError(f"Stale identifier for {row['record_id']}: reload and review")
        existing = record.get(alt_field) or ''
        alt = existing if any(normalize_doi(part.strip()) == normalize_doi(old)
                              for part in existing.split(',')) else append_alt_identifier(existing, old)
        fields = {doi_field: new, alt_field: alt}
        if all((record.get(field) or '') == (value or '') for field, value in fields.items()):
            continue  # rerunning an already applied batch appends no log entries
        note = (row.get('note') or 'Approved published-version identifier correction').strip()
        if len(note) > flora_edits.NOTE_LIMIT:
            raise ValueError('Correction note is too long')
        plans.append({'record_id': record['export_id'], 'fields': fields,
                      'version': version,
                      'notes': {field: note for field in fields}})
    # Both sides of one record share one version guard and one audit entry.
    combined = {}
    for item in plans:
        prior = combined.setdefault(item['record_id'], {**item, 'fields': {}, 'notes': {}})
        if prior['version'] != item['version']:
            raise flora_edits.VersionConflict(item['version'])
        prior['fields'].update(item['fields'])
        prior['notes'].update(item['notes'])
    return list(combined.values())


def prepare_metadata(cur, plans):
    """Ensure a corrected DOI can be published immediately, in this transaction."""
    import bibliographic_helpers as references
    from enrich_works import load_metadata, _store_reference_rows
    metadata = load_metadata(cur)
    targets = {value for p in plans for field, value in p['fields'].items()
               if field in ('doi_o', 'doi_r')}
    for doi in sorted(targets):
        current = metadata.get(doi) or {}
        if current.get('title'):
            continue
        row = references.merge_present(dict(current), references.fetch_doi_reference(doi))
        if not row.get('title'):
            raise ValueError(f'No publication metadata for {doi}; no corrections applied')
        _store_reference_rows(cur, [{**row, 'doi': doi}])


def apply_plans(cur, plans, admin):
    """Use the editor's save, audit log, metadata rebuild and live publication."""
    return [flora_service.update_record(cur, p['record_id'], p['fields'], [],
                                       p['version'], admin, p['notes']) for p in plans]


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--admin-handle', required=True)
    args = parser.parse_args()
    with args.csv.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    conn = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            plans = plan(cur, rows)
            if args.apply:
                prepare_metadata(cur, plans)
                result = apply_plans(cur, plans, {'handle': args.admin_handle})
                import flora_store
                for p in plans:
                    live = flora_store.get_record(cur, p['record_id'])
                    if not live or any(live.get(k) != v for k, v in p['fields'].items()):
                        raise ValueError('Publication verification failed; rolling back all corrections')
            else:
                result = plans
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        print(json.dumps({'applied': args.apply, 'count': len(result), 'results': result}, indent=2))
    finally:
        conn.close()  # any exception rolls back the whole batch


if __name__ == '__main__':
    main()
