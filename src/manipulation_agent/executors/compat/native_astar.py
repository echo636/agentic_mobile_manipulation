"""OmniGibson 3.9.2's map A*, with lazily populated distance scores."""
import heapq
import math


def astar(search_map, start, goal, eight_connected=True):
    """Preserve native neighbors, costs and heap tie-breaking without a full-grid dict."""
    import torch

    offsets = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if eight_connected:
        offsets += [(1, 1), (-1, -1), (1, -1), (-1, 1)]
    rows, cols = search_map.shape
    open_set = [(0, start)]
    came_from = {}
    visited = set()
    # Native code constructs a tensor pair and Python dict entry for every
    # map cell, even for a one-cell move. Missing scores already mean infinity.
    g_score = {start: 0}
    while open_set:
        _, current = heapq.heappop(open_set)
        visited.add(current)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return torch.tensor(path[::-1])
        for dx, dy in offsets:
            neighbor = (current[0] + dx, current[1] + dy)
            if not (0 <= neighbor[0] < rows and 0 <= neighbor[1] < cols):
                continue
            if search_map[neighbor] == 0 or neighbor in visited:
                continue
            cost = 1 if dx == 0 or dy == 0 else math.sqrt(2)
            tentative = g_score[current] + cost
            if tentative < g_score.get(neighbor, math.inf):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                distance = math.sqrt((neighbor[0] - goal[0]) ** 2 + (neighbor[1] - goal[1]) ** 2)
                heapq.heappush(open_set, (tentative + distance, neighbor))
    return None


def install_sparse_native_astar():
    from omnigibson.maps import traversable_map

    # Keep TraversableMap's erosion, coordinates, waypoints and path length.
    # Only its search helper changes; other native planners are untouched.
    traversable_map.astar = astar
    return {'status': 'configured', 'scope': 'process_local_TraversableMap_astar',
            'change': 'allocate_distance_scores_only_for_discovered_cells',
            'native_neighbors_costs_tie_breaking_and_paths': 'preserved'}


def sync_open_door_map(backend):
    """Remove only opened doors' baked footprints from the native static map.

    The asset's no-door image supplies the floor underneath each doorway.
    Closed doors retain the original map. This changes neither collision
    geometry nor native erosion / A*, and does not project movable clutter.
    """
    scene = backend.env.scene
    trav = scene.trav_map
    cache = getattr(backend, '_native_door_map_cache', None)
    if cache is None:
        doors = [obj for obj in scene.objects if obj.fixed_base and 'door' in obj.category]
        if not doors:
            backend._native_door_map_cache = ([], [])
            return []
        import cv2
        from pathlib import Path
        from omnigibson.object_states import Open
        from omnigibson.utils.asset_utils import get_scene_path
        from omnigibson.utils.transform_utils import quat2mat

        originals = [floor.clone() for floor in trav.floor_map]
        no_door = {}
        patches = []
        for obj in doors:
            if Open not in obj.states:
                continue
            pos, quat = obj.get_position_orientation()
            rotation = quat2mat(quat)
            center = pos + rotation @ obj.scaled_bbox_center_in_base_frame
            half = rotation.abs() @ (obj.native_bbox * obj.scale) / 2
            floor = min(range(len(originals)), key=lambda i: abs(float(center[2]-half[2])-trav.floor_heights[i]))
            if floor not in no_door:
                path = Path(get_scene_path(scene.scene_model)) / 'layout' / f'floor_trav_no_door_{floor}.png'
                pixels = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) if path.is_file() else None
                if pixels is not None:
                    pixels = cv2.resize(pixels, tuple(reversed(originals[floor].shape)))
                    pixels[pixels < 255] = 0
                    pixels = backend.torch.as_tensor(pixels)
                no_door[floor] = pixels
            if no_door[floor] is None:
                continue
            lo = trav.world_to_map((center-half)[:2]-trav.map_resolution).tolist()
            hi = trav.world_to_map((center+half)[:2]+trav.map_resolution).tolist()
            window = tuple(slice(min(size, max(0, low)), min(size, max(0, high+2)))
                           for low, high, size in zip(lo, hi, originals[floor].shape))
            original = originals[floor][window]
            opened = backend.torch.maximum(original, no_door[floor][window])
            patches.append((obj.states[Open], obj.name, floor, window, opened))
        cache = backend._native_door_map_cache = (originals, patches)
    originals, patches = cache
    for floor in {patch[2] for patch in patches}:
        trav.floor_map[floor] = originals[floor].clone()
    opened_doors = []
    for state, name, floor, window, opened in patches:
        if state.get_value():
            trav.floor_map[floor][window] = opened
            opened_doors.append(name)
    return opened_doors
