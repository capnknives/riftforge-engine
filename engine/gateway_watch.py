"""
gateway_watch.py -- paths that force a client disconnect on deploy.

``watch_and_run`` and ``auto_deploy`` share this list so in-game Veil
countdowns can warn when a deploy tears down the binding Veil instead of
a game-only copyover (gateway holds TCP across game-child restart).

Only **long-lived gateway-process** modules belong here — not
``engine/gateway_client.py`` (game-side IPC; game-child copyover is
enough). ``engine/watch_and_run.py`` is included because a watcher
re-exec kills the gateway child the same way a gateway-module edit does.
"""

from __future__ import annotations

import os

# Normpath keys — gateway parent process modules plus the watcher entrypoint.
GATEWAY_RESTART_PATHS = frozenset({
    os.path.normpath("engine/gateway.py"),
    os.path.normpath("engine/gateway_protocol.py"),
    os.path.normpath("engine/watch_and_run.py"),
})


def normalize_repo_path(path: str) -> str:
    """Normalize a git-relative path for set membership."""
    return os.path.normpath((path or "").replace("\\", "/"))


def paths_touch_gateway_restart(paths) -> bool:
    """True when any path in *paths* requires killing the gateway child."""
    for raw in paths or ():
        if normalize_repo_path(raw) in GATEWAY_RESTART_PATHS:
            return True
    return False


def snapshot_touches_gateway_restart(before: dict, after: dict) -> bool:
    """True when a watcher mtime snapshot delta hits gateway modules."""
    keys = set(before) | set(after)
    for path in keys:
        if normalize_repo_path(path) not in GATEWAY_RESTART_PATHS:
            continue
        if before.get(path) != after.get(path):
            return True
    return False
