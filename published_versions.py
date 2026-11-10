"""Find relation-backed journal versions of FLoRA preprints; never rewrite data.

Run after preparation: python3 published_versions.py. Copy reviewed candidates to
cache/confirmed_preprint_duplicates.csv with action=proposed_action to apply them
on the next build, including alt_identifier_o/r via preprint_dedup.apply_confirmed.
No title-only inference is used. Missing API records expire after seven days;
transient failures are reported and never cached as absence.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from preprint_dedup import is_preprint_doi, normalize_doi

ROOT = Path(__file__).parent
COLUMNS = ['side', 'doi_1', 'doi_2', 'proposed_action', 'action', 'confidence',
           'evidence', 'evidence_url', 'title_1', 'title_2', 'row_ids', 'row_count']


class Client:
    """Serial, bounded retries; disk cache includes misses but excludes failures."""
    def __init__(self, cache, ttl_days=7):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_days * 86400
        self.session = requests.Session()
        contact = os.environ.get('OPENALEX_MAILTO', '').strip()
        self.session.headers['User-Agent'] = 'flora-validation/1.0' + (f' (mailto:{contact})' if contact else '')
        self.errors = {}

    def get(self, url, params=None):
        request = requests.Request('GET', url, params=params).prepare()
        key = hashlib.sha256(request.url.encode()).hexdigest()
        path = self.cache / (key + '.json')
        if path.exists():
            try:
                cached = json.loads(path.read_text())
                if time.time() - cached['at'] < self.ttl:
                    return cached['data']
            except (ValueError, KeyError):
                pass
        for attempt in range(3):
            response = None
            time.sleep(.5)
            try:
                response = self.session.get(url, params=params, timeout=30)
                if response.status_code in (404, 410):
                    data = {}
                else:
                    response.raise_for_status()
                    data = response.json()
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps({'at': time.time(), 'data': data}))
                tmp.replace(path)
                return data
            except (requests.RequestException, ValueError) as exc:
                if attempt < 2:
                    retry = response.headers.get('Retry-After', '') if response is not None else ''
                    time.sleep(int(retry) if retry.isdigit() else 5 * 2 ** attempt)
                else:
                    self.errors[request.url] = str(exc)
                    print(f'Lookup failed: {request.url}: {exc}', file=sys.stderr, flush=True)
        return {}

    def work(self, doi):
        return self.get('https://api.crossref.org/works/' + quote(doi, safe='')).get('message', {})


def eligible(doi):
    return is_preprint_doi(doi) or doi.startswith('10.17605/')


def osf_id(doi):
    # New OSF identifiers have _vN version suffixes. They name versions of the
    # same preprint; the API detail uses its five-character base GUID.
    match = re.fullmatch(r'10\.(?:31234|31219|31235|31222|31236|35542|17605)/osf\.io/([a-z0-9]{5})(?:_v?\d+)?', doi)
    return match[1] if match else None


def valid_target(source, target, work):
    return (bool(re.fullmatch(r'10\.\d{4,9}/\S+', target)) and target != source
            and not eligible(target) and work.get('type') == 'journal-article')


def linked_versions(doi, client):
    """Only explicit DOI relations to verified journal articles prove publication.

    Query reverse relations too: publishers need not deposit on the preprint's
    record. is-version-of can name another preprint, so verify the target type.
    """
    links = {}
    guid = osf_id(doi)
    if guid:
        url = f'https://api.osf.io/v2/preprints/{guid}/'
        data = client.get(url).get('data', {})
        target = normalize_doi(data.get('attributes', {}).get('article_doi', ''))
        if target:
            links.setdefault(target, []).append(('osf:article_doi', url))
    work = client.work(doi)
    for relation in ('is-preprint-of', 'is-version-of'):
        for item in work.get('relation', {}).get(relation, []):
            if item.get('id-type', '').lower() == 'doi':
                target = normalize_doi(item.get('id'))
                links.setdefault(target, []).append(('crossref:' + relation, 'https://api.crossref.org/works/' + quote(doi, safe='')))
    # This filter returns works with ANY relation to this DOI; inspect direction
    # and type locally, and follow pagination rather than truncating results.
    cursor = '*'
    while True:
        url = 'https://api.crossref.org/works'
        params = {'filter': 'relation.object:' + doi, 'rows': 100, 'cursor': cursor}
        data = client.get(url, params).get('message', {})
        items = data.get('items', [])
        for item in items:
            for relation in ('has-preprint', 'has-version'):
                if any(r.get('id-type', '').lower() == 'doi' and normalize_doi(r.get('id')) == doi
                       for r in item.get('relation', {}).get(relation, [])):
                    target = normalize_doi(item.get('DOI'))
                    links.setdefault(target, []).append(('crossref:' + relation, requests.Request('GET', url, params=params).prepare().url))
        next_cursor = data.get('next-cursor')
        if len(items) < 100 or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    candidates = []
    for target, evidence in sorted(links.items()):
        published = client.work(target)
        if valid_target(doi, target, published):
            candidates.append({'doi_2': target, 'title_2': '; '.join(published.get('title', [])),
                               'evidence': '; '.join(sorted({e[0] for e in evidence})),
                               'evidence_url': '; '.join(sorted({e[1] for e in evidence}))})
    return candidates


def scan(frame, client):
    results = []
    # One network lookup for a DOI even if it occurs on both sides or many rows.
    found = {}
    for suffix, side in [('o', 'original'), ('r', 'replication published version')]:
        normalized = frame['doi_' + suffix].map(normalize_doi)
        for doi in sorted(set(normalized)):
            if not eligible(doi):
                continue
            if doi not in found:
                found[doi] = linked_versions(doi, client)
                print(f'Checked {doi}: {len(found[doi])} published candidate(s)', flush=True)
            rows = frame[normalized == doi]
            for candidate in found[doi]:
                results.append(dict(candidate, side=side, doi_1=doi,
                    proposed_action='keep_2' if len(found[doi]) == 1 else '', action='',
                    confidence='high' if len(found[doi]) == 1 else 'conflicting_relations_review',
                    title_1=str(rows.iloc[0].get('title_' + suffix, '')),
                    row_ids='; '.join(rows['id'].astype(str)), row_count=len(rows)))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'output/flora.csv')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/published_version_candidates.csv')
    parser.add_argument('--cache', type=Path, default=ROOT / 'cache/published_versions')
    args = parser.parse_args()
    client = Client(args.cache)
    frame = pd.read_csv(args.input).fillna('')
    candidates = scan(frame, client)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(candidates)
    status = {'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
              'checked_dois': len({normalize_doi(d) for side in ('o', 'r') for d in frame['doi_' + side] if eligible(normalize_doi(d))}),
              'candidate_pairs': len(candidates), 'affected_rows': len({rid for c in candidates for rid in c['row_ids'].split('; ')}),
              'errors': client.errors}
    args.output.with_suffix('.status.json').write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))
    return 1 if client.errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
