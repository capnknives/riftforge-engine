"""
display_prefs.py -- player output chrome (D65 + formatting prefs catalog).

Aliases, custom prompts, sheet width, screenreader / map / combat-gag /
color-depth helpers. Pure presentation + input rewrite -- no networking,
no game rules. See docs/plans/mud_formatting_preferences.md.
"""

# Classic gothic bracket prompt (DIKU-adjacent): <[100/100hp] [30/30st]>
# Color tags use style.render after token expansion. %h/%H are percent.
DEFAULT_PROMPT = (
    "<dark_grey><"
    "<dark_red>[%h/%Hhp]"
    "<dark_grey> "
    "<teal>[%s/%Sst]"
    "<dark_grey>>"
)
# Exact old default -- migrate to DEFAULT_PROMPT on ensure (custom stays).
_OLD_DEFAULT_PROMPT = "[%h/%Hhp]"

# Caps so a malicious / accidental alias cannot explode input.
_MAX_ALIASES = 40
_MAX_ALIAS_KEY_LEN = 24
_MAX_ALIAS_VALUE_LEN = 120
# Room for color tags in the default template (~40 visible + markup).
_MAX_PROMPT_LEN = 160

# Allowed sheet widths for framed ASCII (prefs #3). Prose stays unwrapped.
WIDTH_MIN = 40
WIDTH_MAX = 120
WIDTH_DEFAULT = 67


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
    elif character.prompt_format == _OLD_DEFAULT_PROMPT:
        character.prompt_format = DEFAULT_PROMPT
    if not hasattr(character, "display_width"):
        character.display_width = WIDTH_DEFAULT
    if not hasattr(character, "screenreader"):
        character.screenreader = False
    if not hasattr(character, "show_minimap"):
        character.show_minimap = True
    if not hasattr(character, "combat_gag_other"):
        # Prefs #20: hide third-party (room) combat lines for this viewer.
        character.combat_gag_other = False
    if not hasattr(character, "color_depth"):
        # Prefs #5 / #6: "ansi" (16) or "xterm256".
        character.color_depth = "ansi"
    if not hasattr(character, "channel_colors") or character.channel_colors is None:
        # Prefs #26: channel id -> style role name (e.g. ooc -> muted).
        character.channel_colors = {}


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
    (e.g. ``ns`` -> ``north``; ``greet`` -> ``say Hello there``).
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
    key = character.key
    if text.startswith("'s ") or text.startswith("'s\t"):
        return f"{key}'s {text[3:].lstrip()}"
    if text.startswith("'s"):
        return f"{key}'s{text[2:]}"
    return f"{key} {text}"


def format_prompt(character, game=None):
    """Expand prompt_format tokens into a single line (prefs #27 / #28).

    Tokens (case-sensitive after %):
      %h  lifeforce percent (0-100; matches score default)
      %H  always 100 when showing percent (pair with %h)
      %e  energy
      %s  stamina current
      %S  stamina max
      %f  fuel (supernatural) or ``-``
      %n  character name
      %r  room key
      %%  literal %
    Color tags (``<dark_red>``, ``<teal>``, …) are allowed and expanded
    via ``style.render`` after tokens. Empty / disabled prompt returns "".

    Hard rule: this module lives under ``engine/`` -- never import ``supers``.
    Caps come from ``hooks.gmcp_char_vitals`` when SUPERS is installed.
    """
    ensure_display_defaults(character)
    template = character.prompt_format
    if template is None or template == "":
        return ""
    hp = 100
    max_hp = 100
    energy = getattr(character, "energy", 0)
    stamina = int(getattr(character, "stamina", 0) or 0)
    max_stamina = stamina
    fuel_str = "-"
    # Reuse GMCP vitals builder (SUPERS) via hooks -- no supers import here.
    try:
        from engine import hooks
        vitals = hooks.gmcp_char_vitals(character) or {}
        if vitals:
            # Always percent-of-max for %h/%H (never raw Tier pools).
            raw_hp = vitals.get("hp_raw", vitals.get("hp"))
            raw_max = vitals.get("maxhp_raw", vitals.get("maxhp"))
            try:
                cur = float(raw_hp)
                cap = float(raw_max) if raw_max not in (None, "", "0") else 0.0
                if cap > 0:
                    # Percent gauges use maxhp=100; raw pools use huge max.
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
                fuel_str = str(vitals["fuel"])
    except Exception:
        pass
    if fuel_str == "-":
        fuel_val = getattr(character, "fuel", None)
        if fuel_val is not None:
            try:
                fuel_str = f"{float(fuel_val):.0f}"
            except (TypeError, ValueError):
                fuel_str = "-"
    room = getattr(character, "location", None)
    room_key = room.key if room is not None else "-"
    name = getattr(character, "key", "?")

    out = []
    i = 0
    while i < len(template):
        ch = template[i]
        if ch == "%" and i + 1 < len(template):
            code = template[i + 1]
            if code == "%":
                out.append("%")
            elif code == "h":
                out.append(str(hp))
            elif code == "H":
                out.append(str(max_hp))
            elif code == "e":
                out.append(str(energy))
            elif code == "s":
                out.append(str(stamina))
            elif code == "S":
                out.append(str(max_stamina))
            elif code == "f":
                out.append(fuel_str)
            elif code == "n":
                out.append(name)
            elif code == "r":
                out.append(room_key)
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
    """Paint a combat line for one viewer (prefs #19). Role is combat_*."""
    from engine import style
    return style.paint_for(character, role, text)


def channel_role(character, channel, default="muted"):
    """Style role for a chat channel (prefs #26), with safe fallback."""
    ensure_display_defaults(character)
    custom = (character.channel_colors or {}).get(channel)
    if custom:
        from engine import style
        if custom in style.COLORS or custom in style.COLORS_XTERM256:
            return custom
    return default
