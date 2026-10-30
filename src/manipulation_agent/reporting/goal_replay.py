"""Offline interpretation of explicit working notes, never generated model reasoning."""
import json


def goal_state(notes):
    try:
        value = json.loads(notes.get('content', ''))
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get('format') == 'goal-tree-v2':
        goals = value.get('goals')
        if not isinstance(value.get('root_id'), str) or not isinstance(goals, list):
            return None
    elif value.get('format') == 'goal-tree-v1' and isinstance(value.get('L1'), dict):
        goals = value.get('L2')
        if not isinstance(goals, list):
            return None
    else:
        return None
    # Keep invalid authored text in the raw notes; only render recognized fields.
    # Graph validity is a display concern, not a gate on the agent's notes.
    if any(not isinstance(g, dict) for g in goals):
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


def goal_replacement_review(events):
    """Adapt PR #6's plan audit to accepted task_notes writes and nested v2 trees.

    Removed IDs may have been renamed or merged; they are review candidates.
    A non-tree write breaks the comparison instead of inventing missing nodes.
    """
    results = {e['request_id']: e.get('result') or {} for e in events
               if e.get('kind') == 'tool_result' and e.get('name') == 'task_notes'
               and e.get('request_id') is not None}
    previous = {}
    transitions = []
    for event in events:
        args = event.get('arguments') or {}
        if (event.get('kind') != 'tool_call' or event.get('name') != 'task_notes'
                or args.get('operation') != 'write'):
            continue
        reply = results.get(event.get('request_id'), {})
        if reply.get('ok') is not True:
            continue
        tree = goal_state(args)
        if tree is None or tree.get('format') != 'goal-tree-v2':
            previous = {}
            transitions.append({'call_id': event['id'], 'comparison': 'unrecognized_notes_format'})
            continue
        nodes = tree['goals']
        ids = [node.get('id') for node in nodes]
        if any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids):
            previous = {}
            transitions.append({'call_id': event['id'], 'comparison': 'ambiguous_goal_ids'})
            continue
        current = {node['id']: node for node in nodes}
        transitions.append({
            'call_id': event['id'], 'version': (reply.get('notes') or {}).get('version'),
            'change': tree.get('change'),
            'removed_unresolved': [node for key, node in previous.items()
                                   if key not in current and node.get('status') in ('pending', 'active')],
            'added': [key for key in current if key not in previous],
            'newly_done': [key for key, node in current.items() if node.get('status') == 'done'
                           and previous.get(key, {}).get('status') != 'done'],
            'rewritten': [key for key, node in current.items() if key in previous
                          and any(node.get(field) != previous[key].get(field) for field in
                                  ('goal', 'parent_id', 'covers', 'depends_on', 'check'))]})
        previous = current
    return transitions
