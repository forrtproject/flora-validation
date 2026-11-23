"""Assemble a FLoRA release offline, then explicitly upload/publish the reviewed bundle."""
import argparse
import csv
import hashlib
import io
import json
import os
import html
import re
import string
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
import release_helpers
import flora_release_projects

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / 'release/flora'
HOSTS = {'sandbox': 'https://sandbox.zenodo.org', 'production': 'https://zenodo.org'}
CONCEPT_DOI = '10.5281/zenodo.24461022'
FRED_DOI = '10.5281/zenodo.24461606'
FAQ_URL = 'https://github.com/forrtproject/flora-validation/blob/main/release/flora/FAQ.md'
# Deposited on Zenodo. The manifest, project report, record-level changes, metadata and
# checksums stay in the local bundle (and its archive) for the next release.
DEPOSITED = ('flora.csv', 'README.md', 'CODEBOOK.md', 'CHANGELOG.md', 'CITATION.cff')

# Public release labels. Internal routes stay intact for syncing and audit.
SOURCE_LABELS = {
    'replications': 'manual entry', 'reproductions': 'manual entry',
    'COS': 'FReD', 'SCORE': 'SCORE',
    'validated': 'search pipeline', 'manual': 'search pipeline',
}
# Codebook text for each public source label (projects first, then intake routes).
SOURCE_MEANINGS = {
    'RP:P': 'Reproducibility Project: Psychology (report DOI 10.1126/science.aac4716).',
    'Student replication projects (Boyce et al.)': 'Collection of student replication projects (report DOI 10.1098/rsos.231240).',
    'SCORE': "Replications from the SCORE programme of the Center for Open Science.",
    'manual entry': "Entered through FLoRA's manual entry sheets.",
    'FReD': 'Taken from FReD, the FORRT Replication Database.',
    'search pipeline': "Added through FLoRA's website validation workflow (not necessarily found by automated search).",
}
REPLICATION_OUTCOME_FIELDS = ('outcome', 'outcome_quote', 'outcome_quote_source')
REPRODUCTION_OUTCOME_FIELDS = (
    'outcome_computation', 'outcome_computational_quote', 'out_quote_computational_source',
    'outcome_robustness', 'outcome_robustness_quote', 'out_quote_robust_source',
)


def project_record(row, columns, project_label=None):
    """Apply the agreed release labels and outcome applicability, without editing inputs."""
    if row['type'] not in {'replication', 'reproduction'}:
        raise ValueError(f"Unknown attempt type for {row['id']}: {row['type']}")
    if not project_label and row['source'] not in SOURCE_LABELS:
        raise ValueError(f"Unmapped source label: {row['source']}; review the release mapping")
    result = {c: row[c] for c in columns}
    result['source'] = project_label or SOURCE_LABELS[row['source']]
    inapplicable = (REPLICATION_OUTCOME_FIELDS if row['type'] == 'reproduction'
                    else REPRODUCTION_OUTCOME_FIELDS)
    for field in inapplicable:
        result[field] = 'NA'
    return result


def read_records(path):
    """Read a BOM-compatible CSV keyed by validated permanent record IDs."""
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
    """Write release metadata as UTF-8 JSON."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def assemble(output, version, destination, previous=None, replaces=False):
    """Build and checksum an offline release from the authoritative CSV export.

    With replaces=True the previous version is being withdrawn: it stays the Zenodo parent
    of the new version, but its changelog section is dropped and the new one says so.
    """
    if not re.fullmatch(r'\d{4}\.(?:0[1-9]|1[0-2])\.[1-9]\d*', version):
        raise ValueError('Version must be YYYY.MM.N (N starts at 1)')
    output, destination = Path(output), Path(destination)
    if destination.exists():
        raise ValueError('Bundle directory already exists; use it with --upload or choose a new directory')
    previous = Path(previous) if previous else None
    prior = None
    if previous:
        prior = json.loads((previous / 'zenodo-state.json').read_text(encoding='utf-8'))
        if not prior.get('published'):
            raise ValueError('Previous bundle must have a published Zenodo receipt')
        if tuple(map(int, version.split('.'))) <= tuple(map(int, prior['version'].split('.'))):
            raise ValueError('Version must increase')
        verify_bundle(previous)
    source = output / 'flora.csv'
    source_manifest = json.loads((output / 'flora_release_manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_manifest['sha256']:
        raise ValueError('flora.csv does not match its existing release manifest')
    columns = json.loads((SOURCES / 'columns.json').read_text(encoding='utf-8'))
    source_columns, source_rows = read_records(source)
    missing = set(columns) - set(source_columns)
    if missing:
        raise ValueError('Release columns missing from source: ' + ', '.join(sorted(missing)))
    validate_overlap(source_columns, source_rows)
    registry = yaml.safe_load((SOURCES / 'projects.yml').read_text(encoding='utf-8'))
    project_labels, project_report = flora_release_projects.classify_projects(source_rows, registry)
    rows = {identifier: project_record(row, columns, project_labels.get(identifier))
            for identifier, row in source_rows.items()}
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
    stats = record_stats(rows.values())
    entries = version_changes(stats, changes, prior, replaces)
    history = previous_history(previous, replaces) if previous else ''
    (destination / 'CHANGELOG.md').write_text(render_changelog(version, entries, history), encoding='utf-8')
    write_json(destination / 'changes.json', changes)
    write_json(destination / 'project_classification.json', project_report)
    # Existing manifest helper remains the authority for exact published CSV checksums.
    manifest = release_helpers.prepare_release(destination / 'flora.csv', destination, len(rows), columns,
                                             release_notes=summary)
    (destination / 'flora_release_notes.md').unlink()  # legacy OSF-era summary; CHANGELOG.md covers it
    manifest['dataset_version'] = version
    manifest['source_manifest'] = source_manifest
    manifest['projection'] = {'columns': columns, 'source_columns': source_columns,
                              'omitted_columns': [c for c in source_columns if c not in columns],
                              'source_labels': SOURCE_LABELS,
                              'project_registry': registry,
                              'project_review_required': project_report['review_required'],
                              'outcome_applicability': 'replication and reproduction fields are type-specific'}
    manifest['previous_release'] = prior
    manifest['replaces_previous'] = bool(replaces)
    write_json(destination / 'flora_release_manifest.json', manifest)
    cff = yaml.safe_load((SOURCES / 'citation-template.cff').read_text(encoding='utf-8'))
    cff['version'] = version
    (destination / 'CITATION.cff').write_text(yaml.safe_dump(cff, allow_unicode=True, sort_keys=False), encoding='utf-8')
    creators = [{'name': f"{a['family-names']}, {a['given-names']}",
                 **({'orcid': a['orcid']} if a.get('orcid') else {})} for a in cff['authors']]
    metadata = {'title': cff['title'], 'upload_type': 'dataset', 'creators': creators,
                'license': 'cc-by-4.0', 'access_right': 'open', 'version': version,
                'description': description(version, stats, entries),
                'keywords': cff['keywords'], 'related_identifiers': [
                    {'identifier': cff['repository-code'], 'relation': 'isSupplementTo'}]}
    write_json(destination / 'zenodo-metadata.json', metadata)
    readme = string.Template((SOURCES / 'README.md').read_text(encoding='utf-8')).substitute(
        version=version, concept_doi=CONCEPT_DOI, fred_doi=FRED_DOI, faq_url=FAQ_URL,
        **{k: f'{v:,}' for k, v in stats.items() if isinstance(v, int)})
    (destination / 'README.md').write_text(readme, encoding='utf-8')
    (destination / 'CODEBOOK.md').write_text(codebook, encoding='utf-8')
    write_json(destination / 'bundle-checksums.json', checksums(destination))
    return counts


def record_stats(rows):
    """Counts quoted in the README, changelog and Zenodo description."""
    rows = list(rows)
    return {'records': len(rows),
            'replications': sum(r['type'] == 'replication' for r in rows),
            'reproductions': sum(r['type'] == 'reproduction' for r in rows)}


def version_changes(stats, changes, prior, replaces):
    """Changelog bullets for this version, in plain language."""
    entries = [f"{stats['records']:,} records: {stats['replications']:,} replications and "
               f"{stats['reproductions']:,} reproductions."]
    counts = changes['counts']
    if replaces:
        same = not any(counts.values()) and not changes['columns_added'] and not changes['columns_removed']
        entries.append(f"Replaces version {prior['version']}, which was withdrawn because of its packaging "
                       "(internal release files and a minimal description). "
                       + ('The records are identical.' if same else
                          f"Records added: {counts['added']:,}; changed: {counts['changed']:,}; "
                          f"retired: {counts['retired']:,}."))
    elif prior:
        entries.append(f"Since version {prior['version']}: {counts['added']:,} records added, "
                       f"{counts['changed']:,} changed and {counts['retired']:,} retired. Retired records are "
                       "no longer in the data; their ids are not reused.")
        if changes['columns_added'] or changes['columns_removed']:
            entries.append('Columns added: ' + (', '.join(changes['columns_added']) or 'none')
                           + '; removed: ' + (', '.join(changes['columns_removed']) or 'none') + '.')
    else:
        entries.append('First release on Zenodo.')
    return entries


def previous_history(previous, replaces):
    """Version sections of the previous CHANGELOG.md, minus a withdrawn version's own section."""
    path = previous / 'CHANGELOG.md'
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    sections = ['## ' + s for s in text.split('\n## ')[1:]]
    return '\n'.join(sections[1:] if replaces else sections).strip()


def render_changelog(version, entries, history):
    lines = ['# FLoRA changelog', '', 'Newest first. Each version of FLoRA on Zenodo carries the full history '
             'up to that version.', '', f'## {version}', '', *[f'- {e}' for e in entries], '']
    return '\n'.join(lines) + (f'\n{history}\n' if history else '')


def description(version, stats, entries):
    """Zenodo record description (HTML)."""
    e = html.escape
    link = lambda url, text=None: f'<a href="{e(url)}">{e(text or url)}</a>'
    return ''.join([
        '<p>The FORRT Library of Reproduction and Replication Attempts (FLoRA) links published original '
        'studies in the social and behavioural sciences to attempts to replicate them (with new data) or '
        'reproduce them (with the original data). Each row pairs one original study with one replication '
        'or reproduction report and records the outcome as the report\'s authors describe it. A report that '
        'tests several original studies contributes several rows.</p>',
        f"<p>This version has {stats['records']:,} records: {stats['replications']:,} replications and "
        f"{stats['reproductions']:,} reproductions.</p>",
        '<p><strong>Files</strong></p><ul>'
        '<li><code>flora.csv</code>: the data (UTF-8 with BOM; <code>NA</code> marks missing or inapplicable values).</li>'
        '<li><code>CODEBOOK.md</code>: definitions of all columns and the categories used.</li>'
        '<li><code>CHANGELOG.md</code>: changes in this and earlier versions.</li>'
        '<li><code>README.md</code>: how to read and reuse the data.</li>'
        '<li><code>CITATION.cff</code>: how to cite this version.</li></ul>',
        '<p><strong>Changes in this version</strong></p><ul>' + ''.join(f'<li>{e(x)}</li>' for x in entries) + '</ul>',
        '<p>Outcomes are the report authors\' conclusions as coded by the FLoRA team, following the '
        f"{link(FAQ_URL, 'FLoRA coding guidance')}. Rows are pairs of publications, not effect sizes; "
        f"effect-level data are in the companion database FReD ({link('https://doi.org/' + FRED_DOI)}). "
        f"To cite FLoRA regardless of version, use {link('https://doi.org/' + CONCEPT_DOI)}. "
        f"Maintained by FORRT ({link('https://forrt.org/replication-hub/')}) in "
        f"{link('https://github.com/forrtproject/flora-validation')}.</p>",
    ])


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
    intro = (section.split('| Field |', 1)[0].strip()
             .replace('Author arrays and citations', 'References and quoted evidence')
             .replace('See the bundled FAQ', f'See the FLoRA coding guidance ({FAQ_URL})'))
    definitions.update({
        'source': 'Established project first (matched by report DOI, alternative report identifiers or linked report URLs), otherwise intake route. Named projects take precedence even for search-pipeline records. Projects qualify at more than 50 distinct released IDs; existing labels persist after retirements. Not a validation-status field.',
        'outcome': 'Replication outcome; NA for reproduction rows, which use the two dedicated reproduction axes.',
        'outcome_quote': 'Evidence for the replication outcome; NA for reproduction rows.',
        'outcome_quote_source': 'Source/location of the replication evidence; NA for reproduction rows.',
    })
    table = ['| Field | Definition |', '| --- | --- |']
    table += [f'| `{column}` | {definitions[column]} |' for column in columns]
    lines = ['# FLoRA codebook', '', intro, '', *table, '',
             '## Categories present in this release', '',
             'These are the exact nonmissing labels in the deposited CSV, not a promise',
             'that every permitted category occurs. `NA` and empty cells mean missing.',
             'Outcome categories are preserved; source routes are relabelled and inapplicable outcome fields use NA.', '']
    rows = list(rows)
    for field in ('type', 'outcome', 'outcome_computation', 'outcome_robustness', 'source'):
        if field in columns:
            values = sorted({row[field] for row in rows if row[field] not in ('', 'NA')})
            lines += [f"- `{field}`: " + '; '.join(f'`{v}`' for v in values) + '.']
    lines += ['', 'Replication outcomes and evidence appear only on replication rows. Reproduction',
              'outcomes and their evidence appear only on reproduction rows. NA can mean',
              'inapplicable as well as unknown; use type to distinguish these cases.',
              f'The FLoRA coding guidance ({FAQ_URL}) explains coding decisions. Definitions are maintained in',
              'https://github.com/forrtproject/flora-validation/blob/main/docs/FLORA_DATA_TABLE.md', '']
    if 'source' in columns:
        used = sorted({row['source'] for row in rows if row['source'] not in ('', 'NA')})
        unexplained = set(used) - set(SOURCE_MEANINGS)
        if unexplained:
            raise ValueError('Add codebook meanings for source labels: ' + ', '.join(sorted(unexplained)))
        lines += ['## Source labels', '',
                  '`source` names the established project a record belongs to, identified from the report\'s',
                  'identifiers, or otherwise how the record entered FLoRA. It is not a quality level.', '',
                  '| Value | Meaning |', '| --- | --- |',
                  *[f'| {label} | {SOURCE_MEANINGS[label]} |' for label in used], '']
    return '\n'.join(lines)


def checksums(bundle):
    """Hash deposited files, excluding local receipts and the checksum manifest."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.iterdir())
            if p.is_file() and p.name not in {'bundle-checksums.json', 'zenodo-state.json'}}


def verify_bundle(bundle):
    """Reject any bundle whose contents changed after assembly."""
    if checksums(bundle) != json.loads((bundle / 'bundle-checksums.json').read_text(encoding='utf-8')):
        raise ValueError('Bundle changed after assembly; review and assemble again')


def verify_remote_files(bundle, deposit):
    """Require the complete remote file set and MD5s to match the reviewed bundle."""
    remote = {f['filename']: f['checksum'].removeprefix('md5:') for f in deposit.get('files', [])}
    expected = {name: hashlib.md5((bundle / name).read_bytes()).hexdigest() for name in DEPOSITED}
    if remote != expected:
        raise ValueError('Remote files differ from the local bundle; inspect the deposit')


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
    # Only the files a release builds on; Zenodo's MD5s guard the download.
    for file in record['files']:
        name = file['key']
        if name not in DEPOSITED:
            continue
        url = file['links']['self']
        if urlparse(url).scheme != 'https' or urlparse(url).netloc != urlparse(base).netloc:
            raise ValueError('Unexpected file host in public record')
        data = requests.get(url, timeout=180)
        data.raise_for_status()
        if hashlib.md5(data.content).hexdigest() != file['checksum'].removeprefix('md5:'):
            raise ValueError(f'Downloaded {name} does not match its Zenodo checksum')
        (destination / name).write_bytes(data.content)
    if not (destination / 'flora.csv').exists():
        raise ValueError('Public record has no flora.csv')
    write_json(destination / 'bundle-checksums.json', checksums(destination))
    write_json(destination / 'zenodo-state.json', {
        'environment': environment, 'id': record['id'], 'conceptrecid': record['conceptrecid'],
        'version': record['metadata']['version'], 'published': True,
        'doi': record['doi'], 'url': record['links']['html']})


class Zenodo:
    """Environment-scoped REST client that keeps credentials out of logs."""
    def __init__(self, environment):
        """Read the environment-specific token without logging its value."""
        self.base = HOSTS[environment]
        variable = 'ZENODO_SANDBOX_TOKEN' if environment == 'sandbox' else 'ZENODO_TOKEN'
        token = os.environ.get(variable)
        if not token:
            raise ValueError(f'Set {variable} in the environment')
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {token}',
                                     'User-Agent': 'FLoRA-release/1.0 (+https://github.com/forrtproject/flora-validation)'})

    def request(self, method, url, **kwargs):
        """Send an authenticated request only to the selected Zenodo host."""
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


def upload(bundle, environment, publish=False, approve_production=False, resume_deposit=None):
    """Upload a reviewed bundle, resuming only the verified intended draft."""
    bundle = Path(bundle)
    if environment == 'production' and not approve_production:
        raise ValueError('Production requires --approve-production after Lukas approves the release')
    verify_bundle(bundle)
    metadata = json.loads((bundle / 'zenodo-metadata.json').read_text(encoding='utf-8'))
    manifest = json.loads((bundle / 'flora_release_manifest.json').read_text(encoding='utf-8'))
    approved = json.loads((SOURCES / 'columns.json').read_text(encoding='utf-8'))
    if (manifest.get('projection', {}).get('columns') != approved
            or manifest['projection'].get('source_labels') != SOURCE_LABELS
            or manifest['projection'].get('project_registry') != yaml.safe_load((SOURCES / 'projects.yml').read_text(encoding='utf-8'))):
        raise ValueError('Reassemble the bundle with the agreed release columns')
    if environment == 'production' and manifest['projection'].get('project_review_required'):
        raise ValueError('Review unmapped report groups above the project threshold before production')
    if environment == 'production':
        source = manifest['source_manifest']
        revision = source.get('data_version') or {}
        if (source.get('source_kind') != 'authoritative_publication'
                or not revision.get('stored_at')
                or not isinstance(revision.get('change_watermark'), int)
                or revision['change_watermark'] < 0):
            raise ValueError('Unverified or preview source: regenerate an authoritative publication before production')
    previous = manifest.get('previous_release')
    if previous and previous['environment'] != environment:
        raise ValueError('Previous release belongs to another Zenodo environment')
    if resume_deposit is not None and not str(resume_deposit).isdigit():
        raise ValueError('Resume deposit must be a numeric draft ID')
    api = Zenodo(environment)
    state_path = bundle / 'zenodo-state.json'
    has_receipt = state_path.exists()
    if has_receipt or resume_deposit is not None:
        state = (json.loads(state_path.read_text(encoding='utf-8')) if has_receipt else
                 {'environment': environment, 'id': resume_deposit,
                  'version': metadata['version'], 'published': False})
        if state['environment'] != environment:
            raise ValueError('Use a separate bundle for each environment')
        if resume_deposit is not None and str(state['id']) != str(resume_deposit):
            raise ValueError('Resume deposit differs from saved receipt')
        deposit = api.request('GET', f"{api.base}/api/deposit/depositions/{state['id']}")
        if not has_receipt:
            state['conceptrecid'] = deposit['conceptrecid']
        # A new-version draft may carry the previous version or, on current Zenodo, none at all.
        expected_versions = {metadata['version'], previous['version'] if previous else None,
                             None if previous else metadata['version']}
        if (str(deposit['id']) != str(state['id'])
                or str(deposit['conceptrecid']) != str(state.get('conceptrecid'))
                or state.get('version') != metadata['version']
                or (previous and str(deposit['conceptrecid']) != str(previous['conceptrecid']))
                or deposit.get('metadata', {}).get('version') not in expected_versions
                or deposit.get('metadata', {}).get('title', metadata['title']) != metadata['title']
                or (deposit.get('submitted') and deposit.get('metadata', {}).get('version') != metadata['version'])
                or (not has_receipt and not previous and deposit.get('files')
                    and not deposit.get('metadata', {}).get('title')
                    and not deposit.get('metadata', {}).get('version'))
                or (state.get('published') and not deposit.get('submitted'))):
            raise ValueError('Resumed draft identity, concept or version mismatch; inspect the deposit')
        if not has_receipt:
            write_json(state_path, state)  # recovery must persist before any remote mutation
    else:
        if previous:
            latest = api.request('GET', f"{api.base}/api/deposit/depositions/{previous['id']}")
            if (str(latest['conceptrecid']) != str(previous['conceptrecid'])
                    or latest.get('metadata', {}).get('version') != previous['version']
                    or latest.get('metadata', {}).get('title') != metadata['title']):
                raise ValueError('Previous concept record, title or version mismatch')
            record = api.request('POST', f"{api.base}/api/deposit/depositions/{previous['id']}/actions/newversion")
            deposit = api.request('GET', record['links']['latest_draft'])
            if str(deposit['conceptrecid']) != str(previous['conceptrecid']):
                raise ValueError('New draft belongs to another concept')
            if deposit.get('metadata', {}).get('version') not in {previous['version'], metadata['version'], None}:
                raise ValueError('An existing draft has another release version; inspect it before continuing')
        else:
            deposit = api.request('POST', f'{api.base}/api/deposit/depositions', json={})
        state = {'environment': environment, 'id': deposit['id'], 'conceptrecid': deposit['conceptrecid'],
                 'version': metadata['version'], 'published': False}
        write_json(state_path, state)  # persist immediately, before metadata/files
    if deposit.get('submitted'):
        if deposit['metadata'].get('version') != metadata['version']:
            raise ValueError('Published deposit version mismatch')
        verify_remote_files(bundle, deposit)
        state.update(published=True, doi=deposit.get('doi'), url=deposit['links']['html'])
        write_json(state_path, state)
        return state
    metadata['prereserve_doi'] = True
    deposit = api.request('PUT', deposit['links']['self'], json={'metadata': metadata})
    doi = deposit['metadata']['prereserve_doi']['doi']
    cff_path = bundle / 'CITATION.cff'
    cff = yaml.safe_load(cff_path.read_text(encoding='utf-8'))
    cff['doi'] = doi  # Only Zenodo's reserved version DOI, never the concept/OSF DOI.
    cff_path.write_text(yaml.safe_dump(cff, allow_unicode=True, sort_keys=False), encoding='utf-8')
    write_json(bundle / 'bundle-checksums.json', checksums(bundle))
    # A new version inherits files. Remove them all, so retired/stale files cannot leak.
    for file in deposit.get('files', []):
        api.request('DELETE', f"{api.base}/api/deposit/depositions/{deposit['id']}/files/{file['id']}")
    for name in DEPOSITED:
        with (bundle / name).open('rb') as f:
            result = api.request('PUT', deposit['links']['bucket'] + '/' + name, data=f)
        expected = hashlib.md5((bundle / name).read_bytes()).hexdigest()
        if result['checksum'].removeprefix('md5:') != expected:
            raise ValueError(f'Upload checksum mismatch: {name}')
    if publish:
        verify_remote_files(bundle, api.request('GET', f"{api.base}/api/deposit/depositions/{deposit['id']}"))
        deposit = api.request('POST', deposit['links']['publish'])
        if not deposit.get('submitted'):
            raise ValueError('Zenodo did not confirm publication; inspect the deposit before retrying')
    state.update(published=bool(deposit.get('submitted')), doi=doi, url=deposit['links']['html'])
    write_json(state_path, state)
    return state


def main():
    """Parse release assembly, recovery and explicitly approved upload commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('output'))
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--version')
    parser.add_argument('--previous', type=Path, help='Previous published bundle, including zenodo-state.json')
    parser.add_argument('--replaces', action='store_true',
                        help='The previous version is being withdrawn; drop its changelog section')
    parser.add_argument('--fetch-previous', help='Download a published version record as --bundle')
    parser.add_argument('--environment', choices=HOSTS, default='production')
    parser.add_argument('--upload', choices=HOSTS)
    parser.add_argument('--resume-deposit', help='Recover an inspected draft ID after losing its local receipt')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--approve-production', action='store_true')
    args = parser.parse_args()
    try:
        if args.fetch_previous:
            fetch_previous(args.fetch_previous, args.environment, args.bundle)
        elif args.upload:
            print(json.dumps(upload(args.bundle, args.upload, args.publish, args.approve_production,
                                    args.resume_deposit)))
        else:
            if args.replaces and not args.previous:
                parser.error('--replaces needs --previous')
            if args.publish or args.approve_production or args.resume_deposit or not args.version:
                parser.error('Offline assembly needs --version; publishing needs --upload')
            print(json.dumps(assemble(args.output_dir, args.version, args.bundle, args.previous, args.replaces)))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
