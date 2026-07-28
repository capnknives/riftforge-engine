"""
stellar_globe_flight.py -- interactive globe tier for Stellar ``fly``.

Airport montages live in supers/flights.py; this module is the Master+
altitude layer where a Stellar pilots the brass globe with n/s/e/w.
"""

from __future__ import annotations

from world import Room

from engine.systems import globe as globe_mod
from engine.systems import overland as overland_mod

# Degrees moved per cardinal step on the globe disk.
GLOBE_LON_STEP = 4.0
GLOBE_LAT_STEP = 2.0
GLOBE_CHARGE_PER_STEP = 0.05

_DIR_DELTA = {
    "north": (0.0, GLOBE_LAT_STEP),
    "south": (0.0, -GLOBE_LAT_STEP),
    "east": (GLOBE_LON_STEP, 0.0),
    "west": (-GLOBE_LON_STEP, 0.0),
    "n": (0.0, GLOBE_LAT_STEP),
    "s": (0.0, -GLOBE_LAT_STEP),
    "e": (GLOBE_LON_STEP, 0.0),
    "w": (-GLOBE_LON_STEP, 0.0),
}


def _earth_america_macro_to_lonlat(mx, my):
    """Rough CONUS lat/lon from America macro coords (78x18)."""
    lon = -125.0 + (float(mx) / 77.0) * 60.0
    lat = 49.0 - (float(my) / 17.0) * 24.0
    return globe_mod.norm_lon(lon), max(-60.0, min(72.0, lat))


# Per-map_id macro->lonlat projections. America is the only live macro grid
# today (the old Wastes grid is retired dead content -- see starter_town.py),
# so this registry has exactly one entry -- but the dispatch below means a
# future second macro region only needs to call register_macro_projection()
# instead of rewriting macro_to_lonlat() itself.
_MACRO_LONLAT_PROJECTIONS = {}


def register_macro_projection(map_id, fn):
    """Register fn(mx, my) -> (lon, lat) for a macro grid's map_id.

    Lets a future macro region (a second overland atlas) plug its own
    coordinate math into Stellar flight without touching macro_to_lonlat.
    """
    _MACRO_LONLAT_PROJECTIONS[map_id] = fn


def macro_to_lonlat(mx, my, map_id=None):
    """Lat/lon on the globe disk for a macro cell on the given map_id.

    Defaults to America (the only registered projection today) when
    map_id is omitted or unrecognized.
    """
    fn = _MACRO_LONLAT_PROJECTIONS.get(
        map_id or overland_mod.EARTH_AMERICA_ID,
        _earth_america_macro_to_lonlat,
    )
    return fn(mx, my)


register_macro_projection(overland_mod.EARTH_AMERICA_ID, _earth_america_macro_to_lonlat)


def ensure_globe_defaults(character):
    """Fill missing globe-flight coords from macro anchor when needed."""
    if getattr(character, "stellar_globe_lon", None) is None:
        macro = getattr(character, "stellar_flight_macro", None)
        if macro and len(macro) == 2:
            lat, lon = macro_to_lonlat(int(macro[0]), int(macro[1]))
            character.stellar_globe_lat = lat
            character.stellar_globe_lon = lon
        else:
            hub = globe_mod.HUBS.get("lebanon") or {}
            character.stellar_globe_lat = float(hub.get("lat", 39.4))
            character.stellar_globe_lon = float(hub.get("lon", -98.5))


def get_globe_flight_room(game):
    """Return (create if needed) the shared ephemeral globe-flight Room."""
    ensure = getattr(game, "stellar_globe_room", None)
    if ensure is not None:
        return ensure
    room = Room(
        "High Atmosphere -- Globe Flight",
        (
            "The world hangs below as a brass orthographic disk. "
            "Continents wheel under your solar wake; hub cities glint "
            "like pins on the curve. Type n/s/e/w to bank across the sky."
        ),
    )
    room.game = game
    room.outdoor = True
    room.stellar_globe_flight = True
    room.plane = "earth"
    room.realm = "prime"
    for direction in ("north", "south", "east", "west"):
        room.exits[direction] = room
    game.stellar_globe_room = room
    return room


def render_globe_view(character, *, screenreader=False):
    """Player-facing globe frame for look/map while on globe tier."""
    ensure_globe_defaults(character)
    lon = float(character.stellar_globe_lon)
    lat = float(character.stellar_globe_lat)
    if screenreader:
        land = "land" if globe_mod.land_at(lat, lon) else "ocean"
        return (
            f"Globe flight. Position lat {lat:.1f}, lon {lon:.1f} ({land}). "
            f"Move n/s/e/w to fly; fly again for orbit; descend or land to drop."
        )
    frame = globe_mod.render_globe(
        character=character,
        center_lon=lon,
        hub_ids=tuple(
            hid for hid, row in globe_mod.HUBS.items() if row.get("glyph")
        ),
    )
    return (
        f"[GLOBE] Brass Earth disk -- lat {lat:.1f}, lon {lon:.1f}\r\n"
        f"{frame}"
    )


def enter_globe_tier(character, game):
    """Bind character to globe-flight tier at current macro anchor."""
    ensure_globe_defaults(character)
    room = get_globe_flight_room(game)
    character.move_to(room)
    return True


def leave_globe_tier(character, game):
    """Drop globe coords only; caller rebinds macro aerial room."""
    character.stellar_globe_lon = None
    character.stellar_globe_lat = None


def try_globe_fly_move(character, direction, game):
    """Move on the globe disk while on stellar globe tier.

    Returns True when handled (including blocked messages).
    """
    from engine.systems import aerial as aerial_mod

    tier = aerial_mod.flight_tier(character)
    if tier != "globe":
        return False
    delta = _DIR_DELTA.get((direction or "").strip().lower())
    if delta is None:
        session = getattr(character, "session", None)
        if session is not None:
            session.send("You can't bank that way.")
        return True
    ensure_globe_defaults(character)
    dlon, dlat = delta
    lon = globe_mod.norm_lon(float(character.stellar_globe_lon) + dlon)
    lat = float(character.stellar_globe_lat) + dlat
    lat = max(-60.0, min(72.0, lat))
    bypass = aerial_mod.gm_flight_bypass(character)
    if not bypass and character.solar_charge < GLOBE_CHARGE_PER_STEP:
        _send(
            character,
            f"Not enough Solar Charge to bank "
            f"(need {GLOBE_CHARGE_PER_STEP:.1f}).",
        )
        return True
    if not bypass:
        aerial_mod.add_solar_charge(character, -GLOBE_CHARGE_PER_STEP)
    character.stellar_globe_lon = lon
    character.stellar_globe_lat = lat
    old_room = character.location
    from command_support import _presence_face, is_staff_stealth_presence
    face = _presence_face(character)
    stealth = is_staff_stealth_presence(character)
    if old_room is not None and not stealth:
        old_room.broadcast(
            f"{face} banks {direction} across the sky.",
            exclude=character,
        )
    land_word = "over land" if globe_mod.land_at(lat, lon) else "over open ocean"
    _send(
        character,
        f"You plane {direction} {land_word} "
        f"(lat {lat:.1f}, lon {lon:.1f}).",
    )
    if not stealth and old_room is not None:
        old_room.broadcast(
            f"{face} streaks {direction} along the curve of the world.",
            exclude=character,
        )
    return True


def _send(character, msg):
    session = getattr(character, "session", None)
    if session is not None:
        session.send(msg)
