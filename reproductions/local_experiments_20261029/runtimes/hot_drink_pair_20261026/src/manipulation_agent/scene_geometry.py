"""CPU-only policy for preserving a scene shell independently of room furniture."""
from __future__ import annotations

import copy

SHELL_CATEGORIES = frozenset({
    'floors', 'walls', 'ceilings', 'lawn', 'driveway', 'fence', 'roof',
    'background', 'fixed_window', 'window', 'openable_window', 'door', 'sliding_door',
    'rail_fence', 'swimming_pool',
})
SHELL_VERSION = 'complete_static_shell_v2'


def is_static_shell(entry):
    args = entry.get('args', {})
    return args.get('category') in SHELL_CATEGORIES and args.get('fixed_base') is True


def restore_static_shell(data, full):
    """Restore absent fixed shell objects and states without changing existing objects."""
    objects = data['objects_info']['init_info']
    states = data['state']['registry']['object_registry']
    added = []
    for name, entry in full['objects_info']['init_info'].items():
        if name in objects or not is_static_shell(entry):
            continue
        state = full['state']['registry']['object_registry'].get(name)
        if state is None:
            raise ValueError('Missing pinned shell state: ' + name)
        objects[name] = copy.deepcopy(entry)
        states[name] = copy.deepcopy(state)
        added.append(name)
    return added


class CompleteShellAdmission:
    """Keep the original room filter for everything except the fixed building shell."""

    def _should_load_object(self, obj_info, task_metadata):
        if is_static_shell(obj_info):
            blacklist = getattr(self, 'not_load_object_categories', None)
            return blacklist is None or obj_info['args']['category'] not in blacklist
        return super()._should_load_object(obj_info, task_metadata)
