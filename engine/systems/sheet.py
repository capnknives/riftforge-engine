"""
engine/systems/sheet.py -- schema-driven character sheet assembly.

The engine owns the **shape** of a score sheet: field catalog
(``engine/content/sheet_profile.json``), pane names, Blood & Velvet
framing via ``engine.style.format_sheet``, and contributor ordering.
Game packages register **hook field providers** and **section
contributors** through ``engine.hooks`` -- never the reverse.

SUPERS-specific rows (Origin, fuel, lifeforce %, GM TOTAL_POWER, …) live
in ``supers/sheet_score.py`` contributors. Basegame registers path + HP
hooks only. Lean ``RIFTFORGE_GAME=none`` boots with engine primaries +
tier when no game hooks are wired.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from engine import display_prefs
from engine import stats as engine_stats
from engine import style

_PROFILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "content",
    "sheet_profile.json",
)
_PROFILE: dict | None = None

# fn(SheetContext) -> str | None
_FIELD_HOOKS: dict[str, object] = {}

# list of (priority, section_id, fn) where fn(ctx) -> SheetSection | list | None
_CONTRIBUTORS: list[tuple[int, str, object]] = []


@dataclass
class SheetContext:
    """Everything a sheet builder needs about subject, viewer, and pane."""

    target: object
    game: object | None = None
    viewer: object | None = None
    pane: str = "default"
    compact: bool = True
    for_gm: bool = False
    filter_mode: str | None = None
    screenreader: bool | None = None
    title: str | None = None
    skip_engine_fields: bool = False

    def resolved_screenreader(self) -> bool:
        """Viewer pref when set; else the sheet subject's pref."""
        if self.screenreader is not None:
            return bool(self.screenreader)
        return bool(getattr(self.target, "screenreader", False))

    def resolved_viewer(self) -> object:
        return self.viewer if self.viewer is not None else self.target

    def sheet_width(self) -> int:
        return display_prefs.sheet_width(self.resolved_viewer())


@dataclass
class SheetSection:
    """One contributed block (plain-text lines, client-wrappable)."""

    id: str
    lines: list[str]
    priority: int = 100
    panes: frozenset[str] | None = None
    replace: bool = False

    def applies(self, pane: str) -> bool:
        if self.panes is None:
            return True
        return pane in self.panes


def _load_profile() -> dict:
    global _PROFILE
    if _PROFILE is not None:
        return _PROFILE
    with open(_PROFILE_PATH, encoding="utf-8") as fh:
        _PROFILE = json.load(fh)
    return _PROFILE


def sheet_profile():
    """Return the cached engine sheet field catalog (read-only dict)."""
    return _load_profile()


def register_field_hook(field_id: str, fn):
    """Register fn(ctx) -> str | None for a ``hook:<field_id>`` profile row."""
    _FIELD_HOOKS[str(field_id)] = fn


def register_contributor(section_id: str, fn, *, priority: int = 100):
    """Register fn(ctx) -> SheetSection | list[SheetSection] | None."""
    _CONTRIBUTORS.append((int(priority), str(section_id), fn))
    _CONTRIBUTORS.sort(key=lambda row: (row[0], row[1]))


def clear_registrations_for_tests():
    """Reset hook tables (smoke tests that flip game packages mid-run)."""
    global _PROFILE
    _PROFILE = None
    _FIELD_HOOKS.clear()
    _CONTRIBUTORS.clear()


def _pane_meta(ctx: SheetContext) -> dict:
    profile = _load_profile()
    panes = profile.get("panes") or {}
    mode = ctx.filter_mode or ctx.pane or "default"
    meta = dict(panes.get(mode) or panes.get("default") or {})
    if ctx.title:
        meta["title"] = ctx.title
    return meta


def _field_applies(field: dict, pane: str) -> bool:
    allowed = field.get("panes")
    if not allowed:
        return True
    return pane in allowed


def _resolve_engine_field(field_id: str, ctx: SheetContext) -> str | None:
    if field_id == "primaries":
        stats = getattr(ctx.target, "stats", None) or {}
        parts = []
        for name in engine_stats.STAT_NAMES:
            val = stats.get(name, 0.0)
            try:
                parts.append(f"{name} {float(val):.1f}")
            except (TypeError, ValueError):
                parts.append(f"{name} ?")
        return "  " + "  ".join(parts)
    if field_id == "tier":
        tier = getattr(ctx.target, "tier", 0)
        return f"  Tier: {tier}"
    return None


def _resolve_field(field: dict, ctx: SheetContext) -> str | None:
    source = str(field.get("source") or "")
    if source.startswith("engine:"):
        return _resolve_engine_field(source.split(":", 1)[1], ctx)
    if source.startswith("hook:"):
        hook_id = source.split(":", 1)[1]
        fn = _FIELD_HOOKS.get(hook_id)
        if fn is None:
            return None
        try:
            return fn(ctx)
        except TypeError:
            return fn(ctx)  # pragma: no cover -- legacy arity
    return None


def _normalize_sections(result) -> list[SheetSection]:
    if result is None:
        return []
    if isinstance(result, SheetSection):
        return [result]
    if isinstance(result, list):
        out = []
        for item in result:
            out.extend(_normalize_sections(item))
        return out
    return []


def assemble_body(ctx: SheetContext) -> tuple[list[str], str]:
    """Build sheet body lines + title from schema, hooks, and contributors."""
    meta = _pane_meta(ctx)
    title = str(meta.get("title") or _load_profile().get("title") or "SCORE")
    pane = ctx.filter_mode or ctx.pane or "default"
    body: list[str] = []
    header_lines: list[str] = []

    if not ctx.skip_engine_fields:
        for field_row in _load_profile().get("fields") or []:
            if not _field_applies(field_row, pane):
                continue
            line = _resolve_field(field_row, ctx)
            if not line:
                continue
            slot = field_row.get("slot") or "body"
            if slot == "header":
                header_lines.append(line)
            else:
                body.append(line)

    for _prio, _sid, fn in list(_CONTRIBUTORS):
        try:
            raw = fn(ctx)
        except TypeError:
            raw = fn(ctx)  # pragma: no cover
        for section in _normalize_sections(raw):
            if not section.applies(pane):
                continue
            if section.replace:
                body = list(section.lines)
                continue
            body.extend(section.lines)

    if header_lines:
        body = header_lines + body
    return body, title


def format_assembled(
    ctx: SheetContext,
    body: list[str],
    title: str | None = None,
) -> str:
    """Apply Blood & Velvet framing (or SR flatten) to assembled body lines."""
    sheet_title = title or _pane_meta(ctx).get("title") or "SCORE"
    sr = ctx.resolved_screenreader()
    if sr:
        lines = ["", f"{sheet_title}."]
        for line in body:
            text = style.strip_ansi(str(line)).rstrip()
            if text and text[-1] not in ".!?":
                text = text + "."
            lines.append(text)
        return "\r\n".join(lines)

    lines = [""]
    lines.extend(
        style.format_sheet(
            sheet_title,
            body,
            width=ctx.sheet_width(),
            screenreader=False,
        )
    )
    return "\r\n".join(lines)


def render_score(ctx: SheetContext) -> str:
    """Full score sheet: schema + hooks + contributors, then frame."""
    body, title = assemble_body(ctx)
    return format_assembled(ctx, body, title=title)
