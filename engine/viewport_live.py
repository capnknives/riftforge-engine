"""Live Ash-viewport safeties — restore + playerfile freeze.

Crashgate/sidecar keep the old wipebodies reset. The live droplet uses this
profile so ``op=reset`` cannot wipe the town, occupy never rides a real PC,
and persist writes the pre-session snapshot until restore.

Stdlib only. No supers imports.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Idle with no viewport ops: restore + park even if MCP/SSH died.
LIVE_IDLE_TIMEOUT_S = 120.0

# Live reset is gm on + lobby seat. Never wipebodies / expelspirits.
LIVE_RESET_STAFF_LINES = ("gm on",)

# Red verbs: playerfile / roster / deploy class. Default refuse on live.
_RED_LINE_RE = re.compile(
    r"""(?ix)
    \bwipebodies\b
    | \bexpelspirits\b
    | \bhakai\b
    | \bunhakai\b
    | \brestore\s+pfile\b
    | \brestore\s+checkpoint\b
    | \brestore\s+all\b
    | \bmaps\s+restore\b
    | \bautodeploy\b
    | \bcopyover\b
    | \bgm\s+recover\b
    | \brecover\s+revert\b
    """
)

# Yellow: restore-guarded mutation of Cast / tagged spawns.
_YELLOW_LINE_RE = re.compile(
    r"""(?ix)
    ^\s*(?:gm\s+)?(?:force|summon|set|enemy\s+spawn|spawn)\b
    """
)

_TRUE = ("1", "true", "True", "yes", "YES", "on", "live")
_OFF = ("off", "0", "false", "False", "no", "crashgate", "sidecar", "isolated")


def viewport_safeties_live() -> bool:
    """True when this Game must use live restore / occupy / persist freeze.

    ``RIFTFORGE_VIEWPORT_SAFETIES=off`` (sidecar + crashgate) disables it.
    ``=live`` forces it. Unset + ``RIFTFORGE_VIEWPORT=1`` defaults to live
    so a forgotten crashgate-style reset cannot run on the droplet.
    Unset with viewport off (unit stubs) stays off so smokes keep minting.
    """
    raw = (os.environ.get("RIFTFORGE_VIEWPORT_SAFETIES") or "").strip().lower()
    if raw in _OFF:
        return False
    if raw in ("live", "on", "1", "true", "yes"):
        return True
    flag = (os.environ.get("RIFTFORGE_VIEWPORT") or "0").strip()
    return flag in _TRUE


def live_line_is_red(line: str) -> bool:
    """True when this command is a hard live refuse (roster / pfile class)."""
    return bool(_RED_LINE_RE.search(line or ""))


def live_line_is_yellow(line: str) -> bool:
    """True when live should snapshot Cast/room before dispatch."""
    return bool(_YELLOW_LINE_RE.search((line or "").strip()))


def live_red_refuse_message(line: str) -> str:
    """Player-facing refuse text for a red live viewport line."""
    snippet = (line or "").strip()[:80]
    return (
        "live viewport refuses this command (playerfile / roster risk): "
        f"{snippet!r}. Spell the verb and named risk; maintainer must "
        "consent. Use host=sidecar or crashgate for wipebodies playtest."
    )


def is_staff_fixture_body(char) -> bool:
    """True for catalog staff-login Ash (the only live occupy target)."""
    if char is None:
        return False
    from engine.accounts import is_staff_login_cast

    return bool(is_staff_login_cast(char))


def is_live_playerfile_body(char) -> bool:
    """True for an account-linked PC that live viewport must not ride or set.

    Staff-login Ash is the exception (fixture occupy).
    """
    if char is None:
        return False
    if is_staff_fixture_body(char):
        return False
    from engine.char_identity import is_account_linked_body

    return bool(is_account_linked_body(char))


def character_game(char) -> Any:
    """Best-effort Game from a live Character."""
    if char is None:
        return None
    session = getattr(char, "session", None)
    game = getattr(session, "game", None) if session is not None else None
    if game is not None:
        return game
    loc = getattr(char, "location", None)
    return getattr(loc, "game", None) if loc is not None else None


def frozen_persist_payload(obj):
    """Return ``(char_row, item_rows)`` freeze for persist, or None.

    Persistence writes this instead of live RAM while the viewport session
    still holds a restore snapshot. Login PCs are never skip-saved.
    """
    game = character_game(obj)
    hub = getattr(game, "viewport_hub", None) if game is not None else None
    if hub is None:
        return None
    store = getattr(hub, "_restore", None) or {}
    key = getattr(obj, "key", None) or ""
    row = store.get(key)
    if not isinstance(row, dict):
        return None
    if row.get("spawn"):
        return None
    char_row = row.get("char_row")
    item_rows = row.get("item_rows")
    if char_row is None:
        return None
    return char_row, list(item_rows or [])
