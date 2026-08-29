"""
gather_nodes.py -- depleting gather-node lifecycle (engine kernel).

Plain-dict mechanics for wilderness harvest nodes: seed, deplete,
respawn refresh, yield/fail curves, and SQLite persistence. No game
resource catalogs, terrain tables, or ore-geology branches.

The game layer keeps its resource definitions, scatter tables, room
glue, and ``harvest()`` orchestration; it delegates generic node math here.
Design: docs/plans/engine_liquid_flavor.md (Wave 1 Part B).
"""

from __future__ import annotations

import math

# Sentinel row marking a cell that was checked but has no gather stock.
_EMPTY_SENTINEL = "__empty__"


def seed_node(nodes, resource, capacity):
    """Insert one gather node dict when the caller has already gated placement.

    Callers skip retired ids, unknown resources, and duplicate keys.
    Ore geology typing (mine mouth element) stays in the game layer.
    """
    cap = int(capacity)
    nodes[resource] = {
        "resource": resource,
        "remaining": cap,
        "capacity": cap,
        "respawn_at_tick": None,
    }


def deplete_node(node, amount, *, now_tick, respawn_window):
    """Subtract *amount* from *node* and start respawn when depleted.

    *respawn_window* is already converted to game ticks by the caller
    (wall-clock pacing lives in the game layer / game_clock_tuning).
    """
    remaining = int(node.get("remaining", 0) or 0)
    node["remaining"] = remaining - int(amount)
    if node["remaining"] <= 0:
        node["remaining"] = 0
        node["respawn_at_tick"] = int(now_tick) + int(respawn_window)


def refresh_expired(nodes, now_tick, default_capacity):
    """Refill nodes in one room cell whose respawn timer has elapsed."""
    now = int(now_tick)
    for resource, node in list(nodes.items()):
        if resource == _EMPTY_SENTINEL:
            continue
        if int(node.get("remaining", 0) or 0) > 0:
            continue
        respawn_at = node.get("respawn_at_tick")
        if respawn_at is None:
            continue
        if now >= int(respawn_at):
            cap = int(node.get("capacity") or default_capacity)
            node["remaining"] = cap
            node["respawn_at_tick"] = None


def yield_for_rank(rank, *, cap=2, divisor=40.0):
    """How many stack units one successful harvest grants (soft yield curve).

    Starts at 1 at rank 0 and steps up toward ``1 + cap`` as rank rises.
    The default divisor (40) matches the shipped gather pacing curve.
    """
    return 1 + int(min(cap, math.floor(float(rank) / divisor)))


def fail_chance_for_rank(rank, *, base=0.25, divisor=400.0):
    """Soft fail chance at low effective skill (drops as rank rises)."""
    return max(0.0, base - (float(rank) / divisor))


def load_nodes_table(conn, table="gather_nodes", retired=frozenset()):
    """Read gather node rows into ``{room_key: {resource: node_dict}}``.

    Skips the ``__empty__`` sentinel and any *retired* resource ids so
    boot-heal can drop legacy node types without rewriting old DB rows.
    """
    store = {}
    try:
        rows = conn.execute(
            f"SELECT room_key, resource, remaining, capacity, respawn_at_tick "
            f"FROM {table}"
        ).fetchall()
    except Exception:
        # Table missing on a very old DB mid-migration -- safe no-op.
        return store
    for room_key, resource, remaining, capacity, respawn_at in rows:
        if resource == _EMPTY_SENTINEL:
            continue
        if resource in retired:
            continue
        bucket = store.setdefault(room_key, {})
        bucket[resource] = {
            "resource": resource,
            "remaining": int(remaining),
            "capacity": int(capacity),
            "respawn_at_tick": (
                None if respawn_at is None else int(respawn_at)
            ),
        }
    return store


def save_nodes_table(
    conn,
    store,
    table="gather_nodes",
    default_capacity=5,
):
    """Rewrite *table* from an in-memory gather node store (DELETE + INSERT).

    Skips ``__empty__`` rows. *default_capacity* fills missing capacity
    on individual node dicts when persisting.
    """
    with conn:
        conn.execute(f"DELETE FROM {table}")
        for room_key, nodes in store.items():
            for resource, node in nodes.items():
                if resource == _EMPTY_SENTINEL:
                    continue
                conn.execute(
                    f"INSERT INTO {table} "
                    f"(room_key, resource, remaining, capacity, "
                    f"respawn_at_tick) VALUES (?, ?, ?, ?, ?)",
                    (
                        room_key,
                        resource,
                        int(node.get("remaining", 0) or 0),
                        int(
                            node.get("capacity", default_capacity)
                            or default_capacity
                        ),
                        node.get("respawn_at_tick"),
                    ),
                )
