"""The author batch preserves identities and never turns unknown writers into 0%."""
import json
from unittest.mock import Mock

import pandas as pd
import pytest
import flora_service
from scripts.apply_score_report_authors import apply_batch, load_batch, preflight


def current(row):
    return {'ref_r': row['before_ref_r'], 'url_r': row['before_url_r'],
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
    monkeypatch.setattr(flora_service, 'editing', Mock(return_value={'version': 7}))
    with pytest.raises(ValueError, match='changed since audit'):
        apply_batch(None, rows, {'id': 1})
    update.assert_not_called()


def test_identity_guard_is_not_weakened_for_cosmetic_reference_refresh(monkeypatch):
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
    monkeypatch.setattr(flora_service, 'editing', Mock(return_value={'version': 7}))
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
    assert result.iloc[0]['author_r'] is None
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
