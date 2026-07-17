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
    _display_name,
    _find_character,
    _find_item,
    _find_item_prefer_locked,
    _is_gm,
    _move_one,
    _pull_followers,
)
from engine.hooks import (
    get_help_categories,
    get_help_topics,
    upgrade_legacy_container,
)


def cmd_look(character, args, game):
    """Show the room (no args), look in a body (`look in <body>`), or look
    at one thing/person here (`look bob`).

    Bare look uses the Master Room Layout
    (docs/plans/colorandformattingforgame.R §1): framed title + area tag,
    indented description, then conditional Paths / Souls / Items sections
    (empty sections are omitted entirely). `look in <body>` lists nested
    belongings (suggestions.log #49). Otherwise same targeting as examine.
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
    if getattr(room, "zone_exit_to", None) is not None:
        extras.append(
            f"Exit: type 'exit' to return to {room.zone_exit_to.key}"
        )
    # Outdoor ambient sky (open-air rooms: overland + tagged town streets).
    # Spawns still key off wilderness; look flavor keys off outdoor.
    from engine import hooks
    if getattr(room, "outdoor", False):
        from engine import game_calendar
        # eclipse_ambient_line() is a hook (Phase 2 purity) that returns ""
        # when there's no game installed or no eclipse active right now --
        # fall back to the plain engine calendar ambience either way.
        eclipse_line = hooks.eclipse_ambient_line(game)
        if eclipse_line:
            extras.append(eclipse_line)
        else:
            extras.append(game_calendar.format_ambient(game.calendar()))

    # Per-room extras (planar influence note, etc.) -- any room, not only outdoor.
    for line in hooks.room_look_extras(room, game):
        if line:
            extras.append(line)

    # Paths: (direction, destination room key) -- columns in format_room.
    # Game may hide exits (e.g. closed Devil's Gates) via filter_look_exits.
    # D66: also hide secret directions until this character has searched.
    exits = [
        (direction, dest.look_title())
        for direction, dest in room.exits.items()
        if hooks.look_exit_visible(dest, game)
        and vision_mod.character_knows_exit(character, room, direction)
    ]

    # Items = floor loot; Souls = other characters (not you; spirits you
    # can't see are skipped -- section 6). Section label is Items (not
    # Relics) so it never collides with Divine/Path relic content.
    floor_items = [
        _display_name(o) for o in room.contents if isinstance(o, Item)
    ]
    souls = [
        _display_name(o) for o in room.contents
        if o is not character
        and isinstance(o, Character)
        and not getattr(o, "vessel_host_key", None)  # riding Mantle: inside host
        and not (o.spirit and not _can_see_spirit(character, o))
    ]

    lines = style.format_room(
        room.look_title(),
        room.description,
        area_tag=area_tag,
        exits=exits,
        souls=souls,
        items=floor_items,
        extras=extras or None,
        width=display_prefs.sheet_width(character),
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    character.session.send("\r\n".join(lines))
    # Soft fear nudge: weak player Vampires sense a co-located Slayer.
    # (hook -- no-op / None without a game installed; Phase 2 purity.)
    from engine import hooks
    fear = hooks.vampire_fear_message(character, room)
    if fear:
        character.session.send(fear)
    # Blank before the custom prompt comes from send_prompt (dispatch),
    # not here -- avoid double-spacing after look.
    # GMCP Room.Info -- also covers auto-look after move (_move_one).
    from engine import gmcp
    gmcp.push_room(character)


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
    """D29 overland ASCII minimap (suggestions.log #26): a local terrain
    window around the player. Grid cells only -- hand-authored rooms
    (Plaza, dungeons) get a clear refusal rather than a blank or crashed
    render. Letter glyphs are the primary signal; ANSI color is optional
    decoration (section 8 a11y -- never color alone).

    Prefs #18 / #30: ``config map off`` or screenreader mode skips ASCII.
    """
    from engine import display_prefs
    display_prefs.ensure_display_defaults(character)
    if getattr(character, "screenreader", False):
        character.session.send(
            "Map suppressed (screenreader mode). Use exits on look, "
            "or 'config screenreader off'."
        )
        return
    if not getattr(character, "show_minimap", True):
        character.session.send(
            "Map is off. Type 'config map on' to show the ASCII minimap."
        )
        return
    import maps as maps_mod
    room = character.location
    # Respect the player's color preference (#51) -- letter glyphs stay the
    # primary signal either way (section 8 a11y).
    rendered = maps_mod.render_minimap(
        game.rooms, room, use_color=getattr(character, "use_color", True)
    )
    if rendered is None:
        character.session.send(
            "No map here. (The overland map only works on wilderness "
            "grid cells -- try walking out to The Wastes.)"
        )
        return
    # render_minimap joins with \\n; convert to telnet \\r\\n for the wire.
    character.session.send(rendered.replace("\n", "\r\n"))


def _look_at(character, query):
    """Show one thing's description: self, carried/floor item, or person here.

    Shared by `look <target>` and `examine <target>` so both verbs surface
    chargen/setdesc text the same way. Returns True if something matched.
    """
    from world import Item

    # look me / look self / look myself -- classic MUD self-examine so you
    # can check your own setdesc / auto-built appearance without leaving
    # the room listing's "everyone but you" carve-out.
    if query.lower() in ("me", "self", "myself"):
        character.session.send(
            f"{_display_name(character)}\r\n{character.description}"
        )
        from engine import hooks
        for line in hooks.look_extra_lines(character, character):
            character.session.send(line)
        return True

    # Your own inventory first (you shouldn't have to drop something to
    # read its description), then what's on the floor, then people here.
    item = _find_item(query, character.inventory)
    if not item:
        items_here = [o for o in character.location.contents if isinstance(o, Item)]
        item = _find_item(query, items_here)
    if item:
        character.session.send(item.description)
        return True

    target = _find_character(query, character.location.characters())
    if target and target.spirit and not _can_see_spirit(character, target):
        # Section 6: same invisibility rule cmd_look's "Here:" line applies
        # -- an un-Attuned viewer can't examine what they can't perceive.
        target = None
    if target:
        # _display_name's echo tag ("Name (echo)" / "Name (echo, pushups)")
        # is normally just how a room LISTS someone; append it here too so
        # examining an Echo tells you it won't respond, same information
        # 'look' already surfaces for the room's "Here:" line.
        character.session.send(f"{_display_name(target)}\r\n{target.description}")
        from engine import hooks
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
    never imports SUPERS.
    """
    from world import Item
    from engine import hooks
    if not query:
        character.session.send("Look in what?")
        return
    items_here = [o for o in character.location.contents if isinstance(o, Item)]
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


def cmd_move(character, direction, game):
    # NOTE: this handler receives a `direction` instead of `args`, because
    # dispatch() calls it specially (see the bottom of the file).
    from engine import hooks
    if getattr(character, "asleep", False):
        character.session.send(
            "You're asleep -- type 'wake' before you can move."
        )
        return
    # Awake rest cancels when you walk. (hook -- no-op without a game.)
    hooks.cancel_rest(character)
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


def cmd_follow(character, args, game):
    """follow <name> to tag along whenever they move; bare 'follow' stops.

    Live-session convenience (world.Character.following/followers), never
    persisted -- see persistence.py. Cadence hunt AI uses the same bond
    helpers (start_following / stop_following) so Echo companions trail
    too. Breaks on disconnect via world.break_follows.
    """
    from engine.command_support import start_following, stop_following
    name = args.strip()
    if not name:
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
    from engine.command_support import stop_following
    stop_following(character)


def _stop_following(character, silent=False):
    """Compat wrapper -- prefer stop_following from command_support."""
    from engine.command_support import stop_following
    stop_following(character, silent=silent)


def _do_transition(character, dest, game, leave_text, arrive_text):
    """Shared leave/arrive/look/encounter for enter, exit, in, out."""
    from engine import hooks
    if getattr(character, "asleep", False):
        character.session.send(
            "You're asleep -- type 'wake' before you can move."
        )
        return False
    hooks.cancel_rest(character)
    room = character.location
    block_message = hooks.move_gate_block(character, room, dest, game)
    if block_message:
        character.session.send(block_message)
        return False
    room.broadcast(leave_text, exclude=character)
    character.move_to(dest)
    dest.broadcast(arrive_text, exclude=character)
    cmd_look(character, "", game)
    import world
    world.encounter_check(game, dest)
    return True


def cmd_enter(character, args, game):
    """Enter a pocket zone from an overland gateway: enter <zonename>.

    Zone links live on Room.zone_entries (not exits{}), so this is separate
    from cardinal moves and from nested indoor 'in'. Bare 'enter' lists
    what you can enter from here.
    """
    room = character.location
    entries = getattr(room, "zone_entries", None) or {}
    raw = (args or "").strip()
    if not entries:
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
    _do_transition(
        character, dest, game,
        f"{character.key} enters {dest.key}.",
        f"{character.key} arrives.",
    )


def cmd_exit_zone(character, args, game):
    """Leave a pocket zone back to its overland grid cell: exit.

    Uses Room.zone_exit_to (stamped on the hub and every room in that
    zone). Nested indoor returns still use 'out' / 'leave'.
    """
    room = character.location
    dest = getattr(room, "zone_exit_to", None)
    if dest is None:
        character.session.send(
            "There's no zone exit from here. "
            "(Indoor returns still use 'out'.)"
        )
        return
    _do_transition(
        character, dest, game,
        f"{character.key} exits to the overland.",
        f"{character.key} arrives.",
    )


def cmd_go_in(character, args, game):
    """Nested indoor enter via exits['in'] (gym annex, chapel sacristy, …).

    Separate from zone travel (`enter <zonename>`).
    """
    room = character.location
    dest = room.exits.get("in")
    if not dest:
        character.session.send("You can't go in from here.")
        return
    _do_transition(
        character, dest, game,
        f"{character.key} goes in.",
        f"{character.key} arrives.",
    )


def cmd_go_out(character, args, game):
    """Nested indoor leave via exits['out']. Separate from zone `exit`."""
    room = character.location
    dest = room.exits.get("out")
    if not dest:
        character.session.send("There's no way out from here.")
        return
    _do_transition(
        character, dest, game,
        f"{character.key} goes out.",
        f"{character.key} arrives.",
    )

def cmd_say(character, args, game):
    """Speak to the room. Prefs #24: trailing ? / ! pick asks / exclaims."""
    if not args:                       # nothing to say
        character.session.send("Say what?")
        return
    from engine import display_prefs
    you_verb, they_verb = display_prefs.say_speech_verb(args)
    # First-person line for the speaker; third-person for the room.
    character.session.send(f'You {you_verb}, "{args}"')
    # Trailing blank so the next tick / tip / chat does not glue on.
    character.session.send("")
    character.location.broadcast(
        f'{character.key} {they_verb}, "{args}"',
        exclude=character,
        blank_after=True,
    )
    # GMCP Comm.Channel -- parallel to prose, never instead of it.
    from engine import gmcp
    from world import Character as CharType
    gmcp.push_comm(character.session, "say", args, character.key)
    for obj in list(getattr(character.location, "contents", []) or []):
        if not isinstance(obj, CharType) or obj is character:
            continue
        other = getattr(obj, "session", None)
        if other is None:
            continue
        gmcp.push_comm(other, "say", args, character.key)


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

    target.session.send(f'{character.key} tells you, "{message}"')
    character.session.send(f'You tell {target.key}, "{message}"')
    from engine import gmcp
    gmcp.push_comm(character.session, "tell", message, character.key)
    gmcp.push_comm(target.session, "tell", message, character.key)


def cmd_ooc(character, args, game):
    """Global out-of-character chat to every connected Session.

    Usage:
      ooc <message>   speak on the global OOC channel
      ooc             show the last 20 OOC lines (server-wide ring buffer)

    Prefs #23 / #26: double-bracket prefix + unnatural muted/ooc color
    (or the player's channel_colors['ooc'] role). Same line for everyone::

        ((OOC)) [Name]: message text

    Offline Echoes have no Session and do not receive OOC. The history
    buffer lives on ``game.ooc_history`` (in-memory; clears on restart /
    copyover) -- it is not a persistent chat log.
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
    plain = f"((OOC)) [{character.key}]: {message}"
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
        gmcp.push_comm(session, "ooc", message, character.key)
        delivered = True
    if not delivered:
        role = display_prefs.channel_role(character, "ooc", default="ooc")
        character.session.send(style.paint_for(character, role, plain))
        character.session.send("")
        gmcp.push_comm(character.session, "ooc", message, character.key)


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
    online = [s.character for s in game.sessions if s.character]
    if not online:
        character.session.send("No one is online.")
        return
    names = ", ".join(sorted(c.key for c in online))
    character.session.send(f"Online ({len(online)}): {names}")


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
    """Show or set display preferences (prefs catalog / D65 companions).

    Usage::
        config
        config width <40-120>
        config screenreader on|off
        config map on|off
        config color 16|256
        config combatgag on|off
        config channel ooc <role>
    """
    from engine import display_prefs
    from engine import style
    display_prefs.ensure_display_defaults(character)
    raw = (args or "").strip()
    if not raw:
        ch = character.channel_colors.get("ooc", "ooc")
        character.session.send(
            "Config:\r\n"
            f"  color: {'on' if character.use_color else 'off'} "
            f"(depth {character.color_depth})\r\n"
            f"  width: {character.display_width} "
            f"(framed sheets only; prose unwraps)\r\n"
            f"  screenreader: "
            f"{'on' if character.screenreader else 'off'}\r\n"
            f"  map: {'on' if character.show_minimap else 'off'}\r\n"
            f"  combatgag: "
            f"{'on' if character.combat_gag_other else 'off'} "
            f"(hide others' room combat lines)\r\n"
            f"  channel ooc color role: {ch}\r\n"
            f"  prompt: {character.prompt_format!r}\r\n"
            "See 'help formatting' / 'help config'."
        )
        return
    parts = raw.split(None, 2)
    key = parts[0].lower()
    if key == "width":
        if len(parts) < 2:
            character.session.send(
                f"Width is {character.display_width}. "
                f"Usage: config width <{display_prefs.WIDTH_MIN}-"
                f"{display_prefs.WIDTH_MAX}>"
            )
            return
        try:
            w = int(parts[1])
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
    if key in ("screenreader", "screen", "a11y", "tts"):
        if len(parts) < 2:
            state = "on" if character.screenreader else "off"
            character.session.send(
                f"Screenreader is {state}. "
                "Usage: config screenreader on|off"
            )
            return
        choice = parts[1].lower()
        if choice in ("on", "yes", "true", "1"):
            character.screenreader = True
            character.session.send(
                "Screenreader mode on -- ASCII frames and minimaps "
                "flatten to lists."
            )
        elif choice in ("off", "no", "false", "0"):
            character.screenreader = False
            character.session.send("Screenreader mode off.")
        else:
            character.session.send("Usage: config screenreader on|off")
        return
    if key == "map":
        if len(parts) < 2:
            state = "on" if character.show_minimap else "off"
            character.session.send(
                f"Map is {state}. Usage: config map on|off"
            )
            return
        choice = parts[1].lower()
        if choice in ("on", "yes", "true", "1"):
            character.show_minimap = True
            character.session.send("ASCII minimap enabled.")
        elif choice in ("off", "no", "false", "0"):
            character.show_minimap = False
            character.session.send("ASCII minimap disabled.")
        else:
            character.session.send("Usage: config map on|off")
        return
    if key == "color":
        if len(parts) < 2:
            character.session.send(
                f"Color depth is {character.color_depth}. "
                "Usage: config color 16|256"
            )
            return
        choice = parts[1].lower()
        if choice in ("16", "ansi", "default"):
            character.color_depth = "ansi"
            character.session.send("Color depth: 16-color ANSI.")
        elif choice in ("256", "xterm", "xterm256"):
            character.color_depth = "xterm256"
            character.session.send(
                "Color depth: Xterm256 (falls back per-role to ANSI)."
            )
        else:
            character.session.send("Usage: config color 16|256")
        return
    if key in ("combatgag", "gag", "combat_gag"):
        if len(parts) < 2:
            state = "on" if character.combat_gag_other else "off"
            character.session.send(
                f"Combat gag (others) is {state}. "
                "Usage: config combatgag on|off"
            )
            return
        choice = parts[1].lower()
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
    if key == "channel":
        # config channel ooc <role>
        if len(parts) < 2:
            character.session.send(
                "Usage: config channel ooc <role>  "
                "(roles: muted, ooc, alert, teal, gold, …)"
            )
            return
        sub = parts[1].lower()
        if sub != "ooc":
            character.session.send(
                "Only channel 'ooc' is configurable today."
            )
            return
        if len(parts) < 3:
            cur = character.channel_colors.get("ooc", "ooc")
            character.session.send(
                f"OOC channel role is {cur}. "
                "Usage: config channel ooc <role>"
            )
            return
        role = parts[2].lower()
        if role not in style.COLORS and role not in style.COLORS_XTERM256:
            character.session.send(
                f"Unknown role '{role}'. Try muted, ooc, alert, teal."
            )
            return
        character.channel_colors["ooc"] = role
        character.session.send(f"OOC channel color role set to {role}.")
        return
    character.session.send(
        "Usage: config [width|screenreader|map|color|combatgag|channel] …"
        "  (see 'help config')"
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
# Sort order is driven by the hidden monotonic ``#N`` id (below), not
# by this date -- same-day merges used to reshuffle when date+file_index
# was the only key.
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


def _parse_unreleased_entries(lines):
    """Parse ``## [Unreleased]`` bullets from CHANGELOG.md line list.

    Each entry is a dict::

        {"category", "id", "date", "summary", "full", "file_index"}

    ``id`` is the hidden monotonic ``#N`` stamp (``0`` if missing).
    ``date`` is ``YYYY-MM-DD`` for player display (or ``_CHANGELOG_UNDATED``).

    Entries are sorted by ``id`` descending (highest = newest). That id
    travels with the bullet text, so GitHub merges that reshuffle
    Fixed/Changed/Added blocks no longer renumber player-facing ``[n]``.
    """
    entries = []
    in_unreleased = False
    category = ""
    current = None  # open bullet so indented continuation lines extend it
    file_index = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
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

    # Highest id first (newest). Missing ids (0) sink to the bottom so
    # unstamped leftovers never jump to [1].
    entries.sort(key=lambda e: e.get("id") or 0, reverse=True)
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

    Reads CHANGELOG.md fresh on every call -- no caching. This is a rare,
    non-performance-critical command (nothing here runs on the tick loop),
    and the file can change between server restarts anyway, so there's
    nothing worth caching.

    Shows each top-level '- **...**' BULLET under '## [Unreleased]', tagged
    with its '### ' category (Fixed/Added/Changed/...), a ``YYYY-MM-DD``
    stamp, and a stable [n] number, most recent first. A live-
    reported bug (bug_reports.log #7): this used to show the '### '
    subsection HEADINGS themselves instead of the bullets underneath them
    -- since a category is repeated for every batch of related fixes
    (e.g. "Fixed", "Fixed (v0.21 live-feedback pass)"), that read as a
    wall of bare "- Fixed"/"- Changed" lines with no actual description
    of what changed.

    Each bullet carries a hidden monotonic ``#N`` id inside the bold
    lead-in (``**#042 2026-07-16 — Summary.**``). Sorting by that id
    (not Keep-a-Changelog section / file order / same-day date ties)
    keeps in-game [n] numbers from jumping when GitHub merges reshuffle
    Fixed/Changed/Added blocks. Players only see the date, never ``#N``.

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

    # This module now lives two directories under the repo root
    # (engine/verbs/basic.py) instead of AT the root like the old
    # commands.py, so CHANGELOG.md needs one more dirname() hop to find.
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "CHANGELOG.md",
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        character.session.send("No changelog available right now.")
        return

    entries = _parse_unreleased_entries(lines)

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


def cmd_help(character, args, game):
    """System help: bare 'help' lists categorized HELP_TOPICS; 'help <name>'
    shows a multi-line topic page, or falls back to a command's one-liner
    from COMMANDS.

    Topic pages and the index use Blood & Velvet tome framing
    (docs/plans/colorandformattingforgame.R). This is deliberately separate
    from 'commands' (cmd_commands), which lists every verb.
    """
    from engine import style
    # Local import: COMMANDS is assembled in commands.py from this very
    # package plus supers.verbs -- importing it at module level here would
    # be circular (commands.py is what imports engine.verbs in the first
    # place). By the time a player can type 'help', commands.py has long
    # since finished loading.
    from commands import COMMANDS

    verb = args.strip().lower()
    topics = get_help_topics()
    categories = get_help_categories()
    if verb:
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
            framed = style.format_tome(title, body_lines, related=related)
            character.session.send("\r\n".join(framed))
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
            character.session.send(
                f"No such command or topic: '{verb}'. "
                "Try 'help' for topics, or 'commands' for the verb list."
            )
            return
        _, help_text = entry
        framed = style.format_tome(
            verb, [help_text], related="commands"
        )
        character.session.send("\r\n".join(framed))
        return

    # Categorized topic index -- Blood & Velvet grimoire (not 'commands').
    lines = [""]
    lines.extend(style.format_help_index(categories))
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
        character.session.send(f"You take {taken.key} from {body.key}.")
        # hook -- generic "<actor> takes <item> from <body>" fallback
        # wording without a game installed; Phase 2 purity.
        from engine import hooks
        room.broadcast(
            hooks.loot_room_line(character.key, body.key, taken),
            exclude=character,
        )
        return

    # Only consider Items in the room (skip other characters).
    items_here = [o for o in room.contents if isinstance(o, Item)]
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
    character.session.send(f"You pick up {item.key}.")
    room.broadcast(f"{character.key} picks up {item.key}.", exclude=character)


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
            f"{character.key} slides {carried.key} off their shoulder.",
            exclude=character,
        )
        return

    # This time we search YOUR inventory, not the room.
    item = _find_item(args, character.inventory)
    if not item:
        character.session.send("You aren't carrying that.")
        return

    # The reverse of get: out of inventory, into the room.
    character.inventory.remove(item)
    character.location.add(item)
    character.session.send(f"You drop {item.key}.")
    character.location.broadcast(
        f"{character.key} drops {item.key}.", exclude=character
    )


def cmd_inventory(character, args, game):
    if character.inventory:            # non-empty list is truthy
        # Build a comma-separated list of the item names you're holding.
        names = ", ".join(i.key for i in character.inventory)
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

    # holder is either a list (character.inventory) or a Room -- both
    # support .remove(obj) with the same signature, so no branch is needed.
    holder.remove(item)

    gains = []
    from engine import hooks
    for reward in item.loot:
        if reward.get("type") == "growth":
            character.growth = round(character.growth + reward["amount"], 2)
            gains.append(f"{reward['amount']:g} banked growth")
        elif reward.get("type") == "relic":
            # hook -- None without a game installed; Phase 2 purity.
            relic = hooks.make_relic_item(reward.get("id"))
            if relic is not None:
                character.inventory.append(relic)
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
        f"{character.key} forces open {item.key}.", exclude=character
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

    No "type it twice to confirm" step -- this telnet server doesn't mask
    input anyway (systems doc note: full telnet negotiation is out of scope
    for now), so a typo is just as visible to you as a confirmation would be.
    """
    from engine import auth
    if not args or len(args) < 4:
        character.session.send("Usage: setpass <new password> (at least 4 characters)")
        return
    character.password_hash = auth.hash_password(args)
    character.session.send("Password updated.")


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
    return entries


def _file_or_capture_report(character, args, game, kind, noun):
    """Shared body for cmd_bug/cmd_suggest. `<cmd> <description>` on one
    line files immediately (unchanged quick-usage behavior, unchanged
    confirmation wording -- "Thanks, your {noun} was logged."). A bare
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


def _reports_section(header, label, entries):
    """Build the lines for one 'reports' section (all bugs, or all ideas).

    entries is already the slice to display, oldest-first. Always emits the
    header, even for an empty section, so a GM can tell "nothing open" from
    "reports is broken."
    """
    lines = [header]
    if not entries:
        lines.append("  (none)")
        return lines
    for entry in entries:
        entry_id = entry.get("id", "?")
        status = entry.get("status", "open")
        time = entry.get("time", "?")
        reporter = entry.get("reporter", "?")
        description = entry.get("description", "")
        lines.append(
            f"  [{label} #{entry_id}] ({status}) {time} {reporter}: "
            f"{description}"
        )
    return lines


def cmd_reports(character, args, game):
    """GM command: list bug and suggestion ("idea") reports in two separate
    sections -- all bugs, then all ideas -- instead of one time-interleaved
    list, so the two kinds don't mix and match as they come in.

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
    if not show_all:
        all_bugs = [e for e in all_bugs if e.get("status", "open") == "open"]
        all_suggestions = [
            e for e in all_suggestions if e.get("status", "open") == "open"
        ]
    bugs = all_bugs[-n:]
    suggestions = all_suggestions[-n:]

    if not bugs and not suggestions:
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
    body += _reports_section("Bugs:", "BUG", bugs)
    body.append(style.wrought_rule(48))
    body += _reports_section("Ideas:", "IDEA", suggestions)
    lines = style.format_sheet("REPORTS", body, width=52)
    character.session.send("\r\n".join(lines))


def cmd_resolve(character, args, game):
    """GM command: resolve <bug|suggest> <id> <open|resolved|rejected> --
    flip a logged report's status. <id> is the number shown by 'reports'
    (a report's line number within its own log file, stable across calls
    since mark() only ever rewrites a line in place). Non-GMs are rejected
    with nothing changed.
    """
    from engine import reports
    if not _is_gm(character):
        character.session.send("You aren't a GM.")
        return

    usage = "Usage: resolve <bug|suggest> <id> <open|resolved|rejected>"
    parts = args.split()
    if len(parts) != 3:
        character.session.send(usage)
        return
    kind_word, id_text, status = parts
    kind = {"bug": reports.BUG, "suggest": reports.SUGGEST}.get(kind_word.lower())
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
        reports.mark(kind, entry_id, status, directory=game.report_dir)
    except IndexError as exc:
        character.session.send(str(exc))
        return

    character.session.send(f"Marked {kind_word} #{entry_id} as {status}.")
    print(f"[GM] {character.key} marked {kind_word} #{entry_id} as {status}.")
