"""Privileged reference policies for custom-task feasibility, never model input.

Targets use private fixture identities. All motion/manipulation passes through
the ordinary checked executor. No task-state setter is used to solve a task.
"""
from __future__ import annotations

import json
import time


class ReferenceTask:
    def __init__(self, backend, output, *, max_attempts=200):
        self.backend = backend
        self.output = output
        self.max_attempts = max_attempts
        self.calls = []

    def obj(self, role):
        name = self.backend.custom_spec['roles'][role]
        return next(obj for obj in self.backend.env.scene.objects if obj.name == name)

    def log(self, event, **data):
        row = {'time': time.time(), 'event': event, **data}
        with (self.output / 'reference_trajectory.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        print(json.dumps(row), flush=True)

    def action(self, primitive, role=None, point=None, **kwargs):
        if len(self.calls) >= self.max_attempts:
            raise RuntimeError('Reference action budget exhausted')
        backend = self.backend
        obj = self.obj(role) if role else None
        selected = (backend.torch.tensor(point, dtype=backend.torch.float32) if point is not None
                    else obj.get_position_orientation()[0].clone() if obj is not None else None)
        row = {'index': len(self.calls) + 1, 'primitive': primitive, 'role': role,
               'point': selected.tolist() if selected is not None else None,
               'selection': 'privileged_fixture_identity', 'started_at': time.time()}
        original = backend._ground
        if obj is not None:
            backend._ground = lambda *a, **kw: (
                obj, selected, {'diagnostic': True, 'selected_object_prim': obj.prim_path,
                                'hit_position': selected.tolist()})
        self.log('action_started', **row)
        try:
            result = backend.execute_visual(primitive, {} if obj is not None else None, 100000, **kwargs)
            row.update(status='passed', result=result)
            return result
        except Exception as exc:
            row.update(status='failed', error=str(exc), code=getattr(exc, 'code', None))
            raise
        finally:
            backend._ground = original
            row.update(seconds=time.time() - row['started_at'], env_step=backend.steps,
                       predicates=backend._predicate_values())
            self.calls.append(row)
            self.log('action_finished', **row)

    def floor(self, x, y):
        return self.action('navigate_to', 'floor', [x, y, 0.])

    def move(self, primitive, role, point=None, **kwargs):
        backend = self.backend
        target = point if point is not None else self.obj(role).get_position_orientation()[0].tolist()
        base = backend.robot.get_position_orientation()[0]
        distance = float(backend.torch.linalg.vector_norm(
            base[:2] - backend.torch.tensor(target[:2], device=base.device)))
        if distance > 1.2:
            task = backend.custom_spec['id']
            if task.startswith('workshop'):
                if role == 'bookshelf':
                    self.floor(23.4, -1.25)
                elif role == 'bin':
                    self.floor(23.15, 1.1)
                elif target[0] < 23.:
                    # The box and tools change sides during the task. Approach
                    # their current geometry, not their initial role location.
                    self.floor(22.95, max(-1.3, min(1.1, target[1])))
                else:
                    self.floor(23.5, max(-1.25, min(1.45, target[1])))
            elif task.startswith('movie') and target[1] < 0 and 5.6 < target[0] < 6.6:
                self.floor(6.1, .35)
            else:
                self.action('navigate_to', role, point)
        return self.action(primitive, role, point, **kwargs)

    def top(self, role, fx=.5, fy=.5):
        low, high = self.obj(role).aabb
        return [float(low[0] + fx * (high[0] - low[0])),
                float(low[1] + fy * (high[1] - low[1])), float(high[2])]

    def inside(self, role):
        for attempt in range(3):
            try:
                return self.move('place_inside', role)
            except Exception as exc:
                if getattr(exc, 'code', None) not in ('sampling_error', 'sampling_budget_exhausted', 'postcondition_error') or attempt == 2:
                    raise

    def transfer_inside(self, item, destination):
        self.move('grasp', item)
        self.inside(destination)

    def transfer_top(self, item, destination, point):
        self.move('grasp', item)
        self.move('place_on_top', destination, point)

    def wait_for(self, wanted, *, attempts=18):
        for _ in range(attempts + 1):
            values = self.backend._predicate_values()
            if all(values[key] == value for key, value in wanted.items()):
                return
            if _ == attempts:
                break
            self.action('wait', seconds=20.)
        raise RuntimeError('Physical process did not reach ' + repr(wanted))

    def lunch_cleanup(self):
        for i in range(1, 4):
            self.transfer_inside(f'can{i}', 'bin')

    def lunch(self, order):
        if order == 'cleanup_first':
            self.lunch_cleanup()
        self.move('open', 'fridge')
        self.move('open', 'microwave')
        for i in (1, 2):
            self.transfer_inside(f'hotdog{i}', 'microwave')
        self.move('close', 'microwave')
        self.move('toggle_on', 'microwave')
        if order == 'cook_first':
            self.lunch_cleanup()
        self.wait_for({'hotdog1_cooked': True, 'hotdog2_cooked': True})
        self.move('toggle_off', 'microwave')
        self.move('open', 'microwave')
        for item in ('hotdog1', 'hotdog2', 'apple1', 'apple2'):
            self.transfer_inside(item, 'box')
        self.move('close', 'microwave')
        for item in ('cookie', 'sandwich'):
            self.transfer_inside(item, 'fridge')
        self.transfer_top('tea', 'table', [7.8, 3.2, .813])
        self.move('close', 'fridge')
        for item, point in [('plate1', [6.5, 3.5, .813]), ('plate2', [7.5, 3.5, .813]),
                            ('fork1', [6.5, 3.2, .813]), ('fork2', [7.5, 3.2, .813])]:
            self.transfer_top(item, 'table', point)
        self.transfer_top('box', 'table', [8., 3.5, .813])
        self.transfer_top('board', 'storage', [5.2, -1.9, .88754])
        self.action('wait', seconds=1.)

    def movie_cleanup(self):
        from omnigibson.object_states import Open
        box = self.obj('toybox')
        if Open in box.states and not box.states[Open].get_value():
            self.move('open', 'toybox')
        for i in (1, 2):
            self.transfer_inside(f'toy{i}', 'toybox')
        for i in (1, 2, 3):
            self.transfer_inside(f'can{i}', 'bin')
        self.move('grasp', 'brush')
        for _ in range(4):
            self.move('wipe', 'table', self.top('table'))
            if not self.backend._predicate_values()['table_dusty']:
                break
        self.move('place_on_top', 'prep', [7.758, -.62, .88736])

    def movie(self, order):
        if order == 'cleanup_first':
            self.movie_cleanup()
        self.move('open', 'microwave')
        self.transfer_inside('bag', 'microwave')
        self.move('close', 'microwave')
        self.move('toggle_on', 'microwave')
        if order == 'cook_overlap':
            self.movie_cleanup()
        for item, x, y in [('plate1', .25, .5), ('plate2', .75, .5), ('cup1', .25, .8),
                           ('cup2', .75, .8), ('fork1', .25, .2), ('fork2', .75, .2)]:
            self.transfer_top(item, 'table', self.top('table', x, y))
        self.wait_for({'cooked_popcorn': True, 'raw_popcorn_present': False})
        self.move('toggle_off', 'microwave')
        self.move('open', 'microwave')
        self.transfer_top('bag', 'table', self.top('table'))
        self.move('close', 'microwave')
        self.action('wait', seconds=1.)

    def workshop_cleanup(self):
        for i in (1, 2, 3):
            self.transfer_inside(f'can{i}', 'bin')
        for i, x in ((1, 23.45), (2, 23.98)):
            self.transfer_top(f'book{i}', 'bookshelf', [x, -2.06, .92754])

    def workshop(self, order):
        from omnigibson.object_states import Inside
        tools = ['pliers', 'allen_wrench', 'screwdriver', 'flashlight', 'drill']
        if order == 'cleanup_first':
            self.workshop_cleanup()
        self.move('open', 'box')
        for item in tools:
            self.transfer_inside(item, 'box')
        self.move('close', 'box')
        if order == 'pack_first':
            self.workshop_cleanup()
        self.transfer_top('box', 'bench', [24.5, 1.5, .92754])
        self.move('open', 'box')
        for item, y in zip(tools, [-1.95, -.8, -.2, .4, 1.]):
            if self.obj(item).states[Inside].get_value(self.obj('box')):
                self.move('open', 'box')
            self.transfer_top(item, 'bench', [24.5, y, .92754])
        self.move('close', 'box')
        self.transfer_top('box', 'source', [21.95, 1.2, .91574])
        self.action('wait', seconds=1.)

    def run(self, order):
        task = self.backend.custom_spec
        if order not in task['acceptance_cases']['reference_orders']:
            raise ValueError('Undeclared reference order: ' + order)
        if task['id'].startswith('warm_lunch'):
            self.lunch(order)
        elif task['id'].startswith('movie_night'):
            self.movie(order)
        elif task['id'].startswith('workshop'):
            self.workshop(order)
        else:
            raise ValueError('Unknown reference task')

    def negative(self, case):
        """Exercise a declared failure with real state, recording its witness.

        Entity removal is explicit fault injection, never a solution action.
        Other cases use the ordinary executor, including its safety rejections.
        A scenario that cannot be reached fails this diagnostic; it is not
        silently counted as evidence that the evaluator detected a violation.
        """
        task = self.backend.custom_spec
        if case not in task['acceptance_cases']['physical_negative_cases']:
            raise ValueError('Undeclared negative case: ' + case)
        expected = {}
        witness = {}
        fault = None
        if case == 'unfinished_food':
            self.move('open', 'fridge')
            self.transfer_inside('hotdog1', 'box')
            witness = {'hotdog1_packed': True, 'hotdog1_cooked': False}
            expected = {'terminal_conditions': {'hotdog1_cooked': False}}
        elif case == 'prep_not_restored':
            original = self.obj('plate1').get_position_orientation()[0].tolist()
            self.transfer_top('plate1', 'table', [6.5, 3.5, .813])
            self.transfer_top('plate1', 'prep', original)
            witness = {'plate1_on_prep': True}
            expected = {'terminal_conditions': {'prep_area_clear': False}}
        elif case == 'missing_required_entity':
            fault = {'kind': 'remove_required_entity', 'role': 'board',
                     'object': self.obj('board').name}
            self.log('fault_injection', **fault)
            self.backend.og.sim.remove_object(self.obj('board'))
            self.action('wait', seconds=.2)
        elif case == 'serve_before_clean':
            self.transfer_top('plate1', 'table', self.top('table', .25, .5))
            witness = {'table_dusty': True, 'plate1_set': True}
            expected = {'constraint_conditions': {'clear_and_dust_before_serving': False}}
        elif case == 'uncooked_snack':
            self.transfer_top('bag', 'table', self.top('table'))
            witness = {'bag_on_table': True, 'cooked_popcorn': False, 'raw_popcorn_present': True}
            expected = {'terminal_conditions': {'popcorn_served': False}}
        elif case == 'reenergized_removal':
            self.move('open', 'microwave')
            self.transfer_inside('bag', 'microwave')
            self.move('close', 'microwave')
            self.move('toggle_on', 'microwave')
            self.wait_for({'cooked_popcorn': True, 'raw_popcorn_present': False})
            self.move('toggle_off', 'microwave')
            self.move('toggle_on', 'microwave')
            self.move('open', 'microwave')
            self.move('grasp', 'bag')
            witness = {'bag_in_microwave': False, 'microwave_on': True}
            expected = {'constraint_conditions': {'power_off_before_cooked_bag_removal': False},
                        'historical_conditions': {'microwave_off_before_removal': True}}
        elif case in ('missing_tool', 'open_lid_transport'):
            self.move('open', 'box')
            items = ['pliers', 'allen_wrench', 'screwdriver', 'flashlight', 'drill']
            for item in (items[:-1] if case == 'missing_tool' else items):
                self.transfer_inside(item, 'box')
            self.move('close', 'box')
            if case == 'missing_tool':
                witness = {item+'_in_box': item != 'drill' for item in items}
                expected = {'historical_conditions': {'packed_at_storage': False}}
            else:
                self.move('open', 'box')
                self.move('grasp', 'box')
                self.floor(23.5, 1.1)
                witness = {'box_open': True}
                expected = {'constraint_conditions': {'closed_loaded_box_transport': False},
                            'historical_conditions': {'packed_at_storage': True}}
        elif case == 'skip_loaded_transport':
            for item, y in zip(['pliers', 'allen_wrench', 'screwdriver', 'flashlight', 'drill'],
                               [-1.95, -.8, -.2, .4, 1.]):
                self.transfer_top(item, 'bench', [24.5, y, .92754])
            witness = {item+'_on_bench': True for item in
                       ['pliers', 'allen_wrench', 'screwdriver', 'flashlight', 'drill']}
            expected = {'historical_conditions': {'loaded_box_delivered': False}}
        else:
            raise ValueError('No physical scenario for ' + case)
        values = self.backend._predicate_values()
        evaluation = self.backend.evaluate()
        checks = {key: values[key] == value for key, value in witness.items()}
        for section, entries in expected.items():
            checks.update({section+'.'+key: evaluation[section][key] is value
                           for key, value in entries.items()})
        checks['task_rejected'] = evaluation['task_success'] is False
        if fault:
            checks['missing_entity_reported'] = 'board' in evaluation.get('missing_required_entities', [])
        result = {'case': case, 'checks': checks, 'witness': witness,
                  'expected_score': expected, 'fault_injection': fault,
                  'status': 'passed' if all(checks.values()) else 'failed'}
        self.log('negative_case_result', **result)
        return result
