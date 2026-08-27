"""
boot_kind.py -- cold compose start vs game-only reload.

Evennia splits Portal (connections) from Server (game). ``reload`` restarts
only the Server; ``reboot`` kills both. Circle/Diku ``copyover_recover``
loads the world, restores descriptors, and enters the game loop — zone
resets run on the pulse, not during boot.

Riftforge mapping:

- **cold** — gateway just started (compose up, gateway bounce). Run content
  ensures and remaining one-time migrations.
- **reload** — gateway stayed up; watcher respawned the game child (auto-
  deploy, SIGUSR1, crash, ``gm recover restart``). Load the saved world,
  repair occupants, open look/who. Do not rescan historical blobs.

The watcher stamps ``RIFTFORGE_BOOT_KIND`` on the game child. The gateway
welcome CTRL repeats ``kind`` so deferred seed still knows after IPC
connect. Tests may set ``game._boot_kind`` or ``_gateway_copyover_reattach``.
"""

from __future__ import annotations

import os


ENV_KEY = "RIFTFORGE_BOOT_KIND"
COLD = "cold"
RELOAD = "reload"

_RELOAD_ALIASES = frozenset({
    "reload", "copyover", "game", "game-only", "game_only",
})
_COLD_ALIASES = frozenset({
    "cold", "compose", "full", "reboot",
})


def parse_boot_kind(raw, *, default=COLD):
    """Return ``cold`` or ``reload`` from an env / CTRL string."""
    text = str(raw or "").strip().lower()
    if text in _RELOAD_ALIASES:
        return RELOAD
    if text in _COLD_ALIASES:
        return COLD
    return default if default in (COLD, RELOAD) else COLD


def from_env(*, default=COLD):
    """Read ``RIFTFORGE_BOOT_KIND`` (unset → ``default``, usually cold)."""
    return parse_boot_kind(os.environ.get(ENV_KEY), default=default)


def is_reload_boot(game=None):
    """True when the watcher stamped this child as a game-only reload.

    Does **not** use ``_gateway_copyover_reattach`` — that flag only means
    held clients are waiting on the veil. Planned copyover also writes
    ``.copyover_skip_deferred``; crash / empty-who reloads use this env.
    """
    if game is not None:
        stamped = getattr(game, "_boot_kind", None)
        if stamped == RELOAD:
            return True
        if stamped == COLD:
            return False
    return from_env() == RELOAD
