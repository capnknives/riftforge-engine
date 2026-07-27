"""
style.py -- gothic ANSI color + old-MUD layout helpers
(suggestions.log #51 / #55; docs/plans/colorandformattingforgame.R).

Preference catalog (phases, gaps, cite prefs #N in PRs):
  docs/plans/mud_formatting_preferences.md

Agent coloring guide (sighted sweeps, role table, layout families):
  docs/plans/sighted_color_guide.md
  Always-apply: .cursor/rules/sighted-color.mdc
  Sibling a11y: .cursor/rules/screenreader-a11y.mdc

Pure presentation. Game logic must never depend on color for meaning
(docs/SYSTEMS_DESIGN.md section 8): every painted string still carries a
plain-text label. Session.send strips ANSI when Character.use_color is off.

Palette (#51 + the plan's named tags):
  - Readable on black -- no dark navy. ``midnight_blue`` / ``pale_blue`` in
    templates remap to soft cyan / dark grey so a11y stays intact.
  - No neon spam; soft amber, crimson, silver, muted green, teal.
  - ``render("<tag>text <other>more")`` switches color at each <tag>.
  - ``paint_layered(role, template)`` / ``paint_layered_for(...)``: like
    paint(), but a ``<tag>`` inside `template` switches color for that
    span only -- an unrecognized tag (including the sentinel ``_base``)
    reverts to `role`'s own color rather than going blank. Lets combat's
    direction-role base coexist with rare gothic accents inside one line
    (docs/plans/combat_color_gothic.md).
  - Semantic combat/chat roles (prefs #10 / #19 / #23): ``combat_out``,
    ``combat_in``, ``combat_other``, ``combat_mitigate``, ``ooc``, ``alert``.
    Combat direction VALUES are gothic parchment/blood/ash/steel (retuned
    2026-07-19 -- the old bright-cyan/teal pair read as sci-fi HUD).

Layout families from the plan (docs/plans/colorandformattingforgame.R):
  - Master Room        -- framed sheets (score / who / help), not look
  - Wrought Iron & Ash -- who list (x-x-x rules, badge columns)
  - Blood & Velvet     -- help tomes / score / shop sheets (==== rules)
  - Abyss menu         -- numbered option menus (chargen, settings)
  - Dialogue frame     -- NPC prompt boxes (reusable; optional callers)
  - Connect splash     -- gothic login banner (wrought rules + paint roles)
"""

import re

# CSI SGR reset -- end every painted span so color never leaks.
RESET = "\x1b[0m"

# Named tags from colorandformattingforgame.R (+ semantic role aliases).
# Values are soft 16-color SGR codes (stdlib only). Prefs #5 / #10 / #19.
COLORS = {
    # Plan tags ----------------------------------------------------------
    "dark_grey": "\x1b[90m",
    "slate_grey": "\x1b[90m",
    "silver": "\x1b[37m",
    "white": "\x1b[97m",
    "bright_white": "\x1b[97m",
    "light_grey": "\x1b[37m",
    "dark_red": "\x1b[31m",
    "gold": "\x1b[33m",
    "dark_purple": "\x1b[35m",
    # sighted_color_guide.md known-gap #1: BADGE_COLORS["witch"] names
    # "violet" but the role was never defined, so Witch badges silently
    # rendered uncolored. Alias into the purple family (16-color magenta;
    # a brighter orchid in 256 below) rather than editing the badge map --
    # "violet" is a perfectly good gothic name other content may reuse.
    "violet": "\x1b[35m",
    "teal": "\x1b[36m",
    "dark_cyan": "\x1b[36m",
    "absinthe_green": "\x1b[32m",
    # Remapped: plan asked for deep blues; #51 forbids dark navy on black.
    "midnight_blue": "\x1b[90m",
    "pale_blue": "\x1b[36m",
    # Semantic roles (first-pass API -- still valid) ---------------------
    "title": "\x1b[33m",
    "exit": "\x1b[37m",
    "header": "\x1b[31m",
    "ok": "\x1b[32m",
    "warn": "\x1b[33m",
    "error": "\x1b[31m",
    "muted": "\x1b[90m",
    "accent": "\x1b[35m",
    # Combat / chat direction roles (prefs #19 / #23 / #29) --------------
    # Gothic + keyword-pop + structural accents (2026-07-19 / 21 / 21):
    # direction roles stay soft tints so every-swing accents (verbs, tags,
    # reaction outcomes) can read. 16-color: out/other silver, in danger-red,
    # mitigate ash -- already distinct at this coarse depth.
    "combat_out": "\x1b[37m",
    "combat_in": "\x1b[31m",
    "combat_other": "\x1b[37m",
    "combat_mitigate": "\x1b[90m",
    "ooc": "\x1b[90m",
    "alert": "\x1b[93m",
    "prose": "\x1b[37m",
    "item": "\x1b[36m",
    "hostile": "\x1b[31m",
}

# Prefs #6: Xterm256 soft gothic counterparts (same keys). Used when the
# player sets ``config color 256``. Graceful degrade: paint_for falls back
# to COLORS when a key is missing here.
COLORS_XTERM256 = {
    "dark_grey": "\x1b[38;5;240m",
    "slate_grey": "\x1b[38;5;242m",
    "silver": "\x1b[38;5;252m",
    "white": "\x1b[38;5;255m",
    "bright_white": "\x1b[38;5;255m",
    "light_grey": "\x1b[38;5;250m",
    "dark_red": "\x1b[38;5;88m",
    "gold": "\x1b[38;5;178m",
    "dark_purple": "\x1b[38;5;97m",
    # Witch-badge violet (see COLORS note): soft orchid, distinct from
    # dark_purple at 256 depth so Witch and Occultist badges don't merge.
    "violet": "\x1b[38;5;134m",
    "teal": "\x1b[38;5;73m",
    "dark_cyan": "\x1b[38;5;66m",
    "absinthe_green": "\x1b[38;5;107m",
    "midnight_blue": "\x1b[38;5;240m",
    "pale_blue": "\x1b[38;5;110m",
    "title": "\x1b[38;5;178m",
    "exit": "\x1b[38;5;187m",
    "header": "\x1b[38;5;88m",
    "ok": "\x1b[38;5;107m",
    "warn": "\x1b[38;5;178m",
    "error": "\x1b[38;5;167m",
    "muted": "\x1b[38;5;240m",
    "accent": "\x1b[38;5;97m",
    # Structural-accents 256 pairing (combat_color_structural_accents.md):
    # Outgoing body = prose grey (250) so bright_white verbs actually pop
    # (parchment 187 was white-on-white after keyword-pop); incoming soft
    # rose; spectators same prose grey; mitigate cooler slate (242) so
    # miss/dodge lines are not mush next to a landed swing.
    "combat_out": "\x1b[38;5;250m",
    "combat_in": "\x1b[38;5;174m",
    "combat_other": "\x1b[38;5;250m",
    "combat_mitigate": "\x1b[38;5;242m",
    "ooc": "\x1b[38;5;245m",
    "alert": "\x1b[38;5;220m",
    "prose": "\x1b[38;5;250m",
    "item": "\x1b[38;5;73m",
    "hostile": "\x1b[38;5;167m",
}

# Backward-compat alias used by paint().
_ROLES = COLORS

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# <tag> color switches inside render() templates.
_TAG_RE = re.compile(r"<([a-z_][a-z0-9_]*)>")

# Default wrap budget -- stay under typical telnet widths.
DEFAULT_WIDTH = 67
WHO_WIDTH = 67
TOME_WIDTH = 67
ROOM_WIDTH = 67


def code_for(role, depth="ansi"):
    """SGR code for `role` at `depth` ('ansi' or 'xterm256').

    Prefs #5 / #6: xterm256 falls back to 16-color when a key is absent.
    """
    if depth in ("256", "xterm", "xterm256"):
        return COLORS_XTERM256.get(role) or COLORS.get(role)
    return COLORS.get(role)


def paint(role, text, depth="ansi"):
    """Wrap `text` in the ANSI codes for a named color / role, then reset.

    Unknown roles pass text through unchanged. Empty text stays empty.
    ``depth`` selects 16-color vs Xterm256 (prefs #6).
    """
    if not text:
        return text
    code = code_for(role, depth)
    if code is None:
        return text
    return f"{code}{text}{RESET}"


def paint_for(character, role, text, depth=None):
    """paint() using the player's color_depth preference when depth omitted."""
    if depth is None:
        try:
            from engine import display_prefs
            depth = display_prefs.color_depth(character)
        except Exception:
            depth = "ansi"
    return paint(role, text, depth=depth)


def strip_ansi(text):
    """Remove every ANSI escape sequence from `text`."""
    if not text or "\x1b" not in text:
        return text
    return _ANSI_RE.sub("", text)


def visible_len(text):
    """Printable width of `text` (ANSI escapes count as zero)."""
    return len(strip_ansi(text))


def pad(text, width, align="left"):
    """Pad `text` to `width` visible columns without breaking ANSI spans.

    Truncates the *plain* content with '...' when too long, then re-paints
    nothing -- callers should pass already-painted short strings, or plain
    text. align: 'left' | 'right' | 'center'.
    """
    width = max(1, int(width))
    plain = strip_ansi(str(text))
    if len(plain) > width:
        plain = plain[: max(1, width - 3)] + "..."
        text = plain  # truncation drops color; safer than mid-escape cuts
    gap = width - len(strip_ansi(text))
    if gap <= 0:
        return text
    if align == "right":
        return (" " * gap) + text
    if align == "center":
        left = gap // 2
        return (" " * left) + text + (" " * (gap - left))
    return text + (" " * gap)


def render(template):
    """Expand a plan-style template with ``<tag>`` color switches.

    Each ``<tag>`` sets the color for following text until the next tag.
    Unknown tags are ignored (text stays uncolored from that point until
    a known tag). Example::

        render("<dark_grey>[ <gold>Hunter <dark_grey>] <white>Name")
    """
    if not template:
        return template
    if "<" not in template:
        return template
    parts = []
    pos = 0
    code = ""
    for match in _TAG_RE.finditer(template):
        if match.start() > pos:
            chunk = template[pos:match.start()]
            parts.append(f"{code}{chunk}{RESET}" if code else chunk)
        code = COLORS.get(match.group(1), "")
        pos = match.end()
    if pos < len(template):
        chunk = template[pos:]
        parts.append(f"{code}{chunk}{RESET}" if code else chunk)
    return "".join(parts)


def paint_layered(role, template, depth="ansi"):
    """Like ``paint(role, ...)``, but a ``<tag>`` inside `template` switches
    color for that span only -- text reverts to `role`'s own color at the
    next tag (or the sentinel ``<_base>``), never to blank.

    Built for combat lines (docs/plans/combat_color_gothic.md): the
    direction-role base (parchment/blood/ash/steel) carries the whole line,
    while a rare high-signal span (a silver blade, a rider callout) gets a
    named accent without losing the base color for everything after it.
    An UNKNOWN tag -- including ``_base``, which names no real color -- also
    reverts to `role`, so callers never need to remember which accent was
    open; they just emit ``<_base>`` to close one.

    A template with no ``<tag>`` at all behaves byte-identically to
    ``paint(role, template, depth)`` -- existing callers that never emit
    tags see no behavior change.
    """
    if not template:
        return template
    palette = COLORS_XTERM256 if depth in ("256", "xterm", "xterm256") else COLORS
    base_code = palette.get(role) or COLORS.get(role) or ""
    if "<" not in template:
        return f"{base_code}{template}{RESET}" if base_code else template
    parts = []
    pos = 0
    code = base_code
    for match in _TAG_RE.finditer(template):
        if match.start() > pos:
            chunk = template[pos:match.start()]
            parts.append(f"{code}{chunk}{RESET}" if code else chunk)
        tag = match.group(1)
        found = palette.get(tag) or COLORS.get(tag)
        code = found if found else base_code
        pos = match.end()
    if pos < len(template):
        chunk = template[pos:]
        parts.append(f"{code}{chunk}{RESET}" if code else chunk)
    return "".join(parts)


def paint_layered_for(character, role, template, depth=None):
    """paint_layered() using the player's color_depth preference when omitted."""
    if depth is None:
        try:
            from engine import display_prefs
            depth = display_prefs.color_depth(character)
        except Exception:
            depth = "ansi"
    return paint_layered(role, template, depth=depth)


# --- Rules -----------------------------------------------------------------

def hrule(width=DEFAULT_WIDTH, char="-"):
    """Classic MUD rule (---------------)."""
    return char * max(1, int(width))


def rule_tilde(width=DEFAULT_WIDTH):
    """Soft secondary rule (~~~~~~~~~~~~~~~)."""
    return hrule(width=width, char="~")


def rule_arrow(width=DEFAULT_WIDTH):
    """Arrowed rule (<--------->)."""
    inner = max(1, int(width) - 2)
    return "<" + ("-" * inner) + ">"


def rule_equals(width=DEFAULT_WIDTH):
    """Blood & Velvet heavy rule (=======)."""
    return "=" * max(1, int(width))


def wrought_rule(width=WHO_WIDTH):
    """Wrought Iron & Ash rule: ``x-x-x-x-...`` (plan who-list borders)."""
    width = max(3, int(width))
    # Build "x-x-x-..." then trim/pad to exact width.
    unit = "x-"
    raw = (unit * ((width // 2) + 1))[:width]
    if width % 2 == 1 and not raw.endswith("x"):
        raw = raw[:-1] + "x"
    return raw


# --- Boxes (kept for callers / smoke) --------------------------------------

def boxed_title(title, width=DEFAULT_WIDTH):
    """Simple ``+--+`` title box (first-pass helper; tomes prefer equals)."""
    width = max(8, int(width))
    inner = width - 2
    top = "+" + ("-" * inner) + "+"
    cleaned = strip_ansi(str(title)).strip()
    if len(cleaned) > inner - 2:
        cleaned = cleaned[: max(1, inner - 5)] + "..."
    body = pad(" " + cleaned, inner)
    return [top, "|" + body + "|", top]


def box(lines, width=None):
    """Wrap content lines in a simple ASCII box."""
    content = [str(line) for line in lines]
    if width is None:
        longest = max((visible_len(line) for line in content), default=0)
        width = min(DEFAULT_WIDTH, max(8, longest + 4))
    width = max(8, int(width))
    inner = width - 2
    top = "+" + ("-" * inner) + "+"
    out = [top]
    for line in content:
        plain = strip_ansi(line)
        if len(plain) > inner - 2:
            plain = plain[: max(1, inner - 5)] + "..."
            out.append("|" + pad(" " + plain, inner) + "|")
        else:
            # Prefer keeping painted content when it already fits.
            if visible_len(line) <= inner - 1:
                out.append("|" + pad(" " + line, inner) + "|")
            else:
                out.append("|" + pad(" " + plain, inner) + "|")
    out.append(top)
    return out


# --- Connect splash (login) ------------------------------------------------
# Gothic wrought chrome via paint() (16-color default -- no Character yet,
# so paint_for / config color 16|256 cannot apply). Labels carry meaning;
# color is decoration. No SUPERS package name on the wire -- listings already
# expose CODEBASE=Riftforge via MSSP.

LOGIN_GAME_TITLE = "Mortals and Monsters"
LOGIN_CREATOR = "CapnKnives"
LOGIN_ENGINE = "Riftforge"
LOGIN_STATUS = "Pre-alpha"
# Spaced title kept for the sighted who-list banner (brand continuity).
# Login splash uses LOGIN_GAME_TITLE (unspaced) for TTS -- see format_login_banner.
LOGIN_SPACED_TITLE = "M O R T A L S   &   M O N S T E R S"


def format_login_banner(width=WHO_WIDTH):
    """Build the connect splash: title, setting blurb, creator + engine.

    Returns painted lines (wrought rules / silver title / muted blurb /
    gold creator / muted engine / soft-crimson status). Callers send each
    line before the name prompt. Width matches the who-list chrome.

    Pre-login has no ``config screenreader`` yet, so the title stays
    **unspaced** (``Mortals and Monsters``, not letter-spaced) and credits
    use ``Label: value.`` lines -- TTS-friendly without a Character pref.
    Color is decoration only; wrought rules are sighted chrome.
    """
    w = max(40, int(width))
    rule = paint("dark_grey", wrought_rule(w))
    # Unspaced brand -- spaced caps force letter-by-letter TTS.
    title = paint("silver", pad(LOGIN_GAME_TITLE, w, "center"))
    # Canon frame (docs/LORE.md): Earth towns under Heaven / Hell /
    # Purgatory; hunters and vessel Celestials -- not Wastes-as-mind.
    blurb = (
        "Hunters work the cases. Monsters wear human faces.",
        "Earth towns sit thin under Heaven, Hell, and older prisons.",
    )
    lines = ["", rule, title, rule, ""]
    for line in blurb:
        lines.append(paint("muted", pad(line, w, "center")))
    lines.append("")
    # Label: value. so screenreaders pause cleanly (a11y rule 7).
    lines.append(
        paint("gold", pad(f"Created by: {LOGIN_CREATOR}.", w, "center"))
    )
    lines.append(
        paint(
            "muted",
            pad(f"Engine: {LOGIN_ENGINE}.", w, "center"),
        )
    )
    lines.append(
        paint("dark_red", pad(f"Status: {LOGIN_STATUS}.", w, "center"))
    )
    lines.extend(["", rule, ""])
    return lines


# --- Wrought Iron & Ash: who list ------------------------------------------

def moral_tide_caption(balance, lean=""):
    """Plain World Tide lean line with signed balance (never color alone).

    Shared by the sighted meter caption, screenreader meter flatten, and
    the who-list SR Tide footer so wording stays one source of truth.
    """
    bal = max(-100, min(100, int(balance)))
    if lean:
        return f"{lean} ({bal:+d})"
    if bal == 0:
        return f"The town hangs in balance ({bal:+d})"
    if bal > 0:
        return f"The town leans toward the light ({bal:+d})"
    return f"The town leans toward darkness ({bal:+d})"


def format_moral_meter(balance, *, lean="", eclipse=False, width=WHO_WIDTH,
                       screenreader=False):
    """Aesthetic Good/Evil world-tide bar (who-list footer / world sheets).

    Scale is -100..+100 (positive = good). Fill grows from the center `|`
    toward EVIL (left) or GOOD (right) -- empty track stays at the outer
    edges. Labels carry meaning -- EVIL / GOOD ends, lean phrase, and
    signed number -- so color-off clients still read the meter (section 8
    a11y). Color is decoration only.

    ``screenreader=True`` drops the ASCII bar and spaced ``W O R L D``
    title; emits semantic Tide lines for TTS (same policy as needs meters).
    """
    w = max(40, int(width))
    bal = max(-100, min(100, int(balance)))
    caption = moral_tide_caption(bal, lean)

    if screenreader:
        # No [#-|] glyphs / letter-spaced banner -- phrase + signed balance.
        # _tts_period lives below in this module; fine at call time.
        out = [
            _tts_period("World Tide"),
            _tts_period(f"Balance: {bal:+d}"),
            _tts_period(caption),
        ]
        if eclipse:
            out.append(_tts_period("Sky: unnatural eclipse"))
        return out

    # Odd inner width so the center `|` sits cleanly between halves.
    inner = 25
    half = inner // 2  # 12 cells each side of the pivot
    # How much of each half is "filled" from the pivot toward that side.
    evil_fill = int(round((-min(0, bal) / 100.0) * half))
    good_fill = int(round((max(0, bal) / 100.0) * half))
    evil_fill = max(0, min(half, evil_fill))
    good_fill = max(0, min(half, good_fill))
    # Center-outward: empty at the labeled edge, `#` abuts the pivot.
    left = ("-" * (half - evil_fill)) + ("#" * evil_fill)
    right = ("#" * good_fill) + ("-" * (half - good_fill))
    # Paint filled cells by side; empty track stays dark grey.
    left_painted = (
        paint("dark_grey", left[: half - evil_fill])
        + paint("dark_red", left[half - evil_fill :])
        if evil_fill
        else paint("dark_grey", left)
    )
    right_painted = (
        paint("gold", right[:good_fill]) + paint("dark_grey", right[good_fill:])
        if good_fill
        else paint("dark_grey", right)
    )
    bar = (
        paint("dark_grey", "[")
        + left_painted
        + paint("silver", "|")
        + right_painted
        + paint("dark_grey", "]")
    )
    # "EVIL  [bar]  GOOD" -- pad to width for a centered wrought look.
    left_label = paint("dark_red", "EVIL")
    right_label = paint("gold", "GOOD")
    meter = left_label + " " + bar + " " + right_label
    pad_left = max(0, (w - visible_len(meter)) // 2)
    meter_line = (" " * pad_left) + meter

    cap_line = paint("muted", pad(caption, w, "center"))

    out = [
        paint("dark_purple", pad("W O R L D   T I D E", w, "center")),
        meter_line,
        cap_line,
    ]
    if eclipse:
        out.append(
            paint("dark_purple", pad("Sky: unnatural eclipse", w, "center"))
        )
    return out


# Pressure bands for need meters -- mirror supers.needs SEEK_THRESHOLD /
# CRITICAL_THRESHOLD. Kept here (not imported) so engine/style stays free
# of SUPERS. If those lifestyle thresholds move, update these too.
NEED_METER_SEEK_BAND = 0.60
NEED_METER_CRITICAL_BAND = 0.95


def need_meter_fill_role(level, *, critical=False):
    """Gothic vitals fill role for a 0→1 need / fuel-pressure bar.

    Bands match Cadence seek / critical so the tint tracks the same
    pressure the phrases already name (a11y: color is decoration only)::

        calm   (< seek)      absinthe_green  -- soft "ok" vitals
        seek   (>= seek)     gold            -- rising warn
        crit   (>= critical) dark_red        -- dried-blood crisis

    ``critical=True`` forces the crisis role even when ``level`` is a
    derived pressure (fuel row) that already flipped the bang glyphs.
    """
    if critical or float(level) >= NEED_METER_CRITICAL_BAND:
        return "dark_red"
    if float(level) >= NEED_METER_SEEK_BAND:
        return "gold"
    return "absinthe_green"


def format_need_meter(level, *, critical=False, width=16):
    """Unipolar 0→1 need bar (left fill), gothic vitals / Tide glyphs.

    Fill grows left→right as the need rises (0 = empty track, 1 = full).
    Critical meters use `=` fill and a trailing `!` inside the brackets so
    color-off clients still read severity (section 8 a11y). Color is
    decoration only -- callers should pair this with a plain-language phrase.

    Sighted paint (prompt / World Tide kinship): dark_grey chrome brackets,
    absinthe→gold→crimson fill by pressure band (see
    ``need_meter_fill_role``), empty track stays dark_grey ash.

    Returns the painted bar string only, e.g. `[########--------]` or
    `[============---!]`.
    """
    inner = max(8, int(width))
    # Leave one cell for the critical bang when needed.
    track = inner - 1 if critical else inner
    lvl = max(0.0, min(1.0, float(level)))
    filled = int(round(lvl * track))
    filled = max(0, min(track, filled))
    empty = track - filled
    fill_ch = "=" if critical else "#"
    body = (fill_ch * filled) + ("-" * empty)
    if critical:
        body = body + "!"
    # Gothic vitals gradient (not flat silver) -- still paired with phrases.
    fill_role = need_meter_fill_role(lvl, critical=critical)
    # Fill first; leftover track (dashes and optional bang) stays ash chrome.
    # The bang sits in the empty slice when critical so it stays readable
    # even if fill_role and alert gold would fight -- dark_grey `!` next to
    # crimson `=` is enough; the plain `!!` phrase carries severity.
    painted_body = (
        paint(fill_role, body[:filled])
        + paint("dark_grey", body[filled:])
    )
    return paint("dark_grey", "[") + painted_body + paint("dark_grey", "]")


def _format_who_entry_row(entry, width):
    """One `[ Badge ] Name .... status` line for format_who (shared)."""
    w = max(40, int(width))
    badge = str(entry.get("badge") or "Mortal")[:8]
    bcolor = entry.get("badge_color") or "silver"
    name = str(entry.get("name") or "?")
    status = str(entry.get("status") or "")
    # [ Badge  ] Name ............ status
    badge_cell = pad(badge, 8)
    left = render(
        f"<dark_grey>[ <{bcolor}>{badge_cell} <dark_grey>] "
        f"<white>{name}"
    )
    # Dot leaders between name and status (visible width aware).
    status_plain = status
    name_width = visible_len(left)
    dots_budget = w - name_width - 1 - len(status_plain)
    if dots_budget < 3:
        # Shrink status rather than blow the wrap budget.
        keep = max(0, w - name_width - 4)
        status_plain = (status_plain[:keep] + "..") if keep else ""
        dots_budget = max(3, w - name_width - 1 - len(status_plain))
    dots = paint("dark_grey", " " + ("." * dots_budget) + " ")
    return left + dots + paint("light_grey", status_plain)


def format_who(entries, *, souls=0, time_label="", width=WHO_WIDTH,
               moral_balance=None, lean="", eclipse=False,
               echo_entries=None, gm_names=None, unknown_count=0,
               screenreader=False):
    """Build the plan's Mortals & Monsters who list.

    `entries` is an iterable of dicts::

        {"badge": "Vampire", "badge_color": "dark_red",
         "name": "Alaric", "status": "Brooding in the Crypt"}

    Badge color is a COLORS key; name/status stay plain labels (a11y).

    When `moral_balance` is an int (-100..+100), a World Tide meter is
    rendered under the souls/time footer (Evil Strikes Back).

    Optional `echo_entries` (same dict shape) adds a second ECHOES section
    when the viewer's `whofull` toggle is on -- logout / idle Echoes still
    walking the map (see cmd_whofull / cmd_who).

    Optional `gm_names` (list of character keys) adds a GM section at the
    top listing online, non-immersion-cast staff as `[GM] Name`. The count
    matches that list (online only; cast members with gm_rank are omitted).

    ``unknown_count`` is the player-facing hole: veiled + unintroduced
    souls that would otherwise appear on this viewer's list.

    ``screenreader=True`` (prefs #30 / #32) flattens wrought rules into
    semantic headers and vertical lists.
    """
    w = max(40, int(width))
    unknown = max(0, int(unknown_count or 0))
    if screenreader:
        lines = ["", "Who list.", ""]
        gm_list = list(gm_names) if gm_names else []
        if gm_list:
            lines.append("Staff:")
            for name in gm_list:
                lines.append(f"  GM: {name}.")
            lines.append("")
        lines.append("Mortals and Monsters:")
        if not entries:
            lines.append("  None online.")
        else:
            for entry in entries:
                badge = entry.get("badge") or "Mortal"
                name = entry.get("name") or "?"
                status = entry.get("status") or ""
                lines.append(f"  {badge}: {name}. {status}".rstrip() + ".")
        if echo_entries is not None:
            lines.append("")
            lines.append("Echoes:")
            if not echo_entries:
                lines.append("  No Echoes in the world.")
            else:
                for entry in echo_entries:
                    badge = entry.get("badge") or "Echo"
                    name = entry.get("name") or "?"
                    status = entry.get("status") or ""
                    lines.append(f"  {badge}: {name}. {status}".rstrip() + ".")
        lines.append("")
        lines.append(f"Visible souls: {souls}. Time: {time_label or 'unknown'}.")
        lines.append(f"Unknown count: {unknown}.")
        if moral_balance is not None:
            bal = int(moral_balance)
            # Same Tide prose as format_moral_meter(screenreader=True).
            lines.append(_tts_period("World Tide"))
            lines.append(_tts_period(f"Balance: {bal:+d}"))
            lines.append(_tts_period(moral_tide_caption(bal, lean)))
            if eclipse:
                lines.append(_tts_period("Sky: unnatural eclipse"))
        return lines

    rule = paint("dark_grey", wrought_rule(w))
    lines = []
    # Staff first -- online real GMs only (immersion cast filtered by caller).
    gm_list = list(gm_names) if gm_names else []
    if gm_list:
        lines.append(rule)
        gm_title = paint("silver", pad("G M", w, "center"))
        lines.append(gm_title)
        lines.append(rule)
        lines.append("")
        count = len(gm_list)
        count_label = "1 online" if count == 1 else f"{count} online"
        lines.append(paint("muted", f"  ({count_label})"))
        for name in gm_list:
            # Plain `[GM] Name` -- rank detail stays on gmlist; a11y without
            # color alone (brackets + letters carry the meaning).
            lines.append(render(f"  <dark_grey>[<gold>GM<dark_grey>] <white>{name}"))
        lines.append("")
    title = paint("silver", pad("M O R T A L S   &   M O N S T E R S", w, "center"))
    lines.extend([rule, title, rule, ""])
    if not entries:
        lines.append(paint("muted", "  (none online)"))
    else:
        for entry in entries:
            lines.append(_format_who_entry_row(entry, w))
    # Optional Echoes block (whofull toggle) -- same column layout, own banner.
    if echo_entries is not None:
        lines.append("")
        lines.append(rule)
        echo_title = paint(
            "silver", pad("E C H O E S", w, "center")
        )
        lines.append(echo_title)
        lines.append(rule)
        lines.append("")
        if not echo_entries:
            lines.append(paint("muted", "  (no Echoes in the world)"))
        else:
            for entry in echo_entries:
                lines.append(_format_who_entry_row(entry, w))
    lines.append("")
    lines.append(rule)
    souls_bit = render(
        f"<dark_purple> Visible Souls: <silver>{souls}"
    )
    time_bit = render(f"<dark_purple> Time: <silver>{time_label or '--'}")
    # Two-column footer; pad middle with spaces.
    gap = max(2, w - visible_len(souls_bit) - visible_len(time_bit))
    lines.append(souls_bit + (" " * gap) + time_bit)
    unknown_bit = render(
        f"<dark_purple> Unknown Count: <silver>{unknown}"
    )
    lines.append(unknown_bit)
    # World Good/Evil meter sits under souls/time, still inside wrought rules.
    if moral_balance is not None:
        lines.append(rule)
        lines.extend(
            format_moral_meter(
                moral_balance, lean=lean, eclipse=eclipse, width=w,
            )
        )
    lines.append(rule)
    # Trailing blank so the wrought footer does not glue onto the next
    # prompt / command output.
    lines.append("")
    return lines


# --- Blood & Velvet: help tomes / sheets -----------------------------------

def _tts_period(text):
    """Ensure a plain line ends with sentence punctuation for TTS pauses."""
    text = str(text).rstrip()
    if not text:
        return text
    if text[-1] not in ".!?:":
        return text + "."
    return text


def format_tome(title, body_lines, *, related=None, syntax=None,
                width=TOME_WIDTH, screenreader=False):
    """Blood & Velvet help / sheet frame (plan section 2).

    `body_lines` is an iterable of plain or already-painted lines.
    Optional `syntax` (string) and `related` (string or list) get labeled
    sections under the header / above the footer.

    ``screenreader=True`` drops equals borders and hard-wrap; emits
    ``Title.`` then body lines with TTS-friendly periods (prefs #30–#32).
    """
    if screenreader:
        lines = ["", _tts_period(f"Help: {title}"), ""]
        if syntax:
            lines.append(_tts_period(f"Syntax: {syntax}"))
            lines.append("")
        for raw in body_lines:
            text = strip_ansi(str(raw)).strip()
            if not text:
                lines.append("")
                continue
            lines.append(_tts_period(text))
        if related:
            if isinstance(related, (list, tuple)):
                related = ", ".join(related)
            lines.append("")
            lines.append(_tts_period(f"Related: {related}"))
        lines.append("")
        return lines

    w = max(40, int(width))
    heavy = paint("dark_red", rule_equals(w))
    light = paint("dark_red", hrule(w))
    lines = [
        heavy,
        render(f"<gold> TOME: <white>{title}"),
        heavy,
    ]
    if syntax:
        lines.append(render(f"<dark_grey> SYNTAX:  <silver>{syntax}"))
        lines.append(light)
    lines.append("")
    for raw in body_lines:
        text = str(raw)
        if not text.strip():
            lines.append("")
            continue
        # Soft-wrap long plain lines at w; keep short/painted lines intact.
        if "\x1b" in text or visible_len(text) <= w:
            lines.append(paint("light_grey", text) if "\x1b" not in text
                         else text)
        else:
            lines.extend(_wrap_plain(text, w, color="light_grey"))
    lines.append("")
    lines.append(light)
    if related:
        if isinstance(related, (list, tuple)):
            related = ", ".join(related)
        lines.append(render(f"<dark_grey> RELATED: <silver>{related}"))
    lines.append(heavy)
    return lines


def format_help_index(categories, *, width=TOME_WIDTH, screenreader=False):
    """Bare `help` grimoire index: category tomes with topic blurbs.

    `categories` is HELP_CATEGORIES shape: [(category, [(name, blurb), ...])].

    ``screenreader=True`` skips equals rules; vertical ``name -- blurb`` lists.
    """
    if screenreader:
        lines = [
            "",
            "Help Index.",
            "Type help followed by a name for a page. Type commands for verbs.",
            "",
        ]
        for category, topics in categories:
            lines.append(_tts_period(str(category)))
            for name, blurb in topics:
                lines.append(f"  {name} -- {blurb}.")
            lines.append("")
        return lines

    w = max(40, int(width))
    heavy = paint("dark_red", rule_equals(w))
    lines = [
        heavy,
        render("<gold> TOME: <white>Help Index"),
        heavy,
        paint("muted", " Type 'help <name>' for a page.  'commands' for verbs."),
        "",
    ]
    for category, topics in categories:
        lines.append(paint("dark_red", category))
        lines.append(paint("dark_grey", hrule(min(40, w))))
        for name, blurb in topics:
            lines.append(
                render(f"<silver>  {name} <dark_grey>-- <light_grey>{blurb}")
            )
        lines.append("")
    lines.append(heavy)
    return lines


def format_commands_list(entries, *, gm_entries=None, width=TOME_WIDTH,
                         screenreader=False):
    """Blood & Velvet frame for the player ``commands`` verb list.

    ``entries`` / ``gm_entries`` are already-sorted
    ``[(verb_label, help_text), ...]`` pairs (aliases already joined with
    ``/``). Verb labels pad into a shared column; long one-liners wrap under
    the blurb column so the sheet stays centered inside ``width`` (prefs #3 /
    #13) instead of looking chunky when a few help texts run long.

    ``screenreader=True`` (prefs #30 / #32) drops the equals borders and
    emits plain ``verb -- blurb`` lines for TTS.
    """
    w = max(40, int(width))
    # Collect every label so the verb column fits the widest alias group
    # without eating the whole sheet. Cap leaves room for a short blurb
    # column (movement's ``n/s/e/w/ne/nw/se/sw/u/d`` is 23 chars).
    all_entries = list(entries or [])
    if gm_entries:
        all_entries.extend(gm_entries)
    verb_col = 12
    for label, _blurb in all_entries:
        verb_col = max(verb_col, len(str(label)))
    verb_col = min(24, verb_col)

    # ``  `` + padded verb + `` -- `` = indent where wrapped blurbs start.
    hang = 2 + verb_col + 4
    # Inner budget for the first blurb segment on a line.
    blurb_w = max(12, w - hang)

    def _soft_tokens(text):
        """Split on spaces, then on ``/``, then hard-chunk leftovers.

        A lone ``north/south/northeast/...`` token is longer than the blurb
        column; breaking after each ``/`` keeps the sheet inside ``width``.
        Truly unbreakable words (no spaces/slashes) get hard-chunked as a
        last resort so a single token can never overrun the border.
        """
        tokens = []
        for word in text.split():
            pieces = [word]
            if "/" in word and len(word) > blurb_w:
                parts = word.split("/")
                pieces = []
                for i, part in enumerate(parts):
                    # Keep the slash on every segment but the last so the
                    # wrapped list still reads as one path (a/b/c).
                    pieces.append(part + ("/" if i < len(parts) - 1 else ""))
            for piece in pieces:
                if len(piece) <= blurb_w:
                    tokens.append(piece)
                    continue
                # Hard-chunk an unbreakable run (rare; keeps the border).
                for start in range(0, len(piece), blurb_w):
                    tokens.append(piece[start:start + blurb_w])
        return tokens

    def _entry_rows(label, blurb):
        """One verb row, plus hang-indented wrap lines for a long blurb."""
        label = str(label)
        blurb = str(blurb).strip()
        words = _soft_tokens(blurb) if blurb else []
        # Show the verb even when help_text is somehow empty.
        if not words:
            return [paint("silver", f"  {pad(label[:verb_col], verb_col)}")]

        # First line: padded verb + first blurb chunk that fits blurb_w.
        # Labels are sized into verb_col above; never ellipsize a verb name
        # (players type what they see).
        #
        # IMPORTANT: paint pieces separately -- do NOT feed help_text through
        # ``render("<tag>...")``. One-liners often contain angle brackets
        # (``rename <old> <new>``) that would be eaten as color tags.
        shown = label

        def _join(left, right):
            """Join wrap tokens; keep ``a/b`` tight, leave lone ``/`` spaced.

            Soft-split path segments end in ``/`` (``north/``); those glue
            to the next segment with no space. A bare ``/`` that was its
            own word in the help_text (``blood / mark``) must keep spaces.
            """
            if left.endswith("/") and left != "/":
                return left + right
            return left + " " + right

        first = words[0]
        i = 1
        while i < len(words):
            trial = _join(first, words[i])
            if len(trial) <= blurb_w:
                first = trial
                i += 1
            else:
                break
        rows = [
            paint("silver", f"  {pad(shown, verb_col)} ")
            + paint("dark_grey", "-- ")
            + paint("light_grey", first)
        ]
        # Continuation lines hang under the blurb column (spaces, not tabs).
        indent = " " * hang
        current = ""
        for word in words[i:]:
            if not current:
                current = word
                continue
            trial = _join(current, word)
            if len(trial) <= blurb_w:
                current = trial
            else:
                rows.append(paint("light_grey", indent + current))
                current = word
        if current:
            rows.append(paint("light_grey", indent + current))
        return rows

    if screenreader:
        # Flat list for TTS -- no equals rules, no color (Session strips
        # ANSI when color is off anyway; keep tags out so readers stay clean).
        lines = ["", "Commands.", ""]
        for label, blurb in entries or []:
            lines.append(f"  {label} -- {blurb}")
        if gm_entries:
            lines.append("")
            lines.append("GM COMMANDS:")
            for label, blurb in gm_entries:
                lines.append(f"  {label} -- {blurb}")
        lines.append("")
        lines.append("For system topics, type: help")
        lines.append("")
        return lines

    heavy = paint("dark_red", rule_equals(w))
    light = paint("dark_red", hrule(w))
    lines = [
        heavy,
        render("<gold> TOME: <white>Commands"),
        heavy,
        paint("muted", " Type 'help <name>' for topics.  One-liners below."),
        "",
    ]
    for label, blurb in entries or []:
        lines.extend(_entry_rows(label, blurb))
    if gm_entries:
        lines.append("")
        # Keep the exact "GM COMMANDS:" label -- smoke tests and players
        # already key off it (suggestions.log #40).
        lines.append(paint("dark_red", "GM COMMANDS:"))
        lines.append(paint("dark_grey", hrule(min(40, w))))
        for label, blurb in gm_entries:
            lines.extend(_entry_rows(label, blurb))
    lines.append("")
    lines.append(light)
    lines.append(paint(
        "muted",
        " For system topics (training, divine, death, ...), type: help",
    ))
    lines.append(heavy)
    return lines


def format_sheet(title, body_lines, *, width=48, screenreader=False):
    """Compact Blood & Velvet frame for score / shop / reports.

    Body lines that exceed ``width`` are word-wrapped (plain text) so they
    never run past the equals border on the sighted path.

    ``screenreader=True`` drops borders and hard-wrap; one semantic line
    per body row with TTS punctuation (prefs #30–#32).
    """
    if screenreader:
        lines = ["", _tts_period(str(title)), ""]
        for raw in body_lines:
            text = strip_ansi(str(raw)).strip()
            if not text:
                lines.append("")
                continue
            lines.append(_tts_period(text))
        lines.append("")
        return lines

    w = max(32, int(width))
    heavy = paint("dark_red", rule_equals(w))
    light = paint("dark_red", hrule(w))
    lines = [
        heavy,
        render(f"<gold> {title}"),
        heavy,
    ]
    # Inner budget: leave a little margin inside the border.
    inner = max(16, w - 1)
    for raw in body_lines:
        text = str(raw)
        plain = strip_ansi(text)
        if visible_len(text) <= inner:
            lines.append(text)
            continue
        # Wrap the plain content; keep a leading indent if the original
        # score line started with spaces.
        lead = len(plain) - len(plain.lstrip(" "))
        indent = plain[:lead]
        body = plain[lead:]
        wrap_w = max(8, inner - lead)
        words = body.split()
        if not words:
            lines.append(text)
            continue
        current = words[0]
        for word in words[1:]:
            trial = current + " " + word
            if len(trial) <= wrap_w:
                current = trial
            else:
                lines.append(indent + current)
                current = word
        lines.append(indent + current)
    lines.append(light)
    return lines


def _wrap_plain(text, width, color="light_grey"):
    """Word-wrap a plain string to `width`, painting each line.

    Leading spaces on the source line are kept on every wrapped row so
    indented help command tables (score / needs style) do not flush left
    when a long gloss soft-wraps inside ``format_tome``.

    Pipe-separated lists (Background menus, See-also glue) never leave a
    trailing ``|`` on a wrapped row -- the last item before the break is
    pulled onto the next line with the following word (so
    ``… hunter |`` / ``occultist`` becomes ``… procurer`` /
    ``hunter | occultist``).
    """
    # Help topics indent with spaces only; tabs are not used there.
    lead_len = len(text) - len(text.lstrip(" "))
    lead = text[:lead_len]
    words = text[lead_len:].split()
    if not words:
        return [paint(color, lead) if lead else ""]
    # Room left for words after the indent column.
    inner_w = max(8, int(width) - lead_len)
    rows = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        if len(trial) <= inner_w:
            current = trial
            continue
        # Overflow: avoid dumping a bare trailing pipe / em-dash glue.
        stripped = current.rstrip()
        if stripped.endswith("|"):
            # Split on pipe so the last real item moves onto the next row
            # with the new word (never leave "… procurer |" as a finished row).
            parts = [p for p in re.split(r"\s*\|\s*", stripped) if p]
            if len(parts) >= 2:
                left = " | ".join(parts[:-1])
                right = parts[-1]
                rows.append(paint(color, lead + left))
                current = f"{right} | {word}"
                continue
        if stripped.endswith("--"):
            without = stripped[:-2].rstrip()
            if without:
                rows.append(paint(color, lead + without))
                current = f"-- {word}"
                continue
        rows.append(paint(color, lead + current))
        current = word
    rows.append(paint(color, lead + current))
    return rows


# --- Abyss menu / dialogue -------------------------------------------------

def format_menu(title, options, *, prompt="What is your will?", width=67,
                screenreader=False):
    """Numbered menu (plan section 3). `options` is [(label, hint), ...].

    Hints are the grey parenthetical descriptions. Numbers are absinthe.

    ``screenreader=True`` skips the box and spaced-cap banner; numbered
    plain lines for TTS (prefs #30 / #32).
    """
    if screenreader:
        lines = ["", _tts_period(str(title)), ""]
        for i, (label, hint) in enumerate(options, start=1):
            if hint:
                lines.append(_tts_period(f"{i}. {label} ({hint})"))
            else:
                lines.append(_tts_period(f"{i}. {label}"))
        lines.append("")
        lines.append(
            _tts_period(f"{prompt} Enter a number from 1 to {len(options)}")
        )
        return lines

    w = max(40, int(width))
    border = paint("midnight_blue", "+" + ("=" * (w - 2)) + "+")
    mid = (
        paint("dark_cyan", "|")
        + pad(paint("absinthe_green", _space_title(title)), w - 2, "center")
        + paint("dark_cyan", "|")
    )
    lines = [border, mid, border, ""]
    for i, (label, hint) in enumerate(options, start=1):
        lines.append(render(
            f"<dark_grey>      (<absinthe_green>{i}<dark_grey>) "
            f"<white>{label}"
            + (f"       <dark_cyan>-> <dark_grey>({hint})" if hint else "")
        ))
    lines.append("")
    lines.append(paint("midnight_blue", "+" + ("-" * (w - 2)) + "+"))
    lines.append(render(
        f"<dark_cyan> > <silver>{prompt} <dark_grey>[1-{len(options)}]:"
    ))
    return lines


def format_dialogue(speaker_line, quote, choices, *, width=65,
                    screenreader=False):
    """NPC dialogue box (plan section 4). `choices` is [reply_str, ...].

    ``screenreader=True`` drops the ASCII box; speaker, quote, then
    numbered replies as plain lines. Empty ``choices`` is allowed (talk
    v1 has no menu replies) -- omit the ``Reply with a number.`` prompt.
    """
    if screenreader:
        lines = [
            "",
            _tts_period(str(speaker_line)),
            _tts_period(f'Quote: "{quote}"'),
            "",
        ]
        choice_list = list(choices) if choices else []
        for i, choice in enumerate(choice_list, start=1):
            lines.append(_tts_period(f'{i}. "{choice}"'))
        if choice_list:
            lines.append("")
            lines.append("Reply with a number.")
        return lines

    w = max(40, int(width))
    top = paint("slate_grey", "." + ("-" * (w - 2)) + ".")
    bot = paint("slate_grey", "'" + ("-" * (w - 2)) + "'")
    div = paint("slate_grey", "|" + ("-" * (w - 2)) + "|")
    lines = [
        top,
        paint("pale_blue", " | ") + paint("silver", speaker_line),
        div,
        paint("white", '  "' + quote + '"'),
        "",
    ]
    choice_list = list(choices) if choices else []
    for i, choice in enumerate(choice_list, start=1):
        lines.append(render(
            f"<slate_grey>  [ <pale_blue>{i} <slate_grey>] "
            f"<light_grey>\"{choice}\""
        ))
    lines.append(bot)
    if choice_list:
        lines.append(render("<pale_blue> > <dark_grey>Reply:"))
    return lines


def _space_title(title):
    """'AWAKENING' -> 'A W A K E N I N G' for menu banners."""
    cleaned = strip_ansi(str(title)).strip()
    if not cleaned:
        return ""
    # Already spaced? leave it.
    if "  " in cleaned or (len(cleaned) > 1 and cleaned[1] == " "):
        return cleaned
    return " ".join(cleaned.upper())


# --- Master Room Layout (plan section 1) -----------------------------------

def room_frame_rule(width=ROOM_WIDTH):
    """``O=====O`` outer frame used by the Master Room header/footer."""
    w = max(8, int(width))
    return "O" + ("=" * (w - 2)) + "O"


def spaced_dash_rule(width=ROOM_WIDTH):
    """``- - - -`` divider between description and Paths/Souls/Items."""
    w = max(3, int(width))
    # " -" repeated, then trim; keep leading spaces out -- callers indent.
    unit = "- "
    raw = (unit * ((w // 2) + 1)).rstrip()
    return raw[:w] if len(raw) > w else raw


def _section_header(label, width=ROOM_WIDTH):
    """``[ Paths ] ........................`` -- label is primary, dots mute."""
    # "  [ Paths ] " then dots to fill. Preserve the two-space indent.
    prefix_plain = f"  [ {label} ] "
    dots = max(3, int(width) - len(prefix_plain))
    return render(
        f"<dark_purple>  [ <silver>{label} <dark_purple>] "
        f"<dark_grey>{'.' * dots}"
    )


def _exit_columns(exits, width=ROOM_WIDTH, cols=2):
    """Format ``(direction, dest)`` pairs into balanced columns.

    Legacy helper kept for tests / callers that still want the old
    ``North: Dest`` column layout. Sighted ``format_room`` uses the
    compass + legend instead.
    """
    if not exits:
        return []
    # Cell width: indent (4) + two columns + gap between.
    # "    " + cell + "  " + cell
    indent = "    "
    gap = "  "
    usable = max(20, int(width) - len(indent))
    cell_w = (usable - len(gap) * (cols - 1)) // cols
    cell_w = max(12, cell_w)

    def _cell(direction, dest):
        # "North: Dest...." truncated to cell_w visible chars.
        d = str(direction).title()
        dest = str(dest)
        plain = f"{d}: {dest}"
        if len(plain) > cell_w:
            dest = dest[: max(1, cell_w - len(d) - 5)] + "..."
            plain = f"{d}: {dest}"
        painted = render(f"<white>{d}<silver>: <light_grey>{dest}")
        return pad(painted if visible_len(painted) <= cell_w else plain, cell_w)

    rows = []
    pair = list(exits)
    for i in range(0, len(pair), cols):
        chunk = pair[i:i + cols]
        cells = [_cell(d, dest) for d, dest in chunk]
        while len(cells) < cols:
            cells.append(" " * cell_w)
        rows.append(indent + gap.join(cells).rstrip())
    return rows


# Compass slot abbrevs for sighted Paths (labels carry meaning).
_COMPASS_CARDINALS = {
    "north": "N", "south": "S", "east": "E", "west": "W",
    "northeast": "NE", "northwest": "NW",
    "southeast": "SE", "southwest": "SW",
    "up": "U", "down": "D",
}
_COMPASS_LEGEND_ORDER = (
    "north", "northeast", "east", "southeast",
    "south", "southwest", "west", "northwest",
    "up", "down",
)


def _exit_dir_set(exits):
    """Lowercased direction -> destination title from exit pairs."""
    out = {}
    for direction, dest in exits:
        key = str(direction).strip().lower()
        if key:
            out[key] = str(dest)
    return out


def _center_visible(text, width, *, color=None):
    """Center a line in the room frame by visible (ANSI-stripped) width.

    ``text`` may already include ANSI. When ``color`` is set, ``text`` is
    treated as plain and painted after centering. Leading/trailing spaces
    on the content are stripped before padding so the glyph block sits
    mid-frame instead of hugging the left.
    """
    w = max(8, int(width))
    if color is not None:
        content = str(text).strip()
        vis = len(content)
        pad = max(0, (w - vis) // 2)
        return (" " * pad) + paint(color, content)
    raw = str(text).rstrip("\r\n")
    # Preserve internal ANSI; measure without escapes.
    plain = strip_ansi(raw).strip()
    # Re-find the colored body: strip leading/trailing plain spaces from
    # the raw string by trimming the same count from each end of plain.
    # Simpler path: if no ANSI, center the stripped plain; if ANSI, strip
    # only outer whitespace from the raw string then pad.
    body = raw.strip()
    vis = visible_len(body)
    pad = max(0, (w - vis) // 2)
    return (" " * pad) + body


def _compass_lines(exits, width=ROOM_WIDTH):
    """Compact centered exit compass + Also: line for non-compass exits.

    Three rows (north-up) instead of a tall five-row rose::

        NW N NE
         W @ E
        SW S SE

    Open directions show their letter abbrev; missing slots are spaces.
    U/D tuck onto the middle row when present. Direction letters are the
    primary signal (a11y -- not color alone). Centered in the room width.
    """
    by_dir = _exit_dir_set(exits)
    if not by_dir:
        return []

    def cell(name, size):
        """Fixed-width slot: abbrev centered, or blank spaces."""
        if name in by_dir:
            abbrev = _COMPASS_CARDINALS.get(name, name[:size].upper())
            return abbrev.center(size)
        return " " * size

    # Compact 3-row rose (much shorter than the classic 5-row layout).
    row_n = f"{cell('northwest', 2)} {cell('north', 1)} {cell('northeast', 2)}"
    # Always show @ in the center (you are here) -- not an exit slot.
    mid = f" {cell('west', 1)} @ {cell('east', 1)}"
    if "up" in by_dir or "down" in by_dir:
        u = cell("up", 1).strip() or " "
        d = cell("down", 1).strip() or " "
        mid = f"{mid}  {u} {d}"
    row_s = f"{cell('southwest', 2)} {cell('south', 1)} {cell('southeast', 2)}"

    lines = []
    for plain in (row_n, mid, row_s):
        # Skip empty N/S bands when those exits are absent -- shorter look.
        if not plain.strip():
            continue
        lines.append(_center_visible(plain, width, color="light_grey"))

    # Non-compass exits: in/out, apartment doors, street numbers, …
    also = []
    for direction, dest in exits:
        key = str(direction).strip().lower()
        if key in _COMPASS_CARDINALS:
            continue
        also.append(f"{direction} ({dest})")
    if also:
        also_text = "Also: " + ", ".join(also)
        lines.append(_center_visible(also_text, width, color="silver"))

    return lines


def _exit_legend_lines(exits, width=ROOM_WIDTH):
    """Compact centered ``N Plaza · E Street`` legend under the compass."""
    by_dir = _exit_dir_set(exits)
    if not by_dir:
        return []
    parts = []
    # Compass dirs first (stable order), then any leftovers.
    seen = set()
    for name in _COMPASS_LEGEND_ORDER:
        if name not in by_dir:
            continue
        abbrev = _COMPASS_CARDINALS[name]
        parts.append(f"{abbrev} {by_dir[name]}")
        seen.add(name)
    for direction, dest in exits:
        key = str(direction).strip().lower()
        if key in seen or key in _COMPASS_CARDINALS:
            continue
        parts.append(f"{direction} {dest}")
        seen.add(key)
    if not parts:
        return []
    # Soft-wrap the joined legend, then center each chunk.
    joined = " · ".join(parts)
    max_w = max(20, int(width) - 4)
    out = []
    while joined:
        if len(joined) <= max_w:
            out.append(_center_visible(joined, width, color="muted"))
            break
        cut = joined.rfind(" · ", 0, max_w)
        if cut < 10:
            cut = max_w
            chunk, joined = joined[:cut], joined[cut:].lstrip()
        else:
            chunk, joined = joined[:cut], joined[cut + 3:].lstrip()
        out.append(_center_visible(chunk, width, color="muted"))
    return out


def _exit_abbrev(direction):
    """Short token for an exit direction (n, ne, in, …)."""
    key = str(direction).strip().lower()
    if key in _COMPASS_CARDINALS:
        return _COMPASS_CARDINALS[key].lower()
    return key


# Compact Exits: line order (cardinals, vertical, diagonals).
_EXIT_LINE_ORDER = (
    "north", "east", "south", "west", "up", "down",
    "northeast", "northwest", "southeast", "southwest",
)


def _exit_display_name(direction):
    """Title-case a direction for the verbose Exits list (``North``)."""
    return str(direction).strip().title()


def _sparse_exits_line(exits, *, verbose=True):
    """Build LOTJ-style verbose exits or compact ``Exits: n, e, s``.

    Verbose (default): header ``Exits:`` then one ``North - Dest`` line
    each. Compact: a single abbrev line. Empty exits -> []. Direction
    words / letters are the primary signal (a11y -- not color alone).
    """
    if not exits:
        return []
    if verbose:
        # LOTJ / FK: header + one line per exit with destination name.
        lines = [paint("silver", "Exits:")]
        by_dir = _exit_dir_set(exits)
        ordered = []
        seen = set()
        for name in _EXIT_LINE_ORDER:
            if name in by_dir:
                ordered.append((name, by_dir[name]))
                seen.add(name)
        for direction, dest in exits:
            key = str(direction).strip().lower()
            if key in seen:
                continue
            ordered.append((direction, dest))
            seen.add(key)
        for direction, dest in ordered:
            label = _exit_display_name(direction)
            # Gold direction + soft dest -- labels carry meaning, not color.
            lines.append(
                render(f"<gold>{label}<silver> - <absinthe_green>{dest}")
            )
        return lines

    tokens = []
    seen = set()
    by_dir = _exit_dir_set(exits)
    for name in _EXIT_LINE_ORDER:
        if name in by_dir:
            tokens.append(_exit_abbrev(name))
            seen.add(name)
    for direction, _dest in exits:
        key = str(direction).strip().lower()
        if key in seen:
            continue
        tokens.append(_exit_abbrev(direction))
        seen.add(key)
    if not tokens:
        return []
    joined = ", ".join(tokens)
    return [render(f"<silver>Exits: <white>{joined}")]


def format_room(title, description, *, area_tag="Indoors", exits=None,
                souls=None, items=None, extras=None, width=ROOM_WIDTH,
                screenreader=False, local_map_lines=None,
                exits_verbose=True):
    """Room look: classic sparse (sighted) or SR flatten.

    Sighted path follows DIKU / LOTJ anatomy (no Master Room
    ``O=====O`` on look -- frames stay on score/who/help)::

        title + area badge
        prose
        Exits:
        North - Dest
        people / items as plain long-desc lines (no list bullets)

    Compact ``Exits: n, e, s`` is opt-in via ``exits_verbose=False``.
    Screenreader keeps vertical Paths / Souls / Items lists.
    """
    w = max(40, int(width))
    exits = list(exits or [])
    souls = list(souls or [])
    items = list(items or [])
    local_map_lines = list(local_map_lines or [])

    # ---- Screen-reader flatten (prefs #30 / #32) ------------------------
    if screenreader:
        lines = ["", f"Room: {title}. ({area_tag}).", ""]
        desc = (description or "").strip()
        if desc:
            for para in desc.split("\n"):
                para = para.strip()
                if para:
                    if para[-1] not in ".!?":
                        para = para + "."
                    lines.append(para)
            lines.append("")
        if extras:
            for extra in extras:
                text = str(extra).strip()
                if text:
                    if text[-1] not in ".!?":
                        text = text + "."
                    lines.append(text)
            lines.append("")
        if exits:
            lines.append("Paths:")
            for direction, dest in exits:
                lines.append(f"  {direction}: {dest}.")
            lines.append("")
        if souls:
            lines.append("Souls:")
            for soul in souls:
                lines.append(f"  {soul}.")
            lines.append("")
        if items:
            lines.append("Items:")
            for item in items:
                lines.append(f"  {item}.")
            lines.append("")
        return lines

    # ---- Classic sparse sighted look ------------------------------------
    # Title may already be ANSI-painted (City - Main - Sub roles). Do not
    # re-paint; width math uses visible length so escapes do not eat budget.
    tag_plain = f"[ {area_tag} ]"
    title_raw = str(title).strip()
    title_visible = strip_ansi(title_raw)
    title_budget = max(8, w - len(tag_plain) - 1)
    if visible_len(title_visible) > title_budget:
        # Truncate the plain text, then drop paint (rare oversized titles).
        title_visible = title_visible[: title_budget - 3] + "..."
        title_display = paint("dark_red", title_visible)
    elif "\x1b[" in title_raw:
        title_display = title_raw
    else:
        title_display = paint("dark_red", title_visible)
    gap = max(1, w - visible_len(title_visible) - len(tag_plain))
    header = (
        title_display
        + (" " * gap)
        + render(f"<dark_grey>[ <slate_grey>{area_tag} <dark_grey>]")
    )

    lines = ["", header, ""]

    desc = (description or "").strip()
    if desc:
        for para in desc.split("\n"):
            para = para.strip()
            if not para:
                lines.append("")
                continue
            wrapped = _wrap_plain(para, w - 2, color="light_grey")
            for row in wrapped:
                plain_row = strip_ansi(row)
                lines.append("  " + paint("light_grey", plain_row))
        lines.append("")

    if extras:
        for extra in extras:
            text = str(extra).strip()
            if text:
                lines.append("  " + paint("muted", text))
        lines.append("")

    if exits:
        lines.extend(_sparse_exits_line(exits, verbose=exits_verbose))
        lines.append("")

    if local_map_lines:
        for row in local_map_lines:
            text = str(row).rstrip("\r\n")
            if text.strip():
                lines.append(_center_visible(text, w))
            else:
                lines.append("")
        lines.append("")

    # Plain long-desc lines (LOTJ) -- no list-bullet chrome on look.
    for soul in souls:
        lines.append(paint("gold", str(soul)))
    if souls:
        lines.append("")
    for item in items:
        lines.append(paint("light_grey", str(item)))
    if items:
        lines.append("")

    return lines


# --- Origin badge colors for who -------------------------------------------

# Path id / origin id -> COLORS key. Every Origin/Path from origins.json
# is listed so who badges never fall through to unmarked silver by accident
# (badge text still carries meaning -- color is decoration only).
BADGE_COLORS = {
    # Origins ---------------------------------------------------------------
    "human": "silver",
    "supernatural": "dark_red",
    "celestial": "gold",
    "mutant": "absinthe_green",
    "cosmic": "accent",
    "constructed": "dark_cyan",
    "alien": "gold",
    "creation": "white",
    # Human Backgrounds -----------------------------------------------------
    "detective": "dark_cyan",
    "scientist": "pale_blue",
    "procurer": "slate_grey",
    "witch": "violet",
    "medic": "teal",
    "soldier": "gold",
    "hunter": "absinthe_green",
    "occultist": "dark_purple",
    "slayer": "dark_red",
    # Supernatural Lineages -------------------------------------------------
    "vampire": "dark_red",
    "shifter": "teal",
    "leviathan": "dark_cyan",
    # Celestial Mantles -----------------------------------------------------
    "angel": "bright_white",
    "demon": "dark_purple",
    "god": "gold",
    "divine": "gold",  # legacy path id alias
    # Mutant Strains --------------------------------------------------------
    "weaponized_biology": "absinthe_green",
    "psychic_projection": "accent",
    "elemental_adaptation": "teal",
    "kinetic_regenerative": "ok",
    # Cosmic Paths ----------------------------------------------------------
    "elemental": "gold",
    "eldritch": "dark_purple",
    # Constructed Forms -----------------------------------------------------
    "android": "dark_cyan",
    "golem": "slate_grey",
    "animated": "silver",
    # Alien / Creation ------------------------------------------------------
    "stellar": "gold",
    "umbral": "silver",
    "maker": "white",
}


def badge_color_for(character):
    """Pick a who-list badge color from Origin / Path (never color alone)."""
    path = getattr(character, "path", None) or ""
    origin = getattr(character, "origin", None) or ""
    if path in BADGE_COLORS:
        return BADGE_COLORS[path]
    if origin in BADGE_COLORS:
        return BADGE_COLORS[origin]
    return "silver"
