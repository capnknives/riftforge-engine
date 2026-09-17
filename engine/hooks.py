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

# Lag S2: game-owned megachar shards (life_log / property stash) live in
# ``character_heavy_blobs``. Engine never imports the game to fill them.
_heavy_sidecar_collect = None
_heavy_sidecar_load = None
_heavy_sidecar_merge = None
_heavy_sidecar_parse_payload = None
_heavy_sidecar_property_stash_empty = None
_mark_property_stash_heavy_dirty = None

# Game-owned Game meta (T3 persistence-api): moral/Tide, Cadence overrides,
# tuning tables, rumor boards, … — loaded/saved around the engine-generic
# world snapshot. Default no-op so lean boots keep __init__ defaults.
_game_meta_loader = None
_game_meta_saver = None

# Per-character built sites (homestead / demesne / shop / realm / town)
# flushed on player ``save`` and archived in the checkpoint tank.
_save_player_built_sites = None
_collect_player_built_checkpoint = None
_restore_player_built_sites = None

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
_after_session_attach_deferred = None

# Optional pre-Echo Session-detach side effect (connection log, ...).
# Runs while the Character still has session-linked fields intact --
# reconnect takeover, intentional quit, and client EOF all pass through
# engine/connection.py before session is cleared.
_on_session_disconnect = None
_on_echo_begin = None
_park_gm_spirit_on_disconnect = None

# GMCP Char.Vitals / Char.Status payload builders (SUPERS fills meters;
# engine/gmcp.py sends). fn(character) -> dict or None.
_gmcp_char_vitals = None
_gmcp_char_status = None
# Room.Occupants / Zone who[] row policy. fn(obj, viewer) -> str | None.
_occupant_kind = None
_occupant_token = None
# Wave 2 folklore peel: fishing + lockpick kernels.
_fishing_tables = None
_fishing_skill = None
_aboard_water_craft = None
_lock_dc = None
_skill_check = None
# Wave 3 folklore peel: devil's trap stamp + temporary circle query.
_temporary_devils_trap = None
_temporary_salt_line = None
_occult_mark_blocks = None
# Wave 5c folklore peel: corporeal-prison plane policy hook.
_is_corporeal_prison_plane = None
# Wave 4 folklore peel: Cadence seek/wander kernel hooks.
_cadence_meter_names = None
_cadence_seek_threshold = None
_cadence_need_resource = None
_cadence_room_passable = None
_cadence_urgent_override = None
_cadence_plan_override = None
# Browser atlas right-click verbs. fn(character, map_id, x, y, landmark) ->
# list of {"label", "command"} using player-facing names only.
_web_map_cell_verbs = None
# Prompt %Tg: fn(character, game) -> foe lifeforce band str or "".
_prompt_target_band = None
# Prompt %Nd: fn(character, game) -> lifestyle need word or "".
_prompt_need_band = None
# Origin default prompt: fn(character) -> template str.
_origin_default_prompt = None
# Factory prompt check: fn(template) -> bool (shipped default, safe to refresh).
_is_factory_prompt = None
# Prompt supplemental bands: fn(character, game) -> dict[str, str].
_prompt_supplemental_bands = None

# System topic pages for bare `help` (game content; engine verbs read these).
_help_topics = {}
_gm_only_help_keywords = frozenset()
_help_categories = []
# Staff-only catalog for ``gmhelp`` (GM/Builder index + jargon overrides).
# Player ``help`` never lists these, even for ranked staff -- they type
# gmhelp so they can still read the player handbook as a player would.
_gmhelp_categories = []
_gmhelp_topic_overrides = {}
# Optional: fn(character) -> bool. When True, the help index includes
# GM/Builder bands. Default falls back to ``_is_gm``. SUPERS gates on
# GM form so ``gm off`` staff see the player catalog (bug report 934).
# Player ``help`` no longer uses this for the catalog (gmhelp owns it);
# kept so diagnostics and older callers stay defined.
_help_index_staff_view = None

# Player-verb dispatch: engine/npc_act.py needs to run one raw command line
# the same way a real player's input would, but the actual `dispatch()`
# function lives in the shared root commands.py (which itself imports
# supers.verbs) -- routing through this hook keeps npc_act.py from ever
# importing that module directly (Phase 2 purity gate).
_dispatch = None

# Optional game-owned verb gates (cage hold, vessel passenger, combat KO, …).
# fn(character, verb, args, game) -> (blocked: bool, message: str|None).
_command_dispatch_gate = None

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

# Room-scoped command hints for ``commands here``. fn(character, game) -> list[str].
_room_command_hints = None

# Virtual Paths not stored on ``Room.exits`` (pit descent after boss kill).
# fn(room, character, game) -> list[(direction, dest_label)].
_room_look_virtual_exits = None

# Soft fear nudge shown to any monster after `look` when the active Called
# Slayer shares the room. fn(character, room) -> str or None.
_monster_sense_message = None

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
_character_placement_block = None
_clinic_on_admit = None
_clinic_on_discharge = None
_clinic_casualty_meter = None
_clinic_ko_clear = None
_clinic_pre_admit = None
_clinic_admit_prologue = None
_clinic_ward_tick = None
_justice_on_robbery = None
_justice_fine_schedule = None
_identity_verify_hook = None
_identity_pierce_supernatural = None
_disguise_pierce_check = None
_follow_pull_skip = None
_follow_pull_handled = None
_follow_pull_homestead_plot = None
# Homestead interior graph self-heal before ``exits`` / failed compass moves.
# fn(character, game) -> bool (True when repair rewired exits this call).
_ensure_homestead_plot_graph_for_actor = None
_follow_pull_household_pet = None
_follow_shares_origin = None
_group_share_travel_spot = None
_report_context_extra = None
_room_broadcast_deliver = None
_room_broadcast_transform = None
_room_broadcast_after = None
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

# Optional look exit door-closed flag. fn(room, direction, dest, game=None,
# character=None) -> bool. When True, look/exits mark the exit sealed.
_look_exit_door_closed = None

# Trickster site mask: filter pocket zone_entries per viewer.
_filter_zone_entries = None

# Look-only zone_entries dedupe (e.g. hide Enter: line when a fixture
# already advertises the same mouth). Travel keeps full entries.
_filter_zone_entries_look = None

# Civic curb fixtures: ``enter shop`` / stale alias recovery on commercial
# streets. fn(character, raw, game) -> bool when the hop was handled.
_try_civic_fixture_enter = None

# Per-viewer exit hide (shrine fold). fn(viewer, room, direction, dest, game) -> bool.
_look_exit_hidden_from_viewer = None

# Cancel any in-progress "awake rest" state -- movement/combat interrupts it.
# fn(character) -> None (side-effecting only; no return value used).
_cancel_rest = None

# Lodging (H3a): bed sharing family check; safe-sleep policy; post-stamp hook.
_lodging_are_family = None
_lodging_bed_eligibility = None
_lodging_sleep_policy = None
_lodging_rent_tick = None
_lodging_room_stamper = None
_lodging_look_home_detail_lines = None

# Paced travel (H3b): overland handler, player hop, cadence step, edge_ok, …
_paced_travel_overland_handler = None
_paced_travel_overland_advance = None
_paced_travel_player_hop = None
_paced_travel_cadence_step = None
_paced_travel_edge_ok = None
_paced_travel_hub_ok = None
_paced_travel_destinations = None
_paced_travel_enter_alias = None
_paced_travel_drive_to = None
_paced_travel_gait_of = None
_paced_travel_hops_of = None
_paced_travel_eta_tick = None
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
# Hidden attacker return-fire gate (hellhound blind-swing / kill-tool).
# fn(viewer, other) -> bool when other is hidden but swinging at viewer.
_can_target_hidden_attacker = None
_can_notice_stealth = None
# Game-side extra presence hide (Signal dwell, …). fn(viewer, other) -> bool.
_presence_hidden_extra = None
_extra_affect_rows = None
# Staff gm-on look pierce for hidden presences (stealth, etc.) -- tagged
# ``(hidden)`` instead of omitted. fn(viewer, other) -> bool.
_staff_tags_hidden_presence = None

# Trickster "laying low" withdraw pierce (bug report 632: a trickster
# folded into their shrine while an archangel wears their face should not
# stand around in plain sight for casual onlookers). Default (no game
# installed): True -- bare engine has no trickster kit, so nobody is hidden
# by this hook. fn(viewer, other) -> bool.
_can_perceive_trickster_laylow = None

# Veil-layer membership + sight pierce (death spirits, faded Ghosts, veiled
# Reapers, vessel-free Mantle walks share Prime's XYZ map but not its
# interaction/visibility). Bare engine default: nobody is Veil-layer, so
# both hooks are moot with no game installed. fn(character) -> bool /
# fn(viewer, other) -> bool / fn() -> str.
_in_veil = None
_veil_visible_to = None
_veil_look_tag = None
_veil_soul_label = None
# Floor / targeting visibility for Veil-layer Items (ghost mirror gear).
# Default: every Item is visible. SUPERS hides spirit copies from mortals.
_item_visible_to = None
_item_in_veil = None

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

# Staff-login fixture keys (lowercase). fn() -> iterable of keys.
# Copyover skips deferred ensure, so Character.staff_login may still be
# False on old blobs -- login consults the catalog through this hook.
_staff_login_cast_keys = None

# Pre-move cancel (e.g. stop an in-progress training montage). Called
# before a single-character move actually happens.
# fn(character) -> player message str, or None if nothing to say.
_before_relocate = None

# Post-move arrival side effects (stop work if the job site was left behind,
# drag a carried body along, lodging owner-walks-in-on-squatter check, ...).
# fn(character, dest, game, was_working) -> None (side-effecting only).
_after_arrive = None
# After Character.move_to actually leaves a room (GM goto, walk, teleport).
# fn(character, old_room, new_room, game) -> None. SUPERS drops in-room
# approach/retreat pair bands so a new room starts at default reach.
_character_relocated = None
# Optional fn(character, direction, dest, game) after a successful room step
# (combat-pit pose feed). Kept separate from after_arrive so direction is known.
_after_move_step = None
# Door parity: auto open/unlock before leave; auto close afterward.
# fn(character, from_room, direction, dest, game) -> None
_before_move_crossing = None
_after_move_crossing = None

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

# Account roster line (login menu + ``account`` command).
# fn(game, key, body=None) -> str. SUPERS adds Origin / Path / Aspect.
_account_roster_label_for = None
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

# Viewer-relative look at a named extra on a person (look ayla head).
# fn(viewer, subject, keyword) -> str | None. None = hook unset (lean engine).
_look_detail_for = None

# Registered combat helper that cannot be targeted (owner's pet/minion).
# fn(character) -> bool. Default False when no game is installed.
_is_untargetable_helper = None

# Chargen placement when ``chargen_start_room_key`` did not resolve.
# fn(character, game) -> Room | None. Default None when no game is installed.
_chargen_fallback_start_room = None

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

# Pre-boot content validation (P3) before ``build_world``. fn() -> None;
# raises on bad catalogs / JSON syntax.
_boot_content_gate = None

# Drop in-memory catalog caches before copyover reload (dungeon catalog +
# bestiary registry). fn() -> None. Default no-op when no game is installed.
_clear_content_caches_for_copyover = None

# Re-derive a character's max HP (used after un-spiriting a character whose
# body was lost on load -- see engine/persistence.py's load_world).
# fn(character) -> None (mutates character.hp in place).
_recompute_hp = None

# After load un-spirits a character whose corpse Item could not be relinked,
# heal marooned afterlife desk flags / Heaven location (bug #387).
# fn(character, game) -> None.
_heal_force_unspirit = None

# When Item corpse relink fails, try living-husk Character relink before
# force-unspirit (embodiment matrix Phase 3).
# fn(game, character) -> bool (True when relinked).
_relink_living_husk_body = None

# Per-character load normalization (home stamps, vehicle board flags) after
# equipment rebind in ``load_world`` -- boot lifecycle Phase C.
# fn(character, game) -> None.
_normalize_character_after_load = None

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
_sync_equipped_flags_from_equipment = None


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


def set_sync_equipped_flags_from_equipment(fn):
    """Register fn(character) to stamp inventory flags from equipment map."""
    global _sync_equipped_flags_from_equipment
    _sync_equipped_flags_from_equipment = fn


def sync_equipped_flags_from_equipment(character):
    """Align inventory equipped flags with live combat slots before save."""
    if _sync_equipped_flags_from_equipment is not None:
        return _sync_equipped_flags_from_equipment(character)
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


def set_boot_content_gate(fn):
    """Register fn() -> None that validates on-disk catalogs before world load.

    Pass None to restore the no-op default (lean engine / tests).
    """
    global _boot_content_gate
    _boot_content_gate = fn


def boot_content_gate():
    """Return the registered pre-boot validator, or None."""
    return _boot_content_gate


def set_clear_content_caches_for_copyover(fn):
    """Register fn() -> None to flush stale in-memory catalog caches.

    Used by engine/copyover.py before reload_world_save_modules so mouth
    alignment reads fresh dungeon catalog + bestiary after auto-deploy
    overlays. Pass None to restore the no-op default.
    """
    global _clear_content_caches_for_copyover
    _clear_content_caches_for_copyover = fn


def clear_content_caches_for_copyover():
    """Flush game-owned catalog caches before a copyover snapshot reload."""
    if _clear_content_caches_for_copyover is not None:
        _clear_content_caches_for_copyover()


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


def set_heal_force_unspirit(fn):
    """Register fn(character, game) after persistence force-unspirit."""
    global _heal_force_unspirit
    _heal_force_unspirit = fn


def heal_force_unspirit(character, game=None):
    """Run post-load un-spirit heal hook, or no-op."""
    if _heal_force_unspirit is not None:
        _heal_force_unspirit(character, game)


def set_relink_living_husk_body(fn):
    """Register fn(game, character) -> bool for living-husk body relink."""
    global _relink_living_husk_body
    _relink_living_husk_body = fn


def relink_living_husk_body(game, character):
    """Try living-husk relink when Item corpse lookup failed on load."""
    if _relink_living_husk_body is not None:
        return bool(_relink_living_husk_body(game, character))
    return False


def set_normalize_character_after_load(fn):
    """Register fn(character, game) after persistence load + equipment rebind."""
    global _normalize_character_after_load
    _normalize_character_after_load = fn


def normalize_character_after_load(character, game=None):
    """Run per-character load normalization hook, or no-op."""
    if _normalize_character_after_load is not None:
        _normalize_character_after_load(character, game)


_gateway_resume_hook = None  # fn(game) -> None, or an awaitable -- see setter.
_should_skip_map_missing_stub_heal = None  # fn(character) -> bool


def set_should_skip_map_missing_stub_heal(fn):
    """Register fn(character) -> bool for ``heal_map_missing_stub_occupants``.

    When True, boot must not yank the body to home/start -- an ephemeral
    instance run (Slumber Depths, pit, Drift, …) will rebuild the floor
    instead. SUPERS registers this in ``bootstrap.register_all_hooks``.
    Pass None to restore the default (never skip).
    """
    global _should_skip_map_missing_stub_heal
    _should_skip_map_missing_stub_heal = fn


def should_skip_map_missing_stub_heal(character):
    """True when persistence stub-heal must leave the body for resume."""
    if _should_skip_map_missing_stub_heal is None:
        return False
    return bool(_should_skip_map_missing_stub_heal(character))


_on_map_missing_stub_healed = None  # fn(character, stub_key) -> None


def set_on_map_missing_stub_healed(fn):
    """Register fn(character, stub_key) -> None, called after a body is
    relocated off a persistence ``map_missing_stub`` room.

    Lets game-owned per-character stamps that reference the dead stub
    (e.g. an instanced dungeon run id) get scrubbed and the player told
    why they moved, instead of a silent generic relocation (bug reports
    1629/1630). Pass None to restore the default (no-op).
    """
    global _on_map_missing_stub_healed
    _on_map_missing_stub_healed = fn


def on_map_missing_stub_healed(character, stub_key):
    """Notify game-owned code that ``character`` was healed off ``stub_key``."""
    if _on_map_missing_stub_healed is None:
        return
    _on_map_missing_stub_healed(character, stub_key)


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


_open_veil_playable_hook = None  # fn(game) -> None


def set_open_veil_playable_hook(fn):
    """Register fn(game) that opens look/who after gateway copyover reattach.

    SUPERS registers ``boot_seed._open_veil_playable`` (tick handlers +
    rewrite-complete). Pass None to restore the lean fallback.
    """
    global _open_veil_playable_hook
    _open_veil_playable_hook = fn


def open_veil_playable(game):
    """Let reattached copyover clients type (Circle/Diku copyover_recover).

    Gap-fill heals must not hold this gate. Lean engine: just clear the
    veil event so ``Session.play`` can start.
    """
    if game is None:
        return
    if _open_veil_playable_hook is not None:
        _open_veil_playable_hook(game)
        return
    game._veil_world_ready = True
    game._veil_hold_ready_until_announce = False
    ev = getattr(game, "_veil_ready_event", None)
    if ev is not None and not ev.is_set():
        ev.set()
    from engine import copyover as copyover_mod

    copyover_mod.mark_copyover_ready()


_deferred_boot_seed_hook = None  # fn(game) -> None
_deferred_boot_seed_async_hook = None  # async fn(game) -> None


def set_deferred_boot_seed_hook(fn):
    """Register fn(game) to run after gateway reattach, before tick_loop.

  SUPERS registers ``seed_content_deferred`` so copyover can open IPC
  and send MSG_AFTER before heavy heals. Pass None to restore no-op.
    """
    global _deferred_boot_seed_hook
    _deferred_boot_seed_hook = fn


def set_deferred_boot_seed_async_hook(fn):
    """Register async fn(game) for gateway welcome (yields between phases).

    When set, ``run_deferred_boot_seed_async`` uses this so gap-fill
    heals can run after look/who. Gateway welcome must not await this
    on the IPC read loop. Falls back to the sync hook if unset.
    """
    global _deferred_boot_seed_async_hook
    _deferred_boot_seed_async_hook = fn


def run_deferred_boot_seed(game):
    """Run the registered deferred boot-seed hook (sync). No-op if unset."""
    if _deferred_boot_seed_hook is None:
        return
    _deferred_boot_seed_hook(game)


async def run_deferred_boot_seed_async(game):
    """Run deferred boot heals, yielding to the event loop between phases."""
    if _deferred_boot_seed_async_hook is not None:
        await _deferred_boot_seed_async_hook(game)
        return
    if _deferred_boot_seed_hook is not None:
        run_deferred_boot_seed(game)


_flush_pending_report_thanks_hook = None


def set_flush_pending_report_thanks(fn):
    """Register fn(game) to deliver queued bug/suggestion thank-yous."""
    global _flush_pending_report_thanks_hook
    _flush_pending_report_thanks_hook = fn


def flush_pending_report_thanks(game):
    """Best-effort flush after Veil rewrite announce (SUPERS registers)."""
    if _flush_pending_report_thanks_hook is not None:
        _flush_pending_report_thanks_hook(game)


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
    "highway": [],
    "trail": [],
    "desert": [],
    "wetland": [],
    "void": [],
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


def blob_codec_registered():
    """True when game persistence can serialize character blobs (not ``{}``)."""
    return _blob_to is not None


def set_heavy_sidecar_codec(collect_fn, load_fn, merge_fn):
    """Register megachar sidecar collect / load / merge (or None to clear).

    collect_fn(game, character, cnum, *, prev_shard_hashes) -> (rows, hashes)
    load_fn(conn) -> {cnum: {shard: dict}}
    merge_fn(saved_dict, sidecar_shards) -> dict
    """
    global _heavy_sidecar_collect, _heavy_sidecar_load, _heavy_sidecar_merge
    _heavy_sidecar_collect = collect_fn
    _heavy_sidecar_load = load_fn
    _heavy_sidecar_merge = merge_fn


def collect_heavy_sidecar_rows(game, character, cnum, *, prev_shard_hashes):
    """Build ``character_heavy_blobs`` INSERT rows, or empty when unregistered."""
    if _heavy_sidecar_collect is None:
        return [], prev_shard_hashes or {}
    return _heavy_sidecar_collect(
        game, character, cnum, prev_shard_hashes=prev_shard_hashes,
    )


def load_heavy_sidecars_index(conn):
    """Boot index of sidecar shards, or ``{}`` when unregistered."""
    if _heavy_sidecar_load is None:
        return {}
    return _heavy_sidecar_load(conn) or {}


def merge_saved_with_sidecars(saved, sidecar_shards):
    """Overlay sidecar shards onto a stats dict (identity when unregistered)."""
    if _heavy_sidecar_merge is None:
        return saved
    return _heavy_sidecar_merge(saved, sidecar_shards)


def set_heavy_sidecar_property_stash_helpers(parse_fn=None, empty_fn=None):
    """Register property-stash shard parse/empty helpers for upsert skip logic."""
    global _heavy_sidecar_parse_payload, _heavy_sidecar_property_stash_empty
    _heavy_sidecar_parse_payload = parse_fn
    _heavy_sidecar_property_stash_empty = empty_fn


def parse_heavy_shard_payload(payload):
    """Parse a sidecar shard payload, or None when unregistered."""
    if _heavy_sidecar_parse_payload is None:
        return None
    return _heavy_sidecar_parse_payload(payload)


def property_stash_payload_empty(parsed):
    """True when a parsed property-stash shard is empty; False when unregistered."""
    if _heavy_sidecar_property_stash_empty is None:
        return False
    return bool(_heavy_sidecar_property_stash_empty(parsed))


def set_mark_property_stash_heavy_dirty(fn):
    """Register fn(game, character, *, allow_empty_write=False) after stash edits."""
    global _mark_property_stash_heavy_dirty
    _mark_property_stash_heavy_dirty = fn


def mark_property_stash_heavy_dirty(game, character, *, allow_empty_write=False):
    """Queue property-stash sidecar writes when the game registers a codec."""
    if _mark_property_stash_heavy_dirty is None:
        return
    try:
        _mark_property_stash_heavy_dirty(
            game, character, allow_empty_write=allow_empty_write,
        )
    except Exception:
        pass


def character_attacher_registered():
    """True when Character composition (stats/Origin/…) will run on init."""
    return _character_attacher is not None


def game_meta_codec_registered():
    """True when Game meta (Tide/Cadence/…) can be saved through hooks."""
    return _game_meta_saver is not None


def character_to_blob(character, **kwargs):
    """Serialize game fields for the characters.stats JSON column."""
    if _blob_to is not None:
        return _blob_to(character, **kwargs)
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


def set_player_built_site_codec(save_fn, collect_fn, restore_fn):
    """Register per-character built-site save / checkpoint / restore.

    save_fn(game, conn, character) -> list of kind labels
    collect_fn(game, character) -> dict for the checkpoint tank
    restore_fn(game, character, payload) -> (ok, kinds)

    Pass None, None, None to clear (lean engine / basegame keep no-ops).
    """
    global _save_player_built_sites
    global _collect_player_built_checkpoint
    global _restore_player_built_sites
    _save_player_built_sites = save_fn
    _collect_player_built_checkpoint = collect_fn
    _restore_player_built_sites = restore_fn


def save_player_built_sites(game, conn, character):
    """Flush this character's homestead / demesne / shop / realm / town."""
    if _save_player_built_sites is None:
        return []
    return _save_player_built_sites(game, conn, character) or []


def collect_player_built_checkpoint(game, character):
    """JSON snapshot of this character's built sites for the save tank."""
    if _collect_player_built_checkpoint is None:
        return {}
    return _collect_player_built_checkpoint(game, character) or {}


def restore_player_built_sites(game, character, payload):
    """Rematerialize built sites from a checkpoint blob."""
    if _restore_player_built_sites is None:
        return True, []
    return _restore_player_built_sites(game, character, payload)


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
    """Run the registered Session-attach hook, or do nothing if none is set.

    A raising game hook must not kill login -- log and continue so the
    Session still reaches play() (see logging_gaps_2026-08-30.md).
    """
    if _after_session_attach is not None:
        try:
            _after_session_attach(character, game)
        except Exception as exc:
            _log_hook_fail(game, "after_session_attach", exc)


def set_after_session_attach_deferred(fn):
    """Register fn(character, game) for post-look login attach work.

    Heavy hooks (mail, vehicles, quest spawns, …) run from ``play()`` after
    the first ``look`` so character select does not wedge the asyncio loop.
    Pass None to restore the no-op default.
    """
    global _after_session_attach_deferred
    _after_session_attach_deferred = fn


def after_session_attach_deferred(character, game):
    """Run deferred Session-attach hooks after the first room look."""
    if _after_session_attach_deferred is not None:
        try:
            _after_session_attach_deferred(character, game)
        except Exception as exc:
            _log_hook_fail(game, "after_session_attach_deferred", exc)


def set_on_session_disconnect(fn):
    """Register fn(character, game, *, to_echo=True) before Session detach.

    ``to_echo`` is False when another client is taking over the same body
    (no Echo leave / vault-on-quit). Pass None to restore the no-op default.
    """
    global _on_session_disconnect
    _on_session_disconnect = fn


def on_session_disconnect(character, game, *, to_echo=True):
    """Run the registered Session-detach hook, or do nothing if none is set.

    Logout must still detach the Session even when a game hook raises.
    """
    if _on_session_disconnect is not None:
        try:
            _on_session_disconnect(character, game, to_echo=to_echo)
        except Exception as exc:
            _log_hook_fail(game, "on_session_disconnect", exc)


def set_on_echo_begin(fn):
    """Register fn(character, game) when a body becomes an offline Echo."""
    global _on_echo_begin
    _on_echo_begin = fn


def on_echo_begin(character, game):
    """Run the registered Echo-begin hook, or no-op."""
    if _on_echo_begin is not None:
        try:
            _on_echo_begin(character, game)
        except Exception as exc:
            _log_hook_fail(game, "on_echo_begin", exc)


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
        try:
            _park_gm_spirit_on_disconnect(spirit, game)
        except Exception as exc:
            _log_hook_fail(game, "park_gm_spirit_on_disconnect", exc)


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
        try:
            return _gmcp_char_vitals(character)
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "gmcp_char_vitals", exc)
            return None
    return None


def set_prompt_target_band(fn):
    """Register fn(character, game) -> str for prompt token %Tg.

    Pass None to restore the no-op default (segment omits itself).
    """
    global _prompt_target_band
    _prompt_target_band = fn


def prompt_target_band(character, game=None):
    """Foe lifeforce band for %Tg, or \"\" when not in a real fight."""
    if _prompt_target_band is not None:
        try:
            return _prompt_target_band(character, game) or ""
        except Exception as exc:
            _log_hook_fail(game, "prompt_target_band", exc)
            return ""
    return ""


def set_prompt_need_band(fn):
    """Register fn(character, game) -> str for prompt token %Nd.

    Pass None to restore the no-op default (segment omits itself).
    """
    global _prompt_need_band
    _prompt_need_band = fn


def prompt_need_band(character, game=None):
    """Most urgent lifestyle need for %Nd, or \"\" when content."""
    if _prompt_need_band is not None:
        try:
            return _prompt_need_band(character, game) or ""
        except Exception as exc:
            _log_hook_fail(game, "prompt_need_band", exc)
            return ""
    return ""


def set_origin_default_prompt(fn):
    """Register fn(character) -> prompt template for Origin defaults.

    Pass None to fall back to display_prefs.DEFAULT_PROMPT only.
    """
    global _origin_default_prompt
    _origin_default_prompt = fn


def origin_default_prompt(character):
    """Origin-specific default prompt template, or engine DEFAULT_PROMPT."""
    if _origin_default_prompt is not None:
        try:
            return _origin_default_prompt(character)
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "origin_default_prompt", exc)
    from engine import display_prefs
    return display_prefs.DEFAULT_PROMPT


def set_is_factory_prompt(fn):
    """Register fn(template) -> bool for factory prompt detection."""
    global _is_factory_prompt
    _is_factory_prompt = fn


def is_factory_prompt(template):
    """True when template is still a shipped factory default."""
    if _is_factory_prompt is not None:
        try:
            return bool(_is_factory_prompt(template))
        except Exception as exc:
            _log_hook_fail(None, "is_factory_prompt", exc)
    from engine import display_prefs
    return display_prefs.is_generic_prompt(template)


def set_prompt_supplemental_bands(fn):
    """Register fn(character, game) -> dict of optional prompt band strings.

    Keys consumed by engine/display_prefs._prompt_vitals: form_band,
    vessel_band, affliction_band, integrity_band, favor_band, hospital_band.
    Pass None to restore the no-op default (all segments omit).
    """
    global _prompt_supplemental_bands
    _prompt_supplemental_bands = fn


def prompt_supplemental_bands(character, game=None):
    """Optional prompt segment values from the game hook (empty dict default)."""
    if _prompt_supplemental_bands is not None:
        try:
            return _prompt_supplemental_bands(character, game) or {}
        except Exception as exc:
            _log_hook_fail(game, "prompt_supplemental_bands", exc)
            return {}
    return {}


def set_gmcp_char_status(fn):
    """Register fn(character) -> dict of extra Char.Status fields (Origin…).

    Merged on top of engine base status. Pass None for no extras.
    """
    global _gmcp_char_status
    _gmcp_char_status = fn


def gmcp_char_status(character):
    """Extra Char.Status fields from the game, or None."""
    if _gmcp_char_status is not None:
        try:
            return _gmcp_char_status(character)
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "gmcp_char_status", exc)
            return None
    return None


def set_occupant_kind(fn):
    """Register fn(obj, viewer) -> kind str or None for Room.Occupants rows.

    Return a value from engine.gmcp.OCCUPANT_KINDS, or None to let the
    engine apply its default rules (player / npc / echo / hostile / other).
    Pass None to clear the hook.
    """
    global _occupant_kind
    _occupant_kind = fn


def occupant_kind(obj, viewer):
    """Game override for one occupant row's ``kind``, or None for default."""
    if _occupant_kind is not None:
        try:
            return _occupant_kind(obj, viewer)
        except Exception as exc:
            game = None
            if viewer is not None:
                game = getattr(getattr(viewer, "session", None), "game", None)
            _log_hook_fail(game, "occupant_kind", exc)
            return None
    return None


def set_occupant_token(fn):
    """Register fn(obj, viewer) -> token id str or None for Room.Occupants.

    Wave 1 clients only understand ``person``; unknown hook values are
    coerced to that default in engine.gmcp. Pass None to clear.
    """
    global _occupant_token
    _occupant_token = fn


def occupant_token(obj, viewer):
    """Game override for one occupant row's ``token``, or None for person."""
    if _occupant_token is not None:
        try:
            return _occupant_token(obj, viewer)
        except Exception as exc:
            game = None
            if viewer is not None:
                game = getattr(getattr(viewer, "session", None), "game", None)
            _log_hook_fail(game, "occupant_token", exc)
            return None
    return None


def set_fishing_tables(fn):
    """Register fn() -> dict of fishing catch tables (JSON shape)."""
    global _fishing_tables
    _fishing_tables = fn


def fishing_tables():
    """Catch tables from the game, or empty dict when unset."""
    if _fishing_tables is not None:
        try:
            data = _fishing_tables()
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            game = None
            _log_hook_fail(game, "fishing_tables", exc)
            return {}
    return {}


def set_fishing_skill(fn):
    """Register fn(character) -> float effective fishing skill rank."""
    global _fishing_skill
    _fishing_skill = fn


def fishing_skill(character):
    """Effective fishing skill from the game, or 0.0 when unset."""
    if _fishing_skill is not None:
        try:
            return float(_fishing_skill(character))
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "fishing_skill", exc)
            return 0.0
    return 0.0


def set_cadence_meter_names(fn):
    """Register fn() -> tuple[str] of lifestyle meter names."""
    global _cadence_meter_names
    _cadence_meter_names = fn


def cadence_meter_names():
    """Meter names for Cadence seek scans (default hunger + thirst)."""
    if _cadence_meter_names is not None:
        try:
            names = _cadence_meter_names()
            if names:
                return tuple(names)
        except Exception as exc:
            _log_hook_fail(None, "cadence_meter_names", exc)
    from engine.systems.cadence_kernel import METER_NAMES_DEFAULT
    return METER_NAMES_DEFAULT


def set_cadence_seek_threshold(fn):
    """Register fn(character) -> float seek threshold for one actor."""
    global _cadence_seek_threshold
    _cadence_seek_threshold = fn


def cadence_seek_threshold(character):
    """Per-actor seek threshold (default ``needs.SEEK_THRESHOLD``)."""
    if _cadence_seek_threshold is not None:
        try:
            return float(_cadence_seek_threshold(character))
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "cadence_seek_threshold", exc)
    from engine.systems import needs as needs_engine
    return needs_engine.SEEK_THRESHOLD


def set_cadence_need_resource(fn):
    """Register fn(need_name) -> resource tag str or None."""
    global _cadence_need_resource
    _cadence_need_resource = fn


def cadence_need_resource(need):
    """Resource tag for a need meter (default hunger->food, thirst->water)."""
    if _cadence_need_resource is not None:
        try:
            tag = _cadence_need_resource(need)
            if tag is not None:
                return str(tag)
        except Exception as exc:
            _log_hook_fail(None, "cadence_need_resource", exc)
    from engine.systems.cadence_kernel import NEED_RESOURCE_DEFAULT
    return NEED_RESOURCE_DEFAULT.get(need)


def set_cadence_room_passable(fn):
    """Register fn(character, dest_room, game) -> bool for seek/wander BFS."""
    global _cadence_room_passable
    _cadence_room_passable = fn


def cadence_room_passable(character, dest_room, game):
    """True when ``character`` may walk into ``dest_room`` during Cadence BFS."""
    if _cadence_room_passable is not None:
        try:
            return bool(_cadence_room_passable(character, dest_room, game))
        except Exception as exc:
            _log_hook_fail(game, "cadence_room_passable", exc)
            return False
    origin = getattr(character, "location", None)
    origin_zone = getattr(origin, "zone", None)
    dest_zone = getattr(dest_room, "zone", None)
    if origin_zone is None and dest_zone is None:
        return True
    return origin_zone == dest_zone


def set_cadence_urgent_override(fn):
    """Register fn(character) -> need name str or None (soiled clothes, …)."""
    global _cadence_urgent_override
    _cadence_urgent_override = fn


def cadence_urgent_override(character):
    """Game override for the urgent need name, or None for meter scan."""
    if _cadence_urgent_override is not None:
        try:
            return _cadence_urgent_override(character)
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "cadence_urgent_override", exc)
            return None
    return None


def set_cadence_plan_override(fn):
    """Register fn(character, game) -> plan dict or None before kernel FSM."""
    global _cadence_plan_override
    _cadence_plan_override = fn


def cadence_plan_override(character, game):
    """Game-owned plan dict replacing the kernel FSM, or None."""
    if _cadence_plan_override is not None:
        try:
            return _cadence_plan_override(character, game)
        except Exception as exc:
            _log_hook_fail(game, "cadence_plan_override", exc)
            return None
    return None


def set_aboard_water_craft(fn):
    """Register fn(character) -> bool for offshore boat requirement."""
    global _aboard_water_craft
    _aboard_water_craft = fn


def aboard_water_craft(character):
    """True when the character is aboard a water craft (offshore fishing)."""
    if _aboard_water_craft is not None:
        try:
            return bool(_aboard_water_craft(character))
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "aboard_water_craft", exc)
            return False
    return False


def set_lock_dc(fn):
    """Register fn(kind) -> float | None override for lock difficulties."""
    global _lock_dc
    _lock_dc = fn


def lock_dc(kind):
    """Per-kind DC override from the game, or None for engine defaults."""
    if _lock_dc is not None:
        try:
            return _lock_dc(kind)
        except Exception as exc:
            game = None
            _log_hook_fail(game, "lock_dc", exc)
            return None
    return None


def set_skill_check(fn):
    """Register fn(character, skill_id, dc) -> bool for lock bypass rolls."""
    global _skill_check
    _skill_check = fn


def skill_check(character, skill_id, dc):
    """Run a registered skill check, or a simple random vs dc fallback."""
    if _skill_check is not None:
        try:
            return bool(_skill_check(character, skill_id, dc))
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "skill_check", exc)
    import random
    return random.random() * 100.0 >= float(dc)


def set_temporary_devils_trap(fn):
    """Register fn(room, game_time_ticks) -> bool for live circle overlays.

    SUPERS wires Hellcraft ``room_has_circle``; basegame leaves this unset
    (always False). Pass None to clear the hook.
    """
    global _temporary_devils_trap
    _temporary_devils_trap = fn


def temporary_devils_trap(room, game_time_ticks=0):
    """True when a hooked temporary devil's trap binds the room."""
    if _temporary_devils_trap is not None:
        try:
            return bool(_temporary_devils_trap(room, game_time_ticks))
        except Exception as exc:
            game = None
            _log_hook_fail(game, "temporary_devils_trap", exc)
            return False
    return False


def set_temporary_salt_line(fn):
    """Register fn(room, game_time_ticks) -> bool for live saltline overlays.

    SUPERS may wire Magic Warding rites; basegame leaves this unset. Pass
    None to clear the hook.
    """
    global _temporary_salt_line
    _temporary_salt_line = fn


def temporary_salt_line(room, game_time_ticks=0):
    """True when a hooked temporary salt line binds the room."""
    if _temporary_salt_line is not None:
        try:
            return bool(_temporary_salt_line(room, game_time_ticks))
        except Exception as exc:
            game = None
            _log_hook_fail(game, "temporary_salt_line", exc)
            return False
    return False


def set_occult_mark_blocks(fn):
    """Register fn(character, room) -> str | None for leave-gate refusals.

    Return a player-facing message when occult marks block exit; None when
    the engine move gate should keep checking other rules. Pass None to clear.
    """
    global _occult_mark_blocks
    _occult_mark_blocks = fn


def occult_mark_blocks(character, room):
    """Game policy for occult mark leave blocks, or None when unset."""
    if _occult_mark_blocks is not None:
        try:
            return _occult_mark_blocks(character, room)
        except Exception as exc:
            game = getattr(getattr(character, "session", None), "game", None)
            _log_hook_fail(game, "occult_mark_blocks", exc)
            return None
    return None


def set_is_corporeal_prison_plane(fn):
    """Register fn(plane) -> bool. Pass None to restore default."""
    global _is_corporeal_prison_plane
    _is_corporeal_prison_plane = fn


def is_corporeal_prison_plane(plane) -> bool:
    """True when this plane id is the corporeal-prison afterlife.

    Default: str(plane or "").strip().lower() == "purgatory".
    If a hook is set, call it; on exception _log_hook_fail and use default.
    """
    default = str(plane or "").strip().lower() == "purgatory"
    if _is_corporeal_prison_plane is not None:
        try:
            return bool(_is_corporeal_prison_plane(plane))
        except Exception as exc:
            _log_hook_fail(None, "is_corporeal_prison_plane", exc)
    return default


def set_web_map_cell_verbs(fn):
    """Register browser atlas right-click verbs, or None to clear."""
    global _web_map_cell_verbs
    _web_map_cell_verbs = fn


def web_map_cell_verbs(character, map_id, x, y, landmark):
    """Player-safe {label, command} rows for one atlas cell.

    Engine fallback: Enter using pocket enter_as aliases only. SUPERS
    adds walk / drive / atlas hub commands via the hook.
    """
    if _web_map_cell_verbs is not None:
        try:
            rows = _web_map_cell_verbs(character, map_id, x, y, landmark)
            if isinstance(rows, list):
                return _sanitize_web_map_verbs(rows)
        except Exception as exc:
            _log_hook_fail(
                getattr(getattr(character, "session", None), "game", None),
                "web_map_cell_verbs",
                exc,
            )
            return []
    return _default_web_map_verbs(landmark)


def _sanitize_web_map_verbs(rows):
    """Keep only label+command dicts with no internal keys in the command."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        command = str(row.get("command") or "").strip()
        if not label or not command:
            continue
        lower = command.lower()
        if "hub_room" in lower or "plot:" in lower or "gmspirit:" in lower:
            continue
        out.append({"label": label[:80], "command": command[:120]})
    return out


def _default_web_map_verbs(landmark):
    """Engine-only enter aliases when SUPERS has not registered a hook."""
    if not isinstance(landmark, dict):
        return []
    name = str(landmark.get("label") or "").strip()
    aliases = landmark.get("enter_as") or []
    if not aliases:
        return []
    alias = str(aliases[0]).strip()
    if not alias:
        return []
    shown = name or alias
    return [{"label": f"Enter {shown}", "command": f"enter {alias}"}]


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


def set_gmhelp(categories, overrides=None):
    """Inject the staff ``gmhelp`` index and topic-body overrides.

    categories: list of (heading, [topic names]) like HELP_CATEGORIES
    overrides: name -> page string (e.g. jargon internals, gmhelp hub)
    """
    global _gmhelp_categories, _gmhelp_topic_overrides
    _gmhelp_categories = list(categories) if categories is not None else []
    _gmhelp_topic_overrides = dict(overrides) if overrides else {}


def get_gmhelp_categories():
    """Return the injected GMHELP_CATEGORIES list (may be empty)."""
    return _gmhelp_categories


def get_gmhelp_topic_overrides():
    """Return staff-only page bodies that replace player HELP_TOPICS keys."""
    return _gmhelp_topic_overrides


def set_gm_only_help_keywords(keywords):
    """Register static help topic keys visible only to staff (``_is_gm``)."""
    global _gm_only_help_keywords
    _gm_only_help_keywords = frozenset(
        (k or "").strip().lower() for k in (keywords or ()) if (k or "").strip()
    )


def is_gm_only_help(keyword):
    """True when a static HELP_TOPICS key requires GM rank to view."""
    return (keyword or "").strip().lower() in _gm_only_help_keywords


def set_help_index_staff_view(fn):
    """Register fn(character) -> bool for GM/Builder help-index visibility.

    Pass None to restore the default (``_is_gm``).
    """
    global _help_index_staff_view
    _help_index_staff_view = fn


def help_index_staff_view(character):
    """True when this viewer should see GM/Builder bands on help indexes."""
    if _help_index_staff_view is not None:
        return bool(_help_index_staff_view(character))
    from engine.command_support import _is_gm
    return _is_gm(character)


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


def set_command_dispatch_gate(fn):
    """Register fn(character, verb, args, game) -> (blocked, message|None).

    Pass None to restore the default (never blocks). SUPERS registers
    cage / vessel / KO gates from ``supers.dispatch_gates``.
    """
    global _command_dispatch_gate
    _command_dispatch_gate = fn


def command_dispatch_gate(character, verb, args, game):
    """Run the game-owned dispatch gate, if any."""
    if _command_dispatch_gate is not None:
        return _command_dispatch_gate(character, verb, args, game)
    return False, None


_dispatch_on_input = None
_dispatch_idlemode_wake = None
_resolve_dispatch_actor = None
_pre_command_handler = None
_post_command_handler = None
_verb_disable_bypass = None


def set_dispatch_on_input(fn):
    """Register fn(character, verb, game) for idle stamp + walk interrupt."""
    global _dispatch_on_input
    _dispatch_on_input = fn


def dispatch_on_input(character, verb, game):
    """Run game-owned dispatch input side effects, if any."""
    if _dispatch_on_input is not None:
        _dispatch_on_input(character, verb, game)


def set_dispatch_idlemode_wake(fn):
    """Register fn(character, verb, game) -> bool (True = stop dispatch)."""
    global _dispatch_idlemode_wake
    _dispatch_idlemode_wake = fn


def try_idlemode_wake_dispatch(character, verb, game):
    """Maybe wake idlemode; return True when dispatch should return early."""
    if _dispatch_idlemode_wake is not None:
        return _dispatch_idlemode_wake(character, verb, game)
    return False


def set_resolve_dispatch_actor(fn):
    """Register fn(character, verb, game, force_actor) -> actor Character."""
    global _resolve_dispatch_actor
    _resolve_dispatch_actor = fn


def resolve_dispatch_actor(character, verb, game, force_actor=None):
    """Resolve which body runs the verb (login, twin, ghost host, …)."""
    if _resolve_dispatch_actor is not None:
        return _resolve_dispatch_actor(
            character, verb, game, force_actor=force_actor,
        )
    return force_actor if force_actor is not None else character


_heal_staff_occupy_session_desync = None


def set_heal_staff_occupy_session_desync(fn):
    """Register fn(character, game) -> corporeal Character for this Session."""
    global _heal_staff_occupy_session_desync
    _heal_staff_occupy_session_desync = fn


def heal_staff_occupy_session_desync(character, game):
    """Heal ranked-PC / cast occupy Session drift (bug report 1714)."""
    if _heal_staff_occupy_session_desync is not None:
        return _heal_staff_occupy_session_desync(character, game)
    return character


_sleep_dream_awareness = None


def set_sleep_dream_awareness(fn):
    """Register fn(character, game) -> Character|None for sleep-astral hop.

    When a live Session is stuck on a sleeping husk (or a God Mantle
    whose twin is asleep), hop awareness onto the Threshold dream-self
    before look / the world-closed gate. Pass None to restore the no-op.
    """
    global _sleep_dream_awareness
    _sleep_dream_awareness = fn


def maybe_sleep_dream_awareness(character, game):
    """Hop a stuck sleeper onto their dream-self, or return None."""
    if _sleep_dream_awareness is None:
        return None
    try:
        return _sleep_dream_awareness(character, game)
    except Exception as exc:
        _log_hook_fail(game, "sleep_dream_awareness", exc)
        return None


def set_pre_command_handler(fn):
    """Register fn(actor, verb, game) -> twin_owner|None before handler."""
    global _pre_command_handler
    _pre_command_handler = fn


def pre_command_handler(actor, verb, game):
    """Game hook before a matched handler runs."""
    if _pre_command_handler is not None:
        return _pre_command_handler(actor, verb, game)
    return None


def set_post_command_handler(fn):
    """Register fn(twin_owner, actor, game) after handler."""
    global _post_command_handler
    _post_command_handler = fn


def post_command_handler(twin_owner, actor, game):
    """Game hook after a matched handler runs."""
    if _post_command_handler is not None:
        _post_command_handler(twin_owner, actor, game)


def set_verb_disable_bypass(fn):
    """Register fn(game, verb, actor) -> bool for disabled-verb bypass."""
    global _verb_disable_bypass
    _verb_disable_bypass = fn


def verb_disable_bypass(game, verb, actor):
    """True when a staff-disabled verb should still run for this actor."""
    if _verb_disable_bypass is not None:
        return _verb_disable_bypass(game, verb, actor)
    return False


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


def set_room_command_hints(fn):
    """Register fn(character, game) -> list[str] for ``commands here``.

    Pass None to restore the empty default. Games list hubs and verbs
    relevant to the viewer's current room (boards, desks, cabins).
    Engine never invents game-specific board copy.
    """
    global _room_command_hints
    _room_command_hints = fn


def room_command_hints(character, game):
    """Return room-scoped command hint lines, or []."""
    if _room_command_hints is None:
        return []
    return list(_room_command_hints(character, game) or [])


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


def set_monster_sense_message(fn):
    """Register fn(character, room) -> str or None for the post-look fear nudge."""
    global _monster_sense_message
    _monster_sense_message = fn


def monster_sense_message(character, room):
    """Return the monster-vs-Called Slayer fear line, or None if none due."""
    if _monster_sense_message is not None:
        return _monster_sense_message(character, room)
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


def register_sheet_field(field_id, fn, **kwargs):
    """Register a ``hook:<field_id>`` row in ``engine/content/sheet_profile.json``.

    Extra kwargs (``slot``, ``panes``, ``compact``, ``filter_prefixes``)
    go to ``register_auto_field`` so a meter can appear before it has a
    catalog row. When the catalog already lists ``field_id``, kwargs are
    ignored and this is a plain hook install.
    """
    from engine.systems import sheet as sheet_mod

    if kwargs:
        sheet_mod.register_auto_field(field_id, fn, **kwargs)
    else:
        sheet_mod.register_field_hook(field_id, fn)


def register_sheet_contributor(section_id, fn, *, priority=100):
    """Register fn(ctx) -> SheetSection | list | None for score assembly."""
    from engine.systems import sheet as sheet_mod

    sheet_mod.register_contributor(section_id, fn, priority=priority)


def register_wallet_ledger_listener(fn):
    """Register ``fn(character, row)`` after each wallet ledger append."""
    from engine.systems import economy as economy_mod

    economy_mod.register_wallet_ledger_listener(fn)


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


def set_character_placement_block(fn):
    """Register fn(character, dest, game) -> block message or None.

    Called from ``Character.move_to`` before a character enters ``dest``
    (teleports, gm goto, cageproject, boot heals, …). Walks still hit
    ``move_gate_block`` first via ``cmd_move``.
    """
    global _character_placement_block
    _character_placement_block = fn


def character_placement_block(character, dest, game):
    """Return a message blocking placement into ``dest``, or None."""
    if _character_placement_block is not None:
        return _character_placement_block(character, dest, game)
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


def set_clinic_pre_admit(fn):
    """Register fn(character, game, *, reason, attacker=None) -> bool."""
    global _clinic_pre_admit
    _clinic_pre_admit = fn


def clinic_pre_admit(character, game, *, reason="injury", attacker=None):
    """Game policy gate before ward selection / engine admit."""
    if _clinic_pre_admit is None:
        return True
    return bool(_clinic_pre_admit(character, game, reason=reason, attacker=attacker))


def set_clinic_admit_prologue(fn):
    """Register fn(character, game, *, reason, attacker=None) before engine admit."""
    global _clinic_admit_prologue
    _clinic_admit_prologue = fn


def clinic_admit_prologue(character, game, *, reason="injury", attacker=None):
    """Run game hook after ward pick, before ``clinic_engine.admit``."""
    if _clinic_admit_prologue is not None:
        _clinic_admit_prologue(
            character, game, reason=reason, attacker=attacker,
        )


def set_clinic_ward_tick(fn):
    """Register fn(patient, room, game, *, now, ready) per hospitalized tick."""
    global _clinic_ward_tick
    _clinic_ward_tick = fn


def clinic_ward_tick(patient, room, game, *, now, ready=False):
    """Run game hook for ward vitals (blood, mood, region regen, …)."""
    if _clinic_ward_tick is not None:
        _clinic_ward_tick(patient, room, game, now=now, ready=ready)


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


def set_identity_verify_hook(fn):
    """Register fn(subject, doc, *, context, game) -> VerifyResult overlay."""
    global _identity_verify_hook
    _identity_verify_hook = fn


def identity_verify_hook(subject, doc, *, context="local", game=None):
    """Optional game policy overlay for ``identity.verify_id``."""
    if _identity_verify_hook is not None:
        return _identity_verify_hook(subject, doc, context=context, game=game)
    return None


def set_identity_pierce_supernatural(fn):
    """Register fn(viewer, subject) -> bool for angel/vampire ID pierce."""
    global _identity_pierce_supernatural
    _identity_pierce_supernatural = fn


def identity_pierce_supernatural(viewer, subject):
    """True when a supernatural sense should pierce paperwork / costume."""
    if _identity_pierce_supernatural is not None:
        return bool(_identity_pierce_supernatural(viewer, subject))
    return False


def set_disguise_pierce_check(fn):
    """Register fn(viewer, subject) -> bool when disguise fails."""
    global _disguise_pierce_check
    _disguise_pierce_check = fn


def disguise_pierce_check(viewer, subject):
    """Run registered disguise pierce check."""
    if _disguise_pierce_check is not None:
        return bool(_disguise_pierce_check(viewer, subject))
    from engine.systems import disguise as disguise_mod
    return disguise_mod.pierce_disguise(viewer, subject)


def set_follow_pull_skip(fn):
    """Register fn(follower, leader, game) -> True to skip follow-pull."""
    global _follow_pull_skip
    _follow_pull_skip = fn


def follow_pull_skip(follower, leader, game):
    """True when leader move must not drag this follower along."""
    if _follow_pull_skip is not None:
        return bool(_follow_pull_skip(follower, leader, game))
    return False


def set_follow_pull_handled(fn):
    """Register fn(follower, origin, dest, game) -> True if it relocated them.

    Used when a follower is aboard their own ride: park that vehicle at
    ``dest`` instead of yanking the rider off the saddle with ``_move_one``.
    """
    global _follow_pull_handled
    _follow_pull_handled = fn


def follow_pull_handled(follower, origin, dest, game):
    """True when a game hook already moved this follower to ``dest``."""
    if _follow_pull_handled is not None:
        return bool(_follow_pull_handled(follower, origin, dest, game))
    return False


def set_follow_pull_homestead_plot(fn):
    """Register fn(follower, leader, origin, dest, game) -> bool."""
    global _follow_pull_homestead_plot
    _follow_pull_homestead_plot = fn


def follow_pull_homestead_plot(follower, leader, origin, dest, game):
    """True when a homestead pet may pull from another room on the plot."""
    if _follow_pull_homestead_plot is not None:
        return bool(_follow_pull_homestead_plot(
            follower, leader, origin, dest, game,
        ))
    return False


def set_ensure_homestead_plot_graph_for_actor(fn):
    """Register fn(character, game) -> bool for homestead exit-graph repair.

    Called from ``exits`` and from ``cmd_move`` when a compass direction has
    no ``Room.exits`` entry. True means the game rewired at least one exit
    on this call (caller may retry the move once). Pass None to clear.
    """
    global _ensure_homestead_plot_graph_for_actor
    _ensure_homestead_plot_graph_for_actor = fn


def ensure_homestead_plot_graph_for_actor(character, game):
    """Self-heal a homestead plot graph when the actor stands on one.

    Default False (no game registered, or plot already validated this session).
    """
    if _ensure_homestead_plot_graph_for_actor is not None:
        return bool(_ensure_homestead_plot_graph_for_actor(character, game))
    return False


def set_follow_pull_household_pet(fn):
    """Register fn(follower, leader, origin, dest, game) -> bool."""
    global _follow_pull_household_pet
    _follow_pull_household_pet = fn


def follow_pull_household_pet(follower, leader, origin, dest, game):
    """True when a bonded beast pet was relocated with the owner."""
    if _follow_pull_household_pet is not None:
        return bool(_follow_pull_household_pet(
            follower, leader, origin, dest, game,
        ))
    return False


def set_follow_shares_origin(fn):
    """Register fn(follower, origin, game) -> True when they share the curb."""
    global _follow_shares_origin
    _follow_shares_origin = fn


def follow_shares_origin(follower, origin, game):
    """True when a boarded follower's ride is still at ``origin``."""
    if _follow_shares_origin is not None:
        return bool(_follow_shares_origin(follower, origin, game))
    return False


def set_group_share_travel_spot(fn):
    """Register fn(a, b, game) -> True when two bodies share a curb or trail cell."""
    global _group_share_travel_spot
    _group_share_travel_spot = fn


def group_share_travel_spot(a, b, game=None):
    """True when separate rides still count as the same group location."""
    if _group_share_travel_spot is not None:
        return bool(_group_share_travel_spot(a, b, game))
    return False


_follow_survival_peel_message = None


def set_follow_survival_peel_message(fn):
    """Register fn(follower, leader) -> str when follower peels off for needs."""
    global _follow_survival_peel_message
    _follow_survival_peel_message = fn


def follow_survival_peel_message(follower, leader):
    """Player message when a live follower stops trailing for critical needs.

    Default (no game installed): None (no peel). SUPERS registers survival
  meter checks from ``supers.needs``.
    """
    if _follow_survival_peel_message is not None:
        return _follow_survival_peel_message(follower, leader)
    return None


def set_report_context_extra(fn):
    """Register fn(character, game) -> dict for bug/suggest report context."""
    global _report_context_extra
    _report_context_extra = fn


def _log_hook_fail(game, key, exc):
    """Rate-limited stderr when a registered hook raises."""
    from engine import log_util
    log_util.ops_once_per_tick(
        game,
        key,
        "hooks",
        f"{key} failed ({exc!r})",
        exc=exc,
    )


def report_context_extra(character, game, *, history=None, description=None):
    """Optional SUPERS gameplay facts merged into filed reports."""
    if _report_context_extra is not None:
        try:
            try:
                extra = _report_context_extra(
                    character, game,
                    history=history, description=description,
                )
            except TypeError:
                # Older hook signatures take only (character, game).
                extra = _report_context_extra(character, game)
        except Exception as exc:
            _log_hook_fail(game, "report_context_extra", exc)
            return {"context_errors": [f"report_context_extra: {exc!r}"]}
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
        except Exception as exc:
            _log_hook_fail(game, "room_broadcast_deliver", exc)
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
        except Exception as exc:
            _log_hook_fail(game, "room_broadcast_transform", exc)
            return text
    return text


def set_room_broadcast_after(fn):
    """Register fn(room, message, game, *, exclude=None) after in-room delivery.

    Lets a game fan room traffic to extra listeners (observation screens)
    without the engine importing game code. ``message`` is the same payload
    Room.broadcast received (str or per-watcher callable).
    """
    global _room_broadcast_after
    _room_broadcast_after = fn


def room_broadcast_after(room, message, game=None, *, exclude=None):
    """Optional extra listeners after a room.broadcast in-room loop."""
    if _room_broadcast_after is not None:
        try:
            _room_broadcast_after(room, message, game, exclude=exclude)
        except Exception as exc:
            _log_hook_fail(game, "room_broadcast_after", exc)


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
        except Exception as exc:
            _log_hook_fail(game, "perception_character", exc)
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
        except Exception as exc:
            _log_hook_fail(game, "preference_character", exc)
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


def set_filter_zone_entries(fn):
    """Register fn(viewer, room, entries, game) -> filtered entries dict."""
    global _filter_zone_entries
    _filter_zone_entries = fn


def filter_zone_entries(viewer, room, entries, game):
    """Return zone_entries visible to viewer (site mask may hide hubs)."""
    base = dict(entries or {})
    if _filter_zone_entries is None:
        return base
    try:
        filtered = _filter_zone_entries(viewer, room, base, game)
    except TypeError:
        filtered = _filter_zone_entries(viewer, room, base)
    if filtered is None:
        return base
    return dict(filtered)


def set_filter_zone_entries_look(fn):
    """Register fn(viewer, room, entries, game) -> look-hint entries dict."""
    global _filter_zone_entries_look
    _filter_zone_entries_look = fn


def filter_zone_entries_look(viewer, room, entries, game):
    """Look-hint subset of zone_entries (fixture dedupe; travel unchanged)."""
    base = dict(entries or {})
    if _filter_zone_entries_look is None:
        return base
    try:
        filtered = _filter_zone_entries_look(viewer, room, base, game)
    except TypeError:
        filtered = _filter_zone_entries_look(viewer, room, base)
    if filtered is None:
        return base
    return dict(filtered)


def set_try_civic_fixture_enter(fn):
    """Register fn(character, raw, game) -> bool (True when enter handled)."""
    global _try_civic_fixture_enter
    _try_civic_fixture_enter = fn


def try_civic_fixture_enter(character, raw, game):
    """Best-effort civic fixture enter before zone-not-found refusal."""
    if _try_civic_fixture_enter is None:
        return False
    try:
        return bool(_try_civic_fixture_enter(character, raw, game))
    except TypeError:
        return bool(_try_civic_fixture_enter(character, raw))


def set_look_exit_hidden_from_viewer(fn):
    """Register fn(viewer, room, direction, dest, game) -> bool (True hides)."""
    global _look_exit_hidden_from_viewer
    _look_exit_hidden_from_viewer = fn


def look_exit_hidden_from_viewer(viewer, room, direction, dest, game):
    if _look_exit_hidden_from_viewer is None:
        return False
    try:
        return bool(
            _look_exit_hidden_from_viewer(
                viewer, room, direction, dest, game,
            )
        )
    except TypeError:
        return bool(
            _look_exit_hidden_from_viewer(viewer, room, direction, dest)
        )


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


def set_look_exit_door_closed(fn):
    """Register fn(room, direction, dest, game=None, character=None) -> bool."""
    global _look_exit_door_closed
    _look_exit_door_closed = fn


def look_exit_door_closed(room, direction, dest, game=None, character=None):
    """True when look/exits should show this exit as door-closed."""
    if _look_exit_door_closed is None:
        return False
    try:
        return bool(
            _look_exit_door_closed(
                room, direction, dest, game=game, character=character,
            )
        )
    except TypeError:
        try:
            return bool(
                _look_exit_door_closed(room, direction, dest, game)
            )
        except TypeError:
            return bool(_look_exit_door_closed(room, direction, dest))


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


# fn(character, body, game) -> refusal str | None
_body_loot_refusal = None

# fn(body) -> iterable of Item
_iter_body_loot_items = None


def set_body_loot_refusal(fn):
    """Register pre-loot gate for ``get <item> from <body>`` / ``loot``."""
    global _body_loot_refusal
    _body_loot_refusal = fn


def body_loot_refusal(character, body, game=None):
    """Refusal message when corpse loot is blocked; None when allowed."""
    if _body_loot_refusal is None:
        return None
    return _body_loot_refusal(character, body, game)


def set_iter_body_loot_items(fn):
    """Register nested loot enumeration for corpse bodies."""
    global _iter_body_loot_items
    _iter_body_loot_items = fn


def iter_body_loot_items(body):
    """Yield lootable items on a corpse (game hook or generic nested loot)."""
    if _iter_body_loot_items is not None:
        yield from _iter_body_loot_items(body)
        return
    for entry in list(getattr(body, "loot", None) or []):
        yield entry
    for piece in (getattr(body, "body_equipment", None) or {}).values():
        if piece is not None:
            yield piece
    for stack in (getattr(body, "body_clothing", None) or {}).values():
        for piece in stack or []:
            if piece is not None:
                yield piece


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
# Before ``give`` transfers an item (in-room). Games may refuse Echo
# gift policy without engine importing the game package.
# fn(giver, target, item) -> optional refusal str, or None.
_before_give = None
# After a successful in-room ``give``. Games may count courtship gifts
# without engine importing the game package.
# fn(giver, target, item, amount=1, game=None) -> None.
_after_give = None


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


def set_before_give(fn):
    """Register fn(giver, target, item) -> str|None before ``give`` transfers.

    Return a refusal message to abort the hand-off, or None to allow it.
    Pass None to restore the no-op default.
    """
    global _before_give
    _before_give = fn


def before_give(giver, target, item):
    """Run the pre-give hook; return refusal message or None if allowed."""
    if _before_give is not None:
        return _before_give(giver, target, item)
    return None


_try_give_cash = None


def set_try_give_cash(fn):
    """Register fn(giver, target, cents, game) -> bool for pocket-cash ``give``.

    Return True when the hook handled the transfer (messages already sent).
    Pass None to restore the no-op default (``give $N`` falls through to items).
    """
    global _try_give_cash
    _try_give_cash = fn


def try_give_cash(giver, target, cents, game):
    """Hand pocket cash via ``give $N to <name>`` when a game hook is registered."""
    if _try_give_cash is not None:
        return _try_give_cash(giver, target, cents, game)
    return False


def set_after_give(fn):
    """Register fn(giver, target, item, amount=1, game=None) after ``give``.

    Called only when at least one unit actually changed hands. Pass None
    to restore the no-op default.
    """
    global _after_give
    _after_give = fn


def after_give(giver, target, item, amount=1, game=None):
    """Run the post-give hook (courtship, quests, etc.)."""
    if _after_give is not None:
        _after_give(giver, target, item, amount=amount, game=game)


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

    Default (no game installed): False. SUPERS registers innate astral
    perception (Angels / Demons / Gods / Cosmic) / glasses / True Sight
    Deal checks from ``supers.hellhounds``.
    """
    if _can_see_hellhound is not None:
        return bool(_can_see_hellhound(viewer, hound))
    return False


def set_can_target_hidden_attacker(fn):
    """Register fn(viewer, other) -> bool for hidden-attacker targeting.

  Pass None to restore the default: allow return fire (True).
    """
    global _can_target_hidden_attacker
    _can_target_hidden_attacker = fn


def can_target_hidden_attacker(viewer, other):
    """May ``viewer`` target ``other`` when hidden but already attacking?

    Default (no game installed): True. SUPERS registers Deal hellhound
    sight / kill-tool gates from ``supers.hellhounds``.
    """
    if _can_target_hidden_attacker is not None:
        return bool(_can_target_hidden_attacker(viewer, other))
    return True


def set_is_untargetable_helper(fn):
    """Register fn(character) -> bool for registered combat helpers.

    Pass None to restore the default: helpers are targetable (False).
    """
    global _is_untargetable_helper
    _is_untargetable_helper = fn


def is_untargetable_helper(character):
    """True when ``character`` is a registered combat helper (game-owned).

    Default (no game installed): False. SUPERS registers from
    ``supers.combat_helpers``.
    """
    if _is_untargetable_helper is not None:
        return bool(_is_untargetable_helper(character))
    return False


def set_chargen_fallback_start_room(fn):
    """Register fn(character, game) -> Room | None for post-chargen placement.

    Pass None to restore the default: no game-specific fallback room.
    """
    global _chargen_fallback_start_room
    _chargen_fallback_start_room = fn


def chargen_fallback_start_room(character, game):
    """Resolve a start room when chargen room key lookup failed.

    Default (no game installed): None. SUPERS may register tutorial-specific
    rooms (e.g. vampire nest when Lebanon homezones are still wiring).
    """
    if _chargen_fallback_start_room is not None:
        return _chargen_fallback_start_room(character, game)
    return None


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


def set_veil_soul_label(fn):
    """Register fn(viewer, other, base_label) -> str for Veil look lines."""
    global _veil_soul_label
    _veil_soul_label = fn


def veil_soul_label(viewer, other, base_label: str) -> str:
    """Face label for a Veil occupant in look / scan (perception axis)."""
    if _veil_soul_label is not None:
        return _veil_soul_label(viewer, other, base_label)
    return f"{veil_look_tag()} {base_label}".strip()


def set_item_visible_to(fn):
    """Register fn(viewer, item) -> bool for floor/target Item sight.

    Pass None to restore the default: every Item is visible.
    """
    global _item_visible_to
    _item_visible_to = fn


def item_visible_to(viewer, item):
    """Can ``viewer`` perceive ``item`` on the floor or in targeting?

    Default (no game installed): True. SUPERS hides Veil-layer ghost
    copies from ordinary living sight.
    """
    if _item_visible_to is not None:
        return bool(_item_visible_to(viewer, item))
    return True


def set_item_in_veil(fn):
    """Register fn(item) -> bool for Veil-layer floor gear.

    Pass None to restore the default: no Item is Veil-layer.
    """
    global _item_in_veil
    _item_in_veil = fn


def item_in_veil(item):
    """Is ``item`` Veil-layer ghost gear (look tag / scavenger skip)?

    Default (no game installed): False.
    """
    if _item_in_veil is not None:
        return bool(_item_in_veil(item))
    return False


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


def set_presence_hidden_extra(fn):
    """Register fn(viewer, other) -> bool for extra look/targeting hide."""
    global _presence_hidden_extra
    _presence_hidden_extra = fn


def presence_hidden_extra(viewer, other) -> bool:
    """True when a game hook hides ``other`` from ``viewer`` (not stealth)."""
    if viewer is other or other is None or viewer is None:
        return False
    if _presence_hidden_extra is None:
        return False
    try:
        return bool(_presence_hidden_extra(viewer, other))
    except Exception:
        return False


def set_extra_affect_rows(fn):
    """Register fn(character) -> list[dict] for ``aff`` / ``affects``."""
    global _extra_affect_rows
    _extra_affect_rows = fn


def extra_affect_rows(character):
    """Optional game-provided affect rows (combat conditions, veils, …)."""
    if _extra_affect_rows is None:
        return []
    try:
        rows = _extra_affect_rows(character)
    except Exception as exc:
        game = getattr(getattr(character, "session", None), "game", None)
        _log_hook_fail(game, "extra_affect_rows", exc)
        return []
    return list(rows or [])


def set_can_perceive_trickster_laylow(fn):
    """Register fn(viewer, other) -> bool for trickster lay-low pierce."""
    global _can_perceive_trickster_laylow
    _can_perceive_trickster_laylow = fn


def can_perceive_trickster_laylow(viewer, other):
    """Can `viewer` see `other` while `other` is laying low (withdrawn)?

    Default (no game installed): True -- bare engine has no trickster kit.
    SUPERS registers ``trickster_laylow.pierce_lay_low`` (staff, trickster
    library research, logged trickster field pages, or tier-3+ grace-sight).
    """
    if viewer is other:
        return True
    if _can_perceive_trickster_laylow is not None:
        return bool(_can_perceive_trickster_laylow(viewer, other))
    return True


def set_staff_tags_hidden_presence(fn):
    """Register fn(viewer, other) -> bool for gm-on look (hidden) tags."""
    global _staff_tags_hidden_presence
    _staff_tags_hidden_presence = fn


def staff_tags_hidden_presence(viewer, other):
    """True when staff in gm on should see a hidden body in look tagged."""
    if _staff_tags_hidden_presence is not None:
        return bool(_staff_tags_hidden_presence(viewer, other))
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


def set_character_relocated(fn):
    """Register fn(character, old_room, new_room, game) after a room change.

    Called from ``Character.move_to`` while the actor is still listed in
    ``old_room`` so pair proximity can be dropped. Pass None to clear.
    """
    global _character_relocated
    _character_relocated = fn


def character_relocated(character, old_room, new_room, game=None):
    """Run the registered relocate hook, or do nothing if none is set."""
    if _character_relocated is not None:
        _character_relocated(character, old_room, new_room, game)


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


def set_before_move_crossing(fn):
    """Register fn(character, from_room, direction, dest, game) before leave."""
    global _before_move_crossing
    _before_move_crossing = fn


def before_move_crossing(character, from_room, direction, dest, game):
    """Auto open/unlock structure doors the actor may pass (SUPERS hook)."""
    if _before_move_crossing is not None:
        _before_move_crossing(character, from_room, direction, dest, game)


def set_after_move_crossing(fn):
    """Register fn(character, from_room, direction, dest, game) after move."""
    global _after_move_crossing
    _after_move_crossing = fn


def after_move_crossing(character, from_room, direction, dest, game):
    """Auto close doors left open for a permitted crossing (SUPERS hook)."""
    if _after_move_crossing is not None:
        _after_move_crossing(character, from_room, direction, dest, game)
    # Engine-side Cadence ping-pong stamp (NPC / Echo / idlemode only).
    # Lives here so we have ``from_room`` without extending ``after_move_step``.
    try:
        from engine.report_debug import note_cadence_pingpong_on_move

        note_cadence_pingpong_on_move(character, from_room, dest, game)
    except Exception:
        pass


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


# Crowd-throttled hub traffic + watch-room stance (SUPERS public_watch).
# One paired registrar so move-hears sampling and traffic-quiet prefs
# cannot drift apart (same pattern as set_tips_hooks).
_movement_hears_predicate = None
_is_watching_room = None


def set_public_watch_hooks(movement_hears_fn=None, is_watching_room_fn=None):
    """Register public-watch hooks for move hears + traffic quiet prefs.

    movement_hears_fn(mover, origin, dest, game, base_hears) -> predicate
    is_watching_room_fn(character, game=None) -> bool
    Pass None to clear either slot (bare engine: pass through base_hears /
    not watching).
    """
    global _movement_hears_predicate, _is_watching_room
    _movement_hears_predicate = movement_hears_fn
    _is_watching_room = is_watching_room_fn


def movement_hears_predicate(mover, origin, dest, game, base_hears):
    """Wrap base_hears with crowd/watch sampling, or pass through when unset."""
    if _movement_hears_predicate is not None:
        return _movement_hears_predicate(mover, origin, dest, game, base_hears)
    return base_hears


def is_watching_room(character, game=None):
    """True when the actor holds room-surveillance stance; False when unset."""
    if _is_watching_room is not None:
        return bool(_is_watching_room(character, game=game))
    return False


# Helper-ticket pings (query / queryread). Bare engine still opens tickets;
# these only notify online helpers / the reporter.
_query_notify_event = None
_query_notify_reporter_live = None


def set_query_notify_hooks(event_fn=None, reporter_live_fn=None):
    """Register query-ticket notify helpers, or None to clear.

    event_fn(game, entry, *, event) -> None
    reporter_live_fn(game, entry, *, author_key, text) -> None
    """
    global _query_notify_event, _query_notify_reporter_live
    _query_notify_event = event_fn
    _query_notify_reporter_live = reporter_live_fn


def notify_query_event(game, entry, *, event="open"):
    """Ping online helpers about a query change, or no-op when unset."""
    if _query_notify_event is not None:
        _query_notify_event(game, entry, event=event)


def notify_reporter_live(game, entry, *, author_key, text):
    """Tell the reporter a helper replied, or no-op when unset."""
    if _query_notify_reporter_live is not None:
        _query_notify_reporter_live(
            game, entry, author_key=author_key, text=text,
        )


def set_account_roster_label_for(fn):
    """Register fn(game, key, body=None) -> str for account roster lines."""
    global _account_roster_label_for
    _account_roster_label_for = fn


def _engine_roster_identity_tag(body):
    """Minimal Origin / Path / Aspect tag when no game hook is registered.

    Uses raw field ids (title-cased) so login still shows *something* after
    ``importlib.reload(hooks)`` clears bootstrap callbacks mid-process.
    """
    if body is None:
        return ""
    parts = []
    for attr in ("origin", "path", "aspect"):
        val = getattr(body, attr, None)
        if val:
            text = str(val).strip().replace("_", " ")
            parts.append(text.title())
    if not parts:
        return "Human"
    return " / ".join(parts)


def account_roster_label_for(game, key, body=None):
    """Roster label for account menu / ``account`` command."""
    if _account_roster_label_for is not None:
        return _account_roster_label_for(game, key, body)
    from engine.command_support import _presence_face

    if body is not None:
        face = _presence_face(body)
        tag = _engine_roster_identity_tag(body)
        if tag:
            return f"{face} ({tag})"
        return face
    return (key or "").strip() or "(unknown)"


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
    except Exception as exc:
        game = getattr(getattr(viewer, "session", None), "game", None)
        _log_hook_fail(game, "extra_target_match_needles", exc)
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


def set_look_detail_for(fn):
    """Register fn(viewer, subject, keyword) -> str|None for look <who> <part>."""
    global _look_detail_for
    _look_detail_for = fn


def look_detail_for(viewer, subject, keyword):
    """Named look-target body, or None when the hook is unset."""
    if _look_detail_for is not None:
        return _look_detail_for(viewer, subject, keyword)
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


def set_staff_login_cast_keys(fn):
    """Register fn() -> iterable of lowercase immersion keys staff may occupy.

    Idle fixture NPCs (Ash) stay ``is_npc`` until ridden. Pass None to clear.
    """
    global _staff_login_cast_keys
    _staff_login_cast_keys = fn


def staff_login_cast_keys():
    """Lowercase catalog keys with ``staff_login`` (empty when unset)."""
    if _staff_login_cast_keys is None:
        return frozenset()
    try:
        return frozenset(
            (k or "").strip().lower()
            for k in (_staff_login_cast_keys() or ())
            if k
        )
    except Exception as exc:
        _log_hook_fail(None, "staff_login_cast_keys", exc)
        return frozenset()


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


# fn(game, character_key) -> Character | None
_try_restore_assigned_character = None


def set_try_restore_assigned_character(fn):
    """Register fn(game, key) -> Character|None for vaulted assigned cast."""
    global _try_restore_assigned_character
    _try_restore_assigned_character = fn


def try_restore_assigned_character(game, key):
    """Hydrate a vaulted immersion cast body for RPC assignment lists."""
    if _try_restore_assigned_character is None:
        return None
    return _try_restore_assigned_character(game, key)


# fn(game, given_name, surname="") -> Character | None
_try_restore_folded_login_identity = None


def set_try_restore_folded_login_identity(fn):
    """Register identity-based vault restore (given + optional surname)."""
    global _try_restore_folded_login_identity
    _try_restore_folded_login_identity = fn


def try_restore_folded_login_identity(game, given_name, surname=""):
    """Hydrate a hard-folded Echo by public identity, or None."""
    if _try_restore_folded_login_identity is None:
        return None
    return _try_restore_folded_login_identity(game, given_name, surname or "")


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


# Account-scoped mid-create resume rows (``account_chargen_draft``).
# fn(game, name) -> bool
_is_chargen_draft_vault = None


def set_is_chargen_draft_vault(fn):
    """Register fn(game, name) -> bool for create-flow draft vault rows."""
    global _is_chargen_draft_vault
    _is_chargen_draft_vault = fn


def is_chargen_draft_vault(game, name):
    """True when ``name`` is vaulted for incomplete account create flow."""
    if _is_chargen_draft_vault is None:
        return False
    return bool(_is_chargen_draft_vault(game, name))


# Chargen-draft vault + generic hard-fold extract (account_chargen_draft).
# fn(game, name) -> folded_by tag str | None
_vault_folded_by = None


def set_vault_folded_by(fn):
    """Register fn(game, storage_key) -> vault tag string or empty."""
    global _vault_folded_by
    _vault_folded_by = fn


def vault_folded_by(game, storage_key):
    """Return the ``folded_by`` tag for a vaulted key, or ``None`` when unset."""
    if _vault_folded_by is None:
        return None
    return _vault_folded_by(game, storage_key)


# fn(character, game, folded_by=None, skip_save=False) -> (ok, msg)
_extract_to_vault = None


def set_extract_to_vault(fn):
    """Register hard-fold extract for partial bodies / chargen drafts."""
    global _extract_to_vault
    _extract_to_vault = fn


def extract_to_vault(character, game, *, folded_by=None, skip_save=False):
    """Vault ``character`` off-world; default refuses when no game registered."""
    if _extract_to_vault is None:
        return False, "Vault unavailable."
    return _extract_to_vault(
        character,
        game,
        folded_by=folded_by,
        skip_save=skip_save,
    )


# fn(character, game, folded_by=None) -> bool
_upsert_vault_snapshot = None


def set_upsert_vault_snapshot(fn):
    """Register a vault upsert that does not despawn the live body."""
    global _upsert_vault_snapshot
    _upsert_vault_snapshot = fn


def upsert_vault_snapshot(character, game, *, folded_by=None):
    """Snapshot ``character`` into the vault without removing them."""
    if _upsert_vault_snapshot is None:
        return False
    return bool(
        _upsert_vault_snapshot(character, game, folded_by=folded_by)
    )


# fn(game, name) -> Character | None -- mid-create copyover restore.
_try_restore_chargen_draft = None


def set_try_restore_chargen_draft(fn):
    """Register fn(game, name) -> Character for copyover chargen resume."""
    global _try_restore_chargen_draft
    _try_restore_chargen_draft = fn


def try_restore_chargen_draft(game, name):
    """Hydrate a chargen-draft vault row without placing it in a room."""
    if _try_restore_chargen_draft is None:
        return None
    return _try_restore_chargen_draft(game, name)


# Account login menu: hard-folded bodies still listed on an account roster.
# fn(game, account, live_character_keys_low: set[str])
#     -> list[(section, entry)]
_account_menu_vault_rows = None


def set_account_menu_vault_rows(fn):
    """Register vaulted roster rows for ``account_login.build_account_menu``."""
    global _account_menu_vault_rows
    _account_menu_vault_rows = fn


def account_menu_vault_rows(game, account, live_character_keys_low):
    """Extra menu rows for vaulted characters not already live in memory."""
    if _account_menu_vault_rows is None:
        return []
    return list(
        _account_menu_vault_rows(game, account, live_character_keys_low) or []
    )


# fn(game, entry) -> Character | entry
_restore_vault_menu_entry = None


def set_restore_vault_menu_entry(fn):
    """Register restore for a vaulted account-menu row pick."""
    global _restore_vault_menu_entry
    _restore_vault_menu_entry = fn


def restore_vault_menu_entry(game, entry):
    """Hydrate a vaulted menu row into a live Character, or pass through."""
    if _restore_vault_menu_entry is None:
        return entry
    return _restore_vault_menu_entry(game, entry)


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

# Game-layer ``open <dir>`` / ``close <dir>`` (SUPERS lodging/structure
# doors). Distinct from ``engine/systems/doors.py`` persisted lock/open
# graph state -- do not merge the two.
_try_directional_open = None

# Landmark / dual-layer `enter` before classic zone_entries.
# True = handled. fn(character, args, game) -> bool
_try_enter_zone = None

# Floor ``get`` miss: scrape / forage a room prop that is not an Item yet.
# True = handled (messages already sent). fn(character, args, game) -> bool
_try_get_unlisted = None

# Boarded vehicle: ``enter`` / ``in`` / ``out`` while in_vehicle is set.
# fn(character, args, game) -> bool  /  fn(character, game, direction=) -> bool
_try_vehicle_enter_as_house_in = None


# --- Fuel-loop roster hook (lag P8.4) -------------------------------------
# SUPERS keeps ``game.fuel_loop_characters`` in sync so ``fuel.tick_all``
# never walks the full live roster, and also invalidates Cadence town /
# hostile-tail caches. fn(game, character, *, op) where op is
# ``add`` | ``remove`` | ``rebuild`` (character ignored on rebuild).
# Do not add a second after-index-mutate registrar beside this one.
_fuel_loop_roster_hook = None


def set_fuel_loop_roster_hook(fn):
    """Register live-roster mutate side effects, or None to clear.

    SUPERS uses this for the fuel-loop index *and* Cadence roster caches.
    """
    global _fuel_loop_roster_hook
    _fuel_loop_roster_hook = fn


def fuel_loop_roster_notify(game, character, *, op):
    """Notify the game hook that the live roster changed."""
    if _fuel_loop_roster_hook is None:
        return
    _fuel_loop_roster_hook(game, character, op=op)


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


# --- Spawn / erase / follow hooks (Phase 2 purity) ------------------------
_spawn_reserved_character_keys_extra = None
_scrub_player_erase_attachments = None
_follow_declined_companion_cooldown = None


def set_spawn_reserved_character_keys_extra(fn):
    """Register fn(game) -> iterable of keys reserved for spawn/chargen names.

    Games add immersion cast keys, assigned NPC ids, etc. Pass None to clear.
    """
    global _spawn_reserved_character_keys_extra
    _spawn_reserved_character_keys_extra = fn


def iter_spawn_reserved_character_keys_extra(game):
    """Yield extra reserved keys from the game hook, or nothing when unset."""
    if _spawn_reserved_character_keys_extra is None:
        return
    try:
        keys = _spawn_reserved_character_keys_extra(game)
        if keys is None:
            return
        for raw in keys:
            key = (raw or "").strip()
            if key:
                yield key
    except Exception:
        return


def set_scrub_player_erase_attachments(fn):
    """Register fn(character, game) -> None before erase / orphan purge.

    Games drop hunt jobs, felony rows, and other world attachments. Pass
    None to clear (engine still zeroes generic crime fields when needed).
    """
    global _scrub_player_erase_attachments
    _scrub_player_erase_attachments = fn


def scrub_player_erase_attachments(character, game):
    """Invoke game scrub hook for deleted/off-roster player bodies."""
    if _scrub_player_erase_attachments is None:
        return
    try:
        _scrub_player_erase_attachments(character, game)
    except Exception:
        pass


def set_follow_declined_companion_cooldown(fn):
    """Register fn(follower, leader, game) -> None after explicit unfollow.

    Games stamp companion beckon-refuse cooldown (SUPERS: companion module).
    Pass None to clear.
    """
    global _follow_declined_companion_cooldown
    _follow_declined_companion_cooldown = fn


def stamp_follow_declined_companion_cooldown(follower, leader, game=None):
    """Mirror companion cooldown when a player peels off follow."""
    if _follow_declined_companion_cooldown is None:
        return
    try:
        _follow_declined_companion_cooldown(follower, leader, game)
    except Exception:
        pass


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
_refresh_help_overlay = None
# Verbs that must never FTS-fallback into the paths catalog (body mentions
# "help newbie" and would hijack onboarding queries).
_help_fts_blocklist = frozenset()


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


def set_try_directional_open(fn):
    """Register fn(character, game, args, *, open_) -> bool for structure doors.

    True means the game handled ``open north`` / ``close west`` (home door,
    interior lodging door). False falls through to container lockbox open.
    Pass None to clear. This is *not* ``engine/systems/doors.py``.
    """
    global _try_directional_open
    _try_directional_open = fn


def try_directional_open(character, game, args, *, open_):
    """True when a registered game handler consumed directional open/close."""
    if _try_directional_open is not None:
        return bool(_try_directional_open(character, game, args, open_=open_))
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


def set_try_get_unlisted(fn):
    """Register fn(character, args, game) -> bool for floor get misses.

    True means the game handled ``get`` (e.g. scrape grit from rails).
    Pass None to clear.
    """
    global _try_get_unlisted
    _try_get_unlisted = fn


def try_get_unlisted(character, args, game):
    """True when a registered game handler consumed this get miss."""
    if _try_get_unlisted is not None:
        return bool(_try_get_unlisted(character, args, game))
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


_vehicle_park_scrub_exempt = None


def set_vehicle_park_scrub_exempt(fn):
    """Register fn(game, veh, park_room, owner) -> bool.

    When True, ``save_parking_state`` leaves ``parked_room`` alone even when
    the curb fails ``room_is_valid_park_spot`` (e.g. no-park plaza
    drive-through; timed municipal tow owns enforcement). Pass None to clear.
    """
    global _vehicle_park_scrub_exempt
    _vehicle_park_scrub_exempt = fn


def vehicle_park_scrub_exempt(game, veh, park_room, owner):
    """True when an invalid curb should not be save-scrubbed."""
    if _vehicle_park_scrub_exempt is not None:
        return bool(_vehicle_park_scrub_exempt(game, veh, park_room, owner))
    return False


_vehicle_extra_persist_fields = None


def set_vehicle_extra_persist_fields(fn):
    """Register fn(veh) -> dict of JSON-safe fields for parking persistence.

    Pass None to clear.
    """
    global _vehicle_extra_persist_fields
    _vehicle_extra_persist_fields = fn


def vehicle_extra_persist_fields(veh):
    """Return extra vehicle fields to persist, or {}."""
    if _vehicle_extra_persist_fields is not None:
        try:
            extra = _vehicle_extra_persist_fields(veh)
            if isinstance(extra, dict) and extra:
                return dict(extra)
        except Exception as exc:
            _log_hook_fail(None, "vehicle_extra_persist_fields", exc)
            return {}
    return {}


_vehicle_extra_persist_apply = None


def set_vehicle_extra_persist_apply(fn):
    """Register fn(veh, extra) to restore persisted extra fields.

    Pass None to clear.
    """
    global _vehicle_extra_persist_apply
    _vehicle_extra_persist_apply = fn


def vehicle_extra_persist_apply(veh, extra):
    """Apply a loaded extra dict onto a live vehicle dict."""
    if _vehicle_extra_persist_apply is None:
        return
    if not isinstance(veh, dict) or not isinstance(extra, dict) or not extra:
        return
    try:
        _vehicle_extra_persist_apply(veh, extra)
    except Exception as exc:
        _log_hook_fail(None, "vehicle_extra_persist_apply", exc)
        return


_vehicle_invalid_park_rehome = None


def set_vehicle_invalid_park_rehome(fn):
    """Register fn(game, veh, park_room, owner) -> room_key or None.

    When ``save_parking_state`` scrubs an invalid curb, the hook may send
    off-Earth parks to municipal impound instead of a random neighbor exit.
    Pass None to clear.
    """
    global _vehicle_invalid_park_rehome
    _vehicle_invalid_park_rehome = fn


def vehicle_invalid_park_rehome(game, veh, park_room, owner):
    """Return a replacement curb key for invalid plaza parks, or None."""
    if _vehicle_invalid_park_rehome is not None:
        key = _vehicle_invalid_park_rehome(game, veh, park_room, owner)
        if isinstance(key, str) and key.strip():
            return key.strip()
    return None


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


_zone_enter_dest = None


def set_zone_enter_dest(fn):
    """Register fn(character, dest, raw_enter, game) -> dest room rewrite."""
    global _zone_enter_dest
    _zone_enter_dest = fn


def zone_enter_dest(character, dest, raw_enter, game):
    """Optional rewrite of classic zone enter destination."""
    if _zone_enter_dest is not None:
        return _zone_enter_dest(character, dest, raw_enter, game)
    return dest


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


def set_help_fts_blocklist(keys):
    """Register help queries that must skip DB FTS (game registers onboarding hubs)."""
    global _help_fts_blocklist
    if not keys:
        _help_fts_blocklist = frozenset()
        return
    _help_fts_blocklist = frozenset(
        str(k).strip().lower() for k in keys if str(k).strip()
    )


def get_help_fts_blocklist():
    """Help verbs that skip FTS when no static/DB exact page matched."""
    return _help_fts_blocklist


_help_alias_target_resolver = None


def set_help_alias_target_resolver(fn):
    """Register fn(keyword) -> hub keyword when ``keyword`` is alias-only.

    Used by ``hedit`` so staff can claim a redirect name as a real page.
    Pass ``None`` to restore the lean-engine default (no static aliases).
    """
    global _help_alias_target_resolver
    _help_alias_target_resolver = fn


def get_help_alias_target(keyword):
    """Hub keyword when ``keyword`` is a static alias-only name, else ``None``."""
    if _help_alias_target_resolver is None:
        return None
    keyword = (keyword or "").strip().lower()
    if not keyword:
        return None
    return _help_alias_target_resolver(keyword)


def set_refresh_help_overlay(fn):
    """Register fn(game, keyword, *, author) -> (ok, message) for GM hrefresh.

    Pass None to restore the lean-engine fallback (static HELP_TOPICS only).
    """
    global _refresh_help_overlay
    _refresh_help_overlay = fn


def refresh_help_overlay(game, keyword, *, author="hrefresh"):
    """Rewrite one help_db overlay from static HELP_TOPICS. Returns (ok, msg)."""
    if _refresh_help_overlay is not None:
        return _refresh_help_overlay(game, keyword, author=author)
    from engine import help_sync as help_sync_mod

    keyword = (keyword or "").strip().lower()
    if not keyword:
        return False, "missing keyword"
    if keyword == "catalog" or keyword in ("paths", "path list", "pathlist"):
        return (
            False,
            "paths catalog refresh needs the full game package.",
        )
    db = getattr(game, "db", None)
    if db is None:
        return False, "no database connection"
    topics = get_help_topics()
    body = topics.get(keyword)
    if not body:
        return False, f"no static help page for '{keyword}'"
    category = ""
    for category_name, entries in get_help_categories():
        for topic_keyword, _blurb in entries:
            if topic_keyword == keyword:
                category = category_name
                break
        if category:
            break
    return help_sync_mod.push_static_topic(
        db,
        keyword,
        body,
        category=category,
        author=author,
    )


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
_item_give_refusal = None
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
_weather_is_demesne_room = None
_weather_demesne_region_id = None
_weather_homestead_macro_xy = None
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


def set_weather_is_demesne_room(fn):
    """Register fn(room) -> bool for god demesne pocket cells."""
    global _weather_is_demesne_room
    _weather_is_demesne_room = fn


def weather_is_demesne_room(room):
    """True when the room is a demesne pocket (not CONUS atlas)."""
    if _weather_is_demesne_room is not None:
        return bool(_weather_is_demesne_room(room))
    return False


def set_weather_demesne_region_id(fn):
    """Register fn(room) -> str pocket region id (e.g. demesne:slug) or None."""
    global _weather_demesne_region_id
    _weather_demesne_region_id = fn


def weather_demesne_region_id(room):
    """Climate region id for a demesne pocket room, or None when not a demesne."""
    if _weather_demesne_region_id is not None:
        return _weather_demesne_region_id(room)
    return None


def set_weather_homestead_macro_xy(fn):
    """Register fn(room, game, character=None) -> (x, y) | None for plot atlas cells."""
    global _weather_homestead_macro_xy
    _weather_homestead_macro_xy = fn


def weather_homestead_macro_xy(room, game, character=None):
    """America macro coords for a homestead plot room, or None when not applicable."""
    if _weather_homestead_macro_xy is not None:
        return _weather_homestead_macro_xy(room, game, character)
    return None


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
_storm_chase_desk_keys = frozenset()


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
_press_beat_desk_keys = frozenset()


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


def set_item_give_refusal(fn):
    """Register fn(character, item) -> str|None for item-give gates.

    A returned string is shown to the giver and blocks the transfer; None
    allows it. Pass None to restore the no-op default.
    """
    global _item_give_refusal
    _item_give_refusal = fn


def item_give_refusal(character, item):
    """Return an item-give refusal message, or None to allow the give."""
    if _item_give_refusal is not None:
        return _item_give_refusal(character, item)
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
_overland_vehicle_compass_as_foot = None
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


def set_overland_vehicle_compass_as_foot(fn):
    """Register fn(character, game) -> bool.

    True means the game placed the boarded character on the on-foot
    micro grid, so this compass verb should walk that grid instead of a
    vehicle macro hop.
    """
    global _overland_vehicle_compass_as_foot
    _overland_vehicle_compass_as_foot = fn


def overland_vehicle_compass_as_foot(character, game):
    if _overland_vehicle_compass_as_foot is not None:
        return bool(_overland_vehicle_compass_as_foot(character, game))
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
_is_rpc_staff = None
_resolve_gm_body = None
_stamp_input_activity = None
_blob_codec_reload = None
_taxi_mode_saver = None
_help_overlay_mode_saver = None
_map_snapshot_write_all = None
_map_snapshot_daily_archive = None

# H6 map_store: rset_field bool/text catalogs + populate helpers.
_rset_bool_flags = frozenset()
_rset_text_fields = frozenset()
_rset_reference_lines_fn = None
_rset_list_validator = None
_rset_item_id_validator = None
_map_store_apply_entry_fields = None
_map_store_place_seed_items = None
_map_store_append_hand_rooms = None

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
_say_voice_mod = None
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


def set_is_rpc_staff(fn):
    """Register fn(character) -> bool for Roleplay Council / GM staff chat."""
    global _is_rpc_staff
    _is_rpc_staff = fn


def is_rpc_staff(character):
    """True for GMs or player archangels (RPC) -- never immersion NPCs."""
    if _is_rpc_staff is not None:
        return _is_rpc_staff(character)
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


# Post-overlay diagnostics (auto_deploy). Games register extra warning
# lines (e.g. SUPERS cuff blob_fragment probe, bug 739). Not gameplay.
_post_overlay_game_checks = None


def set_post_overlay_game_checks(fn):
    """Register fn() -> list[str] for deploy_guard overlay diagnostics."""
    global _post_overlay_game_checks
    _post_overlay_game_checks = fn


def post_overlay_game_checks():
    """Game-registered post-overlay warnings; default empty."""
    if _post_overlay_game_checks is None:
        return []
    try:
        out = _post_overlay_game_checks()
        return list(out) if out else []
    except Exception as exc:
        return [f"post-overlay game check failed: {exc}"]


def set_taxi_mode_saver(fn):
    """Register fn(conn, game) -> None to persist game.taxi_mode meta."""
    global _taxi_mode_saver
    _taxi_mode_saver = fn


def save_taxi_mode_meta(conn, game):
    """Persist taxi pacing mode when a game registered a saver."""
    if _taxi_mode_saver is not None:
        _taxi_mode_saver(conn, game)


def set_help_overlay_mode_saver(fn):
    """Register fn(conn, game) -> None to persist game.help_overlay_mode meta."""
    global _help_overlay_mode_saver
    _help_overlay_mode_saver = fn


def save_help_overlay_mode_meta(conn, game):
    """Persist help overlay mode when a game registered a saver."""
    if _help_overlay_mode_saver is not None:
        _help_overlay_mode_saver(conn, game)


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


def set_rset_list_validator(fn):
    """Register fn(field, tokens) -> None; raise ValueError on bad list tokens."""
    global _rset_list_validator
    _rset_list_validator = fn


def rset_list_validate(field, tokens):
    """Optional game hook: validate parsed rset list-field tokens."""
    if _rset_list_validator is not None:
        _rset_list_validator(field, tokens)


def set_rset_item_id_validator(fn):
    """Register fn(item_id, *, where) -> None; raise ValueError on bad ids."""
    global _rset_item_id_validator
    _rset_item_id_validator = fn


def rset_item_id_validate(item_id, *, where=""):
    """Optional game hook: validate one item catalog id at author time."""
    if _rset_item_id_validator is not None:
        _rset_item_id_validator(item_id, where=where)
        return
    if _item_catalog_get is not None and not _item_catalog_get(item_id):
        label = f" ({where})" if where else ""
        raise ValueError(f"Unknown item id {item_id!r}{label}.")


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


def set_map_store_append_hand_rooms(fn):
    """Register fn(game, anchor_room, new_rooms, extra_exits=...) -> str."""
    global _map_store_append_hand_rooms
    _map_store_append_hand_rooms = fn


def map_store_append_hand_rooms(
    game,
    anchor_room,
    new_rooms,
    *,
    extra_exits=None,
    skip_amenity_catchup=False,
):
    """Batch-create hand rooms + persist JSON (SUPERS ``map_store`` hook)."""
    if _map_store_append_hand_rooms is None:
        raise RuntimeError(
            "append_hand_rooms is not registered — boot SUPERS "
            "(supers.bootstrap.register_core_hooks)."
        )
    return _map_store_append_hand_rooms(
        game,
        anchor_room,
        new_rooms,
        extra_exits=extra_exits,
        skip_amenity_catchup=skip_amenity_catchup,
    )


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


def set_say_voice_mod(fn):
    global _say_voice_mod
    _say_voice_mod = fn


def say_voice_mod(character):
    """Return third-person voice flavor for ``say`` (e.g. ``in a gravelly voice``)."""
    if _say_voice_mod is not None:
        return _say_voice_mod(character)
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
    session = getattr(character, "session", None)
    if session is not None:
        session.send(
            f"You {you_verb},{tone_bit} \"{spoken}\"{tag}"
        )
    if room is not None:
        line = (
            f'{_display_name(character)} {they_verb},{tone_bit} "{spoken}"{tag}'
        )
        room.broadcast(line, exclude=character)
        try:
            from engine import channels
            channels.append_room_say(game, room, line)
        except Exception as exc:
            _log_hook_fail(game, "deliver_say_history", exc)


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


def blocked_foot_step(game, macro, micro, character=None):
    if _blocked_foot_step is not None:
        return _blocked_foot_step(game, macro, micro, character)
    return None


_can_enter_water = None
_water_may_enter_band = None
_max_water_band = None


def set_can_enter_water(fn):
    """Register hard refuse before entering playable water (Fire Elemental)."""
    global _can_enter_water
    _can_enter_water = fn


def can_enter_water(character):
    """(ok, tell) — permissive when no game hook is registered."""
    if _can_enter_water is not None:
        return _can_enter_water(character)
    return True, ""


def set_water_may_enter_band(fn):
    """Register depth-band endurance gate (character, water_kind, band)."""
    global _water_may_enter_band
    _water_may_enter_band = fn


def water_may_enter_band(character, water_kind, band):
    """(ok, tell) for one target depth band."""
    if _water_may_enter_band is not None:
        return _water_may_enter_band(character, water_kind, band)
    return True, ""


def set_max_water_band(fn):
    """Register max depth label for (character, water_kind)."""
    global _max_water_band
    _max_water_band = fn


def max_water_band(character, water_kind):
    """Shallowest allowed max band label, or ``surface`` / ``inshore``."""
    if _max_water_band is not None:
        return _max_water_band(character, water_kind)
    return "surface" if str(water_kind) == "lake" else "inshore"


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
_containers_remote_stash_gate = None
_containers_heal_folded_kit_bags = None
_containers_consolidate_ammo_stack = None
_containers_heal_folded_gear_bag_stacks = None


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


def set_containers_remote_stash_gate(fn):
    """Game overlay: named-archangel remote stash / retrieve / outfit."""
    global _containers_remote_stash_gate
    _containers_remote_stash_gate = fn


def containers_remote_stash_gate(character, action):
    """Return ``(ok, msg)`` to skip the in-room home gate.

    Default denies. SUPERS registers named-archangel remote retrieve /
    outfit (Grace) and free remote deposit.
    """
    if _containers_remote_stash_gate is not None:
        return _containers_remote_stash_gate(character, action)
    return False, "You can only stash things at your claimed home."


def set_containers_heal_folded_kit_bags(fn):
    global _containers_heal_folded_kit_bags
    _containers_heal_folded_kit_bags = fn


def containers_heal_folded_kit_bags(game):
    if _containers_heal_folded_kit_bags is not None:
        return _containers_heal_folded_kit_bags(game)
    return 0


def set_containers_consolidate_ammo_stack(fn):
    global _containers_consolidate_ammo_stack
    _containers_consolidate_ammo_stack = fn


def containers_consolidate_ammo_stack(pieces, catalog_id):
    """Merge ammo box rows into one Item, or None when not ammo / no game."""
    if _containers_consolidate_ammo_stack is not None:
        return _containers_consolidate_ammo_stack(pieces, catalog_id)
    return None


def set_containers_heal_folded_gear_bag_stacks(fn):
    global _containers_heal_folded_gear_bag_stacks
    _containers_heal_folded_gear_bag_stacks = fn


def containers_heal_folded_gear_bag_stacks(game):
    """Boot heal: collapse duplicate kit-bag rows in folded vault blobs."""
    if _containers_heal_folded_gear_bag_stacks is not None:
        return _containers_heal_folded_gear_bag_stacks(game)
    return 0


# Pocket-grid micro room builder (optional; default blank stamp when unset).
_build_pocket_micro_room = None


def set_build_pocket_micro_room(fn):
    """Register room builder. Pass None to restore default blank stamp."""
    global _build_pocket_micro_room
    _build_pocket_micro_room = fn


def build_pocket_micro_room(game, pocket, mx, my, ux, uy):
    """Call the registered builder, or return None for the engine default.

    On exception, log and return None so ``get_or_create_pocket_micro`` falls
    back to a stamped ``Open ground.`` cell.
    """
    if _build_pocket_micro_room is None:
        return None
    try:
        return _build_pocket_micro_room(game, pocket, mx, my, ux, uy)
    except Exception as exc:
        _log_hook_fail(game, "build_pocket_micro_room", exc)
        return None


# Demesne micro-room persistence (SUPERS demesne/overland registers these).
_demesne_resolve_room_key = None
_demesne_lookup_room_for_persist = None
_demesne_iter_micro_rooms = None
_quest_bank_growth_reward = None
_fuel_phase_summary_line = None


def set_demesne_resolve_room_key(fn):
    global _demesne_resolve_room_key
    _demesne_resolve_room_key = fn


def demesne_resolve_room_key(game, room_key):
    if _demesne_resolve_room_key is not None:
        return _demesne_resolve_room_key(game, room_key)
    return None


_resolve_party_run_saved_room = None


def set_resolve_party_run_saved_room(fn):
    """Register fn(game, room_key, character=None) -> Room | None.

    SUPERS party private dungeons use ephemeral ``partyrun:`` clone keys.
    Pass None to restore the default (no resolver).
    """
    global _resolve_party_run_saved_room
    _resolve_party_run_saved_room = fn


def resolve_party_run_saved_room(game, room_key, character=None):
    """Rematerialize a private party dungeon room key when registered."""
    if _resolve_party_run_saved_room is not None:
        return _resolve_party_run_saved_room(game, room_key, character=character)
    return None


def set_demesne_lookup_room_for_persist(fn):
    global _demesne_lookup_room_for_persist
    _demesne_lookup_room_for_persist = fn


def demesne_lookup_room_for_persist(game, room_key):
    if _demesne_lookup_room_for_persist is not None:
        return _demesne_lookup_room_for_persist(game, room_key)
    return None


def set_demesne_iter_micro_rooms(fn):
    global _demesne_iter_micro_rooms
    _demesne_iter_micro_rooms = fn


def demesne_iter_micro_rooms(game):
    if _demesne_iter_micro_rooms is not None:
        yield from _demesne_iter_micro_rooms(game)


def set_quest_bank_growth_reward(fn):
    """Register fn(character, amount) -> banked float for growth quest rewards."""
    global _quest_bank_growth_reward
    _quest_bank_growth_reward = fn


def quest_bank_growth_reward(character, amount):
    if _quest_bank_growth_reward is not None:
        return _quest_bank_growth_reward(character, amount)
    return 0


def set_fuel_phase_summary_line(fn):
    """Register fn(breakdown_dict) -> str | None for lag diag export."""
    global _fuel_phase_summary_line
    _fuel_phase_summary_line = fn


def fuel_phase_summary_line(breakdown):
    if _fuel_phase_summary_line is not None:
        return _fuel_phase_summary_line(breakdown)
    return None


# Content kind profiles (OLC / content_new / Area Studio) — game registers
# profile dirs, domain validators, and catalog save paths.
_content_kinds_dirs: list[str] = []
_content_kind_domain_validate = None
_content_kind_save_entity = None
_content_kind_load_entity = None
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


def register_builder_audit(domain_id, title, runner):
    """Register a builder hygiene audit (jobs, quests, …)."""
    from engine.content_kinds import audit as audit_mod
    audit_mod.register(domain_id, title, runner)


def run_builder_audits(*, game=None):
    """Run all registered builder audits; return section tuples."""
    from engine.content_kinds import audit as audit_mod
    return audit_mod.run_all(game=game)


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


def set_content_kind_load_entity(fn):
    """Register fn(kind_id, entity_id) -> dict for OLC edit drafts."""
    global _content_kind_load_entity
    _content_kind_load_entity = fn


def content_kind_load_entity(kind_id, entity_id):
    """Load one catalog row for in-game OLC edit."""
    if _content_kind_load_entity is None:
        raise RuntimeError(
            "content_kind_load_entity hook not registered "
            "(game must call set_content_kind_load_entity at boot)"
        )
    return _content_kind_load_entity(kind_id, entity_id)


_content_kind_capability = None


def set_content_kind_capability(fn):
    """Register fn(kind_id) -> 'full' | 'create' | 'none'.

    'full'   = save_entity and load_entity both work (new + edit)
    'create' = save_entity works, load_entity does not (olc new only)
    'none'   = no persist helper (explain / lint only)

    Games that do not register this default to 'full' for every kind, which
    preserves the pre-hook behavior (try the save, report the error).
    """
    global _content_kind_capability
    _content_kind_capability = fn


def content_kind_capability(kind_id):
    """Persist capability for one kind ('full' when no game hook)."""
    if _content_kind_capability is None:
        return "full"
    try:
        return _content_kind_capability(kind_id) or "none"
    except Exception:
        # A broken game hook must never break the kind listing.
        return "full"


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


def set_lodging_bed_eligibility(fn):
    """Register fn(character, bed, room) -> bool for sharing a bed with one occupant."""
    global _lodging_bed_eligibility
    _lodging_bed_eligibility = fn


def lodging_bed_eligibility(character, bed, room):
    """True when ``character`` may join ``bed`` (default: False)."""
    if _lodging_bed_eligibility is not None:
        return bool(_lodging_bed_eligibility(character, bed, room))
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


def set_lodging_rent_tick(fn):
    """Register fn(game) called once per lease tick (hotel rent, eviction, …)."""
    global _lodging_rent_tick
    _lodging_rent_tick = fn


def lodging_rent_tick(game):
    """Run game lease tick hook, or no-op."""
    if _lodging_rent_tick is not None:
        _lodging_rent_tick(game)


def set_lodging_room_stamper(fn):
    """Register fn(room) called after engine ``stamp_home_basics``."""
    global _lodging_room_stamper
    _lodging_room_stamper = fn


def stamp_lodging_room(room):
    """Apply game lodging room stamp hook, or no-op."""
    if _lodging_room_stamper is not None:
        _lodging_room_stamper(room)


def set_lodging_look_home_detail_lines(fn):
    """Register fn(room, game, character=None) -> list[str] for look houses."""
    global _lodging_look_home_detail_lines
    _lodging_look_home_detail_lines = fn


def lodging_look_home_detail_lines(room, game, character=None):
    """For-sale home rows, or [] when no game / no listings."""
    if _lodging_look_home_detail_lines is not None:
        result = _lodging_look_home_detail_lines(room, game, character)
        if result:
            return list(result)
    return []


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
    """Register fn(actor, dest, game, *, quiet=...) -> bool for Cadence pathing."""
    global _paced_travel_cadence_step
    _paced_travel_cadence_step = fn


def paced_travel_cadence_step(actor, dest, game, *, quiet=True):
    """Cadence seek one-hop; False when unregistered.

    ``quiet`` mirrors ``paced_travel_player_hop`` -- SUPERS bootstrap
    registers a step that accepts it for idlemode / seek presentation.
    """
    if _paced_travel_cadence_step is not None:
        return bool(_paced_travel_cadence_step(actor, dest, game, quiet=quiet))
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


def set_paced_travel_hub_ok(fn):
    """Register fn(hub, from_room, *, actor, game) -> bool for pocket enters."""
    global _paced_travel_hub_ok
    _paced_travel_hub_ok = fn


def paced_travel_hub_ok(hub, from_room, *, actor=None, game=None):
    """May paced BFS enter this pocket hub? Default True."""
    if _paced_travel_hub_ok is not None:
        return bool(
            _paced_travel_hub_ok(
                hub, from_room, actor=actor, game=game,
            ),
        )
    return True


def set_paced_travel_destinations(fn):
    """Register fn(character, game, zone) -> extra destination label strings."""
    global _paced_travel_destinations
    _paced_travel_destinations = fn


def paced_travel_destinations(character, game, zone):
    """Extra zone destination labels for paced walk (default: none)."""
    if _paced_travel_destinations is not None:
        return _paced_travel_destinations(character, game, zone) or ()
    return ()


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


def set_paced_travel_hops_of(fn):
    """Register fn(character, pace) -> hops int or None (keep engine default)."""
    global _paced_travel_hops_of
    _paced_travel_hops_of = fn


def paced_travel_hops_of(character, pace="run"):
    """Optional Origin hop override for one paced advance, or None."""
    if _paced_travel_hops_of is not None:
        return _paced_travel_hops_of(character, pace)
    return None


def set_paced_travel_eta_tick(fn):
    """Register fn(character, game, focus, now) for mode=eta journeys."""
    global _paced_travel_eta_tick
    _paced_travel_eta_tick = fn


def paced_travel_eta_tick(character, game, focus, now):
    """Advance one skip-road ETA stamp, or False if no hook."""
    if _paced_travel_eta_tick is not None:
        return _paced_travel_eta_tick(character, game, focus, now)
    return False


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


_channel_audience_ok = None


def set_channel_audience_ok(fn):
    """Register fn(viewer, audience_token, game) -> bool for custom audiences."""
    global _channel_audience_ok
    _channel_audience_ok = fn


def channel_audience_ok(viewer, audience_token, game):
    """Extension hook for origin:/custom channel audience (default deny)."""
    if _channel_audience_ok is not None:
        return bool(_channel_audience_ok(viewer, audience_token, game))
    return False


_channel_speech_blocked = None


def set_channel_speech_blocked(fn):
    """Register fn(character, game) -> bool when speech must be blocked.

    Pass None to restore the default (never blocked). SUPERS registers
    archangel biokinesis mute from ``supers.archangel_powers``.
    """
    global _channel_speech_blocked
    _channel_speech_blocked = fn


def channel_speech_blocked(character, game):
    """Run the game-owned channel speech block hook, if any."""
    if _channel_speech_blocked is not None:
        return bool(_channel_speech_blocked(character, game))
    return False


# Party-merge auto-accept (Echo / idlemode close-tie). Default True so a
# lean engine still auto-accepts merges the way the old ImportError path
# did; games register a stricter predicate (SUPERS: companion.can_auto_companion).
_can_auto_companion = None


def set_can_auto_companion(fn):
    """Register fn(leader, target, game=None, *, require_close_tie=True) -> bool.

    Pass None to restore the default (always True). SUPERS registers
    ``supers.companion.can_auto_companion`` from bootstrap.
    """
    global _can_auto_companion
    _can_auto_companion = fn


def can_auto_companion(leader, target, game=None, *, require_close_tie=True):
    """Run the game-owned party-merge auto-accept hook, if any.

    Default True matches the pre-hook ImportError path (no game package
    means Echo/idlemode merges still go through).
    """
    if _can_auto_companion is not None:
        return bool(_can_auto_companion(
            leader, target, game, require_close_tie=require_close_tie,
        ))
    return True


_default_mssp_description = None


def set_default_mssp_description(fn):
    """Register fn() -> str copied onto ``Game.mssp_description`` at boot.

    Pass None to restore the empty default (``engine.mssp`` falls back to
    ``RIFTFORGE_MSSP_DESCRIPTION`` or the generic engine-demo blurb).
    """
    global _default_mssp_description
    _default_mssp_description = fn


def default_mssp_description():
    """Return the game-owned MSSP DESCRIPTION seed for ``Game.__init__``."""
    if _default_mssp_description is not None:
        return str(_default_mssp_description() or "")
    return ""


_discord_staff_op_executor = None


def set_discord_staff_op_executor(fn):
    """Register fn(game, op, args) -> (ok, message) for Discord #ops inbox.

    Pass None to restore the default (unknown op). SUPERS wires GM restore
    / revive parity from ``supers.discord_staff_ops_hooks``.
    """
    global _discord_staff_op_executor
    _discord_staff_op_executor = fn


def discord_staff_op_executor(game, op, args):
    """Run a Discord staff ops mutation through the game hook, if any."""
    if _discord_staff_op_executor is not None:
        return _discord_staff_op_executor(game, op, args)
    op_s = str(op or "").strip().lower()
    return False, f"Unknown op {op_s!r}"


# --- Mining system hooks (docs/plans/mine_system.md) -----------------------

_mine_stratum_tables = {}
_mine_marker_chance = None
_mine_geology_seed = None
_mine_tool_check = None
_mine_carry_weight_cap = None
_mine_company_job_gate_permission = None
_roll_mine_discoverable = None
_on_mine_face_cleared = None
_mine_support_catalog = None
_mine_alloy_recipes = None
_mine_forge_recipes = None
_mine_blast = None
_mine_company_layout = None
_mine_gate_breach = None
_pick_mine_ambient_line = None
_maybe_mine_job_bark = None


def register_mine_stratum_table(realm_id, table):
    """Register element depth-band table for ``realm_id`` (earth, hell, …)."""
    _mine_stratum_tables[str(realm_id)] = table or {}


def mine_stratum_table(realm_id):
    """Return registered stratum table or empty dict."""
    return dict(_mine_stratum_tables.get(str(realm_id), {}) or {})


def set_mine_marker_chance(fn):
    """Register fn(room, character) -> float stumble chance 0..1."""
    global _mine_marker_chance
    _mine_marker_chance = fn


def mine_marker_chance(room, character):
    """Wilderness marker stumble chance; default 0."""
    if _mine_marker_chance is not None:
        try:
            return float(_mine_marker_chance(room, character) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def set_mine_geology_seed(fn):
    """Register fn(mouth_key, area_type) -> geology dict."""
    global _mine_geology_seed
    _mine_geology_seed = fn


def mine_geology_seed(mouth_key, area_type):
    """Geology seed for a mouth; engine hash fallback when unset."""
    if _mine_geology_seed is not None:
        return _mine_geology_seed(mouth_key, area_type)
    import hashlib
    digest = hashlib.sha256(f"{mouth_key}:{area_type}".encode()).hexdigest()
    elements = ("coal", "copper", "iron", "lead", "tin")
    primary = elements[int(digest[:2], 16) % len(elements)]
    secondary = elements[int(digest[2:4], 16) % len(elements)]
    return {
        "primary_element": primary,
        "secondary_element": secondary,
        "contamination_tag": None,
    }


def set_mine_tool_check(fn):
    """Register fn(character, action) -> (ok, slow_mult, tell_line|None)."""
    global _mine_tool_check
    _mine_tool_check = fn


def mine_tool_check(character, action):
    """Tool gate for carve/harvest; default requires pick/shovel in inventory."""
    if _mine_tool_check is not None:
        return _mine_tool_check(character, action)
    from engine.systems import material_instances as mat_mod
    needles = ("pick", "shovel", "pickaxe", "mining pick")
    for item in mat_mod.iter_carried_items(character):
        key = (getattr(item, "key", "") or "").lower()
        if any(n in key for n in needles):
            return True, 1.0, None
    return True, 2.5, None


def set_mine_carry_weight_cap(fn):
    """Register fn(character) -> max weight or None for unlimited."""
    global _mine_carry_weight_cap
    _mine_carry_weight_cap = fn


def mine_carry_weight_cap(character):
    """Character-level carry cap; default unlimited."""
    if _mine_carry_weight_cap is not None:
        return _mine_carry_weight_cap(character)
    return None


def set_mine_company_job_gate_permission(fn):
    """Register fn(character) -> bool for NPC gate hang/lock (v1: False)."""
    global _mine_company_job_gate_permission
    _mine_company_job_gate_permission = fn


def mine_company_job_gate_permission(character):
    """Whether Cadence actors may hang/lock mine gates; default False."""
    if _mine_company_job_gate_permission is not None:
        return bool(_mine_company_job_gate_permission(character))
    return False


def set_roll_mine_discoverable(fn):
    """Register fn(context) -> discoverable dict or None."""
    global _roll_mine_discoverable
    _roll_mine_discoverable = fn


def roll_mine_discoverable(context):
    """Roll a room discoverable; default nothing."""
    if _roll_mine_discoverable is not None:
        return _roll_mine_discoverable(context)
    return None


def set_on_mine_face_cleared(fn):
    """Register fn(room, direction, miner, game) after a carve completes."""
    global _on_mine_face_cleared
    _on_mine_face_cleared = fn


def on_mine_face_cleared(room, direction, miner, game):
    """Post-carve hook; default no-op."""
    if _on_mine_face_cleared is not None:
        _on_mine_face_cleared(room, direction, miner, game)


def set_mine_support_catalog(fn):
    """Register fn(realm) -> {timber: rating, steel: rating, …}."""
    global _mine_support_catalog
    _mine_support_catalog = fn


def mine_support_catalog(realm):
    """Support material ratings; generic timber/steel defaults."""
    if _mine_support_catalog is not None:
        return dict(_mine_support_catalog(realm) or {})
    return {"timber": 1, "steel": 2}


def set_mine_alloy_recipes(fn):
    """Register fn() -> list of alloy recipe dicts."""
    global _mine_alloy_recipes
    _mine_alloy_recipes = fn


def mine_alloy_recipes():
    """Alloy smelt recipes; default empty."""
    if _mine_alloy_recipes is not None:
        return list(_mine_alloy_recipes() or ())
    return []


def set_mine_forge_recipes(fn):
    """Register fn() -> list of forge recipe dicts."""
    global _mine_forge_recipes
    _mine_forge_recipes = fn


def mine_forge_recipes():
    """Forge recipes; default empty."""
    if _mine_forge_recipes is not None:
        return list(_mine_forge_recipes() or ())
    return []


def set_mine_blast(fn):
    """Register fn(game, mouth, room_id, direction) -> message (parked v1)."""
    global _mine_blast
    _mine_blast = fn


def mine_blast(game, mouth, room_id, direction):
    """Staff blast test hook; default reports unavailable."""
    if _mine_blast is not None:
        return _mine_blast(game, mouth, room_id, direction)
    return "Blasting is not available."


_smelt_handler = None
_forge_handler = None


def set_smelt_handler(fn):
    """Register fn(character, game, ore_item, smelt_tier) -> (ok, msg, item)."""
    global _smelt_handler
    _smelt_handler = fn


def smelt_ore(character, game, ore_item, *, smelt_tier=1):
    if _smelt_handler is None:
        return False, "Smelting is not available.", None
    return _smelt_handler(character, game, ore_item, smelt_tier)


def set_forge_handler(fn):
    """Register fn(character, game, recipe_id, ingot) -> (ok, msg, item)."""
    global _forge_handler
    _forge_handler = fn


def forge_at_station(character, game, recipe_id, ingot, *, impress=False):
    if _forge_handler is None:
        return False, "Forging is not available.", None
    return _forge_handler(character, game, recipe_id, ingot, impress=impress)


_mine_forge_type_lines = None
_mine_find_ingot = None
_gain_skill = None


def set_mine_forge_type_lines(fn):
    """Register fn() -> list[str] for bare ``forge`` type catalog help."""
    global _mine_forge_type_lines
    _mine_forge_type_lines = fn


def mine_forge_type_lines():
    """Player forge type listing; empty when no game catalog is registered."""
    if _mine_forge_type_lines is not None:
        return list(_mine_forge_type_lines() or ())
    return []


def set_mine_find_ingot(fn):
    """Register fn(character, needle=None) -> ingot Item | None."""
    global _mine_find_ingot
    _mine_find_ingot = fn


def mine_find_ingot(character, needle=None):
    """Find a carried ingot for ``forge <type> with <metal>``."""
    if _mine_find_ingot is not None:
        return _mine_find_ingot(character, needle)
    return None


def set_gain_skill(fn):
    """Register fn(character, skill_key, amount, **kwargs) for profession XP."""
    global _gain_skill
    _gain_skill = fn


def gain_skill(character, skill_key, amount, **kwargs):
    """Award profession/survival skill XP when a game registers the hook."""
    if _gain_skill is not None:
        _gain_skill(character, skill_key, amount, **kwargs)


def set_mine_company_layout_resolver(fn):
    """Register fn(company_id) -> layout dict or None."""
    global _mine_company_layout
    _mine_company_layout = fn


def mine_company_layout(company_id):
    if _mine_company_layout is not None:
        return _mine_company_layout(company_id)
    return None


def set_mine_gate_breach(fn):
    """Register fn(character, verb) -> bool for gate pick/force/bypass."""
    global _mine_gate_breach
    _mine_gate_breach = fn


def mine_gate_breach(character, verb):
    if _mine_gate_breach is not None:
        return bool(_mine_gate_breach(character, verb))
    import random
    if verb == "bypass":
        return random.random() < 0.7
    if verb == "pick":
        return random.random() < 0.5
    if verb == "force":
        return random.random() < 0.4
    return False


def set_pick_mine_ambient_line(fn):
    """Register fn(room, game, *, rng=None) -> str|None for mine tick ambience."""
    global _pick_mine_ambient_line
    _pick_mine_ambient_line = fn


def pick_mine_ambient_line(room, game, *, rng=None):
    """Return one ambient line for an occupied virtual mine room, or None."""
    if _pick_mine_ambient_line is not None:
        return _pick_mine_ambient_line(room, game, rng=rng)
    return None


def set_maybe_mine_job_bark(fn):
    """Register fn(character, game, action) -> bool for mine-action job barks."""
    global _maybe_mine_job_bark
    _maybe_mine_job_bark = fn


def maybe_mine_job_bark(character, game, action):
    """Maybe broadcast a job-flavored line during mine carve/harvest dispatch."""
    if _maybe_mine_job_bark is not None:
        return bool(_maybe_mine_job_bark(character, game, action))
    return False


# --- Phase 2 purity (2026-09 v0.7.0) -------------------------------------
# These replaced live ``from supers import …`` inside engine modules.
# Games register the real implementations in bootstrap.

_resolve_account_character = None
_after_group_focus_change = None
_ensure_quest_mentor_reach = None
_note_zone_visit = None
_is_ephemeral_instance_room = None
_report_persist_helper_gaps = None
_containers_is_profession_bag_item = None
_containers_matching_profession_bag = None
_containers_is_weave_bag_item = None
_containers_is_extradim_bag_item = None
_containers_is_generic_weave_query = None
_containers_active_weave_bag = None


def set_resolve_account_character(fn):
    """Register fn(character, game) -> Character for account-face mapping.

    God bilocate twins have no account row; SUPERS maps them to the Mantle.
    Pass None to restore identity (lean engine).
    """
    global _resolve_account_character
    _resolve_account_character = fn


def resolve_account_character(character, game):
    """Return the account-owning body, or ``character`` unchanged."""
    if character is None:
        return None
    if _resolve_account_character is None:
        return character
    mapped = _resolve_account_character(character, game)
    return mapped if mapped is not None else character


def set_after_group_focus_change(fn):
    """Register fn(game) after a player sets or clears group focus."""
    global _after_group_focus_change
    _after_group_focus_change = fn


def after_group_focus_change(game):
    """Notify the game that party focus changed (NPC mate retarget)."""
    if _after_group_focus_change is not None:
        _after_group_focus_change(game)


def set_ensure_quest_mentor_reach(fn):
    """Register fn(game, pin_map) -> int for quest-mentor phone/mail reach."""
    global _ensure_quest_mentor_reach
    _ensure_quest_mentor_reach = fn


def ensure_quest_mentor_reach(game, pin_map):
    """Ensure quest mentors are reachable; default no-op returns 0."""
    if _ensure_quest_mentor_reach is None:
        return 0
    return int(_ensure_quest_mentor_reach(game, pin_map) or 0)


def set_note_zone_visit(fn):
    """Register fn(character, game, room=dest) after a landmark/hub enter."""
    global _note_zone_visit
    _note_zone_visit = fn


def note_zone_visit(character, game, room=None):
    """Optional visit stamp (Cultivator qi sites, etc.). Default no-op."""
    if _note_zone_visit is not None:
        _note_zone_visit(character, game, room=room)


def set_is_ephemeral_instance_room(fn):
    """Register fn(room) -> bool for wilderness procedural pockets."""
    global _is_ephemeral_instance_room
    _is_ephemeral_instance_room = fn


def is_ephemeral_instance_room(room):
    """True when the game says this room is a short-lived combat pocket."""
    if _is_ephemeral_instance_room is None:
        return False
    return bool(_is_ephemeral_instance_room(room))


def set_report_persist_helper_gaps(fn):
    """Register fn() -> list[str] of missing persist helper names."""
    global _report_persist_helper_gaps
    _report_persist_helper_gaps = fn


def report_persist_helper_gaps():
    """Names of save helpers the game process does not have yet."""
    if _report_persist_helper_gaps is None:
        return []
    try:
        out = _report_persist_helper_gaps()
        return list(out) if out else []
    except Exception as exc:
        return [f"persist_helper_gap_check_failed:{exc}"]


def set_containers_is_profession_bag_item(fn):
    """Register fn(item) -> bool for hip-slot profession satchels."""
    global _containers_is_profession_bag_item
    _containers_is_profession_bag_item = fn


def containers_is_profession_bag_item(item):
    if _containers_is_profession_bag_item is not None:
        return bool(_containers_is_profession_bag_item(item))
    return False


def set_containers_matching_profession_bag(fn):
    """Register fn(character, item) -> bag Item | None."""
    global _containers_matching_profession_bag
    _containers_matching_profession_bag = fn


def containers_matching_profession_bag(character, item):
    if _containers_matching_profession_bag is not None:
        return _containers_matching_profession_bag(character, item)
    return None


def set_containers_is_weave_bag_item(fn):
    """Register fn(item) -> bool for temporary pocket-weave bags."""
    global _containers_is_weave_bag_item
    _containers_is_weave_bag_item = fn


def containers_is_weave_bag_item(item):
    if _containers_is_weave_bag_item is not None:
        return bool(_containers_is_weave_bag_item(item))
    return False


def set_containers_is_extradim_bag_item(fn):
    """Register fn(item) -> bool for bound/stitched extra-carry bags."""
    global _containers_is_extradim_bag_item
    _containers_is_extradim_bag_item = fn


def containers_is_extradim_bag_item(item):
    if _containers_is_extradim_bag_item is not None:
        return bool(_containers_is_extradim_bag_item(item))
    return False


def set_containers_is_generic_weave_query(fn):
    """Register fn(query) -> bool for 'weave' / pocket-weave look tokens."""
    global _containers_is_generic_weave_query
    _containers_is_generic_weave_query = fn


def containers_is_generic_weave_query(query):
    if _containers_is_generic_weave_query is not None:
        return bool(_containers_is_generic_weave_query(query))
    return False


def set_containers_active_weave_bag(fn):
    """Register fn(character) -> weave bag Item | None."""
    global _containers_active_weave_bag
    _containers_active_weave_bag = fn


def containers_active_weave_bag(character):
    if _containers_active_weave_bag is not None:
        return _containers_active_weave_bag(character)
    return None


def set_press_beat_desk_keys(keys):
    """Register extra news-desk room keys (game towns, not engine defaults)."""
    global _press_beat_desk_keys
    _press_beat_desk_keys = frozenset(keys or ())


def press_beat_desk_keys():
    """Room keys the game treats as a news desk (empty in a bare engine)."""
    return _press_beat_desk_keys


def set_storm_chase_desk_keys(keys):
    """Register extra storm-watch desk room keys."""
    global _storm_chase_desk_keys
    _storm_chase_desk_keys = frozenset(keys or ())


def storm_chase_desk_keys():
    """Room keys the game treats as a storm-watch desk."""
    return _storm_chase_desk_keys
