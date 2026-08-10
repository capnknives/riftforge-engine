"""
log_util.py -- tagged stderr lines for server ops (Docker logs).

Riftforge does not use stdlib ``logging`` for the game process. Ops and
agents grep container stderr for ``[tag]`` prefixes. This module keeps
that shape consistent and offers once-per-tick rate limiting for hot
paths (combat prose, hooks) so a broken helper cannot spam every heartbeat.
"""

from __future__ import annotations

import sys
import traceback


def ops(tag, message, *, exc=None):
    """Print one tagged ops line to stderr; optional exception traceback."""
    from engine import log_context

    suffix = log_context.correlation_suffix()
    text = f"[{tag}] {message}{suffix}"
    print(text, file=sys.stderr, flush=True)
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)


def ops_once_per_tick(game, key, tag, message, *, exc=None):
    """Like ``ops`` but at most once per ``(game_time_ticks, key)``."""
    if game is None:
        ops(tag, message, exc=exc)
        return
    tick = int(getattr(game, "game_time_ticks", 0) or 0)
    seen = getattr(game, "_ops_log_seen", None)
    if seen is None:
        seen = {}
        game._ops_log_seen = seen
    dedupe = (tick, key)
    if seen.get(dedupe):
        return
    seen[dedupe] = True
    # Drop entries from prior ticks so the dict cannot grow without bound.
    if len(seen) > 256:
        game._ops_log_seen = {
            k: v for k, v in seen.items() if k[0] == tick
        }
    ops(tag, message, exc=exc)
