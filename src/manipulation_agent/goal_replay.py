"""Offline interpretation of explicit working notes, never generated model reasoning."""
import json


def goal_state(notes):
    try:
        value = json.loads(notes.get('content', ''))
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or value.get('format') != 'goal-tree-v1':
        return None
    if not isinstance(value.get('L1'), dict) or not isinstance(value.get('L2'), list):
        return None
    # Keep invalid authored text in the raw notes; only render recognized fields.
    if any(not isinstance(g, dict) for g in value['L2']):
        return None
    return value


def planning_metrics(steps):
    actions = [s for s in steps if s['is_motor_action']]
    writes = [s for s in steps if s['tool'] == 'task_notes' and
              s['arguments'].get('operation') == 'write' and s['status'] == 'passed']
    reads = [s for s in steps if s['tool'] == 'read_skill' and s['status'] == 'passed' and
             s['arguments'].get('name') == 'goal-decomposition']
    trees = [s for s in writes if s.get('goal_state') is not None]
    first = actions[0]['index'] if actions else None
    return {'notes_writes': len(writes), 'goal_tree_versions': len(trees),
            'notes_before_first_action': any(s['index'] < first for s in writes) if first else None,
            'goal_tree_before_first_action': any(s['index'] < first for s in trees) if first else None,
            'goal_skill_read_before_first_action': any(s['index'] < first for s in reads) if first else None,
            'scope': 'Authored planning records and ordering only; not plan quality or task success'}
