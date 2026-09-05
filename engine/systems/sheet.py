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

# fn(SheetContext) -> str | list[str] | None
_FIELD_HOOKS: dict[str, object] = {}

# list of (priority, section_id, fn) where fn(ctx) -> SheetSection | list | None
_CONTRIBUTORS: list[tuple[int, str, object]] = []

# Runtime fields that are not (yet) in sheet_profile.json. Same shape as a
# catalog row plus ``fn``. Games call ``register_auto_field`` so a new meter
# shows up on score without waiting on a catalog edit -- the JSON row is
# still the preferred long-term home (audit warns on auto-only fields).
_DYNAMIC_FIELDS: list[dict] = []

# Slots the engine default assembler owns. Custom game layouts (SUPERS
# two-column identity/vitals) skip these on overflow so primaries/HP do
# not double-print.
_DEFAULT_ASSEMBLY_SLOTS = frozenset({"header", "body"})


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
    # Slots this layout already merged (identity wallet, vitals pairing, …).
    claimed_slots: set[str] = field(default_factory=set)
    # Cache so pairing a slot's first cell does not re-resolve later.
    _slot_cache: dict[str, list[str]] = field(default_factory=dict)

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
    """Register fn(ctx) -> str | list[str] | None for a ``hook:<field_id>`` row.

    Idempotent: a later call with the same ``field_id`` replaces the
    provider (boot runs register twice -- package import + ``register_all_hooks``).
    """
    _FIELD_HOOKS[str(field_id)] = fn


def register_contributor(section_id: str, fn, *, priority: int = 100):
    """Register fn(ctx) -> SheetSection | list[SheetSection] | None.

    Idempotent on ``section_id`` (same boot-twice habit as field hooks).
    """
    global _CONTRIBUTORS
    sid = str(section_id)
    _CONTRIBUTORS = [row for row in _CONTRIBUTORS if row[1] != sid]
    _CONTRIBUTORS.append((int(priority), sid, fn))
    _CONTRIBUTORS.sort(key=lambda row: (row[0], row[1]))


def register_auto_field(
    field_id: str,
    fn,
    *,
    slot: str = "body",
    panes: list[str] | tuple[str, ...] | None = None,
    compact: bool | str = True,
    filter_prefixes: list[str] | tuple[str, ...] | None = None,
):
    """Register a score field that appears even without a catalog row.

    If ``sheet_profile.json`` already has this ``id``, the catalog wins for
    slot / panes and this only installs the hook provider. Otherwise the
    field is appended in ``slot`` (or overflow) the next time a layout
    band resolves -- so a new Origin meter can ship from the game package
    without a same-PR catalog edit, then get a JSON row when convenient.

    ``compact``: True = always (hook may still hide); False = hide on the
    compact default sheet; ``"only"`` = compact sheet only.
    """
    fid = str(field_id)
    register_field_hook(fid, fn)
    catalog_ids = {
        str(row.get("id") or "") for row in (_load_profile().get("fields") or [])
    }
    if fid in catalog_ids:
        return
    _DYNAMIC_FIELDS[:] = [row for row in _DYNAMIC_FIELDS if row.get("id") != fid]
    _DYNAMIC_FIELDS.append({
        "id": fid,
        "slot": str(slot or "body"),
        "panes": list(panes) if panes else None,
        "compact": compact,
        "filter_prefixes": list(filter_prefixes) if filter_prefixes else [],
        "source": f"hook:{fid}",
        "fn": fn,
    })


def clear_registrations_for_tests():
    """Reset hook tables (smoke tests that flip game packages mid-run)."""
    global _PROFILE
    _PROFILE = None
    _FIELD_HOOKS.clear()
    _CONTRIBUTORS.clear()
    _DYNAMIC_FIELDS.clear()


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


def _field_applies_compact(field: dict, compact: bool) -> bool:
    """Honor optional catalog ``compact``: true / false / \"only\"."""
    flag = field.get("compact", True)
    if flag is True or flag is None:
        return True
    if flag is False:
        return not compact
    if str(flag).lower() == "only":
        return bool(compact)
    return True


def layout_bands() -> list[dict]:
    """Ordered layout bands from the catalog (empty list = no layout key)."""
    layout = _load_profile().get("layout") or {}
    bands = layout.get("bands") or []
    return [dict(row) for row in bands if isinstance(row, dict)]


def layout_band(band_id: str) -> dict | None:
    """One layout band by id, or None."""
    wanted = str(band_id)
    for row in layout_bands():
        if str(row.get("id") or "") == wanted:
            return row
    return None


def catalog_slots() -> list[str]:
    """Unique slot names in catalog order (first occurrence wins)."""
    seen: list[str] = []
    for field_row in _load_profile().get("fields") or []:
        slot = str(field_row.get("slot") or "body")
        if slot not in seen:
            seen.append(slot)
    for dyn in _DYNAMIC_FIELDS:
        slot = str(dyn.get("slot") or "body")
        if slot not in seen:
            seen.append(slot)
    return seen


def catalog_hook_ids(*, include_optional: bool = True) -> list[str]:
    """``hook:<id>`` field ids from the catalog (for boot audits)."""
    out: list[str] = []
    for field_row in _load_profile().get("fields") or []:
        source = str(field_row.get("source") or "")
        if not source.startswith("hook:"):
            continue
        if not include_optional and field_row.get("optional"):
            continue
        out.append(source.split(":", 1)[1])
    return out


def registered_hook_ids() -> frozenset[str]:
    """Currently registered ``hook:*`` provider ids."""
    return frozenset(_FIELD_HOOKS)


def unregistered_catalog_hooks(*, include_optional: bool = False) -> list[str]:
    """Catalog ``hook:*`` ids with no provider registered (audit gap).

    Basegame-only rows (header / primaries / hp) set ``optional: true`` so
    a SUPERS boot does not fail the audit.
    """
    have = registered_hook_ids()
    return [
        fid
        for fid in catalog_hook_ids(include_optional=include_optional)
        if fid not in have
    ]


def filter_prefixes_for_pane(pane: str) -> list[str]:
    """Collect ``filter_prefixes`` from catalog + auto fields for a pane."""
    prefixes: list[str] = []
    seen: set[str] = set()

    def _add(items):
        for raw in items or []:
            text = str(raw)
            if text and text not in seen:
                seen.add(text)
                prefixes.append(text)

    for field_row in _load_profile().get("fields") or []:
        if not _field_applies(field_row, pane):
            continue
        _add(field_row.get("filter_prefixes"))
    for dyn in _DYNAMIC_FIELDS:
        panes = dyn.get("panes")
        if panes and pane not in panes:
            continue
        _add(dyn.get("filter_prefixes"))
    return prefixes


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
    if field_id in ("cash", "bank"):
        from engine.systems import economy as economy_mod

        economy_mod.migrate_wallet_fields(ctx.target)
        if field_id == "cash":
            return f"  Cash: {economy_mod.format_carry_cash(ctx.target)}"
        bank = economy_mod.format_bank(ctx.target)
        if not bank or bank in ("$0", "$0.00"):
            return None
        return f"  Bank: {bank}"
    if field_id == "solar_charge":
        from engine.systems import aerial as aerial_mod

        if not aerial_mod.is_stellar(ctx.target):
            return None
        aerial_mod.ensure_stellar_defaults(ctx.target)
        pct = int(round(float(getattr(ctx.target, "solar_charge", 0.0) or 0.0) * 100.0))
        tier = aerial_mod.flight_tier(ctx.target)
        return f"  Solar charge: {pct}%   Flight: {tier}"
    if field_id == "umbral_charge":
        from engine.systems import umbral as umbral_mod

        if not umbral_mod.is_umbral(ctx.target):
            return None
        umbral_mod.ensure_umbral_defaults(ctx.target)
        pct = int(round(float(getattr(ctx.target, "umbral_charge", 0.0) or 0.0) * 100.0))
        shroud = "shrouded" if getattr(ctx.target, "umbral_shrouded", False) else "open"
        return f"  Umbral charge: {pct}%   ({shroud})"
    return None


def resolve_field_by_id(field_id: str, ctx: SheetContext) -> str | None:
    """Resolve one catalog row by ``id`` for the active pane (engine:* + hook:*)."""
    pane = ctx.filter_mode or ctx.pane or "default"
    for field_row in _load_profile().get("fields") or []:
        if str(field_row.get("id") or "") != str(field_id):
            continue
        if not _field_applies(field_row, pane):
            return None
        return _resolve_field(field_row, ctx)
    return None


def _coerce_field_lines(raw) -> list[str]:
    """Normalize a field hook result to zero or more body lines."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    text = str(raw)
    return [text] if text else []


def resolve_profile_slot(ctx: SheetContext, slot: str) -> list[str]:
    """Resolve every catalog + auto-field row in ``slot`` for the active pane.

    Marks ``slot`` claimed on ``ctx`` so overflow does not reprint it.
    Repeat calls reuse a per-context cache (vitals pairing, then a
    resources band, must not double-resolve side effects).
    """
    wanted = str(slot)
    cache = ctx._slot_cache
    if wanted in cache:
        ctx.claimed_slots.add(wanted)
        return list(cache[wanted])
    pane = ctx.filter_mode or ctx.pane or "default"
    lines: list[str] = []
    catalog_ids: set[str] = set()
    for field_row in _load_profile().get("fields") or []:
        fid = str(field_row.get("id") or "")
        catalog_ids.add(fid)
        row_slot = str(field_row.get("slot") or "body")
        if row_slot != wanted:
            continue
        if not _field_applies(field_row, pane):
            continue
        if not _field_applies_compact(field_row, ctx.compact):
            continue
        lines.extend(_coerce_field_lines(_resolve_field(field_row, ctx)))
    for dyn in _DYNAMIC_FIELDS:
        if str(dyn.get("id") or "") in catalog_ids:
            continue
        if str(dyn.get("slot") or "body") != wanted:
            continue
        panes = dyn.get("panes")
        if panes and pane not in panes:
            continue
        if not _field_applies_compact(dyn, ctx.compact):
            continue
        fn = dyn.get("fn") or _FIELD_HOOKS.get(str(dyn.get("id") or ""))
        if fn is None:
            continue
        try:
            raw = fn(ctx)
        except TypeError:
            raw = fn(ctx)  # pragma: no cover -- legacy arity
        lines.extend(_coerce_field_lines(raw))
    cache[wanted] = list(lines)
    ctx.claimed_slots.add(wanted)
    return list(lines)


def append_profile_slots(ctx: SheetContext, body: list[str], *slots: str) -> None:
    """Merge schema slot rows onto a custom-layout body in catalog order."""
    for slot in slots:
        body.extend(resolve_profile_slot(ctx, slot))


def resolve_layout_band(
    ctx: SheetContext,
    band_id: str,
    *,
    skip_claimed: bool = False,
) -> list[str]:
    """Resolve every slot listed on a layout band."""
    band = layout_band(band_id)
    if not band:
        return []
    lines: list[str] = []
    for slot in band.get("slots") or []:
        name = str(slot)
        if skip_claimed and name in ctx.claimed_slots:
            continue
        lines.extend(resolve_profile_slot(ctx, name))
    return lines


def append_layout_band(
    ctx: SheetContext,
    body: list[str],
    band_id: str,
    *,
    skip_claimed: bool = False,
    width: int | None = None,
    screenreader: bool | None = None,
) -> None:
    """Append a labeled layout band when it produced any lines.

    Sighted sheets draw ``style.sheet_band`` when the catalog band sets
    ``band: true``. Screenreader skips decorative chrome and keeps the
    ``Label: value`` rows the hooks already emit.
    """
    band = layout_band(band_id)
    if not band:
        return
    # Compact-only / full-only bands.
    show_compact = band.get("compact", True)
    if show_compact is False and ctx.compact:
        return
    if str(show_compact).lower() == "only" and not ctx.compact:
        return
    lines = resolve_layout_band(ctx, band_id, skip_claimed=skip_claimed)
    if not lines:
        return
    sr = ctx.resolved_screenreader() if screenreader is None else bool(screenreader)
    if band.get("band") and not sr:
        label = str(band.get("label") or band_id)
        sheet_w = int(width) if width is not None else ctx.sheet_width()
        body.append(style.sheet_band(label, width=sheet_w))
    body.extend(lines)


def append_unclaimed_slots(
    ctx: SheetContext,
    body: list[str],
    *,
    skip_slots: frozenset[str] | None = None,
    width: int | None = None,
    screenreader: bool | None = None,
) -> None:
    """Append catalog/auto slots this layout has not merged yet.

    This is the future-proof catch-all: a new ``hook:*`` row with a fresh
    ``slot`` (or ``register_auto_field`` with a new slot) shows up here
    without editing ``format_score``. Default assembly slots (header/body)
    stay skipped so custom two-column layouts do not reprint primaries.
    """
    skip = set(skip_slots or ()) | set(_DEFAULT_ASSEMBLY_SLOTS)
    overflow = (_load_profile().get("layout") or {}).get("overflow") or {}
    extra_skip = overflow.get("skip_slots") or []
    skip.update(str(name) for name in extra_skip)
    leftover: list[str] = []
    for slot in catalog_slots():
        if slot in skip or slot in ctx.claimed_slots:
            continue
        leftover.extend(resolve_profile_slot(ctx, slot))
    if not leftover:
        return
    sr = ctx.resolved_screenreader() if screenreader is None else bool(screenreader)
    if overflow.get("band") and not sr:
        label = str(overflow.get("label") or "more")
        sheet_w = int(width) if width is not None else ctx.sheet_width()
        body.append(style.sheet_band(label, width=sheet_w))
    body.extend(leftover)


def resolve_profile_lines(
    ctx: SheetContext,
    *,
    field_ids: frozenset[str] | None = None,
) -> list[str]:
    """Resolve profile rows in catalog order (optional ``field_ids`` filter)."""
    pane = ctx.filter_mode or ctx.pane or "default"
    lines: list[str] = []
    for field_row in _load_profile().get("fields") or []:
        fid = str(field_row.get("id") or "")
        if field_ids is not None and fid not in field_ids:
            continue
        if not _field_applies(field_row, pane):
            continue
        line = _resolve_field(field_row, ctx)
        if line:
            if isinstance(line, list):
                lines.extend(str(item) for item in line if item)
            else:
                lines.append(line)
    return lines


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
            if not _field_applies_compact(field_row, ctx.compact):
                continue
            for line in _coerce_field_lines(_resolve_field(field_row, ctx)):
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
