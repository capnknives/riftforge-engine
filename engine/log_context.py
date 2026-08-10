"""
log_context.py -- per-command correlation fields for tagged stderr.

Single-threaded asyncio: one command runs to completion before the next
starts, so a module-level slot is enough. ``commands.dispatch`` sets
context at entry and clears in ``finally``.
"""

from __future__ import annotations

_ctx = {
    "tick": None,
    "sid": None,
    "actor": None,
}


def set_command_context(*, game=None, session=None, character=None):
    """Stamp tick / session / actor for the current command."""
    tick = None
    if game is not None:
        tick = int(getattr(game, "game_time_ticks", 0) or 0)
    sid = None
    if session is not None:
        sid = getattr(session, "gateway_session_id", None)
        if not sid:
            sid = getattr(session, "_log_session_id", None)
    actor = getattr(character, "key", None) if character is not None else None
    _ctx["tick"] = tick
    _ctx["sid"] = sid
    _ctx["actor"] = actor


def clear_command_context():
    """Drop correlation fields after a command finishes."""
    _ctx["tick"] = None
    _ctx["sid"] = None
    _ctx["actor"] = None


def correlation_suffix():
    """Space-prefixed tail for ``log_util.ops`` lines, or '' when unset."""
    parts = []
    if _ctx.get("tick") is not None:
        parts.append(f"tick={_ctx['tick']}")
    sid = _ctx.get("sid")
    if sid:
        parts.append(f"sid={sid}")
    actor = _ctx.get("actor")
    if actor:
        parts.append(f"actor={actor}")
    if not parts:
        return ""
    return " " + " ".join(parts)
