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

from command_support import (
    _can_see_spirit,
    _is_presence_hidden,
    _display_name,
    floor_item_look_lines,
    _find_character,
    _find_item,
    _find_item_prefer_locked,
    _is_gm,
    _presence_face,
    _public_label,
    is_staff_stealth_presence,
    _move_one,
    _pull_followers,
)
from engine.hooks import (
    get_help_categories,
    get_help_topics,
    item_drop_refusal,
    upgrade_legacy_container,
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
    stripped = args.strip()
    if stripped:
        # `look in <thing>` -- body belongings (#49).
        lower = stripped.lower()
        if lower.startswith("in "):
            _look_in(character, stripped[3:].strip(), game)
            return
        _look_at(character, stripped)
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
    # never shown as the terrain label). Plain text; color is decoration.
    area_tag = getattr(room, "area_type", "plains").title()

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
        import maps as maps_mod
        for vista_line in maps_mod.landmark_vista_lines(room):
            extras.append(vista_line)
    # Pocket zone travel is separate from cardinal / in-out moves.
    zone_entries = getattr(room, "zone_entries", None) or {}
    if zone_entries:
        # Show unique hub names (not every alias).
        hubs = sorted({hub.key for hub in zone_entries.values()})
        hint = ", ".join(hubs[:4])
        extras.append(f"Enter: enter <name> -- here: {hint}")
    # Exit only at the pocket mouth you entered (zone_exit + entry stamp).
    # Keep these short -- TTS reads them on every look in town.
    revalidate_zone_entry_stamp(character, game)
    stamped_entry = getattr(character, "zone_entry_hub_key", None)
    screenreader = bool(getattr(character, "screenreader", False))
    if stamped_entry and room.key != stamped_entry:
        if getattr(room, "zone", None) or getattr(room, "zone_exit_to", None):
            if screenreader:
                extras.append(f"Zone exit: {stamped_entry}.")
            else:
                extras.append(
                    f"Zone exit at {stamped_entry} -- type exit there."
                )
    elif (
        getattr(room, "zone_exit", False)
        and getattr(room, "zone_exit_to", None) is not None
        and (not stamped_entry or room.key == stamped_entry)
    ):
        if screenreader:
            extras.append("Zone exit: type exit.")
        else:
            dest_key = getattr(room.zone_exit_to, "key", None) or "overland"
            extras.append(f"Zone exit: type exit ({dest_key}).")
    # Outdoor ambient sky (open-air rooms: overland + tagged town streets).
    # Spawns still key off wilderness; look flavor keys off outdoor.
    # Weather clause (CONUS climatology) prefers the game's weather model
    # (supers.weather via hook) when available; eclipse still wins over
    # ordinary sky when active.
    if getattr(room, "outdoor", False):
        from engine import game_calendar
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
    if not whiteout:
        for line in hooks.room_look_extras(room, game, character):
            if line:
                extras.append(line)

    # Paths: (direction, destination look label) -- columns in format_room.
    # Game may hide exits (e.g. closed Devil's Gates) via filter_look_exits.
    # D66: also hide secret directions until this character has searched.
    # Street-address exits: avoid "12223: 12223 Campbell Pass" — show
    # "12223: Campbell Pass" when the dest title already starts with the number.
    exits = []
    floor_items = []
    souls = []
    if not whiteout:
        for direction, dest in room.exits.items():
            if not hooks.look_exit_visible(dest, game):
                continue
            if not vision_mod.character_knows_exit(character, room, direction):
                continue
            # Dual-layer wilderness: exits point at self -- label what that
            # step approaches (Lebanon / bunker / terrain) for sighted + SR.
            title = hooks.look_exit_dest_label(
                room, direction, dest, game=game, character=character,
            )
            if not title:
                title = dest.look_title()
            if str(direction).isdigit():
                from engine.room_naming import strip_address_from_exit_label
                title = strip_address_from_exit_label(direction, title)
            exits.append((direction, title))

        # Items = floor loot; Souls = other characters (not you; spirits you
        # can't see are skipped -- section 6). Section label is Items (not
        # Relics) so it never collides with Divine/Path relic content.
        # Identical catalog stacks collapse to ``N X are here`` (digits).
        floor_item_objs = [
            o for o in room.contents if isinstance(o, Item)
        ]
        floor_items = floor_item_look_lines(floor_item_objs, character)
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
            if _is_presence_hidden(character, o):
                continue
            label = _display_name(o, viewer=character)
            souls.append(
                hooks.room_presence_line(label, o, room, game)
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
    if getattr(character, "gm_mode", False):
        raw_v = getattr(room, "vnum", None)
        if raw_v is not None and str(raw_v).strip():
            try:
                staff_vnum = room_vnum_mod.validate_vnum(raw_v)
            except ValueError:
                staff_vnum = str(raw_v).strip()
    room_heading = room_naming_mod.paint_structured_room_title(
        character,
        plain_name,
        room=room,
        game=game,
        staff_vnum=staff_vnum,
    )

    # Local ASCII map embeds only when config maplook is on (default off
    # -- short classic look). Bare ``map`` / mapmove still available.
    # Whiteout: no minimap — you cannot read the street grid either.
    local_map_lines = None
    display_prefs.ensure_display_defaults(character)
    if (
        not whiteout
        and getattr(character, "map_on_look", False)
        and getattr(character, "show_minimap", True)
        and not getattr(character, "screenreader", False)
    ):
        import maps as maps_mod
        map_center = room
        # Dual-layer vehicles: resolve America cell for the overland window.
        if getattr(room, "grid_prefix", None) is None:
            from engine import hooks
            resolved = hooks.map_center_room(character, game)
            if resolved is not None and getattr(
                resolved, "grid_prefix", None
            ) is not None:
                map_center = resolved
        rendered = maps_mod.render_local_map(
            game.rooms,
            map_center,
            use_color=getattr(character, "use_color", True),
            compact=True,
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
        and getattr(character, "brief", False)
        and not whiteout
    ):
        look_description = ""

    lines = style.format_room(
        room_heading,
        look_description,
        area_tag=area_tag,
        exits=exits,
        souls=souls,
        items=floor_items,
        extras=extras or None,
        width=display_prefs.sheet_width(character),
        screenreader=bool(getattr(character, "screenreader", False)),
        local_map_lines=local_map_lines,
        exits_verbose=bool(getattr(character, "exits_verbose", True)),
    )
    character.session.send("\r\n".join(lines))
    # Builder/debug until Phase 3: show VNUM + internal graph id (hidden
    # from players). Useful when many rooms share a ROOM NAME.
    if getattr(character, "gm_mode", False):
        vnum = getattr(room, "vnum", None) or "(none)"
        internal = room_vnum_mod.internal_room_key(room)
        character.session.send(
            f"[GM] vnum={vnum}  internal={internal}"
        )
    # Soft fear nudge: weak player Vampires sense a co-located Slayer.
    # (hook -- no-op / None without a game installed; Phase 2 purity.)
    from engine import hooks
    fear = hooks.vampire_fear_message(character, room)
    if fear:
        character.session.send(fear)
    # Procurer case read / other game tells after bare look.
    for line in hooks.after_bare_look(character, room, game):
        if line:
            character.session.send(line)
    # Blank before the custom prompt comes from send_prompt (dispatch),
    # not here -- avoid double-spacing after look.
    # GMCP Room.Info -- also covers auto-look after move (_move_one).
    from engine import gmcp
    gmcp.push_room(character)
    if after_move:
        maybe_map_after_move(character, game)


def maybe_map_after_move(character, game):
    """Print the local minimap after a move when ``config mapmove`` is on.

    Skips screenreader and when ``config map`` is off. Does not run when
    ``map_on_look`` already embedded the map in the auto-look above.
    """
    from engine import display_prefs

    display_prefs.ensure_display_defaults(character)
    if not getattr(character, "map_on_move", False):
        return
    if getattr(character, "map_on_look", False):
        # Already shown inside look -- avoid a second dump.
        return
    if character.session is None:
        return
    if getattr(character, "screenreader", False):
        return
    if not getattr(character, "show_minimap", True):
        return
    room = getattr(character, "location", None)
    if room is None:
        return
    import maps as maps_mod

    map_center = room
    if getattr(room, "grid_prefix", None) is None:
        from engine import hooks
        resolved = hooks.map_center_room(character, game)
        if resolved is not None and getattr(
            resolved, "grid_prefix", None
        ) is not None:
            map_center = resolved
    rendered = maps_mod.render_local_map(
        game.rooms,
        map_center,
        use_color=getattr(character, "use_color", True),
        compact=True,
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
    lines = []
    for direction, dest in (room.exits or {}).items():
        if not hooks.look_exit_visible(dest, game):
            continue
        if not vision_mod.character_knows_exit(character, room, direction):
            continue
        title = dest.look_title()
        if str(direction).isdigit():
            from engine.room_naming import strip_address_from_exit_label
            title = strip_address_from_exit_label(direction, title)
        lines.append(f"  {direction}: {title}")
    if not lines:
        character.session.send("Exits: none you can see.")
        return
    character.session.send("Exits:")
    for line in lines:
        character.session.send(line)


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


def cmd_map(character, args, game):
    """Local ASCII minimap: overland grid, Studio layout, or exit-graph.

    Prefs #18 / #30: ``config map off`` or screenreader mode skips ASCII.
    Screenreader gets a directional text summary instead (exits + terrain).

    ``map big`` / ``map full`` / ``map atlas`` (alias: ``bigmap``)
    dump the entire current overland grid through the pager so America
    (78x18) or the Wastes (100x100) stay readable with more. Bare
    ``atlas`` is the travel verb (America map + hubs) -- see help atlas.
    Town / dungeon rooms use the hybrid local map (layout coords when
    stamped, else linked-exit neighborhood). Look embeds the same window
    when ``config map`` is on.
    """
    from engine import display_prefs
    from engine import pager as pager_mod

    display_prefs.ensure_display_defaults(character)
    want_full = _map_wants_full(args)
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
        if want_full:
            lines.extend(_full_map_sr_lines(character, game))
        character.session.send("\r\n".join(lines))
        return
    if not getattr(character, "show_minimap", True):
        character.session.send(
            "Map is off. Type 'config map on' to show the ASCII minimap."
        )
        return
    import maps as maps_mod
    room = _overland_map_center_room(character, game)
    use_color = getattr(character, "use_color", True)
    if want_full:
        rendered = _render_full_map_for(character, game, use_color=use_color)
        if rendered is None:
            character.session.send(
                "No full map here. Stand on an overland grid cell "
                "(Wastes, America Overland, …) and try 'map big' again."
            )
            return
        # Page so a 100x100 Wastes dump does not flood the client.
        pager_mod.page(character, rendered)
        return
    # Respect the player's color preference (#51) -- letter glyphs stay the
    # primary signal either way (section 8 a11y).
    rendered = maps_mod.render_local_map(
        game.rooms, room, use_color=use_color
    )
    if rendered is None:
        character.session.send(
            "No map here. (Need a room -- try looking first.)"
        )
        return
    # render_* joins with \\n; convert to telnet \\r\\n for the wire.
    character.session.send(rendered.replace("\n", "\r\n"))


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
    return width, height, wrap


def _overland_map_center_room(character, game):
    """Room used as map center: location, or America cell from macro_pos.

    Dual-layer vehicles sit in an interior Room without grid stamps, so
    ``map`` / ``map big`` resolve the atlas cell from ``macro_pos`` via
    the map_center_room hook (Phase 2 purity -- no supers import here).
    """
    from engine import hooks
    room = getattr(character, "location", None)
    if room is not None and getattr(room, "grid_prefix", None) is not None:
        return room
    resolved = hooks.map_center_room(character, game)
    if resolved is not None:
        return resolved
    return room


def _render_full_map_for(character, game, *, use_color=True):
    """Render the full-grid atlas string, or None if not on a sized grid."""
    import maps as maps_mod

    room = _overland_map_center_room(character, game)
    width, height, wrap = _grid_meta_for_room(game, room)
    if width is None or height is None:
        return None
    return maps_mod.render_full_grid(
        game.rooms,
        room,
        width=width,
        height=height,
        use_color=use_color,
        wrap=wrap,
    )


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
    """Alias: dump the full overland grid (same as ``map big``)."""
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
    visible = [
        (direction, dest.look_title())
        for direction, dest in exits.items()
        if hooks.look_exit_visible(dest, game)
        and vision_mod.character_knows_exit(character, room, direction)
    ]
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
        character.session.send(
            f"{_display_name(character)}\r\n{character.description}"
        )
        from engine import hooks
        for line in hooks.look_extra_lines(character, character):
            character.session.send(line)
        return True

    # Carried inventory first -- tactile even in pitch dark (you know what
    # you are holding). Floor loot waits until vision clears below.
    item = _find_item(query, character.inventory)
    if item:
        character.session.send(item.description)
        from engine import hooks
        game = getattr(getattr(character, "session", None), "game", None)
        hooks.after_look_item(character, item, game)
        return True

    room = character.location
    if not vision_mod.can_see_room(character, room):
        # Handled: examine must not fall through to "You don't see that."
        character.session.send(
            "It is pitch dark. You can't make that out."
        )
        return True

    items_here = [o for o in room.contents if isinstance(o, Item)]
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
        character.session.send(desc)
        from engine import hooks
        game = getattr(getattr(character, "session", None), "game", None)
        hooks.after_look_item(character, item, game)
        return True

    from engine.char_identity import parse_target_ordinal
    from command_support import _collect_character_matches

    ordinal, rest = parse_target_ordinal(query)
    visible = [
        c for c in room.characters()
        if c is character or not _is_presence_hidden(character, c)
    ]
    matches = _collect_character_matches(
        rest, visible, self_character=character,
    )
    if len(matches) > 1 and ordinal is None:
        character.session.send(
            f"Which one? Try 'look 2.{rest}' or 'look other {rest}' "
            f"({len(matches)} matches here)."
        )
        return True
    target = _find_character(
        query, room.characters(), self_character=character,
    )
    if target and _is_presence_hidden(character, target):
        # Section 6 spirit-sight + living Reaper Mantle veil: same rule
        # cmd_look's souls list applies -- you can't examine what you
        # can't perceive.
        target = None
    if target:
        # Viewer-relative header + body so hood / unintroduced never leak
        # login keys or unique setdesc text to strangers.
        from engine import hooks
        header = _display_name(target, viewer=character)
        body = hooks.look_body_for(character, target)
        if body is None:
            body = target.description
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

    return False


def _look_in(character, query, game=None):
    """List belongings nested inside a body, or game-handled containers.

    Bodies: nested loot (suggestions.log #49). Game content (e.g. home
    refrigerators) registers via engine.hooks.look_in_item -- the engine
    never imports SUPERS. Dark rooms block look-in without light/night-sight.
    """
    from world import Item
    from engine import hooks
    from engine import vision as vision_mod
    if not query:
        character.session.send("Look in what?")
        return
    room = character.location
    if not vision_mod.can_see_room(character, room):
        character.session.send(
            "It is pitch dark. You can't make that out."
        )
        return
    items_here = [o for o in room.contents if isinstance(o, Item)]
    item = _find_item(query, items_here)
    if item is None:
        character.session.send("You don't see that here.")
        return
    if game is None:
        game = getattr(getattr(character, "session", None), "game", None)
    handled = hooks.look_in_item(character, item, game)
    if handled:
        for line in handled:
            character.session.send(line)
        return
    if not getattr(item, "is_body", False):
        character.session.send(f"You can't look in {item.key}.")
        return
    loot = getattr(item, "loot", None) or []
    if not loot:
        character.session.send(f"You look in {item.key} -- nothing of note.")
        return
    names = ", ".join(o.key for o in loot)
    character.session.send(f"Looking in {item.key}, you find: {names}.")
    hooks.after_look_in_body(character, item, game)


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
    """Stand up from sitting (bug #58). Resting/asleep still need 'wake'."""
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
    if getattr(character, "asleep", False) or getattr(character, "resting", False):
        character.session.send("You're resting -- type 'wake' to get up.")
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
    if getattr(character, "asleep", False):
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
    if hooks.try_directional_move(character, direction, game):
        return
    room = character.location
    dest = room.exits.get(direction)   # .get() returns None if there's no such exit
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

    Staff GMs use a separate diagnostic tail (``staff_tailing``) so
    ``follow`` does not form a Group or touch pack glue.
    """
    from engine.command_support import (
        start_following,
        start_staff_tail,
        stop_following,
        stop_staff_tail,
    )
    from engine import group as group_mod
    name = args.strip()
    if _is_gm(character):
        if not name:
            if getattr(character, "staff_tailing", None) is not None:
                stop_staff_tail(character)
                return
            if group_mod.in_group(character) and not group_mod.is_leader(character):
                if group_mod.try_leave_group(character, game, confirm=False) == "blocked":
                    return
            stop_following(character)
            return

        target = _find_character(name, character.location.characters())
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

    target = _find_character(name, character.location.characters())
    if not target:
        character.session.send(f"No one named '{name}' is here.")
        return
    if target is character:
        character.session.send("You can't follow yourself.")
        return
    if character.following is target:
        character.session.send(f"You're already following {target.key}.")
        return

    start_following(character, target)
    character.session.send(f"You start following {target.key}.")


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
    stop_following(character)


def cmd_group(character, args, game):
    """Show follow/beckon party roster, or set your display row.

    Bare ``group`` lists members with lifeforce % and Front/Back Row,
    plus any game ``Group wants`` objective (pack convoy / shared needs)
    so a live or idlemode leader can lead the errand (food, wash, …).
    ``group front`` / ``group back`` (or ``group row front|back``) sets
    *your* display row only -- not combat math yet.

    ``group leave confirm`` peels a follower off the party.
    ``group disband confirm`` breaks the whole party (leader).
    """
    from engine import group as group_mod
    from engine import display_prefs as dprefs
    dprefs.ensure_display_defaults(character)
    raw = (args or "").strip().lower()
    if not raw:
        character.session.send(group_mod.format_group_sheet(character, game))
        return
    parts = raw.split()
    choice = parts[0]
    confirm = "confirm" in parts
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
    if choice == "row" and len(parts) >= 2:
        choice = parts[1]
    if choice in ("front", "f", "fore"):
        group_mod.set_row(character, group_mod.ROW_FRONT)
        character.session.send(
            f"You shift to the {group_mod.ROW_LABELS[group_mod.ROW_FRONT]}."
        )
        return
    if choice in ("back", "b", "rear"):
        group_mod.set_row(character, group_mod.ROW_BACK)
        character.session.send(
            f"You shift to the {group_mod.ROW_LABELS[group_mod.ROW_BACK]}."
        )
        return
    character.session.send(
        "Usage: group  |  group front  |  group back  |  "
        "group leave confirm  |  group disband confirm\r\n"
        "(Front/Back Row is display-only for now -- see 'help group'.)"
    )


def _stop_following(character, silent=False):
    """Compat wrapper -- prefer stop_following from command_support."""
    from engine.command_support import stop_following
    stop_following(character, silent=silent)


def _do_transition(character, dest, game, leave_text, arrive_text):
    """Shared leave/arrive/look/encounter for enter, exit, in, out."""
    from engine import hooks
    from engine import group as group_mod
    if group_mod.block_live_group_move(character):
        return False
    if getattr(character, "asleep", False):
        character.session.send(
            "You're asleep -- type 'wake' before you can move."
        )
        return False
    hooks.cancel_rest(character)
    room = character.location
    # Game hook may spill barred actors off no_loiter hubs (Central Plaza).
    dest = hooks.transition_dest(character, dest, game)
    block_message = hooks.move_gate_block(character, room, dest, game)
    if block_message:
        character.session.send(block_message)
        return False
    # True-invis staff presence -- same stealth as compass leave/arrive.
    stealth = is_staff_stealth_presence(character)
    if not stealth:
        room.broadcast(leave_text, exclude=character)
    character.move_to(dest)
    if not stealth:
        dest.broadcast(arrive_text, exclude=character)
    cmd_look(character, "", game)
    hooks.encounter_check(game, dest)
    return True


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
    rooms = getattr(game, "rooms", None) or {} if game else {}
    hub = rooms.get(stamped)
    if hub is None:
        clear_zone_entry(character)
        return
    hub_zone = getattr(hub, "zone", None)
    cur_zone = getattr(room, "zone", None)
    if hub_zone and cur_zone and hub_zone != cur_zone:
        clear_zone_entry(character)


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
    room = character.location
    entries = getattr(room, "zone_entries", None) or {}
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
            character.session.send(
                f"No zone named '{raw}' here. Try bare 'enter' for a list."
            )
            return
    # Epic-run partner gate + non-player dungeon refusal (game rules via hook).
    refuse = hooks.dungeon_entry_refusal(character, dest, game)
    if refuse:
        character.session.send(refuse)
        return
    # Leaving dual-layer wilderness into a classic zone.
    hooks.clear_overland_coords(character)
    stamp_zone_entry(character, dest)
    face = _presence_face(character)
    _do_transition(
        character, dest, game,
        f"{face} enters {dest.key}.",
        f"{face} arrives.",
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
            character.session.send(
                f"You entered at {stamped}. Walk back there, then type "
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
    if hooks.try_exit_zone(character, game):
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
    face = _presence_face(character)
    _do_transition(
        character, dest, game,
        f"{face} exits to the overland.",
        f"{face} arrives.",
    )


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
    room = character.location
    dest = room.exits.get("in")
    if not dest:
        character.session.send("You can't go in from here.")
        return
    face = _presence_face(character)
    _do_transition(
        character, dest, game,
        f"{face} goes in.",
        f"{face} arrives.",
    )


def cmd_go_out(character, args, game):
    """Nested indoor leave via exits['out']. Separate from zone `exit`."""
    if getattr(character, "in_vehicle", None):
        from engine import hooks
        if hooks.try_vehicle_nested_in_out(character, game, direction="out"):
            return
    room = character.location
    dest = room.exits.get("out")
    if not dest:
        character.session.send("There's no way out from here.")
        return
    face = _presence_face(character)
    _do_transition(
        character, dest, game,
        f"{face} goes out.",
        f"{face} arrives.",
    )

def cmd_say(character, args, game):
    """Speak to the room. Prefs #24: trailing ? / ! pick asks / exclaims."""
    if not args:                       # nothing to say
        character.session.send("Say what?")
        return
    from engine import display_prefs
    you_verb, they_verb = display_prefs.say_speech_verb(args)
    # Possession exile: speech stays in the personal realm pocket.
    from engine import hooks
    speak_room = character.location
    if hooks.is_consciousness_exile(character):
        sensory = hooks.consciousness_sensory_room(character)
        if sensory is not None:
            speak_room = sensory
        character.session.send(
            f'You {you_verb} into the afterlife pocket, "{args}"'
        )
        character.session.send("")
        # Only other minds in the same pocket hear (rare guests).
        if speak_room is not None:
            speak_room.broadcast(
                f'{_display_name(character)} {they_verb}, "{args}"',
                exclude=character,
                blank_after=True,
            )
        return
    # First-person line for the speaker; third-person for the room.
    character.session.send(f'You {you_verb}, "{args}"')
    # Trailing blank so the next tick / tip / chat does not glue on.
    character.session.send("")
    character.location.broadcast(
        f'{_display_name(character)} {they_verb}, "{args}"',
        exclude=character,
        blank_after=True,
    )
    # GMCP Comm.Channel -- parallel to prose, never instead of it.
    from engine import gmcp
    from world import Character as CharType
    face = _display_name(character)
    gmcp.push_comm(character.session, "say", args, face)
    for obj in list(getattr(character.location, "contents", []) or []):
        if not isinstance(obj, CharType) or obj is character:
            continue
        other = getattr(obj, "session", None)
        if other is None:
            continue
        gmcp.push_comm(other, "say", args, face)


def cmd_emote(character, args, game):
    """Free-form third-person action text.

    Prefs #25: ``emote 's eyes glow.`` becomes ``Name's eyes glow.``
    Unlike cmd_say, there is no You-vs-X split -- the line already includes
    the speaker's name.
    """
    from engine import display_prefs
    line = display_prefs.emote_body(character, args)
    if not line:
        character.session.send("Emote what?")
        return
    character.session.send(line)
    character.session.send("")
    character.location.broadcast(line, exclude=character, blank_after=True)


def cmd_tell(character, args, game):
    """Send a private message to one person anywhere in the world (unlike
    'say', which is room-only). Uses game.find_character -- the same
    exact-name, world-wide lookup GM commands like 'breaktier'/'setgravity'
    already use to target someone outside the room.

    An offline Echo (session is None) can't hear anything -- logging off
    doesn't delete a character (systems doc section 4-E), but it does mean
    nobody's there to read a tell. That case gets the SAME message as "no
    such name exists" so a 'tell' can't be used to probe who's an Echo vs.
    who was never a character at all.
    """
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        character.session.send("Tell whom what?")
        return
    name, message = parts

    target = game.find_character(name)
    if not target or target.session is None:
        character.session.send("No one by that name is available.")
        return
    # Sleep closes the outside world -- tells don't land until they wake.
    if getattr(target, "asleep", False):
        character.session.send(
            f"{target.key} is asleep and can't hear you right now."
        )
        return

    target.session.send(f'{_display_name(character)} tells you, "{message}"')
    character.session.send(f'You tell {_display_name(target)}, "{message}"')
    from engine import gmcp
    face = _display_name(character)
    gmcp.push_comm(character.session, "tell", message, face)
    gmcp.push_comm(target.session, "tell", message, face)


def _ooc_speaker_face(character, game):
    """OOC channel speaker label (feature D).

    When the speaker's Account prefers ``ooc_identity=account``, use the
    account display name. Otherwise the character presence face. Account
    names are allowed on OOC (feature E).
    """
    try:
        from engine import accounts as accounts_mod
        account = accounts_mod.account_for_character(game, character)
        if (
            account is not None
            and account.ooc_identity == accounts_mod.OOC_IDENTITY_ACCOUNT
        ):
            return account.display_name or account.name
    except Exception:
        pass
    return _display_name(character)


def cmd_ooc(character, args, game):
    """Global out-of-character chat to every connected Session.

    Usage:
      ooc <message>   speak on the global OOC channel
      ooc             show the last 20 OOC lines (server-wide ring buffer)

    Prefs #23 / #26: double-bracket prefix + unnatural muted/ooc color
    (or the player's channel_colors['ooc'] role). Same line for everyone::

        ((OOC)) [Name]: message text

    Offline Echoes have no Session and do not receive OOC. The history
    buffer lives on ``game.ooc_history`` and is saved in meta on every
    ``game.save()`` (so copyover / restart keep the last 20 lines). Still
    a short ring — not a forever chat log.
    """
    from engine import display_prefs
    from engine import style
    display_prefs.ensure_display_defaults(character)

    # Bare `ooc` -- replay the global ring buffer instead of usage nag.
    if not args or not args.strip():
        history = getattr(game, "ooc_history", None) or ()
        role = display_prefs.channel_role(character, "ooc", default="ooc")
        if not history:
            character.session.send(
                "No recent OOC. Type 'ooc <message>' to speak."
            )
            return
        character.session.send("Recent OOC (last 20):")
        for plain in history:
            character.session.send(style.paint_for(character, role, plain))
        character.session.send("")
        return

    message = args.strip()
    # Plain-text ((OOC)) carries meaning without color (a11y).
    # Feature D: account pref ooc_identity may show account display name.
    face = _ooc_speaker_face(character, game)
    plain = f"((OOC)) [{face}]: {message}"
    # Record before broadcast so the speaker's later bare `ooc` includes
    # this line even if delivery somehow skips their own Session.
    history = getattr(game, "ooc_history", None)
    if history is not None:
        history.append(plain)
    delivered = False
    from engine import gmcp
    for session in list(game.sessions):
        other = getattr(session, "character", None)
        if other is None:
            continue
        display_prefs.ensure_display_defaults(other)
        role = display_prefs.channel_role(other, "ooc", default="ooc")
        session.send(style.paint_for(other, role, plain))
        session.send("")
        gmcp.push_comm(session, "ooc", message, face)
        delivered = True
    if not delivered:
        role = display_prefs.channel_role(character, "ooc", default="ooc")
        character.session.send(style.paint_for(character, role, plain))
        character.session.send("")
        gmcp.push_comm(character.session, "ooc", message, face)
    # Optional Discord #ooc mirror (env-gated; silent if unset). Bare
    # `ooc` history replay above returns early -- only live sends mirror.
    try:
        from engine import discord_bridge

        discord_bridge.schedule_ooc(plain)
    except Exception as exc:
        print(f"[discord_bridge] ooc schedule skipped: {exc}", flush=True)


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
    """brief [on|off] -- skip room prose after moves (classic MUD brief).

    Bare ``brief`` toggles. Same pref as ``config brief``. Explicit
    ``look`` always shows the full description; only auto-look after a
    walk skips prose when brief is on.
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
            "Brief on -- room descriptions skip after you move; "
            "type look for the full prose."
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

    Bare ``config`` lists every setting in one submenu. ``config <key> …``
    mutates that setting (or forwards to the matching short verb).

    Usage::
        config
        config width <40-120>
        config screenreader on|off
        config map on|off
        config mapmove on|off
        config brief on|off
        config color on|off|16|256
        config combatgag on|off
        config combattags on|off
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
            "\r\n".join(_config_status_lines(character))
        )
        return
    parts = raw.split(None, 2)
    key = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if len(parts) > 2:
        rest = parts[1] + " " + parts[2]

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
            character.session.send("ASCII minimap enabled.")
        elif choice in ("off", "no", "false", "0"):
            character.show_minimap = False
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
            if not character.show_minimap:
                character.show_minimap = True
                character.session.send(
                    "Map-on-move enabled (also turned config map on)."
                )
            else:
                character.session.send(
                    "Map-on-move enabled -- local map prints after each move."
                )
        elif choice in ("off", "no", "false", "0"):
            character.map_on_move = False
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
                    "Drive map: full atlas (also turned config map on)."
                )
            else:
                character.session.send(
                    "Drive map: full atlas -- map big redraws each cruise step."
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
                    "Map view: full atlas (also turned config map on)."
                )
            else:
                character.session.send(
                    "Map view: full atlas -- bare 'map' now shows the full "
                    "grid (type 'map small' for the local window)."
                )
        elif choice in (
            "minimap", "mini", "local", "small", "off", "no", "false", "0",
        ):
            character.map_view_full = False
            character.session.send(
                "Map view: minimap -- bare 'map' shows the local window "
                "again (type 'map big' for the full grid)."
            )
        else:
            character.session.send(
                "Usage: config mapview atlas|minimap"
            )
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
            if not character.show_minimap:
                character.show_minimap = True
                character.session.send(
                    "Map-on-look enabled (also turned config map on)."
                )
            else:
                character.session.send(
                    "Map-on-look enabled -- local map embeds in look."
                )
        elif choice in ("off", "no", "false", "0"):
            character.map_on_look = False
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
                "Brief on -- room descriptions skip after you move; "
                "type look for the full prose."
            )
        elif choice in ("off", "no", "false", "0"):
            character.brief = False
            character.session.send(
                "Brief off -- full room descriptions after each move."
            )
        else:
            character.session.send("Usage: config brief on|off")
        return
    if key in ("exits", "exitstyle", "exits_verbose"):
        if not rest:
            state = "verbose" if character.exits_verbose else "compact"
            character.session.send(
                f"Exits style is {state}. "
                "Usage: config exits compact|verbose"
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("compact", "short", "abbrev", "off", "0"):
            character.exits_verbose = False
            character.look_exits_rev = 1
            character.session.send(
                "Exits style: compact (Exits: n, e, s)."
            )
        elif choice in ("verbose", "long", "full", "on", "1"):
            character.exits_verbose = True
            character.look_exits_rev = 1
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
                f"Combat tags are {state} "
                "(screenreader always shows them). "
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
            if character.screenreader:
                character.session.send(
                    "Combat tags pref off, but screenreader mode still "
                    "shows tags (a11y)."
                )
            else:
                character.session.send(
                    "Combat tags off -- cinematic combat without "
                    "[DMG]/[HIT] prefixes."
                )
        else:
            character.session.send("Usage: config combattags on|off")
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
        account = accounts_mod.account_for_character(game, character)
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
            character.session.send(
                f"OOC will show your account name "
                f"({account.display_name})."
            )
            try:
                game.save()
            except Exception:
                pass
        elif choice in ("character", "char", "c", "name"):
            account.ooc_identity = accounts_mod.OOC_IDENTITY_CHARACTER
            character.session.send(
                "OOC will show your character name."
            )
            try:
                game.save()
            except Exception:
                pass
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
        account = accounts_mod.account_for_character(game, character)
        if account is None:
            body = getattr(character, "gm_mode_body", None)
            if body is not None:
                account = accounts_mod.account_for_character(game, body)
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
                "(GM form shows Character(Account))."
            )
            return
        choice = rest.split(None, 1)[0].lower()
        if choice in ("on", "yes", "true", "1"):
            account.gm_see_accounts = True
            character.session.send(
                "See-accounts on -- names show as Character(Account) "
                "while you are in GM form."
            )
            try:
                game.save()
            except Exception:
                pass
        elif choice in ("off", "no", "false", "0"):
            account.gm_see_accounts = False
            character.session.send("See-accounts off -- plain names.")
            try:
                game.save()
            except Exception:
                pass
        else:
            character.session.send("Usage: config seeaccounts on|off")
        return
    if key == "channel":
        # config channel ooc <role>
        bits = rest.split(None, 1)
        if not bits:
            character.session.send(
                "Usage: config channel ooc <role>  "
                "(roles: muted, ooc, alert, teal, gold, …)"
            )
            return
        sub = bits[0].lower()
        if sub != "ooc":
            character.session.send(
                "Only channel 'ooc' is configurable today."
            )
            return
        if len(bits) < 2:
            cur = character.channel_colors.get("ooc", "ooc")
            character.session.send(
                f"OOC channel role is {cur}. "
                "Usage: config channel ooc <role>"
            )
            return
        role = bits[1].strip().lower()
        if role not in style.COLORS and role not in style.COLORS_XTERM256:
            character.session.send(
                f"Unknown role '{role}'. Try muted, ooc, alert, teal."
            )
            return
        character.channel_colors["ooc"] = role
        character.session.send(f"OOC channel color role set to {role}.")
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
        "combatdiag": "combatdiag",
        "fightlog": "fightlog",
        "autoidle": "autoidle",
        "idlemode": "idlemode",
        "idle": "idle",
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
        "Usage: config [<setting> …]. Type bare 'config' for the full "
        "list. See 'help config'."
    )


def _config_status_lines(character):
    """Categorized bare-config submenu (all client/display prefs).

    Screenreader mode skips decorative blank separators' noise by keeping
    short labeled sections with terminal periods where helpful.
    """
    from engine import display_prefs
    display_prefs.ensure_display_defaults(character)
    ch = character.channel_colors.get("ooc", "ooc")
    alias_n = len(character.command_aliases or {})
    clock = (
        "12h" if getattr(character, "time_format", "24h") == "12h" else "24h"
    )
    tags_state = "on" if character.show_combat_tags else "off"
    if character.screenreader:
        tags_note = f"{tags_state} (forced on while screenreader)"
    else:
        tags_note = tags_state
    lines = [
        "Config -- client and display preferences.",
        "Type config <setting> … to change. Short verbs still work.",
        "",
        "Display:",
        f"  color: {'on' if character.use_color else 'off'} "
        f"(depth {character.color_depth})  -- config color on|off|16|256",
        f"  width: {character.display_width}  -- config width <40-120>",
        f"  pager: {getattr(character, 'pager_lines', 20)}  "
        "-- config pager <5-100> (lines per more page)",
        f"  screenreader: "
        f"{'on' if character.screenreader else 'off'}  "
        "-- config screenreader on|off",
        f"  map: {'on' if character.show_minimap else 'off'}  "
        "-- config map on|off (bare map command)",
        f"  maplook: {'on' if character.map_on_look else 'off'}  "
        "-- config maplook on|off (embed map in look)",
        f"  brief: {'on' if getattr(character, 'brief', False) else 'off'}  "
        "-- config brief on|off (skip prose after move; look for full)",
        f"  mapmove: {'on' if character.map_on_move else 'off'}  "
        "-- config mapmove on|off (map after each move)",
        f"  drivemap: "
        f"{'atlas' if getattr(character, 'drive_map_full', True) else 'minimap'}  "
        "-- config drivemap atlas|minimap (cruise redraw)",
        f"  mapview: "
        f"{'atlas' if getattr(character, 'map_view_full', False) else 'minimap'}  "
        "-- config mapview atlas|minimap (bare map default)",
        f"  exits: "
        f"{'verbose' if character.exits_verbose else 'compact'}  "
        "-- config exits compact|verbose",
        f"  timeformat: {clock}  -- config timeformat 12|24",
        "",
        "Combat / chat:",
        f"  combatgag: "
        f"{'on' if character.combat_gag_other else 'off'}  "
        "-- config combatgag on|off",
        f"  combattags: {tags_note}  -- config combattags on|off",
        f"  tips: {'on' if character.show_tips else 'off'}  "
        "-- config tips on|off ([TIP] hints every 5-15 min)",
        f"  combatnumbers: "
        f"{'on' if getattr(character, 'combat_numbers', False) else 'off'}  "
        "-- config combatnumbers on|off",
        f"  combatdiag: "
        f"{'on' if getattr(character, 'combat_diag', False) else 'off'}  "
        "-- config combatdiag on|off",
        f"  fightlog: "
        f"{'on' if getattr(character, 'fightlog_enabled', False) else 'off'}  "
        "-- config fightlog on|off (cinematic replay after fights)",
        f"  channel ooc: {ch}  -- config channel ooc <role>",
        "",
        "Account / OOC:",
        "  oocname: account|character  -- config oocname … "
        "(requires a linked account)",
        "  seeaccounts: on|off  -- config seeaccounts … "
        "(staff GM form only)",
        "",
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
        "",
        "Macros / prompt:",
        f"  aliases: {alias_n} set  -- config alias …",
        f"  prompt: {character.prompt_format!r}  -- config prompt …",
        "",
        "See also: help formatting | help config | help alias | "
        "help prompt | help account",
    ]
    # Fill live oocname / seeaccounts values when linked.
    try:
        from engine import accounts as accounts_mod
        game = getattr(getattr(character, "session", None), "game", None)
        account = accounts_mod.account_for_character(game, character)
        if account is None:
            body = getattr(character, "gm_mode_body", None)
            if body is not None:
                account = accounts_mod.account_for_character(game, body)
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
    account = accounts_mod.account_for_character(game, body_for_acct)

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
        try:
            from engine import reports as reports_mod
            bug_counts = {}
            for entry in reports_mod.recent(
                reports_mod.BUG, None, directory=game.report_dir
            ):
                if entry.get("status") != "resolved":
                    continue
                key = (entry.get("reporter") or "").strip()
                if key:
                    bug_counts[key] = bug_counts.get(key, 0) + 1
            suggest_counts = {}
            for entry in reports_mod.recent(
                reports_mod.SUGGEST, None, directory=game.report_dir
            ):
                if entry.get("status") != "resolved":
                    continue
                key = (entry.get("reporter") or "").strip()
                if key:
                    suggest_counts[key] = suggest_counts.get(key, 0) + 1
            bugs = 0
            suggests = 0
            for key in list(account.character_keys):
                k = (key or "").strip()
                bugs += int(bug_counts.get(k, 0))
                suggests += int(suggest_counts.get(k, 0))
            account.bugs_squashed = bugs
            account.features_suggested = suggests
        except Exception:
            pass
        faces = []
        for key in account.character_keys:
            finder = getattr(game, "find_login_character", None)
            body = finder(key) if callable(finder) else None
            if body is None:
                body = game.find_character(key)
            if body is not None:
                faces.append(_presence_face(body))
            else:
                faces.append(key)
        lines = [
            f"Account: {account.display_name}",
            f"  Characters: {', '.join(faces) or '(none)'}",
            f"  OOC name: {account.ooc_identity} "
            f"(config oocname account|character)",
            f"  Bugs squashed: {int(account.bugs_squashed or 0)} "
            "(account lifetime)",
            f"  Ideas shipped: "
            f"{int(account.features_suggested or 0)} "
            "(account lifetime)",
        ]
        if account.gm_rank in ("gm", "head_gm"):
            lines.append(f"  Staff rank: {account.gm_rank}")
            cast_n = len(accounts_mod.list_immersion_cast(game))
            lines.append(
                f"  Cast roster: {cast_n} immersion cast "
                "(account login menu + gm off <name>)"
            )
            state = "on" if account.gm_see_accounts else "off"
            lines.append(
                f"  See-accounts: {state} "
                "(config seeaccounts on|off)"
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
        try:
            game.save()
        except Exception:
            pass
        character.session.send(
            f"Account '{new_acct.display_name}' created and linked to "
            f"{_presence_face(link_body)}. "
            "Type 'account' to review."
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
        try:
            game.save()
        except Exception:
            pass
        character.session.send(
            f"Linked {_presence_face(link_body)} to account "
            f"'{existing.display_name}'. "
            "Type 'account' to review."
        )
        return

    if sub in ("oocname", "ooc"):
        return cmd_config(character, f"oocname {rest}".strip(), game)

    character.session.send(
        "Usage: account | account create <name> <password> | "
        "account link <name> <password> | account oocname …  "
        "See 'help account'."
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
        character.prompt_format = display_prefs.DEFAULT_PROMPT
        character.session.send(
            f"Prompt reset to default: {display_prefs.DEFAULT_PROMPT}"
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


def cmd_time(character, args, game):
    """Bare-engine clock: calendar only, no eclipse/World-Tide flavor.

    This is the LEAN stub (two-repo purity Phase 2 -- see this module's
    docstring). The full version with the eclipse ambient line and the
    World Tide "lean" phrase appended moved to
    `supers/verbs/engine_flavor.py`'s `cmd_time`, which SUPERS_COMMANDS
    overrides this stub with whenever SUPERS is installed.
    """
    from engine import game_calendar
    cal = game.calendar()
    clock = game_calendar.format_clock(cal, fmt=character.time_format)
    character.session.send(
        f"It is {clock} ({cal['day_period']}) in {cal['season']} "
        f"on {cal['weekday_name']}, {cal['month_name']} "
        f"{cal['day_of_month']}, {cal['year']}. "
        "(Time moves 3x real speed here -- roughly 8 real hours per game-day.)"
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
    cal = game.calendar()
    character.session.send(
        game_calendar.format_date(cal)
        + " (Time moves 3x real speed here -- roughly 8 real hours per game-day.)"
    )


# Undated Unreleased bullets keep a sentinel date for display only.
# Player-facing sort is date descending, then hidden ``#N`` id, then
# file_index -- dates must read newest-first even when parallel PRs
# reused or raced the same ``#N`` (id-only sort made the list look
# scrambled). Same-day ties still use ``#N`` so merges stay stable.
_CHANGELOG_UNDATED = "0001-01-01"

# Hidden change id at the start of a bold lead-in: ``#042 2026-07-16 — …``.
# Zero-padding is optional (``#42`` and ``#042`` both parse). Players never
# see this id -- it only stabilizes sort across GitHub merges.
_CHANGELOG_ID_PREFIX_RE = re.compile(
    r"^#(\d+)\s+(.*)$",
    re.DOTALL,
)

# Leading date inside a bold lead-in: ``2026-07-16 — Summary`` or
# ``2026-07-16 -- Summary``. Em dash, en dash, ASCII ``--``, or a lone
# hyphen after the date are all accepted so merge conflict punctuation
# does not drop the stamp.
_CHANGELOG_DATE_PREFIX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})"
    r"(?:\s*[—–]\s*|\s+--\s+|\s+-\s+)"
    r"(.*)$",
    re.DOTALL,
)


def _strip_changelog_date_prefix(text):
    """Pull a leading ``YYYY-MM-DD —`` stamp off *text*.

    Returns ``(date_or_None, remainder)``. Used for both the short summary
    and the first ``full`` line so ``changes detail`` does not print the
    date twice (once from the parsed field, once from the markdown body).
    """
    match = _CHANGELOG_DATE_PREFIX_RE.match(text or "")
    if not match:
        return None, text or ""
    return match.group(1), match.group(2)


def _strip_changelog_stamps(text):
    """Pull a leading ``#N`` id and optional ``YYYY-MM-DD —`` off *text*.

    Returns ``(id_int, date_or_None, remainder)``. ``id_int`` is ``0`` when
    the bullet has no hidden id (sorts to the bottom). The id is never
    shown to players -- only used as the stable sort key.
    """
    remainder = text or ""
    change_id = 0
    id_match = _CHANGELOG_ID_PREFIX_RE.match(remainder)
    if id_match:
        # int() drops leading zeros so ``#042`` and ``#42`` compare equal.
        change_id = int(id_match.group(1))
        remainder = id_match.group(2)
    date, remainder = _strip_changelog_date_prefix(remainder)
    return change_id, date, remainder


def _changelog_sort_key(entry):
    """Sort key for ``changes``: newest date, then highest ``#N``, then file.

    Returns a tuple suitable for ``sort(..., reverse=True)``. Undated
    entries use ``_CHANGELOG_UNDATED`` (``0001-01-01``) so they sink.
    Missing ids use ``0`` so they lose same-day ties to stamped bullets.
    """
    date = entry.get("date") or _CHANGELOG_UNDATED
    change_id = entry.get("id") or 0
    file_index = entry.get("file_index") or 0
    return (date, change_id, file_index)


def _parse_unreleased_entries(lines, *, fragment=False, file_index_start=0):
    """Parse Unreleased bullets from CHANGELOG.md or a CHANGELOG.d fragment.

    Each entry is a dict::

        {"category", "id", "date", "summary", "full", "file_index"}

    ``id`` is the hidden monotonic ``#N`` stamp (``0`` if missing).
    ``date`` is ``YYYY-MM-DD`` for player display (or ``_CHANGELOG_UNDATED``).

    When ``fragment`` is True, the whole file is treated as Unreleased body
    (no ``## [Unreleased]`` heading required) so parallel PR fragment files
    can hold one bullet each without editing CHANGELOG.md.

    Entries are sorted newest date first, then ``id`` descending. The
    date-primary key keeps the in-game list reading chronologically even
    when parallel PRs raced or reused ``#N``; same-day ties still use
    ``#N`` so Keep-a-Changelog reshuffles do not renumber ``[n]``.
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
            change_id, date, summary = _strip_changelog_stamps(lead)
            current = {
                "category": category,
                "id": change_id,
                "date": date or _CHANGELOG_UNDATED,
                "summary": summary,
                "full": [bullet],
                "file_index": file_index,
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

    # Newest date first; highest id wins same-day ties; id 0 / undated sink.
    entries.sort(key=_changelog_sort_key, reverse=True)
    return entries


def _changelog_repo_root():
    """Repo root from this module (engine/verbs/basic.py -> three hops)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _load_unreleased_entries(repo_root=None):
    """Load Unreleased entries from CHANGELOG.md plus CHANGELOG.d/*.md.

    Legacy bullets stay in CHANGELOG.md. New ships add a unique fragment
    under CHANGELOG.d/ so parallel PRs never conflict on Unreleased lines.
    """
    root = repo_root or _changelog_repo_root()
    entries = []
    file_index = 0
    main_path = os.path.join(root, "CHANGELOG.md")
    try:
        with open(main_path, "r", encoding="utf-8") as f:
            main_entries = _parse_unreleased_entries(
                f.readlines(), file_index_start=file_index,
            )
        entries.extend(main_entries)
        file_index += len(main_entries)
    except OSError:
        pass

    frag_dir = os.path.join(root, "CHANGELOG.d")
    if os.path.isdir(frag_dir):
        # Stable order for file_index tie-breaks; sort key still id-desc.
        names = sorted(
            n for n in os.listdir(frag_dir)
            if n.endswith(".md") and n.lower() != "readme.md"
        )
        for name in names:
            path = os.path.join(frag_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    frag_entries = _parse_unreleased_entries(
                        f.readlines(),
                        fragment=True,
                        file_index_start=file_index,
                    )
            except OSError:
                continue
            entries.extend(frag_entries)
            file_index += len(frag_entries)

    entries.sort(key=_changelog_sort_key, reverse=True)
    return entries


def _changelog_detail_body(entry):
    """Full text for ``changes detail``, with date shown once (no ``#N``).

    The markdown ``full`` lines still contain the stamped bold lead-in
    (``#N YYYY-MM-DD — …``); strip both stamps from the first line, then
    prepend only the parsed date so players never see the hidden id.
    """
    parts = list(entry.get("full") or [])
    if parts:
        # First line may be ``**#042 2026-07-16 — Summary.** rest`` or a
        # multi-line bold that only has the opening ``**`` on this line.
        first = parts[0]
        bold = re.match(r"\*\*(.+?)\*\*(.*)$", first, re.DOTALL)
        if bold:
            _id, _date, rest_lead = _strip_changelog_stamps(bold.group(1))
            rebuilt = rest_lead + bold.group(2)
            parts[0] = rebuilt.strip() or rest_lead
        elif first.startswith("**"):
            _id, _date, remainder = _strip_changelog_stamps(first[2:])
            parts[0] = remainder
        else:
            _id, _date, remainder = _strip_changelog_stamps(first)
            parts[0] = remainder
    body = " ".join(parts).strip()
    date = entry.get("date") or _CHANGELOG_UNDATED
    if date == _CHANGELOG_UNDATED:
        return body
    return f"{date} {body}".strip()


def cmd_changes(character, args, game):
    """A live player suggestion (suggestions.log, 2026-07-12): "the changelog
    should feed into an in-game 'changes' command like traditional MUDs,"
    instead of players having to go read CHANGELOG.md by hand.

    Reads CHANGELOG.md and CHANGELOG.d/*.md fresh on every call -- no
    caching. This is a rare, non-performance-critical command (nothing
    here runs on the tick loop), and the files can change between server
    restarts anyway, so there's nothing worth caching.

    Shows each top-level '- **...**' BULLET under '## [Unreleased]' (plus
    fragment files under CHANGELOG.d/), tagged
    with its '### ' category (Fixed/Added/Changed/...), a ``YYYY-MM-DD``
    stamp, and a stable [n] number, most recent first. A live-
    reported bug (bug_reports.log #7): this used to show the '### '
    subsection HEADINGS themselves instead of the bullets underneath them
    -- since a category is repeated for every batch of related fixes
    (e.g. "Fixed", "Fixed (v0.21 live-feedback pass)"), that read as a
    wall of bare "- Fixed"/"- Changed" lines with no actual description
    of what changed.

    Each bullet carries a hidden monotonic ``#N`` id inside the bold
    lead-in (``**#042 2026-07-16 — Summary.**``). Listing sorts by
    date descending, then ``#N`` (not Keep-a-Changelog section / file
    order alone) so players see newest calendar days first even when
    parallel PRs raced the same id, while same-day ties stay stable.
    Players only see the date, never ``#N``.

    New Unreleased ships should add ``CHANGELOG.d/<slug>.md`` (not edit
    the top of CHANGELOG.md) so parallel PRs do not conflict.

    Suggestion #73: bullets whose bold summary starts with ``[ops]`` are
    GM-only (deploy helpers, SSH paths, host ops). Players never see them;
    numbering for players indexes the filtered visible list. The ``[ops]``
    tag sits *after* the date separator so filtering still matches.

    Usage:
      changes [n]        -- the n most recent one-line summaries (n=10 default)
      changes detail <n> -- the FULL text of entry [n] (every wrapped line a
                             one-line summary drops), by the same numbering.
                             A live suggestion (suggestions.log #25): "changes
                             should have a number... but when you type changes
                             1 it shows the full info on change #1." <n> here
                             always indexes the complete Unreleased list, not
                             just whatever a plain 'changes n' happened to cap
                             the short listing at.
    """
    usage = "Usage: changes [n] | changes detail <n>"
    raw = args.strip()

    entries = _load_unreleased_entries()
    if not entries and not os.path.isfile(
        os.path.join(_changelog_repo_root(), "CHANGELOG.md")
    ):
        character.session.send("No changelog available right now.")
        return

    # Suggestion #73: hide [ops] bullets from non-GM players.
    # Filter after parse/sort so [ops] never shifts a player's [n] for a
    # non-ops entry when a GM-only bullet sits between two player ones.
    if not _is_gm(character):
        entries = [
            e for e in entries
            if not str(e.get("summary") or "").lstrip().startswith("[ops]")
        ]

    if not entries:
        character.session.send("Nothing unreleased right now -- all caught up.")
        return

    if raw.lower().startswith("detail"):
        rest = raw[len("detail"):].strip()
        try:
            idx = int(rest)
        except ValueError:
            character.session.send("Usage: changes detail <n>")
            return
        if idx < 1 or idx > len(entries):
            character.session.send(f"No change #{idx} (there are {len(entries)}).")
            return
        entry = entries[idx - 1]
        body = _changelog_detail_body(entry)
        character.session.send(f"[{idx}] [{entry['category']}] {body}")
        return

    n = 10
    if raw:
        try:
            n = int(raw)
        except ValueError:
            character.session.send(usage)
            return
        if n <= 0:
            character.session.send(f"{usage}  (n must be a positive number)")
            return

    lines_out = ["Recent changes (most recent first):"]
    for i, entry in enumerate(entries[:n], start=1):
        date = entry.get("date") or _CHANGELOG_UNDATED
        if date == _CHANGELOG_UNDATED:
            # Should not happen after the Unreleased backfill; still render
            # without a fake year so undated outliers are obvious.
            lines_out.append(
                f"  [{i}] [{entry['category']}] {entry['summary']}"
            )
        else:
            lines_out.append(
                f"  [{i}] [{entry['category']}] {date} {entry['summary']}"
            )
    lines_out.append("('changes detail <n>' shows an entry's full text.)")
    character.session.send("\n".join(lines_out))


def _format_help_db_entry(character, entry):
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
    body_lines = []
    if entry["syntax_block"]:
        body_lines.append("Syntax:")
        body_lines.extend(entry["syntax_block"].split("\n"))
        body_lines.append("")
    body_lines.extend(entry["body_text"].split("\n"))
    title = entry["primary_keyword"]
    if entry["is_ic"]:
        title = f"{title} [IC]"
    return style.format_tome(
        title, body_lines,
        width=display_prefs.sheet_width(character),
        screenreader=bool(getattr(character, "screenreader", False)),
    )


def cmd_help(character, args, game):
    """System help: bare 'help' lists categorized HELP_TOPICS; 'help <name>'
    shows a multi-line topic page, or falls back to a command's one-liner
    from COMMANDS.

    Topic pages and the index use Blood & Velvet tome framing
    (docs/plans/colorandformattingforgame.R). This is deliberately separate
    from 'commands' (cmd_commands), which lists every verb.

    Lookup order (docs/plans/helpfile_editing_system.md): a hot-editable
    DB-overlay page (engine/help_db.py) wins over everything -- a GM can
    'hedit' a live typo fix or a brand-new page without a deploy -- then
    the static HELP_TOPICS page, then a DB full-text search hit, then a
    bare COMMANDS one-liner, and finally a DB fuzzy "did you mean" before
    giving up and logging the miss.
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
    # Frame budget for every help path (prefs #3) -- matches score / who.
    help_w = display_prefs.sheet_width(character)
    if verb:
        db = getattr(game, "db", None)
        is_gm_viewer = _is_gm(character)
        if db is not None:
            from engine import help_db
            db_entry = help_db.get_entry(db, verb, is_gm=is_gm_viewer)
            if db_entry:
                character.session.send(
                    "\r\n".join(_format_help_db_entry(character, db_entry))
                )
                from engine import hooks
                hooks.after_help_topic(character, verb, game)
                return
        # Prefer an extended topic page when one exists for this name
        # (covers both system topics like 'divine' and richer pages for
        # verbs like 'congregation' / 'miracle').
        topic = topics.get(verb)
        if topic:
            body = topic.strip("\n")
            related = None
            # Pull a trailing "See: ..." line into the RELATED footer when
            # present so the tome frame matches the plan's help layout.
            body_lines = body.split("\n")
            # Trailing See: / See also: becomes the RELATED footer.
            last = body_lines[-1].strip().lower() if body_lines else ""
            if last.startswith("see also:"):
                related = body_lines[-1].strip()[9:].strip()
                body_lines = body_lines[:-1]
            elif last.startswith("see:"):
                related = body_lines[-1].strip()[4:].strip()
                body_lines = body_lines[:-1]
                # Drop trailing blank lines left after peeling See:.
                while body_lines and not body_lines[-1].strip():
                    body_lines.pop()
            # First non-empty line is the topic's own title line -- use the
            # whole thing as the TOME header (keeps "Divine -- the faith
            # economy" searchable / readable) and skip it in the body.
            title = verb
            if body_lines and body_lines[0].strip():
                first = body_lines[0].strip()
                if " -- " in first or first.lower().startswith(verb):
                    title = first
                    body_lines = body_lines[1:]
                    while body_lines and not body_lines[0].strip():
                        body_lines.pop(0)
            framed = style.format_tome(
                title, body_lines, related=related,
                width=help_w,
                screenreader=bool(getattr(character, "screenreader", False)),
            )
            character.session.send("\r\n".join(framed))
            # Authored quests may gate on help <topic> (e.g. help haunts).
            from engine import hooks
            hooks.after_help_topic(character, verb, game)
            return
        # DB full-text search -- ahead of the bare COMMANDS one-liner, so a
        # rich DB page (once one exists) beats a one-line command blurb.
        if db is not None:
            from engine import help_db
            fts_entry = help_db.search_fts(db, verb, is_gm=is_gm_viewer)
            if fts_entry:
                character.session.send(
                    "\r\n".join(_format_help_db_entry(character, fts_entry))
                )
                from engine import hooks
                hooks.after_help_topic(
                    character, fts_entry["primary_keyword"], game
                )
                return
        entry = COMMANDS.get(verb)
        if not entry:
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
                suggestion = help_db.fuzzy_suggest(
                    db, verb, is_gm=is_gm_viewer,
                    extra_candidates=set(topics) | set(COMMANDS),
                )
            message = f"No such command or topic: '{verb}'. "
            if suggestion:
                message += f"Did you mean '{suggestion}'? "
            message += "Try 'help' for topics, or 'commands' for the verb list."
            character.session.send(message)
            return
        _, help_text = entry
        framed = style.format_tome(
            verb, [help_text], related="commands",
            width=help_w,
            screenreader=bool(getattr(character, "screenreader", False)),
        )
        character.session.send("\r\n".join(framed))
        return

    # Categorized topic index -- Blood & Velvet grimoire (not 'commands').
    lines = [""]
    lines.extend(style.format_help_index(
        categories,
        width=help_w,
        screenreader=bool(getattr(character, "screenreader", False)),
    ))
    character.session.send("\r\n".join(lines).rstrip("\n"))


def cmd_commands(character, args, game):
    """List every command with its one-line help_text from COMMANDS.

    Renders through ``style.format_commands_list`` (Blood & Velvet tome,
    same family as bare ``help``) so verb labels share a column and long
    one-liners wrap under the blurb instead of shoving the sheet off-center.

    GM-gated commands are grouped into a separate, clearly labeled
    "GM COMMANDS:" section shown ONLY to GMs (suggestions.log #40). The split
    keys off each command's help_text prefix -- every GM command's help_text
    begins with "GM:" or "head GM:" -- so a NEW GM command MUST keep that
    prefix for it to land in the GM section (and stay hidden from ordinary
    players). System topic pages live under bare 'help' (HELP_CATEGORIES /
    HELP_TOPICS), not in this listing -- keep the two indexes separate so
    neither crowds the other.
    """
    from engine import display_prefs, style
    # Local import -- see cmd_help's comment above (same circular-import
    # reason: COMMANDS is assembled in commands.py from this package).
    from commands import COMMANDS

    # Group aliases of the same handler onto one line (e.g. "attack/kill"
    # instead of two separate, identical-text lines) -- a dict keyed by the
    # handler function itself. Sort each alias group and the final listing
    # alphabetically so 'commands' is easy to scan (bug #25).
    grouped = {}
    for cmd_verb, (handler, _help_text) in COMMANDS.items():
        grouped.setdefault(handler, []).append(cmd_verb)

    # Two buckets: ordinary commands everyone sees, and GM commands (help_text
    # prefixed "GM:"/"head GM:") shown only to GMs. Each entry is a
    # (sort_key, verb_label, help_text) triple so we can alphabetize, then
    # hand (label, help_text) pairs to the formatter.
    normal_entries = []
    gm_entries = []
    for handler, verbs in grouped.items():
        verbs = sorted(verbs)
        help_text = COMMANDS[verbs[0]][1]
        entry = (verbs[0], "/".join(verbs), help_text)
        if help_text.startswith("GM:") or help_text.startswith("head GM:"):
            gm_entries.append(entry)
        else:
            normal_entries.append(entry)

    # Movement is dispatched specially (DIRECTIONS, below -- not COMMANDS,
    # see dispatch()), so it isn't in the loop above. Short label keeps the
    # verb column aligned; full names live in the blurb (comma-separated so
    # wrap never has to hard-split a slash run mid-name).
    normal_entries.append(
        # Sort key matches the short-form label so alphabetical scans
        # (and smoke) stay consistent with what players see.
        ("n",
         "n/s/e/w/ne/nw/se/sw/u/d",
         "walk that way if an exit exists (north, south, east, west, "
         "northeast, northwest, southeast, southwest, up, down)"),
    )
    normal_entries.sort(key=lambda triple: triple[0])
    gm_entries.sort(key=lambda triple: triple[0])

    normal_pairs = [(label, help_text) for _k, label, help_text in normal_entries]
    gm_pairs = [(label, help_text) for _k, label, help_text in gm_entries]
    # GM section only for GMs -- an ordinary player never sees GM verbs listed.
    framed = style.format_commands_list(
        normal_pairs,
        gm_entries=gm_pairs if _is_gm(character) and gm_pairs else None,
        width=display_prefs.sheet_width(character),
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    character.session.send("\r\n".join(framed))


def cmd_get(character, args, game):
    # Imported here (inside the function) rather than at the top of the file so
    # world.py and commands.py don't have to import each other in a loop.
    from world import Item
    if not args:
        character.session.send("Get what?")
        return

    room = character.location
    # `get <item> from <body>` -- loot nested belongings (#49).
    lower = args.lower()
    if " from " in lower:
        left, _, right = args.partition(" from ")
        items_here = [o for o in room.contents if isinstance(o, Item)]
        body = _find_item(right.strip(), items_here)
        if body is None or not getattr(body, "is_body", False):
            character.session.send("You don't see a body like that here.")
            return
        loot = getattr(body, "loot", None) or []
        taken = _find_item(left.strip(), loot)
        if taken is None:
            character.session.send(f"You don't find that in {body.key}.")
            return
        loot.remove(taken)
        character.inventory.append(taken)
        # hook -- SUPERS may auto-stow reagents into the gear bag.
        from engine import hooks
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

    # Only consider Items in the room (skip other characters).
    items_here = [o for o in room.contents if isinstance(o, Item)]

    # `get all` / `get *` / `get everything` -- scoop every pocketable item
    # on the floor. Bodies stay for `drag`; furniture stays put (beds, etc.).
    # Exact token only so a named item containing "all" still matches via
    # the single-item path below.
    if args.strip().lower() in ("all", "*", "everything"):
        from engine import hooks
        takeable = [
            o for o in items_here
            if not getattr(o, "is_body", False)
            and not getattr(o, "furniture", False)
        ]
        if not takeable:
            character.session.send("There's nothing here you can pick up.")
            return
        # Snapshot first: room.remove mutates contents while we iterate.
        names = []
        stow_msgs = []
        for item in list(takeable):
            room.remove(item)
            character.inventory.append(item)
            stow_msg = hooks.after_acquire_item(character, item)
            names.append(item.key)
            if stow_msg:
                stow_msgs.append(stow_msg)
        character.session.send("You pick up: " + ", ".join(names) + ".")
        for msg in stow_msgs:
            character.session.send(msg)
        room.broadcast(
            f"{_presence_face(character)} scoops up everything on the ground.",
            exclude=character,
        )
        return

    item = _find_item(args, items_here)
    if not item:
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

    # Move the item from the room's contents into your inventory (two steps).
    room.remove(item)
    character.inventory.append(item)
    # hook -- SUPERS may auto-stow reagents into the gear bag.
    from engine import hooks
    stow_msg = hooks.after_acquire_item(character, item)
    character.session.send(f"You pick up {item.key}.")
    if stow_msg:
        character.session.send(stow_msg)
    room.broadcast(
        f"{_presence_face(character)} picks up {item.key}.",
        exclude=character,
    )


def cmd_drop(character, args, game):
    if not args:
        character.session.send("Drop what?")
        return

    # A body heaved onto your shoulder (cmd_heave) isn't in your inventory --
    # it rides in the room with you via _carrying_body -- so handle it first:
    # "dropping" it just means sliding it off your shoulder (stop carrying).
    carried = getattr(character, "_carrying_body", None)
    if carried is not None and _find_item(args, [carried]) is carried:
        character._carrying_body = None
        character.session.send(f"You slide {carried.key} off your shoulder.")
        character.location.broadcast(
            f"{_presence_face(character)} slides {carried.key} off their shoulder.",
            exclude=character,
        )
        return

    # `drop all` / `drop *` / `drop everything` -- dump every carried item
    # onto the floor. Exact token only so a named item containing "all"
    # still matches via the single-item path below. Items that refuse
    # (loaners, etc.) stay in inventory and get a separate line.
    if args.strip().lower() in ("all", "*", "everything"):
        inv = list(getattr(character, "inventory", None) or [])
        if not inv:
            character.session.send("You aren't carrying anything.")
            return
        dropped = []
        refused = []
        for item in inv:
            refuse = item_drop_refusal(character, item)
            if refuse:
                refused.append((item.key, refuse))
                continue
            character.inventory.remove(item)
            character.location.add(item)
            dropped.append(item.key)
        if dropped:
            character.session.send("You drop: " + ", ".join(dropped) + ".")
            character.location.broadcast(
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
    item = _find_item(args, character.inventory)
    if not item:
        character.session.send("You aren't carrying that.")
        return

    # Case loaners (and other game-specific drop gates) stay until the game
    # says otherwise -- e.g. SUPERS refuses to drop a Calder case loaner
    # until reportcase / abandon (see supers/bootstrap.py set_item_drop_refusal).
    refuse = item_drop_refusal(character, item)
    if refuse:
        character.session.send(refuse)
        return

    # The reverse of get: out of inventory, into the room.
    character.inventory.remove(item)
    character.location.add(item)
    character.session.send(f"You drop {item.key}.")
    character.location.broadcast(
        f"{_presence_face(character)} drops {item.key}.", exclude=character
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

    Searches inventory first, then the room floor: a player might carry a
    box out of a dungeon before opening it, or just open it on the spot --
    either should work, same "check the obvious place first" order cmd_get
    uses for the room and cmd_drop uses for inventory.

    Opening CONSUMES the box (matches the "force it open" framing, and
    avoids leaving an inert "empty opened box" Item cluttering the world
    forever) and banks every loot entry: growth onto character.growth, and
    Divine relics into inventory (congregation-happiness items -- see
    supers.faith.DIVINE_RELICS).
    """
    from world import Item
    if not args:
        character.session.send("Open what?")
        return

    # Inventory first: only Items count; prefer a locked container when
    # several keys match (e.g. two "strongbox" Items in the same pile).
    item = _find_item_prefer_locked(
        args, [o for o in character.inventory if isinstance(o, Item)]
    )
    holder = character.inventory
    if not item:
        item = _find_item_prefer_locked(
            args, [o for o in character.location.contents if isinstance(o, Item)]
        )
        holder = character.location
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
    if not item.locked:
        character.session.send(f"{item.key} isn't locked.")
        return

    # Game hook: pit mimic strongboxes reveal and attack instead of paying
    # loot (supers/purgatory_dungeon/mimic.py when a game is installed).
    from engine import hooks
    if hooks.before_open_container(character, item, holder, game):
        return

    # holder is either a list (character.inventory) or a Room -- both
    # support .remove(obj) with the same signature, so no branch is needed.
    holder.remove(item)

    gains = []
    from engine import hooks
    for reward in item.loot:
        if reward.get("type") == "growth":
            amount = float(reward["amount"])
            character.growth = round(character.growth + amount, 2)
            hooks.after_growth_banked(character, amount, "lockbox")
            gains.append(f"{reward['amount']:g} banked growth")
        elif reward.get("type") == "relic":
            # hook -- None without a game installed; Phase 2 purity.
            relic = hooks.make_relic_item(reward.get("id"))
            if relic is not None:
                character.inventory.append(relic)
                hooks.after_acquire_item(character, relic)
                gains.append(f"{relic.key} (Divine relic)")
            else:
                gains.append("a cracked relic (useless)")
        elif reward.get("type") == "coins":
            # Town scrip (Character.coins) -- mission strongboxes and any
            # future locked container that pays cash instead of growth.
            amount = int(reward.get("amount", 0) or 0)
            character.coins = int(getattr(character, "coins", 0) or 0) + amount
            gains.append(f"{amount} scrip")
        elif reward.get("type") == "item":
            # Catalog id via hooks.make_world_item (SUPERS items catalog
            # when a game is installed; None / no-op without one).
            made = hooks.make_world_item({"item": reward.get("id")})
            if made is not None:
                character.inventory.append(made)
                hooks.after_acquire_item(character, made)
                gains.append(made.key)
            else:
                gains.append("a ruined kit scrap (useless)")

    if gains:
        character.session.send(
            f"You force open {item.key}, breaking the seal. "
            f"Inside: {', '.join(gains)}."
        )
    else:
        character.session.send(f"You force open {item.key}. It's empty.")
    character.location.broadcast(
        f"{_presence_face(character)} forces open {item.key}.",
        exclude=character,
    )
    # Game hook: mission hunts (and future systems) track container opens.
    hooks.after_open_container(character, item)


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


def cmd_setpass(character, args, game):
    """Set or change your character's password (see auth.py for the hashing).

    When a password already exists: ``setpass <current> <new>`` (everyone).
    When somehow blank: ``setpass <new>`` once. Mortals need min length only;
    GM / head_gm new passwords also need letter + digit + symbol.

    No "type it twice to confirm" step -- this telnet server doesn't mask
    input anyway (systems doc note: full telnet negotiation is out of scope
    for now), so a typo is just as visible to you as a confirmation would be.

    Strips client session tags (P1/Pn prefixes) the same way login does, so
    a mudlet/tintin paste cannot bake tags into the stored hash. Persists
    immediately when the Game exposes save().
    """
    from engine import auth
    from engine.connection import strip_client_session_tags
    from command_support import _is_gm

    raw = strip_client_session_tags(args or "").strip()
    for_gm = _is_gm(character)
    has_hash = bool(getattr(character, "password_hash", None))

    if has_hash:
        # Split once: new password may contain spaces.
        parts = raw.split(None, 1)
        if len(parts) < 2:
            character.session.send(
                "Usage: setpass <current password> <new password>"
            )
            return
        current, new_password = parts
        if not auth.verify_password(current, character.password_hash):
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

    character.password_hash = auth.hash_password(new_password)
    character.session.send("Password updated.")
    # Persist now so a crash before the next autosave cannot lose setpass.
    save = getattr(game, "save", None)
    if callable(save):
        save()


def cmd_quit(character, args, game):
    character.session.send("Goodbye.")
    character.session.close()   # flips the session's 'alive' flag; the input loop then ends


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
                or last_line in ("bug", "suggest"):
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
    """
    from engine import reports
    _file_or_capture_report(character, args, game, reports.BUG, "report")


def cmd_suggest(character, args, game):
    """Log a suggestion to suggestions.log -- same shape as cmd_bug, separate
    file so bug triage and feature ideas don't mix.
    """
    from engine import reports
    _file_or_capture_report(character, args, game, reports.SUGGEST, "suggestion")


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


def cmd_hedit(character, args, game):
    """GM: open the modal helpfile editor for <keyword> (docs/plans/
    helpfile_editing_system.md). Loads an existing DB-overlay page to
    revise; otherwise seeds a new draft from the static help_topics.py page
    of the same name if one exists (so hot-patching a typo doesn't mean
    retyping the whole page from scratch), or starts blank for a brand-new
    keyword. Overlay pages win over the static page at lookup time -- this
    is for hot-patching or drafting live, not for editing the git-tracked
    canon file itself, which the static page still is until /save.

    While editing, plain text appends a body line; '/list /i /d /r
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
    existing = help_db.get_entry(db, keyword) if db is not None else None
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
            "Type text to append, or /list /i /d /r /syntax /category "
            "/alias /gm /ic /preview /save /cancel."
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
            "/list /i /d /r /syntax /category /alias /gm /ic /preview."
        )
        return

    session.help_edit = {
        "keyword": keyword,
        "body": [],
        "syntax": [],
        "category": "",
        "aliases": [],
        "gm_only": False,
        "is_ic": False,
    }
    session.send(
        f"New overlay page '{keyword}'. Type text to append lines, "
        "then /save when ready (or /cancel to abort). "
        "See /list /i /d /r /syntax /category /alias /gm /ic /preview."
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
        lines.append(
            f"  [{label} #{entry_id}] ({status}) {time} {reporter}: "
            f"{description}"
        )
    return lines


def cmd_reports(character, args, game):
    """GM command: list bug, suggestion ("idea"), and help-proposal reports
    in three separate sections -- all bugs, then all ideas, then all help
    proposals -- instead of one time-interleaved list, so the kinds don't
    mix and match as they come in.

    Usage: reports [n] [all]
    - n defaults to 5 (the last n OPEN entries of each kind).
    - 'all' also includes resolved/rejected entries, so nothing is hidden
      once a GM wants the full picture.

    Non-GMs are rejected with nothing shown.
    """
    from engine import reports
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    usage = "Usage: reports [n] [all]"
    n = 5
    show_all = False
    for token in args.split():
        if token.lower() == "all":
            show_all = True
            continue
        try:
            n = int(token)
        except ValueError:
            character.session.send(usage)
            return
        if n <= 0:
            character.session.send(f"{usage}  (n must be a positive number)")
            return

    # Fetch EVERY entry (not just the last n) so filtering by status can't
    # hide an older open report behind a run of already-resolved recent
    # ones -- only THEN take the last n of what's left.
    all_bugs = reports.recent(reports.BUG, None, directory=game.report_dir)
    all_suggestions = reports.recent(
        reports.SUGGEST, None, directory=game.report_dir
    )
    all_help_ideas = reports.recent(
        reports.HELP, None, directory=game.report_dir
    )
    if not show_all:
        all_bugs = [e for e in all_bugs if e.get("status", "open") == "open"]
        all_suggestions = [
            e for e in all_suggestions if e.get("status", "open") == "open"
        ]
        all_help_ideas = [
            e for e in all_help_ideas if e.get("status", "open") == "open"
        ]
    bugs = all_bugs[-n:]
    suggestions = all_suggestions[-n:]
    help_ideas = all_help_ideas[-n:]

    if not bugs and not suggestions and not help_ideas:
        character.session.send(
            "No open reports."
            if not show_all
            else "No reports logged yet."
        )
        return

    scope = f"up to {n} of each kind"
    if show_all:
        scope += ", all statuses"
    from engine import style
    body = [style.paint("muted", f"({scope})")]
    body += _reports_section("Bugs:", "BUG", bugs, game=game)
    body.append(style.wrought_rule(48))
    body += _reports_section("Ideas:", "IDEA", suggestions, game=game)
    body.append(style.wrought_rule(48))
    body += _reports_section("Help ideas:", "HELP", help_ideas, game=game)
    lines = style.format_sheet(
        "REPORTS", body, width=52,
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    character.session.send("\r\n".join(lines))


def cmd_resolve(character, args, game):
    """GM command: resolve <bug|suggest|help> <id> [open|resolved|rejected]
    -- flip a logged report's status. Omit the status to mark resolved
    (``resolve bug 39``); pass ``rejected`` or ``open`` when triaging.
    <id> is the number shown by 'reports' (a report's line number within
    its own log file, stable across calls since mark() only ever rewrites a
    line in place). ``resolve help <id>`` closes out a helpsubmit proposal
    once its page has been written with 'hedit'. Non-GMs are rejected with
    nothing changed.
    """
    from engine import reports
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    usage = (
        "Usage: resolve <bug|suggest|help> <id> [open|resolved|rejected] "
        "(default: resolved)"
    )
    parts = args.split()
    if len(parts) == 2:
        kind_word, id_text = parts
        status = "resolved"
    elif len(parts) == 3:
        kind_word, id_text, status = parts
    else:
        character.session.send(usage)
        return
    kind = {
        "bug": reports.BUG, "suggest": reports.SUGGEST, "help": reports.HELP,
    }.get(kind_word.lower())
    if kind is None:
        character.session.send(usage)
        return
    try:
        entry_id = int(id_text)
    except ValueError:
        character.session.send(usage)
        return
    status = status.lower()
    if status not in reports.STATUSES:
        character.session.send(
            f"Status must be one of: {'/'.join(reports.STATUSES)}"
        )
        return

    try:
        reports.mark(kind, entry_id, status, directory=game.report_dir, game=game)
    except IndexError as exc:
        character.session.send(str(exc))
        return

    character.session.send(f"Marked {kind_word} #{entry_id} as {status}.")
    print(f"[GM] {character.key} marked {kind_word} #{entry_id} as {status}.")
