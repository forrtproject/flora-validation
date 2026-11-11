import copy
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from scripts.apply_score_paper_identifiers import (
    PAPERS, append_identifier, apply_batch, load_batch,
)


def test_identifiers_are_appended_without_erasing_existing_values():
    doi = PAPERS['replication'][1]
    assert append_identifier('10.17605/osf.io/w3kuz', doi) == '10.17605/osf.io/w3kuz; ' + doi
    existing = 'https://doi.org/' + doi.upper() + '; local:42'
    assert append_identifier(existing, doi) == existing
    assert append_identifier('NA', doi) == doi
    with pytest.raises(ValueError, match='Conflicting SCORE'):
        append_identifier(PAPERS['reproduction'][1], doi)


def test_batch_separates_papers_and_requires_attempt_evidence(tmp_path):
    rows = load_batch()
    assert len(rows) == 301
    assert sum(r['type'] == 'replication' for r in rows) == 166
    assert sum(r['type'] == 'reproduction' for r in rows) == 135
    assert {r['add_alt_identifier_r'] for r in rows if r['type'] == 'reproduction'} == {
        PAPERS['reproduction'][1]}
    from scripts.apply_score_paper_identifiers import BATCH
    batch = json.loads(BATCH.read_text())
    first = batch['corrections'][0]
    other = 'replication' if first['type'] == 'reproduction' else 'reproduction'
    first['add_alt_identifier_r'] = PAPERS[other][1]
    path = tmp_path / 'batch.json'
    path.write_text(json.dumps(batch))
    with pytest.raises(ValueError, match='does not match record type'):
        load_batch(path)
    first['add_alt_identifier_r'] = PAPERS[first['type']][1]
    first['evidence']['report_ids'] = []
    path.write_text(json.dumps(batch))
    with pytest.raises(ValueError, match='published-outcome evidence'):
        load_batch(path)


def test_both_paper_dois_classify_new_records_without_intake_or_title_hints():
    from pathlib import Path
    import yaml
    from flora_release_projects import classify_projects

    root = Path(__file__).resolve().parents[1]
    registry = yaml.safe_load((root / 'release/flora/projects.yml').read_text())
    records = {f'new-{i}': {'source': 'validated', 'title_r': 'Report',
                           'alt_identifier_r': '10.17605/osf.io/w3kuz; ' + r['add_alt_identifier_r']}
               for i, r in enumerate(load_batch())}
    assigned, report = classify_projects(records, registry)
    assert assigned == {identifier: 'SCORE' for identifier in records}
    assert not report['review_required']


@pytest.fixture
def current(monkeypatch):
    import flora_edits
    import flora_service

    rows = copy.deepcopy(load_batch()[:2])
    records = [{'export_id': r['id'], 'type': r['type'], **r['baseline'],
                'alt_identifier_r': 'local:keep', 'outcome': 'mixed'} for r in rows]
    monkeypatch.setattr(flora_service, 'dataset', lambda cur: pd.DataFrame(records))
    save = Mock(return_value={'change_id': 900})
    publish = Mock(return_value=True)
    monkeypatch.setattr(flora_edits, 'save', save)
    monkeypatch.setattr(flora_edits, 'publish', publish)
    cur = Mock()
    cur.fetchall.side_effect = [[{'id': r['id']} for r in rows],
                               [{'export_id': r['id'], 'version': 42} for r in rows]]
    return rows, records, cur, save, publish


def test_current_values_are_preserved_and_only_alt_is_logged(current):
    rows, records, cur, save, publish = current
    receipt = apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'})
    assert len(receipt) == 2
    for call in save.call_args_list:
        assert set(call.args[3]) == {'alt_identifier_r'}
        assert call.args[3]['alt_identifier_r'].startswith('local:keep; ')
        assert call.args[5] == 42
    for call in publish.call_args_list:
        before, after = call.args[1:]
        assert {k for k in before if before[k] != after[k]} == {'alt_identifier_r'}


def test_entire_batch_is_checked_before_writes(current):
    rows, records, cur, save, publish = current
    records[-1]['url_r'] = 'https://osf.io/changed'
    with pytest.raises(ValueError, match='url_r changed since review'):
        apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'})
    save.assert_not_called()
    publish.assert_not_called()


def test_reapplying_existing_dois_is_a_noop(current):
    rows, records, cur, save, publish = current
    for row, record in zip(rows, records):
        record['alt_identifier_r'] = 'local:keep; ' + row['add_alt_identifier_r']
    assert apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'}) == []
    save.assert_not_called()
    publish.assert_not_called()
