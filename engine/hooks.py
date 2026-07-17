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

# Look exit filter (e.g. closed Devil's Gates). fn(dest, game) -> bool.
# True / missing hook = show the exit; False = hide it from look Paths.
_look_exit_visible = None

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

# Public leave/arrive display name (Celestial riding a host -> host key).
# fn(character, game) -> str | None. None / missing hook -> character.key.
_move_public_name = None

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
    """Register fn(room, game) -> list[str] for per-room look extras.

    Pass None to restore the empty default. Used for planar influence
    notes and similar room-scoped flavor (plain text, not color-alone).
    """
    global _room_look_extras
    _room_look_extras = fn


def room_look_extras(room, game):
    """Return extra look lines for this room, or []."""
    if _room_look_extras is not None:
        return list(_room_look_extras(room, game) or [])
    return []


def set_vampire_fear_message(fn):
    """Register fn(character, room) -> str or None for the post-look fear nudge."""
    global _vampire_fear_message
    _vampire_fear_message = fn


def vampire_fear_message(character, room):
    """Return the Vampire-vs-Slayer fear line, or None if no game/none due."""
    if _vampire_fear_message is not None:
        return _vampire_fear_message(character, room)
    return None


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


def set_look_exit_visible(fn):
    """Register fn(dest, game) -> bool (False hides the exit from look)."""
    global _look_exit_visible
    _look_exit_visible = fn


def look_exit_visible(dest, game):
    """True when look may list an exit into `dest`."""
    if _look_exit_visible is not None:
        return bool(_look_exit_visible(dest, game))
    return True


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
