from .base import register
from ..contracts import object_schema, INT, SkillError
from ..executors.primitives import PRIMITIVES

VISUAL_TARGET = object_schema({"image_ref": {"type":"string"}, "point": {
    "type":"array", "minItems":2, "maxItems":2, "items":{"type":"number","minimum":0,"maximum":1}}})
VISUAL_TARGET["type"] = ["object", "null"]

@register("act", """Perform one action on a visible target in the latest RGB. Select its image_ref and normalized [x,y]: x runs left to right, y top to bottom. Use the current revision and null for unused options.
Request manipulation directly when the target is visible; the robot can approach it if needed. navigate_to selects a floor destination or an object to approach. Grasping a container carries its supported contents too. open/close require an empty hand. You can toggle a held object; toggling another object requires an empty hand.
place_inside selects the receiving container; place_on_top its support surface; place_under the fixture to go under; place_next_to the reference object. attach joins the held item to a compatible mount; hang does the same for a nail, hook or hanger. fold requires empty hands and cloth resting on a surface.
wipe/sweep/vacuum/spray/spread/soak/cut require a compatible held tool. Select the surface to treat, the fluid source for soak, or the object to cut.
pour transfers loose objects and a material portion from the held vessel, board or ready dispenser into an open container or onto a support. scoop collects a material portion from the selected source into the held open vessel. Both retain the held object. Read transferred/remaining counts and source_has_more_material; one call may leave some contents behind.
release/wait take target=null. wait_seconds is 0.1–20 simulation seconds, default 5. placement_yaw_degrees rotates a place_on_top/place_next_to item about world-up relative to its current orientation. For place_on_top, placement_roll_degrees rotates about world X before yaw; 180 flips an upright item upside down. The released item must have physical support.
Inspect the returned images and feedback, including after failures.""", object_schema({
    "primitive":{"type":"string","enum":list(PRIMITIVES)}, "target":VISUAL_TARGET, "revision":INT,
    "placement_yaw_degrees":{"type":["number","null"],"minimum":-180,"maximum":180},
    "placement_roll_degrees":{"type":["number","null"],"minimum":-180,"maximum":180},
    "wait_seconds":{"type":["number","null"],"minimum":0.1,"maximum":20}}))
def act(ctx, primitive, target, revision, placement_yaw_degrees=None, wait_seconds=None,
        placement_roll_degrees=None):
    if placement_yaw_degrees is not None and primitive not in {'place_on_top','place_next_to'}:
        raise SkillError('invalid_arguments','placement_yaw_degrees is supported only for place_on_top and place_next_to')
    if wait_seconds is not None and primitive!='wait':
        raise SkillError('invalid_arguments','wait_seconds is supported only for wait')
    if placement_roll_degrees is not None and primitive!='place_on_top':
        raise SkillError('invalid_arguments','placement_roll_degrees is supported only for place_on_top')
    options={}
    if placement_yaw_degrees is not None:options['placement_yaw_degrees']=placement_yaw_degrees
    if placement_roll_degrees is not None:options['placement_roll_degrees']=placement_roll_degrees
    if wait_seconds is not None:options['seconds']=wait_seconds
    return ctx.perform(primitive, target, revision,**options)
