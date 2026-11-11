import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
import zenodo_flora as release


def csv_file(path, rows, columns=('id', 'title_r')):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def bundle(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    columns = json.loads((release.SOURCES / 'columns.json').read_text())
    columns += ['author_o', 'author_r', 'author_overlap', 'id_md5']
    row = dict.fromkeys(columns, 'NA')
    row.update(id='A', type='replication', source='replications', title_r='Röseler, "test"\nnext line',
               author_o='[{"family":"Smith"}]', author_r='[{"family":"Jones"}]',
               author_overlap='0', author_overlap_pct='0.0')
    source = csv_file(output / 'flora.csv', [row], columns)
    release.write_json(output / 'flora_release_manifest.json', {
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'data_version': {'change_watermark': 346}})
    target = tmp_path / 'bundle'
    assert release.assemble(output, '2026.11.1', target) == {'added': 1, 'changed': 0, 'retired': 0}
    return target


def test_assembly(bundle):
    release.verify_bundle(bundle)
    assert (bundle / 'flora.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    assert release.read_records(bundle / 'flora.csv')[1]['A']['title_r'] == 'Röseler, "test"\nnext line'
    assert {'CODEBOOK.md', 'FAQ.md', 'README.md', 'REUSE.md', 'CITATION.cff',
            'CHANGELOG.md', 'changes.json'} <= {p.name for p in bundle.iterdir()}
    cff = yaml.safe_load((bundle / 'CITATION.cff').read_text())
    assert cff['version'] == '2026.11.1' and 'doi' not in cff
    metadata = json.loads((bundle / 'zenodo-metadata.json').read_text())
    assert len(metadata['creators']) == len(cff['authors'])
    assert all('email' not in c for c in metadata['creators'])
    manifest = json.loads((bundle / 'flora_release_manifest.json').read_text())
    assert manifest['source_manifest']['data_version']['change_watermark'] == 346
    assert '| FReD | COS | FReD Replication Success input |' in (bundle / 'CODEBOOK.md').read_text()
    with pytest.raises(ValueError, match='exists'):
        release.assemble(bundle, '2026.11.1', bundle)


def test_net_history(tmp_path):
    old = csv_file(tmp_path / 'old.csv', [{'id': 'A', 'title_r': 'before'}, {'id': 'B', 'title_r': 'retire'}])
    new = csv_file(tmp_path / 'new.csv', [{'id': 'A', 'title_r': 'after'}, {'id': 'C', 'title_r': 'added'}])
    changes = release.changelog(new, old)
    assert changes['counts'] == {'added': 1, 'changed': 1, 'retired': 1}
    assert changes['changed'] == {'A': ['title_r']}
    assert changes['retired'] == ['B']
    assert release.changelog(new, new)['counts'] == dict(added=0, changed=0, retired=0)


def test_identity_and_schema(tmp_path):
    bad = csv_file(tmp_path / 'bad.csv', [{'id': 'A', 'title_r': 'one'}, {'id': 'A', 'title_r': 'two'}])
    with pytest.raises(ValueError, match='unique'):
        release.read_records(bad)
    old = csv_file(tmp_path / 'old.csv', [{'id': 'A', 'title_r': 'unchanged'}])
    new = csv_file(tmp_path / 'new.csv', [{'id': 'A', 'extra': 'value'}], ('id', 'extra'))
    changes = release.changelog(new, old)
    assert changes['columns_removed'] == ['title_r']
    assert changes['changed']['A'] == ['title_r', 'extra']


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
    release.assemble(bundle.parent / 'output', '2026.11.2', target, prior)
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
    release.assemble(bundle.parent / 'output', '2026.11.2', tmp_path / 'offline')


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


def test_codebook_matches_snapshot(bundle):
    columns, rows = release.read_records(bundle / 'flora.csv')
    book = (bundle / 'CODEBOOK.md').read_text()
    assert all(f'| `{column}` |' in book for column in columns)
    assert '## Relationship to the existing tables' not in book
    with pytest.raises(ValueError, match='definitions missing'):
        release.render_codebook(['id', 'undocumented_field'], [])
    rendered = release.render_codebook(['id', 'outcome', 'outcome_computation'], [
        {'id': 'A', 'outcome': 'computational issues, robust', 'outcome_computation': 'computational issues'}])
    assert '`computational issues, robust`' in rendered
    assert '`computational issues`' in rendered
    assert '| `author_overlap` |' not in rendered
    assert '| `author_overlap_pct` |' not in rendered


def test_release_projection_order_and_cells(bundle):
    columns, rows = release.read_records(bundle / 'flora.csv')
    assert columns == json.loads((release.SOURCES / 'columns.json').read_text())
    assert len(columns) == 31
    assert 'author_overlap_pct' in columns and 'author_overlap' not in columns
    assert not {'author_o', 'author_r', 'id_md5', 'flora_id'} & set(columns)
    _, original = release.read_records(bundle.parent / 'output' / 'flora.csv')
    assert rows['A']['source'] == 'bulk entry'
    assert all(rows[key][column] == row[column]
               for key, row in original.items() for column in columns if column != 'source')
    import re
    book = (bundle / 'CODEBOOK.md').read_text()
    assert re.findall(r'^\| `([^`]+)` \|', book, re.MULTILINE) == columns
    assert hashlib.sha256((bundle / 'flora.csv').read_bytes()).hexdigest() != hashlib.sha256((bundle.parent / 'output' / 'flora.csv').read_bytes()).hexdigest()


def test_stale_overlap_blocks_release_before_files_are_written(bundle, tmp_path):
    source = bundle.parent / 'output'
    columns, rows = release.read_records(source / 'flora.csv')
    rows['A']['author_o'] = '[{"family":"Smith"},{"name":"The TwiLex Group"}]'
    rows['A']['author_overlap_pct'] = '100.0'
    csv_file(source / 'flora.csv', rows.values(), columns)
    release.write_json(source / 'flora_release_manifest.json', {
        'sha256': hashlib.sha256((source / 'flora.csv').read_bytes()).hexdigest()})
    with pytest.raises(ValueError, match='Stale author_overlap_pct.*A'):
        release.assemble(source, '2026.11.1', tmp_path / 'stale')
    assert not (tmp_path / 'stale').exists()


def test_missing_selected_column_blocks_release(bundle, tmp_path):
    source = bundle.parent / 'output'
    columns, rows = release.read_records(source / 'flora.csv')
    columns.remove('bibtex_ref_o')
    for row in rows.values():
        del row['bibtex_ref_o']
    csv_file(source / 'flora.csv', rows.values(), columns)
    release.write_json(source / 'flora_release_manifest.json', {
        'sha256': hashlib.sha256((source / 'flora.csv').read_bytes()).hexdigest()})
    with pytest.raises(ValueError, match='bibtex_ref_o'):
        release.assemble(source, '2026.11.1', tmp_path / 'missing')


def test_preview_and_superseded_bundle_cannot_publish_production(bundle):
    manifest_path = bundle / 'flora_release_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['source_manifest']['source_kind'] = 'offline_pipeline_preview'
    release.write_json(manifest_path, manifest)
    release.write_json(bundle / 'bundle-checksums.json', release.checksums(bundle))
    with pytest.raises(ValueError, match='Preview bundle'):
        release.upload(bundle, 'production', publish=True, approve_production=True)
    del manifest['projection']
    release.write_json(manifest_path, manifest)
    release.write_json(bundle / 'bundle-checksums.json', release.checksums(bundle))
    with pytest.raises(ValueError, match='agreed release columns'):
        release.upload(bundle, 'production', publish=True, approve_production=True)


@pytest.mark.parametrize('internal,label', [
    ('replications', 'bulk entry'), ('reproductions', 'bulk entry'), ('COS', 'FReD'),
    ('SCORE', 'SCORE'), ('manual', 'search pipeline'), ('validated', 'search pipeline'),
])
def test_public_source_labels_do_not_change_internal_record(internal, label):
    columns = json.loads((release.SOURCES / 'columns.json').read_text())
    row = dict.fromkeys(columns, 'NA')
    row.update(id='TEST', type='replication', source=internal)
    assert release.project_record(row, columns)['source'] == label
    assert row['source'] == internal


@pytest.mark.parametrize('kind', ['replication', 'reproduction'])
def test_release_only_contains_applicable_outcomes(kind):
    columns = json.loads((release.SOURCES / 'columns.json').read_text())
    row = dict.fromkeys(columns, 'NA')
    row.update(id='TEST', type=kind, source='validated')
    for field in (*release.REPLICATION_OUTCOME_FIELDS, *release.REPRODUCTION_OUTCOME_FIELDS):
        row[field] = 'specific evidence or outcome'
    result = release.project_record(row, columns)
    applicable, inapplicable = (release.REPLICATION_OUTCOME_FIELDS, release.REPRODUCTION_OUTCOME_FIELDS) if kind == 'replication' else (release.REPRODUCTION_OUTCOME_FIELDS, release.REPLICATION_OUTCOME_FIELDS)
    assert all(result[field] == row[field] for field in applicable)
    assert all(result[field] == 'NA' for field in inapplicable)
    assert all(row[field] != 'NA' for field in inapplicable)


def test_unregistered_source_is_not_silently_published():
    columns = json.loads((release.SOURCES / 'columns.json').read_text())
    row = dict.fromkeys(columns, 'NA')
    row.update(id='TEST', type='replication', source='new intake')
    with pytest.raises(ValueError, match='Unmapped source'):
        release.project_record(row, columns)
