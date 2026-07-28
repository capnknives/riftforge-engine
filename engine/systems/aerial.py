"""aerial.py -- simplified Stellar flight tiers for the public engine demo.

Full Solar Arts live in supers/solar.py; this module covers ground → macro
→ globe → orbit for basegame's optional Stellar chargen path.
"""

from __future__ import annotations

ORBIT_ROOM_KEY = "LM00002"
ORBIT_ZONE = "stellar-orbit"
GLOBE_CHARGE_STEP = 0.05


def ensure_stellar_defaults(character):
    """Attach flight fields if missing."""
    if not hasattr(character, "bg_stellar"):
        character.bg_stellar = False
    if not hasattr(character, "solar_charge"):
        character.solar_charge = 1.0
    if not hasattr(character, "stellar_flight_tier"):
        character.stellar_flight_tier = "ground"
    if not hasattr(character, "is_flying"):
        character.is_flying = False
    if not hasattr(character, "stellar_hovering"):
        character.stellar_hovering = False
    if not hasattr(character, "stellar_flight_macro"):
        character.stellar_flight_macro = None
    if not hasattr(character, "stellar_globe_lon"):
        character.stellar_globe_lon = None
    if not hasattr(character, "stellar_globe_lat"):
        character.stellar_globe_lat = None
    if not hasattr(character, "orbit_return_room"):
        character.orbit_return_room = None


def is_stellar(character):
    """True when this character took the Stellar demo path."""
    ensure_stellar_defaults(character)
    return bool(getattr(character, "bg_stellar", False))


def flight_tier(character):
    """Return ground | macro | globe | orbit."""
    ensure_stellar_defaults(character)
    tier = getattr(character, "stellar_flight_tier", "ground") or "ground"
    if tier not in ("ground", "macro", "globe", "orbit"):
        return "ground"
    room = getattr(character, "location", None)
    if tier == "ground" and room is not None and getattr(room, "zone", None) == ORBIT_ZONE:
        return "orbit"
    return tier


def set_flight_tier(character, tier):
    """Set altitude tier and sync legacy flags."""
    ensure_stellar_defaults(character)
    if tier not in ("ground", "macro", "globe", "orbit"):
        tier = "ground"
    character.stellar_flight_tier = tier
    character.is_flying = tier != "ground"
    character.stellar_hovering = tier in ("macro", "globe")


def clear_hover(character):
    """Drop sustained flight without messaging."""
    ensure_stellar_defaults(character)
    character.stellar_hovering = False
    character.is_flying = False
    character.stellar_flight_tier = "ground"
    character.stellar_flight_macro = None
    character.stellar_globe_lon = None
    character.stellar_globe_lat = None


def add_solar_charge(character, delta):
    """Adjust demo Solar Charge (0..1)."""
    ensure_stellar_defaults(character)
    character.solar_charge = max(0.0, min(1.0, float(character.solar_charge) + float(delta)))


def gm_flight_bypass(character):
    """Staff bypass for flight costs — demo build has no GM flight."""
    return False


def cmd_fly(character, args, game):
    """Climb flight tiers: ground → macro → globe → orbit."""
    from engine.systems import globe_flight as globe_flight_mod
    from engine.systems import overland as overland_mod

    ensure_stellar_defaults(character)
    if not is_stellar(character):
        character.session.send("Only Stellar characters can fly in this demo.")
        return
    tier = flight_tier(character)
    if tier == "orbit":
        character.session.send("You are already in orbit. Type descend.")
        return
    if tier == "globe":
        set_flight_tier(character, "orbit")
        room = game.rooms.get(ORBIT_ROOM_KEY)
        if room is None:
            character.session.send("Orbit platform is not loaded.")
            return
        character.orbit_return_room = getattr(character, "location", None)
        character.move_to(room)
        character.session.send(
            "You burst into low orbit — yellow sun hammers down. Type descend."
        )
        return
    if tier == "macro":
        macro = overland_mod._parse_pos_pair(character.stellar_flight_macro)
        if macro is None:
            macro = (35, 10)
        lat, lon = globe_flight_mod.macro_to_lonlat(macro[0], macro[1])
        character.stellar_globe_lat = lat
        character.stellar_globe_lon = lon
        set_flight_tier(character, "globe")
        globe_flight_mod.enter_globe_tier(character, game)
        character.session.send(
            "You rise to the brass globe — the world spreads below. "
            "Type n/s/e/w to bank, fly again for orbit, descend to land."
        )
        return
    room = getattr(character, "location", None)
    macro = overland_mod.america_macro_from_room(room, game) or (35, 10)
    if not getattr(room, "outdoor", False):
        character.session.send("You need open sky to take off.")
        return
    character.stellar_flight_macro = list(macro)
    overland_mod.place_aerial_overland(character, game, macro)
    set_flight_tier(character, "macro")
    character.session.send(
        f"You lift into the sky above overland ({macro[0]}, {macro[1]}). "
        "Fly again to reach the globe layer."
    )


def cmd_descend(character, args, game):
    """Step down one flight tier or land."""
    from engine.systems import globe_flight as globe_flight_mod
    from engine.systems import overland as overland_mod

    ensure_stellar_defaults(character)
    if not is_stellar(character):
        character.session.send("You are not airborne.")
        return
    tier = flight_tier(character)
    if tier == "orbit":
        back = getattr(character, "orbit_return_room", None)
        set_flight_tier(character, "globe")
        if back is not None:
            character.move_to(back)
        else:
            globe_flight_mod.enter_globe_tier(character, game)
        character.session.send("You drop from orbit toward the globe layer.")
        return
    if tier == "globe":
        globe_flight_mod.leave_globe_tier(character, game)
        macro = overland_mod._parse_pos_pair(character.stellar_flight_macro) or (35, 10)
        overland_mod.place_aerial_overland(character, game, macro)
        set_flight_tier(character, "macro")
        character.session.send("You sink back to macro altitude.")
        return
    if tier == "macro":
        macro = overland_mod._parse_pos_pair(character.stellar_flight_macro) or (35, 10)
        overland_mod.place_on_overland(character, game, macro, overland_mod.LANDMARK_MICRO)
        clear_hover()
        character.session.send("You settle back to the ground.")
        return
    character.session.send("You are already on the ground.")
