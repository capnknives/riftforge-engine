"""
origin_registry.py -- the generic pluggable origin/archetype registry.

Same id -> registration-dict shape as ``combat_engine.py`` (Phase 1 of
``docs/plans/riftforge_engine_game_shell.md``): a game turns an origin
"on" by importing the module that self-registers it, and turns it "off"
by never importing it. No enable flag, env var, or JSON catalog -- the
import *is* the on/off switch.

An "origin" here is just an id plus optional chargen / attach hooks a
game (or the engine's own shipped demo -- see ``origin_alien.py``)
registers under that id:

  * ``chargen_step(session, character) -> bool`` (async, optional)
    Walk the player through any origin-specific prompts after they pick
    this origin. Returns ``False`` on disconnect so chargen can bail
    without leaving a half-made character in the world -- same contract
    as ``basegame.chargen.run``.
  * ``on_attach(character) -> None`` (optional)
    Stamp origin-specific field defaults. Called by the *chargen* path
    when the player picks this origin -- **not** by
    ``character_attach.py``. Mundane / unregistered characters never
    run origin-specific attach code.

This module owns only the id -> origin lookup. It has no opinion on what
an origin's fields look like, and it never cross-imports
``combat_engine`` (the two registries stay independent -- an origin's
``on_attach`` may set ``character.combat_engine`` as a flavor default,
but combat engines never branch on ``character.origin``).

``"mundane"`` is never registered. It is the zero-config default every
bare engine Character already has (``engine/world.py`` sets
``character.origin = "mundane"``), and basegame's four ``bg_path`` jobs
are unchanged. The chargen menu always offers "Mundane" as option 1
plus one line per ``known_origins()``.
"""

from __future__ import annotations

# id -> {"name": str, "summary": str, "chargen_step": fn|None, "on_attach": fn|None}
_ORIGINS: dict[str, dict] = {}


def register_origin(origin_id, *, name, summary="", chargen_step=None, on_attach=None):
    """Register a pluggable origin under ``origin_id``.

    Idempotent -- re-registering the same id overwrites its entry (same
    convention as ``register_combat_engine`` / ``register_nest_ai``), so
    re-importing a module during a test run or a hot-reload never raises
    or duplicates state.

    ``chargen_step`` is an async ``fn(session, character) -> bool``
    (``False`` = client disconnected mid-prompt). ``on_attach`` is a
    sync ``fn(character) -> None`` called by the origin's own chargen
    path when the player picks it -- not by ``character_attach.py``.
    """
    _ORIGINS[str(origin_id)] = {
        "name": name,
        "summary": summary,
        "chargen_step": chargen_step,
        "on_attach": on_attach,
    }


def known_origins():
    """Frozen set of every registered origin id."""
    return frozenset(_ORIGINS)


def get_origin(origin_id):
    """Return the raw registration dict for ``origin_id``, or ``None``.

    Dict keys are ``name`` / ``summary`` / ``chargen_step`` /
    ``on_attach``. Callers that need "unknown origin" to be a hard error
    should check ``known_origins()`` themselves (e.g. at chargen time).
    """
    return _ORIGINS.get(str(origin_id))
