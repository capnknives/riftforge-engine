"""
report_context.py -- diagnostic facts attached to bug/suggest reports.

Keeps engine/ networking-free. Game-specific fields arrive via
``hooks.report_context_extra`` (SUPERS registers in bootstrap).

Bugs get the full modular dump. Suggestions, typos, and help proposals
keep a slim identity/location snapshot so staff and the implementer
webhook are not buried in combat/Cadence/loadout walls.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque

_MAX_OCCUPANTS = 15
_OUTPUT_RING_MAX = 8
_OUTPUT_LINE_MAX = 240

# Report kinds that must not attach the full diagnostic blob.
_MINIMAL_KINDS = frozenset(("suggest", "help", "typo"))

# Gameplay keys worth keeping on a suggestion (who/where, not meters).
_SLIM_GAMEPLAY_KEYS = (
    "origin", "path", "background", "tier",
    "home_zone", "away_from_home_zone",
)


def _note_context_error(errors, label, exc):
    """Append a short failure tag for bug-report triage."""
    if errors is None:
        return
    errors.append(f"{label}: {exc!r}")


def _context_snap(errors, label, fn, default=None):
    """Run a snapshot helper; record failure without aborting the report."""
    try:
        return fn()
    except Exception as exc:
        _note_context_error(errors, label, exc)
        return default


def _safe_str(value, fallback="?"):
    """Plain string for JSON logs -- never None."""
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _serialize_pos(value):
    """JSON-friendly macro/micro coord pair."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value)


def note_report_output(session, message):
    """Stamp one client-visible line onto the bug-report output ring.

    Skips blank coalesced lines. Truncates long help/look dumps so a
    report stays readable. Safe to call from FakeSession.send in smokes.
    ANSI escapes are stripped so triage logs stay plain-text readable.
    """
    if session is None:
        return
    try:
        from engine.style import strip_ansi

        text = strip_ansi(str(message or "")).strip()
    except Exception:
        text = str(message or "").strip()
    if not text:
        return
    if len(text) > _OUTPUT_LINE_MAX:
        text = text[: _OUTPUT_LINE_MAX - 1] + "…"
    ring = getattr(session, "_report_output_ring", None)
    if ring is None:
        ring = deque(maxlen=_OUTPUT_RING_MAX)
        session._report_output_ring = ring
    try:
        ring.append(text)
    except Exception:
        pass


def note_verb_gate(character, verb, code, *, detail=None):
    """Stamp the last dispatch refusal onto the login Session.

    Bug reports read ``session.last_verb_gate`` so triage can see *why* a
    verb closed without re-deriving asleep/frozen/KO/cage gates from prose.
    """
    if character is None or not code:
        return
    sess = getattr(character, "session", None)
    if sess is None:
        return
    verb_text = str(verb or getattr(sess, "last_command", None) or "")[:80]
    entry = {
        "verb": verb_text,
        "code": str(code)[:120],
    }
    if detail:
        entry["detail"] = str(detail)[:200]
    sess._last_verb_gate = entry


def note_last_look(character, room):
    """Stamp the room a bare ``look`` just rendered (bug report 981 class).

    After a highway fill or copyover, look can describe a different room
    than ``character.location``. Bug reports compare this key to the
    live location so triage does not need a live repro.
    """
    if character is None:
        return
    sess = getattr(character, "session", None)
    if sess is None:
        return
    key = getattr(room, "key", None) if room is not None else None
    sess._last_look_room_key = str(key) if key else None
    title = getattr(room, "title", None) if room is not None else None
    if title:
        sess._last_look_room_title = str(title)[:120]


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


def _auto_deploy_state(report_dir):
    """Parsed ``.auto_deploy_state.json`` beside the DB, or ``{}``."""
    if not report_dir:
        return {}
    path = os.path.join(report_dir, ".auto_deploy_state.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _sha_or_none(value):
    """Strip a git SHA string, or None when empty."""
    return _safe_str(value, "") or None


def _same_sha(left, right):
    """True when both look like the same commit (ignore case / whitespace)."""
    if not left or not right:
        return False
    return str(left).strip().lower() == str(right).strip().lower()


def _deploy_sha(report_dir):
    """Last Fix/Ship overlay SHA (``last_deploy``). Not the tracked tip.

    Feature merges advance ``origin_main`` without rewriting this field --
    see ``_tracked_tip_sha``.
    """
    last = _auto_deploy_state(report_dir).get("last_deploy")
    if not isinstance(last, dict):
        return None
    return _sha_or_none(last.get("sha"))


def _tracked_tip_sha(report_dir):
    """SHA auto-deploy last recorded as origin/main (silent advances included).

    ``last_deploy`` is only the last Fix overlay. Comparing HEAD to that
    false-positives ``overlay_dirty`` after every later feature merge.
    Fall back to last_deploy when origin_main is missing (old state files).
    """
    tip = _sha_or_none(_auto_deploy_state(report_dir).get("origin_main"))
    if tip:
        return tip
    return _deploy_sha(report_dir)


def _head_sha_cheap(report_dir):
    """Read ``.git/HEAD`` without spawning git (bug reports must stay cheap)."""
    roots = []
    if report_dir:
        roots.append(report_dir)
    roots.append(os.getcwd())
    seen = set()
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        git_dir = os.path.join(root, ".git")
        head_path = os.path.join(git_dir, "HEAD")
        try:
            with open(head_path, encoding="utf-8") as fh:
                ref = fh.read().strip()
        except OSError:
            continue
        if not ref:
            continue
        if ref.startswith("ref:"):
            ref_path = os.path.join(git_dir, ref[4:].strip())
            try:
                with open(ref_path, encoding="utf-8") as fh:
                    sha = fh.read().strip()
            except OSError:
                continue
        else:
            sha = ref
        if sha:
            return sha
    return None


def _copyover_abort_snap():
    """Last cancelled Veil rewrite, if the abort log is on disk."""
    path = None
    try:
        from engine import copyover as copyover_mod

        path = copyover_mod.abort_log_path()
    except Exception:
        path = os.path.join(os.getcwd(), ".copyover_last_abort.json")
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    at = data.get("at")
    if at:
        out["at"] = str(at)
    reason = data.get("reason")
    if reason:
        out["reason"] = str(reason)[:200]
    detail = (data.get("detail") or "").strip()
    if detail:
        out["detail"] = detail[:240]
    return out or None


def _persist_helpers_missing():
    """Names of save/copyover helpers this process does not have yet.

    Partial auto-deploy overlays land new persist_blob / skills_sheet
    before the matching helper exists in ``sys.modules``. A missing
    name here is the 960 / 968 / stale-taxi-blob class.
    """
    from engine import hooks as hooks_mod

    return list(hooks_mod.report_persist_helper_gaps())


def _tick_health_snapshot(game):
    """Compact heartbeat / autosave facts for lag and auto-diag triage."""
    if game is None:
        return None
    out = {}
    ring = list(getattr(game, "_tick_stats", ()) or ())
    if ring:
        totals = [float(s.get("total_ms", 0) or 0) for s in ring]
        last = ring[-1]
        out["last_tick_ms"] = round(float(last.get("total_ms", 0) or 0), 1)
        out["avg_tick_ms"] = round(sum(totals) / len(totals), 1)
        out["worst_tick_ms"] = round(max(totals), 1)
        out["tick_samples"] = len(ring)
        slow = last.get("slow") or []
        if slow:
            out["last_slow_handlers"] = [
                {"name": str(name), "ms": round(float(ms or 0), 1)}
                for name, ms in slow[:6]
            ]
    meta = getattr(game, "_cadence_budget_meta", None) or {}
    if meta:
        used = meta.get("used")
        limit = meta.get("limit")
        if used is not None:
            out["cadence_used"] = used
        if limit is not None:
            out["cadence_limit"] = limit
    autosave = getattr(game, "_last_autosave_stats", None) or {}
    if autosave:
        save_ms = autosave.get("save_ms")
        if save_ms is not None:
            out["last_autosave_ms"] = save_ms
        if autosave.get("skipped"):
            out["last_autosave_skipped"] = True
    return out or None


def _runtime_snapshot(game, report_dir):
    """Always-on copyover / overlay honesty for every full bug report.

    ``overlay_dirty`` means git HEAD is behind the tracked tip
    (``origin_main`` in auto-deploy state). That is "overlay landed,
    process still old" -- not "the last Fix SHA is older than HEAD,"
    which is normal after silent feature merges.
    """
    out = {}
    deploy = _deploy_sha(report_dir)
    if deploy:
        out["deploy_sha"] = deploy
    tip = _tracked_tip_sha(report_dir)
    if tip:
        out["origin_main_sha"] = tip
    head = _head_sha_cheap(report_dir)
    if head:
        out["head_sha"] = head
    # Process still old: checkout is not at the tracked origin/main tip.
    if head and tip and not _same_sha(head, tip):
        out["overlay_dirty"] = True
    from engine.report_debug import (
        copyover_abort_is_fresh,
        last_copyover_ok_snap,
    )

    abort = _copyover_abort_snap()
    # Stale abort stamps (days old) polluted every report -- gate on age.
    if abort and copyover_abort_is_fresh(abort):
        out["last_copyover_abort"] = abort
    ok_snap = last_copyover_ok_snap(report_dir)
    if ok_snap:
        out["last_copyover_ok"] = ok_snap
    try:
        from engine import copyover as copyover_mod

        ready_path = copyover_mod._ready_path()
        if os.path.isfile(ready_path):
            out["copyover_ready"] = True
            try:
                age = max(0, int(time.time() - os.path.getmtime(ready_path)))
                out["copyover_ready_age_s"] = age
            except OSError:
                pass
    except Exception:
        pass
    missing = _persist_helpers_missing()
    if missing:
        out["persist_helpers_missing"] = missing
        out["partial_overlay"] = True
    if game is not None:
        try:
            out["tick"] = int(getattr(game, "game_time_ticks", 0) or 0)
        except (TypeError, ValueError):
            pass
        tick_health = _tick_health_snapshot(game)
        if tick_health:
            out["tick_health"] = tick_health
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _room_occupants(room, reporter, context_errors=None):
    """Other bodies in the room (keys), capped for log size."""
    if room is None:
        return [], 0
    try:
        chars = list(room.characters())
    except Exception as exc:
        _note_context_error(context_errors, "room_occupants", exc)
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


def _group_snapshot(character, context_errors=None):
    """Follow-bond party summary (engine.group)."""
    try:
        from engine import group as group_mod
    except Exception as exc:
        _note_context_error(context_errors, "group_import", exc)
        return {}
    from engine.report_debug import last_group_event_snap

    last_event = last_group_event_snap(character)
    if not group_mod.in_group(character):
        out = {"in_group": False}
        if last_event:
            out["last_event"] = last_event
        return out
    members = group_mod.group_members(character)
    leader = group_mod.resolve_leader(character)
    out = {
        "in_group": True,
        "leader": _safe_str(getattr(leader, "key", None)),
        "member_count": len(members),
        "members": [
            _safe_str(getattr(m, "key", None))
            for m in members[:_MAX_OCCUPANTS]
        ],
    }
    if last_event:
        out["last_event"] = last_event
    return out


def _is_minimal_kind(kind):
    """True when this filing kind should skip the full diagnostic dump."""
    token = str(kind or "").strip().lower()
    return token in _MINIMAL_KINDS


def build_minimal(character, game):
    """Identity + location only -- used for suggestions / typos / help ideas.

    Staff still need to know *who* filed and *where* they were standing.
    Combat scene, Cadence trails, vitals, loadout, and modular gameplay
    dumps stay on bugs.
    """
    if character is None:
        return {}

    from engine import room_vnum as room_vnum_mod

    room = getattr(character, "location", None)
    report_dir = getattr(game, "report_dir", ".") if game is not None else "."
    ctx = {
        "report_kind": "minimal",
        "character": {
            "key": _safe_str(getattr(character, "key", None)),
            "presence": _presence_mode(character),
        },
        "room": {
            "staff_line": room_vnum_mod.describe_actor_room(
                character, staff=True,
            ),
        },
        "game": {
            "tick": int(getattr(game, "game_time_ticks", 0) or 0)
            if game is not None else 0,
            "deploy_sha": _deploy_sha(report_dir),
        },
    }
    title = _safe_str(getattr(room, "title", None) if room else None, "")
    if title:
        ctx["room"]["title"] = title
    zone = _safe_str(getattr(room, "zone", None) if room else None, "")
    if zone:
        ctx["room"]["zone"] = zone
    gm_rank = getattr(character, "gm_rank", None)
    if gm_rank:
        ctx["character"]["gm_rank"] = gm_rank

    gameplay = {}
    for attr in ("origin", "path", "background"):
        value = getattr(character, attr, None)
        if value:
            gameplay[attr] = value
    try:
        tier = int(getattr(character, "tier", 0) or 0)
    except (TypeError, ValueError):
        tier = 0
    if tier:
        gameplay["tier"] = tier
    home_zone = getattr(character, "home_zone", None)
    current_zone = getattr(room, "zone", None) if room is not None else None
    if home_zone:
        gameplay["home_zone"] = home_zone
        gameplay["away_from_home_zone"] = bool(
            current_zone and home_zone != current_zone
        )
    if gameplay:
        ctx["gameplay"] = gameplay
    if not ctx["game"].get("deploy_sha"):
        ctx["game"].pop("deploy_sha", None)
    return ctx


def slim_suggestion_context(context):
    """Reduce a stored diagnostic blob to suggestion-sized identity/location.

    New filings already use ``build_minimal``. This helper also trims
    older fat ``suggestions.log`` rows when GM ``sendsuggest`` POSTs them
    to the implementer webhook.
    """
    if not isinstance(context, dict):
        return {}
    char = context.get("character") if isinstance(context.get("character"), dict) else {}
    room = context.get("room") if isinstance(context.get("room"), dict) else {}
    game = context.get("game") if isinstance(context.get("game"), dict) else {}
    gameplay = context.get("gameplay") if isinstance(context.get("gameplay"), dict) else {}
    out = {
        "report_kind": "minimal",
        "character": {},
        "room": {},
        "game": {},
    }
    for key in ("key", "presence", "gm_rank"):
        if char.get(key) not in (None, ""):
            out["character"][key] = char[key]
    for key in ("staff_line", "title", "zone"):
        if room.get(key) not in (None, ""):
            out["room"][key] = room[key]
    if game.get("tick") is not None:
        out["game"]["tick"] = game["tick"]
    if game.get("deploy_sha"):
        out["game"]["deploy_sha"] = game["deploy_sha"]
    slim_gp = {
        key: gameplay[key]
        for key in _SLIM_GAMEPLAY_KEYS
        if gameplay.get(key) not in (None, "")
    }
    if slim_gp:
        out["gameplay"] = slim_gp
    account = context.get("account")
    if account:
        out["account"] = account
    return {k: v for k, v in out.items() if v}


def build(character, game, *, history=None, description=None, kind=None):
    """Return a JSON-serializable dict of triage facts for a filed report.

    Optional ``history`` (session command ring) and ``description`` (bug text)
    select which SUPERS diagnostic modules attach via report_modules.

    ``kind`` of suggest / typo / help skips the modular dump and returns
    ``build_minimal`` instead.
    """
    if character is None:
        return {}

    if _is_minimal_kind(kind):
        return build_minimal(character, game)

    from engine import display_prefs
    from engine import room_vnum as room_vnum_mod
    from engine import hooks
    from engine.command_support import strip_ephemeral_storage_prefix

    display_prefs.ensure_display_defaults(character)

    context_errors = []
    room = getattr(character, "location", None)
    occupants, occupant_count = _room_occupants(
        room, character, context_errors,
    )
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
        "group": _group_snapshot(character, context_errors),
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
    runtime = _runtime_snapshot(game, report_dir)
    if runtime:
        # Keep deploy_sha on game for older triage tools; runtime adds
        # overlay / copyover honesty beside it.
        ctx["game"]["runtime"] = runtime
        if runtime.get("deploy_sha") and not ctx["game"].get("deploy_sha"):
            ctx["game"]["deploy_sha"] = runtime["deploy_sha"]

    for flag in ("gm_mode", "gm_spirit", "gm_away"):
        if getattr(character, flag, False):
            ctx["character"][flag] = True

    if room is not None:
        if getattr(room, "dark", False):
            ctx["room"]["dark"] = True
            try:
                ctx["room"]["can_see_in_dark"] = bool(
                    hooks.can_see_in_dark(character, room),
                )
            except Exception as exc:
                _note_context_error(context_errors, "can_see_in_dark", exc)
        plane = getattr(room, "plane", None)
        if plane:
            ctx["room"]["plane"] = str(plane)
        legacy = getattr(room, "legacy_key", None)
        if legacy:
            ctx["room"]["legacy_key"] = str(legacy)

    sess = getattr(character, "session", None)
    if sess is not None:
        last_cmd = getattr(sess, "last_command", None)
        if last_cmd:
            ctx["session"]["last_command"] = str(last_cmd)[:200]
        ring = getattr(sess, "_report_output_ring", None)
        if ring:
            ctx["session"]["last_output"] = list(ring)[-_OUTPUT_RING_MAX:]
        gate = getattr(sess, "_last_verb_gate", None)
        if isinstance(gate, dict) and gate:
            ctx["session"]["last_verb_gate"] = gate
        last_look_key = getattr(sess, "_last_look_room_key", None)
        if last_look_key:
            ctx["session"]["last_look_room_key"] = str(last_look_key)
            loc_key = getattr(room, "key", None) if room is not None else None
            if loc_key and str(loc_key) != str(last_look_key):
                ctx["session"]["look_location_mismatch"] = True
        last_look_title = getattr(sess, "_last_look_room_title", None)
        if last_look_title:
            ctx["session"]["last_look_room_title"] = str(last_look_title)[:120]
        if getattr(sess, "playcast_gm_tools", False):
            ctx["session"]["playcast_gm_tools"] = True
        staff_acct = getattr(sess, "staff_account", None)
        if staff_acct:
            ctx["session"]["staff_account"] = str(staff_acct)
        occupy_acct = getattr(character, "staff_occupy_account", None)
        if occupy_acct:
            ctx["session"]["staff_occupy_account"] = str(occupy_acct)
        try:
            from engine.command_support import _is_gm

            if _is_gm(character):
                ctx["session"]["effective_gm"] = True
        except Exception:
            pass

    pos = {}
    macro = getattr(character, "macro_pos", None)
    micro = getattr(character, "micro_pos", None)
    if macro:
        pos["macro_pos"] = _serialize_pos(macro)
    if micro:
        pos["micro_pos"] = _serialize_pos(micro)
    if macro or micro:
        try:
            from engine.systems.overland import overland_mode

            pos["overland_mode"] = overland_mode(character)
        except Exception as exc:
            _note_context_error(context_errors, "overland_mode", exc)
    if pos:
        ctx["position"] = pos

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

    extra = hooks.report_context_extra(
        character, game, history=history, description=description,
    )
    if extra:
        if isinstance(extra, dict):
            gameplay_errors = extra.pop("context_errors", None)
            if gameplay_errors:
                context_errors.extend(gameplay_errors)
            ctx["gameplay"] = extra
        else:
            context_errors.append("gameplay: non-dict extra from hook")
    if context_errors:
        ctx["context_errors"] = context_errors

    from engine.report_debug import last_signature_snap, last_vehicle_snap

    sess = getattr(character, "session", None)
    sig = last_signature_snap(sess)
    veh = last_vehicle_snap(character)
    if sig or veh:
        gameplay = ctx.setdefault("gameplay", {})
        if sig:
            gameplay["last_signature"] = sig
        if veh:
            gameplay["last_vehicle"] = veh

    return ctx
