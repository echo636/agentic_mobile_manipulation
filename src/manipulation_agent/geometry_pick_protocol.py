"""Opt-in intentful Manip protocol; historical waypoint/recipe runs stay readable."""
import os


def enabled():
    return os.environ.get('MAS_MANIP_ENGINE') == 'geometry_pick_v1'


def specs(legacy):
    names = {'initialize', 'observe_scene', 'navigate_to', 'wait', 'finish', 'task_notes'}
    rows = [row for row in legacy if row['name'] in names]
    rows.append({'name': 'manip',
        'description': 'Geometry-driven pick at the current station. Backend checkpoints the start state, generates contact frames, selects the arm, checks IK/collisions, executes each planned batch open loop, closes the native assisted gripper, lifts and verifies hold. Failed attempts restore the checkpoint and retain failure evidence. Spectator and wrist RGB videos are observation only. No recipe or model-authored joint/EEF waypoints. No base motion. Inspect the failure and rollback status before a changed attempt; do not replay unchanged calls.',
        'inputSchema': {'type': 'object', 'properties': {
            'operation': {'type': 'string', 'enum': ['pick']},
            'target': {'type': 'string'},
            'arm': {'type': 'string', 'enum': ['auto', 'left', 'right']},
            'lift_m': {'type': 'number', 'minimum': .05, 'maximum': .20},
            'revision': {'type': 'integer', 'minimum': 0}},
            'required': ['operation', 'target', 'arm', 'lift_m', 'revision'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False, 'openWorldHint': False},
        '_meta': {'mas': {'profile': 'oracle_waypoints', 'mutates_world': True,
            'requires_fresh_observation': True, 'closes_episode': False, 'motion_source': 'geometry_pick_v1',
            'execution_mode': 'open_loop_rollback',
            'observer_video_views': ['spectator', 'left_wrist', 'right_wrist']}}})
    return rows


SYSTEM_PROMPT = '''You control R1Pro in a privileged Oracle3D geometry-pick experiment.
Call initialize first. Choose navigation targets using the latest surround RGB
image_ref and normalized pixel. Manip videos are spectator and wrist RGB for
observation only, never trajectory feedback.
Navigate while empty using the existing ideal/kinematic navigator. Never navigate
while holding anything. After arrival inspect the fresh state, then call
manip(operation="pick", target="wine_bottle.n.01_1", arm="auto", lift_m=0.10,
revision=<current revision>). The backend computes grasp frames, arm, collision
checks and timed trajectories. Each planned batch executes open loop. Failed
attempts restore the pre-call checkpoint; inspect rollback status and retained
failure evidence. The lift is planned after native retention is established.
Do not write code, recipes, joint targets or EEF
waypoints. You have no shell, reset, or evaluator access. Use task_notes to record
your plan and real evidence. Runtime ok is not independent acceptance. Grasping
uses OmniGibson native assisted retention, not pure friction. Inspect returned
failure phase, actual poses and native retention before any changed attempt.
Do not blindly repeat a failed Manip. If local reach fails you may reposition
only while empty, based on current RGB/geometry. Finish after verified lift and
stable hold, or report the concrete failure. This goal is a single pick, not the
whole sorting task. No arbitrary per-tick model planning is required.
'''
