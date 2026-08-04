"""
report_context.py -- diagnostic facts attached to bug/suggest reports.

Keeps engine/ networking-free. Game-specific fields arrive via
``hooks.report_context_extra`` (SUPERS registers in bootstrap).
"""

from __future__ import annotations

import json
import os

_MAX_OCCUPANTS = 15


def _safe_str(value, fallback="?"):
    """Plain string for JSON logs -- never None."""
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _presence_mode(character):
    """Short presence label for triage."""
    if getattr(character, "is_npc", False):
        return "npc"
    if getattr(character, "spirit", False):
        return "spirit"
    if getattr(character, "session", None) is None:
        return "echo"
    if getattr(character, "idle_mode", False):
        return "idlemode"
    return "live"


def _deploy_sha(report_dir):
    """Best-effort live deploy SHA from auto_deploy state beside the DB."""
    if not report_dir:
        return None
    path = os.path.join(report_dir, ".auto_deploy_state.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    last = data.get("last_deploy")
    if not isinstance(last, dict):
        return None
    sha = last.get("sha")
    return _safe_str(sha, "") or None


def _room_occupants(room, reporter):
    """Other bodies in the room (keys), capped for log size."""
    if room is None:
        return [], 0
    try:
        chars = list(room.characters())
    except Exception:
        return [], 0
    from engine.command_support import strip_ephemeral_storage_prefix

    names = []
    for obj in chars:
        if obj is reporter:
            continue
        key = strip_ephemeral_storage_prefix(
            getattr(obj, "key", None) or "?"
        )
        names.append(key)
    names.sort()
    total = len(names)
    if len(names) > _MAX_OCCUPANTS:
        names = names[:_MAX_OCCUPANTS]
        names.append(f"(+{total - _MAX_OCCUPANTS} more)")
    return names, total


def _group_snapshot(character):
    """Follow-bond party summary (engine.group)."""
    try:
        from engine import group as group_mod
    except Exception:
        return {}
    if not group_mod.in_group(character):
        return {"in_group": False}
    members = group_mod.group_members(character)
    leader = group_mod.resolve_leader(character)
    return {
        "in_group": True,
        "leader": _safe_str(getattr(leader, "key", None)),
        "member_count": len(members),
        "members": [
            _safe_str(getattr(m, "key", None))
            for m in members[:_MAX_OCCUPANTS]
        ],
    }


def build(character, game):
    """Return a JSON-serializable dict of triage facts for a filed report."""
    if character is None:
        return {}

    from engine import display_prefs
    from engine import room_vnum as room_vnum_mod
    from engine import hooks
    from engine.command_support import strip_ephemeral_storage_prefix

    display_prefs.ensure_display_defaults(character)

    room = getattr(character, "location", None)
    occupants, occupant_count = _room_occupants(room, character)
    report_dir = getattr(game, "report_dir", ".") if game is not None else "."

    following = getattr(character, "following", None)
    staff_tail = getattr(character, "staff_tailing", None)
    combat_target = getattr(character, "target", None)

    ctx = {
        "character": {
            "key": _safe_str(getattr(character, "key", None)),
            "presence": _presence_mode(character),
            "gm_rank": getattr(character, "gm_rank", None),
            "criminal": bool(getattr(character, "criminal", False)),
            "peaceful": bool(getattr(character, "peaceful", False)),
        },
        "room": {
            "staff_line": room_vnum_mod.describe_actor_room(
                character, staff=True,
            ),
            "key": _safe_str(getattr(room, "key", None) if room else None),
            "title": _safe_str(getattr(room, "title", None) if room else None),
            "vnum": _safe_str(getattr(room, "vnum", None) if room else None, ""),
            "zone": _safe_str(getattr(room, "zone", None) if room else None, ""),
            "occupant_count": occupant_count,
            "occupants": occupants,
        },
        "session": {
            "idle_mode": bool(getattr(character, "idle_mode", False)),
            "spirit": bool(getattr(character, "spirit", False)),
            "in_vehicle": getattr(character, "in_vehicle", None),
            "following": (
                _safe_str(getattr(following, "key", None))
                if following is not None else None
            ),
            "staff_tailing": (
                _safe_str(getattr(staff_tail, "key", None))
                if staff_tail is not None else None
            ),
            "companion_leader_key": getattr(
                character, "companion_leader_key", None,
            ),
        },
        "combat": {
            "target": (
                _safe_str(getattr(combat_target, "key", None))
                if combat_target is not None else None
            ),
            "mutual_focus": (
                getattr(combat_target, "target", None) is character
                if combat_target is not None else None
            ),
            "sparring": bool(getattr(character, "sparring", False)),
            "combat_stance": _safe_str(
                getattr(character, "combat_stance", None), "balanced",
            ),
            "combat_style": _safe_str(
                getattr(character, "combat_style", None), "",
            ) or None,
            "auto_combat_style": _safe_str(
                getattr(character, "auto_combat_style", None), "",
            ) or None,
        },
        "group": _group_snapshot(character),
        "display": {
            "screenreader": bool(getattr(character, "screenreader", False)),
            "color_depth": _safe_str(
                getattr(character, "color_depth", None), "ansi",
            ),
            "show_combat_tags": bool(
                getattr(character, "show_combat_tags", True),
            ),
            "show_tips": bool(getattr(character, "show_tips", True)),
            "combat_numbers": bool(getattr(character, "combat_numbers", False)),
            "combat_diag": bool(getattr(character, "combat_diag", False)),
            "fightlog_enabled": bool(
                getattr(character, "fightlog_enabled", False),
            ),
        },
        "game": {
            "tick": int(getattr(game, "game_time_ticks", 0) or 0)
            if game is not None else 0,
            "deploy_sha": _deploy_sha(report_dir),
        },
    }

    combat_aim = getattr(character, "combat_aim", None)
    if combat_aim:
        ctx["combat"]["aim"] = _safe_str(combat_aim)
    if combat_target is not None:
        try:
            mom = float(getattr(character, "momentum", 0.0) or 0.0)
            ctx["combat"]["momentum"] = round(mom, 1)
        except (TypeError, ValueError):
            pass

    # Drop empty combat-style keys (most fighters have neither set).
    for style_key in ("combat_style", "auto_combat_style"):
        if not ctx["combat"].get(style_key):
            ctx["combat"].pop(style_key, None)

    # Strip empty optional strings for a tighter log.
    if not ctx["room"]["vnum"]:
        del ctx["room"]["vnum"]
    if not ctx["room"]["zone"]:
        del ctx["room"]["zone"]

    extra = hooks.report_context_extra(character, game)
    if extra:
        ctx["gameplay"] = extra
    return ctx
