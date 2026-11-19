"""Harness option --stance-repeat-guard (v12, route A, default off): repeated failure at one stance.

The failure memory only calls a retry "the same" when view and point are close. A model can keep failing the same
kind of action from one place while moving its point a little each time. This guard counts, per stance, consecutive
failures of one primitive regardless of point or view; at N (default 3) the receipt asks for a new stance or a new
look before continuing, and the finish guard / unfinished-turn nudge use the same wording instead of urging more
attempts in place. It only advises; nothing is blocked.

Built only from what the robot itself knows: the model's own act arguments (primitive), the receipt's ok/error, and
its own navigation history (a completed navigate_to starts a new stance). No simulator state, no object identity.
v12b: a navigate_to whose public receipt says navigation_status "already_at_closest_approach" (the base did not move)
is treated as a repeat at the same stance.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_LIMIT = 3


NO_MOVE = 'already_at_closest_approach'   # v12b public navigation status: the base did not move


def stance_message(primitive: str, count: int) -> str:
    if primitive == 'navigate_to':
        return (f'navigate_to has left the base in the same place {count} times in a row. Clicking the same target '
                'again will not move it: choose a different destination (for example a doorway or open floor on the '
                'way, then the target from there) or use look to see the surroundings from another direction.')
    return (f'{primitive} has failed {count} times in a row from this stance. Before trying it again, change '
            'the stance (navigate_to a place with a closer, more head-on view of the target) or use look to see '
            'it from another direction; repeating the action from the same stance is unlikely to work.')


class StanceRepeatGuard:
    def __init__(self, limit: int = DEFAULT_LIMIT, state_path: str | Path | None = None):
        self.limit = max(2, int(limit))
        self.state_path = Path(state_path) if state_path else None
        self.primitive = None      # primitive of the current failure run at this stance
        self.count = 0
        self.active = False        # set at `limit`; cleared by a completed navigate_to or look
        self._save()

    def _save(self):
        if self.state_path is not None:
            self.state_path.write_text(json.dumps({'active': self.active, 'primitive': self.primitive,
                                                   'count': self.count, 'limit': self.limit}) + '\n')

    def message(self) -> str | None:
        return stance_message(self.primitive, self.count) if self.active else None

    def observe(self, name, arguments, result):
        """Record one act/look result; return the advisory note to attach, or None."""
        if name not in ('act', 'look') or not isinstance(result, dict):
            return None
        error = result.get('error') if isinstance(result.get('error'), dict) else None
        if error is not None and error.get('action_step_used') is False:
            return None          # rejected before execution (budget/argument checks): not an attempt
        primitive = (arguments or {}).get('primitive', name) if isinstance(arguments, dict) else name
        ok = error is None and result.get('ok', True) is not False
        effect = result.get('effect') if isinstance(result.get('effect'), dict) else {}
        if ok and name == 'act' and primitive == 'navigate_to' and effect.get('navigation_status') == NO_MOVE:
            ok = False           # v12b: a navigate_to that left the base in place counts as a repeat at this stance
        if ok:
            # any completed act/look ends the run of consecutive failures (a look or a move is the advised change)
            self.primitive, self.count, self.active = None, 0, False
        elif name == 'act':
            if primitive == self.primitive:
                self.count += 1
            else:
                self.primitive, self.count = primitive, 1
            if self.count >= self.limit:
                self.active = True
        self._save()
        if self.active and not ok and name == 'act' and primitive == self.primitive:
            return {'stance_repeat': self.message()}
        return None


def active_message(state_path: str | Path | None) -> str | None:
    """For the controller's unfinished-turn nudge: the advisory if the guard is active, else None."""
    try:
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not state.get('active') or not state.get('primitive'):
        return None
    return stance_message(state['primitive'], int(state.get('count') or 0))
