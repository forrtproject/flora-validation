"""Find relation-backed journal versions of FLoRA preprints; never rewrite data.

Run after preparation: python3 published_versions.py. Review the companion
.corrections.csv and approve identifier corrections through
correct_publication_identifiers.py / flora_edits. No title-only search is used; an
OpenAlex DOI merge requires relation evidence or title + first-author confirmation. Missing API records expire after seven days;
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
import unicodedata
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from preprint_dedup import is_preprint_doi, normalize_doi, title_similarity, extract_first_author

ROOT = Path(__file__).parent
MAX_RETRY_DELAY = 60
COLUMNS = ['side', 'doi_1', 'doi_2', 'proposed_action', 'action', 'confidence',
           'evidence', 'evidence_url', 'title_1', 'title_2', 'row_ids', 'row_count', 'auto_eligible', 'title_sim', 'first_author_2']


def retry_delay(value, attempt):
    """Read Retry-After seconds/date, falling back to bounded exponential backoff."""
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, date.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 5 * 2 ** attempt


class Client:
    """Serial, bounded retries; disk cache includes misses but excludes failures."""
    def __init__(self, cache, ttl_days=7):
        """Create an expiring response cache and a polite generic API session."""
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_days * 86400
        self.session = requests.Session()
        contact = os.environ.get('OPENALEX_MAILTO', '').strip()
        self.session.headers['User-Agent'] = 'flora-validation/1.0' + (f' (mailto:{contact})' if contact else '')
        self.errors = {}

    def get(self, url, params=None):
        """Return a cached/API response; report bounded lookup failures without caching them."""
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
                error = str(exc)
                if attempt == 2:
                    break
                retry = response.headers.get('Retry-After', '') if response is not None else ''
                delay = retry_delay(retry, attempt)
                if delay > MAX_RETRY_DELAY:
                    # Do not retry before the provider permits it, or wait long
                    # enough to lose the run's artifacts to the job timeout.
                    error += f'; Retry-After exceeds {MAX_RETRY_DELAY}s budget'
                    break
                time.sleep(delay)
        self.errors[request.url] = error
        print(f'Lookup failed: {request.url}: {error}', file=sys.stderr, flush=True)
        return {}

    def work(self, doi):
        """Read the Crossref work record for one normalised DOI."""
        return self.get('https://api.crossref.org/works/' + quote(doi, safe='')).get('message', {})


def eligible(doi):
    """Recognise preprint-server and generic OSF identifiers worth checking."""
    return is_preprint_doi(doi) or doi.startswith(('10.17605/', '10.2196/preprints.'))


def osf_id(doi):
    """Extract the base OSF preprint GUID from unversioned or versioned DOIs."""
    # New OSF identifiers have _vN version suffixes. They name versions of the
    # same preprint; the API detail uses its five-character base GUID.
    match = re.fullmatch(r'10\.(?:31234|31219|31235|31222|31236|35542|17605)/osf\.io/([a-z0-9]{5})(?:_v?\d+)?', doi)
    return match[1] if match else None


def author_key(family):
    """Compare surname spellings with/without metadata diacritics consistently."""
    return ''.join(c for c in unicodedata.normalize('NFKD', family or '').casefold()
                   if not unicodedata.combining(c))


def valid_target(source, target, work):
    """Require a distinct, registered journal DOI outside known preprint repositories."""
    return (bool(re.fullmatch(r'10\.\d{4,9}/\S+', target)) and target != source
            and not eligible(target) and work.get('type') == 'journal-article')


def datacite_article(response):
    """Shape only DataCite JournalArticle records for bibliographic confirmation."""
    attrs = response.get('data', {}).get('attributes', {})
    if attrs.get('types', {}).get('resourceTypeGeneral') != 'JournalArticle':
        return {}
    return {'type': 'journal-article',
            'title': [t['title'] for t in attrs.get('titles', []) if t.get('title')],
            'author': [{'family': c.get('familyName') or extract_first_author(c.get('name', ''))}
                       for c in attrs.get('creators', [])]}


def linked_versions(doi, client, title='', author=''):
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
    # A DOI lookup can resolve to an OpenAlex work with a different primary DOI.
    # This is a signal, not equivalence: verify against Crossref bibliography.
    oa_url = 'https://api.openalex.org/works/https://doi.org/' + doi
    oa = client.get(oa_url, {'select': 'id,doi,title,type,authorships'})
    target = normalize_doi(oa.get('doi', ''))
    if target and target != doi:
        links.setdefault(target, []).append(('openalex:merged-doi', oa_url))
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
        if eligible(target) or not re.fullmatch(r'10\.\d{4,9}/\S+', target):
            continue
        published = client.work(target)
        verifier = 'crossref'
        verification_url = 'https://api.crossref.org/works/' + quote(target, safe='')
        if not published:
            verifier = 'datacite'
            verification_url = 'https://api.datacite.org/dois/' + target
            published = datacite_article(client.get(verification_url))
        if valid_target(doi, target, published):
            relation_backed = any(e[0] != 'openalex:merged-doi' for e in evidence)
            source_title = '; '.join(work.get('title', [])) or title
            source_author = extract_first_author(json.dumps(work.get('author', []))) if work.get('author') else extract_first_author(author)
            source_author = author_key(source_author)
            target_author = author_key(extract_first_author(json.dumps(published.get('author', []))))
            similarity = title_similarity(source_title, '; '.join(published.get('title', [])))
            matched = bool(source_author) and source_author == target_author and similarity >= .90
            if matched:
                evidence.append((verifier + ':title-first-author-match', verification_url))
            candidates.append({'doi_2': target, 'confidence': 'high' if matched else 'review',
                               'auto_eligible': 'yes' if relation_backed and matched else '',
                               'title_sim': round(similarity, 4), 'first_author_2': target_author or '', 'title_2': '; '.join(published.get('title', [])),
                               'evidence': '; '.join(sorted({e[0] for e in evidence})),
                               'evidence_url': '; '.join(sorted({e[1] for e in evidence}))})
    return candidates


def scan(frame, client):
    """Find publication candidates once per DOI and retain affected FLoRA records."""
    results = []
    # One network lookup for a DOI even if it occurs on both sides or many rows.
    found = {}
    for suffix, side in [('o', 'original'), ('r', 'replication')]:
        normalized = frame['doi_' + suffix].map(normalize_doi)
        # Include preserved preprint aliases: a previous identifier correction
        # may have left two current rows for the same published paper.
        group = pd.DataFrame({'primary': normalized,
                              'other': frame['doi_' + ('o' if suffix == 'r' else 'r')].map(normalize_doi),
                              'type': frame.get('type', pd.Series('', index=frame.index))})
        duplicate = group.duplicated(keep=False) & normalized.ne('') & ~normalized.map(eligible)
        aliases = frame.get('alt_identifier_' + suffix, pd.Series('', index=frame.index)).fillna('').where(duplicate, '').map(
            lambda value: {normalize_doi(part.strip()) for part in str(value).split(',')})
        dois = set(normalized) | {doi for group in aliases for doi in group if eligible(doi)}
        for doi in sorted(dois):
            if not eligible(doi):
                continue
            mask = normalized.eq(doi) | aliases.map(lambda group: doi in group)
            if doi not in found:
                source = frame[mask].iloc[0]
                found[doi] = linked_versions(doi, client, str(source.get('title_' + suffix, '')), str(source.get('author_' + suffix, '')))
                print(f'Checked {doi}: {len(found[doi])} published candidate(s)', flush=True)
            rows = frame[mask]
            for candidate in found[doi]:
                candidate = dict(candidate)
                if len(found[doi]) != 1:
                    candidate['confidence'] = 'conflicting_relations_review'
                    candidate['auto_eligible'] = ''
                results.append(dict(candidate, side=side, doi_1=doi,
                    proposed_action='identifier_correction' if candidate['confidence'] == 'high' else '', action='',
                    title_1=str(rows.iloc[0].get('title_' + suffix, '')),
                    row_ids='; '.join(rows['id'].astype(str)), row_count=len(rows)))
    return results


def correction_rows(frame, candidates):
    """A reviewable, record-level CSV consumed by correct_publication_identifiers."""
    records = frame.set_index('id')
    for candidate in candidates:
        for record_id in candidate['row_ids'].split('; '):
            record = records.loc[record_id]
            yield {'record_id': record_id, 'flora_id': record.get('flora_id', ''),
                   'side': 'r' if candidate['side'] == 'replication' else 'o',
                   'old_identifier': candidate['doi_1'], 'new_doi': candidate['doi_2'],
                   'confidence': candidate['confidence'], 'approved': '',
                   'auto_eligible': candidate['auto_eligible'],
                   'evidence': candidate['evidence'], 'evidence_url': candidate['evidence_url'],
                   'note': ('Published-version check: ' + candidate['evidence'])[:500]}


def write_candidates(frame, candidates, output):
    """Write discovery and per-record review artifacts without applying edits."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(candidates)
    correction_fields = ['record_id', 'flora_id', 'side', 'old_identifier', 'new_doi',
                         'confidence', 'approved', 'auto_eligible', 'evidence', 'evidence_url', 'note']
    with output.with_suffix('.corrections.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=correction_fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(correction_rows(frame, candidates))


def main():
    """Write discovery, per-record review and status artifacts without editing FLoRA."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'output/flora.csv')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/published_version_candidates.csv')
    parser.add_argument('--cache', type=Path, default=ROOT / 'cache/published_versions')
    args = parser.parse_args()
    client = Client(args.cache)
    frame = pd.read_csv(args.input).fillna('')
    candidates = scan(frame, client)
    write_candidates(frame, candidates, args.output)
    status = {'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
              'checked_dois': len({normalize_doi(d) for side in ('o', 'r') for d in frame['doi_' + side] if eligible(normalize_doi(d))}),
              'candidate_pairs': len(candidates), 'affected_rows': len({rid for c in candidates for rid in c['row_ids'].split('; ')}),
              'errors': client.errors}
    args.output.with_suffix('.status.json').write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))
    return 1 if client.errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
