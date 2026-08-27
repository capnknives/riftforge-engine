"""
display_prefs.py -- player output chrome (D65 + formatting prefs catalog).

Aliases, custom prompts, sheet width, screenreader / map / combat-gag /
color-depth helpers. Pure presentation + input rewrite -- no networking,
no game rules. See docs/plans/mud_formatting_preferences.md.
"""

import re

# Paragraph spacing for *asynchronous* arrivals -- a room event (say,
# emote, another player's action) or an incoming tell that lands on your
# screen between your own commands. Airy adds one blank line after each
# such event so a busy room does not read as a wall of text; packed keeps
# the old tight layout. This never touches your own command's reply text
# (see cmd_get's "You pick up X." + "You tuck it into your bag." staying
# glued together) or anything that already manages its own spacing
# (combat, pager pages, list/table dumps that loop session.send for one
# logical block) -- see engine.world.Room.broadcast and cmd_tell for the
# only two call sites that apply it.
OUTPUT_SPACING_AIRY = "airy"
OUTPUT_SPACING_PACKED = "packed"

# Dense gothic prompt built from *optional segment* tokens (%Hp, %Fu, …).
# Segments that do not apply (no fuel, no mana, solo) expand to "" so the
# field never leaves an empty bracket. Color via style.render.
DEFAULT_PROMPT = (
    "<dark_grey><"
    "%Hp%En%St%Mn%Fu%Ex"
    "<dark_grey>>"
    "%Gr"
)
# Exact old defaults -- migrate to DEFAULT_PROMPT on ensure (custom stays).
_OLD_DEFAULT_PROMPT = "[%h/%Hhp]"
_OLD_BRACKET_PROMPT = (
    "<dark_grey><"
    "<dark_red>[%h/%Hhp]"
    "<dark_grey> "
    "<teal>[%s/%Sst]"
    "<dark_grey>>"
)
_OLD_EXITS_PROMPT = (
    "<dark_grey><"
    "<dark_red>[%h/%Hhp]"
    "<dark_grey> "
    "<teal>[%s/%Sst]"
    "<dark_grey> "
    "<silver>%E"
    "<dark_grey>>"
)

# Caps so a malicious / accidental alias cannot explode input.
_MAX_ALIASES = 40
_MAX_ALIAS_KEY_LEN = 24
_MAX_ALIAS_VALUE_LEN = 120
# Room for color tags + segment tokens in the default template.
_MAX_PROMPT_LEN = 240

# Allowed sheet widths for framed ASCII (prefs #3). Prose stays unwrapped.
WIDTH_MIN = 40
WIDTH_MAX = 120
WIDTH_DEFAULT = 67

# NPC foot-traffic verbosity (bug report 782 / screenreader peace).
TRAFFIC_QUIET = "quiet"
TRAFFIC_NORMAL = "normal"
TRAFFIC_VERBOSE = "verbose"
TRAFFIC_MODES = frozenset({
    TRAFFIC_QUIET,
    TRAFFIC_NORMAL,
    TRAFFIC_VERBOSE,
})

# Two-character optional-field tokens (checked before single-letter codes).
_SEGMENT_TOKENS = frozenset({
    "Hp", "En", "St", "Mn", "Fu", "Mo", "Tg", "Ex", "Gr", "Nd",
    "Fm", "Vs", "Af", "In", "Fv", "Hs",
})

# Templates treated as "still on factory default" for Origin migration.
_GENERIC_PROMPT_TEMPLATES = frozenset({
    DEFAULT_PROMPT,
    _OLD_DEFAULT_PROMPT,
    _OLD_BRACKET_PROMPT,
    _OLD_EXITS_PROMPT,
})

# Brackets/quotes in chat *bodies* still trip VoiceOver/Mudrammer when channel
# chrome is already plain — strip them for plaincomms viewers only.
_PLAIN_COMM_BODY_SYMBOLS = re.compile(r"\(\(|\)\)|[()\[\]{}<>\"`]")


def normalize_output_spacing(value):
    """Return ``airy`` or ``packed`` for persist / config."""
    raw = str(value or "").strip().lower()
    if raw in (OUTPUT_SPACING_PACKED, "packed", "dense", "compact"):
        return OUTPUT_SPACING_PACKED
    return OUTPUT_SPACING_AIRY


def wants_airy_spacing(character):
    """True when the player wants a blank line after async room/tell events."""
    ensure_display_defaults(character)
    if getattr(character, "screenreader", False):
        return False
    return normalize_output_spacing(
        getattr(character, "output_spacing", OUTPUT_SPACING_AIRY),
    ) == OUTPUT_SPACING_AIRY


def preference_character(character, game=None):
    """Return the Character row that owns client/display prefs for ``character``.

    God bilocate twins inherit prefs from the owning Mantle so ``config exits
    compact`` and ``config screenreader`` still apply while act focus runs
    bare verbs through the twin body.
    """
    try:
        from engine import hooks
        resolved = hooks.preference_character(character, game)
        if resolved is not None:
            character = resolved
    except Exception:
        pass
    return character


def is_generic_prompt(template):
    """True when the player still has a factory prompt (eligible for Origin default)."""
    if template is None:
        return True
    return template in _GENERIC_PROMPT_TEMPLATES


def apply_origin_default_prompt(character, *, force=False):
    """Stamp Origin-appropriate prompt when still on generic default.

    ``force=True`` is used by ``prompt default`` to reset custom templates.
    Empty string (prompt off) is never overwritten unless forced.
    """
    ensure_display_defaults(character)
    if not force:
        if character.prompt_format == "":
            return
        from engine import hooks
        if not hooks.is_factory_prompt(character.prompt_format):
            return
    from engine import hooks
    character.prompt_format = hooks.origin_default_prompt(character)


def ensure_display_defaults(character):
    """Attach display-pref fields if missing (load / old Characters).

    Safe to call repeatedly. Defaults match attach_supers.
    Migrates the exact old ``[%h/%Hhp]`` default to the colored classic
    template; leaves any custom prompt alone.
    """
    if not hasattr(character, "command_aliases") or character.command_aliases is None:
        character.command_aliases = {}
    if not hasattr(character, "prompt_format") or character.prompt_format is None:
        character.prompt_format = DEFAULT_PROMPT
    elif character.prompt_format in (
        _OLD_DEFAULT_PROMPT,
        _OLD_BRACKET_PROMPT,
        _OLD_EXITS_PROMPT,
    ):
        character.prompt_format = DEFAULT_PROMPT
    # Empty string means prompt off -- leave it alone (do not treat as
    # missing). Custom templates must survive copyover / reattach.
    if not hasattr(character, "display_width"):
        character.display_width = WIDTH_DEFAULT
    if not hasattr(character, "screenreader"):
        character.screenreader = False
    if not hasattr(character, "output_spacing"):
        character.output_spacing = OUTPUT_SPACING_AIRY
    else:
        character.output_spacing = normalize_output_spacing(
            character.output_spacing,
        )
    if not hasattr(character, "show_minimap"):
        character.show_minimap = True
    if not hasattr(character, "map_on_move"):
        # Pref: after each move, also print the local minimap (default off).
        character.map_on_move = False
    if not hasattr(character, "map_on_look"):
        # Pref: embed local ASCII map in look (default off -- short look).
        character.map_on_look = False
    if not hasattr(character, "brief"):
        # Pref: skip room prose on auto-look after a move (default off).
        # Explicit ``look`` still shows the full description.
        character.brief = False
    if not hasattr(character, "drive_map_full"):
        # Vehicle / overland cruise redraw: full atlas (default) vs local
        # minimap. Screenreader always gets text bearings instead of ASCII.
        character.drive_map_full = True
    if not hasattr(character, "map_view_full"):
        # Pref: bare `map` shows the local minimap (default) vs the full
        # atlas grid -- config mapview atlas|minimap.
        character.map_view_full = False
    if not hasattr(character, "mapzone_overlay"):
        # Pref: append nearby zone bearings under ASCII maps (default off).
        character.mapzone_overlay = False
    if not hasattr(character, "exits_verbose"):
        # Pref: LOTJ-style Exits: / North - Dest (default); compact opt-in.
        character.exits_verbose = True
    # One-shot migrate: #534's first squash left compact as the saved
    # default. Locked design is verbose look -- bump once, then honor
    # an explicit ``config exits compact`` afterward.
    if int(getattr(character, "look_exits_rev", 0) or 0) < 1:
        character.exits_verbose = True
        character.look_exits_rev = 1
    if not hasattr(character, "group_row"):
        # Display-only party row (front/back); not combat math yet.
        character.group_row = "front"
    if not hasattr(character, "pager_lines"):
        # Lines per `more` page for long dumps (engine/pager.py).
        character.pager_lines = 20
    if not hasattr(character, "combat_gag_other"):
        # Prefs #20: hide third-party (room) combat lines for this viewer.
        character.combat_gag_other = False
    if not hasattr(character, "show_combat_tags"):
        # Default on (a11y). Any player may opt out via config combattags.
        character.show_combat_tags = True
    if not hasattr(character, "show_combat_hints"):
        # Tutorial nudges on engage/KO/score/connect reads; config combathints off.
        character.show_combat_hints = True
    if not hasattr(character, "compact_floor_items"):
        # config items compact on|off -- one paragraph for floor loot on look.
        character.compact_floor_items = False
    if not hasattr(character, "compact_vehicles"):
        # config vehicles compact on|off -- summarize 3+ parked rides on look.
        character.compact_vehicles = False
    if not hasattr(character, "compact_houses"):
        # config houses compact on|off -- summarize 3+ for-sale homes on look.
        character.compact_houses = False
    if not hasattr(character, "autokill"):
        # Dungeon fodder only -- pit / portal / stronghold trash (help autokill).
        character.autokill = False
    if not hasattr(character, "show_tips"):
        character.show_tips = True
    if not hasattr(character, "next_tip_tick"):
        character.next_tip_tick = None
    if not hasattr(character, "last_tip_index"):
        character.last_tip_index = None
    if not hasattr(character, "color_depth"):
        # Prefs #5 / #6: "ansi" (16) or "xterm256".
        character.color_depth = "ansi"
    if not hasattr(character, "channel_colors") or character.channel_colors is None:
        # Prefs #26: channel id -> style role name (e.g. ooc -> muted).
        character.channel_colors = {}
    if not hasattr(character, "suppress_surname_alert"):
        # Mortal mononyms: silence the login no-surname reminder (config).
        character.suppress_surname_alert = False
    if not hasattr(character, "plaincomms"):
        # Plain say/tell/OOC sentences without channel symbols (config plaincomms).
        character.plaincomms = False
    if not hasattr(character, "traffic_mode") or character.traffic_mode is None:
        # quiet | normal | verbose -- NPC leave/arrive crowd sampling.
        character.traffic_mode = TRAFFIC_NORMAL


def normalize_traffic_mode(raw):
    """Return a valid traffic mode id, or None."""
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if key in TRAFFIC_MODES:
        return key
    return None


def traffic_mode(character):
    """Resolved traffic verbosity for NPC movement lines."""
    ensure_display_defaults(character)
    return normalize_traffic_mode(getattr(character, "traffic_mode", None)) or (
        TRAFFIC_NORMAL
    )


def traffic_wants_quiet(character, game=None):
    """True when this viewer wants sampled NPC foot traffic in busy hubs."""
    ensure_display_defaults(character)
    if character is None:
        return False
    try:
        from engine.hooks import is_watching_room
        if is_watching_room(character, game=game):
            return False
    except Exception:
        pass
    mode = traffic_mode(character)
    if mode == TRAFFIC_VERBOSE:
        return False
    if mode == TRAFFIC_QUIET:
        return True
    return bool(getattr(character, "screenreader", False))


def drive_map_render_args(character):
    """``cmd_map`` args for vehicle cruise redraw, or ``None`` when skipped.

    Sighted players default to the full overland atlas (``map big``).
    ``config drivemap minimap`` keeps the smaller local window. Screenreader
    and ``config map off`` callers use text bearings and never invoke this.
    """
    ensure_display_defaults(character)
    if getattr(character, "screenreader", False):
        return None
    if not getattr(character, "show_minimap", True):
        return None
    if getattr(character, "drive_map_full", True):
        return "big"
    return ""


def wants_combat_tags(character):
    """True when this viewer should see [DMG]/[HIT]/… on combat lines.

    Default on. ``config combattags off`` hides tag prefixes for any viewer.
    Screenreader combat still uses short SVO lines that name hit/miss/limb in
    plain words when tags are off.
    """
    ensure_display_defaults(character)
    return bool(getattr(character, "show_combat_tags", True))


def wants_combat_hints(character):
    """True when repeat combat tutorial lines should show (engage/KO/score).

    Default on; ``config combathints off`` silences the long KO/engage
    reminders and help-topic pointers on connect reads.
    """
    ensure_display_defaults(character)
    return bool(getattr(character, "show_combat_hints", True))


def wants_compact_floor_items(character):
    """True when floor loot on look should collapse to one paragraph."""
    ensure_display_defaults(character)
    return bool(getattr(character, "compact_floor_items", False))


def wants_compact_vehicles(character):
    """True when 3+ parked vehicles should summarize on ordinary look."""
    ensure_display_defaults(character)
    return bool(getattr(character, "compact_vehicles", False))


def wants_compact_houses(character):
    """True when 3+ for-sale street homes should summarize on look / homes here."""
    ensure_display_defaults(character)
    return bool(getattr(character, "compact_houses", False))


def wants_plain_comms(character):
    """True when say/tell/OOC should drop channel symbols for TTS."""
    ensure_display_defaults(character)
    return bool(getattr(character, "plaincomms", False))


def plain_comm_face(face: str) -> str:
    """Rewrite a chat speaker label without bracket chrome."""
    text = str(face or "").strip()
    if not text:
        return "someone"
    if text.endswith("(GM)"):
        base = text[:-4].rstrip()
        return f"GM {base}" if base else "GM"
    return text


def plain_comm_message(message: str) -> str:
    """Strip bracket/quote symbols from chat body text for TTS readers."""
    text = str(message or "").strip()
    if not text:
        return ""
    cleaned = _PLAIN_COMM_BODY_SYMBOLS.sub(" ", text)
    return " ".join(cleaned.split())


def apply_plain_comms_mode(character, enabled):
    """Toggle plain comms and return a short confirmation string."""
    ensure_display_defaults(character)
    if enabled:
        character.plaincomms = True
        return (
            "Plain comms on. Example: OOC. GM CapnKnives says. hello. "
            "Turn off with config plaincomms off."
        )
    character.plaincomms = False
    return "Plain comms off."


def _plain_say_verb(you, you_verb, they_verb, tone):
    """Pick a plain speech verb (no curly quotes)."""
    if tone == "whisper":
        return "whisper" if you else "whispers"
    if tone == "shout":
        return "shout" if you else "shouts"
    if tone == "drawl":
        return "drawl" if you else "drawls"
    return you_verb if you else they_verb


def format_ooc_chat(viewer, face, message, *, kind="normal"):
    """OOC line for one viewer (plain or default ``((OOC))`` chrome)."""
    text = str(message or "").strip()
    if not text:
        return ""
    if wants_plain_comms(viewer):
        text = plain_comm_message(text)
        if not text:
            return ""
        label = plain_comm_face(face)
        from engine import ooc_channel
        if kind == ooc_channel.OOC_KIND_AUTHOR_NUDGE:
            return f"OOC. Author {label} says. {text}."
        return f"OOC. {label} says. {text}."
    from engine import ooc_channel
    return ooc_channel.format_ooc_line(face, text, kind=kind)


def format_questions_chat(viewer, face, message):
    """Questions channel line for one viewer (plain or ``((QUESTIONS))`` chrome)."""
    text = str(message or "").strip()
    if not text:
        return ""
    if wants_plain_comms(viewer):
        text = plain_comm_message(text)
        if not text:
            return ""
        label = plain_comm_face(face)
        return f"Questions. {label} asks. {text}."
    from engine import questions_channel
    return questions_channel.format_questions_line(face, text)


def format_say_chat(
    viewer,
    face,
    message,
    *,
    you=False,
    you_verb="say",
    they_verb="says",
    tone=None,
    voice_phrase=None,
):
    """Room say line for one viewer (plain or quoted default)."""
    text = str(message or "").strip()
    if not text:
        return ""
    vp = ""
    if not you:
        vp = str(voice_phrase or "").strip()
    if wants_plain_comms(viewer):
        text = plain_comm_message(text)
        if not text:
            return ""
        verb = _plain_say_verb(you, you_verb, they_verb, tone)
        who = "You" if you else plain_comm_face(face)
        if vp:
            return f"{who} {verb} {vp}. {text}."
        return f"{who} {verb}. {text}."
    if tone:
        if tone == "whisper":
            if you:
                return f'You whisper, "{text}"'
            if vp:
                return f'{face} whispers {vp}, "{text}"'
            return f'{face} whispers, "{text}"'
        if tone == "shout":
            if you:
                return f'You shout, "{text}"'
            if vp:
                return f'{face} shouts {vp}, "{text}"'
            return f'{face} shouts, "{text}"'
        if tone == "drawl":
            if you:
                return f'You drawl, "{text}"'
            if vp:
                return f'{face} drawls {vp}, "{text}"'
            return f'{face} drawls, "{text}"'
    if you:
        return f'You {you_verb}, "{text}"'
    if vp:
        return f'{face} {they_verb} {vp}, "{text}"'
    return f'{face} {they_verb}, "{text}"'


def format_tell_chat(viewer, *, outgoing, peer_face, message):
    """Private tell line for one viewer."""
    text = str(message or "").strip()
    if not text:
        return ""
    peer = str(peer_face or "?").strip() or "?"
    if wants_plain_comms(viewer):
        text = plain_comm_message(text)
        if not text:
            return ""
        label = plain_comm_face(peer)
        if outgoing:
            return f"You tell {label}. {text}."
        return f"Tell from {label}. {text}."
    if outgoing:
        return f'You tell {peer}, "{text}"'
    return f'{peer} tells you, "{text}"'


def format_custom_chat(viewer, channel_title, prefix, face, message):
    """Staff/custom global channel line for one viewer."""
    text = str(message or "").strip()
    if not text:
        return ""
    if wants_plain_comms(viewer):
        text = plain_comm_message(text)
        if not text:
            return ""
        title = str(channel_title or "Channel").strip() or "Channel"
        label = plain_comm_face(face)
        return f"{title}. {label} says. {text}."
    label = str(face or "?").strip() or "?"
    prefix_text = str(prefix or "").strip()
    if prefix_text:
        return f"{prefix_text} [{label}]: {text}"
    return f"[{label}]: {text}"


def apply_screenreader_mode(character, enabled):
    """Turn screenreader mode on or off and sync related display prefs.

    When enabling: flatten ASCII UI, turn the ASCII minimap off (directional
    ``map`` text is used instead), and default fightlog on. When disabling:
    only clear the screenreader flag -- leave map / tags / color alone so a
    later ``config screenreader off`` does not surprise someone who had
    customized those separately.

    Returns a short confirmation string for the caller to send.
    """
    ensure_display_defaults(character)
    if enabled:
        character.screenreader = True
        character.show_minimap = False
        character.map_on_move = False
        character.map_on_look = False
        if traffic_mode(character) == TRAFFIC_NORMAL:
            character.traffic_mode = TRAFFIC_QUIET
        # Default fightlog on for screenreader (cinematic replay after fights).
        character.fightlog_enabled = True
        _push_screenreader_status(character)
        return (
            "Screenreader mode on -- ASCII frames and minimaps "
            "flatten to lists; combat stays tagged and brief. "
            "NPC foot traffic is quiet (config traffic normal to restore). "
            "Fightlog on -- cinematic lines buffer for 'fightlog read' "
            "after a fight (config fightlog off to stop)."
        )
    character.screenreader = False
    _push_screenreader_status(character)
    return "Screenreader mode off."


def _push_screenreader_status(character):
    """Tell GMCP clients (browser HUD) the atlas should hide or return."""
    from engine import gmcp

    gmcp.push_char_status(character)


def sheet_width(character):
    """Framed-sheet column budget for this player (prefs #3)."""
    ensure_display_defaults(character)
    try:
        w = int(character.display_width)
    except (TypeError, ValueError):
        w = WIDTH_DEFAULT
    return max(WIDTH_MIN, min(WIDTH_MAX, w))


def color_depth(character):
    """Return 'ansi' or 'xterm256' for paint_for."""
    ensure_display_defaults(character)
    depth = getattr(character, "color_depth", "ansi") or "ansi"
    if depth in ("256", "xterm", "xterm256"):
        return "xterm256"
    return "ansi"


def expand_aliases(character, raw):
    """Rewrite the first word through command_aliases if present.

    Only expands when the verb is NOT already a real COMMANDS key -- so an
    alias can never shadow a built-in. Alias values may include args
    (e.g. ``ns`` -> ``north``).
    Returns the (possibly unchanged) raw line.
    """
    ensure_display_defaults(character)
    raw = (raw or "").strip()
    if not raw:
        return raw
    parts = raw.split(maxsplit=1)
    verb = parts[0].lower()
    # commands.py imports display_prefs at module scope for _dispatch_body.
    from commands import COMMANDS, DIRECTIONS
    if verb in COMMANDS or verb in DIRECTIONS:
        return raw
    aliases = character.command_aliases or {}
    expansion = aliases.get(verb)
    if not expansion:
        return raw
    rest = parts[1] if len(parts) > 1 else ""
    if rest:
        return f"{expansion} {rest}".strip()
    return expansion.strip()


def say_speech_verb(message):
    """Pick says / asks / exclaims from trailing punctuation (prefs #24)."""
    text = (message or "").rstrip()
    if text.endswith("?"):
        return "ask", "asks"
    if text.endswith("!"):
        return "exclaim", "exclaims"
    return "say", "says"


def emote_body(character, args):
    """Build emote text with leading ``'s`` possessive support (prefs #25).

    ``emote 's eyes glow.`` -> ``Name's eyes glow.``
    ``emote grins.`` -> ``Name grins.``
    """
    text = (args or "").strip()
    if not text:
        return None
    try:
        from engine.command_support import _display_name
        key = _display_name(character)
    except Exception:
        key = getattr(character, "key", "?")
    if text.startswith("'s ") or text.startswith("'s\t"):
        return f"{key}'s {text[3:].lstrip()}"
    if text.startswith("'s"):
        return f"{key}'s{text[2:]}"
    return f"{key} {text}"


def format_exit_abbrevs(character, game=None):
    """Compact exit string for prompt ``%E`` (e.g. ``n,e,s,w``).

    Honors the same visibility gates as look (hooks + known secret exits).
    Returns ``-`` when nowhere / no visible exits. Labels are the signal.
    """
    try:
        from engine import hooks
        character = hooks.perception_character(character, game)
    except Exception:
        pass
    room = getattr(character, "location", None)
    if room is None:
        return "-"
    try:
        from engine import hooks
        from engine import vision as vision_mod
        from engine import style as style_mod
    except Exception:
        return "-"
    if not vision_mod.can_see_room(character, room):
        return "-"
    pairs = []
    for direction, dest in (room.exits or {}).items():
        if not hooks.look_exit_visible(dest, game):
            continue
        if not vision_mod.character_knows_exit(character, room, direction):
            continue
        pairs.append((direction, dest.look_title() if hasattr(dest, "look_title") else ""))
    if not pairs:
        return "-"
    # Reuse style's compact token order without paint.
    tokens = []
    seen = set()
    by_dir = {str(d).strip().lower(): d for d, _ in pairs}
    for name in style_mod._EXIT_LINE_ORDER:
        if name in by_dir:
            tokens.append(style_mod._exit_abbrev(name))
            seen.add(name)
    for direction, _dest in pairs:
        key = str(direction).strip().lower()
        if key in seen:
            continue
        tokens.append(style_mod._exit_abbrev(direction))
        seen.add(key)
    return ",".join(tokens) if tokens else "-"


def format_group_names(character):
    """Comma-separated faces of other *colocated* party members, or ``\"\"``.

    Used by raw ``%g`` and the optional ``%Gr`` segment. Excludes self.
    Only groupmates sharing the leader's room or vehicle appear -- apart
    mates stay on ``group`` / room (Group) tags but not the prompt (live
    bug #256: stale [Sam, Dean] while walking solo after a crash).
    """
    try:
        from engine import group as group_mod
    except Exception:
        return ""
    if not group_mod.in_group(character):
        return ""
    leader = group_mod.resolve_leader(character)
    if leader is None:
        return ""
    names = []
    for member in group_mod.group_members(character):
        if member is character:
            continue
        if not group_mod.mates_share_group_location(leader, member):
            continue
        face = (
            getattr(member, "assumed_face", None)
            or getattr(member, "husk_display_name", None)
            or getattr(member, "key", "?")
        )
        names.append(str(face))
    return ", ".join(names)


def _prompt_momentum_value(character, vitals):
    """Integer Momentum for prompt tokens (-100..100; 0 when unset).

    Prefers Char.Vitals ``momentum`` when SUPERS stamped it so the prompt
    line matches GMCP Mudlet gauges; otherwise reads Character.momentum
    directly (engine-safe -- the field lives on world.Character).
    """
    raw = None
    if isinstance(vitals, dict) and "momentum" in vitals:
        raw = vitals.get("momentum")
    else:
        raw = getattr(character, "momentum", 0.0)
    try:
        return int(round(float(raw or 0.0)))
    except (TypeError, ValueError):
        return 0


def _prompt_vitals(character, game=None):
    """Gather meter values for prompt expansion (engine-safe via hooks).

    Optional resources (fuel / mana) only appear when the vitals builder
    stamped them -- humans never get a phantom fuel field.
    """
    try:
        from engine import hooks
        character = hooks.perception_character(character, game)
    except Exception:
        pass
    hp = 100
    max_hp = 100
    energy = getattr(character, "energy", 0)
    energy_max = ""
    stamina = int(getattr(character, "stamina", 0) or 0)
    max_stamina = stamina
    fuel_str = ""
    fuel_label = "fuel"
    mana_str = ""
    max_mana_str = ""
    has_fuel = False
    has_mana = False
    try:
        from engine import hooks
        vitals = hooks.gmcp_char_vitals(character) or {}
    except Exception:
        vitals = {}
    if vitals:
        raw_hp = vitals.get("hp_raw", vitals.get("hp"))
        raw_max = vitals.get("maxhp_raw", vitals.get("maxhp"))
        try:
            cur = float(raw_hp)
            cap = float(raw_max) if raw_max not in (None, "", "0") else 0.0
            if cap > 0:
                if cap == 100.0 and "hp_raw" not in vitals:
                    hp = max(0, min(100, int(round(cur))))
                    max_hp = 100
                else:
                    hp = max(0, min(100, int(round(100.0 * cur / cap))))
                    max_hp = 100
        except (TypeError, ValueError):
            pass
        if "energy" in vitals:
            energy = vitals["energy"]
        if "energymax" in vitals:
            energy_max = str(vitals["energymax"])
        elif "energy_raw" in vitals:
            # Percent mode without energymax still has raw ceiling in payload.
            energy_max = ""
        raw_stam = vitals.get("stamina_raw", vitals.get("stamina"))
        raw_stam_max = vitals.get("maxstamina_raw", vitals.get("maxstamina"))
        try:
            cur = float(raw_stam)
            cap = float(raw_stam_max) if raw_stam_max not in (None, "", "0") else 0.0
            if cap > 0:
                if cap == 100.0 and "stamina_raw" not in vitals:
                    stamina = max(0, min(100, int(round(cur))))
                    max_stamina = 100
                else:
                    stamina = max(0, min(100, int(round(100.0 * cur / cap))))
                    max_stamina = 100
        except (TypeError, ValueError):
            pass
        if "fuel" in vitals:
            has_fuel = True
            fuel_str = str(vitals["fuel"])
            # Path noun from SUPERS vitals (blood / grace / …); fall back.
            raw_label = vitals.get("fuel_label") or "fuel"
            fuel_label = str(raw_label).strip().lower() or "fuel"
        if "mana" in vitals:
            has_mana = True
            mana_str = str(vitals["mana"])
            max_mana_str = str(vitals.get("maxmana", ""))
    room = getattr(character, "location", None)
    room_key = room.key if room is not None else "-"
    try:
        from engine.command_support import _display_name
        name = _display_name(character)
    except Exception:
        name = getattr(character, "key", "?")
    bands = {
        "hp": hp,
        "max_hp": max_hp,
        "energy": energy,
        "energy_max": energy_max,
        "stamina": stamina,
        "max_stamina": max_stamina,
        "has_fuel": has_fuel,
        "fuel": fuel_str,
        "fuel_label": fuel_label,
        "has_mana": has_mana,
        "mana": mana_str,
        "max_mana": max_mana_str,
        # Fight Momentum (-100..100). Prefer Char.Vitals when SUPERS stamped
        # it (keeps prompt + GMCP in lockstep); else Character.momentum.
        "momentum": _prompt_momentum_value(character, vitals),
        "room": room_key,
        "name": name,
        "exits": format_exit_abbrevs(character, game),
        "group": format_group_names(character),
        "target_band": hooks.prompt_target_band(character, game),
        "need_band": hooks.prompt_need_band(character, game),
    }
    bands.update(hooks.prompt_supplemental_bands(character, game))
    return bands


def _seg(space_markup, colored_inner):
    """Prefix a segment with a muted space when the field is present."""
    if not colored_inner:
        return ""
    return f"{space_markup}{colored_inner}"


def _expand_segment(code, v):
    """Map a two-letter segment token to a colored ``[field]`` or ``\"\"``.

    Missing resources (fuel, mana, group) return empty -- never ``[-]``.
    """
    if code == "Hp":
        return f"<dark_red>[{v['hp']}/{v['max_hp']}hp]"
    if code == "En":
        if v.get("energy_max"):
            inner = f"[{v['energy']}/{v['energy_max']}en]"
        else:
            inner = f"[{v['energy']}en]"
        return _seg("<dark_grey> ", f"<gold>{inner}")
    if code == "St":
        return _seg(
            "<dark_grey> ",
            f"<teal>[{v['stamina']}/{v['max_stamina']}st]",
        )
    if code == "Mn":
        if not v["has_mana"]:
            return ""
        return _seg(
            "<dark_grey> ",
            f"<pale_blue>[{v['mana']}/{v['max_mana']}mn]",
        )
    if code == "Fu":
        if not v["has_fuel"]:
            return ""
        # Path noun from vitals (blood / grace / …); never bare "fuel".
        noun = v.get("fuel_label") or "fuel"
        return _seg("<dark_grey> ", f"<violet>[{v['fuel']}{noun}]")
    if code == "Mo":
        # Omit at 0 so out-of-fight prompts stay quiet; negative hex shove
        # and banked fight tempo both show.
        mom = int(v.get("momentum", 0) or 0)
        if mom == 0:
            return ""
        return _seg("<dark_grey> ", f"<gold>[{mom}mo]")
    if code == "Tg":
        band = (v.get("target_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<dark_red>[{band}]")
    if code == "Ex":
        return _seg("<dark_grey> ", f"<silver>[{v['exits']}]")
    if code == "Gr":
        names = v["group"]
        if not names:
            return ""
        return _seg("<dark_grey> ", f"<white>[{names}]")
    if code == "Nd":
        band = (v.get("need_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<silver>[{band}]")
    if code == "Fm":
        band = (v.get("form_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<white>[{band}]")
    if code == "Vs":
        band = (v.get("vessel_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<violet>[{band}]")
    if code == "Af":
        band = (v.get("affliction_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<gold>[{band}]")
    if code == "In":
        band = (v.get("integrity_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<silver>[{band}]")
    if code == "Fv":
        band = (v.get("favor_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<pale_blue>[{band}]")
    if code == "Hs":
        band = (v.get("hospital_band") or "").strip()
        if not band:
            return ""
        return _seg("<dark_grey> ", f"<dark_red>[{band}]")
    return ""


def format_prompt(character, game=None):
    """Expand prompt_format tokens into a single line (prefs #27 / #28).

    Raw tokens (always emit a value -- fine for custom templates)::

      %h %H   lifeforce percent / out of 100
      %e      focus pool (legacy token name; score labels this Focus)
      %E      exit abbrevs (or ``-``)
      %s %S   stamina percent / out of 100 (remaining effort)
      %f      fuel number, or ``\"\"`` when you have no fuel resource
      %m %M   mana / max mana, or ``\"\"`` when you have no mana
      %o      Momentum (-100..100; 0 out of fight)
      %n %r   name / room
      %g      other groupmates ``Name1, Name2``, or ``\"\"`` if solo
      %%      literal %

    Optional *segment* tokens (two letters) -- each is a full colored
    ``[field]`` that **omits itself** when the resource does not apply::

      %Hp  [72/100hp]
      %En  [100/100en]   (Focus pool at Tier; raw ceiling with combatnumbers)
      %St  [72/100st]
      %Mn  [88/100mn]   (mages only; percent of pool by default)
      %Fu  [80blood]   (fuel Origins only; Path noun)
      %Mo  [66mo]      (fight Momentum; omits at 0)
      %Tg  [bloodied]  foe lifeforce band while fighting (RP only; omits when unscathed)
      %Nd  [hungry]    most urgent lifestyle need (omits when content)
      %Fm  [true]      guise / true form / frenzy (fuel Origins; omits in guise)
      %Vs  [strained]  vessel strain (Mantle embodied; omits when settled)
      %Af  [winded]    first active combat condition (omits when clean)
      %In  [damaged]   Constructed Integrity state (omits when stable)
      %Fv  [waning]    Cosmic Favor state (omits when attuned)
      %Hs  [clinic]     hospitalized at Town Clinic (omits when ambulatory)
      %Ex  [n,e,s,w]
      %Gr  [Sam, Dean] (colocated groupmates only; omits when solo/apart)

    Color tags (``<dark_red>``, ``<teal>``, …) expand via ``style.render``
    after tokens. Empty / disabled prompt returns \"\".

    Hard rule: this module lives under ``engine/`` -- never import ``supers``.
    Caps come from ``hooks.gmcp_char_vitals`` when SUPERS is installed.
    """
    ensure_display_defaults(character)
    template = character.prompt_format
    if template is None or template == "":
        return ""
    v = _prompt_vitals(character, game)

    out = []
    i = 0
    while i < len(template):
        ch = template[i]
        if ch == "%" and i + 1 < len(template):
            # Two-letter optional segments first (%Hp, %Fu, %Gr, …).
            if i + 2 < len(template):
                two = template[i + 1: i + 3]
                if two in _SEGMENT_TOKENS:
                    out.append(_expand_segment(two, v))
                    i += 3
                    continue
            code = template[i + 1]
            if code == "%":
                out.append("%")
            elif code == "h":
                out.append(str(v["hp"]))
            elif code == "H":
                out.append(str(v["max_hp"]))
            elif code == "e":
                out.append(str(v["energy"]))
            elif code == "E":
                out.append(v["exits"])
            elif code == "s":
                out.append(str(v["stamina"]))
            elif code == "S":
                out.append(str(v["max_stamina"]))
            elif code == "f":
                out.append(v["fuel"])
            elif code == "m":
                out.append(v["mana"])
            elif code == "M":
                out.append(v["max_mana"])
            elif code == "o":
                out.append(str(v["momentum"]))
            elif code == "n":
                out.append(v["name"])
            elif code == "r":
                out.append(v["room"])
            elif code == "g":
                out.append(v["group"])
            else:
                out.append("%")
                out.append(code)
            i += 2
            continue
        out.append(ch)
        i += 1
    expanded = "".join(out)
    # Apply gothic <tag> color switches after tokens (DEFAULT_PROMPT uses them).
    from engine import style
    return style.render(expanded)


def send_prompt(character, game=None):
    """Send the player's prompt line if they have a live telnet Session.

    Skips FakeSession / SilentSession (smoke + Cadence) so tests and AI
    path stay free of chrome. Real clients get the tokenized prompt.
    """
    session = getattr(character, "session", None)
    if session is None:
        return
    # Only the live telnet Session from engine.connection -- not smoke
    # FakeSession or npc_act.SilentSession.
    try:
        from engine.connection import Session
        if not isinstance(session, Session):
            return
    except Exception:
        return
    line = format_prompt(character, game)
    if not line:
        return
    # Blank before the prompt so framed look/who/score never glue into it.
    session.send("")
    # Already color-rendered in format_prompt -- do not mute the whole line
    # (that would wipe segment colors). Session.send strips when color off.
    session.send(line)


def paint_combat_line(character, role, text):
    """Paint a combat line for one viewer (prefs #19). Role is combat_*.

    Uses style.paint_layered_for (docs/plans/combat_color_gothic.md): the
    direction role is still the WHOLE line's base color exactly as before,
    but combat_prose may have embedded a rare ``<tag>...<_base>`` accent
    span (a silver blade, a rider callout) that switches color for just
    that span. A line with no such markup renders byte-identical to the
    old flat style.paint_for call -- this is a superset, not a behavior
    change, for every caller that never emits tags.
    """
    from engine import style
    return style.paint_layered_for(character, role, text)


def channel_role(character, channel, default="muted"):
    """Style role for a chat channel (prefs #26), with safe fallback."""
    ensure_display_defaults(character)
    custom = (character.channel_colors or {}).get(channel)
    if custom:
        from engine import style
        if custom in style.COLORS or custom in style.COLORS_XTERM256:
            return custom
    return default


def paint_channel_line(character, channel, text, *, default="muted"):
    """Paint one chat line for ``channel`` respecting viewer prefs."""
    from engine import style

    ensure_display_defaults(character)
    if getattr(character, "screenreader", False) or not getattr(
        character, "use_color", True,
    ):
        return text
    role = channel_role(character, channel, default=default)
    return style.paint_for(character, role, text)


def _explicit_channel_color(character, channel):
    """Return a player-chosen channel role, or None for default layered chrome.

    ``config channel ooc gold`` (and the same for questions) paints the
    whole line one color. The catalog default ``ooc`` still means the
    layered crimson-parens / gold-letters treatment.
    """
    ensure_display_defaults(character)
    custom = (character.channel_colors or {}).get(channel)
    if not custom or custom == "ooc":
        return None
    from engine import style
    if custom in style.COLORS or custom in style.COLORS_XTERM256:
        return custom
    return None


def paint_double_bracket_channel_line(
    character, mark, rest, *, channel="ooc", body_role="ooc",
):
    """Sighted ``((MARK))`` chrome: crimson parens, gold letters, body on rest.

    *rest* is never parsed as ``paint_layered`` markup -- player text can
    contain ``<gold>`` or URLs without eating the line. An explicit
    ``config channel <channel> <role>`` other than ``ooc`` paints the
    reconstructed whole line in that one role instead.
    """
    from engine import style

    ensure_display_defaults(character)
    whole = f"(({mark})){rest}"
    custom = _explicit_channel_color(character, channel)
    depth = color_depth(character)
    if custom:
        return style.paint_preserving_urls(custom, whole, depth=depth)
    # Layered prefix only -- mark is a fixed catalog word (OOC / QUESTIONS).
    prefix = style.paint_layered_for(
        character,
        "dark_red",
        f"<dark_red>((<gold>{mark}<dark_red>))",
    )
    if not rest:
        return prefix
    return prefix + style.paint_preserving_urls(body_role, rest, depth=depth)


def paint_plaincomms_channel_lead(character, plain, lead, *, body_role="ooc"):
    """Gold the leading ``OOC.`` / ``Questions.`` word; body stays parchment."""
    from engine import style

    ensure_display_defaults(character)
    text = str(plain or "")
    if not text.startswith(lead):
        return style.paint_for(character, body_role, text)
    rest = text[len(lead):]
    depth = color_depth(character)
    return (
        style.paint_for(character, "gold", lead)
        + style.paint_preserving_urls(body_role, rest, depth=depth)
    )


def format_tag_line(
    character,
    tag,
    body,
    *,
    tag_role="gold",
    body_role="prose",
):
    """Plain ``[TAG]`` plus body, with optional split role paint for sighted play.

    System notifications ([TIP], [RIFT], …) pair a bracket label with prose
    so meaning never depends on color alone (SYSTEMS_DESIGN.md section 8).
    Sighted players with color on get ``tag_role`` on the label and
    ``body_role`` on the remainder (60-30-10 — prefs #8 / #29 partial).

    Returns flat text when ``character`` is None, ``screenreader`` is on, or
    ``use_color`` is off. Uses ``paint_layered_for`` so the tag span can
    accent without painting the whole line one role.

    Agent guide: docs/plans/sighted_color_guide.md section 6 pairing rule.
    """
    from engine import style

    marker = f"[{str(tag or '').strip()}]"
    body_text = str(body or "").strip()
    if not body_text:
        plain = marker
    else:
        plain = f"{marker} {body_text}"
    if character is None:
        return plain
    ensure_display_defaults(character)
    if getattr(character, "screenreader", False) or not getattr(
        character, "use_color", True,
    ):
        return plain
    if not body_text:
        return style.paint_for(character, tag_role, marker)
    template = f"<{tag_role}>{marker}<{body_role}> {body_text}"
    return style.paint_layered_for(character, body_role, template)


# Known system tags for ``format_tagged_text`` — tag chrome only; body stays
# prose so meaning never depends on color (prefs #8 / rule 7).
_TAG_ROLES = {
    "ALERT": "alert",
    "HEAL": "ok",
    "TIP": "gold",
    "RIFT": "alert",
    "WARD": "alert",
    "DMG": "error",
    "TRAP": "warn",
    "GAIN": "ok",
    "TIER": "alert",
    "JOURNAL": "accent",
    "INTEL": "alert",
    "OOCMAIL": "gold",
}


def format_tagged_text(character, text, *, body_role="prose"):
    """Paint a line that already starts with ``[TAG] body``.

    Unknown tags, empty text, or tags that are not a single token stay
    unchanged. Flat when screenreader / color off (via ``format_tag_line``).
    """
    raw = str(text or "")
    if not raw.startswith("["):
        return raw
    close = raw.find("]")
    if close < 2:
        return raw
    tag = raw[1:close]
    if not tag.isalpha():
        return raw
    role = _TAG_ROLES.get(tag.upper())
    if role is None:
        return raw
    body = raw[close + 1 :].lstrip()
    return format_tag_line(
        character, tag, body, tag_role=role, body_role=body_role,
    )


def send_tagged(character, text, *, body_role="prose"):
    """Send one tagged system line, painted for this viewer."""
    session = getattr(character, "session", None)
    if session is None or not text:
        return False
    session.send(format_tagged_text(character, text, body_role=body_role))
    return True


def broadcast_tagged(room, text, exclude=None, *, body_role="prose"):
    """Room-broadcast a tagged line, painted per watcher."""
    if room is None or not text:
        return
    def _line(watcher):
        return format_tagged_text(watcher, text, body_role=body_role)

    room.broadcast(_line, exclude=exclude)


def paint_feedback_line(character, text, *, role="prose"):
    """Paint a one-shot player feedback line (shop, quest, lifestyle).

    Flat when screenreader on or color off. ``character`` None returns plain
    text (offline helpers / sims).
    """
    plain = str(text or "")
    if not plain or character is None:
        return plain
    ensure_display_defaults(character)
    if getattr(character, "screenreader", False) or not getattr(
        character, "use_color", True,
    ):
        return plain
    from engine import style
    return style.paint_for(character, role, plain)


def paint_shop_result(character, ok, message):
    """Paint economy buy/sell feedback (ok / warn / error roles)."""
    role = "ok" if ok else "warn"
    if not ok:
        low = (message or "").lower()
        if any(
            bit in low
            for bit in (
                "can't afford",
                "payment failed",
                "not carrying",
                "doesn't sell",
                "doesn't have",
            )
        ):
            role = "error"
    return paint_feedback_line(character, message, role=role)


def send_status_sheet(character, title, lines, *, width=48):
    """Send a Blood & Velvet framed status dump as one session string.

    Used for Origin fuel hubs (blood, mana, grace, …) and wallet. Flat
    borders when screenreader on (``format_sheet`` SR path). ``Session.send``
    strips ANSI when color is off.
    """
    session = getattr(character, "session", None)
    if session is None:
        return False
    body = [str(row) for row in (lines or [])]
    from engine import style
    framed = style.format_sheet(
        str(title or "Status"),
        body,
        width=width,
        screenreader=bool(getattr(character, "screenreader", False)),
    )
    session.send("\r\n".join(framed))
    return True
