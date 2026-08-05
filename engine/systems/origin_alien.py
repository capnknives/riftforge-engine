"""
origin_alien.py -- the "alien" demo origin (Stellar + Umbral paths).

Self-registers on import, the same way ``combat_martial_arts.py``
self-registers ``"martial_arts"``. A game turns Alien on by importing
this module (basegame does that from ``bootstrap.register_all_hooks``);
SUPERS never imports it, so ``"alien"`` never appears in
``origin_registry.known_origins()`` in a SUPERS process.

Stellar reuses the existing ``engine/systems/aerial.py`` flight tiers
(``bg_stellar`` gate) -- this module only decides *how that flag gets
set* (chargen path pick instead of the old yes/no prompt). Umbral is
net-new and lives in ``engine/systems/umbral.py``.

Summary text is fresh engine copy inspired by SUPERS' Alien catalog
entry -- this file never imports ``supers/`` (two-repo purity).
"""

from __future__ import annotations

from engine.systems import origin_registry

# Path id -> one-line menu blurb. Order is the numbered menu order.
ALIEN_PATHS = {
    "stellar": "Stellar (yellow-sun flight)",
    "umbral": "Umbral (night shroud)",
}
ALIEN_PATH_ORDER = ("stellar", "umbral")


def on_attach(character):
    """Stamp Alien field defaults when chargen picks this origin.

    ``alien_path`` stays ``None`` until the player finishes the path
    menu below -- a mid-prompt disconnect never leaves a half-set path.
    """
    character.alien_path = None


async def chargen_step(session, character):
    """Numbered menu: Stellar flight vs Umbral shroud.

    Returns ``False`` on disconnect (same contract as the
    ``_prompt_stellar`` function this replaces in ``basegame/chargen.py``).
    """
    while True:
        session.send("")
        session.send("Choose your Alien Bloodline:")
        for index, path_id in enumerate(ALIEN_PATH_ORDER, start=1):
            session.send(f"  {index}. {ALIEN_PATHS[path_id]}")
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
            if 0 <= index < len(ALIEN_PATH_ORDER):
                path_id = ALIEN_PATH_ORDER[index]
            else:
                session.send(
                    f"Number out of range (1-{len(ALIEN_PATH_ORDER)})."
                )
                continue
        elif choice in ALIEN_PATH_ORDER:
            path_id = choice
        else:
            session.send("Not a valid Bloodline -- try again.")
            continue

        character.alien_path = path_id
        if path_id == "stellar":
            from engine.systems import aerial as aerial_mod

            aerial_mod.ensure_stellar_defaults(character)
            character.bg_stellar = True
            session.send(
                "Yellow sun answers you. Type fly outdoors to climb the sky."
            )
            return True
        # umbral
        from engine.systems import umbral as umbral_mod

        umbral_mod.ensure_umbral_defaults(character)
        character.bg_umbral = True
        session.send(
            "Night answers you. Type shroud at dusk or night to fade from sight."
        )
        return True


# Self-registers on import -- basegame/bootstrap.py imports this module
# purely for the side effect; SUPERS must never add that import.
origin_registry.register_origin(
    "alien",
    name="Alien",
    summary=(
        "Extraterrestrial Bloodline -- Stellar (yellow-sun flight) or "
        "Umbral (night shroud). Engine demo flavor, not SUPERS canon."
    ),
    chargen_step=chargen_step,
    on_attach=on_attach,
)
