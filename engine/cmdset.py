"""
cmdset.py -- optional stacked command tables merged by priority.

Games register named cmdsets (``register_cmdset``) with an optional matcher
callable ``(character, room) -> bool``. Dispatch asks ``lookup_command`` for
the active merge before falling back to the flat ``COMMANDS`` table.

When nothing is registered, lookup returns None and dispatch is unchanged --
this module is purely additive.
"""

from __future__ import annotations

from typing import Callable, Optional

# id -> {commands: dict, priority: int, matcher: callable|None}
_REGISTRY: dict[str, dict] = {}


def register_cmdset(
    cmdset_id: str,
    commands_dict: dict,
    *,
    priority: int = 0,
    matcher: Optional[Callable] = None,
):
    """Register or replace one named cmdset.

    ``commands_dict`` uses the same ``{verb: (handler, help_text)}`` shape as
    ``COMMANDS``. Higher ``priority`` wins on key collision when multiple
    active cmdsets overlap. ``matcher(character, room)`` gates activation;
    when omitted, the cmdset is always eligible (rare -- prefer a matcher).
    """
    _REGISTRY[cmdset_id] = {
        "commands": dict(commands_dict or {}),
        "priority": int(priority),
        "matcher": matcher,
    }


def unregister_cmdset(cmdset_id: str) -> None:
    """Drop one registration (tests / hot reload)."""
    _REGISTRY.pop(cmdset_id, None)


def clear_cmdsets() -> None:
    """Remove every registration (engine smoke / unit tests)."""
    _REGISTRY.clear()


def active_cmdsets(character, room):
    """Return merged ``{verb: (handler, help_text)}`` for active cmdsets.

    Sort registered cmdsets by ascending priority so later (higher) entries
    overwrite earlier keys -- stable dict merge semantics.
    """
    merged = {}
    # Snapshot values so a matcher cannot mutate the registry mid-merge.
    entries = sorted(
        _REGISTRY.values(),
        key=lambda row: int(row.get("priority") or 0),
    )
    for row in entries:
        matcher = row.get("matcher")
        if matcher is not None:
            try:
                if not matcher(character, room):
                    continue
            except Exception:
                # A broken matcher must not break dispatch for everyone.
                continue
        merged.update(row.get("commands") or {})
    return merged


def lookup_command(character, room, verb: str):
    """Return a ``(handler, help_text)`` tuple or None.

  Checked by ``commands.dispatch`` before the global ``COMMANDS`` table.
    """
    if not verb:
        return None
    entry = active_cmdsets(character, room).get(verb)
    if entry is None:
        return None
    return entry
