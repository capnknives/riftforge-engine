"""floor_loot.py -- abandoned floor salvage + vault TTL (generic engine core).

Cadence scavengers pick up loose takeable floor Items once they have sat
long enough -- immersion parity via ``npc_do get``. Corpses still use
the existing scavenge / bury / feast loops; this module is only for
non-body clutter (severed heads, kill salvage, dropped junk).

Silent TTL applies **only** in the lost-item vault room the game
registers via hooks. Town / sewer / wilderness floors are cleaned by
scavengers, not by decay.
"""

from __future__ import annotations

from engine import hooks as hooks_mod

# Wait before Cadence scoops a fresh drop (~5 real minutes). Gives players
# time to ``get`` kill loot / trophies themselves. Session-pacing, not a
# calendar quantity -- converted to actual game_time_ticks via
# ticks_for_wall_seconds at the live gm clock scale wherever consumed.
FLOOR_SCAVENGE_AGE_SECONDS = 300.0

# Vault-only crumple (~30 real minutes). Stamped on sink arrival; legacy
# piles get a deadline on first decay tick so the Cage does not hold
# forever.
VAULT_ITEM_DECAY_SECONDS = 1800.0

# Authored plaza seed and similar props scavengers must never pocket.
_SKIP_ITEM_KEYS = frozenset({
    "a rusted sword",
})

# Catalog ids that stay on the floor (signs, fixtures that are not furniture).
_SKIP_CATALOG_IDS = frozenset({
    "wayfinding_sign",
})


def stamp_floor_drop(game, item):
    """Mark ``item`` as freshly dropped onto a room floor.

    Idempotent when already stamped -- keeps the earlier drop clock so a
    second spill helper cannot reset the scavenger grace window.
    """
    if item is None:
        return
    if getattr(item, "floor_dropped_tick", None) is not None:
        return
    now = 0
    if game is not None:
        now = int(getattr(game, "game_time_ticks", 0) or 0)
    item.floor_dropped_tick = now


def stamp_vault_arrival(game, item):
    """Start the vault TTL when an Item lands in the lost-item vault."""
    if item is None:
        return
    if getattr(item, "vault_decay_at_tick", None) is not None:
        return
    now = 0
    if game is not None:
        now = int(getattr(game, "game_time_ticks", 0) or 0)
    from engine import game_clock_tuning as clock_mod
    item.vault_decay_at_tick = now + clock_mod.ticks_for_wall_seconds(
        VAULT_ITEM_DECAY_SECONDS, game,
    )


def _catalog_id(item):
    """Lowercased catalog id, or empty string."""
    return str(getattr(item, "catalog_id", None) or "").strip().lower()


def is_scavengeable_floor_item(item, game=None):
    """True when a Cadence scavenger may ``get`` this floor Item.

    Skips bodies, furniture, phones, mythic artifacts, locked strongboxes,
    authored seed keys, and drops still inside the grace window.
    Unstamped takeables count as abandoned (pre-stamp live piles).
    """
    if item is None:
        return False
    if getattr(item, "is_body", False):
        return False
    if getattr(item, "furniture", False):
        return False
    if getattr(item, "is_phone", False) or getattr(item, "is_payphone", False):
        return False
    cat = _catalog_id(item)
    if cat in _SKIP_CATALOG_IDS:
        return False
    try:
        exclude = hooks_mod.floor_loot_artifact_exclude_ids()
        if cat and cat in exclude:
            return False
    except Exception:
        pass
    key = (getattr(item, "key", None) or "").strip().lower()
    if key in {k.lower() for k in _SKIP_ITEM_KEYS}:
        return False
    # Sealed lockboxes / strongboxes stay for players to crack.
    if getattr(item, "locked", False) and getattr(item, "loot", None):
        return False
    dropped = getattr(item, "floor_dropped_tick", None)
    if dropped is not None and game is not None:
        now = int(getattr(game, "game_time_ticks", 0) or 0)
        from engine import game_clock_tuning as clock_mod
        age = clock_mod.ticks_for_wall_seconds(FLOOR_SCAVENGE_AGE_SECONDS, game)
        if now < int(dropped) + age:
            return False
    return True


def pick_floor_scavenge_item(room, game=None):
    """One scavengable floor Item in ``room``, or None.

    Prefer severed heads (trophy piles) then anything else takeable.
    """
    from world import Item

    if room is None:
        return None
    heads = []
    other = []
    for obj in list(getattr(room, "contents", None) or []):
        if not isinstance(obj, Item):
            continue
        if not is_scavengeable_floor_item(obj, game=game):
            continue
        cat = _catalog_id(obj)
        if cat.startswith("severed_") or "severed head" in (
            getattr(obj, "key", "") or ""
        ).lower():
            heads.append(obj)
        else:
            other.append(obj)
    if heads:
        return heads[0]
    if other:
        return other[0]
    return None


def room_has_floor_scavenge(room, game=None):
    """True when ``room`` has at least one scavengable floor Item."""
    return pick_floor_scavenge_item(room, game=game) is not None


def is_lost_item_vault(room):
    """True when ``room`` is the registered lost-item vault."""
    if room is None:
        return False
    vault_key = hooks_mod.lost_item_vault_room_key()
    key = getattr(room, "key", None)
    leg = getattr(room, "legacy_key", None)
    if vault_key and (
        key == vault_key or leg == vault_key
    ):
        return True
    if key == "BE00005" or getattr(room, "vnum", None) == "BE00005":
        return True
    return False


def tick_vault_item_decay(game):
    """Crumble expired floor Items in the lost-item vault only.

    Bodies and furniture are left alone (wipebodies / props own those).
    Unstamped vault clutter gets a deadline on first sight so legacy piles
    clear after one TTL window. Returns how many Items were removed.
    """
    from world import Item

    if game is None:
        return 0
    vault = hooks_mod.orphan_item_room_for_game(game)
    if vault is None:
        return 0
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    removed = 0
    for obj in list(getattr(vault, "contents", None) or []):
        if not isinstance(obj, Item):
            continue
        if getattr(obj, "is_body", False):
            continue
        if getattr(obj, "furniture", False):
            continue
        deadline = getattr(obj, "vault_decay_at_tick", None)
        if deadline is None:
            stamp_vault_arrival(game, obj)
            continue
        if now < int(deadline):
            continue
        vault.remove(obj)
        removed += 1
    if removed and vault.characters():
        vault.broadcast(
            "Forgotten things crumble into dust and are gone.",
            exclude=None,
        )
    return removed
