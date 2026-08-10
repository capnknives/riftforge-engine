"""basegame/anatomy_demo.py -- prove engine body_parts without SUPERS combat."""

from __future__ import annotations

from engine.systems import body_parts as body_parts_engine


def region_strike(attacker, defender, region, amount, *, rng=None):
    """Deal structural damage to ``defender``'s ``region``.

    Returns the frozen plan dict from ``plan_region_damage`` so smokes can
    assert tier transitions. Not a player verb -- smoke / dev helper only.
    """
    del attacker, rng
    plan = body_parts_engine.plan_region_damage(defender, region, amount)
    body_parts_engine.apply_region_damage(defender, plan)
    return plan
