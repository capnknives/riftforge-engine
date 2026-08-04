"""
gates -- generic rotating gate / rift mouth network primitives.

A "gate network" is a named, round-robin open-set of room keys on a live
Game (config in ``network.py``), shared open/rotate math (``rotation.py``),
and look/move visibility hide for the closed mouths (``visibility.py``).
Nothing here knows what a gate leads to, what flag marks a room as a gate,
or what a game calls its network (Devil's Gate, a purgatory rift, an
elemental rift nexus, ...) -- callers supply a ``GateNetwork`` config and an
ordered room list; this package only owns the open-set bookkeeping.

Peeled from ``supers/gates/{network,rotation,visibility}.py`` under
``docs/plans/riftforge_core_expansion.md`` Phase 1 -- that package's
``devils.py``/``purgatory_rift.py`` stay in ``supers/`` (Devil's Gate /
purgatory lore, authored-room flags, tuned constants) and now import these
modules instead of defining them. ``supers/gates/network.py`` etc. are thin
facades over this package so every existing SUPERS call site keeps working.

Proven by two independent callers: SUPERS' Devil's Gate network (round-robin
rotation via ``rotation.tick_rotation``/``ensure_initialized``) and
basegame's elemental gate-nexus demo. A network with different needs --
weighted/probabilistic room selection, a "some ticks nothing opens" roll,
direct single-room special-casing -- is NOT what ``tick_rotation`` models;
SUPERS' purgatory rift has exactly that shape and deliberately does not use
``tick_rotation``/``ensure_initialized`` (it only reuses the ``GateNetwork``
config shape and ``rotation.open_keys``), and games with similar needs
should do the same rather than bend this driver to fit.
"""

from __future__ import annotations

from engine.systems.gates.network import GateNetwork
from engine.systems.gates.rotation import (
    ensure_initialized,
    is_open,
    open_keys,
    tick_rotation,
)
from engine.systems.gates.visibility import exit_visible_for_flag, visible_exits

__all__ = [
    "GateNetwork",
    "ensure_initialized",
    "exit_visible_for_flag",
    "is_open",
    "open_keys",
    "tick_rotation",
    "visible_exits",
]
