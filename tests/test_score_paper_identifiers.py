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
    assert append_identifier('10.17605/osf.io/w3kuz', doi) == '10.17605/osf.io/w3kuz, ' + doi
    existing = 'https://doi.org/' + doi.upper() + '; local:42'
    assert append_identifier(existing, doi) == existing
    assert append_identifier('NA', doi) == doi
    with pytest.raises(ValueError, match='Conflicting SCORE'):
        append_identifier(PAPERS['reproduction'][1], doi)


def test_batch_separates_papers_and_requires_attempt_evidence(tmp_path):
    import csv
    from scripts.apply_score_paper_identifiers import BATCH
    rows = load_batch()
    assert len(rows) == 301
    assert sum(r['type'] == 'replication' for r in rows) == 166
    assert sum(r['type'] == 'reproduction' for r in rows) == 135
    with BATCH.open(encoding='utf-8-sig', newline='') as f:
        table = list(csv.DictReader(f))
    path = tmp_path / 'batch.csv'
    def write():
        with path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(table[0]))
            writer.writeheader(); writer.writerows(table)
    first = table[0]
    other = 'replication' if first['type'] == 'reproduction' else 'reproduction'
    first['add_alt_identifier_r'] = PAPERS[other][1]
    write()
    with pytest.raises(ValueError, match='does not match record type'):
        load_batch(path)
    first['add_alt_identifier_r'] = PAPERS[first['type']][1]
    first['report_ids'] = '[]'
    write()
    with pytest.raises(ValueError, match='published-outcome evidence'):
        load_batch(path)






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
    cur.fetchall.side_effect = [[{'export_id': r['id'], 'version': 42} for r in rows],
                               [{'id': r['id']} for r in rows]]
    return rows, records, cur, save, publish


def test_current_values_are_preserved_and_only_alt_is_logged(current):
    rows, records, cur, save, publish = current
    receipt = apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'})
    assert len(receipt) == 2
    for call in save.call_args_list:
        assert set(call.args[3]) == {'alt_identifier_r'}
        assert call.args[3]['alt_identifier_r'].startswith('local:keep, ')
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
        record['doi_r'] = '10.17605/osf.io/cwmb5'  # filled by the later report-DOI batch
    assert apply_batch(cur, rows, {'id': 3, 'handle': 'Lukas'}) == []
    save.assert_not_called()
    publish.assert_not_called()


@pytest.mark.parametrize('report_ids', ['not-a-list', {'guid': 'abcde'}, [42], [' ']])
def test_report_evidence_requires_nonblank_identifier_list(tmp_path, report_ids):
    import csv
    from scripts.apply_score_paper_identifiers import BATCH
    with BATCH.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    rows[0]['report_ids'] = json.dumps(report_ids)
    path = tmp_path / 'batch.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match='list of nonblank report identifiers'):
        load_batch(path)


def test_truncated_batch_is_rejected_against_reviewed_counts(tmp_path):
    from scripts.apply_score_paper_identifiers import BATCH
    path = tmp_path / 'batch.csv'
    path.write_text('\n'.join(BATCH.read_text(encoding='utf-8').splitlines()[:-1]) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='counts differ from reviewed provenance'):
        load_batch(path)


def test_concurrent_correction_during_snapshot_cannot_be_overwritten(current, monkeypatch):
    import flora_edits
    import flora_service
    rows, records, cur, save, publish = current
    latest = {'version': 42}
    def snapshot(_):
        latest['version'] = 43  # another editor commits after our version read
        return pd.DataFrame(records)
    monkeypatch.setattr(flora_service, 'dataset', snapshot)
    cur.fetchall.side_effect = lambda: ([{'export_id': r['id'], 'version': latest['version']} for r in rows]
                                      if 'MAX(change_id)' in cur.execute.call_args.args[0]
                                      else [{'id': r['id']} for r in rows])
    def guarded_save(*args):
        if args[5] != latest['version']:
            raise flora_edits.VersionConflict(latest['version'])
        return {'change_id': 900}
    save.side_effect = guarded_save
    with pytest.raises(flora_edits.VersionConflict):
        apply_batch(cur, rows, {'id': 3})
    publish.assert_not_called()
