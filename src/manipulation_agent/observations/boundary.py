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
    'needs_macro_navigation':'The selected target is outside local adjustment range. Use navigate_to for a closer general approach, then select the target in the returned RGB.',
    'local_adjustment_blocked':'The executor could not find a safe, visible, reachable local stance for this selected target. Inspect the returned RGB and choose a different general approach or report blocked; do not repeat the same operation or make tiny navigation corrections.',
    'local_adjustment_exhausted':'The executor reached its bounded local motion or operation budget. Inspect the current RGB before choosing a different general approach; do not blindly repeat the same action.',
    "stale_image_ref":"Select a point in the latest returned image.",
    "no_surface_at_point":"The selected pixel could not be grounded to a surface. Inspect RGB and choose another point.",
    "invalid_visual_target":"The selected surface cannot be used for this action. Inspect RGB and select a different visible point.",
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
    "sampling_error":"The executor could not find a valid placement; inspect the new RGB before recovery.",
    "post_condition_error":"The executor could not verify its operation after settling; inspect the new RGB.",
    "physics_instability":"The simulator reported an unstable physical state; do not repeat this action blindly.",
    "postcondition_error":"The requested effect did not remain valid after physics settling. Inspect the new RGB. For placement or closing, check for obstructing contents and try a different placement before retrying; repeating the same command may repeat the failure.",
    "execution_error":"Execution failed or partially completed; inspect the new RGB.",
    "action_timeout":"The executor exhausted this action's time or step budget.",
    "episode_timeout":"The episode wall-clock deadline has expired; no further action can start.",
    "episode_cancelled":"Episode closure was requested; the active action stopped before final scoring.",
    "sampling_budget_exhausted":"The executor exhausted its placement sampling budget.",
    "unsupported_relation":"The selected object does not support this placement or attachment operation.",
}

def public_execution_error(exc: SkillError) -> dict:
    code = exc.code if exc.code in ERROR_MESSAGES else "execution_error"
    return {"code":code,"message":ERROR_MESSAGES[code],"world_may_have_changed":exc.changed}
