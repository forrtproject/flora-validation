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
        if 'api.openalex.org' in url:
            return {}
        if 'api.osf.io' in url:
            return {'data': {'attributes': {'article_doi': self.article_doi}}}
        return {'message': {'items': self.reverse}}

    def work(self, doi):
        if doi == PREPRINT:
            return {'relation': self.relations}
        return {'type': self.target_type, 'title': ['Published paper']}


def test_seda_no_publication():
    assert linked_versions(PREPRINT, FakeClient()) == []
    version_only = {'is-version-of': [{'id-type': 'doi', 'id': '10.31219/osf.io/x394e_v1'}]}
    assert linked_versions(PREPRINT, FakeClient(relations=version_only)) == []
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
    assert rows[1]['side'] == 'replication'
    assert all(r['proposed_action'] == 'identifier_correction' and r['action'] == '' for r in rows)
    assert len(client.calls) == 3  # OSF and reverse Crossref, once for both sides
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


def test_reverse_relation_pagination():
    client = FakeClient()
    calls = []
    def get(url, params=None):
        if 'api.openalex.org' in url:
            return {}
        if 'api.osf.io' in url:
            return {}
        calls.append(params['cursor'])
        if len(calls) == 1:
            return {'message': {'items': [{}]*100, 'next-cursor': 'next'}}
        return {'message': {'items': [{'DOI': ARTICLE, 'relation': {'has-preprint': [
            {'id-type': 'doi', 'id': PREPRINT}]}}]}}
    client.get = get
    assert linked_versions(PREPRINT, client)[0]['doi_2'] == ARTICLE
    assert calls == ['*', 'next']


def test_server_coverage_excludes_cshl_journals():
    from published_versions import eligible
    for doi in ['10.17605/osf.io/abcde', '10.31222/osf.io/abcde',
                '10.31234/osf.io/abcde_v2', '10.31235/osf.io/abcde',
                '10.1101/2026.01.01.123456', '10.64898/2026.01.01.123456',
                '10.2139/ssrn.123456', '10.21203/rs.3.rs-12345/v1']:
        assert eligible(doi)
    assert not eligible('10.1101/gr.126516.111')
    assert not eligible('10.1101/lm.035493.114')


def test_openalex_merge_requires_bibliographic_confirmation():
    client = FakeClient()
    original_get = client.get
    def get(url, params=None):
        if 'api.openalex.org' in url:
            return {'doi': 'https://doi.org/' + ARTICLE}
        return original_get(url, params)
    client.get = get
    original_work = client.work
    def work(doi):
        if doi == PREPRINT:
            return {'title': ['A replication study'], 'author': [{'family': 'Smith'}]}
        return {**original_work(doi), 'title': ['A replication study'], 'author': [{'family': 'Smith'}]}
    client.work = work
    assert linked_versions(PREPRINT, client)[0]['confidence'] == 'high'
    client.work = lambda doi: {**work(doi), 'author': [{'family': 'Jones' if doi == ARTICLE else 'Smith'}]}
    result = linked_versions(PREPRINT, client)[0]
    assert result['confidence'] == 'review'
    assert result['evidence'] == 'openalex:merged-doi'


def test_arxiv_datacite_journal_verification():
    from published_versions import datacite_article
    article = datacite_article({'data': {'attributes': {
        'types': {'resourceTypeGeneral': 'JournalArticle'},
        'titles': [{'title': 'Reproduction and Replication of an Adversarial Stylometry Experiment'}],
        'creators': [{'name': 'Wang, Haining'}, {'name': 'Juola, Patrick'}]}}})
    assert article['type'] == 'journal-article'
    assert article['author'][0]['family'] == 'wang'
    assert datacite_article({'data': {'attributes': {'types': {'resourceTypeGeneral': 'Dissertation'}}}}) == {}


def test_all_seven_recent_openalex_merges_are_detected():
    from pathlib import Path
    cases = json.loads((Path(__file__).parent / 'fixtures/published_version_merges.json').read_text())
    assert len(cases) == 7
    for case in cases:
        class RecordedClient:
            def work(self, doi):
                return case['source_crossref'] if doi == case['source'] else case['target_crossref']
            def get(self, url, params=None):
                if 'api.openalex.org' in url:
                    return {'doi': 'https://doi.org/' + case['target']}
                if 'api.datacite.org' in url:
                    return case['target_datacite']
                return {}
        result = linked_versions(case['source'], RecordedClient(), case['title'], case['author'])
        assert len(result) == 1, case['flora_id']
        assert result[0]['doi_2'] == case['target']
        # Renamed citation lists remain review-only until a human verifies them.
        expected = 'review' if case['flora_id'] in {'REPL-001390', 'REPL-001332'} else 'high'
        assert result[0]['confidence'] == expected, case['flora_id']
