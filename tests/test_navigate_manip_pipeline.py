"""Offline end-to-end recipe selection, editing and independent admission checks."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.exploration_frontend import tool_specs as exploration_specs
from manipulation_agent.exploration_session import ExplorationSession, prepare_session
from manipulation_agent.exploration_qualification import qualification
from manipulation_agent.manip_runtime import ManipRecipes, RecipeContext, checked_invocation
from manipulation_agent.oracle_contracts import tool_specs
from manipulation_agent.oracle_harness import OracleWaypointHarness
from manipulation_agent.records import Recorder
from manipulation_agent.tools import tool_specs as profile_specs


CODE = '''def Manip(ctx, *, recipe, targets, parameters, constraints, revision=None):
    reply=ctx.call("wait",steps=parameters["steps"])
    return {"ok":reply["ok"],"recipe":recipe}
def main(ctx):
    ctx.call("wait",steps=2)
    return {"ok":True}
'''


def fixture_source(folder):
    folder.mkdir(parents=True)
    (folder/'controller.py').write_text(CODE)
    (folder/'SKILL.md').write_text('CPU fixture; not physical evidence.\n')
    (folder/'spec.json').write_text(json.dumps({'recipe': 'fixture_pick', 'applicability': 'CPU fixture only',
        'recipe_contract': {'targets': {'object': {'required': True, 'type': 'EntityRef'}},
            'parameters': {'type': 'object', 'properties': {'steps': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'default': 2}}, 'additionalProperties': False},
            'constraints': {'type': 'object', 'properties': {
                'allow_base_motion': {'type': 'boolean', 'default': False},
                'allow_trunk_motion': {'type': 'boolean', 'default': True}}, 'additionalProperties': False}}}))


class Backend:
    steps = 0

    def __init__(self):
        self.moves = []

    def observe(self):
        return {'observation_mode': 'oracle_3d_waypoints', 'capture': {'capture_id': 'fixture', 'sim_step': self.steps},
                'images': [], 'oracle_3d': {'frame': 'robot_base', 'units': {}, 'object_scope': 'CPU fixture',
                                           'omitted_objects': 0, 'objects': [], 'robot': {'arms': {
                    'left': {'held_object': None}, 'right': {'held_object': None}}}}}

    def provenance(self): return {'fixture_only': True}
    def control_contract(self): return {'transport_policy': 'native_only'}
    def execute_motor(self, name, **arguments):
        self.moves.append((name, arguments)); self.steps += arguments.get('steps', 1)
        return {'reached': True}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'
        fixture_source(self.source/'pick')

    def args(self, **extras):
        return {'recipe': 'fixture_pick', 'targets': {'object': {'task_name': 'bottle'}},
                'revision': 0, 'allow_draft': True, **extras}

    def test_oracle_selects_recipe_and_uses_frozen_code_on_owner_thread(self):
        backend = Backend()
        h = OracleWaypointHarness(backend, Recorder(self.root/'run', {'task': 'fixture'}),
                                  Budget(wall_seconds=None), manip_root=self.source, experience_root=self.root/'no-experience')
        self.assertEqual(h.initialize()['manip_recipes'][0]['recipe'], 'fixture_pick')
        self.assertEqual(h.call('read_manip_recipe', {'recipe': 'fixture_pick'}, 'read')['contract']['parameters']['properties']['steps']['default'], 2)
        (self.source/'pick/controller.py').write_text('broken source after snapshot')
        result = h.call('manip', self.args(), 'one')
        self.assertTrue(result['ok'], result)
        self.assertEqual(backend.moves, [('wait', {'steps': 2})])
        self.assertEqual(result['observation']['revision'], 1)
        self.assertEqual(h.call('manip', self.args(), 'one'), result)
        self.assertEqual(len(backend.moves), 1)
        self.assertFalse(h.call('manip', self.args(), 'stale')['ok'])
        self.assertEqual(len(backend.moves), 1)

    def test_typed_contract_rejects_before_calls_and_always_marks_workspace_draft(self):
        entry = ManipRecipes(self.source).get('fixture_pick')
        for update in ({'parameters': {'steps': True}}, {'parameters': {'steps': 11}},
                       {'parameters': {'extra': 1}}, {'allow_draft': False},
                       {'targets': {'object': {'id': 'cached-id'}}}):
            with self.subTest(update=update), self.assertRaises(SkillError):
                checked_invocation(entry, self.args(**update))
        self.assertFalse(checked_invocation(entry, self.args())['constraints']['allow_base_motion'])

    def test_host_permissions_and_stop_after_failure_survive_recipe_edits(self):
        calls = []
        def send(name, args, ident):
            calls.append(name)
            return {'ok': False}
        ctx = RecipeContext(send, lambda: Backend().observe(), {'allow_base_motion': False, 'allow_trunk_motion': False})
        with self.assertRaises(SkillError):ctx.call('move_joints', group='trunk')
        with self.assertRaises(RuntimeError):ctx.call('wait', steps=1)
        self.assertEqual(calls, [])
        ctx = RecipeContext(send, lambda: Backend().observe(), {'allow_base_motion': True, 'allow_trunk_motion': True})
        held = Backend().observe(); held['oracle_3d']['robot']['arms']['right']['held_object'] = 'bottle'
        ctx._snapshot = lambda: held
        with self.assertRaises(SkillError):ctx.call('navigate_to')
        self.assertEqual(calls, [])
        ctx = RecipeContext(send, lambda: Backend().observe(), {'allow_base_motion': False, 'allow_trunk_motion': True})
        with self.assertRaises(RuntimeError):ctx.call('grasp')
        with self.assertRaises(RuntimeError):ctx.call('release')
        self.assertEqual(calls, ['grasp'])

    def test_recipe_baseline_notes_do_not_overwrite_model_working_notes(self):
        calls = []
        folder = self.root/'recipe-notes'
        def send(*args):calls.append(args);raise AssertionError('Recipe notes leaked to model task_notes')
        args = (send, lambda: {'revision': 3}, {'allow_base_motion': False, 'allow_trunk_motion': True})
        ctx = RecipeContext(*args, notes_path=folder)
        ctx.call('task_notes', operation='write', content='recipe baseline', expected_version=0)
        restored = RecipeContext(*args, notes_path=folder)
        self.assertEqual(restored.call('task_notes', operation='read', content=None, expected_version=None)['notes']['content'], 'recipe baseline')
        self.assertEqual(calls, [])

    def test_rgb_catalog_stays_separate_and_exploration_discovers_recipes(self):
        for profile in ('skills', 'minimal', 'workflow'):
            self.assertNotIn('manip', {t['name'] for t in profile_specs(profile)})
        names = [t['name'] for t in exploration_specs(tool_specs())]
        self.assertEqual(names.count('manip'), 1)
        self.assertIn('read_manip_recipe', names)


class Episode:
    tools = tool_specs()
    def __init__(self, spec): self.spec, self.revision, self.calls = spec, 0, []
    def call(self, name, arguments, ident):
        self.calls.append(name)
        if name == 'finish':return {'ok': True, 'closed': True}
        if name == 'wait':self.revision += 1
        return {'ok': True, 'observation': {**Backend().observe(), 'revision': self.revision}}
    def collect(self):return {'official_task_success': True, 'run_dir': '/fixture-runs/'+self.spec['key']}


class Factory:
    def start(self, spec, **kw):return Episode(spec)


def runner(bundle, digest, output, call, allowed, **kw):
    if 'invocation' in kw:
        call('wait', {'steps': kw['invocation']['parameters']['steps']}, 'child')
    return {'clean': True, 'controller_result': {'ok': True}, 'unattended': True,
            'no_live_agent': True, 'calls': []}


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'session'
        self.config = {'task': 'fixture', 'qualification': {'required_successes': 2, 'metric': 'official_task_success', 'distinct_instances': True},
            'exploration': [{'key': 'dev', 'identity': {'task': 'fixture', 'instance': 1, 'seed': 0}},
                            {'key': 'repeat1', 'identity': {'task': 'fixture', 'instance': 2, 'seed': 0}},
                            {'key': 'repeat2', 'identity': {'task': 'fixture', 'instance': 3, 'seed': 0}}],
            'formal': [{'key': 'formal', 'identity': {'task': 'fixture', 'instance': 4, 'seed': 0}}]}
        prepare_session(self.root, self.config, [])
        fixture_source(self.root/'workspace/code/pick')
        self.session = ExplorationSession(self.root, Factory(), runner=runner)
        self.addCleanup(self.session.close)

    def test_select_edit_and_manip_on_same_episode_is_not_qualification(self):
        self.session.dispatch('start_episode', {})
        self.assertEqual(self.session.dispatch('list_manip_recipes', {})['recipes'][0]['project'], 'pick')
        args = {'recipe': 'fixture_pick', 'targets': {'object': {'task_name': 'bottle'}}, 'revision': 0, 'allow_draft': True}
        first = self.session.dispatch('manip', args)
        self.assertTrue(first['ok'])
        current = self.session.dispatch('read_workspace_file', {'path': 'code/pick/controller.py'})
        self.session.dispatch('patch_workspace_file', {'path': current['path'], 'expected_sha256': current['sha256'],
                              'old_text': 'reply=ctx.call', 'new_text': '# changed hypothesis\n    reply=ctx.call'})
        args['revision'] = 1
        second = self.session.dispatch('manip', args)
        self.assertNotEqual(first['source_sha256'], second['source_sha256'])
        self.assertEqual(self.session.state['exploration_used'], 1)
        self.assertEqual(self.session.state['rehearsals'], [])

    def test_two_independent_successes_gate_submission_and_edits_reset_counts(self):
        bundle = self.session.dispatch('register', {'project': 'pick'})['bundle']
        self.session.dispatch('rehearse', {'bundle': bundle})
        with self.assertRaisesRegex(ValueError, 'independent rehearsal qualification'):
            self.session.dispatch('submit', {'bundle': bundle})
        self.assertEqual(self.session.status()['bundles'][0]['qualification']['independent_successes'], 1)
        self.session.dispatch('rehearse', {'bundle': bundle})
        self.assertTrue(self.session.status()['bundles'][0]['rehearsal_qualified'])
        code = self.root/'workspace/code/pick/controller.py';code.write_text(code.read_text()+'\n# revised\n')
        new = self.session.dispatch('register', {'project': 'pick'})['bundle']
        self.assertNotEqual(new, bundle)
        self.assertEqual(qualification(self.config, new, self.session.state['rehearsals'])['independent_successes'], 0)
        self.session.dispatch('submit', {'bundle': bundle})

    def test_clean_self_claim_and_duplicate_evidence_do_not_count(self):
        bundle = self.session.dispatch('register', {'project': 'pick'})['bundle']
        row = self.session.dispatch('rehearse', {'bundle': bundle})
        records = [row, copy.deepcopy(row)]
        self.assertEqual(qualification(self.config, bundle, records)['independent_successes'], 1)
        records[0]['qualification']['passed'] = False
        records[1]['qualification']['passed'] = False
        self.assertFalse(qualification(self.config, bundle, records)['qualified'])

    def test_clean_controller_true_without_independent_success_is_rejected(self):
        bundle = self.session.dispatch('register', {'project': 'pick'})['bundle']
        with patch.object(Episode, 'collect', return_value={'official_task_success': False, 'run_dir': '/fixture-failure'}):
            row = self.session.dispatch('rehearse', {'bundle': bundle})
        self.assertFalse(row['qualification']['passed'])
        self.assertEqual(qualification(self.config, bundle, self.session.state['rehearsals'])['independent_successes'], 0)

    def test_actual_offline_sandbox_invokes_manip_with_supplied_parameters(self):
        from manipulation_agent.exploration_runner import run_controller
        self.session.runner = run_controller
        self.session.dispatch('start_episode', {})
        result = self.session.dispatch('manip', {'recipe': 'fixture_pick', 'targets': {'object': {'task_name': 'bottle'}},
                                               'parameters': {'steps': 3}, 'revision': 0, 'allow_draft': True})
        self.assertTrue(result['ok'], result)
        self.assertEqual([c['tool'] for c in result['execution']['calls']], ['wait', 'manip'])
        self.assertEqual(self.session.episode.calls.count('wait'), 1)


if __name__ == '__main__':unittest.main()
