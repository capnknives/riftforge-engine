"""Out-of-character / meta verbs that bypass the combat FIFO buffer (Q49).

Sourced from ``engine/verbs`` only: the bug/suggestion/typo report pipeline,
OOC and account channels, async helper tickets, and session meta verbs listed
in ``ENGINE_COMMANDS`` (see ``engine/verbs/__init__.py`` module docstring).

SUPERS-only meta verbs (for example ``preport``) extend this set in
``supers/combat_command_buffer.py`` — do not add game verbs here.
"""

from __future__ import annotations

# Report + staff ticket pipeline (``engine/verbs/basic.py``).
_REPORT_META = frozenset({
    "bug",
    "bugs",
    "suggest",
    "ideas",
    "suggestions",
    "typo",
    "typos",
    "helpsubmit",
    "reports",
    "resolve",
})

# OOC / public channels (``engine/verbs/basic.py``, ``channels.py``).
_OOC_META = frozenset({
    "ooc",
    "osay",
    "tell",
    "whisper",
    "reply",
    "r",
    "ooctell",
    "otell",
    "questions",
    "question",
    "answers",
    "replay",
    "mute",
    "unmute",
    "mutes",
    "chans",
})

# Offline helper tickets (``engine/verbs/player_queries.py``).
_QUERY_META = frozenset({
    "query",
    "queries",
    "querylist",
    "queryread",
    "querycomment",
    "queryclose",
})

# Session / prefs meta — never trap a player behind combat typing.
_SESSION_META = frozenset({
    "quit",
    "logout",
    "help",
    "commands",
    "more",
    "stop",
    "changes",
    "who",
    "time",
    "timeformat",
    "date",
    "account",
    "config",
    "prompt",
    "alias",
    "color",
    "brief",
    "setpass",
    "save",
    "mpkg",
    "gmhelp",
})

# Sense / sheet verbs already in ENGINE_COMMANDS (look + inventory families).
# Bug report 1820: these are instant reads, not combat spends — they must
# not wait on the Q49 FIFO. Aliases come from the same ENGINE_COMMANDS rows.
_SENSE_META = frozenset({
    "look",
    "l",
    "nearby",
    "inventory",
    "inv",
    "i",
})

INSTANT_META_COMMANDS = (
    _REPORT_META | _OOC_META | _QUERY_META | _SESSION_META | _SENSE_META
)


def verb_bypasses_combat_buffer(verb: str) -> bool:
    """True when this engine verb must not wait in the combat FIFO."""
    return (verb or "").lower() in INSTANT_META_COMMANDS
