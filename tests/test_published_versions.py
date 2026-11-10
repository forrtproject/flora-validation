import json
from unittest.mock import Mock

import pandas as pd
import requests

import preprint_dedup
from published_versions import Client, linked_versions, osf_id, scan

PREPRINT = '10.31219/osf.io/x394e_v2'
ARTICLE = '10.1234/published'


class FakeClient:
    def __init__(self, article_doi='', relations=None, reverse=None, target_type='journal-article'):
        self.article_doi = article_doi
        self.relations = relations or {}
        self.reverse = reverse or []
        self.target_type = target_type
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if 'osf.io' in url:
            return {'data': {'attributes': {'article_doi': self.article_doi}}}
        return {'message': {'items': self.reverse}}

    def work(self, doi):
        if doi == PREPRINT:
            return {'relation': self.relations}
        return {'type': self.target_type, 'title': ['Published paper']}


def test_seda_no_publication():
    assert linked_versions(PREPRINT, FakeClient()) == []
    assert osf_id(PREPRINT) == 'x394e'
    assert osf_id('10.31219/osf.io/x394e') == 'x394e'


def test_osf_article_and_crossref_directions():
    relation = lambda doi: [{'id-type': 'doi', 'id': doi}]
    for client in [FakeClient(article_doi='https://doi.org/' + ARTICLE),
                   FakeClient(relations={'is-preprint-of': relation(ARTICLE)}),
                   FakeClient(relations={'is-version-of': relation(ARTICLE)}),
                   FakeClient(reverse=[{'DOI': ARTICLE, 'relation': {'has-preprint': relation(PREPRINT)}}])]:
        assert linked_versions(PREPRINT, client)[0]['doi_2'] == ARTICLE
    assert linked_versions(PREPRINT, FakeClient(article_doi=ARTICLE, target_type='posted-content')) == []
    assert linked_versions(PREPRINT, FakeClient(article_doi=PREPRINT)) == []
    assert linked_versions(PREPRINT, FakeClient(relations={'has-preprint': relation(ARTICLE)})) == []


def test_scan_conflicts_and_repeated_rows():
    f = pd.DataFrame({'id': ['a', 'b'], 'doi_o': [PREPRINT]*2, 'doi_r': [PREPRINT]*2})
    client = FakeClient(article_doi=ARTICLE)
    rows = scan(f, client)
    assert len(rows) == 2
    assert rows[0]['row_count'] == 2
    assert rows[1]['side'] == 'replication published version'
    assert all(r['proposed_action'] == 'keep_2' and r['action'] == '' for r in rows)
    assert len(client.calls) == 2  # OSF and reverse Crossref, once for both sides
    client = FakeClient(article_doi=ARTICLE, relations={'is-preprint-of': [{'id-type': 'doi', 'id': '10.1234/other'}]})
    assert all(r['proposed_action'] == '' for r in scan(f, client))


def test_cache_misses_expiry_and_transient_failure(tmp_path, monkeypatch):
    monkeypatch.setattr('published_versions.time.sleep', lambda _: None)
    client = Client(tmp_path)
    response = Mock(status_code=404)
    client.session.get = Mock(return_value=response)
    assert client.get('https://example.org/a') == {}
    client.get('https://example.org/a')
    assert client.session.get.call_count == 1
    path = next(tmp_path.glob('*.json'))
    data = json.loads(path.read_text()); data['at'] = 0; path.write_text(json.dumps(data))
    client.get('https://example.org/a')
    assert client.session.get.call_count == 2
    client.session.get = Mock(side_effect=requests.Timeout('temporary'))
    assert client.get('https://example.org/b') == {}
    assert len(list(tmp_path.glob('*.json'))) == 1
    assert client.errors


def test_confirmed_standalone_publication_preserves_preprint(tmp_path):
    f = pd.DataFrame({'doi_o': ['10.1234/original'], 'doi_r': [PREPRINT],
                      'id': ['a'], 'type': ['replication'], 'outcome': ['successful']})
    path = tmp_path / 'confirmed.csv'
    pd.DataFrame([{'side': 'replication published version', 'doi_1': PREPRINT,
                   'doi_2': ARTICLE, 'action': 'keep_2'}]).to_csv(path, index=False)
    result = preprint_dedup.apply_confirmed(f, path, verbose=False)
    assert result.iloc[0]['doi_r'] == ARTICLE
    assert result.iloc[0]['alt_identifier_r'] == PREPRINT
    assert result.iloc[0]['id'] == 'a'


def test_confirmed_publication_merges_existing_row(tmp_path):
    f = pd.DataFrame({'doi_o': ['10.1234/original']*2, 'doi_r': [PREPRINT, ARTICLE],
                      'id': ['a', 'b'], 'type': ['replication']*2,
                      'outcome': ['successful']*2})
    path = tmp_path / 'confirmed.csv'
    pd.DataFrame([{'side': 'replication published version', 'doi_1': PREPRINT,
                   'doi_2': ARTICLE, 'action': 'keep_2'}]).to_csv(path, index=False)
    result = preprint_dedup.apply_confirmed(f, path, verbose=False)
    assert len(result) == 1
    assert result.iloc[0]['doi_r'] == ARTICLE
    assert result.iloc[0]['alt_identifier_r'] == PREPRINT


def test_confirmed_targets_enriched_before_transform(monkeypatch):
    import enrich_works
    import transform_sources
    from unittest.mock import Mock
    monkeypatch.setattr(transform_sources, 'load_dedup_decisions', lambda cur: [
        {'side': 'replication published version', 'doi_1': PREPRINT,
         'doi_2': ARTICLE, 'action': 'keep_2'}])
    monkeypatch.setattr(preprint_dedup, 'load_confirmed', lambda path: [])
    assert enrich_works.confirmed_publication_targets(Mock()) == [ARTICLE]


def test_reverse_relation_pagination():
    client = FakeClient()
    calls = []
    def get(url, params=None):
        if 'osf.io' in url:
            return {}
        calls.append(params['cursor'])
        if len(calls) == 1:
            return {'message': {'items': [{}]*100, 'next-cursor': 'next'}}
        return {'message': {'items': [{'DOI': ARTICLE, 'relation': {'has-preprint': [
            {'id-type': 'doi', 'id': PREPRINT}]}}]}}
    client.get = get
    assert linked_versions(PREPRINT, client)[0]['doi_2'] == ARTICLE
    assert calls == ['*', 'next']


def test_confirmed_original_publication_target_is_enriched(monkeypatch):
    import enrich_works
    import transform_sources
    monkeypatch.setattr(transform_sources, 'load_dedup_decisions', lambda cur: [])
    monkeypatch.setattr(preprint_dedup, 'load_confirmed', lambda path: [
        {'side': 'original', 'doi_1': PREPRINT, 'doi_2': ARTICLE, 'action': 'keep_2'}])
    assert enrich_works.confirmed_publication_targets(Mock()) == [ARTICLE]


def test_server_coverage_excludes_cshl_journals():
    from published_versions import eligible
    for doi in ['10.17605/osf.io/abcde', '10.31222/osf.io/abcde',
                '10.31234/osf.io/abcde_v2', '10.31235/osf.io/abcde',
                '10.1101/2026.01.01.123456', '10.64898/2026.01.01.123456',
                '10.2139/ssrn.123456', '10.21203/rs.3.rs-12345/v1']:
        assert eligible(doi)
    assert not eligible('10.1101/gr.126516.111')
    assert not eligible('10.1101/lm.035493.114')
