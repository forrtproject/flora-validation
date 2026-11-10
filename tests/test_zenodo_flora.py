import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
import zenodo_flora as release


def csv_file(path, rows, columns=('id', 'title')):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def bundle(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    source = csv_file(output / 'flora.csv', [{'id': 'A', 'title': 'Röseler, "test"\nnext line'}])
    release.write_json(output / 'flora_release_manifest.json', {
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'data_version': {'change_watermark': 346}})
    target = tmp_path / 'bundle'
    assert release.assemble(output, '2026.11.1', target) == {'added': 1, 'changed': 0, 'retired': 0}
    return target


def test_assembly(bundle):
    release.verify_bundle(bundle)
    assert (bundle / 'flora.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    assert release.read_records(bundle / 'flora.csv')[1]['A']['title'] == 'Röseler, "test"\nnext line'
    assert {'CODEBOOK.md', 'FAQ.md', 'README.md', 'REUSE.md', 'CITATION.cff',
            'CHANGELOG.md', 'changes.json'} <= {p.name for p in bundle.iterdir()}
    cff = yaml.safe_load((bundle / 'CITATION.cff').read_text())
    assert cff['version'] == '2026.11.1' and 'doi' not in cff
    metadata = json.loads((bundle / 'zenodo-metadata.json').read_text())
    assert len(metadata['creators']) == len(cff['authors'])
    assert all('email' not in c for c in metadata['creators'])
    manifest = json.loads((bundle / 'flora_release_manifest.json').read_text())
    assert manifest['source_manifest']['data_version']['change_watermark'] == 346
    with pytest.raises(ValueError, match='exists'):
        release.assemble(bundle, '2026.11.1', bundle)


def test_net_history(tmp_path):
    old = csv_file(tmp_path / 'old.csv', [{'id': 'A', 'title': 'before'}, {'id': 'B', 'title': 'retire'}])
    new = csv_file(tmp_path / 'new.csv', [{'id': 'A', 'title': 'after'}, {'id': 'C', 'title': 'added'}])
    changes = release.changelog(new, old)
    assert changes['counts'] == {'added': 1, 'changed': 1, 'retired': 1}
    assert changes['changed'] == {'A': ['title']}
    assert changes['retired'] == ['B']
    assert release.changelog(new, new)['counts'] == dict(added=0, changed=0, retired=0)


def test_identity_and_schema(tmp_path):
    bad = csv_file(tmp_path / 'bad.csv', [{'id': 'A', 'title': 'one'}, {'id': 'A', 'title': 'two'}])
    with pytest.raises(ValueError, match='unique'):
        release.read_records(bad)
    old = csv_file(tmp_path / 'old.csv', [{'id': 'A', 'title': 'unchanged'}])
    new = csv_file(tmp_path / 'new.csv', [{'id': 'A', 'extra': 'value'}], ('id', 'extra'))
    changes = release.changelog(new, old)
    assert changes['columns_removed'] == ['title']
    assert changes['changed']['A'] == ['title', 'extra']


def test_guards(bundle, monkeypatch):
    with pytest.raises(ValueError, match='approve-production'):
        release.upload(bundle, 'production', True)
    monkeypatch.delenv('ZENODO_SANDBOX_TOKEN', raising=False)
    with pytest.raises(ValueError, match='ZENODO_SANDBOX_TOKEN'):
        release.upload(bundle, 'sandbox')
    (bundle / 'flora.csv').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        release.verify_bundle(bundle)


def test_manifest_mismatch(bundle, tmp_path):
    (bundle / 'flora.csv').write_text('bad')
    with pytest.raises(ValueError, match='manifest'):
        release.assemble(bundle, '2026.11.2', tmp_path / 'new')


def test_upload_and_retry(bundle, monkeypatch):
    base = release.HOSTS['sandbox']
    deposit = {'id': 12, 'conceptrecid': 10, 'submitted': False, 'files': [{'id': 'inherited'}],
               'metadata': {'prereserve_doi': {'doi': 'reserved-by-api'}},
               'links': {k: f'{base}/{k}' for k in ('self', 'bucket', 'publish', 'html')}}
    calls = []
    def request(method, url, **kw):
        calls.append((method, url))
        if '/bucket/' in url:
            return {'checksum': 'md5:' + hashlib.md5(kw['data'].read()).hexdigest()}
        return deposit
    api = Mock(base=base)
    api.request.side_effect = request
    monkeypatch.setattr(release, 'Zenodo', lambda env: api)
    state = release.upload(bundle, 'sandbox')
    assert not state['published']
    assert ('DELETE', f'{base}/api/deposit/depositions/12/files/inherited') in calls
    assert not any(url.endswith('/publish') for _, url in calls)
    assert yaml.safe_load((bundle / 'CITATION.cff').read_text())['doi'] == 'reserved-by-api'
    release.verify_bundle(bundle)
    calls.clear()
    release.upload(bundle, 'sandbox')
    assert calls[0] == ('GET', f'{base}/api/deposit/depositions/12')
    assert not any(m == 'POST' for m, _ in calls)


def test_new_version(bundle, tmp_path, monkeypatch):
    prior = tmp_path / 'prior'
    import shutil
    shutil.copytree(bundle, prior)
    receipt = dict(environment='sandbox', id=11, conceptrecid=10, version='2026.10.1', published=True)
    release.write_json(prior / 'zenodo-state.json', receipt)
    target = tmp_path / 'next'
    release.assemble(bundle, '2026.11.2', target, prior)
    base = release.HOSTS['sandbox']
    draft = {'id': 12, 'conceptrecid': 10, 'submitted': False, 'files': [],
             'metadata': {'version': '2026.10.1', 'prereserve_doi': {'doi': 'api-reserved-next'}},
             'links': {k: f'{base}/{k}' for k in ('self', 'bucket', 'publish', 'html')}}
    api = Mock(base=base)
    api.request.side_effect = [dict(conceptrecid=10, metadata={'version': '2026.10.1'}), {'links': {'latest_draft': f'{base}/draft'}}, draft, draft]
    monkeypatch.setattr(release, 'Zenodo', lambda env: api)
    # Interrupt at first file upload; state must already be recoverable.
    with pytest.raises(StopIteration):
        release.upload(target, 'sandbox')
    assert api.request.call_args_list[1].args == ('POST', f'{base}/api/deposit/depositions/11/actions/newversion')
    assert json.loads((target / 'zenodo-state.json').read_text())['id'] == 12


def test_client_rejects_foreign_host_and_hides_errors(monkeypatch):
    monkeypatch.setenv('ZENODO_SANDBOX_TOKEN', 'test-secret-value')
    api = release.Zenodo('sandbox')
    api.session.request = Mock()
    with pytest.raises(ValueError, match='unexpected host'):
        api.request('PUT', 'https://example.com/upload')
    api.session.request.assert_not_called()
    response = Mock(status_code=401, content=b'test-secret-value')
    api.session.request.return_value = response
    with pytest.raises(ValueError) as exc:
        api.request('POST', api.base + '/api/deposit/depositions')
    assert 'test-secret-value' not in str(exc.value)
    assert 'HTTP 401' in str(exc.value)
    assert api.session.request.call_args.kwargs['allow_redirects'] is False


def test_no_network_for_dry_run(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(release.requests, 'Session', Mock(side_effect=AssertionError('Network access')))
    release.assemble(bundle, '2026.11.2', tmp_path / 'offline')


def test_publish_only_after_checksums(bundle, monkeypatch):
    base = release.HOSTS['sandbox']
    links = {k: f'{base}/{k}' for k in ('self', 'bucket', 'publish', 'html')}
    deposit = {'id': 12, 'conceptrecid': 10, 'submitted': False, 'files': [],
               'metadata': {'version': '2026.11.1', 'prereserve_doi': {'doi': 'api-reserved'}}, 'links': links}
    calls = []
    def request(method, url, **kw):
        calls.append((method, url))
        if '/bucket/' in url:
            return {'checksum': hashlib.md5(kw['data'].read()).hexdigest()}
        if url == links['publish']:
            return {**deposit, 'submitted': True}
        return deposit
    api = Mock(base=base)
    api.request.side_effect = request
    monkeypatch.setattr(release, 'Zenodo', lambda env: api)
    assert release.upload(bundle, 'sandbox', publish=True)['published']
    assert calls[-1] == ('POST', links['publish'])
    assert len([url for _, url in calls if '/bucket/' in url]) == 12
