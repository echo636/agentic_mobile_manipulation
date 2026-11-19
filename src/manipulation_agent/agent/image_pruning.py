"""Prune only old image parts in an outbound Responses request.

Uses jinkai's fixed-placeholder approach from habitat-gs/bench/tool/
relay_image_prune_proxy.py. Authored text, item order and current pixels stay intact.
"""
import json

IMAGE_TYPES = {'input_image', 'image', 'image_url'}
PLACEHOLDER = '[omitted stale surround image]'
CAMERA_TOOLS = {'act', 'look', 'initialize', 'inspect_target', 'finish'}
INVALID_CAMERA_ERRORS = {'observation_unavailable', 'episode_cancelled', 'episode_timeout',
                         'episode_closed', 'initialize_first'}


def is_image(node):
    if not isinstance(node, dict):
        return False
    kind = node.get('type')
    mime = node.get('mimeType') or node.get('mime_type')
    return (isinstance(kind, str) and kind.lower() in IMAGE_TYPES or
            isinstance(mime, str) and mime.startswith('image/') and isinstance(node.get('data'), str))


def count_images(node):
    if is_image(node):
        return 1
    if isinstance(node, dict):
        return sum(count_images(value) for value in node.values())
    if isinstance(node, list):
        return sum(count_images(value) for value in node)
    return 0


def replace_images(node):
    if is_image(node):
        return {'type': 'input_text', 'text': PLACEHOLDER}
    if isinstance(node, dict):
        return {key: replace_images(value) for key, value in node.items()}
    if isinstance(node, list):
        return [replace_images(value) for value in node]
    return node


def retired_camera_items(items):
    """Locate RGB issued before the robot withdrew its observation or closed."""
    calls = {}
    camera_items = []
    retired_through = -1
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get('call_id'), str):
            continue
        call_id, kind = item['call_id'], item.get('type')
        if kind in ('function_call', 'custom_tool_call'):
            name = item.get('name', '')
            if isinstance(name, str):
                if name.startswith('mcp__manipulation__'):
                    calls[call_id] = name.removeprefix('mcp__manipulation__')
                elif item.get('namespace') == 'mcp__manipulation':
                    calls[call_id] = name
            continue
        tool = calls.get(call_id)
        if kind not in ('function_call_output', 'custom_tool_call_output') or tool not in CAMERA_TOOLS:
            continue
        camera_items.append(index)
        output = item.get('output')
        parts = output if isinstance(output, list) else [{'type': 'input_text', 'text': output}]
        for part in parts:
            if not isinstance(part, dict) or part.get('type') not in ('text', 'input_text'):
                continue
            value = part.get('text')
            if not isinstance(value, str):
                continue
            value = value.rsplit('\nOutput:', 1)[-1].lstrip()
            if not value.startswith('{'):
                continue
            try:
                receipt = json.loads(value)
            except ValueError:
                continue
            if not isinstance(receipt, dict):
                continue
            error = receipt.get('error')
            if ((tool == 'finish' and receipt.get('closed') is True) or
                    (isinstance(error, dict) and isinstance(error.get('code'), str)
                     and error['code'] in INVALID_CAMERA_ERRORS)):
                retired_through = index
            break
    return {index for index in camera_items if index <= retired_through}


def prune_stale_images(payload, keep_latest_items=1):
    """Mutate the request copy; zero keeps all images for matched controls."""
    items = payload.get('input') if isinstance(payload, dict) else None
    stats = {'image_items': 0, 'kept_images': 0, 'dropped_images': 0}
    if not isinstance(items, list):
        return stats
    counts = [(i, count_images(item)) for i, item in enumerate(items)]
    bearing = [(i, count) for i, count in counts if count]
    stats['image_items'] = len(bearing)
    retired = retired_camera_items(items) if keep_latest_items and bearing else set()
    available = [index for index, _ in bearing if index not in retired]
    keep = set(available[-keep_latest_items:]) if keep_latest_items else set(available)
    for index, count in bearing:
        if index not in keep:
            items[index] = replace_images(items[index])
            stats['dropped_images'] += count
        else:
            stats['kept_images'] += count
    return stats
