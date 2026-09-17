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
# Per-heal totals (phase -> {heal_name: ms}). Live 2026-09-11 copyover sat
# on load_world_normalize / load_accounts for ~200s with no named line;
# the next regression should print the actual heal, not a 100s bucket.
_accum_by_phase = {}


def enabled():
    """True when boot profiling is active for this process."""
    return (os.environ.get("RIFTFORGE_BOOT_PROFILE") or "").strip().lower() in (
        "1", "on", "yes", "true",
    )


def reset():
    """Start a new boot profile session (called from Game.__init__)."""
    global _phases, _start, _last, _accum_by_phase
    if not enabled():
        return
    _phases = []
    _accum_by_phase = {}
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


def accum(phase, name, ms):
    """Add *ms* to the named heal under *phase* (no-op when profiling is off)."""
    if not enabled():
        return
    try:
        elapsed = float(ms)
    except (TypeError, ValueError):
        return
    bucket = _accum_by_phase.setdefault(str(phase), {})
    key = str(name)
    bucket[key] = bucket.get(key, 0.0) + elapsed


def format_accum(phase, top=10):
    """Top-N heal names for one boot phase, slowest first."""
    if not enabled():
        return []
    bucket = _accum_by_phase.get(str(phase)) or {}
    if not bucket:
        return [f"[boot_profile] {phase} heals: (none recorded)"]
    rows = sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)
    n = max(1, int(top or 10))
    lines = [f"[boot_profile] {phase} heals (top {n}, ms):"]
    for name, ms in rows[:n]:
        lines.append(f"  {name}: {ms:.1f}")
    return lines


def print_accum(phase, top=10):
    """Emit format_accum lines to stdout."""
    if not enabled():
        return
    for line in format_accum(phase, top=top):
        print(line, flush=True)
