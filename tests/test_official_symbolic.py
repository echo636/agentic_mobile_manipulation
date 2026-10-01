import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.executors.official_symbolic import OfficialSymbolicBackend, OFFICIAL_PRIMITIVES
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.tools import tool_specs
from manipulation_agent.vision_harness import VisionHarness


class UpstreamErrors(Exception):
    def __init__(self):
        self.exceptions = [SimpleNamespace(reason=SimpleNamespace(name='PRE_CONDITION_ERROR'))]
        super().__init__('PRIVATE_OBJECT /World/private')


class DirectDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.b = OfficialSymbolicBackend.__new__(OfficialSymbolicBackend)
        b = self.b
        b.output = Path(self.tmp.name); b.steps = 0; b.sampling_physics_steps = 0
        b._official_terminated = False; b._inside_primitive = False
        b._primitive_enum = SimpleNamespace(**{p.upper(): p.upper() for p in OFFICIAL_PRIMITIVES})
        b._primitive_error_group = UpstreamErrors
        self.obj = SimpleNamespace(name='PRIVATE_OBJECT')
        b._ground = Mock(return_value=(self.obj, [1,2,3], {'private': True}))
        b._step = lambda action: setattr(b, 'steps', b.steps + 1)
        self.physics = Mock()
        b.og = SimpleNamespace(sim=SimpleNamespace(step_physics=self.physics))
        self.calls = []; self.closed = []
        def native(primitive, *args, attempts):
            self.calls.append((primitive, args, attempts))
            try:
                b.og.sim.step_physics()
                yield 'official_action_1'
                yield 'official_action_2'
            finally:
                self.closed.append(True)
        b.primitives = SimpleNamespace(apply_ref=native)
        for name in ['execute','_navigate','_ideal_grasp','_checked_place_inside','_checked_place_on_top','_ideal_state_action','_ideal_release','_carry_follow','_restore_base_target']:
            setattr(b, name, Mock(side_effect=AssertionError('Custom executor must not run: ' + name)))

    def test_all_fourteen_route_original_enum_object_and_one_attempt(self):
        for primitive in OFFICIAL_PRIMITIVES:
            target = None if primitive == 'release' else {'image_ref':'rgb','point':[.4,.5]}
            result = self.b.execute_visual(primitive, target, 10)
            self.assertEqual(self.calls[-1], (primitive.upper(), () if target is None else (self.obj,), 1))
            self.assertEqual(result['steps'], 2)
            self.assertEqual(result['internal_physics_ticks'], 1)
            self.assertIs(self.b.og.sim.step_physics, self.physics)
        self.assertEqual(len(self.closed), 14)

    def test_upstream_missing_planner_error_preserved_without_fallback(self):
        def missing(*args, **kwargs):
            raise AttributeError('NoneType planner PRIVATE_OBJECT')
            yield
        self.b.primitives.apply_ref = missing
        with self.assertRaises(SkillError):
            self.b.execute_visual('navigate_to', {'image_ref':'rgb','point':[.4,.5]}, 10)
        record = json.loads((self.b.output/'official_primitives.jsonl').read_text())
        self.assertEqual(record['error_type'], 'AttributeError')
        self.assertEqual(self.b.steps, 0)
        self.assertIs(self.b.og.sim.step_physics, self.physics)

    def test_step_budget_preserves_partial_progress_and_closes_generator(self):
        with self.assertRaisesRegex(SkillError, 'step budget'):
            self.b.execute_visual('grasp', {'image_ref':'rgb','point':[.4,.5]}, 1)
        self.assertEqual(self.b.steps, 1); self.assertEqual(self.closed, [True])
        self.assertIs(self.b.og.sim.step_physics, self.physics)

    def test_upstream_precondition_error_not_retried(self):
        def reject(*args, **kwargs):
            raise UpstreamErrors()
            yield
        native = Mock(side_effect=reject); self.b.primitives.apply_ref = native
        with self.assertRaises(SkillError) as error:
            self.b.execute_visual('open', {'image_ref':'rgb','point':[.4,.5]}, 10)
        self.assertEqual(error.exception.code, 'pre_condition_error')
        self.assertEqual(native.call_count, 1)

    def test_internal_physics_budget_restores_original_function(self):
        def loop(*args, **kwargs):
            for _ in range(20): self.b.og.sim.step_physics()
            yield 0
        self.b.primitives.apply_ref = loop
        with self.assertRaises(SkillError) as error:
            self.b.execute_visual('place_inside', {'image_ref':'rgb','point':[.4,.5]}, 2)
        self.assertEqual(error.exception.code, 'sampling_budget_exhausted')
        self.assertEqual(self.physics.call_count, 8)
        self.assertIs(self.b.og.sim.step_physics, self.physics)

    def test_custom_arguments_and_targetless_manipulation_rejected(self):
        for primitive, target, options in [('wait', None, {}), ('look',None,{}), ('grasp',None,{}),
                ('release',{'image_ref':'rgb','point':[.5,.5]},{}),
                ('place_on_top',{'image_ref':'rgb','point':[.5,.5]}, {'placement_yaw_degrees': 30})]:
            with self.subTest(primitive=primitive), self.assertRaises(SkillError):
                self.b.execute_visual(primitive, target, 10, **options)
        self.assertEqual(self.calls, [])


class OfficialBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'run'; self.rec = Recorder(self.path, {'backend':'mock','observation_mode':'rgb_only'})
        self.b = MockRGBBackend(self.path)
        self.h = VisionHarness(self.b, self.rec, Budget(), profile='official')

    def args(self, primitive='toggle_on'):
        return {'primitive':primitive,'revision':self.h.revision,
                'target':{'image_ref':self.h.snapshot['images'][0]['image_ref'],'point':[.5,.5]}}

    def test_catalog_isolation_and_no_custom_options(self):
        self.assertEqual(set(self.h.catalog), {'observe','act','finish','start_observation','get_observation','cancel_observation'})
        schema = self.h.catalog['act']['inputSchema']
        self.assertEqual(set(schema['properties']), {'primitive','target','revision'})
        self.assertEqual(len(schema['properties']['primitive']['enum']), 14)
        for profile in ['minimal','skills','workflow']:
            act = next(t for t in tool_specs(profile) if t['name']=='act')
            self.assertNotIn('wipe', act['inputSchema']['properties']['primitive']['enum'])
            self.assertIn('wait_seconds', act['inputSchema']['properties'])
        result = self.h.call('act', {**self.args(),'wait_seconds':None}, 'bad')
        self.assertFalse(result['ok']); self.assertEqual(self.b.steps, 0)

    def test_current_images_idempotency_and_fresh_error_observation(self):
        args = self.args('wipe'); self.b.fail_next = True
        result = self.h.call('act',args,'first')
        self.assertFalse(result['ok']); self.assertNotIn('PRIVATE_OBJECT',json.dumps(result))
        self.assertEqual(len(result['observation']['images']), 4)
        self.assertEqual(result['observation']['revision'], 1)
        self.assertEqual(self.h.call('act',args,'first'),result)
        self.assertEqual(self.b.steps,1)
        self.assertEqual(self.h.call('act',args,'stale')['error']['code'],'stale_observation')

    def test_agent_claim_cannot_set_task_success_or_read_evaluator(self):
        result=self.h.call('finish', {'outcome':'achieved','reason':'unsupported claim'}, 'finish')
        self.assertTrue(result['closed']); self.assertNotIn('task_success', result)
        self.assertFalse(self.rec.run['task_success'])


if __name__ == '__main__': unittest.main()
