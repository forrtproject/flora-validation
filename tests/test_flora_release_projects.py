import pytest
from flora_release_projects import report_identifiers, classify_projects


def record(identifier, doi='NA', alt='NA', source='validated', url='NA'):
    return {'id': identifier, 'doi_r': doi, 'alt_identifier_r': alt,
            'source': source, 'url_r': url, 'title_r': 'Report'}


def registry(**overrides):
    project = {'key': 'example', 'label': 'Example project', 'identifiers': ['10.1234/project']}
    project.update(overrides)
    return {'threshold': 50, 'projects': [project]}


def test_report_identifier_normalisation_and_multiple_alternatives():
    row = record('A', 'https://DOI.org/10.1234/PROJECT',
                 'https://doi.org/10.5678/preprint; 10.17605/OSF.IO/ABCDE | https://osf.io/abcde/?view_only=hidden')
    row['doi_o'] = '10.9999/not-a-report'
    assert report_identifiers(row) == {'doi:10.1234/project', 'doi:10.5678/preprint', 'osf:abcde'}
    assert report_identifiers(record('A', '10.1016/0010-0285(72)90003-5')) == {'doi:10.1016/0010-0285(72)90003-5'}


def test_strict_threshold_and_project_overrides_search_pipeline():
    rows = {str(i): record(str(i), '10.1234/project') for i in range(50)}
    assigned, _ = classify_projects(rows, registry())
    assert assigned == {}
    rows['50'] = record('50', '10.9999/individual', '10.1234/project', 'manual')
    assigned, report = classify_projects(rows, registry())
    assert len(assigned) == 51 and assigned['50'] == 'Example project'
    assert report['projects'][0]['record_count'] == 51
    assert rows['50']['source'] == 'manual'


def test_multi_report_project_counted_by_unique_records_not_identifier_occurrences():
    rows = {str(i): record(str(i), '10.1234/a', '10.1234/a, 10.1234/b') for i in range(50)}
    config = registry(identifiers=['10.1234/a', '10.1234/b'])
    assert classify_projects(rows, config)[0] == {}
    rows['50'] = record('50', '10.1234/b')
    assert len(classify_projects(rows, config)[0]) == 51


def test_qualified_project_keeps_label_after_retirement():
    assigned, report = classify_projects({'A': record('A', '10.1234/project')},
                                         registry(qualification={'record_count': 51}))
    assert assigned['A'] == 'Example project'
    assert report['projects'][0]['established']


def test_alias_expansion_is_one_hop_not_transitive():
    rows = {'seed': record('seed', '10.1234/project', '10.1234/alias'),
            'match': record('match', '10.1234/alias', '10.1234/bridge'),
            'unrelated': record('unrelated', '10.1234/bridge')}
    assigned, _ = classify_projects(rows, registry(qualification={'record_count': 51}))
    assert set(assigned) == {'seed', 'match'}


def test_score_intake_can_seed_identifiers_without_report_doi():
    rows = {'seed': record('seed', source='SCORE', url='https://osf.io/abcde'),
            'search': record('search', '10.17605/OSF.IO/ABCDE', source='validated')}
    config = registry(identifiers=[], intake_sources=['SCORE'], qualification={'record_count': 88})
    assigned, _ = classify_projects(rows, config)
    assert set(assigned) == {'seed', 'search'}


def test_unmapped_large_group_flagged_for_review_without_inventing_project_name():
    rows = {str(i): record(str(i), '10.9999/new') for i in range(51)}
    assigned, report = classify_projects(rows, {'threshold': 50, 'projects': []})
    assert assigned == {}
    assert report['review_required']
    assert report['unmapped_large_groups'][0]['identifier'] == 'doi:10.9999/new'
    assert report['unmapped_large_groups'][0]['record_count'] == 51


def test_conflicting_project_matches_stop_instead_of_silently_picking_one():
    rows = {'A': record('A', '10.1234/project', '10.5678/other')}
    config = registry(qualification={'record_count': 51})
    config['projects'].append({'key': 'other', 'label': 'Other',
                              'identifiers': ['10.5678/other'], 'qualification': {'record_count': 51}})
    with pytest.raises(ValueError, match='Ambiguous project membership.*A'):
        classify_projects(rows, config)


def test_title_mentions_do_not_establish_membership():
    rows = {'A': {**record('A', source='validated'), 'title_r': 'A critique of SCORE'}}
    assert classify_projects(rows, registry(intake_sources=['SCORE']))[0] == {}


def test_nested_osf_file_matches_direct_file_without_joining_sibling_files():
    nested = report_identifiers(record('A', url='https://osf.io/8uc5z/files/jmk9d?view_only=x'))
    assert nested == {'osf:jmk9d'}
    assert nested == report_identifiers(record('B', url='https://osf.io/jmk9d/'))
    assert nested != report_identifiers(record('C', url='https://osf.io/8uc5z/files/abcde'))
    assert report_identifiers(record('D', url='https://osf.io/8uc5z/')) == {'osf:8uc5z'}


def test_both_score_paper_dois_match_across_intake_routes():
    from pathlib import Path
    import yaml
    registry = yaml.safe_load((Path(__file__).resolve().parents[1] / 'release/flora/projects.yml').read_text())
    rows = {'rep': record('rep', alt='10.1038/s41586-025-10078-y', source='validated'),
            'repro': record('repro', alt='10.17605/osf.io/w3kuz; 10.1038/s41586-026-10203-5', source='reproductions')}
    assert classify_projects(rows, registry)[0] == {'rep': 'SCORE', 'repro': 'SCORE'}
