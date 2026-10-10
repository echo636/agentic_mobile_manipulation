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
    "stale_image_ref":"Select a point in the latest returned image.",
    "no_surface_at_point":"The selected pixel could not be grounded to a surface. Inspect RGB and choose another point.",
    "invalid_visual_target":"The selected surface cannot be used for this action. Inspect RGB and select a different visible point.",
    "target_changed":"The selected object changed during this action and its result could not be verified. Inspect the updated RGB before selecting the next target.",
    "navigation_unreachable":"The motor executor could not reach the selected visual point. Choose a visible alternative.",
    "navigation_invalid_start":"The executor cannot safely navigate from its current base position. Changing the target point will not repair this; stop repeating navigation and report blocked if the limitation persists.",
    "navigation_stalled":"The navigation executor made no progress. Inspect the fresh RGB before trying one alternative; do not repeat the same request.",
    "navigation_path_blocked":"The selected navigation endpoint became blocked or unobserved. Inspect fresh RGB and choose a different visible approach.",
    "navigation_unobserved_route":"No observed free route reaches this point. Inspect fresh RGB and choose a different visible approach.",
    "out_of_reach":"The selected surface is beyond the executor's manipulation reach. Approach using RGB first.",
    "hand_occupied":"The executor cannot perform this action while carrying an object.",
    "empty_hand":"The executor cannot place, attach or release without a held object.",
    "container_closed":"The executor could not access the requested placement or grasp. Reinspect the opening in RGB.",
    "fixed_object":"The selected object cannot be moved by this executor.",
    "pre_condition_error":"The executor rejected this operation. Reinspect the selected object and action using RGB.",
    "sampling_error":"The executor could not find a valid placement; inspect the new RGB before recovery.",
    "post_condition_error":"The executor could not verify its operation after settling; inspect the new RGB.",
    "physics_instability":"The simulator reported an unstable physical state; do not repeat this action blindly.",
    "postcondition_error":"The executor could not complete the requested action; inspect the new RGB.",
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
