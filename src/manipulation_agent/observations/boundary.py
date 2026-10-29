"""Strict allowlist: fail closed if an observer accidentally includes simulator truth."""
import copy
from ..contracts import SkillError

OBS_KEYS = {"images", "observation_mode"}
IMAGE_KEYS = {"image_ref", "view", "width", "height", "mime_type", "sha256"}
CAPTURE_KEYS = {'capture_id','captured_at','sim_step','sim_time_seconds'}

def public_observation(value: dict, revision: int) -> dict:
    if set(value) not in (OBS_KEYS, OBS_KEYS | {'capture'}) or value["observation_mode"] != "rgb_only":
        raise RuntimeError("RGB observation boundary violation")
    if not value["images"]:
        raise RuntimeError("RGB observer returned no image")
    for image in value["images"]:
        if set(image) != IMAGE_KEYS:
            raise RuntimeError("RGB image metadata boundary violation")
    if 'capture' in value:
        capture = value['capture']
        if set(capture) != CAPTURE_KEYS or {i['view'] for i in value['images']} != {'front','back','left','right'} or len(value['images']) != 4:
            raise RuntimeError('Four-camera capture boundary violation')
    return {**copy.deepcopy(value), "revision":revision}

ERROR_MESSAGES = {
    'articulation_endpoint_unresolved':'The executor could not find a checked, stable open position for the selected object\'s door or lid.',
    'look_rotation_blocked':'The requested turn has no checked clearance for the robot and carried item. Use the current four views; choose a different general approach if needed instead of repeating this turn.',
    'needs_macro_navigation':'The selected target is outside local adjustment range. Use navigate_to for a closer general approach, then select the target in the returned RGB.',
    'local_adjustment_blocked':'The executor could not find a safe, visible, reachable operation stance around this selected target. Inspect the returned RGB and choose a different general approach or report blocked; do not repeat the same operation or make tiny navigation corrections.',
    'local_adjustment_exhausted':'The executor reached its approach planning or operation work budget. Inspect the current RGB before choosing a different general approach; do not blindly repeat the same action.',
    "stale_image_ref":"Select a point in the latest returned image.",
    "no_surface_at_point":"The selected pixel could not be grounded to a surface. Inspect RGB and choose another point.",
    "invalid_visual_target":"The selected surface cannot be used for this action. Inspect RGB and select a different visible point.",
    "robot_surface_selected":"The selected pixel is on the robot's own body or arm. Select a visible surface of the intended object in the returned RGB.",
    "target_changed":"The selected object changed during this action and its result could not be verified. Inspect the updated RGB before selecting the next target.",
    "navigation_unreachable":"The motor executor could not reach the selected visual point. Choose a visible alternative.",
    "navigation_search_exhausted":"The navigation search reached its per-action work limit. This does not prove the target is unreachable. Inspect the returned RGB and choose another visible approach.",
    "navigation_invalid_start":"The executor cannot safely navigate from its current base position. Changing the target point will not repair this; stop repeating navigation and report blocked if the limitation persists.",
    "navigation_stalled":"The navigation executor made no progress. Inspect the fresh RGB before trying one alternative; do not repeat the same request.",
    "navigation_path_blocked":"The selected navigation endpoint became blocked or unobserved. Inspect fresh RGB and choose a different visible approach.",
    "navigation_unobserved_route":"No observed free route reaches this point. Inspect fresh RGB and choose a different visible approach.",
    "out_of_reach":"The selected surface cannot be reached from the current base position. Manipulation does not move the base. Call act with primitive=navigate_to to choose a closer visible approach, then reselect the target in the returned RGB before retrying.",
    "hand_occupied":"The executor cannot perform this action while carrying an object.",
    "empty_hand":"The executor cannot place, attach or release without a held object.",
    "container_closed":"The executor could not access the requested placement or grasp. Reinspect the opening in RGB.",
    "fixed_object":"The selected object cannot be moved by this executor.",
    "particle_target":"The selected point is on loose particles, which cannot be grasped directly. Select a visible container or a suitable tool instead.",
    "pre_condition_error":"The executor rejected this operation. Reinspect the selected object and action using RGB.",
    "no_removable_material":"The held tool found no material it can remove from the selected surface. The surface may already be clean, or the tool conditions may not match. Inspect RGB before repeating the operation.",
    "sampling_error":"The executor could not find a valid placement; inspect the new RGB before recovery.",
    "post_condition_error":"The executor could not verify its operation after settling; inspect the new RGB.",
    "physics_instability":"The simulator reported an unstable physical state; do not repeat this action blindly.",
    "postcondition_error":"The requested effect did not remain valid after physics settling. Inspect the new RGB. For placement or closing, check for obstructing contents and try a different placement before retrying; repeating the same command may repeat the failure.",
    "execution_error":"Execution failed or partially completed; inspect the new RGB.",
    "action_timeout":"The executor exhausted this action's time or step budget.",
    "episode_timeout":"The episode wall-clock deadline has expired; no further action can start.",
    "episode_cancelled":"Episode closure was requested; the active action stopped before final scoring.",
    "sampling_budget_exhausted":"The executor exhausted its placement sampling budget.",
    "unsupported_relation":"The selected objects do not support the requested operation.",
    "same_transfer_vessel":"The material source and receiving vessel are the same object. A transfer requires two different vessels.",
    "invalid_transfer_destination":"The selected receiving object has no supported volume for loose material. Select the receiving vessel itself.",
    "material_dispenser_off":"The material dispenser is switched off. Enable it with toggle_on before dispensing.",
    "material_source_empty":"No transferable contents or ready dispenser were found. Pour moves loose objects and material; scoop moves material. Check the selected source and its contents.",
}

# Only fixed stages/messages cross the RGB boundary; exception text and private
# maps, positions, object identities and rejection counts stay executor-private.
LOCAL_STANCE_MESSAGES = {
    'local_stance_collision': 'No sampled collision-free operation stance was found within reach of the selected target. Inspect the target surface and surrounding space before choosing a different general approach or reporting blocked. This does not prove the target is globally unreachable.',
    'local_target_visibility': 'Collision-free local stances were considered, but none passed all checks including visibility of the selected point. Inspect all four views and choose a different visible surface point or general approach.',
    'local_departure_clearance': 'Some local stances passed collision and visibility checks but lacked a checked departure. Choose a more open general approach, allowing room for the robot and carried item.',
    'local_articulation_clearance': 'Local preparation could not admit a stance with safe door or lid movement and departure. Choose a different general approach with space around the opening.',
    'local_loaded_departure': 'Local preparation could not admit a safe pickup and departure with the item. Approach from another side with room to lift and carry it.',
    'local_route': 'Operation stances around the selected target passed preliminary checks, but no complete checked route was found. Choose a different general approach; nearby space alone does not establish a usable route.',
    'local_planning_budget': 'Local preparation reached its search budget before finding a fully checked stance and route. This does not prove the approach is impossible. Inspect RGB and choose a materially different approach.',
    'local_candidate_preparation_budget': 'The executor exhausted stance preparation before admitting any route endpoint; route search has not started. Inspect all four views and select another visible point or approach. This does not prove the target is unreachable.',
}


def public_execution_error(exc: SkillError) -> dict:
    code = exc.code if exc.code in ERROR_MESSAGES else "execution_error"
    result = {"code":code,"message":ERROR_MESSAGES[code],"world_may_have_changed":exc.changed}
    if code in {'local_adjustment_blocked','local_adjustment_exhausted'} and exc.stage in LOCAL_STANCE_MESSAGES:
        result.update(stage=exc.stage,message=LOCAL_STANCE_MESSAGES[exc.stage])
    return result
