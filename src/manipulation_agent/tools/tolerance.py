"""Client/interface adaptation: accept common CLI-client argument wrappings.

This is not a route A component: every arm gets it alike when the episode runs with
``--argument-tolerance on``. It never invents an action or a target. It only removes
transport wrappings and fills the latest ``revision`` when the client omitted it.
"""
from __future__ import annotations

import json

WRAPPER_KEYS = ('Arguments', 'arguments', 'args', 'params', 'parameters', 'input')
ROUTING_KEYS = frozenset({'ServerName', 'ToolName', 'serverName', 'toolName', 'server_name', 'tool_name',
                          'server', 'tool'})
MAX_UNWRAP = 3


def _strip_key(key):
    if not isinstance(key, str):
        return key
    k = key.strip()
    while len(k) >= 2 and k[0] == k[-1] and k[0] in '"\'':
        k = k[1:-1].strip()
    return k


def _clean_keys(value):
    if isinstance(value, dict):
        return {_strip_key(k): _clean_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_keys(v) for v in value]
    return value


def _maybe_json(value):
    if isinstance(value, str):
        text = value.strip()
        if text.startswith('{'):
            try:
                parsed = json.loads(text)
            except ValueError:
                return value
            return parsed
    return value


def unwrap(arguments):
    """Return (arguments, notes) with string/wrapper layers removed."""
    notes = []
    value = _maybe_json(arguments)
    if value is not arguments:
        notes.append('parsed_json_string')
    for _ in range(MAX_UNWRAP):
        if not isinstance(value, dict):
            break
        cleaned = _clean_keys(value)
        if cleaned != value:
            notes.append('stripped_quoted_keys')
            value = cleaned
        payload_keys = [k for k in value if k not in ROUTING_KEYS]
        if len(payload_keys) == 1 and payload_keys[0] in WRAPPER_KEYS:
            inner = _maybe_json(value[payload_keys[0]])
            if isinstance(inner, dict):
                notes.append('unwrapped_' + payload_keys[0])
                value = inner
                continue
        if any(k in ROUTING_KEYS for k in value) and payload_keys:
            notes.append('dropped_routing_keys')
            value = {k: v for k, v in value.items() if k not in ROUTING_KEYS}
        break
    if isinstance(value, dict):
        cleaned = _clean_keys(value)
        if cleaned != value:
            notes.append('stripped_quoted_keys')
            value = cleaned
    return value, notes


def tolerate_arguments(name, arguments, latest_revision):
    """Return (tool_name, arguments, notes). Unknown shapes pass through for normal validation."""
    value, notes = unwrap(arguments)
    if not isinstance(value, dict):
        return name, arguments, []
    value = dict(value)
    if name == 'act' and value.get('primitive') == 'look':
        look = {k: value[k] for k in ('yaw_degrees', 'revision') if k in value}
        look.setdefault('yaw_degrees', 0)
        value = look
        name = 'look'
        notes.append('routed_act_look_to_look')
    if name == 'look' and 'primitive' in value and value.get('primitive') in ('look', None):
        value.pop('primitive')
        notes.append('dropped_look_primitive')
    if name in ('act', 'look') and 'revision' not in value:
        value['revision'] = latest_revision
        notes.append('filled_latest_revision')
    if name == 'act':
        for key in ('placement_yaw_degrees', 'placement_roll_degrees', 'wait_seconds'):
            if key not in value:
                value[key] = None
    return name, value, notes
