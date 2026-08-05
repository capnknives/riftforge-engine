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

# Room composition (Phase 7 Stage 8): game attaches its own room-flavor
# fields (Vampire/Demon lore, Cadence lodging, town-system flags, ...) --
# same shape as the Character attacher above.
_room_attacher = None

# Map JSON room overrides (Phase 7 Stage G): after maps._add_room stamps
# engine-generic fields, the game may layer its authored flavor flags
# from the same room / cell-override dict. Default no-op so lean boots
# ignore SUPERS-only JSON keys.
_map_room_stamper = None

# Persistence: engine owns SQLite; game owns the opaque JSON blob fields.
_blob_to = None
_blob_from = None

# Game-owned Game meta (T3 persistence-api): moral/Tide, Cadence overrides,
# tuning tables, rumor boards, … — loaded/saved around the engine-generic
# world snapshot. Default no-op so lean boots keep __init__ defaults.
_game_meta_loader = None
_game_meta_saver = None

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
_park_gm_spirit_on_disconnect = None

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

# Virtual Paths not stored on ``Room.exits`` (pit descent after boss kill).
# fn(room, character, game) -> list[(direction, dest_label)].
_room_look_virtual_exits = None

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
_clinic_on_admit = None
_clinic_on_discharge = None
_clinic_casualty_meter = None
_clinic_ko_clear = None
_justice_on_robbery = None
_justice_fine_schedule = None
_follow_pull_skip = None
_report_context_extra = None
_room_broadcast_deliver = None
_room_broadcast_transform = None
_perception_character = None
_preference_character = None

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

# Lodging (H3a): bed sharing family check; safe-sleep policy; post-stamp hook.
_lodging_are_family = None
_lodging_sleep_policy = None
_lodging_room_stamper = None

# Paced travel (H3b): overland handler, player hop, cadence step, edge_ok, …
_paced_travel_overland_handler = None
_paced_travel_overland_advance = None
_paced_travel_player_hop = None
_paced_travel_cadence_step = None
_paced_travel_edge_ok = None
_paced_travel_enter_alias = None
_paced_travel_drive_to = None
_paced_travel_gait_of = None
_paced_travel_engaged_refuse = None
_paced_travel_list_destinations = None
_paced_travel_zone_rooms = None

# Room broadcast line for `get <item> from <body>` (nested loot leaving a
# body). fn(actor_key, body_key, item) -> str.
_loot_room_line = None

# Build an inventory Item for a strongbox's {"type": "relic", "id": ...}
# reward. fn(relic_id) -> Item or None.
_make_relic_item = None
_grant_relic_loot = None

# After a locked container is forced open (cmd_open). Games use this for
# mission strongbox objective flags, etc. fn(character, item) -> None.
_after_open_container = None
_before_open_container = None
_after_growth_banked = None

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

# Deal hellhound invis pierce. Default False (no Deal kit in bare engine).
# fn(viewer, hound) -> bool.
_can_see_hellhound = None
_can_notice_stealth = None

# Veil-layer membership + sight pierce (death spirits, faded Ghosts, veiled
# Reapers, vessel-free Mantle walks share Prime's XYZ map but not its
# interaction/visibility). Bare engine default: nobody is Veil-layer, so
# both hooks are moot with no game installed. fn(character) -> bool /
# fn(viewer, other) -> bool / fn() -> str.
_in_veil = None
_veil_visible_to = None
_veil_look_tag = None

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
_room_presence_line = None
# Echo room-look tag bits (``['echo']`` quiet vs full idle/regimen). SUPERS
# registers so ``echo look quiet|full`` works without engine importing game.
_echo_look_bits = None

# Extra room-target match needles (Origin/Path/kind aliases).
# fn(viewer, subject) -> iterable[str] | None. Engine matches the typed
# query against these the same way as name faces (substring). Bare engine
# returns empty so kind targeting is game-owned.
_extra_target_match_needles = None

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

# Heal on-disk content catalogs (duplicate ids, ...) after an auto-deploy
# protect-restore. fn() -> {name: count}.
_boot_content_heal = None

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


def set_boot_content_heal(fn):
    """Register fn() -> {name: count} that heals on-disk content catalogs
    (duplicate ids, ...) after an auto-deploy protect-restore. Pass None
    to restore the no-op default (a bare engine install has no catalog
    files to heal).
    """
    global _boot_content_heal
    _boot_content_heal = fn


def boot_content_heal():
    """Run the registered content-heal hook and return its {name: count}
    stats dict, or {} if none is registered."""
    if _boot_content_heal is not None:
        return _boot_content_heal()
    return {}


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


def set_room_attacher(fn):
    """Register fn(room) called at the end of Room.__init__.

    Pass None to clear (lean engine Rooms only).
    """
    global _room_attacher
    _room_attacher = fn


def attach_room(room):
    """Run the registered room attacher, or do nothing if none is set."""
    if _room_attacher is not None:
        _room_attacher(room)


def set_map_room_stamper(fn):
    """Register fn(room, room_data, *, filename=None) for map JSON overrides.

    Called from ``engine.world_maps._add_room`` after engine-generic fields are stamped
    onto the Room. ``room_data`` is the hand-room dict or grid cell
    override (may be empty). Pass None to clear (lean engine / basegame
    ignore SUPERS-only keys).
    """
    global _map_room_stamper
    _map_room_stamper = fn


def stamp_map_room(room, room_data, *, filename=None):
    """Apply the registered map-JSON stamper, or do nothing if none is set."""
    if _map_room_stamper is not None:
        _map_room_stamper(room, room_data or {}, filename=filename)


# Map JSON loader hooks (two-repo purity H1a -- engine/world_maps.py).
_map_json_validator = None
_map_area_types = None
_map_room_city_stamper = None

# Lean / basegame default until a game registers its full vocabulary.
_DEFAULT_MAP_AREA_TYPES = {
    "ruins": [],
    "city": [],
    "city_street": [],
    "mountains": [],
    "ocean": [],
    "lake": [],
    "forest": [],
    "plains": [],
    "furnace": [],
}


def set_map_json_validator(validator):
    """Register map JSON validation helpers (require_keys, …).

    SUPERS registers ``content_validate`` at boot. When unset,
    ``engine/world_maps`` falls back to ``engine.content_validate``.
    Pass None to clear.
    """
    global _map_json_validator
    _map_json_validator = validator


def map_json_validator():
    """Return the registered map JSON validator module, or None."""
    return _map_json_validator


def set_map_area_types(area_types):
    """Register area_type vocabulary for map loader validation.

    Accepts a dict (area_type -> default bestiary_categories) or a
    ``frozenset`` of allowed keys (empty bestiary defaults). Pass None
    to restore engine defaults.
    """
    global _map_area_types
    if area_types is None:
        _map_area_types = None
    elif isinstance(area_types, frozenset):
        _map_area_types = {key: [] for key in area_types}
    else:
        _map_area_types = dict(area_types)


def map_area_types():
    """Return registered area_type dict (never None)."""
    if _map_area_types is not None:
        return _map_area_types
    return dict(_DEFAULT_MAP_AREA_TYPES)


def set_map_room_city_stamper(fn):
    """Register fn(room, map_data) for city_name / color header stamps.

    Called from ``engine.world_maps`` after hand rooms are created.
    Pass None to clear (lean engine ignores city header fields).
    """
    global _map_room_city_stamper
    _map_room_city_stamper = fn


def stamp_map_room_city_meta(room, data):
    """Apply the registered city-meta stamper, or do nothing if unset."""
    if _map_room_city_stamper is not None:
        _map_room_city_stamper(room, data or {})


# Enter-alias preference order (H1b -- engine/world_maps._best_player_enter_alias).
_map_enter_alias_pref = None


def set_map_enter_alias_preference(prefs):
    """Register the full ordered ``enter <alias>`` preference tuple used to
    pick one label for a hub's look footer / gossip line. Pass None to
    restore the generic engine default (``engine.world_maps
    ._LOOK_ENTER_ALIAS_PREF``).
    """
    global _map_enter_alias_pref
    _map_enter_alias_pref = tuple(prefs) if prefs else None


def map_enter_alias_preference():
    """Registered preference tuple, or None to use the engine default."""
    return _map_enter_alias_pref


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


def set_game_meta_codec(load_fn, save_fn):
    """Register Game meta load/save (SUPERS Tide, Cadence, tuning, …).

    load_fn(game, conn) -> None — mutate game from SQLite meta rows.
    save_fn(game, conn) -> None — write game fields into meta.

    Pass None, None to clear (lean engine / basegame keep defaults).
    """
    global _game_meta_loader, _game_meta_saver
    _game_meta_loader = load_fn
    _game_meta_saver = save_fn


def load_game_meta(game, conn):
    """Run the registered meta loader, or no-op if none is set."""
    if _game_meta_loader is not None:
        _game_meta_loader(game, conn)


def save_game_meta(game, conn):
    """Run the registered meta saver, or no-op if none is set."""
    if _game_meta_saver is not None:
        _game_meta_saver(game, conn)


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


def set_park_gm_spirit_on_disconnect(fn):
    """Register fn(spirit, game) to fold a permanent GM spirit on logout.

    Called from ``engine.connection`` when a staff Session disconnects while
    in ``gm on``. Pass None to clear.
    """
    global _park_gm_spirit_on_disconnect
    _park_gm_spirit_on_disconnect = fn


def park_gm_spirit_on_disconnect(spirit, game):
    """Fold a sessionless permanent GM spirit on disconnect, if registered."""
    if _park_gm_spirit_on_disconnect is not None:
        _park_gm_spirit_on_disconnect(spirit, game)


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


def set_room_look_virtual_exits(fn):
    """Register fn(room, character, game) -> list[(direction, label)].

    Used for exits that are not physical ``Room.exits`` links (Purgatory
    pit ``down`` after a floor boss dies). Pass None to clear.
    """
    global _room_look_virtual_exits
    _room_look_virtual_exits = fn


def room_look_virtual_exits(room, character, game):
    """Return virtual look exits for this viewer, or []."""
    if _room_look_virtual_exits is None:
        return []
    try:
        return list(
            _room_look_virtual_exits(room, character, game) or []
        )
    except TypeError:
        return list(_room_look_virtual_exits(room, character) or [])


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


def register_sheet_field(field_id, fn):
    """Register a ``hook:<field_id>`` row in ``engine/content/sheet_profile.json``."""
    from engine.systems import sheet as sheet_mod

    sheet_mod.register_field_hook(field_id, fn)


def register_sheet_contributor(section_id, fn, *, priority=100):
    """Register fn(ctx) -> SheetSection | list | None for score assembly."""
    from engine.systems import sheet as sheet_mod

    sheet_mod.register_contributor(section_id, fn, priority=priority)


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


def set_clinic_on_admit(fn):
    """Register fn(character, room, game, reason, attacker=None) for post-admit side effects."""
    global _clinic_on_admit
    _clinic_on_admit = fn


def clinic_on_admit(character, room, game, reason, attacker=None):
    """Run game hook after a successful clinic admit (side effects only)."""
    if _clinic_on_admit is not None:
        _clinic_on_admit(character, room, game, reason, attacker=attacker)


def set_clinic_on_discharge(fn):
    """Register fn(character, game) for post-discharge side effects."""
    global _clinic_on_discharge
    _clinic_on_discharge = fn


def clinic_on_discharge(character, game):
    """Run game hook after a clinic discharge (side effects only)."""
    if _clinic_on_discharge is not None:
        _clinic_on_discharge(character, game)


def set_clinic_casualty_meter(fn):
    """Register fn(character, game) for balance / casualty-meter notes."""
    global _clinic_casualty_meter
    _clinic_casualty_meter = fn


def clinic_note_casualty(character, game):
    """Note a clinic casualty when a game registers the meter hook."""
    if _clinic_casualty_meter is not None:
        _clinic_casualty_meter(character, game)


def set_clinic_ko_clear(fn):
    """Register fn(character, game) when KO clears without a normal admit."""
    global _clinic_ko_clear
    _clinic_ko_clear = fn


def clinic_ko_clear(character, game):
    """Run game hook after generic KO clear (not via admit)."""
    if _clinic_ko_clear is not None:
        _clinic_ko_clear(character, game)


def set_justice_on_robbery(fn):
    """Register fn(actor, game, amount) after a successful robbery."""
    global _justice_on_robbery
    _justice_on_robbery = fn


def justice_on_robbery(actor, game, amount):
    """Run game hook after robbery succeeds (telemetry / side effects)."""
    if _justice_on_robbery is not None:
        _justice_on_robbery(actor, game, amount)


def set_justice_fine_schedule(fn):
    """Register fn(offense_type) -> fine cents for default sentencing."""
    global _justice_fine_schedule
    _justice_fine_schedule = fn


def justice_fine_schedule(offense_type):
    """Return scheduled fine cents; engine default when no hook registered."""
    from engine.systems import justice as justice_mod
    if _justice_fine_schedule is not None:
        return int(_justice_fine_schedule(offense_type))
    return justice_mod.DEFAULT_FINE_CENTS


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


def set_room_broadcast_deliver(fn):
    """Register fn(watcher, room, game) -> bool for room.broadcast delivery.

    Return False to skip sending a line to ``watcher`` (God Mantle body
    while focused through a twin elsewhere, …). Default: deliver.
    """
    global _room_broadcast_deliver
    _room_broadcast_deliver = fn


def room_broadcast_deliver(watcher, room, game=None):
    """True when ``watcher`` should receive traffic from ``room``."""
    if _room_broadcast_deliver is not None:
        try:
            return bool(_room_broadcast_deliver(watcher, room, game))
        except Exception:
            return True
    return True


def set_room_broadcast_transform(fn):
    """Register fn(watcher, room, text, game) -> str for room.broadcast.

    Lets Gods tag dual-sense traffic before it reaches the Session.
    """
    global _room_broadcast_transform
    _room_broadcast_transform = fn


def room_broadcast_transform(watcher, room, text, game=None):
    """Return room line text after optional per-watcher transforms."""
    if _room_broadcast_transform is not None:
        try:
            return _room_broadcast_transform(watcher, room, text, game)
        except Exception:
            return text
    return text


def set_perception_character(fn):
    """Register fn(character, game) -> Character for prompt / located UI.

    Used when the login body is not the body the player perceives (God
    twin focus). Return the same character when unchanged.
    """
    global _perception_character
    _perception_character = fn


def perception_character(character, game=None):
    """Character whose room / exits the client UI should reflect."""
    if _perception_character is not None:
        try:
            perceived = _perception_character(character, game)
            if perceived is not None:
                return perceived
        except Exception:
            pass
    return character


def set_preference_character(fn):
    """Register fn(character, game) -> Character for client/display prefs.

    Used when bare verbs run through a God bilocate twin while ``config``
    and other prefs stay on the owning Mantle. Return the same character
    when unchanged.
    """
    global _preference_character
    _preference_character = fn


def preference_character(character, game=None):
    """Character whose client/display prefs apply to this session."""
    if _preference_character is not None:
        try:
            owner = _preference_character(character, game)
            if owner is not None:
                return owner
        except Exception:
            pass
    return character


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


def set_grant_relic_loot(fn):
    """Register fn(character, relic_id, tier=1) -> loot summary str."""
    global _grant_relic_loot
    _grant_relic_loot = fn


def grant_relic_loot(character, relic_id, tier=1):
    """Apply a relic strongbox payout, or None if no game hook."""
    if _grant_relic_loot is not None:
        return _grant_relic_loot(character, relic_id, tier=tier)
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


def set_before_open_container(fn):
    """Register fn(character, item, holder, game) -> bool before cmd_open pays.

    Return True when the handler consumed the open (e.g. pit mimic reveal).
    Pass None to restore the no-op default.
    """
    global _before_open_container
    _before_open_container = fn


def before_open_container(character, item, holder, game):
    """Run the registered pre-open hook; True means cmd_open should stop."""
    if _before_open_container is not None:
        return bool(_before_open_container(character, item, holder, game))
    return False


def set_after_growth_banked(fn):
    """Register fn(character, amount, source) after growth is banked.

    ``source`` is a short label (e.g. ``lockbox``). Pass None for no-op.
    """
    global _after_growth_banked
    _after_growth_banked = fn


def after_growth_banked(character, amount, source="unknown"):
    """Notify the game that banked growth was applied."""
    if _after_growth_banked is not None:
        _after_growth_banked(character, amount, source)


# After a character acquires an Item into inventory (get / open / loot).
# Games may auto-stow reagents into a gear bag. fn(character, item) ->
# optional player message str, or None.
_after_acquire_item = None
_before_acquire_item = None


def set_before_acquire_item(fn):
    """Register fn(character, item) -> str|None refusal before inventory add."""
    global _before_acquire_item
    _before_acquire_item = fn


def before_acquire_item(character, item):
    """Run pre-acquire hook; return refusal message or None if allowed."""
    if _before_acquire_item is not None:
        return _before_acquire_item(character, item)
    return None


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


def set_can_see_hellhound(fn):
    """Register fn(viewer, hound) -> bool for Deal hellhound invis pierce.

    Pass None to restore the default: nobody pierces hellhound invis.
    """
    global _can_see_hellhound
    _can_see_hellhound = fn


def can_see_hellhound(viewer, hound=None):
    """Can `viewer` see a Deal hellhound (or other hellhound_invisible)?

    Default (no game installed): False. SUPERS registers Celestial /
    glasses / engage checks from ``supers.hellhounds``.
    """
    if _can_see_hellhound is not None:
        return bool(_can_see_hellhound(viewer, hound))
    return False


def set_in_veil(fn):
    """Register fn(character) -> bool for the Veil-layer membership check.

    Pass None to restore the default: nobody is Veil-layer.
    """
    global _in_veil
    _in_veil = fn


def in_veil(character):
    """Does `character` operate on the Veil layer rather than Prime?

    Default (no game installed): False. SUPERS registers the real check
    (death spirits, faded Ghosts, veiled Reapers, vessel-free Mantle walks)
    from ``supers.veil``.
    """
    if _in_veil is not None:
        return bool(_in_veil(character))
    return False


def set_veil_visible_to(fn):
    """Register fn(viewer, other) -> bool for Veil-layer sight pierce.

    Pass None to restore the default: nobody pierces the Veil.
    """
    global _veil_visible_to
    _veil_visible_to = fn


def veil_visible_to(viewer, other):
    """Can `viewer` see `other` while `other` is on the Veil layer?

    Only meaningful when ``in_veil(other)`` is True. Default (no game
    installed): False.
    """
    if _veil_visible_to is not None:
        return bool(_veil_visible_to(viewer, other))
    return False


def set_veil_look_tag(fn):
    """Register fn() -> str for the Veil look-line tag prefix.

    Pass None to restore the default: empty string.
    """
    global _veil_look_tag
    _veil_look_tag = fn


def veil_look_tag():
    """Plain-text tag prefix for a Veil-layer look line (a11y: never
    color-only). Default (no game installed): "".
    """
    if _veil_look_tag is not None:
        return _veil_look_tag()
    return ""


def set_can_notice_stealth(fn):
    """Register fn(viewer, other, game=None) -> bool for hide/sneak pierce."""
    global _can_notice_stealth
    _can_notice_stealth = fn


def can_notice_stealth(viewer, other, game=None):
    """Can `viewer` spot ``other`` while ``other`` is in mundane stealth?"""
    if viewer is other:
        return True
    if _can_notice_stealth is not None:
        return bool(_can_notice_stealth(viewer, other, game))
    return True


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


def set_extra_target_match_needles(fn):
    """Register fn(viewer, subject) -> iterable[str] for kind/race targeting.

    SUPERS uses this so ``kill arachne`` / ``stake vampire`` resolve when
    the kit is publicly obvious or the viewer has logged recognition.
    Pass None to clear (bare engine: no extra needles).
    """
    global _extra_target_match_needles
    _extra_target_match_needles = fn


def extra_target_match_needles(viewer, subject):
    """Lowercase-ready kind/race aliases for room targeting, or []."""
    if _extra_target_match_needles is None:
        return []
    try:
        raw = _extra_target_match_needles(viewer, subject)
    except Exception:
        return []
    if not raw:
        return []
    out = []
    for item in raw:
        text = str(item or "").strip().lower()
        if text and text not in out:
            out.append(text)
    return out


def set_room_presence_line(fn):
    """Register fn(face_label, character, room, game, *, viewer=None) -> str for look souls.

    SUPERS uses this for positional state (``is standing here``, ``[KO] …
    is unconscious here``). Pass None to restore the lean engine default.
    """
    global _room_presence_line
    _room_presence_line = fn


def room_presence_line(face_label, character, room, game=None, *, viewer=None):
    """Full room-presence line for one Character, or a bare label fallback."""
    if _room_presence_line is not None:
        return _room_presence_line(
            face_label, character, room, game, viewer=viewer,
        )
    label = (face_label or "?").strip()
    if getattr(character, "asleep", False):
        return f"{label} is sleeping here"
    return f"{label} is standing here"


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


# Boarded-vehicle catalog loaders and park-spot gates (engine/systems/vehicles.py).
_vehicle_catalog_loader = None
_travel_hub_catalog_loader = None
_vehicle_catalog_extra_validator = None
_vehicle_park_spot_extra_gate = None


def register_vehicle_catalog(loader_fn):
    """Register fn() -> {vehicle_id: spec_dict} for ensure_game_vehicles.

  Pass None to clear. When unset, ensure_game_vehicles uses an empty catalog.
    """
    global _vehicle_catalog_loader
    _vehicle_catalog_loader = loader_fn


def vehicle_catalog_loader():
    """Return the registered vehicle catalog loader, or None."""
    return _vehicle_catalog_loader


def register_travel_hub_catalog(loader_fn):
    """Register fn() -> {hub_id: hub_dict} for ensure_game_vehicles.

  Pass None to clear. When unset, travel_hubs defaults to {}.
    """
    global _travel_hub_catalog_loader
    _travel_hub_catalog_loader = loader_fn


def travel_hub_catalog_loader():
    """Return the registered travel-hub catalog loader, or None."""
    return _travel_hub_catalog_loader


def set_vehicle_catalog_extra_validator(fn):
    """Register fn(vehicle_id, spec, *, where) for extra catalog row checks.

  Games layer IMPALA-style required keys here. Pass None to clear.
    """
    global _vehicle_catalog_extra_validator
    _vehicle_catalog_extra_validator = fn


def vehicle_catalog_extra_validator(vehicle_id, spec, *, where=None):
    """Run game-registered extra vehicle/hub validation after engine defaults."""
    if _vehicle_catalog_extra_validator is not None:
        _vehicle_catalog_extra_validator(vehicle_id, spec, where=where)


def set_vehicle_park_spot_extra_gate(fn):
    """Register fn(room, game, character) -> bool; True means park blocked.

  SUPERS registers evil-ward / driveability gates here. Pass None to clear.
    """
    global _vehicle_park_spot_extra_gate
    _vehicle_park_spot_extra_gate = fn


def vehicle_park_spot_blocked_extra(room, game, character):
    """True when the game hook blocks parking in ``room``."""
    if _vehicle_park_spot_extra_gate is not None:
        return bool(_vehicle_park_spot_extra_gate(room, game, character))
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
_inventory_item_match_rank = None

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


# Optional regional-weather game hooks (SUPERS registers; basegame uses defaults).
_weather_is_elemental_realm = None
_weather_room_plane = None
_weather_clinic_admit = None
_weather_radio_bulletin = None


def set_weather_is_elemental_realm(fn):
    """Register fn(room) -> bool for non-CONUS Reach-style planes."""
    global _weather_is_elemental_realm
    _weather_is_elemental_realm = fn


def weather_is_elemental_realm(room):
    """True when CONUS weather should not run for this room."""
    if _weather_is_elemental_realm is not None:
        return bool(_weather_is_elemental_realm(room))
    return False


def set_weather_room_plane(fn):
    """Register fn(room) -> plane id string (earth, fire, …)."""
    global _weather_room_plane
    _weather_room_plane = fn


def weather_room_plane(room):
    """Return the room's plane id for weather flavor routing."""
    if _weather_room_plane is not None:
        return _weather_room_plane(room)
    if room is None:
        return None
    return getattr(room, "plane", None) or "earth"


def set_weather_clinic_admit(fn):
    """Register fn(game, character, reason=…) -> bool after tornado drop."""
    global _weather_clinic_admit
    _weather_clinic_admit = fn


def weather_clinic_admit(game, character, reason="injury"):
    """Admit an injured character to a clinic; return True if handled."""
    if _weather_clinic_admit is not None:
        return bool(_weather_clinic_admit(game, character, reason=reason))
    if game is None or character is None:
        return False
    rooms = getattr(game, "rooms", None) or {}
    for room in rooms.values():
        if getattr(room, "hospital", False):
            character.move_to(room)
            character.hp = max(1, int(getattr(character, "hp", 0) or 0))
            character.hospitalized = True
            return True
    return False


def set_weather_radio_bulletin(fn):
    """Register fn(game, line) for scheduled WX radio interrupts."""
    global _weather_radio_bulletin
    _weather_radio_bulletin = fn


def weather_radio_bulletin(game, line):
    """Optional in-game radio bulletin hook; no-op when unset."""
    if _weather_radio_bulletin is not None:
        _weather_radio_bulletin(game, line)


_storm_chase_is_on_duty = None


def set_storm_chase_is_on_duty(fn):
    """Register fn(character, game) -> bool for desk duty gate."""
    global _storm_chase_is_on_duty
    _storm_chase_is_on_duty = fn


def storm_chase_is_on_duty(character, game=None):
    """True when character may run on-duty storm desk verbs."""
    if _storm_chase_is_on_duty is not None:
        return bool(_storm_chase_is_on_duty(character, game=game))
    return bool(getattr(character, "on_duty", False))


_press_beat_is_reporter = None
_press_beat_is_on_duty = None
_press_beat_room_excitement = None
_press_beat_interview_line = None


def set_press_beat_is_reporter(fn):
    """Register fn(character, game) -> bool for Reporter path / job gate."""
    global _press_beat_is_reporter
    _press_beat_is_reporter = fn


def press_beat_is_reporter(character, game=None):
    """True when character has Reporter path or equivalent."""
    if _press_beat_is_reporter is not None:
        return bool(_press_beat_is_reporter(character, game=game))
    path = (getattr(character, "bg_path", None) or "").strip().lower()
    return path == "reporter"


def set_press_beat_is_on_duty(fn):
    """Register fn(character, game) -> bool for Gazette desk duty gate."""
    global _press_beat_is_on_duty
    _press_beat_is_on_duty = fn


def press_beat_is_on_duty(character, game=None):
    """True when character may run on-duty Gazette desk verbs."""
    if _press_beat_is_on_duty is not None:
        return bool(_press_beat_is_on_duty(character, game=game))
    return bool(getattr(character, "on_duty", False))


def set_press_beat_room_excitement(fn):
    """Register fn(room, game) -> (score, label) | dict | None."""
    global _press_beat_room_excitement
    _press_beat_room_excitement = fn


def press_beat_room_excitement(room, game):
    """Optional hook: return excitement override or None for engine default."""
    if _press_beat_room_excitement is not None:
        return _press_beat_room_excitement(room, game)
    return None


def set_press_beat_interview_line(fn):
    """Register fn(character, target, game) -> str for interview flavor."""
    global _press_beat_interview_line
    _press_beat_interview_line = fn


def press_beat_interview_line(character, target, game):
    """Optional hook: custom interview quote line."""
    if _press_beat_interview_line is not None:
        return _press_beat_interview_line(character, target, game)
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


def set_inventory_item_match_rank(fn):
    """Register fn(character, item) -> int for duplicate inventory picks.

    Lower rank sorts earlier when ``_find_item`` has several substring hits
    and no ordinal (e.g. equipped vs carried duplicate names). Pass None to
    restore the flat default (inventory order only).
    """
    global _inventory_item_match_rank
    _inventory_item_match_rank = fn


def inventory_item_match_rank(character, item):
    """Sort key for ambiguous inventory item matches; 0 = default."""
    if _inventory_item_match_rank is not None:
        return _inventory_item_match_rank(character, item)
    return 0


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


# Optional overland peers (engine/systems/overland.py -- no importlib supers).
_overland_starter_keys = None
_overland_room_influenced = None
_overland_queue_vehicle_macro_move = None
_overland_notify_dungeon_hub = None
_overland_cadence_travel_toward = None
_overland_solar_land_all_the_way = None


def set_overland_starter_keys(fn):
    """Register fn() -> (plaza_key, hub_key, bunker_pad_key)."""
    global _overland_starter_keys
    _overland_starter_keys = fn


def overland_starter_keys():
    """Starter-town keys from the game hook, or None when unset."""
    if _overland_starter_keys is not None:
        return _overland_starter_keys()
    return None


def set_overland_room_influenced(fn):
    """Register fn(room, game) -> bool (skip virtual-room prune when True)."""
    global _overland_room_influenced
    _overland_room_influenced = fn


def overland_room_influenced(room, game=None):
    if _overland_room_influenced is not None:
        return bool(_overland_room_influenced(room, game))
    return False


def set_overland_queue_vehicle_macro_move(fn):
    """Register fn(character, direction, game) -> bool."""
    global _overland_queue_vehicle_macro_move
    _overland_queue_vehicle_macro_move = fn


def overland_queue_vehicle_macro_move(character, direction, game):
    if _overland_queue_vehicle_macro_move is not None:
        return bool(
            _overland_queue_vehicle_macro_move(character, direction, game),
        )
    return False


def set_overland_notify_dungeon_hub(fn):
    """Register fn(character, game, hub_room) after entering a dungeon hub."""
    global _overland_notify_dungeon_hub
    _overland_notify_dungeon_hub = fn


def overland_notify_dungeon_hub(character, game, hub_room):
    if _overland_notify_dungeon_hub is not None:
        _overland_notify_dungeon_hub(character, game, hub_room)


def set_overland_cadence_travel_toward(fn):
    """Register fn(game, character, dest_room_key) -> bool."""
    global _overland_cadence_travel_toward
    _overland_cadence_travel_toward = fn


def overland_cadence_travel_toward(game, character, dest_room_key):
    if _overland_cadence_travel_toward is not None:
        return bool(
            _overland_cadence_travel_toward(game, character, dest_room_key),
        )
    return False


def set_overland_solar_land_all_the_way(fn):
    """Register fn(character, game) to land a flying Solar character."""
    global _overland_solar_land_all_the_way
    _overland_solar_land_all_the_way = fn


def overland_solar_land_all_the_way(character, game):
    if _overland_solar_land_all_the_way is not None:
        _overland_solar_land_all_the_way(character, game)


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


# Auto-deploy / boot map heal (supers.map_heal.heal_all_from_hot_backups)
# after git reset --hard + protect restore, and again before build_world so
# copyover picks up populate rooms that only survived in map_backups.
# fn(root) -> list[str] log lines.
_auto_deploy_map_heal = None


def set_auto_deploy_map_heal(fn):
    """Register fn(root) -> list[str] for post-reset map backup merge.

    Pass None to restore the no-op default (bare engine has no map_backups).
    """
    global _auto_deploy_map_heal
    _auto_deploy_map_heal = fn


def ensure_auto_deploy_map_heal(*, reload_impl=False):
    """Late-bind engine map heal when bootstrap never ran (watcher).

    ``watch_and_run.reload_auto_deploy`` reloads this module every deploy
    poll, which resets ``_auto_deploy_map_heal`` to None. The game child
    re-registers via bootstrap; the long-lived watcher never runs bootstrap.
    Re-bind ``engine.map_heal`` here so post-reset heal merges hot backups.

    Returns True when a heal callback is bound.
    """
    global _auto_deploy_map_heal
    try:
        from engine import map_heal as map_heal_mod
        if reload_impl:
            import importlib
            map_heal_mod = importlib.reload(map_heal_mod)
        _auto_deploy_map_heal = map_heal_mod.heal_all_from_hot_backups
        return True
    except ImportError:
        return False


def auto_deploy_map_heal(root):
    """Merge protected map_backups into live JSON after deploy reset / boot.

    Always late-binds when the callback is missing so the Docker watcher
    (which reloads hooks and never runs bootstrap) still heals after
    ``reset --hard``.
    """
    if _auto_deploy_map_heal is None:
        ensure_auto_deploy_map_heal(reload_impl=True)
    if _auto_deploy_map_heal is not None:
        return _auto_deploy_map_heal(root)
    return []


# --- Two-repo purity hooks (engine never imports supers) -------------------
# Games register real implementations in supers/bootstrap.py register_all_hooks.

_is_gm_spirit = None
_resolve_gm_body = None
_stamp_input_activity = None
_blob_codec_reload = None
_taxi_mode_saver = None
_map_snapshot_write_all = None
_map_snapshot_daily_archive = None

# H6 map_store: rset_field bool/text catalogs + populate helpers.
_rset_bool_flags = frozenset()
_rset_text_fields = frozenset()
_rset_reference_lines_fn = None
_map_store_apply_entry_fields = None
_map_store_place_seed_items = None

_containers_resolve_loot_bag = None
_containers_find_in_loot_bag = None
_containers_unstow_from_loot_bag = None
_containers_unstow_all_from_loot_bag = None

_after_floor_drop = None

_say_strip_tone_prefix = None
_say_drunk_meter = None
_say_slur_text = None
_say_drunk_tag = None
_say_maybe_stumble_tell = None
_deliver_say = None

_autoloot_is_combat_zone = None
_autosplit_wallet_cash = None
_autosplit_distribute_items = None
_autosplit_is_splitable_item = None

_config_handlers = {}

_on_hidden_exit_revealed = None

_on_virtual_room_created = None
_blocked_foot_step = None

_character_atmos_tick = None
_utility_delay_begin = None


def set_is_gm_spirit(fn):
    """Register fn(character) -> bool for GM staff spirits."""
    global _is_gm_spirit
    _is_gm_spirit = fn


def is_gm_spirit(character):
    """True when character is a GM staff spirit (not a playable body)."""
    if _is_gm_spirit is not None:
        return _is_gm_spirit(character)
    return False


def set_resolve_gm_body(fn):
    """Register fn(spirit, game) -> Character | None for gm off / logout."""
    global _resolve_gm_body
    _resolve_gm_body = fn


def resolve_gm_body(spirit, game=None):
    """Return the Cadence Echo body linked to a GM spirit, or None."""
    if _resolve_gm_body is not None:
        return _resolve_gm_body(spirit, game)
    return None


def set_stamp_input_activity(fn):
    """Register fn(character, game) to stamp AFK / autoidle input time."""
    global _stamp_input_activity
    _stamp_input_activity = fn


def stamp_input_activity(character, game):
    """Stamp last player input for autoidle (monotonic fallback when unset)."""
    if _stamp_input_activity is not None:
        _stamp_input_activity(character, game)
        return
    import time
    character.last_input_monotonic = time.monotonic()


def set_blob_codec_reload(fn):
    """Register fn() -> None to reload persist blob + re-register codec."""
    global _blob_codec_reload
    _blob_codec_reload = fn


def reload_blob_codec():
    """Reload game blob codec from disk before copyover snapshot."""
    if _blob_codec_reload is not None:
        _blob_codec_reload()


def set_taxi_mode_saver(fn):
    """Register fn(conn, game) -> None to persist game.taxi_mode meta."""
    global _taxi_mode_saver
    _taxi_mode_saver = fn


def save_taxi_mode_meta(conn, game):
    """Persist taxi pacing mode when a game registered a saver."""
    if _taxi_mode_saver is not None:
        _taxi_mode_saver(conn, game)


def set_map_snapshot_hooks(write_all=None, daily_archive=None):
    """Register world-backup snapshot hooks (write_all, daily_archive)."""
    global _map_snapshot_write_all, _map_snapshot_daily_archive
    _map_snapshot_write_all = write_all
    _map_snapshot_daily_archive = daily_archive


def write_map_backup_all(root=None):
    """Staff snapshot all maps/zones (no-op without a registered hook)."""
    if _map_snapshot_write_all is not None:
        _map_snapshot_write_all(root=root)


def write_map_daily_archive(root=None, confirmed_by=""):
    """Daily map archive line (None when unset or nothing archived)."""
    if _map_snapshot_daily_archive is not None:
        return _map_snapshot_daily_archive(
            root=root, confirmed_by=confirmed_by,
        )
    return None


def set_rset_flag_catalog(bool_flags, text_fields):
    """Register (frozenset, frozenset) of field names the game wants
    bool/text-coerced by rset_field; anything else falls back to naive
    inference (try bool 'true'/'false' literal, else keep as string)."""
    global _rset_bool_flags, _rset_text_fields
    _rset_bool_flags = frozenset(bool_flags or ())
    _rset_text_fields = frozenset(text_fields or ())


def rset_flag_catalog():
    """Return the registered (bool_flags, text_fields) tuple."""
    return _rset_bool_flags, _rset_text_fields


def set_rset_reference_lines(fn):
    """Register fn() -> list[str] for bare ``room rset`` / help body."""
    global _rset_reference_lines_fn
    _rset_reference_lines_fn = fn


def rset_reference_lines():
    """Return registered rset reference lines, or a minimal default."""
    if _rset_reference_lines_fn is not None:
        return list(_rset_reference_lines_fn())
    return ["Usage: room rset <field|flag> <value…>"]


def set_map_store_apply_entry_fields(fn):
    """Register fn(live, entry) to stamp JSON rooms[] onto a live Room."""
    global _map_store_apply_entry_fields
    _map_store_apply_entry_fields = fn


def map_store_apply_entry_fields(live, entry):
    """Stamp authored rooms[] fields onto a live Room when registered."""
    if _map_store_apply_entry_fields is not None:
        _map_store_apply_entry_fields(live, entry)


def set_map_store_place_seed_items(fn):
    """Register fn(game, room_key, seed_specs, where=...) -> int placed."""
    global _map_store_place_seed_items
    _map_store_place_seed_items = fn


def map_store_place_seed_items(game, room_key, seed_specs, *, where):
    """Place seed items when a game registered a catalog hook."""
    if _map_store_place_seed_items is not None:
        return _map_store_place_seed_items(
            game, room_key, seed_specs, where=where,
        )
    return 0


def set_containers_resolve_loot_bag(fn):
    global _containers_resolve_loot_bag
    _containers_resolve_loot_bag = fn


def containers_resolve_loot_bag(character, query):
    if _containers_resolve_loot_bag is not None:
        return _containers_resolve_loot_bag(character, query)
    return None


def set_containers_find_in_loot_bag(fn):
    global _containers_find_in_loot_bag
    _containers_find_in_loot_bag = fn


def containers_find_in_loot_bag(character, needle, loot_bag=None):
    if _containers_find_in_loot_bag is not None:
        return _containers_find_in_loot_bag(
            character, needle, loot_bag=loot_bag,
        )
    return None


def set_containers_unstow_from_loot_bag(fn):
    global _containers_unstow_from_loot_bag
    _containers_unstow_from_loot_bag = fn


def containers_unstow_from_loot_bag(character, item, loot_bag=None):
    if _containers_unstow_from_loot_bag is not None:
        return _containers_unstow_from_loot_bag(
            character, item, loot_bag=loot_bag,
        )
    return False, "You don't have a loot bag."


def set_containers_unstow_all_from_loot_bag(fn):
    global _containers_unstow_all_from_loot_bag
    _containers_unstow_all_from_loot_bag = fn


def containers_unstow_all_from_loot_bag(character, loot_bag=None):
    if _containers_unstow_all_from_loot_bag is not None:
        return _containers_unstow_all_from_loot_bag(
            character, loot_bag=loot_bag,
        )
    return False, "You don't have a loot bag."


def set_after_floor_drop(fn):
    global _after_floor_drop
    _after_floor_drop = fn


def after_floor_drop(game, item):
    if _after_floor_drop is not None:
        _after_floor_drop(game, item)


def set_say_strip_tone_prefix(fn):
    global _say_strip_tone_prefix
    _say_strip_tone_prefix = fn


def say_strip_tone_prefix(message):
    if _say_strip_tone_prefix is not None:
        return _say_strip_tone_prefix(message)
    return None, message


def set_say_drunk_meter(fn):
    global _say_drunk_meter
    _say_drunk_meter = fn


def say_drunk_meter(character):
    if _say_drunk_meter is not None:
        return _say_drunk_meter(character)
    return 0.0


def set_say_slur_text(fn):
    global _say_slur_text
    _say_slur_text = fn


def say_slur_text(text, level):
    if _say_slur_text is not None:
        return _say_slur_text(text, level)
    return text


def set_say_drunk_tag(fn):
    global _say_drunk_tag
    _say_drunk_tag = fn


def say_drunk_tag(level):
    if _say_drunk_tag is not None:
        return _say_drunk_tag(level)
    return ""


def set_say_maybe_stumble_tell(fn):
    global _say_maybe_stumble_tell
    _say_maybe_stumble_tell = fn


def say_maybe_stumble_tell(character, level):
    if _say_maybe_stumble_tell is not None:
        return _say_maybe_stumble_tell(character, level)
    return None


def set_deliver_say(fn):
    global _deliver_say
    _deliver_say = fn


def deliver_say(character, spoken, game, **kwargs):
    """Broadcast say text; lean default is a simple room broadcast."""
    if _deliver_say is not None:
        return _deliver_say(character, spoken, game, **kwargs)
    room = kwargs.get("speak_room") or character.location
    you_verb = kwargs.get("you_verb") or "say"
    they_verb = kwargs.get("they_verb") or "says"
    tone = kwargs.get("tone")
    drunk_tag = kwargs.get("drunk_tag") or ""
    from engine.command_support import _display_name, _presence_face
    face = _presence_face(character)
    tag = f" {drunk_tag}" if drunk_tag else ""
    tone_bit = f" {tone}" if tone else ""
    character.session.send(
        f"You {you_verb},{tone_bit} \"{spoken}\"{tag}"
    )
    if room is not None:
        room.broadcast(
            f'{_display_name(character)} {they_verb},{tone_bit} "{spoken}"{tag}',
            exclude=character,
        )


def set_autoloot_is_combat_zone(fn):
    global _autoloot_is_combat_zone
    _autoloot_is_combat_zone = fn


def autoloot_is_combat_zone(room, game):
    if _autoloot_is_combat_zone is not None:
        return _autoloot_is_combat_zone(room, game)
    return False


def set_autosplit_wallet_cash(fn):
    global _autosplit_wallet_cash
    _autosplit_wallet_cash = fn


def autosplit_wallet_cash(game, character, defender, amount):
    if _autosplit_wallet_cash is not None:
        return _autosplit_wallet_cash(game, character, defender, amount)
    return []


def set_autosplit_distribute_items(fn):
    global _autosplit_distribute_items
    _autosplit_distribute_items = fn


def autosplit_distribute_items(game, character, defender, items):
    if _autosplit_distribute_items is not None:
        return _autosplit_distribute_items(
            game, character, defender, items,
        )
    return []


def set_autosplit_is_splitable_item(fn):
    global _autosplit_is_splitable_item
    _autosplit_is_splitable_item = fn


def autosplit_is_splitable_item(item):
    if _autosplit_is_splitable_item is not None:
        return _autosplit_is_splitable_item(item)
    return False


def set_config_handler(key, fn):
    """Register cmd_config handler for one pref key (autokill, autoloot, …)."""
    if fn is None:
        _config_handlers.pop(key, None)
    else:
        _config_handlers[key] = fn


def set_config_handlers(mapping):
    """Replace the full config-handler map (key -> handler)."""
    global _config_handlers
    _config_handlers = dict(mapping or {})


def config_handler(key):
    """Return registered cmd_config handler for key, or None."""
    return _config_handlers.get(key)


def set_on_hidden_exit_revealed(fn):
    global _on_hidden_exit_revealed
    _on_hidden_exit_revealed = fn


def on_hidden_exit_revealed(character, room):
    if _on_hidden_exit_revealed is not None:
        _on_hidden_exit_revealed(character, room)


def set_on_virtual_room_created(fn):
    global _on_virtual_room_created
    _on_virtual_room_created = fn


def on_virtual_room_created(game, room):
    if _on_virtual_room_created is not None:
        _on_virtual_room_created(game, room)


def set_blocked_foot_step(fn):
    global _blocked_foot_step
    _blocked_foot_step = fn


def blocked_foot_step(game, macro, micro):
    if _blocked_foot_step is not None:
        return _blocked_foot_step(game, macro, micro)
    return None


def set_character_atmos_tick(fn):
    global _character_atmos_tick
    _character_atmos_tick = fn


def character_atmos_tick(character):
    if _character_atmos_tick is not None:
        _character_atmos_tick(character)


def set_utility_delay_begin(fn):
    global _utility_delay_begin
    _utility_delay_begin = fn


def utility_delay_begin(character, game, action_key):
    if _utility_delay_begin is not None:
        return _utility_delay_begin(character, game, action_key)
    return None


# --- Peeled framework peers (two-repo Track 2) -----------------------------

_item_catalog_get = None
_weapon_grip_for_fn = None

_map_restore_hot_reload = None

_floor_loot_artifact_exclude_ids = None
_lost_item_vault_room_key = None
_orphan_item_room_for_game = None

_containers_is_gear_item = None
_containers_ensure_gear_bag = None
_containers_on_body_carry_refusal = None
_containers_item_worn_on_body = None
_containers_surface_inventory_items = None
_containers_stacked_carry_lines = None
_containers_gear_acquire_refusal = None
_containers_relic_acquire_refusal = None
_containers_room_is_character_home = None
_containers_heal_folded_kit_bags = None


def set_item_catalog_get(fn):
    global _item_catalog_get
    _item_catalog_get = fn


def get_item_spec(catalog_id):
    if _item_catalog_get is not None:
        return _item_catalog_get(catalog_id)
    return None


def set_weapon_grip_for(fn):
    global _weapon_grip_for_fn
    _weapon_grip_for_fn = fn


def weapon_grip_for(item):
    if _weapon_grip_for_fn is not None:
        return _weapon_grip_for_fn(item)
    return None


def set_map_restore_hot_reload(fn):
    global _map_restore_hot_reload
    _map_restore_hot_reload = fn


def map_restore_hot_reload(game, map_id):
    if _map_restore_hot_reload is not None:
        return _map_restore_hot_reload(game, map_id)
    return None


def set_floor_loot_artifact_exclude_ids(fn):
    global _floor_loot_artifact_exclude_ids
    _floor_loot_artifact_exclude_ids = fn


def floor_loot_artifact_exclude_ids():
    if _floor_loot_artifact_exclude_ids is not None:
        return _floor_loot_artifact_exclude_ids()
    return frozenset()


def set_lost_item_vault_room_key(key):
    global _lost_item_vault_room_key
    _lost_item_vault_room_key = key


def lost_item_vault_room_key():
    return _lost_item_vault_room_key or ""


def set_orphan_item_room_for_game(fn):
    global _orphan_item_room_for_game
    _orphan_item_room_for_game = fn


def orphan_item_room_for_game(game):
    if _orphan_item_room_for_game is not None:
        return _orphan_item_room_for_game(game)
    return None


def set_containers_is_gear_item(fn):
    global _containers_is_gear_item
    _containers_is_gear_item = fn


def containers_is_gear_item(item):
    if _containers_is_gear_item is not None:
        return _containers_is_gear_item(item)
    return False


def set_containers_ensure_gear_bag(fn):
    global _containers_ensure_gear_bag
    _containers_ensure_gear_bag = fn


def containers_ensure_gear_bag(character):
    if _containers_ensure_gear_bag is not None:
        return _containers_ensure_gear_bag(character)
    return []


def set_containers_on_body_carry_refusal(fn):
    global _containers_on_body_carry_refusal
    _containers_on_body_carry_refusal = fn


def containers_on_body_carry_refusal(character, item):
    if _containers_on_body_carry_refusal is not None:
        return _containers_on_body_carry_refusal(character, item)
    return None


def set_containers_item_worn_on_body(fn):
    global _containers_item_worn_on_body
    _containers_item_worn_on_body = fn


def containers_item_worn_on_body(character, item):
    if _containers_item_worn_on_body is not None:
        return _containers_item_worn_on_body(character, item)
    return False


def set_containers_surface_inventory_items(fn):
    global _containers_surface_inventory_items
    _containers_surface_inventory_items = fn


def containers_surface_inventory_items(character):
    if _containers_surface_inventory_items is not None:
        return _containers_surface_inventory_items(character)
    inv = getattr(character, "inventory", None) or []
    return list(inv)


def set_containers_stacked_carry_lines(fn):
    global _containers_stacked_carry_lines
    _containers_stacked_carry_lines = fn


def containers_stacked_carry_lines(items, character):
    if _containers_stacked_carry_lines is not None:
        return _containers_stacked_carry_lines(items, character)
    return [str(x) for x in items or []]


def set_containers_gear_acquire_refusal(fn):
    global _containers_gear_acquire_refusal
    _containers_gear_acquire_refusal = fn


def containers_gear_acquire_refusal(character, item):
    if _containers_gear_acquire_refusal is not None:
        return _containers_gear_acquire_refusal(character, item)
    return None


def set_containers_relic_acquire_refusal(fn):
    global _containers_relic_acquire_refusal
    _containers_relic_acquire_refusal = fn


def containers_relic_acquire_refusal(character, item):
    if _containers_relic_acquire_refusal is not None:
        return _containers_relic_acquire_refusal(character, item)
    return None


def set_containers_room_is_character_home(fn):
    global _containers_room_is_character_home
    _containers_room_is_character_home = fn


def containers_room_is_character_home(character, room, game):
    if _containers_room_is_character_home is not None:
        return _containers_room_is_character_home(character, room, game)
    return False


def set_containers_heal_folded_kit_bags(fn):
    global _containers_heal_folded_kit_bags
    _containers_heal_folded_kit_bags = fn


def containers_heal_folded_kit_bags(game):
    if _containers_heal_folded_kit_bags is not None:
        return _containers_heal_folded_kit_bags(game)
    return 0


# Content kind profiles (OLC / content_new / Area Studio) — game registers
# profile dirs, domain validators, and catalog save paths.
_content_kinds_dirs: list[str] = []
_content_kind_domain_validate = None
_content_kind_save_entity = None
_olc_authorizer = None


def set_content_kinds_dirs(dirs):
    """Register one or more directories of *.json kind profiles.

    Call before listing kinds or validating entities. Later registrations
    replace the list and invalidate the profile cache.
    """
    global _content_kinds_dirs
    _content_kinds_dirs = [str(d) for d in (dirs or [])]
    try:
        from engine.content_kinds.engine import _clear_profiles_for_tests
        _clear_profiles_for_tests()
    except ImportError:
        pass


def content_kinds_dirs():
    """Absolute paths to kind-profile JSON directories."""
    return list(_content_kinds_dirs)


def set_content_kind_domain_validator(fn):
    """Register fn(kind_id, obj, *, where) for boot-aligned domain checks.

    Pass None to skip domain validation (bare engine / lint-only profiles).
    """
    global _content_kind_domain_validate
    _content_kind_domain_validate = fn


def content_kind_domain_validate(kind_id, obj, *, where=None):
    """Run game-registered domain validation after profile checks."""
    if _content_kind_domain_validate is not None:
        _content_kind_domain_validate(kind_id, obj, where=where)


def set_content_kind_save_entity(fn):
    """Register fn(kind_id, entity_id, obj, **kwargs) -> str save message."""
    global _content_kind_save_entity
    _content_kind_save_entity = fn


def content_kind_save_entity(kind_id, entity_id, obj, **kwargs):
    """Persist a validated entity through the game hook."""
    if _content_kind_save_entity is None:
        raise RuntimeError(
            "content_kind_save_entity hook not registered "
            "(game must call set_content_kind_save_entity at boot)"
        )
    return _content_kind_save_entity(kind_id, entity_id, obj, **kwargs)


# Pluggable calendar (Gregorian default; games may swap at boot).


def set_calendar_provider(provider):
    """Register the active CalendarProvider (see engine/calendar_provider.py)."""
    from engine import game_calendar

    game_calendar.set_calendar_provider(provider)


def get_calendar_provider():
    """Return the active calendar provider (Gregorian when unset)."""
    from engine import game_calendar

    return game_calendar.get_calendar_provider()


def set_olc_authorizer(fn):
    """Register fn(character) -> bool for in-game OLC access."""
    global _olc_authorizer
    _olc_authorizer = fn


def olc_authorizer(character):
    """True when character may use menu OLC wizards."""
    if _olc_authorizer is not None:
        return bool(_olc_authorizer(character))
    return False


# --- Lodging hooks (H3a) ---------------------------------------------------


def set_lodging_are_family(fn):
    """Register fn(a, b) -> bool for bed-sharing (lover / family)."""
    global _lodging_are_family
    _lodging_are_family = fn


def lodging_are_family(a, b):
    """True when ``a`` and ``b`` may share a bed (default: False)."""
    if _lodging_are_family is not None:
        return bool(_lodging_are_family(a, b))
    return False


def set_lodging_sleep_policy(fn):
    """Register fn(room, character, game) -> bool or None for safe sleep."""
    global _lodging_sleep_policy
    _lodging_sleep_policy = fn


def lodging_sleep_policy(room, character=None, game=None):
    """Game sleep policy, or None to use engine defaults."""
    if _lodging_sleep_policy is not None:
        return _lodging_sleep_policy(room, character, game)
    return None


def set_lodging_room_stamper(fn):
    """Register fn(room) called after engine ``stamp_home_basics``."""
    global _lodging_room_stamper
    _lodging_room_stamper = fn


def stamp_lodging_room(room):
    """Apply game lodging room stamp hook, or no-op."""
    if _lodging_room_stamper is not None:
        _lodging_room_stamper(room)


# --- Paced travel hooks (H3b) --------------------------------------------


def set_paced_travel_overland_handler(fn):
    """Register fn(character, args, game, pace) -> True when handled."""
    global _paced_travel_overland_handler
    _paced_travel_overland_handler = fn


def paced_travel_overland_handler(character, args, game, pace):
    """True when the overland walk handler consumed this command."""
    if _paced_travel_overland_handler is not None:
        return bool(_paced_travel_overland_handler(character, args, game, pace))
    return False


def set_paced_travel_overland_advance(fn):
    """Register fn(character, game, focus) -> True while still walking."""
    global _paced_travel_overland_advance
    _paced_travel_overland_advance = fn


def paced_travel_overland_advance(character, game, focus):
    """Advance one overland foot hop; default clears focus (unhandled)."""
    if _paced_travel_overland_advance is not None:
        return bool(_paced_travel_overland_advance(character, game, focus))
    clear_walk_focus = None  # noqa: F841 -- avoid import cycle at load
    from engine.systems.paced_travel import clear_walk_focus as _clear

    _clear(
        character,
        notice="Overland walk cancelled -- not available here.",
    )
    return False


def set_paced_travel_player_hop(fn):
    """Register fn(character, hop, game, quiet) -> bool for one player hop."""
    global _paced_travel_player_hop
    _paced_travel_player_hop = fn


def paced_travel_player_hop(character, hop, game, quiet=True):
    """Apply one paced-travel hop (default: engine cardinal/enter/exit)."""
    if _paced_travel_player_hop is not None:
        return bool(_paced_travel_player_hop(character, hop, game, quiet))
    from engine.systems.paced_travel import _default_player_hop

    return _default_player_hop(character, hop, game, quiet=quiet)


def set_paced_travel_cadence_step(fn):
    """Register fn(actor, dest, game) -> bool for Cadence one-step pathing."""
    global _paced_travel_cadence_step
    _paced_travel_cadence_step = fn


def paced_travel_cadence_step(actor, dest, game):
    """Cadence seek one-hop; False when unregistered."""
    if _paced_travel_cadence_step is not None:
        return bool(_paced_travel_cadence_step(actor, dest, game))
    return False


def set_paced_travel_edge_ok(fn):
    """Register fn(from_room, neighbor, *, actor, game) -> bool."""
    global _paced_travel_edge_ok
    _paced_travel_edge_ok = fn


def paced_travel_edge_ok(from_room, neighbor, *, actor=None, game=None):
    """May this cardinal / pocket edge be used for player walk BFS?"""
    if _paced_travel_edge_ok is not None:
        return bool(
            _paced_travel_edge_ok(
                from_room, neighbor, actor=actor, game=game,
            )
        )
    return neighbor is not from_room


def set_paced_travel_enter_alias(fn):
    """Register fn(entries, hub) -> enter alias str or None."""
    global _paced_travel_enter_alias
    _paced_travel_enter_alias = fn


def paced_travel_enter_alias(entries, hub):
    """Best enter alias for a zone_entries hub (default: first match)."""
    if _paced_travel_enter_alias is not None:
        return _paced_travel_enter_alias(entries, hub)
    if not entries or hub is None:
        return None
    for alias, target in entries.items():
        if target is hub:
            return alias
    return None


def set_paced_travel_drive_to(fn):
    """Register fn(character, dest_room, game) -> str message or None."""
    global _paced_travel_drive_to
    _paced_travel_drive_to = fn


def paced_travel_drive_to(character, dest_room, game):
    """When aboard a vehicle, return drive status; None if not applicable."""
    if _paced_travel_drive_to is not None:
        return _paced_travel_drive_to(character, dest_room, game)
    return None


def set_paced_travel_gait_of(fn):
    """Register fn(character) -> gait verb for hop lines (go/walk/jog/run)."""
    global _paced_travel_gait_of
    _paced_travel_gait_of = fn


def paced_travel_gait_of(character):
    """Gait word for paced-hop room traffic (default ``go``)."""
    if _paced_travel_gait_of is not None:
        return _paced_travel_gait_of(character)
    return "go"


def set_paced_travel_engaged_refuse(fn):
    """Register fn(character) -> refuse message or None."""
    global _paced_travel_engaged_refuse
    _paced_travel_engaged_refuse = fn


def paced_travel_engaged_refuse(character):
    """Refuse line when walking away mid-fight; None for engine default."""
    if _paced_travel_engaged_refuse is not None:
        return _paced_travel_engaged_refuse(character)
    return None


def set_paced_travel_list_destinations(fn):
    """Register fn(character, game, pace) -> True when list was sent."""
    global _paced_travel_list_destinations
    _paced_travel_list_destinations = fn


def paced_travel_list_destinations(character, game, pace):
    """Send bare-verb destination list; False when unhandled."""
    if _paced_travel_list_destinations is not None:
        return bool(_paced_travel_list_destinations(character, game, pace))
    return False


def set_paced_travel_zone_rooms(fn):
    """Register fn(game, zone) -> list of rooms in a settlement zone."""
    global _paced_travel_zone_rooms
    _paced_travel_zone_rooms = fn


def paced_travel_zone_rooms(game, zone):
    """Rooms sharing ``zone``; default scans ``game.rooms``."""
    if _paced_travel_zone_rooms is not None:
        return list(_paced_travel_zone_rooms(game, zone) or ())
    if not zone or game is None:
        return []
    return [
        room for room in (getattr(game, "rooms", None) or {}).values()
        if getattr(room, "zone", None) == zone
    ]


# --- Appearance catalog hooks (two-repo purity H7b) -----------------------
_appearance_content_path = None
_appearance_kits = None
_appearance_kit_person_words = {}
_appearance_kit_short_nouns = {}
_appearance_no_crown_styles = {}
_kit_for_character_resolver = None
_appearance_age_phrase_fn = None


def set_appearance_content_path(fn):
    """Register fn() -> str absolute path to the appearance catalog JSON.

    Mirrors ``set_maps_dir``'s pattern from H1. No default -- engine raises
    a clear error if a caller needs the catalog before this is registered.
    Pass None to clear.
    """
    global _appearance_content_path
    _appearance_content_path = fn


def appearance_content_path():
    """Return the registered mortal appearance catalog path."""
    if _appearance_content_path is None:
        raise RuntimeError(
            "appearance catalog path not registered -- call "
            "hooks.set_appearance_content_path() at game boot "
            "(supers/bootstrap.py or basegame bootstrap)."
        )
    return _appearance_content_path()


def set_appearance_kits(kits):
    """Register kit_id -> slot catalog dict for ``catalog_for`` / validate.

    Pass None to clear.
    """
    global _appearance_kits
    _appearance_kits = kits


def appearance_kits():
    """Return the registered appearance kit registry."""
    if _appearance_kits is None:
        raise RuntimeError(
            "appearance kits not registered -- call "
            "hooks.set_appearance_kits() when loading game catalogs."
        )
    return _appearance_kits


def set_appearance_kit_person_words(mapping):
    """Register kit_id -> {pronoun: noun} overrides for look prose."""
    global _appearance_kit_person_words
    _appearance_kit_person_words = dict(mapping or {})


def appearance_kit_person_words():
    """Return kit-specific person-word maps (may be empty)."""
    return _appearance_kit_person_words


def set_appearance_kit_short_nouns(mapping):
    """Register kit_id -> room-face noun for non-mortal kits."""
    global _appearance_kit_short_nouns
    _appearance_kit_short_nouns = dict(mapping or {})


def appearance_kit_short_nouns():
    """Return kit-specific short-desc nouns (empty when unset)."""
    return _appearance_kit_short_nouns


def set_appearance_no_crown_styles(mapping):
    """Register hair_style id -> (short_bit, full_bit) for no-crown styles."""
    global _appearance_no_crown_styles
    _appearance_no_crown_styles = dict(mapping or {})


def appearance_no_crown_styles():
    """Return registered no-crown style tuples (may be empty)."""
    return _appearance_no_crown_styles


def set_kit_for_character_resolver(fn):
    """Register fn(character) -> kit_id or None for inferred kits.

    SUPERS registers Cosmic Elemental Aspect inference here. Pass None to
    clear.
    """
    global _kit_for_character_resolver
    _kit_for_character_resolver = fn


def kit_for_character_resolver():
    """Return the registered kit inference hook, or None."""
    return _kit_for_character_resolver


def set_appearance_age_phrase_fn(fn):
    """Register fn(age_years:int) -> decade phrase str for build_description."""
    global _appearance_age_phrase_fn
    _appearance_age_phrase_fn = fn


def appearance_age_phrase_fn():
    """Return the registered age-phrase hook, or None."""
    return _appearance_age_phrase_fn


# --- Persona registry hooks (H7c) ------------------------------------------

_persona_content_path_fn = None


def set_persona_content_path(fn):
    """Register callable returning absolute path to personas.json."""
    global _persona_content_path_fn
    _persona_content_path_fn = fn


def persona_content_path():
    """Return the registered personas.json path (raises if unset)."""
    if _persona_content_path_fn is None:
        raise RuntimeError(
            "persona content path not registered -- call "
            "hooks.set_persona_content_path at game boot."
        )
    return _persona_content_path_fn()


# --- Phone hooks (H7a) -----------------------------------------------------

_phone_dial_alias_resolver = None
_phone_paint_fn = lambda character, role, text: text  # noqa: E731
_phone_tag_fn = lambda character=None: ""  # noqa: E731
_phone_call_tag_fn = None
_phone_voicemail_line_fn = None
_phone_payphone_fee_fn = lambda: 0  # noqa: E731


def set_phone_dial_alias_resolver(fn):
    """Register fn(raw, character, game) -> str|None for dial alias override."""
    global _phone_dial_alias_resolver
    _phone_dial_alias_resolver = fn


def phone_dial_alias_resolver(raw, character, game):
    """Game dial alias (WKNZ, phonebook); None = engine default lookup."""
    if _phone_dial_alias_resolver is not None:
        return _phone_dial_alias_resolver(raw, character, game)
    return None


def set_phone_room_emote_style(paint_fn, tag_fn, call_tag_fn=None):
    """Register paint/tag helpers for phone room emotes and line tags."""
    global _phone_paint_fn, _phone_tag_fn, _phone_call_tag_fn
    _phone_paint_fn = paint_fn
    _phone_tag_fn = tag_fn
    _phone_call_tag_fn = call_tag_fn if call_tag_fn is not None else tag_fn


def phone_paint(character, role, text):
    """Optional ANSI paint for phone lines (default passthrough)."""
    return _phone_paint_fn(character, role, text)


def phone_tag(character=None):
    """Plain + painted [PHONE] tag (default empty)."""
    return _phone_tag_fn(character)


def phone_call_tag(character=None):
    """Plain + painted [CALL] tag (default matches phone_tag)."""
    fn = _phone_call_tag_fn if _phone_call_tag_fn is not None else _phone_tag_fn
    return fn(character)


def set_phone_voicemail_line(fn):
    """Register fn(caller, callee_number) -> str for Echo voicemail stub."""
    global _phone_voicemail_line_fn
    _phone_voicemail_line_fn = fn


def phone_voicemail_line(caller, callee_number):
    """Voicemail refusal line when callee declines pickup."""
    if _phone_voicemail_line_fn is not None:
        return _phone_voicemail_line_fn(caller, callee_number)
    return (
        f"{phone_tag(caller)} {callee_number} — voicemail. "
        "The line is not taking calls."
    )


def set_phone_payphone_fee(fn):
    """Register fn() -> int dollars per outbound payphone call."""
    global _phone_payphone_fee_fn
    _phone_payphone_fee_fn = fn


def phone_payphone_fee():
    """Outbound payphone fee in dollars (default 0)."""
    try:
        return int(_phone_payphone_fee_fn())
    except (TypeError, ValueError):
        return 0


# --- Procedural build hooks (populate peel) --------------------------------

_populate_room_title = None
_populate_city_label = None
_populate_city_for_map_id = None
_populate_neighborhood_names = None
_populate_lodging_entry_stamper = None

_DEFAULT_NEIGHBORHOOD_NAMES = (
    "Stevenson", "Ferguson", "Ash", "Cedar", "Maple",
    "Oak", "Elm", "Willow", "Pine", "Birch",
    "Harper", "Miller", "Baker", "Cooper", "Parker",
    "Sullivan", "Brennan", "Callahan", "Donovan", "Murphy",
    "Ridge", "Valley", "Meadow", "Harbor", "Summit",
    "Liberty", "Madison", "Jefferson", "Lincoln", "Washington",
    "Prairie", "Cottonwood", "Hickory", "Sycamore", "Magnolia",
)


def set_populate_room_namer(fn):
    """Register fn(city, main, sub=None) -> structured ROOM NAME string."""
    global _populate_room_title
    _populate_room_title = fn


def populate_room_title(city, main, sub=None):
    """Build a structured room title (default: engine.room_naming)."""
    if _populate_room_title is not None:
        return _populate_room_title(city, main, sub)
    from engine.room_naming import structured_title

    return structured_title(city, main, sub)


def set_populate_city_label(fn):
    """Register fn(room) -> city label for procedural builders."""
    global _populate_city_label
    _populate_city_label = fn


def populate_city_label(room):
    """City label for a standing room (default: city_name or map_id title-case)."""
    if _populate_city_label is not None:
        return _populate_city_label(room)
    stamped = str(getattr(room, "city_name", None) or "").strip()
    if stamped:
        return stamped
    map_id = str(getattr(room, "map_id", None) or "").strip()
    return populate_city_for_map_id(map_id)


def set_populate_city_for_map_id(fn):
    """Register fn(map_id) -> city label string."""
    global _populate_city_for_map_id
    _populate_city_for_map_id = fn


def populate_city_for_map_id(map_id):
    """Map/zone id → city label (default: title-cased id)."""
    if _populate_city_for_map_id is not None:
        return _populate_city_for_map_id(map_id)
    mid = str(map_id or "").strip()
    if not mid:
        return "Town"
    return mid.replace("_", " ").title()


def set_populate_neighborhood_names(fn):
    """Register fn() -> sequence of name tokens for neighborhood titles."""
    global _populate_neighborhood_names
    _populate_neighborhood_names = fn


def populate_neighborhood_names():
    """Name pool for ``populate neighborhood`` (generic engine default)."""
    if _populate_neighborhood_names is not None:
        return _populate_neighborhood_names()
    return _DEFAULT_NEIGHBORHOOD_NAMES


def set_populate_lodging_entry_stamper(fn):
    """Register fn(entry, unit_kind) -> None to stamp game lodging flags."""
    global _populate_lodging_entry_stamper
    _populate_lodging_entry_stamper = fn


def populate_lodging_entry_stamper(entry, unit_kind):
    """Apply game lodging entry stamp hook, or no-op."""
    if _populate_lodging_entry_stamper is not None:
        _populate_lodging_entry_stamper(entry, unit_kind)
