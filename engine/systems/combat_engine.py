"""
combat_engine.py -- the generic pluggable combat-engine registry.

`docs/plans/riftforge_core_expansion.md` (Phase 5) declined to build this: at
the time, SUPERS was the engine's only combat consumer, so a registry would
have been speculative abstraction with no real second implementation to check
its shape against. That call is superseded here by explicit maintainer
direction (`docs/plans/riftforge_engine_game_shell.md`): the engine itself
should ship as a fully functional generic game, with several hookable combat
*styles* (not just one "mundane" default) as real, functional content -- not
merely a bare mechanism waiting for a game to fill in.

A "combat engine" here is just an id plus three functions a game (or the
engine's own shipped defaults -- see `combat_mundane.py`,
`combat_martial_arts.py`, and `combat_osr.py`) registers under that id:

  * ``build_brief(attacker, defender, game=None, *, rng=None, **ctx) -> brief``
    Pure computation, no mutation -- decide what *would* happen (who got hit,
    how hard, any state the engine wants to carry forward) and return it as a
    plain dict. Different engines can put whatever they want in a brief --
    this module never inspects the brief's contents.
  * ``apply_brief(brief, game=None) -> result``
    Mutate world state (HP, persisted combo counters, whatever the engine
    needs) from a *frozen* brief, and return a small result dict describing
    what actually happened.
  * ``narrate(brief, result) -> str | None`` (optional)
    Render prose from the brief + result. Hard rule 5 (`AGENTS.md`): combat is
    always resolved into data first, with any prose rendered as a strictly
    separate step reading that data -- never merged into the math. Making
    ``narrate`` its own optional hook (rather than folding text into
    ``apply_brief``) keeps that separation even for the simplest engines.

This module owns only the id -> engine lookup, the same shape as
`engine/systems/spawn/nest_ai.py`'s `register_nest_ai` /
`known_nest_ai` / `make_nest_hostile` -- it has no opinion on what a brief
or result look like, only that build happens before apply, and apply happens
before (optional) narration.
"""

from __future__ import annotations

# id -> {"build_brief": fn, "apply_brief": fn, "narrate": fn | None}
_ENGINES: dict[str, dict] = {}


def register_combat_engine(engine_id, *, build_brief, apply_brief, narrate=None):
    """Register a pluggable combat engine under ``engine_id``.

    Idempotent -- re-registering the same id overwrites its entry (same
    convention as ``register_nest_ai``), so re-importing a module during a
    test run or a hot-reload never raises or duplicates state.
    """
    _ENGINES[str(engine_id)] = {
        "build_brief": build_brief,
        "apply_brief": apply_brief,
        "narrate": narrate,
    }


def known_combat_engines():
    """Frozen set of every registered combat engine id."""
    return frozenset(_ENGINES)


def get_combat_engine(engine_id):
    """Return the raw registration dict (``build_brief``/``apply_brief``/
    ``narrate`` keys) for ``engine_id``, or ``None`` if nothing is
    registered under that id.
    """
    return _ENGINES.get(str(engine_id))


def resolve_swing(engine_id, attacker, defender, game=None, *, rng=None, **ctx):
    """Run one full build -> apply -> narrate pass through ``engine_id``.

    Returns ``{"brief": ..., "result": ..., "text": ...}`` (``text`` is
    ``None`` when the engine did not register a ``narrate`` function).
    Returns ``None`` when ``engine_id`` has no registered engine -- same
    soft-fail convention as ``nest_ai.make_nest_hostile``: callers that need
    "unknown engine" to be a hard error should check ``known_combat_engines()``
    themselves (e.g. at chargen / content-load time), and callers that would
    rather silently fall back to a default engine (see
    ``basegame/combat.py``) can do that with the ``None`` return instead of
    this function guessing a fallback on their behalf.
    """
    engine = get_combat_engine(engine_id)
    if engine is None:
        return None
    brief = engine["build_brief"](attacker, defender, game, rng=rng, **ctx)
    result = engine["apply_brief"](brief, game)
    narrate_fn = engine.get("narrate")
    text = narrate_fn(brief, result) if narrate_fn is not None else None
    return {"brief": brief, "result": result, "text": text}
