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


def _stove_spec():
    """Cooktop process with an overcook window (v3): done after 60 s, burns 30 s later if still heating."""
    spec = _sample_spec()
    spec['roles'].update({'stove': 'stove_0', 'steak': 'steak_0'})
    spec['timed_processes'].append({
        'id': 'sear_steak',
        'appliance': 'stove',
        'items': ['steak'],
        'containment': 'OnTop',
        'require_closed': False,
        'require_toggled_on': True,
        'duration_seconds': 60.0,
        'set_object_state': 'Cooked',
        'overcook_after_seconds': 30.0,
        'running_message': 'The steak is sizzling on the cooktop.',
        'completed_message': 'The steak on the cooktop is done.',
        'stopped_early_message': 'The cooktop stopped before the steak was done.',
        'overcooked_message': 'A burning smell: the steak on the cooktop has burnt.',
    })
    spec['evaluation']['predicates'].update({
        'steak_seared': {'object': 'steak', 'state': 'ProcessComplete', 'process': 'sear_steak'},
        'steak_overcooked': {'object': 'steak', 'state': 'ProcessOvercooked', 'process': 'sear_steak'},
    })
    spec['evaluation']['terminal']['all_done'] = {
        'all': ['hotdog1_cooked', 'hotdog2_cooked', 'cup1_washed', 'steak_seared', {'not': 'steak_overcooked'}]}
    return spec


def _start_stove(b):
    stove = b.mock_objects['stove_0']
    steak = b.mock_objects['steak_0']
    steak.mock_relations['OnTop'].add(stove)
    b.run_action('toggle_on', selected='stove_0', body=lambda: stove.mock_states.__setitem__('ToggledOn', True))
    return stove, steak


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
            # v2 public timing: the run's own total and remaining time, like an appliance panel.
            self.assertIn('takes about 60 s in total; about 60 s remain', out['public_process']['message'])
            self.assertEqual(out['public_process']['running_remaining_s'], [60.0])

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
            self.assertIn('About 35 s remain.', out['public_process']['message'])
            self.assertNotIn('in total', out['public_process']['message'])

            # Walk 5m (10s) to microwave and open door early -> travel counts (+10s = 35s), manipulation does not; emits stopped_early
            out2 = b.run_action('open', travel_m=5.0, selected='microwave_0',
                                body=lambda: mw.mock_states.__setitem__('Open', True))
            self.assertAlmostEqual(b.timed.elapsed['microwave_heat']['hotdog1'], 35.0)
            self.assertIn('stopped before heating finished', out2['public_process']['message'])
            self.assertIn('About 35 s of 60 s were completed.', out2['public_process']['message'])
            self.assertNotIn('running_remaining_s', out2['public_process'])

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


    def test_overcook_window_validation(self):
        self.assertEqual(validate_spec(_stove_spec())['status'], 'passed')
        for mutate, msg in (
                (lambda r: r.__setitem__('overcook_after_seconds', 0), 'overcook_after_seconds'),
                (lambda r: r.pop('overcooked_message'), 'overcooked_message'),
                (lambda r: r.pop('overcook_after_seconds'), 'requires overcook_after_seconds'),
        ):
            spec = _stove_spec()
            mutate(spec['timed_processes'][2])
            with self.assertRaisesRegex(ValueError, msg):
                validate_timed_processes(spec)
        spec = _stove_spec()
        spec['evaluation']['predicates']['steak_overcooked'] = {
            'object': 'cup1', 'state': 'ProcessOvercooked', 'process': 'wash_dishes'}
        with self.assertRaisesRegex(ValueError, 'ProcessOvercooked'):
            validate_spec(spec)
        # Tasks without the field behave as before: nothing ever overcooks.
        b = _MockBackend(_sample_spec(), tempfile.mkdtemp())
        self.assertEqual(b.timed.rules[0]['overcook_after_seconds'], None)

    def test_overcook_burns_when_left_heating_and_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(_stove_spec(), tmp)
            stove, steak = _start_stove(b)
            # 25 m walk + manipulation elsewhere twice: 2 x (50 s + 10 s) = 120 s > 60 + 30
            out = b.run_action('grasp', travel_m=25.0, selected='cup_1')
            self.assertTrue(b.timed.is_completed('sear_steak', 'steak'))
            self.assertIn('The steak on the cooktop is done. If it keeps heating, it will burn in about 30 s.',
                          out['public_process']['message'])
            self.assertFalse(b.timed.is_overcooked('sear_steak', 'steak'))
            out = b.run_action('place_on_top', travel_m=25.0, selected='dishwasher_0')
            self.assertTrue(b.timed.is_overcooked('sear_steak', 'steak'))
            self.assertTrue(steak.mock_states['Burnt'])
            self.assertIn('the steak on the cooktop has burnt', out['public_process']['message'])
            # Burnt is permanent, even after switching off.
            b.run_action('toggle_off', selected='stove_0', body=lambda: stove.mock_states.__setitem__('ToggledOn', False))
            self.assertTrue(b.timed.is_overcooked('sear_steak', 'steak'))
            self.assertTrue(b.timed.summary(b)['processes']['sear_steak']['overcooked']['steak'])

    def test_overcook_avoided_by_switching_off_or_removing_in_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(_stove_spec(), tmp)
            stove, steak = _start_stove(b)
            b.run_action('wait', wait_s=65.0)
            self.assertTrue(b.timed.is_completed('sear_steak', 'steak'))
            out = b.run_action('wait', wait_s=10.0)
            self.assertIn('It is still heating and will burn in about 15 s.', out['public_process']['message'])
            b.run_action('toggle_off', selected='stove_0', body=lambda: stove.mock_states.__setitem__('ToggledOn', False))
            b.run_action('wait', wait_s=120.0)
            self.assertFalse(b.timed.is_overcooked('sear_steak', 'steak'))
            self.assertTrue(steak.mock_states['Cooked'])
            self.assertFalse(steak.mock_states['Burnt'])

        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(_stove_spec(), tmp)
            stove, steak = _start_stove(b)
            b.run_action('wait', wait_s=70.0)
            # Lift the steak off the still-lit cooktop: holding it stops the heating.
            b.held = steak
            b.run_action('grasp', selected='steak_0')
            b.run_action('navigate_to', travel_m=60.0)
            self.assertFalse(b.timed.is_overcooked('sear_steak', 'steak'))

    def test_switch_off_when_complete_validation(self):
        spec = _sample_spec()
        spec['timed_processes'][1]['switch_off_when_complete'] = True
        self.assertEqual(validate_spec(spec)['status'], 'passed')
        for mutate, msg in (
                (lambda r: r.__setitem__('switch_off_when_complete', 'yes'), 'must be boolean'),
                (lambda r: r.update(overcook_after_seconds=30.0, overcooked_message='Burnt.'), 'cannot both'),
                (lambda r: r.__setitem__('require_toggled_on', False), 'requires require_toggled_on'),
        ):
            spec = _sample_spec()
            spec['timed_processes'][1]['switch_off_when_complete'] = True
            mutate(spec['timed_processes'][1])
            with self.assertRaisesRegex(ValueError, msg):
                validate_timed_processes(spec)
        # Without the field nothing switches itself off (T1-T7 behaviour).
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(_sample_spec(), tmp)
            dw = b.mock_objects['dishwasher_0']
            b.mock_objects['cup_1'].mock_relations['Inside'].add(dw)
            dw.mock_states['ToggledOn'] = True
            b.run_action('wait', wait_s=60.0)
            self.assertTrue(b.timed.is_completed('wash_dishes', 'cup1'))
            self.assertTrue(dw.mock_states['ToggledOn'])

    def test_switch_off_when_complete_stops_appliance_after_the_running_load(self):
        spec = _sample_spec()
        spec['roles']['cup2'] = 'cup_2'
        rule = spec['timed_processes'][1]
        rule['items'] = ['cup1', 'cup2']
        rule['switch_off_when_complete'] = True
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(spec, tmp)
            dw = b.mock_objects['dishwasher_0']
            cup1, cup2 = b.mock_objects['cup_1'], b.mock_objects['cup_2']
            cup1.mock_relations['Inside'].add(dw)          # cup2 forgotten outside
            b.run_action('toggle_on', selected='dishwasher_0',
                         body=lambda: dw.mock_states.__setitem__('ToggledOn', True))
            out = b.run_action('wait', wait_s=40.0)
            self.assertTrue(b.timed.is_completed('wash_dishes', 'cup1'))
            self.assertFalse(dw.mock_states['ToggledOn'])
            self.assertIn('dishwasher finished its cycle', out['public_process']['message'])
            self.assertEqual(b.timed.summary(b)['auto_switched_off'][0]['process'], 'wash_dishes')
            # The forgotten cup needs a second run; the finished one is not re-counted.
            cup2.mock_relations['Inside'].add(dw)
            b.run_action('toggle_on', selected='dishwasher_0',
                         body=lambda: dw.mock_states.__setitem__('ToggledOn', True))
            b.run_action('wait', wait_s=20.0)
            self.assertTrue(dw.mock_states['ToggledOn'])
            self.assertFalse(b.timed.is_completed('wash_dishes', 'cup2'))
            b.run_action('wait', wait_s=20.0)
            self.assertTrue(b.timed.is_completed('wash_dishes', 'cup2'))
            self.assertFalse(dw.mock_states['ToggledOn'])
            b.run_action('wait', wait_s=60.0)
            self.assertAlmostEqual(b.timed.elapsed['wash_dishes']['cup2'], 40.0, places=3)


def _thaw_bake_spec():
    """v5: a frozen dish thaws in the microwave (40 s) and only then bakes in the oven (60 s)."""
    spec = _sample_spec()
    spec['roles'].update({'oven': 'oven_0', 'dish': 'dish_0', 'dish2': 'dish_2'})
    spec['timed_processes'] = [
        {'id': 'thaw', 'appliance': 'microwave', 'items': ['dish', 'dish2'], 'containment': 'Inside',
         'duration_seconds': 40.0, 'switch_off_when_complete': True,
         'running_message': 'The microwave is thawing the dish.', 'completed_message': 'The dish is thawed.',
         'stopped_early_message': 'The microwave stopped before the dish was thawed.'},
        {'id': 'bake', 'appliance': 'oven', 'items': ['dish'], 'containment': 'Inside',
         'duration_seconds': 60.0, 'requires_completed': 'thaw',
         'running_message': 'The oven is baking the dish.', 'completed_message': 'The dish is baked.',
         'stopped_early_message': 'The oven stopped before the dish was baked.',
         'not_ready_message': 'The dish in the oven is still frozen, so it is not baking yet.'},
    ]
    spec['evaluation']['predicates'] = {
        'dish_baked': {'object': 'dish', 'state': 'ProcessComplete', 'process': 'bake'},
        'hotdog1_cooked': {'object': 'hotdog1', 'state': 'Cooked'}}
    spec['evaluation']['terminal'] = {'all_done': 'dish_baked'}
    spec['appliance_capacities'] = [{'appliance': 'microwave', 'capacity': 1, 'counted_items': ['dish', 'dish2'],
                                     'full_message': 'The microwave takes only one baking dish at a time.'}]
    return spec


class StagesAndCapacityTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(validate_spec(_thaw_bake_spec())['status'], 'passed')
        for mutate, msg in (
                (lambda s: s['timed_processes'][1].__setitem__('requires_completed', 'nope'), 'unknown process'),
                (lambda s: s['timed_processes'][1].pop('not_ready_message'), 'not_ready_message'),
                (lambda s: s['timed_processes'][0].__setitem__('not_ready_message', 'x'), 'requires requires_completed'),
                (lambda s: s['timed_processes'][1].__setitem__('items', ['dish', 'hotdog1']), 'must all belong'),
                (lambda s: s['appliance_capacities'][0].__setitem__('capacity', 0), 'positive integer'),
                (lambda s: s['appliance_capacities'][0].pop('full_message'), 'full_message'),
        ):
            spec = _thaw_bake_spec()
            mutate(spec)
            with self.assertRaisesRegex(ValueError, msg):
                validate_spec(spec)

    def test_frozen_dish_does_not_bake_until_thawed(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(_thaw_bake_spec(), tmp)
            mw, oven, dish = b.mock_objects['microwave_0'], b.mock_objects['oven_0'], b.mock_objects['dish_0']
            dish.mock_relations['Inside'].add(oven)
            out = b.run_action('toggle_on', selected='oven_0',
                               body=lambda: oven.mock_states.__setitem__('ToggledOn', True))
            self.assertIn('still frozen', out['public_process']['message'])
            b.run_action('wait', wait_s=90.0)
            self.assertEqual(b.timed.elapsed['bake']['dish'], 0.0)
            self.assertFalse(b.timed.is_completed('bake', 'dish'))
            # thaw in the microwave, then the oven bakes it
            dish.mock_relations['Inside'] = {mw}
            b.run_action('toggle_on', selected='microwave_0',
                         body=lambda: mw.mock_states.__setitem__('ToggledOn', True))
            b.run_action('wait', wait_s=40.0)
            self.assertTrue(b.timed.is_completed('thaw', 'dish'))
            self.assertFalse(mw.mock_states['ToggledOn'])
            dish.mock_relations['Inside'] = {oven}
            out = b.run_action('close', selected='oven_0')
            self.assertIn('oven is baking', out['public_process']['message'])
            b.run_action('wait', wait_s=60.0)
            self.assertTrue(b.timed.is_completed('bake', 'dish'))

    def test_capacity_rejects_second_dish_before_motion(self):
        from manipulation_agent.executors.timed_processes import check_appliance_capacity
        from manipulation_agent.observations.boundary import public_execution_error
        spec = _thaw_bake_spec()
        with tempfile.TemporaryDirectory() as tmp:
            b = _MockBackend(spec, tmp)
            mw, dish, dish2 = b.mock_objects['microwave_0'], b.mock_objects['dish_0'], b.mock_objects['dish_2']
            check_appliance_capacity(spec, b.mock_objects, dish, mw)       # empty microwave: fine
            dish.mock_relations['Inside'].add(mw)
            with self.assertRaises(SkillError) as ctx:
                check_appliance_capacity(spec, b.mock_objects, dish2, mw)
            self.assertEqual(ctx.exception.code, 'appliance_full')
            public = public_execution_error(ctx.exception)
            self.assertEqual(public['code'], 'appliance_full')
            self.assertIn('only one baking dish', public['message'])
            self.assertIn('take something out first', public['message'])
            # an uncounted item, or another appliance, is not limited
            check_appliance_capacity(spec, b.mock_objects, b.mock_objects['cup_1'], mw)
            check_appliance_capacity(spec, b.mock_objects, dish2, b.mock_objects['oven_0'])


if __name__ == '__main__':
    unittest.main()


class CavityInsideAcceptanceTests(unittest.TestCase):
    """2026-11-21: an item resting on the cavity floor of a declared Inside appliance counts as inside."""

    def _backend(self):
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        b = CustomTaskBackend.__new__(CustomTaskBackend)
        b.custom_spec = _thaw_bake_spec()
        return b

    class _Obj:
        def __init__(self, name, lo, hi, body=None):
            self.name, self.aabb = name, (lo, hi)
            if body is not None:
                self.links = {'base_link': type('L', (), {'aabb': body})()}

    def test_floor_of_cavity_counts_door_does_not(self):
        b = self._backend()
        # whole oven aabb includes the open door reaching y=1.0; the body is y in [0, 0.5]
        oven = self._Obj('oven_0', (0, 0, 0), (0.6, 1.0, 0.6), body=((0, 0, 0), (0.6, 0.5, 0.6)))
        on_floor = self._Obj('dish_0', (0.2, 0.15, 0.05), (0.4, 0.35, 0.10))
        on_door = self._Obj('dish_0', (0.2, 0.7, 0.05), (0.4, 0.9, 0.10))
        self.assertTrue(b._released_inside_extra(on_floor, oven))
        self.assertFalse(b._released_inside_extra(on_door, oven))
        # an appliance without a declared Inside process gets no extra acceptance
        cup = self._Obj('cup_1', (0, 0, 0), (0.6, 0.5, 0.6), body=((0, 0, 0), (0.6, 0.5, 0.6)))
        self.assertFalse(b._released_inside_extra(on_floor, cup))


class OvenRackAndPayloadTests(unittest.TestCase):
    """2026-11-23: racks of an open_state_joints appliance are locked in; payload check uses cavity acceptance."""

    class _Joint:
        def __init__(self, kind, low=0.0, high=0.3):
            self.is_single_dof, self.joint_type, self.lower_limit, self.upper_limit = True, kind, low, high
            self.pos = None
        def set_pos(self, p):
            self.pos = p

    def test_non_door_joints_locked_at_lower_limit(self):
        import sys, tempfile
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        Open = type('Open', (), {})
        states = ModuleType('omnigibson.object_states'); states.Open = Open
        door, rack1, rack2 = self._Joint('RevoluteJoint', 0, 1.57), self._Joint('PrismaticJoint'), self._Joint('PrismaticJoint')
        fixed = self._Joint('FixedJoint')
        open_state = SimpleNamespace(relevant_joints_info=None, clear_cache=lambda: None)
        oven = SimpleNamespace(name='oven_0', states={Open: open_state},
                               joints={'j_door': door, 'rack1': rack1, 'rack2': rack2, 'fixed': fixed})
        b = CustomTaskBackend.__new__(CustomTaskBackend)
        b.env = SimpleNamespace(scene=SimpleNamespace(objects=[oven]))
        with tempfile.TemporaryDirectory() as d:
            b.output = Path(d)
            with patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'), 'omnigibson.object_states': states}):
                b._apply_open_state_joints({'roles': {'oven': 'oven_0'}, 'open_state_joints': {'oven': ['j_door']}})
            self.assertTrue((Path(d) / 'open_state_joints.json').exists())
        self.assertEqual(open_state.relevant_joints_info[1], [door])
        self.assertIsNone(door.pos); self.assertEqual(door.upper_limit, 1.57)
        for rack in (rack1, rack2):
            self.assertEqual(rack.pos, 0.0); self.assertAlmostEqual(rack.upper_limit, 1e-4)
        self.assertEqual(fixed.upper_limit, 0.3)
        self.assertEqual([r['joint'] for r in b._open_state_joints_applied['oven']['locked_in']], ['rack1', 'rack2'])

    def test_payload_inside_by_cavity_acceptance_is_not_escaped(self):
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        Inside = type('Inside', (), {})
        states = ModuleType('omnigibson.object_states'); states.Inside = Inside
        fish = SimpleNamespace(name='fish', states={Inside: SimpleNamespace(get_value=lambda t: False)})
        oven = SimpleNamespace(name='oven_0')
        b = CustomTaskBackend.__new__(CustomTaskBackend)
        with patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'), 'omnigibson.object_states': states}):
            b.released_inside = lambda item, target: True
            b._verify_container_payload(oven, [(fish, None, None)])           # accepted: no error
            b.released_inside = lambda item, target: False
            with self.assertRaises(SkillError):
                b._verify_container_payload(oven, [(fish, None, None)])
