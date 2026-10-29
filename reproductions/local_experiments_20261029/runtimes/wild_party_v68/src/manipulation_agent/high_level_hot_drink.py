"""Small task-state machine for the hot-drink planning harness.

The simulator supplies images and walking. This state machine supplies the
high-level effects and success check, without relying on particle settling or
contact predicates. It receives only the model's selected primitive/target.
"""
from .contracts import SkillError


class HighLevelHotDrink:
    INITIAL_INSIDE = {
        'water_bottle': 'refrigerator',
        **{name: 'cabinet' for name in (
            'kettle', 'mug', 'dinner_napkin', 'place_mat', 'plate',
            'teaspoon', 'tray', 'sugar_cookie', 'honey_jar')},
    }

    def __init__(self, *, initial_open=None, legacy_wait_heating=False):
        self.support = dict(self.INITIAL_INSIDE)
        # The pinned scene instance starts with its storage cabinet open.
        # Keep the symbolic precondition aligned with what the RGB shows.
        self.open = {'cabinet'} if initial_open is None else set(initial_open)
        self.held = None
        self.stove_on = False
        self.water = {'water_bottle': 'water'}
        self.heating_seconds = 0.0
        self.legacy_wait_heating = legacy_wait_heating
        self.history = []

    def _require_access(self, target):
        parent=self.support.get(target)
        seen=set()
        while parent is not None and parent not in seen:
            if parent in {'refrigerator','cabinet'} and parent not in self.open:
                raise SkillError('container_closed', 'Containing appliance is closed')
            seen.add(parent)
            parent=self.support.get(parent)

    def apply(self, primitive, target=None, *, seconds=0.0):
        if primitive in {'open', 'close'}:
            if target not in {'refrigerator', 'cabinet'}:
                raise SkillError('unsupported_relation', 'This target is not a container')
            if self.held is not None:
                raise SkillError('hand_occupied', 'Place the carried object first')
            (self.open.add if primitive == 'open' else self.open.discard)(target)
        elif primitive == 'grasp':
            if self.held is not None:
                raise SkillError('hand_occupied', 'Place the carried object first')
            if target not in self.support:
                raise SkillError('unsupported_relation', 'This object cannot be carried')
            self._require_access(target)
            self.held = target
            del self.support[target]
        elif primitive in {'place_on_top', 'place_near_heating_element', 'place_inside'}:
            if self.held is None:
                raise SkillError('empty_hand', 'Carry an object first')
            self._require_access(target)
            if primitive == 'place_near_heating_element' and target != 'stove':
                raise SkillError('unsupported_relation', 'Target has no heating element')
            if primitive == 'place_inside' and target not in self.open:
                raise SkillError('container_closed', 'Containing appliance is closed')
            if target == self.held:
                raise SkillError('unsupported_relation', 'Cannot place an object on itself')
            if target in {child for child, _ in self.descendants(self.held)}:
                raise SkillError('unsupported_relation', 'Cannot place an object on its own contents')
            self.support[self.held] = target
            self.held = None
        elif primitive == 'release':
            if self.held is None:
                raise SkillError('empty_hand', 'No object is held')
            self.support[self.held] = 'floor'
            self.held = None
        elif primitive == 'pour':
            if self.held is None:
                raise SkillError('empty_hand', 'Carry a liquid container first')
            self._require_access(target)
            content = self.water.get(self.held)
            if content is None or target not in {'kettle', 'mug'}:
                raise SkillError('unsupported_relation', 'No transferable water or destination')
            self.water[target] = content
            if self.held == 'water_bottle':
                del self.water[self.held]
        elif primitive in {'toggle_on', 'toggle_off'}:
            if target != 'stove':
                raise SkillError('unsupported_relation', 'Target has no heating element')
            if self.held is not None:
                raise SkillError('hand_occupied', 'Place the carried object first')
            self.stove_on = primitive == 'toggle_on'
        elif primitive == 'boil':
            if target not in {'stove', 'kettle'}:
                raise SkillError('unsupported_relation', 'Select the kettle or stove to boil water')
            if self.held is not None:
                raise SkillError('hand_occupied', 'Place the carried object first')
            if self.support.get('kettle') != 'stove' or self.water.get('kettle') != 'water':
                raise SkillError('invalid_precondition', 'Place a kettle containing water on the stove first')
            self.water['kettle'] = 'cooked__water'
        elif primitive == 'wait':
            if seconds < 0:
                raise SkillError('invalid_arguments', 'Wait must be nonnegative')
            if self.legacy_wait_heating and self.stove_on and self.support.get('kettle') == 'stove' and self.water.get('kettle') == 'water':
                self.heating_seconds += seconds
                if self.heating_seconds >= 20:
                    self.water['kettle'] = 'cooked__water'
            else:
                self.heating_seconds = 0.0
        else:
            raise SkillError('unsupported_relation', 'Unsupported high-level action')
        self.history.append({'primitive': primitive, 'target': target,
                             'seconds': seconds if primitive == 'wait' else None})

    def _supported_by(self, child, root):
        seen=set()
        while child in self.support and child not in seen:
            seen.add(child)
            child=self.support[child]
            if child==root:
                return True
        return False

    def descendants(self, root):
        """Return every object supported by root, including nested contents."""
        edges=[]
        queue=[root]
        for parent in queue:
            for child,support in self.support.items():
                if support==parent:
                    edges.append((child,parent))
                    queue.append(child)
        return edges

    def snapshot(self):
        return {'support': dict(self.support), 'open': sorted(self.open),
                'held': self.held, 'stove_on': self.stove_on,
                'water': dict(self.water), 'heating_seconds': self.heating_seconds,
                'action_count': len(self.history)}

    def evaluate(self):
        goals = {
            'water_boiled': self.water.get('kettle') == 'cooked__water',
            'kettle_has_hot_water': self.water.get('kettle') == 'cooked__water',
            'napkin_served': self._supported_by('dinner_napkin', 'coffee_table'),
            'mat_on_table': self._supported_by('place_mat', 'coffee_table'),
            'plate_served': self._supported_by('plate', 'coffee_table'),
            'spoon_served': self._supported_by('teaspoon', 'coffee_table'),
            'tray_on_table': self._supported_by('tray', 'coffee_table'),
            'cookie_served': self._supported_by('sugar_cookie', 'coffee_table'),
            'honey_served': self._supported_by('honey_jar', 'coffee_table'),
            'mug_has_hot_water': self.water.get('mug') == 'cooked__water',
            'mug_served': self._supported_by('mug', 'coffee_table'),
        }
        return {'task_success': all(goals.values()), 'goal_satisfaction_fraction':
                sum(goals.values()) / len(goals), 'semantic_goals': goals,
                'protocol': 'high_level_hot_drink_state_v1',
                'official_task_success': None, 'official_submission_eligible': False,
                'action_count': len(self.history)}
