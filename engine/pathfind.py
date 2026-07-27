"""pathfind.py -- the engine's generic room-graph BFS.

Every MUD eventually needs the same shape: walk ``Room.exits`` in memory,
find the nearest room matching a predicate, and return either the full
list of exit labels or just the first hop. That mechanism -- not any
particular game's idea of "evil zone", "private home door", or "pocket
enter/exit" -- is what lives here.

SUPERS' Cadence town simulation (``supers/pathfind.py``) wraps these
helpers with its own ``passable`` gate (evil_zone / vampire_safe /
hunter_safe / evil_ward / lodging ACL / no_loiter) and keeps pocket
homeward lore (``next_hop_homeward``, wilderness gateways, preferred
enter aliases) entirely in supers -- same boundary-rule call as
Stage 7's ``roll_weighted_outcome`` (docs/plans/two_repo_purity.md
Phase 7 Stage 6). Callers inject an ``edge_ok(from_room, neighbor)``
callback so the engine never needs to know why an edge is blocked.

Pure graph walk: no networking, no database, no game loop, zero
``supers`` imports.
"""

from __future__ import annotations

from collections import deque


def path_directions_to(start, predicate, *, edge_ok, max_nodes=None):
    """BFS full path of exit labels from ``start`` to nearest matching room.

    ``edge_ok(from_room, neighbor)`` decides whether a cardinal exit may
    be traversed -- the game supplies its own passability rules.
    ``max_nodes`` caps how many rooms the BFS may visit (``None`` =
    uncapped). Returns a list like ``["north", "east", "in"]``, or
    ``[]`` when already there / unreachable / inputs incomplete.
    """
    if start is None or predicate is None:
        return []
    if predicate(start):
        return []
    seen = {start}
    # room -> (prev_room, direction_taken_to_reach_room)
    came_from = {}
    queue = deque()
    limit = int(max_nodes) if max_nodes is not None else None

    for direction, neighbor in (start.exits or {}).items():
        if neighbor is None or neighbor in seen:
            continue
        if not edge_ok(start, neighbor):
            continue
        seen.add(neighbor)
        came_from[neighbor] = (start, direction)
        queue.append(neighbor)
    goal = None
    while queue:
        if limit is not None and len(seen) >= limit:
            break
        room = queue.popleft()
        if predicate(room):
            goal = room
            break
        for direction, neighbor in (room.exits or {}).items():
            if neighbor is None or neighbor in seen:
                continue
            if not edge_ok(room, neighbor):
                continue
            seen.add(neighbor)
            came_from[neighbor] = (room, direction)
            queue.append(neighbor)
    if goal is None:
        return []
    # Reconstruct path start -> goal.
    hops = []
    cur = goal
    while cur is not start:
        prev, direction = came_from[cur]
        hops.append(direction)
        cur = prev
    hops.reverse()
    return hops


def next_step_toward(start, predicate, *, edge_ok, max_nodes=None):
    """BFS from ``start`` for the nearest matching room; return FIRST hop.

    Returns the first exit label on the path, or ``None`` when already
    there / unreachable. Thin wrapper over ``path_directions_to``.
    """
    path = path_directions_to(
        start, predicate, edge_ok=edge_ok, max_nodes=max_nodes,
    )
    if not path:
        return None
    return path[0]


def path_to_room(start, dest, *, edge_ok, max_nodes=None):
    """Directions list from ``start`` to exact ``dest`` Room, or []."""
    if dest is None:
        return []
    return path_directions_to(
        start, lambda r, goal=dest: r is goal,
        edge_ok=edge_ok, max_nodes=max_nodes,
    )
