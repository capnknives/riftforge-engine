"""chargen_menu.py -- engine-owned chargen cancel sentinel.

Account login and the game chargen flow share one "return to roster"
token so ``engine/account_login.py`` never imports a game package
(Phase 2 two-repo purity). ``supers/chargen.py`` re-exports these names.
"""

from __future__ import annotations

# Unique sentinel -- never compare with ``== True`` / ``== False``.
# Callers test ``result is CHARGEN_MENU``.
CHARGEN_MENU = object()

_CANCEL_WORDS = frozenset({"cancel", "menu", "abort", "quit", "exit"})


def is_menu_cancel(raw):
    """True when the player wants to vault the draft and leave chargen."""
    return (raw or "").strip().lower() in _CANCEL_WORDS
