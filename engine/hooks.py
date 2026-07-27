"""
engine/hooks.py -- registration points so a game can extend the engine
without the engine importing the game.

SUPERS (or any future game) calls the set_* helpers at boot. Defaults are
safe no-ops so a bare engine import/Character create works with no game
installed -- the two-repo purity gate (docs/plans/two_repo_purity.md).

See docs/ENGINE_CONSUMER.md for the consumer-facing summary.
"""

# Character composition (AGENTS.md rule 4): game attaches stats/Origin/etc.
_character_attacher = None

# Persistence: engine owns SQLite; game owns the opaque JSON blob fields.
_blob_to = None
_blob_from = None

# Optional post-password new-character flow (appearance, Background, ...).
_chargen = None

# Optional post-placement new-character side effect (homezone tutorial
# kickoff, ...). Runs AFTER chargen finishes AND the character has been
# placed in the world (move_to already happened) -- see set_after_new_
# character's docstring for why the ordering matters.
_after_new_character = None

# Optional post-Session-attach side effect (mail inbox notify, ...).
# Runs for reconnects AND brand-new characters, after the Session is
# wired and the character is in the world, before play()/first look.
_after_session_attach = None

# Optional pre-Echo Session-detach side effect (connection log, ...).
# Runs while the Character still has session-linked fields intact --
# reconnect takeover, intentional quit, and client EOF all pass through
# engine/connection.py before session is cleared.
_on_session_disconnect = None

# GMCP Char.Vitals / Char.Status payload builders (SUPERS fills meters;
# engine/gmcp.py sends). fn(character) -> dict or None.
_gmcp_char_vitals = None
_gmcp_char_status = None

# System topic pages for bare `help` (game content; engine verbs read these).
_help_topics = {}
_help_categories = []

# Player-verb dispatch: engine/npc_act.py needs to run one raw command line
# the same way a real player's input would, but the actual `dispatch()`
# function lives in the shared root commands.py (which itself imports
# supers.verbs) -- routing through this hook keeps npc_act.py from ever
# importing that module directly (Phase 2 purity gate).
_dispatch = None

# --- Phase 2 game-flavor hooks -------------------------------------------
# The hooks below are the small, single-purpose extension points that
# replace the LAZY (function-local) SUPERS imports `engine/verbs/basic.py`
# used to have. Each one defaults to a safe no-op
# (usually "return None", meaning "no flavor to add") so a bare engine with
# no game installed still runs; SUPERS registers the real implementations
# in `supers/bootstrap.py`'s `register_all_hooks()`.

# Outdoor "eclipse" ambient line for `look`/`time` on outdoor rooms.
# fn(game) -> str ("" or falsy means "no eclipse right now").
_eclipse_ambient_line = None

# Per-room look extras (e.g. planar influence note). fn(room, game) -> list[str].
_room_look_extras = None

# Soft fear nudge shown to a Vampire after `look` when a Slayer/hunter
# shares the room. fn(character, room) -> str or None.
_vampire_fear_message = None

# One-sided relationship "quirk" line shown after looking at/examining a
# person. fn(viewer, target) -> str or None.
_look_quirk = None

# Public extra lines after a character's description on look/examine
# (including look me). fn(viewer, target) -> list[str] (may be empty).
_look_extra_lines = None

# Pre-move gate (jail cells, hunter-safe sanctuaries, ...). Called AFTER the
# engine has already confirmed `dest` is a real exit -- this hook only
# decides whether the game's rules allow walking through it right now.
# fn(character, room, dest, game) -> block message str, or None to allow.
_move_gate = None
_follow_pull_skip = None
_report_context_extra = None

# Optional rewrite of enter/exit/in/out destinations (e.g. spill NPCs off
# a no_loiter hub). fn(character, dest, game) -> Room (may be dest).
_transition_dest = None

# Look exit filter (e.g. closed Devil's Gates). fn(dest, game) -> bool.
# True / missing hook = show the exit; False = hide it from look Paths.
_look_exit_visible = None

# Optional look destination label. fn(room, direction, dest, game=None,
# character=None) -> str or None. Used when dest.look_title() is useless
# (dual-layer virtual wilderness exits point at self).
_look_exit_dest_label = None

# Cancel any in-progress "awake rest" state -- movement/combat interrupts it.
# fn(character) -> None (side-effecting only; no return value used).
_cancel_rest = None

# Room broadcast line for `get <item> from <body>` (nested loot leaving a
# body). fn(actor_key, body_key, item) -> str.
_loot_room_line = None

# Build an inventory Item for a strongbox's {"type": "relic", "id": ...}
# reward. fn(relic_id) -> Item or None.
_make_relic_item = None

# After a locked container is forced open (cmd_open). Games use this for
# mission strongbox objective flags, etc. fn(character, item) -> None.
_after_open_container = None

# --- Phase 2b hooks -------------------------------------------------------
# command_support.py (repo root) used to reach into `supers` directly for a
# handful of shared move/spirit-sight helpers (docs/plans/two_repo_purity.md
# Phase 2b). These four hooks are what let engine/command_support.py stay
# supers-agnostic the same way engine/verbs/basic.py already is.

# Spirit-sight gate (section 6): can `viewer` perceive `spirit`? A spirit
# always perceives itself even with no game installed -- everything past
# that (Spirit Magic, Attunement) is game-specific and needs the hook.
# fn(viewer, spirit) -> bool.
_can_see_spirit = None

# Dark-room sight gate (D67): can `character` see in a dark room without
# a carried light? Engine default is False (torch required). SUPERS
# registers night-sight for GM form, gods, monsters, Umbral, etc.
# fn(character, room) -> bool. `room` may be unused by some games.
_can_see_in_dark = None

# Living Reaper Mantle veil (Vesseldetails3). Default: everyone perceives.
# fn(viewer, other) -> bool.
_can_perceive_reaper = None

# Login name reserve (immersion cast keys, …). fn(name) -> bool.
# True means "refuse new chargen for this name".
_reserved_login_name = None

# Pre-move cancel (e.g. stop an in-progress training montage). Called
# before a single-character move actually happens.
# fn(character) -> player message str, or None if nothing to say.
_before_relocate = None

# Post-move arrival side effects (stop work if the job site was left behind,
# drag a carried body along, lodging owner-walks-in-on-squatter check, ...).
# fn(character, dest, game, was_working) -> None (side-effecting only).
_after_arrive = None
# Optional fn(character, direction, dest, game) after a successful room step
# (combat-pit pose feed). Kept separate from after_arrive so direction is known.
_after_move_step = None

# Public leave/arrive display name (Celestial riding a host -> host key).
# fn(character, game) -> str | None. None / missing hook -> character.key.
_move_public_name = None

# Presence subject for leave/arrive hears filters (Celestial riding ->
# living host). fn(character, game) -> Character | None. None / missing
# hook -> the mover. Separate from move_public_name so ordinary watchers
# hear "Host leaves north" even though the Mantle is still spirit=True.
_move_presence_actor = None

# Leave/arrive prose for ordinary walks (SUPERS gait: walks / glides).
# fn(face, direction, character) -> str | None for leave;
# fn(face, direction, character, *, carried=None) -> str | None for arrive.
# Missing hook -> engine fallback "leaves" / "arrives".
_move_leave_line = None
_move_arrive_line = None

# Mundane hood/mask look/who face. fn(character) -> str or None.
# None means "not concealed; use the normal key path".
_concealed_presence_name = None

# Viewer-relative room / look / leave face.
# fn(viewer, subject) -> str | None. None / missing -> fall back to key path.
_presence_face_for = None
# Echo room-look tag bits (``['echo']`` quiet vs full idle/regimen). SUPERS
# registers so ``echo look quiet|full`` works without engine importing game.
_echo_look_bits = None

# Viewer-relative look/examine body text.
# fn(viewer, subject) -> str | None.
_look_body_for = None

# Room-entry spawn/aggro rolls (wilderness hostiles, procedural dungeons,
# idle-hostile aggro). fn(game, room) -> None (side-effecting only).
_encounter_check = None

# --- Phase 3 persistence hooks --------------------------------------------
# engine/persistence.py stays supers-agnostic the same way; these replace
# the two lazy `from supers import balance/stats` calls the old root
# persistence.py made directly.

# Ensure Evil Strikes Back world-meter fields exist on `game` before saving
# them. fn(game) -> None (side-effecting only).
_ensure_game_defaults = None

# Re-derive a character's max HP (used after un-spiriting a character whose
# body was lost on load -- see engine/persistence.py's load_world).
# fn(character) -> None (mutates character.hp in place).
_recompute_hp = None

# Build a seed Item from a map file's seed_items entry (catalog_id lookup
# etc.) -- maps.py's loader needs this so it stays supers-agnostic too.
# fn(item_data, where) -> Item.
_make_world_item = None

# Promote a legacy flavor-only strongbox Item (pre-lockbox save data) into a
# real locked container with rolled loot. fn(item) -> bool (mutates item in
# place; True if it upgraded something). engine/persistence.py's load_world
# needs this so it stays supers-agnostic (the reward math lives in
# supers/world_ext.py, which reaches into supers.faith for relic drops).
_upgrade_legacy_container = None


def set_upgrade_legacy_container(fn):
    """Register fn(item) -> bool for legacy strongbox promotion on load.

    Pass None to restore the no-op default (a bare engine install has no
    lockbox/strongbox content, so there's nothing to promote).
    """
    global _upgrade_legacy_container
    _upgrade_legacy_container = fn


def upgrade_legacy_container(item):
    """Run the registered legacy-strongbox-upgrade hook, or do nothing (and
    report no upgrade) if none is set."""
    if _upgrade_legacy_container is not None:
        return _upgrade_legacy_container(item)
    return False


# Floor-item sink when a saved room key is gone (map rename / unload /
# deleted homestead). Games register a room; bare engine falls back to
# game.start_room so load never crashes.
_orphan_item_room = None


def set_orphan_item_room(fn):
    """Register fn(game) -> Room for homeless floor items on load.

    Pass None to restore the start-room fallback. SUPERS points this at
    the vault under Lucifer's Cage so Central Plaza stays clear.
    """
    global _orphan_item_room
    _orphan_item_room = fn


def orphan_item_room(game):
    """Room where floor items land when their holder room is missing.

    Returns the registered room, else ``game.start_room`` (may be None
    only if the game has no start room yet -- callers still guard).
    """
    if _orphan_item_room is not None:
        room = _orphan_item_room(game)
        if room is not None:
            return room
    return getattr(game, "start_room", None)


# Combat-gear persistence helpers (SUPERS items catalog enrich + rebind).
_enrich_loaded_item = None
_rebind_character_equipment = None


def set_enrich_loaded_item(fn):
    """Register fn(item) to copy catalog slot/mods onto a loaded Item."""
    global _enrich_loaded_item
    _enrich_loaded_item = fn


def enrich_loaded_item(item):
    """Copy catalog gear fields onto a persistence-loaded Item (no-op bare)."""
    if _enrich_loaded_item is not None:
        return _enrich_loaded_item(item)
    return item


def set_rebind_character_equipment(fn):
    """Register fn(character) to rebuild equipment from inventory flags."""
    global _rebind_character_equipment
    _rebind_character_equipment = fn


def rebind_character_equipment(character):
    """Rebuild character.equipment after inventory load (no-op bare)."""
    if _rebind_character_equipment is not None:
        return _rebind_character_equipment(character)
    return None


_item_display_key = None


def set_item_display_key(fn):
    """Register fn(item, viewer=None) -> str for painted item names."""
    global _item_display_key
    _item_display_key = fn


def item_display_key(item, viewer=None):
    """Return a display name for an item (painted when the game registers it)."""
    if _item_display_key is not None:
        return _item_display_key(item, viewer)
    return getattr(item, "key", "") or ""


def set_make_world_item(fn):
    """Register fn(item_data, where) -> Item for map seed_items entries.

    Pass None to restore the default, a bare flavor Item built straight
    from item_data's "key"/"description" (no catalog lookup) -- enough for
    a bare engine install to boot with no SUPERS catalog registered.
    """
    global _make_world_item
    _make_world_item = fn


def make_world_item(item_data, where=""):
    """Build a seed Item for a map room, via the registered game catalog
    if one is set, else a plain flavor Item from item_data alone."""
    if _make_world_item is not None:
        return _make_world_item(item_data, where=where)
    from engine.world import Item
    return Item(
        item_data.get("key", "an unremarkable object"),
        item_data.get("description", "You see nothing special."),
    )


def set_ensure_game_defaults(fn):
    """Register fn(game) that backfills Evil Strikes Back world-meter
    fields (moral_balance, eclipse_until_tick, ...) before they're saved.
    Pass None to restore the no-op default.
    """
    global _ensure_game_defaults
    _ensure_game_defaults = fn


def ensure_game_defaults(game):
    """Run the registered world-meter-defaults hook, or do nothing if none
    is set (a bare engine install has no moral-balance meter)."""
    if _ensure_game_defaults is not None:
        _ensure_game_defaults(game)


def set_recompute_hp(fn):
    """Register fn(character) that mutates character.hp back to its max
    (SUPERS: supers.stats.max_hp). Pass None to restore the no-op default.
    """
    global _recompute_hp
    _recompute_hp = fn


def recompute_hp(character):
    """Run the registered max-HP recompute hook, or do nothing if none is
    set (a bare engine Character has no stat spine to derive HP from)."""
    if _recompute_hp is not None:
        _recompute_hp(character)


_gateway_resume_hook = None  # fn(game) -> None, or an awaitable -- see setter.


def set_gateway_resume_hook(fn):
    """Register fn(game) (sync or async) to run once gateway reattach
    finishes welcoming held clients back (engine/gateway_client.py's
    ``_on_ctrl`` "welcome" branch). SUPERS uses this to vault offline
    bodies still mid-tutorial-onboarding (supers.tutorial.
    heal_incomplete_tutorial_offline) only after reattach, so still-
    connected mid-tutorial PCs are not swept. Pass None to restore the
    no-op default.
    """
    global _gateway_resume_hook
    _gateway_resume_hook = fn


async def gateway_resume_hook(game):
    """Run the registered gateway-resume hook, awaiting it if it's a
    coroutine function; no-op if none is set (a bare engine boot has
    nothing SUPERS-specific to sweep on gateway reattach)."""
    if _gateway_resume_hook is None:
        return
    import inspect
    result = _gateway_resume_hook(game)
    if inspect.isawaitable(result):
        await result


def set_character_attacher(fn):
    """Register fn(character) called at the end of Character.__init__.

    Pass None to clear (lean engine Characters only).
    """
    global _character_attacher
    _character_attacher = fn


def attach_character(character):
    """Run the registered attacher, or do nothing if none is set."""
    if _character_attacher is not None:
        _character_attacher(character)


def set_blob_codec(to_blob, from_blob):
    """Register character <-> JSON-blob helpers for persistence.

    to_blob(character) -> dict
    from_blob(character, data_dict) -> None (mutates character)

    Pass None, None to restore empty defaults.
    """
    global _blob_to, _blob_from
    _blob_to = to_blob
    _blob_from = from_blob


def character_to_blob(character):
    """Serialize game fields for the characters.stats JSON column."""
    if _blob_to is not None:
        return _blob_to(character)
    return {}


def apply_character_blob(character, data):
    """Apply a saved JSON blob onto a Character (game fields).

    Returns whatever the registered codec returns (SUPERS uses a
    (body_room_key, body_key) pending-link tuple, or None).
    """
    if _blob_from is not None:
        return _blob_from(character, data or {})
    return None


def set_chargen(async_fn):
    """Register async_fn(session, character) -> bool for new characters.

    Return False if the client disconnected mid-chargen. Pass None to skip
    chargen (engine demo / tests that only need a bare Character).
    """
    global _chargen
    _chargen = async_fn


async def run_chargen(session, character):
    """Run registered chargen, or succeed immediately if none is set."""
    if _chargen is None:
        return True
    return await _chargen(session, character)


def set_after_new_character(fn):
    """Register fn(character, game), called once right after a BRAND-NEW
    character has been placed in the world (chargen finished, move_to
    already ran, the session is registered for 'who'/broadcasts).

    Placement must come first: SUPERS' tutorial.begin_if_needed narrates
    the homezone room the character just materialized into, and
    tutorial.ensure_mentors needs `game.rooms` populated to seed mentors --
    calling this any earlier (e.g. mid-chargen) would be narrating a room
    the character isn't actually standing in yet. Pass None to restore the
    no-op default (a bare engine install has no post-create content).
    """
    global _after_new_character
    _after_new_character = fn


def after_new_character(character, game):
    """Run the registered post-placement hook, or do nothing if none is set."""
    if _after_new_character is not None:
        _after_new_character(character, game)


def set_after_session_attach(fn):
    """Register fn(character, game), called whenever a Session attaches to
    a character that is already in the world -- reconnect of an Echo, or
    a brand-new character right after after_new_character.

    Ordering: after_new_character (new chars only) → after_session_attach
    (everyone) → save → play/look. Pass None to restore the no-op default.
    """
    global _after_session_attach
    _after_session_attach = fn


def after_session_attach(character, game):
    """Run the registered Session-attach hook, or do nothing if none is set."""
    if _after_session_attach is not None:
        _after_session_attach(character, game)


def set_on_session_disconnect(fn):
    """Register fn(character, game, *, to_echo=True) before Session detach.

    ``to_echo`` is False when another client is taking over the same body
    (no Echo leave / vault-on-quit). Pass None to restore the no-op default.
    """
    global _on_session_disconnect
    _on_session_disconnect = fn


def on_session_disconnect(character, game, *, to_echo=True):
    """Run the registered Session-detach hook, or do nothing if none is set."""
    if _on_session_disconnect is not None:
        _on_session_disconnect(character, game, to_echo=to_echo)


def set_gmcp_char_vitals(fn):
    """Register fn(character) -> dict for Char.Vitals GMCP payloads.

    Pass None to restore the no-op default (engine/gmcp.py falls back to
    a minimal hp dict when the hook is unset).
    """
    global _gmcp_char_vitals
    _gmcp_char_vitals = fn


def gmcp_char_vitals(character):
    """Build a Char.Vitals dict, or None when no game hook is registered."""
    if _gmcp_char_vitals is not None:
        return _gmcp_char_vitals(character)
    return None


def set_gmcp_char_status(fn):
    """Register fn(character) -> dict of extra Char.Status fields (Origin…).

    Merged on top of engine base status. Pass None for no extras.
    """
    global _gmcp_char_status
    _gmcp_char_status = fn


def gmcp_char_status(character):
    """Extra Char.Status fields from the game, or None."""
    if _gmcp_char_status is not None:
        return _gmcp_char_status(character)
    return None


def set_help(topics, categories):
    """Inject HELP_TOPICS dict and HELP_CATEGORIES list for cmd_help.

    topics: name -> multi-line page string
    categories: list of (heading, [topic names]) as help_topics defines
    """
    global _help_topics, _help_categories
    _help_topics = topics if topics is not None else {}
    _help_categories = list(categories) if categories is not None else []


def get_help_topics():
    """Return the injected HELP_TOPICS map (may be empty)."""
    return _help_topics


def get_help_categories():
    """Return the injected HELP_CATEGORIES list (may be empty)."""
    return _help_categories


def set_dispatch(fn):
    """Register fn(character, raw, game) -- the real command dispatcher.

    Pass None to clear. engine/npc_act.py calls this through get_dispatch()
    instead of importing the root commands.py module directly.
    """
    global _dispatch
    _dispatch = fn


def get_dispatch():
    """Return the registered dispatcher, or None if none is set yet."""
    return _dispatch


def set_eclipse_ambient_line(fn):
    """Register fn(game) -> str for the outdoor eclipse ambient line.

    Pass None to restore the no-op default (never shows eclipse flavor).
    """
    global _eclipse_ambient_line
    _eclipse_ambient_line = fn


def eclipse_ambient_line(game):
    """Return the eclipse ambient line for this tick, or "" if none/no game."""
    if _eclipse_ambient_line is not None:
        return _eclipse_ambient_line(game)
    return ""


def set_room_look_extras(fn):
    """Register fn(room, game, character=None) -> list[str] for look extras.

    Pass None to restore the empty default. Used for planar influence,
    Croatoan panic, and similar room-scoped flavor (plain text, not
    color-alone). Older ``fn(room, game)`` callbacks still work.
    """
    global _room_look_extras
    _room_look_extras = fn


def room_look_extras(room, game, character=None):
    """Return extra look lines for this room, or [].

    ``character`` is the look viewer when known (infection / immunity).
    """
    if _room_look_extras is None:
        return []
    try:
        return list(_room_look_extras(room, game, character) or [])
    except TypeError:
        return list(_room_look_extras(room, game) or [])


def set_vampire_fear_message(fn):
    """Register fn(character, room) -> str or None for the post-look fear nudge."""
    global _vampire_fear_message
    _vampire_fear_message = fn


def vampire_fear_message(character, room):
    """Return the Vampire-vs-Slayer fear line, or None if no game/none due."""
    if _vampire_fear_message is not None:
        return _vampire_fear_message(character, room)
    return None


# Optional lines after bare room look (Procurer case tell, …).
# fn(character, room, game) -> list[str] or None.
_after_bare_look = None

# Optional extra block on bare ``group`` roster (SUPERS convoy objective).
# fn(character, game=None) -> str or None.
_group_sheet_extra = None


def set_after_bare_look(fn):
    """Register fn(character, room, game) -> list[str] after bare look."""
    global _after_bare_look
    _after_bare_look = fn


def after_bare_look(character, room, game):
    """Return extra lines after bare room look, or an empty list."""
    if _after_bare_look is not None:
        result = _after_bare_look(character, room, game)
        if result:
            return list(result)
    return []


def set_group_sheet_extra(fn):
    """Register fn(character, game=None) -> str for extra ``group`` sheet text.

    Pass None to restore the empty default. Engine ``group`` stays
    game-agnostic; SUPERS fills the pack / convoy "what the group wants"
    block for leaders who are in control.
    """
    global _group_sheet_extra
    _group_sheet_extra = fn


def group_sheet_extra(character, game=None):
    """Return extra ``group`` sheet text, or \"\" when unset / empty."""
    if _group_sheet_extra is None:
        return ""
    try:
        result = _group_sheet_extra(character, game)
    except TypeError:
        # Older one-arg callbacks still work.
        result = _group_sheet_extra(character)
    if not result:
        return ""
    return str(result)


def set_look_quirk(fn):
    """Register fn(viewer, target) -> str or None for the look/examine quirk."""
    global _look_quirk
    _look_quirk = fn


def look_quirk(viewer, target):
    """Return a one-sided relationship quirk line, or None if no game/none due."""
    if _look_quirk is not None:
        return _look_quirk(viewer, target)
    return None


def set_look_extra_lines(fn):
    """Register fn(viewer, target) -> list[str] after look/examine description."""
    global _look_extra_lines
    _look_extra_lines = fn


def look_extra_lines(viewer, target):
    """Return public extra look lines (tattoos, …), or an empty list."""
    if _look_extra_lines is not None:
        result = _look_extra_lines(viewer, target)
        if result:
            return list(result)
    return []


# `look in <item>` game handlers (fridge stock, …). fn(character, item, game)
# -> list[str] lines to send, or None/[] to fall through to body loot.
_look_in_item = None


def set_look_in_item(fn):
    """Register fn(character, item, game) -> list[str] or None for look-in."""
    global _look_in_item
    _look_in_item = fn


def look_in_item(character, item, game):
    """Return look-in lines from the game, or None if unhandled."""
    if _look_in_item is not None:
        return _look_in_item(character, item, game)
    return None


def set_move_gate(fn):
    """Register fn(character, room, dest, game) -> block message or None."""
    global _move_gate
    _move_gate = fn


def move_gate_block(character, room, dest, game):
    """Return a message blocking this move, or None to allow it through."""
    if _move_gate is not None:
        return _move_gate(character, room, dest, game)
    return None


def set_follow_pull_skip(fn):
    """Register fn(follower, leader, game) -> True to skip follow-pull."""
    global _follow_pull_skip
    _follow_pull_skip = fn


def follow_pull_skip(follower, leader, game):
    """True when leader move must not drag this follower along."""
    if _follow_pull_skip is not None:
        return bool(_follow_pull_skip(follower, leader, game))
    return False


def set_report_context_extra(fn):
    """Register fn(character, game) -> dict for bug/suggest report context."""
    global _report_context_extra
    _report_context_extra = fn


def report_context_extra(character, game):
    """Optional SUPERS gameplay facts merged into filed reports."""
    if _report_context_extra is not None:
        try:
            extra = _report_context_extra(character, game)
        except Exception:
            return {}
        if isinstance(extra, dict):
            return extra
    return {}


def set_transition_dest(fn):
    """Register fn(character, dest, game) -> Room for enter/exit/in/out."""
    global _transition_dest
    _transition_dest = fn


def transition_dest(character, dest, game):
    """Maybe rewrite a zone/plane transition destination (default: unchanged)."""
    if _transition_dest is not None:
        rewritten = _transition_dest(character, dest, game)
        if rewritten is not None:
            return rewritten
    return dest


def set_look_exit_visible(fn):
    """Register fn(dest, game) -> bool (False hides the exit from look)."""
    global _look_exit_visible
    _look_exit_visible = fn


def look_exit_visible(dest, game):
    """True when look may list an exit into `dest`."""
    if _look_exit_visible is not None:
        return bool(_look_exit_visible(dest, game))
    return True


def set_look_exit_dest_label(fn):
    """Register optional look exit destination label rewriter.

    ``fn(room, direction, dest, game=None, character=None) -> str|None``.
    Return a plain label to replace ``dest.look_title()``, or None to keep
    the default. Dual-layer wilderness uses this so Paths name Lebanon /
    bunker / terrain instead of repeating the same virtual room title.
    """
    global _look_exit_dest_label
    _look_exit_dest_label = fn


def look_exit_dest_label(room, direction, dest, game=None, character=None):
    """Return an overridden look exit label, or None for the default title."""
    if _look_exit_dest_label is None:
        return None
    try:
        return _look_exit_dest_label(
            room, direction, dest, game=game, character=character,
        )
    except TypeError:
        # Older fn(room, direction, dest, game) without character=.
        try:
            return _look_exit_dest_label(room, direction, dest, game)
        except TypeError:
            return _look_exit_dest_label(room, direction, dest)


def set_cancel_rest(fn):
    """Register fn(character) that silently ends an "awake rest" state."""
    global _cancel_rest
    _cancel_rest = fn


def cancel_rest(character):
    """Run the registered cancel-rest hook, or do nothing if none is set."""
    if _cancel_rest is not None:
        _cancel_rest(character)


def set_loot_room_line(fn):
    """Register fn(actor_key, body_key, item) -> str for the loot broadcast."""
    global _loot_room_line
    _loot_room_line = fn


def loot_room_line(actor_key, body_key, item):
    """Room broadcast for `get <item> from <body>` (generic fallback wording
    if no game is installed to supply its own flavor).
    """
    if _loot_room_line is not None:
        return _loot_room_line(actor_key, body_key, item)
    return f"{actor_key} takes {item.key} from {body_key}."


_after_body_loot = None


def set_after_body_loot(fn):
    """Register fn(character, body, item, game) after nested body loot.

    Used for immersion-cast loot barks. Pass None to clear.
    """
    global _after_body_loot
    _after_body_loot = fn


def after_body_loot(character, body, item, game=None):
    """Run the registered post-body-loot hook, or do nothing."""
    if _after_body_loot is not None:
        _after_body_loot(character, body, item, game)


_after_look_in_body = None


def set_after_look_in_body(fn):
    """Register fn(character, body, game) after `look in <body>` lists loot."""
    global _after_look_in_body
    _after_look_in_body = fn


def after_look_in_body(character, body, game=None):
    """Run the registered look-in-body hook, or do nothing."""
    if _after_look_in_body is not None:
        _after_look_in_body(character, body, game)


_after_look_item = None


def set_after_look_item(fn):
    """Register fn(character, item, game) after examining an item."""
    global _after_look_item
    _after_look_item = fn


def after_look_item(character, item, game=None):
    """Run the registered look-item hook, or do nothing."""
    if _after_look_item is not None:
        _after_look_item(character, item, game)


def set_make_relic_item(fn):
    """Register fn(relic_id) -> Item or None for strongbox relic rewards."""
    global _make_relic_item
    _make_relic_item = fn


def make_relic_item(relic_id):
    """Build a relic Item from a strongbox reward id, or None if no game."""
    if _make_relic_item is not None:
        return _make_relic_item(relic_id)
    return None


def set_after_open_container(fn):
    """Register fn(character, item) after cmd_open consumes a locked box.

    Pass None to restore the no-op default (bare engine has no quests).
    """
    global _after_open_container
    _after_open_container = fn


def after_open_container(character, item):
    """Run the registered post-open hook, or do nothing if none is set."""
    if _after_open_container is not None:
        _after_open_container(character, item)


# After a character acquires an Item into inventory (get / open / loot).
# Games may auto-stow reagents into a gear bag. fn(character, item) ->
# optional player message str, or None.
_after_acquire_item = None


def set_after_acquire_item(fn):
    """Register fn(character, item) -> str|None after inventory acquire.

    Pass None to clear. Engine get/open call this so SUPERS can auto-stow
    gear-bag reagents without engine importing supers.
    """
    global _after_acquire_item
    _after_acquire_item = fn


def after_acquire_item(character, item):
    """Run the acquire hook; return optional player message (or None)."""
    if _after_acquire_item is not None:
        return _after_acquire_item(character, item)
    return None


def set_can_see_spirit(fn):
    """Register fn(viewer, spirit) -> bool for the spirit-sight gate.

    Pass None to restore the default: only a spirit sees itself.
    """
    global _can_see_spirit
    _can_see_spirit = fn


def can_see_spirit(viewer, spirit):
    """Can `viewer` perceive the discorporate spirit `spirit`?

    Default (no game installed): a spirit always perceives itself; nobody
    else can. SUPERS registers the real Spirit Magic / Attunement check.
    """
    if viewer is spirit:
        return True
    if _can_see_spirit is not None:
        return _can_see_spirit(viewer, spirit)
    return False


def set_can_see_in_dark(fn):
    """Register fn(character, room) -> bool for dark-room night-sight.

    Pass None to restore the default: nobody pierces dark without light.
    """
    global _can_see_in_dark
    _can_see_in_dark = fn


def can_see_in_dark(character, room=None):
    """Can `character` see in a dark room without a carried light source?

    Default (no game installed): False -- torch/lantern only. SUPERS
    registers GM form, God Mantle, hostiles, Monster Origin, Umbral,
    and active heatvision.
    """
    if _can_see_in_dark is not None:
        return bool(_can_see_in_dark(character, room))
    return False


def set_can_perceive_reaper(fn):
    """Register fn(viewer, other) -> bool for the living-Reaper veil.

    Pass None to restore the default (everyone perceives everyone).
    """
    global _can_perceive_reaper
    _can_perceive_reaper = fn


def can_perceive_reaper(viewer, other):
    """Can ``viewer`` perceive living Reaper ``other`` (Mantle veil)?

    Default (no game): True for everyone. SUPERS hides veiled Reapers
    from ordinary sight unless spirit-sight / dying / astral / peer /
    staff pierce the veil. Non-Reaper ``other`` always returns True.
    """
    if _can_perceive_reaper is not None:
        return bool(_can_perceive_reaper(viewer, other))
    return True


def set_before_relocate(fn):
    """Register fn(character) -> player message str or None, run just
    before a single-character move actually happens (e.g. cancel an
    in-progress training montage). Pass None to restore the no-op default.
    """
    global _before_relocate
    _before_relocate = fn


def before_relocate(character):
    """Run the pre-move hook and return its player-facing message, or None
    if nothing needs to be said (including when no game is installed)."""
    if _before_relocate is not None:
        return _before_relocate(character)
    return None


def set_after_arrive(fn):
    """Register fn(character, dest, game, was_working) called right after a
    single-character move lands in `dest` (stop work if the job site was
    left behind, drag a carried body along, lodging owner-enters check,
    ...). Pass None to restore the no-op default.
    """
    global _after_arrive
    _after_arrive = fn


def after_arrive(character, dest, game, was_working):
    """Run the registered post-arrival hook, or do nothing if none is set."""
    if _after_arrive is not None:
        _after_arrive(character, dest, game, was_working)


def set_after_move_step(fn):
    """Register fn(character, direction, dest, game) after a successful step.

    Used by the opt-in combat-pit feed to stamp facing / pose without
    importing supers into engine/command_support. Pass None to clear.
    """
    global _after_move_step
    _after_move_step = fn


def after_move_step(character, direction, dest, game):
    """Run the registered post-step hook, or do nothing if none is set."""
    if _after_move_step is not None:
        _after_move_step(character, direction, dest, game)


def set_move_public_name(fn):
    """Register fn(character, game) -> str for leave/arrive broadcast names.

    Used so a Celestial riding a living vessel walks as the host in room
    text. Pass None to restore character.key.
    """
    global _move_public_name
    _move_public_name = fn


def move_public_name(character, game=None):
    """Display name for a move leave/arrive line (host while riding)."""
    if _move_public_name is not None:
        name = _move_public_name(character, game)
        if name:
            return name
    return getattr(character, "key", "?")


def set_move_presence_actor(fn):
    """Register fn(character, game) -> Character for leave/arrive visibility.

    Riding Mantles stay ``spirit=True`` (hidden on look) but room traffic
    names the host -- the hears filter must check the host, not the
    Mantle, or ordinary watchers never hear the walk. Pass None to clear.
    """
    global _move_presence_actor
    _move_presence_actor = fn


def move_presence_actor(character, game=None):
    """Character whose presence gates leave/arrive hears (host while riding)."""
    if _move_presence_actor is not None:
        actor = _move_presence_actor(character, game)
        if actor is not None:
            return actor
    return character


def set_move_leave_line(fn):
    """Register fn(face, direction, character) -> str for leave broadcasts.

    SUPERS uses this for curated gait (``walks east``, ``glides west``).
    Pass None to restore the bare-engine ``leaves`` wording.
    """
    global _move_leave_line
    _move_leave_line = fn


def move_leave_line(face, direction, character):
    """Third-person leave line for ordinary walks."""
    if _move_leave_line is not None:
        line = _move_leave_line(face, direction, character)
        if line:
            return line
    return f"{face} leaves {direction}."


def set_move_arrive_line(fn):
    """Register arrive-line formatter for ordinary walks.

    Signature: fn(face, direction, character, *, carried=None) -> str.
    *direction* is the exit taken (east); the game may invert it for
    ``in from the west``. Pass None to restore ``arrives``.
    """
    global _move_arrive_line
    _move_arrive_line = fn


def move_arrive_line(face, direction, character, *, carried=None):
    """Third-person arrive line for ordinary walks."""
    if _move_arrive_line is not None:
        line = _move_arrive_line(
            face, direction, character, carried=carried,
        )
        if line:
            return line
    if carried is not None:
        body_key = getattr(carried, "key", None) or "a body"
        return (
            f"{face} arrives, {body_key} slung over one shoulder."
        )
    return f"{face} arrives."


def set_concealed_presence_name(fn):
    """Register fn(character) -> str|None for hood/mask look/who faces.

    When the game returns a non-empty string, engine `_display_name` uses
    it instead of the login key. Pass None to clear (bare engine: always
    show the real key).
    """
    global _concealed_presence_name
    _concealed_presence_name = fn


def concealed_presence_name(character):
    """Short-desc face while hooded/masked, or None when not concealed."""
    if _concealed_presence_name is not None:
        return _concealed_presence_name(character)
    return None


def set_presence_face_for(fn):
    """Register fn(viewer, subject) -> str|None for viewer-relative faces.

    Used by room listings, leave/arrive, look headers, and socials so
    unintroduced / hooded people never leak a login key to strangers.
    Pass None to clear (bare engine: always the storage key path).
    """
    global _presence_face_for
    _presence_face_for = fn


def presence_face_for(viewer, subject):
    """Viewer-relative public face, or None when the hook is unset."""
    if _presence_face_for is not None:
        return _presence_face_for(viewer, subject)
    return None


def set_echo_look_bits(fn):
    """Register fn(character) -> list[str] for Echo room-look tags.

    Quiet mode returns ``['echo']`` only (still labeled for a11y). Full
    mode may append idle / regimen / criminal. Pass None to clear.
    """
    global _echo_look_bits
    _echo_look_bits = fn


def echo_look_bits(obj):
    """Echo look tag bits, or None when no game hook is registered."""
    if _echo_look_bits is not None:
        return _echo_look_bits(obj)
    return None


def set_look_body_for(fn):
    """Register fn(viewer, subject) -> str|None for look/examine bodies."""
    global _look_body_for
    _look_body_for = fn


def look_body_for(viewer, subject):
    """Viewer-relative look body, or None when the hook is unset."""
    if _look_body_for is not None:
        return _look_body_for(viewer, subject)
    return None


def set_encounter_check(fn):
    """Register fn(game, room) for room-entry spawn/aggro rolls (wilderness
    hostiles, procedural dungeons, idle-hostile aggro). Pass None to
    restore the no-op default (a bare engine install has no spawn tables).
    """
    global _encounter_check
    _encounter_check = fn


def encounter_check(game, room):
    """Run the registered encounter-check hook, or do nothing if none is set."""
    if _encounter_check is not None:
        _encounter_check(game, room)


def set_reserved_login_name(fn):
    """Register fn(name) -> bool for chargen name reservation.

    True means the name is reserved (refuse new character create). Pass
    None to clear (bare engine allows any unused name).
    """
    global _reserved_login_name
    _reserved_login_name = fn


def is_reserved_login_name(name):
    """True when the game has reserved this login name for a catalog body."""
    if _reserved_login_name is None:
        return False
    return bool(_reserved_login_name(name))


# Hard gm fold: restore a vaulted pfile when login name is not in memory.
# fn(game, name) -> Character | None
_try_restore_folded_login = None


def set_try_restore_folded_login(fn):
    """Register fn(game, name) -> Character|None for vaulted login restore.

    Called when ``find_login_character`` misses so a folded mortal can log
    in without looking like a brand-new name. Pass None to clear.
    """
    global _try_restore_folded_login
    _try_restore_folded_login = fn


def try_restore_folded_login(game, name):
    """Hydrate a hard-folded Echo from the vault, or None."""
    if _try_restore_folded_login is None:
        return None
    return _try_restore_folded_login(game, name)


# Unfinished homezone onboarding: body vaulted on copyover boot; gateway
# reattach must not treat it like a restorable hard fold.
# fn(game, name) -> bool
_is_tutorial_incomplete_vault = None


def set_is_tutorial_incomplete_vault(fn):
    """Register fn(game, name) -> bool for onboarding vault rows."""
    global _is_tutorial_incomplete_vault
    _is_tutorial_incomplete_vault = fn


def is_tutorial_incomplete_vault(game, name):
    """True when ``name`` is vaulted for unfinished chargen onboarding."""
    if _is_tutorial_incomplete_vault is None:
        return False
    return bool(_is_tutorial_incomplete_vault(game, name))


# --- Dual-layer overland / zone / help (Phase 2 purity) ------------------
# Dual-layer America travel, dungeon hub soft-stamps, and authored-quest
# help gates used to be lazy `from supers import …` inside
# engine/verbs/basic.py. Those imports break the two-repo purity scan;
# games register the real implementations here instead.

# Resolve atlas map center when the character is in a vehicle interior
# (no grid stamp) but still has macro_pos. fn(character, game) -> Room|None.
_map_center_room = None

# Cardinal / diagonal move on virtual wilderness (vehicle macro or foot
# micro). True = handled (do not follow Room.exits). False / missing =
# fall through to classic exit walk.
# fn(character, direction, game) -> bool
_try_directional_move = None

# Landmark / dual-layer `enter` before classic zone_entries.
# True = handled. fn(character, args, game) -> bool
_try_enter_zone = None

# Boarded vehicle: ``enter`` / ``in`` / ``out`` while in_vehicle is set.
# fn(character, args, game) -> bool  /  fn(character, game, direction=) -> bool
_try_vehicle_enter_as_house_in = None


# --- Companion duty hook (Phase 2 purity) ---------------------------------
# Games may register a companion cleanup function so engine code need not
# import game-specific `supers` modules. This keeps engine generic and testable.
_clear_companion_duty = None


def set_clear_companion_duty(fn):
    """Register fn(member, game, *, reason=, silent=) that clears companion duty.

    Pass None to clear (no-op)."""
    global _clear_companion_duty
    _clear_companion_duty = fn


def clear_companion_duty(member, game, *, reason="group_split", silent=False):
    """Invoke the registered companion cleanup, or no-op if none is set."""
    if _clear_companion_duty is None:
        return None
    return _clear_companion_duty(member, game, reason=reason, silent=silent)


# --- Player tips hooks ----------------------------------------------------
# Games can register rich tips helpers; engine exposes a tiny fallback so
# config tips remains safe in lean engine runs.
_tips_status_line = None
_set_tips_enabled = None


def set_tips_hooks(status_fn=None, set_fn=None):
    """Register tips hooks: status_fn(character, game) and
    set_fn(character, game, enabled). Pass None to clear."""
    global _tips_status_line, _set_tips_enabled
    _tips_status_line = status_fn
    _set_tips_enabled = set_fn


def tips_status_line(character, game=None):
    """Return a status line for tips; falls back to empty string."""
    if _tips_status_line is None:
        try:
            return "Tips: " + ("on" if getattr(character, "tips_enabled", False) else "off")
        except Exception:
            return ""
    return _tips_status_line(character, game)


def set_tips_enabled(character, game, enabled):
    """Enable/disable tips via registered hook or in-engine fallback."""
    if _set_tips_enabled is None:
        try:
            setattr(character, "tips_enabled", bool(enabled))
            return "Tips enabled" if enabled else "Tips disabled"
        except Exception:
            return ""
    return _set_tips_enabled(character, game, enabled)
_try_vehicle_nested_in_out = None

# After classic `enter <zone>` succeeds (stamp already applied). Clear
# overland coords + soft-stamp dungeon hubs. fn(character, game, dest)
_after_zone_enter = None

# Dual-layer `exit` onto virtual wilderness before classic zone_exit_to.
# True = handled. fn(character, game) -> bool
_try_exit_zone = None

# After a HELP_TOPICS page is shown (authored quests gate on help topics).
# fn(character, topic, game)
_after_help_topic = None


def set_map_center_room(fn):
    """Register fn(character, game) -> Room|None for atlas map centering.

    Used when location has no grid_prefix but the game tracks overland
    macro_pos (e.g. vehicle interiors). Pass None to clear.
    """
    global _map_center_room
    _map_center_room = fn


def map_center_room(character, game):
    """Return a grid Room for map center, or None if the game has none."""
    if _map_center_room is not None:
        return _map_center_room(character, game)
    return None


def set_try_directional_move(fn):
    """Register fn(character, direction, game) -> bool for special moves.

    True means the game handled the move (e.g. dual-layer overland).
    Pass None to clear (classic Room.exits only).
    """
    global _try_directional_move
    _try_directional_move = fn


def try_directional_move(character, direction, game):
    """True when a registered game handler consumed this directional move."""
    if _try_directional_move is not None:
        return bool(_try_directional_move(character, direction, game))
    return False


def set_try_enter_zone(fn):
    """Register fn(character, args, game) -> bool for special zone enter.

    True means the game handled `enter` (e.g. landmark gate at micro
    center). Pass None to clear.
    """
    global _try_enter_zone
    _try_enter_zone = fn


def try_enter_zone(character, args, game):
    """True when a registered game handler consumed this enter attempt."""
    if _try_enter_zone is not None:
        return bool(_try_enter_zone(character, args, game))
    return False


def set_try_vehicle_enter_as_house_in(fn):
    """Register fn(character, args, game) -> bool for boarded ``enter``.

    True means the game handled porch/garage enter while in_vehicle.
    Pass None to clear.
    """
    global _try_vehicle_enter_as_house_in
    _try_vehicle_enter_as_house_in = fn


def try_vehicle_enter_as_house_in(character, args, game):
    """True when a registered game handler consumed boarded ``enter``."""
    if _try_vehicle_enter_as_house_in is not None:
        return bool(_try_vehicle_enter_as_house_in(character, args, game))
    return False


def set_try_vehicle_nested_in_out(fn):
    """Register fn(character, game, *, direction) -> bool for boarded in/out.

    True means the game handled nested indoor move while in_vehicle.
    Pass None to clear.
    """
    global _try_vehicle_nested_in_out
    _try_vehicle_nested_in_out = fn


def try_vehicle_nested_in_out(character, game, *, direction):
    """True when a registered game handler consumed boarded in/out."""
    if _try_vehicle_nested_in_out is not None:
        return bool(_try_vehicle_nested_in_out(character, game, direction=direction))
    return False


def set_after_zone_enter(fn):
    """Register fn(character, game, dest) after classic zone enter succeeds.

    Games clear overland coords and soft-stamp dungeon hubs here.
    Pass None to clear.
    """
    global _after_zone_enter
    _after_zone_enter = fn


def after_zone_enter(character, game, dest):
    """Run the registered post-zone-enter hook, or do nothing."""
    if _after_zone_enter is not None:
        _after_zone_enter(character, game, dest)


def set_try_exit_zone(fn):
    """Register fn(character, game) -> bool for special zone exit.

    True means the game handled `exit` (e.g. onto virtual wilderness).
    Pass None to clear.
    """
    global _try_exit_zone
    _try_exit_zone = fn


def try_exit_zone(character, game):
    """True when a registered game handler consumed this zone exit."""
    if _try_exit_zone is not None:
        return bool(_try_exit_zone(character, game))
    return False


def set_after_help_topic(fn):
    """Register fn(character, topic, game) after a HELP_TOPICS page shows.

    Pass None to clear (bare engine has no authored quest gates).
    """
    global _after_help_topic
    _after_help_topic = fn


def after_help_topic(character, topic, game):
    """Run the registered post-help-topic hook, or do nothing."""
    if _after_help_topic is not None:
        _after_help_topic(character, topic, game)


# --- Weather / possession-exile / dungeon-entry (Phase 2 purity) ----------
# These replaced the last lazy `from supers import weather/personal_realm/
# dungeons/overland` calls inside engine/verbs/basic.py, which tripped the
# two-repo purity scan (smoke_test.engine_hooks_purity_tests). Games register
# the real implementations in supers/bootstrap.py; bare engine defaults keep
# `look`/`say`/`enter` working with no game installed.

# Weather look clause (supers.weather.format_look_clause) for outdoor/indoor
# rooms. fn(room, game, screenreader, character) -> str | None ("" / None =
# no weather line; look falls back to the plain calendar ambient).
_weather_look_clause = None

# Hybrid look vision (supers.weather.assess_look_vision): always-on overlay
# + chance whiteout for rain/storm/snow/nearby tornado. fn(character, room,
# game, screenreader, after_move) -> dict | None.
_weather_look_vision = None

# Possession consciousness exile: is this character's mind pinned inside a
# personal Heaven/Hell pocket while the body walks Earth? fn(character) -> bool.
_is_consciousness_exile = None

# The sensory Room a consciousness-exiled mind looks/speaks through.
# fn(character) -> Room | None.
_consciousness_sensory_room = None

# Dungeon-entry gate (epic party-ready + non-player refusal). Called only
# after the engine has resolved a real destination; returns a player-facing
# refusal string to block entry, or None to allow it.
# fn(character, dest, game) -> str | None.
_dungeon_entry_refusal = None

# Item-drop gate (e.g. a case loaner that must stay on the holder until
# reportcase/abandon). Called from cmd_drop before an item leaves inventory;
# returns a player-facing refusal string to block the drop, or None to allow
# it. fn(character, item) -> str | None.
_item_drop_refusal = None

# Clear a character's dual-layer overland coordinates when they step into a
# classic zone. fn(character) -> None (side-effecting only).
_clear_overland_coords = None

# Resolve a personal-mission zone entrance for `enter <raw>` (e.g. a hunter's
# own stronghold sharing a roadside trailhead). fn(character, game, room, raw)
# -> Room | None (None = no personal-mission entrance; fall back to public
# zone_entries).
_mission_entrance = None


def set_weather_look_clause(fn):
    """Register fn(room, game, screenreader, character) -> str|None.

    Pass None to restore the no-op default (a bare engine has no weather
    model, so `look` uses the plain calendar ambient line instead).
    """
    global _weather_look_clause
    _weather_look_clause = fn


def weather_look_clause(room, game, screenreader=False, character=None):
    """Return the game's weather clause for this room, or None if unset."""
    if _weather_look_clause is not None:
        return _weather_look_clause(
            room, game, screenreader=screenreader, character=character,
        )
    return None


def set_weather_look_vision(fn):
    """Register hybrid look-vision assessor.

    ``fn(character, room, game, *, screenreader, after_move)`` returns
    ``None`` or a dict with ``overlay``, ``whiteout``, and ``fail_line``.
    Pass None to restore the bare-engine default (no weather whiteout).
    """
    global _weather_look_vision
    _weather_look_vision = fn


def weather_look_vision(
    character, room, game, *, screenreader=False, after_move=False,
):
    """Return the game's look-vision assessment, or None if unset."""
    if _weather_look_vision is not None:
        return _weather_look_vision(
            character,
            room,
            game,
            screenreader=screenreader,
            after_move=after_move,
        )
    return None


def set_is_consciousness_exile(fn):
    """Register fn(character) -> bool for possession consciousness exile.

    Pass None to restore the default (bare engine has no afterlife pockets,
    so no character is ever mind-exiled).
    """
    global _is_consciousness_exile
    _is_consciousness_exile = fn


def is_consciousness_exile(character):
    """True when the game reports this mind is exiled to an afterlife pocket."""
    if _is_consciousness_exile is not None:
        return bool(_is_consciousness_exile(character))
    return False


def set_consciousness_sensory_room(fn):
    """Register fn(character) -> Room|None for the exile's sensory room.

    Pass None to restore the no-op default.
    """
    global _consciousness_sensory_room
    _consciousness_sensory_room = fn


def consciousness_sensory_room(character):
    """The Room a consciousness-exiled mind perceives, or None if unset."""
    if _consciousness_sensory_room is not None:
        return _consciousness_sensory_room(character)
    return None


def set_dungeon_entry_refusal(fn):
    """Register fn(character, dest, game) -> str|None for dungeon-entry gates.

    A returned string is shown to the player and blocks entry; None allows
    it. Pass None to restore the no-op default (bare engine has no dungeons).
    """
    global _dungeon_entry_refusal
    _dungeon_entry_refusal = fn


def dungeon_entry_refusal(character, dest, game):
    """Return a dungeon-entry refusal message, or None to allow entry."""
    if _dungeon_entry_refusal is not None:
        return _dungeon_entry_refusal(character, dest, game)
    return None


def set_item_drop_refusal(fn):
    """Register fn(character, item) -> str|None for item-drop gates.

    A returned string is shown to the player and blocks the drop; None
    allows it. Pass None to restore the no-op default (bare engine drops
    anything unconditionally).
    """
    global _item_drop_refusal
    _item_drop_refusal = fn


def item_drop_refusal(character, item):
    """Return an item-drop refusal message, or None to allow the drop."""
    if _item_drop_refusal is not None:
        return _item_drop_refusal(character, item)
    return None


def set_clear_overland_coords(fn):
    """Register fn(character) that clears dual-layer overland coordinates.

    Pass None to restore the no-op default (bare engine has no overland).
    """
    global _clear_overland_coords
    _clear_overland_coords = fn


def clear_overland_coords(character):
    """Clear the character's overland coords via the game hook, or do nothing."""
    if _clear_overland_coords is not None:
        _clear_overland_coords(character)


def set_mission_entrance(fn):
    """Register fn(character, game, room, raw) -> Room|None for `enter`.

    Pass None to restore the no-op default (bare engine has no personal
    missions, so `enter` uses the public zone_entries only).
    """
    global _mission_entrance
    _mission_entrance = fn


def mission_entrance(character, game, room, raw):
    """Resolve a personal-mission zone entrance, or None if unset/none apply."""
    if _mission_entrance is not None:
        return _mission_entrance(character, game, room, raw)
    return None


# Auto-deploy map heal (supers.map_heal.heal_all_from_hot_backups) after
# git reset --hard + protect restore. fn(root) -> list[str] log lines.
_auto_deploy_map_heal = None


def set_auto_deploy_map_heal(fn):
    """Register fn(root) -> list[str] for post-reset map backup merge.

    Pass None to restore the no-op default (bare engine has no map_backups).
    """
    global _auto_deploy_map_heal
    _auto_deploy_map_heal = fn


def auto_deploy_map_heal(root):
    """Merge protected map_backups into live JSON after deploy reset."""
    if _auto_deploy_map_heal is not None:
        return _auto_deploy_map_heal(root)
    return []
