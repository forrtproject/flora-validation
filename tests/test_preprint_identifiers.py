"""History-backed identifier retention and backfill, without external API calls."""
from unittest.mock import Mock

import pytest
import pandas as pd

import backfill_preprint_identifiers as audit
from preprint_identifiers import preserve_preprint, validated_identifiers
import sync_validated

PREPRINT = '10.31234/osf.io/abcde'
ARTICLE = '10.1234/journal'


@pytest.mark.parametrize('old', [PREPRINT, 'https://doi.org/' + PREPRINT.upper(),
                               '10.2196/preprints.45481', '10.17605/osf.io/abcde'])
def test_replaced_preprint_is_appended_and_deduplicated(old):
    """Resolver forms and DOI case do not create duplicate alternatives."""
    result = preserve_preprint('https://example.org/paper', old, ARTICLE)
    assert preserve_preprint(result, old, ARTICLE) == result
    assert result.startswith('https://example.org/paper, 10.')


@pytest.mark.parametrize('old,new', [(ARTICLE, '10.1234/other'), (PREPRINT, ''),
                                    (PREPRINT, PREPRINT), ('10.1101/gr.1', ARTICLE)])
def test_unrelated_article_changes_and_clears_do_not_add_aliases(old, new):
    """Journal DOIs sharing a preprint prefix are not treated as preprints."""
    assert preserve_preprint(None, old, new) is None


def test_both_validation_sides_preserve_the_extracted_preprint():
    """Consensus/admin originals and the replication publication field survive sync."""
    result = validated_identifiers({'doi_o': ARTICLE, 'doi_r': PREPRINT,
                                   'doi_r_published': ARTICLE,
                                   '_extracted_identifiers': {'doi_o': PREPRINT}})
    assert result == {'doi_o': ARTICLE, 'doi_r': ARTICLE,
                      'alt_identifier_o': PREPRINT, 'alt_identifier_r': PREPRINT}


def test_intermediate_versions_are_followed_but_unrelated_decisions_are_ignored():
    """Only logged transitions reaching the published DOI add intermediate aliases."""
    version = PREPRINT + '_v2'
    result = validated_identifiers({'doi_o': ARTICLE, '_identifier_history': [
        {'doi_o': [PREPRINT, version]}, {'doi_o': [version, ARTICLE]},
        {'doi_o': ['10.31234/osf.io/other', '10.1234/unrelated']}]})
    assert result['alt_identifier_o'] == PREPRINT + ', ' + version


def test_audit_counts_rows_and_distinct_missing_identifiers_from_real_transitions():
    """A two-stage correction counts once per missing alias, excluding existing aliases."""
    records = [{'id': 'REPL-1', 'doi_o': ARTICLE, 'doi_r': ARTICLE,
                'alt_identifier_r': 'https://doi.org/' + PREPRINT.upper()}]
    history = [{'id': 'REPL-1', 'side': side, 'old': old, 'new': new, 'evidence': 'log:1'}
               for side, old, new in [('o', PREPRINT, PREPRINT + '_v2'),
                                      ('o', PREPRINT + '_v2', ARTICLE),
                                      ('r', PREPRINT, ARTICLE)]]
    findings = audit.dropped_identifiers(records, history)
    assert [f['old_identifier'] for f in findings] == [PREPRINT, PREPRINT + '_v2']
    assert all(f['side'] == 'o' for f in findings)
    assert audit.dropped_identifiers(records, []) == []


def test_backfill_groups_both_sides_in_one_logged_save(monkeypatch):
    """Backfill changes alternatives only and checks the resulting publication."""
    cur = Mock()
    monkeypatch.setattr(audit.flora_edits, 'version', lambda *a: 9)
    monkeypatch.setattr(audit.flora_service, 'get_record', lambda *a: {
        'doi_o': ARTICLE, 'doi_r': ARTICLE, 'alt_identifier_r': 'https://example.org/old'})
    update = Mock(return_value={'change_id': 10})
    monkeypatch.setattr(audit.flora_service, 'update_record', update)
    fields = {'alt_identifier_o': PREPRINT,
              'alt_identifier_r': 'https://example.org/old, ' + PREPRINT}
    monkeypatch.setattr(audit.flora_store, 'get_record', lambda *a: fields)
    findings = [{'id': 'REPL-1', 'side': side, 'old_identifier': PREPRINT, 'current_doi': ARTICLE}
                for side in ('o', 'r')]
    assert audit.backfill(cur, findings, {'handle': 'test'})[0]['change_id'] == 10
    assert update.call_count == 1
    assert update.call_args.args[2] == fields


def test_backfill_refuses_a_concurrent_primary_doi_change(monkeypatch):
    """Historical evidence cannot authorize appending to a changed record."""
    monkeypatch.setattr(audit.flora_edits, 'version', lambda *a: 9)
    monkeypatch.setattr(audit.flora_service, 'get_record', lambda *a: {'doi_o': '10.1234/different'})
    with pytest.raises(ValueError, match='Primary DOI changed'):
        audit.backfill(Mock(), [{'id': 'REPL-1', 'side': 'o', 'old_identifier': PREPRINT,
                                 'current_doi': ARTICLE}], {'handle': 'test'})


def test_bridge_publishes_aliases_on_both_sides_without_exporting_private_history():
    """A validator's corrected original and published replication reach source_records."""
    row = sync_validated._build_row({
        'validated_record_id': '123', 'doi_r': PREPRINT, 'doi_r_published': ARTICLE,
        'doi_o': ARTICLE, '_extracted_identifiers': {'doi_o': PREPRINT},
        '_identifier_history': [{'doi_o': [PREPRINT, ARTICLE]}]})
    assert row['doi_r'] == ARTICLE and row['doi_o'] == ARTICLE
    assert row['alt_identifier_r'] == PREPRINT and row['alt_identifier_o'] == PREPRINT
    assert not any(k.startswith('_') for k in row['raw'].adapted)


def test_daily_export_uses_the_same_alias_policy(monkeypatch):
    """The legacy CSV path retains the same alternatives as the source bridge."""
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    import export_validated
    frame = pd.DataFrame([{'doi_r': PREPRINT, 'doi_r_published': ARTICLE, 'doi_o': ARTICLE,
                           '_extracted_identifiers': {'doi_o': PREPRINT},
                           '_identifier_history': None}])
    result = export_validated.retain_preprint_identifiers(frame)
    assert result.loc[0, 'doi_r'] == ARTICLE
    assert result.loc[0, 'alt_identifier_o'] == PREPRINT
    assert not any(k.startswith('_') for k in result.columns)


@pytest.mark.parametrize('side', ['o', 'r'])
def test_unpublished_manual_entry_logs_the_alias_with_its_doi_change(monkeypatch, side):
    """A hand-entered record preserves a preprint even before it first reaches FLoRA."""
    import manual_records
    doi, alt = 'doi_' + side, 'alt_identifier_' + side
    record = {**dict.fromkeys(manual_records.FIELDS), 'manual_id': 'manual-1',
              'display_id': 'MAN-1', 'status': 'active', 'version': 1, doi: PREPRINT}
    values = {**dict.fromkeys(manual_records.FIELDS), doi: ARTICLE}
    monkeypatch.setattr(manual_records, '_resolve', lambda *a, **k: record)
    monkeypatch.setattr(manual_records, '_frozen', lambda *a: None)
    monkeypatch.setattr(manual_records, 'clean', lambda *a: dict(values))
    monkeypatch.setattr(manual_records, '_refuse_excluded', lambda *a: None)
    monkeypatch.setattr(manual_records, '_project', lambda *a: None)
    log = Mock()
    monkeypatch.setattr(manual_records, '_log', log)
    manual_records.update(Mock(), 'manual-1', values, 'Published version identified', 1, {'handle': 'test'})
    assert log.call_args.args[4][alt] == [None, PREPRINT]
