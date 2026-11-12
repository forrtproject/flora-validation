"""The author batch preserves identities and never turns unknown writers into 0%."""
import json
from unittest.mock import Mock

import pandas as pd
import pytest
import flora_service
import flora_edits
from scripts.apply_score_report_authors import apply_batch, load_batch, preflight


def current(row):
    return {'export_id': row['id'], 'ref_r': row['before_ref_r'], 'url_r': row['before_url_r'],
            'doi_o': row['before_doi_o'], 'type': row['type'],
            'doi_r': '10.17605/osf.io/cwmb5',
            'alt_identifier_r': '10.1038/s41586-025-10078-y'}


def test_batch_is_bibliographic_and_explicitly_represents_unknown_authors():
    import flora_edits
    rows = load_batch()
    assert len(rows) == 289
    assert sum(r['author_status'] == 'named' for r in rows) == 277
    assert sum(r['author_status'] == 'unknown' for r in rows) == 12
    for row in rows:
        for kind in ('replication', 'reproduction'):
            for field, value in row['fields'].items():
                flora_edits.clean_value(field, value, kind)
        assert not {'doi_r','alt_identifier_r','url_r','outcome'} & row['fields'].keys()


def test_preflight_rejects_changed_reference_before_any_save(monkeypatch):
    rows = load_batch()[:2]
    changed = current(rows[1]); changed['ref_r'] = 'new reference'
    monkeypatch.setattr(flora_service, 'get_record', Mock(side_effect=[current(rows[0]), changed]))
    update = Mock()
    monkeypatch.setattr(flora_service, 'update_record', update)
    monkeypatch.setattr(flora_edits, 'version', Mock(return_value=7))
    with pytest.raises(ValueError, match='changed since audit'):
        apply_batch(None, rows, {'id': 1})
    update.assert_not_called()


def test_identity_guard_is_not_weakened_for_cosmetic_reference_refresh(monkeypatch):
    monkeypatch.setattr(flora_edits, 'version', Mock(return_value=7))
    row = next(r for r in load_batch() if r['id'] == 'REPRO-000101')
    wrong = current(row); wrong['url_r'] = 'https://osf.io/cwmb5'
    monkeypatch.setattr(flora_service, 'get_record', Mock(return_value=wrong))
    with pytest.raises(ValueError, match='identity changed'):
        preflight(None, [row])


@pytest.mark.parametrize('status', ['named','unknown'])
def test_apply_uses_version_and_is_idempotent_for_nullable_fields(monkeypatch, status):
    row = next(r for r in load_batch() if r['author_status'] == status)
    get = Mock(return_value=current(row))
    update = Mock()
    monkeypatch.setattr(flora_service, 'get_record', get)
    monkeypatch.setattr(flora_edits, 'version', Mock(return_value=7))
    monkeypatch.setattr(flora_service, 'update_record', update)
    assert apply_batch(None, [row], {'id': 1}) == 1
    assert update.call_args.args[4] == 7
    assert set(update.call_args.args[2]) == {'ref_r','title_r','author_r','year_r','journal_r','bibtex_ref_r'}
    get.return_value = {**current(row), **row['fields']}
    update.reset_mock()
    assert apply_batch(None, [row], {'id': 1}) == 0
    update.assert_not_called()


def test_unknown_authors_recompute_overlap_as_missing():
    import flora_edits
    row = next(r for r in load_batch() if r['author_status'] == 'unknown')
    frame = pd.DataFrame([{'export_id': row['id'], 'source_record_id': 'source', 'type': row['type'],
                           'author_o': '[{"family":"Smith"}]',
                           'author_r': '[{"family":"Project administrator"}]',
                           'author_overlap': 0, 'author_overlap_pct': 0.0}])
    result = flora_edits.apply(None, frame, corrections={row['id']: row['fields']})
    assert pd.isna(result.iloc[0]['author_r'])
    assert pd.isna(result.iloc[0]['author_overlap'])
    assert pd.isna(result.iloc[0]['author_overlap_pct'])
    assert 'author =' not in row['fields']['bibtex_ref_r'].lower()


def test_unknown_without_review_evidence_is_rejected(tmp_path):
    rows = load_batch()
    row = json.loads(json.dumps(next(r for r in rows if r['author_status'] == 'unknown')))
    row['id'] = 'not-reviewed'
    p = tmp_path/'batch.json'; p.write_text(json.dumps([row]),encoding='utf-8')
    with pytest.raises(ValueError, match='held audit record'):
        load_batch(p)


@pytest.mark.parametrize('field', ['before_ref_r', 'before_url_r', 'before_doi_o', 'ref_r'])
def test_unknown_correction_must_match_retained_audit(tmp_path, field):
    row = next(r for r in load_batch() if r['author_status'] == 'unknown')
    (row['fields'] if field == 'ref_r' else row)[field] = 'changed after audit'
    path = tmp_path / 'batch.json'
    path.write_text(json.dumps([row]), encoding='utf-8')
    with pytest.raises(ValueError, match='differs from retained audit'):
        load_batch(path)


@pytest.mark.parametrize('assignment', ['author={Project staff}', 'AUTHOR = {Project staff}'])
def test_unknown_bibtex_cannot_credit_authors_with_alternative_spacing(tmp_path, assignment):
    row = next(r for r in load_batch() if r['author_status'] == 'unknown')
    row['fields']['bibtex_ref_r'] = '@misc{report, ' + assignment + '}'
    path = tmp_path / 'batch.json'
    path.write_text(json.dumps([row]), encoding='utf-8')
    with pytest.raises(ValueError, match='must not credit contributors'):
        load_batch(path)


@pytest.mark.parametrize('field,value', [('reason', ''), ('disposition', 'named_report_byline')])
def test_unknown_requires_reason_and_explicit_audit_disposition(tmp_path, monkeypatch, field, value):
    import csv
    import scripts.apply_score_report_authors as script
    row = next(r for r in load_batch() if r['author_status'] == 'unknown')
    with script.AUDIT.open(encoding='utf-8-sig', newline='') as f:
        records = list(csv.DictReader(f))
    next(r for r in records if r['row_id'] == row['id'])[field] = value
    path = tmp_path / 'audit.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    monkeypatch.setattr(script, 'AUDIT', path)
    with pytest.raises(ValueError, match='held audit record'):
        load_batch()


@pytest.mark.parametrize('authors', ['[]', '[{}]', '[{"literal":"SCORE team"}]'])
def test_named_batch_entry_needs_at_least_one_usable_family(tmp_path, authors):
    row = next(r for r in load_batch() if r['author_status'] == 'named')
    row['fields']['author_r'] = authors
    path = tmp_path / 'batch.json'
    path.write_text(json.dumps([row]), encoding='utf-8')
    with pytest.raises(ValueError, match='at least one author family name'):
        load_batch(path)


def test_concurrent_edit_after_version_read_is_not_overwritten(monkeypatch):
    row = load_batch()[0]
    latest = {'version': 7}
    monkeypatch.setattr(flora_edits, 'version', lambda *args: latest['version'])
    def snapshot(*args):
        latest['version'] = 8
        return current(row)
    monkeypatch.setattr(flora_service, 'get_record', snapshot)
    def update(cur, identifier, fields, revert, expected_version, *args):
        if expected_version != latest['version']:
            raise flora_edits.VersionConflict(latest['version'])
    monkeypatch.setattr(flora_service, 'update_record', update)
    with pytest.raises(flora_edits.VersionConflict):
        apply_batch(None, [row], {'id': 1})


def test_source_identity_drift_aborts_batch_before_commit(monkeypatch):
    row = load_batch()[0]
    changed = {**current(row), 'doi_o': '10.1234/changed'}
    monkeypatch.setattr(flora_edits, 'version', Mock(return_value=7))
    monkeypatch.setattr(flora_service, 'get_record', Mock(side_effect=[current(row), changed]))
    update = Mock()
    monkeypatch.setattr(flora_service, 'update_record', update)
    with pytest.raises(ValueError, match='identity changed since audit'):
        apply_batch(None, [row], {'id': 1})
    update.assert_called_once()
