"""Gamewide help overlay resolution mode (``gm helpmode``).

``hedit`` (default): live hedit SQLite rows shadow git canon at ``help``,
except stale shrink-stubs (existing behavior).

``git``: when shipped git canon differs from a hedit overlay, players read
git at ``help`` -- the same body ``hunder`` shows staff. Substantial Matt
gold hedit still exists in the DB; staff flip back to ``hedit`` to restore
overlay-first play.
"""
from __future__ import annotations

MODE_HEDIT = "hedit"
MODE_GIT = "git"
MODES = (MODE_HEDIT, MODE_GIT)
META_KEY = "help_overlay_mode"
DEFAULT_MODE = MODE_HEDIT


def normalize_loaded(raw):
    """Sanitize a persisted mode string."""
    mode = raw or DEFAULT_MODE
    if isinstance(mode, dict):
        mode = mode.get("mode") or mode.get("value")
    mode = str(mode or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        return DEFAULT_MODE
    return mode


def ensure_game_mode(game):
    """Attach a valid mode string on ``game.help_overlay_mode``."""
    if game is None:
        return DEFAULT_MODE
    game.help_overlay_mode = normalize_loaded(
        getattr(game, "help_overlay_mode", None),
    )
    return game.help_overlay_mode


def is_git_prefer(game):
    """True when drifted overlays lose at player ``help`` lookup."""
    return ensure_game_mode(game) == MODE_GIT


def overlay_bodies_differ(overlay_body, static_body):
    """True when hedit body is not byte-identical to shipped static."""
    return (overlay_body or "").strip() != (static_body or "").strip()


def skip_overlay_at_help_lookup(overlay_body, static_body, *, game=None, mode=None):
    """True when ``help`` should serve git static instead of the hedit row.

    Stale shrink-stubs always skip. In ``git`` mode, any body drift skips too.
    """
    from engine import help_db

    if help_db.overlay_is_stale_stub(overlay_body, static_body):
        return True
    resolved = mode if mode in MODES else ensure_game_mode(game)
    if resolved == MODE_GIT and overlay_bodies_differ(overlay_body, static_body):
        return True
    return False


def set_mode(game, mode):
    """Set staff mode. Returns (ok, message)."""
    if game is None:
        return False, "No game."
    cleaned = str(mode or "").strip().lower()
    if cleaned not in MODES:
        return False, (
            f"Usage: gm helpmode {MODE_HEDIT} | gm helpmode {MODE_GIT}"
        )
    game.help_overlay_mode = cleaned
    if cleaned == MODE_GIT:
        label = (
            "drifted hedit overlays lose at help -- players read shipped git "
            "(same as hunder) until you flip back"
        )
    else:
        label = (
            "hedit overlays win at help (default) -- stale shrink-stubs still "
            "fall through to git"
        )
    return True, f"Help overlay mode: {cleaned} -- {label}."


def format_report(game):
    """Staff status line for bare ``gm helpmode``."""
    mode = ensure_game_mode(game)
    if mode == MODE_GIT:
        detail = (
            "Player help prefers shipped git whenever a hedit row differs "
            "from code. Use hnews for the drift inbox; hunder still reads "
            "one page for staff."
        )
    else:
        detail = (
            "Player help prefers live hedit overlays (Matt gold). Stale "
            "shrink-stubs still lose to git automatically."
        )
    return (
        f"Help overlay mode: {mode}\n"
        f"{detail}\n"
        f"Set: gm helpmode {MODE_HEDIT} | gm helpmode {MODE_GIT}"
    )
