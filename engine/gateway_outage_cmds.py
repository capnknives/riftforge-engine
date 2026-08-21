"""gateway_outage_cmds.py -- static staff help while game IPC is down.

The gateway process must not import ``supers/``; these strings and thin
engine helpers are what ``engine.gateway`` serves during stitch mode
(copyover, crash, deploy rewrite) when ``_game_writer`` is None or stale.
"""

from __future__ import annotations

from engine import auto_deploy
from engine import crash_recovery
from engine import watcher_request


def format_recover_status(*, root=None) -> str:
    """Crash recovery + auto-deploy status (same facts as ``gm recover status``)."""
    root = root or watcher_request._repo_root()
    parts = [crash_recovery.status_text(root=root)]
    try:
        parts.append(auto_deploy.status_text())
    except Exception as exc:
        parts.append(f"Auto-deploy status unavailable: {exc!r}")
    return "\r\n\r\n".join(parts)


def outage_cheat_sheet() -> str:
    """Bare ``gm`` / ``gm outage`` index while the game child is unreachable."""
    return "\r\n".join([
        "GM outage cheat-sheet (game IPC down — gateway stitch mode):",
        "",
        "  gm recover status              crash / revert / deploy holds",
        "  gm recover restart [backup]    game-only respawn (gateway stays up)",
        "  gm recover revert              reset code to last stable SHA",
        "  gm recover restoredb [date]    restore riftforge.db from backup",
        "  gm recover clearhold           clear revert hold + deploy catch-up",
        "  gm recover gateway confirm     full gateway + game bounce (drops all clients)",
        "",
        "  ooc / wiznet                   still work here",
        "  help outage                    this page + longer notes",
        "",
        "Other gm verbs need the rewrite to finish.",
    ])


def outage_help_text() -> str:
    """``help outage`` while stitch mode is on."""
    return "\r\n".join([
        "help outage -- staff commands while the game reloads",
        "",
        "Copyover, auto-deploy, or a crash can leave the game child down.",
        "The gateway keeps your telnet socket and handles a small command set.",
        "Players get OOC only; staff also get wiznet and the recover verbs below.",
        "",
        "Read-only:",
        "  gm recover status     revert holds, stable SHA, recent exits, deploy",
        "",
        "Head GM recovery (queued to the Docker watcher):",
        "  gm recover restart [backup]   respawn server.py only",
        "  gm recover revert             git reset to last stable + respawn",
        "  gm recover restoredb [YYYY-MM-DD]",
        "  gm recover clearhold          after a revert, resume auto-deploy",
        "  gm recover gateway confirm    stop gateway + game, respawn both",
        "                                (brief disconnect for everyone)",
        "",
        "Prefer game-only restart when possible (hard rule 19).",
        "Use gateway confirm only when gateway IPC is wedged or gateway code changed.",
        "",
        "See also: gm outage (short index) | help gmops | help gm",
    ])
