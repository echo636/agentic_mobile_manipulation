"""CPU control-flow/geometry checks. These fixtures are never robot-success evidence."""
import copy
import asyncio
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from manipulation_agent.compiled_oracle import CompiledOracleHarness
from manipulation_agent.contracts import Budget
from manipulation_agent.program_library import ProgramLibrary, source_files, digest
from manipulation_agent.program_geometry import point, hinge_path
from manipulation_agent.records import Recorder, write_json
from manipulation_agent.tools import tool_specs
from manipulation_agent.vision_policy import system_prompt

BLUEPRINTS = Path(__file__).resolve().parents[1] / 'program_skills/skills'


def fixture_observation():
    identity = [0., 0., 0., 1.]
    hand = {'eef_pose': {'position_m': [.3, 0., .6], 'quaternion_xyzw': identity},
            'held_object': 'can', 'native_grasp': {'object_id': 'can', 'target_link': 'base_link', 'mode': 'assisted'}}
    empty = copy.deepcopy(hand);empty.update(held_object=None, native_grasp={'object_id': None, 'target_link': None, 'mode': 'assisted'})
    def obj(name, position, low, high):
        return {'id': name, 'fixed_base': False, 'pose': {'position_m': position, 'quaternion_xyzw': identity},
                'aabb': {'min_m': low, 'max_m': high}, 'joints': []}
    return {'observation_mode': 'oracle_3d_waypoints', 'images': [],
            'capture': {'capture_id': 'fixture', 'sim_step': 0},
            'oracle_3d': {'frame': 'robot_base', 'units': {}, 'object_scope': 'CPU fixture', 'omitted_objects': 0,
                'robot': {'arms': {'right': hand, 'left': empty}}, 'objects': [
                    obj('can', [.3, 0., .55], [.27, -.03, .48], [.33, .03, .62]),
                    obj('bucket', [.5, 0., .4], [.4, -.1, .2], [.6, .1, .6])]}}


class Backend:
    def __init__(self):
        self.packet = fixture_observation();self.steps = 0;self.moves = [];self.fail_rotation = None;self.close_on_wait = False
    def observe(self):
        packet = copy.deepcopy(self.packet);packet['capture']['sim_step'] = self.steps
        return packet
    def provenance(self): return {'name': 'CPU fixture; no physical validation'}
    def control_contract(self): return {'protocol': 'CPU fixture'}
    def execute_motor(self, name, **args):
        self.moves.append((name, args));self.steps += 1
        hand = self.packet['oracle_3d']['robot']['arms'][args.get('arm', 'right')]
        if name == 'place':
            target = self.packet['oracle_3d']['objects'][0]
            offset = [hand['eef_pose']['position_m'][i]-target['pose']['position_m'][i] for i in range(3)]
            release = args['placement_waypoints'][-1]['position_m']
            target['pose']['position_m'] = [release[i]-offset[i] for i in range(3)]
            hand['held_object'] = None;hand['native_grasp']['object_id'] = None
            return {'reached': True, 'verified': True, 'relation_satisfied': True, 'released': True,
                    'stability': {'stable': True, 'samples': 90, 'position_drift_m': .0001}}
        if name == 'rotate':
            rotations = sum(n == 'rotate' for n, _ in self.moves)
            if rotations == self.fail_rotation: return {'reached': False, 'verified': False}
            self.packet['oracle_3d']['objects'][0]['joints'][0]['position'] += args['angle_rad']
            hand['eef_pose'] = {k: v for k, v in args['motion_waypoints'][-1].items() if k != 'gripper'}
            return {'reached': True, 'verified': True, 'actual_delta': args['angle_rad']}
        if name == 'release':
            hand['held_object'] = None;hand['native_grasp']['object_id'] = None
            return {'reached': True, 'verified': True, 'released': True}
        if name == 'execute_waypoints':
            hand['eef_pose'] = {k: v for k, v in args['waypoints'][-1].items() if k != 'gripper'}
        if name == 'wait':
            self.steps += args['steps']-1
            if self.close_on_wait: self.packet['oracle_3d']['objects'][0]['joints'][0]['position'] = 0.
        return {'reached': True}


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name)
        self.library = ProgramLibrary(self.root / 'library')
        self.demo = self.root / 'demonstration';(self.demo / 'env').mkdir(parents=True)
        write_json(self.demo / 'env/run.json', {'status': 'passed', 'task_success': False,
            'config': {'task': 'collecting_aluminum_cans', 'observation_mode': 'oracle_3d_waypoints',
                       'agent_profile': 'oracle_waypoints', 'instance': 301, 'seed': 0},
            'evaluation': {'official_task_success': False}})
        write_json(self.demo / 'engineering_validation.json', {'engineering_success': True})
        self.image = self.demo / 'env/reference.jpg';self.image.write_bytes(b'CPU fixture; no robot image')
        for name in ['place-held-object', 'open-retained-door']:
            self.library.publish(BLUEPRINTS / name, 0, self.demo, self.image)
        self.backend = Backend()
    def tearDown(self): self.temp.cleanup()
    def harness(self, name='episode'):
        return CompiledOracleHarness(self.backend, Recorder(self.root / name, {'task': 'collecting_aluminum_cans'}),
            Budget(wall_seconds=None), program_root=self.root / 'library', experience_root=self.root / 'empty_text_library')
    def invoke(self, h, name='place-held-object', parameters=None, ident='one', allow=True, revision=None):
        return h.call('execute_skill', {'name': name, 'parameters_json': json.dumps(parameters or {
            'arm': 'right', 'target_object_id': 'can', 'destination_object_id': 'bucket',
            'release_position_in_destination_m': [0., 0., .04], 'allow_trunk': False}),
            'allow_draft': allow, 'revision': h.revision if revision is None else revision}, ident)
    def door(self):
        obs = self.backend.packet;hand = obs['oracle_3d']['robot']['arms']['right']
        hand['native_grasp']['target_link'] = 'door_link'
        obj = obs['oracle_3d']['objects'][0];obj['fixed_base'] = True
        obj['joints'] = [{'id': 'hinge', 'type': 'revolute', 'child_link': 'door_link', 'frame': 'robot_base',
            'position': 0., 'lower_limit': 0., 'upper_limit': 1.57, 'axis_origin_m': [0., 0., .6], 'axis_direction': [0., 0., -1.]}]
        return {'arm': 'right', 'target_object_id': 'can', 'target_link': 'door_link', 'joint_id': 'hinge',
                'goal_position_rad': .6, 'minimum_released_position_rad': .5,
                'retreat_direction_base': [1., 0., 0.], 'allow_trunk': False}
    def test_existing_profiles_do_not_get_code_tools(self):
        for profile in ['minimal', 'skills', 'workflow', 'oracle_waypoints']:
            self.assertNotIn('execute_skill', {t['name'] for t in tool_specs(profile)})
        self.assertIn('execute_skill', {t['name'] for t in tool_specs('oracle_compiled')})
        self.assertIn('code skills', system_prompt('oracle_compiled'))
    def test_runtime_relocalizes_moved_rotated_destination(self):
        bucket = self.backend.packet['oracle_3d']['objects'][1]
        bucket['pose'].update(position_m=[.45, .12, .35], quaternion_xyzw=[0, 0, 2**-.5, 2**-.5])
        params = {'arm': 'right', 'target_object_id': 'can', 'destination_object_id': 'bucket',
                  'release_position_in_destination_m': [.03, 0., .04], 'allow_trunk': False}
        h = self.harness();reply = self.invoke(h, parameters=params)
        self.assertTrue(reply['ok'], reply)
        desired = point(bucket['pose'], params['release_position_in_destination_m'])
        self.assertEqual(self.backend.packet['oracle_3d']['objects'][0]['pose']['position_m'], desired)
        self.assertEqual([n for n, _ in self.backend.moves], ['place'])
        self.assertEqual(reply['execution']['measured']['stable_flag_window_steps'], 10)
        # Native children are recorded separately from the model's one macro request.
        rows = [json.loads(l) for l in (h.recorder.output / 'events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('name') == 'place' and e.get('origin') == 'compiled_skill' for e in rows))
    def test_default_refuses_draft_without_moving(self):
        h = self.harness();reply = self.invoke(h, allow=False)
        self.assertFalse(reply['ok']);self.assertEqual(self.backend.moves, [])
    def test_wrong_retained_object_refuses_before_motion(self):
        self.backend.packet['oracle_3d']['robot']['arms']['right']['held_object'] = 'different'
        h = self.harness();self.assertFalse(self.invoke(h)['ok']);self.assertEqual(self.backend.moves, [])
    def test_stale_revision_and_cached_macro_do_not_repeat_motion(self):
        h = self.harness();self.assertFalse(self.invoke(h, ident='stale', revision=1)['ok'])
        reply = self.invoke(h, revision=0);count = len(self.backend.moves)
        self.assertEqual(reply, self.invoke(h, revision=0));self.assertEqual(count, len(self.backend.moves))
    def test_corrupt_snapshot_refuses_before_motion(self):
        h = self.harness();entry = h.programs['place-held-object']
        with (entry['directory'] / 'code/run.py').open('a') as f: f.write('\n# changed\n')
        self.assertFalse(self.invoke(h)['ok']);self.assertEqual(self.backend.moves, [])
    def test_door_chains_native_arcs_then_one_wait(self):
        parameters = self.door();h = self.harness();reply = self.invoke(h, 'open-retained-door', parameters)
        self.assertTrue(reply['ok'], reply)
        names = [n for n, _ in self.backend.moves]
        self.assertEqual(names, ['rotate']*6+['release', 'execute_waypoints', 'wait'])
        self.assertEqual(self.backend.moves[-1][1]['steps'], 60)
        self.assertIn('independent', reply['execution']['measured']['continuous_hold'])
    def test_failed_arc_never_continues_or_releases(self):
        parameters = self.door();self.backend.fail_rotation = 2
        h = self.harness();reply = self.invoke(h, 'open-retained-door', parameters)
        self.assertFalse(reply['ok']);self.assertEqual([n for n, _ in self.backend.moves], ['rotate', 'rotate'])
        self.assertEqual(h.snapshot['oracle_3d']['robot']['arms']['right']['held_object'], 'can')
    def test_post_release_relaxation_fails_hold(self):
        parameters = self.door();self.backend.close_on_wait = True
        h = self.harness();self.assertFalse(self.invoke(h, 'open-retained-door', parameters)['ok'])
    def test_model_demonstrations_never_promote_code(self):
        with self.assertRaises(ValueError): self.library.set_status('place-held-object', 'verified', 1)
    def test_new_version_does_not_change_frozen_episode(self):
        h = self.harness();old = h.programs['place-held-object']['code_sha256']
        source = self.root / 'changed'
        import shutil
        shutil.copytree(BLUEPRINTS / 'place-held-object', source)
        p = source / 'code/place_held_object/params.py';p.write_text(p.read_text().replace('SETTLE_STEPS = 90', 'SETTLE_STEPS = 120'))
        new = self.library.publish(source, 1, self.demo, self.image)
        self.assertEqual(new['status'], 'draft');self.assertNotEqual(new['code_sha256'], old)
        self.assertTrue(self.invoke(h)['ok']);self.assertEqual(h.programs['place-held-object']['code_sha256'], old)
        self.assertEqual(self.backend.moves[-1][1]['settle_steps'], 90)

    def code_evidence(self, episode, invocation_id='run-one', reviewed=True, frames=True, unattended=True):
        """Synthetic review fixtures, never persisted as real robot evidence."""
        folder = self.root / episode;env = folder / 'env'
        (env / 'program_runs').mkdir(parents=True, exist_ok=True)
        record = json.loads((self.demo / 'env/run.json').read_text())
        record['config']['agent_profile'] = 'oracle_compiled'
        write_json(env / 'run.json', record)
        write_json(folder / 'engineering_validation.json', {'engineering_success': True})
        entry = self.library.current('place-held-object')
        refs = []
        if frames:
            (env / 'frames').mkdir(exist_ok=True)
            pixels = b'synthetic control-flow test, not robot evidence'
            (env / 'frames/test.jpg').write_bytes(pixels)
            refs = [{'image_ref': 'test', 'sha256': hashlib.sha256(pixels).hexdigest()}]
        invocation = {'name': entry['name'], 'version': entry['version'], 'code_sha256': entry['code_sha256'],
            'library_commit': self.library.git.head(),
            'runtime_sha256': entry['runtime_sha256'], 'dependency_code_sha256': entry['dependency_code_sha256'],
            'unattended': unattended, 'result': {'ok': True, 'frames': refs, 'measured': {}, 'why': ''}}
        p = env / 'program_runs' / (invocation_id + '.json')
        if not p.exists(): write_json(p, invocation)
        if reviewed:
            write_json(folder / 'program_validation.json', {'invocations': [{
                'invocation_id': invocation_id, 'code_sha256': entry['code_sha256'],
                'runtime_sha256': entry['runtime_sha256'], 'invocation_sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                'scope': 'program_invocation', 'passed': True}]})
        return folder

    def test_verification_requires_distinct_reviewed_unattended_runs(self):
        for ident in ['run-one', 'run-two']:
            folder = self.code_evidence('same-episode', ident)
            self.library.record_run(folder, ident)
        with self.assertRaises(ValueError): self.library.set_status('place-held-object', 'verified', 1)
        other = self.code_evidence('second-episode')
        self.library.record_run(other, 'run-one')
        self.assertEqual(self.library.set_status('place-held-object', 'verified', 1)['status'], 'verified')

    def test_incomplete_and_review_bound_to_changed_invocation_do_not_verify(self):
        for case, options in [('unreviewed', {'reviewed': False}), ('no-image', {'frames': False}),
                              ('helped', {'unattended': False})]:
            self.library.record_run(self.code_evidence(case, **options), 'run-one')
        folder = self.code_evidence('changed-invocation')
        p = folder / 'env/program_runs/run-one.json'
        packet = json.loads(p.read_text());packet['result']['measured'] = {'changed': True};write_json(p, packet)
        row = self.library.record_run(folder, 'run-one')
        self.assertFalse(row['independently_reviewed'])
        with self.assertRaises(ValueError): self.library.set_status('place-held-object', 'verified', 1)

    def test_review_can_be_appended_but_never_overwrites_old_evidence(self):
        folder = self.code_evidence('review-later', reviewed=False)
        old = self.library.record_run(folder, 'run-one');self.assertEqual(old['outcome'], 'failure_or_unreviewed')
        with self.assertRaises(ValueError): self.library.record_run(folder, 'run-one')
        self.code_evidence('review-later')
        new = self.library.record_run(folder, 'run-one');self.assertEqual(new['outcome'], 'success')
        ledger = self.library.folder('place-held-object') / 'evidence/runs.jsonl'
        self.assertEqual(len(ledger.read_text().splitlines()), 2)
        self.library.record_run(self.code_evidence('another'), 'run-one')
        self.library.set_status('place-held-object', 'verified', 1)
        import shutil
        source = self.root / 'updated';shutil.copytree(BLUEPRINTS / 'place-held-object', source)
        code = source / 'code/place_held_object/params.py';code.write_text(code.read_text()+'\n# revision\n')
        self.library.publish(source, 1, self.demo, self.image)
        with self.assertRaises(ValueError): self.library.set_status('place-held-object', 'verified', 2)

    def test_runtime_drift_refuses_publication_evidence_reuse_and_motion(self):
        h = self.harness()
        with patch('manipulation_agent.program_runtime.runtime_identity', return_value={'runtime_sha256': 'changed'}):
            self.assertFalse(self.invoke(h)['ok'])
        self.assertEqual(self.backend.moves, [])
        with patch('manipulation_agent.program_library.runtime_identity', return_value={'runtime_sha256': 'changed'}):
            with self.assertRaises(ValueError): self.library.freeze(self.root / 'new-freeze')
            with self.assertRaises(ValueError): self.library.set_status('place-held-object', 'verified', 1)

    def test_dependency_cycle_refused_and_changed_dependency_needs_republication(self):
        import shutil
        source = self.root / 'composite';shutil.copytree(BLUEPRINTS / 'open-retained-door', source)
        spec_path = source / 'spec.json';spec = json.loads(spec_path.read_text())
        spec['uses'] = ['open-retained-door'];write_json(spec_path, spec)
        with self.assertRaises(ValueError): self.library.publish(source, 1, self.demo, self.image)
        spec['uses'] = ['place-held-object'];write_json(spec_path, spec)
        self.library.publish(source, 1, self.demo, self.image)
        changed = self.root / 'changed-dependency';shutil.copytree(BLUEPRINTS / 'place-held-object', changed)
        p = changed / 'code/place_held_object/params.py';p.write_text(p.read_text()+'\n# changed dependency\n')
        self.library.publish(changed, 1, self.demo, self.image)
        with self.assertRaises(ValueError): self.library.freeze(self.root / 'bad-freeze')

    def test_hinge_geometry_uses_observed_signed_nonunit_axis(self):
        eef = {'position_m': [2., 0., 1.], 'quaternion_xyzw': [0., 0., 0., 1.]}
        last = hinge_path(eef, [1., 0., 1.], [0., 0., -3.], math.pi/2)[-1]
        for actual, expected in zip(last['position_m'], [1., -1., 1.]): self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(last['quaternion_xyzw'], [0., 0., -2**-.5, 2**-.5]):
            self.assertAlmostEqual(actual, expected)

    def test_fastmcp_keeps_compiled_schema_and_encoded_parameters(self):
        from manipulation_agent.mcp_server import create_server
        h = self.harness();reply = self.invoke(h)
        parameters = {'name': 'place-held-object', 'parameters_json': '{"caller": "keeps JSON as text"}',
                      'allow_draft': True, 'revision': 0}
        async def exercise():
            with patch('manipulation_agent.mcp_server.fetch', return_value=json.dumps({
                    'ready': True, 'tools': tool_specs('oracle_compiled')}).encode()), \
                 patch('manipulation_agent.mcp_server.rpc', return_value=reply) as rpc:
                server = await create_server('http://127.0.0.1:1')
                listed = {t.name: t.inputSchema for t in await server.list_tools()}
                self.assertEqual(listed['execute_skill'], next(t['inputSchema'] for t in
                    tool_specs('oracle_compiled') if t['name'] == 'execute_skill'))
                blocks = await server.call_tool('execute_skill', parameters)
                self.assertTrue(json.loads(blocks[0].text)['ok'])
                self.assertEqual(rpc.call_args.args[2], parameters)
        asyncio.run(exercise())

    def test_git_tracks_current_sources_and_old_release_without_revision_copies(self):
        entry = self.library.current('place-held-object');folder = self.library.folder(entry['name'])
        self.assertEqual(entry['directory'], folder)
        self.assertFalse((folder / 'revisions').exists())
        self.assertEqual(self.library.git.command('remote').stdout, '')
        self.library.git.require_clean()
        import shutil
        source = self.root / 'git-update';shutil.copytree(BLUEPRINTS / entry['name'], source)
        p = source / 'code/place_held_object/params.py'
        before = p.read_text();p.write_text(before.replace('SETTLE_STEPS = 90', 'SETTLE_STEPS = 120'))
        self.library.publish(source, 1, self.demo, self.image)
        original = self.library.git.command('show',
            'oracle_waypoints/place-held-object/v000001:oracle_waypoints/skills/place-held-object/code/place_held_object/params.py').stdout
        self.assertEqual(original, before)
        self.assertIn('SETTLE_STEPS = 120', (folder / 'code/place_held_object/params.py').read_text())
        frozen = self.library.freeze(self.root / 'git-snapshot')
        self.assertEqual(frozen[entry['name']]['library_commit'], self.library.git.head())
        self.assertEqual(self.library.history(entry['name'])[0]['message'], 'Publish place-held-object v2 as draft')
        tracked = self.library.git.command('ls-files').stdout.splitlines()
        self.assertFalse(any('/revisions/' in p or p.endswith('.jpg') for p in tracked))

    def test_git_preserves_dirty_user_changes_and_refuses_new_snapshots(self):
        folder = self.library.folder('place-held-object')
        p = folder / 'code/run.py';original = p.read_text();p.write_text(original+'\n# manual edit\n')
        head = self.library.git.head()
        with self.assertRaisesRegex(ValueError, 'uncommitted'):
            self.library.publish(BLUEPRINTS / 'place-held-object', 1, self.demo, self.image)
        with self.assertRaises(ValueError): self.library.freeze(self.root / 'dirty-snapshot')
        self.assertEqual(self.library.git.head(), head)
        self.assertIn('# manual edit', p.read_text())

    def test_git_migration_keeps_legacy_history_and_missing_image(self):
        import shutil
        legacy = ProgramLibrary(self.root / 'legacy-library')
        folder = legacy.folder('place-held-object');version = folder / 'revisions/000001'
        entry = self.library.current('place-held-object')
        for relative in source_files(entry['directory']):
            p = version / relative;p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry['directory'] / relative, p)
        (version / 'evidence/images').mkdir(parents=True)
        shutil.copyfile(self.image, version / 'evidence/images/demonstration.jpg')
        demo = {'image': str(self.root / 'missing.jpg'), 'image_sha256': hashlib.sha256(self.image.read_bytes()).hexdigest(),
                'proves_unattended_code_success': False}
        write_json(version / 'evidence/demonstration.json', demo)
        meta = {k:v for k,v in entry.items() if k not in {'directory', 'storage'}}
        write_json(version / 'source_manifest.json', {'files': source_files(version), **meta})
        write_json(folder / 'current.json', meta)
        (folder / 'evidence').mkdir();(folder / 'evidence/runs.jsonl').write_text('')
        result = legacy.initialize_git()
        self.assertEqual(result['migrated_skills'], ['place-held-object'])
        self.assertFalse((folder / 'revisions').exists())
        current = legacy.current('place-held-object');self.assertEqual(current['code_sha256'], entry['code_sha256'])
        archive = json.loads((folder / 'pre_git_versions.json').read_text())['versions'][0]
        old = legacy.git.command('show', archive['commit']+':'+archive['path']+'/code/run.py').stdout
        self.assertEqual(old, (folder / 'code/run.py').read_text())
        image = Path(json.loads((folder / 'evidence/demonstration.json').read_text())['image'])
        self.assertEqual(image.read_bytes(), self.image.read_bytes())
        legacy.git.require_clean()
        self.assertEqual(legacy.initialize_git()['migrated_skills'], [])

    def test_git_never_adopts_an_unrelated_repository(self):
        import subprocess
        root = self.root / 'other';root.mkdir()
        subprocess.run(['git','-C',str(root),'init','--quiet'],check=True)
        with self.assertRaisesRegex(ValueError, 'unrelated repository'):
            ProgramLibrary(root).initialize_git()
        self.assertFalse((root / 'README.md').exists())

if __name__ == '__main__': unittest.main()
