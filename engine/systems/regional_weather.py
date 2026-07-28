"""
weather.py -- CONUS climatology + moving tornadoes.

Regional weather from ``content/climate/conus_normals.json`` (derived
NOAA/NCEI-style monthly bands). Same game-day + region is stable across
reboot via a seeded RNG. Tornadoes are rare severe overlays that move
on the America atlas and can injure unsheltered people in town cells.

Player surfaces: ``weather`` / ``forecast``, outdoor look clauses,
hybrid look vision (always-on hard-to-see overlay + chance whiteout),
radio / Discord WX (scheduled top-of-hour every 4 game-hours), sparse
atmospheric beats. Staff: ``gm weather``.

CONUS mechanics apply only to prime-material (Earth) rooms. Every public
surface below is gated on ``elemental.is_elemental_realm`` and, for a
Reach room, short-circuits to plain ambient flavor text instead -- no
whiteout, no tornadoes, no fabricated regional readout (a fire plane has
no business rolling snow). See ``ELEMENTAL_FLAVOR_LINES``.

No networking. Stdlib only. Design SoT: docs/plans/weather_climatology.md.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from engine import game_calendar, snoop


def _is_elemental_realm(room):
    from engine import hooks as hooks_mod
    return hooks_mod.weather_is_elemental_realm(room)


def _room_plane(room):
    from engine import hooks as hooks_mod
    return hooks_mod.weather_room_plane(room)



# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

CONDITIONS = (
    "clear",
    "cloudy",
    "fog",
    "rain",
    "storm",
    "snow",
)

HOME_REGION = "great_plains"  # Lebanon / WKNZ home bulletin
FALLBACK_REGION = "great_plains"

# Ambient-only flavor lines for the four Reach planes (supers/elemental.py
# ELEMENTAL_MAP_PLANES). These never touch CONUS climatology and carry no
# mechanical weight (no whiteout, no vision fight, no tornado) -- purely
# descriptive text so e.g. the Fire Plane never reads as snowing. Plain
# sentences only, no color/ASCII reliance (accessibility hard rule).
ELEMENTAL_FLAVOR_LINES = {
    "fire": (
        "Heat shimmers off broken ground, embers drifting lazy on the air.",
        "A curtain of fine ash sifts down like snow that never melts.",
        "The horizon pulses faint orange, breathing with the plane itself.",
        "Sparks crawl along every surface, harmless, restless.",
        "Smoke threads the sky in slow, deliberate spirals.",
    ),
    "water": (
        "Mist curls low over still water, thick enough to taste.",
        "The surface breathes in slow swells, though no wind stirs it.",
        "Droplets hang motionless in the air before falling all at once.",
        "A deep hush rolls in off unseen currents.",
        "Pale light refracts through water that has no floor.",
    ),
    "air": (
        "Restless gusts needle past, never settling, never still.",
        "Loose grit rides the wind in long, looping spirals.",
        "The sky churns slow and endless, gray shading to violet.",
        "Distant thunderheads rumble without ever arriving.",
        "Every sound stretches thin, carried off before it finishes.",
    ),
    "stone": (
        "Dust devils spin lazily across cracked, patient ground.",
        "The air sits heavy and still, pressed flat by old stone.",
        "Fine grit sifts down from ledges no one can see.",
        "The ground hums faintly, a heartbeat under bedrock.",
        "Shadows pool long and slow across weathered rock.",
    ),
}
# Fallback pool if a Reach room's plane id is not one of the four above.
_ELEMENTAL_FLAVOR_FALLBACK = (
    "The plane presses close, alive in a way no prime room ever is.",
)

EF_SCALES = ("EF0", "EF1", "EF2", "EF3", "EF4", "EF5")
# Tier gate: at/above = near-miss; below = HP damage. EF0 = theater only.
EF_TIER_GATE = {
    "EF0": None,  # never HP from gate math
    "EF1": 0,
    "EF2": 1,
    "EF3": 2,
    "EF4": 3,
    "EF5": 4,
}
EF_DAMAGE = {
    "EF0": 0,
    "EF1": 8,
    "EF2": 18,
    "EF3": 35,
    "EF4": 55,
    "EF5": 80,
}

HEADINGS = ("n", "ne", "e", "se", "s", "sw", "w", "nw")
_HEADING_DELTA = {
    "n": (0, -1),
    "ne": (1, -1),
    "e": (1, 0),
    "se": (1, 1),
    "s": (0, 1),
    "sw": (-1, 1),
    "w": (-1, 0),
    "nw": (-1, -1),
}

# Game ticks between tornado cell steps (~90 real seconds at 3s/tick).
TORNADO_STEP_TICKS = 30
TORNADO_DEFAULT_TTL_STEPS = 12
ATMOS_COOLDOWN_TICKS = {
    "clear": 400,
    "cloudy": 350,
    "fog": 300,
    "rain": 220,
    "storm": 140,
    "snow": 200,
    "tornado": 60,
    # Reach planes: quieter than CONUS "clear" -- ambient flavor only,
    # no urgency to convey.
    "elemental": 500,
}

# Bare outdoor look: chance to hide desc / people / items / exits.
# Rain gets overlay only (no whiteout). Nearby tornado uses the
# ``tornado`` key even when the day-condition is not storm.
VISION_WHITEOUT_CHANCE = {
    "rain": 0.0,
    "storm": 0.32,
    "snow": 0.38,
    "tornado": 0.55,
}
# Car windows soften whiteout; auto-look after a step rarely whites out.
VISION_VEHICLE_WHITEOUT_MULT = 0.4
VISION_AFTER_MOVE_WHITEOUT_MULT = 0.2

# Game-seconds between weather-radio report beats while tuned in.
WEATHER_REPORT_INTERVAL_SEC = 45

# Scheduled town-radio / Discord WX bulletin: top of the hour every N
# game-hours (not on boot / cache miss / copyover). 400 ticks = 1 game-hour
# (must match engine.game_calendar.TICKS_PER_HOUR).
TICKS_PER_GAME_HOUR = 400
WX_BULLETIN_EVERY_GAME_HOURS = 4
WX_BULLETIN_INTERVAL_TICKS = TICKS_PER_GAME_HOUR * WX_BULLETIN_EVERY_GAME_HOURS

_PACK = None
_CLIMATE_PACK_PATH = None


def set_climate_pack_path(path):
    """Override climate JSON path (call before first load)."""
    global _CLIMATE_PACK_PATH, _PACK
    _CLIMATE_PACK_PATH = Path(path) if path else None
    _PACK = None


def _default_pack_path():
    if _CLIMATE_PACK_PATH is not None:
        return _CLIMATE_PACK_PATH
    return (
        Path(__file__).resolve().parents[2]
        / "content"
        / "climate"
        / "conus_normals.json"
    )

# Region display names when pack missing a label.
_REGION_LABELS = {
    "pacific_nw": "Pacific Northwest",
    "california": "California",
    "southwest_desert": "Southwest desert",
    "rockies": "Rockies",
    "great_plains": "Great Plains / Midwest",
    "great_lakes": "Great Lakes",
    "northeast": "Northeast",
    "southeast": "Southeast",
    "gulf_coast": "Gulf Coast",
    "interior_west": "Interior West / Basin",
}


# ---------------------------------------------------------------------------
# Pack load
# ---------------------------------------------------------------------------

def load_climate_pack(path=None, *, force=False):
    """Load (and cache) the CONUS normals JSON pack."""
    global _PACK
    if _PACK is not None and not force:
        return _PACK
    p = Path(path) if path else _default_pack_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[weather] climate pack load failed: {exc}", flush=True)
        data = {
            "version": 0,
            "regions": {},
            "atlas_overrides": [],
            "glyph_default": {"~": None, "o": None, ".": FALLBACK_REGION},
        }
    _PACK = data
    return data


def get_pack():
    """Return the cached climate pack (loads on first call)."""
    return load_climate_pack()


def region_display_name(region_id):
    """Player/GM facing region label."""
    pack = get_pack()
    entry = (pack.get("regions") or {}).get(region_id) or {}
    return entry.get("display_name") or _REGION_LABELS.get(
        region_id, region_id or FALLBACK_REGION
    )


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def _cal(game):
    """Calendar breakdown dict, or a minimal stub for unit tests."""
    if hasattr(game, "calendar") and callable(game.calendar):
        return game.calendar()
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    return game_calendar.breakdown(ticks)


def _day_of_year(cal):
    """1..366 day-of-year from calendar breakdown."""
    # Prefer Gregorian from calendar_day when available.
    try:
        from datetime import date, timedelta

        epoch = getattr(game_calendar, "EPOCH_DATE", date(2024, 1, 1))
        cd = int(cal.get("calendar_day") or 0)
        return (epoch + timedelta(days=cd)).timetuple().tm_yday
    except Exception:
        month = int(cal.get("month") or 1)
        day = int(cal.get("day_of_month") or cal.get("day") or 1)
        # Rough non-leap cumulative days.
        cum = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
        return cum[max(0, min(11, month - 1))] + max(1, min(31, day))


def _days_in_month(month, year=2024):
    """Days in calendar month (simple Gregorian)."""
    import calendar as _calmod

    return _calmod.monthrange(int(year), int(month))[1]


# ---------------------------------------------------------------------------
# Region resolve
# ---------------------------------------------------------------------------

def _macro_xy_for_room(room, game, character=None):
    """Best-effort America macro (x, y) for a room / optional actor."""
    if character is not None and game is not None:
        try:
            from engine.systems import overland as overland_mod

            mp = overland_mod._parse_pos_pair(getattr(character, "macro_pos", None))
            if mp is not None:
                return mp
            pos = None
            # Off-prime (Hell, Heaven, Reach, …) returns None for travel
            # math -- fall through to room stamps / default, not login-crash.
            if pos is not None:
                return pos
        except Exception:
            pass
    if room is not None:
        om = getattr(room, "overland_macro", None)
        if isinstance(om, (list, tuple)) and len(om) == 2:
            try:
                return (int(om[0]), int(om[1]))
            except (TypeError, ValueError):
                pass
        gx = getattr(room, "grid_x", None)
        gy = getattr(room, "grid_y", None)
        if gx is not None and gy is not None:
            try:
                return (int(gx), int(gy))
            except (TypeError, ValueError):
                pass
        # Pocket / zone exit stamp toward Lebanon if nothing else.
        oex = getattr(room, "overland_exit_macro", None)
        if isinstance(oex, (list, tuple)) and len(oex) == 2:
            try:
                return (int(oex[0]), int(oex[1]))
            except (TypeError, ValueError):
                pass
    return (35, 10)  # Lebanon default


def _override_map(pack):
    """Build (x,y) → region from atlas_overrides."""
    out = {}
    for row in pack.get("atlas_overrides") or []:
        try:
            out[(int(row["x"]), int(row["y"]))] = str(row["region"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _glyph_at(game, mx, my):
    """Atlas map_glyph for a macro cell, or None."""
    try:
        from engine.systems import overland as overland_mod

        atlas = overland_mod.ensure_game_overland(game) if game else None
        if atlas is None:
            atlas = overland_mod.load_earth_america_atlas()
        cell = atlas.terrain.get((int(mx), int(my)))
        if not cell:
            return None
        return cell.get("map_glyph") or cell.get("glyph")
    except Exception:
        return None


def _nearest_land_region(game, mx, my, pack, overrides):
    """BFS to nearest non-water cell with a resolvable region."""
    from collections import deque

    glyph_default = pack.get("glyph_default") or {}
    water = {g for g, rid in glyph_default.items() if rid is None}
    seen = set()
    q = deque([(int(mx), int(my), 0)])
    while q:
        x, y, dist = q.popleft()
        if (x, y) in seen or dist > 24:
            continue
        seen.add((x, y))
        if (x, y) in overrides:
            return overrides[(x, y)]
        g = _glyph_at(game, x, y)
        if g is not None and g not in water:
            mapped = glyph_default.get(g, FALLBACK_REGION)
            if mapped:
                return mapped
            # Landmark letters / roads → plains default.
            if g not in water:
                return FALLBACK_REGION
        for dx, dy in (
            (0, -1), (0, 1), (-1, 0), (1, 0),
            (-1, -1), (1, -1), (-1, 1), (1, 1),
        ):
            q.append((x + dx, y + dy, dist + 1))
    return FALLBACK_REGION


def resolve_region(room, game, character=None):
    """Resolve climate region id for a room / actor location."""
    pack = get_pack()
    # 1. Explicit room / zone override.
    if room is not None:
        rid = getattr(room, "climate_region", None)
        if rid:
            return str(rid)
        zone = getattr(room, "zone", None)
        # Zone may be a string id or an object with climate_region.
        if hasattr(zone, "climate_region") and zone.climate_region:
            return str(zone.climate_region)
        # Authored zone JSON sometimes stamps climate on the room dict
        # only; also check game.zones metadata when present.
        zkey = getattr(room, "zone", None)
        if isinstance(zkey, str) and game is not None:
            zones = getattr(game, "zones", None) or {}
            zmeta = zones.get(zkey) if isinstance(zones, dict) else None
            if isinstance(zmeta, dict) and zmeta.get("climate_region"):
                return str(zmeta["climate_region"])
            # Lebanon town pocket → great_plains without atlas stamp.
            if "lebanon" in zkey.lower():
                return "great_plains"

    mx, my = _macro_xy_for_room(room, game, character)
    overrides = _override_map(pack)
    # 2–3. Atlas override at cell.
    if (mx, my) in overrides:
        return overrides[(mx, my)]
    # 4. Glyph heuristic (+ nearest land for water).
    glyph = _glyph_at(game, mx, my)
    glyph_default = pack.get("glyph_default") or {}
    if glyph is not None:
        mapped = glyph_default.get(glyph, "__missing__")
        if mapped is None:
            return _nearest_land_region(game, mx, my, pack, overrides)
        if mapped != "__missing__":
            return str(mapped)
        # Unknown glyph (hub letter, highway): treat as land → plains/nearest.
        if glyph in ("~", "o"):
            return _nearest_land_region(game, mx, my, pack, overrides)
        return FALLBACK_REGION
    # 5. Fallback.
    return FALLBACK_REGION


# ---------------------------------------------------------------------------
# Monthly interpolate + seeded roll
# ---------------------------------------------------------------------------

def _month_band(region_id, month):
    """Return the month dict (1..12) for a region, or None."""
    regions = get_pack().get("regions") or {}
    entry = regions.get(region_id) or regions.get(FALLBACK_REGION) or {}
    months = entry.get("months") or []
    for m in months:
        if int(m.get("month") or 0) == int(month):
            return m
    if months:
        return months[max(0, min(len(months) - 1, int(month) - 1))]
    return None


def _lerp(a, b, t):
    """Linear interpolate numbers."""
    return a + (b - a) * t


def _interpolate_bands(region_id, month, day_of_month, year):
    """Lerp numeric fields between this month and next."""
    a = _month_band(region_id, month) or {}
    next_m = 1 if int(month) >= 12 else int(month) + 1
    b = _month_band(region_id, next_m) or a
    n = max(1, _days_in_month(month, year))
    t = (max(1, int(day_of_month)) - 1) / float(n)
    out = {
        "tmax_f": _lerp(float(a.get("tmax_f", 70)), float(b.get("tmax_f", 70)), t),
        "tmin_f": _lerp(float(a.get("tmin_f", 40)), float(b.get("tmin_f", 40)), t),
        "precip_chance": _lerp(
            float(a.get("precip_chance", 0.2)),
            float(b.get("precip_chance", 0.2)),
            t,
        ),
        "snow_chance": _lerp(
            float(a.get("snow_chance", 0)),
            float(b.get("snow_chance", 0)),
            t,
        ),
        "storm_chance": _lerp(
            float(a.get("storm_chance", 0.05)),
            float(b.get("storm_chance", 0.05)),
            t,
        ),
        "tornado_chance": _lerp(
            float(a.get("tornado_chance", 0)),
            float(b.get("tornado_chance", 0)),
            t,
        ),
        "wind_band": list(a.get("wind_band") or ["calm", "light", "moderate"]),
        "condition_weights": dict(a.get("condition_weights") or {}),
    }
    # Lerp weights then renormalize.
    wa = dict(a.get("condition_weights") or {})
    wb = dict(b.get("condition_weights") or {})
    keys = set(wa) | set(wb) | set(CONDITIONS)
    lerped = {}
    for k in keys:
        lerped[k] = _lerp(float(wa.get(k, 0)), float(wb.get(k, 0)), t)
    out["condition_weights"] = lerped
    return out


def _rng_for(year, day_of_year, region_id):
    """Stable RNG for one region-day."""
    seed = f"{int(year)}:{int(day_of_year)}:{region_id}"
    return random.Random(seed)


def _weighted_pick(weights, rng):
    """Pick a condition id from a weight map."""
    bag = []
    for cid, w in (weights or {}).items():
        if cid not in CONDITIONS:
            continue
        n = int(round(float(w)))
        if n > 0:
            bag.extend([cid] * n)
    if not bag:
        return "clear"
    return rng.choice(bag)


def _period_temp_factor(period):
    """0 near tmin (night) … 1 near tmax (day)."""
    return {
        "night": 0.15,
        "dawn": 0.35,
        "day": 0.85,
        "dusk": 0.55,
    }.get(period or "day", 0.7)


def _forecast_line(condition, season, rng):
    """One short outlook stub."""
    options = {
        "clear": (
            "Skies stay fair through the next watch.",
            "High pressure holding -- clear overnight.",
        ),
        "cloudy": (
            "Clouds linger; a break possible by morning.",
            "Overcast continues with little change.",
        ),
        "fog": (
            "Fog burns back after dawn; drive careful.",
            "Patchy fog overnight in low ground.",
        ),
        "rain": (
            "Showers taper by evening.",
            "More rain likely before the front clears.",
        ),
        "storm": (
            "Storm cell moving out; gusty winds easing.",
            "Severe weather watch expires after midnight.",
        ),
        "snow": (
            "Snow tapers; roads stay slick overnight.",
            "Additional flurries possible toward morning.",
        ),
    }
    pool = list(options.get(condition, options["cloudy"]))
    if season == "winter" and condition == "rain":
        pool.append("Rain may mix with ice on untreated roads.")
    return rng.choice(pool)


def _roll_region_day(game, region_id, cal=None):
    """Build a fresh regional snapshot for today (stable seed)."""
    cal = cal or _cal(game)
    year = int(cal.get("year") or 2024)
    month = int(cal.get("month") or 1)
    dom = int(cal.get("day_of_month") or cal.get("day") or 1)
    doy = _day_of_year(cal)
    period = cal.get("day_period") or "day"
    season = cal.get("season") or "autumn"
    bands = _interpolate_bands(region_id, month, dom, year)
    rng = _rng_for(year, doy, region_id)

    weights = dict(bands["condition_weights"])
    # Bias wet/storm/snow from chances (soft nudge on top of weights).
    if rng.random() < bands["precip_chance"]:
        for k in ("rain", "storm", "snow"):
            weights[k] = float(weights.get(k, 0)) + 2
    if rng.random() < bands["storm_chance"]:
        weights["storm"] = float(weights.get("storm", 0)) + 3
    if rng.random() < bands["snow_chance"]:
        weights["snow"] = float(weights.get("snow", 0)) + 3
        # Cold regions: snow displaces rain a bit.
        weights["rain"] = max(0, float(weights.get("rain", 0)) - 1)

    condition = _weighted_pick(weights, rng)
    # Tropical / warm: block snow if tmin high.
    if bands["tmin_f"] > 40 and condition == "snow":
        condition = "rain" if bands["precip_chance"] > 0.15 else "cloudy"

    tmax = bands["tmax_f"] + rng.uniform(-3, 3)
    tmin = bands["tmin_f"] + rng.uniform(-3, 3)
    if tmin > tmax:
        tmin, tmax = tmax, tmin
    temp_f = int(round(_lerp(tmin, tmax, _period_temp_factor(period))))

    wind_band = bands["wind_band"] or ["calm", "light", "moderate"]
    wind = rng.choice(list(wind_band))
    if condition == "storm":
        wind = rng.choice(("gusty", "strong"))

    # Tornado watch arm (rare): only when already storm-class day.
    tornado_watch = False
    if condition == "storm" and bands["tornado_chance"] > 0:
        if rng.random() < bands["tornado_chance"]:
            tornado_watch = True

    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    return {
        "condition": condition,
        "temp_f": temp_f,
        "tmax_f": int(round(tmax)),
        "tmin_f": int(round(tmin)),
        "wind": wind,
        "season": season,
        "day_period": period,
        "forecast": _forecast_line(condition, season, rng),
        "rolled_at_tick": ticks,
        "region": region_id,
        "region_name": region_display_name(region_id),
        "day_of_year": doy,
        "year": year,
        "tornado_watch": tornado_watch,
        "tornado_chance": bands["tornado_chance"],
        "notes": "",
    }


def _cache_get(game, region_id, doy):
    """Return cached regional snapshot or None."""
    cache = getattr(game, "weather_regional_cache", None)
    if not isinstance(cache, dict):
        return None
    return cache.get((region_id, doy))


def _cache_set(game, region_id, doy, snap):
    """Store regional snapshot on the game object."""
    cache = getattr(game, "weather_regional_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        game.weather_regional_cache = cache
    cache[(region_id, doy)] = snap
    # Keep a legacy global blob for older callers (Lebanon / home).
    if region_id == HOME_REGION:
        game.weather_state = dict(snap)


def _apply_force(game, snap, region_id):
    """Overlay GM force pins onto a snapshot."""
    force = getattr(game, "weather_force", None)
    if not isinstance(force, dict):
        return snap
    # Global pin or matching region.
    scope = force.get("region_id")
    if scope and scope != region_id and scope != "all":
        return snap
    out = dict(snap)
    if force.get("condition") in CONDITIONS:
        out["condition"] = force["condition"]
    if force.get("temp_f") is not None:
        out["temp_f"] = int(force["temp_f"])
    if force.get("wind") is not None:
        out["wind"] = str(force["wind"])
    if force.get("forecast") is not None:
        out["forecast"] = str(force["forecast"])
    out["forced"] = True
    return out


def snapshot(game, *, room=None, region_id=None, character=None):
    """Return live weather for a region (cached per game-day).

    Prefer ``weather_for_room`` when a room is known. Without region, uses
    HOME_REGION (Lebanon plains) so radio / Discord stay coherent.
    """
    load_climate_pack()
    cal = _cal(game)
    doy = _day_of_year(cal)
    rid = region_id
    if not rid:
        if room is not None or character is not None:
            rid = resolve_region(room, game, character)
        else:
            rid = HOME_REGION
    snap = _cache_get(game, rid, doy)
    if snap is None:
        snap = _roll_region_day(game, rid, cal)
        _cache_set(game, rid, doy, snap)
        # Discord / town-radio WX blast is NOT on cache miss -- that
        # re-fired on every game restart. Scheduled bulletins only
        # (see _maybe_scheduled_weather_bulletin).
        # Natural tornado arm once per region-day when watch flags.
        if snap.get("tornado_watch"):
            _maybe_natural_tornado(game, rid, snap)
    else:
        # Keep period / temp feel honest as the clock advances.
        period = cal.get("day_period") or snap.get("day_period")
        snap["day_period"] = period
        snap["season"] = cal.get("season") or snap.get("season")
        tmin = float(snap.get("tmin_f", snap.get("temp_f", 50)))
        tmax = float(snap.get("tmax_f", snap.get("temp_f", 70)))
        snap["temp_f"] = int(round(_lerp(tmin, tmax, _period_temp_factor(period))))
    return _apply_force(game, snap, rid)


def weather_for_room(room, game, character=None):
    """Regional weather for a specific room (preferred player path)."""
    rid = resolve_region(room, game, character)
    return snapshot(game, room=room, region_id=rid, character=character)


def force_condition(
    game,
    condition,
    *,
    temp_f=None,
    wind=None,
    forecast=None,
    region_id=None,
):
    """Staff / test helper: pin a condition (region or global)."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown weather condition: {condition!r}")
    game.weather_force = {
        "condition": condition,
        "temp_f": temp_f,
        "wind": wind,
        "forecast": forecast,
        "region_id": region_id or "all",
    }
    # Bust caches so next snapshot sees the force.
    game.weather_regional_cache = {}
    # Also refresh legacy blob.
    snap = snapshot(game, region_id=region_id or HOME_REGION)
    game.weather_state = dict(snap)
    return snap


def clear_force(game):
    """Drop GM condition force (tornado tracks untouched)."""
    game.weather_force = None
    game.weather_regional_cache = {}
    return snapshot(game, region_id=HOME_REGION)


# ---------------------------------------------------------------------------
# Player / radio text
# ---------------------------------------------------------------------------

def report_lines(game, room=None, character=None):
    """Plain-text weather radio lines (tag added by radio layer)."""
    # Radio/vehicles don't really reach a Reach, but gate defensively so a
    # misrouted call never fabricates CONUS numbers for a plane room.
    effective_room = room if room is not None else getattr(
        character, "location", None,
    )
    if _is_elemental_realm(effective_room):
        return [
            "No regional broadcast reaches this plane -- conditions here "
            "are ambient only.",
            _elemental_flavor_line(effective_room),
        ]
    if room is not None or character is not None:
        w = weather_for_room(room, game, character)
    else:
        w = snapshot(game, region_id=HOME_REGION)
    cal = _cal(game)
    weekday = cal.get("weekday_name") or cal.get("weekday") or "today"
    month = cal.get("month_name") or ""
    day = cal.get("day_of_month") or cal.get("day") or ""
    date_bit = f"{weekday}, {month} {day}".strip()
    cond = w["condition"]
    temp = w["temp_f"]
    wind = w["wind"]
    period = w.get("day_period") or "day"
    forecast = w.get("forecast") or "Conditions unchanged."
    region = w.get("region_name") or region_display_name(w.get("region"))
    lines = [
        f"Regional conditions ({region}) for {date_bit}: {cond}, "
        f"about {temp} degrees, {wind} wind, {period}.",
        f"Forecast: {forecast}",
        f"Traveler advisory: roads are ordinary for {cond} weather.",
    ]
    if w.get("tornado_watch") or _region_has_active_tornado(game, w.get("region")):
        lines.insert(
            1,
            "[WARNING] Tornado watch or active funnel in this climate region "
            "-- seek sturdy shelter.",
        )
    return lines


def current_blurb(character=None):
    """Short WX blurb for vehicle NEWS/WX kit lines."""
    game = getattr(character, "game", None) if character else None
    room = getattr(character, "location", None) if character else None
    # Cars don't drive on the Reaches, but gate defensively -- same reason
    # as report_lines above.
    if _is_elemental_realm(room):
        return _elemental_flavor_line(room)
    if game is None:
        return "Conditions ordinary; check the weather dial for a bulletin."
    w = weather_for_room(room, game, character) if room else snapshot(game)
    bit = f"{w['condition']}, about {w['temp_f']}F, {w.get('wind', 'calm')} wind"
    if _nearby_tornado(game, room, character):
        return f"[WARNING] Tornado threat nearby. {bit}."
    return f"{bit}."


def _elemental_flavor_line(room, *, screenreader=False, rng=None):
    """One ambient flavor line for a Reach room -- no mechanics attached.

    Picks from ``ELEMENTAL_FLAVOR_LINES`` by the room's plane id (falls
    back to a generic elemental line for an unrecognized plane so this
    never raises). Uses the same plain ``[WX]`` / ``Weather:`` label
    convention as the CONUS clauses below so screenreader users get
    identical framing either way.
    """
    plane = _room_plane(room)
    pool = ELEMENTAL_FLAVOR_LINES.get(plane) or _ELEMENTAL_FLAVOR_FALLBACK
    roller = rng if rng is not None else random
    line = roller.choice(pool)
    if screenreader:
        return f"Weather: {line}"
    return f"[WX] {line}"


# Eerie pit weather -- not Earth CONUS rain (bug #95).
_PIT_LOOK_LINES = (
    "Ash hangs still. No sky answers here.",
    "The air tastes of old verdicts and cold stone.",
    "Silence presses in -- no wind, yet dust drifts.",
    "Grey light from nowhere; the pit has no weather, only mood.",
    "A distant grind echoes, then fades into ash.",
)


def _pit_look_clause(room, *, screenreader=False):
    """One labeled look line for Purgatory pit pocket floors."""
    floor = int(getattr(room, "pit_floor", 0) or 0)
    key = getattr(room, "key", "") or ""
    rng = random.Random(hash((floor, key, len(_PIT_LOOK_LINES))))
    line = rng.choice(_PIT_LOOK_LINES)
    if screenreader:
        return f"Weather: {line}"
    return f"[WX] {line}"


def look_clause(room, game, *, screenreader=False, character=None):
    """One look-line for weather (outdoor full / indoor dampen).

    Always pairs meaning with a plain ``[WX]`` / ``Weather:`` label —
    never color alone.
    """
    if room is None or game is None:
        return None
    if getattr(room, "purgatory_pit", False):
        return _pit_look_clause(room, screenreader=screenreader)
    if _is_elemental_realm(room):
        return _elemental_flavor_line(room, screenreader=screenreader)
    plane = _room_plane(room)
    outdoor = bool(getattr(room, "outdoor", False))
    # Off-Earth indoor rooms must not show CONUS rain / °F chrome
    # (Purgatory Waystation bug #73 / #74).
    if plane and plane != "earth":
        if outdoor:
            return _elemental_flavor_line(room, screenreader=screenreader)
        return None
    w = weather_for_room(room, game, character)
    cond = w.get("condition") or "clear"
    temp = w.get("temp_f")
    wind = w.get("wind") or "calm"
    near = _nearby_tornado(game, room, character)
    if near:
        scale = near.get("scale") or "EF?"
        if screenreader:
            return (
                f"Weather warning: tornado {scale} nearby. "
                f"Seek sturdy shelter. About {temp} F."
            )
        return (
            f"[WARNING] Tornado ({scale}) nearby — seek sturdy shelter. "
            f"[WX] {cond}, about {temp} F, {wind} wind."
        )
    if outdoor:
        if screenreader:
            return f"Weather: {cond}. {wind} wind. About {temp} F."
        return f"[WX] {cond}, about {temp} F, {wind} wind."
    # Indoor dampen — only speak sky when precip/storm/tornado matter.
    if cond in ("rain", "storm", "snow") or w.get("tornado_watch"):
        damp = {
            "rain": "Rain ticks on the roof.",
            "storm": "The wind presses the windows.",
            "snow": "Snow brushes the panes.",
        }.get(cond, "Weather presses the windows.")
        if screenreader:
            return f"Weather: indoor. {damp} Outside about {temp} F."
        return f"[WX] Indoor — {damp} Outside about {temp} F."
    return None


def assess_look_vision(
    character,
    room,
    game,
    *,
    screenreader=False,
    after_move=False,
    rng=None,
):
    """Hybrid outdoor look vision for rain / storm / snow / nearby tornado.

    Signature matches ``hooks.weather_look_vision`` (character first).

    Returns ``None`` when vision is not impaired (clear indoor, cellar,
    or fair sky). Otherwise a dict::

        {
            "overlay": str,       # always-on hard-to-see line ([WX] / Weather:)
            "whiteout": bool,     # hide desc / people / items / exits
            "fail_line": str|None # replaces room description when whiteout
        }

    Vehicles soften whiteout chance; auto-look after a move rarely whites
    out so walking the atlas does not brick every step. Plain labels pair
    every line — never color alone (hard rule 7).
    """
    if room is None or game is None or character is None:
        return None
    if _is_elemental_realm(room):
        # Ambient-only: never fight the player for vision on a Reach.
        return None
    shelter = _shelter_class(character, room)
    # Sturdy indoor / storm cellar: dampen clause only (no vision fight).
    if shelter in ("indoor", "cellar"):
        return None
    # Outdoor streets and vehicle windows only.
    if shelter == "outdoor" and not getattr(room, "outdoor", False):
        return None

    w = weather_for_room(room, game, character)
    cond = w.get("condition") or "clear"
    near = _nearby_tornado(game, room, character, radius=1)
    # Severity key: funnel nearby beats day-condition.
    if near:
        severity = "tornado"
    elif cond in ("rain", "storm", "snow"):
        severity = cond
    else:
        return None

    overlay = _vision_overlay_line(severity, screenreader=screenreader)
    if overlay is None:
        return None

    chance = float(VISION_WHITEOUT_CHANCE.get(severity, 0.0))
    if shelter == "vehicle":
        chance *= VISION_VEHICLE_WHITEOUT_MULT
    if after_move:
        chance *= VISION_AFTER_MOVE_WHITEOUT_MULT

    roller = rng if rng is not None else random
    whiteout = bool(chance > 0 and roller.random() < chance)
    fail_line = None
    if whiteout:
        fail_line = _vision_fail_line(
            severity, screenreader=screenreader, rng=roller,
        )
    return {
        "overlay": overlay,
        "whiteout": whiteout,
        "fail_line": fail_line,
    }


def _vision_overlay_line(severity, *, screenreader=False):
    """Always-on vision-hard line for severe outdoor weather."""
    if screenreader:
        lines = {
            "rain": "Weather: rain makes it hard to see far.",
            "storm": "Weather: storm curtains make it hard to see far.",
            "snow": "Weather: blowing snow cuts visibility.",
            "tornado": (
                "Weather warning: debris and rain make it almost "
                "impossible to see."
            ),
        }
    else:
        lines = {
            "rain": "[WX] Rain sheets make it hard to see far.",
            "storm": "[WX] Curtains of rain and wind make it hard to see far.",
            "snow": "[WX] Blowing snow cuts visibility to a few paces.",
            "tornado": (
                "[WX] Debris and rain whip past; you can barely see."
            ),
        }
    return lines.get(severity)


def _vision_fail_line(severity, *, screenreader=False, rng=None):
    """Random whiteout replacement for the room description."""
    roller = rng if rng is not None else random
    if screenreader:
        pools = {
            "storm": (
                "You cannot see through the rain.",
                "The storm blanks the street.",
                "Wind and water erase the view.",
            ),
            "snow": (
                "You cannot see through the snow.",
                "Whiteout. Nothing clear beyond a pace.",
                "Blowing snow blinds you for a moment.",
            ),
            "tornado": (
                "You cannot see through the debris and rain.",
                "The funnel weather blots out the street.",
                "Wind and grit erase everything ahead.",
            ),
            "rain": (
                "You cannot see through the rain.",
            ),
        }
    else:
        pools = {
            "storm": (
                "[WX] You can't see through the rain.",
                "[WX] The storm blanks the street.",
                "[WX] Wind and water erase the view.",
            ),
            "snow": (
                "[WX] You can't see through the snow.",
                "[WX] Whiteout — nothing clear beyond a pace.",
                "[WX] Blowing snow blinds you for a moment.",
            ),
            "tornado": (
                "[WX] You can't see through the debris and rain.",
                "[WX] Funnel weather blots out the street.",
                "[WX] Wind and grit erase everything ahead.",
            ),
            "rain": (
                "[WX] You can't see through the rain.",
            ),
        }
    pool = pools.get(severity) or pools["storm"]
    return roller.choice(pool)


def player_weather_report(character, game):
    """Full on-demand ``weather`` verb text."""
    room = getattr(character, "location", None)
    if _is_elemental_realm(room):
        return (
            "[WX] This plane has no mechanical weather -- conditions are "
            "ambient only.\r\n"
            f"{_elemental_flavor_line(room)}"
        )
    w = weather_for_room(room, game, character)
    lines = [
        f"[WX] {w.get('region_name') or region_display_name(w.get('region'))}",
        f"Condition: {w['condition']}. Temp: about {w['temp_f']} F "
        f"(day range {w.get('tmin_f')}–{w.get('tmax_f')} F).",
        f"Wind: {w.get('wind')}. Period: {w.get('day_period')}.",
        f"Forecast: {w.get('forecast')}",
    ]
    if w.get("tornado_watch"):
        lines.append("[WARNING] Tornado watch active for this region today.")
    for tline in tornado_warning_lines(game, room, character):
        lines.append(tline)
    return "\r\n".join(lines)


def player_forecast_report(character, game):
    """Short ``forecast`` verb text."""
    room = getattr(character, "location", None)
    if _is_elemental_realm(room):
        return (
            "[WX] Forecast: not applicable -- this plane's weather is "
            "ambient only, no forward conditions to call.\r\n"
            f"{_elemental_flavor_line(room)}"
        )
    w = weather_for_room(room, game, character)
    return (
        f"[WX] Forecast ({w.get('region_name')}): {w.get('forecast')}\r\n"
        f"Now: {w['condition']}, about {w['temp_f']} F, {w.get('wind')} wind."
    )


# ---------------------------------------------------------------------------
# Tornado tracks
# ---------------------------------------------------------------------------

def _ensure_tornadoes(game):
    """Attach tornado list on game if missing."""
    tracks = getattr(game, "weather_tornadoes", None)
    if not isinstance(tracks, list):
        tracks = []
        game.weather_tornadoes = tracks
    return tracks


def list_tornadoes(game):
    """Return active tornado track dicts."""
    return list(_ensure_tornadoes(game))


def _parse_scale(scale):
    """Normalize EF label or 0..5 int → EF string."""
    if scale is None:
        return "EF0"
    s = str(scale).strip().upper()
    if s.isdigit():
        s = f"EF{int(s)}"
    if not s.startswith("EF"):
        s = f"EF{s}"
    if s not in EF_SCALES:
        raise ValueError(f"unknown tornado scale: {scale!r}")
    return s


def _parse_heading(direction):
    """Normalize cardinal / ordinal heading."""
    d = (direction or "n").strip().lower()
    aliases = {
        "north": "n", "south": "s", "east": "e", "west": "w",
        "northeast": "ne", "northwest": "nw",
        "southeast": "se", "southwest": "sw",
    }
    d = aliases.get(d, d)
    if d not in HEADINGS:
        raise ValueError(f"unknown heading: {direction!r}")
    return d


def spawn_tornado(
    game,
    *,
    mx,
    my,
    scale=None,
    heading=None,
    micro=None,
    ttl=None,
    natural=False,
):
    """Create a moving tornado track on the atlas. Returns the track dict."""
    rng = random.Random(
        f"tornado:{getattr(game, 'game_time_ticks', 0)}:{mx}:{my}"
    )
    if scale is None:
        # Natural rolls bias EF0–EF2.
        scale = rng.choices(
            EF_SCALES, weights=(40, 30, 20, 7, 2, 1), k=1
        )[0]
    else:
        scale = _parse_scale(scale)
    if heading is None:
        heading = rng.choice(HEADINGS)
    else:
        heading = _parse_heading(heading)
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    track = {
        "id": f"t{ticks}-{mx}-{my}-{rng.randint(100, 999)}",
        "scale": scale,
        "heading": heading,
        "macro_xy": (int(mx), int(my)),
        "micro_xy": (
            (int(micro[0]), int(micro[1]))
            if isinstance(micro, (list, tuple)) and len(micro) == 2
            else None
        ),
        "ttl_steps": int(ttl if ttl is not None else TORNADO_DEFAULT_TTL_STEPS),
        "next_step_tick": ticks + TORNADO_STEP_TICKS,
        "cells_hit": [],  # "mx,my" keys already swept for damage
        "natural": bool(natural),
        "spawned_at_tick": ticks,
    }
    _ensure_tornadoes(game).append(track)
    # Town pass immediately if spawn lands on a populated cell.
    apply_town_pass(game, track)
    if _region_for_macro(game, mx, my) == HOME_REGION:
        _mirror_discord_tornado(track)
    return track


def clear_tornadoes(game, *, here_xy=None, clear_all=False):
    """Remove tornado tracks. Returns count cleared."""
    tracks = _ensure_tornadoes(game)
    if clear_all or here_xy is None:
        n = len(tracks)
        game.weather_tornadoes = []
        return n
    hx, hy = int(here_xy[0]), int(here_xy[1])
    keep = []
    cleared = 0
    for t in tracks:
        mx, my = t.get("macro_xy") or (None, None)
        if mx == hx and my == hy:
            cleared += 1
        else:
            keep.append(t)
    game.weather_tornadoes = keep
    return cleared


def _region_for_macro(game, mx, my):
    """Climate region for an atlas cell."""
    pack = get_pack()
    overrides = _override_map(pack)
    if (mx, my) in overrides:
        return overrides[(mx, my)]
    glyph = _glyph_at(game, mx, my)
    glyph_default = pack.get("glyph_default") or {}
    if glyph in ("~", "o") or glyph_default.get(glyph) is None and glyph in ("~", "o"):
        return _nearest_land_region(game, mx, my, pack, overrides)
    mapped = glyph_default.get(glyph)
    if mapped:
        return mapped
    return FALLBACK_REGION


def _region_has_active_tornado(game, region_id):
    """True if any funnel is currently in that climate region."""
    if not region_id:
        return False
    for t in list_tornadoes(game):
        mx, my = t.get("macro_xy") or (None, None)
        if mx is None:
            continue
        if _region_for_macro(game, mx, my) == region_id:
            return True
    return False


def _nearby_tornado(game, room, character=None, *, radius=1):
    """Closest active tornado within Chebyshev radius of actor/room, or None."""
    mx, my = _macro_xy_for_room(room, game, character)
    best = None
    best_d = None
    for t in list_tornadoes(game):
        tx, ty = t.get("macro_xy") or (None, None)
        if tx is None:
            continue
        d = max(abs(int(tx) - mx), abs(int(ty) - my))
        if d <= radius and (best_d is None or d < best_d):
            best = t
            best_d = d
    return best


def tornado_warning_lines(game, room=None, character=None):
    """Player-facing [WARNING] lines for nearby / regional funnels."""
    lines = []
    near = _nearby_tornado(game, room, character, radius=0)
    if near:
        lines.append(
            f"[WARNING] Tornado {near.get('scale')} overhead / same cell "
            f"— heading {near.get('heading')}. Seek sturdy shelter NOW."
        )
        return lines
    near = _nearby_tornado(game, room, character, radius=1)
    if near:
        mx, my = near.get("macro_xy")
        lines.append(
            f"[WARNING] Tornado {near.get('scale')} near ({mx},{my}), "
            f"heading {near.get('heading')}."
        )
    rid = resolve_region(room, game, character) if room else HOME_REGION
    if _region_has_active_tornado(game, rid) and not lines:
        lines.append(
            "[WARNING] A tornado is tracking somewhere in this climate region."
        )
    return lines


def _is_ocean_cell(game, mx, my):
    """True when the funnel should quench (deep water ~)."""
    g = _glyph_at(game, mx, my)
    return g == "~"


def _veer_heading(heading, rng):
    """Prefer forward / 45° turns; rare 90°+."""
    idx = HEADINGS.index(heading) if heading in HEADINGS else 0
    roll = rng.random()
    if roll < 0.55:
        return heading
    if roll < 0.80:
        return HEADINGS[(idx + rng.choice((-1, 1))) % 8]
    if roll < 0.95:
        return HEADINGS[(idx + rng.choice((-2, 2))) % 8]
    return HEADINGS[(idx + rng.choice((-3, 3, 4))) % 8]


def _step_tornado(game, track):
    """Advance one cell; return False if dissipated."""
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    if ticks < int(track.get("next_step_tick") or 0):
        return True
    rng = random.Random(f"step:{track.get('id')}:{ticks}")
    if rng.random() < 0.35:
        track["heading"] = _veer_heading(track.get("heading") or "n", rng)
    dx, dy = _HEADING_DELTA.get(track.get("heading") or "n", (0, -1))
    mx, my = track.get("macro_xy") or (35, 10)
    nx, ny = int(mx) + dx, int(my) + dy
    # Atlas bounds (78×18).
    if nx < 0 or ny < 0 or nx >= 78 or ny >= 18:
        return False
    if _is_ocean_cell(game, nx, ny):
        return False
    track["macro_xy"] = (nx, ny)
    track["ttl_steps"] = int(track.get("ttl_steps") or 1) - 1
    track["next_step_tick"] = ticks + TORNADO_STEP_TICKS
    apply_town_pass(game, track)
    _maybe_flee_indoors(game, track)
    if track["ttl_steps"] <= 0:
        return False
    return True


def _shelter_class(character, room):
    """Return 'cellar', 'indoor', 'vehicle', or 'outdoor'."""
    if getattr(character, "in_vehicle", None):
        return "vehicle"
    if room is None:
        return "outdoor"
    resources = {str(r).lower() for r in (getattr(room, "resources", None) or ())}
    key = str(getattr(room, "key", "") or "").lower()
    if (
        "storm_shelter" in resources
        or "clinic" in resources
        or getattr(room, "hospital", False)
        or "bunker" in key
        or "storm watch" in key
        or "storm_watch" in key
    ):
        return "cellar"
    if not getattr(room, "outdoor", False):
        return "indoor"
    return "outdoor"


def _tier_of(character):
    """Safe character tier int."""
    try:
        return int(getattr(character, "tier", 0) or 0)
    except (TypeError, ValueError):
        return 0


def apply_town_pass(game, track):
    """Once per cell entry: injure exposed people in rooms on this macro.

    Skips if this macro was already swept for this track.
    """
    mx, my = track.get("macro_xy") or (None, None)
    if mx is None:
        return
    cell_key = f"{mx},{my}"
    hit = track.setdefault("cells_hit", [])
    if cell_key in hit:
        return
    hit.append(cell_key)

    scale = track.get("scale") or "EF0"
    # Only bother when this macro is a town/hub/pocket (landmark or city).
    if not _macro_is_settled(game, mx, my):
        return

    victims = _characters_on_macro(game, mx, my)
    for ch in victims:
        _tornado_hit_character(game, ch, track, scale)


def _macro_is_settled(game, mx, my):
    """True for hubs / town pockets / city glyphs."""
    try:
        from engine.systems import overland as overland_mod

        atlas = overland_mod.ensure_game_overland(game)
        if atlas.landmark_at(mx, my):
            return True
        cell = atlas.terrain.get((mx, my)) or {}
        if (cell.get("area_type") or "") in ("city", "town"):
            return True
        g = cell.get("map_glyph") or ""
        if g and g.isalpha():
            return True
        if g == "*":
            return True
    except Exception:
        pass
    # Lebanon coords always count.
    return (mx, my) in ((35, 10), (35, 11), (36, 10))


def _characters_on_macro(game, mx, my):
    """Characters whose best macro pos matches (mx, my)."""
    out = []
    world = getattr(game, "world", None)
    chars = getattr(world, "characters", None) if world else None
    if not isinstance(chars, dict):
        # Fallback: scan rooms.
        rooms = getattr(world, "rooms", None) if world else None
        if isinstance(rooms, dict):
            for room in rooms.values():
                for obj in getattr(room, "contents", None) or ():
                    if getattr(obj, "key", None) and hasattr(obj, "location"):
                        if _macro_xy_for_room(room, game, obj) == (mx, my):
                            out.append(obj)
        return out
    for ch in chars.values():
        room = getattr(ch, "location", None)
        if _macro_xy_for_room(room, game, ch) == (mx, my):
            out.append(ch)
    return out


def _tornado_hit_character(game, character, track, scale):
    """Apply shelter + tier gate; damage or near-miss tell."""
    if getattr(character, "hospitalized", False):
        return
    if getattr(character, "is_spirit", False) or getattr(character, "spirit", False):
        return
    room = getattr(character, "location", None)
    shelter = _shelter_class(character, room)
    msg = None
    dmg = int(EF_DAMAGE.get(scale, 0))

    if scale == "EF0":
        msg = (
            f"[WARNING] A weak rope funnel ({scale}) scrapes past — "
            "debris grit and loud wind, no real injury."
        )
        _tell(character, msg)
        return

    gate = EF_TIER_GATE.get(scale)
    tier = _tier_of(character)
    resists = gate is not None and tier >= gate

    if shelter == "cellar":
        if scale in ("EF4", "EF5"):
            dmg = max(1, dmg // 4)
        else:
            msg = (
                f"[WARNING] Tornado {scale} passes — sturdy shelter holds. "
                "You ride it out."
            )
            _tell(character, msg)
            return
    elif shelter == "indoor":
        if scale in ("EF0", "EF1"):
            msg = (
                f"[WARNING] Tornado {scale} rattles the building. "
                "Indoor walls take the scrape."
            )
            _tell(character, msg)
            return
        dmg = max(1, dmg // 2)
    elif shelter == "vehicle":
        # Cars are not storm cellars.
        msg_prefix = (
            f"[WARNING] Tornado {scale} — a vehicle is NOT sturdy shelter. "
        )
        if resists:
            _tell(
                character,
                msg_prefix + "You muscle through the near miss.",
            )
            return
        _tell(character, msg_prefix + "Glass and debris find you.")
    else:
        # Outdoor
        if resists:
            _tell(
                character,
                f"[WARNING] Tornado {scale} — you are strong enough to "
                "weather the near miss outdoors.",
            )
            return
        _tell(
            character,
            f"[WARNING] Tornado {scale} catches you outdoors — "
            "debris and wind tear at you.",
        )

    if resists and shelter != "vehicle":
        return
    if dmg <= 0:
        return

    hp = int(getattr(character, "hp", 0) or 0)
    character.hp = max(0, hp - dmg)
    _tell(character, f"[DMG] Tornado debris hits you for {dmg} HP.")
    if int(getattr(character, "hp", 0) or 0) <= 0:
        try:
            from engine import hooks as hooks_mod

            if hooks_mod.weather_clinic_admit(game, character, reason="injury"):
                _tell(
                    character,
                    "[ALERT] The funnel drops you — you wake in Town Clinic.",
                )
        except Exception as exc:
            print(f"[weather] clinic admit failed: {exc}", flush=True)


def _tell(character, text):
    """Send a viewpoint line (session + GM snoop mirrors).

    Characters have no ``.message`` — use ``snoop.tell`` like other verbs.
    ``session.send`` already appends ``\\r\\n``, so do not double it here.
    """
    snoop.tell(character, text)


def _maybe_flee_indoors(game, track):
    """Light Cadence hint: outdoors NPCs near the funnel prefer indoor rooms."""
    mx, my = track.get("macro_xy") or (None, None)
    if mx is None or not _macro_is_settled(game, mx, my):
        return
    for ch in _characters_on_macro(game, mx, my):
        if getattr(ch, "session", None):
            continue  # players decide
        if not getattr(ch, "is_npc", False) and not getattr(ch, "echo", False):
            # Offline Echoes still count as Cadence-ish; prefer is_npc / no session.
            if getattr(ch, "session", None) is not None:
                continue
        room = getattr(ch, "location", None)
        if room is None or not getattr(room, "outdoor", False):
            continue
        # One cheap hop: first indoor exit.
        for _dir, dest in (getattr(room, "exits", None) or {}).items():
            if dest is not None and not getattr(dest, "outdoor", False):
                try:
                    from engine.npc_act import npc_do

                    npc_do(ch, f"{_dir}", game)
                except Exception:
                    # Fail soft — flee is nice immersion, not required.
                    try:
                        room.contents.remove(ch)
                        dest.contents.append(ch)
                        ch.location = dest
                    except Exception:
                        pass
                break


def _maybe_natural_tornado(game, region_id, snap):
    """Spawn at most one natural funnel per region-day when watch armed."""
    armed = getattr(game, "weather_tornado_armed_days", None)
    if not isinstance(armed, set):
        armed = set()
        game.weather_tornado_armed_days = armed
    key = (region_id, snap.get("day_of_year"), snap.get("year"))
    if key in armed:
        return
    armed.add(key)
    # Pick a land cell in-region (hub overrides first, else Lebanon-ish).
    pack = get_pack()
    candidates = [
        (int(r["x"]), int(r["y"]))
        for r in (pack.get("atlas_overrides") or [])
        if r.get("region") == region_id
    ]
    if not candidates:
        candidates = [(35, 10)]
    rng = _rng_for(snap.get("year"), snap.get("day_of_year"), f"spawn:{region_id}")
    mx, my = rng.choice(candidates)
    spawn_tornado(game, mx=mx, my=my, natural=True)


def _home_bulletin_line(state):
    """Build one plain-language home-region WX line for radio / Discord."""
    if not isinstance(state, dict):
        return None, "weather"
    cond = str(state.get("condition") or "clear")
    temp = state.get("temp_f")
    wind = state.get("wind") or "calm"
    period = state.get("day_period") or "day"
    forecast = state.get("forecast") or "Conditions unchanged."
    region = state.get("region_name") or region_display_name(HOME_REGION)
    line = (
        f"Local conditions ({region}): {cond}, about {temp} degrees, "
        f"{wind} wind, {period}. Forecast: {forecast}"
    )
    kind = "warning" if cond in ("storm", "snow") else "weather"
    if cond == "storm":
        line = f"Severe weather watch -- {line}"
    elif cond == "snow":
        line = f"Winter travel advisory -- {line}"
    if state.get("tornado_watch"):
        kind = "warning"
        line = f"[WARNING] Tornado watch -- {line}"
    return line, kind


def _mirror_discord_weather(state, game=None):
    """Post one WKNZ Discord weather line for the home region.

    Call only from the scheduled bulletin path -- never from snapshot
    cache miss / boot (copyover would re-spam Discord).
    """
    if not isinstance(state, dict):
        return
    if state.get("region") not in (None, HOME_REGION):
        if state.get("region") != HOME_REGION:
            return
    line, kind = _home_bulletin_line(state)
    if not line:
        return
    try:
        from engine import discord_bridge

        discord_bridge.schedule_wknz(line, kind=kind)
    except Exception as exc:
        print(f"[discord_bridge] wknz weather schedule skipped: {exc}", flush=True)


def _bulletin_slot(ticks):
    """Absolute 4-game-hour slot index from tick count (0-based)."""
    ticks = max(0, int(ticks or 0))
    return ticks // WX_BULLETIN_INTERVAL_TICKS


def _maybe_scheduled_weather_bulletin(game):
    """Top-of-hour every 4 game-hours: radio interrupt + Discord WX.

    Fires only on the exact interval boundary (``ticks % 1600 == 0``) and
    never at tick 0 / cold boot -- weather snapshot still arms on startup,
    but the town-radio blast waits for the first real bulletin slot.
    Restart mid-slot cannot re-fire (boundary gate). Duplicate posts on
    the same slot are blocked by ``game.weather_discord_bulletin_slot``.
    """
    if game is None:
        return False
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    # Skip cold start (tick 0) -- weather system may still snapshot.
    if ticks <= 0:
        return False
    if ticks % WX_BULLETIN_INTERVAL_TICKS != 0:
        return False
    slot = _bulletin_slot(ticks)
    last = getattr(game, "weather_discord_bulletin_slot", None)
    if last == slot:
        return False
    # Ensure home snapshot exists, then blast.
    snap = snapshot(game, region_id=HOME_REGION)
    line, _kind = _home_bulletin_line(snap)
    if not line:
        return False
    game.weather_discord_bulletin_slot = slot
    _mirror_discord_weather(snap, game)
    try:
        from engine import hooks as hooks_mod

        hooks_mod.weather_radio_bulletin(game, line)
    except Exception as exc:
        print(f"[weather] radio bulletin interrupt skipped: {exc}", flush=True)
    return True


def _mirror_discord_tornado(track):
    """Discord warning when a funnel involves the home region."""
    scale = track.get("scale")
    pair = track.get("macro_xy") or (None, None)
    mx, my = pair[0], pair[1]
    line = (
        f"[WARNING] Tornado {scale} reported near atlas ({mx},{my}), "
        f"heading {track.get('heading')}."
    )
    try:
        from engine import discord_bridge

        discord_bridge.schedule_wknz(line, kind="warning")
    except Exception as exc:
        print(f"[discord_bridge] wknz tornado schedule skipped: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Atmospheric messaging + tick
# ---------------------------------------------------------------------------

def _atmos_line(condition, tornado_near=False):
    """One sparse ambient beat."""
    if tornado_near:
        return random.choice((
            "[WARNING] The air goes still, then the wind screams.",
            "[WARNING] Distant sirens — funnel weather.",
            "[WARNING] Debris grit stings; seek sturdy shelter.",
        ))
    pools = {
        "clear": ("A dry breeze slides past.", "Sun holds steady."),
        "cloudy": ("Clouds thicken overhead.", "The light goes flat."),
        "fog": ("Fog softens the edges of the street.", "Moisture hangs low."),
        "rain": ("Rain needles the open air.", "Puddles tick underfoot."),
        "storm": (
            "[WX] Thunder mutters to the west.",
            "[WX] Gusts shove at you.",
        ),
        "snow": ("Snowflakes find your collar.", "The cold bites sharper."),
    }
    return random.choice(pools.get(condition, pools["cloudy"]))


def _tick_atmosphere(game):
    """Sparse per-player outdoor (and severe indoor) weather tells."""
    world = getattr(game, "world", None)
    chars = getattr(world, "characters", None) if world else None
    if not isinstance(chars, dict):
        return
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    for ch in chars.values():
        if not getattr(ch, "session", None):
            continue
        room = getattr(ch, "location", None)
        if room is None:
            continue
        if _is_elemental_realm(room):
            # Reach rooms skip CONUS entirely -- ambient flavor beat only,
            # on its own (quieter) cooldown, indoors or out.
            cool = ATMOS_COOLDOWN_TICKS["elemental"]
            last = int(getattr(ch, "weather_atmos_tick", 0) or 0)
            if ticks - last < cool:
                continue
            ch.weather_atmos_tick = ticks
            _tell(ch, _elemental_flavor_line(room))
            continue
        w = weather_for_room(room, game, ch)
        cond = w.get("condition") or "clear"
        near = _nearby_tornado(game, room, ch, radius=1)
        key = "tornado" if near else cond
        cool = ATMOS_COOLDOWN_TICKS.get(key, 300)
        last = int(getattr(ch, "weather_atmos_tick", 0) or 0)
        if ticks - last < cool:
            continue
        # Quiet weather: only outdoors.
        if not near and cond in ("clear", "cloudy") and not getattr(
            room, "outdoor", False
        ):
            continue
        if not near and not getattr(room, "outdoor", False) and cond not in (
            "rain", "storm", "snow",
        ):
            continue
        ch.weather_atmos_tick = ticks
        _tell(ch, _atmos_line(cond, tornado_near=bool(near)))


def tick_all(game):
    """Heartbeat: step tornadoes + sparse atmosphere + scheduled WX blast."""
    load_climate_pack()
    # Ensure home snapshot exists (may arm natural tornado). Does NOT
    # post Discord -- that is the scheduled bulletin below.
    try:
        snapshot(game, region_id=HOME_REGION)
    except Exception as exc:
        print(f"[weather] snapshot tick failed: {exc}", flush=True)

    tracks = _ensure_tornadoes(game)
    keep = []
    for t in tracks:
        try:
            if _step_tornado(game, t):
                keep.append(t)
        except Exception as exc:
            print(f"[weather] tornado step failed: {exc}", flush=True)
    game.weather_tornadoes = keep

    try:
        _tick_atmosphere(game)
    except Exception as exc:
        print(f"[weather] atmosphere tick failed: {exc}", flush=True)

    try:
        _maybe_scheduled_weather_bulletin(game)
    except Exception as exc:
        print(f"[weather] scheduled bulletin failed: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Boot / legacy compatibility
# ---------------------------------------------------------------------------

def _ensure(game):
    """Attach a weather blob on ``game`` if missing (boot-safe)."""
    return snapshot(game, region_id=HOME_REGION)


# Legacy name used by supers/bootstrap before peel.
format_look_clause = look_clause
