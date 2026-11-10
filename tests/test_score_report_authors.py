"""The reviewed batch must only change references, through the ordinary log."""
from unittest.mock import Mock

import pytest
import flora_service
from scripts.apply_score_report_authors import apply_batch, load_batch


def test_batch_is_bibliographic_and_valid_for_both_record_types():
    import flora_edits
    rows = load_batch()
    assert rows
    for row in rows:
        for kind in ('replication', 'reproduction'):
            for field, value in row['fields'].items():
                flora_edits.clean_value(field, value, kind)


def test_preflight_rejects_changed_reference_before_any_save(monkeypatch):
    rows = load_batch()[:2]
    get = Mock(side_effect=[{'ref_r': rows[0]['before_ref_r']}, {'ref_r': 'new reference'}])
    update = Mock()
    monkeypatch.setattr(flora_service, 'get_record', get)
    monkeypatch.setattr(flora_service, 'editing', Mock(return_value={'version': 7}))
    monkeypatch.setattr(flora_service, 'update_record', update)
    with pytest.raises(ValueError, match='changed since audit'):
        apply_batch(None, rows, {'id': 1})
    update.assert_not_called()


def test_apply_uses_service_version_and_is_idempotent(monkeypatch):
    row = load_batch()[0]
    get = Mock(return_value={'ref_r': row['before_ref_r']})
    update = Mock()
    monkeypatch.setattr(flora_service, 'get_record', get)
    monkeypatch.setattr(flora_service, 'editing', Mock(return_value={'version': 7}))
    monkeypatch.setattr(flora_service, 'update_record', update)
    assert apply_batch(None, [row], {'id': 1}) == 1
    assert update.call_args.args[4] == 7
    get.return_value = row['fields']
    update.reset_mock()
    assert apply_batch(None, [row], {'id': 1}) == 0
    update.assert_not_called()
