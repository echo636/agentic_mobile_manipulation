"""Presentation and client policy for economical long-running exploration."""
from copy import deepcopy

DEFAULT_CONTEXT_POLICY = {
    'blocking_tool_yield_ms': 2147483647,
    'auto_compact_token_limit': 48000,
    'image_delivery': 'on_demand_cached',
    'compact_development_results': True,
}


def context_policy(config):
    # Old session records keep their actual presentation unless explicitly
    # migrated. prepare installs DEFAULT_CONTEXT_POLICY for new sessions.
    policy = {**dict(blocking_tool_yield_ms=None, auto_compact_token_limit=None,
                     image_delivery='automatic', compact_development_results=False),
              **config.get('context_policy', {})}
    if policy['image_delivery'] not in {'automatic', 'on_demand_cached'}:
        raise ValueError('Unknown exploration image delivery mode')
    if type(policy['compact_development_results']) is not bool:
        raise ValueError('compact_development_results must be a boolean')
    wait = policy['blocking_tool_yield_ms']
    if wait is not None and (type(wait) is not int or not 60000 <= wait <= 2147483647):
        raise ValueError('blocking_tool_yield_ms must be an integer between 60000 and 2147483647')
    limit = policy['auto_compact_token_limit']
    if limit is not None and (type(limit) is not int or limit < 16000):
        raise ValueError('auto_compact_token_limit must be an integer of at least 16000')
    return policy


def development_reply(execution, run_id):
    """Keep definitive results, errors and evidence refs; archive call lists."""
    result = deepcopy(execution)
    calls = result.pop('calls', [])
    result['call_summary'] = {
        'count': len(calls),
        'failed_count': sum(c.get('ok') is False for c in calls),
        'first_failed': next((c for c in calls if c.get('ok') is False), None),
        'last': calls[-1] if calls else None,
    }
    result['full_record'] = f'development/{run_id}/execution.json'
    return result


def deliver_rgb(name, config):
    return name == 'get_cached_rgb' or context_policy(config)['image_delivery'] == 'automatic'


def tune_prepared_project(project, config, workflow, tool_names):
    """Update resumes as well as new starts; retain provider/auth/storage args."""
    import json
    policy = context_policy(config)
    overrides = {
        'developer_instructions': workflow,
        'mcp_servers.manipulation.enabled_tools': tool_names,
        'model_reasoning_effort': config.get('reasoning_effort', 'high'),
    }
    if policy['auto_compact_token_limit'] is not None:
        overrides['model_auto_compact_token_limit'] = policy['auto_compact_token_limit']
    if policy['blocking_tool_yield_ms'] is not None:
        overrides['features.code_mode_host'] = True
        overrides['features.code_mode.enabled'] = True
        overrides['features.code_mode.default_exec_yield_time_ms'] = policy['blocking_tool_yield_ms']
    argv = []
    index = 0
    while index < len(project.argv):
        arg = project.argv[index]
        if arg == '-c' and index + 1 < len(project.argv):
            key = project.argv[index + 1].split('=', 1)[0]
            if key in overrides:
                index += 2
                continue
        if arg in {'--enable', '--disable'} and index + 1 < len(project.argv):
            if 'features.' + project.argv[index + 1] in overrides:
                index += 2
                continue
        argv.append(arg)
        index += 1
    settings = [part for key, value in overrides.items() for part in ('-c', key + '=' + json.dumps(value))]
    # Both exec and exec resume use a final '-' for stdin. The stored base argv
    # remains resumable and receives the same effective settings next time.
    if not argv or argv[-1] != '-':
        raise ValueError('Expected stdin Codex project')
    argv[-1:-1] = settings
    project.argv = argv
    return project
