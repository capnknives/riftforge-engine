"""game_select.py -- resolves which game package (if any) runs on the engine.

RiftForge is the engine; SUPERS, basegame, and classic are mutually
exclusive game packages. Each registers hooks at import time (see
supers/__init__.py, basegame/__init__.py, classic/__init__.py), so
importing more than one in a process would clobber hooks. This module is
the single choke point (server.py, commands.py) -- nothing else should
import game packages directly at module scope.

``RIFTFORGE_GAME`` environment variable selects the active game:

    supers    -- the production game. Must be importable or this raises.
    basegame  -- the reference demo game. Must be importable or this raises.
    classic   -- OSR fantasy MVP (Millbrook + wilds). Must be importable.
    none      -- lean engine only, no game package.
    unset     -- auto: supers if importable, else lean engine. basegame and
                 classic are never auto-selected.

Resolution happens once per process and is cached; call `_reset_for_tests()`
to clear the cache (smoke tests rebuild Game wiring per run).
"""

import os

_GAME_NAME = None  # "supers" | "basegame" | "classic" | "none", once resolved


def _reset_for_tests():
    """Clear the cached resolution (tests that flip RIFTFORGE_GAME mid-run)."""
    global _GAME_NAME
    _GAME_NAME = None


def _resolve():
    """Pick the active game exactly once and cache the choice."""
    global _GAME_NAME
    if _GAME_NAME is not None:
        return _GAME_NAME

    choice = (os.environ.get("RIFTFORGE_GAME") or "auto").strip().lower()
    if choice not in ("supers", "basegame", "classic", "none", "auto"):
        raise ValueError(
            f"RIFTFORGE_GAME={choice!r} must be one of: "
            "supers, basegame, classic, none"
        )

    if choice == "supers":
        import supers  # noqa: F401 -- import triggers core hook registration
        _GAME_NAME = "supers"
    elif choice == "basegame":
        import basegame  # noqa: F401 -- import triggers core hook registration
        _GAME_NAME = "basegame"
    elif choice == "classic":
        import classic  # noqa: F401 -- import triggers core hook registration
        _GAME_NAME = "classic"
    elif choice == "none":
        _GAME_NAME = "none"
        from engine.lean_boot import configure_lean_maps
        configure_lean_maps()
    else:  # auto
        try:
            import supers  # noqa: F401
            _GAME_NAME = "supers"
        except ImportError:
            _GAME_NAME = "none"
            from engine.lean_boot import configure_lean_maps
            configure_lean_maps()

    return _GAME_NAME


def game_name():
    """"supers", "basegame", "classic", or "none" -- resolved active game."""
    return _resolve()


def game_commands():
    """The active game's COMMANDS dict, or {} for a lean engine boot."""
    name = _resolve()
    if name == "supers":
        from supers.verbs import SUPERS_COMMANDS
        return SUPERS_COMMANDS
    if name == "basegame":
        from basegame.verbs import BASEGAME_COMMANDS
        return BASEGAME_COMMANDS
    if name == "classic":
        from classic.verbs import CLASSIC_COMMANDS
        return CLASSIC_COMMANDS
    return {}


def register_all_hooks():
    """Register the active game's full hook set (movement, look, combat, ...).

    No-op for "none" -- a lean engine runs on hook no-op defaults alone.
    """
    name = _resolve()
    if name == "supers":
        from supers.bootstrap import register_all_hooks as fn
        fn()
    elif name == "basegame":
        from basegame.bootstrap import register_all_hooks as fn
        fn()
    elif name == "classic":
        from classic.bootstrap import register_all_hooks as fn
        fn()


def register_default_ticks(game):
    """Wire the active game's tick handlers onto `game`. No-op for "none"."""
    name = _resolve()
    if name == "supers":
        from supers.tick_bootstrap import register_default_ticks as fn
        fn(game)
    elif name == "basegame":
        from basegame.tick_bootstrap import register_default_ticks as fn
        fn(game)
    elif name == "classic":
        from classic.tick_bootstrap import register_default_ticks as fn
        fn(game)


def seed_content(game):
    """Idempotent game-package world backfill, called once at Game boot."""
    name = _resolve()
    if name == "supers":
        from supers.boot_seed import seed_content as fn
        fn(game)
    elif name == "basegame":
        from basegame.seed import seed_content as fn
        fn(game)
        register_default_ticks(game)
    elif name == "classic":
        from classic.seed import seed_content as fn
        fn(game)
