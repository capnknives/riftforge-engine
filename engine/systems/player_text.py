"""Shared caps for player-authored paste-editor prose.

Suggestion 534: RP text boxes (setdesc, history bio, journal, mail, MOTD,
homestead custom looks, demesne cell prose, …) no longer hit a low
character wall. A high sanity bound still blocks accidental megabyte
pastes from wedging SQLite or tick loops.
"""

from __future__ import annotations

# Not a player-facing limit — practical "no cap" for telnet paste editors.
PLAYER_TEXT_WALL_MAX = 65535
