"""
mine.py -- player mining verbs (engine-generic dispatch).

SUPERS wires these into COMMANDS; gathering ``mine`` delegates here when
args are present or the actor stands underground / uses mine down.
"""
from __future__ import annotations

from command_support import broadcast_here

import hashlib
import random

from engine import hooks
from engine.systems import mine_channel as channel_mod
from engine.systems import mine_graph as graph_mod
from engine.systems import mine_rooms as rooms_mod
from engine.systems import material_instances as mat_mod
from engine.systems import smelter as smelter_mod

_CARDINALS = frozenset(graph_mod.GRAPH_DIRECTIONS)

# Ungated, low-chance "anyone might notice" stumble on a wilderness look.
# Deliberately vaguer than the company-specific supers/content/mine/companies.json
# marker_pool lines -- those are the reliable prospect-skill read; this is
# just "something's here," no company identity.
_MARKER_STUMBLE_LINES = (
    "Your boot catches on something half-buried in the dirt -- a weathered "
    "marker stake, or what's left of one.",
    "A scrap of rotted signage pokes up out of the ground here, whatever it "
    "once said long since worn away.",
    "Something metal glints faintly in the dirt underfoot -- an old stake, "
    "maybe, driven by hands that never came back for it.",
)


def _send(character, message):
    """Send to a live Session if present (HB-42: tick/helper calls stay quiet)."""
    session = getattr(character, "session", None)
    if session is not None:
        session.send(str(message))


def _in_mine_room(room):
    return room is not None and getattr(room, "virtual_mine", False)


def _parse_dir(args):
    raw = (args or "").strip().lower()
    if not raw:
        return None
    parts = raw.split(None, 1)
    return parts[0] if parts[0] in _CARDINALS else None


def is_mine_context(character, args):
    """True when engine mine verbs should handle ``mine`` instead of gather."""
    room = character.location
    if _in_mine_room(room):
        return True
    direction = _parse_dir(args)
    if direction == "down" and graph_mod.mouth_key_from_room(room):
        return True
    return False


def cmd_prospect(character, args, game):
    """Read geology / markers; trains mining skill."""
    room = character.location
    mouth_key = graph_mod.mouth_key_from_room(room)
    if mouth_key is None and _in_mine_room(room):
        mouth_key = getattr(room, "mouth_key", None)
    if mouth_key is None:
        _send(character, "Prospect where? Stand on a wilderness mining cell.")
        return
    area = getattr(room, "area_type", None) or "mountains"
    if not graph_mod.is_mine_capable_room(room) and not _in_mine_room(room):
        _send(character, "This ground does not read as mine-country.")
        return
    mouth = graph_mod.get_mouth(game, mouth_key, create=True, area_type=area)
    graph_mod._touch_activity(game, mouth)
    geo = mouth.get("geology") or {}
    lines = [
        "Geology read:",
        f"  Primary: {geo.get('primary_element', '?')}",
        f"  Secondary: {geo.get('secondary_element', '?')}",
    ]
    if geo.get("contamination_tag"):
        lines.append(f"  Contamination hint: {geo['contamination_tag']}")
    marker = mouth.get("marker") or {}
    if marker.get("visible") or marker.get("identified"):
        lines.append(
            f"  Surface marker: company traces "
            f"({marker.get('company_id') or mouth.get('company_id') or 'unknown'})."
        )
    else:
        lines.append("  No surface company marker visible.")
    if _in_mine_room(room):
        lines.append(f"  Chamber depth: {getattr(room, 'mine_depth', '?')}")
        lines.append(f"  Carved rooms here: {graph_mod.carved_room_count(mouth)}")
    _send(character, "\r\n".join(lines))
    _gain_mining_skill(character)


def _gain_mining_skill(character):
    hooks.gain_skill(character, "mining", 0.1)


def cmd_mine_engine(character, args, game):
    """Underground vein harvest or carve ``mine <dir>``."""
    room = character.location
    direction = _parse_dir(args)
    if direction and not _in_mine_room(room):
        if direction == "down":
            return _mine_down(character, game, room)
        _send(character, "Mine down from a wilderness cell to enter a shaft.")
        return
    if not _in_mine_room(room):
        _send(character, "You are not underground.")
        return
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    mouth = graph_mod.get_mouth(game, mouth_key)
    if not mouth or not room_id:
        _send(character, "This mine passage is not wired.")
        return
    if direction:
        ok, msg = channel_mod.join_channel(game, mouth, room_id, direction, character)
        _send(character, msg)
        if ok and room:
            room.broadcast(
                f"{character.key} works the {direction} face.",
                exclude=character,
            )
            hooks.maybe_mine_job_bark(character, game, "carve")
        return
    _harvest_vein(character, game, room, mouth, room_id)


def _mine_down(character, game, room):
    mouth_key = graph_mod.mouth_key_from_room(room)
    if mouth_key is None:
        _send(character, "You cannot mine down here.")
        return
    area = getattr(room, "area_type", None) or "mountains"
    if not graph_mod.is_mine_capable_room(room):
        _send(character, "This terrain is not mine-capable.")
        return
    mouth = graph_mod.get_mouth(game, mouth_key, create=True, area_type=area)
    company_layout = None
    marker = mouth.get("marker") or {}
    if marker.get("visible") or marker.get("company_id"):
        company_layout = hooks.mine_company_layout(mouth.get("company_id"))
    shaft_id = graph_mod.ensure_shaft_room(
        game, mouth_key, mouth, company_layout=company_layout,
    )
    dest = rooms_mod.get_mine_room(game, mouth_key, shaft_id)
    if dest is None:
        _send(character, "The shaft fails to open.")
        return
    character.move_to(dest)
    _send(character, "You descend into the mine.")
    dest.broadcast(f"{character.key} descends into the mine.", exclude=character)
    _roll_discoverable(game, mouth_key, shaft_id, character)


def _elements_at_depth(table, depth):
    """Return (element_id, yield_weight) pairs eligible at chamber depth."""
    depth = int(depth)
    candidates = []
    if not isinstance(table, dict):
        return candidates
    for element_id, row in table.items():
        if not isinstance(row, dict):
            continue
        for band in row.get("depth_bands") or []:
            if not isinstance(band, dict):
                continue
            lo = int(band.get("min_depth", 0))
            hi = int(band.get("max_depth", lo))
            if lo <= depth <= hi:
                weight = int(band.get("yield_weight", 1) or 1)
                candidates.append((str(element_id), max(1, weight)))
                break
    return candidates


def _roll_vein_element(table, depth, seed_key, *, fallback="iron"):
    """Weighted stratum pick for one shared vein (stable per mouth room)."""
    candidates = _elements_at_depth(table, depth)
    if not candidates:
        return fallback
    total = sum(weight for _element, weight in candidates)
    digest = hashlib.sha256(str(seed_key or "").encode()).hexdigest()
    pick = int(digest[:8], 16) % total
    acc = 0
    for element_id, weight in candidates:
        acc += weight
        if pick < acc:
            return element_id
    return candidates[-1][0]


def _harvest_vein(character, game, room, mouth, room_id):
    node = graph_mod.room_by_id(mouth, room_id)
    if node and node.get("vein_depleted"):
        _send(character, "This vein is picked clean for now.")
        return
    geo = mouth.get("geology") or {}
    depth = int(node.get("depth") or 1) if node else 1
    table = hooks.mine_stratum_table("earth")
    mouth_key = getattr(room, "mouth_key", None) or mouth.get("mouth_key")
    element = _roll_vein_element(
        table,
        depth,
        f"{mouth_key}:{room_id}:{depth}",
        fallback=geo.get("primary_element") or "iron",
    )
    row = table.get(element) if isinstance(table, dict) else {}
    ore_key = (row.get("catalog_ore_id") if isinstance(row, dict) else None) or f"ore_{element}"
    try:
        weight = float(row.get("weight_per_unit") or 2.0)
    except (TypeError, ValueError):
        weight = 2.0
    if not mat_mod.can_carry_weight(character, weight):
        _send(character, mat_mod.refuse_carry_message())
        return
    try:
        item = hooks.make_world_item({"item": ore_key}, where="mine_vein")
    except Exception:
        item = None
    if item is None:
        _send(character, f"No ore catalog for {element}.")
        return
    purity = random.randint(40, 85)
    mat_mod.stamp_ore_instance(
        item, material_id=element, purity=purity, depth_band=depth,
    )
    ok, msg = mat_mod.try_give_item(character, item)
    if not ok:
        _send(character, msg)
        return
    if node:
        node["vein_depleted"] = True
    game._mine_graphs_dirty = True
    _send(character, f"You chip free {element} ore from the vein.")
    room.broadcast(f"{character.key} mines ore from the wall.", exclude=character)
    hooks.maybe_mine_job_bark(character, game, "harvest")
    _gain_mining_skill(character)


def cmd_shore(character, args, game):
    """Raise support rating on a face (shore <dir> [timber|steel])."""
    room = character.location
    if not _in_mine_room(room):
        _send(character, "Shore supports underground only.")
        return
    parts = (args or "").strip().split()
    if not parts:
        _send(character, "Usage: shore <direction> [timber|steel]")
        return
    direction = parts[0].lower()
    material = (parts[1] if len(parts) > 1 else "timber").lower()
    catalog = hooks.mine_support_catalog(getattr(room, "realm", "prime"))
    rating = int(catalog.get(material, 0))
    if rating <= 0:
        _send(character, f"Unknown support material '{material}'.")
        return
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id)
    if not node:
        _send(character, "No mine node here.")
        return
    face = graph_mod.get_face(node, direction)
    face["shore_material"] = material
    face["shore_rating"] = rating
    node["support_rating"] = max(int(node.get("support_rating") or 0), rating)
    if face.get("blocked_collapse"):
        face["blocked_collapse"] = False
    game._mine_graphs_dirty = True
    _send(character, f"You shore the {direction} face with {material}.")
    room.broadcast(
        f"{character.key} shores the {direction} passage with {material}.",
        exclude=character,
    )


def cmd_survey(character, args, game):
    """ASCII graph of carved rooms for this mouth."""
    room = character.location
    mouth_key = graph_mod.mouth_key_from_room(room)
    if mouth_key is None and _in_mine_room(room):
        mouth_key = getattr(room, "mouth_key", None)
    if mouth_key is None:
        _send(character, "Survey requires a mine mouth or underground room.")
        return
    mouth = graph_mod.get_mouth(game, mouth_key)
    if not mouth:
        _send(character, "No carved workings recorded here.")
        return
    lines = [f"Mine survey ({mouth_key}):", f"  Rooms: {graph_mod.carved_room_count(mouth)}"]
    here = getattr(room, "mine_room_id", None) if _in_mine_room(room) else None
    for rid, node in sorted((mouth.get("rooms") or {}).items()):
        depth = node.get("depth", "?")
        mark = " *" if rid == here else ""
        sup = node.get("support_rating", 0)
        lines.append(f"  [{rid}] depth {depth} support {sup}{mark}")
    _send(character, "\r\n".join(lines))


def cmd_hang(character, args, game):
    """hang torch | lantern | gate <dir>"""
    room = character.location
    if not _in_mine_room(room):
        _send(character, "Hang fixtures underground only.")
        return
    parts = (args or "").strip().split()
    if not parts:
        _send(character, "Usage: hang torch | hang lantern | hang gate <dir>")
        return
    kind = parts[0].lower()
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id) if mouth else None
    if not node:
        _send(
            character,
            "This chamber isn't wired to the mine -- you can't hang anything here.",
        )
        return
    if kind in ("torch", "lantern"):
        _hang_light(character, game, room, node, kind)
        return
    if kind == "gate":
        if len(parts) < 2:
            _send(character, "Usage: hang gate <direction>")
            return
        direction = parts[1].lower()
        if hooks.mine_company_job_gate_permission(character) is False and getattr(
            character, "is_npc", False
        ):
            _send(character, "You cannot hang a gate here.")
            return
        face = graph_mod.get_face(node, direction)
        gate = face.setdefault("gate", {})
        gate["installed"] = True
        gate["locked"] = False
        gate["owner"] = character.key
        gate["access"] = []
        game._mine_graphs_dirty = True
        _send(character, f"You hang a gate on the {direction} passage.")
        room.broadcast(f"{character.key} hangs a gate to the {direction}.", exclude=character)
        return
    _send(character, "Hang torch, lantern, or gate <dir>.")


def _hang_light(character, game, room, node, kind):
    from engine.systems import material_instances as mat_mod
    needle = "torch" if kind == "torch" else "lantern"
    item = mat_mod.find_carried_item(character, needle)
    if item is None:
        _send(character, f"You need a {needle} to hang.")
        return
    if kind == "torch":
        mat_mod.remove_from_inventory(character, item)
    node.setdefault("fixtures", []).append({
        "kind": kind,
        "provides_light": True,
        "expires_at_tick": int(getattr(game, "game_time_ticks", 0) or 0) + 120,
    })
    room.dark = False
    room.mine_has_light = True
    game._mine_graphs_dirty = True
    _send(character, f"You hang a {kind}; the chamber brightens.")
    room.broadcast(f"{character.key} hangs a {kind}.", exclude=character)


def try_lock_gate(character, game, args, *, unlock=False):
    """lock gate <dir> / unlock gate <dir> sub-target."""
    room = character.location
    if not _in_mine_room(room):
        return False
    parts = (args or "").strip().split()
    if len(parts) < 2 or parts[0].lower() != "gate":
        return False
    direction = parts[1].lower()
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id) if mouth else None
    if not node:
        _send(
            character,
            "This chamber isn't wired to the mine -- no gate to lock here.",
        )
        return True
    face = graph_mod.get_face(node, direction)
    gate = face.get("gate") or {}
    if not gate.get("installed"):
        _send(character, f"No gate on the {direction} passage.")
        return True
    owner = gate.get("owner")
    access = list(gate.get("access") or [])
    if unlock:
        if owner != character.key and character.key not in access:
            _send(character, "That gate is not yours to unlock.")
            return True
        gate["locked"] = False
        _send(character, f"You unlock the {direction} gate.")
    else:
        gate["locked"] = True
        gate["owner"] = gate.get("owner") or character.key
        _send(character, f"You lock the {direction} gate.")
    face["gate"] = gate
    game._mine_graphs_dirty = True
    room.broadcast(
        f"{character.key} {'unlocks' if unlock else 'locks'} the {direction} gate.",
        exclude=character,
    )
    return True


def try_gate_breach(character, game, verb, args):
    """pick/force/bypass on locked mine gate. Returns True if handled."""
    room = character.location
    if not _in_mine_room(room):
        return False
    direction = _parse_dir(args)
    if not direction:
        return False
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id)
    if not node:
        return False
    face = graph_mod.get_face(node, direction)
    gate = face.get("gate") or {}
    if not gate.get("installed") or not gate.get("locked"):
        return False
    ok = hooks.mine_gate_breach(character, verb)
    if ok:
        gate["locked"] = False
        if verb == "force" and random.random() < 0.2:
            node["support_rating"] = max(0, int(node.get("support_rating") or 0) - 1)
        game._mine_graphs_dirty = True
        room.broadcast(
            f"{character.key} forces the {direction} gate ({verb}).",
            exclude=character,
        )
    _send(character, f"You {verb} the {direction} gate." if ok else "It holds.")
    return True


def cmd_smelt(character, args, game):
    room = character.location
    if not smelter_mod.room_has_smelter(room):
        _send(character, "You need a smelter or foundry (see 'help mining').")
        return
    needle = (args or "").strip().lower()
    from engine.systems import material_instances as mat_mod
    item = mat_mod.find_carried_item(character, needle or "ore")
    if item is None or not getattr(item, "material_id", None):
        _send(character, "Smelt what? Carry typed ore (see 'help mining').")
        return
    ok, msg, ingot = smelter_mod.smelt_ore(character, game, item)
    _send(character, msg)
    if ok and ingot:
        mat_mod.remove_from_inventory(character, item)
        gave_ok, gave_msg = mat_mod.try_give_item(character, ingot)
        if not gave_ok:
            character.inventory.append(item)
            _send(character, gave_msg)
            return
        room.broadcast(f"{character.key} smelts ore.", exclude=character)


def cmd_alloy(character, args, game):
    """Combine ingots or bind stub ore into alloys at a smelter."""
    room = character.location
    if not smelter_mod.room_has_smelter(room):
        _send(character, "You need a smelter or foundry (see 'help mining').")
        return
    parts = (args or "").strip().split()
    if not parts:
        lines = ["Alloy recipes at this station:"]
        for recipe in hooks.mine_alloy_recipes():
            ins = ", ".join(
                f"{key} x{count}"
                for key, count in sorted((recipe.get("inputs") or {}).items())
            )
            lines.append(f"  {recipe['id']}: {ins} -> {recipe['output']}")
        _send(character, "\n".join(lines))
        return
    recipe_id = parts[0].lower()
    ok, msg, ingot = smelter_mod.alloy_at_station(character, game, recipe_id)
    _send(character, msg)
    if ok and ingot:
        room.broadcast(f"{character.key} works metal in the crucible.", exclude=character)


def cmd_stop_mine(character, args, game):
    """Stop working a carve channel on optional direction."""
    room = character.location
    if not _in_mine_room(room):
        _send(character, "You are not in a mine.")
        return
    direction = _parse_dir(args)
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    mouth = graph_mod.get_mouth(game, mouth_key)
    if direction:
        channel_mod.leave_channel(mouth, room_id, direction, character.key)
        _send(character, f"You stop working the {direction} face.")
    else:
        channel_mod.leave_all_channels_for_character(game, character.key)
        _send(character, "You stop mining.")


def cmd_forge_mine(character, args, game):
    """Player forge at smelter (type x metal; legacy infused recipes)."""
    room = character.location
    if not smelter_mod.room_has_smelter(room):
        _send(character, "You need a smelter or foundry station.")
        return
    raw = (args or "").strip()
    if not raw:
        lines = list(hooks.mine_forge_type_lines())
        lines.append("Legacy infused recipes (need matching ingot):")
        for recipe in hooks.mine_forge_recipes():
            lines.append(f"  {recipe['id']} ({recipe.get('material_id', '?')} ingot)")
        _send(character, "\n".join(lines))
        return

    tokens = raw.split()
    impress = False
    if tokens and tokens[-1].lower() == "impress":
        impress = True
        tokens = tokens[:-1]

    ingot_needle = None
    type_parts = []
    idx = 0
    while idx < len(tokens):
        if tokens[idx].lower() == "with" and idx + 1 < len(tokens):
            ingot_needle = tokens[idx + 1]
            idx += 2
            continue
        type_parts.append(tokens[idx])
        idx += 1

    type_token = " ".join(type_parts).strip()
    if not type_token:
        _send(character, "Usage: forge <type> [with <ingot>] [impress]")
        return

    from engine.systems import material_instances as mat_mod

    ingot = hooks.mine_find_ingot(character, ingot_needle)
    if ingot is None:
        _send(character, "Carry an ingot to forge (forge <type> with <metal>).")
        return

    ok, msg, out = hooks.forge_at_station(
        character, game, type_token, ingot, impress=impress,
    )
    _send(character, msg)
    if ok and out:
        mat_mod.remove_from_inventory(character, ingot)
        extras = list(getattr(out, "_forge_extra_consumables", None) or ())
        for extra in extras:
            mat_mod.remove_from_inventory(character, extra)
        gave_ok, gave_msg = mat_mod.try_give_item(character, out)
        if not gave_ok:
            character.inventory.append(ingot)
            for extra in extras:
                character.inventory.append(extra)
            _send(character, gave_msg)
            return
        room.broadcast(f"{character.key} forges metal.", exclude=character)


def stumble_marker_on_look(room, character, game):
    """Wilderness look: chance to notice unmarked company marker."""
    mouth_key = graph_mod.mouth_key_from_room(room)
    if mouth_key is None:
        return None
    area = getattr(room, "area_type", None) or "plains"
    chance = hooks.mine_marker_chance(room, character)
    if graph_mod.is_mine_capable_room(room):
        chance = max(chance, 0.08)
    else:
        chance = max(chance, 0.02)
    mouth = graph_mod.get_mouth(game, mouth_key, create=True, area_type=area)
    marker = mouth.setdefault("marker", {"visible": False, "identified": False})
    if marker.get("visible"):
        return None
    if random.random() > chance:
        return None
    marker["visible"] = True
    game._mine_graphs_dirty = True
    return random.choice(_MARKER_STUMBLE_LINES)


def _roll_discoverable(game, mouth_key, room_id, character):
    ctx = {
        "game": game,
        "mouth_key": mouth_key,
        "room_id": room_id,
        "character": character,
    }
    row = hooks.roll_mine_discoverable(ctx)
    if not row:
        return
    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id)
    if node:
        node["discoverable_id"] = row.get("id")
        game._mine_graphs_dirty = True
    text = row.get("text") or ""
    if text and character.location:
        broadcast_here(character, text)


def on_character_relocate(character, game):
    """Cancel mine channels when character moves."""
    channel_mod.leave_all_channels_for_character(game, character.key)


def cancel_mine_channels(character, game=None):
    """Hook target for before_relocate."""
    if game is not None:
        channel_mod.leave_all_channels_for_character(game, character.key)


def try_mine_directional_move(character, direction, game):
    """Move between carved mine rooms or up to wilderness mouth."""
    room = character.location
    if not _in_mine_room(room):
        return False
    direction = (direction or "").strip().lower()
    mouth_key = getattr(room, "mouth_key", None)
    room_id = getattr(room, "mine_room_id", None)
    mouth = graph_mod.get_mouth(game, mouth_key)
    node = graph_mod.room_by_id(mouth, room_id)
    if not node:
        return False
    if direction == "up" and room_id == mouth.get("shaft_room_id"):
        surface = rooms_mod.wilderness_mouth_room(game, mouth_key)
        if surface is None:
            _send(character, "No surface mouth to return to.")
            return True
        character.move_to(surface)
        _send(
            character,
            "You climb back to the surface. The shaft stays open -- "
            "type mine down to go back in.",
        )
        surface.broadcast(f"{character.key} emerges from the mine.", exclude=character)
        return True
    face = graph_mod.get_face(node, direction)
    if not face.get("carved"):
        _send(character, f"You cannot go {direction} — the face is not cleared.")
        return True
    gate = face.get("gate") or {}
    if gate.get("installed") and gate.get("locked"):
        if gate.get("owner") != character.key and character.key not in (
            gate.get("access") or []
        ):
            _send(character, f"The {direction} gate is locked.")
            return True
    dest_id = face.get("dest_room_id")
    if not dest_id:
        _send(character, f"You cannot go {direction}.")
        return True
    dest = rooms_mod.get_mine_room(game, mouth_key, dest_id)
    if dest is None:
        _send(character, "That passage is not open.")
        return True
    character.move_to(dest)
    _send(character, f"You go {direction}.")
    dest.broadcast(f"{character.key} arrives from the {graph_mod.OPPOSITE.get(direction, 'passage')}.", exclude=character)
    _roll_discoverable(game, mouth_key, dest_id, character)
    return True
