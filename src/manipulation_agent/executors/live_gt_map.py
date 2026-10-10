"""Private GT walkability with live door links and measured base clearance.

The GT floor image retains static furniture/walls. Only door geometry is removed
from the source image and reinserted at its current articulated pose. This is
not online SLAM and no map, depth, or simulator object identity goes to the model.
"""
from pathlib import Path
import hashlib
import math


def disk_footprint(radius, resolution):
    import numpy as np
    n = math.ceil(radius / resolution)
    yy, xx = np.mgrid[-n:n+1, -n:n+1]
    return ((xx * resolution)**2 + (yy * resolution)**2 <= radius**2 + 1e-12).astype('uint8')


def build_navigation_grid(backend, floor):
    import cv2
    import numpy as np
    from .gt_navigation import GridMap
    scene = backend.env.scene
    trav = scene.trav_map
    resolution = float(trav.map_resolution)
    source = Path(scene.scene_dir) / 'layout' / f'floor_trav_no_door_{floor}.png'
    cache = getattr(backend, '_gt_floor_cache', None)
    if cache is None:
        cache = backend._gt_floor_cache = {}
    if floor not in cache:
        if source.is_file():
            raw = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
            h, w = trav.floor_map[floor].shape
            raw = cv2.resize(raw, (w, h))
            raw = (raw == 255).astype('uint8')
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        else:
            # Missing variant cannot authorize removing closed doors. Keep the
            # original conservative map and make the limitation explicit.
            raw = (trav.floor_map[floor].cpu().numpy() != 0).astype('uint8')
            source = Path(scene.scene_dir) / 'layout' / f'floor_trav_{floor}.png'
            source_hash = hashlib.sha256(raw.tobytes()).hexdigest()
        cache[floor] = raw, str(source), source_hash
    raw, source_path, source_hash = cache[floor]
    occupied = raw.copy()
    h, w = occupied.shape
    origin = np.array([-w*resolution/2, -h*resolution/2])
    floor_z = float(trav.floor_heights[floor])
    base = backend.robot.get_position_orientation()[0].cpu().numpy()
    # Measure the chassis, not the whole reset-pose arm AABB. The circumscribed
    # disk admits arbitrary base yaw and cannot be smaller than the chassis.
    chassis = []
    for link in backend.robot.links.values():
        if link.is_meta_link:
            continue
        points = link.collision_boundary_points_world
        if points is None:
            continue
        points = points.cpu().numpy()
        if points[:, 2].min() < floor_z + .20:
            low = points[points[:, 2] <= floor_z + .30]
            if len(low):
                chassis.extend(low[:, :2] - base[:2])
    if not chassis:
        raise ValueError('Robot chassis collision geometry unavailable for GT footprint')
    chassis_radius = float(np.linalg.norm(np.asarray(chassis), axis=1).max())
    radius = chassis_radius + .01
    doors = []
    for obj in scene.objects:
        if obj.category not in {'door', 'sliding_door'}:
            continue
        link_count = 0
        for link in obj.links.values():
            if link.is_meta_link:
                continue
            # Keep links separate: the convex hull of an entire open door and
            # its frame would erroneously fill the open doorway again.
            for mesh in link.collision_meshes.values():
                if mesh.get_attribute('physics:collisionEnabled') is False:
                    continue
                points = mesh.points_in_parent_frame
                if points is None or not len(points):
                    continue
                points = link.transform_local_points_to_world(points).cpu().numpy()
                if points[:, 2].max() < floor_z + .05 or points[:, 2].min() > floor_z + 1.8:
                    continue
                # Project each actual collision mesh. This also preserves door
                # frames and the leaf's current angle, not just an Open flag.
                pixel = (points[:, :2] - origin) / resolution
                hull = cv2.convexHull(np.round(pixel * 256).astype('int32'))
                cv2.fillConvexPoly(occupied, hull, 0, shift=8)
                link_count += 1
        doors.append({'name':obj.name, 'meshes':link_count,
                      'joint_positions':obj.get_joint_positions().cpu().tolist()})
    kernel = disk_footprint(radius, resolution)
    free = cv2.erode(occupied, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    grid = GridMap(w, h, resolution, tuple(origin), free.tobytes())
    return grid, {'source':source_path, 'source_sha256':source_hash,
        'live_doors':doors, 'chassis_radius_m':chassis_radius, 'clearance_radius_m':radius,
        'footprint':'measured_chassis_circumscribed_disk', 'kernel_shape':list(kernel.shape),
        'dynamic_collision_check':'door_mesh_occupancy_at_plan_time',
        'collision_substrate':'GT_static_furniture_and_live_door_meshes',
        'held_object_footprint_included':False}
