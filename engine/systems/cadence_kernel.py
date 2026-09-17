"""cadence_kernel.py -- generic seek / wander Cadence FSM (folklore Wave 4).

Pure decision + apply split for loop-side lifestyle AI:

* ``plan_tick`` is read-only -- returns ``{"verb": str|None, "reason": str}``.
* ``apply_plan`` runs on the asyncio tick loop only -- ``npc_do`` or
  ``needs.satisfy`` so room broadcasts and ``Room.Occupants`` fire.

The read-only ``engine.cadence_planner`` thread is a **separate** path
(sleep / wander for LOD actors). It must never call ``pick_seek_room``,
``apply_plan``, or touch live ``Character`` / ``Room`` objects.

Zero ``supers`` imports (hard rule 21 / two-repo purity).
"""

from __future__ import annotations

import random

from engine import hooks
from engine import pathfind as pathfind_engine
from engine.systems import needs as needs_engine

# Reuse the engine-wide SEEK line -- do not fork a second constant here.
SEEK_THRESHOLD = needs_engine.SEEK_THRESHOLD

NEED_RESOURCE_DEFAULT = {"hunger": "food", "thirst": "water"}
METER_NAMES_DEFAULT = ("hunger", "thirst")
IDLE_WANDER_CHANCE = 0.06  # match engine/cadence_planner.py / supers/cadence.py


def most_urgent_need_from_needs(needs: dict) -> str | None:
    """Planner-safe urgent meter name from a plain float dict.

    No ``Character`` / ``Room`` -- safe for ``CadenceActorProjection.needs``
    on the read-only planner thread. Uses hooked meter names and the default
    ``SEEK_THRESHOLD`` (per-character ``seek_mult`` stays on the loop path).
    """
    if not isinstance(needs, dict):
        return None
    names = hooks.cadence_meter_names()
    threshold = SEEK_THRESHOLD
    worst = None
    for name in names:
        level = float(needs.get(name, 0.0) or 0.0)
        if level >= threshold and (worst is None or level > worst[1]):
            worst = (name, level)
    return worst[0] if worst else None


def most_urgent_need(character) -> str | None:
    """Worst meter at/above the hooked seek threshold -- name only.

    Read-only ``getattr`` on ``character``. Games may replace the pick via
    ``hooks.cadence_urgent_override`` (SUPERS: soiled-clothes hygiene force).
    """
    override = hooks.cadence_urgent_override(character)
    if override:
        return override
    names = hooks.cadence_meter_names()
    threshold = hooks.cadence_seek_threshold(character)
    result = needs_engine.most_urgent(
        character, names, threshold=threshold,
    )
    return result[0] if result else None


def _room_has_resource(room, resource_tag) -> bool:
    """True when ``room.resources`` includes ``resource_tag``."""
    if room is None or not resource_tag:
        return False
    return resource_tag in (getattr(room, "resources", None) or ())


def pick_seek_room(character, game):
    """Nearest same-zone room that offers the urgent need's resource tag.

    Tiny BFS over ``room.exits`` using ``hooks.cadence_room_passable``.
    Loop-only: walks live ``Room`` graph objects. Forbidden on the planner
    thread (no live rooms there).
    """
    need = most_urgent_need(character)
    if need is None:
        return None
    resource = hooks.cadence_need_resource(need)
    if not resource:
        return None
    room = getattr(character, "location", None)
    if room is None:
        return None
    if _room_has_resource(room, resource):
        return room

    def _predicate(candidate):
        return _room_has_resource(candidate, resource)

    def _edge_ok(_from_room, neighbor):
        return hooks.cadence_room_passable(character, neighbor, game)

    path = pathfind_engine.path_directions_to(
        room, _predicate, edge_ok=_edge_ok,
    )
    if not path:
        return None
    dest = room
    for direction in path:
        dest = (dest.exits or {}).get(direction)
        if dest is None:
            return None
    return dest


def _first_hop_toward(character, game, dest):
    """Cardinal exit label toward ``dest``, or None when unreachable."""
    room = getattr(character, "location", None)
    if room is None or dest is None:
        return None
    if room is dest:
        return None

    def _predicate(candidate):
        return candidate is dest

    def _edge_ok(_from_room, neighbor):
        return hooks.cadence_room_passable(character, neighbor, game)

    return pathfind_engine.next_step_toward(
        room, _predicate, edge_ok=_edge_ok,
    )


def plan_tick(character, game) -> dict:
    """Read-only lifestyle FSM -- never mutates world state.

    Returns ``{"verb": str|None, "reason": str}``. Priority:

    1. ``hooks.cadence_plan_override`` (unused in basegame this wave)
    2. SEEK hop toward a resource room, or ``sate:<meter>`` when already there
    3. Idle wander when ``random() < IDLE_WANDER_CHANCE`` and exits exist

    The kernel does **not** emit ``"work"`` except via the override hook.
    """
    override_plan = hooks.cadence_plan_override(character, game)
    if override_plan is not None:
        return override_plan

    need = most_urgent_need(character)
    if need is not None:
        resource = hooks.cadence_need_resource(need)
        room = getattr(character, "location", None)
        if _room_has_resource(room, resource):
            return {"verb": None, "reason": f"sate:{need}"}
        dest = pick_seek_room(character, game)
        if dest is not None and room is not dest:
            direction = _first_hop_toward(character, game, dest)
            if direction:
                return {
                    "verb": direction,
                    "reason": f"seek:{need}",
                }

    room = getattr(character, "location", None)
    if room is not None and random.random() < IDLE_WANDER_CHANCE:
        options = [
            direction
            for direction, neighbor in (room.exits or {}).items()
            if hooks.cadence_room_passable(character, neighbor, game)
        ]
        if options:
            return {
                "verb": random.choice(options),
                "reason": "wander",
            }

    return {"verb": None, "reason": "idle"}


def apply_plan(character, game, plan: dict) -> None:
    """Apply one ``plan_tick`` result on the asyncio loop only.

    * ``verb`` set -> ``engine.npc_act.npc_do`` (cardinal walk, …)
    * ``reason`` startswith ``sate:`` -> ``needs.satisfy`` on the meter

    Never call from ``CadencePlannerThread`` (planner thread is read-only).
    """
    if not isinstance(plan, dict):
        return
    verb = plan.get("verb")
    reason = str(plan.get("reason") or "")
    if verb:
        from engine.npc_act import npc_do
        npc_do(character, verb, game)
        return
    if reason.startswith("sate:"):
        meter = reason.split(":", 1)[1].strip()
        if meter:
            needs_engine.satisfy(character, meter)
