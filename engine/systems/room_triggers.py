"""
room_triggers.py -- room.trigger load/validate + effect-pipeline executor.

Mirrors engine/systems/quests_loader.py's shape: generic load/validate here,
SUPERS registers the concrete effect-action handlers (supers/triggers/policy.py)
the same way quests.py's complete_when types are SUPERS-extensible.
"""

from __future__ import annotations

_ACTIONS: dict[str, callable] = {}
_TRIGGERS_BY_ROOM: dict[str, list] = {}


def register_action(effect_type: str, fn) -> None:
    """SUPERS calls this once per action type at bootstrap."""
    _ACTIONS[effect_type] = fn


def load_triggers(directory="supers/content/triggers") -> None:
    """Load every room.trigger JSON file and index by attach room."""
    import json
    import os

    _TRIGGERS_BY_ROOM.clear()
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            trigger = json.load(fh)
        room_key = (trigger.get("attach") or {}).get("room")
        if not room_key:
            continue
        _TRIGGERS_BY_ROOM.setdefault(room_key, []).append(trigger)


def triggers_for_room(room_key):
    """Return trigger dicts attached to ``room_key`` (testing / GM list)."""
    return list(_TRIGGERS_BY_ROOM.get(room_key) or [])


def maybe_fire_on_enter(character, room, game) -> None:
    """Called from the after-move chain -- same call shape as arachne hazards."""
    if character is None or room is None:
        return
    room_key = getattr(room, "key", None)
    _note_visit_room_change(character, room_key)
    for trigger in _TRIGGERS_BY_ROOM.get(room_key, []):
        if (trigger.get("fires_on") or {}).get("type") != "on_enter":
            continue
        if trigger.get("once_per_visit") and _already_fired_this_visit(
            character, room_key, trigger,
        ):
            continue
        if not _conditions_pass(
            trigger.get("conditions"), character, game, trigger_id=trigger.get("id"),
        ):
            continue
        _mark_fired_this_visit(character, room_key, trigger)
        _run_effects(
            trigger.get("effects") or [],
            character,
            room,
            game,
            trigger_id=trigger.get("id"),
        )


def tick_timer_triggers(game) -> None:
    """Fire ``fires_on.type: timer`` triggers for occupants of attached rooms."""
    if game is None:
        return
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    for room_key, triggers in _TRIGGERS_BY_ROOM.items():
        room = game.rooms.get(room_key)
        if room is None:
            continue
        occupants = list(getattr(room, "contents", None) or [])
        for trigger in triggers:
            fires = trigger.get("fires_on") or {}
            if fires.get("type") != "timer":
                continue
            every = int(fires.get("every_ticks") or 0)
            if every <= 0:
                continue
            trigger_id = trigger.get("id")
            for character in occupants:
                if character is None:
                    continue
                last = int(
                    getattr(character, f"_trigger_timer_last_{trigger_id}", 0) or 0
                )
                if last and (now - last) < every:
                    continue
                if not _conditions_pass(
                    trigger.get("conditions"),
                    character,
                    game,
                    trigger_id=trigger_id,
                ):
                    continue
                setattr(character, f"_trigger_timer_last_{trigger_id}", now)
                _run_effects(
                    trigger.get("effects") or [],
                    character,
                    room,
                    game,
                    trigger_id=trigger_id,
                )


def _run_effects(effects, character, room, game, *, trigger_id) -> None:
    """Run effects[] in order; ``delay`` pauses and resumes the remainder."""
    for index, action in enumerate(effects):
        if action.get("type") == "delay":
            remaining = effects[index + 1 :]
            if remaining:
                from engine.systems import scheduler as scheduler_mod

                scheduler_mod.schedule_ticks(
                    game,
                    int(action.get("ticks") or 0),
                    "room_trigger_resume",
                    effects=remaining,
                    character_key=getattr(character, "key", None),
                    room_key=getattr(room, "key", None),
                    trigger_id=trigger_id,
                )
            return
        handler = _ACTIONS.get(action.get("type"))
        if handler is None:
            continue
        handler(action, character, room, game, trigger_id=trigger_id)


def resume_effects(game, *, effects, character_key, room_key, trigger_id) -> None:
    """Scheduler callback that continues a trigger's effect list past a delay."""
    from engine.char_index import find_character_by_key

    character = find_character_by_key(game, character_key)
    room = game.rooms.get(room_key) if room_key else None
    if character is None or room is None:
        return
    _run_effects(effects, character, room, game, trigger_id=trigger_id)


def _conditions_pass(conditions, character, game, *, trigger_id) -> bool:
    if not conditions:
        return True
    from engine.systems import predicates as predicates_mod

    return all(
        predicates_mod.check(cond, character, game, trigger_id=trigger_id)
        for cond in conditions
    )


def _visit_box(character) -> dict:
    box = getattr(character, "_trigger_fired_this_visit", None)
    if box is None or not isinstance(box, dict):
        box = {}
        character._trigger_fired_this_visit = box
    return box


def _note_visit_room_change(character, room_key) -> None:
    """Drop per-room once_per_visit state when the character changes rooms."""
    prev = getattr(character, "_trigger_visit_room", None)
    if prev and prev != room_key:
        box = _visit_box(character)
        box.pop(prev, None)
    character._trigger_visit_room = room_key


def _already_fired_this_visit(character, room_key, trigger) -> bool:
    fired = _visit_box(character).get(room_key)
    if not fired:
        return False
    return trigger.get("id") in fired


def _mark_fired_this_visit(character, room_key, trigger) -> None:
    box = _visit_box(character)
    fired = box.get(room_key)
    if fired is None:
        fired = set()
        box[room_key] = fired
    fired.add(trigger.get("id"))
