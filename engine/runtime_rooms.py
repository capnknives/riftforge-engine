"""
runtime_rooms.py -- registry for persistable rooms not in map JSON.

Hand-authored map/zone rooms load before ``load_world``.  Systems that
create ``Room`` objects at runtime (vehicle interiors, charter cabins, …)
must register a **pre-load ensure** so saved ``room_key`` / VNUM values
resolve to real rooms instead of ``map_missing_stub`` placeholders
(see ``persistence._resolve_saved_room``).

Game packages register hooks via :func:`register_pre_load_room_ensure`.
``server.Game`` calls :func:`ensure_runtime_rooms_before_load`` after
SQLite-backed room rebuilds and before ``load_world``.

Engine-pure: no ``supers`` imports.
"""

from __future__ import annotations

from collections.abc import Callable

# (name, fn) in registration order -- deterministic boot.
_PRE_LOAD_ENSURES: list[tuple[str, Callable]] = []


def register_pre_load_room_ensure(name: str, fn: Callable) -> None:
    """Register ``fn(game)`` to run before ``load_world`` places characters.

    ``name`` must be unique; re-registering the same name replaces the
    callable (idempotent hot-reload / test overrides).
    """
    text = str(name or "").strip()
    if not text:
        raise ValueError("pre_load room ensure name must be non-empty")
    if not callable(fn):
        raise TypeError(f"pre_load room ensure {text!r} must be callable")
    for idx, (existing, _) in enumerate(_PRE_LOAD_ENSURES):
        if existing == text:
            _PRE_LOAD_ENSURES[idx] = (text, fn)
            return
    _PRE_LOAD_ENSURES.append((text, fn))


def registered_pre_load_room_ensure_names() -> tuple[str, ...]:
    """Registered ensure names in boot order (for smoke / audits)."""
    return tuple(name for name, _ in _PRE_LOAD_ENSURES)


def ensure_runtime_rooms_before_load(game) -> None:
    """Run every registered pre-load ensure (fail loud on error)."""
    if not _PRE_LOAD_ENSURES:
        print(
            "[runtime_rooms] no pre_load ensures registered -- "
            "runtime persistable rooms may stub on load",
            flush=True,
        )
        return
    for name, fn in _PRE_LOAD_ENSURES:
        fn(game)


def reset_pre_load_room_ensures_for_tests() -> None:
    """Clear the registry (unit/smoke tests only)."""
    _PRE_LOAD_ENSURES.clear()
