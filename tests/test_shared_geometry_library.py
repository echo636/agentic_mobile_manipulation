"""Local Git/snapshot tests, without models, native simulation or robot motion."""
import json
import importlib.util
from pathlib import Path

import pytest

from manipulation_agent.contracts import Budget
from manipulation_agent.geometry_library import GeometryProgramLibrary
from manipulation_agent.manip_programs import ManipPrograms
from manipulation_agent.oracle_harness import OracleWaypointHarness
from manipulation_agent.records import Recorder
from test_model_manip import SOURCE


def test_new_episode_reuses_collected_draft_and_keeps_its_snapshot(tmp_path):
    shared = tmp_path / 'shared'
    first = ManipPrograms(tmp_path / 'first', library_root=shared)
    saved = first.write('shared_grasp', SOURCE, 'Reusable geometry fixture', None)
    assert saved['library_publication']['status'] == 'published'
    assert saved['status'] == 'draft' and saved['version'] == 1
    second = ManipPrograms(tmp_path / 'second', library_root=shared)
    assert second.read('shared_grasp')['source'] == SOURCE
    assert 'shared_grasp' in {row['program'] for row in second.catalog()}
    changed = first.write('shared_grasp', SOURCE+'\n# improved\n', 'Improved fixture', saved['source_sha256'])
    assert changed['version'] == 2
    assert second.read('shared_grasp')['source'] == SOURCE
    # Reopening an active episode must not replace its frozen source either.
    assert ManipPrograms(tmp_path / 'second', library_root=shared).read('shared_grasp')['source'] == SOURCE
    third = ManipPrograms(tmp_path / 'third', library_root=shared)
    assert third.read('shared_grasp')['source'] == SOURCE+'\n# improved\n'
    git = first.shared.library.git
    assert git.command('show', saved['library_release']['release_tag']+
        ':oracle_waypoints/geometry_programs/shared_grasp/source.py').stdout == SOURCE
    git.require_clean()


def test_concurrent_old_snapshot_cannot_overwrite_shared_update(tmp_path):
    shared = tmp_path / 'shared'
    first = ManipPrograms(tmp_path / 'first', library_root=shared)
    saved = first.write('grasp', SOURCE, 'Initial', None)
    second = ManipPrograms(tmp_path / 'second', library_root=shared)
    updated = first.write('grasp', SOURCE+'\n# first change\n', 'New', saved['source_sha256'])
    conflict = second.write('grasp', SOURCE+'\n# other change\n', 'Local alternative', saved['source_sha256'])
    assert conflict['library_publication']['status'] == 'pending'
    assert conflict['library_publication']['local_source_preserved']
    assert second.read('grasp')['source'] == SOURCE+'\n# other change\n'
    current = GeometryProgramLibrary(shared).snapshot()[0]
    assert current['source_sha256'] == updated['source_sha256']
    first.shared.library.git.require_clean()


def test_dirty_library_preserves_manual_edits_and_local_source_then_retry_collects(tmp_path):
    shared = tmp_path / 'shared'
    programs = ManipPrograms(tmp_path / 'episode', library_root=shared)
    saved = programs.write('grasp', SOURCE, 'Initial', None)
    readme = shared / 'README.md'
    original = readme.read_text()
    readme.write_text(original+'\nManual work in progress.\n')
    changed = programs.write('grasp', SOURCE+'\n# correction\n', 'Correction', saved['source_sha256'])
    assert changed['library_publication']['status'] == 'pending'
    assert 'uncommitted changes' in changed['library_publication']['error']
    assert readme.read_text() == original+'\nManual work in progress.\n'
    assert programs.read('grasp')['source'] == SOURCE+'\n# correction\n'
    with pytest.raises(ValueError, match='uncommitted changes'):
        ManipPrograms(tmp_path / 'blocked_snapshot', library_root=shared)
    readme.write_text(original)
    retried = programs.write('grasp', SOURCE+'\n# correction\n', 'Correction', changed['source_sha256'])
    assert retried['library_publication']['status'] == 'published' and retried['version'] == 2
    programs.shared.library.git.require_clean()


def test_repeated_collection_is_idempotent_and_validates_source(tmp_path):
    programs = ManipPrograms(tmp_path / 'episode', library_root=tmp_path / 'shared')
    saved = programs.write('grasp', SOURCE, 'Initial', None)
    before = programs.shared.library.git.head()
    again = programs.write('grasp', SOURCE, 'Unchanged', saved['source_sha256'])
    assert again['version'] == 1 and programs.shared.library.git.head() == before
    broken = {**programs.read('grasp'), 'source': SOURCE+'\n'}
    with pytest.raises(ValueError, match='hash mismatch'):
        programs.shared.publish(broken, saved['source_sha256'])
    with pytest.raises(ValueError, match='identifier'):
        programs.shared.publish({**broken, 'program': '../escape'}, None)
    assert programs.shared.library.git.head() == before


def test_shared_hash_corruption_is_rejected_before_snapshot(tmp_path):
    programs = ManipPrograms(tmp_path / 'episode', library_root=tmp_path / 'shared')
    programs.write('grasp', SOURCE, 'Initial', None)
    library = programs.shared
    (library.folder('grasp') / 'source.py').write_text(SOURCE+'\n# corrupted\n')
    library.library.git.commit('CPU fixture: inconsistent source metadata')
    with pytest.raises(ValueError, match='identity/source mismatch'):
        ManipPrograms(tmp_path / 'new_episode', library_root=tmp_path / 'shared')


def test_host_write_collects_for_next_host_without_a_robot_action(tmp_path, monkeypatch):
    from test_oracle_waypoints import FakeBackend
    monkeypatch.setenv('MAS_MANIP_ENGINE', 'model_program_v1')
    monkeypatch.setenv('MAS_PROGRAM_LIBRARY_ROOT', str(tmp_path / 'shared'))
    backend = FakeBackend()
    first = OracleWaypointHarness(backend, Recorder(tmp_path / 'first', {}), Budget(wall_seconds=None))
    reply = first.call('write_manip_program', {'program': 'shared_grasp', 'source': SOURCE,
        'description': 'CPU fixture', 'expected_sha256': None}, 'write')
    assert reply['ok'] and reply['library_publication']['status'] == 'published'
    second = OracleWaypointHarness(backend, Recorder(tmp_path / 'second', {}), Budget(wall_seconds=None))
    assert 'shared_grasp' in {row['program'] for row in second.initialize()['programs']}
    assert second.call('read_manip_program', {'program': 'shared_grasp'}, 'read')['source'] == SOURCE
    assert not backend.moves
    config = json.loads((tmp_path / 'second/run.json').read_text())['config']
    assert config['shared_geometry_library'] == str(tmp_path / 'shared')


def test_historical_collection_preserves_all_versions_without_rewriting_archive(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'scripts/collect_manip_programs.py'
    spec = importlib.util.spec_from_file_location('collect_manip_programs', path)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    archive = ManipPrograms(tmp_path / 'old_episode')
    first = archive.write('old_grasp', SOURCE, 'Archived fixture', None)
    last = archive.write('old_grasp', SOURCE+'\n# old improvement\n', 'Archived fixture', first['source_sha256'])
    pointer = (archive.root / 'old_grasp/current.json').read_bytes()
    result = collector.collect(tmp_path / 'shared', [archive.root])
    assert len(result['collected']) == 4  # Two built-ins and both archived versions.
    shared = GeometryProgramLibrary(tmp_path / 'shared')
    current = {row['program']: row for row in shared.snapshot()}
    assert current['old_grasp']['source_sha256'] == last['source_sha256']
    assert current['old_grasp']['version'] == 2
    assert (archive.root / 'old_grasp/current.json').read_bytes() == pointer
    repeated = collector.collect(tmp_path / 'shared', [archive.root])
    assert not repeated['collected'] and repeated['library_commit'] == result['library_commit']
    shared.library.git.require_clean()


def test_catalog_reports_existing_native_evidence_for_exact_source_only(tmp_path):
    programs = ManipPrograms(tmp_path / 'episode', library_root=tmp_path / 'shared')
    saved = programs.write('grasp', SOURCE, 'Initial', None)
    evidence = programs.shared.folder('grasp') / 'evidence/runs.jsonl'
    evidence.parent.mkdir()
    rows = [{'source_sha256': saved['source_sha256'], 'fixed_program_native_passed': True},
            {'source_sha256': '0'*64, 'fixed_program_native_passed': True},
            {'source_sha256': saved['source_sha256'], 'fixed_program_native_passed': False}]
    evidence.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    programs.shared.library.git.commit('CPU fixture: record exact-source outcomes')
    frozen = ManipPrograms(tmp_path / 'next_episode', library_root=tmp_path / 'shared').read('grasp')
    assert frozen['recorded_validation'] == {'runs': 2, 'native_passed_runs': 1}
    assert frozen['status'] == 'draft'
