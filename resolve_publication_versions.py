"""Automatically resolve explicit publication links with matching bibliography.

Dry run by default; --apply writes standalone identifier corrections through the
FLoRA log and existing-pair resolutions through preprint_dedup_decisions.
OpenAlex-only, renamed, conflicting, and human-ruled pairs remain for review.
"""
import argparse
import json
import os
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

import correct_publication_identifiers as corrections
import flora_service
import preprint_dedup as dedup
import published_versions as discovery
import transform_sources

ACTOR = 'automation:published-versions'
ROOT = Path(__file__).parent


def automatic_actions(frame, candidates, rulings=()):
    """Route each verified record to a correction or compatible version-pair merge."""
    human = {dedup.pair_key(r['doi_1'], r['doi_2']) for r in rulings
             if r.get('decided_by') != ACTOR or r['action'] == 'keep_both'}
    decisions, edits, review = {}, [], []
    records = frame.set_index('id', drop=False)
    for candidate in candidates:
        key = dedup.pair_key(candidate['doi_1'], candidate['doi_2'])
        if candidate.get('auto_eligible') != 'yes' or key in human:
            review.append({**candidate, 'reason': 'human ruling' if key in human else 'bibliography or relation requires review'})
            continue
        side = 'r' if candidate['side'] == 'replication' else 'o'
        other = 'o' if side == 'r' else 'r'
        column = 'doi_' + side
        for identifier in candidate['row_ids'].split('; '):
            if identifier not in records.index:
                raise ValueError('Candidate no longer exists: ' + identifier)
            record = records.loc[identifier]
            # Do not replace a manually changed paper using stale discovery data.
            current = dedup.normalize_doi(record.get(column))
            matches = (current in (candidate['doi_1'], candidate['doi_2'])
                       and dedup.title_similarity(record.get('title_' + side), candidate['title_2']) >= .90
                       and discovery.author_key(dedup.extract_first_author(record.get('author_' + side))) == discovery.author_key(candidate.get('first_author_2')))
            if not matches or not candidate.get('first_author_2'):
                review.append({**candidate, 'row_ids': identifier, 'reason': 'current record differs from verified bibliography'})
                continue
            peers = frame[(frame[column].map(dedup.normalize_doi) == candidate['doi_2'])
                          & frame['doi_' + other].map(dedup.normalize_doi).eq(dedup.normalize_doi(record.get('doi_' + other)))
                          & frame['type'].eq(record['type']) & frame['id'].ne(identifier)]
            if not dedup.normalize_doi(record.get('doi_' + other)):
                peers = peers.iloc[:0]  # no automatic merge on an unknown other paper
            if side == 'o' and 'url_r' in frame:
                links = {str(v).strip() for v in [record.get('url_r'), *peers['url_r']] if v}
                if len(links) > 1:
                    peers = peers.iloc[:0]
            if peers.empty:
                row = next(discovery.correction_rows(frame, [{**candidate, 'row_ids': identifier}]))
                edits.append({**row, 'approved': 'yes', 'note': 'Automatic explicit publication link with matching title and first author: ' + candidate['evidence']})
            else:
                distinct = record.get('duplicate_status') == 'distinct' or peers.get(
                    'duplicate_status', pd.Series('', index=peers.index)).eq('distinct').any()
                if distinct:
                    review.append({**candidate, 'reason': 'reviewer ruled a record distinct'})
                    continue
                decisions[key] = {**candidate, 'side': candidate['side'] + ' (publication link)',
                                  'action': 'keep_2', 'pair_key': key}
    return edits, list(decisions.values()), review


def apply_automatic(cur, frame, candidates):
    """Apply the automatic subset transactionally, keeping human decisions intact."""
    cur.execute('LOCK TABLE preprint_dedup_decisions IN SHARE ROW EXCLUSIVE MODE')
    # Use the editor's per-record locks, including publisher counterparts. Reload
    # afterwards so an edit committed during discovery cannot be absorbed unseen.
    ids = set()
    for candidate in candidates:
        if candidate.get('auto_eligible') != 'yes':
            continue
        ids.update(candidate['row_ids'].split('; '))
        column = 'doi_r' if candidate['side'] == 'replication' else 'doi_o'
        ids.update(frame.loc[frame[column].map(dedup.normalize_doi).eq(candidate['doi_2']), 'id'])
    for identifier in sorted(ids):
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('flora_record_changes'), hashtext(%s))", (identifier,))
    frame = live_frame(cur)
    rulings = dedup.confirmed_decisions(rulings=transform_sources.load_dedup_decisions(cur))
    edits, decisions, review = automatic_actions(frame, candidates, rulings)
    plans = corrections.plan(cur, edits)
    corrections.prepare_metadata(cur, plans)
    results = corrections.apply_plans(cur, plans, {'handle': ACTOR})
    inserted = 0
    for row in decisions:
        cur.execute("""
            INSERT INTO preprint_dedup_decisions
                (pair_key, side, doi_1, doi_2, action, title_1, title_2, note, decided_by)
            VALUES (%s, %s, %s, %s, 'keep_2', %s, %s, %s, %s)
            ON CONFLICT (pair_key) DO NOTHING
        """, (row['pair_key'], row['side'], row['doi_1'], row['doi_2'], row['title_1'],
              row['title_2'], json.dumps({'policy': 'explicit link + title + first author',
                  'evidence': row['evidence'], 'evidence_url': row['evidence_url'],
                  'title_similarity': row['title_sim'], 'first_author': row['first_author_2']}), ACTOR))
        inserted += cur.rowcount
    return {'corrections': results, 'new_dedup_decisions': inserted,
            'deduplication': 'queued_for_preparation' if inserted else 'unchanged',
            'review': [{'row_ids': r['row_ids'], 'reason': r['reason']} for r in review]}


def live_frame(cur):
    """Current prepared records, retaining provenance and reviewer distinctions."""
    frame = transform_sources.drop_untitled(flora_service.dataset(cur)).copy()
    frame = frame[frame['export_id'].notna() & frame['export_id'].ne('')]
    frame['id'] = frame['export_id']
    return frame.fillna('')


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--cache', type=Path, default=ROOT / 'cache/published_versions')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/published_version_candidates.csv')
    args = parser.parse_args()
    conn = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=15)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            frame = live_frame(cur)
        conn.rollback()  # no open DB transaction during external lookups
        client = discovery.Client(args.cache)
        candidates = discovery.scan(frame, client)
        discovery.write_candidates(frame, candidates, args.output)
        result = {'applied': args.apply, 'errors': client.errors, 'candidates': len(candidates)}
        if client.errors:
            result['applied'] = False
        else:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                fresh = live_frame(cur)
                if args.apply:
                    result.update(apply_automatic(cur, frame, candidates))
                    conn.commit()
                else:
                    edits, pairs, review = automatic_actions(fresh, candidates,
                        dedup.confirmed_decisions(rulings=transform_sources.load_dedup_decisions(cur)))
                    result.update(corrections=len(edits), dedup_decisions=len(pairs), review=len(review))
                    conn.rollback()
        args.output.with_suffix('.status.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        return 1 if client.errors else 0
    except Exception as exc:
        conn.rollback()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix('.status.json').write_text(json.dumps({'applied': False, 'error': str(exc)}, indent=2))
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
