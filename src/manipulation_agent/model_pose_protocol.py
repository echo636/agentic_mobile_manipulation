"""Model-authored EEF goals, planned by cuRobo; separate from historical pick."""
import os

from .contracts import INT, STRING, object_schema

ENGINE = 'model_pose_v1'
PLANNER = 'StanfordVL/curobo@78612f45cef52c3fa0298de243a54cd7ca614414'
POSITION_TOLERANCE_M = .01
ORIENTATION_TOLERANCE_RAD = .08


def enabled():
    return os.environ.get('MAS_MANIP_ENGINE') in {ENGINE, 'model_program_v1'}


def specs(legacy):
    from .posture_goals import goal_schema
    names = {'initialize', 'observe_scene', 'navigate_to', 'wait', 'finish', 'task_notes'}
    rows = [row for row in legacy if row['name'] in names]
    contact = object_schema({'object_id': STRING, 'link_name': STRING})
    contact['type'] = ['object', 'null']
    rows.append({'name': 'manip',
        'description': 'Plan to YOUR single EEF-link goal with cuRobo, then execute the complete trajectory open loop. '
        'For a saved whole-body recovery pose, use arm=both and goal={joint_positions_rad:{all18 torso/arm joint names: radians}} '
        'with gripper=hold and contact_target=null. cuRobo plans jointly for the torso and both arms; base/grippers hold their current state. '
        'Use current robot_base coordinates, metres and quaternion xyzw. Choose arm and every goal from task/Oracle3D; '
        'the backend never samples grasp poses, adds a lift, restores torso height or substitutes another goal. '
        'For EEF goals, the torso and selected arm may move; base and other arm joints are locked in planning. '
        'Under model_authored_v1, planning/execution joint-position and rate checks, native torso/arm position limits and native position-command clamps are disabled. Read initialize for the actually applied native policy. No extra torso-tilt or height envelope is imposed. '
        'gripper executes AFTER reaching the pose; hold preserves its command. '
        'contact_target=null keeps all world collisions; a named physical link permits selected finger contact only, '
        'with other robot/target pairs still checked. close requires that target and verifies native retention. '
        'Actual held free objects are included at full scale in collision planning. '
        'Choose a positive control_steps estimate for this move from its translation, rotation, payload and actual tracking feedback (30Hz). Balance smoothness and speed; there is no fixed initial estimate or preference for one step. Under model_authored_v1 the backend uses your duration without rate-based extension, then densely checks collisions, with no fixed100-step cap. '
        'Failed planning/execution/terminal verification restores the call-start snapshot and preserves evidence. '
        'Only spectator/left_wrist/right_wrist RGB is collected during execution, for observation.',
        'inputSchema': object_schema({
            'arm': {'type': 'string', 'enum': ['left', 'right', 'both']},
            'frame': {'type': 'string', 'enum': ['robot_base']},
            'goal': goal_schema(),
            'gripper': {'type': 'string', 'enum': ['hold', 'open', 'close']},
            'contact_target': contact, 'revision': INT}),
        'annotations': {'readOnlyHint': False, 'destructiveHint': True,
                        'idempotentHint': False, 'openWorldHint': False},
        '_meta': {'mas': {'profile': 'oracle_waypoints', 'mutates_world': True,
            'requires_fresh_observation': True, 'closes_episode': False,
            'motion_source': ENGINE, 'goal_source': 'model_authored', 'planner': PLANNER,
            'execution_mode': 'open_loop_rollback',
            'observer_video_views': ['spectator', 'left_wrist', 'right_wrist']}}})
    schema = rows[-1]['inputSchema']
    schema['properties']['control_steps'] = {'type': 'integer', 'minimum': 1, 'description': 'Your duration estimate for this move at30Hz, balancing smoothness and speed using current geometry, payload and actual feedback. No fixed initial value; excludes gripper.'}
    return rows


SYSTEM_PROMPT = '''You control R1Pro using task instructions and privileged Oracle 3D.
Call initialize first. YOU choose the arm and exact EEF-link target position and
orientation from the task description, object/link geometry and measured finger
offsets. All manip goals use the CURRENT robot_base frame, metres, quaternion
[x,y,z,w]. EEF origin is not the finger contact centre. Recompute coordinates
after navigation and use the latest revision. Use get_oracle_geometry if the
client presents compact geometry; never invent missing geometry.
For an explicitly requested saved whole-body recovery, supply arm="both" and
goal={joint_positions_rad:{all four torso and both seven-joint arms: radians}},
gripper="hold",contact_target=null. This is a joint-space Pose goal: cuRobo plans
all18 joints together, leaving base and both grippers unchanged. It works with
either/both hands occupied using their actual payload geometry; no EEF IK is needed.
Verify all target joints and native retention. A saved posture does not certify
reachability from an arbitrary scene or a new carried object.
Call manip(arm, frame="robot_base", goal={position_m, quaternion_xyzw},
gripper="hold|open|close", contact_target=null|{object_id,link_name}, revision).
This submits ONE model-authored goal to cuRobo for collision-aware joint planning.
For EEF goals, cuRobo may move the torso and selected arm; the base and other arm joints are
locked. It does not invent a grasp, pregrasp, lift, placement or torso recovery
goal, and never falls back to local IK or a bottle-specific sampler. Explicitly
choose each needed approach, contact, lift and destination pose yourself.
Under model_authored_v1, planner and execution checks impose no joint position,
velocity, acceleration or jerk limits. Native torso/arm position limits and native
controller position-command clamps are also disabled; initialize reports the
actually applied policy. Collision checks and native motor dynamics still apply.
No additional upright-torso goal or tilt/height envelope applies.
The gripper command runs AFTER the trajectory; hold keeps the existing command.
For contact, identify the physical link exactly: only selected fingers may touch
it; other links and obstacles remain checked. close requires contact_target and
checks actual native assisted retention. Retained free-object collision geometry
is carried at full scale. Articulated held objects need a separate constrained
planner and are rejected by this mode. Opening checks release, not placement or
task success. Pose tracking alone never proves grasp or task success.
Each trajectory runs fully open loop. There is no per-tick state feedback,
waypoint IK or corrective replanning. Spectator and wrist RGB are observer video
only. Choose each move's execution control_steps (30Hz) yourself from translation,
rotation, payload and actual tracking feedback. Balance smoothness and speed;
do not use a fixed initial estimate or mechanically request the smallest value.
Explicitly supply your positive duration estimate. With model_authored_v1
the backend uses that duration without joint-rate extension and densely checks
the actual command interpolants for collisions. Do not omit the estimate:
omission retains the planner duration. Do not use a
fixed100-step cap or truncate a path. Historical timing policies remain explicit.
Gripper commands take30 native control steps separately.
Failed calls restore their call-start snapshot; inspect rollback and the
failure before choosing a changed target. A successful call keeps its result.
Reuse request IDs for transport retries; never replay an unchanged failed action.
Navigate using latest surround RGB image_ref/pixel only while BOTH hands are
empty. You have no shell, reset, recipe or evaluator tool. Record decisions and
actual evidence in task_notes. Finish according to the user task; independent
evaluation decides task success, not tool completion.
'''
