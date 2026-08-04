"""
spawn -- generic creature-catalog loading + named-AI dispatch for live
world spawns (wilderness bestiary tables, nest/den top-up ticks, ...).

Peeled from ``supers/bestiary.py`` and ``supers/spawn_nests.py`` under
docs/plans/riftforge_core_expansion.md Phase 6. Nothing here knows what a
creature "is" or what an AI id means -- games declare their own domain
vocabulary (body types, blood economy tags, tier ceiling, which AI ids
exist and how to build one) and this package only owns the load/merge/
validate/lookup/dispatch mechanism.
"""

from __future__ import annotations

from engine.systems.spawn.bestiary import (
    build_registry,
    find_creature,
    get_pool,
    load_catalog_files,
    roll_stats,
)
from engine.systems.spawn.nest_ai import (
    known_nest_ai,
    make_nest_hostile,
    register_nest_ai,
)

__all__ = [
    "build_registry",
    "find_creature",
    "get_pool",
    "known_nest_ai",
    "load_catalog_files",
    "make_nest_hostile",
    "register_nest_ai",
    "roll_stats",
]
