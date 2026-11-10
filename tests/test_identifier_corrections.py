from unittest.mock import Mock

import pytest

import correct_publication_identifiers as bulk


NEUBAUER = {'record_id': 'REPRO-000363', 'side': 'r',
            'old_identifier': 'https://hdl.handle.net/10419/280689',
            'new_doi': '10.18718/81781.44', 'confidence': 'high', 'approved': 'yes'}
ONE_NIGHT = {'record_id': 'REPL-001618', 'side': 'r',
             'old_identifier': '10.31234/osf.io/84bfy_v2',
             'new_doi': '10.15626/mp.2025.4708', 'confidence': 'high', 'approved': 'yes'}


@pytest.fixture
def records(monkeypatch):
    records = {
        'REPRO-000363': {'export_id': 'FLORA-002771', 'type': 'reproduction',
                         'doi_r': '', 'url_r': NEUBAUER['old_identifier'], 'alt_identifier_r': ''},
        'REPL-001618': {'export_id': 'FLORA-002723', 'type': 'replication',
                        'doi_r': ONE_NIGHT['new_doi'], 'alt_identifier_r': '10.1234/another'},
    }
    monkeypatch.setattr(bulk.flora_service, 'get_record', lambda cur, rid: records.get(rid))
    monkeypatch.setattr(bulk.flora_edits, 'version', lambda cur, rid: 17)
    return records


def test_known_cases_preserve_ids_and_append_identifiers(records, monkeypatch):
    plans = bulk.plan(Mock(), [NEUBAUER, ONE_NIGHT])
    assert plans[0]['record_id'] == 'FLORA-002771'
    assert plans[0]['fields'] == {'doi_r': NEUBAUER['new_doi'], 'alt_identifier_r': NEUBAUER['old_identifier']}
    assert plans[1]['fields']['doi_r'] == ONE_NIGHT['new_doi']
    assert plans[1]['fields']['alt_identifier_r'] == '10.1234/another, ' + ONE_NIGHT['old_identifier']
    save = Mock(return_value={'change_id': 18})
    monkeypatch.setattr(bulk.flora_service, 'update_record', save)
    bulk.apply_plans(Mock(), plans, {'handle': 'reviewer'})
    assert save.call_count == 2
    assert save.call_args.args[4] == 17  # editor optimistic concurrency guard
    assert save.call_args.args[5] == {'handle': 'reviewer'}
    assert set(save.call_args.args[6]) == {'doi_r', 'alt_identifier_r'}


def test_rerun_is_noop(records):
    records['REPRO-000363'].update(doi_r=NEUBAUER['new_doi'], alt_identifier_r=NEUBAUER['old_identifier'])
    assert bulk.plan(Mock(), [NEUBAUER]) == []


def test_unapproved_and_weaker_candidates_are_not_bulk_applied(records):
    assert bulk.plan(Mock(), [{**NEUBAUER, 'approved': ''}]) == []
    with pytest.raises(ValueError, match='weaker'):
        bulk.plan(Mock(), [{**NEUBAUER, 'confidence': 'review'}])


def test_stale_target_and_conflicting_rows_refused_before_any_save(records):
    records['REPRO-000363']['doi_r'] = '10.1234/changed'
    with pytest.raises(ValueError, match='Stale'):
        bulk.plan(Mock(), [NEUBAUER])
    records['REPRO-000363']['doi_r'] = ''
    with pytest.raises(ValueError, match='Multiple'):
        bulk.plan(Mock(), [NEUBAUER, NEUBAUER])


def test_change_during_planning_refused(records, monkeypatch):
    monkeypatch.setattr(bulk.flora_edits, 'version', Mock(side_effect=[17, 18]))
    with pytest.raises(bulk.flora_edits.VersionConflict):
        bulk.plan(Mock(), [NEUBAUER])


def test_existing_doi_alias_is_not_duplicated(records):
    records['REPL-001618']['alt_identifier_r'] = 'https://doi.org/' + ONE_NIGHT['old_identifier']
    assert bulk.plan(Mock(), [ONE_NIGHT]) == []


def test_metadata_is_prepared_before_logging_changes(records, monkeypatch):
    import enrich_works
    import bibliographic_helpers
    monkeypatch.setattr(enrich_works, 'load_metadata', lambda cur: {})
    fetch = Mock(return_value={'title': 'The journal article', 'authors': 'Neubauer, F.'})
    store = Mock()
    monkeypatch.setattr(bibliographic_helpers, 'fetch_doi_reference', fetch)
    monkeypatch.setattr(enrich_works, '_store_reference_rows', store)
    bulk.prepare_metadata(Mock(), bulk.plan(Mock(), [NEUBAUER]))
    assert store.call_args.args[1][0]['doi'] == NEUBAUER['new_doi']
    fetch.return_value = {}
    with pytest.raises(ValueError, match='No publication metadata'):
        bulk.prepare_metadata(Mock(), bulk.plan(Mock(), [NEUBAUER]))


def test_both_sides_of_one_record_share_one_save(records):
    records['REPRO-000363'].update(doi_o='10.1234/old-original', alt_identifier_o='')
    original = {**NEUBAUER, 'side': 'o', 'old_identifier': '10.1234/old-original', 'new_doi': '10.1234/new-original'}
    plans = bulk.plan(Mock(), [NEUBAUER, original])
    assert len(plans) == 1
    assert set(plans[0]['fields']) == {'doi_o', 'doi_r', 'alt_identifier_o', 'alt_identifier_r'}
