"""Checked task-level reference for the shared-library construction candidate."""
from .diverse_reference import DiverseReference


class BranchingLibraryReference(DiverseReference):
    def wipe(self, role):
        self.ref.move('grasp', self.plan['wipe_tool'])
        self.ref.move('wipe', role)
        home = self.plan['wipe_home']
        self.ref.move('place_on_top', home,
                      [*self.plan['wipe_xy'], float(self.ref.obj(home).aabb[1][2])])

    def run(self, order, negative=None):
        if negative is not None:
            raise ValueError('Use the explicit physical negative dispatcher')
        schedule = self.plan['schedules'][order]
        rooms = {r['id']: r for r in self.plan['rooms']}
        for step in schedule:
            self.ref.log('branch_started', branch=step)
            if step == 'library':
                for w in self.plan['clutter']:
                    self.transfer(w['item'], w['destination'], w['destination_xy'])
                self.wipe('shared_shelf')
            elif step == 'close':
                for lamp in self.plan['lights']:
                    self.ref.move('toggle_off', lamp)
            else:
                room_id, phase = step.split('_', 1)
                room = rooms[room_id]
                if phase == 'stage':
                    for w in room['work']:
                        self.transfer(w['item'], w['stage'], w['stage_xy'])
                elif phase == 'vacuum':
                    self.acquire()
                    self.ref.move('vacuum', room['floor'])
                    self.return_tool()
                elif phase == 'restore':
                    for w in room['work']:
                        self.transfer(w['item'], w['destination'], w['destination_xy'])
                elif phase == 'wipe':
                    self.wipe(room_id + '_stage')
                else:
                    raise ValueError('Unknown library reference phase: ' + phase)
            self.ref.log('branch_finished', branch=step)

    def negative(self, case):
        if case not in self.backend.custom_spec['acceptance_cases']['physical_negative_cases']:
            raise ValueError('Unknown library negative case')
        if case == 'early_lights_off':
            self.ref.move('toggle_off', 'a_lamp0')
            expected = 'a_lamp0_last'
        else:
            role, expected = {'early_shelf_wipe': ('shared_shelf', 'clear_shelf_before_wipe'),
                              'early_stage_wipe': ('a_stage', 'a_restore_before_stage_wipe')}[case]
            self.wipe(role)
        result = self.backend.evaluate()
        violations = {v['id'] for v in result['public_constraint_violations']}
        checks = {'controlled_action_completed': True, 'specific_ordering_witness': expected in violations,
                  'independent_evaluator_rejected': result['task_success'] is False}
        return dict(case=case, status='passed' if all(checks.values()) else 'failed', checks=checks)
