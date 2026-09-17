"""engine/verbs/basic.py -- generic, game-agnostic MUD verbs.

Peeled out of the old monolithic commands.py (see that module's docstring
for the split rationale). Every handler here is a plain MUD-engine verb that
would make sense in ANY game built on this engine, not just SUPERS: moving
around, looking, talking, inventory, the clock, help/commands listings, and
the bug/suggestion report pipeline.

Two-repo purity Phase 2 (docs/plans/two_repo_purity.md): this module must
NOT import the SUPERS game package at all -- not at the top of the file,
and not with a LAZY (function-local) import either. An earlier pass
allowed lazy SUPERS imports here for game flavor (eclipse ambience, crime/
lodging move gates, ...); Phase 2 forbids that outright, because a plain
grep for a SUPERS import anywhere under `engine/` must return zero matches
for the purity gate to pass. Those flavor sites now call optional callables
registered on `engine/hooks.py` instead (SUPERS wires the real
implementations in `supers/bootstrap.py`'s `register_all_hooks()`) -- see
each hook's docstring in `engine/hooks.py` for the exact contract. `who`,
`time`, and `idlemode` were almost entirely SUPERS game content once you
strip the flavor away, so those three verbs moved wholesale to
`supers/verbs/engine_flavor.py` instead of growing hooks; the versions left
here are lean engine-only stubs that a bare engine install still needs, and
SUPERS_COMMANDS overrides them at dict-merge time in commands.py.

Shared helpers that themselves need `supers` at a deeper level
(`_can_see_spirit`, `_is_gm`, `_move_one`, ...) live in `command_support.py`
instead (repo root, not under `engine/`) -- that module has no such
restriction; see its own docstring for why.
"""
import os
import re

import engine.systems.economy as economy_wallet
from engine import map_ui
from engine import world_maps
from engine.systems.civic_fixture import floor_items_excluding_fixtures

from command_support import (
    _can_see_spirit,
    _is_presence_hidden,
    _display_name,
    floor_item_look_lines,
    format_floor_items_for_look,
    visible_floor_items,
    split_shell_args,
    _find_character,
    _find_item,
    _find_carried_item_for_open,
    _find_item_for_open,
    _collect_item_matches,
    parse_bulk_item_query,
    parse_qty_item_query,
    format_item_tally,
    _is_gm,
    _presence_face,
    _public_label,
    is_staff_stealth_presence,
    _staff_tags_hidden_in_look,
    _move_one,
    _pull_followers,
    _pull_followers_to,
    require_here,
    broadcast_here,
    send_if_online,
)
from engine.hooks import (
    get_help_categories,
    get_help_topics,
    item_drop_refusal,
    item_give_refusal,
    upgrade_legacy_container,
)
from engine.command_support import drop_item_refusal


def _exit_dest_room(dest, game):
    """Resolve an exits{} value to a Room (some pockets store key strings)."""
    if dest is None:
        return None
    if not isinstance(dest, str):
        return dest
    from engine.room_vnum import lookup_room

    return lookup_room(game, dest)


def _exit_dest_look_title(dest, game, *, room, direction, character):
    """Player-facing exit label for look / exits / SR nav."""
    from engine import hooks

    title = hooks.look_exit_dest_label(
        room, direction, dest, game=game, character=character,
    )
    if title:
        return title
    dest_room = _exit_dest_room(dest, game)
    if dest_room is not None:
        return dest_room.look_title()
    if isinstance(dest, str):
        return dest
    return None


def _exit_label_for_look(dest, game, *, room, direction, character):
    """Exit label + closed flag for look / exits / SR paths.

    Closed structure doors never leak the destination chamber name --
    only a shut-door label (bug report 1545).
    """
    from engine import hooks
    from engine.systems import doors as doors_engine

    closed = hooks.look_exit_door_closed(
        room, direction, dest, game=game, character=character,
    )
    if closed:
        return doors_engine.closed_exit_look_label(direction), True
    title = _exit_dest_look_title(
        dest, game, room=room, direction=direction, character=character,
    )
    return title, bool(closed)


def _persist_or_warn(character, game, *, already_told=None):
    """Write ``game.save()`` or tell the player it did not stick (HB-17).

    Account / config used to swallow save errors after success prose.
    """
    try:
        game.save()
        return True
    except Exception:
        import traceback
        print("[persist] game.save failed after player-facing change:", flush=True)
        traceback.print_exc()
        sess = getattr(character, "session", None)
        if sess is not None:
            if already_told:
                sess.send(
                    "That change is in memory but did not write to disk. "
                    "Try again or tell staff."
                )
            else:
                sess.send(
                    "Could not save that change to disk. Try again or tell staff."
                )
        return False


# Bare ``look items`` / ``look souls`` / ``look vehicles`` -- section only.
_LOOK_SECTION_ALIASES = {
    "items": "items",
    "item": "items",
    "souls": "souls",
    "soul": "souls",
    "people": "souls",
    "persons": "souls",
    "vehicles": "vehicles",
    "vehicle": "vehicles",
    "cars": "vehicles",
    "rides": "vehicles",
    "houses": "houses",
    "house": "houses",
    "homes": "houses",
    "home": "houses",
}


def _look_room_section(character, game, section):
    """Print only floor items, parked vehicles, or souls here -- skip chrome."""
    from engine import hooks
    from engine import style
    from engine import vision as vision_mod
    from world import Character, Item

    room = character.location
    if hooks.is_consciousness_exile(character):
        sensory = hooks.consciousness_sensory_room(character)
        if sensory is not None:
            room = sensory
    if room is None:
        character.session.send("You are nowhere.")
        return
    if not vision_mod.can_see_room(character, room):
        character.session.send(
            "It is pitch dark. You can still move by direction, "
            "but you see nothing here."
        )
        return

    from engine import display_prefs
    prefs = display_prefs.preference_character(character, game)
    screenreader = bool(getattr(prefs, "screenreader", False))

    if section == "items":
        floor_item_objs = visible_floor_items(
            character,
            floor_items_excluding_fixtures(
                [o for o in room.contents if isinstance(o, Item)],
            ),
        )
        item_lines = format_floor_items_for_look(
            floor_item_look_lines(floor_item_objs, character),
            character,
        )
        if not item_lines:
            character.session.send("You see no items here.")
            return
        if screenreader:
            out = ["Items:"]
            for row in item_lines:
                text = style.strip_ansi(str(row)).strip()
                if text and text[-1] not in ".!?":
                    text = text + "."
                out.append(f"  {text}")
            character.session.send("\n".join(out))
            return
        character.session.send(
            "\n".join(style.paint("muted", str(row)) for row in item_lines)
        )
        return

    if section == "vehicles":
        from engine.systems import vehicles as vehicles_mod
        rows = vehicles_mod.look_vehicle_detail_lines(room, game, character)
        if not rows:
            character.session.send("You see no vehicles here.")
            return
        if screenreader:
            out = ["Vehicles:"]
            for row in rows:
                text = style.strip_ansi(str(row)).strip()
                if text and text[-1] not in ".!?":
                    text = text + "."
                out.append(f"  {text}")
            character.session.send("\n".join(out))
            return
        character.session.send(
            "Vehicles:\n"
            + "\n".join(style.paint("muted", str(row)) for row in rows)
        )
        return

    if section == "houses":
        rows = hooks.lodging_look_home_detail_lines(
            room, game, character,
        )
        if not rows:
            character.session.send("You see no homes for sale here.")
            return
        if screenreader:
            out = ["Homes for sale:"]
            for row in rows:
                text = style.strip_ansi(str(row)).strip()
                if text and text[-1] not in ".!?":
                    text = text + "."
                out.append(f"  {text}")
            character.session.send("\n".join(out))
            return
        character.session.send(
            "Homes for sale:\n"
            + "\n".join(style.paint("muted", str(row)) for row in rows)
        )
        return

    souls = []
    for o in room.contents:
        if o is character:
            continue
        if not isinstance(o, Character):
            continue
        if getattr(o, "vessel_host_key", None):
            continue
        if getattr(o, "husk_ridden", False):
            continue
        if hooks.in_veil(o) and hooks.veil_visible_to(character, o):
            base = _display_name(o, viewer=character)
            label = hooks.veil_soul_label(character, o, base)
            souls.append(
                hooks.room_presence_line(
                    label, o, room, game, viewer=character,
                )
            )
            continue
        if _is_presence_hidden(character, o):
            if not _staff_tags_hidden_in_look(character, o):
                continue
            label = f"{_display_name(o, viewer=character)} (hidden)"
            souls.append(
                hooks.room_presence_line(
                    label, o, room, game, viewer=character,
                )
            )
            continue
        label = _display_name(o, viewer=character)
        souls.append(
            hooks.room_presence_line(
                label, o, room, game, viewer=character,
            )
        )
    if not souls:
        character.session.send("Nobody else is here.")
        return
    if screenreader:
        out = ["Souls:"]
        for row in souls:
            text = style.strip_ansi(str(row)).strip()
            if text and text[-1] not in ".!?":
                text = text + "."
            out.append(f"  {text}")
        character.session.send("\n".join(out))
        return
    character.session.send(
        "\n".join(style.paint("dark_magenta", str(row)) for row in souls)
    )


def cmd_nearby(character, args, game):
    """List people in the room without a full look (screenreader-friendly).

    Same output as ``look people`` / ``look souls`` -- on-demand presence
    when NPC foot traffic is quiet.
    """
    _ = args
    _look_room_section(character, game, "souls")


_LOOK_WINDOW_ALIASES = frozenset({
    "window", "windows", "out", "outside", "outdoors",
    "out the window", "out window", "through the window",
})


def _look_out_window(character, game):
    """Glance through glass -- indoor rooms only (OOC Hasao).

    Uses an outdoor neighbor's prose when one is linked; otherwise a
    short generic street glimpse. Never names VNUMs.
    """
    room = getattr(character, "location", None)
    if room is None:
        character.session.send("You have no window here.")
        return
    if getattr(room, "outdoor", False):
        character.session.send(
            "You're already outside. The sky does not need a window."
        )
        return
    outdoor = None
    exits = getattr(room, "exits", None) or {}
    for dest in exits.values():
        if dest is not None and getattr(dest, "outdoor", False):
            outdoor = dest
            break
    if outdoor is not None:
        prose = (getattr(outdoor, "description", None) or "").strip()
        if prose:
            clip = prose.split(".")[0].strip()
            if clip:
                character.session.send(
                    f"Through the glass: {clip}."
                )
                return
        from engine.room_vnum import room_name
        character.session.send(
            f"Through the glass you see {room_name(outdoor)}."
        )
        return
    character.session.send(
        "Glass shows the street beyond -- sky, whoever is walking past, "
        "and the weather sitting on the curb."
    )


def cmd_look(character, args, game, *, after_move=False):
    """Show the room (no args), look in a body (`look in <body>`), or look
    at one thing/person here (`look bob`).

    Bare look uses the Master Room Layout
    (docs/plans/colorandformattingforgame.R §1): framed title + area tag,
    indented description, then conditional Paths / Souls / Items sections
    (empty sections are omitted entirely). `look in <body>` lists nested
    belongings (suggestions.log #49). Otherwise same targeting as examine.

    ``after_move``: True when this look is the auto-look after a move (or
    walk arrival). When ``config mapmove on``, also prints the local
    minimap after look (unless ``config maplook`` already embedded it).
    """
    from engine.command_support import send_asleep_world_closed
    from engine import hooks

    aware = hooks.maybe_sleep_dream_awareness(character, game)
    if aware is not None:
        character = aware
    if send_asleep_world_closed(character):
        return

    stripped = args.strip()
    if stripped:
        # `look items` / `look souls` -- section only (#163).
        lower = stripped.lower()
        if lower in _LOOK_WINDOW_ALIASES:
            _look_out_window(character, game)
            return
        section = _LOOK_SECTION_ALIASES.get(lower)
        if section:
            _look_room_section(character, game, section)
            return
        # `look in <thing>` -- body belongings (#49).
        if lower.startswith("in "):
            _look_in(character, stripped[3:].strip(), game)
            return
        if not _look_at(character, stripped):
            character.session.send("You don't see that here.")
        return

    from engine import style
    from engine import display_prefs
    from engine import vision as vision_mod
    from world import Character, Item

    room = character.location
    # Possession consciousness exile: look into personal Heaven/Hell.
    from engine import hooks
    if hooks.is_consciousness_exile(character):
        sensory = hooks.consciousness_sensory_room(character)
        if sensory is not None:
            room = sensory

    if room is None:
        character.session.send(
            "You have no location right now. Staff: try `goto` a room, "
            "or reconnect if this persists."
        )
        return

    # Stamp the room this look rendered so a later ``bug`` can compare it
    # to character.location (highway fill / copyover desync class).
    from engine.report_context import note_last_look

    note_last_look(character, room)

    # D67: dark rooms need a carried light source (full blackout).
    if not vision_mod.can_see_room(character, room):
        character.session.send(
            "It is pitch dark. You can still move by direction, "
            "but you see nothing here."
        )
        # Still push Room.Info (id/area only -- gmcp omits exits/desc).
        from engine import gmcp
        gmcp.push_room(character)
        return

    # Area badge: always area_type (bug #26 -- wilderness is a spawn flag,
    # never shown as the terrain label). Plane + zone may relabel for
    # spirit/elemental maps and soak venues (docs/plans/area_type_taxonomy.md).
    # Vehicle interiors inherit the host curb / atlas cell (bug report 646).
    from engine import map_ui as map_ui_mod
    from engine.systems import vehicles as vehicles_mod

    area_room = vehicles_mod.look_area_source_room(game, room)
    area_tag = map_ui_mod.area_display_label(
        getattr(area_room, "plane", None) or "earth",
        getattr(area_room, "area_type", "plains"),
        zone=getattr(area_room, "zone", None),
    )

    # Client prefs live on the login Mantle even when act focus runs look
    # through a God bilocate twin (bug #196).
    prefs = display_prefs.preference_character(character, game)
    display_prefs.ensure_display_defaults(prefs)

    extras = []
    # Pressure training (section 4-D): only call out non-normal load.
    # Internal field remains Room.gravity; player-facing label is Pressure.
    gravity = getattr(room, "gravity", 1.0)
    if gravity != 1.0:
        extras.append(f"Pressure: {gravity:g}x")
    # D29: overland grid cells only -- coordinate hint for `map`.
    if getattr(room, "grid_prefix", None) is not None:
        extras.append(
            f"Overland: ({room.grid_x}, {room.grid_y}) -- type 'map' for terrain."
        )
        # Dual-layer micro coords (and similar) come via room_look_extras
        # so engine/ never imports supers (Phase 2 purity).
        # Distant named pockets (visible_as) by 8-way bearing + range band.
        # Pass character so staff gm_mode keeps long vista; players stay local.
        for vista_line in map_ui.landmark_vista_lines(room, character):
            extras.append(vista_line)
    # Pocket zone travel is separate from cardinal / in-out moves.
    zone_entries = getattr(room, "zone_entries", None) or {}
    zone_entries = hooks.filter_zone_entries_look(
        character, room, zone_entries, game,
    )
    if zone_entries:
        hints = world_maps.zone_entry_look_hints(zone_entries)
        if hints:
            extras.append(
                f"Enter: enter <name> -- here: {', '.join(hints)}"
            )
    # Exit only at the pocket mouth you entered (zone_exit + entry stamp).
    # Keep these short -- TTS reads them on every look in town.
    revalidate_zone_entry_stamp(character, game)
    stamped_entry = getattr(character, "zone_entry_hub_key", None)
    screenreader = bool(getattr(prefs, "screenreader", False))
    # Brief auto-look: skip prose (below), ambient weather lines, and
    # [TRAIL] clutter -- suggestion #115 / SR speed-read.
    brief_auto = after_move and getattr(prefs, "brief", False)
    if stamped_entry and room.key != stamped_entry:
        # Homestead interiors stamp zone=plot_id for membership but only the
        # porch mouth is a zone exit (bug report 1532 — hallway zone hint).
        homestead_interior = bool(getattr(room, "homestead_plot_id", None))
        in_pocket_zone = (
            not homestead_interior
            and (
                getattr(room, "zone", None)
                or getattr(room, "zone_exit_to", None)
            )
        )
        if in_pocket_zone:
            hub_label = _player_place_label(
                game, stamped_entry, fallback="the zone exit mouth",
            )
            if screenreader:
                extras.append(f"Zone exit: {hub_label}.")
            else:
                extras.append(
                    f"Zone exit at {hub_label} -- type exit there."
                )
    elif (
        getattr(room, "zone_exit", False)
        and getattr(room, "zone_exit_to", None) is not None
        and (not stamped_entry or room.key == stamped_entry)
    ):
        if screenreader:
            extras.append("Zone exit: type exit.")
        else:
            dest = getattr(room, "zone_exit_to", None)
            dest_label = (
                _player_place_label(game, dest, fallback="overland")
                if dest is not None
                else "overland"
            )
            extras.append(f"Zone exit: type exit ({dest_label}).")
    # Outdoor ambient sky (open-air rooms: overland + tagged town streets).
    # Spawns still key off wilderness; look flavor keys off outdoor.
    # Weather clause (CONUS climatology) prefers the game's weather model
    # (supers.weather via hook) when available; eclipse still wins over
    # ordinary sky when active.
    if getattr(room, "outdoor", False):
        from engine import game_calendar
        if not brief_auto:
            eclipse_line = hooks.eclipse_ambient_line(game)
            if eclipse_line:
                extras.append(eclipse_line)
            else:
                wx_line = hooks.weather_look_clause(
                    room, game, screenreader=screenreader, character=character,
                )
                if wx_line:
                    extras.append(wx_line)
                else:
                    extras.append(game_calendar.format_ambient(game.calendar()))
    else:
        # Indoor dampen: precip / storm / tornado may still speak one line.
        if not brief_auto:
            wx_line = hooks.weather_look_clause(
                room, game, screenreader=screenreader, character=character,
            )
            if wx_line:
                extras.append(wx_line)

    # Hybrid weather vision (rain / storm / snow / nearby tornado): always
    # an overlay when severe outdoors; chance whiteout hides the rest of
    # the room for this look. Hook is a no-op without a weather model.
    vision = hooks.weather_look_vision(
        character,
        room,
        game,
        screenreader=screenreader,
        after_move=after_move,
    )
    if isinstance(vision, dict) and vision.get("overlay"):
        extras.append(vision["overlay"])
    whiteout = bool(isinstance(vision, dict) and vision.get("whiteout"))

    # Per-room extras (planar influence, Croatoan panic, etc.) -- any room.
    # On whiteout, skip non-weather extras so debris does not still name
    # every landmark the eye cannot find.
    import time as _time
    from engine import lag_watch
    t_ex = _time.perf_counter()
    if not whiteout:
        for line in hooks.room_look_extras(room, game, character):
            if line:
                extras.append(line)
    lag_watch.stamp_move_phase(game, "look_extras", t_ex)

    # Paths: (direction, destination look label) -- columns in format_room.
    # Game may hide exits (e.g. closed Devil's Gates) via filter_look_exits.
    # D66: also hide secret directions until this character has searched.
    # Street-address exits: avoid "12223: 12223 Campbell Pass" — show
    # "12223: Campbell Pass" when the dest title already starts with the number.
    exits = []
    floor_items = []
    souls = []
    exit_room = vehicles_mod.exit_source_room(game, room)
    if not whiteout:
        for direction, dest in (exit_room.exits or {}).items():
            if dest is None:
                continue
            if not hooks.look_exit_visible(dest, game):
                continue
            if hooks.look_exit_hidden_from_viewer(
                character, room, direction, dest, game,
            ):
                continue
            if not vision_mod.character_knows_exit(character, exit_room, direction):
                continue
            # Dual-layer wilderness: exits point at self -- label what that
            # step approaches (Lebanon / bunker / terrain) for sighted + SR.
            title, closed = _exit_label_for_look(
                dest, game, room=exit_room, direction=direction, character=character,
            )
            if not title:
                continue
            if str(direction).isdigit():
                from engine.room_naming import strip_address_from_exit_label
                title = strip_address_from_exit_label(direction, title)
            exits.append((direction, title, closed))
        for direction, title in hooks.room_look_virtual_exits(
            room, character, game,
        ):
            if direction and title:
                exits.append((direction, title))

        # Items = floor loot; Souls = other characters (not you; spirits you
        # can't see are skipped -- section 6). Section label is Items (not
        # Relics) so it never collides with Divine/Path relic content.
        # Identical catalog stacks collapse to ``N X are here`` (digits).
        floor_item_objs = visible_floor_items(
            character,
            floor_items_excluding_fixtures(
                [o for o in room.contents if isinstance(o, Item)],
            ),
        )
        floor_items = format_floor_items_for_look(
            floor_item_look_lines(floor_item_objs, character),
            character,
        )
        souls = []
        for o in room.contents:
            if o is character:
                continue
            if not isinstance(o, Character):
                continue
            # Riding Mantle: inside host -- hide the Mantle body.
            if getattr(o, "vessel_host_key", None):
                continue
            # Living husk while Mantle rides -- hide the shell.
            if getattr(o, "husk_ridden", False):
                continue
            if hooks.in_veil(o) and hooks.veil_visible_to(character, o):
                base = _display_name(o, viewer=character)
                label = hooks.veil_soul_label(character, o, base)
                souls.append(
                    hooks.room_presence_line(
                        label, o, room, game, viewer=character,
                    )
                )
                continue
            if _is_presence_hidden(character, o):
                if not _staff_tags_hidden_in_look(character, o):
                    continue
                label = f"{_display_name(o, viewer=character)} (hidden)"
                souls.append(
                    hooks.room_presence_line(
                        label, o, room, game, viewer=character,
                    )
                )
                continue
            label = _display_name(o, viewer=character)
            souls.append(
                hooks.room_presence_line(
                    label, o, room, game, viewer=character,
                )
            )

    # Room chrome: players always get the ROOM NAME. Staff in GM form see
    # ROOM NAME[VNUM] so dig / mappers match GMCP without opaque graph ids
    # in the framed title (docs/plans/room_vnum_identity_migration.md).
    # Sighted: paint City - Main - Sub from zone city_color / main_colors
    # (docs/AREA_BUILDING.md). Screenreader stays plain text.
    from engine import room_naming as room_naming_mod
    from engine import room_vnum as room_vnum_mod
    plain_name = room_vnum_mod.room_name(room)
    staff_vnum = None
    from engine.command_support import staff_room_vnums_in_look
    if staff_room_vnums_in_look(character):
        raw_v = getattr(room, "vnum", None)
        if raw_v is not None and str(raw_v).strip():
            try:
                staff_vnum = room_vnum_mod.validate_vnum(raw_v)
            except ValueError:
                staff_vnum = str(raw_v).strip()
    room_heading = room_naming_mod.paint_structured_room_title(
        prefs,
        plain_name,
        room=room,
        game=game,
        staff_vnum=staff_vnum,
    )

    # Local ASCII map embeds only when config maplook is on (default off
    # -- short classic look). Bare ``map`` / mapmove still available.
    # Whiteout: no minimap — you cannot read the street grid either.
    local_map_lines = None
    if (
        not whiteout
        and getattr(prefs, "map_on_look", False)
        and getattr(prefs, "show_minimap", True)
        and not getattr(prefs, "screenreader", False)
    ):
        map_center = _local_map_center_room(character, game, room)
        rendered = map_ui.render_local_map(
            game.rooms,
            map_center,
            use_color=getattr(prefs, "use_color", True),
            compact=True,
            character=character,
            game=game,
        )
        if rendered:
            local_map_lines = rendered.split("\n")

    # Whiteout replaces authored prose with a can't-see-through line.
    # Brief mode (config brief): skip prose on auto-look after a move;
    # explicit ``look`` still shows the full description.
    look_description = room.description
    if whiteout and isinstance(vision, dict):
        look_description = (
            vision.get("fail_line")
            or "[WX] You can't see through the weather."
        )
    elif (
        after_move
        and getattr(prefs, "brief", False)
        and not whiteout
    ):
        look_description = ""

    t_fmt = _time.perf_counter()
    lines = style.format_room(
        room_heading,
        look_description,
        area_tag=area_tag,
        exits=exits,
        souls=souls,
        items=floor_items,
        extras=extras or None,
        width=style.layout_width(
            character=character,
            session=getattr(character, "session", None),
        ),
        screenreader=bool(getattr(prefs, "screenreader", False)),
        local_map_lines=local_map_lines,
        exits_verbose=bool(getattr(prefs, "exits_verbose", True)),
    )
    lag_watch.stamp_move_phase(game, "look_format", t_fmt)
    character.session.send("\r\n".join(lines))
    # Soft fear nudge: any monster senses a co-located Called Slayer.
    # (hook -- no-op / None without a game installed; Phase 2 purity.)
    from engine import hooks
    fear = hooks.monster_sense_message(character, room)
    if fear:
        character.session.send(fear)
    # Procurer case read / other game tells after bare look.
    for line in hooks.after_bare_look(character, room, game):
        if not line:
            continue
        if brief_auto and str(line).strip().startswith("[TRAIL]"):
            continue
        character.session.send(line)
    # Blank before the custom prompt comes from send_prompt (dispatch),
    # not here -- avoid double-spacing after look.
    # GMCP Room.Info -- also covers auto-look after move (_move_one).
    from engine import gmcp
    gmcp.push_room(character)
    if after_move:
        # Pan the Mudlet atlas camera even when ASCII mapmove is off.
        t_map = _time.perf_counter()
        _render_full_map_for(character, game, use_color=False)
        maybe_map_after_move(character, game)
        lag_watch.stamp_move_phase(game, "look_map", t_map)


def maybe_map_after_move(character, game):
    """Print the local minimap after a move when ``config mapmove`` is on.

    Skips screenreader and when ``config map`` is off. Does not run when
    ``map_on_look`` already embedded the map in the auto-look above.
    """
    from engine import display_prefs

    prefs = display_prefs.preference_character(character, game)
    display_prefs.ensure_display_defaults(prefs)
    if not getattr(prefs, "map_on_move", False):
        return
    if getattr(prefs, "map_on_look", False):
        # Already shown inside look -- avoid a second dump.
        return
    if character.session is None:
        return
    if getattr(prefs, "screenreader", False):
        return
    if not getattr(prefs, "show_minimap", True):
        return
    room = getattr(character, "location", None)
    if room is None:
        return
    map_center = _local_map_center_room(character, game, room)
    rendered = map_ui.render_local_map(
        game.rooms,
        map_center,
        use_color=getattr(prefs, "use_color", True),
        compact=True,
        character=character,
        game=game,
    )
    if rendered:
        character.session.send(rendered.replace("\n", "\r\n"))


def cmd_exits(character, args, game):
    """List visible exits from this room (plain labels -- not color-alone).

    Used by soft-gated openers (Family Business foyer) and players who want
    doors without a full look. Honors the same visibility filters as look
    (hidden Devil's Gates, secret exits until searched, dark rooms).
    """
    _ = args
    from engine import hooks
    from engine import vision as vision_mod
    from engine import display_prefs

    prefs = display_prefs.preference_character(character, game)
    display_prefs.ensure_display_defaults(prefs)

    room = character.location
    if room is None:
        character.session.send("You are nowhere -- no exits.")
        return
    if not vision_mod.can_see_room(character, room):
        character.session.send(
            "It is pitch dark. You can still move by direction, "
            "but you cannot read the exits."
        )
        return
    # Porch doors and interior links can vanish after stale world heals; the
    # game may repair the plot graph once per session before we list exits.
    hooks.ensure_homestead_plot_graph_for_actor(character, game)
    from engine.systems import vehicles as vehicles_mod

    exit_room = vehicles_mod.exit_source_room(game, room)
    lines = []
    for direction, dest in (exit_room.exits or {}).items():
        if dest is None:
            continue
        if not hooks.look_exit_visible(dest, game):
            continue
        if not vision_mod.character_knows_exit(character, exit_room, direction):
            continue
        title, closed = _exit_label_for_look(
            dest, game, room=exit_room, direction=direction, character=character,
        )
        if not title:
            continue
        if str(direction).isdigit():
            from engine.room_naming import strip_address_from_exit_label
            title = strip_address_from_exit_label(direction, title)
        lines.append((direction, title, closed))
    if not lines:
        character.session.send("Exits: none you can see.")
        return
    if not getattr(prefs, "exits_verbose", True):
        # Compact: honor config exits compact (abbrevs only, no dest list).
        from engine import style as style_mod

        tokens = []
        seen = set()
        closed_dirs = style_mod._exit_closed_set(lines)
        by_dir = style_mod._exit_dir_set(lines)
        for name in style_mod._EXIT_LINE_ORDER:
            if name in by_dir:
                tokens.append(
                    style_mod._exit_abbrev(
                        name, closed=name in closed_dirs,
                    ),
                )
                seen.add(name)
        for direction, _title, closed in lines:
            key = str(direction).strip().lower()
            if key in seen:
                continue
            tokens.append(style_mod._exit_abbrev(direction, closed=closed))
            seen.add(key)
        character.session.send(f"Exits: {', '.join(tokens)}")
        return
    character.session.send("Exits:")
    for direction, title, _closed in lines:
        character.session.send(f"  {direction}: {title}")


def cmd_search(character, args, game):
    """Search the current room for secret exits (D66).

    Reveals every direction listed in Room.hidden_directions into this
    character's known_exits. Does not find items or traps in v1 -- exits
    only. Works in the dark (you can feel along the walls).
    """
    from engine import vision as vision_mod

    room = character.location
    if room is None:
        character.session.send("You are nowhere.")
        return
    newly = vision_mod.reveal_hidden_exits(character, room)
    if not newly:
        character.session.send("You find nothing unusual.")
        return
    # Plain text list -- never color alone (a11y).
    listed = ", ".join(newly)
    character.session.send(
        f"You find a hidden way: {listed}."
        if len(newly) == 1
        else f"You find hidden ways: {listed}."
    )
    try:
        from engine import hooks
        hooks.on_hidden_exit_revealed(character, room)
    except Exception:
        # HB-36: player already saw the exit; persist/quest hooks must
        # not fail silently.
        import traceback
        print("[look] hidden-exit reveal hook failed:", flush=True)
        traceback.print_exc()


def cmd_map(character, args, game):
    """Local ASCII minimap: overland grid, Studio layout, or exit-graph.

    Prefs #18 / #30: ``config map off`` or screenreader mode skips ASCII.
    Screenreader gets a directional text summary instead (exits + terrain).

    ``map big`` / ``map full`` / ``map atlas`` (alias: ``bigmap``)
    show a camera window of the current overland grid sized to the
    client (never a wrapping full dump). Mudlet clients that advertise
    RiftForge.Map also get the full country out of band. Bare
    ``atlas`` is the America map (drive travels) -- see help atlas.
    Town / dungeon rooms use the hybrid local map (layout coords when
    stamped, else linked-exit neighborhood). Look embeds the same window
    when ``config map`` is on.
    """
    from engine import display_prefs

    display_prefs.ensure_display_defaults(character)
    want_zone = _map_wants_zone(args)
    want_full = _map_wants_full(args)
    if want_zone:
        lines = list(_directional_map_lines(character, game))
        from engine.systems import overland as overland_mod
        room = _local_map_center_room(character, game)
        if room is None:
            room = getattr(character, "location", None)
        zone_lines = overland_mod.look_nearby_zone_lines(room, game)
        if zone_lines:
            lines.extend(zone_lines)
        elif len(lines) <= 2:
            lines.append("No nearby zone mouths from here.")
        lines.append("")
        character.session.send("\r\n".join(lines))
        return
    if (
        not want_full
        and not _map_wants_local(args)
        and getattr(character, "map_view_full", False)
    ):
        # config mapview atlas: bare `map` (no explicit big/small token)
        # defaults to the full grid, same as if the player had typed
        # `map big` themselves.
        want_full = True
    if getattr(character, "screenreader", False):
        # ASCII dump is unusable for TTS -- local exits plus atlas size.
        lines = list(_directional_map_lines(character, game))
        from engine.systems import overland as overland_mod
        room = _local_map_center_room(character, game)
        if room is None:
            room = getattr(character, "location", None)
        zone_lines = overland_mod.look_nearby_zone_lines(room, game)
        if zone_lines:
            lines.extend(zone_lines)
        if want_full:
            lines.extend(_full_map_sr_lines(character, game))
            # Glyphs stay off the telnet line; Mudlet still gets the camera.
            _render_full_map_for(character, game, use_color=False)
        character.session.send("\r\n".join(lines))
        return
    if not getattr(character, "show_minimap", True):
        character.session.send(
            "Map is off. Type 'config map on' to show the ASCII minimap."
        )
        return
    use_color = getattr(character, "use_color", True)
    if want_full:
        room = _overland_map_center_room(character, game)
        rendered = _render_full_map_for(character, game, use_color=use_color)
        if rendered is None:
            character.session.send(
                "No full map here. Stand on an overland grid cell "
                "(Wastes, America Overland, …) and try 'map big' again."
            )
            return
        # Camera window is sized to the client -- do not page (paging a
        # map splits the country). GMCP clients get the same view OOB.
        character.session.send(rendered.replace("\n", "\r\n"))
        return
    # Respect the player's color preference (#51) -- letter glyphs stay the
    # primary signal either way (section 8 a11y).
    room = _local_map_center_room(character, game)
    rendered = map_ui.render_local_map(
        game.rooms,
        room,
        use_color=use_color,
        character=character,
        game=game,
    )
    if rendered is None:
        if room is None:
            character.session.send(
                "No map here. (Need a room -- try looking first.)"
            )
        else:
            character.session.send(
                "No map here. (This room has no exits to sketch.)"
            )
        return
    # render_* joins with \\n; convert to telnet \\r\\n for the wire.
    character.session.send(rendered.replace("\n", "\r\n"))


def cmd_zone(character, args, game):
    """Plain nearby-zone bearings for screenreader / VI navigation."""
    cmd_map(character, "zone", game)


def _map_wants_zone(args):
    """True when the player asked for nearby zone bearings only."""
    token = (args or "").strip().lower().split(None, 1)[0] if args else ""
    return token == "zone"


def _map_wants_full(args):
    """True when the player asked for the giant / full-grid atlas."""
    token = (args or "").strip().lower().split(None, 1)[0] if args else ""
    return token in (
        "big", "full", "atlas", "giant", "all", "world",
    )


def _map_wants_local(args):
    """True when the player explicitly asked for the small/local window.

    Only matters when ``config mapview atlas`` made the full grid the
    default -- lets a player force the tiny map back for one call. Same
    vocabulary as ``config drivemap minimap|mini|local|small``.
    """
    token = (args or "").strip().lower().split(None, 1)[0] if args else ""
    return token in ("small", "mini", "local", "minimap")


def _grid_meta_for_room(game, room):
    """Return (width, height, wrap) from map_registry for this grid room.

    Falls back to (None, None, False) when the room is not stamped or the
    registry row is missing -- caller refuses the full dump.
    """
    if room is None:
        return None, None, False
    map_id = getattr(room, "map_id", None)
    meta = (getattr(game, "map_registry", None) or {}).get(map_id) or {}
    width = meta.get("width")
    height = meta.get("height")
    wrap = bool(meta.get("wrap"))
    if width is None or height is None:
        prefix = getattr(room, "grid_prefix", None)
        for attr in ("overland_atlas", "frontierland_atlas"):
            atlas = getattr(game, attr, None)
            if atlas is not None and prefix == getattr(atlas, "prefix", None):
                width = getattr(atlas, "width", None)
                height = getattr(atlas, "height", None)
                break
    return width, height, wrap


def _local_map_center_room(character, game, room=None):
    """Center for the small minimap (not ``map big`` / atlas).

    Classic zone interiors (town hand rooms) stay on the room itself so
    we never paint the parent America macro cell into look/mapmove. Only
    dual-layer characters (``macro_pos`` set, including vehicle interiors)
    anchor the local window to the overland grid.
    """
    from engine import hooks

    if room is None:
        room = getattr(character, "location", None)
    if room is None:
        return None
    if getattr(room, "grid_prefix", None) is not None:
        return room
    if getattr(character, "macro_pos", None) is not None:
        resolved = hooks.map_center_room(character, game)
        if resolved is not None and getattr(
            resolved, "grid_prefix", None
        ) is not None:
            return resolved
    return room


def _overland_map_center_room(character, game):
    """Room used as map center: location, or America cell from macro_pos.

    Dual-layer vehicles sit in an interior Room without grid stamps, so
    ``map big`` resolves the atlas cell from ``macro_pos`` via the
    map_center_room hook (Phase 2 purity -- no supers import here).
    Town/dungeon interiors without ``macro_pos`` still resolve through the
    zone's mouth for full atlas / ``@`` placement (bug #44).
    """
    from engine import hooks
    room = getattr(character, "location", None)
    if room is not None and getattr(room, "grid_prefix", None) is not None:
        return room
    resolved = hooks.map_center_room(character, game)
    if resolved is not None:
        return resolved
    return room


def _push_overland_map_gmcp(character, game):
    """Refresh RiftForge.Map after an overland step without printing ASCII."""
    _render_full_map_for(character, game, use_color=False)


def _render_full_map_for(character, game, *, use_color=True):
    """Render a client-fitting atlas camera, or None if not on a sized grid.

    The live atlas may be wider than a telnet window. We never dump a row
    that would wrap -- see ``map_ui.render_viewport_grid``.
    """
    room = _overland_map_center_room(character, game)
    width, height, wrap = _grid_meta_for_room(game, room)
    if width is None or height is None:
        return None
    view_w, view_h = map_ui.viewport_dims(character, width, height)
    text, payload = map_ui.render_viewport_grid(
        game.rooms,
        room,
        width=width,
        height=height,
        view_w=view_w,
        view_h=view_h,
        use_color=use_color,
        character=character,
        game=game,
    )
    if payload:
        from engine import gmcp
        gmcp.push_map_view(character, payload)
    return text


def _full_map_sr_lines(character, game):
    """Screenreader supplement for ``map big`` (no ASCII dump)."""
    room = getattr(character, "location", None)
    width, height, wrap = _grid_meta_for_room(game, room)
    if width is None or height is None:
        return [
            "Full atlas: not available here "
            "(need an overland grid cell with a known size)."
        ]
    prefix = getattr(room, "grid_prefix", None) or "overland"
    cx = getattr(room, "grid_x", None)
    cy = getattr(room, "grid_y", None)
    wrap_bit = " Edges wrap like a globe." if wrap else ""
    return [
        (
            f"Full atlas: {prefix}, {width} by {height} cells. "
            f"You are at ({cx}, {cy}).{wrap_bit} "
            "Use walk <x> <y> to path without reading glyphs "
            "(help walk)."
        ),
    ]


def cmd_bigmap(character, args, game):
    """Alias: atlas camera window (same as ``map big``)."""
    # Ignore extra args -- bigmap always means the giant atlas.
    cmd_map(character, "big", game)


def _directional_map_lines(character, game):
    """Plain-text nav summary for screenreader mode (no ASCII grid).

    Lists the current room, optional terrain tag, and each exit with its
    destination title so TTS users get spatial info without glyph spam.
    Mirrors look's exit visibility gates (hooks + D66 known exits).
    Dark rooms without light/night-sight stay pitch-dark (no exit leak).
    """
    from engine import hooks
    from engine import vision as vision_mod

    room = getattr(character, "location", None)
    if room is None:
        return ["No location to describe."]
    # D67: do not list exits/title details when the viewer is blind here.
    if not vision_mod.can_see_room(character, room):
        return [
            "",
            "It is pitch dark. You can still move by direction, "
            "but you see nothing here.",
            "",
        ]
    lines = ["", f"Location: {room.look_title()}."]
    area_type = getattr(room, "area_type", None)
    if area_type:
        lines.append(f"Terrain: {area_type}.")
    exits = getattr(room, "exits", None) or {}
    visible = []
    for direction, dest in exits.items():
        if dest is None:
            continue
        if not hooks.look_exit_visible(dest, game):
            continue
        if not vision_mod.character_knows_exit(character, room, direction):
            continue
        dest_name, _closed = _exit_label_for_look(
            dest, game, room=room, direction=direction, character=character,
        )
        if dest_name:
            visible.append((direction, dest_name))
    if not visible:
        lines.append("No obvious exits.")
        lines.append("")
        return lines
    lines.append("Exits:")
    for direction, dest_name in sorted(visible, key=lambda p: str(p[0]).lower()):
        lines.append(f"  {direction}: {dest_name}.")
    lines.append("")
    lines.append(
        "ASCII minimap is off in screenreader mode. "
        "Type look for the full room."
    )
    lines.append("")
    return lines


def _split_look_detail_query(query):
    """Split ``look 'Earl Jacobs' head`` into (who, extra).

    Uses shell quoting so a two-word name stays one token. One-token
    queries are not extras -- those stay ordinary look <name>.
    """
    tokens = split_shell_args(query)
    if len(tokens) < 2:
        return None, None
    extra = (tokens[-1] or "").strip()
    who = " ".join(tokens[:-1]).strip()
    if not who or not extra:
        return None, None
    return who, extra


def _try_look_person_detail(character, query, room, can_see):
    """``look ayla head`` -- True when a person+extra was handled."""
    who, extra = _split_look_detail_query(query)
    if not who or not extra:
        return False
    chars = list(room.characters()) if room is not None else []
    target = _find_character(who, chars, self_character=character)
    if target is None:
        return False
    if target is not character:
        if not can_see:
            return False
        if _is_presence_hidden(character, target):
            if not _staff_tags_hidden_in_look(character, target):
                return False
    from engine import hooks
    detail = hooks.look_detail_for(character, target, extra)
    if detail is None:
        return False
    character.session.send(detail)
    return True


def _look_at(character, query):
    """Show one thing's description: self, carried/floor item, or person here.

    Shared by `look <target>` and `examine <target>` so both verbs surface
    chargen/setdesc text the same way. Returns True if something matched.

    Dark rooms (D67): self and carried inventory stay examinable by touch;
    floor items and other people need light or night-sight.
    """
    from world import Item
    from engine import vision as vision_mod

    # look me / look self / look myself -- classic MUD self-examine so you
    # can check your own setdesc / auto-built appearance without leaving
    # the room listing's "everyone but you" carve-out.
    from command_support import is_self_name
    if is_self_name(query):
        # Same viewer-relative body as look <name> so vessel-free spirits
        # and ridden husks do not dump a stale mortal setdesc (bug 836).
        from engine import hooks
        header = _display_name(character)
        body = hooks.look_body_for(character, character)
        if body is None:
            body = character.description or ""
        character.session.send(f"{header}\r\n{body}")
        for line in hooks.look_extra_lines(character, character):
            character.session.send(line)
        return True

    room = character.location
    # Authored exit look-desc (``look north``) before generic targeting.
    if room is not None:
        from engine.command_support import resolve_walk_direction

        direction = resolve_walk_direction(query.strip().lower(), room)
        if direction:
            descs = getattr(room, "exit_descs", None) or {}
            prose = None
            if isinstance(descs, dict):
                prose = descs.get(direction) or descs.get(direction.lower())
            if prose:
                character.session.send(prose)
                return True
        # Extra-descs wait until after people / items so ``look Mira``
        # cannot hit an authored keyword that merely contains "mira".
    # Limbo is not a visible room -- walking ``room.characters()`` here
    # used to AttributeError. Inventory still works below (tactile).
    can_see = room is not None and vision_mod.can_see_room(character, room)

    # Staff inspect by INUM (AD00001). Players never target instance numbers.
    from engine.command_support import (
        find_staff_item_by_inum,
        staff_room_vnums_in_look,
    )
    from engine.item_inum import staff_item_label

    staff_hit = find_staff_item_by_inum(
        character, query, room=room, can_see=can_see,
    )
    if staff_hit:
        character.session.send(staff_item_label(staff_hit))
        character.session.send(staff_hit.description or "")
        from engine import hooks
        game = getattr(getattr(character, "session", None), "game", None)
        hooks.after_look_item(character, staff_hit, game)
        return True

    # When you can see the room, people here beat carried kit on substring
    # collisions (bug #344: ``look Mira`` must not hit a ``mirror`` in pack).
    # Inventory still wins in pitch dark -- tactile examine of what you hold.
    if can_see:
        target = _find_character(
            query, room.characters(), self_character=character,
        )
        if target and _is_presence_hidden(character, target):
            if not _staff_tags_hidden_in_look(character, target):
                # Section 6 spirit-sight + living Reaper Mantle veil: same rule
                # cmd_look's souls list applies -- you can't examine what you
                # can't perceive.
                target = None
        if target:
            # Viewer-relative header + body so hood / unintroduced never leak
            # login keys or unique setdesc text to strangers.
            from engine import hooks
            header = _display_name(target, viewer=character)
            if _staff_tags_hidden_in_look(character, target):
                header = f"{header} (hidden)"
            body = hooks.look_body_for(character, target)
            if body is None:
                # Game hook unset (lean engine) or chain returned None --
                # never import supers from engine code (Phase 2 purity).
                body = ""
            character.session.send(f"{header}\r\n{body}")
            for line in hooks.look_extra_lines(character, target):
                character.session.send(line)
            # One-sided relationship quirk (asymmetric tags) -- private, rare.
            # (hook -- no-op / None without a game installed; Phase 2 purity.)
            if target is not character and getattr(character, "session", None):
                quirk = hooks.look_quirk(character, target)
                if quirk:
                    character.session.send(quirk)
            return True

    # Carried inventory -- tactile even in pitch dark (you know what
    # you are holding). Floor loot waits until vision clears below.
    item = _find_item(query, character.inventory, character=character)
    if item:
        if staff_room_vnums_in_look(character):
            character.session.send(staff_item_label(item))
        character.session.send(item.description)
        from engine import hooks
        game = getattr(getattr(character, "session", None), "game", None)
        hooks.after_look_item(character, item, game)
        return True

    # look ayla head / look me thumb -- after a full-query miss. Self works
    # in the dark; other people need light. Do this before floor items so
    # a person in the room wins over a kit item named the same extra.
    if _try_look_person_detail(character, query, room, can_see):
        return True

    if not can_see:
        return False

    items_here = visible_floor_items(
        character,
        [o for o in room.contents if isinstance(o, Item)],
    )
    item = _find_item(query, items_here)
    if item:
        desc = item.description or ""
        cat = getattr(item, "catalog_id", None) or ""
        # Wayfinding furniture: lead with a plain [SIGN] label; faint
        # chrome for sighted only (a11y -- never color alone).
        if cat == "wayfinding_sign" or "wayfinding" in (
            getattr(item, "key", "") or ""
        ).lower():
            if not desc.startswith("[SIGN]"):
                desc = f"[SIGN] {desc}"
            if not getattr(character, "screenreader", False):
                from engine import style as style_mod
                header = style_mod.paint_for(
                    character, "muted", "[SIGN] Wayfinding",
                )
                character.session.send(header)
        if staff_room_vnums_in_look(character):
            character.session.send(staff_item_label(item))
        character.session.send(desc)
        from engine import hooks
        game = getattr(getattr(character, "session", None), "game", None)
        hooks.after_look_item(character, item, game)
        return True

    # Authored room extra-desc (look painting) — after people and items so
    # a live character or floor object always wins the same keyword.
    extra_descs = getattr(room, "extra_descs", None) or {}
    if extra_descs:
        from engine.map_store import match_extra_desc_keyword

        _key, extra_prose = match_extra_desc_keyword(query, extra_descs)
        if extra_prose:
            character.session.send(extra_prose)
            return True

    return False


def _look_in_item_resolved(character, item, game):
    """List nested loot for one resolved container (bag, body, fridge, …)."""
    from engine import hooks

    handled = hooks.look_in_item(character, item, game)
    if handled:
        for line in handled:
            character.session.send(line)
        return
    # Worn/carried backpacks and kit bags (``bag_contents`` on the Item).
    contents = getattr(item, "bag_contents", None)
    if contents is not None:
        if not contents:
            character.session.send(
                f"You look in {item.key} -- nothing of note."
            )
            return
        from engine.systems import containers as containers_mod
        if containers_mod.is_gear_bag_item(item):
            containers_mod.consolidate_gear_bag_stacks(character)
        elif containers_mod.is_bag_item(item):
            containers_mod.consolidate_loot_bag_stacks(character)
        stacked = hooks.containers_stacked_carry_lines(contents, character)
        character.session.send(f"You look in {item.key}:")
        for line in stacked:
            character.session.send(line)
        return
    if not getattr(item, "is_body", False):
        character.session.send(f"You can't look in {item.key}.")
        return
    loot = [
        o for o in (getattr(item, "loot", None) or [])
        if hooks.item_visible_to(character, o)
    ]
    if not loot:
        character.session.send(f"You look in {item.key} -- nothing of note.")
        return
    names = ", ".join(o.key for o in loot)
    character.session.send(f"Looking in {item.key}, you find: {names}.")
    hooks.after_look_in_body(character, item, game)


def _look_in(character, query, game=None):
    """List belongings nested inside a body, or game-handled containers.

    Bodies: nested loot (suggestions.log #49). Carried/worn backpacks and kit
    bags use ``bag_contents``. Game content (e.g. home refrigerators) registers
    via engine.hooks.look_in_item -- the engine never imports SUPERS. Dark
    rooms block look-in without light/night-sight.
    """
    from world import Item
    from engine import vision as vision_mod
    if not query:
        character.session.send("Look in what?")
        return
    room = character.location
    # Dark rooms block look-in. Limbo is not "dark" -- inventory/weave
    # stay tactile; the floor walk below refuses nowhere instead.
    if room is not None and not vision_mod.can_see_room(character, room):
        character.session.send(
            "It is pitch dark. You can't make that out."
        )
        return
    if game is None:
        game = getattr(getattr(character, "session", None), "game", None)
    # Generic ``backpack`` / ``loot bag`` tokens match put/get: the worn loot
    # bag wins over loose empty duplicates in open inventory (bug report 487).
    from engine.systems import containers as containers_mod
    if containers_mod.is_generic_loot_bag_query(query):
        worn_loot = containers_mod.resolve_loot_bag(character, query)
        if worn_loot is not None:
            _look_in_item_resolved(character, worn_loot, game)
            return
    # Temporary pocket weave lives on the caster, not in inventory.
    from engine import hooks as hooks_mod

    if hooks_mod.containers_is_generic_weave_query(query):
        weave = hooks_mod.containers_active_weave_bag(character)
        if weave is not None:
            _look_in_item_resolved(character, weave, game)
            return
        character.session.send(
            "You have no pocket weave open (help pocketweave)."
        )
        return
    # Carried inventory -- specific bag names; unworn bags when none worn.
    item = _find_item(query, character.inventory, character=character)
    if item is not None:
        _look_in_item_resolved(character, item, game)
        return
    if room is None:
        send_if_online(character, "You're nowhere.")
        return
    items_here = visible_floor_items(
        character,
        [o for o in room.contents if isinstance(o, Item)],
    )
    item = _find_item(query, items_here)
    if item is None:
        character.session.send("You don't see that here.")
        return
    _look_in_item_resolved(character, item, game)


def cmd_examine(character, args, game):
    """Look closely at one specific thing: an item you're carrying, an item
    on the floor, a person in the room, or yourself (`examine me`). Same
    targeting as `look <target>` -- both verbs call `_look_at`.
    """
    if not args:
        character.session.send("Examine what?")
        return
    if not _look_at(character, args.strip()):
        character.session.send("You don't see that here.")


def cmd_sit(character, args, game):
    """Sit down where you are -- flavor posture (bug #58). No mechanical
    effect; walking away (cmd_move) silently stands you back up.
    """
    if getattr(character, "asleep", False) or getattr(character, "resting", False):
        character.session.send("You're already resting. Type 'wake' first.")
        return
    if getattr(character, "target", None) is not None:
        character.session.send("You can't sit down while fighting.")
        return
    if getattr(character, "sitting", False):
        character.session.send("You're already sitting.")
        return
    character.sitting = True
    character.session.send("You sit down.")
    room = getattr(character, "location", None)
    if room is not None:
        room.broadcast(
            f"{_presence_face(character)} sits down.",
            exclude=character,
        )


def cmd_stand(character, args, game):
    """Stand up from sitting, or leave awake rest (bug report 1300)."""
    if getattr(character, "asleep", False):
        character.session.send("You're asleep -- type 'wake' to get up.")
        return
    if getattr(character, "resting", False):
        from engine import hooks
        hooks.cancel_rest(character)
        character.session.send("You stand up from rest.")
        room = getattr(character, "location", None)
        if room is not None:
            room.broadcast(
                f"{_presence_face(character)} stands up from rest.",
                exclude=character,
            )
        return
    if getattr(character, "sitting", False):
        character.sitting = False
        character.session.send("You stand up.")
        room = getattr(character, "location", None)
        if room is not None:
            room.broadcast(
                f"{_presence_face(character)} stands up.",
                exclude=character,
            )
        return
    character.session.send("You're already standing.")


def cmd_move(character, direction, game):
    # NOTE: this handler receives a `direction` instead of `args`, because
    # dispatch() calls it specially (see the bottom of the file).
    from engine import hooks
    from engine import group as group_mod
    blocked = group_mod.live_move_blocked_message(character)
    if blocked:
        character.session.send(blocked)
        return
    if getattr(character, "asleep", False) and not (
        getattr(character, "is_sleep_dream_clone", False)
        or (
            getattr(character, "sleep_dream_tour", False)
            and not getattr(character, "sleep_dream_clone_key", None)
        )
    ):
        character.session.send(
            "You're asleep -- type 'wake' before you can move."
        )
        return
    # Awake rest cancels when you walk. (hook -- no-op without a game.)
    hooks.cancel_rest(character)
    # Sitting / lying are silent, unlike rest/sleep -- walking away stands.
    character.sitting = False
    character.lying = False
    character.posture_furniture_id = None
    # Dual-layer America overland (vehicle macro / on-foot micro).
    import time as _time
    from engine import lag_watch
    t_dir = _time.perf_counter()
    handled = hooks.try_directional_move(character, direction, game)
    lag_watch.stamp_move_phase(game, "try_dir", t_dir)
    if handled:
        return
    room = character.location
    if room is None:
        character.session.send("You're nowhere.")
        return
    dest = room.exits.get(direction)   # .get() returns None if there's no such exit
    if not dest:
        # Broken homestead graphs can drop porch doors; repair at most once
        # when the game reports it actually rewired exits (hook -- no-op lean).
        if hooks.ensure_homestead_plot_graph_for_actor(character, game):
            dest = room.exits.get(direction)
    if not dest:                       # None is falsy -> no exit that way
        character.session.send("You can't go that way.")
        return                         # stop here; nothing else to do

    # D66: hidden exits act like missing exits until searched/known.
    from engine import vision as vision_mod
    if not vision_mod.character_knows_exit(character, room, direction):
        character.session.send("You can't go that way.")
        return

    # Jail cells, hunter-safe sanctuaries, closed Devil's Gates, etc. --
    # one combined game-rules gate (hook -- always None/allowed without a
    # game; Phase 2 purity).
    block_message = hooks.move_gate_block(character, room, dest, game)
    if block_message:
        character.session.send(block_message)
        return

    _move_one(character, direction, dest, game)
    _pull_followers(character, room, direction, game)
    group_mod.validate_group_colocation(character, game)


def cmd_follow(character, args, game):
    """follow <name> to tag along whenever they move; bare 'follow' stops.

    Live-session convenience (world.Character.following/followers), never
    persisted -- see persistence.py. Cadence hunt AI uses the same bond
    helpers (start_following / stop_following) so Echo companions trail
    too. Breaks on disconnect via world.break_follows.

    Staff actually piloting GM form use a separate diagnostic tail
    (``staff_tailing``) so ``follow`` does not form a Group or touch pack
    glue -- a staff account playing an immersion cast body with GM powers
    off gets ordinary follow/group behavior like any other player (bug
    report 1574).
    """
    from engine.command_support import (
        in_active_gm_form,
        start_following,
        start_staff_tail,
        stop_following,
        stop_staff_tail,
    )
    from engine import group as group_mod
    name = args.strip()
    if in_active_gm_form(character):
        if not name:
            if getattr(character, "staff_tailing", None) is not None:
                stop_staff_tail(character)
                return
            if group_mod.in_group(character) and not group_mod.is_leader(character):
                if group_mod.try_leave_group(character, game, confirm=False) == "blocked":
                    return
            stop_following(character)
            return

        here = character.location
        if here is None:
            character.session.send("You're nowhere.")
            return
        target = _find_character(name, here.characters())
        if not target:
            character.session.send(f"No one named '{name}' is here.")
            return
        if target is character:
            character.session.send("You can't follow yourself.")
            return
        if getattr(character, "staff_tailing", None) is target:
            character.session.send(f"You're already tailing {_public_label(target)}.")
            return
        start_staff_tail(character, target)
        character.session.send(
            f"You tail {_public_label(target)}. "
            "[Staff] Diagnostic only -- no group or pack."
        )
        return

    if not name:
        if group_mod.in_group(character) and not group_mod.is_leader(character):
            if group_mod.try_leave_group(character, game, confirm=False) == "blocked":
                return
        stop_following(character)
        return

    here = character.location
    if here is None:
        character.session.send("You're nowhere.")
        return
    target = _find_character(name, here.characters())
    if not target:
        character.session.send(f"No one named '{name}' is here.")
        return
    if target is character:
        character.session.send("You can't follow yourself.")
        return
    if character.following is target:
        character.session.send(f"You're already following {_display_name(target)}.")
        return

    if not start_following(character, target):
        character.session.send(
            "That would loop the follow chain -- stop one trail first."
        )
        return
    character.session.send(f"You start following {_display_name(target)}.")
    # Live leaders get a private nudge (same gate as beckon auto-follow copy).
    if (
        getattr(target, "session", None) is not None
        and not (hasattr(target, "acts_as_echo") and target.acts_as_echo())
    ):
        target.session.send(f"{_display_name(character)} starts following you.")


def cmd_unfollow(character, args, game):
    """Stop following whoever you're currently following."""
    from engine.command_support import stop_following, stop_staff_tail
    from engine import group as group_mod
    if _is_gm(character) and getattr(character, "staff_tailing", None) is not None:
        stop_staff_tail(character)
        return
    if group_mod.in_group(character):
        confirm = "confirm" in (args or "").strip().lower().split()
        result = group_mod.try_leave_group(
            character, game, confirm=confirm,
        )
        if result == "blocked":
            return
        if result == "left":
            return
    stop_following(character, declined=True, game=game)


def cmd_group(character, args, game):
    """Party hub: roster, nested options, focus, merge, row, leave.

    Bare ``group`` opens a framed menu (roster + numbered options).
    Type ``group 2`` or ``group focus`` for the focus page; ``group
    focus <name>`` sets who NPC / Echo mates attack. ``group front`` /
    ``group back`` still set your display row. ``group leave confirm``
    peels a follower; ``group disband confirm`` breaks the party.
    """
    from engine import group as group_mod
    from engine import display_prefs as dprefs
    from engine import party_invite as party_mod

    dprefs.ensure_display_defaults(character)
    group_mod.ensure_session_defaults(character)
    raw = (args or "").strip()
    low = raw.lower()
    if not low:
        _send_group_hub(character, game)
        return
    parts = low.split()
    choice = parts[0]
    rest = " ".join(parts[1:]).strip()
    confirm = "confirm" in parts
    mapped = group_mod.lookup_hub_option(character, choice)
    if mapped:
        choice = mapped
    if choice == "form":
        character.session.send(
            "Follow someone, type beckon <name>, or party invite <name> "
            "to form a group (see help group)."
        )
        return
    if choice == "roster":
        sheet = group_mod.format_group_sheet(character, game)
        if not isinstance(sheet, str):
            sheet = "\r\n".join(str(line) for line in sheet)
        character.session.send(sheet)
        return
    if choice == "focus":
        _cmd_group_focus(character, rest, game)
        return
    if choice == "invite":
        if not rest:
            character.session.send(
                "Usage: group invite <name>  -- ask someone in the room "
                "(same as party invite). See help group."
            )
            return
        target = party_mod.resolve_invite_target(character, rest)
        if target is None:
            character.session.send(f"No one here named {rest!r}.")
            return
        ok, msg = party_mod.invite(character, target, game)
        character.session.send(msg)
        # Echo auto-join still has a Session in idlemode -- only prompt
        # when a live handshake is actually pending.
        if (
            ok
            and party_mod.invite_pending_accept(target)
            and getattr(target, "session", None) is not None
        ):
            target.session.send(
                f"{character.key} invites you to a party. "
                "Type 'party accept' or 'party decline'."
            )
        return
    if choice == "merge":
        if not rest:
            character.session.send(
                "Usage: group merge <name>  -- fold your group into theirs."
            )
            return
        target = party_mod.resolve_invite_target(character, rest)
        if target is None:
            character.session.send(f"No one here named {rest!r}.")
            return
        ok, msg, moved = party_mod.invite_merge(character, target, game)
        character.session.send(msg)
        if ok and not moved:
            dest_leader = group_mod.resolve_leader(target)
            if (
                dest_leader is not None
                and getattr(dest_leader, "session", None) is not None
                and dest_leader is not character
            ):
                dest_leader.session.send(
                    f"{character.key} wants to merge their group into yours. "
                    "Type 'party accept' or 'party decline'."
                )
        return
    if choice == "row":
        _cmd_group_row(character, rest, game)
        return
    if choice in ("front", "f", "fore"):
        _cmd_group_row(character, "front", game)
        return
    if choice in ("back", "b", "rear"):
        _cmd_group_row(character, "back", game)
        return
    if choice in ("leave", "split", "peel"):
        result = group_mod.try_leave_group(
            character, game, confirm=confirm,
        )
        if result == "ok":
            character.session.send("You are not in a group.")
        return
    if choice in ("disband", "break"):
        result = group_mod.try_disband_group(
            character, game, confirm=confirm,
        )
        if result == "ok":
            character.session.send("You are not in a group.")
        return
    character.session.send(
        "Type group for the menu. "
        "group focus <name>  |  group invite <name>  |  "
        "group merge <name>  |  group front  |  group back  |  "
        "group leave confirm  |  group disband confirm  "
        "(see help group)."
    )


def _send_group_hub(character, game):
    """Send the framed group menu as a single string."""
    from engine import group as group_mod

    blob = group_mod.format_group_hub(character, game)
    if not isinstance(blob, str):
        blob = "\r\n".join(str(line) for line in blob)
    character.session.send(blob)


def _cmd_group_row(character, rest, game):
    """Nested row page, or set front / back immediately."""
    from engine import group as group_mod

    token = str(rest or "").strip().lower().split()
    pick = token[0] if token else ""
    if pick in ("front", "f", "fore", "1"):
        group_mod.set_row(character, group_mod.ROW_FRONT)
        character.session.send(
            f"You shift to the {group_mod.ROW_LABELS[group_mod.ROW_FRONT]}."
        )
        return
    if pick in ("back", "b", "rear", "2"):
        group_mod.set_row(character, group_mod.ROW_BACK)
        character.session.send(
            f"You shift to the {group_mod.ROW_LABELS[group_mod.ROW_BACK]}."
        )
        return
    current = group_mod.row_label(character)
    character.session.send(
        f"Your row is {current}. Type group row front or group row back "
        "(display only -- see help group)."
    )


def _cmd_group_focus(character, rest, game):
    """Show or set the party combat focus (leader only to set)."""
    from engine import group as group_mod

    group_mod.ensure_session_defaults(character)
    token = str(rest or "").strip()
    if not token:
        _send_group_focus_menu(character, game)
        return
    low = token.lower()
    if low in ("clear", "none", "off", "drop"):
        if not group_mod.is_leader(character) and group_mod.in_group(character):
            character.session.send(
                "Only the group leader can clear focus."
            )
            return
        group_mod.clear_focus(character)
        character.session.send(
            "Group focus cleared. NPC and Echo mates attack whoever "
            "you are fighting."
        )
        return
    if not group_mod.in_group(character):
        character.session.send(
            "You are not in a group. Follow someone first, then "
            "group focus <name>."
        )
        return
    if not group_mod.is_leader(character):
        character.session.send(
            "Only the group leader can set focus. Type group to see "
            "the current target."
        )
        return
    picks = list(getattr(character, "_group_focus_picks", None) or [])
    if low.isdigit() and picks:
        index = int(low) - 1
        if 0 <= index < len(picks):
            token = picks[index]
    target = game.find_character(token) if game is not None else None
    if target is None:
        character.session.send(f"No one here named {token!r}.")
        return
    if group_mod.same_group(character, target):
        character.session.send(
            f"{getattr(target, 'key', token)} is in your group -- "
            "pick a foe, not a mate."
        )
        return
    group_mod.set_focus_key(character, getattr(target, "key", token))
    face = getattr(target, "key", token)
    character.session.send(
        f"Group focus is {face}. NPC and Echo mates attack them "
        "when you share a room."
    )
    from engine import hooks as hooks_mod

    try:
        hooks_mod.after_group_focus_change(game)
    except Exception:
        pass


def _send_group_focus_menu(character, game):
    """Nested focus page: current target + numbered people in the room."""
    from engine import group as group_mod
    from engine import style as style_mod
    from engine import display_prefs as dprefs

    dprefs.ensure_display_defaults(character)
    sr = bool(getattr(character, "screenreader", False))
    width = dprefs.sheet_width(character)
    body = []
    focus_line = group_mod.format_focus_line(character, game)
    body.append(focus_line or "Focus: (none).")
    body.append(
        "Type group focus <name> to set who NPC and Echo mates attack. "
        "group focus clear drops it -- they then attack whoever the "
        "leader is fighting."
    )
    picks = []
    room = getattr(character, "location", None)
    if room is not None:
        for obj in list(room.characters()):
            if obj is character:
                continue
            if group_mod.same_group(character, obj):
                continue
            if float(getattr(obj, "hp", 0) or 0) <= 0:
                continue
            key = getattr(obj, "key", None)
            if not key:
                continue
            picks.append(key)
    character._group_focus_picks = picks
    if picks:
        body.append("")
        body.append("People here")
        for index, key in enumerate(picks, start=1):
            body.append(f"{index}. {key}")
        body.append("Type group focus 1 (or the name).")
    else:
        body.append("No other people in this room to focus.")
    lines = style_mod.format_sheet(
        "Group focus", body, width=width, screenreader=sr,
    )
    character.session.send("\r\n".join(str(line) for line in lines))


def cmd_party(character, args, game):
    """Party invite/accept -- explicit grouping before follow glue.

    ``party`` shows the roster (same follow tree as ``group``) plus any
    pending invite. ``party invite <name>`` asks someone in the room;
    they type ``party accept`` to join. The leader moves the party and
    can pull dungeon dispatches for everyone colocated.
    """
    from engine import group as group_mod
    from engine import party_invite as party_mod

    raw = (args or "").strip().lower()
    if not raw:
        character.session.send(group_mod.format_group_sheet(character, game))
        pending = party_mod.format_pending_line(character)
        if pending:
            character.session.send(pending)
        character.session.send(
            "Invite: party invite <name>  |  Accept: party accept  |  "
            "Leave: party leave confirm  (see 'help party')"
        )
        return
    parts = raw.split()
    verb = parts[0]
    if verb == "invite":
        if len(parts) < 2:
            character.session.send(
                "Usage: party invite <name>  -- ask someone in the room "
                "(see 'help party')."
            )
            return
        name = " ".join(parts[1:]).strip()
        target = party_mod.resolve_invite_target(character, name)
        if target is None:
            character.session.send(f"No one here named {name!r}.")
            return
        ok, msg = party_mod.invite(character, target, game)
        character.session.send(msg)
        if (
            ok
            and party_mod.invite_pending_accept(target)
            and getattr(target, "session", None) is not None
        ):
            target.session.send(
                f"{character.key} invites you to a party. "
                "Type 'party accept' or 'party decline'."
            )
        return
    if verb in ("accept", "join"):
        ok, msg = party_mod.accept(character, game)
        character.session.send(msg)
        return
    if verb in ("decline", "refuse"):
        ok, msg = party_mod.decline(character, game)
        character.session.send(msg)
        return
    if verb in ("leave", "split", "peel"):
        confirm = "confirm" in parts
        result = group_mod.try_leave_group(character, game, confirm=confirm)
        if result == "ok":
            character.session.send("You are not in a party.")
        return
    if verb in ("disband", "break"):
        confirm = "confirm" in parts
        result = group_mod.try_disband_group(character, game, confirm=confirm)
        if result == "ok":
            character.session.send("You are not in a party.")
        return
    character.session.send(
        "Usage: party  |  party invite <name>  |  party accept  |  "
        "party decline  |  party leave confirm\r\n"
        "(Roster rows: group front / group back -- see 'help group'.)"
    )


def _stop_following(character, silent=False):
    """Compat wrapper -- prefer stop_following from command_support."""
    from engine.command_support import stop_following
    stop_following(character, silent=silent)


def _transition_presence_line(template, watcher, presence, game):
    """Format a leave/arrive template with viewer-relative presence face."""
    if not template:
        return ""
    if "{name}" not in template:
        return template
    from engine import hooks
    from engine.command_support import _presence_face

    fallback = _presence_face(presence)
    try:
        face = hooks.presence_face_for(watcher, presence)
    except Exception:
        face = None
    return template.format(name=face or fallback)


def _do_transition(character, dest, game, leave_text, arrive_text):
    """Shared leave/arrive/look/encounter for enter, exit, in, out."""
    from engine import hooks
    from engine import group as group_mod
    if group_mod.block_live_group_move(character):
        return False
    if getattr(character, "asleep", False) and not (
        getattr(character, "is_sleep_dream_clone", False)
        or (
            getattr(character, "sleep_dream_tour", False)
            and not getattr(character, "sleep_dream_clone_key", None)
        )
    ):
        send_if_online(
            character,
            "You're asleep -- type 'wake' before you can move.",
        )
        return False
    hooks.cancel_rest(character)
    room = require_here(character)
    if room is None:
        return False
    # Game hook may spill barred actors off no_loiter hubs (Central Plaza).
    dest = hooks.transition_dest(character, dest, game)
    if isinstance(dest, str):
        from engine import room_vnum as room_vnum_mod

        key = dest
        dest = room_vnum_mod.lookup_room(game, key)
    if dest is None:
        send_if_online(character, "You can't go that way.")
        return False
    block_message = hooks.move_gate_block(character, room, dest, game)
    if block_message:
        send_if_online(character, block_message)
        return False
    presence = hooks.move_presence_actor(character, game)
    # True-invis staff presence -- same stealth as compass leave/arrive.
    stealth = is_staff_stealth_presence(character)
    if not stealth:
        room.broadcast(
            lambda watcher: _transition_presence_line(
                leave_text, watcher, presence, game,
            ),
            exclude=character,
        )
    character.move_to(dest)
    if not stealth:
        dest.broadcast(
            lambda watcher: _transition_presence_line(
                arrive_text, watcher, presence, game,
            ),
            exclude=character,
        )
    # Compass ``cmd_move`` already pulls the follow tree. enter / exit /
    # in / out used to leave mates behind at the mouth (castle stairs,
    # portal, taxi curb). Pull them to the same dest.
    _pull_followers_to(character, room, dest, game, direction="in")
    # Cadence ``npc_do`` attaches SilentSession. Auto-look + encounter on
    # every pocket enter/exit froze Azure ashen-prey ``exit`` / ``enter
    # waystation`` at ~3.5s (billed as ashen_prey_ms). Live players still
    # get the room dump; watchers still see leave/arrive broadcasts.
    from engine.npc_act import is_live_session
    if is_live_session(getattr(character, "session", None)):
        cmd_look(character, "", game)
        hooks.encounter_check(game, dest)
    return True


def _player_place_label(game, key_or_room, *, fallback="somewhere"):
    """ROOM NAME for player prose from a hub key or Room (never bare VNUM/dig key).

    Zone-exit hints and ``exit`` errors store ``zone_entry_hub_key`` as the
    graph id (``PM00002``, legacy ``Main Street S9``, …). Players must only
    see authored titles.
    """
    from engine import room_vnum as room_vnum_mod

    if key_or_room is not None and not isinstance(key_or_room, str):
        label = room_vnum_mod.describe_room(key_or_room, staff=False)
        if label and label != "?":
            return label
        key_or_room = room_vnum_mod.internal_room_key(key_or_room)
    hub = room_vnum_mod.lookup_room(game, key_or_room)
    if hub is not None:
        label = room_vnum_mod.describe_room(hub, staff=False)
        if label and label != "?":
            return label
    return room_vnum_mod.describe_room_key(
        game, key_or_room, staff=False, fallback=fallback,
    )


def stamp_zone_entry(character, hub_room):
    """Remember which pocket mouth this character entered through.

    ``exit`` only works from that hub -- sewers / side streets cannot
    teleport you back onto the grid. Cleared on a successful exit.
    """
    if character is None or hub_room is None:
        return
    character.zone_entry_hub_key = getattr(hub_room, "key", None)


def clear_zone_entry(character):
    """Drop the pocket-entry stamp after leaving (or on heal)."""
    if character is None:
        return
    character.zone_entry_hub_key = None


def revalidate_zone_entry_stamp(character, game):
    """Drop a pocket-entry stamp when the hub lives in another zone (bug #54)."""
    stamped = getattr(character, "zone_entry_hub_key", None)
    if not stamped:
        return
    room = getattr(character, "location", None)
    if room is None:
        clear_zone_entry(character)
        return
    from engine.room_vnum import lookup_room

    hub = lookup_room(game, stamped)
    if hub is None:
        clear_zone_entry(character)
        return
    hub_zone = getattr(hub, "zone", None)
    cur_zone = getattr(room, "zone", None)
    if hub_zone and cur_zone and hub_zone != cur_zone:
        clear_zone_entry(character)
        return
    # Bug 978: hub drives stamped municipal decks (PM00002) instead of the
    # atlas zone mouth (SY00001). Remap on look so exit works after the fix.
    if getattr(hub, "zone_exit", False):
        return
    zone = hub_zone or cur_zone
    if not zone:
        return
    from engine.systems import overland as overland_mod

    overland_mod.ensure_game_overland(game)
    atlas = getattr(game, "overland_atlas", None)
    if atlas is None:
        return
    for landmark in atlas.landmarks.values():
        mouth_key = landmark.get("hub_room")
        mouth = lookup_room(game, mouth_key) if mouth_key else None
        if (
            mouth is not None
            and getattr(mouth, "zone", None) == zone
            and getattr(mouth, "zone_exit", False)
        ):
            character.zone_entry_hub_key = mouth.key
            return


def can_exit_zone_here(character, room):
    """True when ``exit`` is legal from ``room`` for this character.

    Requires a pocket mouth (``zone_exit`` + ``zone_exit_to`` and/or
    overland macro). If the character stamped an entry hub, they must
    stand on that exact room -- not another mouth in the same zone.
    """
    if character is None or room is None:
        return False
    if not getattr(room, "zone_exit", False):
        return False
    has_classic = getattr(room, "zone_exit_to", None) is not None
    has_overland = getattr(room, "overland_exit_macro", None) is not None
    if not has_classic and not has_overland:
        return False
    stamped = getattr(character, "zone_entry_hub_key", None)
    if stamped:
        return room.key == stamped
    return True


def cmd_enter(character, args, game):
    """Enter a pocket zone from an overland gateway: enter <zonename>.

    Zone links live on Room.zone_entries (not exits{}), so this is separate
    from cardinal moves and from nested indoor 'in'. Bare 'enter' lists
    what you can enter from here. Dual-layer wilderness uses landmark
    gates at micro (5,5) via supers.overland.

    Stamps ``zone_entry_hub_key`` so ``exit`` only works from that hub.
    """
    from engine import hooks
    # Dual-layer landmark enter (virtual wilderness at gate center).
    if hooks.try_enter_zone(character, args, game):
        return
    # Boarded on a porch/street: bare enter soft-aliases curb ``in``
    # (home garage redirect when remodeled).
    if getattr(character, "in_vehicle", None):
        if hooks.try_vehicle_enter_as_house_in(character, args, game):
            return
    room = require_here(character)
    if room is None:
        return
    entries = getattr(room, "zone_entries", None) or {}
    entries = hooks.filter_zone_entries(character, room, entries, game)
    raw = (args or "").strip()
    # Prefer this hunter's own stronghold when several hunts share a
    # roadside trailhead (America Overland cell). zone_entries only keeps
    # one public alias pointer.
    dest = hooks.mission_entrance(character, game, room, raw)
    if dest is None and not entries:
        character.session.send(
            "You can't enter a zone from here. "
            "(Nested doors still use 'in'.)"
        )
        return
    if not raw:
        names = sorted(set(entries))
        # Prefer short unique labels for the hint.
        character.session.send(
            "Enter which zone? Try: enter "
            + ", ".join(names[:8])
            + ("..." if len(names) > 8 else "")
        )
        return
    needle = raw.lower()
    # Exact alias first, then substring / startswith.
    if dest is None:
        dest = entries.get(needle)
    if dest is None:
        hits = [
            (alias, hub) for alias, hub in entries.items()
            if needle in alias or alias.startswith(needle)
        ]
        # Dedupe by hub room.
        by_hub = {}
        for alias, hub in hits:
            by_hub.setdefault(hub.key, (alias, hub))
        hits = list(by_hub.values())
        if len(hits) == 1:
            dest = hits[0][1]
        elif len(hits) > 1:
            character.session.send(
                "Which zone? "
                + ", ".join(f"enter {a}" for a, _h in hits)
            )
            return
        else:
            if hooks.try_civic_fixture_enter(character, raw, game):
                return
            character.session.send(
                f"No zone named '{raw}' here. Try bare 'enter' for a list."
            )
            return
    # Epic-run partner gate + non-player dungeon refusal (game rules via hook).
    refuse = hooks.dungeon_entry_refusal(character, dest, game)
    if refuse:
        character.session.send(refuse)
        return
    dest = hooks.zone_enter_dest(character, dest, needle, game)
    # Leaving dual-layer wilderness into a classic zone.
    hooks.clear_overland_coords(character)
    stamp_zone_entry(character, dest)
    from engine import room_vnum as room_vnum_mod
    place = room_vnum_mod.describe_room(dest, staff=False)
    _do_transition(
        character, dest, game,
        f"{{name}} enters {place}.",
        "{name} arrives.",
    )
    # Clear overland coords + soft-stamp dungeon hubs (game hook).
    hooks.after_zone_enter(character, game, dest)


def cmd_exit_zone(character, args, game):
    """Leave a pocket zone back to its overland grid cell: exit.

    Only the pocket mouth you entered through (``zone_entry_hub_key``)
    may exit, and that room must be flagged ``zone_exit``. House
    interiors and side streets do not. Nested indoor returns still use
    ``out`` / ``leave``. Dual-layer America pockets drop you onto
    virtual wilderness at micro (5,5).
    """
    room = character.location
    if room is None:
        return
    if not can_exit_zone_here(character, room):
        stamped = getattr(character, "zone_entry_hub_key", None)
        if stamped and room.key != stamped:
            hub_label = _player_place_label(
                game, stamped, fallback="the zone exit mouth",
            )
            character.session.send(
                f"You entered at {hub_label}. Walk back there, then type "
                f"'exit'. (Indoor returns still use 'out'.)"
            )
        elif getattr(room, "zone", None) and not getattr(room, "zone_exit", False):
            character.session.send(
                "You can only leave the zone from the entry road "
                "(where you arrived when you entered). Walk back there, "
                "then type 'exit'. (Indoor returns still use 'out'.)"
            )
        else:
            character.session.send(
                "There's no zone exit from here. "
                "(Indoor returns still use 'out'.)"
            )
            return
    # Dual-layer America: flagged mouth exits onto virtual overland.
    from engine import hooks
    origin = room
    if hooks.try_exit_zone(character, game):
        dest = character.location
        # ``try_exit_zone`` / ``place_on_overland`` move only the leader.
        # Graph ``zone_exit_to`` uses ``_do_transition`` (which pulls).
        # Without this, grouped mates stay in town and ``live_move_blocked``
        # locks their compass (bug report 1327).
        if dest is not None and dest is not origin:
            _pull_followers_to(
                character, origin, dest, game, direction="out",
            )
            try:
                from engine.systems import overland as overland_mod

                overland_mod.adopt_foot_overland_presence(character, game)
                for mate in list(dest.characters()):
                    if mate is character:
                        continue
                    overland_mod.adopt_foot_overland_presence(mate, game)
                    clear_zone_entry(mate)
            except Exception:
                pass
        clear_zone_entry(character)
        return
    dest = getattr(room, "zone_exit_to", None)
    if dest is None:
        character.session.send(
            "There's no zone exit from here. "
            "(Indoor returns still use 'out'.)"
        )
        return
    clear_zone_entry(character)
    _do_transition(
        character, dest, game,
        "{name} exits to the overland.",
        "{name} arrives.",
    )


def heal_nested_enter_exits(game):
    """Migrate legacy exits['enter'] nested mouths to exits['in'].

    Look lists exit directions literally; ``in`` is the player verb for
    nested indoor travel (``enter <zone>`` stays pocket-zone travel).
    Idempotent -- safe every boot.
    """
    if game is None:
        return 0
    rooms = getattr(game, "rooms", None) or {}
    touched = 0
    for room in rooms.values():
        exits = getattr(room, "exits", None)
        if not isinstance(exits, dict):
            continue
        enter_dest = exits.get("enter")
        if enter_dest is None:
            continue
        if exits.get("in") is None:
            exits["in"] = enter_dest
            touched += 1
        exits.pop("enter", None)
    return touched


def cmd_go_in(character, args, game):
    """Nested indoor enter via exits['in'] (gym annex, chapel sacristy, …).

    Separate from zone travel (`enter <zonename>`). While aboard, steer
    using the curb room's ``in`` (home porch → remodeled garage when set).
    """
    # Boarded: ``in`` is a COMMAND so it never becomes a curb walk via
    # resolve_walk_direction -- route through town-drive instead.
    if getattr(character, "in_vehicle", None):
        from engine import hooks
        if hooks.try_vehicle_nested_in_out(character, game, direction="in"):
            return
    room = require_here(character)
    if room is None:
        return
    # Nested mouths use exits['in'] (porch → living). Realm-town templates
    # historically wired parent_exit='enter' -- accept that alias too.
    dest = room.exits.get("in") or room.exits.get("enter")
    if not dest:
        from engine import hooks
        if hooks.ensure_homestead_plot_graph_for_actor(character, game):
            dest = room.exits.get("in") or room.exits.get("enter")
    if not dest:
        character.session.send("You can't go in from here.")
        return
    _do_transition(
        character, dest, game,
        "{name} goes in.",
        "{name} arrives.",
    )


def cmd_go_out(character, args, game):
    """Nested indoor leave via exits['out']. Separate from zone `exit`."""
    if getattr(character, "in_vehicle", None):
        from engine import hooks
        if hooks.try_vehicle_nested_in_out(character, game, direction="out"):
            return
    room = require_here(character)
    if room is None:
        return
    dest = room.exits.get("out")
    if not dest:
        from engine import hooks
        if hooks.ensure_homestead_plot_graph_for_actor(character, game):
            dest = room.exits.get("out")
    if not dest:
        character.session.send("There's no way out from here.")
        return
    _do_transition(
        character, dest, game,
        "{name} goes out.",
        "{name} arrives.",
    )

def cmd_say(character, args, game):
    """Speak to the room. Prefs #24: trailing ? / ! pick asks / exclaims.

    D58: optional leading whisper / shout / drawl (``say whisper …``).
    Bare ``say`` replays recent speech in this room (last 20 lines).
    """
    from engine import channels

    if not args or not args.strip():
        channels.replay_room_say(character, game)
        return
    from engine import hooks
    tone, message = hooks.say_strip_tone_prefix(args)
    if not message and tone:
        character.session.send("Say what?")
        return
    if not message:
        character.session.send("Say what?")
        return
    from engine import display_prefs
    you_verb, they_verb = display_prefs.say_speech_verb(message)
    level = hooks.say_drunk_meter(character)
    spoken = hooks.say_slur_text(message, level)
    tag = hooks.say_drunk_tag(level)
    stumble = hooks.say_maybe_stumble_tell(character, level)
    if stumble:
        character.session.send(stumble)
    voice_phrase = hooks.say_voice_mod(character)
    # Possession exile: speech stays in the personal realm pocket.
    speak_room = character.location
    if speak_room is None and not hooks.is_consciousness_exile(character):
        send_if_online(character, "You're nowhere.")
        return
    if hooks.is_consciousness_exile(character):
        sensory = hooks.consciousness_sensory_room(character)
        if sensory is not None:
            speak_room = sensory
        character.session.send(
            f'You {you_verb} into the afterlife pocket, "{spoken}"'
        )
        character.session.send("")
        # Only other minds in the same pocket hear (rare guests).
        if speak_room is not None:
            from world import Character as CharType
            for obj in list(getattr(speak_room, "contents", []) or []):
                if not isinstance(obj, CharType) or obj is character:
                    continue
                session = getattr(obj, "session", None)
                if session is None:
                    continue
                face = _display_name(character, viewer=obj)
                from engine import display_prefs
                line = display_prefs.format_say_chat(
                    obj,
                    face,
                    spoken,
                    you=False,
                    you_verb=you_verb,
                    they_verb=they_verb,
                    tone=tone,
                    voice_phrase=voice_phrase,
                )
                session.send(line)
                session.send("")
        return
    # First-person line for the speaker; third-person for the room.
    hooks.deliver_say(
        character,
        spoken,
        game,
        you_verb=you_verb,
        they_verb=they_verb,
        speak_room=character.location,
        tone=tone,
        drunk_tag=tag,
        voice_phrase=voice_phrase,
    )


def cmd_emote(character, args, game):
    """Free-form third-person action text.

    Prefs #25: ``emote 's eyes glow.`` becomes ``Name's eyes glow.``
    D57: ``@name`` and ``$me`` resolve per viewer (``emote pats @erin``).
    Also: ``@name's``, ``@name/subj|obj|poss``, ``$subj``, ``$obj``, ``$poss``.
    """
    text = (args or "").strip()
    if not text:
        from engine import channels
        channels.replay_room_emote(character, game)
        return
    from engine import rp_emote
    rp_emote.broadcast_emote(character, text, game, mode="emote")


def cmd_smote(character, args, game):
    """Third-person action with auto subject (``smote grins`` / ``:grins``).

    Same tokens as ``emote``. Strips redundant ``I …`` if you type it anyway.
    Colon shorthand: ``:waves at @bob.``
    """
    text = (args or "").strip()
    if not text:
        from engine import channels
        channels.replay_room_emote(character, game)
        return
    from engine import rp_emote
    rp_emote.broadcast_emote(character, text, game, mode="smote")


def _tell_account_label(game, character):
    """Account name for tell -- used when the sender addressed an account."""
    from engine.accounts import account_for_character

    account = account_for_character(game, character)
    if account is not None:
        name = (getattr(account, "name", None) or "").strip()
        if name:
            return name
    raw = (getattr(character, "account", None) or "").strip()
    return raw or None


def _tell_character_label(character):
    """Character name for tell -- used when the sender addressed a body.

    GM spirit sessions are ``gmspirit:Ash`` internally; players type Ash.
    """
    key = (getattr(character, "key", None) or "").strip()
    if not key:
        return None
    if ":" in key:
        prefix, rest = key.split(":", 1)
        if prefix.lower() in ("gmspirit", "gm") and rest.strip():
            return rest.strip()
    return key


def _tell_account_names(game, character):
    """Lowercased account labels that reach this connected Session."""
    names = []
    seen = set()
    label = _tell_account_label(game, character)
    raw = (getattr(character, "account", None) or "").strip()
    for name in (label, raw):
        if not name:
            continue
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        names.append(name)
    return names


def _tell_character_names(character):
    """Names players can type to hit this body (key, plus GM spirit short)."""
    names = []
    seen = set()
    key = (getattr(character, "key", None) or "").strip()
    short = _tell_character_label(character)
    for name in (key, short):
        if not name:
            continue
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        names.append(name)
    return names


def _tell_storage_key(character):
    """Body label ``tell`` / ``reply`` resolve -- never ``gmspirit:`` keys."""
    label = _tell_character_label(character)
    if label:
        return label
    from command_support import strip_ephemeral_storage_prefix

    peeled = strip_ephemeral_storage_prefix(
        getattr(character, "key", None) or ""
    )
    text = (peeled or "").strip()
    return text or None


def _tell_incoming_speaker_label(viewer, speaker, game):
    """Viewer-relative face on an incoming private tell."""
    face = None
    if viewer is not None and speaker is not None:
        try:
            from engine.hooks import presence_face_for

            face = presence_face_for(viewer, speaker)
        except Exception:
            face = None
    face = (face or "").strip()
    if face and face not in ("?",):
        return face
    storage = _tell_storage_key(speaker)
    if storage:
        return storage
    return "someone"


def _tell_extra_character_labels(game, other, viewer):
    """Additional ``tell`` needles for occupy / cover / public faces."""
    extra = []
    seen = set()
    for name in (
        _presence_face(other),
        _tell_storage_key(other),
    ):
        if not name:
            continue
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        extra.append(name)
    if viewer is not None:
        try:
            from engine.hooks import presence_face_for

            shown = presence_face_for(viewer, other)
        except Exception:
            shown = None
        if shown:
            low = str(shown).lower()
            if low not in seen:
                seen.add(low)
                extra.append(str(shown))
    return extra


def _tell_reply_token(viewer, speaker, game, *, address_kind):
    """Player-typable ``reply`` token that still reaches *speaker*."""
    if address_kind == "account":
        label = _tell_account_label(game, speaker) or "someone"
        return "@" + label
    needles = []
    storage = _tell_storage_key(speaker)
    if storage:
        needles.append(storage)
    shown = _tell_incoming_speaker_label(viewer, speaker, game)
    if shown and shown.lower() != "someone":
        needles.append(shown)
    for name in _tell_extra_character_labels(game, speaker, viewer):
        if name and name not in needles:
            needles.append(name)
    for needle in needles:
        target, _label, kind = _find_connected_tell_target(
            game, needle, viewer=viewer,
        )
        if target is speaker and kind == "character":
            return needle
    return storage or shown or "someone"


def _tell_pick_live(matches):
    """Prefer presence=live when several Sessions match the same name."""
    if not matches:
        return None
    live = [
        c for c in matches
        if str(getattr(c, "presence", "") or "").lower() == "live"
    ]
    return (live or matches)[0]


def _iter_connected_tell_bodies(game):
    """Connected Sessions that can receive a tell."""
    for session in list(getattr(game, "sessions", None) or []):
        other = getattr(session, "character", None)
        if other is None or getattr(other, "session", None) is None:
            continue
        yield other


def _find_connected_tell_target(game, name, viewer=None):
    """Resolve a connected tell target by character or account name.

    Bare ``tell Matt`` prefers an exact character-name match, then an
    exact account match. ``tell @Matt`` is account-only, so a character
    named Matt and a GM account named Matt do not collide.

    Returns ``(target, display_label, address_kind)`` where address_kind
    is ``character`` or ``account``. Display labels follow how the sender
    addressed them -- character names stay character names; account
    names stay account names (never leak the other).
    """
    raw = (name or "").strip()
    if not raw or game is None:
        return None, None, None
    force_account = raw.startswith("@")
    needle = raw[1:].strip() if force_account else raw
    if not needle:
        return None, None, None
    needle_l = needle.lower()

    char_exact = []
    acct_exact = []
    for other in _iter_connected_tell_bodies(game):
        char_labels = [n.lower() for n in _tell_character_names(other)]
        for extra in _tell_extra_character_labels(game, other, viewer):
            low = extra.lower()
            if low and low not in char_labels:
                char_labels.append(low)
        if viewer is not None:
            try:
                from engine.hooks import presence_face_for

                mask = presence_face_for(viewer, other)
            except Exception:
                mask = None
            true_key = (getattr(other, "key", None) or "").lower()
            if (
                getattr(other, "deep_cover_active", False)
                and mask
                and true_key
                and true_key not in str(mask).lower()
            ):
                # Cover hides the true name from public tell lookup.
                # Reply still reaches them via last_tell_from (the mask)
                # or last_tell_from_key (privileged storage match below).
                char_labels = [str(mask).lower()]
        acct_labels = [n.lower() for n in _tell_account_names(game, other)]
        if needle_l in char_labels and other not in char_exact:
            char_exact.append(other)
        if needle_l in acct_labels and other not in acct_exact:
            acct_exact.append(other)

    if force_account:
        target = _tell_pick_live(acct_exact)
        if target is None:
            return None, None, None
        label = _tell_account_label(game, target) or needle
        return target, label, "account"

    if char_exact:
        target = _tell_pick_live(char_exact)
        label = _tell_character_label(target) or needle
        return target, label, "character"
    if acct_exact:
        target = _tell_pick_live(acct_exact)
        label = _tell_account_label(game, target) or needle
        return target, label, "account"
    return None, None, None


def _whisper_ic_peer(character, game, name):
    """Return (target, where) for an in-character whisper, or (None, None).

    ``where`` is ``room`` (same room, bystanders hear a no-text line) or
    ``group`` (follow party, even in another room). Remote, ungrouped
    whispers stay OOC tell (bug reports 1296 / 1301).
    """
    from world import Character as CharType

    needle = (name or "").strip()
    if not needle or needle.startswith("@"):
        return None, None
    room = getattr(character, "location", None)
    if room is not None:
        occupants = [
            obj for obj in room.contents
            if isinstance(obj, CharType) and obj is not character
        ]
        here = _find_character(needle, occupants, self_character=character)
        if here is not None and here is not character:
            return here, "room"
    from engine import group as group_mod

    mates = [
        mate for mate in group_mod.group_members(character)
        if mate is not None and mate is not character
    ]
    mate = _find_character(needle, mates, self_character=character)
    if mate is not None and mate is not character:
        return mate, "group"
    return None, None


def _deliver_ic_whisper(character, target, message, game, *, where):
    """Send IC whisper lines; room bystanders get no message text."""
    from engine import display_prefs as display_prefs_mod
    from engine import gmcp

    speaker_label_for = lambda viewer: _display_name(character, viewer=viewer)
    peer_label_for = lambda viewer: _display_name(target, viewer=viewer)
    speaker_self = speaker_label_for(character)
    peer_self = peer_label_for(character)
    outgoing = display_prefs_mod.format_ic_whisper_chat(
        character, outgoing=True, peer_face=peer_self, message=message,
    )
    incoming = display_prefs_mod.format_ic_whisper_chat(
        target, outgoing=False, peer_face=speaker_self, message=message,
    )
    outgoing_line = display_prefs_mod.paint_channel_line(
        character, "whisper", outgoing, default="whisper",
    )
    incoming_line = display_prefs_mod.paint_channel_line(
        target, "whisper", incoming, default="whisper",
    )
    speaker_session = getattr(character, "session", None)
    target_session = getattr(target, "session", None)
    if speaker_session is not None:
        gmcp.deliver_comm(
            speaker_session, "whisper", message, speaker_self, outgoing_line,
        )
    else:
        send_if_online(character, outgoing)
    if target_session is not None:
        gmcp.deliver_comm(
            target_session, "whisper", message, speaker_self, incoming_line,
        )
        target_session.last_tell_from = _tell_reply_token(
            target, character, game, address_kind="character",
        )
        target_session.last_tell_from_key = _tell_storage_key(character)
    else:
        send_if_online(target, incoming)
    if where == "room":
        def _bystander(watcher):
            face = speaker_label_for(watcher)
            peer = peer_label_for(watcher)
            return f"{face} whispers to {peer}."

        broadcast_here(character, _bystander, exclude=(character, target))
    try:
        from engine import rp_transcript as transcript_mod
        transcript_mod.capture(character, outgoing)
        transcript_mod.capture(target, incoming)
    except Exception:
        pass


def cmd_whisper(character, args, game):
    """IC whisper when close or grouped; otherwise OOC like tell.

    Same room or same follow-group: private IC (bystanders in the room
    see that you whispered, not the words). Remote and not grouped:
    the OOC tell channel printed as whisper (bug reports 1296 / 1301).
    ``say whisper …`` is a different, room-wide tone.
    """
    if not args or not args.strip():
        character.session.send(
            "Whisper whom what? Use a character name in the room or "
            "your group, or a connected name for OOC whisper."
        )
        return
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        character.session.send(
            "Whisper whom what? Use a character name in the room or "
            "your group, or a connected name for OOC whisper."
        )
        return
    name, message = parts
    target, where = _whisper_ic_peer(character, game, name)
    if target is not None and where:
        if getattr(target, "session", None) is None:
            send_if_online(
                character,
                "No one by that name is available.",
            )
            return
        _deliver_ic_whisper(character, target, message, game, where=where)
        return
    cmd_tell(character, args, game, voice="whisper")


def cmd_tell(character, args, game, *, voice="tell"):
    """Private OOC message to a connected character or account.

    ``tell Ayla hello`` names the character on both sides. ``tell Andri``
    or ``tell @Andri`` names the account and does not show which body
    they are playing. ``@`` is the disambiguator when a character and an
    account share a name. Works through sleep. Bare ``tell`` replays
    the last 20 tells. ``whisper`` uses this same path with whisper verbs.

    Offline Echoes cannot receive it. Misses use the same line as an
    unknown name so tell cannot census who is online.
    """
    from engine import channels

    if not args or not args.strip():
        channels.replay_tells(character, game)
        return

    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        if str(voice or "").strip().lower() == "whisper":
            character.session.send(
                "Whisper whom what? Use a character name, an account name, "
                "or @account."
            )
        else:
            character.session.send(
                "Tell whom what? Use a character name, an account name, "
                "or @account."
            )
        return
    name, message = parts

    target, peer_label, kind = _find_connected_tell_target(
        game, name, viewer=character,
    )
    if not target or target.session is None or not peer_label or not kind:
        character.session.send("No one by that name is available.")
        return
    _deliver_resolved_tell(
        character, target, message, game, kind=kind, voice=voice,
        peer_label=peer_label,
    )


def _deliver_resolved_tell(
    character, target, message, game, *, kind, voice="tell", peer_label=None,
):
    """Send a private tell to an already-resolved connected body."""
    from engine import channels
    from engine import display_prefs as display_prefs_mod
    from engine import gmcp

    if target is None or target.session is None:
        return
    if not peer_label:
        if kind == "account":
            peer_label = _tell_account_label(game, target) or "someone"
        else:
            peer_label = (
                _tell_character_label(target)
                or _tell_storage_key(target)
                or "someone"
            )
    if kind == "account":
        speaker_label = _tell_account_label(game, character) or "someone"
        reply_token = "@" + speaker_label
        target.session.last_tell_from_key = None
    else:
        speaker_label = _tell_incoming_speaker_label(target, character, game)
        reply_token = _tell_reply_token(
            target, character, game, address_kind="character",
        )
        target.session.last_tell_from_key = _tell_storage_key(character)
    channel = "whisper" if str(voice or "").strip().lower() == "whisper" else "tell"
    incoming = display_prefs_mod.format_tell_chat(
        target, outgoing=False, peer_face=speaker_label, message=message,
        voice=channel,
    )
    outgoing = display_prefs_mod.format_tell_chat(
        character, outgoing=True, peer_face=peer_label, message=message,
        voice=channel,
    )
    incoming_line = display_prefs_mod.paint_channel_line(
        target, channel, incoming, default=channel,
    )
    outgoing_line = display_prefs_mod.paint_channel_line(
        character, channel, outgoing, default=channel,
    )
    if display_prefs_mod.wants_airy_spacing(target):
        gmcp.deliver_comm(
            target.session, channel, message, speaker_label, incoming_line, "",
        )
    else:
        gmcp.deliver_comm(
            target.session, channel, message, speaker_label, incoming_line,
        )
    gmcp.deliver_comm(
        character.session, channel, message, speaker_label, outgoing_line,
    )
    target.session.last_tell_from = reply_token
    channels.append_tell(
        character.session,
        {"kind": "out", "peer": peer_label, "message": message},
        game=game,
    )
    channels.append_tell(
        target.session,
        {"kind": "in", "peer": speaker_label, "message": message},
        game=game,
    )
    try:
        from engine import rp_transcript as transcript_mod
        transcript_mod.capture(character, outgoing)
        transcript_mod.capture(target, incoming)
    except Exception:
        pass


def cmd_ooctell(character, args, game):
    """Retired alias -- ooctell is tell (account-name OOC).

    Bare ``ooctell`` still replays the old OOC-tell ring if one exists,
    then the shared tell history.
    """
    from engine import channels

    if not args or not args.strip():
        channels.replay_ooctells(character, game)
        channels.replay_tells(character, game)
        return
    cmd_tell(character, args, game)


def cmd_reply(character, args, game):
    """Private reply to whoever last told this Session (``reply <message>``)."""
    message = (args or "").strip()
    if not message:
        character.session.send("Reply what?")
        return
    session = character.session
    last_from = getattr(session, "last_tell_from", None) or getattr(
        session, "last_ooctell_from", None
    )
    last_key = getattr(session, "last_tell_from_key", None)
    if not last_from and not last_key:
        character.session.send("You have no one to reply to.")
        return
    # Prefer the stamped body key (occupy / cover) so reply does not
    # re-parse a public face that tell lookup cannot resolve.
    if last_key:
        want = str(last_key).lower()
        for other in _iter_connected_tell_bodies(game):
            stored = (_tell_storage_key(other) or "").lower()
            if stored == want and other.session is not None:
                _deliver_resolved_tell(
                    character, other, message, game, kind="character",
                )
                return
    if last_from:
        target, _label, kind = _find_connected_tell_target(
            game, last_from, viewer=character,
        )
        if target is not None and target.session is not None and kind:
            _deliver_resolved_tell(
                character, target, message, game, kind=kind,
            )
            return
    character.session.send("You have no one to reply to.")


def _ooc_speaker_face(character, game):
    """OOC channel speaker label (feature D).

    When the speaker's Account prefers ``ooc_identity=account``, use the
    occupying or linked account display name (assigned-cast occupy
    included). Otherwise the character presence face. Account
    names are allowed on OOC (feature E).
    """
    from engine import ooc_channel

    return ooc_channel.speaker_face_for_character(character, game)


def cmd_osay(character, args, game):
    """Room-only out-of-character speech (local OOC).

    Usage:
      osay <message>   speak OOC to everyone in this room only

    Global ``ooc`` still reaches every connected player. ``osay`` uses the
    same ``((OOC)) [Name]:`` chrome and ``ooc`` channel color role so the
    line never relies on color alone. Bare ``osay`` prints usage -- it does
    not replay history (use bare ``say`` / ``ooc`` for room/global replay).
    """
    from engine import display_prefs
    from engine import ooc_channel

    display_prefs.ensure_display_defaults(character)
    session = getattr(character, "session", None)
    if session is None:
        return

    if not args or not args.strip():
        session.send(
            "Usage: osay <message>  (room OOC to everyone here; "
            "see 'help osay')"
        )
        return

    room = character.location
    if room is None:
        session.send("You're nowhere.")
        return

    message = args.strip()
    face = ooc_channel.speaker_face_for_character(character, game)
    from world import Character as CharType

    # Only connected players in this room hear osay (Echoes offline skip).
    for obj in list(getattr(room, "contents", []) or []):
        if not isinstance(obj, CharType):
            continue
        viewer_session = getattr(obj, "session", None)
        if viewer_session is None:
            continue
        line = ooc_channel.render_ooc_line(obj, face, message)
        viewer_session.send(line)
        viewer_session.send("")


def cmd_ooc(character, args, game):
    """Global out-of-character chat to every connected Session.

    Usage:
      ooc <message>   speak on the global OOC channel
      ooc             show the last 20 OOC lines, numbered
      replay ooc 12   replay one numbered line

    Prefs #23 / #26: whole-line bright aqua (role ``ooc``) unless the
    player set channel_colors['ooc'] to another role. Same plain text for
    everyone::

        ((OOC)) [Name]: message text

    Offline Echoes have no Session and do not receive OOC. The history
    buffer lives on ``game.ooc_history`` and is saved in meta on every
    ``game.save()`` (so copyover / restart keep the last 20 lines). Still
    a short ring — not a forever chat log. Staff mining also appends
    ``ooc.log`` beside the save DB.
    """
    from engine import display_prefs
    from engine import channels
    from engine import ooc_channel
    display_prefs.ensure_display_defaults(character)

    # Bare `ooc` -- replay the global ring buffer instead of usage nag.
    if not args or not args.strip():
        channels.replay_global(character, game, "ooc")
        return

    message = args.strip()
    from engine import ooc_channel

    # Record + broadcast before the speaker's later bare `ooc` replay.
    ooc_channel.broadcast_ooc(
        game, character, message, speaker_session=character.session,
    )


def cmd_questions(character, args, game):
    """Global player-help questions channel (OOC-adjacent).

    Usage:
      question <message>    ask everyone online (questions is the same verb)
      questions             replay the last 20 lines, numbered
      replay questions 12   replay one numbered line

  Use this for how-do-I-play tutoring. For code bugs use bug; for ideas use
  suggest; for async tickets when nobody is online use query (help query).
    """
    from engine import channels
    from engine import display_prefs
    from engine import questions_channel

    display_prefs.ensure_display_defaults(character)
    if not args or not args.strip():
        channels.replay_global(character, game, "questions")
        return
    message = args.strip()
    questions_channel.broadcast_questions(
        game, character, message, speaker_session=character.session,
    )


def cmd_answers(character, args, game):
    """Helper-only answers net (staff hear too).

    Usage:
      answers <message>   speak on the helper channel
      answers             replay recent helper lines
    """
    from engine import channels

    spec = channels.get_channel("answers")
    session = getattr(character, "session", None)
    if spec is None or session is None:
        return
    if not channels.can_speak(character, spec, game):
        session.send("You cannot speak on the answers channel.")
        return
    text = (args or "").strip()
    if not text:
        channels.replay_global(character, game, "answers")
        return
    from engine import answers_channel

    answers_channel.broadcast_answers(
        game, character, text, speaker_session=session,
    )


def cmd_replay(character, args, game):
    """Replay one numbered global-channel line without speaking.

    Usage:
      replay ooc 12
      replay questions 12
      replay ooc            same numbered dump as bare ooc
    """
    from engine import channels

    session = getattr(character, "session", None)
    if session is None:
        return
    text = (args or "").strip()
    if not text:
        session.send("Usage: replay ooc 12  or  replay questions 12")
        return
    parts = text.split(None, 1)
    token = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    spec = channels.resolve_global_channel(token)
    if spec is None:
        session.send(
            f"No channel named '{token}'. Try replay ooc 12 or replay questions 12."
        )
        return
    index = channels.parse_replay_index(rest) if rest else None
    if rest and index is None:
        session.send("Usage: replay ooc 12  or  replay questions 12")
        return
    channels.replay_global(character, game, spec.name, index=index)


def cmd_who(character, args, game):
    """Bare-engine who list: just who's online and where, nothing more.

    This is deliberately the LEAN stub. The Wrought Iron & Ash version with
    Origin/Path badges and the World Tide Good/Evil meter is almost entirely
    SUPERS game content, so it moved wholesale to
    `supers/verbs/engine_flavor.py`'s `cmd_who` -- commands.py merges
    `SUPERS_COMMANDS` over `ENGINE_COMMANDS`, so that richer version is what
    actually runs whenever SUPERS is installed. This stub only exists so a
    bare engine (no game) still has a working `who` (two-repo purity
    Phase 2 -- see this module's docstring).
    """
    # Mirror SUPERS who: staff in true-invis GM form count as the spirit
    # for other staff (players never see them). Left-behind bodies are
    # Echoes, not a second live line.
    online = []
    seen = set()
    viewer_is_gm = getattr(character, "gm_rank", None) in ("gm", "head_gm")
    for session in list(game.sessions):
        other = getattr(session, "character", None)
        if other is None:
            continue
        if (
            getattr(other, "gm_spirit", False)
            and not viewer_is_gm
            and getattr(other, "wizinvis", True)
        ):
            continue
        marker = id(other)
        if marker in seen:
            continue
        seen.add(marker)
        online.append(other)
    if not online:
        character.session.send("No one is online.")
        return
    from engine.command_support import _display_name, _presence_face
    # Staff form shows Wits(GM); everyone else uses the public face.
    labels = []
    for c in online:
        if getattr(c, "gm_spirit", False) or getattr(c, "gm_mode", False):
            labels.append(_display_name(c))
        else:
            labels.append(_presence_face(c))
    names = ", ".join(sorted(labels))
    character.session.send(f"Online ({len(online)}): {names}")


def cmd_brief(character, args, game):
    """brief [on|off] -- skip room clutter after moves (classic MUD brief).

    Bare ``brief`` toggles. Same pref as ``config brief``. Explicit
    ``look`` always shows the full description; auto-look after a walk
    skips prose, weather sky lines, and [TRAIL] tracking when brief is on.
    """
    from engine import display_prefs
    display_prefs.ensure_display_defaults(character)
    choice = (args or "").strip().lower()
    if choice in ("status", "?"):
        state = "on" if character.brief else "off"
        character.session.send(
            f"Brief is {state}. Usage: brief [on|off] "
            "(or config brief on|off)"
        )
        return
    if not choice:
        character.brief = not character.brief
    elif choice in ("on", "yes", "true", "1"):
        character.brief = True
    elif choice in ("off", "no", "false", "0"):
        character.brief = False
    else:
        character.session.send("Usage: brief [on|off]")
        return
    if character.brief:
        character.session.send(
            "Brief on -- after you move, room prose, weather lines, and "
            "trail clutter are skipped; type look for the full room."
        )
    else:
        character.session.send(
            "Brief off -- full room descriptions after each move."
        )


def cmd_color(character, args, game):
    """color [on|off|status] -- show or set ANSI color preference (#51).

    Bare `color` toggles. `color status` / `?` reports without changing.
    Display-only: gothic palette stays optional decoration; every colored
    string still carries a plain-text label (section 8 a11y). Session.send
    strips escapes when use_color is False. See also ``config color 16|256``
    (prefs #5 / #6) and ``help formatting``.
    """
    choice = args.strip().lower()
    if choice in ("status", "?"):
        state = "on" if character.use_color else "off"
        depth = getattr(character, "color_depth", "ansi") or "ansi"
        character.session.send(
            f"Color is {state} (depth {depth}). "
            f"Usage: color [on|off|status]  -- or config color 16|256 "
            f"(see 'help formatting')"
        )
        return
    if not choice:
        # Bare verb flips the preference.
        character.use_color = not character.use_color
        state = "on" if character.use_color else "off"
        character.session.send(
            f"Color {state}"
            + (" (gothic palette)." if character.use_color
               else " (plain text).")
        )
        return
    if choice in ("on", "yes", "true", "1"):
        character.use_color = True
        character.session.send("Color enabled (gothic palette).")
    elif choice in ("off", "no", "false", "0"):
        character.use_color = False
        character.session.send("Color disabled (plain text).")
    else:
        character.session.send("Usage: color [on|off|status]")


def cmd_config(character, args, game):
    """Show or set display / client preferences (prefs hub).

    Bare ``config`` lists category names. ``config <category>`` shows that
    section; ``config full`` dumps every setting. ``config <key> …`` mutates
    a setting (or forwards to the matching short verb).

    Usage::
        config
        config brief|combat|autoloot|display|…
        config full
        config width <40-120>
        config screenreader on|off
        config spacing airy|packed
        config map on|off
        config mapmove on|off
        config brief on|off
        config compact on|off
        config color on|off|16|256
        config combatgag on|off
        config combattags on|off
        config combathints on|off
        config channel ooc <role>
        config prompt|alias|timeformat|whofull|whohide|…
    """
    from engine import display_prefs
    from engine import style
    from engine import hooks
    display_prefs.ensure_display_defaults(character)
    raw = (args or "").strip()
    if not raw:
        character.session.send(
            "\r\n".join(_config_category_index())
        )
        return
    parts = raw.split(None, 2)
    # Trailing colon is a common typo ("config vehicles: compact on").
    key = parts[0].lower().rstrip(":")
    rest = parts[1] if len(parts) > 1 else ""
    if len(parts) > 2:
        rest = parts[1] + " " + parts[2]

    # ``config surname alert on`` -- fold a multi-word snake_case setting
    # before the first token is treated as a category (map / look / …).
    from engine.player_input import fold_catalog_id

    tokens = raw.split()
    for n in (3, 2):
        if len(tokens) < n:
            continue
        folded = fold_catalog_id(" ".join(tokens[:n]))
        if folded in _CONFIG_SNAKE_KEYS:
            key = folded
            rest = " ".join(tokens[n:])
            break

    # Category pages: config combat / config brief / config full / …
    # Mutation args (config brief on) fall through to the setters below.
    category = _config_resolve_category(key)
    rest_l = rest.strip().lower()

    # config reset combat -- chargen defaults for one category (OOC Celtichawk).
    if key in ("reset", "defaults", "default"):
        _config_reset_dispatch(character, rest_l, game)
        return

    # Category pages: config combat / config brief / config full / …
    if category is not None and (
        not rest_l or rest_l in ("list", "status", "?", "show", "menu")
    ):
        character.session.send(
            "\r\n".join(
                _config_status_lines(character, category=category)
            )
        )
        return

    # config compact on|off -- flip all three look-compact prefs at once
    # (items / vehicles / houses). Bare config compact is the brief page.
    if key in ("compact", "lookcompact", "compacts"):
        if not rest_l:
            character.session.send(
                "\r\n".join(
                    _config_status_lines(character, category="brief")
                )
            )
            return
        choice = rest_l.split(None, 1)[0]
        if choice in ("on", "yes", "true", "1"):
            character.compact_floor_items = True
            character.compact_vehicles = True
            character.compact_houses = True
            character.session.send(
                "Look compact on -- floor items, parked vehicles (3+), "
                "and for-sale homes (3+) summarize on look. "
                "Type look items / look vehicles / look houses for full "
                "lists; config items|vehicles|houses compact off for one."
            )
            return
        if choice in ("off", "no", "false", "0"):
            character.compact_floor_items = False
            character.compact_vehicles = False
            character.compact_houses = False
            character.session.send(
                "Look compact off -- look lists floor items, parked "
                "rides, and for-sale homes in full."
            )
            return
        character.session.send(
            "Usage: config compact on|off  "
            "(or config items|vehicles|houses compact on|off)"
        )
        return

    # --- Core display keys handled here ---------------------------------
    if key == "width":
        if not rest:
            character.session.send(
                f"Width is {character.display_width}. "
                f"Usage: config width <{display_prefs.WIDTH_MIN}-"
                f"{display_prefs.WIDTH_MAX}>"
            )
            return
        try:
            w = int(rest.split(None, 1)[0])
        except ValueError:
            character.session.send("Width must be a number.")
            return
        if w < display_prefs.WIDTH_MIN or w > display_prefs.WIDTH_MAX:
            character.session.send(
                f"Width must be {display_prefs.WIDTH_MIN}-"
                f"{display_prefs.WIDTH_MAX}."
            )
            return
        character.display_width = w
        character.session.send(f"Sheet width set to {w}.")
        return
    if key in ("pager", "pagesize", "page"):
        from engine import pager as pager_mod
        if not rest:
            character.session.send(
                f"Pager is {pager_mod.page_size(character)} lines/page. "
                f"Usage: config pager <{pager_mod.PAGE_LINES_MIN}-"
                f"{pager_mod.PAGE_LINES_MAX}>  (see 'help more')"
            )
            return
        try:
            n = int(rest.split(None, 1)[0])
        except ValueError:
            character.session.send("Pager size must be a number.")
            return
        if n < pager_mod.PAGE_LINES_MIN or n > pager_mod.PAGE_LINES_MAX:
            character.session.send(
                f"Pager must be {pager_mod.PAGE_LINES_MIN}-"
                f"{pager_mod.PAGE_LINES_MAX} lines."
            )
            return
        character.pager_lines = n
        character.session.send(
            f"Pager set to {n} lines per page. "
            "Long dumps pause with 'more' / 'stop'."
        )
        return
    if key in ("screenreader", "screen", "a11y", "tts"):
        if not rest:
            state = "on" if character.screenreader else "off"
            character.session.send(
                f"Screenreader is {state}. "
                "Usage: config screenreader on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.session.send(
                display_prefs.apply_screenreader_mode(character, True)
            )
        elif choice in ("off", "no", "false", "0"):
            character.session.send(
                display_prefs.apply_screenreader_mode(character, False)
            )
        else:
            character.session.send("Usage: config screenreader on|off")
        return
    if key in ("traffic", "foottraffic", "npctraffic"):
        if not rest:
            mode = display_prefs.traffic_mode(character)
            character.session.send(
                f"Traffic is {mode}. "
                "Usage: config traffic quiet|normal|verbose "
                "(quiet samples NPC leave/arrive in shops and plazas; "
                "watch room for full feed)"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice == display_prefs.TRAFFIC_QUIET:
            character.traffic_mode = display_prefs.TRAFFIC_QUIET
            display_prefs.mark_pref_manual(character, "traffic_mode")
            character.session.send(
                "Traffic quiet -- NPC foot traffic is sampled in busy "
                "rooms (you hear fewer leave/arrive lines). "
                "Type watch room in a plaza for the full crowd feed."
            )
        elif choice == display_prefs.TRAFFIC_NORMAL:
            character.traffic_mode = display_prefs.TRAFFIC_NORMAL
            display_prefs.mark_pref_manual(character, "traffic_mode")
            character.session.send(
                "Traffic normal -- plaza crowd sampling only; full "
                "leave/arrive prose in shops."
            )
        elif choice == display_prefs.TRAFFIC_VERBOSE:
            character.traffic_mode = display_prefs.TRAFFIC_VERBOSE
            display_prefs.mark_pref_manual(character, "traffic_mode")
            character.session.send(
                "Traffic verbose -- hear every NPC leave and arrive "
                "(watch room still helps in plazas)."
            )
        else:
            character.session.send(
                "Usage: config traffic quiet|normal|verbose"
            )
        return
    if key in ("radio", "angelradio"):
        # Game owns radiant-kit gate -- engine must not import supers.
        handler = hooks.config_handler("radio")
        if handler is not None:
            handler(character, rest, game)
        else:
            character.session.send("Angel Radio prefs are not available.")
        return
    if key in ("spacing", "outputspacing", "paragraph"):
        if not rest:
            mode = display_prefs.normalize_output_spacing(
                getattr(character, "output_spacing", display_prefs.OUTPUT_SPACING_AIRY),
            )
            character.session.send(
                f"Spacing is {mode}. "
                "Usage: config spacing airy|packed"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("airy", "roomy", "spaced", "on", "yes", "true", "1"):
            character.output_spacing = display_prefs.OUTPUT_SPACING_AIRY
            character.session.send(
                "Spacing set to airy -- other people's room events, incoming "
                "tells, and your fight rounds get a blank line between them. "
                "Your own command replies, lists, score, who, and maps "
                "stay packed. Screenreader ignores airy spacing."
            )
        elif choice in (
            "packed", "dense", "compact", "tight", "off", "no", "false", "0",
        ):
            character.output_spacing = display_prefs.OUTPUT_SPACING_PACKED
            character.session.send(
                "Spacing set to packed -- no extra blank lines after room "
                "events, tells, or fight-round events."
            )
        else:
            character.session.send("Usage: config spacing airy|packed")
        return
    if key == "map":
        if not rest:
            state = "on" if character.show_minimap else "off"
            character.session.send(
                f"Map is {state}. Usage: config map on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.show_minimap = True
            display_prefs.mark_pref_manual(character, "show_minimap")
            character.session.send("ASCII minimap enabled.")
        elif choice in ("off", "no", "false", "0"):
            character.show_minimap = False
            display_prefs.mark_pref_manual(character, "show_minimap")
            character.session.send("ASCII minimap disabled.")
        else:
            character.session.send("Usage: config map on|off")
        return
    if key in ("mapmove", "map_on_move", "automap"):
        # Print local map after each move look (when map is on).
        if not rest:
            state = "on" if character.map_on_move else "off"
            character.session.send(
                f"Map-on-move is {state}. Usage: config mapmove on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.map_on_move = True
            display_prefs.mark_pref_manual(character, "map_on_move")
            if not character.show_minimap:
                character.show_minimap = True
                display_prefs.mark_pref_manual(character, "show_minimap")
                character.session.send(
                    "Map-on-move enabled (also turned config map on)."
                )
            else:
                character.session.send(
                    "Map-on-move enabled -- local map prints after each move."
                )
        elif choice in ("off", "no", "false", "0"):
            character.map_on_move = False
            display_prefs.mark_pref_manual(character, "map_on_move")
            character.session.send("Map-on-move disabled.")
        else:
            character.session.send("Usage: config mapmove on|off")
        return
    if key in ("drivemap", "drive_map", "drivemapfull"):
        # Full atlas vs local minimap during vehicle / overland cruise redraw.
        if not rest:
            mode = "atlas" if getattr(character, "drive_map_full", True) else "minimap"
            character.session.send(
                f"Drive map is {mode}. "
                "Usage: config drivemap atlas|minimap "
                "(full grid vs local window while cruising)"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("atlas", "full", "big", "world", "on", "yes", "true", "1"):
            character.drive_map_full = True
            if not character.show_minimap:
                character.show_minimap = True
                character.session.send(
                    "Drive map: atlas camera (also turned config map on)."
                )
            else:
                character.session.send(
                    "Drive map: atlas camera -- map big redraws each cruise step."
                )
        elif choice in (
            "minimap", "mini", "local", "small", "off", "no", "false", "0",
        ):
            character.drive_map_full = False
            character.session.send(
                "Drive map: minimap -- local window redraws each cruise step."
            )
        else:
            character.session.send(
                "Usage: config drivemap atlas|minimap"
            )
        return
    if key in ("mapview", "map_view", "mapviewfull"):
        # Full atlas vs local minimap for bare `map` (not the cruise redraw
        # -- see config drivemap for that).
        if not rest:
            mode = "atlas" if getattr(character, "map_view_full", False) else "minimap"
            character.session.send(
                f"Map view is {mode}. "
                "Usage: config mapview atlas|minimap "
                "(bare 'map' default: full grid vs local window)"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("atlas", "full", "big", "world", "on", "yes", "true", "1"):
            character.map_view_full = True
            if not character.show_minimap:
                character.show_minimap = True
                character.session.send(
                    "Map view: atlas camera (also turned config map on)."
                )
            else:
                character.session.send(
                    "Map view: atlas camera -- bare 'map' now shows a "
                    "screen-sized atlas window (type 'map small' for local)."
                )
        elif choice in (
            "minimap", "mini", "local", "small", "off", "no", "false", "0",
        ):
            character.map_view_full = False
            character.session.send(
                "Map view: minimap -- bare 'map' shows the local window "
                "again (type 'map big' for the atlas camera)."
            )
        else:
            character.session.send(
                "Usage: config mapview atlas|minimap"
            )
        return
    if key in ("mapzone", "map_zone", "mapzones"):
        if not rest:
            state = "on" if getattr(character, "mapzone_overlay", False) else "off"
            character.session.send(
                f"Map zone overlay is {state}. "
                "Usage: config mapzone on|off "
                "(nearby zone bearings under ASCII maps)"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.mapzone_overlay = True
            character.session.send(
                "Map zone overlay on -- nearby zone bearings print under "
                "ASCII maps on overland."
            )
        elif choice in ("off", "no", "false", "0"):
            character.mapzone_overlay = False
            character.session.send("Map zone overlay off.")
        else:
            character.session.send("Usage: config mapzone on|off")
        return
    if key in ("maplook", "map_on_look"):
        # Embed local map inside look (default off -- short classic look).
        if not rest:
            state = "on" if character.map_on_look else "off"
            character.session.send(
                f"Map-on-look is {state}. Usage: config maplook on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.map_on_look = True
            display_prefs.mark_pref_manual(character, "map_on_look")
            if not character.show_minimap:
                character.show_minimap = True
                display_prefs.mark_pref_manual(character, "show_minimap")
                character.session.send(
                    "Map-on-look enabled (also turned config map on)."
                )
            else:
                character.session.send(
                    "Map-on-look enabled -- local map embeds in look."
                )
        elif choice in ("off", "no", "false", "0"):
            character.map_on_look = False
            display_prefs.mark_pref_manual(character, "map_on_look")
            character.session.send("Map-on-look disabled.")
        else:
            character.session.send("Usage: config maplook on|off")
        return
    if key in ("brief", "briefmode", "brief_look"):
        # Skip room prose on auto-look after a move (classic MUD brief).
        if not rest:
            state = "on" if getattr(character, "brief", False) else "off"
            character.session.send(
                f"Brief is {state}. Usage: config brief on|off "
                "(or bare 'brief' to toggle)"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.brief = True
            character.session.send(
                "Brief on -- after you move, room prose, weather lines, and "
                "trail clutter are skipped; type look for the full room."
            )
        elif choice in ("off", "no", "false", "0"):
            character.brief = False
            character.session.send(
                "Brief off -- full room descriptions after each move."
            )
        else:
            character.session.send("Usage: config brief on|off")
        return
    if key in ("emote", "emoteself", "emote_third"):
        # Suggestion report 446: self-view as name vs You.
        from engine import display_prefs as display_prefs_mod

        prefs = display_prefs_mod.preference_character(character, game)
        display_prefs_mod.ensure_display_defaults(prefs)
        tokens = rest.split()
        # ``config emote third on|off`` or ``config emote third``.
        if tokens and tokens[0].lower() in ("third", "name", "self"):
            tokens = tokens[1:]
        if not tokens:
            state = "on" if getattr(prefs, "emote_third", False) else "off"
            character.session.send(
                f"Emote third-person self-view is {state}. "
                "Usage: config emote third on|off"
            )
            return
        choice = tokens[0].lower()
        if choice in ("on", "yes", "true", "1", "third"):
            prefs.emote_third = True
            character.session.send(
                "Emote self-view: third person -- you see your name, "
                "not You (config emote third off to restore)."
            )
        elif choice in ("off", "no", "false", "0", "you"):
            prefs.emote_third = False
            character.session.send(
                "Emote self-view: You … (config emote third on for your name)."
            )
        else:
            character.session.send("Usage: config emote third on|off")
        return
    if key in ("exits", "exitstyle", "exits_verbose"):
        from engine import display_prefs

        prefs = display_prefs.preference_character(character, game)
        display_prefs.ensure_display_defaults(prefs)
        if not rest:
            state = "verbose" if prefs.exits_verbose else "compact"
            character.session.send(
                f"Exits style is {state}. "
                "Usage: config exits compact|verbose"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("compact", "short", "abbrev", "brief", "off", "0"):
            prefs.exits_verbose = False
            prefs.look_exits_rev = 1
            character.session.send(
                "Exits style: compact (Exits: n, e, s)."
            )
        elif choice in ("verbose", "long", "full", "on", "1"):
            prefs.exits_verbose = True
            prefs.look_exits_rev = 1
            character.session.send(
                "Exits style: verbose (Exits: / North - Destination)."
            )
        else:
            character.session.send("Usage: config exits compact|verbose")
        return
    if key == "color":
        # on|off|status -> ANSI toggle; 16|256 -> depth.
        if not rest:
            character.session.send(
                f"Color is {'on' if character.use_color else 'off'} "
                f"(depth {character.color_depth}). "
                "Usage: config color on|off|16|256"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("16", "ansi", "default"):
            character.color_depth = "ansi"
            character.session.send("Color depth: 16-color ANSI.")
            return
        if choice in ("256", "xterm", "xterm256"):
            character.color_depth = "xterm256"
            character.session.send(
                "Color depth: Xterm256 (falls back per-role to ANSI)."
            )
            return
        # on|off|status|toggle -- same as the color verb.
        if choice in ("yes", "true", "1"):
            choice = "on"
        elif choice in ("no", "false", "0"):
            choice = "off"
        if choice in ("on", "off", "status", "?"):
            return cmd_color(character, choice, game)
        if not choice:
            return cmd_color(character, "", game)
        character.session.send(
            "Usage: config color on|off|16|256"
        )
        return
    if key in ("combatgag", "gag", "combat_gag"):
        if not rest:
            state = "on" if character.combat_gag_other else "off"
            character.session.send(
                f"Combat gag (others) is {state}. "
                "Usage: config combatgag on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.combat_gag_other = True
            character.session.send(
                "Combat gag on -- you will not see others' room "
                "combat lines."
            )
        elif choice in ("off", "no", "false", "0"):
            character.combat_gag_other = False
            character.session.send("Combat gag off.")
        else:
            character.session.send("Usage: config combatgag on|off")
        return
    if key in ("combattags", "combat_tags", "tags"):
        if not rest:
            state = "on" if character.show_combat_tags else "off"
            character.session.send(
                f"Combat tags are {state}. "
                "Usage: config combattags on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.show_combat_tags = True
            character.session.send(
                "Combat tags on -- [DMG]/[HIT]/[MISS] prefixes show."
            )
        elif choice in ("off", "no", "false", "0"):
            character.show_combat_tags = False
            character.session.send(
                "Combat tags off -- combat without "
                "[DMG]/[HIT] prefixes."
            )
        else:
            character.session.send("Usage: config combattags on|off")
        return
    if key in ("plaincomms", "plain_comms", "plainchat"):
        if not rest:
            state = "on" if display_prefs.wants_plain_comms(character) else "off"
            character.session.send(
                f"Plain comms are {state}. "
                "Usage: config plaincomms on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.session.send(
                display_prefs.apply_plain_comms_mode(character, True)
            )
        elif choice in ("off", "no", "false", "0"):
            character.session.send(
                display_prefs.apply_plain_comms_mode(character, False)
            )
        else:
            character.session.send("Usage: config plaincomms on|off")
        return
    if key in ("combathints", "combat_hints", "combathelp"):
        if not rest:
            state = "on" if display_prefs.wants_combat_hints(character) else "off"
            character.session.send(
                f"Combat hints are {state}. "
                "Usage: config combathints on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.show_combat_hints = True
            character.session.send(
                "Combat hints on -- engage and knock-out lines include "
                "clinic/lethal reminders."
            )
        elif choice in ("off", "no", "false", "0"):
            character.show_combat_hints = False
            character.session.send(
                "Combat hints off -- shorter engage/KO lines without "
                "repeat tutorial text."
            )
        else:
            character.session.send("Usage: config combathints on|off")
        return
    if key == "items":
        sub = (rest or "").split(None, 1)
        subkey = sub[0].lower() if sub else ""
        subrest = sub[1] if len(sub) > 1 else ""
        if subkey in ("compact", "floor", "flooritems"):
            if not subrest:
                state = (
                    "on" if getattr(character, "compact_floor_items", False)
                    else "off"
                )
                character.session.send(
                    f"Compact floor items are {state}. "
                    "Usage: config items compact on|off"
                )
                return
            choice = subrest.split(None, 1)[0].lower()
            if choice in ("on", "yes", "true", "1"):
                character.compact_floor_items = True
                character.session.send(
                    "Floor items compact on -- look lists loot in one "
                    "paragraph."
                )
            elif choice in ("off", "no", "false", "0"):
                character.compact_floor_items = False
                character.session.send(
                    "Floor items compact off -- each stack on its own line."
                )
            else:
                character.session.send("Usage: config items compact on|off")
            return
        character.session.send("Usage: config items compact on|off")
        return
    if key in ("vehicles", "vehicle", "rides"):
        sub = (rest or "").split(None, 1)
        subkey = sub[0].lower() if sub else ""
        subrest = sub[1] if len(sub) > 1 else ""
        if subkey in ("compact", "look", "lookcompact"):
            if not subrest:
                state = (
                    "on" if getattr(character, "compact_vehicles", False)
                    else "off"
                )
                character.session.send(
                    f"Compact parked vehicles are {state}. "
                    "Usage: config vehicles compact on|off"
                )
                return
            choice = subrest.split(None, 1)[0].lower()
            if choice in ("on", "yes", "true", "1"):
                character.compact_vehicles = True
                character.session.send(
                    "Parked vehicles compact on -- when more than two rides "
                    "are here, ordinary look summarizes them; type "
                    "look vehicles for the full list."
                )
            elif choice in ("off", "no", "false", "0"):
                character.compact_vehicles = False
                character.session.send(
                    "Parked vehicles compact off -- look lists every ride."
                )
            else:
                character.session.send(
                    "Usage: config vehicles compact on|off"
                )
            return
        character.session.send("Usage: config vehicles compact on|off")
        return
    if key in ("houses", "house", "homes", "home"):
        sub = (rest or "").split(None, 1)
        subkey = sub[0].lower() if sub else ""
        subrest = sub[1] if len(sub) > 1 else ""
        if subkey in ("compact", "look", "lookcompact"):
            if not subrest:
                state = (
                    "on" if getattr(character, "compact_houses", False)
                    else "off"
                )
                character.session.send(
                    f"Compact for-sale homes are {state}. "
                    "Usage: config houses compact on|off"
                )
                return
            choice = subrest.split(None, 1)[0].lower()
            if choice in ("on", "yes", "true", "1"):
                character.compact_houses = True
                character.session.send(
                    "For-sale homes compact on -- when more than two street "
                    "houses attach here, ordinary look and homes here "
                    "summarize; type look houses for the full list."
                )
            elif choice in ("off", "no", "false", "0"):
                character.compact_houses = False
                character.session.send(
                    "For-sale homes compact off -- look and homes here list "
                    "every door."
                )
            else:
                character.session.send(
                    "Usage: config houses compact on|off"
                )
            return
        character.session.send("Usage: config houses compact on|off")
        return
    if key == "autokill":
        handler = hooks.config_handler("autokill")
        if handler is not None:
            handler(character, rest, game)
        else:
            character.session.send("Autokill is not available.")
        return
    if key == "autoloot":
        handler = hooks.config_handler("autoloot")
        if handler is not None:
            handler(character, rest, game)
        else:
            character.session.send("Autoloot is not available.")
        return
    if key in ("autotap", "auto tap", "auto_tap"):
        handler = hooks.config_handler("autotap")
        if handler is not None:
            handler(character, rest, game)
        else:
            character.session.send("Autotap is not available.")
        return
    if key == "autosplit":
        handler = hooks.config_handler("autosplit")
        if handler is not None:
            handler(character, rest, game)
        else:
            character.session.send("Autosplit is not available.")
        return
    if key in ("tips", "tip", "gameplaytips"):
        from engine import hooks
        if not rest:
            character.session.send(hooks.tips_status_line(character, game))
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.session.send(hooks.set_tips_enabled(character, game, True))
        elif choice in ("off", "no", "false", "0"):
            character.session.send(hooks.set_tips_enabled(character, game, False))
        else:
            character.session.send("Usage: config tips on|off")
        return
    if key in ("oocname", "oocidentity", "ooc_identity"):
        # Feature D: account vs character face on OOC.
        from engine import accounts as accounts_mod
        account = accounts_mod.account_for_session_character(game, character)
        if account is None:
            character.session.send(
                "Link an account first (type 'account' or create one at "
                "login). Then: config oocname account|character"
            )
            return
        if not rest:
            character.session.send(
                f"OOC name is {account.ooc_identity} "
                f"(account '{account.display_name}'). "
                "Usage: config oocname account|character"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("account", "acct", "a"):
            account.ooc_identity = accounts_mod.OOC_IDENTITY_ACCOUNT
            if _persist_or_warn(character, game):
                character.session.send(
                    f"OOC will show your account name "
                    f"({account.display_name})."
                )
        elif choice in ("character", "char", "c", "name"):
            account.ooc_identity = accounts_mod.OOC_IDENTITY_CHARACTER
            if _persist_or_warn(character, game):
                character.session.send(
                    "OOC will show your character name."
                )
        else:
            character.session.send(
                "Usage: config oocname account|character"
            )
        return
    if key in ("seeaccounts", "gmaccounts", "gm_see_accounts"):
        # Feature F: staff-only Character(Account) labels.
        from engine import accounts as accounts_mod
        if not _is_gm(character):
            character.session.send("That setting is for staff GMs.")
            return
        account = accounts_mod.account_for_session_character(game, character)
        if account is None:
            body = getattr(character, "gm_mode_body", None)
            if body is not None:
                account = accounts_mod.account_for_session_character(
                    game, body
                )
        if account is None:
            character.session.send(
                "Link a staff account first. "
                "Usage: config seeaccounts on|off"
            )
            return
        if not rest:
            state = "on" if account.gm_see_accounts else "off"
            character.session.send(
                f"See-accounts is {state}. "
                "Usage: config seeaccounts on|off "
                "(GM form: Character(Account) on who/say/tell/OOC)."
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            account.gm_see_accounts = True
            if _persist_or_warn(character, game):
                character.session.send(
                    "See-accounts on -- names show as Character(Account) "
                    "on who, say, tell, and OOC (character OOC name only) "
                    "while you are in GM form."
                )
        elif choice in ("off", "no", "false", "0"):
            account.gm_see_accounts = False
            if _persist_or_warn(character, game):
                character.session.send("See-accounts off -- plain names.")
        else:
            character.session.send("Usage: config seeaccounts on|off")
        return
    if key == "channel":
        # config channel ooc|say|emote|tell|questions <role>
        bits = rest.split(None, 2)
        channel_names = ("ooc", "say", "emote", "tell", "questions")
        channel_aliases = {"question": "questions"}
        if not bits:
            character.session.send(
                "OOC channel role is "
                f"{character.channel_colors.get('ooc', 'ooc')}."
            )
            character.session.send(
                "Say channel role is "
                f"{character.channel_colors.get('say', 'say')}."
            )
            character.session.send(
                "Emote channel role is "
                f"{character.channel_colors.get('emote', 'emote')}."
            )
            character.session.send(
                "Tell channel role is "
                f"{character.channel_colors.get('tell', 'tell')}."
            )
            character.session.send(
                "Questions channel role is "
                f"{character.channel_colors.get('questions', 'ooc')}."
            )
            character.session.send(
                "Usage: config channel ooc|say|emote|tell|questions <role>  "
                "(roles: muted, ooc, say, emote, tell, alert, teal, gold, …)"
            )
            return
        sub = channel_aliases.get(bits[0].lower(), bits[0].lower())
        if sub not in channel_names:
            character.session.send(
                "Configurable channels: ooc, say, emote, tell, questions. "
                "Usage: config channel ooc|say|emote|tell|questions <role>"
            )
            return
        if len(bits) < 2:
            default = "ooc" if sub in ("ooc", "questions") else sub
            cur = character.channel_colors.get(sub, default)
            character.session.send(
                f"{sub.upper()} channel role is {cur}. "
                f"Usage: config channel {sub} <role>"
            )
            return
        role = bits[1].strip().lower()
        if role not in style.COLORS and role not in style.COLORS_XTERM256:
            character.session.send(
                f"Unknown role '{role}'. Try muted, ooc, say, alert, teal."
            )
            return
        character.channel_colors[sub] = role
        character.session.send(f"{sub.upper()} channel color role set to {role}.")
        return

    if key in ("surname_alert", "surnamealert", "surname_reminder"):
        if not rest:
            state = "off" if character.suppress_surname_alert else "on"
            character.session.send(
                f"Surname login reminder is {state}. "
                "Usage: config surname alert on|off"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            character.suppress_surname_alert = False
            character.session.send(
                "Surname reminder on -- you will be nudged when no surname "
                "is on file (mortals only)."
            )
            return
        if choice in ("off", "no", "false", "0"):
            character.suppress_surname_alert = True
            character.session.send(
                "Surname reminder off -- no more login alerts about "
                "missing surnames."
            )
            return
        character.session.send("Usage: config surname alert on|off")
        return

    # --- Forward to sibling preference verbs (same session messages) ----
    if key == "prompt":
        return cmd_prompt(character, rest, game)
    if key == "alias":
        return cmd_alias(character, rest, game)
    if key == "timeformat":
        return cmd_timeformat(character, rest, game)

    # SUPERS / game prefs: re-enter dispatch so the real handlers run
    # without engine importing supers (two-repo purity).
    _FORWARD = {
        "whofull": "whofull",
        "whohide": "whohide",
        "combatnumbers": "combatnumbers",
        "scoremeters": "scoremeters",
        "combatdiag": "combatdiag",
        "combatterse": "combatterse",
        "combatairy": "combatairy",
        "combatprose": "combatprose",
        "fightlog": "fightlog",
        "autoidle": "autoidle",
        "idlemode": "idlemode",
        "idle": "idle",
        "afk": "afk",
    }
    if key in _FORWARD:
        dispatch = hooks.get_dispatch()
        if dispatch is None:
            character.session.send(
                f"'{key}' needs the full game installed."
            )
            return
        line = _FORWARD[key] if not rest else f"{_FORWARD[key]} {rest}"
        return dispatch(character, line, game)

    character.session.send(
        "Usage: config [<category>|<setting> …]. Type bare 'config' for "
        "categories, or 'config full' for every setting. See 'help config'."
    )


# Category ids for the prefs hub. Player-facing names in the index below.
_CONFIG_CATEGORY_ORDER = (
    "display",
    "brief",
    "combat",
    "autoloot",
    "chat",
    "account",
    "who",
    "macros",
)

# Aliases → canonical category id. ``brief`` / ``compact`` open the
# look-clutter page; mutation still uses ``config brief on`` etc.
_CONFIG_CATEGORY_ALIASES = {
    "full": "full",
    "all": "full",
    "everything": "full",
    "display": "display",
    "client": "display",
    "brief": "brief",
    "compact": "brief",
    "look": "brief",
    "combat": "combat",
    "fight": "combat",
    "autoloot": "autoloot",
    "loot": "autoloot",
    "chat": "chat",
    "comms": "chat",
    "channels": "chat",
    "account": "account",
    "ooc": "account",
    "who": "who",
    "macros": "macros",
    "macro": "macros",
    "aliases": "macros",
}


def _config_resolve_category(key):
    """Return canonical category id for a config hub word, or None."""
    return _CONFIG_CATEGORY_ALIASES.get((key or "").strip().lower())


# Settings whose catalog id contains an underscore. Players may type the
# spaced form (``config surname alert off``) -- cmd_config folds these
# before the first-token category lookup.
_CONFIG_SNAKE_KEYS = frozenset({
    "map_on_move",
    "drive_map",
    "map_view",
    "map_zone",
    "map_on_look",
    "brief_look",
    "combat_gag",
    "combat_tags",
    "plain_comms",
    "combat_hints",
    "ooc_identity",
    "gm_see_accounts",
    "surname_alert",
    "surname_reminder",
    "auto_tap",
})


def _config_category_index():
    """Short bare-config menu: pick a category or dump everything."""
    return [
        "Config -- client and display preferences.",
        "Type config <category> for one page, or config <setting> … to change.",
        "Short verbs (color, brief, prompt, …) still work.",
        "",
        "Categories:",
        "  display   -- color, width, map, screenreader, spacing, traffic, radio",
        "  brief     -- brief look + items/vehicles/houses compact "
        "(alias: compact)",
        "  combat    -- gag, tags, hints, numbers, diag, fightlog, autokill",
        "  autoloot  -- kill scoop, dungeon/relics, autosplit, autotap",
        "  chat      -- config channel ooc|say|emote|tell|questions, "
        "plaincomms, tips, surname reminder",
        "  account   -- OOC name, staff seeaccounts",
        "  who       -- whofull, whohide, autoidle, idle",
        "  macros    -- prompt + aliases",
        "  full      -- every setting at once",
        "",
        "Examples: config brief | config combat | config channel | "
        "config compact on | config vehicles compact on | "
        "config reset combat",
        "config reset <category> restores one page to chargen defaults "
        "(no reset-all; screenreader stays on config screenreader).",
        "See also: help config | help formatting | help alias | help prompt",
    ]


def _config_reset_dispatch(character, rest_l, game):
    """Restore one config category to chargen defaults (OOC Celtichawk).

    There is no reset-all. Display reset never flips screenreader -- use
    config screenreader off for that. Autoloot reset applies the smart
    scoop preset.
    """
    from engine import display_prefs
    from engine import hooks

    display_prefs.ensure_display_defaults(character)
    token = (rest_l or "").split(None, 1)[0] if rest_l else ""
    category = _config_resolve_category(token)
    if not token or category is None:
        character.session.send(
            "Usage: config reset <category> -- display, brief, combat, "
            "autoloot, chat, or who. There is no reset-all. "
            "Screenreader stays on config screenreader on|off."
        )
        return
    if category in ("account", "macros", "full"):
        character.session.send(
            "Config reset does not wipe account names, aliases, or "
            "everything at once. Pick display, brief, combat, autoloot, "
            "chat, or who -- or prompt default for the chargen prompt."
        )
        return
    if category == "display":
        character.use_color = True
        character.color_depth = "ansi"
        character.display_width = display_prefs.WIDTH_DEFAULT
        character.pager_lines = 20
        character.output_spacing = display_prefs.OUTPUT_SPACING_AIRY
        character.traffic_mode = display_prefs.TRAFFIC_NORMAL
        character.angel_radio_mode = display_prefs.RADIO_MODE_FULL
        character.show_minimap = True
        character.map_on_move = False
        character.map_on_look = False
        character.drive_map_full = True
        character.map_view_full = False
        character.mapzone_overlay = False
        character.time_format = "24h"
        for attr in (
            "show_minimap", "map_on_move", "map_on_look", "traffic_mode",
        ):
            display_prefs.mark_pref_manual(character, attr)
    elif category == "brief":
        character.brief = False
        character.compact_floor_items = False
        character.compact_vehicles = False
        character.compact_houses = False
        character.exits_verbose = True
    elif category == "combat":
        character.combat_gag_other = False
        character.show_combat_tags = True
        character.show_combat_hints = True
        character.autokill = False
        character.combat_numbers = False
        character.score_meters = True
        character.combat_diag = False
        character.combat_prose = False
        character.combat_terse = False
        character.combat_terse_airy = False
        character.fightlog_enabled = False
        display_prefs.mark_pref_manual(character, "fightlog_enabled")
    elif category == "autoloot":
        dispatch = hooks.get_dispatch()
        if dispatch is not None:
            dispatch(character, "autoloot smart", game)
        character.autosplit = True
        character.auto_tap = False
        character.autoloot_ignored = []
    elif category == "chat":
        character.plaincomms = False
        character.show_tips = True
        character.suppress_surname_alert = False
        character.channel_colors = {}
    elif category == "who":
        character.who_full = False
        character.who_hide = False
        character.auto_idle = True
        character.idle_mode = False
    else:
        character.session.send(
            "Usage: config reset <category> -- display, brief, combat, "
            "autoloot, chat, or who."
        )
        return
    character.session.send(
        f"Config {category} restored to chargen defaults."
    )
    character.session.send(
        "\r\n".join(
            _config_status_lines(character, category=category)
        )
    )


def _config_section_display(character, display_prefs):
    """Display / client chrome lines (maps, color, width, …)."""
    clock = (
        "12h" if getattr(character, "time_format", "24h") == "12h" else "24h"
    )
    return [
        "Display:",
        f"  color: {'on' if character.use_color else 'off'} "
        f"(depth {character.color_depth})  -- config color on|off|16|256",
        f"  width: {character.display_width}  -- config width <40-120>",
        f"  pager: {getattr(character, 'pager_lines', 20)}  "
        "-- config pager <5-100> (lines per more page)",
        f"  screenreader: "
        f"{'on' if character.screenreader else 'off'}  "
        "-- config screenreader on|off",
        f"  traffic: {display_prefs.traffic_mode(character)}  "
        "-- config traffic quiet|normal|verbose (NPC foot traffic)",
        f"  radio: {display_prefs.angel_radio_mode(character)}  "
        "-- config radio full|quiet (Angel Radio; Angels and Gods)",
        f"  spacing: "
        f"{display_prefs.normalize_output_spacing(getattr(character, 'output_spacing', display_prefs.OUTPUT_SPACING_AIRY))}  "
        "-- config spacing airy|packed (room, tells, and fight rounds)",
        f"  map: {'on' if character.show_minimap else 'off'}  "
        "-- config map on|off (bare map command)",
        f"  maplook: {'on' if character.map_on_look else 'off'}  "
        "-- config maplook on|off (embed map in look)",
        f"  mapmove: {'on' if character.map_on_move else 'off'}  "
        "-- config mapmove on|off (map after each move)",
        f"  drivemap: "
        f"{'atlas' if getattr(character, 'drive_map_full', True) else 'minimap'}  "
        "-- config drivemap atlas|minimap (cruise redraw)",
        f"  mapview: "
        f"{'atlas' if getattr(character, 'map_view_full', False) else 'minimap'}  "
        "-- config mapview atlas|minimap (bare map default)",
        f"  mapzone: "
        f"{'on' if getattr(character, 'mapzone_overlay', False) else 'off'}  "
        "-- config mapzone on|off (zone bearings under ASCII map)",
        f"  timeformat: {clock}  -- config timeformat 12|24",
    ]


def _config_section_brief(character):
    """Brief + look-compact clutter prefs (suggestion report 279)."""
    from engine import display_prefs

    prefs = display_prefs.preference_character(character)
    display_prefs.ensure_display_defaults(prefs)
    items_compact = (
        "on" if getattr(character, "compact_floor_items", False) else "off"
    )
    vehicles_compact = (
        "on" if getattr(character, "compact_vehicles", False) else "off"
    )
    houses_compact = (
        "on" if getattr(character, "compact_houses", False) else "off"
    )
    return [
        "Brief / compact look:",
        f"  brief: {'on' if getattr(character, 'brief', False) else 'off'}  "
        "-- config brief on|off (skip prose/weather/trail after move; "
        "look for full)",
        f"  items: compact {items_compact}  "
        "-- config items compact on|off (floor loot paragraph)",
        f"  vehicles: compact {vehicles_compact}  "
        "-- config vehicles compact on|off (summarize 3+ parked rides)",
        f"  houses: compact {houses_compact}  "
        "-- config houses compact on|off (summarize 3+ for-sale homes)",
        "  all compact:  -- config compact on|off "
        "(items + vehicles + houses together)",
        f"  exits: "
        f"{'verbose' if prefs.exits_verbose else 'compact'}  "
        "-- config exits compact|verbose",
    ]


def _config_section_combat(character, display_prefs):
    """Combat chrome and autokill (not autoloot -- see autoloot page)."""
    tags_state = "on" if character.show_combat_tags else "off"
    if getattr(character, "screenreader", False) and not character.show_combat_tags:
        tags_state += "  (hit/miss still named in plain words)"
    return [
        "Combat:",
        f"  combatgag: "
        f"{'on' if character.combat_gag_other else 'off'}  "
        "-- config combatgag on|off",
        f"  combattags: {tags_state}  -- config combattags on|off",
        f"  combathints: "
        f"{'on' if display_prefs.wants_combat_hints(character) else 'off'}  "
        "-- config combathints on|off (engage/KO tutorial lines)",
        f"  autokill: "
        f"{'on' if getattr(character, 'autokill', False) else 'off'}  "
        "-- config autokill on|off (dungeon fodder KO finish)",
        f"  combatnumbers: "
        f"{'on' if getattr(character, 'combat_numbers', False) else 'off'}  "
        "-- config combatnumbers on|off",
        f"  scoremeters: "
        f"{'on' if getattr(character, 'score_meters', True) else 'off'}  "
        "-- config scoremeters on|off (gothic bars on score)",
        f"  combatdiag: "
        f"{'on' if getattr(character, 'combat_diag', False) else 'off'}  "
        "-- config combatdiag on|off",
        f"  combatterse: "
        f"{'on' if getattr(character, 'combat_terse', False) else 'off'}  "
        "-- config combatterse on|off (sighted short colored lines)",
        f"  spacing: "
        f"{display_prefs.normalize_output_spacing(getattr(character, 'output_spacing', display_prefs.OUTPUT_SPACING_AIRY))}  "
        "-- config spacing airy|packed (fight rounds too; combatairy is the same switch)",
        f"  combatprose: "
        f"{'on' if getattr(character, 'combat_prose', False) else 'off'}  "
        "-- config combatprose on|off"
        # Screenreader players default to a short SVO line. combatprose is
        # how they opt into the full cinematic wording without combatdiag's
        # (role:pool) machinery -- say so here, because "how do I get the
        # cool combat text" is the question this page keeps failing to
        # answer (suggestion 383, OOC 2026-09-06).
        + ("  (full cinematic wording instead of the short line)"
           if getattr(character, "screenreader", False) else ""),
        f"  fightlog: "
        f"{'on' if getattr(character, 'fightlog_enabled', False) else 'off'}  "
        "-- config fightlog on|off (cinematic replay after fights)",
    ]


def _config_section_autoloot(character):
    """Kill-scoop and party split prefs."""
    return [
        "Autoloot:",
        f"  autoloot: "
        f"{'on' if getattr(character, 'autoloot', False) else 'off'}  "
        "-- config autoloot on|off (combat-zone kill scoop)",
        f"  autoloot dungeon: "
        f"{'on' if getattr(character, 'autoloot_dungeon', True) else 'off'}  "
        "-- config autoloot dungeon on|off",
        f"  autoloot relics: "
        f"{'on' if getattr(character, 'autoloot_relics', False) else 'off'}  "
        "-- config autoloot relics on|off",
        "  autoloot categories: bare autoloot lists weapons/armor/coins/… "
        "-- config autoloot <category> on|off",
        "  autoloot ignore: bare autoloot ignore lists types left on the floor "
        "-- config autoloot ignore <name> | unignore <name> | ignore clear",
        f"  autosplit: "
        f"{'on' if getattr(character, 'autosplit', True) else 'off'}  "
        "-- config autosplit on|off (party coin/salvage split)",
    ]


def _config_section_chat(character, display_prefs):
    """Channels, tips, and chat chrome."""
    ch = character.channel_colors.get("ooc", "ooc")
    return [
        "Chat:",
        f"  plaincomms: "
        f"{'on' if display_prefs.wants_plain_comms(character) else 'off'}  "
        "-- config plaincomms on|off (plain say/tell/OOC sentences)",
        f"  tips: {'on' if character.show_tips else 'off'}  "
        "-- config tips on|off ([TIP] hints every 5-15 min)",
        f"  surname alert: "
        f"{'off' if getattr(character, 'suppress_surname_alert', False) else 'on'}  "
        "-- config surname alert on|off (mortals; silences no-surname login nag)",
        f"  channel ooc: {ch}  -- config channel ooc <role>",
        f"  channel say: {character.channel_colors.get('say', 'say')}  "
        "-- config channel say <role>",
        f"  emote third: "
        f"{'on' if getattr(character, 'emote_third', False) else 'off'}  "
        "-- config emote third on|off (see your name instead of You)",
        f"  channel emote: {character.channel_colors.get('emote', 'emote')}  "
        "-- config channel emote <role>",
        f"  channel tell: {character.channel_colors.get('tell', 'tell')}  "
        "-- config channel tell <role>",
        f"  channel questions: "
        f"{character.channel_colors.get('questions', 'ooc')}  "
        "-- config channel questions <role>",
    ]


def _config_section_account(character):
    """Account / OOC identity lines (values filled when linked)."""
    lines = [
        "Account / OOC:",
        "  oocname: account|character  -- config oocname … "
        "(linked account or assigned-cast occupy)",
        "  seeaccounts: on|off  -- config seeaccounts … "
        "(staff GM form only)",
    ]
    try:
        from engine import accounts as accounts_mod
        game = getattr(getattr(character, "session", None), "game", None)
        account = accounts_mod.account_for_session_character(game, character)
        if account is None:
            body = getattr(character, "gm_mode_body", None)
            if body is not None:
                account = accounts_mod.account_for_session_character(
                    game, body
                )
        if account is not None:
            for i, line in enumerate(lines):
                if line.startswith("  oocname:"):
                    lines[i] = (
                        f"  oocname: {account.ooc_identity}  "
                        "-- config oocname account|character"
                    )
                if line.startswith("  seeaccounts:"):
                    state = "on" if account.gm_see_accounts else "off"
                    lines[i] = (
                        f"  seeaccounts: {state}  "
                        "-- config seeaccounts on|off (staff)"
                    )
    except Exception:
        pass
    return lines


def _config_section_who(character):
    """Who list and idle prefs."""
    return [
        "Who / idle:",
        f"  whofull: "
        f"{'on' if getattr(character, 'who_full', False) else 'off'}  "
        "-- config whofull on|off",
        f"  whohide: "
        f"{'on' if getattr(character, 'who_hide', False) else 'off'}  "
        "-- config whohide on|off",
        f"  autoidle: "
        f"{'on' if getattr(character, 'auto_idle', True) else 'off'}  "
        "-- config autoidle on|off",
        f"  idlemode: "
        f"{'on' if getattr(character, 'idle_mode', False) else 'off'}  "
        "-- config idle on|off",
    ]


def _config_section_macros(character):
    """Prompt template and command aliases."""
    alias_n = len(character.command_aliases or {})
    return [
        "Macros / prompt:",
        f"  aliases: {alias_n} set  -- config alias …",
        f"  prompt: {character.prompt_format!r}  -- config prompt …",
    ]


def _config_status_lines(character, category=None):
    """Categorized config submenu (one page or full dump).

    ``category`` is a canonical id from ``_CONFIG_CATEGORY_ALIASES``, or
    ``None`` / ``\"full\"`` for every section (legacy bare-config dump).
    """
    from engine import display_prefs
    display_prefs.ensure_display_defaults(character)
    cat = (category or "full").strip().lower()
    if cat == "full" or cat is None:
        wanted = list(_CONFIG_CATEGORY_ORDER)
        header = [
            "Config -- all client and display preferences.",
            "Type config <setting> … to change. "
            "Bare config lists categories.",
            "",
        ]
    else:
        if cat not in _CONFIG_CATEGORY_ORDER:
            return _config_category_index()
        wanted = [cat]
        header = [
            f"Config -- {cat} preferences.",
            "Type config <setting> … to change, or config full for everything.",
            "",
        ]

    builders = {
        "display": lambda: _config_section_display(character, display_prefs),
        "brief": lambda: _config_section_brief(character),
        "combat": lambda: _config_section_combat(character, display_prefs),
        "autoloot": lambda: _config_section_autoloot(character),
        "chat": lambda: _config_section_chat(character, display_prefs),
        "account": lambda: _config_section_account(character),
        "who": lambda: _config_section_who(character),
        "macros": lambda: _config_section_macros(character),
    }
    lines = list(header)
    for i, section_id in enumerate(wanted):
        if i:
            lines.append("")
        lines.extend(builders[section_id]())
    lines.append("")
    lines.append(
        "See also: help formatting | help config | help alias | "
        "help prompt | help account"
    )
    return lines


def cmd_account(character, args, game):
    """Show, create, or link an engine Account for this character.

    Usage::
        account                 status (name, characters, totals, prefs)
        account create <name>   start create+link (prompts for password)
        account link <name>     link to existing (prompts for password)
        account oocname …       alias of config oocname

    When typed from GM form, create/link attaches the left-behind playable
    body (not the ``gmspirit:`` actor).
    """
    from engine import accounts as accounts_mod
    from engine import auth

    raw = (args or "").strip()
    # Status / already-linked checks use the playable body when in GM form.
    link_body, body_err = accounts_mod.playable_link_target(game, character)
    body_for_acct = link_body if link_body is not None else character
    account = accounts_mod.account_for_session_character(game, body_for_acct)

    if not raw:
        if account is None:
            character.session.send(
                "You have no linked account. "
                "Type 'account create <name> <password>' or "
                "'account link <name> <password>', "
                "or wait for the login offer next time you connect. "
                "See 'help account'."
            )
            return
        # Refresh contribution totals from logs (engine-pure).
        from engine import account_display as account_display_mod
        from engine import display_prefs

        lines = account_display_mod.format_account_status(
            game,
            account,
            character,
            screenreader=bool(getattr(character, "screenreader", False)),
            width=display_prefs.sheet_width(character),
        )
        character.session.send("\r\n".join(lines))
        return

    parts = raw.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("create", "new"):
        if account is not None:
            character.session.send(
                f"Already linked to '{account.display_name}'. "
                "Ask staff if you need to change that."
            )
            return
        if body_err:
            character.session.send(body_err)
            return
        if not rest:
            character.session.send("Usage: account create <name>")
            return
        # Synchronous-ish: reuse the async prompt via a mini loop is hard
        # from a sync verb -- do create inline with a password on the
        # same line OR ask them to use login offer. Prefer inline:
        # account create Name password
        bits = rest.split(None, 1)
        name = bits[0]
        if len(bits) < 2:
            character.session.send(
                "Usage: account create <name> <password> "
                f"(password at least {auth.MIN_PASSWORD_LEN} characters)"
            )
            return
        password = bits[1]
        cleaned, err = accounts_mod.normalize_account_name(name)
        if err:
            character.session.send(err)
            return
        new_acct, err = accounts_mod.create_account(game, cleaned, password)
        if err:
            character.session.send(err)
            return
        link_err = accounts_mod.link_character(game, new_acct, link_body)
        if link_err:
            # Do not leave an orphan account with no characters.
            accounts_mod.unregister_account(game, new_acct)
            character.session.send(link_err)
            return
        accounts_mod.migrate_legacy_gm_ranks(game)
        if not _persist_or_warn(character, game):
            return
        character.session.send(
            f"Account '{new_acct.display_name}' created and linked to "
            f"{_presence_face(link_body)}. "
            "Type 'account' to review."
        )
        from engine import gm_notify
        who = gm_notify.public_who(link_body, game)
        gm_notify.ping_gms(
            game,
            f"{who} created account {new_acct.display_name} and linked "
            f"{_presence_face(link_body)}.",
            exclude=character,
            peer_session=character.session,
        )
        return

    if sub == "link":
        if account is not None:
            character.session.send(
                f"Already linked to '{account.display_name}'."
            )
            return
        if body_err:
            character.session.send(body_err)
            return
        bits = rest.split(None, 1)
        if len(bits) < 2:
            character.session.send(
                "Usage: account link <name> <password>"
            )
            return
        cleaned, err = accounts_mod.normalize_account_name(bits[0])
        if err:
            character.session.send(err)
            return
        existing = accounts_mod.find_account(game, cleaned)
        if existing is None:
            character.session.send("No such account.")
            return
        if not accounts_mod.verify_account_password(existing, bits[1]):
            character.session.send("Incorrect password.")
            return
        link_err = accounts_mod.link_character(game, existing, link_body)
        if link_err:
            character.session.send(link_err)
            return
        accounts_mod.migrate_legacy_gm_ranks(game)
        if not _persist_or_warn(character, game):
            return
        character.session.send(
            f"Linked {_presence_face(link_body)} to account "
            f"'{existing.display_name}'. "
            "Type 'account' to review."
        )
        from engine import gm_notify
        who = gm_notify.public_who(link_body, game)
        gm_notify.ping_gms(
            game,
            f"{who} linked to account {existing.display_name}.",
            exclude=character,
            peer_session=character.session,
        )
        return

    if sub in ("oocname", "ooc"):
        return cmd_config(character, f"oocname {rest}".strip(), game)

    if sub == "discord":
        return _cmd_account_discord(character, rest, game, account)

    character.session.send(
        "Usage: account | account create <name> <password> | "
        "account link <name> <password> | account discord link|unlink | "
        "account oocname …  "
        "See 'help account'."
    )


def _cmd_account_discord(character, rest, game, account):
    """account discord [link|unlink|status] -- Discord OOC bridge."""
    import time

    from engine import discord_ooc_links

    if account is None:
        character.session.send(
            "Link a MUD account first (account create / account link). "
            "See 'help account'."
        )
        return
    sub = (rest or "").strip().lower()
    if sub in ("", "status", "show"):
        character.session.send(
            discord_ooc_links.status_for_account(game, account.name)
        )
        return
    if sub == "link":
        try:
            display, _code, expires = discord_ooc_links.generate_link_code(
                account.name
            )
        except ValueError as exc:
            character.session.send(str(exc))
            return
        mins = max(1, int((expires - time.time()) // 60))
        character.session.send(
            "Discord link code (one-time, expires in "
            f"{mins} min):\r\n"
            f"  {display}\r\n"
            "In Discord #ooc, post:\r\n"
            f"  !link {display}\r\n"
            "or use slash /link with that code."
        )
        return
    if sub == "unlink":
        removed = discord_ooc_links.unlink_account(account.name)
        if removed:
            character.session.send(
                f"Discord unlinked ({removed} mapping(s) removed)."
            )
        else:
            character.session.send("Discord was not linked.")
        return
    character.session.send(
        "Usage: account discord  |  account discord link  |  "
        "account discord unlink"
    )


def cmd_alias(character, args, game):
    """List, set, or clear command aliases (D65 / prefs macros).

    Usage::
        alias
        alias <short> <expansion>
        alias clear <short>
        alias clear
    """
    from engine import display_prefs
    display_prefs.ensure_display_defaults(character)
    raw = (args or "").strip()
    if not raw:
        aliases = character.command_aliases or {}
        if not aliases:
            character.session.send(
                "No aliases. Usage: alias <short> <expansion>  "
                "(see 'help alias')"
            )
            return
        lines = ["Aliases:"]
        for key in sorted(aliases):
            lines.append(f"  {key} -> {aliases[key]}")
        character.session.send("\r\n".join(lines))
        return
    parts = raw.split(maxsplit=1)
    if parts[0].lower() == "clear":
        target = parts[1].strip().lower() if len(parts) > 1 else ""
        if not target:
            character.command_aliases = {}
            character.session.send("All aliases cleared.")
            return
        if target in character.command_aliases:
            del character.command_aliases[target]
            character.session.send(f"Alias '{target}' cleared.")
        else:
            character.session.send(f"No alias named '{target}'.")
        return
    if len(parts) < 2:
        character.session.send(
            "Usage: alias <short> <expansion>  | alias clear [<short>]"
        )
        return
    short = parts[0].lower()
    expansion = parts[1].strip()
    if len(short) > display_prefs._MAX_ALIAS_KEY_LEN:
        character.session.send("Alias name too long.")
        return
    if len(expansion) > display_prefs._MAX_ALIAS_VALUE_LEN:
        character.session.send("Alias expansion too long.")
        return
    # Never allow aliasing over a real verb -- expand_aliases also skips.
    from commands import COMMANDS, DIRECTIONS
    if short in COMMANDS or short in DIRECTIONS:
        character.session.send(
            f"'{short}' is a built-in command -- pick another short name."
        )
        return
    if len(character.command_aliases) >= display_prefs._MAX_ALIASES and (
        short not in character.command_aliases
    ):
        character.session.send(
            f"Alias limit ({display_prefs._MAX_ALIASES}) reached."
        )
        return
    character.command_aliases[short] = expansion
    character.session.send(f"Alias set: {short} -> {expansion}")


def cmd_prompt(character, args, game):
    """Show or set the custom prompt string (D65 / prefs #27 / #28).

    Usage::
        prompt
        prompt default
        prompt off
        prompt [%h/%Hhp] [%f fuel]
    Tokens: %h %H %e %s %S %f %n %r %%
    """
    from engine import display_prefs
    display_prefs.ensure_display_defaults(character)
    raw = (args or "").strip()
    if not raw:
        sample = display_prefs.format_prompt(character, game)
        character.session.send(
            f"Prompt template: {character.prompt_format!r}\r\n"
            f"Renders as: {sample or '(empty)'}\r\n"
            "Usage: prompt <template> | prompt default | prompt off  "
            "(see 'help prompt')"
        )
        return
    lower = raw.lower()
    if lower in ("off", "none", "clear", ""):
        character.prompt_format = ""
        character.session.send("Prompt cleared.")
        return
    if lower == "default":
        display_prefs.apply_origin_default_prompt(character, force=True)
        character.session.send(
            f"Prompt reset to default: {character.prompt_format}"
        )
        return
    if len(raw) > display_prefs._MAX_PROMPT_LEN:
        character.session.send(
            f"Prompt too long (max {display_prefs._MAX_PROMPT_LEN})."
        )
        return
    character.prompt_format = raw
    sample = display_prefs.format_prompt(character, game)
    character.session.send(f"Prompt set. Renders as: {sample}")


def cmd_mpkg(character, args, game):
    """Install / update the Mudlet HUD package on demand (pull-based).

    First-install helper to complement the automatic Client.GUI push: on a
    Mudlet client ``mpkg download`` re-sends the package so Mudlet fetches and
    installs it right now (handy if auto-install was off, or to force an
    upgrade). Browser players are told the HUD is already built in; telnet /
    other clients get the download URL to install once in Mudlet.
    """
    from engine import gmcp

    session = getattr(character, "session", None)
    if session is None:
        return
    # Core.Hello sets gmcp_client: "Mudlet" / "riftforge-web" / "" (telnet).
    client = str(getattr(session, "gmcp_client", "") or "").lower()
    url = gmcp.MUDLET_GUI_URL
    label = gmcp.MUDLET_PACKAGE_LABEL
    arg = (args or "").strip().lower()

    # Bare / help: explain what this does before pulling anything.
    if arg in ("", "help", "?"):
        session.send(
            f"mpkg -- the {label} Mudlet HUD.\r\n"
            "  mpkg download   install or update the package now\r\n"
            "Mudlet also auto-updates it on login once installed. See 'help mudlet'."
        )
        return

    if arg in ("download", "install", "get", "update", "upgrade", "pull"):
        if client == "mudlet":
            if gmcp.force_push_client_gui(session):
                session.send(
                    f"Sending {label} to Mudlet -- it installs in a few seconds.\r\n"
                    "If nothing happens, turn on Mudlet Preferences -> General -> "
                    "'allow the server to install packages', then run mpkg download again."
                )
            else:
                session.send(
                    "Could not send the package right now. Install it manually in "
                    f"Mudlet from:\r\n  {url}"
                )
            return
        if client.startswith("riftforge-web"):
            session.send(
                "You're on the browser client -- the HUD is already built in, no "
                "package needed. mpkg is for the Mudlet desktop client."
            )
            return
        # Plain telnet or an unknown client: point them at the download.
        session.send(
            f"The {label} HUD is a Mudlet package. In Mudlet, install it once from:\r\n"
            f"  {url}\r\n"
            "(or Package Manager -> Install Package). After that it auto-updates. "
            "See 'help mudlet'."
        )
        return

    session.send("Usage: mpkg download  (installs/updates the Mudlet HUD). See 'help mudlet'.")


def cmd_time(character, args, game):
    """Bare-engine clock: calendar only, no eclipse/World-Tide flavor.

    This is the LEAN stub (two-repo purity Phase 2 -- see this module's
    docstring). The full version with the eclipse ambient line and the
    World Tide "lean" phrase appended moved to
    `supers/verbs/engine_flavor.py`'s `cmd_time`, which SUPERS_COMMANDS
    overrides this stub with whenever SUPERS is installed.
    """
    from engine import game_calendar
    from engine import game_clock_tuning
    cal = game.calendar()
    clock = game_calendar.format_clock(cal, fmt=character.time_format)
    speed = game_clock_tuning.speed_phrase(
        game,
        ticks_per_game_day=game_calendar.TICKS_PER_GAME_DAY,
    )
    character.session.send(
        f"It is {clock} ({cal['day_period']}) in {cal['season']} "
        f"on {cal['weekday_name']}, {cal['month_name']} "
        f"{cal['day_of_month']}, {cal['year']}. "
        f"{speed}"
    )


def cmd_timeformat(character, args, game):
    """timeformat [12|24] -- show or set your own 24h/12h clock display
    preference (suggestions.log #46). Display-only: purely cosmetic, the
    underlying game clock (and training pacing) never changes.
    """
    choice = args.strip().lower()
    if not choice:
        current = "12-hour (AM/PM)" if character.time_format == "12h" else "24-hour"
        character.session.send(
            f"Your clock is set to {current}. Usage: timeformat 12|24"
        )
        return
    if choice in ("12", "12h"):
        character.time_format = "12h"
        character.session.send("Clock set to 12-hour (AM/PM).")
    elif choice in ("24", "24h"):
        character.time_format = "24h"
        character.session.send("Clock set to 24-hour.")
    else:
        character.session.send("Usage: timeformat 12|24")


def cmd_date(character, args, game):
    """Full Gregorian calendar stack: weekday, date, week, season, moon
    (suggestions.log #16). Shares the same tick source as cmd_time.
    """
    from engine import game_calendar
    from engine import game_clock_tuning
    cal = game.calendar()
    pace = game_clock_tuning.speed_phrase(
        game,
        ticks_per_game_day=game_calendar.TICKS_PER_GAME_DAY,
    )
    character.session.send(
        game_calendar.format_date(cal)
        + f" {pace}"
    )


# Undated Unreleased bullets keep a sentinel date for display only.
# The player ``changes`` page is lookup-number order (highest ``#N`` first)
# once the ledger has overlaid ids. Visit ceiling / ``changes new`` still
# key off ``sort_ts``. After chronological minting those two agree.
# Legacy bullets may still carry an optional hidden ``#N`` id for old
# ``changes detail <n>`` bookmarks; new ships omit ``#N`` entirely.
# Visit-ceiling model (``changes`` UX lock, Aug 2026 — see
# ``docs/plans/changes_ux_lock.md``): ``last_seen_changelog_sort_ts`` stores
# the newest ship timestamp this account has **fully** acknowledged
# (catchup, opening one ship, or finishing the whole unread pile). Stars on
# bare ``changes`` still use that ceiling. ``changes new`` paging does **not**
# raise it on a partial page (bug 678) — it walks a closed lookup-# window
# (``unread_cursor`` .. ``unread_high_water``). Login ``[NEWS]`` counts the
# remaining pool, so a copyover that lands one new ship while you are mid-page
# adds 1 instead of replaying the whole backlog. Bare ``changes`` always
# shows the global recent feed. Legacy floor watermarks migrate once via
# ``changelog_visit_migrated``.
_CHANGELOG_UNDATED = "0001-01-01"
_CHANGELOG_WM_MIN = "0000-00-00T00:00:00Z"
_CHANGELOG_WM_MAX = "9999-99-99T99:99:99Z"

# Change id at the start of a bold lead-in: ``#042 2026-07-16 — …``.
# Zero-padding is optional (``#42`` and ``#042`` both parse). Players see
# ``#N`` on ``changes`` lines and type ``changes 42`` to open that ship.
# Agents never mint ``#N`` in feature PRs — ``main`` assigns after merge.
_CHANGELOG_ID_PREFIX_RE = re.compile(
    r"^#(\d+)\s+(.*)$",
    re.DOTALL,
)

# Leading date inside a bold lead-in: ``2026-07-16 — Summary`` or
# ``2026-07-16T14:30:00Z — Summary``. The optional ``T…Z`` tail is a
# hidden sort timestamp (UTC); players only see ``YYYY-MM-DD``. Em dash,
# en dash, ASCII ``--``, or a lone hyphen after the stamp are all accepted.
_CHANGELOG_DATE_PREFIX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})"
    r"(?:T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z)?"
    r"(?:\s*[—–]\s*|\s+--\s+|\s+-\s+|\s+ΓÇö\s+)"
    r"(.*)$",
    re.DOTALL,
)

_CHANGELOG_LEAD_TAG_RE = re.compile(r"^\[([^\]]+)\]\s+")


def _changelog_sort_ts_from_date(date, time_hms=None):
    """Build a normalized UTC sort key from display date + optional ``HH:MM:SS``."""
    if not date or date == _CHANGELOG_UNDATED:
        return _CHANGELOG_UNDATED + "T00:00:00Z"
    if time_hms:
        return f"{date}T{time_hms}Z"
    return f"{date}T00:00:00Z"


def _strip_changelog_date_prefix(text):
    """Pull a leading date stamp off *text*.

    Returns ``(date_or_None, sort_ts_or_None, remainder)``. ``sort_ts`` is
    the hidden UTC ISO key (``YYYY-MM-DDTHH:MM:SSZ``) used for ordering;
    ``date`` is the player-facing ``YYYY-MM-DD`` only. Used for both the
    short summary and the first ``full`` line so ``changes detail`` does not
    print the date twice (once from the parsed field, once from the markdown
    body).
    """
    match = _CHANGELOG_DATE_PREFIX_RE.match(text or "")
    if not match:
        return None, None, text or ""
    date = match.group(1)
    time_hms = match.group(2)
    sort_ts = _changelog_sort_ts_from_date(date, time_hms)
    return date, sort_ts, match.group(3)


def _strip_changelog_stamps(text):
    """Pull a leading ``#N`` id and optional date/sort stamp off *text*.

    Returns ``(id_int, date_or_None, sort_ts_or_None, remainder)``.
    ``id_int`` is ``0`` when the bullet has not been stamped yet (the short
    window between squash-merge and the ``main`` assign job). The id is the
    visible ``#N`` on ``changes`` listings.
    """
    remainder = text or ""
    change_id = 0
    id_match = _CHANGELOG_ID_PREFIX_RE.match(remainder)
    if id_match:
        # int() drops leading zeros so ``#042`` and ``#42`` compare equal.
        change_id = int(id_match.group(1))
        remainder = id_match.group(2)
    # Some legacy fragments put ``[ops]`` (or ``[ops] #N``) before the date.
    lead_tags = ""
    while True:
        tag_match = _CHANGELOG_LEAD_TAG_RE.match(remainder)
        if not tag_match:
            break
        lead_tags += tag_match.group(0)
        remainder = remainder[tag_match.end():]
    if not change_id:
        id_match = _CHANGELOG_ID_PREFIX_RE.match(remainder)
        if id_match:
            change_id = int(id_match.group(1))
            remainder = id_match.group(2)
    date, sort_ts, remainder = _strip_changelog_date_prefix(remainder)
    if lead_tags:
        remainder = f"{lead_tags}{remainder}"
    return change_id, date, sort_ts, remainder


def _changelog_sort_key(entry):
    """Window / visit-ceiling key: newest ``sort_ts``, then slug, then file_index."""
    date = entry.get("date") or _CHANGELOG_UNDATED
    sort_ts = entry.get("sort_ts") or _changelog_sort_ts_from_date(date)
    slug = (entry.get("slug") or "").lower()
    file_index = entry.get("file_index") or 0
    return (sort_ts, slug, file_index)


def _changelog_unread_cursor_token(entry):
    """Encode the last row of an unread page for the next ``changes unread``.

    Player lists scroll ``#N`` high-to-low (``_changelog_display_order_key``),
    not ``sort_ts`` + slug. A composite sort-key cursor could mark the whole
    same-second wave read or replay page one (bug reports 835 / 935).
    """
    change_id = int(entry.get("id") or 0)
    if change_id > 0:
        return f"id\t{change_id}"
    sort_ts, slug, file_index = _changelog_sort_key(entry)
    return f"unstamped\t{sort_ts}\t{slug}\t{file_index}"


def _changelog_unread_cursor_key(cursor):
    """Parse stored unread cursor; legacy bare timestamps still compare."""
    cur = str(cursor or "").strip()
    if not cur:
        return None
    if "\t" not in cur:
        return ("legacy_ts", cur)
    parts = cur.split("\t")
    kind = parts[0]
    if kind == "id" and len(parts) >= 2:
        try:
            return ("id", int(parts[1]))
        except ValueError:
            return None
    if kind == "unstamped":
        sort_ts = parts[1] if len(parts) > 1 else ""
        slug = (parts[2] if len(parts) > 2 else "").lower()
        try:
            file_index = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            file_index = 0
        return ("unstamped", sort_ts, slug, file_index)
    # Legacy composite sort_ts/slug/file_index (pre id-cursor saves).
    sort_ts = parts[0]
    slug = (parts[1] if len(parts) > 1 else "").lower()
    try:
        file_index = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        file_index = 0
    return ("legacy_composite", sort_ts, slug, file_index)


def _changes_entry_unread_after_cursor(entry, cur_key):
    """True when *entry* belongs on the next unread page after *cur_key*."""
    if cur_key is None:
        return True
    kind = cur_key[0]
    if kind == "legacy_ts":
        return str(entry.get("sort_ts") or "") < cur_key[1]
    if kind == "id":
        return int(entry.get("id") or 0) < cur_key[1]
    if kind == "unstamped":
        entry_key = _changelog_sort_key(entry)
        cursor_key = (cur_key[1], cur_key[2], cur_key[3])
        return entry_key < cursor_key
    if kind == "legacy_composite":
        entry_key = _changelog_sort_key(entry)
        cursor_key = (cur_key[1], cur_key[2], cur_key[3])
        return entry_key < cursor_key
    return True


def _changelog_display_order_key(entry, display_id_map=None):
    """List-line key: unstamped newest-first, then printed ``#N`` high-to-low.

    Player lists print a dense ``#N`` ranked by ``sort_ts`` (oldest visible
    ship = 1), not the raw ledger mint id. Two fragments can mint out of
    timestamp order when they land on different boots — ledger ``#3531``
    might be the 15:34 ship while dense ``#3531`` is the 15:35 ship. When
    *display_id_map* is set, order follows that printed number so
    timestamps and ``#N`` both descend. Only the recent ``changes`` /
    ``changes all`` feed should pass the map; unread paging must keep
    ledger-id order (closed ``id\\tN`` window). Staff ``changes ops``
    (no map) still uses the ledger id. Unstamped rows (id 0) stay at the
    top until boot mints them.
    """
    cid = int(entry.get("id") or 0)
    date = entry.get("date") or _CHANGELOG_UNDATED
    sort_ts = entry.get("sort_ts") or _changelog_sort_ts_from_date(date)
    slug = (entry.get("slug") or "").lower()
    if cid <= 0:
        # reverse=True: bucket 1 (unstamped) sorts above bucket 0 (numbered).
        return (1, sort_ts, slug)
    if display_id_map is not None:
        printed = _player_changes_display_n(entry, display_id_map)
        return (0, printed, sort_ts, slug)
    return (0, cid, sort_ts, slug)


def _changelog_arrange_display(entries, display_id_map=None):
    """Arrange a ``changes`` window high-to-low by the number on the line.

    Unstamped rows stay at the top of the page until copyover mints ``#N``.
    Pass the player dense-id map so the page matches printed ``#N`` /
    ``sort_ts``; omit it for staff ledger-id order.
    """
    return sorted(
        entries,
        key=lambda row: _changelog_display_order_key(
            row, display_id_map=display_id_map,
        ),
        reverse=True,
    )


def _changelog_display_stamp(entry):
    """Player-facing timestamp stamp for list/detail headers."""
    sort_ts = entry.get("sort_ts") or ""
    if sort_ts and not sort_ts.startswith(_CHANGELOG_UNDATED):
        if "T" in sort_ts:
            date, time_part = sort_ts.split("T", 1)
            return f"{date} {time_part[:5]}"
        return sort_ts[:10]
    date = entry.get("date") or _CHANGELOG_UNDATED
    if date == _CHANGELOG_UNDATED:
        return ""
    return date


def _changelog_entry_by_legacy_id(entries, change_id):
    """Return the Unreleased entry whose legacy stamped ``#N`` matches."""
    for entry in entries:
        if entry.get("id") == change_id:
            return entry
    return None


def _changelog_entry_by_id(entries, change_id):
    """Legacy alias — prefer ``_changelog_resolve_ref`` for new lookups."""
    return _changelog_entry_by_legacy_id(entries, change_id)


def _changelog_resolve_ref(entries, ref, *, display_id_map=None, ops_mode=False):
    """Resolve a ``changes detail`` reference to one loaded entry.

    Accepts (in order): dense player ``#N`` (player feed), legacy ledger ``#N``,
    full/partial ``sort_ts``, calendar date when unique, or a ``CHANGELOG.d``
    fragment slug.
    """
    token = (ref or "").strip()
    if not token:
        return None
    if token.startswith("#"):
        token = token[1:].strip()
    if token.isdigit():
        n = int(token)
        if ops_mode:
            legacy = _changelog_entry_by_legacy_id(entries, n)
            if legacy is not None:
                return legacy
        else:
            id_map = display_id_map or _player_changes_display_id_map(entries)
            hit = _player_changes_entry_by_display_n(entries, n, id_map)
            if hit is not None:
                return hit
            legacy = _changelog_entry_by_legacy_id(entries, n)
            if legacy is not None:
                return legacy
    token_lower = token.lower().replace(".md", "")
    for entry in entries:
        slug = (entry.get("slug") or "").lower()
        if slug and slug == token_lower:
            return entry
    ref_norm = token.replace(" ", "T")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ref_norm):
        ref_norm = f"{ref_norm}T00:00:00Z"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", token):
        date_part, time_part = token.split(" ", 1)
        ref_norm = f"{date_part}T{time_part}:00Z"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", ref_norm):
        ref_norm = f"{ref_norm}:00Z"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ref_norm):
        ref_norm = f"{ref_norm}Z"
    exact = []
    prefix = []
    for entry in entries:
        sort_ts = entry.get("sort_ts") or ""
        if not sort_ts:
            continue
        if sort_ts == ref_norm:
            exact.append(entry)
        elif sort_ts.startswith(ref_norm):
            prefix.append(entry)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        exact.sort(key=_changelog_sort_key, reverse=True)
        return exact[0]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        prefix.sort(key=_changelog_sort_key, reverse=True)
        return prefix[0]
    day_match = re.fullmatch(r"\d{4}-\d{2}-\d{2}", token)
    if day_match:
        day = day_match.group(0)
        day_hits = [
            entry for entry in entries
            if (entry.get("date") or "").startswith(day)
            or (entry.get("sort_ts") or "").startswith(day)
        ]
        if len(day_hits) == 1:
            return day_hits[0]
    return None


# Default ``changes`` / ``changes list`` size and the largest list scrollback.
_CHANGES_DEFAULT_LIMIT = 10
_CHANGES_MAX_LIST_COUNT = 100


def _changes_list_footer(*, ops_mode):
    """How to get a longer list and how to open one ship on this feed."""
    if ops_mode:
        return (
            "(changes ops all 25 — more headlines; "
            "type changes ops <#> to open one staff ship.)"
        )
    return (
        "(changes all 25 — more headlines; type the # number to open one ship.)"
    )


def _player_changes_display_id_map(entries):
    """Dense player ``#N`` for visible rows (oldest stamped ship = 1).

    Staff-only / heuristic-hidden ships are already filtered out of
    *entries* before this runs, so the in-game list no longer shows gaps
    between numbers when ops bullets share the old global id space or were
    mis-tagged at mint time. Ledger ids are unchanged for ``changes detail``.
    """
    stamped = [
        entry for entry in entries
        if int(entry.get("id") or 0) > 0
    ]
    stamped.sort(key=_changelog_sort_key)
    return {
        int(entry["id"]): rank
        for rank, entry in enumerate(stamped, start=1)
    }


def _player_changes_display_n(entry, display_id_map):
    """Player list ``#N`` (dense) or 0 when unstamped / no map."""
    ledger_id = int(entry.get("id") or 0)
    if not ledger_id or not display_id_map:
        return 0
    return int(display_id_map.get(ledger_id) or 0)


def _player_changes_entry_by_display_n(entries, n, display_id_map):
    """Open the player ship whose dense list number is *n*."""
    if n <= 0 or not entries or not display_id_map:
        return None
    for entry in entries:
        if _player_changes_display_n(entry, display_id_map) == n:
            return entry
    return None


def _changes_open_numeric_entry(entries, n, *, ops_mode=False):
    """Resolve bare ``changes <n>`` to one ship on this feed.

    Player feed: *n* is the dense ``#N`` printed on ``changes`` lines
    (oldest visible player ship = 1). Ops feed: *n* is the staff ledger id.
    When no dense match exists, fall back to the legacy ledger id so old
    bookmarks still open.
    """
    if n <= 0 or not entries:
        return None
    if ops_mode:
        return _changelog_entry_by_legacy_id(entries, n)
    display_id_map = _player_changes_display_id_map(entries)
    hit = _player_changes_entry_by_display_n(entries, n, display_id_map)
    if hit is not None:
        return hit
    return _changelog_entry_by_legacy_id(entries, n)


def _changes_send_detail(
    character,
    game,
    entry,
    *,
    is_gm=False,
    display_id_map=None,
    ops_mode=False,
):
    """Show one changelog entry's full text and bump the read watermark."""
    if entry is None:
        character.session.send("No matching change entry.")
        return
    body = _changelog_detail_body(entry)
    character.session.send(
        f"{_format_changes_detail_prefix(entry, is_gm=is_gm, display_id_map=display_id_map, ops_mode=ops_mode)} {body}".strip()
    )
    _changes_visit_ts_bump(game, character, entry.get("sort_ts"))


def _dedupe_changelog_entries_by_slug(entries):
    """Keep one in-game row per fragment slug (``CHANGELOG.d/<slug>.md``).

    Parallel PRs should use unique fragment paths; if two bullets share a
    slug, the newest ``sort_ts`` wins.
    """
    if not entries:
        return entries
    by_slug = {}
    no_slug = []
    for entry in entries:
        slug = (entry.get("slug") or "").strip().lower()
        if not slug:
            no_slug.append(entry)
            continue
        existing = by_slug.get(slug)
        if existing is None or _changelog_sort_key(entry) > _changelog_sort_key(existing):
            by_slug[slug] = entry
    deduped = list(by_slug.values()) + no_slug
    deduped.sort(key=_changelog_sort_key, reverse=True)
    return deduped


def _format_changes_list_prefix(
    entry, *, is_gm=False, unread=False, display_id_map=None, ops_mode=False,
):
    """Timestamp, optional #N, optional GM audience tags, optional category."""
    stamp = _changelog_display_stamp(entry)
    tags, _summary = _changelog_strip_leading_tags(entry.get("summary") or "")
    aud = _changelog_audience_tag_labels(tags, is_gm=is_gm)
    category = (entry.get("category") or "").strip()
    star = "*" if unread else ""
    change_id = int(entry.get("id") or 0)
    if ops_mode:
        list_number = change_id
    elif display_id_map is not None:
        list_number = _player_changes_display_n(entry, display_id_map)
    else:
        list_number = change_id
    parts = []
    if stamp:
        parts.append(f"{star}{stamp}" if star else stamp)
    elif star:
        parts.append(star)
    if list_number:
        parts.append(f"#{list_number}")
    parts.extend(aud)
    if category:
        parts.append(f"[{category}]")
    return " ".join(parts)


def _format_changes_detail_prefix(
    entry, *, is_gm=False, display_id_map=None, ops_mode=False,
):
    """Detail header: timestamp + optional GM tags/category (no unread star)."""
    return _format_changes_list_prefix(
        entry,
        is_gm=is_gm,
        unread=False,
        display_id_map=display_id_map,
        ops_mode=ops_mode,
    )


def _changes_watermark_holder(game, character):
    """Account (preferred) or character blob that stores the read watermark."""
    from engine import accounts as accounts_mod

    account = accounts_mod.account_for_character(game, character)
    return account if account is not None else character


def _changes_watermark_persist(game, character):
    """Flush changelog read state without a full world snapshot.

    Linked accounts get an immediate ``save_accounts`` pass (same pattern as
    ``account_login._persist_account_change``) so a deploy copyover before the
    next autosave pulse cannot replay the same ``changes unread`` backlog (bug
    reports 744 / 773). Unlinked bodies checkpoint one character row only;
    copyover still force-writes the whole world.

    Waits for the background persistence writer and retries on SQLITE_BUSY —
    silent failure here replays ``changes unread`` after the next copyover.
    """
    holder = _changes_watermark_holder(game, character)
    if holder is None:
        return
    from engine.persistence import (
        flush_accounts_now,
        flush_character_checkpoint_now,
        mark_account_dirty,
        mark_character_dirty,
    )

    db = getattr(game, "db", None)
    if hasattr(holder, "password_hash"):
        mark_account_dirty(game, holder)
        if db is not None:
            if not flush_accounts_now(db, game, reason="changes_watermark"):
                print(
                    "[changes] account watermark flush failed — "
                    "changes unread may replay after copyover",
                    flush=True,
                )
    else:
        mark_character_dirty(game, holder)
        if db is not None:
            if not flush_character_checkpoint_now(
                db, game, holder, reason="changes_watermark",
            ):
                print(
                    "[changes] character watermark flush failed — "
                    "changes unread may replay after copyover",
                    flush=True,
                )


def _changes_visit_ts_get(game, character, player_entries=None):
    """Return the visit ceiling — newest acknowledged ship ``sort_ts``."""
    holder = _changes_watermark_holder(game, character)
    if holder is None:
        return ""
    if player_entries is None:
        all_entries = _load_unreleased_entries()
        player_entries = _filter_changelog_entries_for_viewer(
            all_entries, character, mode="player",
        )
    if not getattr(holder, "changelog_visit_migrated", False):
        _migrate_changelog_visit_model(game, character, player_entries)
    sort_ts = str(getattr(holder, "last_seen_changelog_sort_ts", "") or "").strip()
    if sort_ts:
        return sort_ts
    old_id = int(getattr(holder, "last_seen_changelog_id", 0) or 0)
    if old_id > 0:
        legacy = _changelog_entry_by_legacy_id(_load_unreleased_entries(), old_id)
        if legacy and legacy.get("sort_ts"):
            holder.last_seen_changelog_sort_ts = legacy["sort_ts"]
            holder.changelog_visit_migrated = True
            _changes_watermark_persist(game, character)
            return legacy["sort_ts"]
    return ""


def _changes_watermark_get(game, character):
    """Legacy alias — prefer ``_changes_visit_ts_get``."""
    return _changes_visit_ts_get(game, character)


def _migrate_changelog_visit_model(game, character, player_entries):
    """One-time uplift from the old floor watermark to visit-ceiling semantics."""
    holder = _changes_watermark_holder(game, character)
    if holder is None or getattr(holder, "changelog_visit_migrated", False):
        return
    entries = list(player_entries or [])
    max_ts = max(
        (entry.get("sort_ts") or "" for entry in entries),
        default="",
    )
    old = str(getattr(holder, "last_seen_changelog_sort_ts", "") or "").strip()
    if old <= _CHANGELOG_WM_MIN or not old or old >= _CHANGELOG_WM_MAX:
        new_ts = max_ts
    else:
        newer = [
            entry for entry in entries
            if (entry.get("sort_ts") or "") > old
        ]
        if newer:
            new_ts = max(entry.get("sort_ts") or "" for entry in newer)
        else:
            new_ts = max_ts or old
    holder.last_seen_changelog_sort_ts = new_ts or _CHANGELOG_WM_MIN
    holder.changelog_visit_migrated = True
    _changes_watermark_persist(game, character)


def _changes_visit_ts_bump(game, character, sort_ts):
    """Raise the visit ceiling to at least *sort_ts* and queue persistence."""
    target = str(sort_ts or "").strip()
    holder = _changes_watermark_holder(game, character)
    if holder is None or not target:
        return
    holder.changelog_visit_migrated = True
    current = str(getattr(holder, "last_seen_changelog_sort_ts", "") or "").strip()
    if current and target <= current:
        return
    holder.last_seen_changelog_sort_ts = target
    _changes_unread_window_clear(game, character)
    _changes_watermark_persist(game, character)


def _changes_watermark_bump(game, character, sort_ts):
    """Legacy alias — prefer ``_changes_visit_ts_bump``."""
    _changes_visit_ts_bump(game, character, sort_ts)


def _changes_unread_cursor_get(game, character):
    """Return the unread paging cursor (low end of the already-listed window)."""
    holder = _changes_watermark_holder(game, character)
    if holder is None:
        return ""
    return str(
        getattr(holder, "last_seen_changelog_unread_cursor", "") or ""
    ).strip()


def _changes_unread_high_water_get(game, character):
    """Return the unread high-water (high end of the already-listed window)."""
    holder = _changes_watermark_holder(game, character)
    if holder is None:
        return ""
    return str(
        getattr(holder, "last_seen_changelog_unread_high_water", "") or ""
    ).strip()


def _unread_token_id(token):
    """Lookup ``#N`` encoded in an ``id\\tN`` paging token, or None."""
    key = _changelog_unread_cursor_key(token)
    if key and key[0] == "id":
        return key[1]
    return None


def _changes_unread_id_window(cursor_ts, high_water_ts):
    """Closed lookup-# interval when both bounds are ``id`` tokens, else None."""
    hw_id = _unread_token_id(high_water_ts)
    cur_id = _unread_token_id(cursor_ts)
    if hw_id is None or cur_id is None:
        return None
    if cur_id <= hw_id:
        return (cur_id, hw_id)
    return (hw_id, cur_id)


def _changes_entry_already_paged(entry, cursor_ts, high_water_ts=""):
    """True when *entry* sits inside the already-listed unread window.

    With both ``id`` bounds, a ship whose lookup number is **above** the
    high-water (a copyover that minted a new ``#N`` while you were paging)
    stays unread. Cursor-only legacy saves keep ``id < cursor`` (bug 935).
    Unstamped rows (id 0) are never inside a numbered window.
    """
    window = _changes_unread_id_window(cursor_ts, high_water_ts)
    if window is not None:
        eid = int(entry.get("id") or 0)
        if eid <= 0:
            return False
        lo_id, hi_id = window
        return lo_id <= eid <= hi_id
    cur_key = _changelog_unread_cursor_key(cursor_ts)
    return not _changes_entry_unread_after_cursor(entry, cur_key)


def _changes_unread_window_from_shown(shown, old_cursor="", old_hw=""):
    """Extend the paged ``#N`` window across *shown* without shrinking it."""
    numbered = [
        entry for entry in shown
        if int(entry.get("id") or 0) > 0
    ]
    if numbered:
        ids = [int(entry.get("id") or 0) for entry in numbered]
        shown_lo, shown_hi = min(ids), max(ids)
        old_lo = _unread_token_id(old_cursor)
        old_hi = _unread_token_id(old_hw)
        lo_id = shown_lo if old_lo is None else min(old_lo, shown_lo)
        hi_id = shown_hi if old_hi is None else max(old_hi, shown_hi)
        return f"id\t{lo_id}", f"id\t{hi_id}"
    arranged = _changelog_arrange_display(shown)
    if not arranged:
        return old_cursor, old_hw
    if _unread_token_id(old_cursor) is not None:
        # Mixed leftover: keep the numbered window; unstamped-only pages
        # do not move lookup-# bounds.
        return old_cursor, old_hw or old_cursor
    return (
        _changelog_unread_cursor_token(arranged[-1]),
        _changelog_unread_cursor_token(arranged[0]),
    )


def _changes_unread_window_set(game, character, cursor, high_water):
    """Persist both unread-window ends in one account/character flush."""
    holder = _changes_watermark_holder(game, character)
    if holder is None:
        return
    holder.last_seen_changelog_unread_cursor = str(cursor or "").strip()
    holder.last_seen_changelog_unread_high_water = str(high_water or "").strip()
    _changes_watermark_persist(game, character)


def _changes_unread_window_clear(game, character):
    """Drop both unread-window ends (full catch-up or visit ceiling advance)."""
    holder = _changes_watermark_holder(game, character)
    if holder is None:
        return
    cur = str(
        getattr(holder, "last_seen_changelog_unread_cursor", "") or ""
    ).strip()
    hw = str(
        getattr(holder, "last_seen_changelog_unread_high_water", "") or ""
    ).strip()
    if not cur and not hw:
        return
    holder.last_seen_changelog_unread_cursor = ""
    holder.last_seen_changelog_unread_high_water = ""
    _changes_watermark_persist(game, character)


def _changes_unread_cursor_set(game, character, sort_ts):
    """Persist the unread paging cursor without moving the visit ceiling."""
    target = str(sort_ts or "").strip()
    holder = _changes_watermark_holder(game, character)
    if holder is None or not target:
        return
    holder.last_seen_changelog_unread_cursor = target
    _changes_watermark_persist(game, character)


def _changes_unread_cursor_clear(game, character):
    """Drop paging cursor and high-water (full catch-up or visit advance)."""
    _changes_unread_window_clear(game, character)


def _changes_unread_pool(entries, visit_ts, cursor_ts, high_water_ts=""):
    """Ships still unread: above the visit ceiling, outside the paged window."""
    vt = str(visit_ts or "").strip()
    pool = []
    for entry in entries:
        ts = str(entry.get("sort_ts") or "")
        if vt and ts <= vt:
            continue
        if _changes_entry_already_paged(entry, cursor_ts, high_water_ts):
            continue
        pool.append(entry)
    return pool


def _changes_unread_ack_page(game, character, shown, pool, catalog=None):
    """Advance unread paging: window on partial pages, ceiling when finished."""
    if not shown:
        return
    if len(pool) > len(shown):
        old_cur = _changes_unread_cursor_get(game, character)
        old_hw = _changes_unread_high_water_get(game, character)
        new_cur, new_hw = _changes_unread_window_from_shown(
            shown, old_cur, old_hw,
        )
        _changes_unread_window_set(game, character, new_cur, new_hw)
        return
    ts_candidates = [
        entry.get("sort_ts") or _CHANGELOG_WM_MIN for entry in pool
    ]
    window = _changes_unread_id_window(
        _changes_unread_cursor_get(game, character),
        _changes_unread_high_water_get(game, character),
    )
    if window is not None:
        lo_id, hi_id = window
        for entry in catalog or shown:
            eid = int(entry.get("id") or 0)
            if eid and lo_id <= eid <= hi_id:
                ts_candidates.append(entry.get("sort_ts") or _CHANGELOG_WM_MIN)
    ceiling = max(ts_candidates) if ts_candidates else _CHANGELOG_WM_MIN
    _changes_unread_window_clear(game, character)
    _changes_visit_ts_bump(game, character, ceiling)


def _changes_visit_ts_bump_entries(game, character, entries):
    """Legacy helper — prefer ``_changes_unread_ack_page`` for unread lists."""
    if not entries:
        return
    ceiling = max(
        (entry.get("sort_ts") or _CHANGELOG_WM_MIN for entry in entries),
        default=_CHANGELOG_WM_MIN,
    )
    _changes_visit_ts_bump(game, character, ceiling)


def _changes_watermark_bump_entries(game, character, entries):
    """Legacy alias — prefer ``_changes_visit_ts_bump_entries``."""
    _changes_visit_ts_bump_entries(game, character, entries)


def _changes_visit_catchup(game, character, entries):
    """Mark every visible ship acknowledged (ceiling = newest in *entries*)."""
    _changes_unread_cursor_clear(game, character)
    if not entries:
        _changes_visit_ts_bump(game, character, _CHANGELOG_WM_MIN)
        return
    ceiling = max(
        (entry.get("sort_ts") or _CHANGELOG_WM_MIN for entry in entries),
        default=_CHANGELOG_WM_MIN,
    )
    _changes_visit_ts_bump(game, character, ceiling)


def _changes_entry_is_new(entry, visit_ts):
    """True when *entry* shipped after the viewer's visit ceiling."""
    vt = str(visit_ts or "").strip()
    if not vt:
        return False
    return (entry.get("sort_ts") or "") > vt


def _changes_new_entries(entries, visit_ts):
    """Player/staff-visible ships newer than the visit ceiling (newest first)."""
    return [
        entry for entry in entries
        if _changes_entry_is_new(entry, visit_ts)
    ]


def _changes_unread_entries(entries, watermark):
    """Legacy alias — ``watermark`` is now a visit ceiling, not a read floor."""
    return _changes_new_entries(entries, watermark)


def _changes_entries_matching_search(entries, term):
    """Case-insensitive filter: every whitespace-separated word must appear in summary."""
    needles = [w for w in (term or "").strip().lower().split() if w]
    if not needles:
        return []
    matched = []
    for entry in entries:
        summary = (entry.get("summary") or "").lower()
        if all(needle in summary for needle in needles):
            matched.append(entry)
    return matched


def _changes_render_list_lines(
    character,
    entries,
    *,
    title,
    visit_ts,
    flag_unread=False,
    is_gm=False,
    display_id_map=None,
    ops_mode=False,
    list_order="ledger",
):
    """Build the multi-line body for a ``changes`` list subcommand.

    ``list_order`` is ``ledger`` (mint id, default) or ``printed`` (dense
    player ``#N``). Unread / search / ops stay on ledger order because
    ``changes new`` paging is a closed ledger-id window (bugs 678 / 835 /
    935 / copyover +1). Rearranging those pages by dense ``#N`` is how
    earlier changelog ships undid unread. Only the recent ``changes`` /
    ``changes all`` feed passes ``printed``.
    """
    lines_out = [title]
    arrange_map = None
    if (
        list_order == "printed"
        and not ops_mode
        and display_id_map is not None
    ):
        arrange_map = display_id_map
    for entry in _changelog_arrange_display(
        entries, display_id_map=arrange_map,
    ):
        unread = flag_unread and _changes_entry_is_new(entry, visit_ts)
        prefix = _format_changes_list_prefix(
            entry,
            is_gm=is_gm,
            unread=unread,
            display_id_map=display_id_map,
            ops_mode=ops_mode,
        )
        summary = _changelog_display_summary(entry.get("summary") or "")
        lines_out.append(f"  {prefix} {summary}")
    return lines_out


def _parse_unreleased_entries(
    lines, *, fragment=False, file_index_start=0, slug="",
):
    """Parse Unreleased bullets from CHANGELOG.md or a CHANGELOG.d fragment.

    Each entry is a dict with ``category``, optional legacy ``id``, ``date``,
    ``sort_ts``, ``summary``, ``full``, ``file_index``, and ``slug``.
    """
    entries = []
    # Fragments are Unreleased-only files; the monolith uses a section gate.
    in_unreleased = bool(fragment)
    category = ""
    current = None  # open bullet so indented continuation lines extend it
    file_index = int(file_index_start)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if fragment:
                # Fragments should not carry release headings; ignore safely.
                continue
            if in_unreleased:
                break  # next top-level section ends Unreleased
            in_unreleased = stripped.startswith("## [Unreleased]")
            continue
        if not in_unreleased:
            continue
        if stripped.startswith("### "):
            # Keep a Changelog categories (Added/Changed/Fixed/Removed/
            # Security) are one word; a heading like "Fixed (v0.21 ...)"
            # just cross-references this same file, so drop everything
            # after that first word rather than repeating it as a tag.
            words = stripped[4:].split()
            category = words[0] if words else stripped[4:]
            current = None
            continue
        if line.startswith("- "):
            # Only a column-0 "- " starts a NEW bullet.
            bullet = stripped[2:]
            # Same-line bold: ``**lead-in.** rest``. Multi-line bold opens
            # with ``**`` here and closes on a later wrapped line -- there
            # is no closing ``**`` on this line, so fall through and strip
            # the opener so the date prefix is still visible to the stamp
            # regex.
            bold = re.match(r"\*\*(.+?)\*\*", bullet)
            if bold:
                lead = bold.group(1)
            elif bullet.startswith("**"):
                lead = bullet[2:]
                if not lead.endswith((".", "!", "?")):
                    lead = lead + " ..."
            elif bullet.endswith((".", "!", "?")):
                lead = bullet
            else:
                # No bold lead-in and the sentence continues on later
                # (indented) lines -- mark it as truncated rather than
                # silently cutting a sentence off mid-word. 'full' below
                # still carries the whole thing for 'changes detail'.
                lead = bullet + " ..."
            change_id, date, sort_ts, summary = _strip_changelog_stamps(lead)
            current = {
                "category": category,
                "id": change_id,
                "date": date or _CHANGELOG_UNDATED,
                "sort_ts": sort_ts,
                "summary": summary,
                "full": [bullet],
                "file_index": file_index,
                "slug": slug,
            }
            entries.append(current)
            file_index += 1
            continue
        # An indented continuation line is that same bullet's own prose, not
        # a separate change -- the short listing still skips it (that's what
        # keeps 'changes' one line per entry), but 'full' collects it so
        # 'changes detail <n>' can show the complete entry, not just its
        # first sentence.
        if current is not None and stripped:
            current["full"].append(stripped)

    # Newest ship timestamp first; slug/file_index break ties.
    entries.sort(key=_changelog_sort_key, reverse=True)
    return entries


def _changelog_repo_root():
    """Repo root from this module (engine/verbs/basic.py -> three hops)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _read_changelog_text(path):
    """Read a changelog file as UTF-8, falling back to cp1252 for legacy fragments.

    Some Windows-authored CHANGELOG.d fragments used cp1252 en-dashes (byte 0x97)
    instead of UTF-8. ``changes`` must not crash when one fragment is mis-encoded.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


_CHANGELOG_CACHE = None  # (cache_key_tuple, entries_list)


def _changelog_cache_key(root):
    """Cheap staleness signature for the in-memory entry cache.

    Does not ``stat`` every ``CHANGELOG.d`` fragment (that full per-file scan
    is reserved for ``tools/`` — see ``_changelog_signature``). A directory's
    own mtime changes whenever a file is added/removed/renamed inside it, and
    the ledger file's mtime changes whenever a new slug is minted (or an
    existing row is touched) — together those cover every case that changes
    what ``changes`` should show. The fragment *count* used to come from a
    fresh ``os.listdir`` on every tap; that walk is now memoized behind the
    same two-stat directory probe (plus a short TTL) in
    ``changelog_ledger.changelog_fragment_names``. In-place edits to an
    existing fragment's prose (rare — old ships are frozen) are not caught
    here; that trade-off matches the listing memo's behavior.
    """
    from engine import changelog_ledger

    def _stat(path):
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)

    probe = changelog_ledger.sources_dir_probe(root)
    # probe is (frag_dir_stat, changelog_md_stat)
    dir_key, main_key = probe[0], probe[1]
    ledger_key = _stat(changelog_ledger.db_path(root))
    count = changelog_ledger.changelog_source_file_count(root, probe=probe)
    return (main_key, dir_key, ledger_key, count)


def _changelog_cache_sources_unchanged(old_key, new_key):
    """True when markdown sources look the same and only the ledger mtime moved.

    Opening the ledger SQLite file (or minting a new slug) changes
    ``changelog.db`` mtime without any new fragment. Re-parsing several
    thousand markdown files for that case is wasted work — overlay ids onto
    the already-cached entries instead.
    """
    if not isinstance(old_key, tuple) or not isinstance(new_key, tuple):
        return False
    if len(old_key) < 4 or len(new_key) < 4:
        return False
    # (main_key, dir_key, ledger_key, count) — ignore ledger_key.
    return (
        old_key[0] == new_key[0]
        and old_key[1] == new_key[1]
        and old_key[3] == new_key[3]
    )


def _apply_ledger_ids(entries, root):
    """Overlay each fragment entry's id with the ledger's permanent id.

    The ledger (``engine/changelog_ledger.py``) is the single source of
    truth for ``#N`` going forward — this replaces the old "regex-parse a
    stamp someone wrote into the markdown text" approach that produced
    duplicate/never-numbered ids. Monolith rows (no fragment ``slug`` —
    frozen ``CHANGELOG.md`` history that never grows again) keep whatever
    id is already embedded in their own text; only ``CHANGELOG.d`` fragment
    rows, which is where new ships actually happen, get their id from the
    ledger. A slug with no ledger row yet (boot has not minted it) shows
    ``id: 0`` — the existing display logic already treats that as
    "unstamped, list at the top until minted," which self-heals on the next
    boot/idle poll without any special-casing here.
    """
    from engine import changelog_ledger

    slug_map = changelog_ledger.slug_id_map(root)
    for entry in entries:
        slug = changelog_ledger.canonical_fragment_slug(entry.get("slug") or "")
        if not slug:
            continue
        # Canonicalize in place so Discord mark_posted and later lookups
        # hit the lowercase ledger key even if the filename kept T/Z.
        entry["slug"] = slug
        hit = slug_map.get(slug)
        entry["id"] = hit[1] if hit else 0
    return entries


def _entries_from_ledger(root):
    """Build ``changes`` list rows from the changelog ledger (no fragment walk).

    ``full`` is left empty so ``changes N`` lazily reads one fragment file.
    """
    import json as json_mod

    from engine import changelog_ledger

    changelog_ledger.assign_new_slugs(root)
    rows = changelog_ledger.list_entries(root)
    if not rows:
        return []
    entries = []
    for row in rows:
        sort_ts = row.get("sort_ts") or ""
        if sort_ts and "T" in sort_ts:
            date = sort_ts.split("T", 1)[0]
        else:
            date = _CHANGELOG_UNDATED
        audience = None
        raw_aud = row.get("audience")
        if raw_aud:
            try:
                parsed = json_mod.loads(raw_aud)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                audience = parsed
        entries.append({
            "category": "",
            "id": int(row.get("id") or 0),
            "date": date,
            "sort_ts": sort_ts,
            "summary": row.get("summary") or "",
            "full": None,
            "file_index": 0,
            "slug": row.get("slug") or "",
            "audience": audience,
            "staff_only": (row.get("sequence") == "staff"),
        })
    _enrich_changelog_entries(entries)
    return entries


def _load_unreleased_entries(repo_root=None):
    """Load Unreleased entries for in-game ``changes``.

    List/unread rows come from the changelog ledger (``content/changelog.db``)
    so boot does not parse every ``CHANGELOG.d`` fragment. Fragment bodies
    load lazily when a player opens ``changes N``. Cached per-process until
    the cheap staleness signature changes.
    """
    global _CHANGELOG_CACHE
    root = repo_root or _changelog_repo_root()

    cache_key = _changelog_cache_key(root)
    if _CHANGELOG_CACHE is not None and _CHANGELOG_CACHE[0] == cache_key:
        return _CHANGELOG_CACHE[1]

    entries = _entries_from_ledger(root)
    if not entries:
        # Tools / first touch before mint: keep the markdown path so the
        # command still works without a Game() boot.
        entries = _load_changelog_entries_from_sources(root)
        _enrich_changelog_entries(entries)
        _apply_ledger_ids(entries, root)
    entries.sort(key=_changelog_display_order_key, reverse=True)
    _CHANGELOG_CACHE = (_changelog_cache_key(root), entries)
    return entries


def _changelog_signature(root):
    """File mtimes for CHANGELOG.md + CHANGELOG.d fragments (builder / CI only)."""
    paths = [os.path.join(root, "CHANGELOG.md")]
    frag_dir = os.path.join(root, "CHANGELOG.d")
    if os.path.isdir(frag_dir):
        for name in sorted(os.listdir(frag_dir)):
            if name.endswith(".md") and name.lower() != "readme.md":
                paths.append(os.path.join(frag_dir, name))
    sig = []
    for path in paths:
        try:
            st = os.stat(path)
            sig.append((path, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((path, None, None))
    return tuple(sig)


# fragment filename -> parsed entries. Fragments accumulate forever (one
# file per ship, never merged/archived); re-parsing every one of them from
# scratch whenever a *single* new fragment lands is what brought the
# in-game command lag back -- the single-threaded asyncio loop (hard rule
# 3) blocks for the whole regex pass over the multi-thousand-file backlog,
# on every deploy, for whichever player types ``changes`` first.
#
# Keyed by filename only, with no ``os.stat`` staleness check: an already
# shipped ``CHANGELOG.d`` fragment is frozen prose (old ships are never
# edited in place) -- ``_changelog_cache_key``'s docstring already accepts
# that exact trade-off for the outer cache, and it matters even more here
# because Docker's overlay2 storage driver makes a per-path ``stat()``
# call expensive; stat-ing the whole backlog on every cache miss undid
# most of the win from skipping the re-parse. A rename/delete still
# invalidates correctly because it changes the ``os.listdir`` name set.
_FRAGMENT_PARSE_CACHE: dict[str, list[dict]] = {}


def _parse_fragment_file(path, slug):
    """Parse one ``CHANGELOG.d`` fragment (cache miss only — see caller).

    ``file_index`` on the returned entries is local to this file (0, 1, …)
    instead of a running count across the whole backlog. That is at least
    as correct as the old cross-file counter: every sort/tie-break here
    already keys on ``slug`` first (unique per fragment), and the one
    lookup that re-derives ``file_index``
    (``_changelog_entry_full_lines``) re-parses this same file in
    isolation anyway, so it never depended on a global count either.
    """
    try:
        text = _read_changelog_text(path)
    except OSError:
        return []
    return _parse_unreleased_entries(
        text.splitlines(keepends=True), fragment=True, slug=slug,
    )


def _load_changelog_entries_from_sources(repo_root=None):
    """Parse CHANGELOG.md + CHANGELOG.d/*.md (slow path; used by index builder)."""
    root = repo_root or _changelog_repo_root()
    entries = []
    main_path = os.path.join(root, "CHANGELOG.md")
    try:
        main_entries = _parse_unreleased_entries(
            _read_changelog_text(main_path).splitlines(keepends=True),
        )
        entries.extend(main_entries)
    except OSError:
        pass

    frag_dir = os.path.join(root, "CHANGELOG.d")
    from engine import changelog_ledger

    names = changelog_ledger.changelog_fragment_names(root)
    for name in names:
        cached = _FRAGMENT_PARSE_CACHE.get(name)
        if cached is not None:
            entries.extend(cached)
            continue
        # Only the (rare) new/uncached fragment pays for path-building
        # and an actual parse -- everything already cached skips both.
        path = os.path.join(frag_dir, name)
        slug = changelog_ledger.canonical_fragment_slug(name)
        parsed = _parse_fragment_file(path, slug)
        _FRAGMENT_PARSE_CACHE[name] = parsed
        entries.extend(parsed)
    # Drop cache rows for fragments that no longer exist (renamed /
    # removed) so this dict does not grow unbounded in a long-lived
    # process.
    live_names = set(names)
    for stale_name in [n for n in _FRAGMENT_PARSE_CACHE if n not in live_names]:
        _FRAGMENT_PARSE_CACHE.pop(stale_name, None)

    entries.sort(key=_changelog_sort_key, reverse=True)
    return _dedupe_changelog_entries_by_slug(entries)


from engine.changelog_audience import (
    audience_tag_labels as _changelog_audience_tag_labels,
    display_summary as _changelog_display_summary,
    entry_audience as _changelog_entry_audience,
    entry_is_staff_only as _changelog_entry_is_staff_only,
    strip_leading_tags as _changelog_strip_leading_tags,
    visible_to_viewer as _changelog_visible_to_viewer,
)


def _enrich_changelog_entries(entries):
    """Precompute audience/staff flags once at load (not per ``changes`` tap).

    The old compiled JSON index baked these fields at build time. Markdown
    loads skipped that step, so every ``changes`` tap ran three full passes of
    ``entry_is_staff_only`` (heavy regex over summary + body) across the
    entire backlog — seconds of asyncio-loop blocking per tap.
    """
    for entry in entries:
        if entry.get("audience") is not None and "staff_only" in entry:
            continue
        tags, _ = _changelog_strip_leading_tags(entry.get("summary") or "")
        entry["audience"] = sorted(_changelog_entry_audience(tags))
        entry["staff_only"] = bool(_changelog_entry_is_staff_only(entry))
    return entries


def _changes_active_game():
    """Resolved active game package for this process (lazy ``game_select``)."""
    import game_select
    return game_select.game_name()


def _filter_changelog_entries_for_viewer(entries, character, *, mode="player"):
    """Drop bullets outside this game's audience and listing mode.

    ``mode`` controls staff-only visibility:

    - ``player`` (default) — hide ``[ops]`` / ``[docs]`` / ``[easter]`` and
      maintainer heuristics for **everyone**, including on-duty GMs playing the game.
    - ``ops`` — staff-only bullets for GMs (``changes ops``).
    - ``detail`` — player feed plus staff-only rows when the viewer is GM
      (``changes detail <n>`` lookup).
    """
    from engine.changelog_audience import entry_is_staff_only, strip_leading_tags
    from engine.changelog_audience import entry_audience as _entry_audience

    active_game = _changes_active_game()
    is_gm = _is_gm(character)
    filtered = []
    for entry in entries:
        audience = entry.get("audience")
        if audience is None:
            tags, _ = strip_leading_tags(entry.get("summary") or "")
            aud_set = _entry_audience(tags)
        else:
            aud_set = set(audience)
        if active_game not in aud_set:
            continue
        staff_only = entry.get("staff_only")
        if staff_only is None:
            staff_only = entry_is_staff_only(entry)
        if mode == "player" and staff_only:
            continue
        if mode == "ops":
            if not is_gm:
                continue
            if not staff_only:
                continue
        if mode == "detail" and staff_only and not is_gm:
            continue
        filtered.append(entry)
    return filtered


def changes_unread_player_entries(character, game):
    """Player-visible ships still unread (visit ceiling + paging window)."""
    all_entries = _load_unreleased_entries()
    player_entries = _filter_changelog_entries_for_viewer(
        all_entries, character, mode="player",
    )
    visit_ts = _changes_visit_ts_get(game, character, player_entries)
    cursor_ts = _changes_unread_cursor_get(game, character)
    high_water_ts = _changes_unread_high_water_get(game, character)
    return _changes_unread_pool(
        player_entries, visit_ts, cursor_ts, high_water_ts,
    )


def deliver_changes_unread_on_login(character, game):
    """One-line nudge for remaining unread ships (not the raw visit ceiling)."""
    session = getattr(character, "session", None)
    if session is None:
        return
    new_entries = changes_unread_player_entries(character, game)
    if not new_entries:
        return
    count = len(new_entries)
    latest = max(
        new_entries,
        key=lambda entry: str(entry.get("sort_ts") or _CHANGELOG_WM_MIN),
    )
    summary = _changelog_display_summary(latest.get("summary") or "")
    if len(summary) > 72:
        summary = summary[:69].rstrip() + "..."
    if count == 1:
        session.send(
            f"[NEWS] 1 new change since your last visit — {summary} "
            "(type changes or help changes)"
        )
    else:
        session.send(
            f"[NEWS] {count} new changes since your last visit — "
            f"newest: {summary} (type changes)"
        )


def _changelog_detail_body(entry):
    """Full text for ``changes detail`` without repeating the timestamp."""
    parts = list(_changelog_entry_full_lines(entry) or [])
    if parts:
        first = parts[0]
        bold = re.match(r"\*\*(.+?)\*\*(.*)$", first, re.DOTALL)
        if bold:
            _id, _date, _sort_ts, rest_lead = _strip_changelog_stamps(bold.group(1))
            rest_lead = _changelog_display_summary(rest_lead)
            rebuilt = rest_lead + bold.group(2)
            parts[0] = rebuilt.strip() or rest_lead
        elif first.startswith("**"):
            _id, _date, _sort_ts, remainder = _strip_changelog_stamps(first[2:])
            parts[0] = _changelog_display_summary(remainder)
        else:
            _id, _date, _sort_ts, remainder = _strip_changelog_stamps(first)
            parts[0] = _changelog_display_summary(remainder)
    return " ".join(parts).strip()


def _changelog_entry_full_lines(entry, repo_root=None):
    """Resolve ``full`` bullet lines for one index row (lazy v2 index)."""
    full = entry.get("full")
    if full:
        return list(full)
    root = repo_root or _changelog_repo_root()
    slug = (entry.get("slug") or "").strip()
    if slug:
        frag_path = os.path.join(root, "CHANGELOG.d", f"{slug}.md")
        try:
            frag_entries = _parse_unreleased_entries(
                _read_changelog_text(frag_path).splitlines(keepends=True),
                fragment=True,
                file_index_start=int(entry.get("file_index") or 0),
                slug=slug,
            )
        except OSError:
            frag_entries = []
        want_ts = entry.get("sort_ts")
        for frag_entry in frag_entries:
            if want_ts and frag_entry.get("sort_ts") == want_ts:
                return list(frag_entry.get("full") or [])
        if len(frag_entries) == 1:
            return list(frag_entries[0].get("full") or [])
        if frag_entries:
            return list(frag_entries[0].get("full") or [])
        # Ledger row exists (boot minted #N) but the fragment is gone --
        # typical after auto-deploy abort-held a tip and rolled the tree
        # back. Do not fall through to CHANGELOG.md Unreleased: every
        # ledger row uses file_index 0, which steals leftover monolith
        # bullets (live 2026-09-11: homestead-deconstruct opened as
        # "plans index rebuild").
        summary = (entry.get("summary") or "").strip()
        return [summary] if summary else []
    main_path = os.path.join(root, "CHANGELOG.md")
    try:
        main_entries = _parse_unreleased_entries(
            _read_changelog_text(main_path).splitlines(keepends=True),
            file_index_start=0,
        )
    except OSError:
        return []
    want_ts = entry.get("sort_ts")
    want_index = entry.get("file_index")
    for main_entry in main_entries:
        if want_ts and main_entry.get("sort_ts") == want_ts:
            return list(main_entry.get("full") or [])
        if want_index is not None and main_entry.get("file_index") == want_index:
            return list(main_entry.get("full") or [])
    return []


def cmd_changes(character, args, game):
    """A live player suggestion (suggestions.log, 2026-07-12): "the changelog
    should feed into an in-game 'changes' command like traditional MUDs,"
    instead of players having to go read CHANGELOG.md by hand.

    Parses fragment summaries from the changelog ledger (permanent ``#N``
    minted in ``engine/changelog_ledger.py``). Opening one ship still reads
    that fragment's markdown for the 1-2 sentence body.
    Watermark updates use incremental account/character dirty flags — not a
    full world save.

    Bare ``changes`` always lists the **10 most recent** player ships (global
    feed). Lines newer than your last visit show a ``*`` before the timestamp.
    Use ``changes new`` for only those ships, or ``changes catchup`` to mark
    everything read. Use ``changes all`` for longer scrollback.

    Shows each top-level '- **...**' BULLET under '## [Unreleased]' (plus
    fragment files under CHANGELOG.d/), tagged
    with its '### ' category (Fixed/Added/Changed/...), a ``YYYY-MM-DD``
    stamp, and a stable [n] number. A live-reported bug (bug_reports.log #7):
    this used to show the '### ' subsection HEADINGS themselves instead of
    the bullets underneath them -- since a category is repeated for every
    batch of related fixes (e.g. "Fixed", "Fixed (v0.21 live-feedback pass)"),
    that read as a wall of bare "- Fixed"/"- Changed" lines with no actual
    description of what changed.

    Each bullet is tagged with its ship timestamp (``YYYY-MM-DD HH:MM`` UTC)
    and a stable ``#N`` assigned on ``main`` after merge. The page is the
    newest ships by timestamp; player lines print a dense list number
    (no holes for hidden staff ships) and scroll that number high-to-low
    so the clock and the ``#`` both count down. Type ``changes 12`` (or
    ``changes #12``) to open the player ship numbered 12 on that list.
    Staff-only bullets keep ledger ids —
    GMs type ``changes ops 12`` to open one (scrollback is
    ``changes ops all 25``, matching ``changes all 25``). Longer player
    headlines: ``changes all 25``.

    New Unreleased ships add ``CHANGELOG.d/<slug>.md`` (not edit the top of
    CHANGELOG.md) so parallel PRs do not conflict.

    Suggestion #73: bullets tagged ``[ops]``, ``[docs]``, or ``[easter]`` (or that match
    maintainer heuristics when agents forget the tag) are hidden from the
    default list for **everyone** — players and on-duty GMs alike. Staff
    who want repo/deploy news type ``changes ops`` (GM only). ``changes detail``
    still resolves staff-only rows when the viewer is GM.

    Audience tags ``[supers]``, ``[basegame]``, ``[engine]``, or
    ``[classic]`` at the summary start (optionally after ``[ops]``) scope a
    bullet to that game package. Untagged bullets default to SUPERS.

    Usage:
      changes                 -- last 10 global ships (* marks new since visit)
      changes all [n]         -- scrollback (alias: changes list; n up to 100)
      changes <n>             -- open the player ship numbered n on the list
      changes ops             -- staff-only [ops]/[docs]/[easter] ships (GM only)
      changes ops <n>         -- open staff changelog #n (GM only)
      changes ops all [n]     -- staff scrollback (GM only)
      changes new [n]       -- ships since your last visit (alias: changes unread)
      changes unread [n]    -- same as changes new
      changes ops new [n]   -- new staff-only ships (GM only)
      changes catchup       -- mark everything read without listing
      changes search <words...> [n] -- filter summaries; each word must match (case-insensitive)
      changes detail <ref>  -- full text (timestamp, slug, or legacy #N)
      changes #<ref>        -- same full-text lookup (legacy #N ok)

    New ships show a ``*`` before their timestamp on ``changes`` / ``changes all``.
    The visit ceiling lives on your account (or this character when unlinked).
    """
    usage = (
        "Usage: changes | changes all [n] | changes <n> | changes ops | "
        "changes ops <n> | changes ops all [n] | "
        "changes new [n] | changes unread [n] | changes ops new [n] | "
        "changes search <word> [n] | "
        "changes catchup | changes detail <time|slug|id> | "
        "changes #<time|slug|id>"
    )
    raw = args.strip()
    raw_lower = raw.lower()

    all_entries = _load_unreleased_entries()
    if not all_entries and not os.path.isfile(
        os.path.join(_changelog_repo_root(), "CHANGELOG.md")
    ):
        character.session.send("No changelog available right now.")
        return

    is_gm = _is_gm(character)
    detail_entries = _filter_changelog_entries_for_viewer(
        all_entries, character, mode="detail",
    )
    player_entries = _filter_changelog_entries_for_viewer(
        all_entries, character, mode="player",
    )
    ops_entries = (
        _filter_changelog_entries_for_viewer(all_entries, character, mode="ops")
        if is_gm else []
    )
    player_display_id_map = _player_changes_display_id_map(player_entries)

    if raw_lower.startswith("detail"):
        if not detail_entries:
            character.session.send("Nothing unreleased right now -- all caught up.")
            return
        if raw_lower.startswith("details"):
            rest = raw[len("details"):].strip()
        else:
            rest = raw[len("detail"):].strip()
        if not rest:
            character.session.send("Usage: changes detail <time|slug|id>")
            return
        entry = _changelog_resolve_ref(
            detail_entries,
            rest,
            display_id_map=player_display_id_map,
            ops_mode=False,
        )
        if entry is None:
            character.session.send(f"No change matching {rest!r}.")
            return
        detail_ops = bool(entry.get("staff_only")) and is_gm
        _changes_send_detail(
            character,
            game,
            entry,
            is_gm=is_gm,
            display_id_map=None if detail_ops else player_display_id_map,
            ops_mode=detail_ops,
        )
        return

    ops_mode = False
    unread_mode = False
    search_mode = False
    search_rest = ""
    count_raw = ""
    if raw_lower.startswith("ops"):
        if not is_gm:
            character.session.send("Staff only.")
            return
        ops_mode = True
        rest = raw[len("ops"):].strip()
        if rest.lower().startswith("search"):
            search_mode = True
            search_rest = rest[len("search"):].strip()
        elif rest.lower() in ("unread", "new"):
            unread_mode = True
        elif rest.lower().startswith("unread "):
            unread_mode = True
            count_raw = rest[len("unread"):].strip()
        elif rest.lower().startswith("new "):
            unread_mode = True
            count_raw = rest[len("new"):].strip()
        elif rest:
            rest_lower = rest.lower()
            # Scrollback matches player ``changes all 25`` / ``changes list 25``.
            if rest_lower.startswith("list") or rest_lower.startswith("all"):
                if rest_lower.startswith("all"):
                    count_raw = rest[3:].strip()
                else:
                    count_raw = rest[4:].strip()
            else:
                token = rest[1:].strip() if rest.startswith("#") else rest
                try:
                    open_n = int(token)
                except ValueError:
                    entry = _changelog_resolve_ref(ops_entries, rest)
                    if entry is None:
                        character.session.send(f"No staff change matching {rest!r}.")
                        return
                    _changes_send_detail(character, game, entry, is_gm=True)
                    return
                if open_n <= 0:
                    character.session.send(f"{usage}  (n must be a positive number)")
                    return
                entry = _changes_open_numeric_entry(
                    ops_entries, open_n, ops_mode=True,
                )
                if entry is None:
                    character.session.send(
                        f"No staff change {open_n}. "
                        "Type changes ops for the recent list, or "
                        "changes ops all 25 for more headlines."
                    )
                    return
                _changes_send_detail(
                    character, game, entry, is_gm=True, ops_mode=True,
                )
                return
        entries = ops_entries
    else:
        entries = player_entries
        if raw_lower.startswith("search"):
            search_mode = True
            search_rest = raw[len("search"):].strip()

    if not entries:
        if ops_mode:
            character.session.send("No staff changelog entries right now.")
        else:
            character.session.send("Nothing unreleased right now -- all caught up.")
        return

    visit_ts = _changes_visit_ts_get(game, character, player_entries)

    if raw_lower in ("catchup", "mark"):
        _changes_visit_catchup(game, character, entries)
        character.session.send("Marked all visible changes as read.")
        return

    if search_mode:
        if not search_rest:
            character.session.send("Usage: changes search <word> [n]")
            return
        parts = search_rest.rsplit(None, 1)
        term = search_rest
        n = _CHANGES_DEFAULT_LIMIT
        if len(parts) == 2:
            try:
                n = int(parts[1])
                term = parts[0]
            except ValueError:
                term = search_rest
        if not term.strip():
            character.session.send("Usage: changes search <word> [n]")
            return
        if n <= 0:
            character.session.send(f"{usage}  (n must be a positive number)")
            return
        if n > _CHANGES_MAX_LIST_COUNT:
            character.session.send(
                f"{usage}  (scrollback is at most {_CHANGES_MAX_LIST_COUNT})"
            )
            return
        pool = _changes_entries_matching_search(entries, term)
        if not pool:
            scope = "staff " if ops_mode else ""
            character.session.send(
                f"No {scope}changes matching {term!r}."
            )
            return
        shown = pool[:n]
        title = (
            f"Staff changes matching {term!r} (listed by lookup number):"
            if ops_mode else
            f"Changes matching {term!r} (listed by lookup number):"
        )
        lines_out = _changes_render_list_lines(
            character,
            shown,
            title=title,
            visit_ts=visit_ts,
            flag_unread=True,
            is_gm=is_gm,
            display_id_map=None if ops_mode else player_display_id_map,
            ops_mode=ops_mode,
        )
        if len(pool) > len(shown):
            suffix = "changes ops search" if ops_mode else "changes search"
            lines_out.append(
                f"({len(pool) - len(shown)} more -- "
                f"{suffix} {term} {len(pool)}.)"
            )
        lines_out.append(_changes_list_footer(ops_mode=ops_mode))
        character.session.send("\n".join(lines_out))
        _changes_visit_ts_bump_entries(game, character, shown)
        return

    if not ops_mode:
        if raw_lower in ("unread", "new"):
            unread_mode = True
        elif raw_lower.startswith("unread "):
            unread_mode = True
            count_raw = raw[len("unread"):].strip()
        elif raw_lower.startswith("new "):
            unread_mode = True
            count_raw = raw[len("new"):].strip()

    n = _CHANGES_DEFAULT_LIMIT
    if unread_mode:
        cursor_ts = _changes_unread_cursor_get(game, character)
        high_water_ts = _changes_unread_high_water_get(game, character)
        pool = _changes_unread_pool(
            entries, visit_ts, cursor_ts, high_water_ts,
        )
        if not pool and (
            cursor_ts or high_water_ts
        ) and _changes_new_entries(entries, visit_ts):
            # Stale legacy cursors could empty the pool while ships remain
            # unread, trapping players in a read/caught-up loop (bug 935).
            _changes_unread_window_clear(game, character)
            pool = _changes_unread_pool(entries, visit_ts, "", "")
        if not pool:
            _changes_unread_window_clear(game, character)
            character.session.send(
                "No new changes since your last visit — you are caught up. "
                "(Type changes for the recent list.)"
            )
            return
        if count_raw:
            try:
                n = int(count_raw)
            except ValueError:
                character.session.send(usage)
                return
            if n <= 0:
                character.session.send(f"{usage}  (n must be a positive number)")
                return
        shown = pool[:n]
        title = (
            "New staff changes since your last visit (listed by lookup number):"
            if ops_mode else
            "New changes since your last visit (listed by lookup number):"
        )
        lines_out = _changes_render_list_lines(
            character,
            shown,
            title=title,
            visit_ts=visit_ts,
            flag_unread=False,
            is_gm=is_gm,
            display_id_map=None if ops_mode else player_display_id_map,
            ops_mode=ops_mode,
        )
        if len(pool) > len(shown):
            suffix = "changes ops new" if ops_mode else "changes new"
            lines_out.append(
                f"({len(pool) - len(shown)} more new — "
                f"{suffix} {len(pool)} or changes catchup.)"
            )
        lines_out.append(_changes_list_footer(ops_mode=ops_mode))
        character.session.send("\n".join(lines_out))
        _changes_unread_ack_page(
            game, character, shown, pool, catalog=entries,
        )
        return

    if raw and not ops_mode:
        if raw.startswith("#"):
            rest = raw[1:].strip()
            if not rest:
                character.session.send(usage)
                return
            entry = _changelog_resolve_ref(
                detail_entries,
                rest,
                display_id_map=player_display_id_map,
                ops_mode=False,
            )
            if entry is None:
                character.session.send(f"No change matching {rest!r}.")
                return
            detail_ops = bool(entry.get("staff_only")) and is_gm
            _changes_send_detail(
                character,
                game,
                entry,
                is_gm=is_gm,
                display_id_map=None if detail_ops else player_display_id_map,
                ops_mode=detail_ops,
            )
            return
        if raw_lower.startswith("list") or raw_lower.startswith("all"):
            if raw_lower.startswith("all"):
                count_raw = raw[3:].strip()
            else:
                count_raw = raw[4:].strip()
        else:
            try:
                open_n = int(raw)
            except ValueError:
                entry = _changelog_resolve_ref(
                    detail_entries,
                    raw,
                    display_id_map=player_display_id_map,
                    ops_mode=False,
                )
                if entry is not None:
                    detail_ops = bool(entry.get("staff_only")) and is_gm
                    _changes_send_detail(
                        character,
                        game,
                        entry,
                        is_gm=is_gm,
                        display_id_map=None if detail_ops else player_display_id_map,
                        ops_mode=detail_ops,
                    )
                    return
                character.session.send(usage)
                return
            if open_n <= 0:
                character.session.send(f"{usage}  (n must be a positive number)")
                return
            entry = _changes_open_numeric_entry(
                player_entries, open_n, ops_mode=False,
            )
            if entry is None:
                if is_gm and _changes_open_numeric_entry(
                    ops_entries, open_n, ops_mode=True,
                ) is not None:
                    character.session.send(
                        f"No player change {open_n}. "
                        f"That number is a staff ship — type changes ops {open_n}."
                    )
                    return
                character.session.send(
                    f"No change {open_n}. "
                    "Type changes for the recent list, or changes all 25 "
                    "for more headlines."
                )
                return
            _changes_send_detail(
                character,
                game,
                entry,
                is_gm=is_gm,
                display_id_map=player_display_id_map,
                ops_mode=False,
            )
            return

    n = _CHANGES_DEFAULT_LIMIT
    if count_raw:
        try:
            n = int(count_raw)
        except ValueError:
            character.session.send(usage)
            return
        if n <= 0:
            character.session.send(f"{usage}  (n must be a positive number)")
            return
        if n > _CHANGES_MAX_LIST_COUNT:
            character.session.send(
                f"{usage}  (scrollback is at most {_CHANGES_MAX_LIST_COUNT})"
            )
            return

    # Player recent-feed slice: newest *n* by printed #N (sort_ts rank),
    # not the *n* highest ledger mint ids. Do not reorder ``entries``
    # itself — ``changes new`` paging uses a closed ledger-# window.
    if ops_mode:
        shown = entries[:n]
    else:
        feed = _changelog_arrange_display(
            entries, display_id_map=player_display_id_map,
        )
        shown = feed[:n]
    new_count = len(_changes_new_entries(entries, visit_ts))
    if ops_mode:
        title = "Recent staff changes (listed by lookup number):"
        if new_count:
            title = (
                f"Recent staff changes (listed by lookup number; "
                f"{new_count} new — try changes ops new):"
            )
    else:
        title = "Recent changes (listed by lookup number):"
        if new_count:
            title = (
                f"Recent changes (listed by lookup number; "
                f"{new_count} new — try changes new or changes catchup):"
            )
    lines_out = _changes_render_list_lines(
        character,
        shown,
        title=title,
        visit_ts=visit_ts,
        flag_unread=True,
        is_gm=is_gm,
        display_id_map=None if ops_mode else player_display_id_map,
        ops_mode=ops_mode,
        list_order="ledger" if ops_mode else "printed",
    )
    lines_out.append(_changes_list_footer(ops_mode=ops_mode))
    character.session.send("\n".join(lines_out))


_HELP_CONTINUATION_SUFFIXES = ("more", "lore", "extra")


def _split_help_continuation(verb, related):
    """Split a peeled 'See also:' string into (continued, related).

    `tools/help_chunk_pass.py` splits long hubs into "<verb> more" /
    "<verb> lore" / "<verb> extra" child pages, linked back only via a
    generic See-also footer -- so ``help cases`` -> ``help cases more``
    rendered identically to ``help cases`` -> ``help detective``, even
    though the first is "the rest of this page" and the second is a
    genuinely different topic (suggestion 317: players read the two as
    unrelated commands). Pull this page's own child(ren) out of the
    RELATED list into a separate CONTINUED line so the frame marks that
    distinction instead of losing it in a flat pipe-separated list.
    """
    if not related:
        return None, related
    base = verb.strip().lower()
    continued, rest = [], []
    for entry in (e.strip() for e in related.split("|")):
        if not entry:
            continue
        target = entry[5:].strip() if entry.lower().startswith("help ") else entry
        suffix = target.lower()[len(base):].strip() if target.lower().startswith(base + " ") else None
        if suffix in _HELP_CONTINUATION_SUFFIXES:
            continued.append(entry)
        else:
            rest.append(entry)
    continued_str = " | ".join(continued) if continued else None
    rest_str = " | ".join(rest) if rest else None
    return continued_str, rest_str


_HELP_OVERFLOW_SUFFIXES = (" more", " lore", " extra")


def _help_overflow_parent(keyword):
    """Hub key for a leftover split page (``angel more`` -> ``angel``).

    ``help more`` (the pager topic) does not match -- it does not end
    with a leading-space suffix. Suggestion 317 / bug report 955:
    after a GM hedit's the hub into one page, typing the old part-2
    name should show that hub (overlay first), not a stale git child.
    """
    low = (keyword or "").strip().lower()
    for suffix in _HELP_OVERFLOW_SUFFIXES:
        if low.endswith(suffix) and len(low) > len(suffix):
            return low[: -len(suffix)].strip()
    return None


def _page_help(character, framed):
    """Send a framed help tome through the line pager.

    Long help is one page plus ``more`` / ``stop``, sized by
    ``config pager`` -- the same transport as ``gm where``, not a second
    ``help X more`` command (suggestion 317).
    """
    from engine import pager as pager_mod

    pager_mod.page(character, framed, block=True)


def _help_lookup_keys(verb):
    """Yield topic keys to try for a player-typed help query.

    Exact first, then spaces/hyphens folded to underscores (``help no
    fixed address`` -> ``no_fixed_address``), then underscores turned
    into spaces (``help dual_wield`` still finds a spaced alias page).
    """
    from engine.player_input import fold_catalog_id

    raw = (verb or "").strip().lower()
    if not raw:
        return
    folded = fold_catalog_id(raw)
    seen = set()
    for candidate in (
        raw,
        folded,
        folded.replace("_", "-") if folded else "",
        raw.replace("_", " "),
        raw.replace("-", " "),
    ):
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield candidate


def _help_prefix_matches(verb, topics, is_gm_viewer):
    """Topic keys that start with the typed query (partial match list)."""
    from engine import hooks as hooks_mod

    raw = (verb or "").strip().lower()
    if not raw or len(raw) < 2:
        return []
    hyphen = raw.replace(" ", "-")
    under = raw.replace(" ", "_")
    hits = []
    seen = set()
    for key in topics:
        low = str(key or "").strip().lower()
        if not low or low in seen:
            continue
        if hooks_mod.is_gm_only_help(low) and not is_gm_viewer:
            continue
        if (
            low.startswith(raw)
            or low.startswith(hyphen)
            or low.startswith(under)
        ):
            seen.add(low)
            hits.append(low)
    hits.sort()
    return hits


def _send_help_disambiguation(character, verb, keys, help_w, sr):
    """List several help pages instead of silently opening one."""
    from engine import style

    unique = []
    seen = set()
    for key in keys:
        low = str(key or "").strip().lower()
        if not low or low in seen:
            continue
        seen.add(low)
        unique.append(low)
    unique.sort()
    lines = [
        f"Several help pages match '{verb}'. Type one:",
    ]
    for key in unique[:12]:
        lines.append(f"  help {key}")
    if len(unique) > 12:
        lines.append(f"  … and {len(unique) - 12} more.")
    framed = style.format_tome(
        "help -- which page?",
        lines,
        related="help topics",
        width=help_w,
        screenreader=sr,
    )
    _page_help(character, framed)


def _resolve_help_topic(verb, topics, db, is_gm_viewer, game=None):
    """Return ``('db', entry)``, ``('static', (key, body))``, or ``(None, None)``.

    Lookup order: exact hedit overlay (unless skipped -- stale shrink-stub,
    or gamewide ``gm helpmode git`` when git differs), then parent overlay
    with the same skip, then parent static, then typed static key.
    """
    from engine import help_db
    from engine import help_overlay_mode as help_overlay_mode_mod
    from engine import hooks as hooks_mod

    for key in _help_lookup_keys(verb):
        if db is not None:
            entry = help_db.get_entry(db, key, is_gm=is_gm_viewer)
            static_body = topics.get(key)
            if entry and help_overlay_mode_mod.skip_overlay_at_help_lookup(
                entry.get("body_text"),
                static_body,
                game=game,
            ):
                entry = None
            if entry:
                return "db", entry
        if key == verb:
            parent = _help_overflow_parent(verb)
            if parent:
                if db is not None:
                    entry = help_db.get_entry(db, parent, is_gm=is_gm_viewer)
                    static_parent = topics.get(parent)
                    if entry and help_overlay_mode_mod.skip_overlay_at_help_lookup(
                        entry.get("body_text"),
                        static_parent,
                        game=game,
                    ):
                        entry = None
                    if entry:
                        return "db", entry
                topic = topics.get(parent)
                if topic and not (
                    hooks_mod.is_gm_only_help(parent) and not is_gm_viewer
                ):
                    return "static", (parent, topic)
        topic = topics.get(key)
        if topic and not (hooks_mod.is_gm_only_help(key) and not is_gm_viewer):
            return "static", (key, topic)
    return None, None


def _send_static_help_page(
    character, verb, topic, *, help_w, sr, preamble=None,
    rewrite_staff_refs=False,
):
    """Frame a git-tracked HELP_TOPICS body and page it.

    ``preamble`` -- optional lines inserted after the title (used by
    ``oldhelp`` to mark a frozen archive page without stealing the
    topic's first-line title).
    ``rewrite_staff_refs`` -- rewrite player-facing ``help gm`` pointers
    to ``gmhelp`` when a staff page is shown from the staff catalog.
    """
    from engine import style

    body = topic.strip("\n")
    if rewrite_staff_refs:
        from engine import help_audience as help_audience_mod
        body = help_audience_mod.rewrite_staff_help_refs(body)
    related = None
    body_lines = body.split("\n")
    last = body_lines[-1].strip().lower() if body_lines else ""
    if last.startswith("see also:"):
        related = body_lines[-1].strip()[9:].strip()
        body_lines = body_lines[:-1]
    elif last.startswith("see:"):
        related = body_lines[-1].strip()[4:].strip()
        body_lines = body_lines[:-1]
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
    title = verb
    if body_lines and body_lines[0].strip():
        first = body_lines[0].strip()
        if " -- " in first or first.lower().startswith(verb):
            title = first
            body_lines = body_lines[1:]
            while body_lines and not body_lines[0].strip():
                body_lines.pop(0)
    if preamble:
        extra = str(preamble).strip("\n").split("\n")
        body_lines = extra + [""] + body_lines
    _continued, related = _split_help_continuation(verb, related)
    # Split part-2 pages are retired; leftover See also children are not
    # a continuation command (suggestion 317).
    framed = style.format_tome(
        title, body_lines, related=related, continued=None,
        width=help_w,
        screenreader=sr,
    )
    _page_help(character, framed)


def _format_help_db_entry(character, entry, *, rewrite_staff_refs=False):
    """Render one help_db row through the same Blood & Velvet tome framing
    static HELP_TOPICS pages use, so a DB-overlay page (engine/help_db.py,
    written with 'hedit') looks no different from a hand-authored one.
    ``syntax_block`` -- kept isolated from the narrative body in the DB --
    becomes a labeled "Syntax:" section ahead of the prose, still going
    through the one screenreader-aware formatter rather than a bespoke one.

    Frame width follows ``config width`` (prefs #3) so a wide client is not
    stuck at the classic 67-column Blood & Velvet ceiling.
    """
    from engine import display_prefs, style
    body_text = entry["body_text"] or ""
    if rewrite_staff_refs:
        from engine import help_audience as help_audience_mod
        body_text = help_audience_mod.rewrite_staff_help_refs(body_text)
    body_lines = []
    if entry["syntax_block"]:
        body_lines.append("Syntax:")
        body_lines.extend(entry["syntax_block"].split("\n"))
        body_lines.append("")
    body_lines.extend(body_text.split("\n"))
    title = entry["primary_keyword"]
    if entry["is_ic"]:
        title = f"{title} [IC]"
    return style.format_tome(
        title, body_lines,
        width=display_prefs.sheet_width(character),
        screenreader=bool(getattr(character, "screenreader", False)),
    )


def _staff_help_db_entry(db, verb, topics):
    """Return a hedit overlay marked gm_only, or None."""
    if db is None or not verb:
        return None
    from engine import help_db

    entry = help_db.get_entry(db, verb, is_gm=True)
    if not entry or not entry.get("gm_only"):
        return None
    if help_db.overlay_is_stale_stub(
        entry.get("body_text"), topics.get(verb),
    ):
        return None
    return entry


def _should_redirect_help_to_gmhelp(verb, topics, db, commands):
    """True when ``help <verb>`` is a staff page a GM should open via gmhelp.

    Shared player hubs (jargon, npcs) stay on ``help`` even when gmhelp
    also has a page or index row for the same name.
    """
    from engine import hooks as hooks_mod
    from engine import help_audience as help_audience_mod

    low = (verb or "").strip().lower()
    if not low:
        return False
    if low in ("gmtopics", "gmhelp"):
        return True
    if hooks_mod.is_gm_only_help(low):
        return True
    if " " in low:
        head = low.split(None, 1)[0]
        if head in ("gmtopics", "gmhelp") or hooks_mod.is_gm_only_help(head):
            return True
    if _staff_help_db_entry(db, low, topics):
        return True
    entry = (commands or {}).get(low)
    if entry:
        _handler, help_text = entry
        if help_audience_mod.is_staff_command_help(help_text):
            return True
    return False


def cmd_gmhelp(character, args, game, *, from_help=False):
    """Staff help catalog: GM/Builder runbooks and technical jargon.

    Player ``help`` never serves these pages, even to ranked staff, so a
    GM can still read the handbook as a player would. Muscle memory
    ``help gm`` quietly opens the matching gmhelp page.
    """
    from engine import display_prefs, style
    from engine import help_audience as help_audience_mod
    from engine import hooks as help_hooks
    from commands import COMMANDS

    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    verb = args.strip().lower()
    verb = verb.strip(" \t\"'`.,;:!?()[]{}")
    topics = get_help_topics()
    gm_categories = help_hooks.get_gmhelp_categories()
    overrides = help_hooks.get_gmhelp_topic_overrides() or {}
    help_w = display_prefs.sheet_width(character)
    sr = bool(getattr(character, "screenreader", False))
    db = getattr(game, "db", None)

    if from_help:
        character.session.send(
            "Staff pages live under gmhelp (player handbook stays on help)."
        )

    def _show_static(key, body):
        _send_static_help_page(
            character, key, body, help_w=help_w, sr=sr,
            rewrite_staff_refs=True,
        )
        from engine import hooks
        hooks.after_help_topic(character, key, game)

    def _show_db(entry):
        _page_help(
            character,
            _format_help_db_entry(
                character, entry, rewrite_staff_refs=True,
            ),
        )
        from engine import hooks
        hooks.after_help_topic(
            character, entry.get("primary_keyword") or verb, game,
        )

    if verb in ("topics", "index", "catalog", "all", "gmtopics"):
        alpha = help_audience_mod.sort_help_entries(
            help_audience_mod.flatten_help_categories(gm_categories)
        )
        lines = [""]
        lines.extend(style.format_help_alphabetical_index(
            alpha,
            title="GM Help Index",
            width=help_w,
            screenreader=sr,
            hint=(
                " A-Z staff catalog.  Type 'gmhelp <name>' for a page.  "
                "'help' for the player handbook."
            ),
            tts_hint=(
                "Type gmhelp followed by a name for one staff page. "
                "Type help for the player handbook"
            ),
        ))
        _page_help(character, "\r\n".join(lines).rstrip("\n"))
        return

    if not verb:
        lines = [""]
        lines.extend(style.format_help_index(
            gm_categories,
            width=help_w,
            screenreader=sr,
            title="GM Help Index",
            hint=(
                " Type 'gmhelp <name>' for a staff page.  "
                "'help' for the player handbook."
            ),
            tts_hint=(
                "Type gmhelp followed by a name for one staff page. "
                "Type help for the player handbook"
            ),
        ))
        _page_help(character, "\r\n".join(lines).rstrip("\n"))
        return

    if verb in overrides:
        _show_static(verb, overrides[verb])
        return

    kind, payload = _resolve_help_topic(verb, topics, db, True, game)
    if kind == "db":
        _show_db(payload)
        return
    if kind == "static":
        resolved_verb, topic = payload
        if (
            help_hooks.is_gm_only_help(resolved_verb)
            or resolved_verb in overrides
        ):
            body = overrides.get(resolved_verb, topic)
            _show_static(resolved_verb, body)
            return

    if " " in verb:
        head, query = verb.split(None, 1)
        if query.strip().lower() not in _HELP_CONTINUATION_SUFFIXES:
            page = overrides.get(head) or topics.get(head)
            if page is not None and (
                help_hooks.is_gm_only_help(head) or head in overrides
            ):
                from engine.help_filter import filter_topic_lines
                page = help_audience_mod.rewrite_staff_help_refs(page)
                filtered = filter_topic_lines(page, query)
                if filtered:
                    title = f"{head} -- matches for {query}"
                    note = [
                        f"Showing matches for '{query}' on gmhelp {head}.",
                        f"Full page: gmhelp {head}.",
                    ]
                    if head == "gm":
                        note.append(f"Staff verb search: gm find {query}")
                    note.append("")
                    framed = style.format_tome(
                        title, note + filtered, related=f"gmhelp {head}",
                        width=help_w,
                        screenreader=sr,
                    )
                    _page_help(character, framed)
                    from engine import hooks
                    hooks.after_help_topic(character, head, game)
                    return
                miss = (
                    f"No lines on gmhelp {head} match '{query}'. "
                    f"Type gmhelp {head} for the full page."
                )
                if head == "gm":
                    miss += (
                        f" Staff: gm find {query} searches the live verb list."
                    )
                character.session.send(miss)
                return

    cmd_entry = COMMANDS.get(verb)
    if cmd_entry:
        _handler, help_text = cmd_entry
        if help_audience_mod.is_staff_command_help(help_text):
            framed = style.format_tome(
                verb, [help_text], related="gmhelp",
                width=help_w,
                screenreader=sr,
            )
            _page_help(character, framed)
            return

    if db is not None:
        from engine import help_db
        from engine import hooks as hooks_mod
        fts_entry = None
        if verb not in hooks_mod.get_help_fts_blocklist():
            fts_entry = help_db.search_fts(db, verb, is_gm=True)
        if fts_entry and fts_entry.get("gm_only"):
            _show_db(fts_entry)
            return

    player_page = topics.get(verb)
    if player_page and not help_hooks.is_gm_only_help(verb):
        character.session.send(
            f"No staff page for '{verb}'. Player handbook: help {verb}."
        )
        return

    message = (
        f"No such staff topic: '{verb}'. "
        "Try gmhelp for the catalog, gmhelp jargon for internals, "
        "or gm find <word> for a live verb."
    )
    character.session.send(message)


def cmd_help(character, args, game):
    """System help: bare 'help' lists categorized HELP_TOPICS; 'help <name>'
    shows a multi-line topic page, or falls back to a command's one-liner
    from COMMANDS.

    Topic pages and the index use Blood & Velvet tome framing
    (docs/plans/colorandformattingforgame.R). This is deliberately separate
    from 'commands' (cmd_commands), which lists every verb.

    Lookup order (docs/plans/helpfile_editing_system.md): a hot-editable
    DB-overlay page (engine.help_db) wins over everything -- a GM can
    'hedit' a live typo fix or a brand-new page without a deploy -- then
    the static HELP_TOPICS page. Overflow names (``help angel more``)
    follow the parent hub so a hedit rewrite of ``angel`` is what
    players read. Long output goes through the line pager (``more`` /
    ``stop``, ``config pager``). Then ``help <topic> <query>`` filters
    that topic's body (so ``help combat dodge`` filters the combat page
    instead of looking up a missing page named "combat dodge"), then a bare
    COMMANDS one-liner when the verb is dispatchable (so ``help scare``
    never FTS-hijacks to ``help paths``), then a DB full-text search hit
    for unknown words, and finally a DB fuzzy "did you mean" before giving
    up and logging the miss. Staff pages (``help gm``, Builder, hedit
    gm-only overlays) redirect ranked staff to ``gmhelp``; players miss.
    """
    from engine import display_prefs, style
    # Local import: COMMANDS is assembled in commands.py from this very
    # package plus supers.verbs -- importing it at module level here would
    # be circular (commands.py is what imports engine.verbs in the first
    # place). By the time a player can type 'help', commands.py has long
    # since finished loading.
    from commands import COMMANDS

    verb = args.strip().lower()
    # Strip wrapping/trailing punctuation so help leviathan' / help "vampire"
    # / help lodging') still finds the topic (common telnet typos from
    # live help_misses).
    verb = verb.strip(" \t\"'`.,;:!?()[]{}")
    topics = get_help_topics()
    categories = get_help_categories()
    # Player handbook: never serve GM/Builder pages here, even to staff.
    # Ranked staff type gmhelp (help gm still redirects).
    is_gm_rank = _is_gm(character)
    is_gm_viewer = False
    from engine import help_audience as help_audience_mod
    visible_categories = help_audience_mod.filter_help_categories(
        categories, is_gm=False,
    )
    # Frame budget for every help path (prefs #3) -- matches score / who.
    help_w = display_prefs.sheet_width(character)
    sr = bool(getattr(character, "screenreader", False))

    # Full categorized catalog -- screenreader bare help is Start Here only,
    # so these verbs recover the complete index for TTS users who want it.
    if verb in ("topics", "index", "catalog", "all"):
        from engine import help_audience as help_audience_mod
        alpha = help_audience_mod.sort_help_entries(
            help_audience_mod.flatten_help_categories(visible_categories)
        )
        lines = [""]
        lines.extend(style.format_help_alphabetical_index(
            alpha,
            title="Help Index",
            width=help_w,
            screenreader=sr,
        ))
        _page_help(character, "\r\n".join(lines).rstrip("\n"))
        return

    if verb == "gmtopics":
        if is_gm_rank:
            cmd_gmhelp(character, "topics", game, from_help=True)
            return
        character.session.send(
            "No such command or topic: 'gmtopics'. "
            "Try 'help' for topics, or 'commands' for the verb list."
        )
        return

    if verb:
        db = getattr(game, "db", None)
        kind, payload = _resolve_help_topic(
            verb, topics, db, is_gm_viewer, game,
        )
        if kind == "db":
            _page_help(
                character, _format_help_db_entry(character, payload),
            )
            from engine import hooks
            hooks.after_help_topic(character, verb, game)
            return
        if kind == "static":
            resolved_verb, topic = payload
            _send_static_help_page(
                character, resolved_verb, topic, help_w=help_w, sr=sr,
            )
            from engine import hooks
            hooks.after_help_topic(character, resolved_verb, game)
            return
        # Spaced names that are real topics (help archivist lore) must
        # not fall through to "filter help archivist for lore".
        if " " in verb:
            for alt in (verb.replace(" ", "-"), verb.replace(" ", "_")):
                if alt == verb:
                    continue
                alt_kind, alt_payload = _resolve_help_topic(
                    alt, topics, db, is_gm_viewer, game,
                )
                if alt_kind == "db":
                    _page_help(
                        character, _format_help_db_entry(character, alt_payload),
                    )
                    from engine import hooks
                    hooks.after_help_topic(character, alt, game)
                    return
                if alt_kind == "static":
                    resolved_verb, topic = alt_payload
                    _send_static_help_page(
                        character, resolved_verb, topic, help_w=help_w, sr=sr,
                    )
                    from engine import hooks
                    hooks.after_help_topic(character, resolved_verb, game)
                    return
        # Prefix / partial: list matches instead of picking one page
        # (suggestion report 325).
        prefix_hits = _help_prefix_matches(verb, topics, is_gm_viewer)
        if len(prefix_hits) > 1:
            _send_help_disambiguation(character, verb, prefix_hits, help_w, sr)
            return
        # help <topic> <query> -- filter a long page (help gm criminal)
        # instead of looking up a topic named "gm criminal".
        if " " in verb:
            head, query = verb.split(None, 1)
            # Overflow names already resolved above. Do not treat
            # ``help cases more`` as a filter for the word "more".
            if query.strip().lower() in _HELP_CONTINUATION_SUFFIXES:
                page = None
            else:
                page = topics.get(head)
            if page is not None:
                from engine import hooks as hooks_mod
                if hooks_mod.is_gm_only_help(head):
                    page = None
            if page is not None:
                from engine.help_filter import filter_topic_lines
                filtered = filter_topic_lines(page, query)
                if filtered:
                    title = f"{head} -- matches for {query}"
                    note = [
                        f"Showing matches for '{query}' on help {head}.",
                        f"Full page: help {head}.",
                    ]
                    # Staff catalog filter lives on gmhelp gm <word>.
                    note.append("")
                    framed = style.format_tome(
                        title, note + filtered, related=f"help {head}",
                        width=help_w,
                        screenreader=sr,
                    )
                    _page_help(character, framed)
                    from engine import hooks
                    hooks.after_help_topic(character, head, game)
                    return
                miss = (
                    f"No lines on help {head} match '{query}'. "
                    f"Type help {head} for the full page."
                )
                character.session.send(miss)
                return
        # Real dispatch verbs win over FTS body hits (e.g. paths catalog
        # mentioning "scare" must not steal ``help scare``).
        from engine.player_input import fold_catalog_id

        entry = COMMANDS.get(verb)
        if entry is None:
            folded_verb = fold_catalog_id(verb)
            if folded_verb and folded_verb != verb:
                entry = COMMANDS.get(folded_verb)
        if entry:
            _, help_text = entry
            if (
                not is_gm_viewer
                and (
                    help_text.startswith("GM:")
                    or help_text.startswith("head GM:")
                )
            ):
                entry = None
        if entry:
            _, help_text = entry
            framed = style.format_tome(
                verb, [help_text], related="commands",
                width=help_w,
                screenreader=bool(getattr(character, "screenreader", False)),
            )
            _page_help(character, framed)
            return
        # Staff pages never stay on help -- ranked GMs jump to gmhelp
        # before FTS can steal a player-body hit.
        if is_gm_rank and _should_redirect_help_to_gmhelp(
            verb, topics, db, COMMANDS,
        ):
            cmd_gmhelp(character, args, game, from_help=True)
            return
        # DB full-text search -- only when the word is not a live command.
        # Onboarding hubs skip FTS: paths catalog bodies mention "help newbie"
        # and would steal ``help newbie`` when static/DB exact pages are missing.
        if db is not None:
            from engine import help_db
            from engine import hooks as hooks_mod
            fts_entry = None
            if verb not in hooks_mod.get_help_fts_blocklist():
                fts_hits = help_db.search_fts_many(
                    db, verb, is_gm=is_gm_viewer, limit=8,
                )
                keys = [
                    str(hit.get("primary_keyword") or "").strip().lower()
                    for hit in fts_hits
                    if str(hit.get("primary_keyword") or "").strip()
                ]
                needle = verb.replace(" ", "-")
                close = [
                    key for key in keys
                    if needle in key or verb in key.replace("-", " ")
                ]
                if len(close) > 1:
                    _send_help_disambiguation(
                        character, verb, close, help_w, sr,
                    )
                    return
                fts_entry = fts_hits[0] if fts_hits else None
            if fts_entry:
                _page_help(
                    character, _format_help_db_entry(character, fts_entry),
                )
                from engine import hooks
                hooks.after_help_topic(
                    character, fts_entry["primary_keyword"], game
                )
                return
        # Last resort before logging a miss: a lot of player verbs are a
        # single mashed-together word (phantomstep, bloodsense, bloodmark,
        # ...) and players naturally type them with a space
        # ("phantom step"). If the exact phrase resolved nowhere above but
        # squashing the spaces out hits a real topic or command, redirect
        # there instead of a dead "no such topic" (help_misses.log had
        # repeat hits for exactly this shape -- see help-audit backlog).
        if " " in verb:
            from engine.player_input import fold_catalog_id

            folded = fold_catalog_id(verb)
            if (
                folded
                and folded != verb
                and (folded in topics or folded in COMMANDS)
            ):
                cmd_help(character, folded, game)
                return
            collapsed = verb.replace(" ", "")
            if collapsed in topics or collapsed in COMMANDS:
                cmd_help(character, collapsed, game)
                return
        # Log the miss so we can later spot missing topics vs typos
        # (engine/help_misses.py → help_misses.log beside the DB).
        try:
            from engine import help_misses
            help_misses.record(
                query=verb,
                reporter=getattr(character, "key", "?"),
                directory=getattr(game, "report_dir", "."),
            )
        except OSError:
            # Disk full / read-only volume -- still answer the player.
            pass
        suggestion = None
        if db is not None:
            from engine import help_db
            from engine import hooks as hooks_mod
            topic_keys = set(topics)
            if not is_gm_viewer:
                topic_keys = {
                    key for key in topic_keys
                    if not hooks_mod.is_gm_only_help(key)
                }
            suggestion = help_db.fuzzy_suggest(
                db, verb, is_gm=is_gm_viewer,
                extra_candidates=topic_keys | set(COMMANDS),
            )
        message = f"No such command or topic: '{verb}'. "
        if suggestion:
            message += f"Did you mean '{suggestion}'? "
        message += "Try 'help' for topics, or 'commands' for the verb list."
        character.session.send(message)
        return

    # Bare help: screenreader users get a short Start Here trail (TTS cannot
    # skim 500+ index blurbs). Sighted players keep the full categorized
    # grimoire. Full catalog for everyone: help topics / help index.
    sr = bool(getattr(character, "screenreader", False))
    lines = [""]
    if sr:
        lines.extend(style.format_help_start_here(screenreader=True))
    else:
        lines.extend(style.format_help_index(
            visible_categories,
            width=help_w,
            screenreader=False,
        ))
    _page_help(character, "\r\n".join(lines).rstrip("\n"))


_COMMAND_DOMAIN_BUCKETS = {
    "travel": frozenset({
        "walk", "drive", "taxi", "travel", "fly", "ticket", "depart",
        "hitch", "atlas", "map", "seek", "enter", "leave", "follow",
        "charter", "flights", "overland", "boardflight", "takeoff",
        "exit", "exits",
    }),
    "combat": frozenset({
        "attack", "kill", "shoot", "aim", "fire", "flee", "rest",
        "guard", "unleash", "rescue", "protect", "wield", "wear",
        "draw", "sheathe", "reload", "load",
    }),
    "work": frozenset({
        "job", "shop", "missions", "cases", "casework", "contracts", "dungeons",
        "quests", "homestead", "bounty", "chaseboard", "storyboard",
        "fireboard", "funeralboard", "rideboard", "work", "commute",
        "dream", "court", "dominion", "pit", "marches",
    }),
    "social": frozenset({
        "say", "tell", "ooc", "replay", "emote", "talk", "whisper", "introduce",
        "relate", "favorite", "who", "look", "consider", "kiss", "hug",
        "radio", "phone",
    }),
}


def _commands_label_in_bucket(label, bucket):
    """True when any slash-grouped verb's first word is in ``bucket``."""
    for part in str(label or "").split("/"):
        token = part.strip().split()[0].lower() if part.strip() else ""
        if token in bucket:
            return True
    return False


def cmd_commands(character, args, game):
    """List commands: compact index, per-verb tip, or full detail dump.

    bare ``commands``              compact verb names (aliases grouped;
                                   glued ``alias:`` flats hidden -- Wave 2d)
    ``commands detail`` / ``all``  full one-line tips including alias rows
    ``commands travel|combat|work|social``  Wave 1.5 domain buckets
    ``commands here``              hubs/verbs for the current room
    ``commands <verb>``            one handler group's tip (alias verbs ok)

    GM commands stay in a separate block (GMs only). Magic/Occultist/Mount
    aliases stay off the index (use cast / ritual / help magic; help horse).
    """
    from engine import display_prefs, style

    normal_triples, gm_triples = _commands_grouped_triples(character)
    width = display_prefs.sheet_width(character)
    sr = bool(getattr(character, "screenreader", False))
    mode = (args or "").strip().lower()

    if mode in ("detail", "all"):
        normal_pairs = [(label, ht) for _k, label, ht in normal_triples]
        gm_pairs = [(label, ht) for _k, label, ht in gm_triples]
        framed = style.format_commands_list(
            normal_pairs,
            gm_entries=gm_pairs if _is_gm(character) and gm_pairs else None,
            width=width,
            screenreader=sr,
        )
        character.session.send("\r\n".join(framed) + style.RESET)
        return

    if mode in _COMMAND_DOMAIN_BUCKETS:
        bucket = _COMMAND_DOMAIN_BUCKETS[mode]
        compact_normal, compact_gm = _commands_compact_triples(
            normal_triples, gm_triples,
        )
        compact_normal = [
            t for t in compact_normal if _commands_label_in_bucket(t[1], bucket)
        ]
        compact_gm = [
            t for t in compact_gm if _commands_label_in_bucket(t[1], bucket)
        ]
        if not compact_normal and not compact_gm:
            character.session.send(
                f"No {mode} commands in the compact index. "
                "Try commands or commands detail."
            )
            return
        normal_labels = [label for _k, label, _ht in compact_normal]
        gm_labels = [label for _k, label, _ht in compact_gm]
        framed = style.format_commands_compact(
            normal_labels,
            gm_labels=gm_labels if _is_gm(character) and gm_labels else None,
            width=width,
            screenreader=sr,
        )
        character.session.send("\r\n".join(framed) + style.RESET)
        return

    if mode == "here":
        from engine import hooks

        hints = list(hooks.room_command_hints(character, game) or [])
        if not hints:
            hints = ["Nothing board-specific here."]
        hints.append(
            "Type commands for the compact index, or commands travel, "
            "combat, work, or social."
        )
        character.session.send("\r\n".join(hints))
        return

    if mode:
        hit = _commands_lookup_triple(mode, normal_triples, gm_triples)
        if hit is None and _is_gm(character):
            hit = _commands_lookup_triple(mode, gm_triples, [])
        if hit is None:
            character.session.send(
                f"No command matching '{args.strip()}'. "
                "Try `commands` or `commands detail`."
            )
            return
        _sort_key, label, help_text = hit
        framed = style.format_commands_list(
            [(label, help_text)],
            width=width,
            screenreader=sr,
        )
        character.session.send("\r\n".join(framed) + style.RESET)
        return

    # Wave 2d: compact index hides glued alias groups (takechase, …).
    # Detail + per-verb lookup still use the full grouped triples above.
    compact_normal, compact_gm = _commands_compact_triples(
        normal_triples, gm_triples,
    )
    normal_labels = [label for _k, label, _ht in compact_normal]
    gm_labels = [label for _k, label, _ht in compact_gm]
    framed = style.format_commands_compact(
        normal_labels,
        gm_labels=gm_labels if _is_gm(character) and gm_labels else None,
        width=width,
        screenreader=sr,
    )
    character.session.send("\r\n".join(framed) + style.RESET)


def _commands_grouped_triples(character):
    """Build sorted (sort_key, label, help_text) triples for ``commands``."""
    from commands import COMMANDS

    grouped = {}
    hidden_handlers = set()
    for _verb, (handler, help_text) in COMMANDS.items():
        # Magic / Hellcraft / Mount kit aliases stay dispatchable; advertise
        # via cast / ritual / help magic / help horse (not bare commands).
        if (
            help_text.startswith("Magic:")
            or help_text.startswith("Occultist:")
            or help_text.startswith("Mount:")
        ):
            hidden_handlers.add(handler)
    for cmd_verb, (handler, _help_text) in COMMANDS.items():
        if handler in hidden_handlers:
            continue
        grouped.setdefault(handler, []).append(cmd_verb)

    normal_triples = []
    gm_triples = []
    for handler, verbs in grouped.items():
        verbs = sorted(verbs)
        help_text = COMMANDS[verbs[0]][1]
        triple = (verbs[0], "/".join(verbs), help_text)
        if help_text.startswith("GM:") or help_text.startswith("head GM:"):
            gm_triples.append(triple)
        else:
            normal_triples.append(triple)

    normal_triples.append(
        (
            "n",
            "n/s/e/w/ne/nw/se/sw/u/d",
            "walk that way if an exit exists (north, south, east, west, "
            "northeast, northwest, southeast, southwest, up, down)",
        )
    )
    normal_triples.sort(key=lambda triple: triple[0])
    gm_triples.sort(key=lambda triple: triple[0])
    return normal_triples, gm_triples


def _commands_listing_alias_text(help_text):
    """Lowercased tip with optional Origin / GM tag stripped for alias checks."""
    text = (help_text or "").strip().lower()
    for prefix in (
        "gm:",
        "head gm:",
        "magic:",
        "occultist:",
        "mount:",
        "angel/demon:",
        "angel:",
        "demon:",
        "human:",
    ):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip()
            break
    return text


def _is_commands_listing_alias(help_text):
    """True when this COMMANDS one-liner is a compact-hidden alias.

    Wave 2d: bare ``commands`` lists hub labels (chaseboard, missions),
    not glued flats (takechase, abandonhunt). Flat names stay in
    ``COMMANDS`` so Cadence / ``npc_do`` still dispatch them, and
    ``commands detail`` / ``commands takechase`` still show the tip.

    Recognized markers: ``alias:``, ``alias for``, ``alias of`` (after
    optional Origin / GM tag strip), plus embedded ``alias of`` on
    separate-handler tips (``build``, ``zone``, …).
    """
    text = _commands_listing_alias_text(help_text)
    if (
        text.startswith("alias:")
        or text.startswith("alias for ")
        or text.startswith("alias of ")
    ):
        return True
    # Separate-handler rows that document the hub in the same tip.
    return (
        "; alias of " in text
        or " … alias of " in text
        or "(alias of " in text
    )


def _commands_compact_triples(normal_triples, gm_triples):
    """Drop listing-alias verbs from compact labels; omit empty groups.

    Same-handler groups like ``smite/fry`` keep the canonical verb when
    ``fry`` is marked ``alias:``. Separate-handler glued flats (takechase
    vs chaseboard) disappear from compact entirely.
    """
    from commands import COMMANDS

    def _filter(triples):
        out = []
        for sort_key, label, help_text in triples:
            # Movement is injected, not a COMMANDS row -- keep n/s/e as-is.
            if sort_key == "n" and label.startswith("n/s/e"):
                out.append((sort_key, label, help_text))
                continue
            verbs = [verb for verb in label.split("/") if verb]
            visible = []
            for verb in verbs:
                row = COMMANDS.get(verb)
                tip = row[1] if row else help_text
                if _is_commands_listing_alias(tip):
                    continue
                visible.append(verb)
            if not visible:
                continue
            new_help = COMMANDS[visible[0]][1]
            out.append((visible[0], "/".join(visible), new_help))
        return out

    return _filter(normal_triples), _filter(gm_triples)


def _commands_lookup_triple(query, normal_triples, gm_triples):
    """Resolve ``commands <verb>`` against alias groups."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for triple in normal_triples + gm_triples:
        sort_key, label, help_text = triple
        aliases = [a.lower() for a in label.split("/")]
        if q == sort_key.lower() or q in aliases:
            return triple
    return None


def cmd_get(character, args, game):
    # Imported here (inside the function) rather than at the top of the file so
    # world.py and commands.py don't have to import each other in a loop.
    from world import Item
    if not args:
        character.session.send("Get what?")
        return

    room = character.location
    if room is None:
        character.session.send("You're nowhere.")
        return
    lower = args.lower()
    stripped = args.strip().lower()
    from engine import hooks

    # `get all backpack` / `get all from backpack` -- pull entire loot bag.
    _BULK_TOKENS = ("all", "*", "everything")
    for bulk in _BULK_TOKENS:
        if stripped in (
            f"{bulk} backpack", f"{bulk} from backpack",
            f"{bulk} weave", f"{bulk} from weave",
            f"{bulk} from pocket weave",
        ):
            if "weave" in stripped:
                bag = hooks.containers_resolve_loot_bag(character, "weave")
                if bag is None:
                    character.session.send(
                        "You have no pocket weave open (help pocketweave)."
                    )
                    return
                ok, msg = hooks.containers_unstow_all_from_loot_bag(
                    character, loot_bag=bag,
                )
                character.session.send(msg)
                return
            ok, msg = hooks.containers_unstow_all_from_loot_bag(character)
            character.session.send(msg)
            return
    if " from " in stripped:
        _left, _, _right = stripped.partition(" from ")
        bag = hooks.containers_resolve_loot_bag(character, _right.strip())
        if bag is not None:
            bulk_tok, frag = parse_bulk_item_query(_left.strip())
            if bulk_tok:
                if not frag:
                    ok, msg = hooks.containers_unstow_all_from_loot_bag(
                        character, loot_bag=bag,
                    )
                    character.session.send(msg)
                    return
                names = []
                while len(names) < 200:
                    taken = hooks.containers_find_in_loot_bag(
                        character, frag, loot_bag=bag,
                    )
                    if taken is None:
                        break
                    ok, msg = hooks.containers_unstow_from_loot_bag(
                        character, taken, loot_bag=bag,
                    )
                    if not ok:
                        if not names:
                            character.session.send(msg)
                            return
                        break
                    names.append(taken.key)
                if not names:
                    character.session.send(
                        f"You don't find that in {bag.key}."
                    )
                    return
                character.session.send(
                    f"You pull from {bag.key}: {format_item_tally(names)}."
                )
                return
            qty, frag = parse_qty_item_query(_left.strip())
            if qty:
                from engine.systems import containers as containers_mod

                _ok, msg = containers_mod.unstow_count_from_loot_bag(
                    character, qty, loot_bag=bag, needle=frag,
                )
                character.session.send(msg)
                return

    # `get <item> from <backpack>` -- before body nested loot (#49).
    if " from " in lower:
        left, _, right = args.partition(" from ")
        bag = hooks.containers_resolve_loot_bag(character, right.strip())
        if bag is not None:
            taken = hooks.containers_find_in_loot_bag(
                character, left.strip(), loot_bag=bag,
            )
            if taken is None:
                character.session.send(
                    f"You don't find that in {bag.key}."
                )
                return
            ok, msg = hooks.containers_unstow_from_loot_bag(
                character, taken, loot_bag=bag,
            )
            # HB-10: bulk path already checks ok; single-item used to
            # send even when unstow failed (and crash on a None msg).
            if msg:
                character.session.send(msg)
            return

    # `get <item> from <jeans>` -- clothing pockets (suggestion reports 312 / 327).
    if " from " in lower:
        left, _, right = args.partition(" from ")
        from engine.systems import clothing_pockets as pocket_mod

        garment = pocket_mod.find_pocket_garment(
            character, right.strip(), include_floor=True,
        )
        if garment is not None:
            taken = pocket_mod.find_in_pocket(garment, left.strip())
            if taken is None:
                character.session.send(
                    f"You don't find that in {garment.key}."
                )
                return
            _ok, msg = pocket_mod.unstow_from_pocket(
                character, taken, garment,
            )
            if msg:
                character.session.send(msg)
            return

    # `get <item> from <body>` -- loot nested belongings (#49).
    if " from " in lower:
        left, _, right = args.partition(" from ")
        items_here = visible_floor_items(
            character,
            [o for o in room.contents if isinstance(o, Item)],
        )
        container = _find_item(right.strip(), items_here)
        if container is None:
            character.session.send("You don't see that here.")
            return
        if not getattr(container, "is_body", False):
            character.session.send(
                f"You can't get things out of {container.key} that way."
            )
            return
        body = container
        loot_refusal = hooks.body_loot_refusal(character, body, game)
        if loot_refusal:
            character.session.send(loot_refusal)
            return
        loot = [
            o for o in hooks.iter_body_loot_items(body)
            if hooks.item_visible_to(character, o)
        ]
        taken = _find_item(left.strip(), loot)
        if taken is None:
            character.session.send(f"You don't find that in {body.key}.")
            return
        if taken in (getattr(body, "body_equipment", None) or {}).values():
            for slot, piece in list(body.body_equipment.items()):
                if piece is taken:
                    body.body_equipment.pop(slot, None)
                    break
        for slot, stack in list((getattr(body, "body_clothing", None) or {}).items()):
            if isinstance(stack, list) and taken in stack:
                stack.remove(taken)
                if not stack:
                    body.body_clothing.pop(slot, None)
        if taken in (getattr(body, "loot", None) or []):
            body.loot.remove(taken)
        from engine import hooks
        refusal = hooks.before_acquire_item(character, taken)
        if refusal:
            # Put the item back on the body. loot.append on the *local*
            # visible list used to orphan it (P16-10).
            stash = getattr(body, "loot", None)
            if not isinstance(stash, list):
                body.loot = []
                stash = body.loot
            if taken not in stash:
                stash.append(taken)
            character.session.send(refusal)
            return
        # hook -- route into gear bag / hands / loot backpack (no pre-append).
        stow_msg = hooks.after_acquire_item(character, taken)
        character.session.send(f"You take {taken.key} from {body.key}.")
        if stow_msg:
            character.session.send(stow_msg)
        # hook -- generic "<actor> takes <item> from <body>" fallback
        # wording without a game installed; Phase 2 purity.
        room.broadcast(
            hooks.loot_room_line(character.key, body.key, taken),
            exclude=character,
        )
        hooks.after_body_loot(character, body, taken, game)
        return

    # Only consider Items in the room (skip other characters). Veil
    # ghost gear is omitted unless the actor pierces the Veil.
    items_here = visible_floor_items(
        character,
        [o for o in room.contents if isinstance(o, Item)],
    )

    # `get all.sword backpack` / `get <item> backpack` shorthand -- last word
    # names a loot bag. Must run before floor ``all.thing`` so the bag is
    # not treated as part of the name fragment.
    parts = args.rsplit(None, 1)
    if len(parts) == 2:
        item_query, container_query = parts[0].strip(), parts[1].strip()
        bag = hooks.containers_resolve_loot_bag(character, container_query)
        if bag is not None and item_query:
            bulk_tok, frag = parse_bulk_item_query(item_query)
            if bulk_tok:
                if not frag:
                    ok, msg = hooks.containers_unstow_all_from_loot_bag(
                        character, loot_bag=bag,
                    )
                    character.session.send(msg)
                    return
                names = []
                while len(names) < 200:
                    taken = hooks.containers_find_in_loot_bag(
                        character, frag, loot_bag=bag,
                    )
                    if taken is None:
                        break
                    ok, msg = hooks.containers_unstow_from_loot_bag(
                        character, taken, loot_bag=bag,
                    )
                    if not ok:
                        if not names:
                            character.session.send(msg)
                            return
                        break
                    names.append(taken.key)
                if not names:
                    character.session.send(
                        f"You don't find that in {bag.key}."
                    )
                    return
                character.session.send(
                    f"You pull from {bag.key}: {format_item_tally(names)}."
                )
                return
            qty, frag = parse_qty_item_query(item_query)
            if qty:
                from engine.systems import containers as containers_mod

                _ok, msg = containers_mod.unstow_count_from_loot_bag(
                    character, qty, loot_bag=bag, needle=frag,
                )
                character.session.send(msg)
                return
            taken = hooks.containers_find_in_loot_bag(
                character, item_query, loot_bag=bag,
            )
            if taken is not None:
                ok, msg = hooks.containers_unstow_from_loot_bag(
                    character, taken, loot_bag=bag,
                )
                character.session.send(msg)
                return
            character.session.send(
                f"You don't find that in {bag.key}."
            )
            return

        from engine.systems import clothing_pockets as pocket_mod

        garment = pocket_mod.find_pocket_garment(
            character, container_query, include_floor=True,
        )
        if garment is not None and item_query:
            taken = pocket_mod.find_in_pocket(garment, item_query)
            if taken is None:
                character.session.send(
                    f"You don't find that in {garment.key}."
                )
                return
            _ok, msg = pocket_mod.unstow_from_pocket(
                character, taken, garment,
            )
            if msg:
                character.session.send(msg)
            return

    # `get all` / `get all.sword` / `get *` -- scoop matching pocketable items
    # on the floor. Bodies stay for `drag`; furniture stays put (beds, etc.).
    bulk_tok, frag = parse_bulk_item_query(args.strip())
    if bulk_tok:
        from engine import hooks
        takeable = [
            o for o in items_here
            if not getattr(o, "is_body", False)
            and not getattr(o, "furniture", False)
        ]
        if frag:
            takeable = _collect_item_matches(frag, takeable)
        if not takeable:
            if frag:
                character.session.send("You don't see that here.")
            else:
                character.session.send("There's nothing here you can pick up.")
            return
        names = []
        stow_msgs = []
        for item in list(takeable):
            refusal = hooks.before_acquire_item(character, item)
            if refusal:
                character.session.send(f"You can't take {item.key}: {refusal}")
                continue
            room.remove(item)
            stow_msg = hooks.after_acquire_item(character, item)
            names.append(item.key)
            if stow_msg:
                stow_msgs.append(stow_msg)
        if not names:
            return
        character.session.send("You pick up: " + format_item_tally(names) + ".")
        for msg in stow_msgs:
            character.session.send(msg)
        if frag:
            room.broadcast(
                f"{_presence_face(character)} scoops up matching items.",
                exclude=character,
            )
        else:
            room.broadcast(
                f"{_presence_face(character)} scoops up everything on the ground.",
                exclude=character,
            )
        return

    qty, frag = parse_qty_item_query(args.strip())
    if qty:
        takeable = [
            o for o in items_here
            if not getattr(o, "is_body", False)
            and not getattr(o, "furniture", False)
        ]
        if frag:
            takeable = _collect_item_matches(frag, takeable)
        if not takeable:
            character.session.send("You don't see that here.")
            return
        names = []
        stow_msgs = []
        from engine import hooks
        for item in list(takeable)[:qty]:
            refusal = hooks.before_acquire_item(character, item)
            if refusal:
                character.session.send(f"You can't take {item.key}: {refusal}")
                continue
            room.remove(item)
            stow_msg = hooks.after_acquire_item(character, item)
            names.append(item.key)
            if stow_msg:
                stow_msgs.append(stow_msg)
        if not names:
            return
        character.session.send("You pick up: " + format_item_tally(names) + ".")
        for msg in stow_msgs:
            character.session.send(msg)
        room.broadcast(
            f"{_presence_face(character)} scoops up a few things.",
            exclude=character,
        )
        return

    item = _find_item(args, items_here)
    if not item:
        from engine import hooks
        if hooks.try_get_unlisted(character, args, game):
            return
        character.session.send("You don't see that here.")
        return
    if item.is_body:
        # Bodies aren't pocketable -- use `drag` to move them (#49).
        character.session.send(
            f"{item.key} is too awkward to pocket -- try 'drag' instead."
        )
        return
    if getattr(item, "furniture", False):
        # Lodging beds and other fixed props stay in the room.
        character.session.send(
            f"{item.key} is furniture -- it stays here. "
            "Try 'sleep' to use a bed (see 'help lodging')."
        )
        return

    from engine import hooks
    hooks.enrich_loaded_item(item)
    refusal = hooks.before_acquire_item(character, item)
    if refusal:
        character.session.send(refusal)
        return

    # Move the item from the room's contents into carry (gear / hands / bag).
    room.remove(item)
    # hook -- route into gear bag / hands / loot backpack.
    from engine import hooks
    stow_msg = hooks.after_acquire_item(character, item)
    character.session.send(f"You pick up {item.key}.")
    if stow_msg:
        character.session.send(stow_msg)
    from engine.systems import phone as phone_mod

    nudge = phone_mod.pickup_threads_nudge(character, item)
    if nudge:
        character.session.send(nudge)
    room.broadcast(
        f"{_presence_face(character)} picks up {item.key}.",
        exclude=character,
    )


def cmd_drop(character, args, game):
    if not args:
        character.session.send("Drop what?")
        return
    if character.location is None:
        character.session.send("You're nowhere.")
        return

    # A body heaved onto your shoulder (cmd_heave) isn't in your inventory --
    # it rides in the room with you via _carrying_body -- so handle it first:
    # "dropping" it just means sliding it off your shoulder (stop carrying).
    carried = getattr(character, "_carrying_body", None)
    if carried is not None and _find_item(args, [carried]) is carried:
        character._carrying_body = None
        character.session.send(f"You slide {carried.key} off your shoulder.")
        broadcast_here(character, 
            f"{_presence_face(character)} slides {carried.key} off their shoulder.",
            exclude=character,
        )
        return

    # Bulk drop: ``all`` / ``*`` / ``everything``, plus Classic ``all.thing``
    # / ``all thing`` name matching (bug reports 469 and 491).
    bulk_tok, frag = parse_bulk_item_query(args.strip())
    if bulk_tok is not None:
        inv = list(getattr(character, "inventory", None) or [])
        if frag:
            targets = _collect_item_matches(frag, inv)
            if not targets:
                character.session.send("You aren't carrying that.")
                return
        else:
            if not inv:
                character.session.send("You aren't carrying anything.")
                return
            targets = inv
        dropped = []
        refused = []
        from engine.systems import containers as containers_mod
        from engine import hooks

        for item in list(targets):
            refuse = drop_item_refusal(character, item)
            if refuse:
                refused.append((item.key, refuse))
                continue
            # Peel one unit at a time so merged stack rows (stack_charges>1)
            # drop every piece -- same rule as single ``drop`` (bug report 329).
            while containers_mod._item_carried_by_character(character, item):
                unit = containers_mod.peel_one_carried_unit(character, item)
                if unit is None:
                    break
                character.location.add(unit)
                hooks.after_floor_drop(game, unit)
                dropped.append(unit.key)
        if dropped:
            character.session.send("You drop: " + format_item_tally(dropped) + ".")
            if frag:
                broadcast_here(character, 
                    f"{_presence_face(character)} drops several things.",
                    exclude=character,
                )
            else:
                broadcast_here(character, 
                    f"{_presence_face(character)} drops everything they can.",
                    exclude=character,
                )
        else:
            character.session.send("You can't drop anything you're carrying.")
        for name, reason in refused:
            # Keep the refusal message; prefix the item so bulk is readable.
            character.session.send(f"{name}: {reason}")
        return

    # This time we search YOUR inventory, not the room.
    item = _find_item(args, character.inventory, character=character)
    if not item:
        character.session.send("You aren't carrying that.")
        return

    # Case loaners, worn kit, equipped weapons, etc. (game hook + engine
    # worn-body belt -- see supers/bootstrap.py set_item_drop_refusal).
    refuse = drop_item_refusal(character, item)
    if refuse:
        character.session.send(refuse)
        return

    from engine.systems import containers as containers_mod

    unit = containers_mod.peel_one_carried_unit(character, item)
    if unit is None:
        character.session.send("You aren't carrying that.")
        return

    # The reverse of get: out of inventory, into the room.
    character.location.add(unit)
    from engine import hooks
    hooks.after_floor_drop(game, unit)
    character.session.send(f"You drop {unit.key}.")
    broadcast_here(character, 
        f"{_presence_face(character)} drops {unit.key}.", exclude=character
    )


# Loose cash aliases players type after a stack count (``give 5 dollars to Erin``).
_GIVE_CASH_ALIASES = frozenset(
    {"dollar", "dollars", "buck", "bucks", "cash", "money", "bill", "bills"},
)


def _parse_give_amount_query(item_query):
    """Split ``5 salt`` into (amount, name) for partial stack gives.

    Returns ``(amount, name, error)``. ``amount`` is a positive int when the
    leading token is digits; otherwise ``None`` (one peeled unit, like drop).
    A bare leading count (``give 1000 to Erin``) leaves ``name`` empty so
    ``cmd_give`` can treat it as pocket cash, not a missing item name.
    """
    text = (item_query or "").strip()
    if not text:
        return None, text, "Give what to whom? Try 'give <item> to <name>'."
    parts = text.split(None, 1)
    if not parts[0].isdigit():
        return None, text, None
    amount = int(parts[0])
    name = parts[1].strip() if len(parts) > 1 else ""
    if amount <= 0:
        return None, text, "Give at least one."
    return amount, name, None


def _resolve_give_cash_cents(item_query, amount):
    """Return pocket-cash cents when ``give`` names money, else 0."""
    from engine.systems import economy as economy_mod

    query = (item_query or "").strip()
    if query:
        cents = economy_mod.money_to_cents(query)
        if cents > 0:
            return cents
        if amount > 1 and query.lower() in _GIVE_CASH_ALIASES:
            return amount * 100
        if amount > 1:
            cents = economy_mod.money_to_cents(f"{amount} {query}")
            if cents > 0:
                return cents
    elif amount > 1:
        return amount * 100
    return 0


def cmd_give(character, args, game):
    """Hand a carried item to someone else in the room."""
    from world import Character, Item

    raw = (args or "").strip()
    if not raw:
        character.session.send(
            "Give what to whom? Try 'give <item> to <name>'."
        )
        return

    # ``give salt packet to erin``, ``give 5 salt to erin``, or
    # ``give erin salt packet`` / ``give erin 5 salt``.
    lower = raw.lower()
    item_query = None
    who_query = None
    if " to " in lower:
        split_at = lower.rfind(" to ")
        item_query = raw[:split_at].strip()
        who_query = raw[split_at + 4 :].strip()
    else:
        parts = raw.split(None, 1)
        if len(parts) < 2:
            character.session.send(
                "Give what to whom? Try 'give <item> to <name>'."
            )
            return
        who_query, item_query = parts[0].strip(), parts[1].strip()

    if not item_query or not who_query:
        character.session.send(
            "Give what to whom? Try 'give <item> to <name>'."
        )
        return

    amount, item_query, amt_err = _parse_give_amount_query(item_query)
    if amt_err:
        character.session.send(amt_err)
        return
    if amount is None:
        amount = 1

    room = character.location
    if room is None:
        character.session.send("You aren't anywhere.")
        return

    occupants = [
        o for o in room.contents
        if isinstance(o, Character) and o is not character
    ]
    target = _find_character(who_query, occupants, self_character=character)
    if target is None:
        character.session.send("You don't see them here.")
        return
    if target is character:
        character.session.send("You can't give something to yourself.")
        return

    cash_cents = _resolve_give_cash_cents(item_query, amount)
    if cash_cents > 0:
        from engine import hooks

        if hooks.try_give_cash(character, target, cash_cents, game):
            return
        character.session.send(
            "Pocket cash moves with pay here. Try: pay "
            f"{item_query or amount} {who_query}"
        )
        return

    if not (item_query or "").strip():
        character.session.send(
            "Give what to whom? Try 'give <item> to <name>' or "
            "'give $5 to <name>'."
        )
        return

    item = _find_item(item_query, character.inventory, character=character)
    if not item:
        from engine.systems import containers as containers_mod
        carried = list(containers_mod.iter_carried_items(character))
        item = _find_item(item_query, carried, character=character)
    if not item:
        character.session.send("You aren't carrying that.")
        return
    if not isinstance(item, Item):
        character.session.send("You can only give items.")
        return

    refuse = item_drop_refusal(character, item)
    if refuse:
        character.session.send(refuse)
        return
    refuse = item_give_refusal(character, item)
    if refuse:
        character.session.send(refuse)
        return

    from engine import hooks
    from engine.systems import containers as containers_mod

    # Game policy (Echo gift audience) -- engine stays generic.
    refuse = hooks.before_give(character, target, item)
    if refuse:
        character.session.send(refuse)
        return

    available = containers_mod._stack_unit_count(item)
    if amount > available:
        character.session.send(
            f"You only have {available} of those."
        )
        return

    target_is_npc = bool(getattr(target, "is_npc", False))
    given_units = []
    stow_msgs = []

    for _ in range(amount):
        hooks.enrich_loaded_item(item)
        if not target_is_npc:
            refusal = hooks.before_acquire_item(target, item)
            if refusal:
                if not given_units:
                    character.session.send(refusal)
                    return
                break

        unit = containers_mod.peel_one_carried_unit(character, item)
        if unit is None:
            if not given_units:
                character.session.send("You aren't carrying that.")
            break

        hooks.enrich_loaded_item(unit)
        if not target_is_npc:
            refusal = hooks.before_acquire_item(target, unit)
            if refusal:
                # Put the peeled unit back on the giver when transfer aborts.
                character.inventory.append(unit)
                if not given_units:
                    character.session.send(refusal)
                    return
                break
            stow_msg = hooks.after_acquire_item(target, unit)
            if stow_msg:
                stow_msgs.append(stow_msg)
        else:
            if target.inventory is None:
                target.inventory = []
            target.inventory.append(unit)

        given_units.append(unit)

    if not given_units:
        return

    item_label = given_units[0].key
    if len(given_units) > 1:
        item_label = f"{len(given_units)} {item_label}"

    # Viewer-relative faces (deep cover, introduce, guise) -- same policy
    # as look / list / whisper; never leak true names via _presence_face.
    giver_label_for = lambda viewer: _display_name(character, viewer=viewer)
    target_label_for = lambda viewer: _display_name(target, viewer=viewer)
    character.session.send(
        f"You give {item_label} to {target_label_for(character)}."
    )
    if getattr(target, "session", None):
        target.session.send(
            f"{giver_label_for(target)} gives you {item_label}."
        )

    def _give_bystander_line(watcher):
        giver = giver_label_for(watcher)
        recip = target_label_for(watcher)
        return f"{giver} gives {item_label} to {recip}."

    broadcast_here(
        character, _give_bystander_line, exclude=[character, target],
    )
    if stow_msgs and getattr(target, "session", None):
        for msg in stow_msgs:
            target.session.send(msg)

    from engine.systems.quests import notify as quest_notify

    last_unit = given_units[-1]
    catalog_id = getattr(last_unit, "catalog_id", None)
    quest_notify(
        character,
        "give",
        npc=getattr(target, "key", None),
        item=catalog_id or last_unit.key,
        catalog_id=catalog_id,
        game=game,
    )
    # Game policy (courtship gifts, etc.) -- engine stays generic.
    hooks.after_give(
        character, target, last_unit,
        amount=len(given_units), game=game,
    )


def cmd_inventory(character, args, game):
    if character.inventory:            # non-empty list is truthy
        # Painted names when the game registers item_display_key; else plain.
        from engine import hooks
        names = ", ".join(
            hooks.item_display_key(i, character) for i in character.inventory
        )
        character.session.send("You are carrying: " + names)
    else:
        character.session.send("You aren't carrying anything.")


def cmd_open(character, args, game):
    """Force open a locked container -- today that's only ever a dungeon
    strongbox (world.make_lockbox), but any future Item built with
    locked=True/loot=[...] works the same way for free.

    When args name an exit direction (``open north``), opens a structure
    door instead (see 'help lodging').

    Searches inventory first, then the room floor: a player might carry a
    box out of a dungeon before opening it, or just open it on the spot --
    either should work, same "check the obvious place first" order cmd_get
    uses for the room and cmd_drop uses for inventory.

    Opening CONSUMES the box (matches the "force it open" framing, and
    avoids leaving an inert "empty opened box" Item cluttering the world
    forever) and applies every loot entry: town dollars (`coins`), catalog
    items (via the same acquire routing as `get`), and Divine relics.
    Legacy ``growth`` loot rows (pre-cash Magi boxes) are ignored -- combat
    banks growth now, not lockboxes (see supers.lockbox_loot).
    """
    from engine import hooks
    if (args or "").strip() and hooks.try_directional_open(
        character, game, args, open_=True,
    ):
        return
    from world import Item
    if not args:
        character.session.send("Open what?")
        return

    # Carried first (open inv, loot backpack, gear bag) -- then room floor.
    # Prefer a picked lockbox over a still-sealed duplicate (bug report 1057).
    item, holder = _find_carried_item_for_open(character, args)
    if not item:
        room = require_here(character)
        if room is None:
            return
        item = _find_item_for_open(
            args,
            visible_floor_items(
                character,
                [
                    o for o in room.contents
                    if isinstance(o, Item)
                ],
            ),
            character=character,
        )
        holder = room
    if not item:
        character.session.send("You don't see that here.")
        return
    if item.is_body:
        # Section 6: "Bodies are warded by default; destroying or claiming
        # a warded body is a Reckoning-tier act" -- D7 (the Reckoning's
        # stakes) is still open, so the honest move is to refuse the
        # interaction outright rather than let 'open' quietly destroy
        # someone's revival point for free.
        character.session.send(f"{item.key} is warded shut -- you can't force it.")
        return
    # Pre-lockbox flavor strongboxes (and saves from before items.container)
    # load as unlocked with no loot -- promote them on the spot so `open`
    # works instead of dead-ending with "isn't locked" (bug_reports.log #21).
    # hook -- no-op without a game installed; Phase 2 purity (the reward
    # math is SUPERS content -- see supers/world_ext.py).
    upgrade_legacy_container(item)
    force_open = bool(getattr(item, "locked", False))
    if (
        not force_open
        and not getattr(item, "loot", None)
        and not getattr(item, "loot_payloads", None)
    ):
        character.session.send(f"{item.key} isn't locked.")
        return

    # Game hook: pit mimic strongboxes reveal and attack instead of paying
    # loot (supers/purgatory_dungeon/mimic.py when a game is installed).
    from engine import hooks
    if hooks.before_open_container(character, item, holder, game):
        return

    from engine.systems import containers as containers_mod
    from engine.systems.economy import can_receive_cash

    preview_rows = list(getattr(item, "loot", None) or [])
    preview_rows.extend(getattr(item, "loot_payloads", None) or [])
    if any(
        isinstance(row, dict) and economy_wallet.is_cash_loot_type(row.get("type"))
        for row in preview_rows
    ) and not can_receive_cash(character):
        character.session.send(
            "Your hands are too full to take the cash inside. "
            "Wear a pocket wallet or free a stack first."
        )
        return

    loot_rows, consumed = containers_mod.peel_open_reward_box(item)
    if consumed:
        # holder is either a list (character.inventory) or a Room -- both
        # support .remove(obj) with the same signature, so no branch is needed.
        holder.remove(item)

    gains = []
    from engine import hooks
    for reward in loot_rows:
        if reward.get("type") == "growth":
            # Retired payout -- combat banks growth. Skip so old saved
            # strongboxes cannot still dump growth on open.
            continue
        elif reward.get("type") == "relic":
            gain_line = hooks.grant_relic_loot(
                character,
                reward.get("id"),
                tier=reward.get("tier", 1),
            )
            if gain_line:
                gains.append(gain_line)
            else:
                gains.append("a cracked relic (useless)")
        elif economy_wallet.is_cash_loot_type(reward.get("type")):
            # Town dollars -- mission strongboxes and locked containers.
            # `gains` is a noun list for "Inside: $5, a bottle…", never full
            # sentences. Autosplit returns "You collect $N." lines for other
            # callers; stuffing those here produced "Inside: You collect $5., …".
            from engine.systems.economy import apply_cash_reward, format_money
            amount = reward.get("amount", 0)
            dkey = getattr(character, "_autosplit_defender_key", None)
            if game is not None and dkey:
                if hooks.autoloot_is_combat_zone(character.location, game):

                    class _Def:
                        key = dkey

                    # Credit (and notify other dealers) via autosplit; keep
                    # the box contents line as a clean money noun. Solo
                    # "You collect $N." is redundant with Inside — only
                    # send multi-party autosplit tells as separate lines.
                    for line in hooks.autosplit_wallet_cash(
                        game, character, _Def(), amount,
                    ):
                        if "(autosplit)" in line or " receives " in line:
                            character.session.send(line)
                    gains.append(format_money(amount))
                    continue
            if apply_cash_reward(character, amount):
                gains.append(format_money(amount))
            else:
                character.session.send(
                    "The cash inside will not fit — wear a pocket wallet "
                    "or free a stack first."
                )
        elif reward.get("type") == "item":
            # Catalog id via hooks.make_world_item (SUPERS items catalog
            # when a game is installed; None / no-op without one).
            made = hooks.make_world_item({"item": reward.get("id")})
            if made is not None:
                dkey = getattr(character, "_autosplit_defender_key", None)
                split_item = False
                if game is not None and dkey:
                    if (
                        hooks.autoloot_is_combat_zone(character.location, game)
                        and hooks.autosplit_is_splitable_item(made)
                    ):
                        class _Def:
                            key = dkey
                        # Same rule as cash: Inside lists the noun; any
                        # multi-party split tell is a separate send.
                        for line in hooks.autosplit_distribute_items(
                            game, character, _Def(), [made],
                        ):
                            # Solo "You collect X." is redundant with Inside.
                            if "(autosplit)" in line:
                                character.session.send(line)
                        split_item = True
                if not split_item:
                    # Route like cmd_get -- refuse before stow so a full
                    # inventory does not orphan the kit (P16-13).
                    refusal = hooks.before_acquire_item(character, made)
                    if refusal:
                        room = getattr(character, "location", None)
                        if room is not None and hasattr(room, "contents"):
                            room.contents.append(made)
                            made.location = room
                        character.session.send(refusal)
                        gains.append(f"{made.key} (left on the floor)")
                    else:
                        hooks.after_acquire_item(character, made)
                        gains.append(made.key)
                else:
                    gains.append(made.key)
            else:
                gains.append("a ruined kit scrap (useless)")

    if gains:
        # Noun phrases only — drop a single trailing period so the join
        # does not produce "$5., a bottle" when a helper returns a full
        # sentence. Use [:-1], not rstrip("."), so "$5.50" stays intact.
        nouns = []
        for g in gains:
            if not g:
                continue
            s = str(g).strip()
            if s.endswith("."):
                s = s[:-1].rstrip()
            if s:
                nouns.append(s)
        inside = ", ".join(nouns)
        if force_open:
            character.session.send(
                f"You force open {item.key}, breaking the seal. "
                f"Inside: {inside}."
            )
        else:
            character.session.send(
                f"You open {item.key}. Inside: {inside}."
            )
    else:
        if force_open:
            character.session.send(f"You force open {item.key}. It's empty.")
        else:
            character.session.send(f"You open {item.key}. It's empty.")
    if force_open:
        broadcast_here(
            character,
            f"{_presence_face(character)} forces open {item.key}.",
            exclude=character,
        )
    else:
        broadcast_here(
            character,
            f"{_presence_face(character)} opens {item.key}.",
            exclude=character,
        )
    # Game hook: mission hunts (and future systems) track container opens.
    hooks.after_open_container(character, item)


def cmd_afk(character, args, game):
    """Bare-engine stub: AFK pause is a SUPERS player convenience."""
    character.session.send(
        "AFK isn't available -- this engine has no game installed."
    )


def cmd_idlemode(character, args, game):
    """Bare-engine stub: idle mode needs Cadence lifestyle AI to actually
    drive the body, and Cadence is entirely SUPERS game content.

    This is the LEAN stub (two-repo purity Phase 2 -- see this module's
    docstring). The real implementation moved to
    `supers/verbs/engine_flavor.py`'s `cmd_idlemode`, which
    SUPERS_COMMANDS overrides this stub with whenever SUPERS is installed.
    """
    character.session.send(
        "Idle mode isn't available -- this engine has no game installed "
        "to drive an Echo's behavior."
    )


def cmd_save(character, args, game):
    """Flush this character's blob to SQLite now (add ``realm``/``all`` for built sites too).

    Bare ``save`` flushes only your body/inventory -- the common case, and
    the cheap one. ``save realm`` or ``save all`` (same thing, two names so
    either reads naturally) additionally upserts homesteads, demesnes,
    shops, personal realms, and townships you own, and archives a JSON
    recovery copy (suggestion report 513 -- players kept typing bare
    ``save`` expecting it to be the light option and getting the full
    site flush every time).

    Clears the autosave dirty queue for your body until you change again.
    Cooldown prevents save spam. Dropped floor loot still relies on the
    normal autosave pulse.
    """
    import time

    from engine.persistence import (
        PLAYER_SAVE_COOLDOWN_SEC,
        persist_save_character,
    )

    arg = (args or "").strip().lower()
    if arg in ("", "realm", "all"):
        player_checkpoint = arg in ("realm", "all")
    else:
        character.session.send("Usage: save (character only) | save realm | save all")
        return

    now = time.monotonic()
    last = float(getattr(character, "_last_player_save_monotonic", 0.0) or 0.0)
    elapsed = now - last
    if elapsed < PLAYER_SAVE_COOLDOWN_SEC:
        wait = int(PLAYER_SAVE_COOLDOWN_SEC - elapsed) + 1
        character.session.send(
            f"Save is cooling down — try again in ~{wait}s."
        )
        return

    conn = getattr(game, "db", None)
    if conn is None:
        character.session.send("Save is not available right now.")
        return

    ok, msg = persist_save_character(
        conn, game, character, player_checkpoint=player_checkpoint,
    )
    if ok:
        character._last_player_save_monotonic = now
    character.session.send(msg)


def cmd_setpass(character, args, game):
    """Set or change your character's password (see auth.py for the hashing).

    When a password already exists: ``setpass <current> <new>`` (everyone).
    When somehow blank: ``setpass <new>`` once. Mortals need min length only;
    GM / head_gm new passwords also need letter + digit + symbol.

    Linked accounts: the new hash is mirrored onto the account row so
    account-first login picks up the change (current password may be the
    body hash or the account hash).

    No "type it twice to confirm" step -- this telnet server doesn't mask
    input anyway (systems doc note: full telnet negotiation is out of scope
    for now), so a typo is just as visible to you as a confirmation would be.

    Strips client session tags (P1/Pn prefixes) the same way login does, so
    a mudlet/tintin paste cannot bake tags into the stored hash. Persists
    immediately when the Game exposes save().
    """
    from engine import accounts as accounts_mod
    from engine import auth
    from engine.connection import strip_client_session_tags
    from command_support import _is_gm

    raw = strip_client_session_tags(args or "").strip()
    for_gm = _is_gm(character)
    has_hash = bool(getattr(character, "password_hash", None))
    account = accounts_mod.account_for_character(game, character)
    if not has_hash and account is not None:
        has_hash = bool((account.password_hash or "").strip())

    if has_hash:
        # Split once: new password may contain spaces.
        parts = raw.split(None, 1)
        if len(parts) < 2:
            character.session.send(
                "Usage: setpass <current password> <new password>"
            )
            return
        current, new_password = parts
        if not accounts_mod.verify_character_or_account_password(
            game, character, current,
        ):
            character.session.send("Current password is incorrect.")
            return
    else:
        new_password = raw
        if not new_password:
            character.session.send(
                f"Usage: setpass <new password> "
                f"(at least {auth.MIN_PASSWORD_LEN} characters)"
            )
            return

    policy_err = auth.password_policy_error(new_password, for_gm=for_gm)
    if policy_err:
        character.session.send(policy_err)
        return

    new_hash = auth.hash_password(new_password)
    accounts_mod.apply_login_password_hash(game, character, new_hash)
    character.session.send("Password updated.")
    from engine import gm_notify
    gm_notify.ping_gms(
        game,
        f"{gm_notify.public_who(character, game)} changed their password{{from}}.",
        exclude=character,
        peer_session=character.session,
    )
    # Targeted account + body flush — not a full world snapshot (P11-14).
    if not accounts_mod.persist_login_password_change(
        game, character, reason="setpass",
    ):
        character.session.send(
            "Password updated in memory but did not write to disk. "
            "Try again or tell staff."
        )
        return


def cmd_quit(character, args, game):
    """Hard disconnect -- body stays as an Echo; TCP closes."""
    character.session.send("Goodbye.")
    character.session.close()   # flips the session's 'alive' flag; the input loop then ends


def cmd_logout(character, args, game):
    """Soft logout -- Echo the body, keep TCP, return to character select.

    Prefer the linked account's pick list on re-entry when the body (or
    staff Session) has an account. ``quit`` still closes the connection.
    """
    session = character.session
    if session is None:
        return
    # Remember account for the soft-relogin character menu.
    prefer = None
    try:
        from engine.accounts import account_for_character
        acct = account_for_character(game, character)
        if acct is not None:
            prefer = acct.name
    except Exception:
        prefer = None
    if not prefer:
        prefer = getattr(session, "staff_account", None) or None
    session._soft_logout_account = prefer
    session._soft_logout = True
    if prefer:
        session.send(
            "Returning to character select "
            f"(account {prefer}). Your body stays as an Echo."
        )
    else:
        session.send(
            "Returning to character select. "
            "Your body stays as an Echo -- log in by name or type 'account'."
        )
    # Exit play() without closing the writer (disconnect keep_connection).
    session.alive = False


def _report_history(character):
    """Build the history list for a bug/suggest report from the session ring
    buffer, EXCLUDING the current 'bug ...' / 'suggest ...' line itself (that
    line is already in Session.history by the time the handler runs, and
    including it would just clutter every report with its own command).

    Returns [] if this character has no real Session.history (e.g. the smoke
    test's FakeSession) -- reports still work, just without prior context.
    """
    history = getattr(character.session, "history", None)
    if not history:
        return []
    # history is a deque of [line, traceback_or_None]; drop the last entry
    # if it's the report command that triggered us.
    entries = list(history)
    if entries:
        last_line = entries[-1][0].strip().lower()
        if last_line.startswith("bug ") or last_line.startswith("suggest ") \
                or last_line.startswith("typo ") \
                or last_line in ("bug", "suggest", "typo"):
            entries = entries[:-1]
    # Defense in depth: redact any setpass lines that predate storage redaction.
    from engine.connection import history_line_for_storage
    cleaned = []
    for line, tb in entries:
        cleaned.append([history_line_for_storage(line), tb])
    return cleaned


def _file_or_capture_report(character, args, game, kind, noun):
    """Shared body for cmd_bug/cmd_suggest. `<cmd> <description>` on one
    line files immediately (unchanged quick-usage behavior). A bare
    `<cmd>` with no description used to just print a "Usage:" line and give
    up -- a live report caught the real cost of that: pasting a multi-line
    combat message into 'suggest' sent each line as its own separate
    command (a raw telnet paste is indistinguishable from several separate
    Enter presses once it's on the wire), so only the FIRST line became the
    report and the rest surfaced as "Unknown command" noise. Now a bare
    `<cmd>` instead starts multi-line paste capture
    (engine/connection.py's Session.report_capture) -- exactly the "literal
    paste document type form" the same report asked for.
    """
    from engine import bug_filing
    description = args.strip()
    if not description:
        character.session.report_capture = {"kind": kind, "lines": []}
        character.session.send(
            f"Paste your {noun} across as many lines as you like. Type a "
            "single '.' on its own line when done (or 'cancel' to back out)."
        )
        return
    bug_filing.record_and_confirm(
        character, kind, description, _report_history(character),
        game.report_dir, noun,
    )


def cmd_bug(character, args, game):
    """Log a bug report to bug_reports.log (beside the save file), including
    this session's recent command lines and any error tracebacks they
    raised. 'bug <description>' files immediately; bare 'bug' starts a
    multi-line paste capture instead -- see _file_or_capture_report.

    When the first word resolves to another character ('bug shent …'),
    the report is about that body and attaches *their* diagnostic context
    (bug report 203) -- free-text lines like 'bug the sword vanished' stay
    self-reports when the first token is not a character name.
    """
    from engine import bug_filing
    from engine import reports

    subject, description = bug_filing.parse_bug_subject(character, args, game)
    if not description:
        capture = {"kind": reports.BUG, "lines": []}
        if subject is not None:
            capture["subject_key"] = subject.key
        character.session.report_capture = capture
        if subject is not None:
            who = bug_filing._subject_display_name(subject)
            character.session.send(
                f"Paste your report about {who} across as many lines as you "
                "like. Type a single '.' on its own line when done (or "
                "'cancel' to back out)."
            )
        else:
            character.session.send(
                "Paste your report across as many lines as you like. Type a "
                "single '.' on its own line when done (or 'cancel' to back out)."
            )
        return
    bug_filing.record_and_confirm(
        character, reports.BUG, description, _report_history(character),
        game.report_dir, "report", subject_character=subject,
    )


def cmd_suggest(character, args, game):
    """Log a suggestion to suggestions.log -- same shape as cmd_bug, separate
    file so bug triage and feature ideas don't mix.
    """
    from engine import reports
    _file_or_capture_report(character, args, game, reports.SUGGEST, "suggestion")


def cmd_typo(character, args, game):
    """Log a copy typo to typos.log -- not a crash, not a feature idea.

    Same one-line / paste-capture UX as suggest. Staff prioritize these
    separately from bugs (idea 194).
    """
    from engine import reports
    _file_or_capture_report(character, args, game, reports.TYPO, "typo")


def cmd_helpsubmit(character, args, game):
    """Propose new help content for staff review (docs/plans/
    helpfile_editing_system.md). Usage: helpsubmit <keyword> [one-line
    body]. With no body, starts a multi-line paste capture -- same UX as
    bug/suggest: type a single '.' on its own line when done, or 'cancel'
    to back out. Logged to help_proposals.log and pings online GMs, same
    as a bug/suggestion; a GM reviews it with 'reports' and, if it should
    be added, writes the real page with 'hedit <keyword>' then closes the
    proposal with 'resolve help <id>'.
    """
    from engine import bug_filing, reports
    parts = args.strip().split(maxsplit=1)
    if not parts:
        character.session.send("Usage: helpsubmit <keyword> [one-line body]")
        return
    keyword = parts[0].strip().lower()
    body = parts[1].strip() if len(parts) > 1 else ""
    prefix = f"Proposed keyword: {keyword}"
    if not body:
        character.session.report_capture = {
            "kind": reports.HELP, "lines": [], "prefix": prefix,
        }
        character.session.send(
            f"Paste the proposed '{keyword}' help text across as many "
            "lines as you like. Type a single '.' on its own line when "
            "done (or 'cancel' to back out)."
        )
        return
    bug_filing.record_and_confirm(
        character, reports.HELP, f"{prefix}\n{body}",
        _report_history(character), game.report_dir, "help idea",
    )


def _static_help_category(keyword):
    """Which HELP_CATEGORIES section (if any) currently lists `keyword` --
    used to pre-fill 'category' when hedit seeds a new overlay draft from
    an existing static page (see cmd_hedit). Empty string when the keyword
    isn't in the categorized index (e.g. it only has a COMMANDS one-liner).
    """
    for category_name, entries in get_help_categories():
        for topic_keyword, _blurb in entries:
            if topic_keyword == keyword:
                return category_name
    return ""


def _hedit_blank_draft(session, keyword):
    """Start a fresh hedit session with an empty body buffer."""
    session.help_edit = {
        "keyword": keyword,
        "body": [],
        "syntax": [],
        "category": "",
        "aliases": [],
        "gm_only": False,
        "is_ic": False,
    }


def cmd_hedit(character, args, game):
    """GM: open the modal helpfile editor for <keyword> (docs/plans/
    helpfile_editing_system.md). Loads an existing DB-overlay page to
    revise when ``keyword`` is the page's primary key; otherwise seeds a
    new draft from the static help_topics.py page of that name if one
    exists (and is not alias-only), or starts blank. Alias-only names
    (DB or static redirects) drop the alias and open a blank draft so
    staff can claim the keyword as its own page. Overlay pages win over
    the static page at lookup time -- this is for hot-patching or drafting
    live, not for editing the git-tracked canon file itself.

    While editing, plain text appends a body line; '/list /i /d /clear /r
    /syntax /category /alias /gm /ic /preview /save /cancel' are the modal
    editor commands (see engine.connection.Session._handle_help_edit_line).
    """
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return
    keyword = args.strip().lower()
    if not keyword:
        character.session.send("Usage: hedit <keyword>")
        return
    session = character.session
    if session.help_edit is not None:
        session.send(
            f"Already editing '{session.help_edit['keyword']}' -- "
            "/save or /cancel that first."
        )
        return

    db = getattr(game, "db", None)
    from engine import help_db
    from engine import hooks as hooks_mod

    existing = (
        help_db.get_primary_entry(db, keyword) if db is not None else None
    )
    if existing:
        session.help_edit = {
            "keyword": keyword,
            "body": existing["body_text"].split("\n") if existing["body_text"] else [],
            "syntax": existing["syntax_block"].split("\n") if existing["syntax_block"] else [],
            "category": existing["category"],
            "aliases": help_db.list_aliases(db, keyword),
            "gm_only": bool(existing["gm_only"]),
            "is_ic": bool(existing["is_ic"]),
        }
        session.send(
            f"Editing existing overlay page '{keyword}' "
            f"({len(session.help_edit['body'])} body lines loaded). "
            "Type text to append, or /list /i /d /clear /r /syntax "
            "/category /alias /gm /ic /preview /save /cancel."
        )
        return

    dropped_hub = None
    if db is not None:
        dropped_hub = help_db.get_alias_target(db, keyword)
        if dropped_hub:
            help_db.remove_alias(db, keyword)
    if dropped_hub is None:
        dropped_hub = hooks_mod.get_help_alias_target(keyword)

    if dropped_hub:
        _hedit_blank_draft(session, keyword)
        session.send(
            f"'{keyword}' was only an alias for '{dropped_hub}' -- "
            f"alias dropped; drafting a new '{keyword}' page. "
            "Type text to append lines, then /save when ready (or /cancel). "
            "See /list /i /d /clear /r /syntax /category /alias /gm /ic /preview."
        )
        return

    static_topic = get_help_topics().get(keyword)
    if static_topic:
        session.help_edit = {
            "keyword": keyword,
            "body": static_topic.strip("\n").split("\n"),
            "syntax": [],
            "category": _static_help_category(keyword),
            "aliases": [],
            "gm_only": False,
            "is_ic": False,
        }
        session.send(
            f"New overlay draft for '{keyword}', pre-filled from the "
            f"static help_topics.py page ({len(session.help_edit['body'])} "
            "lines). The static file is untouched until you /save this "
            "here -- /save publishes a hot-patched override; /cancel "
            "discards the draft and leaves the static page as-is. "
            "/list /i /d /clear /r /syntax /category /alias /gm /ic /preview."
        )
        return

    _hedit_blank_draft(session, keyword)
    session.send(
        f"New overlay page '{keyword}'. Type text to append lines, "
        "then /save when ready (or /cancel to abort). "
        "See /list /i /d /clear /r /syntax /category /alias /gm /ic /preview."
    )


def cmd_hrefresh(character, args, game):
    """GM: rewrite a help_db overlay from the static HELP_TOPICS page.

    Use this when a saved ``hedit`` row is stale but you still want the
    overlay layer (not a delete-back-to-static workaround). ``hrefresh
    catalog`` rebuilds ``help paths`` from ``origins.json`` and pushes it
    into the ``paths`` / ``path list`` / ``pathlist`` overlay rows.
    """
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return
    keyword = args.strip().lower()
    if not keyword:
        character.session.send(
            "Usage: hrefresh <keyword|catalog>\r\n"
            "  hrefresh catalog   refresh help paths from origins.json\r\n"
            "  hrefresh <keyword> copy static help_topics.py into the overlay"
        )
        return
    author = getattr(character, "key", None) or "gm"
    from engine import hooks

    ok, message = hooks.refresh_help_overlay(game, keyword, author=author)
    if ok:
        character.session.send(f"Help overlay refreshed: {message}")
    else:
        character.session.send(f"Help overlay not refreshed: {message}")


def _parse_help_overlay_keyword_args(args):
    """Split GM help overlay verb args into ``(keyword, confirmed)``.

    A trailing ``confirm`` token commits the action; everything before it
    is the help keyword (may contain spaces, e.g. ``angel more``).
    """
    parts = (args or "").strip().split()
    if not parts:
        return "", False
    if parts[-1].lower() == "confirm":
        confirmed = True
        parts = parts[:-1]
    else:
        confirmed = False
    keyword = " ".join(parts).strip().lower()
    return keyword, confirmed


def cmd_hdelete(character, args, game):
    """GM: remove a hedit-created DB-overlay help page (suggestion 317).

    Only ever touches the SQLite `helpfiles` overlay layer -- a page
    authored purely in the git-tracked ``help_topics.py`` / ``help/topics/``
    source cannot be deleted at runtime (that needs a code PR, same as any
    other static content). This exists for the case ``hedit`` itself warns
    about but doesn't clean up: staff redoes a page and links a new "more"
    child in, and the old draft/overlay row is left behind with nothing
    pointing at it -- dead weight the help-audit's orphan detector will
    keep flagging until someone removes it.
    """
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return
    keyword, confirmed = _parse_help_overlay_keyword_args(args)
    if not keyword:
        character.session.send(
            "Usage: hdelete <keyword>          preview what would be removed\r\n"
            "       hdelete <keyword> confirm   actually remove it"
        )
        return

    db = getattr(game, "db", None)
    if db is None:
        character.session.send("No help database is open.")
        return

    from engine import help_db

    entry = help_db.get_primary_entry(db, keyword)
    if entry is None:
        alias_target = help_db.get_alias_target(db, keyword)
        if alias_target:
            character.session.send(
                f"'{keyword}' is only an alias for '{alias_target}' -- "
                f"hdelete {alias_target} removes the page itself, or "
                f"hedit {alias_target} then /alias to change its aliases."
            )
            return
        if keyword in get_help_topics():
            character.session.send(
                f"'{keyword}' is a static help_topics.py page, not a hedit "
                "overlay -- there is nothing live to delete. Removing it "
                "needs a code change (PR), same as any other canon page."
            )
            return
        character.session.send(f"No overlay or static help page named '{keyword}'.")
        return

    aliases = help_db.list_aliases(db, keyword)
    static_fallback = keyword in get_help_topics()
    if not confirmed:
        preview = (entry["body_text"] or "").strip().splitlines()
        first_line = preview[0] if preview else "(empty body)"
        lines = [
            f"Overlay page '{keyword}' -- {len(preview)} line(s), starts:",
            f"  {first_line[:100]}",
        ]
        if aliases:
            lines.append(f"Aliases that will also drop: {', '.join(aliases)}")
        lines.append(
            f"Falls back to the static page after delete: {'yes' if static_fallback else 'no -- becomes a miss'}"
        )
        lines.append(f"Type: hdelete {keyword} confirm")
        character.session.send("\r\n".join(lines))
        return

    help_db.delete_entry(db, keyword)
    if static_fallback:
        character.session.send(
            f"Overlay deleted -- '{keyword}' now falls back to the static "
            "help_topics.py page."
        )
    else:
        character.session.send(
            f"Overlay deleted -- '{keyword}' has no static page underneath, "
            "so it will be logged as a help miss until content exists again."
        )


def cmd_hunder(character, args, game):
    """GM: show the git-authored static page underneath a hedit overlay.

    When ``hedit`` has saved a row, ``help <keyword>`` serves the overlay.
    Matt and other staff use this to read what shipped in
    ``help_topics.py`` / ``help/topics/*.py`` without deleting or
    refreshing the live overlay first.
    """
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return
    keyword, _confirmed = _parse_help_overlay_keyword_args(args)
    if not keyword:
        character.session.send(
            "Usage: hunder <keyword>\r\n"
            "  Shows the git-authored help page behind a hedit overlay."
        )
        return

    from engine import display_prefs, help_db

    topics = get_help_topics()
    static_body = topics.get(keyword)

    db = getattr(game, "db", None)
    overlay = (
        help_db.get_primary_entry(db, keyword, is_gm=True)
        if db is not None
        else None
    )

    if static_body is None and overlay is None:
        character.session.send(
            f"No static or overlay help page named '{keyword}'."
        )
        return

    if static_body is None:
        author = overlay.get("author") or "?"
        o_lines = len((overlay.get("body_text") or "").splitlines())
        character.session.send(
            f"'{keyword}' has a hedit overlay only ({o_lines} line(s), "
            f"author {author}) -- no static help_topics.py page underneath.\r\n"
            f"help {keyword} shows the overlay. "
            f"hdelete {keyword} confirm removes it."
        )
        return

    preamble_lines: list[str] = []
    if overlay is not None:
        author = overlay.get("author") or "?"
        overlay_words = len((overlay.get("body_text") or "").split())
        static_words = len(static_body.split())
        stale = help_db.overlay_is_stale_stub(
            overlay.get("body_text"), static_body,
        )
        preamble_lines.append(
            "[Staff] Git-authored layer (hedit overlay is still active)."
        )
        preamble_lines.append(
            f"Players see the overlay ({overlay_words} words, author {author})."
            + (
                " Overlay is a stale shrink-stub -- help may already show git."
                if stale
                else ""
            )
        )
        preamble_lines.append(
            f"Git page below ({static_words} words). "
            f"hrefresh {keyword} copies git into the overlay; "
            f"hdelete {keyword} confirm drops the overlay."
        )
    else:
        preamble_lines.append(
            "[Staff] No hedit overlay -- players already see this git page."
        )

    help_w = display_prefs.sheet_width(character)
    sr = bool(getattr(character, "screenreader", False))
    _send_static_help_page(
        character,
        keyword,
        static_body,
        help_w=help_w,
        sr=sr,
        preamble="\n".join(preamble_lines),
    )


def cmd_hnews(character, args, game):
    """GM: list hedit overlays where shipped git canon has changed.

    Matt uses this after deploys to see which live hedit pages no longer
    match the handbook in code. Pair with ``hunder <keyword>`` to read
    git, then ``hrefresh`` or ``hedit`` to merge.
    """
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    db = getattr(game, "db", None)
    if db is None:
        character.session.send("No help database is open.")
        return

    from engine import display_prefs, style
    from engine.help_overlay_drift import collect_hedit_git_drift

    filter_key, _confirmed = _parse_help_overlay_keyword_args(args)
    topics = get_help_topics()
    drift = collect_hedit_git_drift(db, topics, is_gm=True)
    if filter_key:
        drift = [row for row in drift if row["keyword"] == filter_key]

    help_w = display_prefs.sheet_width(character)
    sr = bool(getattr(character, "screenreader", False))

    if not drift:
        if filter_key:
            character.session.send(
                f"No git drift for '{filter_key}' -- overlay matches shipped "
                "canon, or there is no overlay / static page."
            )
        else:
            character.session.send(
                "No hedit overlays differ from shipped git canon."
            )
        return

    body_lines: list[str] = []
    if filter_key:
        body_lines.append(
            f"Git drift for '{filter_key}' (shipped canon vs live hedit)."
        )
    else:
        body_lines.append(
            f"{len(drift)} hedit overlay(s) differ from shipped git canon."
        )
    body_lines.append(
        "Players read hedit unless helpmode git or the row is a stale stub."
    )
    body_lines.append("")

    for row in drift[:60]:
        tag = ""
        if row["stale_stub"]:
            tag = " stale stub -- git wins at help"
        elif row["word_delta"] > 0:
            tag = f" git +{row['word_delta']} words"
        elif row["word_delta"] < 0:
            tag = f" git {row['word_delta']} words"
        else:
            tag = " same length, text differs"
        body_lines.append(
            f"  {row['keyword']}: overlay {row['overlay_words']}w "
            f"({row['author']}) vs git {row['git_words']}w --{tag}"
        )
    if len(drift) > 60:
        body_lines.append(f"  ... and {len(drift) - 60} more.")

    body_lines.append("")
    body_lines.append(
        "hunder <keyword> -- read git | hrefresh <keyword> -- copy git "
        "| hedit <keyword> -- merge in your voice"
    )

    framed = style.format_tome(
        "hedit git drift",
        body_lines,
        related="gmhelp hnews",
        width=help_w,
        screenreader=sr,
    )
    _page_help(character, framed)


def _my_reports_lines(
    kind, entries, *, noun, file_verb, cmd_name, viewer_key=None,
):
    """Body lines for a player's own bug/suggestion listing."""
    from engine import reports

    label = reports.DISPLAY_LABELS.get(kind, (kind or "?").upper())
    if not entries:
        return [
            f"You have no open {noun}.",
            f"Use `{file_verb} <description>` to file one.",
            f"Read or add notes: {cmd_name} read <id> | {cmd_name} comment <id> <text>",
        ]
    lines = []
    for entry in entries:
        entry_id = entry.get("id", "?")
        status = entry.get("status", "open")
        time = entry.get("time", "?")
        description = (entry.get("description") or "").replace("\n", " ").strip()
        status_bit = f" ({status})" if status != "open" else ""
        n_comments = reports.comment_count(entry)
        comment_bit = f" [{n_comments} comments]" if n_comments else ""
        filed_bit = ""
        reporter = (entry.get("reporter") or "").strip()
        if viewer_key and reporter and reporter != viewer_key:
            filed_bit = f" [filed as {reporter}]"
        lines.append(
            f"  [{label} #{entry_id}]{status_bit} {time} — {description}"
            f"{filed_bit}{comment_bit}"
        )
    lines.append("")
    lines.append(
        f"{cmd_name} read <id> for the thread | "
        f"{cmd_name} comment <id> <text> to add a note"
    )
    return lines


def _parse_ticket_id(token):
    """Positive ticket id from a player/staff arg, or None."""
    raw = (token or "").strip()
    if raw.startswith("#"):
        raw = raw[1:].strip()
    try:
        entry_id = int(raw)
    except (TypeError, ValueError):
        return None
    if entry_id < 1:
        return None
    return entry_id


def _notify_ticket_comment(game, kind, entry, commenter, text, *, staff):
    """Ping the other side of a ticket comment (reporter or online staff)."""
    from engine import reports
    from engine import gm_notify
    from engine.char_identity import legal_public_name

    entry_id = entry.get("id", "?")
    label = reports.DISPLAY_LABELS.get(kind, kind).lower()
    preview = (text or "").replace("\n", " ").strip()
    if len(preview) > 80:
        preview = preview[:77] + "..."
    who = legal_public_name(commenter, force_surname=True)
    list_cmd = {
        reports.BUG: "bugs",
        reports.SUGGEST: "ideas",
        reports.TYPO: "typos",
    }.get(kind, "bugs")

    if staff:
        reporter_key = (entry.get("reporter") or "").strip()
        if not reporter_key:
            return
        finder = getattr(game, "find_character", None)
        reporter = finder(reporter_key) if finder else None
        session = getattr(reporter, "session", None) if reporter else None
        if session is not None and reporter is not commenter:
            session.send(
                f"Staff replied on your {label} ticket #{entry_id}. "
                f"Type {list_cmd} read {entry_id} to see it."
            )
        return

    if game is None:
        return
    gm_notify.ping_gms(
        game,
        f"{who} commented on {label} #{entry_id}: {preview}",
        exclude=commenter,
    )


def _cmd_report_thread_read(character, rest, game, kind, *, cmd_name):
    """Player: read one of their own tickets including comments."""
    from engine import reports
    from engine import style

    staff = _is_gm(character)
    entry_id = _parse_ticket_id((rest or "").split(maxsplit=1)[0] if rest else "")
    if entry_id is None:
        character.session.send(f"Usage: {cmd_name} read <id>")
        return
    entry = reports.get_by_id(kind, entry_id, directory=game.report_dir)
    if entry is None or not reports.can_ticket_read(
        entry, character.key, staff=staff, game=game, character=character,
    ):
        label = reports.DISPLAY_LABELS.get(kind, "ticket").lower()
        character.session.send(f"No {label} ticket #{entry_id} of yours.")
        return
    body = reports.format_player_thread_lines(kind, entry, game=game)
    label = reports.DISPLAY_LABELS.get(kind, "TICKET")
    lines = style.format_sheet(
        f"{label} #{entry_id}",
        body,
        width=52,
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    character.session.send("\r\n".join(lines))


def _cmd_report_thread_comment(character, rest, game, kind, *, cmd_name):
    """Player: comment on an open ticket they filed. Does not resolve it."""
    from engine import reports

    staff = _is_gm(character)
    parts = (rest or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        character.session.send(f"Usage: {cmd_name} comment <id> <text>")
        return
    entry_id = _parse_ticket_id(parts[0])
    if entry_id is None:
        character.session.send(f"Usage: {cmd_name} comment <id> <text>")
        return
    text = parts[1]
    entry = reports.get_by_id(kind, entry_id, directory=game.report_dir)
    if entry is None or not reports.can_ticket_read(
        entry, character.key, staff=staff, game=game, character=character,
    ):
        label = reports.DISPLAY_LABELS.get(kind, "ticket").lower()
        character.session.send(f"No {label} ticket #{entry_id} of yours.")
        return
    if not reports.can_ticket_comment(
        entry, character.key, staff=staff, game=game, character=character,
    ):
        character.session.send(
            f"Ticket #{entry_id} is {entry.get('status', 'closed')} — "
            "you can still read it, but only staff can add notes now."
        )
        return
    tick = int(getattr(game, "game_time_ticks", 0) or 0)
    try:
        reports.append_comment(
            kind, entry_id, character.key, text,
            directory=game.report_dir, staff=staff, tick=tick,
        )
    except (ValueError, IndexError, reports.ReportsIOError) as exc:
        character.session.send(str(exc))
        return
    label = reports.DISPLAY_LABELS.get(kind, "ticket")
    if staff:
        character.session.send(f"Commented on {label} #{entry_id}.")
    else:
        character.session.send(
            f"Noted on ticket #{entry_id}. Staff can see it with reports show."
        )
    _notify_ticket_comment(
        game, kind, entry, character, text, staff=staff,
    )


def _cmd_my_reports(character, args, game, kind, *, noun, file_verb, cmd_name, title):
    """Shared body for ``bugs`` / ``ideas`` -- list, read, or comment."""
    from engine import reports
    from engine import style

    raw = (args or "").strip()
    parts = raw.split(maxsplit=1)
    if parts:
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if sub in ("read", "show"):
            _cmd_report_thread_read(
                character, rest, game, kind, cmd_name=cmd_name,
            )
            return
        if sub == "comment":
            _cmd_report_thread_comment(
                character, rest, game, kind, cmd_name=cmd_name,
            )
            return

    show_all = False
    for token in raw.split():
        if token.lower() == "all":
            show_all = True
            continue
        character.session.send(
            f"Usage: {cmd_name} [all]  |  {cmd_name} read <id>  |  "
            f"{cmd_name} comment <id> <text>"
        )
        return

    entries = reports.by_reporter(
        kind, character.key, directory=game.report_dir,
        open_only=not show_all, game=game, character=character,
    )
    if not entries and show_all:
        character.session.send(
            f"You have not filed any {noun} yet. "
            f"Use `{file_verb} <description>` to send one to staff."
        )
        return

    scope = "all statuses" if show_all else "open only"
    body = [style.paint("muted", f"({scope})")]
    if not entries and not show_all:
        keys = reports.reporter_keys_for_character(game, character)
        cnums = reports.reporter_cnums_for_character(game, character)
        recent_resolved = reports.recently_resolved_for_keys(
            kind, keys, game.report_dir, cnums=cnums,
        )
        if recent_resolved:
            body.append(
                "You have no open tickets. These were marked resolved "
                "recently (often after a world rewrite):"
            )
            body += _my_reports_lines(
                kind, recent_resolved, noun=noun, file_verb=file_verb,
                cmd_name=cmd_name, viewer_key=character.key,
            )
            body.append("")
            body.append(
                f"Type `{cmd_name} all` for full history, or wait for a "
                "thank-you line when staff ship a fix while you are online."
            )
            lines = style.format_sheet(
                title, body, width=52,
                screenreader=bool(getattr(character, "screenreader", False)),
            )
            character.session.send("\r\n".join(lines))
            return

    body += _my_reports_lines(
        kind, entries, noun=noun, file_verb=file_verb, cmd_name=cmd_name,
        viewer_key=character.key,
    )
    lines = style.format_sheet(
        title, body, width=52,
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    character.session.send("\r\n".join(lines))


def cmd_bugs(character, args, game):
    """List bug reports this character filed that are still open.

    ``bugs`` shows open tickets only; ``bugs all`` includes resolved and
    rejected history. Use when you forgot whether you already filed something.
    """
    from engine import reports
    _cmd_my_reports(
        character, args, game, reports.BUG,
        noun="bug reports", file_verb="bug", cmd_name="bugs",
        title="YOUR BUGS",
    )


def cmd_ideas(character, args, game):
    """List suggestions this character filed that are still open.

    Same shape as ``bugs``: ``ideas`` for open tickets, ``ideas all`` for
    full history. Alias: ``suggestions``.
    """
    from engine import reports
    _cmd_my_reports(
        character, args, game, reports.SUGGEST,
        noun="suggestions", file_verb="suggest", cmd_name="ideas",
        title="YOUR IDEAS",
    )


def cmd_typos(character, args, game):
    """List typo reports this character filed that are still open."""
    from engine import reports
    _cmd_my_reports(
        character, args, game, reports.TYPO,
        noun="typo reports", file_verb="typo", cmd_name="typos",
        title="YOUR TYPOS",
    )


def _reports_section(header, label, entries, game=None):
    """Build the lines for one 'reports' section (all bugs, or all ideas).

    entries is already the slice to display, oldest-first. Always emits the
    header, even for an empty section, so a GM can tell "nothing open" from
    "reports is broken." *game* lets reporter keys resolve to legal names.
    """
    lines = [header]
    if not entries:
        lines.append("  (none)")
        return lines
    from engine.char_identity import reporter_display_name
    for entry in entries:
        entry_id = entry.get("id", "?")
        status = entry.get("status", "open")
        time = entry.get("time", "?")
        reporter_raw = entry.get("reporter", "?")
        # Storage key stays in JSONL; display prefers legal_public_name.
        reporter = reporter_display_name(game, reporter_raw)
        description = entry.get("description", "")
        from engine import reports as reports_mod
        n_comments = reports_mod.comment_count(entry)
        comment_bit = f" [{n_comments} comments]" if n_comments else ""
        badge_bit = reports_mod.staff_ticket_badges(entry)
        lines.append(
            f"  [{label} #{entry_id}] ({status}) {time} {reporter}: "
            f"{description}{badge_bit}{comment_bit}"
        )
    return lines


def _parse_reports_list_tokens(args):
    """Parse optional kind filter, count, and status from list args.

    Returns (kind_filter, n, status_filter, err). *status_filter* is
    ``open`` (inbox, default), ``approved`` (ideas waiting to ship), or
    ``all``. Bare ``reports approved`` implies ideas.
    """
    from engine import reports

    raw = (args or "").strip()
    kind_filter = None
    tail = raw
    if raw:
        parts = raw.split(maxsplit=1)
        maybe_kind = reports.parse_kind_word(parts[0])
        if maybe_kind is not None:
            kind_filter = maybe_kind
            tail = parts[1] if len(parts) > 1 else ""

    usage = (
        "Usage: reports [bugs|ideas|typos|help] [n] [all|approved]  |  "
        "reports approved [n]  |  "
        "reports show <bug|suggest|help|typo> <id>  |  "
        "reports approve <id>"
    )
    n = 5
    status_filter = "open"
    for token in tail.split():
        low = token.lower()
        if low == "all":
            status_filter = "all"
            continue
        if low in ("approved", "approve"):
            # "approve" here is the list filter typo; the mark verb is
            # dispatched before this parser (reports approve <id>).
            status_filter = "approved"
            continue
        if low == "open":
            status_filter = "open"
            continue
        try:
            n = int(token)
        except ValueError:
            return None, None, None, usage
        if n <= 0:
            return None, None, None, f"{usage}  (n must be a positive number)"
    if status_filter == "approved" and kind_filter is None:
        kind_filter = reports.SUGGEST
    return kind_filter, n, status_filter, None


def _reports_kind_slice(kind, *, n, status_filter, directory):
    """Last *n* entries for one kind, filtered by staff list status."""
    from engine import reports

    entries = reports.recent(kind, None, directory=directory)
    if status_filter == "all":
        pass
    elif status_filter == "approved":
        entries = [e for e in entries if e.get("status") == "approved"]
    else:
        entries = [e for e in entries if e.get("status", "open") == "open"]
    return entries[-n:]


def _reports_kind_noun(kind):
    """Short plural label for empty-state / scope lines."""
    from engine import reports

    if kind == reports.BUG:
        return "bugs"
    if kind == reports.SUGGEST:
        return "ideas"
    if kind == reports.TYPO:
        return "typos"
    if kind == reports.HELP:
        return "help proposals"
    if kind == reports.PREPORT:
        return "player reports"
    return "reports"


def cmd_reports(character, args, game):
    """GM command: list bug, suggestion ("idea"), typo, and help-proposal
    reports in separate sections -- open bugs, then open ideas, typos, then
    help proposals -- instead of one time-interleaved list.

    Usage: reports [bugs|ideas|typos|help] [n] [all|approved]
    - n defaults to 5 (the last n OPEN entries per kind or for the filter).
    - 'all' also includes resolved/rejected entries.
    - 'approved' lists ideas marked yes-implement, not yet shipped
      (bare reports approved implies ideas).
    - reports show <bug|suggest|help|typo> <id>  -- full one-ticket dump
      (same as gm reports show …). See help gm-reports.
    - reports comment <kind> <id> <text> -- staff note on the thread
      (does not resolve; players use bugs comment / ideas comment).
    - reports approve <id> -- mark an idea approved (squashsuggest queue).

    Non-GMs get a distinct hint (not the death-scene or hunt-turn-in verbs).
    """
    from engine import reports
    if not _is_gm(character):
        character.session.send(
            "Staff tickets are gm reports. File a ticket with bug. "
            "Death on scene is report death. Hunt turn-in is missions report."
        )
        return

    raw = (args or "").strip()
    if raw.lower().startswith("show "):
        cmd_report_show(character, raw[5:].strip(), game)
        return
    if raw.lower().startswith("comment "):
        cmd_report_comment(character, raw[8:].strip(), game)
        return
    first, _, approve_rest = raw.partition(" ")
    if first.lower() == "approve":
        cmd_report_approve(character, approve_rest.strip(), game)
        return

    kind_filter, n, status_filter, err = _parse_reports_list_tokens(args)
    if err:
        character.session.send(err)
        return

    from engine import style

    def _empty_msg(kind):
        noun = _reports_kind_noun(kind) if kind else "reports"
        if status_filter == "all":
            return f"No {noun} logged yet." if kind else "No reports logged yet."
        if status_filter == "approved":
            return f"No approved {noun} waiting to ship."
        return f"No open {noun}." if kind else "No open reports."

    def _scope_line(kind=None):
        noun = _reports_kind_noun(kind) if kind else "each kind"
        if status_filter == "all":
            if kind:
                return f"last {n} {noun}, all statuses"
            return f"up to {n} of each kind, all statuses"
        if status_filter == "approved":
            if kind:
                return f"last {n} approved {noun}"
            return f"up to {n} approved of each kind"
        if kind:
            return f"last {n} open {noun}"
        return f"up to {n} of each kind"

    if kind_filter is not None:
        entries = _reports_kind_slice(
            kind_filter, n=n, status_filter=status_filter,
            directory=game.report_dir,
        )
        if not entries:
            character.session.send(_empty_msg(kind_filter))
            return
        label = reports.DISPLAY_LABELS.get(kind_filter, kind_filter.upper())
        header = {
            reports.BUG: "Bugs:",
            reports.SUGGEST: "Ideas:",
            reports.TYPO: "Typos:",
            reports.HELP: "Help ideas:",
            reports.PREPORT: "Player conduct:",
        }.get(kind_filter, f"{label}:")
        body = [style.paint("muted", f"({_scope_line(kind_filter)})")]
        body += _reports_section(header, label, entries, game=game)
        title = f"REPORTS -- {label}"
        lines = style.format_sheet(
            title, body, width=52,
            screenreader=bool(getattr(character, "screenreader", False)),
        )
        character.session.send("\r\n".join(lines))
        return

    # Combined sheet: last n of each kind.
    sections = []
    for kind, header, label in reports.REPORT_LIST_SECTIONS:
        entries = _reports_kind_slice(
            kind, n=n, status_filter=status_filter,
            directory=game.report_dir,
        )
        sections.append((header, label, entries))

    if not any(entries for _h, _l, entries in sections):
        character.session.send(_empty_msg(None))
        return

    body = [style.paint("muted", f"({_scope_line()})")]
    first = True
    for header, label, entries in sections:
        if not first:
            body.append(style.wrought_rule(48))
        first = False
        body += _reports_section(header, label, entries, game=game)
    lines = style.format_sheet(
        "REPORTS", body, width=52,
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    character.session.send("\r\n".join(lines))


def cmd_report_approve(character, args, game):
    """GM: reports approve <id> -- mark an idea approved, not closed.

    Approved ideas leave the open inbox (``reports 100``) and wait on
    ``reports approved`` until squashsuggest ships them. ``resolve
    suggest <id> approved`` is the same status flip.
    """
    from engine import reports

    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    usage = (
        "Usage: reports approve <id>  "
        "(marks an idea approved, not shipped — see help gm-reports)"
    )
    raw = (args or "").strip()
    if not raw:
        character.session.send(usage)
        return
    parts = raw.split()
    id_text = parts[0]
    if reports.parse_kind_word(parts[0]) == reports.SUGGEST and len(parts) >= 2:
        id_text = parts[1]
    elif reports.parse_kind_word(parts[0]) not in (None, reports.SUGGEST):
        character.session.send(
            "Only ideas can be approved. "
            "Bugs stay open until squashbugs / resolve."
        )
        return
    try:
        suggestion_id = int(id_text.lstrip("#"))
    except ValueError:
        character.session.send(usage)
        return

    entry = reports.get_by_id(
        reports.SUGGEST, suggestion_id, directory=game.report_dir,
    )
    if entry is None:
        character.session.send(f"No idea report #{suggestion_id}.")
        return
    current = entry.get("status", "open")
    if current == "approved":
        character.session.send(
            f"Idea #{suggestion_id} is already approved. "
            "squashsuggest will queue it."
        )
        return
    if current in ("resolved", "rejected"):
        character.session.send(
            f"Idea #{suggestion_id} is {current} — reopen it first "
            f"(resolve suggest {suggestion_id} open)."
        )
        return
    try:
        reports.mark(
            reports.SUGGEST, suggestion_id, "approved",
            directory=game.report_dir, game=game,
        )
    except (IndexError, ValueError) as exc:
        character.session.send(str(exc))
        return
    character.session.send(
        f"Marked idea #{suggestion_id} approved. "
        "It is off the open list; type reports approved to see it. "
        "squashsuggest will open a PR for approved ideas."
    )
    print(f"[GM] {character.key} marked idea #{suggestion_id} approved.")


def cmd_report_show(character, args, game):
    """GM command: show <bug|suggest|help> <id> -- one full report dump.

    Prints description, session history, tracebacks, and the context block
    for a single ticket. The <id> is the number from ``gm reports``
    (``[BUG #N]``, ``[IDEA #N]``, ``[HELP #N]``). Non-GMs are rejected.
    """
    from engine import reports
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    usage = (
        "Usage: reports show <bug|suggest|help|typo> <id>  "
        "(see help gm-reports)"
    )
    parts = (args or "").split()
    if len(parts) != 2:
        character.session.send(usage)
        return
    kind_word, id_text = parts
    kind = reports.parse_kind_word(kind_word)
    if kind is None:
        character.session.send(usage)
        return
    try:
        entry_id = int(id_text.lstrip("#"))
    except ValueError:
        character.session.send(usage)
        return

    entry = reports.get_by_id(kind, entry_id, directory=game.report_dir)
    if entry is None:
        label = reports.DISPLAY_LABELS.get(kind, kind_word).lower()
        character.session.send(f"No {label} report #{entry_id}.")
        return

    label = reports.DISPLAY_LABELS.get(kind, kind_word)
    from engine import style
    body = reports.format_entry_lines(kind, entry, game=game)
    lines = style.format_sheet(
        f"REPORT {label} #{entry_id}",
        body,
        width=52,
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    character.session.send("\r\n".join(lines))


def cmd_report_comment(character, args, game):
    """GM: reports comment <kind> <id> <text> -- staff note, does not resolve."""
    from engine import reports

    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    usage = (
        "Usage: reports comment <bug|suggest|help|typo> <id> <text>  "
        "(does not resolve; see help gm-reports)"
    )
    parts = (args or "").strip().split(maxsplit=2)
    if len(parts) < 3:
        character.session.send(usage)
        return
    kind_word, id_text, text = parts
    kind = reports.parse_kind_word(kind_word)
    if kind is None:
        character.session.send(usage)
        return
    entry_id = _parse_ticket_id(id_text)
    if entry_id is None:
        character.session.send(usage)
        return
    entry = reports.get_by_id(kind, entry_id, directory=game.report_dir)
    if entry is None:
        label = reports.DISPLAY_LABELS.get(kind, kind_word).lower()
        character.session.send(f"No {label} report #{entry_id}.")
        return
    tick = int(getattr(game, "game_time_ticks", 0) or 0)
    try:
        reports.append_comment(
            kind, entry_id, character.key, text,
            directory=game.report_dir, staff=True, tick=tick,
        )
    except (ValueError, IndexError, reports.ReportsIOError) as exc:
        character.session.send(str(exc))
        return
    label = reports.DISPLAY_LABELS.get(kind, kind_word)
    character.session.send(f"Commented on {label} #{entry_id}.")
    _notify_ticket_comment(
        game, kind, entry, character, text, staff=True,
    )


def cmd_resolve(character, args, game):
    """GM command: resolve <bug|suggest|help|typo> <id> [status] [reason...]

    Flip a logged report's status. Omit the status to mark resolved
    (``resolve bug 39``). A trailing reason is stored as a staff comment
    so the ticket does not stay open for discussion after a decision
    (suggestion report 332). ``approved`` parks an idea for squashsuggest
    without closing it. ``rejected`` or ``open`` still work when triaging.
    Non-GMs are rejected with nothing changed.
    """
    from engine import reports
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    usage = (
        "Usage: resolve <bug|suggest|help|typo> <id> "
        "[open|approved|resolved|rejected] [reason...]"
    )
    parts = args.split()
    if len(parts) < 2:
        character.session.send(usage)
        return
    kind_word, id_text = parts[0], parts[1]
    rest = parts[2:]
    status = "resolved"
    reason_bits = []
    if rest:
        # Third token is a status word, or the start of the close reason.
        if rest[0].lower() in reports.STATUSES:
            status = rest[0].lower()
            reason_bits = rest[1:]
        else:
            reason_bits = rest
    kind = reports.parse_kind_word(kind_word)
    if kind is None:
        character.session.send(usage)
        return
    try:
        entry_id = int(id_text)
    except ValueError:
        character.session.send(usage)
        return

    reason = " ".join(reason_bits).strip()
    if reason:
        if len(reason) > reports.MAX_COMMENT_CHARS:
            character.session.send(
                f"Keep the close reason under {reports.MAX_COMMENT_CHARS} "
                f"characters (yours is {len(reason)})."
            )
            return
        try:
            reports.append_comment(
                kind,
                entry_id,
                character.key,
                reason,
                directory=game.report_dir,
                staff=True,
                tick=int(getattr(game, "game_time_ticks", 0) or 0),
            )
        except (IndexError, ValueError) as exc:
            character.session.send(str(exc))
            return

    try:
        reports.mark(kind, entry_id, status, directory=game.report_dir, game=game)
    except IndexError as exc:
        character.session.send(str(exc))
        return

    if reason:
        character.session.send(
            f"Marked {kind_word} #{entry_id} as {status} "
            f"(reason noted on the ticket)."
        )
    else:
        character.session.send(f"Marked {kind_word} #{entry_id} as {status}.")
    print(f"[GM] {character.key} marked {kind_word} #{entry_id} as {status}.")
