"""Cached original-work types and advisory, record-specific format warnings.

Types are screening evidence, never grounds for changing or excluding a record.
Only successful OpenAlex answers update this cache; failures keep prior reviews
open. Type-only updates cannot invalidate cached bibliographic metadata.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import math
from datetime import datetime, timedelta, timezone

from preprint_dedup import normalize_doi
from validate_flora import finding

HIGH_CHECK = 'original_target_nonstudy'
FORMAT_CHECK = 'original_target_format'
CHECK_CODES = {HIGH_CHECK, FORMAT_CHECK}
HIGH_TYPES = {'dataset', 'paratext', 'erratum', 'retraction', 'peer-review',
              'supplementary-materials', 'component', 'standard', 'grant', 'other'}
EXEMPT_TYPES = {'article', 'preprint'}
API = 'https://api.openalex.org/works'
BATCH = 40
TIMEOUT = 15
DELAY = .5
DOI = re.compile(r'^10\.\d{4,9}/\S+$')


def _columns_present(cur):
    """Check schema without taking an ALTER TABLE lock on routine reads."""
    cur.execute("SELECT COUNT(*) = 2 AS present FROM pg_attribute "
                "WHERE attrelid = to_regclass('work_metadata') AND NOT attisdropped "
                "AND attname IN ('work_type', 'work_type_checked_at')")
    return bool(cur.fetchone()['present'])


def ensure_cache(cur):
    """Add only the type cache columns; safe before the website has deployed."""
    if _columns_present(cur):
        return
    cur.execute("SELECT to_regclass('work_metadata') IS NOT NULL AS present")
    if not cur.fetchone()['present']:
        return  # Link-only validation can use a database without bibliography.
    cur.execute('ALTER TABLE work_metadata ADD COLUMN IF NOT EXISTS work_type TEXT')
    cur.execute('ALTER TABLE work_metadata ADD COLUMN IF NOT EXISTS work_type_checked_at TIMESTAMPTZ')


def cache_days(doi):
    """Spread successful type refreshes over 25–35 days, deterministically."""
    return 25 + int(hashlib.sha256(doi.encode()).hexdigest()[:8], 16) % 11


def fresh(doi, row, now=None):
    """Known types expire monthly; successful unknowns are retried after a day."""
    checked = row.get('work_type_checked_at')
    if not checked:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    days = cache_days(doi) if row.get('work_type') else 1
    return checked > (now or datetime.now(timezone.utc)) - timedelta(days=days)


def load_cache(cur):
    """Read type evidence independently of the bibliographic not_found flag."""
    if cur is None:
        return {}
    if not _columns_present(cur):
        return {}
    cur.execute('SELECT doi, work_type, work_type_checked_at FROM work_metadata')
    return {normalize_doi(row['doi']): dict(row) for row in cur.fetchall()}


def remember(cur, doi, work_type):
    """Persist a successful type answer without changing any reference fields."""
    cur.execute('''
        INSERT INTO work_metadata (doi, work_type, work_type_checked_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (doi) DO UPDATE SET
            work_type = EXCLUDED.work_type,
            work_type_checked_at = EXCLUDED.work_type_checked_at
    ''', (doi, work_type))


def request(url, params):
    """Make one bounded, polite request; transient failures are never cached."""
    params = dict(params)
    contact = os.environ.get('OPENALEX_MAILTO', '').strip()
    if contact:
        params['mailto'] = contact
    url += '?' + urllib.parse.urlencode(params)
    time.sleep(DELAY)
    req = urllib.request.Request(url, headers={'User-Agent': 'flora-validation/1.0'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def fetch_batch(dois):
    """Fetch a minimal type-only batch; missing aliases require singleton lookups."""
    data = request(API, {'filter': 'doi:' + '|'.join(dois), 'per-page': BATCH,
                        'select': 'id,doi,type'})
    works = data.get('results') if isinstance(data, dict) else None
    if not isinstance(works, list) or any(not isinstance(work, dict) for work in works):
        raise ValueError('Malformed OpenAlex batch response')
    return works


def fetch_one(doi):
    """Resolve an alias under its requested DOI; a genuine 404 is an unknown type."""
    try:
        work = request(API + '/https://doi.org/' + urllib.parse.quote(doi, safe=''),
                       {'select': 'id,doi,type'})
        if not isinstance(work, dict) or not work.get('id'):
            raise ValueError('Malformed OpenAlex singleton response')
        return work
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise


def original_dois(cur):
    """Current original identifiers, including logged corrections and pending sources."""
    import flora_edits
    cur.execute('''
        SELECT DISTINCT doi_o AS doi FROM source_records
         WHERE deleted_at IS NULL AND duplicate_status IS DISTINCT FROM 'duplicate'
        UNION SELECT doi_o AS doi FROM flora_data WHERE retired_at IS NULL
    ''')
    values = [row['doi'] for row in cur.fetchall()]
    values += flora_edits.identifier_values(cur, ('doi_o',))
    return sorted({normalize_doi(value) for value in values
                   if DOI.fullmatch(normalize_doi(value))})


def refresh(cur, *, minutes=3, limit=0, dry_run=False):
    """Refresh type evidence within a separate optional enrichment budget.

    Stop on the first provider failure, including a rate limit: no early retries
    violate Retry-After. Prior successful batches remain useful. The caller owns
    commit/rollback and keeps this separate from bibliographic enrichment.
    """
    if not math.isfinite(minutes) or minutes < 0 or limit < 0:
        raise ValueError('Type refresh needs a finite nonnegative budget and limit')
    cache, wanted = load_cache(cur), original_dois(cur)
    todo = [doi for doi in wanted if not fresh(doi, cache.get(doi, {}))]
    if limit:
        todo = todo[:limit]
    stats = {'targets': len(wanted), 'pending': len(todo), 'updated': 0,
             'unknown': 0, 'deferred': 0, 'errors': []}
    if dry_run:
        return stats
    deadline = time.monotonic() + minutes * 60
    for start in range(0, len(todo), BATCH):
        if time.monotonic() >= deadline:
            break
        batch = todo[start:start + BATCH]
        try:
            works = fetch_batch(batch)
            found = {normalize_doi(w.get('doi')): w for w in works if w.get('id')}
            for doi in batch:
                if time.monotonic() >= deadline:
                    break
                work = found.get(doi)
                if work is None:
                    work = fetch_one(doi)
                # Malformed responses are failures, not successful misses.
                if not isinstance(work, dict) or (work and not work.get('id')):
                    raise ValueError('Malformed OpenAlex work response')
                work_type = work.get('type') or None
                if work_type is not None and not isinstance(work_type, str):
                    raise ValueError('Malformed OpenAlex work type')
                remember(cur, doi, work_type)
                stats['updated'] += 1
                stats['unknown'] += work_type is None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            stats['errors'].append(type(exc).__name__ + ': ' + str(exc))
            break
    stats['deferred'] = len(todo) - stats['updated']
    return stats


def check(cur, frame, *, answered=None, statistics=None, cache=None, now=None):
    """Emit one advisory finding per record/DOI, preserving review identity."""
    cache = load_cache(cur) if cache is None else cache
    answered = answered if answered is not None else {}
    findings, unchecked, targets, ids = [], [], set(), {}
    for row in frame.to_dict('records'):
        doi = normalize_doi(row.get('doi_o'))
        if not DOI.fullmatch(doi):
            continue  # DOI shape/missing-identifier checks own these cases.
        targets.add(doi)
        record_id = str(row.get('flora_id') or row.get('id') or '').strip()
        ids.setdefault(doi, set()).add(record_id)
        row = {**row, 'flora_id': record_id}
        item = cache.get(doi, {})
        work_type = item.get('work_type') if fresh(doi, item, now) else None
        answered[doi] = 'ok' if work_type else None
        if not work_type or work_type in EXEMPT_TYPES:
            continue
        code = HIGH_CHECK if work_type in HIGH_TYPES else FORMAT_CHECK
        priority = 'Higher priority' if code == HIGH_CHECK else 'Publication-format review'
        detail = (f'{priority}. OpenAlex type: {work_type or "unavailable"}. Check that this original '
                  'is the study the replication tests. If valid, mark Not a problem '
                  'with a reason; otherwise correct the identifier or review an '
                  'extraneous original row. This warning changes no record. '
                  f'https://openalex.org/works/doi:{doi}')
        findings.append(finding(code, [row], 'doi_o', doi, detail))
    for doi in sorted(targets):
        if answered.get(doi) is None:
            unchecked.append({'target': doi, 'columns': ['doi_o'],
                              'flora_ids': sorted(ids[doi] - {''}),
                              'reason': 'Original work type unavailable or stale; previous review retained'})
    if statistics is not None:
        known = sum(answered.get(doi) == 'ok' for doi in targets)
        statistics.update(targets=len(targets), cached=known, checked=len(targets),
                          unchecked=len(targets) - known, deferred=0, advisory=True)
    return findings, unchecked
