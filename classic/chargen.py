"""chargen.py -- class pick + ability point-buy for classic characters."""

from classic import classes as classes_module
from classic import stats as stats_module
from classic.character_attach import apply_class_kit
from engine.style import format_sheet


async def run(session, character):
    """Walk a new character through class + ability assignment."""
    session.send("")
    session.send("Welcome to Millbrook and the wild country beyond.")
    session.send(
        "Choose a class, assign your abilities, and step into the frontier."
    )

    class_id = await _prompt_class(session)
    if class_id is None:
        return False
    apply_class_kit(character, class_id)

    if not await _prompt_abilities(session, character):
        return False
    stats_module.sync_engine_stats(character)
    character.hp = stats_module.max_hp(character)
    character.hp_cap = stats_module.max_hp(character)

    session.send("")
    session.send(
        f"You are a {classes_module.CLASS_NAMES[class_id]} "
        f"ready to explore."
    )
    _send_sheet(session, character)
    session.send("Type 'help classic' for how to play.")
    session.send("Type 'help classes' for class details.")
    return True


async def _prompt_class(session):
    """Numbered class menu."""
    while True:
        session.send("")
        session.send("Choose your class:")
        for index, class_id in enumerate(classes_module.CLASS_ORDER, start=1):
            session.send(f"  {index}. {classes_module.CLASS_BLURBS[class_id]}")
        session.send("Enter a number or a name:")
        raw = await session.read_line()
        if raw is None:
            return None
        choice = raw.strip().lower()
        if not choice:
            session.send("Please pick one of the options.")
            continue
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(classes_module.CLASS_ORDER):
                return classes_module.CLASS_ORDER[idx]
            session.send(
                f"Number out of range (1-{len(classes_module.CLASS_ORDER)})."
            )
            continue
        if choice in classes_module.CLASS_ORDER:
            return choice
        session.send("Not a valid class -- try again.")


async def _prompt_abilities(session, character):
    """Point-buy across STR/DEX/CON/INT/WIS/CHA."""
    session.send("")
    session.send(
        f"Every ability starts at {stats_module.ABILITY_DEFAULT}. "
        f"You have {stats_module.BONUS_POOL} bonus points "
        f"(max {stats_module.ABILITY_MAX} each)."
    )
    remaining = stats_module.BONUS_POOL
    for name in stats_module.ABILITY_NAMES:
        while True:
            current = character.classic_abilities[name]
            room_left = stats_module.ABILITY_MAX - current
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
            character.classic_abilities[name] = current + amount
            remaining -= amount
            break
    return True


def _send_sheet(session, character):
    """Post-chargen summary using the shared sheet formatter."""
    from classic.verbs.character import build_sheet_lines

    screenreader = bool(getattr(character, "screenreader", False))
    for line in format_sheet("Character", build_sheet_lines(character), screenreader=screenreader):
        session.send(line)
