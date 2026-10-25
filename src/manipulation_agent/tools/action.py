from .base import register
from ..contracts import object_schema, INT, SkillError
from ..executors.primitives import PRIMITIVES

VISUAL_TARGET = object_schema({"image_ref": {"type":"string"}, "point": {
    "type":"array", "minItems":2, "maxItems":2, "items":{"type":"number","minimum":0,"maximum":1}}})
VISUAL_TARGET["type"] = ["object", "null"]

@register("act", "Complete one bounded operation on a point YOU select in the latest RGB. x: left=0/right=1, y: top=0/bottom=1. No object IDs or names accepted. With target_locked_approach_v2 (see initialize.control), directly request the intended manipulation for a clearly selectable target in any current view, even before the first navigation. A preceding navigate_to is not required. The action locks that target, finds a collision-free stance within operation reach around it, and plans the approach from the current position without a fixed start-centered travel radius. Reach, visibility, carried-item collision and work budgets still apply. Reserve navigate_to for travel between areas, exploration/reacquisition when a target cannot be reliably selected, or a different approach justified by feedback. Check all four views before moving to restore target visibility. On local_adjustment_blocked/exhausted inspect RGB and change strategy instead of repeating. Legacy explicit_navigation_v1 requires explicit navigation on out_of_reach. attach/hang connect a held object to the selected compatible parent. place_under uses the simulator's Under placement sampler for the carried item and selected parent. place_next_to searches supported poses near the selected reference point and verifies the simulator's NextTo relation. wipe/sweep/vacuum/spray/spread/soak/cut require a compatible held tool and a selected target surface, source, or cuttable object. pour transfers one portion from the held vessel, board or ready dispenser into the selected open vessel; scoop transfers one portion from the selected vessel or supported loose material into the held open vessel. Both retain the held object and report amounts; a portion need not empty the source. release/wait use target=null. For wait use wait_seconds (0.1–20 simulation seconds, null defaults to 5). For place_on_top and place_next_to, placement_yaw_degrees optionally rotates the carried item about world-up relative to its current orientation. Use null for both options on other primitives. Tool completion is not task success; inspect returned RGB.", object_schema({
    "primitive":{"type":"string","enum":list(PRIMITIVES)}, "target":VISUAL_TARGET, "revision":INT,
    "placement_yaw_degrees":{"type":["number","null"],"minimum":-180,"maximum":180},
    "wait_seconds":{"type":["number","null"],"minimum":0.1,"maximum":20}}))
def act(ctx, primitive, target, revision, placement_yaw_degrees=None, wait_seconds=None):
    if placement_yaw_degrees is not None and primitive not in {'place_on_top','place_next_to'}:
        raise SkillError('invalid_arguments','placement_yaw_degrees is supported only for place_on_top and place_next_to')
    if wait_seconds is not None and primitive!='wait':
        raise SkillError('invalid_arguments','wait_seconds is supported only for wait')
    options={}
    if placement_yaw_degrees is not None:options['placement_yaw_degrees']=placement_yaw_degrees
    if wait_seconds is not None:options['seconds']=wait_seconds
    return ctx.perform(primitive, target, revision,**options)
