"""verbs/combat.py -- attack and cast (instant active combat)."""

from command_support import _find_character

from classic import combat as combat_module
from classic import spells as spells_module
from classic import stats as stats_module
from classic.rules import registries


def _attack_candidates(character, room):
    return [
        c for c in room.characters()
        if c is not character and float(getattr(c, "hp", 0) or 0) > 0
    ]


def _resolve_target(character, name, game):
    room = character.location
    if room is None:
        return None, "You are nowhere."
    candidates = _attack_candidates(character, room)
    target = _find_character(name, candidates, self_character=character)
    if target is None:
        return None, "No one like that here."
    return target, None


def cmd_attack(character, args, game):
    """Instant melee attack vs a visible target (sets ongoing target)."""
    name = (args or "").strip()
    if not name:
        character.session.send("Attack whom?  attack <name>")
        return
    target, err = _resolve_target(character, name, game)
    if err:
        character.session.send(err)
        return
    character.target = target
    combat_module.resolve_instant_action(character, target, game)


def _roll_spell_heal(caster, target, spell_row, *, rng=None):
    import random
    from classic import stats as stats_mod

    sides = int(spell_row.get("heal_die", 8))
    if rng is None:
        roll = random.randint(1, sides)
    else:
        roll = int(float(rng()) * sides) + 1
    ability = spell_row.get("ability", "WIS")
    mod = stats_mod.ability_mod(stats_mod.get_ability(caster, ability))
    heal = roll + max(0, mod)
    cap = stats_mod.max_hp(target)
    target.hp = min(cap, float(getattr(target, "hp", 0) or 0) + heal)
    return heal


def _resolve_builtin_spell(caster, spell_id, target, game, *, rng=None):
    spell_row = spells_module.get_spell(spell_id)
    if spell_row is None:
        return {"ok": False, "text": "Unknown spell."}
    class_id = getattr(caster, "classic_class", None)
    if class_id not in spell_row.get("classes", ()):
        return {"ok": False, "text": "Your class cannot cast that spell."}
    kind = spell_row.get("spell_kind")
    if kind == "heal":
        if target is None:
            return {"ok": False, "text": "Heal whom?"}
        amount = _roll_spell_heal(caster, target, spell_row, rng=rng)
        caster_name = getattr(caster, "key", "You")
        target_name = getattr(target, "key", "someone")
        return {
            "ok": True,
            "text": f"[HEAL] {caster_name} restores {amount} HP to {target_name}.",
        }
    if kind == "attack":
        if target is None:
            return {"ok": False, "text": "Cast at whom?"}
        # Reuse combat brief with spell damage die override via context.
        from engine.systems import combat_engine

        def _spell_build(attacker, defender, game=None, *, rng=None, **ctx):
            brief = combat_module.build_brief(
                attacker, defender, game, rng=rng, **ctx
            )
            if brief["outcome"] in ("hit", "critical"):
                sides = int(spell_row.get("damage_die", 6))
                import random
                if rng is None:
                    dmg = random.randint(1, sides)
                else:
                    dmg = int(float(rng()) * sides) + 1
                ability = spell_row.get("ability", "INT")
                mod = stats_module.ability_mod(
                    stats_module.get_ability(attacker, ability)
                )
                brief["damage"] = max(1, dmg + max(0, mod))
            return brief

        brief = _spell_build(caster, target, game, rng=rng)
        result = combat_module.apply_brief(brief, game)
        text = combat_module.narrate(brief, result)
        ticks = int(getattr(game, "game_time_ticks", 0) or 0)
        caster.last_instant_action_tick = ticks
        return {"ok": True, "text": text.replace("[HIT]", "[SPELL]")}
    return {"ok": False, "text": "That spell is not implemented yet."}


def cmd_cast(character, args, game):
    """Cast a class spell at a target (instant)."""
    parts = (args or "").strip().split()
    if len(parts) < 1:
        character.session.send("Cast what?  cast <spell> [target]")
        return
    spell_id = parts[0].lower()
    target_name = " ".join(parts[1:]).strip()
    target = character
    if target_name:
        target, err = _resolve_target(character, target_name, game)
        if err:
            character.session.send(err)
            return
    hook = registries.get_spell_resolver()
    if hook is not None:
        outcome = hook(character, spell_id, target, game)
    else:
        outcome = _resolve_builtin_spell(character, spell_id, target, game)
    text = outcome.get("text")
    if text:
        from classic.combat import _broadcast_combat
        _broadcast_combat(game, character, text)
    elif not outcome.get("ok", True):
        character.session.send(outcome.get("text", "Cast failed."))
