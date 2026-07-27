"""chargen.py -- basegame's character creation flow.

Mirrors root chargen.py's role for SUPERS, but far shorter: every basegame
resident is an ordinary human, so there is no Nature fork, no appearance
kit catalog, no Awakened Path tree -- just "which of the four jobs do you
do" and a small stat point-buy. Registered via engine.hooks.set_chargen in
basegame/bootstrap.py.register_all_hooks.

Flow for a NEW character only (reconnects skip this, same as SUPERS):
    path choice -> stat point-buy -> summary sheet
Returns False if the client disconnects mid-flow so connection.py can bail
without move_to / save / broadcast (no half-made Echo left behind) --
same contract as root chargen.run.
"""

from engine import stats as engine_stats
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
}
PATH_ORDER = ("detective", "medic", "laborer", "ranger")


async def run(session, character):
    """Walk a brand-new character through path choice + stat point-buy."""
    session.send("")
    session.send("Welcome to the RiftForge reference town.")
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

    session.send("")
    session.send(f"You are a {PATHS[path].split(' -- ')[0]}.")
    _send_sheet(session, character)
    session.send("Type 'help paths' any time for a refresher on what your path does.")
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


def _send_sheet(session, character):
    """Post-chargen summary: every stat, Tier, plus HP."""
    session.send("Your stats:")
    for name in engine_stats.STAT_NAMES:
        session.send(f"  {name}: {character.stats[name]:g}")
    session.send(f"Tier: {character.tier}")
    session.send(f"HP: {character.hp}/{stats_module.max_hp(character)}")
