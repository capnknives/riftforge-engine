"""
boot_profile.py -- optional cold-boot phase timing (env-gated).

Set ``RIFTFORGE_BOOT_PROFILE=1`` before ``Game(...)`` to record wall-clock
ms between ``boot_profile.mark()`` calls in ``server.py``. The CLI wrapper
``tools/boot_profile.py`` sets this automatically.

No overhead when unset (marks are no-ops).
"""

from __future__ import annotations

import os
import time

_phases = []
_start = 0.0
_last = 0.0


def enabled():
    """True when boot profiling is active for this process."""
    return (os.environ.get("RIFTFORGE_BOOT_PROFILE") or "").strip().lower() in (
        "1", "on", "yes", "true",
    )


def reset():
    """Start a new boot profile session (called from Game.__init__)."""
    global _phases, _start, _last
    if not enabled():
        return
    _phases = []
    _start = _last = time.perf_counter()


def mark(name):
    """Record elapsed ms since the previous mark (or reset)."""
    global _last
    if not enabled():
        return
    now = time.perf_counter()
    _phases.append((str(name), (now - _last) * 1000.0))
    _last = now


def format_report():
    """Human-readable phase table for CLI / logs."""
    if not enabled():
        return []
    if not _phases:
        return ["[boot_profile] (no phases recorded)"]
    total = (time.perf_counter() - _start) * 1000.0
    lines = ["[boot_profile] cold boot phases (ms):"]
    for name, ms in _phases:
        lines.append(f"  {name}: {ms:.1f}")
    lines.append(f"  TOTAL: {total:.1f}")
    return lines


def print_report():
    """Emit format_report lines to stdout."""
    if not enabled():
        return
    for line in format_report():
        print(line, flush=True)
