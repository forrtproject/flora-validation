import json
from unittest.mock import Mock

import pandas as pd
import pytest

from scripts.apply_score_report_dois import apply_batch, load_batch


@pytest.fixture
def batch(monkeypatch):
    import flora_edits
    import flora_service
    doi = '10.17605/osf.io/cwmb5'
    rows = [{'id': 'A', 'doi_r': doi, 'baseline': {'url_r': 'https://osf.io/9hjxc'},
             'evidence': {'identifier_api': 'https://api.osf.io/v2/nodes/cwmb5/identifiers/'}}]
    old = {'export_id': 'A', 'doi_r': None, 'url_r': rows[0]['baseline']['url_r'],
           'author_r': '[{"family":"Ritov"}]', 'alt_identifier_r': '10.1038/s41586-025-10078-y'}
    new = {**old, 'doi_r': doi}
    dataset = Mock(side_effect=lambda cur: pd.DataFrame([old if dataset.call_count == 1 else new]))
    monkeypatch.setattr(flora_service, 'dataset', dataset)
    save = Mock(return_value={'change_id': 999})
    publish = Mock(return_value=True)
    monkeypatch.setattr(flora_edits, 'save', save)
    monkeypatch.setattr(flora_edits, 'publish', publish)
    cur = Mock()
    cur.fetchall.side_effect = [[{'id': 'A'}], [{'export_id': 'A', 'version': 746}]]
    return rows, old, new, cur, save, publish


def test_rebuilds_and_preserves_report_byline_and_score_alternative(batch):
    rows, old, new, cur, save, publish = batch
    receipt = apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'})
    assert receipt[0]['after'] == rows[0]['doi_r']
    assert save.call_args.args[3] == {'doi_r': rows[0]['doi_r']}
    assert publish.call_args.args[2]['author_r'] == old['author_r']
    assert publish.call_args.args[2]['alt_identifier_r'] == old['alt_identifier_r']


def test_refuses_existing_primary_doi_before_writing(batch):
    rows, old, new, cur, save, publish = batch
    old['doi_r'] = '10.17605/osf.io/23ghe'
    with pytest.raises(ValueError, match='replace an existing DOI'):
        apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'})
    save.assert_not_called()


def test_refuses_metadata_changes_before_publishing(batch):
    rows, old, new, cur, save, publish = batch
    new['author_r'] = '[{"literal":"SCORE consortium"}]'
    with pytest.raises(ValueError, match='Metadata changed.*author_r'):
        apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'})
    publish.assert_not_called()


def test_reapplication_is_noop(batch):
    rows, old, new, cur, save, publish = batch
    old['doi_r'] = rows[0]['doi_r'].upper()
    assert apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'}) == []
    save.assert_not_called()
    publish.assert_not_called()


def test_only_identifier_endpoint_values_are_accepted(tmp_path):
    row = {'id': 'A', 'doi_r': '10.17605/OSF.IO/CWMB5',
           'evidence': {'node': 'cwmb5', 'dois': ['10.17605/OSF.IO/CWMB5']}}
    p = tmp_path / 'batch.json'
    p.write_text(json.dumps({'corrections': [row]}))
    assert load_batch(p) == [row]
    row['evidence']['dois'] = []
    p.write_text(json.dumps({'corrections': [row]}))
    with pytest.raises(ValueError, match='not returned'):
        load_batch(p)
