"""
bestiary.py -- classic hostile catalog (schema-validated at boot).

Uses the generic spawn registry builder; classic combat fields (hp, ac,
attack_bonus, damage_die) ride on each template for future spawn wiring.
"""

import os

from classic.content import load_bestiary_catalog
from engine.systems import spawn as spawn_engine

_BESTIARY_DIR = os.path.join(os.path.dirname(__file__), "content", "bestiary")


def _registry():
    return load_bestiary_catalog()["registry"]


def get_pool(categories, tier, *, room=None):
    del room
    return spawn_engine.get_pool(_registry(), categories, tier)


def roll_stats(creature):
    return spawn_engine.roll_stats(creature)


def find_creature(identifier):
    return spawn_engine.find_creature(_registry(), identifier)
