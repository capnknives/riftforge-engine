"""chargen.py -- basegame's character creation flow.

Mirrors root chargen.py's role for SUPERS, but far shorter: every basegame
resident starts Mundane (the four jobs below) unless they pick a registered
origin (Alien today -- see engine/systems/origin_registry.py). No Nature
fork, no appearance kit catalog, no Awakened Path tree -- just job +
optional origin + a small stat point-buy. Registered via
engine.hooks.set_chargen in basegame/bootstrap.py.register_all_hooks.

Flow for a NEW character only (reconnects skip this, same as SUPERS):
    path choice -> stat point-buy -> origin choice -> summary sheet
Returns False if the client disconnects mid-flow so connection.py can bail
without move_to / save / broadcast (no half-made Echo left behind) --
same contract as root chargen.run.
"""

from engine import stats as engine_stats
from engine.systems import origin_registry
from basegame import stats as stats_module

# Path id -> one-line description shown at chargen and in `help paths`.
# Order matters here (numbered menu + help page) -- PATH_ORDER is the
# single source of truth both chargen and help_topics.py walk.
PATHS = {
    "detective": "Detective -- work cases: investigate scenes, question "
                 "witnesses, close them out.",
    "medic": "Medic -- treat the injured and run the town clinic.",
    "laborer": "Laborer/Courier -- take shifts and deliveries for honest "
               "pay.",
    "ranger": "Ranger/Guard -- patrol the wilds and answer the town's "
              "wandering trouble.",
    "reporter": "Reporter -- snap photos, interview locals, file stories "
                "for the weekly paper.",
}
PATH_ORDER = ("detective", "medic", "laborer", "ranger", "reporter")


async def run(session, character):
    """Walk a brand-new character through path choice + stat point-buy."""
    session.send("")
    session.send("Welcome to Notbigville, Kansas.")
    session.send(
        "Every resident here starts as an ordinary human -- what sets you "
        "apart is the work you do."
    )

    path = await _prompt_path(session)
    if path is None:
        return False
    character.bg_path = path

    if not await _prompt_stats(session, character):
        return False
    character.hp = stats_module.max_hp(character)

    if not await _prompt_origin(session, character):
        return False

    character.hp_cap = stats_module.max_hp(character)
    character.hp = character.hp_cap

    session.send("")
    session.send(f"You are a {PATHS[path].split(' -- ')[0]}.")
    _send_sheet(session, character)
    session.send("Type 'help paths' any time for a refresher on what your path does.")
    session.send("Type 'help origins' for Mundane vs Alien Bloodlines.")
    return True


async def _prompt_path(session):
    """Numbered-menu path choice. Returns a PATH_ORDER id, or None on disconnect."""
    while True:
        session.send("")
        session.send("Choose your path:")
        for index, path_id in enumerate(PATH_ORDER, start=1):
            session.send(f"  {index}. {PATHS[path_id]}")
        session.send("Enter a number or a name:")
        raw = await session.read_line()
        if raw is None:
            return None
        choice = raw.strip().lower()
        if not choice:
            session.send("Please pick one of the options.")
            continue
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(PATH_ORDER):
                return PATH_ORDER[index]
            session.send(f"Number out of range (1-{len(PATH_ORDER)}).")
            continue
        if choice in PATH_ORDER:
            return choice
        session.send("Not a valid path -- try again.")


async def _prompt_stats(session, character):
    """Sequential point-buy over engine_stats.STAT_NAMES (the six shared
    primaries -- character.stats already holds the default 5.0 for each,
    set generically by engine/world.py's Character.__init__).

    The player spends BONUS_POOL points across the six, one prompt per
    stat, each capped by both the remaining pool and STAT_MAX. Returns
    False on disconnect.
    """
    session.send("")
    default_value = character.stats[engine_stats.STAT_NAMES[0]]
    session.send(
        f"Every stat starts at {default_value:g}. You have "
        f"{stats_module.BONUS_POOL} bonus points to spread across "
        f"{', '.join(engine_stats.STAT_NAMES)} (max {stats_module.STAT_MAX} each)."
    )
    remaining = stats_module.BONUS_POOL
    for name in engine_stats.STAT_NAMES:
        while True:
            room_left = stats_module.STAT_MAX - character.stats[name]
            cap = min(remaining, room_left)
            session.send(f"{name} bonus (0-{cap:g}, {remaining} left):")
            raw = await session.read_line()
            if raw is None:
                return False
            raw = raw.strip() or "0"
            if not raw.lstrip("-").isdigit():
                session.send("Enter a whole number.")
                continue
            amount = int(raw)
            if amount < 0 or amount > cap:
                session.send(f"Pick a number from 0 to {cap:g}.")
                continue
            character.stats[name] += amount
            remaining -= amount
            break
    return True


async def _prompt_origin(session, character):
    """Numbered menu: Mundane (default) + one line per registered origin.

    Mundane keeps today's four ``bg_path`` jobs with no origin-specific
    attach. Any registered origin (Alien today) stamps ``character.origin``,
    runs its ``on_attach``, then its async ``chargen_step`` (Stellar /
    Umbral path pick). Returns ``False`` on disconnect.
    """
    # Mundane is never registered -- it's always option 1, then sorted
    # registered ids so the menu is stable across reloads.
    ids = ["mundane"] + sorted(origin_registry.known_origins())
    while True:
        session.send("")
        session.send("Choose your origin:")
        for index, origin_id in enumerate(ids, start=1):
            if origin_id == "mundane":
                session.send(
                    "  1. Mundane -- ordinary human; your path is the work "
                    "you do."
                )
                continue
            entry = origin_registry.get_origin(origin_id) or {}
            name = entry.get("name") or origin_id
            summary = (entry.get("summary") or "").strip()
            if summary:
                # Keep the menu one line; full summary lives in help origins.
                short = summary.split(".")[0].strip()
                session.send(f"  {index}. {name} -- {short}.")
            else:
                session.send(f"  {index}. {name}")
        session.send("Enter a number or a name:")
        raw = await session.read_line()
        if raw is None:
            return False
        choice = raw.strip().lower()
        if not choice:
            session.send("Please pick one of the options.")
            continue
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(ids):
                origin_id = ids[index]
            else:
                session.send(f"Number out of range (1-{len(ids)}).")
                continue
        elif choice in ids:
            origin_id = choice
        else:
            session.send("Not a valid origin -- try again.")
            continue

        if origin_id == "mundane":
            character.origin = "mundane"
            return True

        character.origin = origin_id
        entry = origin_registry.get_origin(origin_id)
        if entry is None:
            # Registry emptied mid-prompt (hot-reload edge) -- fall back.
            character.origin = "mundane"
            return True
        on_attach = entry.get("on_attach")
        if on_attach is not None:
            on_attach(character)
        chargen_step = entry.get("chargen_step")
        if chargen_step is not None:
            return await chargen_step(session, character)
        return True


def _send_sheet(session, character):
    """Post-chargen summary: every stat, Tier, plus HP."""
    session.send("Your stats:")
    for name in engine_stats.STAT_NAMES:
        session.send(f"  {name}: {character.stats[name]:g}")
    session.send(f"Tier: {character.tier}")
    session.send(f"HP: {character.hp}/{stats_module.max_hp(character)}")
