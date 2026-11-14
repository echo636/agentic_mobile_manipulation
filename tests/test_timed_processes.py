from pathlib import Path
import tempfile
import unittest

from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.executors.timed_processes import TimedProcesses, validate_timed_processes
from manipulation_agent.records import Recorder
from manipulation_agent.references.full_workflow_reference import validate_workflow_plan
from manipulation_agent.runtime.robot_clock import charge_base_motion, clocked_action, clocked_leaf
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.tasks.custom_tasks import validate_spec
from manipulation_agent.tools.action import act


class _MockObj:
    def __init__(self, name, *, is_open=False, toggled_on=False):
        self.name = name
        self.prim_path = f'/World/scene_0/{name}'
        self.mock_states = {'Open': is_open, 'ToggledOn': toggled_on, 'Cooked': False, 'Burnt': False}
        self.mock_relations = {'Inside': set(), 'OnTop': set()}


class _MockBackend:
    def __init__(self, spec, output):
        self.output = Path(output)
        self.steps = 0
        self.held = None
        self.mock_objects = {
            name: _MockObj(name) for name in spec['roles'].values()
        }
        self.timed = TimedProcesses.from_spec(spec)

    def _get_held(self):
        return self.held

    def run_action(self, primitive, *, travel_m=0.0, wait_s=None, selected=None, fail=False, body=None):
        snap = self.timed.before_action(self)

        def _exec():
            if travel_m > 0:
                charge_base_motion(self, [(0.0, 0.0), (travel_m, 0.0)], (0.0, 0.0, 0.0, 1.0), 0.0)
            if primitive == 'wait':
                self.steps += 5
                return {'primitive': 'wait', 'steps': 5, 'sim_seconds': float(wait_s or 5.0)}
            if primitive in ('navigate_to', 'look'):
                self.steps += 1
                return {'primitive': primitive}

            def _leaf():
                self.steps += 1
                if body is not None:
                    body()
                if fail:
                    raise SkillError('grasp_failed', 'Could not grasp target')
                prim = f'/World/scene_0/{selected}' if selected else ''
                return {'primitive': primitive, 'private_grounding': {'selected_object_prim': prim}}

            return clocked_leaf(self, _leaf)

        try:
            opts = {'seconds': wait_s} if wait_s is not None else {}
            details = clocked_action(self, primitive, opts, _exec)
            pub = self.timed.after_action(self, snap, primitive, details=details, error=None)
            if pub is not None:
                details = {**details, 'public_process': pub}
            return details
        except SkillError as exc:
            self.timed.after_action(self, snap, primitive, details=None, error=exc)
            raise


def _sample_spec():
    return {
        'schema_version': 2,
        'id': 'lunch_test',
        'instance_id': 'lunch_test_i0',
        'official_submission_eligible': False,
        'public_instruction': 'Heat two hotdogs and wash the cup.',
        'public_context': ['Heat both hotdogs and wash the cup.'],
        'roles': {
            'microwave': 'microwave_0',
            'dishwasher': 'dishwasher_0',
            'hotdog1': 'hotdog_1',
            'hotdog2': 'hotdog_2',
            'cup1': 'cup_1',
        },
        'timed_processes': [
            {
                'id': 'microwave_heat',
                'appliance': 'microwave',
                'items': ['hotdog1', 'hotdog2'],
                'containment': 'Inside',
                'require_closed': True,
                'require_toggled_on': True,
                'duration_seconds': 60.0,
                'set_object_state': 'Cooked',
                'running_message': 'The microwave is running; the food inside is still heating.',
                'completed_message': 'The microwave chime sounded: heating is complete.',
                'stopped_early_message': 'The microwave stopped before heating finished.',
            },
            {
                'id': 'wash_dishes',
                'appliance': 'dishwasher',
                'items': ['cup1'],
                'containment': 'Inside',
                'require_closed': True,
                'require_toggled_on': True,
                'duration_seconds': 40.0,
                'running_message': 'The dishwasher is running.',
                'completed_message': 'The dishwasher finished its cycle.',
                'stopped_early_message': 'The dishwasher stopped before finishing.',
            },
        ],
        'initial_assertions': {'raw1': {'not': 'hotdog1_cooked'}},
        'requirements': [{
            'id': 'all_done',
            'text': 'Heat both hotdogs and wash the cup.',
            'terminal': ['all_done'],
        }],
        'evaluation': {
            'predicates': {
                'hotdog1_cooked': {'object': 'hotdog1', 'state': 'Cooked'},
                'hotdog2_cooked': {'object': 'hotdog2', 'state': 'Cooked'},
                'cup1_washed': {'object': 'cup1', 'state': 'ProcessComplete', 'process': 'wash_dishes'},
            },
            'terminal': {
                'all_done': {'all': ['hotdog1_cooked', 'hotdog2_cooked', 'cup1_washed']},
            },
        },
        'acceptance_cases': {
            'reference_orders': ['order_a', 'order_b'],
            'physical_negative_cases': ['neg_a'],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 50,
        },
    }


class TimedProcessesTests(unittest.TestCase):
    def test_spec_validation_accepts_valid_and_rejects_bad_references(self):
        spec = _sample_spec()
        res = validate_spec(spec)
        self.assertEqual(res['status'], 'passed')

        bad_proc = _sample_spec()
        bad_proc['evaluation']['predicates']['cup1_washed'] = {
            'object': 'hotdog1', 'state': 'ProcessComplete', 'process': 'wash_dishes'
        }
        with self.assertRaisesRegex(ValueError, 'ProcessComplete'):
            validate_spec(bad_proc)

        bad_dur = _sample_spec()
        bad_dur['timed_processes'][0]['duration_seconds'] = 0
        with self.assertRaisesRegex(ValueError, 'duration_seconds'):
            validate_timed_processes(bad_dur)

    def test_parallel_work_advances_batch_heating_and_syncs_cooked(self):
        spec = _sample_spec()
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(spec, tmp)
            mw = b.mock_objects['microwave_0']
            h1 = b.mock_objects['hotdog_1']
            h2 = b.mock_objects['hotdog_2']
            h1.mock_relations['Inside'].add(mw)
            h2.mock_relations['Inside'].add(mw)
            mw.mock_states['Open'] = False

            # Turning on the microwave does NOT count the pre-toggle walk/button time as heating.
            out = b.run_action('toggle_on', travel_m=5.0, selected='microwave_0',
                               body=lambda: mw.mock_states.__setitem__('ToggledOn', True))
            self.assertEqual(b.timed.elapsed['microwave_heat']['hotdog1'], 0.0)
            self.assertIn('still heating', out['public_process']['message'])
            self.assertFalse(h1.mock_states['Cooked'])

            # Doing two manipulations with 10 m travel each elsewhere: each costs 20s walk + 10s manip = 30s!
            b.run_action('grasp', travel_m=10.0, selected='cup_1')
            self.assertAlmostEqual(b.timed.elapsed['microwave_heat']['hotdog1'], 30.0)
            self.assertAlmostEqual(b.timed.elapsed['microwave_heat']['hotdog2'], 30.0)
            self.assertFalse(h1.mock_states['Cooked'])

            out2 = b.run_action('place_on_top', travel_m=10.0, selected='dishwasher_0')
            self.assertAlmostEqual(b.timed.elapsed['microwave_heat']['hotdog1'], 60.0)
            self.assertTrue(b.timed.is_completed('microwave_heat', 'hotdog1'))
            self.assertTrue(b.timed.is_completed('microwave_heat', 'hotdog2'))
            self.assertTrue(h1.mock_states['Cooked'])
            self.assertTrue(h2.mock_states['Cooked'])
            self.assertFalse(h1.mock_states['Burnt'])
            self.assertIn('chime sounded', out2['public_process']['message'])

    def test_open_door_or_early_stop_gates_progress_and_emits_cue(self):
        spec = _sample_spec()
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(spec, tmp)
            mw = b.mock_objects['microwave_0']
            h1 = b.mock_objects['hotdog_1']
            h1.mock_relations['Inside'].add(mw)
            mw.mock_states['Open'] = False
            mw.mock_states['ToggledOn'] = True

            # Wait 25s -> 25s accumulated
            out = b.run_action('wait', wait_s=25.0)
            self.assertAlmostEqual(b.timed.elapsed['microwave_heat']['hotdog1'], 25.0)
            self.assertIn('still heating', out['public_process']['message'])

            # Walk 5m (10s) to microwave and open door early -> travel counts (+10s = 35s), manipulation does not; emits stopped_early
            out2 = b.run_action('open', travel_m=5.0, selected='microwave_0',
                                body=lambda: mw.mock_states.__setitem__('Open', True))
            self.assertAlmostEqual(b.timed.elapsed['microwave_heat']['hotdog1'], 35.0)
            self.assertIn('stopped before heating finished', out2['public_process']['message'])

            # While door is open, waiting does not advance heating
            b.run_action('wait', wait_s=50.0)
            self.assertAlmostEqual(b.timed.elapsed['microwave_heat']['hotdog1'], 35.0)
            self.assertFalse(b.timed.is_completed('microwave_heat', 'hotdog1'))

    def test_completion_during_failed_action_delivers_chime_on_next_success(self):
        spec = _sample_spec()
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(spec, tmp)
            dw = b.mock_objects['dishwasher_0']
            cup = b.mock_objects['cup_1']
            cup.mock_relations['Inside'].add(dw)
            dw.mock_states['Open'] = False
            dw.mock_states['ToggledOn'] = True

            # Failed grasp after 15m walk (30s travel + 10s attempted leaf = 40s >= 40s duration!)
            with self.assertRaises(SkillError):
                b.run_action('grasp', travel_m=15.0, selected='hotdog_1', fail=True)
            self.assertTrue(b.timed.is_completed('wash_dishes', 'cup1'))

            # Next successful action receives the queued completion chime
            out = b.run_action('navigate_to', travel_m=1.0)
            self.assertIn('dishwasher finished its cycle', out['public_process']['message'])

    def test_act_wait_seconds_allows_up_to_120_and_vision_harness_exposes_process(self):
        class _StubCtx:
            def __init__(self):
                self.captured = None

            def perform(self, primitive, target, revision, **options):
                self.captured = (primitive, target, revision, options)
                return {'ok': True}

        ctx = _StubCtx()
        act(ctx, 'wait', None, 0, wait_seconds=120)
        self.assertEqual(ctx.captured[3]['seconds'], 120)

        class _HarnessBackend:
            steps = 0
            local_manipulation = True

            def observe(self):
                return {
                    'observation_mode': 'rgb_only',
                    'images': [{
                        'image_ref': 'img0',
                        'view': 'front',
                        'width': 64,
                        'height': 64,
                        'mime_type': 'image/png',
                        'sha256': '0' * 64,
                    }],
                }

            def provenance(self):
                return {'backend': 'stub'}

            def execute_visual(self, primitive, target, limit, **kwargs):
                return {
                    'primitive': primitive,
                    'public_process': {
                        'messages': ['The microwave chime sounded: heating is complete.'],
                        'message': 'The microwave chime sounded: heating is complete.',
                        'any_process_running': False,
                    },
                }

        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(Path(tmp) / 'run', {'task': 't'})
            vh = VisionHarness(_HarnessBackend(), rec, Budget(wall_seconds=None),
                               profile='basic-skills', agent_step_limit=10)
            vh.initialize()
            res = vh.perform('wait', None, 0, seconds=60.0)
            self.assertEqual(res['effect']['process']['message'],
                             'The microwave chime sounded: heating is complete.')

    def test_workflow_plan_allows_strategy_variants_and_60s_wait_for(self):
        spec = _sample_spec()
        spec['acceptance_cases'] = {
            'reference_orders': ['habit_seq', 'optimal_parallel'],
            'physical_negative_cases': ['omit_heat'],
            'fresh_restore_repetitions': 2,
            'max_reference_action_attempts': 100,
        }
        spec['reference_plan'] = {
            'kind': 'full_workflow',
            'allow_strategy_variants': True,
            'phases': {
                'start_heat': {'actions': [{'primitive': 'toggle_on', 'role': 'microwave'}], 'after': []},
                'idle_wait': {
                    'actions': [{'primitive': 'wait_for', 'condition': 'hotdog1_cooked', 'seconds': 60.0, 'attempts': 3}],
                    'after': ['start_heat'],
                },
                'parallel_prep': {'actions': [{'primitive': 'grasp', 'role': 'cup1'}], 'after': ['start_heat']},
                'finish_heat': {'actions': [{'primitive': 'toggle_off', 'role': 'microwave'}], 'after': ['start_heat']},
            },
            'orders': {
                'habit_seq': ['start_heat', 'idle_wait', 'parallel_prep', 'finish_heat'],
                'optimal_parallel': ['start_heat', 'parallel_prep', 'finish_heat'],
            },
            'negative_omissions': {'omit_heat': 'finish_heat'},
        }
        info = validate_workflow_plan(spec)
        self.assertEqual(info['phase_count'], 4)


if __name__ == '__main__':
    unittest.main()
