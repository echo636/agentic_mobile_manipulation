import importlib.util
from pathlib import Path


def proxy_module():
    path = Path(__file__).resolve().parents[1] / 'scripts/responses_compat_proxy.py'
    spec = importlib.util.spec_from_file_location('responses_compat_proxy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qwen_namespace_tool_round_trip():
    proxy = proxy_module()
    request = {
        'model': 'qwen3.8-flash',
        'include': ['reasoning.encrypted_content'],
        'reasoning': {'effort': 'low', 'summary': 'auto'},
        'tools': [
            {'type': 'namespace', 'name': 'mcp__manipulation', 'tools': [
                {'type': 'function', 'name': 'ping', 'parameters': {'type': 'object'}}]},
            {'type': 'namespace', 'name': 'multi_agent_v1', 'tools': [
                {'type': 'function', 'name': 'spawn_agent'}]},
        ],
        'input': [{'type': 'function_call', 'namespace': 'mcp__manipulation',
                   'name': 'ping', 'call_id': 'call-1'},
                  {'type': 'reasoning', 'summary': [{'text': 'unsupported'}]}],
    }
    request, removed, removed_reasoning, names = proxy.compatible_request(request)
    assert removed is True
    assert removed_reasoning == 1
    assert request['reasoning'] == {'effort': 'low'}
    assert [tool['name'] for tool in request['tools']] == ['mcp__manipulation__ping']
    assert request['input'][0]['name'] == 'mcp__manipulation__ping'
    assert 'namespace' not in request['input'][0]
    assert len(request['input']) == 1
    event = {'item': {'type': 'function_call', 'name': 'mcp__manipulation__ping'},
             'response': {'output': [{'type': 'function_call',
                                      'name': 'mcp__manipulation__ping'}]}}
    proxy.restore_namespaces(event, names)
    assert event['item']['namespace'] == 'mcp__manipulation'
    assert event['item']['name'] == 'ping'
    assert event['response']['output'][0]['namespace'] == 'mcp__manipulation'


def test_luna_drops_unverifiable_reasoning_without_dropping_tool_history():
    proxy = proxy_module()
    request = {
        'model': 'gpt-5.6-luna',
        'include': ['reasoning.encrypted_content'],
        'input': [
            {'type': 'reasoning', 'id': 'rs-1', 'encrypted_content': 'opaque'},
            {'type': 'message', 'id': 'msg-1', 'role': 'assistant', 'content': 'Working'},
            {'type': 'function_call', 'id': 'fc-1', 'call_id': 'call-2', 'name': 'ping'},
            {'type': 'custom_tool_call', 'id': 'call-1', 'name': 'ping'},
            {'type': 'custom_tool_call_output', 'call_id': 'call-1', 'output': 'pong'},
        ],
    }
    request, removed, count, _ = proxy.compatible_request(request)
    assert removed is True
    assert count == 1
    assert [item['type'] for item in request['input']] == [
        'message', 'function_call', 'custom_tool_call', 'custom_tool_call_output']
    assert request['input'][0] == {'type': 'message', 'role': 'assistant',
                                   'content': 'Working'}
    assert request['input'][1] == {'type': 'function_call', 'call_id': 'call-2',
                                   'name': 'ping'}
    assert request['input'][2] == {'type': 'custom_tool_call', 'name': 'ping'}


def test_old_rgb_images_are_trimmed_but_recent_images_and_text_remain():
    proxy = proxy_module()
    payload = {'input': [
        {'type': 'function_call_output', 'output': [
            {'type': 'input_text', 'text': f'Feedback {number}'},
            {'type': 'input_image', 'image_url': f'data:image/png;base64,{number}'},
        ]} for number in range(55)
    ]}
    assert proxy.trim_history_images(payload, keep=24) == 31
    assert [item['output'][0]['text'] for item in payload['input']] == [
        f'Feedback {number}' for number in range(55)]
    assert all(len(item['output']) == 1 for item in payload['input'][:31])
    assert all(len(item['output']) == 2 for item in payload['input'][31:])
