"""
style.py -- gothic ANSI color + old-MUD layout helpers
(suggestions.log #51 / #55; docs/plans/colorandformattingforgame.R).

Preference catalog (phases, gaps, cite prefs #N in PRs):
  docs/plans/mud_formatting_preferences.md

Pure presentation. Game logic must never depend on color for meaning
(docs/SYSTEMS_DESIGN.md section 8): every painted string still carries a
plain-text label. Session.send strips ANSI when Character.use_color is off.

Palette (#51 + the plan's named tags):
  - Readable on black -- no dark navy. ``midnight_blue`` / ``pale_blue`` in
    templates remap to soft cyan / dark grey so a11y stays intact.
  - No neon spam; soft amber, crimson, silver, muted green, teal.
  - ``render("<tag>text <other>more")`` switches color at each <tag>.
  - Semantic combat/chat roles (prefs #10 / #19 / #23): ``combat_out``,
    ``combat_in``, ``combat_other``, ``combat_mitigate``, ``ooc``, ``alert``.

Layout families from the plan (docs/plans/colorandformattingforgame.R):
  - Master Room        -- look sheet (O=====O, Paths / Souls / Items)
  - Wrought Iron & Ash -- who list (x-x-x rules, badge columns)
  - Blood & Velvet     -- help tomes / score / shop sheets (==== rules)
  - Abyss menu         -- numbered option menus (chargen, settings)
  - Dialogue frame     -- NPC prompt boxes (reusable; optional callers)
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
    # Outgoing = bright cyan; incoming = red alert; third-party = muted;
    # mitigate = soft teal; OOC = unnatural muted grey; alert = gold.
    "combat_out": "\x1b[96m",
    "combat_in": "\x1b[91m",
    "combat_other": "\x1b[90m",
    "combat_mitigate": "\x1b[36m",
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
    "combat_out": "\x1b[38;5;123m",
    "combat_in": "\x1b[38;5;196m",
    "combat_other": "\x1b[38;5;242m",
    "combat_mitigate": "\x1b[38;5;73m",
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


# --- Wrought Iron & Ash: who list ------------------------------------------

def format_moral_meter(balance, *, lean="", eclipse=False, width=WHO_WIDTH):
    """Aesthetic Good/Evil world-tide bar (who-list footer).

    Scale is -100..+100 (positive = good). Fill grows from the center `|`
    toward EVIL (left) or GOOD (right) -- empty track stays at the outer
    edges. Labels carry meaning -- EVIL / GOOD ends, lean phrase, and
    signed number -- so color-off clients still read the meter (section 8
    a11y). Color is decoration only.
    """
    w = max(40, int(width))
    bal = max(-100, min(100, int(balance)))
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

    # Caption: lean phrase (or "even") + signed number -- never color alone.
    if lean:
        caption = f"{lean} ({bal:+d})"
    elif bal == 0:
        caption = f"The town hangs in balance ({bal:+d})"
    elif bal > 0:
        caption = f"The town leans toward the light ({bal:+d})"
    else:
        caption = f"The town leans toward darkness ({bal:+d})"
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


def format_need_meter(level, *, critical=False, width=16):
    """Unipolar 0→1 need bar (left fill), World Tide–adjacent glyphs.

    Fill grows left→right as the need rises (0 = empty track, 1 = full).
    Critical meters use `=` fill and a trailing `!` inside the brackets so
    color-off clients still read severity (section 8 a11y). Color is
    decoration only -- callers should pair this with a plain-language phrase.

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
    # Paint: critical fill dark_red, normal fill silver; empty dark_grey.
    fill_role = "dark_red" if critical else "silver"
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
               echo_entries=None, gm_names=None, screenreader=False):
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

    ``screenreader=True`` (prefs #30 / #32) flattens wrought rules into
    semantic headers and vertical lists.
    """
    w = max(40, int(width))
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
        if moral_balance is not None:
            lines.append(f"World Tide balance: {moral_balance}.")
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

def format_tome(title, body_lines, *, related=None, syntax=None,
                width=TOME_WIDTH):
    """Blood & Velvet help / sheet frame (plan section 2).

    `body_lines` is an iterable of plain or already-painted lines.
    Optional `syntax` (string) and `related` (string or list) get labeled
    sections under the header / above the footer.
    """
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


def format_help_index(categories, *, width=TOME_WIDTH):
    """Bare `help` grimoire index: category tomes with topic blurbs.

    `categories` is HELP_CATEGORIES shape: [(category, [(name, blurb), ...])].
    """
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


def format_sheet(title, body_lines, *, width=48):
    """Compact Blood & Velvet frame for score / shop / reports.

    Body lines that exceed ``width`` are word-wrapped (plain text) so they
    never run past the equals border.
    """
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
    """Word-wrap a plain string to `width`, painting each line."""
    words = text.split()
    if not words:
        return [""]
    rows = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        if len(trial) <= width:
            current = trial
        else:
            rows.append(paint(color, current))
            current = word
    rows.append(paint(color, current))
    return rows


# --- Abyss menu / dialogue -------------------------------------------------

def format_menu(title, options, *, prompt="What is your will?", width=67):
    """Numbered menu (plan section 3). `options` is [(label, hint), ...].

    Hints are the grey parenthetical descriptions. Numbers are absinthe.
    """
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


def format_dialogue(speaker_line, quote, choices, *, width=65):
    """NPC dialogue box (plan section 4). `choices` is [reply_str, ...]."""
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
    for i, choice in enumerate(choices, start=1):
        lines.append(render(
            f"<slate_grey>  [ <pale_blue>{i} <slate_grey>] "
            f"<light_grey>\"{choice}\""
        ))
    lines.append(bot)
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

    Each cell looks like ``North: The Dining Hall``. Direction is white,
    destination light_grey -- labels carry meaning; color is decoration.
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


def format_room(title, description, *, area_tag="Indoors", exits=None,
                souls=None, items=None, extras=None, width=ROOM_WIDTH,
                screenreader=False):
    """Master Room Layout (colorandformattingforgame.R section 1).

    Parameters
    ----------
    title : str
        Room key / name (plain text; painted dark_red in the header).
    description : str
        Room prose; soft-wrapped and indented two spaces.
    area_tag : str
        Right-side header badge, e.g. ``Ruins`` / ``City``.
    exits : list[tuple[str, str]] | None
        ``(direction, destination_name)`` pairs. Omitted or empty -> no
        Paths section (plan instruction 3: hide empty sections).
    souls : list[str] | None
        People present (display names / short lines). Empty -> hidden.
    items : list[str] | None
        Floor items (plan draft called this "Relics"; we use Items so it
        isn't confused with Divine/Path relics). Empty -> hidden.
    extras : list[str] | None
        Optional lines after the description (gravity, overland, ambient
        sky) before the dash divider. Plain text; muted paint applied.
    width : int
        Outer frame width (default ROOM_WIDTH=67).
    screenreader : bool
        Prefs #30 / #32: skip ASCII frames and columns; semantic headers
        and vertical lists for TTS.

    Returns a list of lines ready to ``\"\\r\\n\".join``.
    """
    w = max(40, int(width))
    exits = list(exits or [])
    souls = list(souls or [])
    items = list(items or [])

    # ---- Screen-reader flatten (prefs #30 / #32) ------------------------
    if screenreader:
        lines = ["", f"Room: {title}. ({area_tag}).", ""]
        desc = (description or "").strip()
        if desc:
            for para in desc.split("\n"):
                para = para.strip()
                if para:
                    # End with period for TTS pauses (prefs #31).
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

    frame = paint("dark_grey", room_frame_rule(w))
    # Header row: "  Title .... [ Tag ]" -- title left, badge right.
    tag_plain = f"[ {area_tag} ]"
    title_plain = str(title).strip()
    # Inner width after leading two spaces.
    inner = w - 2
    title_budget = max(8, inner - len(tag_plain) - 1)
    if len(title_plain) > title_budget:
        title_plain = title_plain[: title_budget - 3] + "..."
    gap = max(1, inner - len(title_plain) - len(tag_plain))
    header = (
        "  "
        + paint("dark_red", title_plain)
        + (" " * gap)
        + render(f"<dark_grey>[ <slate_grey>{area_tag} <dark_grey>]")
    )

    lines = ["", frame, header, frame, ""]

    # Description: preserve indentation (two spaces); wrap at w-2.
    desc = (description or "").strip()
    if desc:
        # Split on existing newlines first so authored paragraphs survive.
        for para in desc.split("\n"):
            para = para.strip()
            if not para:
                lines.append("")
                continue
            wrapped = _wrap_plain(para, w - 2, color="light_grey")
            for row in wrapped:
                # _wrap_plain already paints; prepend indent spaces outside
                # the paint so leading spaces aren't colored oddly.
                plain_row = strip_ansi(row)
                lines.append("  " + paint("light_grey", plain_row))
        lines.append("")

    if extras:
        for extra in extras:
            text = str(extra).strip()
            if text:
                lines.append("  " + paint("muted", text))
        lines.append("")

    # Dash divider only if at least one of Paths/Souls/Items will show
    # (keeps an empty room from a lonely divider under the description).
    has_sections = bool(exits or souls or items)
    if has_sections:
        lines.append("  " + paint("dark_grey", spaced_dash_rule(w - 2)))

    if exits:
        lines.append(_section_header("Paths", width=w))
        lines.extend(_exit_columns(exits, width=w, cols=2))
        lines.append("")

    if souls:
        lines.append(_section_header("Souls", width=w))
        for soul in souls:
            lines.append(render(
                f"<dark_grey>    > <white>{soul}"
            ))
        lines.append("")

    if items:
        lines.append(_section_header("Items", width=w))
        for item in items:
            lines.append(render(
                f"<dark_grey>    > <light_grey>{item}"
            ))
        lines.append("")

    lines.append(frame)
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
