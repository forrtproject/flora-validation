"""Audit and restore dropped preprint aliases using recorded DOI transitions.

Dry run by default. --apply uses the existing FLoRA correction log and publishes
only alternative-identifier changes. No primary identifier is changed here.
"""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

import flora_edits
import flora_service
import flora_store
from preprint_dedup import normalize_doi
from preprint_identifiers import is_preprint_identifier, preserve_preprint


def dropped_identifiers(records, transitions, *, include_osf=False):
    """Find history-backed preprints reaching today's primary DOI but absent as aliases."""
    findings = []
    for record in records:
        for side in ('o', 'r'):
            doi, alt = 'doi_' + side, 'alt_identifier_' + side
            current = normalize_doi(record.get(doi))
            if not current or is_preprint_identifier(current) or current.startswith('10.17605/'):
                continue
            edges = [t for t in transitions if t['id'] == record['id'] and t['side'] == side
                     and normalize_doi(t['old']) and normalize_doi(t['new'])]
            reachable = {current}
            while True:
                prior = set(reachable)
                reachable.update(normalize_doi(t['old']) for t in edges
                                 if normalize_doi(t['new']) in reachable)
                if reachable == prior:
                    break
            aliases = {normalize_doi(part.strip()) for part in (record.get(alt) or '').split(',')}
            missing = sorted(value for value in reachable - aliases - {current}
                             if is_preprint_identifier(value)
                             or (include_osf and value.startswith('10.17605/')))
            for old in missing:
                evidence = sorted({t['evidence'] for t in edges if normalize_doi(t['old']) == old
                                   and normalize_doi(t['new']) in reachable})
                findings.append({'id': record['id'], 'side': side, 'old_identifier': old,
                                 'current_doi': current, 'evidence': evidence})
    return findings


def load_history(cur):
    """Read only public identifiers and transition evidence, never validator identities."""
    cur.execute("SELECT id, doi_o, doi_r, alt_identifier_o, alt_identifier_r "
                "FROM flora_data WHERE retired_at IS NULL ORDER BY export_position")
    records = [dict(row) for row in cur.fetchall()]
    cur.execute("""
        SELECT f.export_id AS id, s.record_id AS source_id, s.doi_o, s.doi_r,
               s.raw->>'doi_o' AS source_raw_o, s.raw->>'doi_r' AS source_raw_r,
               s.raw->>'doi_r_published' AS source_published_r,
               u.doi_o AS extracted_o, u.doi_r AS extracted_r,
               COALESCE(u.final_doi_o, u.doi_o) AS final_o,
               COALESCE(NULLIF(u.final_doi_r, ''), u.doi_r) AS final_r,
               u.doi_r_published, u.record_id AS validation_id
          FROM flora_records f
          JOIN source_records s ON s.record_id = ANY(
              ARRAY[f.primary_source_record_id] || f.merged_source_record_ids
              || f.historical_source_record_ids)
          LEFT JOIN unvalidated u ON s.source = 'validated'
               AND u.record_id::text = s.raw->>'record_id'
         WHERE f.export_id IN (SELECT id FROM flora_data WHERE retired_at IS NULL)
    """)
    sources = [dict(row) for row in cur.fetchall()]
    by_source, by_validation, transitions = {}, {}, []

    def add(identifier, side, old, new, evidence):
        """Record real, nonblank DOI transitions, leaving normalization to the audit."""
        if old and new and normalize_doi(old) != normalize_doi(new):
            transitions.append({'id': identifier, 'side': side, 'old': old,
                                'new': new, 'evidence': evidence})

    for source in sources:
        identifier = source['id']
        by_source.setdefault(str(source['source_id']), set()).add(identifier)
        if source['validation_id']:
            by_validation.setdefault(str(source['validation_id']), set()).add(identifier)
        for side in ('o', 'r'):
            add(identifier, side, source['extracted_' + side], source['final_' + side],
                'validation:extracted-to-final')
        add(identifier, 'r', source['final_r'], source['doi_r_published'],
            'validation:published-doi')
        # source_records retains the validated snapshot even after validated is
        # deleted/reinserted; its raw payload is durable validation history.
        for side in ('o', 'r'):
            if source['validation_id']:
                add(identifier, side, source['extracted_' + side], source['source_raw_' + side],
                    'validation:source-snapshot')
                add(identifier, side, source['source_raw_' + side], source['final_' + side],
                    'validation:prior-published-snapshot')
        add(identifier, 'r', source['source_raw_r'], source['source_published_r'],
            'validation:source-published-doi')
    cur.execute("SELECT change_id, export_id, changes->'doi_o' AS doi_o, "
                "changes->'doi_r' AS doi_r FROM flora_record_changes "
                "WHERE changes ?| ARRAY['doi_o', 'doi_r'] ORDER BY change_id")
    for change in cur.fetchall():
        for side in ('o', 'r'):
            pair = change.get('doi_' + side)
            if isinstance(pair, list) and len(pair) == 2:
                add(change['export_id'], side, *pair, 'flora-change:' + str(change['change_id']))
    cur.execute("SELECT edit_id, record_id, field, old_value, new_value FROM source_record_edits "
                "WHERE field IN ('doi_o', 'doi_r') ORDER BY edited_at, edit_id")
    for change in cur.fetchall():
        for identifier in sorted(by_source.get(str(change['record_id']), [])):
            add(identifier, change['field'][-1], change['old_value'], change['new_value'],
                'source-edit:' + str(change['edit_id']))
    cur.execute("""
        SELECT e.event_id, m.source_record_id, e.changes->'doi_o' AS doi_o,
               e.changes->'doi_r' AS doi_r
          FROM manual_record_events e JOIN manual_records m USING (manual_id)
         WHERE e.changes ?| ARRAY['doi_o', 'doi_r'] ORDER BY e.event_id
    """)
    for change in cur.fetchall():
        for identifier in sorted(by_source.get(str(change['source_record_id']), [])):
            for side in ('o', 'r'):
                pair = change.get('doi_' + side)
                if isinstance(pair, list) and len(pair) == 2:
                    add(identifier, side, *pair, 'manual-event:' + str(change['event_id']))
    cur.execute("SELECT decision_id, record_id, changes->'doi_o' AS doi_o, "
                "changes->'doi_r' AS doi_r FROM admin_decisions "
                "WHERE changes ?| ARRAY['doi_o', 'doi_r'] ORDER BY decision_id")
    for change in cur.fetchall():
        for identifier in sorted(by_validation.get(str(change['record_id']), [])):
            for side in ('o', 'r'):
                pair = change.get('doi_' + side)
                if isinstance(pair, list) and len(pair) == 2:
                    add(identifier, side, *pair, 'admin-decision:' + str(change['decision_id']))
    return records, transitions


def backfill(cur, findings, admin):
    """Append missing aliases once per record through the normal logged editor."""
    groups, results = {}, []
    for item in findings:
        groups.setdefault(item['id'], []).append(item)
    for identifier, items in groups.items():
        version = flora_edits.version(cur, identifier)
        record = flora_service.get_record(cur, identifier)
        if record is None:
            raise flora_edits.RecordNotFound(identifier)
        if flora_edits.version(cur, identifier) != version:
            raise flora_edits.VersionConflict(flora_edits.version(cur, identifier))
        fields = {}
        for item in items:
            side = item['side']
            if normalize_doi(record.get('doi_' + side)) != item['current_doi']:
                raise ValueError('Primary DOI changed during audit; reload before applying')
            field = 'alt_identifier_' + side
            fields[field] = preserve_preprint(fields.get(field, record.get(field)),
                                               item['old_identifier'], item['current_doi'])
        notes = {field: 'Restore preprint identifier replaced during validation/editing (#162); '
                        'recorded DOI history, no primary DOI change.' for field in fields}
        result = flora_service.update_record(cur, identifier, fields, [], version, admin, notes)
        live = flora_store.get_record(cur, identifier)
        if not live or any(live.get(field) != value for field, value in fields.items()):
            raise ValueError('Alias backfill publication failed; rolling back')
        results.append({'id': identifier, 'change_id': result.get('change_id'), 'fields': fields})
    return results


def main():
    """Write the public audit and optionally commit one atomic logged backfill."""
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--admin-handle', required=True)
    parser.add_argument('--report', type=Path, default=Path('output/preprint_alias_audit.json'))
    args = parser.parse_args()
    conn = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Exercise the actual publication-boundary query against this schema
            # as part of the audit, without refreshing or changing source rows.
            from sync_validated import _fetch_validated
            validated_rows = _fetch_validated(cur)
            records, transitions = load_history(cur)
            findings = dropped_identifiers(records, transitions)
            osf_review = [f for f in dropped_identifiers(records, transitions, include_osf=True)
                          if f['old_identifier'].startswith('10.17605/')]
            results = backfill(cur, findings, {'handle': args.admin_handle}) if args.apply else []
            if args.apply:
                after, history = load_history(cur)
                remaining = dropped_identifiers(after, history)
                if remaining:
                    raise ValueError('Backfill left missing aliases; rolling back')
            else:
                remaining = findings
        report = {'applied': args.apply, 'published_rows': len(records),
                  'validated_rows_checked': len(validated_rows),
                  'affected_rows': len({f['id'] for f in findings}),
                  'review_only_osf_rows': len({f['id'] for f in osf_review}),
                  'missing_identifiers': len(findings),
                  'original_rows': len({f['id'] for f in findings if f['side'] == 'o'}),
                  'replication_rows': len({f['id'] for f in findings if f['side'] == 'r'}),
                  'remaining_rows': len({f['id'] for f in remaining}),
                  'findings': findings, 'osf_deposit_review': osf_review, 'corrections': results}
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
