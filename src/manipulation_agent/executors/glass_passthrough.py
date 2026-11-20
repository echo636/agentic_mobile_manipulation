"""rc1 A4 (2026-11-20, benchmark side): resolve a click on a fixed glass pane to the surface the RGB shows behind it.

The model selects pixels in RGB, where a window, glass door or appliance-door pane is transparent. The executor's depth
image renders the pane as opaque, so the backprojected point lies on the glass and the action is applied to the pane
(v12/v12b: 212 act clicks on glass, ~60 failures of the "item behind the glass" kind; T6 sol r1: three placements
onto the staging table seen through the patio door refused). The selected pixel is never changed; only the depth
artefact is corrected. This is not selection snapping: snapping moves the point onto something the model did not click.

A hit is passed through only if all hold:
1. glass pane: the hit visual mesh's link name contains "glass" or is "pane"/"pane<N>" (case-insensitive);
2. the owning object is fixed (fixed_base: doors, windows, appliance and cabinet doors); movable objects (cups,
   bottles, jars, trays) are never passed through, whatever their material;
3. the pane is not a horizontal surface (glass table top or shelf): its link AABB's thinnest axis is not z;
4. the action is not open / close / toggle_on / toggle_off on an object with an Open or ToggledOn state (clicking a
   glass door to operate it still selects that door or appliance).
Passing through takes the first ray hit beyond the pane that is not itself a passable pane; no hit -> the existing
"no surface at the selected pixel" receipt. Grasp / place through a closed openable pane (e.g. a closed microwave door)
gets the existing container_closed receipt and changes nothing. Private record: `through_glass`.
Switch: MAS_GLASS_PASSTHROUGH=off restores v12b behaviour (default on).
"""
import os
import re

GLASS_LINK = re.compile(r'glass|^pane\d*$', re.I)
OPERATE = ('open', 'close', 'toggle_on', 'toggle_off')
GRASP_PLACE = ('grasp', 'place_on_top', 'place_inside')
HORIZONTAL_MAX_THICKNESS_M = 0.05
BEYOND_M = 0.005


def enabled():
    return os.environ.get('MAS_GLASS_PASSTHROUGH', 'on') != 'off'


def split_mesh_path(path):
    """'/World/scene_0/<object>/<link>/visuals/...' -> (object, link) or (None, None)."""
    m = re.search(r'/World/scene_0/([^/]+)/([^/]+)/', str(path or ''))
    return (m.group(1), m.group(2)) if m else (None, None)


def is_glass_link(link):
    return bool(link) and bool(GLASS_LINK.search(str(link)))


def horizontal(link_aabb):
    lo, hi = link_aabb
    ext = [float(hi[i]) - float(lo[i]) for i in range(3)]
    return ext.index(min(ext)) == 2 and ext[2] <= HORIZONTAL_MAX_THICKNESS_M


def operates_owner(primitive, has_open, has_toggle):
    return primitive in OPERATE and (has_open or has_toggle)


def decide(primitive, link, fixed, link_aabb, has_open, has_toggle):
    """(pass_through: bool, reason: str) for one resolved hit."""
    if not enabled():
        return False, 'switch_off'
    if not is_glass_link(link):
        return False, 'not_glass'
    if not fixed:
        return False, 'movable_object'
    if link_aabb is not None and horizontal(link_aabb):
        return False, 'horizontal_pane'
    if operates_owner(primitive, has_open, has_toggle):
        return False, 'operates_glass_owner'
    return True, 'pass_through'


def next_surface(hits, depth_along_ray, passable_body):
    """hits: [(distance along the unit ray, rigid body path)]. First hit beyond the pane that is not a passable pane."""
    for dist, body in sorted(hits):
        if dist <= depth_along_ray + BEYOND_M:
            continue
        if passable_body(body):
            continue
        return dist, body
    return None
