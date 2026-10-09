from .base import register
from ..contracts import object_schema, INT, SkillError
from ..executors.primitives import PRIMITIVES

VISUAL_TARGET = object_schema({"image_ref": {"type":"string"}, "point": {
    "type":"array", "minItems":2, "maxItems":2, "items":{"type":"number","minimum":0,"maximum":1}}})
VISUAL_TARGET["type"] = ["object", "null"]

@register("act", "Execute one robot primitive on a point YOU select in the latest RGB. x: left=0/right=1, y: top=0/bottom=1. No object IDs or names accepted. attach/hang connect a held object to the selected compatible parent. place_under uses the simulator's Under placement sampler for the carried item and selected parent. place_next_to searches floor poses near the selected parent point and verifies the simulator's NextTo relation. wipe/sweep/vacuum/spray/spread/soak/cut require a compatible held tool and a selected target surface, source, or cuttable object. release/wait use target=null. For wait use wait_seconds (0.1–20 simulation seconds, null defaults to 5). For place_on_top placement_yaw_degrees optionally rotates the carried item about world-up relative to its current orientation. Use null for both options on other primitives. Tool completion is not task success; inspect returned RGB.", object_schema({
    "primitive":{"type":"string","enum":list(PRIMITIVES)}, "target":VISUAL_TARGET, "revision":INT,
    "placement_yaw_degrees":{"type":["number","null"],"minimum":-180,"maximum":180},
    "wait_seconds":{"type":["number","null"],"minimum":0.1,"maximum":20}}))
def act(ctx, primitive, target, revision, placement_yaw_degrees=None, wait_seconds=None):
    if placement_yaw_degrees is not None and primitive!='place_on_top':
        raise SkillError('invalid_arguments','placement_yaw_degrees is supported only for place_on_top')
    if wait_seconds is not None and primitive!='wait':
        raise SkillError('invalid_arguments','wait_seconds is supported only for wait')
    options={}
    if placement_yaw_degrees is not None:options['placement_yaw_degrees']=placement_yaw_degrees
    if wait_seconds is not None:options['seconds']=wait_seconds
    return ctx.perform(primitive, target, revision,**options)
