"""Project-first release labels from shared report identifiers and explicit project seeds."""
from collections import Counter, defaultdict
import re
from urllib.parse import unquote

DOI = re.compile(r'10\.\d{4,9}/[^\s,;|<>"\]]+', re.I)
OSF_URL = re.compile(r'(?:https?://)?(?:www\.)?osf\.io/([a-z0-9]{5})(?=[/?#\s,;|<>"\]]|$)', re.I)
OSF_DOI = re.compile(r'10\.17605/osf\.io/([a-z0-9]{5})$', re.I)


def report_identifiers(row):
    """Use the report side only. Preserve DOI punctuation except enclosing syntax."""
    identifiers = set()
    for field in ('doi_r', 'alt_identifier_r', 'url_r'):
        text = unquote(str(row.get(field) or ''))
        for match in DOI.finditer(text):
            doi = match.group().lower().split('?')[0].split('#')[0].rstrip('.,;:')
            while doi.endswith(')') and doi.count(')') > doi.count('('):
                doi = doi[:-1]
            osf = OSF_DOI.fullmatch(doi)
            identifiers.add('osf:' + osf[1].lower() if osf else 'doi:' + doi)
        # A DOI's embedded osf.io path must not create a second OSF identifier;
        # OSF preprint DOIs remain separate identifiers unless explicitly linked.
        without_dois = DOI.sub(' ', text)
        identifiers.update('osf:' + m[1].lower() for m in OSF_URL.finditer(without_dois))
    return identifiers


def classify_projects(records, registry):
    """Explicit anchors seed one-hop identifier matches. Never merge transitively.

    The strict size threshold qualifies a project, not individual membership.
    Registry qualification evidence keeps established labels stable after retirement.
    Unknown large groups are reported for review; no project names are invented.
    """
    threshold = registry['threshold']
    if not isinstance(threshold, int) or threshold < 1:
        raise ValueError('Project threshold must be a positive integer')
    projects = registry['projects']
    keys = [p['key'] for p in projects]
    if len(set(keys)) != len(keys) or len({p['label'] for p in projects}) != len(keys):
        raise ValueError('Project keys and labels must be unique')
    ids = {identifier: report_identifiers(row) for identifier, row in records.items()}
    index = defaultdict(set)
    for identifier, values in ids.items():
        for value in values:
            index[value].add(identifier)
    matches, summaries = {}, []
    for project in projects:
        anchors = set()
        for value in project.get('identifiers', []):
            normal = report_identifiers({'doi_r': value})
            if len(normal) != 1:
                raise ValueError(f"Invalid project identifier: {value}")
            anchors.update(normal)
        explicit = set(project.get('record_ids', [])) & records.keys()
        routes = set(project.get('intake_sources', []))
        seeds = {identifier for identifier, row in records.items()
                 if identifier in explicit or ids[identifier] & anchors or row['source'] in routes}
        # Only explicitly anchored/intake rows seed aliases. A new match cannot
        # propagate another identifier and silently join a chain of projects.
        aliases = anchors | set().union(*(ids[i] for i in seeds)) if seeds else anchors
        members = seeds | {identifier for value in aliases for identifier in index[value]}
        qualification = project.get('qualification') or {}
        established = qualification.get('record_count', 0) > threshold
        eligible = len(members) > threshold or established
        matches[project['key']] = members if eligible else set()
        summaries.append({'key': project['key'], 'label': project['label'],
                          'seed_count': len(seeds), 'record_count': len(members),
                          'eligible': eligible, 'established': established,
                          'record_ids': sorted(members), 'seed_ids': sorted(seeds),
                          'identifiers': sorted(aliases)})
    by_record = defaultdict(list)
    for key, members in matches.items():
        for identifier in members:
            by_record[identifier].append(key)
    conflicts = {identifier: keys for identifier, keys in by_record.items() if len(keys) > 1}
    if conflicts:
        raise ValueError('Ambiguous project membership; review mapping: ' +
                         '; '.join(f'{i}: {", ".join(keys)}' for i, keys in sorted(conflicts.items())))
    labels = {p['key']: p['label'] for p in projects}
    assigned = {identifier: labels[keys[0]] for identifier, keys in by_record.items()}
    candidates = []
    for value, members in sorted(index.items()):
        if len(members) > threshold and any(identifier not in assigned for identifier in members):
            candidates.append({'identifier': value, 'record_count': len(members),
                               'record_ids': sorted(members),
                               'example_title': records[min(members)].get('title_r')})
    mentions = []
    for identifier, row in records.items():
        if identifier in assigned:
            continue
        text = str(row.get('title_r', '')) + ' ' + str(row.get('apa_ref_r', ''))
        possible = [p['key'] for p in projects
                    if re.search(r'(?<!\w)' + re.escape(p['label']) + r'(?!\w)', text)]
        if possible:
            mentions.append({'id': identifier, 'possible_projects': possible,
                             'current_intake': row['source']})
    return assigned, {'threshold': threshold, 'unit': 'distinct active released record IDs',
                      'precedence': 'established project, then intake route; no search-pipeline protection',
                      'projects': summaries, 'unmapped_large_groups': candidates,
                      'review_required': bool(candidates),
                      'unmatched_project_mentions': mentions,
                      'assigned_counts': dict(Counter(assigned.values()))}
