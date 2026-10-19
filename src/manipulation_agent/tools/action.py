from .base import register
from ..contracts import object_schema, INT, SkillError
from ..executors.primitives import PRIMITIVES

VISUAL_TARGET = object_schema({"image_ref": {"type":"string"}, "point": {
    "type":"array", "minItems":2, "maxItems":2, "items":{"type":"number","minimum":0,"maximum":1}}})
VISUAL_TARGET["type"] = ["object", "null"]

@register("act", "Execute one robot primitive on a point YOU select in the latest RGB. x: left=0/right=1, y: top=0/bottom=1. No object IDs or names accepted. A successful grasp effect reports grasped_category for the item actually picked; check it before choosing a destination. attach/hang connect a held object to the selected compatible parent. place_under uses the simulator's Under placement sampler for the carried item and selected parent. place_next_to searches floor poses near the selected parent point and verifies the simulator's NextTo relation. wipe/sweep/vacuum/spray/spread/soak/cut require a compatible held tool and a selected target surface, source, or cuttable object. For the high-level hot-drink task, boil targets a visible kettle or stove and returns water_boiled=true after successful heating. release/wait use target=null. For wait use wait_seconds (0.1–20 simulation seconds, null defaults to 5). For place_on_top placement_yaw_degrees optionally rotates the carried item about world-up relative to its current orientation. For grasp, confirm_bulk_grasp=true deliberately permits lifting a support with multiple loose items or furniture-sized object after a confirmation error; leave false for ordinary objects. A confirmation error makes no grasp and returns fresh RGB. Tool completion is not task success; inspect returned RGB.", object_schema({
    "primitive":{"type":"string","enum":list(PRIMITIVES)}, "target":VISUAL_TARGET, "revision":INT,
    "placement_yaw_degrees":{"type":["number","null"],"minimum":-180,"maximum":180},
    "wait_seconds":{"type":["number","null"],"minimum":0.1,"maximum":20},
    "confirm_bulk_grasp":{"type":"boolean"}}))
def act(ctx, primitive, target, revision, placement_yaw_degrees=None, wait_seconds=None, confirm_bulk_grasp=False):
    if placement_yaw_degrees is not None and primitive!='place_on_top':
        raise SkillError('invalid_arguments','placement_yaw_degrees is supported only for place_on_top')
    if wait_seconds is not None and primitive!='wait':
        raise SkillError('invalid_arguments','wait_seconds is supported only for wait')
    if confirm_bulk_grasp and primitive!='grasp':
        raise SkillError('invalid_arguments','confirm_bulk_grasp is supported only for grasp')
    options={}
    if placement_yaw_degrees is not None:options['placement_yaw_degrees']=placement_yaw_degrees
    if wait_seconds is not None:options['seconds']=wait_seconds
    if confirm_bulk_grasp:options['confirm_bulk_grasp']=True
    return ctx.perform(primitive, target, revision,**options)


SEMANTIC_PRIMITIVES = (
    'grasp', 'place_on_top', 'place_inside', 'place_near_heating_element',
    'pour', 'boil', 'open', 'close', 'toggle_on', 'toggle_off', 'release', 'wait',
)


@register('act_semantic',
          'Execute one high-level magic action for the hot-drink planning experiment. '
          'Use a target name from initialize.semantic_targets, such as water_bottle, '
          'kettle, cabinet, stove, mug, tray, or coffee_table. The executor walks '
          'continuously to the named target, then checks the requested operation. '
          'It does not choose your action order or report task-goal truth. '
          'release and wait use target=null; wait_seconds is 0.1–20 simulated seconds.',
          object_schema({
              'primitive': {'type': 'string', 'enum': list(SEMANTIC_PRIMITIVES)},
              'target': {'type': ['string', 'null']},
              'revision': INT,
              'wait_seconds': {'type': ['number', 'null'], 'minimum': 0.1, 'maximum': 20},
          }))
def act_semantic(ctx, primitive, target, revision, wait_seconds=None):
    if wait_seconds is not None and primitive != 'wait':
        raise SkillError('invalid_arguments', 'wait_seconds applies only to wait')
    options = {'seconds': wait_seconds} if wait_seconds is not None else {}
    return ctx.perform(primitive, target, revision, **options)
