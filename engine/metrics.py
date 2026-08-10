"""
metrics.py -- in-process ops counters on ``Game.metrics``.

Lightweight process-local tallies for staff ``gm metrics`` and future
watcher tooling. Not Prometheus -- stdlib-only (logging audit Phase 4).
"""

from __future__ import annotations


def ensure(game):
    """Return the mutable metrics dict on ``game``, creating when needed."""
    metrics = getattr(game, "metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
        game.metrics = metrics
    return metrics


def bump(game, key, amount=1):
    """Increment one named counter (no-op when ``game`` is None)."""
    if game is None or not key:
        return
    metrics = ensure(game)
    metrics[key] = int(metrics.get(key, 0) or 0) + int(amount)


def snapshot(game):
    """Sorted copy of counters for display / webhook payloads."""
    if game is None:
        return {}
    return dict(sorted(ensure(game).items()))


def format_report(game):
    """Plain lines for ``gm metrics``."""
    data = snapshot(game)
    if not data:
        return ["(no counters yet)"]
    lines = []
    for key, value in data.items():
        lines.append(f"{key}: {value}")
    return lines
