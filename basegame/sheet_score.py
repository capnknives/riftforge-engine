"""basegame/sheet_score.py -- score-sheet hooks for the reference game."""

from __future__ import annotations

import time

from basegame.chargen import PATHS
from basegame import body_parts as body_parts_module
from basegame import needs as needs_module
from basegame import stats as stats_module
from engine import hooks
from engine import stats as engine_stats
from engine import style
from engine.command_support import _display_name
from engine.systems import needs as needs_engine
from engine.systems import readiness as readiness_mod
from engine.systems import sheet as sheet_mod

_PANES_DEFAULT = frozenset({"default", "full"})
_PANES_VITALS = frozenset({"default", "vitals", "full"})
_PANES_COMBAT = frozenset({"combat"})
_PANES_NEEDS = frozenset({"needs", "full"})


def _path_name(character):
    """Short Path label (``Reporter``), never the chargen blurb."""
    path_id = getattr(character, "bg_path", None)
    raw = PATHS.get(path_id, "Path: (none chosen)")
    return str(raw).split(" -- ", 1)[0].strip()


def _path_blurb(character):
    """Long chargen description after `` -- ``."""
    path_id = getattr(character, "bg_path", None)
    raw = PATHS.get(path_id)
    if not raw or " -- " not in raw:
        return ""
    return raw.split(" -- ", 1)[1].strip()


def _hp_percent(character):
    """Whole-number lifeforce percent for score lines."""
    mx = stats_module.max_hp(character)
    if mx <= 0:
        return 0
    cur = float(getattr(character, "hp", 0.0) or 0.0)
    return int(round(100.0 * cur / mx))


def _header(ctx):
    """Title row: name, Path, Tier (Blood & Velvet title role when sighted)."""
    target = ctx.target
    path_name = _path_name(target)
    tier = int(getattr(target, "tier", 0) or 0)
    title = f"{_display_name(target)} -- Path: {path_name} (Tier {tier})"
    if ctx.resolved_screenreader():
        return f"  {title}"
    return style.paint("title", title)


def _primaries_rows(ctx):
    """Six primaries split across two readable rows."""
    stats = getattr(ctx.target, "stats", None) or {}
    row_a = []
    row_b = []
    for index, name in enumerate(engine_stats.STAT_NAMES):
        val = stats.get(name, 0.0)
        try:
            bit = f"{name} {float(val):.1f}"
        except (TypeError, ValueError):
            bit = f"{name} ?"
        if index < 3:
            row_a.append(bit)
        else:
            row_b.append(bit)
    return "  " + "  ".join(row_a), "  " + "  ".join(row_b)


def _primaries_row1(ctx):
    return _primaries_rows(ctx)[0]


def _primaries_row2(ctx):
    return _primaries_rows(ctx)[1]


def _hp(ctx):
    """Lifeforce line -- percent by default, raw pool with combatnumbers."""
    target = ctx.target
    pct = _hp_percent(target)
    if bool(getattr(target, "combat_numbers", False)):
        cur = int(getattr(target, "hp", 0) or 0)
        mx = int(stats_module.max_hp(target) or 0)
        return f"  Lifeforce: {cur}/{mx} ({pct}%)"
    return f"  Lifeforce: {pct}%"


def _readiness_label(character, track):
    """Plain Balance / Equilibrium line for combat panes."""
    readiness_mod.ensure_defaults(character)
    attr = (
        "balance_ready_at"
        if track == readiness_mod.TRACK_BALANCE
        else "equilibrium_ready_at"
    )
    deadline = getattr(character, attr, None)
    label = "Balance" if track == readiness_mod.TRACK_BALANCE else "Equilibrium"
    if deadline is None or time.monotonic() >= float(deadline):
        return f"  {label}: ready"
    remaining = float(deadline) - time.monotonic()
    return f"  {label}: {remaining:.1f}s"


def contribute_basegame(ctx):
    """Origin / Bloodline + wallet lines below the engine schema block."""
    target = ctx.target
    lines = []
    origin = getattr(target, "origin", "mundane") or "mundane"
    if origin == "alien":
        alien_path = getattr(target, "alien_path", None) or "?"
        if alien_path == "stellar":
            blood = "Stellar"
        elif alien_path == "umbral":
            blood = "Umbral"
        else:
            blood = str(alien_path).title()
        lines.append(f"  Origin: Alien ({blood})")
    elif origin != "mundane":
        lines.append(f"  Origin: {str(origin).title()}")
    from engine.systems import economy as economy_mod

    economy_mod.migrate_wallet_fields(target)
    cash = economy_mod.format_wallet(target)
    lines.append(f"  Cash: {cash}")
    bank = economy_mod.format_bank(target)
    if bank and bank not in ("$0", "$0.00"):
        lines.append(f"  Bank: {bank}")
    if not lines:
        return None
    return sheet_mod.SheetSection(
        id="basegame",
        lines=lines,
        priority=50,
        panes=_PANES_DEFAULT | _PANES_VITALS,
    )


def contribute_path_blurb(ctx):
    """Full sheet only: the chargen one-liner under identity."""
    pane = ctx.filter_mode or ctx.pane or "default"
    if pane != "full":
        return None
    blurb = _path_blurb(ctx.target)
    if not blurb:
        return None
    return sheet_mod.SheetSection(
        id="path_blurb",
        lines=[f"  {blurb}"],
        priority=45,
        panes=frozenset({"full"}),
    )


def contribute_injuries(ctx):
    """Compact injury line on default; per-limb rows on vitals/combat."""
    target = ctx.target
    pane = ctx.filter_mode or ctx.pane or "default"
    compact = ctx.compact and pane == "default"
    if pane in ("combat", "vitals"):
        lines = body_parts_module.combat_injury_lines(target)
        if not lines:
            if pane == "combat":
                lines = ["  (no regional injuries)"]
            else:
                return None
        else:
            lines = ["  Injuries:"] + lines
        return sheet_mod.SheetSection(
            id="injuries",
            lines=lines,
            priority=70,
            panes=frozenset({"combat", "vitals"}),
        )
    line = body_parts_module.status_line(
        target,
        screenreader=ctx.resolved_screenreader(),
        compact=compact,
    )
    if not line:
        return None
    return sheet_mod.SheetSection(
        id="injuries",
        lines=[f"  {line}"],
        priority=70,
        panes=_PANES_DEFAULT,
    )


def contribute_needs(ctx):
    """Hunger/thirst meters for needs/vitals panes and urgent default line."""
    target = ctx.target
    pane = ctx.filter_mode or ctx.pane or "default"
    lines = []
    if pane in ("needs", "vitals"):
        if pane == "needs":
            lines.append("")
        for name in needs_module.NEEDS:
            level = float(getattr(target, name, 0.0) or 0.0)
            phrase = needs_engine.level_phrase(level)
            label = name.title()
            lines.append(f"  {label}: {phrase} {name}")
        return sheet_mod.SheetSection(
            id="needs",
            lines=lines,
            priority=60,
            panes=frozenset({"needs", "vitals"}),
        )
    urgent = needs_engine.most_urgent(target, needs_module.NEEDS)
    if urgent and pane == "default" and ctx.compact:
        name, level = urgent
        phrase = needs_engine.level_phrase(level)
        lines.append(
            f"  {name.title()}: {phrase} {name} (score needs for detail)"
        )
        return sheet_mod.SheetSection(
            id="needs",
            lines=lines,
            priority=65,
            panes=frozenset({"default"}),
        )
    return None


def contribute_combat(ctx):
    """Balance, Equilibrium, firearm sight, and flight state on combat pane."""
    target = ctx.target
    lines = [
        _readiness_label(target, readiness_mod.TRACK_BALANCE),
        _readiness_label(target, readiness_mod.TRACK_EQUILIBRIUM),
    ]
    from engine.systems import firearms as firearms_mod

    firearms_mod.ensure_firearm(target)
    sight = firearms_mod.get_sight(target)
    if sight and sight.get("target"):
        name = (
            getattr(sight["target"], "key", None)
            or getattr(sight["target"], "name", None)
            or "someone"
        )
        zone = sight.get("zone") or "center mass"
        lines.append(f"  Sight: {name} ({zone})")
    else:
        lines.append("  Sight: (none)")
    weapon = firearms_mod.get_firearm(target)
    if weapon:
        chamber = "chambered" if weapon.get("chambered") else "empty chamber"
        mag = int(weapon.get("magazine") or 0)
        cap = int(weapon.get("max_magazine") or 0)
        lines.append(f"  Firearm: {mag}/{cap} mag, {chamber}")
    from engine.systems import aerial as aerial_mod

    if getattr(target, "is_flying", False) or getattr(target, "room_hover", False):
        if getattr(target, "room_hover", False):
            lines.append("  Flight: hovering (in-room)")
        else:
            tier = aerial_mod.flight_tier(target)
            lines.append(f"  Flight: {tier or 'airborne'}")
    return sheet_mod.SheetSection(
        id="combat",
        lines=lines,
        priority=55,
        panes=_PANES_COMBAT,
    )


def contribute_detail_footer(ctx):
    """Point compact default score at nested panes."""
    if not ctx.compact or (ctx.filter_mode or ctx.pane or "default") != "default":
        return None
    return sheet_mod.SheetSection(
        id="detail",
        lines=[
            "  Detail: score vitals | score combat | score needs | score full",
        ],
        priority=200,
        panes=frozenset({"default"}),
    )


def register_score_sheet_hooks():
    """Wire basegame Path + HP + injuries/needs/combat into the engine sheet."""
    hooks.register_sheet_field("header", _header)
    hooks.register_sheet_field("primaries_row1", _primaries_row1)
    hooks.register_sheet_field("primaries_row2", _primaries_row2)
    hooks.register_sheet_field("hp", _hp)
    hooks.register_sheet_contributor("basegame", contribute_basegame, priority=50)
    hooks.register_sheet_contributor("path_blurb", contribute_path_blurb, priority=45)
    hooks.register_sheet_contributor("injuries", contribute_injuries, priority=70)
    hooks.register_sheet_contributor("needs", contribute_needs, priority=60)
    hooks.register_sheet_contributor("combat", contribute_combat, priority=55)
    hooks.register_sheet_contributor(
        "detail", contribute_detail_footer, priority=200,
    )
