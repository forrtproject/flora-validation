from unittest.mock import Mock

import pandas as pd
import pytest

import preprint_dedup as dedup
from resolve_publication_versions import ACTOR, automatic_actions
from published_versions import scan
from tests.test_published_versions import FakeClient, PREPRINT, ARTICLE


def frame(publisher=False, different_type=False, distinct=False):
    """Build a minimal version pair with optional type and human-ruling differences."""
    rows = [{'id': 'preprint-id', 'doi_o': '10.9999/original', 'doi_r': PREPRINT,
             'title_r': 'Published paper', 'author_r': 'Sam Smith', 'type': 'replication',
             'alt_identifier_r': '', 'duplicate_status': 'distinct' if distinct else ''}]
    if publisher:
        rows.append({**rows[0], 'id': 'journal-id', 'doi_r': ARTICLE, 'duplicate_status': '',
                     'type': 'reproduction' if different_type else 'replication'})
    return pd.DataFrame(rows)


def candidate(f):
    """Discover an explicit OSF publication link using synthetic registered metadata."""
    return scan(f, FakeClient(article_doi=ARTICLE))


def test_explicit_link_matching_title_and_author_is_automatic():
    """A verified standalone preprint becomes a logged identifier correction."""
    f = frame()
    found = candidate(f)
    assert found[0]['auto_eligible'] == 'yes'
    edits, pairs, review = automatic_actions(f, found)
    assert len(edits) == 1 and not pairs and not review
    assert edits[0]['approved'] == 'yes'
    assert edits[0]['old_identifier'] == PREPRINT and edits[0]['new_doi'] == ARTICLE


def test_existing_versions_use_dedup_ruling_not_identifier_edit():
    """An existing compatible version pair uses native dedup rather than standalone edits."""
    f = frame(publisher=True)
    edits, pairs, review = automatic_actions(f, candidate(f))
    assert not edits and not review and len(pairs) == 1
    assert pairs[0]['side'] == 'replication (publication link)'
    assert pairs[0]['action'] == 'keep_2'


def test_other_original_or_type_is_not_a_duplicate():
    """Different originals and study types must retain separate replication records."""
    f = frame(publisher=True, different_type=True)
    edits, pairs, _ = automatic_actions(f, candidate(f))
    assert len(edits) == 1 and not pairs
    f = frame(publisher=True)
    f.loc[1, 'doi_o'] = '10.9999/other-original'
    edits, pairs, _ = automatic_actions(f, candidate(f))
    assert len(edits) == 1 and not pairs


def test_human_decision_and_distinct_ruling_are_not_overwritten():
    """Explicit human decisions take priority over automatic publication equivalence."""
    f = frame(publisher=True)
    human = [{'doi_1': PREPRINT, 'doi_2': ARTICLE, 'action': 'keep_both'}]
    edits, pairs, review = automatic_actions(f, candidate(f), human)
    assert not edits and not pairs and review[0]['reason'] == 'human ruling'
    f = frame(publisher=True, distinct=True)
    assert automatic_actions(f, candidate(f))[2][0]['reason'] == 'reviewer ruled a record distinct'


def test_renamed_missing_author_conflicting_and_openalex_only_stay_review():
    """Weak evidence and contradictory current bibliography cannot trigger automatic writes."""
    f = frame()
    for changed in [dict(auto_eligible=''), dict(auto_eligible='', confidence='conflicting_relations_review')]:
        found = [{**candidate(f)[0], **changed}]
        assert automatic_actions(f, found)[0:2] == ([], [])
    f.loc[0, 'author_r'] = 'Someone Jones'
    assert not automatic_actions(f, candidate(f))[0]
    f = frame()
    f.loc[0, 'title_r'] = 'An entirely different paper'
    assert not automatic_actions(f, candidate(f))[0]


def test_preserved_alias_can_trigger_merge_after_prior_correction():
    """A preserved preprint alias identifies duplicate versions even after DOI promotion."""
    f = frame(publisher=True)
    f.loc[0, ['doi_r', 'alt_identifier_r']] = [ARTICLE, PREPRINT]
    found = candidate(f)
    assert found[0]['doi_1'] == PREPRINT
    edits, pairs, review = automatic_actions(f, found)
    assert not edits and len(pairs) == 1 and not review


def test_linked_merge_keeps_publisher_identity_source_ids_aliases_and_outcomes(tmp_path):
    """Merging retains the publisher identity, absorbed provenance and both outcome evidence sets."""
    f = frame(publisher=True)
    f['record_id'] = ['source-preprint', 'source-journal']
    f['outcome'] = ['successful', 'failed']
    f['outcome_quote'] = ['Preprint evidence', 'Journal evidence']
    f['url_r'] = ['https://osf.io/preprint', 'https://journal.example/article']
    f['alt_identifier_r'] = ['10.9999/early-version', '10.9999/publisher-alias']
    p = tmp_path / 'confirmed.csv'
    pd.DataFrame([{'side': 'replication (publication link)', 'doi_1': PREPRINT,
                   'doi_2': ARTICLE, 'action': 'keep_2'}]).to_csv(p, index=False)
    result = dedup.apply_confirmed(f, p, verbose=False)
    assert len(result) == 1
    row = result.iloc[0]
    assert row['id'] == 'journal-id' and row['record_id'] == 'source-journal'
    assert 'source-preprint' in row['merged_record_ids']
    assert row['outcome'] == 'mixed'
    assert 'Preprint evidence' in row['outcome_quote'] and 'Journal evidence' in row['outcome_quote']
    for alias in [PREPRINT, '10.9999/early-version', '10.9999/publisher-alias', 'https://osf.io/preprint']:
        assert alias in row['alt_identifier_r']
    assert row['url_r'] == 'https://journal.example/article'
    # The stored ruling cannot promote a lone preprint through the merge path.
    assert dedup.apply_confirmed(f.iloc[:1], p, verbose=False).iloc[0]['doi_r'] == PREPRINT


def test_confirmed_file_overrules_automation_but_admin_still_overrules_file(tmp_path):
    """File-based human rulings outrank automation while later admin rulings retain precedence."""
    p = tmp_path / 'confirmed.csv'
    pd.DataFrame([{'side': 'replication', 'doi_1': PREPRINT, 'doi_2': ARTICLE,
                   'action': 'keep_both'}]).to_csv(p, index=False)
    ruling = {'side': 'replication (publication link)', 'doi_1': PREPRINT, 'doi_2': ARTICLE,
              'action': 'keep_2', 'decided_by': ACTOR}
    assert dedup.confirmed_decisions(p, [ruling])[0]['action'] == 'keep_both'
    assert dedup.confirmed_decisions(p, [{**ruling, 'decided_by': 'admin'}])[0]['action'] == 'keep_2'


def test_incomplete_api_lookups_prevent_all_automatic_writes(tmp_path, monkeypatch):
    """Any lookup failure prevents the automatic batch from committing changes."""
    import sys
    from unittest.mock import MagicMock
    import resolve_publication_versions as runner
    connection = MagicMock()
    monkeypatch.setenv('DATABASE_URL', 'unused-test-value')
    monkeypatch.setattr(runner.psycopg2, 'connect', lambda *a, **kw: connection)
    monkeypatch.setattr(runner, 'live_frame', lambda cur: frame())
    monkeypatch.setattr(runner.discovery, 'Client', lambda *a: Mock(errors={'https://api.example/lookup': 'timeout'}))
    monkeypatch.setattr(runner.discovery, 'scan', lambda *a: candidate(frame()))
    apply = Mock()
    monkeypatch.setattr(runner, 'apply_automatic', apply)
    monkeypatch.setattr(sys, 'argv', ['resolve_publication_versions.py', '--apply', '--output', str(tmp_path / 'candidates.csv')])
    assert runner.main() == 1
    apply.assert_not_called()
    connection.commit.assert_not_called()


def test_scheduled_resolution_runs_before_the_final_preparation():
    """Nightly resolution precedes the preparation and validation gate."""
    import yaml
    from pathlib import Path
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/sync-sources.yml').read_text())
    steps = workflow['jobs']['sync']['steps']
    auto = next(i for i,s in enumerate(steps) if s.get('id') == 'publication_versions')
    build = next(i for i,s in enumerate(steps) if s.get('id') == 'build_dataset')
    assert auto < build
    assert steps[auto]['run'] == 'python resolve_publication_versions.py --apply'
    assert 'DATABASE_URL' in steps[auto]['env']


def test_already_correct_standalone_alias_does_not_trigger_extra_api_lookups():
    """Correct standalone journal records do not re-query historical preprint aliases."""
    f = frame().copy()
    f.loc[0, ['doi_r', 'alt_identifier_r']] = [ARTICLE, PREPRINT]
    client = FakeClient(article_doi=ARTICLE)
    assert scan(f, client) == []
    assert not client.calls


def test_original_side_merges_only_an_existing_same_replication_pair(tmp_path):
    """Original-version merging requires an existing counterpart for the same replication."""
    f = frame(publisher=True)
    f['doi_o'] = [PREPRINT, ARTICLE]
    f['doi_r'] = ['10.9999/report'] * 2
    f['title_o'] = 'Published paper'
    f['author_o'] = 'Sam Smith'
    f['record_id'] = ['source-preprint', 'source-journal']
    found = candidate(f)
    edits, pairs, review = automatic_actions(f, found)
    assert not edits and not review and pairs[0]['side'] == 'original (publication link)'
    p = tmp_path / 'confirmed.csv'
    pd.DataFrame(pairs).to_csv(p, index=False)
    result = dedup.apply_confirmed(f, p, verbose=False)
    assert len(result) == 1 and result.iloc[0]['id'] == 'journal-id'
    assert PREPRINT in result.iloc[0]['alt_identifier_o']
    assert 'source-preprint' in result.iloc[0]['merged_record_ids']
    assert dedup.apply_confirmed(f.iloc[:1], p, verbose=False).iloc[0]['doi_o'] == PREPRINT


def test_publication_merge_preserves_unrelated_product_identifier_choices(tmp_path):
    """An unrelated identifier correction prevents the source row being absorbed."""
    f = frame(publisher=True)
    f['record_id'] = ['source-preprint', 'source-journal']
    f['url_r'] = ''
    p = tmp_path / 'confirmed.csv'
    pd.DataFrame([{'side': 'replication (publication link)', 'doi_1': PREPRINT,
                   'doi_2': ARTICLE, 'action': 'keep_2'}]).to_csv(p, index=False)
    result = dedup.apply_confirmed(f, p, verbose=False,
        publication_guards={'source-preprint': {'doi_r': '10.9999/a-different-corrected-paper'}})
    assert len(result) == 2  # do not absorb a record whose actual paper was corrected
    result = dedup.apply_confirmed(f, p, verbose=False,
        publication_guards={'source-preprint': {'doi_r': ARTICLE}})
    assert len(result) == 1  # a prior correction to this journal is compatible


def test_registry_diacritic_variants_do_not_block_an_explicit_link():
    """Surname diacritics in registered metadata do not create false author mismatches."""
    f = frame()
    f['author_r'] = 'Michal Doležel'
    found = candidate(frame())
    found[0]['first_author_2'] = 'dolezel'
    edits, pairs, review = automatic_actions(f, found)
    assert len(edits) == 1 and not pairs and not review


def test_manual_automatic_resolution_uses_the_normal_preparation_gate():
    """Manual automation also validates the rebuilt dataset before committing its release."""
    import yaml
    from pathlib import Path
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/publish-flora.yml').read_text())
    steps = workflow['jobs']['publish']['steps']
    resolve = next(i for i,s in enumerate(steps) if s['name'] == 'Resolve verified published versions')
    prepare = next(i for i,s in enumerate(steps) if s['name'] == 'Prepare automatically resolved dataset')
    commit = next(i for i,s in enumerate(steps) if s['name'] == 'Commit the FLoRA release to output/')
    assert resolve < prepare < commit
    assert 'prepare_flora.py' in steps[prepare]['run']
    writer = next(s for s in steps if s['name'] == 'Write the stored dataset to output/')
    assert '!inputs.resolve_published_versions' in writer['if']


def test_discovery_artifact_failure_cannot_block_validated_release():
    """Missing files and upload-service errors leave the nightly release runnable."""
    import yaml
    from pathlib import Path
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/sync-sources.yml').read_text())
    steps = workflow['jobs']['sync']['steps']
    upload = next(s for s in steps if s['name'] == 'Upload published-version candidates and lookup status')
    assert upload['continue-on-error'] is True
    assert upload['with']['if-no-files-found'] == 'warn'
    resolver = next(s for s in steps if s.get('id') == 'publication_versions')
    assert resolver['continue-on-error'] is True
