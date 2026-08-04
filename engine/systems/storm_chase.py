"""
storm_chase.py -- Tornado Hunter desk job + chase board (engine framework).
"""

from __future__ import annotations

import random
import engine.systems.economy as economy_wallet

JOB_ID = "tornado_hunter"
DESK_KEYS = frozenset({
    "lebanon:Storm Watch Office",
    "Storm Watch Office",
    "NB00002",
    "notbigville:Storm Watch Office",
})

RESEARCH_PAY_DOLLARS = 2
CHASE_BASE_PAY_DOLLARS = 12
CHASE_DUTY_BONUS = 4
PROBE_RADIUS = 1  # Chebyshev tiles from target macro


def is_storm_desk_room(room):
    """True when room is the Storm Watch Office (job site + board)."""
    if room is None:
        return False
    if room.key in DESK_KEYS:
        return True
    jobs = tuple(getattr(room, "jobs", None) or ())
    return JOB_ID in jobs


def is_on_duty_hunter(character, game=None):
    """True when on-duty tornado_hunter at the desk."""
    from engine import hooks as hooks_mod

    if getattr(character, "job", None) != JOB_ID:
        return False
    if not is_storm_desk_room(getattr(character, "location", None)):
        return False
    return hooks_mod.storm_chase_is_on_duty(character, game=game)


def refuse_duty(character, game=None):
    """Plain refusal for gated desk verbs."""
    if getattr(character, "job", None) != JOB_ID:
        return (
            "You need the Tornado Hunter gig here. "
            "Type 'work' (or 'work as tornado_hunter') at Storm Watch Office."
        )
    from engine import hooks as hooks_mod

    if not hooks_mod.storm_chase_is_on_duty(character, game=game):
        return "Clock in first (work), then try again."
    return "Research and radar start at the Storm Watch Office."


def research(character, game):
    """On-duty desk research: small dollars + regional flavor log."""
    if not is_on_duty_hunter(character, game):
        return False, refuse_duty(character, game), None
    from engine import hooks
    busy = hooks.utility_delay_begin(character, game, "storm_research")
    if busy:
        return False, busy, None
    from engine.systems import regional_weather as weather_mod

    room = getattr(character, "location", None)
    w = weather_mod.weather_for_room(room, game, character)
    economy_wallet.credit_wallet(character, dollars=RESEARCH_PAY_DOLLARS)
    msg = (
        f"[RESEARCH] You log {w.get('region_name')} normals -- "
        f"{w.get('condition')}, about {w.get('temp_f')} F, "
        f"{w.get('wind')} wind. Watch flag: "
        f"{'yes' if w.get('tornado_watch') else 'no'}. "
        f"+{RESEARCH_PAY_DOLLARS} dollars."
    )
    room_line = f"{character.key} hunches over a radar terminal, logging sky data."
    return True, msg, room_line


def radar(character, game):
    """On-duty: list watches / active tornado tracks."""
    if not is_on_duty_hunter(character, game):
        return False, refuse_duty(character, game), None
    from engine.systems import regional_weather as weather_mod

    room = getattr(character, "location", None)
    w = weather_mod.weather_for_room(room, game, character)
    lines = [
        f"[RADAR] Home region: {w.get('region_name')} — "
        f"{w.get('condition')}, watch={'yes' if w.get('tornado_watch') else 'no'}.",
    ]
    tracks = weather_mod.list_tornadoes(game)
    if not tracks:
        lines.append("No active tornado tracks on the scope.")
    else:
        lines.append(f"Active funnels: {len(tracks)}")
        for t in tracks:
            mx, my = t.get("macro_xy") or (None, None)
            lines.append(
                f"  {t.get('scale')} heading {t.get('heading')} at ({mx},{my}) "
                f"ttl={t.get('ttl_steps')}"
            )
    return True, "\r\n".join(lines), None


def _clear_chase(character):
    """Wipe chase board state on a character."""
    character.chase_id = None
    character.chase_brief = None
    character.chase_flags = {}


def has_chase(character):
    """True when a chase brief is open."""
    return bool(getattr(character, "chase_id", None))


def board_lines(game, character=None):
    """Preview chase opportunities at the desk."""
    from engine.systems import regional_weather as weather_mod

    lines = ["Storm Watch chase board:"]
    tracks = weather_mod.list_tornadoes(game)
    if tracks:
        for i, t in enumerate(tracks, start=1):
            mx, my = t.get("macro_xy") or (None, None)
            lines.append(
                f"  {i}. LIVE {t.get('scale')} funnel at ({mx},{my}) "
                f"heading {t.get('heading')}"
            )
    else:
        lines.append(
            "  (no live funnels — takechase still assigns a storm cell to probe)"
        )
    lines.append("Commands: takechase | track chase | probe | reportchase | abandonchase")
    if character is not None and has_chase(character):
        brief = getattr(character, "chase_brief", None) or {}
        flags = getattr(character, "chase_flags", None) or {}
        lines.append(
            f"Your chase: {brief.get('title')} → ({brief.get('mx')},{brief.get('my')}) "
            f"data={'yes' if flags.get('data_collected') else 'no'}"
        )
    return "\r\n".join(lines)


def takechase(character, game):
    """Accept a chase brief at Storm Watch Office."""
    room = getattr(character, "location", None)
    if not is_storm_desk_room(room):
        return False, "Stand at the Storm Watch Office chase board to take a chase.", None
    if has_chase(character):
        return False, "You already have an open chase. reportchase or abandonchase.", None

    from engine.systems import regional_weather as weather_mod

    tracks = weather_mod.list_tornadoes(game)
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    if tracks:
        t = tracks[ticks % len(tracks)]
        mx, my = t.get("macro_xy") or (35, 10)
        brief = {
            "chase_id": f"live-{t.get('id')}",
            "title": f"Live {t.get('scale')} chase",
            "blurb": (
                f"Track the {t.get('scale')} funnel heading {t.get('heading')} "
                f"near ({mx},{my}). Probe close, then report back."
            ),
            "mx": int(mx),
            "my": int(my),
            "scale": t.get("scale"),
            "kind": "live_tornado",
            "pay_dollars": CHASE_BASE_PAY_DOLLARS,
            "board_room": "lebanon:Storm Watch Office",
        }
    else:
        # Seeded storm cell in great_plains when no live funnel.
        rng = random.Random(f"chase:{ticks}:{getattr(character, 'key', '?')}")
        mx = 35 + rng.randint(-3, 3)
        my = 10 + rng.randint(-2, 2)
        mx = max(0, min(77, mx))
        my = max(0, min(17, my))
        brief = {
            "chase_id": f"cell-{ticks}-{mx}-{my}",
            "title": "Storm-cell probe",
            "blurb": (
                f"Radar paints a storm cell near ({mx},{my}). "
                "Drive out, probe outdoors, bring the data home."
            ),
            "mx": mx,
            "my": my,
            "scale": None,
            "kind": "storm_cell",
            "pay_dollars": CHASE_BASE_PAY_DOLLARS - 2,
            "board_room": "lebanon:Storm Watch Office",
        }

    character.chase_id = brief["chase_id"]
    character.chase_brief = brief
    character.chase_flags = {"data_collected": False}
    msg = (
        f"[CHASE] Accepted: {brief['title']}.\r\n"
        f"{brief['blurb']}\r\n"
        "track chase → probe near the cell → reportchase here."
    )
    room_line = f"{character.key} pins a chase card to their jacket."
    return True, msg, room_line


def track_chase(character, game):
    """Soft lead toward the chase target cell."""
    if not has_chase(character):
        return False, "No open chase. takechase at Storm Watch Office.", None
    brief = getattr(character, "chase_brief", None) or {}
    tx, ty = int(brief.get("mx", 35)), int(brief.get("my", 10))
    from engine.systems import regional_weather as weather_mod

    room = getattr(character, "location", None)
    cx, cy = weather_mod._macro_xy_for_room(room, game, character)
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        tip = "You are on the target cell — probe outdoors here."
    else:
        bits = []
        if abs(dy) >= abs(dx):
            bits.append("north" if dy < 0 else "south")
        if dx != 0:
            bits.append("west" if dx < 0 else "east")
        tip = (
            f"Soft lead: push {'/'.join(bits)} toward ({tx},{ty}) "
            f"(you are near ({cx},{cy}))."
        )
    return True, f"[CHASE] {tip}", None


def _actor_macro(character, game):
    """Macro cell for the chaser."""
    from engine.systems import regional_weather as weather_mod

    return weather_mod._macro_xy_for_room(
        getattr(character, "location", None), game, character
    )


def probe(character, game):
    """Collect data outdoors near the target macro cell."""
    if not has_chase(character):
        return False, "No open chase. takechase at Storm Watch Office.", None
    flags = getattr(character, "chase_flags", None)
    if not isinstance(flags, dict):
        flags = {}
        character.chase_flags = flags
    if flags.get("data_collected"):
        return False, "You already have the data. reportchase at Storm Watch.", None

    room = getattr(character, "location", None)
    if room is None or not getattr(room, "outdoor", False):
        return False, "Probe outdoors (town street or overland) near the target.", None

    brief = getattr(character, "chase_brief", None) or {}
    tx, ty = int(brief.get("mx", 35)), int(brief.get("my", 10))
    cx, cy = _actor_macro(character, game)
    dist = max(abs(cx - tx), abs(cy - ty))
    if dist > PROBE_RADIUS:
        return (
            False,
            f"Too far from the target cell ({tx},{ty}). "
            f"You are near ({cx},{cy}). track chase for a lead.",
            None,
        )

    flags["data_collected"] = True
    character.chase_flags = flags
    scale = brief.get("scale")
    bit = f" ({scale})" if scale else ""
    msg = (
        f"[PROBE] You plant sensors and log the cell{bit}. "
        "Data secured — reportchase at Storm Watch Office."
    )
    room_line = f"{character.key} kneels with a probe kit, reading the sky."
    return True, msg, room_line


def reportchase(character, game):
    """Turn in collected data at the board."""
    if not has_chase(character):
        return False, "No open chase to report.", None
    room = getattr(character, "location", None)
    if not is_storm_desk_room(room):
        return False, "Report at the Storm Watch Office.", None
    flags = getattr(character, "chase_flags", None) or {}
    if not flags.get("data_collected"):
        return False, "No data yet. probe near the target cell first.", None

    brief = getattr(character, "chase_brief", None) or {}
    pay = int(
        brief.get("pay_dollars")
        or brief.get("pay_coins")
        or CHASE_BASE_PAY_DOLLARS
    )
    bonus = 0
    if is_on_duty_hunter(character, game):
        bonus = CHASE_DUTY_BONUS
        pay += bonus
    economy_wallet.credit_wallet(character, dollars=pay)
    title = brief.get("title") or "chase"
    _clear_chase(character)
    bonus_bit = f" (includes +{bonus} on-duty bonus)" if bonus else ""
    msg = f"[CHASE] Reported {title}. +{pay} dollars{bonus_bit}."
    room_line = f"{character.key} dumps a storm data packet on the desk."
    return True, msg, room_line


def abandonchase(character, game=None):
    """Drop the open chase without pay."""
    if not has_chase(character):
        return False, "No open chase.", None
    _clear_chase(character)
    return True, "[CHASE] Chase abandoned.", None
