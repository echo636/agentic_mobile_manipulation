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
