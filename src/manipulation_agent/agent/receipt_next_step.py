"""Harness option --receipt-next-step (2026-11-21, coordinator 12:03; default off; on in the h4 full tier).

For an act refused because the hand state does not fit (empty_hand / hand_occupied) the public receipt says what is
wrong but not what to call next. This appends one operation-only sentence chosen by the refused primitive. It uses
only the primitive name and the public error code (no simulator knowledge, no task content). A sentence already in
the message (for example the auxiliary-feedback hint for hand_occupied) is not repeated. With the option off nothing
is touched.
"""
from __future__ import annotations

PLACE = ('place_on_top', 'place_inside', 'place_next_to', 'place_under')
TOOL = ('wipe', 'sweep', 'vacuum', 'spray', 'spread', 'soak', 'cut', 'pour', 'scoop')
STAGE = (' open/close and unheld switches require robot.hand == "empty". Stage the held item on a nearby clear '
         'surface first, perform open/close/toggle, then re-grasp.')     # the auxiliary-feedback text, verbatim

EMPTY = {
    **{p: ' The hand is empty. First grasp the object you want to place (act grasp on it in a current image), then '
          'call this action on the destination.' for p in PLACE},
    **{p: ' The hand is empty. First grasp the part you want to attach, then call attach/hang on the mounting point.'
       for p in ('attach', 'hang')},
    **{p: ' The hand is empty. These actions use a held tool or vessel: first grasp it, then call this action on the '
          'target.' for p in TOOL},
    'release': ' The hand is already empty; there is nothing to release.',
}
OCCUPIED = {
    'open': STAGE, 'close': STAGE,
    'grasp': ' The hand is holding an object. Put it down first (place_on_top on a nearby clear surface), then grasp.',
    **{p: ' The hand is holding an object. To switch the object in your hand, select that object in an image where it '
          'is visible; to switch something else, put down what you hold first.' for p in ('toggle_on', 'toggle_off')},
}


def next_step(result, tool_name, arguments):
    """Append the sentence for this refusal to result['error']['message'] (in place). Returns the result."""
    if tool_name != 'act' or not isinstance(result, dict) or not isinstance(arguments, dict):
        return result
    error = result.get('error')
    if not isinstance(error, dict):
        return result
    table = {'empty_hand': EMPTY, 'hand_occupied': OCCUPIED}.get(error.get('code'))
    extra = table.get(str(arguments.get('primitive'))) if table else None
    message = str(error.get('message') or '')
    if extra and extra.strip() not in message:
        error['message'] = message + extra
    return result
