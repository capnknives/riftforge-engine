"""game_select.py -- resolves which game package (if any) runs on the engine.

RiftForge is the engine; SUPERS and basegame are two mutually exclusive
game packages that can sit on top of it. Both register hooks at import
time (see supers/__init__.py, basegame/__init__.py), so importing both in
one process would let the second one silently clobber the first's hooks.
This module is the single choke point every other root module (server.py,
commands.py) goes through to pick a game -- nothing else should import
`supers` or `basegame` directly at module scope.

``RIFTFORGE_GAME`` environment variable selects the active game:

    supers    -- the production game. Must be importable or this raises.
    basegame  -- the reference demo game. Must be importable or this raises.
    none      -- lean engine only, no game package (even if supers/basegame
                 are both installed).
    unset     -- auto: supers if importable, else lean engine. This is
                 today's live behavior, unchanged by basegame's existence --
                 basegame is never auto-selected; it must be requested
                 explicitly. (Auto-falling into basegame the moment supers
                 fails to import would be surprising, and would break the
                 existing two-repo-purity engine-only-smoke gate, which
                 renames only supers/ aside and expects a truly lean
                 engine, not a silent basegame pickup.)

Resolution happens once per process and is cached; call `_reset_for_tests()`
to clear the cache (smoke / basegame_smoke rebuild Game wiring per run).
"""

import os

_GAME_NAME = None  # "supers" | "basegame" | "none", once resolved


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
    if choice not in ("supers", "basegame", "none", "auto"):
        raise ValueError(
            f"RIFTFORGE_GAME={choice!r} must be one of: supers, basegame, none"
        )

    if choice == "supers":
        import supers  # noqa: F401 -- import triggers core hook registration
        _GAME_NAME = "supers"
    elif choice == "basegame":
        import basegame  # noqa: F401 -- import triggers core hook registration
        _GAME_NAME = "basegame"
    elif choice == "none":
        _GAME_NAME = "none"
    else:  # auto
        try:
            import supers  # noqa: F401
            _GAME_NAME = "supers"
        except ImportError:
            _GAME_NAME = "none"

    return _GAME_NAME


def game_name():
    """"supers", "basegame", or "none" -- the resolved active game."""
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


def register_default_ticks(game):
    """Wire the active game's tick handlers onto `game`. No-op for "none"."""
    name = _resolve()
    if name == "supers":
        from supers.tick_bootstrap import register_default_ticks as fn
        fn(game)
    elif name == "basegame":
        from basegame.tick_bootstrap import register_default_ticks as fn
        fn(game)


def seed_content(game):
    """Idempotent game-package world backfill, called once at Game boot.

    SUPERS: ``supers.boot_seed.seed_content`` (Cadence, immersion, heals,
    ``register_default_ticks``). Basegame: ``basegame.seed.seed_content``
    plus tick registration. Lean engine (``none``): no-op.
    """
    name = _resolve()
    if name == "supers":
        from supers.boot_seed import seed_content as fn
        fn(game)
    elif name == "basegame":
        from basegame.seed import seed_content as fn
        fn(game)
        register_default_ticks(game)
