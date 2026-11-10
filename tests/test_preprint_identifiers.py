"""History-backed identifier retention and backfill, without external API calls."""
from unittest.mock import Mock

import pytest

import backfill_preprint_identifiers as audit
from preprint_identifiers import preserve_preprint, validated_identifiers

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
