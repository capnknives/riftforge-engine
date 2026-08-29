"""
predicates.py -- small, stateless "is this true right now" predicate
evaluator, shared by anything that needs a yes/no gate outside quest event
matching: room.trigger conditions today, dialogue.tree option ``requires``
tomorrow. Deliberately NOT the same function as
``engine.systems.quests._match_when``, which matches predicates against a
specific incoming event + active quest context.
"""

from __future__ import annotations


def check(predicate: dict, character, game, *, trigger_id=None) -> bool:
    """Return whether ``predicate`` is satisfied for ``character`` right now."""
    if not predicate or not isinstance(predicate, dict):
        return True
    kind = predicate.get("type")
    if kind == "flag":
        return _flag_value(character, predicate, trigger_id) is True
    if kind == "not_flag":
        return _flag_value(character, predicate, trigger_id) is not True
    if kind == "has_item":
        from engine.systems.quests import _inventory_has

        return _inventory_has(character, predicate.get("item"))
    if kind == "true_form":
        return bool(getattr(character, "true_form", False))
    return True


def _flag_value(character, predicate, trigger_id):
    """Read a namespaced quest_flags entry for trigger/dialogue gating."""
    from engine.systems import quest_flags as quest_flags_mod

    namespace = predicate.get("namespace") or "trigger"
    owner_id = predicate.get("owner") or trigger_id
    if not owner_id:
        return None
    return quest_flags_mod.get_flag(
        character,
        f"{namespace}:{owner_id}",
        predicate.get("flag"),
    )
