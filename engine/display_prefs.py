"""
display_prefs.py -- player output chrome (D65 + formatting prefs catalog).

Aliases, custom prompts, sheet width, screenreader / map / combat-gag /
color-depth helpers. Pure presentation + input rewrite -- no networking,
no game rules. See docs/plans/mud_formatting_preferences.md.
"""

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

# Two-character optional-field tokens (checked before single-letter codes).
_SEGMENT_TOKENS = frozenset({
    "Hp", "En", "St", "Mn", "Fu", "Ex", "Gr",
})


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
    if not hasattr(character, "display_width"):
        character.display_width = WIDTH_DEFAULT
    if not hasattr(character, "screenreader"):
        character.screenreader = False
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
        # Default on (a11y). Sighted players may opt out via config combattags.
        # Screenreader mode always shows tags regardless of this flag.
        character.show_combat_tags = True
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

    Screenreader mode always forces tags on. Sighted players default on
    and may hide them with ``config combattags off``.
    """
    ensure_display_defaults(character)
    if getattr(character, "screenreader", False):
        return True
    return bool(getattr(character, "show_combat_tags", True))


def apply_screenreader_mode(character, enabled):
    """Turn screenreader mode on or off and sync related display prefs.

    When enabling: flatten ASCII UI, keep combat tags on, and turn the
    ASCII minimap off (directional ``map`` text is used instead). When
    disabling: only clear the screenreader flag -- leave map / tags /
    color alone so a later ``config screenreader off`` does not surprise
    someone who had customized those separately.

    Returns a short confirmation string for the caller to send.
    """
    ensure_display_defaults(character)
    if enabled:
        character.screenreader = True
        character.show_combat_tags = True
        character.show_minimap = False
        character.map_on_move = False
        character.map_on_look = False
        # Default fightlog on for screenreader (cinematic replay after fights).
        character.fightlog_enabled = True
        return (
            "Screenreader mode on -- ASCII frames and minimaps "
            "flatten to lists; combat stays tagged and brief. "
            "Fightlog on -- cinematic lines buffer for 'fightlog read' "
            "after a fight (config fightlog off to stop)."
        )
    character.screenreader = False
    return "Screenreader mode off."


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
    # Lazy import: commands imports display_prefs only inside dispatch.
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
    _ = game
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
    """Comma-separated faces of other party members, or ``\"\"`` if solo.

    Used by raw ``%g`` and the optional ``%Gr`` segment. Excludes self.
    """
    try:
        from engine import group as group_mod
    except Exception:
        return ""
    if not group_mod.in_group(character):
        return ""
    names = []
    for member in group_mod.group_members(character):
        if member is character:
            continue
        face = (
            getattr(member, "assumed_face", None)
            or getattr(member, "husk_display_name", None)
            or getattr(member, "key", "?")
        )
        names.append(str(face))
    return ", ".join(names)


def _prompt_vitals(character, game=None):
    """Gather meter values for prompt expansion (engine-safe via hooks).

    Optional resources (fuel / mana) only appear when the vitals builder
    stamped them -- humans never get a phantom fuel field.
    """
    _ = game
    hp = 100
    max_hp = 100
    energy = getattr(character, "energy", 0)
    energy_max = ""
    stamina = int(getattr(character, "stamina", 0) or 0)
    max_stamina = stamina
    fuel_str = ""
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
        if "stamina" in vitals:
            try:
                stamina = int(float(vitals["stamina"]))
            except (TypeError, ValueError):
                pass
        if "maxstamina" in vitals:
            try:
                max_stamina = int(float(vitals["maxstamina"]))
            except (TypeError, ValueError):
                pass
        if "fuel" in vitals:
            has_fuel = True
            fuel_str = str(vitals["fuel"])
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
    return {
        "hp": hp,
        "max_hp": max_hp,
        "energy": energy,
        "energy_max": energy_max,
        "stamina": stamina,
        "max_stamina": max_stamina,
        "has_fuel": has_fuel,
        "fuel": fuel_str,
        "has_mana": has_mana,
        "mana": mana_str,
        "max_mana": max_mana_str,
        "room": room_key,
        "name": name,
        "exits": format_exit_abbrevs(character, game),
        "group": format_group_names(character),
    }


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
        return _seg("<dark_grey> ", f"<violet>[{v['fuel']}fuel]")
    if code == "Ex":
        return _seg("<dark_grey> ", f"<silver>[{v['exits']}]")
    if code == "Gr":
        names = v["group"]
        if not names:
            return ""
        return _seg("<dark_grey> ", f"<white>[{names}]")
    return ""


def format_prompt(character, game=None):
    """Expand prompt_format tokens into a single line (prefs #27 / #28).

    Raw tokens (always emit a value -- fine for custom templates)::

      %h %H   lifeforce percent / out of 100
      %e      energy
      %E      exit abbrevs (or ``-``)
      %s %S   stamina current / max
      %f      fuel number, or ``\"\"`` when you have no fuel resource
      %m %M   mana / max mana, or ``\"\"`` when you have no mana
      %n %r   name / room
      %g      other groupmates ``Name1, Name2``, or ``\"\"`` if solo
      %%      literal %

    Optional *segment* tokens (two letters) -- each is a full colored
    ``[field]`` that **omits itself** when the resource does not apply::

      %Hp  [72/100hp]
      %En  [100/100en]   (Focus capacity at Tier; raw ceiling with combatnumbers)
      %St  [28/30st]
      %Mn  [88/100mn]   (mages only; percent of pool by default)
      %Fu  [80fuel]    (fuel Origins only)
      %Ex  [n,e,s,w]
      %Gr  [Sam, Dean] (only when grouped)

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
