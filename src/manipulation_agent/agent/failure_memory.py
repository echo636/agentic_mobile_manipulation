"""Harness option --failure-memory (route A, default off): remember failed attempts so the model stops repeating them.

Built only from what the robot itself knows: the model's own act arguments (primitive, view, normalized point)
and the public receipt's outcome code. "Place" is the robot's own navigation history: each completed
navigate_to starts a new stance segment. No simulator coordinates, object identities or private codes.
"""
from __future__ import annotations

import math

MAX_KEPT = 8
NEAR_PX = 40.0   # 512-px views: about one hand-sized object at 1 m
IMAGE_PX = 512.0


class FailureMemory:
    def __init__(self):
        self.step = 0
        self.segment = 0          # stance segment; advances on every completed navigate_to
        self.failures: list[dict] = []

    @staticmethod
    def _target(arguments):
        target = arguments.get('target') if isinstance(arguments, dict) else None
        if not isinstance(target, dict):
            return None, None
        ref, point = target.get('image_ref'), target.get('point')
        view = ref.rsplit('-', 1)[-1] if isinstance(ref, str) and '-' in ref else None
        if isinstance(point, list) and len(point) == 2:
            try:
                return view, [float(point[0]), float(point[1])]
            except (TypeError, ValueError):
                return view, None
        return view, None

    def _similar(self, a, b):
        if a['primitive'] != b['primitive'] or a['segment'] != b['segment'] or a['view'] != b['view']:
            return False
        if a['point'] is None or b['point'] is None:
            return a['point'] is None and b['point'] is None
        dx = (a['point'][0] - b['point'][0]) * IMAGE_PX
        dy = (a['point'][1] - b['point'][1]) * IMAGE_PX
        return math.hypot(dx, dy) <= NEAR_PX

    def observe(self, name, arguments, result):
        """Record one act/look result. Returns the public summary to attach, or None."""
        if name not in ('act', 'look') or not isinstance(result, dict):
            return None
        error = result.get('error') if isinstance(result.get('error'), dict) else None
        if error is not None and error.get('action_step_used') is False:
            return self.summary()
        self.step += 1
        primitive = (arguments or {}).get('primitive', name) if isinstance(arguments, dict) else name
        if name == 'act' and not error and primitive == 'navigate_to':
            self.segment += 1
        if name == 'act' and error is not None:
            view, point = self._target(arguments)
            entry = {'step': self.step, 'primitive': primitive, 'view': view, 'point': point,
                     'segment': self.segment, 'outcome': str(error.get('code') or 'failed')}
            entry['repeats'] = 1 + sum(1 for old in self.failures if self._similar(old, entry))
            self.failures = [*self.failures, entry][-MAX_KEPT:]
        return self.summary()

    def summary(self):
        if not self.failures:
            return None
        lines = []
        for f in self.failures:
            where = (f"{f['view']} [{f['point'][0]:.2f}, {f['point'][1]:.2f}]" if f['point'] is not None
                     else (f['view'] or 'no point'))
            stance = 'this stance' if f['segment'] == self.segment else f'stance {f["segment"]}'
            lines.append(f"step {f['step']}: {f['primitive']} at {where}, {stance} -> {f['outcome']}"
                         + (f" (tried {f['repeats']}x here)" if f['repeats'] > 1 else ''))
        last = self.failures[-1]
        out = {'recent_failed_attempts': lines,
               'note': ('Your own recent failed act attempts (newest last). Stance numbers count your completed '
                        'navigate_to moves.')}
        if last['repeats'] > 1 and last['segment'] == self.segment:
            out['repeat_warning'] = (
                f"{last['primitive']} has now failed {last['repeats']} times at about the same point from this "
                'stance. Repeating it is unlikely to work: choose a different point, view, stance or action.')
        return out
