"""
fishing.py -- engine fishing kernel (Wave 2 folklore peel).

Catch math and water detection live here so basegame and SUPERS share one
loop. Game-specific tables, boat checks, bait consumption, skill XP, and
effort delays stay in the game facade (supers/fishing.py) or player verbs.

Returns a 4-tuple from ``try_fish`` for GMCP / room broadcast hooks; the
live SUPERS facade keeps the historical 2-tuple player API.
"""

from __future__ import annotations

import random

from engine import game_calendar as cal_mod
from engine import hooks
from engine.command_support import _display_name
from engine.systems import material_instances as mat_mod
from engine.systems import regional_weather as regional_weather_mod

FISH_RESOURCES = frozenset({
    "fish_shore", "fish_pier", "fish_river", "fish_pond", "fish_offshore",
})

WATER_AREA_TYPES = frozenset({"lake", "ocean"})


def _resources(room):
    """Return the room's resource tag list (may be empty)."""
    return list(getattr(room, "resources", None) or [])


def _shore_body(room):
    """Pick lake vs inshore when only a generic fish_shore tag is present."""
    area = str(getattr(room, "area_type", "") or "").strip().lower()
    if area == "lake":
        return "lake"
    if area == "ocean":
        return "inshore"
    return "lake"


def water_body_for_room(room):
    """Resolve the fishing table body id for this room, or None when dry."""
    if room is None:
        return None
    resources = _resources(room)
    for tag, body in (
        ("fish_offshore", "offshore"),
        ("fish_pier", "pier"),
        ("fish_pond", "pond"),
        ("fish_river", "river"),
        ("fish_shore", "shore"),
    ):
        if tag in resources:
            return body if body != "shore" else _shore_body(room)
    area = str(getattr(room, "area_type", "") or "").strip().lower()
    if area == "lake" and _can_wilderness_fish(room):
        return "lake"
    if area == "ocean" and _can_wilderness_fish(room):
        return "inshore"
    if "fish_pond" in resources or getattr(room, "homestead_fish_pond", False):
        return "pond"
    return None


def _can_wilderness_fish(room):
    """Wilderness lake/ocean tiles fish unless blocked by homestead rules."""
    if getattr(room, "homestead_plot_id", None) and getattr(
        room, "homestead_fish_pond", False
    ):
        return True
    if not getattr(room, "wilderness", False):
        return False
    if getattr(room, "homestead_plot_id", None) and not getattr(
        room, "homestead_fish_pond", False
    ):
        return False
    return True


def room_can_fish(room):
    """True when ``water_body_for_room`` would return a body id."""
    return water_body_for_room(room) is not None


def _tables():
    """Fishing JSON dict from the registered game hook (may be empty)."""
    return hooks.fishing_tables() or {}


def _calendar(game):
    """Break down ``game.game_time_ticks`` into season / hour / week fields."""
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    return cal_mod.breakdown(ticks)


def _weather_condition(game, room=None):
    """Regional sky id (clear / rain / storm / fog / …) or empty string."""
    try:
        if room is not None:
            snap = regional_weather_mod.weather_for_room(room, game)
            if isinstance(snap, dict) and snap.get("condition"):
                return str(snap.get("condition") or "").strip().lower()
        state = getattr(game, "weather_state", None) or {}
        if isinstance(state, dict) and state.get("condition"):
            return str(state.get("condition") or "").strip().lower()
    except Exception:
        pass
    forced = getattr(game, "weather_condition", None)
    if forced:
        return str(forced).strip().lower()
    return ""


def _is_spooky(cal, game, room=None):
    """Late-night or fog swaps the optional spooky table (no World Tide)."""
    period = str(cal.get("day_period") or "day").lower()
    if period == "night":
        hour = int(cal.get("hour", 12) or 12)
        if 2 <= hour <= 5:
            return True
    cond = _weather_condition(game, room)
    if cond and "fog" in cond:
        return True
    return False


def _week_modifier(cal, data):
    """ISO-week spawn-run bump from tables, or empty dict."""
    week = int(cal.get("week_of_year") or 0)
    for row in data.get("week_modifiers") or []:
        start = int(row.get("start") or 0)
        end = int(row.get("end") or 0)
        if start and end and start <= week <= end:
            return dict(row)
    return {}


def _bait_spec(bait_id):
    """Look up bait tuning rows when the facade passes a catalog id."""
    if not bait_id:
        return {}
    return dict((_tables().get("bait") or {}).get(bait_id) or {})


def _pick_weighted(entries):
    """Weighted random choice from a list of {id, weight} rows."""
    total = sum(max(1, int(e.get("weight", 1) or 1)) for e in entries)
    roll = random.randint(1, total)
    acc = 0
    for entry in entries:
        w = max(1, int(entry.get("weight", 1) or 1))
        acc += w
        if roll <= acc:
            return entry["id"]
    return entries[-1]["id"]


def _item_label(item_id, item_obj=None):
    """Player-facing name for a catalog id (never a raw vnum)."""
    if item_obj is not None:
        label = getattr(item_obj, "short_desc", None) or getattr(item_obj, "key", None)
        if label:
            return str(label)
    spec = hooks.get_item_spec(item_id) or {}
    return str(spec.get("key") or item_id)


def _resolve_catch(body_id, cal, game, character, *, bait_spec=None, room=None):
    """Roll junk vs quality tiers; return (item_id, tier_or_error)."""
    data = _tables()
    body = dict((data.get("bodies") or {}).get(body_id) or {})
    if not body:
        return None, "No fish table for this water."
    if body.get("requires_boat"):
        if not hooks.aboard_water_craft(character):
            return None, (
                "The deep water wants a boat -- launch one at a boat ramp "
                "(help hitch, help fishing)."
            )
    spooky = _is_spooky(cal, game, room)
    if spooky and (data.get("bodies") or {}).get("spooky"):
        body = dict(data["bodies"]["spooky"])

    junk_weight = int(body.get("junk_weight", 15) or 15)
    season = str(cal.get("season") or "spring").lower()
    season_mod = (data.get("season_modifiers") or {}).get(season) or {}
    junk_weight += int(season_mod.get("junk_weight_delta", 0) or 0)
    period = str(cal.get("day_period") or "day").lower()
    time_mod = (data.get("time_modifiers") or {}).get(period) or {}
    if spooky:
        time_mod = dict((data.get("time_modifiers") or {}).get("fog") or {})
    junk_weight += int(time_mod.get("junk_weight_delta", 0) or 0)

    cond = _weather_condition(game, room)
    weather_mod = (data.get("weather_modifiers") or {}).get(cond) or {}
    junk_weight += int(weather_mod.get("junk_weight_delta", 0) or 0)

    week_mod = _week_modifier(cal, data)
    junk_weight += int(week_mod.get("junk_weight_delta", 0) or 0)

    bait_spec = bait_spec or {}
    junk_weight += int(bait_spec.get("junk_weight_delta", 0) or 0)

    skill = float(hooks.fishing_skill(character))
    junk_weight = max(5, junk_weight - int(skill / 10))

    rare_mult = float(season_mod.get("rare_mult", 1.0) or 1.0)
    rare_mult *= float(time_mod.get("rare_mult", 1.0) or 1.0)
    rare_mult *= float(weather_mod.get("rare_mult", 1.0) or 1.0)
    rare_mult *= float(week_mod.get("rare_mult", 1.0) or 1.0)
    rare_mult *= float(bait_spec.get("rare_mult", 1.0) or 1.0)
    rare_mult *= 1.0 + (skill / 200.0)

    roll = random.random() * 100.0
    if roll < junk_weight:
        junk = data.get("junk") or []
        if junk:
            item_id = _pick_weighted(junk)
            return item_id, None
    uncommon_gate = 12.0 * rare_mult
    rare_gate = 3.0 * rare_mult
    if roll < rare_gate + uncommon_gate and body.get("rare"):
        item_id = _pick_weighted(body["rare"])
        return item_id, "rare"
    if roll < uncommon_gate + 25.0 and body.get("uncommon"):
        item_id = _pick_weighted(body["uncommon"])
        return item_id, "uncommon"
    common = body.get("common") or []
    if not common:
        junk = data.get("junk") or []
        return _pick_weighted(junk), None
    return _pick_weighted(common), "common"


def try_fish(character, game, *, bait=None):
    """Cast a line. Always grants an item unless blocked (boat, carry).

    ``bait`` is an optional **catalog id** (e.g. ``fishing_worms``) already
    validated by the game facade. This kernel does **not** stamp effort
    delay or consume bait -- the facade owns those steps.

    Returns ``(ok, actor_message, room_line, catch_dict | None)``.
    ``catch_dict`` on success: ``item_id``, ``tier``, ``item_name``.
    """
    room = character.location
    body_id = water_body_for_room(room)
    if not body_id:
        return False, (
            "No fishable water here. Lakes, rivers, piers, and homestead "
            "ponds work (help fishing)."
        ), None, None

    bait_spec = _bait_spec(bait)
    cal = _calendar(game)
    item_id, tier = _resolve_catch(
        body_id, cal, game, character, bait_spec=bait_spec, room=room,
    )
    if item_id is None:
        err = tier or "Nothing bit."
        return False, err, None, None
    if isinstance(tier, str) and tier.startswith("No "):
        return False, tier, None, None

    item = hooks.make_world_item({"item": item_id}, where="fish")
    if item is None:
        return False, f"Missing catalog item {item_id}.", None, None
    if not mat_mod.can_carry_weight(character, mat_mod.instance_weight(item)):
        return False, mat_mod.refuse_carry_message(), None, None
    ok, msg = mat_mod.try_give_item(character, item)
    if not ok:
        return False, msg, None, None

    label = _item_label(item_id, item)
    bait_note = ""
    if bait:
        bait_note = f" ({bait_spec.get('label', bait)})"
    if tier == "rare":
        actor = f"You haul in a prize catch{bait_note}: {label}!"
    elif str(item_id).startswith("fishing_"):
        actor = f"Your line snags something useless{bait_note}: {label}."
    else:
        actor = f"You reel in {label}{bait_note}."

    actor_name = _display_name(character)
    room_line = f"{actor_name} reels in {label}."
    catch_dict = {
        "item_id": item_id,
        "tier": tier,
        "item_name": label,
    }
    return True, actor, room_line, catch_dict
