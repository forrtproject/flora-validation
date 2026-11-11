"""Real PostgreSQL checks for type caching, review persistence and CLI behavior."""
from datetime import datetime, timezone

import pandas as pd
import pytest
from psycopg2.extras import RealDictCursor

import enrich_works
import entry_checks
import original_work_types as types
import validate_flora_network as network
from tests.test_preparation_database import local_database  # noqa: F401

DOI = '10.1002/npr2.70058'
ADMIN = {'handle': 'type-review-test'}


def typed_frame(**changes):
    """One reviewed source record with a permanent finding identity."""
    return pd.DataFrame([{'flora_id': 'REPL-1', 'id': 'FLORA-1', 'doi_o': DOI,
                          'doi_r': '10.1000/replication', 'title_o': 'Correction', **changes}])


def findings(cur, data, work_type):
    """Run the real classifier with controlled fresh provider evidence."""
    cache = {DOI: {'work_type': work_type, 'work_type_checked_at': datetime.now(timezone.utc)}}
    items, _ = types.check(cur, data, cache=cache)
    return [{**item, 'level': 'possible_problem'} for item in items]


def run_id(cur):
    """Create the real run referenced by stored findings."""
    cur.execute("INSERT INTO flora_check_runs (part, status) VALUES ('network', 'running') RETURNING run_id")
    return cur.fetchone()['run_id']


def test_review_decisions_survive_metadata_changes_new_rows_and_reopening(local_database):
    """A marked finding stays marked; a new row and higher risk need their own decisions."""
    conn = local_database
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o, doi_r, "
                    "reviewed_by, reviewed_at) VALUES ('replications', 'type-test', 'REPL-1', "
                    "'replication', %s, '10.1000/replication', 'previous-reviewer', NOW())", (DOI,))
        cur.execute("SELECT reviewed_by, reviewed_at, version FROM source_records WHERE display_id='REPL-1'")
        previous_review = dict(cur.fetchone())
        rid = run_id(cur)
        first = findings(cur, typed_frame(), 'review')
        key = first[0]['key']
        entry_checks._upsert(cur, 'network', first, rid)
        marked = entry_checks.dismiss(cur, key, ADMIN, 'Checked the replication: legitimate original target')
        for label in ('book-chapter', 'conference-paper'):
            changed = typed_frame(title_o='Provider changed the title')
            second = typed_frame(flora_id='REPL-2', id='FLORA-2')
            combined = pd.concat([changed, second], ignore_index=True)
            entry_checks._upsert(cur, 'network', findings(cur, combined, label), rid)
            cur.execute('SELECT finding_key, dismissed_by FROM flora_check_findings ORDER BY finding_key')
            rows = {r['finding_key']: r for r in cur.fetchall()}
            assert rows[key]['dismissed_by'] == ADMIN['handle']
            assert len(rows) == 2 and sum(r['dismissed_by'] is None for r in rows.values()) == 1
        # Explicit higher-tier evidence does not inherit a format-only decision.
        high = findings(cur, typed_frame(), 'erratum')
        entry_checks._upsert(cur, 'network', high, rid)
        assert high[0]['key'] != key
        entry_checks._resolve(cur, {key}, rid)
        entry_checks._upsert(cur, 'network', first, rid)
        cur.execute('SELECT dismissed_at, dismissed_by, resolved_at FROM flora_check_findings WHERE finding_key=%s', (key,))
        reopened = cur.fetchone()
        assert reopened['dismissed_at'] == marked['dismissed_at']
        assert reopened['dismissed_by'] == ADMIN['handle'] and reopened['resolved_at'] is None
        cur.execute("SELECT reviewed_by, reviewed_at, version FROM source_records WHERE display_id='REPL-1'")
        assert dict(cur.fetchone()) == previous_review


def test_a_changed_original_resolves_the_old_finding_without_erasing_its_decision(local_database):
    """Equivalent DOI forms retain review; a different original is a fresh target."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        rid = run_id(cur)
        items = findings(cur, typed_frame(), 'dataset')
        entry_checks._upsert(cur, 'network', items, rid)
        key = items[0]['key']
        entry_checks.dismiss(cur, key, ADMIN, 'Study identity confirmed from the paper')
        cur.execute('SELECT * FROM flora_check_findings WHERE finding_key=%s', (key,))
        stored = dict(cur.fetchone())
        unchanged = {'REPL-1': {'doi_o': 'https://doi.org/' + DOI.upper()}}
        assert not entry_checks._network_resolved([stored], unchanged, {}, {'work_types': {DOI: None}})
        changed = {'REPL-1': {'doi_o': '10.1000/new'}}
        resolved = entry_checks._network_resolved([stored], changed, {}, {'work_types': {DOI: None}})
        entry_checks._resolve(cur, resolved, rid)
        entry_checks._upsert(cur, 'network', items, rid)
        cur.execute('SELECT dismissed_by, dismiss_note FROM flora_check_findings WHERE finding_key=%s', (key,))
        assert cur.fetchone()['dismissed_by'] == ADMIN['handle']


def test_type_only_refresh_preserves_bibliography_and_does_not_poison_misses(local_database, monkeypatch):
    """Alias lookups update only type evidence on the requested identifier."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("INSERT INTO work_metadata (doi, title, authors, year, not_found) "
                    "VALUES (%s, 'Cached real title', 'Cached authors', '2020', FALSE)", (DOI,))
        monkeypatch.setattr(types, 'original_dois', lambda c: [DOI])
        monkeypatch.setattr(types, 'fetch_batch', lambda ds: [])
        monkeypatch.setattr(types, 'fetch_one', lambda doi: {
            'id': 'https://openalex.org/W1', 'doi': 'https://doi.org/10.1000/primary', 'type': 'erratum'})
        stats = types.refresh(cur)
        assert stats['updated'] == 1
        cur.execute('SELECT * FROM work_metadata WHERE doi=%s', (DOI,))
        row = cur.fetchone()
        assert row['title'] == 'Cached real title' and row['authors'] == 'Cached authors'
        assert row['year'] == '2020' and row['not_found'] is False
        assert row['work_type'] == 'erratum' and row['work_type_checked_at']
        assert '10.1000/primary' not in types.load_cache(cur)


def test_type_only_rows_do_not_prevent_future_bibliographic_enrichment(local_database):
    """A type snapshot is not a complete title/reference cache entry."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        types.remember(cur, DOI, 'erratum')
        assert DOI not in enrich_works.known_dois(cur)
        cur.execute('UPDATE work_metadata SET title=%s WHERE doi=%s', ('Real title', DOI))
        assert DOI in enrich_works.known_dois(cur)


def test_a_database_without_bibliography_keeps_network_screening_advisory(local_database):
    """Link-only deployments can run while type metadata is unavailable."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('DROP TABLE work_metadata')
        types.ensure_cache(cur)
        assert types.load_cache(cur) == {}
        report = network.validate(cur, typed_frame(), checks='work_types')
        assert report['problems'] == 0 and report['status'] == 'needs_attention'


@pytest.mark.parametrize('work_type', ['erratum', 'dataset', 'review', None])
def test_strict_type_only_cli_does_not_fail_publication(local_database, monkeypatch, tmp_path, work_type):
    """Both priority tiers and unavailable metadata remain advisory in the real CLI."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        types.remember(cur, DOI, work_type)
    source = tmp_path / 'flora.csv'
    typed_frame().to_csv(source, index=False)
    monkeypatch.setattr(network, 'start_logging', lambda *args: None)
    monkeypatch.setattr(network.sys, 'argv', [
        'validate_flora_network.py', '--checks', 'work_types', '--input', str(source),
        '--output', str(tmp_path / 'report.md'), '--fail-on-issues'])
    assert network.main() == 0
    assert (tmp_path / 'report.md').exists()
