"""Private real-simulator probe of a model-selected item taken from a movable container.

The archived robot pose, grasp point and child pose come from an autonomous
attempt. This scripted component probe does not call a model or assess task
success; it isolates whether the visible grasp motion disturbs the container.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--child', required=True)
    parser.add_argument('--parent', required=True)
    parser.add_argument('--start', nargs=3, required=True, type=float)
    parser.add_argument('--point', nargs=3, required=True, type=float)
    parser.add_argument('--staged-child-position', nargs=3, required=True, type=float)
    parser.add_argument('--disable-support-anchors', action='store_true',
                        help='Red control: reproduce the old unprotected reach')
    parser.add_argument('--reach-only', action='store_true',
                        help='Isolate the first visible reach before any grasp or approach retry')
    parser.add_argument('--retry-start', nargs=3, type=float,
                        help='Private replay of the archived closer base pose after a first reach')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    recorder = Recorder(args.output, {'task':args.task, 'instance':args.instance,
        'seed':args.seed, 'policy':'scripted_private_child_grasp_support_probe',
        'model_used':False, 'task_success_assessed':False,
        'validation_level':'real_simulator_executor_component'})
    backend = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from omnigibson.object_states import Inside
        import omnigibson.utils.transform_utils as T
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed,
                             record_video=False, inside_placement='official_volume')
        recorder.run['backend'] = backend.provenance()
        write_json(args.output / 'run.json', recorder.run)
        child = backend.env.scene.object_registry('name', args.child)
        parent = backend.env.scene.object_registry('name', args.parent)
        if child is None or parent is None:
            raise AssertionError('Archived object was not found')

        # Stage only the chosen container and its existing contents together.
        # This recreates the archived pre-grasp local arrangement without
        # choosing an object or target for the autonomous model.
        lo, hi = parent.aabb
        payload = []
        for obj in backend.env.scene.objects:
            if obj in (parent, backend.robot) or getattr(obj, 'fixed_base', True):
                continue
            a, b = obj.aabb
            center = (a + b) / 2
            if (Inside in getattr(obj, 'states', {}) and
                    bool(((center >= lo - .03) & (center <= hi + .03)).all()) and
                    obj.states[Inside].get_value(parent)):
                payload.append(obj)
        if child not in payload:
            payload.append(child)
        original_child_position = child.get_position_orientation()[0].clone()
        delta = backend.torch.tensor(args.staged_child_position,
            device=original_child_position.device) - original_child_position
        # Preserve the real basket's resting height. A world-Z translation
        # would embed the basket in the floor and create a false settling jump.
        delta[2] = 0
        for obj in (parent, *payload):
            position, orientation = obj.get_position_orientation()
            obj.set_position_orientation(position + delta, orientation)
            obj.keep_still()
        base_position, _ = backend.robot.get_position_orientation()
        base_position = base_position.clone()
        base_position[:2] = backend.torch.tensor(args.start[:2], device=base_position.device)
        base_orientation = T.euler2quat(backend.torch.tensor([0., 0., args.start[2]],
                                                     device=base_position.device))
        backend.robot.set_position_orientation(base_position, base_orientation)
        backend.robot.keep_still()
        initial_containment = {obj.name: bool(obj.states[Inside].get_value(parent))
                               for obj in payload if Inside in obj.states}
        staged_child_position = child.get_position_orientation()[0].clone()
        staged_point = backend.torch.tensor(args.point, device=base_position.device)
        staged_point[2] += staged_child_position[2] - args.staged_child_position[2]
        trace = []
        phase = 'reach'
        original_step = backend._step
        def snapshot():
            position, orientation = parent.get_position_orientation()
            cosine = min(1., abs(float((orientation * initial_orientation).sum())))
            return {'phase':phase, 'env_step':backend.steps,
                'parent_displacement_m':float(backend.torch.linalg.norm(position - initial_position)),
                'parent_rotation_deg':2 * math.degrees(math.acos(cosine)),
                'parent_position':position.tolist()}
        def traced_step(action):
            original_step(action)
            trace.append(snapshot())
        backend._step = traced_step
        pregrasp_payload = backend._grasp_payload_snapshot(child)
        support_anchors = (backend._grasp_support_anchors(child)
                           if not args.disable_support_anchors and
                           hasattr(backend, '_grasp_support_anchors') else ())
        initial_position, initial_orientation = parent.get_position_orientation()
        initial_position = initial_position.clone()
        initial_orientation = initial_orientation.clone()
        reach_anchor = (child, *support_anchors) if support_anchors else child
        backend._demo_reach(staged_point, 90, anchor=reach_anchor, action='probe_child_reach',
                            support_payload=pregrasp_payload[1])
        if args.retry_start is not None:
            phase = 'retry'
            retry_position, _ = backend.robot.get_position_orientation()
            retry_position = retry_position.clone()
            retry_position[:2] = backend.torch.tensor(args.retry_start[:2],
                                                      device=retry_position.device)
            retry_orientation = T.euler2quat(backend.torch.tensor(
                [0., 0., args.retry_start[2]], device=retry_position.device))
            backend.robot.set_position_orientation(retry_position, retry_orientation)
            backend.robot.keep_still()
            backend._demo_reach(staged_point, 90, anchor=reach_anchor, arm='right',
                                action='probe_child_reach_after_approach',
                                support_payload=pregrasp_payload[1])
        if not args.reach_only:
            phase = 'attach'
            backend._ideal_grasp(child, 50, payload_snapshot=pregrasp_payload,
                                 support_anchors=support_anchors)
            phase = 'lift'
            hand = backend.robot.eef_links[backend._demo_arm].get_position_orientation()[0]
            lifted = hand.clone()
            lifted[2] += .16
            backend._demo_reach(lifted, 90, anchor=support_anchors or None,
                                arm=backend._demo_arm, action='probe_child_lift')
            phase = 'stow'
            backend._demo_transport_pose(90, anchor=support_anchors or None)
        phase = 'free'
        for _ in range(10):
            backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
        backend._step = original_step
        write_json(args.output / 'basket_trace.json', trace)
        ending = snapshot()
        containment = {obj.name: bool(obj.states[Inside].get_value(parent))
                       for obj in payload if obj is not child and Inside in obj.states}
        status = ('passed' if ending['parent_displacement_m'] < .03 and
                  ending['parent_rotation_deg'] < 5 and
                  all(containment.values()) else 'failed')
        result = {'status':status, 'model_used':False, 'task_success_assessed':False,
                  'synthetic_container_pose_setup':True,
                  'reach_only':args.reach_only,
                  'archived_retry_start':args.retry_start,
                  'initial_containment':initial_containment,
                  'anchored_support_names':[obj.name for obj in support_anchors],
                  'parent_motion':ending,
                  'max_parent_displacement_m':max(row['parent_displacement_m'] for row in trace),
                  'max_parent_rotation_deg':max(row['parent_rotation_deg'] for row in trace),
                  'remaining_contents_inside':containment,
                  'archived_selected_world_point':args.point,
                  'staged_selected_world_point':staged_point.tolist(),
                  'staged_child_position':staged_child_position.tolist(),
                  'sim_steps':backend.steps}
        write_json(args.output / 'validation.json', result)
        recorder.finish({'status':status, 'task_success':None, 'validation':result})
        print(json.dumps(result))
        return 0 if status == 'passed' else 1
    except Exception as exc:
        result = {'status':'failed', 'model_used':False, 'task_success_assessed':False,
                  'error':str(exc), 'failure_type':type(exc).__name__}
        write_json(args.output / 'validation.json', result)
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        recorder.finish({'status':'failed', 'task_success':None, 'validation':result})
        print(json.dumps(result))
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
