"""Harness options --place-names and --place-return on the MCP proxy (2026-11-21, both default off).

--place-names: act and look accept an optional name_here (text, at most 40 characters): the place where the robot is
after this call gets that name in state_memory. A navigate_to whose name_here matches an existing name is merged into
that place. The name is the model's own; nothing is recognised by the harness. A wrong name never blocks the action.
--place-return: act gets the primitive return_to with option place (a name given with name_here, or a loc id from
state_memory). The proxy resolves it to the observation revision of that place's latest visit and calls the bridge
act with return_revision; poses, routes and lengths stay on the bridge. Unknown places are refused without a step.
With both off, the catalog and every call are untouched (byte-identical model-visible content).
"""
from __future__ import annotations

from copy import deepcopy

NAME_MAX = 40
NAME_SCHEMA = {'type': ['string', 'null'], 'maxLength': NAME_MAX}
PLACE_SCHEMA = {'type': ['string', 'null'], 'maxLength': NAME_MAX}
NAME_TEXT = (' Optional name_here (text up to 40 characters, or null) names the place where the robot is after this '
             'call; the name appears in state_memory.')
RETURN_TEXT = (' return_to drives back to a place the robot has visited: target=null and place = a name you gave '
               'with name_here or a loc id from state_memory. It takes one step; travel time is charged as usual.')
NAME_HELP = 'name_here must be text of 1 to 40 characters, for example {"name_here": "kitchen counter"}.'


def place_catalog(catalog, *, names=False, ret=False):
    """The model-facing catalog: adds name_here / return_to+place; hides the bridge-only return_revision."""
    if not (names or ret):
        return catalog
    out = []
    for spec in catalog:
        spec = deepcopy(spec)
        props = spec.get('inputSchema', {}).get('properties', {})
        if spec['name'] in ('act', 'look') and names:
            props['name_here'] = deepcopy(NAME_SCHEMA)
            spec['description'] = spec.get('description', '') + NAME_TEXT
        if spec['name'] == 'act':
            props.pop('return_revision', None)
            if ret:
                enum = props.get('primitive', {}).get('enum')
                if isinstance(enum, list) and 'return_to' not in enum:
                    enum.append('return_to')
                props['place'] = deepcopy(PLACE_SCHEMA)
                spec['description'] = spec.get('description', '') + RETURN_TEXT
        out.append(spec)
    return out


def refusal(message):
    return {'ok': False, 'error': {'code': 'invalid_arguments', 'action_step_used': False,
                                   'world_may_have_changed': False, 'message': message}}


def prepare(name, arguments, memory, *, names=False, ret=False):
    """-> (forward_arguments, observe_arguments, name_here, early_result). Without options: unchanged."""
    if not (names or ret) or name not in ('act', 'look') or not isinstance(arguments, dict):
        return arguments, arguments, None, None
    forward = dict(arguments)
    name_here = forward.pop('name_here', None) if names else None
    place = forward.pop('place', None) if ret else None
    observe = dict(forward)
    if name == 'act' and ret and forward.get('primitive') == 'return_to':
        resolved = memory.resolve_place(place) if memory is not None else None
        if resolved is None:
            known = list(memory.named_places()) if memory is not None else []
            return forward, observe, name_here, refusal(
                'return_to needs place = a name you gave with name_here'
                + (f' ({", ".join(chr(34) + k + chr(34) for k in known[:8])})' if known else '')
                + ' or a loc id from state_memory, and target=null, e.g. {"primitive": "return_to", "target": null, '
                  '"place": "loc_3", "revision": <latest revision>}.')
        loc, revision = resolved
        forward['return_revision'] = revision
        observe = {**observe, 'primitive': 'navigate_to', 'location': loc}
    elif name == 'act' and names and forward.get('primitive') == 'navigate_to' and memory is not None:
        loc = memory.location_for_name(name_here) if isinstance(name_here, str) else None
        if loc is not None:
            observe = {**observe, 'location': loc}
    return forward, observe, name_here, None


def apply_name(result, memory, name_here):
    """After the call: bind name_here to the current place and report it (never blocks the action)."""
    if name_here is None or memory is None or not isinstance(result, dict):
        return result
    if not isinstance(name_here, str) or not name_here.strip() or len(name_here.strip()) > NAME_MAX:
        result['place_name'] = {'recorded': False, 'message': 'The action was carried out; the name was not recorded. ' + NAME_HELP}
        return result
    error = result.get('error') if isinstance(result.get('error'), dict) else {}
    if error.get('action_step_used') is False:
        return result   # refused calls do not move the robot; nothing to name
    note = memory.name_location(memory.current_location, name_here)
    result['place_name'] = {'recorded': not note.get('ignored'), 'place': note.get('location'),
                            'name': note.get('name'), 'message': note.get('message')}
    return result
