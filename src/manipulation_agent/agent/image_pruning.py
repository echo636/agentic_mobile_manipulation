"""Prune only old image parts in an outbound Responses request.

Uses jinkai's fixed-placeholder approach from habitat-gs/bench/tool/
relay_image_prune_proxy.py. Authored text, item order and current pixels stay intact.
"""
IMAGE_TYPES = {'input_image', 'image', 'image_url'}
PLACEHOLDER = '[omitted stale surround image]'


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


def prune_stale_images(payload, keep_latest_items=1):
    """Mutate the request copy; zero keeps all images for matched controls."""
    items = payload.get('input') if isinstance(payload, dict) else None
    stats = {'image_items': 0, 'kept_images': 0, 'dropped_images': 0}
    if not isinstance(items, list):
        return stats
    counts = [(i, count_images(item)) for i, item in enumerate(items)]
    bearing = [(i, count) for i, count in counts if count]
    stats['image_items'] = len(bearing)
    keep_from = max(0, len(bearing) - keep_latest_items) if keep_latest_items else 0
    for position, (index, count) in enumerate(bearing):
        if position < keep_from:
            items[index] = replace_images(items[index])
            stats['dropped_images'] += count
        else:
            stats['kept_images'] += count
    return stats
