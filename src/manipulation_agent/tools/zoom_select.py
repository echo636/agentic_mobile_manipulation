"""Harness option --two-step-select (v12, route A, default off): the `zoom` tool and the act target it enables.

`zoom` crops the SAME camera frame around a coarse point and returns it enlarged with a grid; the next act may then
target {"zoom_ref", "point"} inside the zoomed image, which the MCP proxy maps back to whole-image coordinates before
the call reaches the simulator. zoom moves nothing and captures nothing: it is not an act/look step, costs no robot
time and never reaches the simulator. One zoom between two act/look calls; a zoom_ref serves the next act only.

Shared by the MCP proxy and the controller, whose frozen catalog must match the proxy's tool list exactly.
Only the robot's own camera image is used.
"""
from __future__ import annotations

from copy import deepcopy

ZOOM_HALF_PX = 64
ZOOM_SCALE = 4
ZOOM_GRID = 8

ZOOM_TOOL = {
    'name': 'zoom',
    'description': ('Enlarge part of the newest camera image around a coarse point, to select a small target more '
                    'precisely. Returns a zoom_ref and the enlarged image with a grid. In the next act you may give '
                    'target {"zoom_ref": <zoom_ref>, "point": [u, v]} with u, v normalized to the ZOOMED image; it is '
                    'converted to the full image. zoom does not move the robot or take a new picture. At most one '
                    'zoom between two act/look calls; a zoom_ref is valid for the next act only.'),
    'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['image_ref', 'point'],
                    'properties': {
                        'image_ref': {'type': 'string', 'description': 'an image_ref from the newest observation'},
                        'point': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                                  'items': {'type': 'number', 'minimum': 0, 'maximum': 1},
                                  'description': 'coarse [x, y], normalized to that full image'}}},
}

ZOOM_TARGET = {'type': 'object', 'additionalProperties': False, 'required': ['zoom_ref', 'point'],
               'properties': {'zoom_ref': {'type': 'string'},
                              'point': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                                        'items': {'type': 'number', 'minimum': 0, 'maximum': 1}}}}


def two_step_catalog(catalog):
    """Catalog with act.target also accepting a zoom target, plus the zoom tool (idempotent)."""
    out = []
    for spec in catalog:
        if spec['name'] == ZOOM_TOOL['name']:
            continue
        spec = deepcopy(spec)
        props = (spec.get('inputSchema') or {}).get('properties') or {}
        if spec['name'] == 'act' and 'target' in props and 'anyOf' not in props['target']:
            props['target'] = {'anyOf': [props['target'], deepcopy(ZOOM_TARGET)]}
        out.append(spec)
    return [*out, deepcopy(ZOOM_TOOL)]


def zoom_window(width, height, point, half=ZOOM_HALF_PX):
    """Pixel window (left, top, size) of the zoom crop around a normalized point, kept inside the image."""
    size = min(2 * half, width, height)
    x = min(width - 1, max(0, round(float(point[0]) * (width - 1))))
    y = min(height - 1, max(0, round(float(point[1]) * (height - 1))))
    return min(max(0, x - size // 2), width - size), min(max(0, y - size // 2), height - size), size


def zoom_image(data, point):
    """(jpeg, region): crop enlarged ZOOM_SCALE x with a grid; region = normalized [x0, y0, x1, y1] of the full image."""
    import io
    from PIL import Image, ImageDraw
    image = Image.open(io.BytesIO(data)).convert('RGB')
    width, height = image.size
    left, top, size = zoom_window(width, height, point)
    side = size * ZOOM_SCALE
    crop = image.crop((left, top, left + size, top + size)).resize((side, side), Image.LANCZOS)
    draw = ImageDraw.Draw(crop)
    for k in range(1, ZOOM_GRID):
        at = round(k * side / ZOOM_GRID)
        for colour, line in (((0, 0, 0), 3), ((255, 255, 0), 1)):
            draw.line([at, 0, at, side], fill=colour, width=line)
            draw.line([0, at, side, at], fill=colour, width=line)
        draw.text((at + 2, 2), f'{k / ZOOM_GRID:.3f}', fill=(255, 255, 0))
        draw.text((2, at + 2), f'{k / ZOOM_GRID:.3f}', fill=(255, 255, 0))
    out = io.BytesIO()
    crop.save(out, format='JPEG', quality=90)
    # same (w - 1) convention as the executor's normalized -> pixel mapping
    region = [left / (width - 1), top / (height - 1), (left + size - 1) / (width - 1), (top + size - 1) / (height - 1)]
    return out.getvalue(), [round(v, 6) for v in region]


def zoom_to_full(region, point):
    u, v = float(point[0]), float(point[1])
    return [round(region[0] + u * (region[2] - region[0]), 6), round(region[1] + v * (region[3] - region[1]), 6)]
