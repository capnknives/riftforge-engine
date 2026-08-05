"""basegame/body_parts.py -- regional injury policy for the reference game.

Thin wrapper over ``engine.systems.body_parts``: always on for non-NPC
Characters (players + Echoes), wires max-HP resolver, attach, persist,
and score status lines. SUPERS keeps its own gate + anatomy-tree layer.
"""

from __future__ import annotations

from engine.systems import body_parts as body_parts_engine

TIER_HEALTHY = body_parts_engine.TIER_HEALTHY
TIER_SEVERITY = body_parts_engine.TIER_SEVERITY


def gate_enabled():
    """Regional injuries are always on in the basegame demo."""
    return True


def is_player_limb_actor(character):
    """Players and Echoes take limb damage; NPCs stay aggregate-HP only."""
    if character is None:
        return False
    return not getattr(character, "is_npc", False)


def attach_character(character):
    """Initialize regional pools + readiness attrs on a new Character."""
    from engine.systems import readiness as readiness_mod

    body_parts_engine.ensure_body_parts(character)
    readiness_mod.ensure_defaults(character)


def register_hooks():
    """Wire aggregate max-HP into regional cap math (bootstrap core hooks)."""
    from basegame import stats as stats_module

    body_parts_engine.set_max_hp_resolver(stats_module.max_hp)


def restore_body_parts(character, saved):
    """Reapply a persisted ``body_parts`` dict from the save blob."""
    if not isinstance(saved, dict):
        body_parts_engine.ensure_body_parts(character)
        return
    character.body_parts = {
        region: dict(part) if isinstance(part, dict) else {"hp": 0}
        for region, part in saved.items()
    }
    body_parts_engine.ensure_body_parts(character)


def blob_body_parts(character):
    """Serialize ``character.body_parts`` for persistence."""
    parts = getattr(character, "body_parts", None)
    if not isinstance(parts, dict):
        return {}
    return {
        region: {"hp": int((part or {}).get("hp", 0) or 0)}
        for region, part in parts.items()
        if isinstance(part, dict)
    }


def status_line(character, *, screenreader=False, compact=False):
    """Plain-text injury summary for score sheets (empty when healthy)."""
    del screenreader
    if not gate_enabled() or not is_player_limb_actor(character):
        return ""
    show_percents = bool(getattr(character, "combat_numbers", False))
    entries = []
    tier_labels = []
    percent_values = []
    for idx, region in enumerate(
        body_parts_engine.character_regions(character)
    ):
        tier = body_parts_engine.part_tier(character, region)
        if tier == TIER_HEALTHY:
            continue
        rank = TIER_SEVERITY.index(tier)
        pct = None
        if show_percents:
            cap = max(1, body_parts_engine.max_part_hp(character, region))
            hp = body_parts_engine.part_hp(character, region)
            pct = int(round(100.0 * hp / cap))
            label = f"{region} {tier} ({pct}%)"
        else:
            label = f"{region} {tier}"
        entries.append((rank, idx, label, tier, pct))
        tier_labels.append(tier)
        if pct is not None:
            percent_values.append(pct)
    if not entries:
        return ""
    entries.sort(key=lambda pair: (pair[0], pair[1]))
    if compact and len(entries) >= 3:
        unique_tiers = set(tier_labels)
        same_pct = (
            show_percents
            and percent_values
            and len(set(percent_values)) == 1
        )
        if len(unique_tiers) == 1:
            tier = next(iter(unique_tiers))
            count = len(entries)
            noun = "region" if count == 1 else "regions"
            summary = f"Injuries: {count} {noun} {tier}"
            if same_pct:
                summary += f" ({percent_values[0]}%)"
            summary += " -- score combat for each limb"
            return summary
    labels = [label for _rank, _idx, label, _tier, _pct in entries]
    return "Injuries: " + ", ".join(labels)


def combat_injury_lines(character):
    """Per-limb rows for ``score combat`` (all non-healthy regions)."""
    if not gate_enabled() or not is_player_limb_actor(character):
        return []
    lines = []
    for region in body_parts_engine.character_regions(character):
        tier = body_parts_engine.part_tier(character, region)
        if tier == TIER_HEALTHY:
            continue
        cap = max(1, body_parts_engine.max_part_hp(character, region))
        hp = body_parts_engine.part_hp(character, region)
        pct = int(round(100.0 * hp / cap))
        lines.append(f"  {region}: {hp}/{cap} ({tier}, {pct}%)")
    return lines
