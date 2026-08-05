"""
loadcombat.py -- switch the game's default combat backend.

  loadcombat list              backends + current default
  loadcombat swing             load + set default to round-based swing
  loadcombat active_combat     load + set default to twitch combat
"""

from __future__ import annotations

from engine.systems import combat_runtime as cr


def _send(character, text):
    session = getattr(character, "session", None)
    if session is not None:
        session.send(text)


def cmd_loadcombat(character, args, game):
    """Load a combat backend and set it as the game default."""
    from basegame import combat_backends as combat_backends_mod

    # Registration is idempotent; safe if bootstrap already ran.
    combat_backends_mod.register_backends()

    raw = (args or "").strip().lower()
    if not raw or raw == "list":
        current = cr.get_game_combat_backend(game)
        lines = cr.describe_backends()
        _send(
            character,
            "Combat backends (game default for new fights):\n"
            f"  current: {current}\n"
            + "\n".join(lines)
            + "\n\nUsage: loadcombat swing | loadcombat active_combat",
        )
        return

    backend = raw.split()[0]
    if backend in ("swing", "round", "narrative"):
        backend = cr.BACKEND_SWING
    elif backend in ("active", "twitch", "active-combat"):
        backend = cr.BACKEND_ACTIVE

    ok, err = cr.load_combat_backend(backend)
    if not ok:
        _send(character, err)
        return

    cr.set_game_combat_backend(game, backend)
    _send(
        character,
        f"[COMBAT] Loaded {backend} and set as game default for new fights.",
    )
