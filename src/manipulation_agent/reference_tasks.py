"""Privileged reference policies for custom-task feasibility, never model input.

Targets use private fixture identities. All motion/manipulation passes through
the ordinary checked executor. No task-state setter is used to solve a task.
"""
from __future__ import annotations

import json
import math
import time

from .contracts import SkillError


def placement_yaw_delta(orientation, desired_world_degrees):
    """Convert a fixture heading into the executor's relative placement yaw."""
    x, y, z, w = map(float, orientation)
    current = math.degrees(math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z)))
    return (desired_world_degrees-current+180.) % 360. - 180.


def appliance_approach_point(bounds, base, *, open_center=None):
    """Select the near AABB face, then let the normal ray test verify visibility.

    A closed appliance's center is occluded by its own door. Once open, use
    the body center: its articulated AABB can include a door and empty space
    outside the cabinet. All candidates still undergo normal visibility checks.
    """
    if open_center is not None:
        return [float(value) for value in open_center]
    low, high = bounds
    center = [(float(a)+float(b))/2 for a, b in zip(low, high)]
    direction = [float(base[i])-center[i] for i in (0, 1)]
    scales = [(float(high[i])-center[i])/abs(direction[i])
              for i in (0, 1) if abs(direction[i]) > 1e-8]
    if not scales:
        return center
    scale = min(scales)
    return [center[0]+scale*direction[0], center[1]+scale*direction[1], center[2]]


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
        if primitive == 'navigate_to' and point is None and role in ('fridge', 'microwave'):
            from omnigibson.object_states import Open
            opened = Open in obj.states and bool(obj.states[Open].get_value())
            point = appliance_approach_point(obj.aabb, backend.robot.get_position_orientation()[0],
                open_center=obj.get_position_orientation()[0] if opened else None)
        selected = (backend.torch.tensor(point, dtype=backend.torch.float32) if point is not None
                    else obj.get_position_orientation()[0].clone() if obj is not None else None)
        row = {'index': len(self.calls) + 1, 'primitive': primitive, 'role': role,
               'point': selected.tolist() if selected is not None else None,
               'options': dict(kwargs),
               'selection': 'privileged_fixture_identity', 'started_at': time.time()}
        original = backend._ground
        if obj is not None:
            backend._ground = lambda *a, **kw: (
                obj, selected, {'diagnostic': True, 'selected_object_prim': obj.prim_path,
                                'hit_position': selected.tolist()})
        self.log('action_started', **row)
        try:
            allowed = {'wait': {'seconds'}, 'look': {'yaw_degrees'},
                       'place_on_top': {'placement_yaw_degrees'}}.get(primitive, set())
            if set(kwargs) - allowed:
                raise SkillError('invalid_arguments',
                    f'Unsupported reference options for {primitive}: {sorted(set(kwargs) - allowed)}')
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

    def move(self, primitive, role, point=None, *, placement_world_yaw=None, **kwargs):
        backend = self.backend
        target = point if point is not None else self.obj(role).get_position_orientation()[0].tolist()
        base = backend.robot.get_position_orientation()[0]
        distance = float(backend.torch.linalg.vector_norm(
            base[:2] - backend.torch.tensor(target[:2], device=base.device)))
        if distance > 1.2:
            task = backend.custom_spec['id']
            if task.startswith('workshop'):
                if role == 'bookshelf':
                    # A fixed floor endpoint may be reachable without a load
                    # but impossible with the book's swept collision hull.
                    # Let the normal object-approach planner choose a visible,
                    # reachable stance for the actual placement target.
                    self.action('navigate_to', role, point)
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
                try:
                    self.action('navigate_to', role, point)
                except Exception as exc:
                    if getattr(exc, 'code', None) not in ('navigation_search_exhausted', 'navigation_unreachable'):
                        raise
                    if task.startswith('movie') and role == 'toybox':
                        # The closest stance can lack a complete turning route
                        # with the second toy held. A preserved failure map
                        # certifies this wider stance ~1.19 m from the box.
                        # The live planner and ordinary reach check still apply.
                        self.floor(5.5, 6.3)
                    elif task.startswith(('warm_lunch', 'kitchen_service')):
                        # Split a long kitchen/dining-room approach into ordinary
                        # checked floor goals. Failed calls retain their budget.
                        self.floor(6.2, 1.2)
                        self.action('navigate_to', role, point)
                    else:
                        raise
        if placement_world_yaw is not None:
            if primitive != 'place_on_top' or 'placement_yaw_degrees' in kwargs:
                raise ValueError('World placement yaw requires one place_on_top rotation request')
            # Navigation may rotate the carried assembly. Resolve the relative
            # placement option only after the final approach has completed.
            held = backend._get_held()
            kwargs['placement_yaw_degrees'] = placement_yaw_delta(
                held.get_position_orientation()[1], placement_world_yaw)
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

    def transfer_top(self, item, destination, point, **kwargs):
        self.move('grasp', item)
        self.move('place_on_top', destination, point, **kwargs)

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
        # Leave the east-side pickup stance empty-handed before attaching
        # the can. The closer stance fits the body but traps its loaded
        # forward departure. This short checked approach was replayed live.
        self.action('navigate_to', 'can1')
        self.floor(9.5, 3.5)
        for i in range(1, 4):
            self.transfer_inside(f'can{i}', 'bin')

    def stage_fridge_food(self, item):
        # A lower-shelf food center is an occluded navigation target, and
        # carrying it at shelf height can trap the assembly beside the
        # refrigerator. Approach the appliance, then use an ordinary counted
        # placement/regrasp on the adjacent counter before travel. This also
        # supports uncooked-food negative cases; no cooking occurs here.
        self.action('navigate_to', 'fridge')
        self.action('grasp', item)
        self.action('place_on_top', 'storage', [7.05, -1.75, .88754])
        self.floor(6.8, -.9)
        self.action('grasp', item)

    def lunch(self, order, *, deliver_box=True):
        if order == 'cleanup_first':
            self.lunch_cleanup()
        self.move('open', 'microwave')
        self.move('open', 'fridge')
        for i in (1, 2):
            self.stage_fridge_food(f'hotdog{i}')
            self.inside('microwave')
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
        if deliver_box:
            self.transfer_top('box', 'table', [8., 3.5, .813])
        self.transfer_top('board', 'storage', [5.2, -1.9, .88754])
        self.action('wait', seconds=1.)

    def serve_meal(self):
        for index in (1, 2):
            plate = f'plate{index}'
            for kind, fraction in (('hotdog', .3), ('apple', .7)):
                self.transfer_top(f'{kind}{index}', plate, self.top(plate, fraction, .5))

    def return_carrier(self):
        self.transfer_top('box', 'storage', self.top('storage', .75, .5))
        self.action('wait', seconds=1.)

    def kitchen_service(self, order):
        self.lunch(order)
        self.serve_meal()
        self.return_carrier()

    def movie_cleanup(self):
        from omnigibson.object_states import Open
        box = self.obj('toybox')
        if Open in box.states and not box.states[Open].get_value():
            self.move('open', 'toybox')
        for i in (1, 2):
            self.transfer_inside(f'toy{i}', 'toybox')
        # The east-side grasp stance becomes trapped after picking up a can.
        # Approach the west side empty-handed: the saved pre-grasp collision
        # map admits this route, and all three cans and the bin are in reach.
        # Live navigation, reach, containment and counted actions still apply.
        self.floor(2.8, 5.1)
        for i in (1, 2, 3):
            # Retain the certified stance. The ordinary 1.4 m reach check
            # allows grid snapping without the helper reapproaching at 1.2 m.
            self.action('grasp', f'can{i}')
            self.inside('bin')
        self.action('navigate_to', 'brush')
        self.floor(7.3, .65)
        self.action('grasp', 'brush')
        # The closest west-side table approach has no bounded complete route
        # with this brush offset. Use the reachable southeast floor stance;
        # the table center remains within the ordinary 1.4 m action reach.
        self.floor(4.4, 4.45)
        for _ in range(4):
            self.action('wipe', 'table', self.top('table'))
            if not self.backend._predicate_values()['table_dusty']:
                break
        self.floor(6.2, 1.2)
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
            item = f'book{i}'
            self.move('grasp', item)
            # Regrasp at the near edge before crossing the narrow aisle. The
            # original remote grasp preserves a >1 m carry offset and can
            # leave no collision-free turning route with the book attached.
            self.move('place_on_top', 'bench', [24.1, 1.25 if i == 1 else -.9, .92754])
            self.move('grasp', item)
            self.move('place_on_top', 'bookshelf', [x, -2.06, .92754])

    def pack_kit(self, *, omit=None, delivered=False):
        if delivered:
            # All five unpacked tools and the delivered box are within the
            # checked reach of this stance. Reposition empty-handed once;
            # the general 1.2 m approach heuristic would otherwise move with
            # the Allen wrench held, despite its valid 1.4 m action reach.
            # Leave margin for grid snapping and the wrench's offset origin:
            # its settled grasp point is ~7 cm beyond its placement target.
            self.floor(23.55, .95)
        self.move('open', 'box')
        for item in ('pliers', 'allen_wrench', 'screwdriver', 'flashlight', 'drill'):
            if item != omit:
                if delivered:
                    self.action('grasp', item)
                    self.inside('box')
                else:
                    self.transfer_inside(item, 'box')
        self.close_packed_kit()

    def close_packed_kit(self):
        # A newly packed kit can spring the lid back before its contents have
        # settled. Preserve each failed checked close and wait before retrying.
        for attempt in range(3):
            self.action('wait', seconds=2.)
            try:
                return self.move('close', 'box')
            except Exception as exc:
                if (getattr(exc, 'code', None) != 'postcondition_error'
                        or 'Requested object operation did not remain stable' not in str(exc)
                        or attempt == 2):
                    raise
                self.log('container_close_recovery', failed_attempt=attempt+1,
                         reason='Checked close rolled back; wait for free settling before retry')

    def return_kit(self, *, empty=False):
        if empty:
            # Both the box and its destination fit within checked reach from
            # this stance. The general 1.2 m approach heuristic would trigger
            # an unnecessary loaded navigation after the grasp.
            self.floor(23.25, 1.1)
            self.action('grasp', 'box')
            self.action('place_on_top', 'source', [21.95, 1.2, .91574])
        else:
            self.transfer_top('box', 'source', [21.95, 1.2, .91574])
        self.action('wait', seconds=1.)

    def open_delivered_kit(self):
        # Carry/placement temporarily controls the rigid contents. Allow them
        # to settle after releasing the delivered box before articulating its
        # lid. A rejected opening is still a counted action, never success.
        self.action('wait', seconds=2.)
        for attempt in range(3):
            try:
                return self.move('open', 'box')
            except Exception as exc:
                if (getattr(exc, 'code', None) != 'postcondition_error'
                        or 'displaced existing contents' not in str(exc) or attempt == 2):
                    raise
                self.log('container_open_recovery', failed_attempt=attempt+1,
                         reason='Checked opening rolled back; wait for free settling before retry')
                self.action('wait', seconds=2.)

    def unpack_delivered_kit(self):
        # Reposition empty-handed and use nearby free bench space. The old
        # far-end placement required a long-offset carried assembly to turn
        # in the narrow aisle. Reverse packing order also removes the
        # flashlight supported on the pliers before grasping the pliers.
        self.floor(23.5, 1.1)
        for item, point in [
            ('drill', [24.4, 1., .92754]),
            ('flashlight', [24.6, .7, .92754]),
            ('screwdriver', [24.4, .5, .92754]),
            ('allen_wrench', [24.6, .3, .92754]),
            ('pliers', [24.2, .3, .92754]),
        ]:
            # Retain this stance. Ordinary reach, collision, payload and
            # contact checks still apply to both counted actions.
            self.action('grasp', item)
            self.action('place_on_top', 'bench', point)
        self.action('wait', seconds=2.)

    def workshop(self, order, *, return_box=True):
        if order == 'cleanup_first':
            self.workshop_cleanup()
        self.pack_kit()
        if order == 'pack_first':
            self.workshop_cleanup()
        # Keep the lid's opening side away from the wall for either branch
        # order; transport may have left the assembly at a different yaw.
        self.transfer_top('box', 'bench', [24.5, 1.5, .92754], placement_world_yaw=-45.)
        self.open_delivered_kit()
        self.unpack_delivered_kit()
        self.move('close', 'box')
        if return_box:
            self.return_kit(empty=True)

    def workshop_inventory(self, order):
        self.workshop(order, return_box=False)
        self.pack_kit(delivered=True)
        self.return_kit()

    def run(self, order):
        task = self.backend.custom_spec
        if order not in task['acceptance_cases']['reference_orders']:
            raise ValueError('Undeclared reference order: ' + order)
        if task['id'] == 'workshop_inventory_roundtrip_v1':
            self.workshop_inventory(order)
        elif task['id'] == 'kitchen_service_and_reset_v1':
            self.kitchen_service(order)
        elif task['id'].startswith('warm_lunch'):
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
        if case == 'skip_inventory_unpack':
            self.workshop_cleanup()
            self.pack_kit()
            self.transfer_top('box', 'bench', [24.5, 1.5, .92754], placement_world_yaw=-45.)
            self.return_kit()
            witness = {'box_at_source': True, 'box_open': False,
                       **{item+'_in_box': True for item in
                          ('pliers', 'allen_wrench', 'screwdriver', 'flashlight', 'drill')}}
            expected = {'historical_conditions': {'loaded_box_delivered': True,
                                                  'tools_unpacked': False,
                                                  'loaded_box_returned': False}}
        elif case in ('return_incomplete_kit', 'open_lid_return'):
            self.workshop('cleanup_first', return_box=False)
            self.pack_kit(omit='drill' if case == 'return_incomplete_kit' else None,
                          delivered=True)
            if case == 'return_incomplete_kit':
                self.return_kit()
                witness = {'box_at_source': True, 'box_open': False, 'drill_in_box': False}
                expected = {'terminal_conditions': {'drill_packed': False},
                            'historical_conditions': {'tools_unpacked': True,
                                                      'repacked_at_bench': False}}
            else:
                self.move('open', 'box')
                self.move('grasp', 'box')
                self.floor(23., 1.1)
                witness = {'box_open': True}
                expected = {'historical_conditions': {'repacked_at_bench': True},
                            'constraint_conditions': {'closed_return_transport': False}}
        elif case == 'skip_meal_delivery':
            self.lunch('cleanup_first', deliver_box=False)
            self.serve_meal()
            self.return_carrier()
            witness = {'box_at_storage': True, 'lunch_delivered': False,
                       'hotdog1_on_plate1': True, 'hotdog2_on_plate2': True,
                       'apple1_on_plate1': True, 'apple2_on_plate2': True}
            expected = {'historical_conditions': {'meal_delivered': False, 'meal_served': False}}
        elif case == 'return_nonempty_box':
            self.lunch('cleanup_first')
            self.return_carrier()
            witness = {'box_at_storage': True, 'hotdog1_packed': True}
            expected = {'terminal_conditions': {'empty_carrier_returned': False},
                        'historical_conditions': {'meal_delivered': True, 'meal_served': False}}
        elif case == 'uncooked_plate':
            self.move('open', 'fridge')
            self.transfer_top('plate1', 'table', [6.5, 3.5, .813])
            self.stage_fridge_food('hotdog1')
            self.move('place_on_top', 'plate1', self.top('plate1', .3, .5))
            witness = {'hotdog1_on_plate1': True, 'hotdog1_cooked': False}
            expected = {'terminal_conditions': {'hotdog1_cooked': False}}
        elif case == 'unfinished_food':
            self.move('open', 'fridge')
            self.stage_fridge_food('hotdog1')
            self.inside('box')
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
            self.close_packed_kit()
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
            # Clear the destination using the valid cleanup branch, so the
            # witness isolates missing loaded transport instead of depending
            # on a placement through the initial books and cans.
            self.workshop_cleanup()
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
