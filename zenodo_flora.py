"""Assemble a FLoRA release offline, then explicitly upload/publish the reviewed bundle."""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
import release_helpers

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / 'release/flora'
HOSTS = {'sandbox': 'https://sandbox.zenodo.org', 'production': 'https://zenodo.org'}


def read_records(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        if not columns or 'id' not in columns or len(columns) != len(set(columns)):
            raise ValueError('CSV needs unique headers and a permanent id column')
        rows = list(reader)
    ids = [r['id'] for r in rows]
    if any(not i or i != i.strip() for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('CSV needs unique nonblank permanent IDs')
    if any(None in r or None in r.values() for r in rows):
        raise ValueError('Malformed CSV row')
    return columns, {r['id']: r for r in rows}


def changelog(current, previous=None):
    """Net published record history, keyed on permanent IDs, never DOI/content keys."""
    columns, new = read_records(current)
    old_columns, old = read_records(previous) if previous else ([], {})
    added = sorted(new.keys() - old.keys())
    retired = sorted(old.keys() - new.keys())
    changed = {i: [c for c in dict.fromkeys(old_columns + columns)
                   if old[i].get(c) != new[i].get(c)]
               for i in sorted(new.keys() & old.keys()) if old[i] != new[i]}
    return {'added': added, 'changed': changed, 'retired': retired,
            'counts': {'added': len(added), 'changed': len(changed), 'retired': len(retired)},
            'columns_added': [c for c in columns if c not in old_columns],
            'columns_removed': [c for c in old_columns if c not in columns]}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def assemble(output, version, destination, previous=None):
    if not re.fullmatch(r'\d{4}\.(?:0[1-9]|1[0-2])\.[1-9]\d*', version):
        raise ValueError('Version must be YYYY.MM.N (N starts at 1)')
    output, destination = Path(output), Path(destination)
    if destination.exists():
        raise ValueError('Bundle directory already exists; use it with --upload or choose a new directory')
    previous = Path(previous) if previous else None
    prior = None
    if previous:
        prior = json.loads((previous / 'zenodo-state.json').read_text())
        if not prior.get('published'):
            raise ValueError('Previous bundle must have a published Zenodo receipt')
        if tuple(map(int, version.split('.'))) <= tuple(map(int, prior['version'].split('.'))):
            raise ValueError('Version must increase')
        verify_bundle(previous)
    source = output / 'flora.csv'
    source_manifest = json.loads((output / 'flora_release_manifest.json').read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_manifest['sha256']:
        raise ValueError('flora.csv does not match its existing release manifest')
    columns = json.loads((SOURCES / 'columns.json').read_text())
    source_columns, source_rows = read_records(source)
    missing = set(columns) - set(source_columns)
    if missing:
        raise ValueError('Release columns missing from source: ' + ', '.join(sorted(missing)))
    validate_overlap(source_columns, source_rows)
    rows = {identifier: {c: row[c] for c in columns} for identifier, row in source_rows.items()}
    # Validate the dictionary before creating a partial bundle.
    codebook = render_codebook(columns, rows.values())
    destination.mkdir(parents=True)
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=columns, quoting=csv.QUOTE_ALL, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows.values())
    (destination / 'flora.csv').write_text(buffer.getvalue(), encoding='utf-8-sig', newline='')
    changes = changelog(destination / 'flora.csv', previous / 'flora.csv' if previous else None)
    counts = changes['counts']
    summary = (f"Added: {counts['added']:,}; changed: {counts['changed']:,}; retired: {counts['retired']:,}. "
               f"Current records: {len(rows):,}.")
    notes = f'# FLoRA {version}\n\n{summary}\n\n'
    notes += ('First Zenodo release: all current records are additions to this release baseline.\n'
              'These counts do not reconstruct additions or retirements before this baseline.\n' if not previous else
              f"Net changes since published version {prior['version']}; intermediate edits that cancel out are excluded.\n")
    notes += '\nRecord IDs and changed field names are in changes.json. Retired means absent from this snapshot, not deleted from the registry.\n'
    (destination / 'CHANGELOG.md').write_text(notes, encoding='utf-8')
    write_json(destination / 'changes.json', changes)
    # Existing manifest helper remains the authority for exact published CSV checksums.
    manifest = release_helpers.prepare_release(destination / 'flora.csv', destination, len(rows), columns,
                                             release_notes=summary)
    manifest['dataset_version'] = version
    manifest['source_manifest'] = source_manifest
    manifest['projection'] = {'columns': columns, 'source_columns': source_columns,
                              'omitted_columns': [c for c in source_columns if c not in columns]}
    manifest['previous_release'] = prior
    write_json(destination / 'flora_release_manifest.json', manifest)
    cff = yaml.safe_load((SOURCES / 'citation-template.cff').read_text())
    cff['version'] = version
    (destination / 'CITATION.cff').write_text(yaml.safe_dump(cff, allow_unicode=True, sort_keys=False), encoding='utf-8')
    creators = [{'name': f"{a['family-names']}, {a['given-names']}",
                 **({'orcid': a['orcid']} if a.get('orcid') else {})} for a in cff['authors']]
    metadata = {'title': cff['title'], 'upload_type': 'dataset', 'creators': creators,
                'license': 'cc-by-4.0', 'access_right': 'open', 'version': version,
                'description': cff['abstract'] + ' Each row links an original study to a replication or reproduction attempt. '
                + summary + ' Includes coding guidance, record-level changelog and reuse guidance.',
                'keywords': cff['keywords'], 'related_identifiers': [
                    {'identifier': cff['repository-code'], 'relation': 'isSupplementTo'}]}
    write_json(destination / 'zenodo-metadata.json', metadata)
    for filename in ('README.md', 'REUSE.md', 'FAQ.md'):
        shutil.copyfile(SOURCES / filename, destination / filename)
    (destination / 'CODEBOOK.md').write_text(codebook, encoding='utf-8')
    write_json(destination / 'bundle-checksums.json', checksums(destination))
    return counts


def validate_overlap(columns, rows):
    """Never ship stale derived percentages or silently correct an existing export."""
    if not {'author_o', 'author_r'} <= set(columns):
        raise ValueError('Author metadata is needed to verify author_overlap_pct before projection')
    from author_overlap import count
    mismatches = []
    for identifier, row in rows.items():
        _, expected = count(row['author_o'], row['author_r'])
        raw = row['author_overlap_pct']
        try:
            actual = None if raw in ('', 'NA') else float(raw)
        except ValueError:
            actual = float('nan')
        if actual != expected:
            mismatches.append(identifier)
    if mismatches:
        raise ValueError(f"Stale author_overlap_pct in {len(mismatches)} records: "
                         + ', '.join(mismatches[:10])
                         + '. Regenerate the authoritative publication through the fixed pipeline; do not edit outputs.')


def render_codebook(columns, rows):
    """Reuse maintained definitions; document the exact snapshot's categorical labels."""
    guide = (ROOT / 'docs/FLORA_DATA_TABLE.md').read_text(encoding='utf-8')
    section = guide.split('## Published CSV codebook\n', 1)[1].split('## Relationship to the existing tables', 1)[0]
    definitions = dict(re.findall(r'^\| `([^`]+)` \| (.+?) \|$', section, re.MULTILINE))
    missing = set(columns) - set(definitions)
    if missing:
        raise ValueError('Codebook definitions missing: ' + ', '.join(sorted(missing)))
    intro = section.split('| Field |', 1)[0].strip().replace('Author arrays and citations', 'References and quoted evidence')
    table = ['| Field | Definition |', '| --- | --- |']
    table += [f'| `{column}` | {definitions[column]} |' for column in columns]
    lines = ['# FLoRA codebook', '', intro, '', *table, '',
             '## Categories present in this release', '',
             'These are the exact nonmissing labels in the deposited CSV, not a promise',
             'that every permitted category occurs. `NA` and empty cells mean missing.',
             'Earlier snapshots can retain legacy labels; release assembly does not recode data.', '']
    rows = list(rows)
    for field in ('type', 'outcome', 'outcome_computation', 'outcome_robustness', 'source'):
        if field in columns:
            values = sorted({row[field] for row in rows if row[field] not in ('', 'NA')})
            lines += [f"- `{field}`: " + '; '.join(f'`{v}`' for v in values) + '.']
    lines += ['', 'The axes are the detailed reproduction codes. Combined `outcome` labels',
              'preserve the released snapshot’s representation; consult both axes for analysis.',
              'The bundled FAQ explains coding decisions. Definitions are maintained in',
              'https://github.com/forrtproject/flora-validation/blob/main/docs/FLORA_DATA_TABLE.md', '']
    if 'source' in columns and '### Source labels' in section:
        labels = section.split('### Source labels', 1)[1].split('### Author-overlap release validation', 1)[0]
        lines += ['## Source labels', '', labels.strip(), '']
    return '\n'.join(lines)


def checksums(bundle):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.iterdir())
            if p.is_file() and p.name not in {'bundle-checksums.json', 'zenodo-state.json'}}


def verify_bundle(bundle):
    if checksums(bundle) != json.loads((bundle / 'bundle-checksums.json').read_text()):
        raise ValueError('Bundle changed after assembly; review and assemble again')


def fetch_previous(record_id, environment, destination):
    """Recover the public release baseline and receipt without a token."""
    if not str(record_id).isdigit():
        raise ValueError('Previous record must be a numeric version record ID')
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Previous directory already exists')
    base = HOSTS[environment]
    response = requests.get(f'{base}/api/records/{record_id}', timeout=60)
    response.raise_for_status()
    record = response.json()
    destination.mkdir(parents=True)
    for file in record['files']:
        name = file['key']
        url = file['links']['self']
        if Path(name).name != name or urlparse(url).netloc != urlparse(base).netloc:
            raise ValueError('Unexpected file name or host in public record')
        data = requests.get(url, timeout=180)
        data.raise_for_status()
        (destination / name).write_bytes(data.content)
    verify_bundle(destination)
    write_json(destination / 'zenodo-state.json', {
        'environment': environment, 'id': record['id'], 'conceptrecid': record['conceptrecid'],
        'version': record['metadata']['version'], 'published': True,
        'doi': record['doi'], 'url': record['links']['html']})


class Zenodo:
    def __init__(self, environment):
        self.base = HOSTS[environment]
        variable = 'ZENODO_SANDBOX_TOKEN' if environment == 'sandbox' else 'ZENODO_TOKEN'
        token = os.environ.get(variable)
        if not token:
            raise ValueError(f'Set {variable} in the environment')
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {token}',
                                     'User-Agent': 'FLoRA-release/1.0 (+https://github.com/forrtproject/flora-validation)'})

    def request(self, method, url, **kwargs):
        if urlparse(url).netloc != urlparse(self.base).netloc or urlparse(url).scheme != 'https':
            raise ValueError('Refusing credentials to an unexpected host')
        try:
            response = self.session.request(method, url, timeout=180, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ValueError('Zenodo request failed; retry using the same bundle and saved state') from None
        if not 200 <= response.status_code < 300:
            # Never expose request headers, response bodies or exception URLs.
            raise ValueError(f'Zenodo {method} failed (HTTP {response.status_code}); inspect the deposit before retrying')
        return response.json() if response.content else None


def upload(bundle, environment, publish=False, approve_production=False):
    bundle = Path(bundle)
    if environment == 'production' and not approve_production:
        raise ValueError('Production requires --approve-production after Lukas approves the release')
    verify_bundle(bundle)
    metadata = json.loads((bundle / 'zenodo-metadata.json').read_text())
    manifest = json.loads((bundle / 'flora_release_manifest.json').read_text())
    approved = json.loads((SOURCES / 'columns.json').read_text())
    if manifest.get('projection', {}).get('columns') != approved:
        raise ValueError('Reassemble the bundle with the agreed release columns')
    if environment == 'production' and manifest['source_manifest'].get('source_kind') == 'offline_pipeline_preview':
        raise ValueError('Preview bundle: regenerate the authoritative publication with the fixed pipeline before production')
    previous = manifest.get('previous_release')
    if previous and previous['environment'] != environment:
        raise ValueError('Previous release belongs to another Zenodo environment')
    api = Zenodo(environment)
    state_path = bundle / 'zenodo-state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state['environment'] != environment:
            raise ValueError('Use a separate bundle for each environment')
        deposit = api.request('GET', f"{api.base}/api/deposit/depositions/{state['id']}")
    else:
        if previous:
            latest = api.request('GET', f"{api.base}/api/deposit/depositions/{previous['id']}")
            if (str(latest['conceptrecid']) != str(previous['conceptrecid'])
                    or latest.get('metadata', {}).get('version') != previous['version']):
                raise ValueError('Previous concept record or version mismatch')
            record = api.request('POST', f"{api.base}/api/deposit/depositions/{previous['id']}/actions/newversion")
            deposit = api.request('GET', record['links']['latest_draft'])
            if str(deposit['conceptrecid']) != str(previous['conceptrecid']):
                raise ValueError('New draft belongs to another concept')
            if deposit.get('metadata', {}).get('version') not in {previous['version'], metadata['version']}:
                raise ValueError('An existing draft has another release version; inspect it before continuing')
        else:
            deposit = api.request('POST', f'{api.base}/api/deposit/depositions', json={})
        state = {'environment': environment, 'id': deposit['id'], 'conceptrecid': deposit['conceptrecid'],
                 'version': metadata['version'], 'published': False}
        write_json(state_path, state)  # persist immediately, before metadata/files
    if deposit.get('submitted'):
        if deposit['metadata'].get('version') != metadata['version']:
            raise ValueError('Published deposit version mismatch')
        remote = {f['filename']: f['checksum'].removeprefix('md5:') for f in deposit.get('files', [])}
        expected = {name: hashlib.md5((bundle / name).read_bytes()).hexdigest()
                    for name in [*checksums(bundle), 'bundle-checksums.json']}
        if remote != expected:
            raise ValueError('Published files differ from the local bundle; inspect the deposit')
        state.update(published=True, doi=deposit.get('doi'), url=deposit['links']['html'])
        write_json(state_path, state)
        return state
    metadata['prereserve_doi'] = True
    deposit = api.request('PUT', deposit['links']['self'], json={'metadata': metadata})
    doi = deposit['metadata']['prereserve_doi']['doi']
    cff_path = bundle / 'CITATION.cff'
    cff = yaml.safe_load(cff_path.read_text())
    cff['doi'] = doi  # Only Zenodo's reserved version DOI, never the concept/OSF DOI.
    cff_path.write_text(yaml.safe_dump(cff, allow_unicode=True, sort_keys=False), encoding='utf-8')
    write_json(bundle / 'bundle-checksums.json', checksums(bundle))
    # A new version inherits files. Remove them all, so retired/stale files cannot leak.
    for file in deposit.get('files', []):
        api.request('DELETE', f"{api.base}/api/deposit/depositions/{deposit['id']}/files/{file['id']}")
    for name in [*checksums(bundle), 'bundle-checksums.json']:
        with (bundle / name).open('rb') as f:
            result = api.request('PUT', deposit['links']['bucket'] + '/' + name, data=f)
        expected = hashlib.md5((bundle / name).read_bytes()).hexdigest()
        if result['checksum'].removeprefix('md5:') != expected:
            raise ValueError(f'Upload checksum mismatch: {name}')
    if publish:
        deposit = api.request('POST', deposit['links']['publish'])
        if not deposit.get('submitted'):
            raise ValueError('Zenodo did not confirm publication; inspect the deposit before retrying')
    state.update(published=bool(deposit.get('submitted')), doi=doi, url=deposit['links']['html'])
    write_json(state_path, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('output'))
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--version')
    parser.add_argument('--previous', type=Path, help='Previous published bundle, including zenodo-state.json')
    parser.add_argument('--fetch-previous', help='Download a published version record as --bundle')
    parser.add_argument('--environment', choices=HOSTS, default='production')
    parser.add_argument('--upload', choices=HOSTS)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--approve-production', action='store_true')
    args = parser.parse_args()
    try:
        if args.fetch_previous:
            fetch_previous(args.fetch_previous, args.environment, args.bundle)
        elif args.upload:
            print(json.dumps(upload(args.bundle, args.upload, args.publish, args.approve_production)))
        else:
            if args.publish or args.approve_production or not args.version:
                parser.error('Offline assembly needs --version; publishing needs --upload')
            print(json.dumps(assemble(args.output_dir, args.version, args.bundle, args.previous)))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
