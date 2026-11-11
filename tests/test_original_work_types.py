"""Advisory target screening, cache failures and durable per-record identities."""
import io
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

import entry_checks
import enrich_works
import original_work_types as types
import validate_flora_network as network

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
DOI = '10.1002/npr2.70058'


def cache(work_type='erratum', checked=NOW):
    """One successful cached answer, with a controllable age."""
    return {DOI: {'work_type': work_type, 'work_type_checked_at': checked}}


def frame(**overrides):
    """One stable record whose titles can change independently of its DOI."""
    return pd.DataFrame([{'id': 'FLORA-1', 'flora_id': 'REPL-1', 'doi_o': DOI,
                          'doi_r': '10.1000/replication', 'title_o': 'Correction', **overrides}])


@pytest.mark.parametrize('work_type,expected', [
    ('erratum', types.HIGH_CHECK), ('dataset', types.HIGH_CHECK),
    ('paratext', types.HIGH_CHECK), ('review', types.FORMAT_CHECK),
    ('book-chapter', types.FORMAT_CHECK), ('conference-paper', types.FORMAT_CHECK),
    ('new-provider-format', types.FORMAT_CHECK), ('article', None), ('preprint', None),
])
def test_type_labels_only_create_advisory_review_findings(work_type, expected):
    """Legitimate formats are reviewable, and an erratum cannot be vetoed by Crossref."""
    findings, unchecked = types.check(None, frame(crossref_type='journal-article'),
                                      cache=cache(work_type), now=NOW)
    assert not unchecked
    assert [f['check'] for f in findings] == ([expected] if expected else [])
    if findings:
        assert 'changes no record' in findings[0]['detail']


def test_keys_preserve_review_across_title_spelling_and_group_membership_changes():
    """Provider wording and another row sharing a DOI cannot erase an old decision."""
    before = types.check(None, frame(), cache=cache(), now=NOW)[0][0]
    changed = frame(doi_o='https://doi.org/' + DOI.upper(), title_o='New title')
    added = pd.concat([changed, frame(flora_id='REPL-2', id='FLORA-2')], ignore_index=True)
    after = types.check(None, added, cache=cache('dataset'), now=NOW)[0]
    assert before['key'] == after[0]['key']
    assert before['key'] != after[1]['key']
    assert len({f['key'] for f in after}) == 2


def test_a_genuine_original_change_has_a_new_review_key():
    """A decision about the old original cannot approve a different target."""
    old = types.check(None, frame(), cache=cache(), now=NOW)[0][0]
    other = '10.1000/new'
    new = types.check(None, frame(doi_o=other), cache={other: cache()[DOI]}, now=NOW)[0][0]
    assert old['key'] != new['key']


def test_risk_escalation_keeps_the_old_decision_and_requests_a_separate_review():
    """The two check codes prevent a low-priority dismissal from approving new risk."""
    low = types.check(None, frame(), cache=cache('review'), now=NOW)[0][0]
    high = types.check(None, frame(), cache=cache('erratum'), now=NOW)[0][0]
    assert low['key'] != high['key']


@pytest.mark.parametrize('evidence', [{}, cache(None), cache('erratum', NOW - timedelta(days=40))])
def test_unknown_and_stale_metadata_do_not_make_a_clean_answer(evidence):
    """Unknown types remain visible as unavailable and cannot close an earlier alert."""
    answered, stats = {}, {}
    findings, unknown = types.check(None, frame(), cache=evidence, now=NOW,
                                    answered=answered, statistics=stats)
    assert not findings and len(unknown) == 1
    assert answered == {DOI: None}
    assert stats['unchecked'] == 1 and stats['advisory']
    assert unknown[0]['flora_ids'] == ['REPL-1']


@pytest.mark.parametrize('answer,resolved', [(None, False), ('ok', True)])
def test_network_resolution_requires_an_answer_for_the_current_original(answer, resolved):
    """A failed refresh preserves the alert, including equivalent resolver spellings."""
    finding = {'finding_key': 'key', 'check_code': types.HIGH_CHECK,
               'column_name': 'doi_o', 'value': DOI, 'flora_ids': ['REPL-1']}
    records = {'REPL-1': {'doi_o': 'https://doi.org/' + DOI.upper()}}
    actual = entry_checks._network_resolved([finding], records, {}, {'work_types': {DOI: answer}})
    assert actual == ({'key'} if resolved else set())
    assert entry_checks._network_resolved([finding], {}, {}, {}) == {'key'}
    assert entry_checks._network_resolved([finding], {'REPL-1': {'doi_o': '10.1000/new'}}, {}, {}) == {'key'}


def test_alias_fallback_is_cached_under_the_requested_identifier(monkeypatch):
    """An OpenAlex merge must not mark a cached preprint as a missing bibliography."""
    requested, published = '10.31234/osf.io/abcde', '10.1000/article'
    monkeypatch.setattr(types, 'original_dois', lambda cur: [requested])
    monkeypatch.setattr(types, 'load_cache', lambda cur: {})
    monkeypatch.setattr(types, 'fetch_batch', lambda ds: [
        {'id': 'https://openalex.org/W1', 'doi': published, 'type': 'article'}])
    one = Mock(return_value={'id': 'https://openalex.org/W1', 'doi': published, 'type': 'article'})
    monkeypatch.setattr(types, 'fetch_one', one)
    remember = Mock()
    monkeypatch.setattr(types, 'remember', remember)
    stats = types.refresh(None)
    one.assert_called_once_with(requested)
    remember.assert_called_once_with(None, requested, 'article')
    assert stats['updated'] == 1 and not stats['errors']


def test_rate_limit_stops_the_refresh_without_early_retries_or_cache_writes(monkeypatch):
    """A long Retry-After cannot stall publication or be retried before its deadline."""
    monkeypatch.setattr(types, 'original_dois', lambda cur: [DOI])
    monkeypatch.setattr(types, 'load_cache', lambda cur: {})
    fetch = Mock(side_effect=urllib.error.HTTPError('https://api.openalex.org', 429,
                                                   'Too many requests', {'Retry-After': '3600'}, None))
    remember = Mock()
    monkeypatch.setattr(types, 'fetch_batch', fetch)
    monkeypatch.setattr(types, 'remember', remember)
    stats = types.refresh(None)
    assert fetch.call_count == 1 and remember.call_count == 0
    assert stats['deferred'] == 1 and len(stats['errors']) == 1


def test_successful_partial_answers_survive_a_later_lookup_failure(monkeypatch):
    """Only completed answers are persisted; provider failures never become misses."""
    other = '10.1000/other'
    monkeypatch.setattr(types, 'original_dois', lambda cur: [DOI, other])
    monkeypatch.setattr(types, 'load_cache', lambda cur: {})
    monkeypatch.setattr(types, 'fetch_batch', lambda ds: [{'id': 'W1', 'doi': DOI, 'type': 'erratum'}])
    monkeypatch.setattr(types, 'fetch_one', Mock(side_effect=urllib.error.URLError('unavailable')))
    remember = Mock()
    monkeypatch.setattr(types, 'remember', remember)
    stats = types.refresh(None)
    remember.assert_called_once_with(None, DOI, 'erratum')
    assert stats['updated'] == 1 and stats['deferred'] == 1


def test_type_only_cache_writes_do_not_overwrite_references_or_not_found():
    """The SQL update is confined to the two type evidence columns."""
    cur = Mock()
    types.remember(cur, DOI, 'erratum')
    sql = cur.execute.call_args.args[0].split('DO UPDATE SET')[1]
    assert 'not_found' not in sql and 'title' not in sql and 'fetched_at' not in sql


def test_requests_use_a_generic_client_and_only_an_explicit_contact(monkeypatch):
    """No personal contact is embedded in type discovery."""
    monkeypatch.delenv('OPENALEX_MAILTO', raising=False)
    monkeypatch.setattr(types.time, 'sleep', lambda *a: None)
    response = Mock()
    response.__enter__ = Mock(return_value=io.BytesIO(b'{"results": []}'))
    response.__exit__ = Mock(return_value=None)
    urlopen = Mock(return_value=response)
    monkeypatch.setattr(types.urllib.request, 'urlopen', urlopen)
    assert types.fetch_batch([DOI]) == []
    req = urlopen.call_args.args[0]
    assert 'mailto=' not in req.full_url and req.get_header('User-agent') == 'flora-validation/1.0'


@pytest.mark.parametrize('work_type', ['erratum', 'review', None])
def test_type_only_validation_is_advisory_even_when_metadata_is_unavailable(monkeypatch, work_type):
    """Neither tier, nor an unavailable cache, changes the strict CLI failure decision."""
    monkeypatch.setattr(types, 'load_cache', lambda cur: {
        DOI: {'work_type': work_type, 'work_type_checked_at': datetime.now(timezone.utc)}})
    report = network.validate(None, frame(), checks='work_types')
    assert not report['problems'] and report['status'] != 'incomplete'
    assert all(r['level'] == 'possible_problem' for r in report['results'])
    assert 'Original work types (advisory)' in network.render_validation(report)


def test_existing_openalex_enrichment_preserves_work_type_for_dois_and_work_ids():
    """The already-requested field is no longer thrown away by either shape."""
    work = {'id': 'https://openalex.org/W1', 'doi': 'https://doi.org/' + DOI,
            'type': 'erratum', 'title': 'Correction'}
    assert enrich_works.shape(work)['work_type'] == 'erratum'
    assert enrich_works.shape_by_work_id({**work, 'doi': None}, 'W1')['work_type'] == 'erratum'


def test_type_screening_never_reads_the_onceoff_model_audit(monkeypatch):
    """Saved votes and their availability cannot create or suppress a warning."""
    def refuse_audit_read(*args, **kwargs):
        raise AssertionError('Once-off review files must not be read by the daily check')
    monkeypatch.setattr(Path, 'open', refuse_audit_read)
    for kind in ('article', 'preprint'):
        assert not types.check(None, frame(), cache=cache(kind), now=NOW)[0]
    assert types.check(None, frame(), cache=cache('dataset'), now=NOW)[0][0]['check'] == types.HIGH_CHECK


def test_removed_identity_check_resolves_without_erasing_its_human_decision():
    """Historic model-based findings are retired by the existing obsolete-check path."""
    old = {'finding_key': 'identity', 'check_code': 'original_target_identity',
           'column_name': 'doi_o', 'value': DOI, 'flora_ids': ['REPL-1']}
    assert entry_checks._network_resolved([old], {'REPL-1': {'doi_o': DOI}}, {}, {}) == {'identity'}


@pytest.mark.parametrize('minutes', [-1, float('nan'), float('inf')])
def test_invalid_budgets_cannot_remove_the_refresh_deadline(minutes):
    """Even a caller-supplied budget cannot create an unbounded refresh."""
    with pytest.raises(ValueError, match='finite nonnegative'):
        types.refresh(None, minutes=minutes)
