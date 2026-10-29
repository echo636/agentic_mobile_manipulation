"""Scripted simulator regression for the recorded grocery garage-door waypoint.

This uses private world positions and is never a model benchmark attempt.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import traceback
import math

from manipulation_agent.executors.omnigibson_rgb import RGBBackend

START = (-3.7999997, 2.6999998)
FLOOR_CLICK = (-2.7683582, 1.3365023, 0.00294)
DOOR_NAME = 'door_bexenl_0'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    backend = None
    result = {'kind': 'scripted_private_geometry_regression', 'model_used': False,
              'task_success_assessed': False, 'start_xy': START, 'selected_floor': FLOOR_CLICK}
    try:
        backend = RGBBackend('carrying_in_groceries', 301, args.output, seed=0, record_video=False)
        from omnigibson import object_states
        door = next(obj for obj in backend.env.scene.objects if obj.name == DOOR_NAME)
        opened = bool(door.states[object_states.Open].set_value(True, fully=True))
        if not opened:
            raise RuntimeError('Garage interior door did not open')
        current, orientation = backend.robot.get_position_orientation()
        pose = current.clone()
        pose[0], pose[1] = START
        backend.robot.set_position_orientation(pose, orientation)
        backend.robot.keep_still()
        from manipulation_agent.executors.gt_navigation import GridMap, mask_dynamic_obstacles
        trav = backend.env.scene.trav_map
        floor = min(range(len(trav.floor_heights)), key=lambda i: abs(float(pose[2])-trav.floor_heights[i]))
        raw = trav.floor_map[floor].clone().cpu().numpy()
        eroded = trav._erode_trav_map(trav.floor_map[floor].clone()).cpu().numpy()
        height, width = eroded.shape
        origin = (-width*trav.map_resolution/2, -height*trav.map_resolution/2)
        static = GridMap(width, height, float(trav.map_resolution), origin,
                         (eroded != 0).astype('uint8').tobytes())
        furniture, names = backend._dynamic_floor_obstacles(float(pose[2]))
        grid, masked = mask_dynamic_obstacles(static, furniture, margin=.38, start=START)
        start_cell, target_cell = grid.cell(START), grid.cell(FLOOR_CLICK[:2])
        reachable = {start_cell}
        queue = [start_cell]
        for cell in queue:
            for nxt, _ in grid.neighbors(cell):
                if nxt not in reachable:
                    reachable.add(nxt)
                    queue.append(nxt)
        nearest = min(reachable, key=lambda cell: math.dist(grid.world(cell), FLOOR_CLICK[:2]))
        result['grid_diagnostic'] = {
            'resolution_m': grid.resolution, 'origin': origin,
            'start_cell': start_cell, 'target_cell': target_cell,
            'target_raw_free': bool(raw[target_cell]),
            'target_eroded_free': bool(static.navigable(target_cell)),
            'target_dynamic_free': bool(grid.navigable(target_cell)),
            'target_reachable': target_cell in reachable,
            'reachable_cells': len(reachable), 'nearest_reachable_cell': nearest,
            'nearest_reachable_xy': grid.world(nearest),
            'nearest_reachable_distance_m': math.dist(grid.world(nearest), FLOOR_CLICK[:2]),
            'dynamic_obstacles': names, 'dynamic_masked_cells': masked,
            'door_joint_positions': door.get_joint_positions().cpu().tolist(),
            'door_open_predicate': bool(door.states[object_states.Open].get_value()),
        }
        original_radius = trav.default_erosion_radius
        erosion_sweep = []
        for radius in (0.0, 0.25, 0.35, 0.45, original_radius):
            trav.default_erosion_radius = radius
            variant = trav._erode_trav_map(trav.floor_map[floor].clone()).cpu().numpy()
            variant_grid = GridMap(width, height, grid.resolution, origin,
                                   (variant != 0).astype('uint8').tobytes())
            variant_grid, _ = mask_dynamic_obstacles(variant_grid, furniture, margin=.38, start=START)
            reached = {start_cell}
            frontier = [start_cell]
            for cell in frontier:
                for nxt, _ in variant_grid.neighbors(cell):
                    if nxt not in reached:
                        reached.add(nxt)
                        frontier.append(nxt)
            closest = min(reached, key=lambda cell: math.dist(variant_grid.world(cell), FLOOR_CLICK[:2]))
            erosion_sweep.append({'radius_m': radius, 'target_free': variant_grid.navigable(target_cell),
                                  'target_reachable': target_cell in reached,
                                  'nearest_reachable_m': math.dist(variant_grid.world(closest), FLOOR_CLICK[:2])})
        trav.default_erosion_radius = original_radius
        result['grid_diagnostic']['erosion_sweep'] = erosion_sweep
        result['grid_diagnostic']['robot_chassis_extent'] = backend.robot.reset_joint_pos_aabb_extent[:2].cpu().tolist()
        point = backend.torch.tensor(FLOOR_CLICK, dtype=pose.dtype, device=pose.device)
        anchor = SimpleNamespace(get_position_orientation=lambda: (point, None), selected_object=None)
        result['motion'] = backend._navigate(anchor, 700)
        final = backend.robot.get_position_orientation()[0].cpu().tolist()
        result['final_position'] = final
        result['crossed_doorway'] = final[1] < 2.0
        result['status'] = 'passed' if result['crossed_doorway'] else 'failed'
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                      traceback=traceback.format_exc())
    finally:
        (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items() if k != 'traceback'}), flush=True)
        if backend is not None:
            try:
                backend.close()
            except SystemExit:
                # OmniGibson shutdown may exit the process; preserve the
                # probe's own pass/fail status instead.
                pass
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
