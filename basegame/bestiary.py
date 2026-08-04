"""
bestiary.py -- basegame prairie critter catalog for the spawn peel demo.

Load/merge/validate lives in ``engine/systems/spawn/bestiary.py``; this
module only supplies basegame's tier ceiling (always 0) and an empty
``field_vocab`` -- no body_type / blood_type vocabulary in the reference game.
"""

import os

from engine.stats import STAT_NAMES
from engine.systems import spawn as spawn_engine

_BESTIARY_DIR = os.path.join(os.path.dirname(__file__), "content", "bestiary")
MAX_TIER = 0


def _build_registry():
    """Merge every ``content/bestiary/*.json`` file into one registry dict."""
    files = spawn_engine.load_catalog_files(_BESTIARY_DIR)
    return spawn_engine.build_registry(
        files,
        max_tier=MAX_TIER,
        stat_names=STAT_NAMES,
        field_vocab={},
    )


_REGISTRY = _build_registry()


def get_pool(categories, tier, *, room=None):
    """Templates matching any ``categories`` at exactly ``tier``."""
    return spawn_engine.get_pool(_REGISTRY, categories, tier)


def roll_stats(creature):
    """Roll primaries for one template."""
    return spawn_engine.roll_stats(creature)


def find_creature(identifier):
    """Case-insensitive id/name lookup across the whole registry."""
    return spawn_engine.find_creature(_REGISTRY, identifier)
